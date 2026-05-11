"""ITS kill switch — every script reads this first.

The kill switch lives in a Smartsheet sheet called ITS_Config with a single row containing a
column called `system_state`. Values: ACTIVE | PAUSED | MAINTENANCE.

- ACTIVE: scripts run normally.
- PAUSED: scheduled scripts skip silently. Watchdog still alerts on missed runs.
- MAINTENANCE: same as PAUSED, but watchdog does not alert.

The point of this is to let anyone with edit access to the sheet halt ITS without touching
code. Useful before sensitive periods (audits, holidays, etc.).
"""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import TypeVar


class SystemState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    MAINTENANCE = "MAINTENANCE"


def check_system_state() -> SystemState:
    """Read ITS_Config sheet, return current state.

    Fail-open: if the config sheet is unreachable, returns ACTIVE. The watchdog catches
    the underlying config-read failure separately, so we don't double-fail here.

    TODO: implement once ITS_Config sheet ID and Smartsheet credentials are decided.
    """
    # Stub until Smartsheet credentials + sheet ID are in place.
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
