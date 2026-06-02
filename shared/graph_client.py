"""Microsoft Graph client — Mail operations for ITS workstreams.

Auth: MSAL client-credentials flow against an Entra ID app registration.
Credentials live in macOS Keychain (ITS_MS_TENANT_ID / ITS_MS_CLIENT_ID /
ITS_MS_CLIENT_SECRET). The proven sandbox path is in scripts/smoke_test_graph.py.

Capabilities exposed:
    list_inbox, get_message, list_attachments, download_attachment,
    mark_read, move_message, send_mail

Error model:
    Every failure raises a typed exception under GraphError. Callers decide
    whether to log, quarantine, or retry — this module does not swallow.

External Send Gate (Foundation Mission v8, Invariant 1):
    This module *exposes* send_mail() as a capability. The architectural gate
    that prevents AI-generated content from being sent externally lives at the
    workflow level: generation scripts must not import this module
    (enforced by tests/test_capability_gating.py). This module's job is to
    make sending possible when authorized — not to gate it.

Untrusted-content boundary (Invariant 2):
    Inbound message bodies, subjects, and attachment metadata returned by this
    module are raw Graph dicts — potentially adversarial. Callers that feed
    this data into an AI prompt are responsible for wrapping it with
    shared.untrusted_content.wrap(). This module does not auto-wrap because
    not every caller is an AI consumer (e.g., human-review queues).

Token cache:
    In-memory, module-level. Fine for single-process scripts launched by
    launchd. Multi-process workers (parallel send fan-out, etc.) need their
    own cache — each process will hold its own token, which is correct but
    pays the auth round-trip per process.
"""
from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from typing import Any, Literal

import msal  # type: ignore[import-untyped]
import requests  # type: ignore[import-untyped]

from . import keychain

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_SCOPES = ["https://graph.microsoft.com/.default"]

# Graph tokens are issued with expires_in=3600. Refresh when within this many
# seconds of expiry — gives us a 50-min effective cache and a 10-min safety
# margin for clock skew + in-flight requests.
TOKEN_REFRESH_MARGIN_SECONDS = 600

MAX_RETRIES = 3
DEFAULT_INBOX_FIELDS = ["id", "subject", "from", "receivedDateTime", "hasAttachments"]

# Fields requested by `get_message` when `include_headers=True`. Mirrors the
# default Graph projection for a message PLUS `internetMessageHeaders` so the
# safety_reports intake Stage 2 header-forgery gate can read Authentication-
# Results / Return-Path / DKIM-Signature without a second round trip.
# `body` is explicit (Graph normally returns it by default, but $select makes
# the projection narrow) and so are the other fields intake reads.
GET_MESSAGE_WITH_HEADERS_FIELDS = [
    "id",
    "subject",
    "from",
    "receivedDateTime",
    "hasAttachments",
    "body",
    "internetMessageHeaders",
]


class GraphError(Exception):
    """Base exception for all Microsoft Graph failures."""


class GraphAuthError(GraphError):
    """Token acquisition failed, or Graph returned 401."""


class GraphPermissionError(GraphError):
    """Graph returned 403 — typically an Application Access Policy denial."""


class GraphNotFoundError(GraphError):
    """Graph returned 404 — mailbox, message, attachment, or folder missing."""


class GraphRateLimitError(GraphError):
    """Graph returned 429 after the retry budget was exhausted."""


class GraphTimeoutError(GraphError):
    """A Graph request (or the MSAL token call) exceeded its connect/read
    timeout. A distinct subclass so a *hang* is grep-distinguishable in
    ITS_Errors from a rate-limit / auth / not-found failure, and so callers
    that already catch GraphError soft-fail it on the normal path."""


# Request timeout — (connect, read) tuple passed to every requests.request call.
#
# §42 rationale (2026-06-02): the Mail wrappers funnel through `_request`, which
# called `requests.request` with NO `timeout=`. A stalled TCP connection
# therefore hung the whole daemon cycle *indefinitely* — an `intake_poll` cycle
# hung ~88 min holding the fcntl lock, starving every later launchd interval
# (the daemon silently stopped while launchd believed it was running). The F08
# Smartsheet circuit breaker can't catch this: it guards Smartsheet, and a call
# that never *returns* never trips the failure counter. A connect/read timeout
# converts that indefinite hang into a finite `requests.Timeout`, translated
# below to `GraphTimeoutError` so it lands in callers' existing `except
# GraphError` fence (e.g. intake.process_message) and the per-cycle fence
# releases the lock. See docs/tech_debt.md (Graph-call timeout) +
# docs/runbooks/... 30s read matches the smartsheet_client REST-helper literal.
#
# requests' read timeout is an *inactivity* timeout (max seconds between bytes
# from the server), not a total-transfer cap. download_attachment buffers the
# whole body (response.content, no stream=True), but the read timeout still
# applies per socket read during that buffering — a large $value attachment
# that keeps arriving will NOT trip it; only a server that goes silent for 30s
# does. That is exactly the hang we want to catch, so download_attachment needs
# no special-casing.
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)

