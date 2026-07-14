"""Change the operator PIN from the dashboard (WS2) — a CURRENT-PIN-gated CHANGE,
NOT a recovery.

A LOST / forgotten PIN still recovers ONLY from the terminal:
    security add-generic-password -U -a "$USER" -s ITS_OPERATOR_PIN -w
The Keychain (= local-machine access) stays the root of trust; initial
provisioning is terminal-only. This flow only lets the operator ROTATE the PIN
when they already know the current one (the router verifies it before this runs).

Hard rules (enforced here + proven by tests):
- The new PIN is entered TWICE and both must match — a typo guard, because the
  write is write-only (never read back) and a mistyped PIN would lock the
  operator out (recoverable only from the terminal).
- Minimum strength enforced (>= MIN_PIN_LEN and not all-digits) — the runbook
  demands a STRONG PIN, never a 4-digit.
- On success: set_secret write-through + RESET the lockout throttle (prior failed
  attempts must not carry into the new PIN) + audit `config_pin_changed`.
- NEVER reads a PIN back — write-only (a source test asserts it), and never
  logs / echoes / persists the value except to Keychain.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from operator_dashboard.auth import PIN_KEYCHAIN_KEY

MIN_PIN_LEN = 8


@dataclass
class PinChangeOutcome:
    kind: str  # changed | rejected | error (also CSS class + test assertion)
    message: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def _validate_new_pin(new_pin: str, confirm: str) -> str:
    """Return the validated new PIN (exact, NOT stripped — it must equal what the
    operator later types in the PIN field) or raise ValueError with the reason."""
    if new_pin != confirm:
        raise ValueError("the two new-PIN entries do not match — re-enter them")
    if len(new_pin) < MIN_PIN_LEN:
        raise ValueError(f"PIN must be at least {MIN_PIN_LEN} characters (a strong passphrase, not a 4-digit)")
    if new_pin.isdigit():
        raise ValueError("PIN must not be all digits — use a strong passphrase")
    return new_pin


def change_pin(new_pin: str, confirm: str, operator: str) -> PinChangeOutcome:
    """Write a new operator PIN. The router has already verified the CURRENT PIN
    (authority). Write-only: this never reads a PIN back."""
    try:
        validated = _validate_new_pin(new_pin, confirm)
    except ValueError as exc:
        return PinChangeOutcome("rejected", str(exc))
    kc = _load("shared.keychain")
    try:
        kc.set_secret(PIN_KEYCHAIN_KEY, validated)  # write-through; -U update-in-place IS the change
    except Exception as exc:  # NEVER include the value in the message
        return PinChangeOutcome("error", f"keychain write failed: {type(exc).__name__}")
    # Reset the shared lockout throttle so prior failed attempts don't carry over
    # into the new PIN (otherwise a lockout window could still deny the new PIN).
    try:
        _load("operator_dashboard.auth").reset_pin_throttle()
    except Exception:
        pass
    _audit(operator)
    return PinChangeOutcome(
        "changed",
        "operator PIN changed — active immediately (write-only; a LOST PIN recovers only from the terminal)",
    )


def _audit(operator: str) -> None:
    # WARN => durable ITS_Errors row, no page. NAMES no value — only that the PIN changed.
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"operator PIN CHANGED by {operator} (elevated-confirm; value not recorded) at {ts}",
            error_code="config_pin_changed",
            alert=False,
        )
    except Exception:
        pass
