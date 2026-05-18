"""ITS error logging — every script wraps its main function with @its_error_log.

Behavior:
- All severities write to a local log file (logs/<YYYY-MM-DD>.log) and print.
- WARN / ERROR / CRITICAL also write to Smartsheet ITS_Errors. INFO writes
  are gated by env var `ITS_ERROR_LOG_INFO=1` (default off) to keep cron-job
  startup latency clean — production scripts run with it unset and pay zero
  Smartsheet round-trips on `started` / `completed` lines.
- CRITICAL severity triggers immediate email + SMS to maintainer.
  TODO: Resend wiring deferred to the alert-routing PR (Op Stds v8 §3
  triple-fire path).

The Smartsheet write path is recursion-guarded and failure-isolated: any
`SmartsheetError` is caught and reduced to a `[smartsheet-write-failed]`
marker line in the local log; it never raises and never re-enters `log()`.

Use the decorator:
    @its_error_log("safety_reports.intake")
    def main():
        ...
"""
from __future__ import annotations

import os
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import TypeVar

from . import sheet_ids, smartsheet_client
from .smartsheet_client import SmartsheetError

LOG_DIR = Path.home() / "its" / "logs"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Module-level recursion guard. Belt-and-suspenders against any future
# caller path that lands back inside log() while we're writing — e.g. if
# add_rows ever grows a callback that emits a log line, the inner call
# must NOT attempt another Smartsheet write.
_in_smartsheet_write = False


def _local_log(severity: Severity, script: str, message: str, exc_info: str | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    line = f"{ts}\t{severity.value}\t{script}\t{message}"
    if exc_info:
        line += f"\n{exc_info}"
    with (LOG_DIR / f"{datetime.now():%Y-%m-%d}.log").open("a") as f:
        f.write(line + "\n")
    print(line)


def _should_write_info_to_smartsheet() -> bool:
    return os.environ.get("ITS_ERROR_LOG_INFO") == "1"


def _smartsheet_log(
    severity: Severity,
    script: str,
    message: str,
    error_code: str,
    exc_info: str | None,
) -> None:
    """Append one row to ITS_Errors. Recursion-guarded; never raises.

    INFO is gated by `ITS_ERROR_LOG_INFO=1`; other severities always write.
    On any `SmartsheetError`, falls back to a marker line in the local log
    file. The original log line was already written by `_local_log` before
    this is called, so the underlying event is captured even if the
    Smartsheet write fails.
    """
    global _in_smartsheet_write
    if _in_smartsheet_write:
        return
    if severity is Severity.INFO and not _should_write_info_to_smartsheet():
        return

    _in_smartsheet_write = True
    try:
        try:
            smartsheet_client.add_rows(
                sheet_ids.SHEET_ERRORS,
                [
                    {
                        "Error": error_code,
                        "Timestamp": date.today().isoformat(),
                        "Severity": severity.value,
                        "Script": script,
                        "Message": message,
                        "Traceback": exc_info or "",
                    }
                ],
            )
        except SmartsheetError as e:
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[smartsheet-write-failed] {e!r}",
            )
    finally:
        _in_smartsheet_write = False


def log(
    severity: Severity,
    script: str,
    message: str,
    *,
    error_code: str | None = None,
    exc_info: str | None = None,
) -> None:
    """Manually log an entry. Use this from inside a script for non-exception events.

    `error_code` is a short stable label written to the ITS_Errors `Error`
    column. Defaults to the lowercased severity value (`"info"` / `"warn"` /
    `"error"` / `"critical"`); the decorator overrides with specific codes
    `"started"` / `"completed"` / `"uncaught_exception"`.
    """
    _local_log(severity, script, message, exc_info=exc_info)
    _smartsheet_log(
        severity,
        script,
        message,
        error_code or severity.value.lower(),
        exc_info,
    )


def _alert_critical(script: str, message: str, exc_info: str) -> None:
    """Send immediate email + SMS for CRITICAL events.

    TODO: Resend wiring deferred to the alert-routing PR (Op Stds v8 §3
    triple-fire path). Until that lands, CRITICALs land in the local log
    and ITS_Errors but do not push out-of-band.
    """
    pass


F = TypeVar("F", bound=Callable)


def its_error_log(script_name: str) -> Callable[[F], F]:
    """Decorator: catch unhandled exceptions, log them, surface CRITICAL via alert path."""
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            log(Severity.INFO, script_name, "started", error_code="started")
            try:
                result = fn(*args, **kwargs)
                log(Severity.INFO, script_name, "completed", error_code="completed")
                return result
            except Exception as e:
                tb = traceback.format_exc()
                log(
                    Severity.CRITICAL,
                    script_name,
                    f"unhandled: {e}",
                    error_code="uncaught_exception",
                    exc_info=tb,
                )
                _alert_critical(script_name, str(e), tb)
                raise
        return wrapper  # type: ignore[return-value]
    return decorator