# The MSAL token-acquisition path uses MSAL's OWN internal HTTP client (a
# separate requests.Session), so REQUEST_TIMEOUT above does NOT cover it.
# ConfidentialClientApplication accepts a top-level timeout= kwarg (msal 1.36).
TOKEN_TIMEOUT_SECONDS = 30.0


_token: str | None = None
_token_expires_at: float = 0.0


def _get_token() -> str:
    """Acquire an app-only access token via MSAL, caching within TTL.

    Returns the cached token until it is within TOKEN_REFRESH_MARGIN_SECONDS
    of expiry, then re-acquires from Entra.

    Raises:
        GraphAuthError: MSAL returned an error response. The MSAL
            error_description is preserved in the exception message.
    """
    global _token, _token_expires_at

    now = time.time()
    if _token is not None and now < _token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
        return _token

    tenant_id = keychain.get_secret("ITS_MS_TENANT_ID")
    client_id = keychain.get_secret("ITS_MS_CLIENT_ID")
    client_secret = keychain.get_secret("ITS_MS_CLIENT_SECRET")

    # timeout= covers MSAL's internal HTTP (instance discovery + token call);
    # wrap construction AND acquisition so a transport stall becomes a finite
    # GraphTimeoutError instead of hanging the daemon (the A2 token surface,
    # distinct from the requests.request surface in _request).
    try:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
            timeout=TOKEN_TIMEOUT_SECONDS,
        )
        result = app.acquire_token_for_client(scopes=DEFAULT_SCOPES)
    except requests.Timeout as exc:
        raise GraphTimeoutError(
            f"MSAL token acquisition timed out after {TOKEN_TIMEOUT_SECONDS}s: {exc!r}"
        ) from exc
    except requests.RequestException as exc:
        raise GraphAuthError(f"MSAL token acquisition transport error: {exc!r}") from exc

    if "access_token" not in result:
        err = result.get("error", "unknown")
        desc = result.get("error_description", "no description")
        raise GraphAuthError(f"MSAL token acquisition failed ({err}): {desc}")

    _token = result["access_token"]
    _token_expires_at = now + float(result.get("expires_in", 3600))
    return _token


def _extract_error_message(response: requests.Response) -> str:
    """Pull the human-readable error message out of a Graph error response."""
    try:
        body = response.json()
        return body.get("error", {}).get("message") or response.text[:200]
    except ValueError:
        return response.text[:200]


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value as seconds. None on unparseable input."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # Graph normally returns seconds. HTTP-date form is technically legal
        # but Graph does not emit it; fall back to backoff in that case.
        return None


