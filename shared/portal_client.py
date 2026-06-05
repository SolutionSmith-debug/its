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


def mark_filed(base_url: str, token: str, *, submission_uuid: str, box_link: str) -> bool:
    """Post the receipt: POST /api/internal/mark-filed → returns `found`.

    Called ONLY after intake has filed the submission to Box + Smartsheet
    (box_verified flips to 1 so the Worker stops serving the row). Idempotent —
    a second call for an already-filed UUID returns `found=True` with no effect.
    `found=False` means the Worker has no row for that UUID (already drained by a
    concurrent actor, or an unknown UUID); the caller treats it as benign.

    Raises `PortalAuthError` / `PortalRateLimitError` / `PortalTransportError`.
    """
    data = _request(
        "POST", base_url, MARK_FILED_PATH, token,
        json_body={"submission_uuid": submission_uuid, "box_link": box_link},
    )
    return bool(data.get("found"))
