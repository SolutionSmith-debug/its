"""Bounded-retry REST helper for the wipe/stand-up family's raw `requests` calls.

The family's dump/restore paths deliberately use raw REST (objectValue round-trip,
workspace loadAll trees) and so bypass BOTH retry layers the repo already has —
the Smartsheet SDK's built-in 429 backoff and `shared/smartsheet_client`'s
`_transient_retry`. A dump loop makes 100+ tight GETs against Smartsheet's
per-minute rate limit, so a transient 429/5xx mid-dump is realistic; before this
helper existed, `wipe_tenant.dump_workspace` classified one as "unreadable sheet"
and PROCEEDED TO DELETE — a fail-open data-loss path (2026-07-23 review finding).

Contract (the polarity is "false abort OK, false skip NOT"):

- TRANSIENT signatures — HTTP 429 (honoring a capped ``Retry-After``), 5xx, and
  ``requests`` timeout/connection errors — are retried with bounded exponential
  backoff. On budget exhaustion the LAST transient error PROPAGATES so callers
  fail CLOSED (the wipe's guard 4 aborts with nothing deleted; a standup stage
  fails with its resume hint).
- PERMANENT signatures (any other 4xx) are never retried. With the default
  ``raise_for_status=True`` they raise ``requests.HTTPError`` immediately;
  classification of permanent-vs-transient beyond that (e.g. "unreadable broken
  shell, safe to skip") is the CALLER's job via `is_permanent_read_failure`.
- ``raise_for_status=False`` returns any NON-transient response as-is for callers
  that inspect status codes themselves (the share-restore POST WARNs on an
  already-shared 4xx dup instead of failing the stage). Transient exhaustion
  STILL raises — that mode must never let a 429 masquerade as a caller-visible
  "share not restorable" WARN (it would silently narrow an F22 approver set).

Deliberately NOT applied to the wipe's DELETE calls: the partial-failure re-run
contract already covers those (absent objects skip on re-run).
"""
from __future__ import annotations

import time
from typing import Any

import requests  # type: ignore[import-untyped]

TRANSIENT_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = 2.0
# Cap both Retry-After and the exponential backoff — a hostile/absurd header
# must not stall an attended run for an hour.
MAX_SLEEP_SECONDS = 120.0

# Smartsheet errorCodes that mark a sheet as PERMANENTLY unreadable (the four
# zero-column ITS_Errors shells from the row-cap incident): 1006 Not Found,
# 1115 sheet in an unusable/invalid state.
PERMANENT_SMARTSHEET_ERROR_CODES: frozenset[int] = frozenset({1006, 1115})


def _retry_after_seconds(response: Any) -> float | None:
    """Parse a numeric Retry-After header (None when absent/unparseable)."""
    raw = getattr(response, "headers", {}).get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def request_with_retry(
    method: str,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    raise_for_status: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """One REST call with bounded retry on transient failures (see module doc)."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        delay: float | None = None
        try:
            r = requests.request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            label = type(exc).__name__
        else:
            if r.status_code not in TRANSIENT_STATUS:
                if raise_for_status:
                    r.raise_for_status()  # permanent 4xx raises HERE, first attempt
                return r
            if r.status_code == 429:
                delay = _retry_after_seconds(r)
            last_exc = requests.HTTPError(
                f"{r.status_code} transient error for url: {url}", response=r)
            label = f"HTTP {r.status_code}"
        if attempt < attempts:
            sleep = min(
                delay if delay is not None else backoff_seconds * (2 ** (attempt - 1)),
                MAX_SLEEP_SECONDS)
            print(f"[WARN] transient_rest_error: {label} on {method.upper()} {url} — "
                  f"retrying in {sleep:g}s (attempt {attempt}/{attempts})")
            time.sleep(sleep)
    assert last_exc is not None  # loop always ran at least once
    raise last_exc


def is_permanent_read_failure(exc: Exception) -> bool:
    """True when a dump-read failure is a PERMANENT signature — safe for
    `wipe_tenant.dump_workspace` to classify the sheet "unreadable" and proceed.

    Permanent = HTTP 404, or a Smartsheet body errorCode in
    `PERMANENT_SMARTSHEET_ERROR_CODES`. Everything else — an exhausted
    429/5xx, a timeout, a truncation RuntimeError — is transient-or-unknown
    and must ABORT the wipe (dump-before-delete, guard 4).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return False
    if getattr(response, "status_code", None) == 404:
        return True
    try:
        code = response.json().get("errorCode")
    except Exception:  # noqa: BLE001 — non-JSON body = not a Smartsheet signature
        return False
    return code in PERMANENT_SMARTSHEET_ERROR_CODES
