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
PUBLISH_PENDING_PATH = "/api/internal/publish/pending"
PUBLISH_CLAIM_PATH = "/api/internal/publish/claim"
PUBLISH_STAMP_PATH = "/api/internal/publish/stamp"
PUBLISH_STUCK_PATH = "/api/internal/publish/stuck"
FIELDOPS_PENDING_JOBS_PATH = "/api/internal/fieldops/pending-jobs"
FIELDOPS_JOBS_MARK_MIRRORED_PATH = "/api/internal/fieldops/jobs-mark-mirrored"
PROGRESS_ROLLUP_PATH = "/api/internal/progress-rollup"


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
