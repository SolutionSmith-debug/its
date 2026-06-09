"""Resend transactional-email client — out-of-band operator alerts.

Auth: API key from macOS Keychain. The keychain entry name is held in the
`ITS_Config` row `system.resend_api_keychain_key` (default `ITS_RESEND_API_KEY`
per the 2026-05-18 seed). The proven sandbox path is in
`scripts/smoke_test_resend.py`.

Capabilities exposed:
    send_alert(subject, body, *, to=None)

Purpose:
    Third leg of the Op Stds v11 §3 triple-fire CRITICAL alert path
    (Sentry + Smartsheet `ITS_Errors` + Resend). Wired into
    `shared.error_log._alert_critical`. NOT for customer-facing email —
    customer email goes through `shared.graph_client.send_mail` per
    Foundation Mission v8 Invariant 1.

Error model:
    Every failure raises a typed exception under `ResendError`. Callers
    decide whether to log, retry, or swallow — this module does not.

Retry: 429/503 with Retry-After honored, exponential backoff fallback,
cap `MAX_RETRIES`.

Sender configuration:
    Resend requires a verified sender domain. `DEFAULT_FROM` below is a
    placeholder. Before the smoke test can deliver, the operator must
    (a) add their Resend API key to Keychain under the name in
    `system.resend_api_keychain_key`, and (b) verify the sender domain
    in their Resend dashboard. Until both are done, smoke calls will
    surface a clear `ResendAuthError` or sender-verification error from
    Resend.
"""
from __future__ import annotations

import time
from typing import Any

import requests  # type: ignore[import-untyped]

from . import keychain

RESEND_BASE = "https://api.resend.com"
MAX_RETRIES = 3
# (connect, read) timeout — the CRITICAL operator-alert path must FAIL FAST, never hang on a
# half-open socket. A hung send (no timeout) would block the whole triple-fire CRITICAL path
# (graph_client documents the 88-min lock-starvation incident this class of bug caused), so
# the page about an outage would itself be lost to the outage.
REQUEST_TIMEOUT = (10.0, 30.0)

# Sender — Resend's sandbox `onboarding@resend.dev`, which is pre-verified
# on every Resend account and accepts any recipient. Right address for
# sandbox / dev / smoke testing. Swap to the operator's verified Resend
# domain at Phase 1.5 live-tenant cutover; the same `DEFAULT_FROM`
# constant is the only touchpoint.
DEFAULT_FROM = "onboarding@resend.dev"


class ResendError(Exception):
    """Base exception for all Resend failures."""


class ResendAuthError(ResendError):
    """API key invalid / missing / unauthorized for the sender domain (HTTP 401/403)."""


class ResendNotFoundError(ResendError):
    """HTTP 404 — endpoint, resource, or sender not found."""


class ResendRateLimitError(ResendError):
    """Resend returned 429 after the retry budget was exhausted."""


_api_key: str | None = None


def get_client() -> str:
    """Return the cached Resend API key, loading from Keychain on first call.

    Resend's REST API has no SDK client object — auth is per-request via a
    Bearer header. This function exists as the lazy-singleton seam that
    tests can override (`mocker.patch('shared.resend_client.get_client',
    return_value='test-key')`).
    """
    global _api_key
    if _api_key is None:
        _api_key = keychain.get_secret("ITS_RESEND_API_KEY")
    return _api_key


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After as seconds. None on unparseable input."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form is legal but rare; fall through to backoff.
        return None


def _extract_error_message(response: requests.Response) -> str:
    """Pull the human-readable error message out of a Resend error response."""
    try:
        body = response.json()
        # Resend's error shape: {"name": "missing_api_key", "message": "..."}
        return body.get("message") or response.text[:200]
    except ValueError:
        return response.text[:200]


def _check_response(response: requests.Response) -> requests.Response:
    code = response.status_code
    if 200 <= code < 300:
        return response

    msg = _extract_error_message(response)
    if code in (401, 403):
        raise ResendAuthError(f"HTTP {code}: {msg}")
    if code == 404:
        raise ResendNotFoundError(f"HTTP 404: {msg}")
    if code == 429:
        raise ResendRateLimitError(f"HTTP 429: {msg}")
    raise ResendError(f"HTTP {code}: {msg}")


def _request(method: str, path: str, *, json_body: dict[str, Any]) -> requests.Response:
    """Execute a Resend request with retry on 429/503 (exponential backoff)."""
    url = RESEND_BASE + path
    headers = {
        "Authorization": f"Bearer {get_client()}",
        "Content-Type": "application/json",
    }

    response: requests.Response | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method, url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            # Network failure / timeout: FAIL FAST. Translate to ResendError so the caller's
            # broad-except isolates it, and do NOT retry — a hung/unreachable host must not be
            # amplified into 3× the wait on the alert path (the durable file + ITS_Errors legs
            # of the triple-fire still land).
            raise ResendError(f"request failed: {type(exc).__name__}: {exc}") from exc
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


def send_alert(subject: str, body: str, *, to: str | None = None) -> None:
    """Send one transactional alert email via Resend.

    `to` defaults to `system.operator_email` from ITS_Config when omitted,
    falling back to `defaults.OPERATOR_EMAIL_FALLBACK` when that read is
    unavailable (see below). Plain-text body only (no HTML).

    Raises `ResendError` (or a subclass) on any failure. Callers that need
    failure isolation (e.g., `error_log._alert_critical`) must catch
    `ResendError` and decide on a fallback.
    """
    if to is None:
        # Lazy import to keep this module's boot-time deps minimal and
        # avoid a circular if smartsheet_client ever needs to log via
        # error_log → alert path.
        from . import defaults, smartsheet_client
        try:
            to = smartsheet_client.get_setting(
                "system.operator_email", workstream="global"
            )
        except smartsheet_client.SmartsheetError:
            # The ITS_Config read is a GUARDED Smartsheet call, so it
            # short-circuits when the circuit breaker is OPEN — i.e. during
            # exactly the outage the prolonged-open CRITICAL page must reach
            # the operator about. Fall back to the build-time recipient so the
            # page still delivers (Resend is HTTP, unaffected by the outage).
            to = None
        if not to:
            to = defaults.OPERATOR_EMAIL_FALLBACK
        if not to:
            raise ResendError(
                "no operator recipient: system.operator_email unreadable and "
                "defaults.OPERATOR_EMAIL_FALLBACK unset; pass `to=` explicitly"
            )

    payload: dict[str, Any] = {
        "from": DEFAULT_FROM,
        "to": to,
        "subject": subject,
        "text": body,
    }
    _request("POST", "/emails", json_body=payload)
