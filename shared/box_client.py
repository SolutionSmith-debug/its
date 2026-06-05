"""Box SDK wrapper — OAuth 2.0 User Authentication.

Auth: OAuth 2.0 Authorization Code Grant against a Box Custom App configured
as a User Authentication app. The first-time browser flow is handled by
`scripts/setup_box_oauth.py`; this module wires the steady-state flow that
every ITS process invocation uses.

**Pivot context (2026-05-20):** ITS originally targeted Box JWT/server-auth.
That path requires the paid Box Platform add-on, which Evergreen's Box
Enterprise tier does not include. OAuth User Authentication works on
standard Enterprise with no add-on; the tradeoff is that ITS authenticates
as a real Box user (operator account for now, dedicated ITS user at
Phase 1.5 cutover) rather than as a service account. Audit trail and file
ownership attribute to that user. Acceptable for Customer 0 sandbox phase.

Credentials live in macOS Keychain:
    ITS_BOX_CLIENT_ID         # operator-seeded once, never rotates here
    ITS_BOX_CLIENT_SECRET     # operator-seeded once, manually rotated in Box console
    ITS_BOX_REFRESH_TOKEN     # written by setup_box_oauth.py; ROTATED on every use

**Critical invariant — refresh-token rotation MUST persist.** Box rotates
refresh tokens on every token exchange. The old token becomes invalid the
moment a new one is issued. If ITS reads the refresh token from Keychain,
exchanges it, gets a new one, then crashes before persisting the new one,
the next ITS invocation reads the old (now invalid) token and fails
authentication. Recovery requires re-running `setup_box_oauth.py`. The
`_store_tokens` callback wired into `boxsdk.OAuth2` MUST write the new
refresh token to Keychain synchronously on every rotation. The test suite
asserts this explicitly.

Ship-and-leave window: refresh tokens are valid for 60 days from last use.
ITS in steady-state runs daily workstreams → token is exchanged daily →
no concern. If ITS goes dark for >60 days, the refresh token expires and
the operator must re-run `setup_box_oauth.py`. A watchdog freshness check
(planned for R2 Watchdog Session 2 or later) will WARN at 50 days idle
and CRITICAL at 58.

Capabilities exposed:
    get_client(), upload_file(), upload_bytes(), download_file(), list_folder(),
    get_folder_by_path(), get_or_create_folder(), search(), get_file_metadata(),
    canonical_job_path()

Error model:
    Every failure raises a typed exception under `BoxError`. Callers
    decide whether to log, retry, or surface — this module does not
    swallow. Mirrors the shape of `shared.graph_client` and
    `shared.resend_client`.

Retry: 429 and 503 with Retry-After header honored, exponential backoff
fallback, cap `MAX_RETRIES=3`. Same pattern as resend_client.
"""
from __future__ import annotations

import time
from typing import Any

from boxsdk import Client, OAuth2  # type: ignore[import-untyped]
from boxsdk.exception import (  # type: ignore[import-untyped]
    BoxAPIException,
    BoxOAuthException,
)

from . import keychain

OAUTH_TOKEN_URL = "https://api.box.com/oauth2/token"  # noqa: S105 — public OAuth endpoint
OAUTH_AUTHORIZE_URL = "https://account.box.com/api/oauth2/authorize"

MAX_RETRIES = 3

# Keychain entry names. Single source of truth — also used by
# setup_box_oauth.py and smoke_test_box.py.
KC_CLIENT_ID = "ITS_BOX_CLIENT_ID"
KC_CLIENT_SECRET = "ITS_BOX_CLIENT_SECRET"  # noqa: S105 — Keychain entry NAME, not the secret itself
KC_REFRESH_TOKEN = "ITS_BOX_REFRESH_TOKEN"  # noqa: S105 — Keychain entry NAME, not the secret itself


# ---- Typed exceptions ----------------------------------------------------


class BoxError(Exception):
    """Base exception for all Box failures."""


class BoxAuthError(BoxError):
    """Token rejected or insufficient scope (HTTP 401/403)."""


class BoxNotFoundError(BoxError):
    """File, folder, or resource missing (HTTP 404)."""