def _check_response(response: requests.Response) -> requests.Response:
    code = response.status_code
    if 200 <= code < 300:
        return response

    msg = _extract_error_message(response)
    if code == 401:
        raise GraphAuthError(f"HTTP 401: {msg}")
    if code == 403:
        raise GraphPermissionError(f"HTTP 403: {msg}")
    if code == 404:
        raise GraphNotFoundError(f"HTTP 404: {msg}")
    if code == 429:
        raise GraphRateLimitError(f"HTTP 429: {msg}")
    raise GraphError(f"HTTP {code}: {msg}")


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> requests.Response:
    """Execute a Graph request with retry on 429/503 (exponential backoff)."""
    headers = {"Authorization": f"Bearer {_get_token()}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    response: requests.Response | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.Timeout as exc:
            # Fail fast — do NOT consume retries on a hang. A hung host rarely
            # un-hangs within the same cycle, and retrying would re-create the
            # very lock-starvation (up to MAX_RETRIES × read timeout) this guard
            # exists to prevent. The launchd interval + watchdog are the
            # recovery net. Typed so it lands in callers' `except GraphError`.
            raise GraphTimeoutError(
                f"Graph request {method} {url} timed out: {exc!r}"
            ) from exc
        except requests.RequestException as exc:
            # Connection reset / DNS failure / etc. — a finite, typed failure
            # rather than a raw requests exception escaping the GraphError
            # hierarchy (mirrors smartsheet_client's RequestException→typed wrap).
            raise GraphError(
                f"Graph request {method} {url} failed: {exc!r}"
            ) from exc
        if response.status_code not in (429, 503):
            break
        if attempt == MAX_RETRIES - 1:
            break
        delay = _parse_retry_after(response.headers.get("Retry-After"))
        if delay is None:
            delay = float(2**attempt)
        time.sleep(delay)

    # Loop always runs at least once, so response is never None here.
    assert response is not None
    return _check_response(response)


# ---- Read ----------------------------------------------------------------


def list_inbox(
    mailbox: str,
    *,
    since: str | None = None,
    top: int = 50,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List messages in a mailbox's Inbox folder.

    Args:
        mailbox: Mailbox address (e.g., "safety@evergreenmirror.com").
        since: ISO-8601 datetime. If provided, filters to
            receivedDateTime >= since.
        top: Max messages to return. Graph caps at 1000; default 50.
        fields: $select fields. Defaults to DEFAULT_INBOX_FIELDS.

    Returns:
        List of raw Graph message dicts (untrusted — see module docstring).
    """
    fields = fields or DEFAULT_INBOX_FIELDS
    params: dict[str, Any] = {
        "$select": ",".join(fields),
        "$top": str(top),
    }
    if since is not None:
        params["$filter"] = f"receivedDateTime ge {since}"

    url = f"{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages"
    response = _request("GET", url, params=params)
    return response.json().get("value", [])


def fetch_latest_inbound_timestamp(mailbox: str) -> datetime | None:
    """Return UTC timestamp of the most recent message in `mailbox`'s Inbox.

    Returns None if the inbox has never received a message (empty `value`
    list — distinct from an error). Used by `scripts/watchdog.py` Check F
    to detect mailboxes that have gone silent past their idle threshold
    (the Mail.app silent-disable pattern documented in `docs/tech_debt.md`).

    Raises:
        GraphError: any auth / network / policy / not-found failure
            propagates as the typed exception from `_check_response`.
    """
    url = f"{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages"
    params: dict[str, Any] = {
        "$select": "receivedDateTime",
        "$top": "1",
        "$orderby": "receivedDateTime desc",
    }
    response = _request("GET", url, params=params)
    messages = response.json().get("value", [])
    if not messages:
        return None
    raw = messages[0].get("receivedDateTime")
    if not isinstance(raw, str):
        return None
    # Graph emits ISO 8601 with a trailing 'Z'; fromisoformat in 3.11+
    # accepts that natively. Normalize to UTC-aware datetime regardless.
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def get_message(
    mailbox: str,
    message_id: str,
    *,
    include_headers: bool = False,
) -> dict[str, Any]:
    """Fetch a single message including body.

    `include_headers=True` projects `internetMessageHeaders` (plus the rest
    of the fields the intake pipeline reads) via `$select`. Headers are NOT
    in Graph's default response shape, so the opt-in is required for the
    Stage 2 SPF/DKIM/DMARC parser.
    """
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    params: dict[str, Any] | None = None
    if include_headers:
        params = {"$select": ",".join(GET_MESSAGE_WITH_HEADERS_FIELDS)}
    response = _request("GET", url, params=params)
    return response.json()


def list_attachments(mailbox: str, message_id: str) -> list[dict[str, Any]]:
    """List attachment metadata for a message (does not download content)."""
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}/attachments"
    response = _request("GET", url)
    return response.json().get("value", [])


def download_attachment(mailbox: str, message_id: str, attachment_id: str) -> bytes:
    """Download a file attachment's raw bytes via the $value endpoint."""
    url = (
        f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
        f"/attachments/{attachment_id}/$value"
    )
    response = _request("GET", url)
    return response.content


def mark_read(mailbox: str, message_id: str) -> None:
    """Set isRead=True on a message."""
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    _request("PATCH", url, json_body={"isRead": True})


def move_message(mailbox: str, message_id: str, destination_folder_id: str) -> None:
    """Move a message into another mail folder by folder ID."""
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}/move"
    _request("POST", url, json_body={"destinationId": destination_folder_id})


# ---- Send ----------------------------------------------------------------


def send_mail(
    *,
    from_mailbox: str,
    to: list[str],
    subject: str,
    body: str,
    content_type: Literal["Text", "HTML"] = "Text",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> None:
    """Send a message from `from_mailbox` via Graph /sendMail.

    Args:
        from_mailbox: Sending mailbox (must be covered by the app's
            Application Access Policy).
        to / cc / bcc: Recipient address lists.
        subject: Message subject.
        body: Message body content.
        content_type: "Text" (default) or "HTML".
        attachments: Optional list of dicts shaped as
            {"name": str, "contentType": str, "contentBytes": bytes}.
            Bytes are base64-encoded internally — pass raw bytes.

    Returns nothing on success (Graph returns 202 Accepted).
    Raises a GraphError subclass on failure.
    """
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": content_type, "content": body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
    }
    if cc:
        message["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc]
    if bcc:
        message["bccRecipients"] = [{"emailAddress": {"address": addr}} for addr in bcc]
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a["name"],
                "contentType": a["contentType"],
                "contentBytes": base64.b64encode(a["contentBytes"]).decode("ascii"),
            }
            for a in attachments
        ]

    payload = {"message": message, "saveToSentItems": True}
    url = f"{GRAPH_BASE}/users/{from_mailbox}/sendMail"
    _request("POST", url, json_body=payload)
