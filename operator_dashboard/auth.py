"""ACT-surface auth for the operator dashboard (WS2 D1-2).

Two controls, both BUILT here (D1-1 shipped none; no reusable Python prior art
existed — only the TS Worker patterns to mirror):

1. Operator PIN — the primary control. Read from Keychain `ITS_OPERATOR_PIN`,
   compared in constant time (SHA-256 both sides, then hmac.compare_digest, so
   there's no length oracle). It FAILS CLOSED: a missing or locked keychain
   DENIES the action (unlike the fail-OPEN kill switch — that is an operator
   convenience, this is a security boundary). The PIN is also the real CSRF
   defense: a cross-origin page cannot forge it.

2. Origin/Referer allowlist — defense-in-depth on top of the PIN. A browser
   request carrying an Origin/Referer NOT on the allowlist is refused (the CSRF
   case). A non-browser client (curl; no Origin AND no Referer) is allowed
   through to the PIN check. The allowlist is localhost:PORT plus any origins in
   the `ITS_DASH_ALLOWED_ORIGINS` env var (comma-separated — set it to your
   Tailscale-served origin, e.g. https://<host>.<tailnet>.ts.net).

This surface must work while the system is PAUSED/MAINTENANCE, so it is NOT
gated behind @require_active.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from urllib.parse import urlsplit

from operator_dashboard.config import PORT

PIN_KEYCHAIN_KEY = "ITS_OPERATOR_PIN"
ALLOWED_ORIGINS_ENV = "ITS_DASH_ALLOWED_ORIGINS"

# Brute-force throttle. The PIN is the primary control and, on a Tailscale
# deployment, any tailnet device can POST — so a wrong-PIN guess is rate-limited
# and, after a burst, the endpoint locks out and pages the operator (CRITICAL).
# Provision a STRONG PIN (not a 4-digit) before any Tailscale exposure.
_MAX_PIN_FAILS = 5
_LOCKOUT_SECONDS = 60.0
_FAIL_SLEEP_SECONDS = 0.5  # monkeypatched to 0 in tests
_throttle_lock = threading.Lock()
_pin_fails: dict[str, float] = {"count": 0.0, "locked_until": 0.0}


def reset_pin_throttle() -> None:
    with _throttle_lock:
        _pin_fails["count"] = 0.0
        _pin_fails["locked_until"] = 0.0


def _alert_lockout() -> None:
    # Best-effort CRITICAL page on lockout (possible brute-force).
    try:
        import shared.error_log as el

        el.log(
            el.Severity.CRITICAL,
            "operator_dashboard.config_editor",
            f"config editor PIN lockout: {_MAX_PIN_FAILS}+ consecutive failed attempts "
            "on /act/config — possible brute-force",
            error_code="config_pin_lockout",
        )
    except Exception:
        pass


class AuthError(Exception):
    """Base for an ACT-surface auth denial (the message is operator-facing)."""


class PinError(AuthError):
    """The operator PIN is missing, wrong, or cannot be read (fail-closed)."""


class OriginError(AuthError):
    """The request Origin/Referer is not on the allowlist (CSRF defense)."""


def verify_pin(submitted: str | None) -> None:
    """Raise PinError unless `submitted` matches the provisioned operator PIN.

    Fail-closed: an absent/locked keychain or unprovisioned PIN DENIES.
    """
    if not submitted:
        raise PinError("PIN required")
    # refuse while locked out (anti-brute-force)
    with _throttle_lock:
        if _pin_fails["locked_until"] > time.monotonic():
            raise PinError("too many failed attempts — temporarily locked out; wait and retry")
    try:
        from shared.keychain import KeychainError, KeychainLockedError, get_secret
    except Exception as exc:  # keychain module unavailable → deny (fail closed)
        raise PinError("keychain unavailable — denying") from exc
    try:
        stored = get_secret(PIN_KEYCHAIN_KEY)
    except KeychainLockedError as exc:
        raise PinError("keychain is locked — run `security unlock-keychain`, then retry") from exc
    except KeychainError as exc:
        # No PIN provisioned → deny (fail CLOSED). Provision with:
        #   security add-generic-password -a "$USER" -s ITS_OPERATOR_PIN -w
        raise PinError("operator PIN not provisioned (ITS_OPERATOR_PIN) — denying") from exc
    submitted_digest = hashlib.sha256(submitted.encode("utf-8")).digest()
    stored_digest = hashlib.sha256(stored.encode("utf-8")).digest()
    if hmac.compare_digest(submitted_digest, stored_digest):
        reset_pin_throttle()  # a correct PIN clears the failure streak
        return
    # wrong PIN — rate-limit, count, and lock out + page on a burst
    time.sleep(_FAIL_SLEEP_SECONDS)
    tripped = False
    with _throttle_lock:
        _pin_fails["count"] += 1
        if _pin_fails["count"] >= _MAX_PIN_FAILS:
            _pin_fails["locked_until"] = time.monotonic() + _LOCKOUT_SECONDS
            tripped = True
    if tripped:
        _alert_lockout()
    raise PinError("incorrect PIN")


def allowed_origins() -> set[str]:
    origins = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
    for raw in os.environ.get(ALLOWED_ORIGINS_ENV, "").split(","):
        origin = raw.strip()
        if origin:
            origins.add(origin)
    return origins


def check_origin(origin: str | None, referer: str | None) -> None:
    """Raise OriginError if a browser-supplied Origin/Referer is off-allowlist.

    A request with NEITHER header is a non-browser client (curl/script) and is
    allowed through — the PIN is still required and is the real CSRF barrier.
    """
    if origin is None and referer is None:
        return
    candidate = origin
    if candidate is None and referer is not None:
        parsed = urlsplit(referer)
        candidate = f"{parsed.scheme}://{parsed.netloc}"
    if candidate not in allowed_origins():
        raise OriginError(f"origin {candidate!r} is not allowed")