class BoxConflictError(BoxError):
    """Conflict (HTTP 409) — typically duplicate filename in a folder."""


class BoxRateLimitError(BoxError):
    """HTTP 429 after the retry budget was exhausted."""


# ---- Lazy-singleton client + token rotation ------------------------------


_client: Client | None = None


def _store_tokens(access_token: str, refresh_token: str) -> None:
    """OAuth2 store_tokens callback — persists the rotated refresh token.

    boxsdk calls this on EVERY token exchange (initial + every refresh).
    Box rotates the refresh token on each exchange; if we don't persist
    the new one, the next ITS process invocation reads the old (now
    invalid) token from Keychain and fails authentication. See module
    docstring "Critical invariant."

    Access tokens are NOT persisted — they have a 60-minute TTL and
    boxsdk re-fetches them on demand within the process.
    """
    keychain.set_secret(KC_REFRESH_TOKEN, refresh_token)


def get_client() -> Client:
    """Return a process-wide Box OAuth client, building it on first use.

    Reads `ITS_BOX_CLIENT_ID`, `ITS_BOX_CLIENT_SECRET`, and
    `ITS_BOX_REFRESH_TOKEN` from Keychain. Constructs `boxsdk.OAuth2`
    with `_store_tokens` wired so refresh-token rotations persist. Wraps
    in a `boxsdk.Client` which handles access-token acquisition + rotation
    transparently inside the process.

    Subsequent calls within the same process return the cached client.

    Raises:
        BoxAuthError: If credential reads fail or the initial token
            exchange is rejected (typical cause: refresh token expired
            after >60 days idle, or revoked from the Box console).
    """
    global _client
    if _client is None:
        try:
            client_id = keychain.get_secret(KC_CLIENT_ID)
            client_secret = keychain.get_secret(KC_CLIENT_SECRET)
            refresh_token = keychain.get_secret(KC_REFRESH_TOKEN)
        except keychain.KeychainError as e:
            raise BoxAuthError(
                f"Box credentials missing from Keychain: {e}. "
                f"Run scripts/setup_box_oauth.py to seed."
            ) from e

        oauth = OAuth2(
            client_id=client_id,
            client_secret=client_secret,
            access_token=None,  # forces refresh-token exchange on first call
            refresh_token=refresh_token,
            store_tokens=_store_tokens,
        )
        _client = Client(oauth)
    return _client


# ---- Retry / error translation -------------------------------------------


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After as seconds. None on unparseable input."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form is legal but Box returns seconds; fall back to backoff.
        return None


def _translate(exc: BoxAPIException) -> BoxError:
    """Map a BoxAPIException onto our typed hierarchy."""
    status = exc.status
    message = exc.message or "Box API error"
    detail = f"HTTP {status}: {message}"
    if status in (401, 403):
        return BoxAuthError(detail)
    if status == 404:
        return BoxNotFoundError(detail)
    if status == 409:
        return BoxConflictError(detail)
    if status == 429:
        return BoxRateLimitError(detail)
    return BoxError(detail)


def _call(operation, *args, **kwargs):  # type: ignore[no-untyped-def]
    """Execute a boxsdk operation with retry on 429/503.

    `operation` is a callable (typically a method bound to a boxsdk
    resource — e.g., `client.folder("0").get_items`). Args/kwargs are
    forwarded. On 429 or 503, retries up to `MAX_RETRIES` with
    Retry-After honored (Box uses seconds); falls back to exponential
    backoff when the header is absent.

    Translates `BoxAPIException` to the typed `BoxError` hierarchy.
    `BoxOAuthException` (auth-layer failures during token exchange)
    surfaces as `BoxAuthError` regardless of status.
    """
    last_exc: BoxAPIException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return operation(*args, **kwargs)
        except BoxOAuthException as e:
            # Auth-layer failure — token exchange itself failed. Don't
            # retry; the refresh token is bad.
            raise BoxAuthError(f"OAuth exchange failed: {e}") from e
        except BoxAPIException as e:
            if e.status not in (429, 503):
                raise _translate(e) from e
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                break
            headers = getattr(e, "headers", None) or {}
            delay = _parse_retry_after(headers.get("Retry-After"))
            if delay is None:
                delay = float(2**attempt)
            time.sleep(delay)
    # Exhausted retries on 429/503.
    assert last_exc is not None
    raise _translate(last_exc) from last_exc


