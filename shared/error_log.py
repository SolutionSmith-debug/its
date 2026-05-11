"""ITS error logging — every script wraps its main function with @its_error_log.

Behavior:
- All severities write to a local log file (logs/<YYYY-MM-DD>.log).
- All severities also write to Smartsheet ITS_Errors (TODO — gated on Smartsheet creds).
- CRITICAL severity triggers immediate email + SMS to maintainer (TODO — gated on Graph creds).

Use the decorator:
    @its_error_log("safety_reports.intake")
    def main():
        ...
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import TypeVar

LOG_DIR = Path.home() / "its" / "logs"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _local_log(severity: Severity, script: str, message: str, exc_info: str | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    line = f"{ts}\t{severity.value}\t{script}\t{message}"
    if exc_info:
        line += f"\n{exc_info}"
    with (LOG_DIR / f"{datetime.now():%Y-%m-%d}.log").open("a") as f:
        f.write(line + "\n")
    print(line)


def log(severity: Severity, script: str, message: str) -> None:
    """Manually log an entry. Use this from inside a script for non-exception events."""
    _local_log(severity, script, message)
    # TODO: also write to Smartsheet ITS_Errors


def _alert_critical(script: str, message: str, exc_info: str) -> None:
    """Send immediate email + SMS for CRITICAL events.

    TODO: implement once Microsoft Graph credentials (for email) and an SMS path are wired.
    For now this is a no-op; the local log captures the event.
    """
    pass


F = TypeVar("F", bound=Callable)


def its_error_log(script_name: str) -> Callable[[F], F]:
    """Decorator: catch unhandled exceptions, log them, surface CRITICAL via alert path."""
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            _local_log(Severity.INFO, script_name, "started")
            try:
                result = fn(*args, **kwargs)
                _local_log(Severity.INFO, script_name, "completed")
                return result
            except Exception as e:
                tb = traceback.format_exc()
                _local_log(Severity.CRITICAL, script_name, f"unhandled: {e}", exc_info=tb)
                _alert_critical(script_name, str(e), tb)
                raise
        return wrapper  # type: ignore[return-value]
    return decorator
