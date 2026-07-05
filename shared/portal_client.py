"""Safety Portal internal-transport client — the Mac-side HTTP leg of the pull model.

Purpose
-------
    Thin, audited HTTP transport for the two internal Worker endpoints the
    `safety_reports/portal_poll.py` daemon drives (decision_phase5-portal-transport):

      GET  /api/internal/pending     → the queue drain (box_verified=0, oldest-first)
      POST /api/internal/mark-filed  → the receipt (flips box_verified=1)

    The Cloudflare Worker (`safety_portal/worker/index.ts`) signs + queues each
    submission send-free in D1; this module is the ONLY Python egress to that
    Worker. Keeping the HTTP here (not inline in `portal_poll`) is what lets the
    daemon import a network capability *through an audited shared/*_client.py*
    rather than acquiring `requests` itself — see the F02 NETWORK_LIB_ALLOWLIST
    note in `tests/test_capability_gating.py`. The puller therefore stays inside
    the capability gate; the Worker (TS) was outside it.

Trust boundary
--------------
    This module is TRANSPORT ONLY. It does NOT verify the per-row HMAC — that is
    the caller's job (`portal_poll` recomputes via `shared.portal_hmac` and
    constant-time-compares each pulled row's `hmac` field BEFORE handing it to
    intake). A row returned by `get_pending` is UNTRUSTED until the caller
    verifies it. `mark_filed` is a control-plane receipt to our own Worker, NOT a
    customer-facing send — it is outside the External Send Gate (Invariant 1).

Credentials
-----------
    `base_url` (the Worker origin) and `token` (the bearer) are passed IN by the
    caller — this module reads no Keychain / ITS_Config, so it stays trivially
    testable and the fail-closed credential check lives in one place
    (`portal_poll`). The bearer mirrors the Worker's `PORTAL_INTERNAL_API_TOKEN`;
    on the Mac it is Keychain `ITS_PORTAL_INTERNAL_TOKEN` (resolved by the caller).

Failure modes
-------------
    Every failure raises a typed exception under `PortalTransportError`; this
    module never swallows. A 401 is `PortalAuthError` (bad/missing bearer). 429
    and 503 are retried (cap `MAX_RETRIES`, Retry-After honored) then surface as
    `PortalTransportError`. The caller logs + skips the cycle (the submission
    stays box_verified=0 and re-pulls next cycle — no silent loss).
"""
from __future__ import annotations

import time
from typing import Any

import requests  # type: ignore[import-untyped]

# Network timeouts (connect, read) in seconds. A hung Worker must not wedge the
# 60 s-cadence daemon — fail fast and let the next cycle retry.
TIMEOUT = (10.0, 30.0)
MAX_RETRIES = 3

PENDING_PATH = "/api/internal/pending"
MARK_FILED_PATH = "/api/internal/mark-filed"
MARK_REJECTED_PATH = "/api/internal/mark-rejected"
SYNC_PATH = "/api/internal/sync"
PDF_REQUESTS_PATH = "/api/internal/pdf-requests"
FILED_PDF_PATH = "/api/internal/filed-pdf"
ITEM_PHOTOS_PENDING_PATH = "/api/internal/item-photos/pending"
ITEM_PHOTO_RESULT_PATH_TEMPLATE = "/api/internal/item-photos/{photo_id}/result"
DAILY_PHOTOS_PENDING_PATH = "/api/internal/daily-photos/pending"
DAILY_PHOTO_RESULT_PATH_TEMPLATE = "/api/internal/daily-photos/{photo_id}/result"
PUBLISH_PENDING_PATH = "/api/internal/publish/pending"
PUBLISH_CLAIM_PATH = "/api/internal/publish/claim"
PUBLISH_STAMP_PATH = "/api/internal/publish/stamp"
PUBLISH_STUCK_PATH = "/api/internal/publish/stuck"
FIELDOPS_PENDING_JOBS_PATH = "/api/internal/fieldops/pending-jobs"
FIELDOPS_JOBS_MARK_MIRRORED_PATH = "/api/internal/fieldops/jobs-mark-mirrored"
FIELDOPS_HOURS_PENDING_PATH = "/api/internal/fieldops/hours-pending"
FIELDOPS_HOURS_MARK_MIRRORED_PATH = "/api/internal/fieldops/hours-mark-mirrored"
FIELDOPS_EQUIPMENT_SNAPSHOT_PATH = "/api/internal/fieldops/equipment-snapshot"
PROGRESS_ROLLUP_PATH = "/api/internal/progress-rollup"
PRUNE_STATUS_PATH = "/api/internal/prune-status"


