"""ITS kill switch — every script reads this first.

The kill switch lives in the ITS_Config Smartsheet (tall key/value layout) as a row keyed
on Setting=`system.state` / Workstream=`global`. Values: ACTIVE | PAUSED | MAINTENANCE.

- ACTIVE: scripts run normally.
- PAUSED: scheduled scripts skip silently. Watchdog still alerts on missed runs.
- MAINTENANCE: same as PAUSED, but watchdog does not alert.

The point of this is to let anyone with edit access to the sheet halt ITS without touching
code. Useful before sensitive periods (audits, holidays, etc.).

Fail-open: on any of three failure modes — Smartsheet unreachable, row missing, value
not in the enum — we return ACTIVE and emit a distinguishable WARN via
`shared.error_log.log`. Per Op Stds v11 §1: a config read failure must never silently
halt the system. The morning log scan reveals which mode tripped.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import TypeVar

from . import smartsheet_client
from .error_log import Severity, log
from .smartsheet_client import SmartsheetError, SmartsheetNotFoundError


class SystemState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    MAINTENANCE = "MAINTENANCE"


_SCRIPT = "shared.kill_switch"


def check_system_state() -> SystemState:
    """Read ITS_Config Setting=`system.state` Workstream=`global`, return current state.

    Fail-open on three modes — each emits a distinguishable WARN so the morning scan
    reveals which one tripped:

    1. Smartsheet unreachable (any `SmartsheetError`).
    2. Row missing (`SmartsheetNotFoundError` from `get_setting`).
    3. Value not in `{ACTIVE, PAUSED, MAINTENANCE}`.
    """
    try:
        raw = smartsheet_client.get_setting("system.state", workstream="global")
    except SmartsheetNotFoundError:
        log(
            Severity.WARN,
            _SCRIPT,
            "system.state row missing in ITS_Config — defaulting to ACTIVE",
        )
        return SystemState.ACTIVE
    except SmartsheetError as e:
        log(
            Severity.WARN,
            _SCRIPT,
            f"system.state read failed: {e!r} — defaulting to ACTIVE",
        )
        return SystemState.ACTIVE

    try:
        # Normalize None (blank cell) → "" so SystemState() consistently raises
        # ValueError and we land in the single fail-open branch below.
        # `get_setting` may return None when the row exists but Value is blank.
        return SystemState(raw if isinstance(raw, str) else "")
    except ValueError:
        log(
            Severity.WARN,
            _SCRIPT,
            f"system.state invalid value {raw!r} "
            f"(expected ACTIVE|PAUSED|MAINTENANCE) — defaulting to ACTIVE",
        )
        return SystemState.ACTIVE


F = TypeVar("F", bound=Callable)


def require_active[F: Callable](fn: F) -> F:
    """Decorator: script exits cleanly if system_state is not ACTIVE."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        state = check_system_state()
        if state != SystemState.ACTIVE:
            print(f"[its] system_state={state.value}; exiting cleanly")
            return None
        return fn(*args, **kwargs)
    return wrapper  # type: ignore[return-value]
