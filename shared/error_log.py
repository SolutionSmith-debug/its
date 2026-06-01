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
  PRs per Op Stds v11 §3 triple-fire.

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
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import TypeVar

from . import sheet_ids, smartsheet_client
from .smartsheet_client import SmartsheetError

_CORRELATION_ID_SUBJECT_PREFIX_LEN = 8

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
    correlation_id: str | None,
) -> None:
    """Append one row to ITS_Errors. Recursion-guarded; never raises.

    INFO is gated by `ITS_ERROR_LOG_INFO=1`; other severities always write.
    On any `SmartsheetError`, falls back to a marker line in the local log
    file. The original log line was already written by `_local_log` before
    this is called, so the underlying event is captured even if the
    Smartsheet write fails.

    `correlation_id` lands in the `Correlation_ID` column when provided;
    empty string for legacy callers (column is TEXT_NUMBER, blank is fine).
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
                        "Correlation_ID": correlation_id or "",
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
    correlation_id: str | None = None,
) -> None:
    """Manually log an entry. Use this from inside a script for non-exception events.

    `error_code` is a short stable label written to the ITS_Errors `Error`
    column. Defaults to the lowercased severity value (`"info"` / `"warn"` /
    `"error"` / `"critical"`); the decorator overrides with specific codes
    `"started"` / `"completed"` / `"uncaught_exception"`.

    `correlation_id` is the shared UUID that ties this log row to its
    triple-fire Resend email + Sentry event. Legacy callers that omit it
    write a blank `Correlation_ID` cell; the column accepts blanks.
    """
    _local_log(severity, script, message, exc_info=exc_info)
    _smartsheet_log(
        severity,
        script,
        message,
        error_code or severity.value.lower(),
        exc_info,
        correlation_id,
    )


def _send_exempt_alert(subject: str, body: str) -> None:
    """Send a cap-EXEMPT meta-alert (F09 cap-reached / window-summary).

    Failure-isolated like the normal Resend send, and deliberately does NOT
    call `alert_dedupe.record_hourly_send` — exempt alerts must not count
    toward (and so accelerate) the very cap they announce.
    """
    try:
        from . import resend_client

        resend_client.send_alert(subject, body)
    except Exception as e:
        _local_log(
            Severity.ERROR, "shared.error_log", f"[resend-alert-failed] exempt: {e!r}"
        )


def _maybe_fire_window_summary(correlation_id: str) -> None:
    """F09 opportunistic window-summary: if a prior suppression episode has
    expired with un-summarized suppressions, emit the one exempt summary now.

    PR 2 adds a guaranteed watchdog-driven sweep; the shared `summarized` flag
    on the window record keeps the two from double-firing. Best-effort — never
    raises.
    """
    try:
        from . import alert_dedupe

        suppressed = alert_dedupe.pop_due_window_summary()
    except Exception as e:
        _local_log(
            Severity.ERROR,
            "shared.error_log",
            f"[alert-rate-cap-error] pop_due_window_summary raised: {e!r}",
        )
        return
    if suppressed:
        _send_exempt_alert(
            "[ITS] alert-rate-cap window summary",
            f"During the last alert-rate-cap window, {suppressed} operator "
            f"alert(s) were suppressed at the email leg. Records are in "
            f"ITS_Errors + Sentry (corr={correlation_id}).",
        )


def _fire_resend_leg(
    script: str,
    subject: str,
    body: str,
    correlation_id: str,
    error_code: str,
) -> None:
    """Resend leg of the triple-fire. Recursion-guarded; failure-isolated.

    Gated by `shared.alert_dedupe.should_fire` on `(script, error_code)`.
    Within the dedupe window, suppressed sends still write a local marker
    so operators can confirm the dedupe path is doing what it says. On a
    successful send, `record_fire` opens the next window.

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
        from . import alert_dedupe, resend_client

        # F09 opportunistic window-summary — fires on the next gate invocation
        # after a suppression episode expires (PR 2 adds a guaranteed sweep).
        _maybe_fire_window_summary(correlation_id)

        dedupe_key = f"{script}::{error_code}"
        try:
            allowed = alert_dedupe.should_fire(dedupe_key)
        except Exception as e:
            # Fail-open: if the dedupe module itself raises, send anyway.
            # The contract is "dedupe can never silently drop a CRITICAL."
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-dedupe-state-error] should_fire raised: {e!r}",
            )
            allowed = True

        if not allowed:
            # Op Stds v11 §3.1: dedupe applies only to push. The Smartsheet
            # row + Sentry event already fired upstream of this leg, so
            # suppressing here loses no record. Marker line lets the
            # operator confirm dedupe is acting.
            _local_log(
                Severity.INFO,
                "shared.error_log",
                f"[resend-alert-suppressed] key={dedupe_key} corr={correlation_id}",
            )
            return

        # F09 — global alerts-per-hour cap (the SECOND gate). Records already
        # fired upstream (§3.1); only the email fan-out is bounded here.
        try:
            cap_decision = alert_dedupe.check_hourly_cap()
        except Exception as e:
            # Fail-open: a cap-module failure must never drop a CRITICAL email.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-rate-cap-error] check_hourly_cap raised: {e!r}",
            )
            cap_decision = alert_dedupe.CapDecision.ALLOW

        if cap_decision is alert_dedupe.CapDecision.SUPPRESS_QUIET:
            _local_log(
                Severity.INFO,
                "shared.error_log",
                f"[resend-alert-rate-capped] key={dedupe_key} corr={correlation_id}",
            )
            return
        if cap_decision is alert_dedupe.CapDecision.SUPPRESS_FIRST:
            # First suppression of the episode — emit ONE exempt brownout alert
            # instead of the original so the operator knows a storm is underway.
            _send_exempt_alert(
                "[ITS] alert-rate cap reached — further alerts suppressed",
                f"The operator alert-rate cap was reached; further alerts this "
                f"hour are suppressed at the email leg (records still land in "
                f"ITS_Errors + Sentry). Most recent: {subject}",
            )
            return

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
            return

        try:
            alert_dedupe.record_fire(dedupe_key)
        except Exception as e:
            # record_fire is best-effort; if it fails, the next CRITICAL
            # within the intended window may produce an extra email. That
            # is the right failure mode per the fail-open contract.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-dedupe-state-error] record_fire raised: {e!r}",
            )

        try:
            # F09: count this confirmed normal-alert send toward the cap window.
            alert_dedupe.record_hourly_send()
        except Exception as e:
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-rate-cap-error] record_hourly_send raised: {e!r}",
            )
    finally:
        _in_resend_alert = False


