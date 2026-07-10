"""ACT-surface auth for the operator dashboard (WS2 D1-2 + D1-3).

Controls (all BUILT here — D1-1 shipped none):

1. Operator PIN (Class A) — read from Keychain `ITS_OPERATOR_PIN`, constant-time
   (SHA-256 both sides, then hmac.compare_digest, no length oracle). FAILS
   CLOSED: a missing/locked keychain DENIES. Brute-force throttled → 60s lockout
   after 5 fails → CRITICAL page.

2. Elevated confirm (D1-3, Class B/C) — the "weight" for actions that change
   trust, identity, credentials, or the global brake: the operator RE-ENTERS the
   PIN AND types an exact confirmation phrase (the target's name). Both must
   match; fail-closed. It SHARES the PIN throttle bucket (same secret → one
   guess budget across both ceremonies, not doubled).

3. Origin/Referer allowlist — CSRF defense-in-depth on top of the PIN.

Not gated behind @require_active (works while PAUSED/MAINTENANCE).
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

# Brute-force throttle. Provision a STRONG PIN before any Tailscale exposure.
_MAX_PIN_FAILS = 5
_LOCKOUT_SECONDS = 60.0
_FAIL_SLEEP_SECONDS = 0.5  # monkeypatched to 0 in tests


class AuthError(Exception):
    """Base for an ACT-surface auth denial (the message is operator-facing)."""


class PinError(AuthError):
    """The PIN / elevated confirmation is missing, wrong, or unreadable (fail-closed)."""


class OriginError(AuthError):
    """The request Origin/Referer is not on the allowlist (CSRF defense)."""


class _Throttle:
    """A per-ceremony failed-attempt counter with a temporary lockout."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self.count = 0.0
        self.locked_until = 0.0

    def check(self) -> None:
        with self._lock:
            if self.locked_until > time.monotonic():
                raise PinError("too many failed attempts — temporarily locked out; wait and retry")

    def reset(self) -> None:
        with self._lock:
            self.count = 0.0
            self.locked_until = 0.0

    def record_failure(self) -> bool:
        with self._lock:
            self.count += 1
            if self.count >= _MAX_PIN_FAILS:
                self.locked_until = time.monotonic() + _LOCKOUT_SECONDS
                return True
        return False


# ONE shared throttle guards both the Class-A PIN and the elevated re-PIN — they
# verify the SAME secret, so the failed-guess budget is SHARED across both
# ceremonies (5 total, not 5-per-route).
_pin_throttle = _Throttle("pin")


def reset_pin_throttle() -> None:
    _pin_throttle.reset()


def _alert_lockout(bucket: str) -> None:
    try:
        import shared.error_log as el

        el.log(
            el.Severity.CRITICAL,
            "operator_dashboard.config_editor",
            f"config editor {bucket} lockout: {_MAX_PIN_FAILS}+ consecutive failed attempts — possible brute-force",
            error_code="config_pin_lockout",
        )
    except Exception:
        pass


def _read_stored_pin() -> str:
    try:
        from shared.keychain import KeychainError, KeychainLockedError, get_secret
    except Exception as exc:  # keychain module unavailable → deny (fail closed)
        raise PinError("keychain unavailable — denying") from exc
    try:
        return get_secret(PIN_KEYCHAIN_KEY)
    except KeychainLockedError as exc:
        raise PinError("keychain is locked — run `security unlock-keychain`, then retry") from exc
    except KeychainError as exc:
        raise PinError("operator PIN not provisioned (ITS_OPERATOR_PIN) — denying") from exc


def _pin_matches(submitted: str, stored: str) -> bool:
    a = hashlib.sha256(submitted.encode("utf-8")).digest()
    b = hashlib.sha256(stored.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def _verify_pin_throttled(submitted: str | None, throttle: _Throttle) -> None:
    if not submitted:
        raise PinError("PIN required")
    throttle.check()  # refuse while locked out
    stored = _read_stored_pin()
    if _pin_matches(submitted, stored):
        throttle.reset()
        return
    time.sleep(_FAIL_SLEEP_SECONDS)
    if throttle.record_failure():
        _alert_lockout(throttle.name)
    raise PinError("incorrect PIN")


def verify_pin(submitted: str | None) -> None:
    """Class-A gate: raise PinError unless `submitted` matches the operator PIN."""
    _verify_pin_throttled(submitted, _pin_throttle)


def verify_elevated(pin: str | None, typed_confirm: str | None, expected: str) -> None:
    """Elevated-confirm ceremony (Class B/C): the operator RE-ENTERS the PIN AND
    types `expected` exactly. Both must match — fail-closed. Separate throttle."""
    # 1. the typed confirmation must exactly match the target (constant-time)
    matches_confirm = bool(typed_confirm) and hmac.compare_digest(
        (typed_confirm or "").strip().encode("utf-8"), expected.strip().encode("utf-8")
    )
    if not matches_confirm:
        raise PinError(f"type the exact confirmation phrase to proceed: {expected!r}")
    # 2. re-enter the PIN (SHARED throttle bucket — one guess budget across ceremonies)
    _verify_pin_throttled(pin, _pin_throttle)


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