# ---- Public API ----------------------------------------------------------


def upload_file(
    folder_id: str,
    file_path: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Upload a local file to a Box folder. Returns minimal file metadata.

    Args:
        folder_id: Box folder ID. "0" is the user's root folder.
        file_path: Local filesystem path to the file to upload.
        name: Optional override for the uploaded file name. Defaults to
            the basename of `file_path`.

    Returns:
        Dict with `id`, `name`, `size` for the uploaded file. Box's
        full file object has many more fields; we expose the minimal
        set callers actually need and force a fresh API call if more
        is required (avoids surprises from boxsdk lazy-loading attrs).

    Raises:
        BoxConflictError: HTTP 409 — a file with the same name already
            exists in the destination folder.
        BoxAuthError / BoxNotFoundError / BoxRateLimitError / BoxError:
            other failure modes per the typed hierarchy.
    """
    client = get_client()
    folder = client.folder(folder_id)
    uploaded = _call(folder.upload, file_path, file_name=name)
    return {"id": uploaded.id, "name": uploaded.name, "size": uploaded.size}


def upload_bytes(folder_id: str, name: str, content: bytes) -> dict[str, Any]:
    """Upload in-memory bytes as a Box file. Returns minimal file metadata.

    The in-memory sibling of `upload_file` — for content produced at runtime
    (e.g. `form_pdf.render_submission_pdf` → PDF bytes) that never touches the
    local filesystem. Uses the boxsdk byte-stream upload path.

    Deliberately NOT routed through `_call`'s 429/503 retry: a `BytesIO` stream
    is consumed on the first attempt, so a naive retry would re-send from EOF and
    upload an empty file. Upload is not safely idempotent to retry anyway. We
    translate exceptions to the typed hierarchy and let the caller decide (the
    portal path suffixes the name + re-uploads on `BoxConflictError` to keep both
    versions of an amended submission).

    Raises:
        BoxConflictError: HTTP 409 — a file named `name` already exists here.
        BoxAuthError / BoxNotFoundError / BoxRateLimitError / BoxError: per the
            typed hierarchy.
    """
    import io
    client = get_client()
    try:
        uploaded = client.folder(folder_id).upload_stream(io.BytesIO(content), name)
    except BoxOAuthException as exc:
        raise BoxAuthError(f"OAuth exchange failed: {exc}") from exc
    except BoxAPIException as exc:
        raise _translate(exc) from exc
    except Exception as exc:  # noqa: BLE001 — honor the module's "every failure → BoxError" contract
        # boxsdk usually raises its own types, but anything else (e.g. an OSError
        # mid-stream) must not escape untranslated past the typed boundary.
        raise BoxError(f"Box upload of {name!r} failed: {exc!r}") from exc
    return {"id": str(uploaded.id), "name": uploaded.name, "size": uploaded.size}


def download_file(file_id: str) -> bytes:
    """Return the raw bytes of a Box file."""
    client = get_client()
    return _call(client.file(file_id).content)


def list_folder(folder_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """List items (files + folders) in a Box folder.

    Returns a list of dicts each containing `id`, `name`, and `type`
    (`'file'` or `'folder'`). Use `folder_id="0"` for the root folder.
    """
    client = get_client()
    items = _call(client.folder(folder_id).get_items, limit=limit)
    return [{"id": item.id, "name": item.name, "type": item.type} for item in items]


def get_folder_by_path(path: str) -> dict[str, Any]:
    """Resolve a slash-separated path under the user's root to a folder.

    `path` is a forward-slash-delimited path under root (e.g.,
    `"Customer/2024.335 — Forefront/2026/"`). Leading and trailing
    slashes are tolerated. Walks the path segment-by-segment from
    root using `list_folder`; raises `BoxNotFoundError` on the first
    segment that doesn't resolve.

    Returns a dict with `id`, `name`, `type='folder'` matching the
    final segment.

    Note: walks via list_folder which is paginated at the default
    limit. Folders containing more than `limit` children with the
    target name buried past that point will fail to resolve. Bump
    `limit` upstream if this becomes a real problem.
    """
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        # Empty/root request — return root folder shape.
        return {"id": "0", "name": "All Files", "type": "folder"}

    current_id = "0"
    current_name = "All Files"
    for segment in segments:
        items = list_folder(current_id)
        match = next(
            (it for it in items if it["type"] == "folder" and it["name"] == segment),
            None,
        )
        if match is None:
            raise BoxNotFoundError(
                f"path segment {segment!r} not found under folder "
                f"{current_id} ({current_name!r}); full path={path!r}"
            )
        current_id = match["id"]
        current_name = match["name"]
    return {"id": current_id, "name": current_name, "type": "folder"}


def _find_child_folder(parent_folder_id: str, name: str) -> str | None:
    """Return the ID of the direct child folder named `name`, or None.

    Lists at a generous page limit (1000, Box's max page) — folders that hold
    more than 1000 same-level children with the target beyond that page won't
    resolve, same documented caveat as `get_folder_by_path`.
    """
    for item in list_folder(parent_folder_id, limit=1000):
        if item["type"] == "folder" and item["name"] == name:
            return str(item["id"])
    return None


def get_or_create_folder(parent_folder_id: str, name: str) -> str:
    """Find a direct child folder named `name` under `parent_folder_id`; create
    it if absent. Idempotent find-or-create. Returns the child folder ID.

    The ITS-auto-created-folder primitive for the Safety Portal Box mirror (the
    compiled-WSR week folder). Per the operator naming rule, callers prefix
    ITS-created folder names with ``ITS`` so the system's own folders are
    distinguishable from the existing job/category tree.

    Race-tolerant: Box does NOT enforce folder-name uniqueness, so two callers
    can both pass the find step and both create. On a create that returns 409
    (BoxConflictError), we re-find and adopt the existing folder if it is now
    visible. If the re-find STILL misses (the folder was concurrently deleted, or
    Box read-replica lag), we re-RAISE the 409 — loud, not silent — so the caller
    retries next cycle rather than proceeding with no folder. Bounded blast
    radius on the adopt path: at worst one extra empty folder for operator cleanup.
    """
    existing = _find_child_folder(parent_folder_id, name)
    if existing is not None:
        return existing
    client = get_client()
    try:
        created = _call(client.folder(parent_folder_id).create_subfolder, name)
        return str(created.id)
    except BoxConflictError:
        refound = _find_child_folder(parent_folder_id, name)
        if refound is not None:
            return refound
        raise


def search(
    query: str,
    *,
    type: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Search Box for items matching `query`.

    Args:
        query: Free-text search query.
        type: Optional `'file'` or `'folder'` to narrow results.
        limit: Max results.

    Returns:
        List of dicts each with `id`, `name`, `type`.
    """
    client = get_client()
    kwargs: dict[str, Any] = {"limit": limit}
    if type is not None:
        kwargs["result_type"] = type
    results = _call(client.search().query, query, **kwargs)
    return [{"id": item.id, "name": item.name, "type": item.type} for item in results]


def get_file_metadata(file_id: str) -> dict[str, Any]:
    """Return basic file metadata (`id`, `name`, `size`, `modified_at`)."""
    client = get_client()
    info = _call(client.file(file_id).get)
    return {
        "id": info.id,
        "name": info.name,
        "size": info.size,
        "modified_at": getattr(info, "modified_at", None),
    }


def canonical_job_path(
    customer: str, job_number: str, job_name: str, year: int
) -> str:
    """Return the canonical Box folder path for a given job.

    Path pattern (per Safety Reports Mission v3 — still open question):
        /Customer/Job Number — Job Name/YYYY/

    Used as the WRITE path for new content. Recognition of pre-existing
    folders is handled by `box_migration/parse_job_v3.py` (which knows
    the many schema variants observed across the closed-archive corpus).
    This helper does not attempt to match those variants.

    TODO: confirm exact path pattern with owner. Kept stub-format from
    the pre-pivot box_client; all workstreams should call this helper
    rather than constructing paths inline so a single edit propagates.
    """
    return f"/{customer}/{job_number} — {job_name}/{year}/"