# ---- Typed exceptions ----------------------------------------------------


class PortalTransportError(Exception):
    """Base exception for all portal-transport failures."""


class PortalAuthError(PortalTransportError):
    """Bearer token rejected (HTTP 401) — bad/missing/rotated token."""


class PortalRateLimitError(PortalTransportError):
    """HTTP 429/503 after the retry budget was exhausted."""


# ---- Internals -----------------------------------------------------------


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After as seconds. None on unparseable / HTTP-date form."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _request(
    method: str,
    base_url: str,
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue one authenticated request with retry on 429/503; return parsed JSON.

    Retries transient 429/503 (Retry-After honored, exponential backoff
    fallback) and connection errors up to `MAX_RETRIES`. Translates the final
    outcome to the typed hierarchy. A 401 is NOT retried (the token is bad).
    """
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last_detail = ""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method, url, params=params, json=json_body,
                headers=headers, timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            # Network-layer failure (DNS / connect / read timeout). Retry a
            # bounded number of times, then surface.
            last_detail = f"{type(exc).__name__}: {exc}"
            if attempt == MAX_RETRIES - 1:
                raise PortalTransportError(
                    f"{method} {path} network failure after {MAX_RETRIES} attempts: {last_detail}"
                ) from exc
            time.sleep(float(2**attempt))
            continue

        if response.status_code == 401:
            raise PortalAuthError(
                f"{method} {path} unauthorized (401) — bearer token rejected"
            )
        if response.status_code in (429, 503):
            last_detail = f"HTTP {response.status_code}"
            if attempt == MAX_RETRIES - 1:
                raise PortalRateLimitError(
                    f"{method} {path} throttled/unavailable after {MAX_RETRIES} attempts ({last_detail})"
                )
            delay = _parse_retry_after(response.headers.get("Retry-After"))
            time.sleep(delay if delay is not None else float(2**attempt))
            continue
        if response.status_code != 200:
            raise PortalTransportError(
                f"{method} {path} unexpected status {response.status_code}: "
                f"{response.text[:300]!r}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PortalTransportError(
                f"{method} {path} returned non-JSON body: {response.text[:300]!r}"
            ) from exc
        if not isinstance(data, dict):
            # Type name only — a hostile/broken Worker could return a huge JSON
            # value, and repr()-ing it into the exception would be an unbounded
            # allocation in the daemon. (Same posture as the text[:300] truncation.)
            raise PortalTransportError(
                f"{method} {path} returned non-object JSON (got {type(data).__name__})"
            )
        return data
    # Unreachable: every loop branch either returns or raises on the last attempt.
    raise PortalTransportError(f"{method} {path} exhausted retries: {last_detail}")


# ---- Public API ----------------------------------------------------------


def get_pending(base_url: str, token: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Drain the pending queue: GET /api/internal/pending (oldest-first).

    Returns the `pending` list verbatim — each row a dict with
    `submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid,
    hmac, created_at`. The Worker caps `limit` at 200. Rows are UNTRUSTED until
    the caller verifies each row's `hmac` (see module docstring trust boundary).

    Raises `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure).
    """
    data = _request("GET", base_url, PENDING_PATH, token, params={"limit": limit})
    pending = data.get("pending")
    if not isinstance(pending, list):
        raise PortalTransportError(
            f"GET {PENDING_PATH} missing/invalid 'pending' array (got {type(pending).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in pending if isinstance(row, dict)]


def mark_filed(
    base_url: str, token: str, *, submission_uuid: str, box_link: str,
    box_file_id: str | None = None,
) -> bool:
    """Post the receipt: POST /api/internal/mark-filed → returns `found`.

    Called ONLY after intake has filed the submission to Box + Smartsheet
    (box_verified flips to 1 so the Worker stops serving the row). Idempotent —
    a second call for an already-filed UUID returns `found=True` with no effect.
    `found=False` means the Worker has no row for that UUID (already drained by a
    concurrent actor, or an unknown UUID); the caller treats it as benign.

    `box_file_id` (PR-4 Part A) is the filed Box file id — the Worker stores it so
    the request-driven PDF-cache servicing pass (get_pdf_requests → download from
    Box → upload_filed_pdf) knows which Box file to fetch. `None` leaves the
    Worker's box_file_id column untouched on that path (it sends `box_file_id: null`).

    Raises `PortalAuthError` / `PortalRateLimitError` / `PortalTransportError`.
    """
    data = _request(
        "POST", base_url, MARK_FILED_PATH, token,
        json_body={
            "submission_uuid": submission_uuid,
            "box_link": box_link,
            "box_file_id": box_file_id,
        },
    )
    return bool(data.get("found"))


def mark_rejected(base_url: str, token: str, *, submission_uuid: str, reason: str) -> bool:
    """Post the terminal-reject receipt (M4): POST /api/internal/mark-rejected → returns `found`.

    Called by portal_poll after an HMAC failure so the bad row is flipped box_verified=-1 and
    stops being re-served by /pending every cycle. Control-plane write to OUR OWN Worker (outside
    the External Send Gate, like mark_filed). Idempotent. Raises the typed PortalTransportError
    hierarchy."""
    data = _request(
        "POST", base_url, MARK_REJECTED_PATH, token,
        json_body={"submission_uuid": submission_uuid, "reason": reason},
    )
    return bool(data.get("found"))


def push_jobs(base_url: str, token: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Full-replace job sync: POST /api/internal/sync → {ok, upserted, deactivated}.

    `jobs` is the COMPLETE ITS_Active_Jobs set, each row
    `{job_id, project_name, active}` (active 1/0). The Worker upserts each and
    deactivates any job_id absent from the set — so this is a full-replace sync,
    NOT an incremental add. The caller MUST refuse to push an empty list (an empty
    set would deactivate the whole dropdown); the Worker also rejects it (400
    empty_jobs). Idempotent: re-pushing the same set is a no-op, so a missed cycle
    self-heals.

    Like `mark_filed`, this is a control-plane receipt/write to our OWN Worker
    (D1 dropdown cache), NOT a customer-facing send — it is outside the External
    Send Gate (Invariant 1).

    Raises `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure).
    """
    return _request("POST", base_url, SYNC_PATH, token, json_body={"jobs": jobs})


# ---- Field-Ops job up-sync (P2.5 Slice 5 — the portal-as-writer mirror I/O) ----


def get_fieldops_pending_jobs(base_url: str, token: str) -> list[dict[str, Any]]:
    """Pull dirty portal jobs to mirror UP: GET /api/internal/fieldops/pending-jobs.

    Returns the `jobs` list verbatim — each a dict with the full SoR payload + version
    vector the `field_ops.fieldops_sync` daemon needs to find-or-create a row in BOTH
    Active-Jobs sheets: `job_id, project_name, lifecycle, address, stakeholder_name/email/
    phone, safety_contact_name/email, safety_cc (list), progress_contact_name/email,
    progress_cc (list), mirror_version, safety_mirrored_version, progress_mirrored_version,
    safety_row_id, progress_row_id, canonical_job_id`. The Worker caps the page at 200 rows
    server-side (no client limit param); the daemon drains across cycles.

    A control-plane read of OUR OWN Worker (bearer = the SEPARATE field-ops token
    `PORTAL_FIELDOPS_API_TOKEN`; privilege-separated from the poller's internal token), NOT a
    customer-facing send. Same typed-error contract as `get_pending` — `PortalAuthError`
    (401) / `PortalRateLimitError` (429/503 exhausted) / `PortalTransportError` (any other,
    incl. a non-object / missing-array body).
    """
    data = _request("GET", base_url, FIELDOPS_PENDING_JOBS_PATH, token)
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        raise PortalTransportError(
            f"GET {FIELDOPS_PENDING_JOBS_PATH} missing/invalid 'jobs' array "
            f"(got {type(jobs).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in jobs if isinstance(row, dict)]


def mark_fieldops_jobs_mirrored(
    base_url: str, token: str, updates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Per-sheet mirror commit point: POST /api/internal/fieldops/jobs-mark-mirrored.

    `updates` is a non-empty list of `{job_id, sheet: 'safety'|'progress', mirrored_version,
    row_id, canonical_job_id?}`. The Worker MONOTONICALLY advances only that sheet's
    watermark (MAX), caches the row_id, writes back `canonical_job_id` (SAFETY sheet only),
    and flips `sync_state` to `synced` once BOTH watermarks reach `mirror_version`. The
    daemon calls this ONCE PER SHEET (after that sheet's upsert confirms) so a progress
    failure leaves the job dirty with only the safety watermark advanced — the version-vector
    self-heal. The Worker rejects an EMPTY list (400) — the caller must never send one.

    Like `push_jobs`/`mark_filed`, a control-plane write to OUR OWN Worker (outside the
    External Send Gate, Invariant 1). Returns the Worker's `{ok, updated}` dict. Raises the
    typed `PortalTransportError` hierarchy on failure (a 400 invalid/empty body surfaces as
    `PortalTransportError`, never a silent return).
    """
    return _request(
        "POST", base_url, FIELDOPS_JOBS_MARK_MIRRORED_PATH, token,
        json_body={"updates": updates},
    )


def get_fieldops_pending_hours(base_url: str, token: str) -> list[dict[str, Any]]:
    """Pull unmirrored crew time entries to mirror UP: GET /api/internal/fieldops/hours-pending.

    Returns the `entries` list verbatim — each a dict with `uuid, job_id, project_name,
    work_started_at, work_ended_at, hours, notes, amends_uuid, created_at, personnel_name`
    (`personnel_name` is the DISPLAY name, never a username). The Worker caps the page at 200
    server-side (no client limit param); the daemon's hours pass drains across cycles.

    A control-plane read of OUR OWN Worker (bearer = the SEPARATE field-ops token
    `PORTAL_FIELDOPS_API_TOKEN`, same as `get_fieldops_pending_jobs`), NOT a customer send. Same
    typed-error contract: `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other, incl. a non-object / missing-array body).
    """
    data = _request("GET", base_url, FIELDOPS_HOURS_PENDING_PATH, token)
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise PortalTransportError(
            f"GET {FIELDOPS_HOURS_PENDING_PATH} missing/invalid 'entries' array "
            f"(got {type(entries).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in entries if isinstance(row, dict)]


def mark_fieldops_hours_mirrored(
    base_url: str, token: str, uuids: list[str]
) -> dict[str, Any]:
    """Hours-pass commit point: POST /api/internal/fieldops/hours-mark-mirrored.

    `uuids` is a non-empty list of the `time_entries.uuid`s whose per-job Hours Log row the daemon
    confirmed this cycle. The Worker stamps `mirrored_at = unixepoch()` for each IFF still NULL
    (idempotent — a replay/re-mirror is a no-op, never a regress) in one atomic batch + a summary
    audit row. The Worker rejects an EMPTY list (400) — the caller must never send one.

    Like `mark_fieldops_jobs_mirrored`, a control-plane write to OUR OWN Worker (outside the
    External Send Gate, Invariant 1). Returns the Worker's `{ok, updated}` dict; raises the typed
    `PortalTransportError` hierarchy on failure.
    """
    return _request(
        "POST", base_url, FIELDOPS_HOURS_MARK_MIRRORED_PATH, token,
        json_body={"uuids": uuids},
    )


def get_fieldops_equipment_snapshot(base_url: str, token: str) -> list[dict[str, Any]]:
    """Pull the CURRENT on-active-job equipment snapshot: GET
    /api/internal/fieldops/equipment-snapshot (P7 Slice 2, Equipment Status & Location).

    Returns the `equipment` list verbatim — each a dict with `equipment_id, job_id,
    project_name, name, kind, identifier, status, status_note, status_changed_at,
    location_label, lat, lon, read_at, recorded_at`. This is a SNAPSHOT (the live
    on-active-job state re-projected every cycle), NOT an event drain: there is no
    watermark and no mark-mirrored companion. The Worker returns the complete set
    (uncapped — the daemon needs the full snapshot to compute retire-off-job).

    A control-plane read of OUR OWN Worker (bearer = the SEPARATE field-ops token
    `PORTAL_FIELDOPS_API_TOKEN`, same as `get_fieldops_pending_hours`), NOT a customer send.
    Same typed-error contract: `PortalAuthError` (401) / `PortalRateLimitError` (429/503
    exhausted) / `PortalTransportError` (any other, incl. a non-object / missing-array body).
    """
    data = _request("GET", base_url, FIELDOPS_EQUIPMENT_SNAPSHOT_PATH, token)
    equipment = data.get("equipment")
    if not isinstance(equipment, list):
        raise PortalTransportError(
            f"GET {FIELDOPS_EQUIPMENT_SNAPSHOT_PATH} missing/invalid 'equipment' array "
            f"(got {type(equipment).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in equipment if isinstance(row, dict)]


# ---- Request-driven PDF cache (PR-4 Part A — the Mac PDF-servicing pass I/O) ----


def get_pdf_requests(
    base_url: str, token: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Pull serviceable PDF-cache requests: GET /api/internal/pdf-requests.

    Each row is a dict `{submission_uuid, box_file_id, form_code, work_date}` for a
    submission the user asked to "make available for download" that is filed
    (box_file_id set) but not yet cached. The Mac pass downloads the Box file by
    `box_file_id`, base64-chunks it, and POSTs each chunk via `upload_filed_pdf`.

    Returns the `pdf_requests` list verbatim (dict rows only). Rows are control-plane
    reads of OUR OWN Worker. Same typed-error contract as `get_pending` —
    `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure, incl. a non-object / missing-array body).
    """
    data = _request(
        "GET", base_url, PDF_REQUESTS_PATH, token, params={"limit": limit}
    )
    pdf_requests = data.get("pdf_requests")
    if not isinstance(pdf_requests, list):
        raise PortalTransportError(
            f"GET {PDF_REQUESTS_PATH} missing/invalid 'pdf_requests' array "
            f"(got {type(pdf_requests).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in pdf_requests if isinstance(row, dict)]


def upload_filed_pdf(
    base_url: str, token: str, *, submission_uuid: str,
    chunk_index: int, chunk_total: int, chunk_b64: str,
) -> dict[str, Any]:
    """Upload one base64 PDF chunk: POST /api/internal/filed-pdf → the ack dict.

    The compiled PDF rides as base64 text inside the JSON body (mirroring the photo
    wire) because `_request` is JSON-only — there is NO raw-binary/multipart path.
    Chunked because a full PDF + base64 inflation can exceed D1's per-row ceiling;
    the Worker reassembles by (submission_uuid, chunk_index) and flips the row to
    ready once `chunk_total` chunks have arrived. Idempotent per chunk
    (INSERT OR REPLACE), so a re-serviced row after a lost ack is a no-op.

    Returns the Worker's ack dict (e.g. `{ok, ready, stored, received}`) verbatim —
    NEVER interpolate `chunk_b64` into a log or error (never log PDF bytes).

    A control-plane write to OUR OWN Worker (outside the External Send Gate,
    Invariant 1 — like `mark_filed`). Raises the typed `PortalTransportError`
    hierarchy on failure (an invalid-chunk 400 surfaces as `PortalTransportError`,
    not a silent return).
    """
    return _request(
        "POST", base_url, FILED_PDF_PATH, token,
        json_body={
            "submission_uuid": submission_uuid,
            "chunk_index": chunk_index,
            "chunk_total": chunk_total,
            "chunk_b64": chunk_b64,
        },
    )


# ---- Checklist item-photo screening queue (G1 Slice 2 — the Mac screening-pass I/O) ----


def get_item_photos_pending(
    base_url: str, token: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Pull the unscreened checklist item-photo queue: GET /api/internal/item-photos/pending.

    Each row is a dict `{id, item_state_id, photo_json, hmac, created_at}` — one
    `item_photos` row (migration 0036) at `status='pending'`, oldest-first. Rows are
    UNTRUSTED until the caller verifies each row's `hmac` against the item-photo
    canonical string (`shared.portal_hmac.verify_item_photo` — the same
    verify-before-anything contract as `get_pending`); `photo_json` is the VERBATIM
    HMAC-covered string and must never be re-serialized before verification.

    A control-plane read of OUR OWN Worker (bearer = the poller's
    `PORTAL_INTERNAL_API_TOKEN` tier — same privilege class as `get_pending`), NOT a
    customer-facing send. Same typed-error contract as `get_pending` —
    `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure, incl. a non-object / missing-array body).
    """
    data = _request(
        "GET", base_url, ITEM_PHOTOS_PENDING_PATH, token, params={"limit": limit}
    )
    item_photos = data.get("item_photos")
    if not isinstance(item_photos, list):
        raise PortalTransportError(
            f"GET {ITEM_PHOTOS_PENDING_PATH} missing/invalid 'item_photos' array "
            f"(got {type(item_photos).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in item_photos if isinstance(row, dict)]


def post_item_photo_result(
    base_url: str, token: str, *, photo_id: int, status: str,
    box_file_id: str | None = None, detail: str | None = None,
) -> bool:
    """Post one screening disposition: POST /api/internal/item-photos/:id/result → `found`.

    `status` is `'clean'` (MUST carry `box_file_id` — the Box record already exists;
    the Worker 400s a clean result without it) or `'refused'` (MUST NOT carry
    `box_file_id`; optional `detail` rides the audit row — the machine reason, NEVER
    photo bytes). The Worker applies the disposition in ONE atomic batch (W4):
    `item_photos.status` flip + **photo_json NULLed (delete-on-screen — the bytes
    leave D1)** + `checklist_item_states.photo_ref` → `'<status>:<id>'` + audit row.

    Idempotent: `found=False` means the row was already screened (a re-post after a
    lost ack) or no longer exists — the caller treats it as benign, exactly like
    `mark_filed`. A control-plane write to OUR OWN Worker (outside the External Send
    Gate, Invariant 1). Raises the typed `PortalTransportError` hierarchy on failure
    (an invalid-result 400 surfaces as `PortalTransportError`, never a silent return).
    """
    body: dict[str, Any] = {"status": status}
    if box_file_id is not None:
        body["box_file_id"] = box_file_id
    if detail is not None:
        body["detail"] = detail
    data = _request(
        "POST", base_url,
        ITEM_PHOTO_RESULT_PATH_TEMPLATE.format(photo_id=int(photo_id)), token,
        json_body=body,
    )
    return bool(data.get("found"))


# ---- Daily-pool photo screening queue (DR-photo-pool Slice 2 — the Mac pass I/O) ----


def get_daily_photos_pending(
    base_url: str, token: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Pull the unscreened daily-pool photo queue: GET /api/internal/daily-photos/pending.

    Each row is a dict `{id, job_id, work_date, photo_json, hmac, created_at}` — one
    `daily_photo_pool` row (migration 0037) at `status='pending'`, oldest-first
    (claimed AND unclaimed alike — a claim changes ownership, not screening need).
    Rows are UNTRUSTED until the caller verifies each row's `hmac` against the
    daily-photo canonical string (`shared.portal_hmac.verify_daily_photo` — the same
    verify-before-anything contract as `get_pending`); `photo_json` is the VERBATIM
    HMAC-covered string and must never be re-serialized before verification.

    A control-plane read of OUR OWN Worker (bearer = the poller's
    `PORTAL_INTERNAL_API_TOKEN` tier — same privilege class as `get_pending`), NOT a
    customer-facing send. Same typed-error contract as `get_pending` —
    `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure, incl. a non-object / missing-array body).
    """
    data = _request(
        "GET", base_url, DAILY_PHOTOS_PENDING_PATH, token, params={"limit": limit}
    )
    daily_photos = data.get("daily_photos")
    if not isinstance(daily_photos, list):
        raise PortalTransportError(
            f"GET {DAILY_PHOTOS_PENDING_PATH} missing/invalid 'daily_photos' array "
            f"(got {type(daily_photos).__name__})"
        )
    # Defensive: keep only dict rows; a non-dict element is malformed transport.
    return [row for row in daily_photos if isinstance(row, dict)]


def post_daily_photo_result(
    base_url: str, token: str, *, photo_id: int, status: str,
    box_file_id: str | None = None, detail: str | None = None,
) -> bool:
    """Post one screening disposition: POST /api/internal/daily-photos/:id/result → `found`.

    `status` is `'clean'` (MUST carry `box_file_id` — the Box record already exists;
    the Worker 400s a clean result without it) or `'refused'` (MUST NOT carry
    `box_file_id`; optional `detail` rides the audit row — the machine reason, NEVER
    photo bytes). The Worker applies the disposition in ONE atomic batch (W4):
    `daily_photo_pool.status` flip + **photo_json NULLed (delete-on-screen — the
    bytes leave D1)** + `box_file_id` + `screened_at` + the changes()-gated audit
    row. Unlike the item-photo twin there is NO sibling ref flip — pool rows
    self-describe their status (the SPA chips + the /pending claim manifest read it).

    Idempotent: `found=False` means the row was already screened (a re-post after a
    lost ack) or no longer exists — the caller treats it as benign, exactly like
    `mark_filed`. A control-plane write to OUR OWN Worker (outside the External Send
    Gate, Invariant 1). Raises the typed `PortalTransportError` hierarchy on failure
    (an invalid-result 400 surfaces as `PortalTransportError`, never a silent return).
    """
    body: dict[str, Any] = {"status": status}
    if box_file_id is not None:
        body["box_file_id"] = box_file_id
    if detail is not None:
        body["detail"] = detail
    data = _request(
        "POST", base_url,
        DAILY_PHOTO_RESULT_PATH_TEMPLATE.format(photo_id=int(photo_id)), token,
        json_body=body,
    )
    return bool(data.get("found"))


# ---- Progress rollup numbers (P6 — the progress weekly-compile's read I/O) ----


def get_progress_rollup(
    base_url: str, token: str, *, job_id: str, week_from: int, week_to: int
) -> dict[str, Any]:
    """Fetch the field-ops rollup aggregate for one job-week: GET /api/internal/progress-rollup.

    Reads the send-free Worker route (P6) that aggregates the structured field-ops D1 tables
    for `job_id` over the Sat→Fri epoch window `[week_from, week_to)`: labor hours
    (`SUM(time_entries.hours)`, amend-collapsed), the DISTINCT equipment on site
    (`equipment_location`), and the open-tasks count (`task_assignments status != 'done'`).
    Returns the Worker's JSON dict verbatim — `{job_id, window:{from,to}, labor_hours,
    equipment:[{name,kind}], open_tasks, materials, generated_at}` — for
    `form_pdf.render_progress_rollup` to lay out. There is NO progress-% (operator decision
    2026-06-30); `materials` is a null M2 placeholder.

    A control-plane READ of OUR OWN Worker (bearer = the poller's `PORTAL_INTERNAL_API_TOKEN`;
    same privilege class as `get_pending`), NOT a customer-facing send — outside the External
    Send Gate (Invariant 1). The Worker computes graceful zeros on empty data, so an
    activity-free week returns `labor_hours=0` / `equipment=[]` / `open_tasks=0`, never an error.

    Typed-shape guard: a malformed body (missing/invalid `labor_hours` / `equipment` /
    `open_tasks`) raises `PortalTransportError` — the daemon's rollup fence then falls back to
    a no-rollup packet (never a wrong number). Same typed-error contract as `get_pending` —
    `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) / `PortalTransportError`
    (any other failure).
    """
    data = _request(
        "GET", base_url, PROGRESS_ROLLUP_PATH, token,
        params={"job_id": job_id, "from": week_from, "to": week_to},
    )
    labor_hours = data.get("labor_hours")
    # bool is an int subclass — exclude it so a stray `true`/`false` is not read as a number.
    if not isinstance(labor_hours, (int, float)) or isinstance(labor_hours, bool):
        raise PortalTransportError(
            f"GET {PROGRESS_ROLLUP_PATH} missing/invalid 'labor_hours' "
            f"(got {type(labor_hours).__name__})"
        )
    if not isinstance(data.get("equipment"), list):
        raise PortalTransportError(
            f"GET {PROGRESS_ROLLUP_PATH} missing/invalid 'equipment' array "
            f"(got {type(data.get('equipment')).__name__})"
        )
    open_tasks = data.get("open_tasks")
    if not isinstance(open_tasks, int) or isinstance(open_tasks, bool):
        raise PortalTransportError(
            f"GET {PROGRESS_ROLLUP_PATH} missing/invalid 'open_tasks' "
            f"(got {type(open_tasks).__name__})"
        )
    return data


# ---- D1 prune observability (GS2 — the watchdog Check V read I/O) ----


def get_prune_status(base_url: str, token: str) -> dict[str, Any] | None:
    """Fetch the D1 prune heartbeat: GET /api/internal/prune-status (GS2).

    Reads the one-row `prune_meta` record the Worker's scheduled daily prune UPSERTs
    after every run (migration 0033) — `{last_run_at, db_size_bytes, size_warn,
    counters, failed_stages}`. Returns the `prune` dict verbatim, or ``None`` when the
    Worker reports no record yet (`prune: null` — the prune has never run since the
    migration; the caller treats that as its own signal, NOT as healthy).

    A control-plane READ of OUR OWN Worker (bearer = the poller's
    `PORTAL_INTERNAL_API_TOKEN` tier, Keychain `ITS_PORTAL_INTERNAL_TOKEN` — same
    privilege class as `get_pending`), NOT a customer-facing send — outside the
    External Send Gate (Invariant 1). Read-only and bounded (single row by schema).

    Consumed by `scripts/watchdog.py` Check V: WARN when `last_run_at` is >48h stale,
    CRITICAL on `failed_stages` non-empty or `db_size_bytes` over the 6 GB threshold.

    Raises `PortalAuthError` (401) / `PortalRateLimitError` (429/503 exhausted) /
    `PortalTransportError` (any other failure, incl. a non-object `prune` value).
    """
    data = _request("GET", base_url, PRUNE_STATUS_PATH, token)
    prune = data.get("prune")
    if prune is None:
        return None
    if not isinstance(prune, dict):
        raise PortalTransportError(
            f"GET {PRUNE_STATUS_PATH} missing/invalid 'prune' object "
            f"(got {type(prune).__name__})"
        )
    return prune


# ---- Form-editor publish pipeline (slice 3b — the Mac publish daemon's queue I/O) ----


def get_publish_pending(base_url: str, token: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Claimable publish requests: GET /api/internal/publish/pending (queued + unleased,
    oldest-first), each row a dict incl. `definition_json`. Same typed-error contract as
    get_pending."""
    data = _request("GET", base_url, PUBLISH_PENDING_PATH, token, params={"limit": limit})
    pending = data.get("pending")
    if not isinstance(pending, list):
        raise PortalTransportError(
            f"GET {PUBLISH_PENDING_PATH} missing/invalid 'pending' (got {type(pending).__name__})"
        )
    return [row for row in pending if isinstance(row, dict)]