def _fire_sentry_leg(
    script: str,
    message: str,
    exc_info: str,
    correlation_id: str,
) -> None:
    """Sentry leg of the triple-fire. Recursion-guarded; failure-isolated.

    Same shape as `_fire_resend_leg`. Sentry needs the structured
    (script / message / traceback) fields rather than the already-
    composed email subject + body, so this helper takes the raw args.
    `correlation_id` lands as a Sentry tag.
    """
    global _in_sentry_capture
    if _in_sentry_capture:
        return

    _in_sentry_capture = True
    try:
        from . import sentry_client

        try:
            sentry_client.capture_exception(
                script, message, exc_info, correlation_id=correlation_id
            )
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


def _alert_critical(
    script: str,
    message: str,
    exc_info: str,
    correlation_id: str | None = None,
    error_code: str = "uncaught_exception",
) -> None:
    """Fire out-of-band CRITICAL alerts on all triple-fire legs.

    Triple-fire per Op Stds v11 §3:
    - Smartsheet `ITS_Errors` row — written earlier by `log()` →
      `_smartsheet_log` (NOT here; that path is unconditional on
      WARN / ERROR / CRITICAL).
    - Resend operator email — `_fire_resend_leg`.
    - Sentry structured event — `_fire_sentry_leg`.

    `correlation_id` is the shared UUID that ties the Smartsheet row,
    Resend email, and Sentry event together. The decorator generates it
    ONCE upstream and threads to both `log()` and here so all three legs
    share the identifier. Standalone callers (smoke tests, direct
    invocation) can omit it; a UUID is generated internally and only
    threaded to the two side-channel legs.

    `error_code` is the dedupe-key suffix passed to the Resend leg's
    `(script, error_code)` dedupe gate. Defaults to `"uncaught_exception"`
    because today's only call site is the decorator's exception path.

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
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    # Subject + body composed once and shared with Resend. Sentry uses
    # the raw structured fields (`_fire_sentry_leg` constructs its own
    # SDK payload from `message` + `exc_info` directly), so it doesn't
    # need these.
    truncated = message
    if len(truncated) > _ALERT_SUBJECT_TRUNCATE:
        truncated = truncated[: _ALERT_SUBJECT_TRUNCATE - 1] + "…"
    short_corr = correlation_id[:_CORRELATION_ID_SUBJECT_PREFIX_LEN]
    subject = f"{_ALERT_SUBJECT_PREFIX} {script}: {truncated} [corr: {short_corr}]"

    ts = datetime.now(UTC).isoformat()
    body = "\n".join([
        f"Script:    {script}",
        f"Timestamp: {ts}",
        f"Correlation: {correlation_id}",
        f"Message:   {message}",
        "",
        "Traceback:",
        exc_info or "(none)",
    ])

    _fire_resend_leg(script, subject, body, correlation_id, error_code)
    _fire_sentry_leg(script, message, exc_info, correlation_id)


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
                # One UUID per CRITICAL, threaded to all three legs so a
                # single grep recovers the full Smartsheet / Resend / Sentry
                # picture during triage.
                correlation_id = str(uuid.uuid4())
                log(
                    Severity.CRITICAL,
                    script_name,
                    msg,
                    error_code="uncaught_exception",
                    exc_info=tb,
                    correlation_id=correlation_id,
                )
                _alert_critical(
                    script_name,
                    msg,
                    tb,
                    correlation_id=correlation_id,
                    error_code="uncaught_exception",
                )
                raise
        return wrapper  # type: ignore[return-value]
    return decorator
