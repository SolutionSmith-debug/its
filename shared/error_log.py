"""ITS error logging — every script wraps its main function with @its_error_log.

Behavior:
- All severities write to a local log file (logs/<YYYY-MM-DD>.log) and print.
- WARN / ERROR / CRITICAL also write to Smartsheet ITS_Errors. INFO writes
  are gated by env var `ITS_ERROR_LOG_INFO=1` (default off) to keep cron-job
  startup latency clean — production scripts run with it unset and pay zero
  Smartsheet round-trips on `started` / `completed` lines.
- CRITICAL severity additionally triggers an out-of-band Resend email to
  the operator (`system.operator_email` from ITS_Config). Failure-isolated:
  if Resend is down, the underlying CRITICAL event is still captured in the
  local log and ITS_Errors. Sentry hook + alert-routing dedupe are separate
  PRs per Op Stds v8 §3 triple-fire.

Both side-channel write paths (Smartsheet + Resend) are recursion-guarded
and failure-isolated: any module-specific exception is caught and reduced
to a marker line in the local log; neither path raises, neither path
re-enters `log()`.

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

# CRITICAL alert subject prefix and message truncation. 80-char truncation
# keeps subjects readable in mobile mail clients and aligns with most SMS
# preview widths if/when SMS is wired later.
_ALERT_SUBJECT_PREFIX = "[ITS CRITICAL]"
_ALERT_SUBJECT_TRUNCATE = 80


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Module-level recursion guards. Belt-and-suspenders against any future
# caller path that lands back inside log() while we're writing — e.g. if
# add_rows or send_alert ever grow callbacks that emit log lines, the
# inner call must NOT attempt another side-channel write.
_in_smartsheet_write = False
_in_resend_alert = False


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
    """Send out-of-band Resend email for CRITICAL events.

    Third leg of the Op Stds v8 §3 triple-fire (Sentry + Smartsheet
    `ITS_Errors` + Resend). Sentry hook + alert-routing dedupe are
    separate PRs; this function handles Resend only.

    Failure-isolated: a `ResendError` is caught and reduced to a
    `[resend-alert-failed]` marker line in the local log. The underlying
    CRITICAL event is captured by `_local_log` and `_smartsheet_log`
    before this function runs, so an out-of-band failure here cannot
    drop the underlying signal.

    Recursion-guarded via `_in_resend_alert` against the (unlikely but
    defensible) scenario where the Resend path triggers another log()
    call. Mirrors `_smartsheet_log`'s guard pattern.
    """
    global _in_resend_alert
    if _in_resend_alert:
        return

    _in_resend_alert = True
    try:
        # Lazy import to keep this module's boot-time deps minimal and
        # avoid a circular if resend_client ever needs to log.
        from . import resend_client

        # Subject: prefix + script + truncated message. Truncation keeps
        # subjects readable in mobile mail clients.
        truncated = message
        if len(truncated) > _ALERT_SUBJECT_TRUNCATE:
            truncated = truncated[: _ALERT_SUBJECT_TRUNCATE - 1] + "…"
        subject = f"{_ALERT_SUBJECT_PREFIX} {script}: {truncated}"

        # Body: full message + traceback + timestamp + script. Plain text.
        ts = datetime.now(UTC).isoformat()
        body_parts = [
            f"Script:    {script}",
            f"Timestamp: {ts}",
            f"Message:   {message}",
            "",
            "Traceback:",
            exc_info or "(none)",
        ]
        body = "\n".join(body_parts)

        try:
            resend_client.send_alert(subject, body)
        except Exception as e:
            # Intentionally broad. A missing keychain entry raises
            # `KeychainError` (not a `ResendError`); ditto network errors
            # that aren't routed through `ResendError`. The brief mandates
            # "must NOT raise...anything," so widening to `Exception`
            # preserves the bulletproof-alert-path contract. The marker
            # line records the exception type so operators can triage.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[resend-alert-failed] {e!r}",
            )
    finally:
        _in_resend_alert = False


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
                # Use the same prefixed message for both Smartsheet row and
                # Resend alert email so operator sees consistent text across
                # the triple-fire channels.
                msg = f"unhandled: {e}"
                log(
                    Severity.CRITICAL,
                    script_name,
                    msg,
                    error_code="uncaught_exception",
                    exc_info=tb,
                )
                _alert_critical(script_name, msg, tb)
                raise
        return wrapper  # type: ignore[return-value]
    return decorator