def claim_publish(
    base_url: str, token: str, *, request_id: int, lease_owner: str
) -> dict[str, Any] | None:
    """Atomically lease a publish request: POST /api/internal/publish/claim.

    Returns the claimed row (incl. `definition_json`) on success, or None if it was
    already leased / no longer queued (`claimed=false`) — a benign concurrent-claim
    outcome the daemon skips."""
    data = _request(
        "POST", base_url, PUBLISH_CLAIM_PATH, token,
        json_body={"id": request_id, "lease_owner": lease_owner},
    )
    if not data.get("claimed"):
        return None
    request = data.get("request")
    return request if isinstance(request, dict) else None


def stamp_publish(
    base_url: str, token: str, *, request_id: int, status: str,
    failed_stage: str | None = None, failure_reason: str | None = None,
) -> bool:
    """Advance a publish request's state machine: POST /api/internal/publish/stamp.

    Returns `found`. failed_stage/failure_reason are sent only for status='failed'
    (the Worker ignores them otherwise)."""
    body: dict[str, Any] = {"id": request_id, "status": status}
    if failed_stage is not None:
        body["failed_stage"] = failed_stage
    if failure_reason is not None:
        body["failure_reason"] = failure_reason
    data = _request("POST", base_url, PUBLISH_STAMP_PATH, token, json_body=body)
    return bool(data.get("found"))


