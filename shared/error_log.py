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
# add_rows / send_alert / sentry capture ever grow callbacks that emit
# log lines, the inner call must NOT attempt another side-channel write.
_in_smartsheet_write = False
_in_resend_alert = False
_in_sentry_capture = False


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


def _fire_resend_leg(script: str, subject: str, body: str) -> None:
    """Resend leg of the triple-fire. Recursion-guarded; failure-isolated.

    The guard + broad-except + marker-line pattern matches `_smartsheet_log`.
    Sister of `_fire_sentry_leg`; the two are independent — one failing
    does NOT prevent the other from running (see `_alert_critical`).
    """
    global _in_resend_alert
    if _in_resend_alert:
        return

    _in_resend_alert = True
    try:
        # Lazy import to keep this module's boot-time deps minimal and
        # avoid a circular if resend_client ever needs to log.
        from . import resend_client

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


def _fire_sentry_leg(script: str, message: str, exc_info: str) -> None:
    """Sentry leg of the triple-fire. Recursion-guarded; failure-isolated.

    Same shape as `_fire_resend_leg`. Sentry needs the structured
    (script / message / traceback) fields rather than the already-
    composed email subject + body, so this helper takes the raw args.
    """
    global _in_sentry_capture
    if _in_sentry_capture:
        return

    _in_sentry_capture = True
    try:
        from . import sentry_client

        try:
            sentry_client.capture_exception(script, message, exc_info)
        except Exception as e:
            # Broad catch for the same reason as the Resend leg —
            # `KeychainError` on missing DSN, network errors during init,
            # SDK transport failures all need to be swallowed. Marker
            # line distinguishes Sentry failures from Resend failures.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[sentry-capture-failed] {e!r}",
            )
    finally:
        _in_sentry_capture = False


def _alert_critical(script: str, message: str, exc_info: str) -> None:
    """Fire out-of-band CRITICAL alerts on all triple-fire legs.

    Triple-fire per Op Stds v8 §3:
    - Smartsheet `ITS_Errors` row — written earlier by `log()` →
      `_smartsheet_log` (NOT here; that path is unconditional on
      WARN / ERROR / CRITICAL).
    - Resend operator email — `_fire_resend_leg`.
    - Sentry structured event — `_fire_sentry_leg`.

    Both side-channel legs are INDEPENDENT: each has its own try/except
    and its own recursion guard. A failure of either does NOT prevent
    the other from running. Failure marker lines differ per leg
    (`[resend-alert-failed]` vs `[sentry-capture-failed]`) so operators
    can triage which leg(s) are down without grepping the call stack.

    Order is "Resend first, Sentry second" — chosen because Resend
    delivery is the higher-stakes leg (operator wake-up), and we want
    that attempt to fire before any Sentry-side latency. If both legs
    succeed in milliseconds, the order is invisible; if Sentry hangs,
    Resend has already gone out.

    Alert-routing dedupe across the three legs is a separate design
    question per Op Stds v8 §3 open items.
    """
    # Subject + body composed once and shared with Resend. Sentry uses
    # the raw structured fields (`_fire_sentry_leg` constructs its own
    # SDK payload from `message` + `exc_info` directly), so it doesn't
    # need these.
    truncated = message
    if len(truncated) > _ALERT_SUBJECT_TRUNCATE:
        truncated = truncated[: _ALERT_SUBJECT_TRUNCATE - 1] + "…"
    subject = f"{_ALERT_SUBJECT_PREFIX} {script}: {truncated}"

    ts = datetime.now(UTC).isoformat()
    body = "\n".join([
        f"Script:    {script}",
        f"Timestamp: {ts}",
        f"Message:   {message}",
        "",
        "Traceback:",
        exc_info or "(none)",
    ])

    _fire_resend_leg(script, subject, body)
    _fire_sentry_leg(script, message, exc_info)


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