def get_publish_stuck(base_url: str, token: str, *, older_than: int) -> list[dict[str, Any]]:
    """Non-terminal publish requests whose updated_at is older than `older_than` seconds — the
    stale-row sweep input (a daemon that claimed-then-died, or a stalled stage). Same typed-error
    contract as get_publish_pending; rows are control-plane reads of OUR OWN Worker."""
    data = _request("GET", base_url, PUBLISH_STUCK_PATH, token, params={"older_than": older_than})
    stuck = data.get("stuck")
    if not isinstance(stuck, list):
        raise PortalTransportError(
            f"GET {PUBLISH_STUCK_PATH} missing/invalid 'stuck' (got {type(stuck).__name__})"
        )
    return [row for row in stuck if isinstance(row, dict)]


def admin_request(
    base_url: str, token: str, method: str, path: str, *,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Issue one bearer-authed admin request to `/api/internal/admin/*` → (status, json).

    The operator-only control-plane leg (user provision / reset / disable / enable /
    list) to OUR OWN Worker — NOT a customer-facing send (outside the External Send
    Gate, Invariant 1). The bearer is the Mac Keychain `ITS_PORTAL_ADMIN_TOKEN`,
    mirroring the Worker's `PORTAL_ADMIN_API_TOKEN` (SEPARATE from the poller's
    internal token — privilege separation). Retries 429/503 + network failures like
    the other portal_client calls.

    A 401 raises `PortalAuthError` (the admin bearer is wrong/missing — a real
    misconfig). Application statuses (200/201/400/404/409) are RETURNED, not raised,
    so the CLI maps them to operator-readable outcomes (created / exists / not_found
    / invalid). A non-JSON body yields `{}` — the caller treats the status as truth.
    """
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last_detail = ""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method, url, json=json_body, headers=headers, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            if attempt == MAX_RETRIES - 1:
                raise PortalTransportError(
                    f"{method} {path} network failure after {MAX_RETRIES} attempts: {last_detail}"
                ) from exc
            time.sleep(float(2**attempt))
            continue
        if response.status_code == 401:
            raise PortalAuthError(f"{method} {path} unauthorized (401) — admin bearer rejected")
        if response.status_code in (429, 503):
            if attempt == MAX_RETRIES - 1:
                raise PortalRateLimitError(
                    f"{method} {path} throttled/unavailable after {MAX_RETRIES} attempts"
                )
            delay = _parse_retry_after(response.headers.get("Retry-After"))
            time.sleep(delay if delay is not None else float(2**attempt))
            continue
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data if isinstance(data, dict) else {}
    # Unreachable: every loop branch returns or raises on the last attempt.
    raise PortalTransportError(f"{method} {path} exhausted retries: {last_detail}")
