"""ITS error logging — every script wraps its main function with @its_error_log.

Behavior:
- All severities write to a local log file (logs/<YYYY-MM-DD>.log) and print.
- WARN / ERROR / CRITICAL also write to Smartsheet ITS_Errors. INFO writes
  are gated by env var `ITS_ERROR_LOG_INFO=1` (default off) to keep cron-job
  startup latency clean — production scripts run with it unset and pay zero
  Smartsheet round-trips on `started` / `completed` lines.
- CRITICAL severity additionally triggers the out-of-band push legs: a
  Resend email to the operator (`system.operator_email` from ITS_Config)
  and a Sentry structured event. Failure-isolated: if either push service
  is down, the underlying CRITICAL event is still captured in the local
  log and ITS_Errors. BOTH push legs are dedupe-gated by
  `shared.alert_dedupe` — Resend on `f"{script}::{error_code}"`, Sentry on
  the namespaced `f"sentry::{script}::{error_code}"` key. Sentry
  reclassified record→deduped-push, operator-ratified 2026-07-03
  (option 1); §3.1 rider: blueprint PR its-blueprint#55; ITS_Errors remains
  the sole per-occurrence record.

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
# Top-level alert-path guard. The two per-leg guards above are asymmetric on
# their own — _in_sentry_capture is still clear while the Resend leg runs — so a
# reentrant log(CRITICAL) raised from INSIDE the Resend send would skip the
# Resend leg but re-fire the Sentry leg (its per-key dedupe would not stop the
# first occurrence). This guard wraps the whole of `_alert_critical`, making
# the alert path fully reentrancy-safe (A3: log() now fires _alert_critical,
# so this reentry is reachable in principle).
_in_alert_critical = False


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
            # §3.1: ITS_Errors is an always-write forensic surface. The breaker
            # must NOT suppress this record write — including during the
            # cooldown-after-recovery window (Smartsheet reachable, breaker still
            # OPEN). bypass() exempts this call from BOTH the short-circuit AND
            # failure-accounting, so the forensic leg can never itself drive the
            # breaker. Mirrors the heartbeat-write + config-read bypass.
            # (Op Stds v16 §3.1; F08 PR 1.) Lazy import matches this file's idiom.
            from . import circuit_breaker

            with circuit_breaker.bypass():
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
    alert: bool = True,
) -> None:
    """Manually log an entry. Use this from inside a script for non-exception events.

    `error_code` is a short stable label written to the ITS_Errors `Error`
    column. Defaults to the lowercased severity value (`"info"` / `"warn"` /
    `"error"` / `"critical"`); the decorator overrides with specific codes
    `"started"` / `"completed"` / `"uncaught_exception"`.

    `correlation_id` is the shared UUID that ties this log row to its
    triple-fire Resend email + Sentry event. For a CRITICAL that pages (see
    `alert`), one is minted here if the caller omits it and threaded to BOTH
    the ITS_Errors row and the alert legs, so a single grep recovers the full
    picture. Non-CRITICAL callers that omit it write a blank `Correlation_ID`
    cell; the column accepts blanks.

    `alert` (A3): a CRITICAL log PAGES the operator by default — after the two
    RECORD legs (local file + ITS_Errors row) it fires the triple-fire alert
    legs (Resend email + Sentry event) via `_alert_critical`. This closes the
    sharp edge where `log(CRITICAL)` silently recorded but never paged. Pass
    `alert=False` to RECORD a CRITICAL without paging — the opt-out for callers
    that manage their own paging (e.g. the watchdog deferring the operator page
    during MAINTENANCE while still writing the forensic record). `alert` is a
    no-op for non-CRITICAL severities (they never paged).
    """
    fire_alert = alert and severity is Severity.CRITICAL
    if fire_alert and correlation_id is None:
        # Mint once so the ITS_Errors row and the alert legs share one id.
        correlation_id = str(uuid.uuid4())

    _local_log(severity, script, message, exc_info=exc_info)
    _smartsheet_log(
        severity,
        script,
        message,
        error_code or severity.value.lower(),
        exc_info,
        correlation_id,
    )
    if fire_alert:
        # correlation_id + error_code passed as KEYWORDS so call sites/tests
        # that inspect _alert_critical positionally still see (script, message,
        # exc_info) as the three positional args.
        _alert_critical(
            script,
            message,
            exc_info or "",
            correlation_id=correlation_id,
            error_code=error_code or "critical",
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
            f"ITS_Errors (corr={correlation_id}).",
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
            # Op Stds §3.1 (as amended 2026-07-03): dedupe applies to the
            # push legs; ITS_Errors is the sole always-write record. The
            # Smartsheet row already fired upstream of this leg, so
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
                f"ITS_Errors). Most recent: {subject}",
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
    error_code: str,
) -> None:
    """Sentry leg of the triple-fire. Recursion-guarded; failure-isolated.

    Same shape as `_fire_resend_leg`. Sentry needs the structured
    (script / message / traceback) fields rather than the already-
    composed email subject + body, so this helper takes the raw args.
    `correlation_id` lands as a Sentry tag.

    Deduped-push leg (Sentry reclassified record→deduped-push,
    operator-ratified 2026-07-03 (option 1); §3.1 rider: blueprint PR
    its-blueprint#55; ITS_Errors remains the sole per-occurrence record).
    Gated by `shared.alert_dedupe.should_fire` on the DELIBERATELY
    namespaced key `f"sentry::{script}::{error_code}"` — NOT shared with
    the Resend leg's `f"{script}::{error_code}"` entry. Resend's window
    opens only on a SUCCESSFUL email send; sharing one entry would either
    leave Sentry ungated during a Resend outage (Resend never records a
    fire, so the window never opens and every occurrence still burns a
    Sentry event) or, in the reverse coupling, suppress emails nobody
    received (a successful Sentry capture opening the shared window while
    the email leg was down). Each leg opens its own window on its own
    success.

    Within the window, suppressed captures write a local
    `[sentry-capture-suppressed]` marker (and `should_fire` increments the
    state entry's `suppressed_count`). `record_fire` runs ONLY after a
    successful capture — a failed capture must not open the window.
    Fail-OPEN on any dedupe-state exception: a dedupe bug must never
    silently drop a Sentry capture.
    """
    global _in_sentry_capture
    if _in_sentry_capture:
        return

    _in_sentry_capture = True
    try:
        from . import alert_dedupe, sentry_client

        dedupe_key = f"sentry::{script}::{error_code}"
        try:
            allowed = alert_dedupe.should_fire(dedupe_key)
        except Exception as e:
            # Fail-open: if the dedupe module itself raises, capture anyway.
            # The contract is "dedupe can never silently drop a CRITICAL"
            # — same as the Resend leg.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-dedupe-state-error] should_fire raised: {e!r}",
            )
            allowed = True

        if not allowed:
            # Op Stds §3.1 (as amended 2026-07-03): Sentry is a deduped
            # push leg. The ITS_Errors row — the sole per-occurrence
            # record — already fired upstream, so suppressing here loses
            # no record. Marker line lets the operator confirm dedupe is
            # acting on this leg.
            _local_log(
                Severity.INFO,
                "shared.error_log",
                f"[sentry-capture-suppressed] key={dedupe_key} corr={correlation_id}",
            )
            return

        try:
            sentry_client.capture_exception(
                script, message, exc_info, correlation_id=correlation_id
            )
        except Exception as e:
            # Broad catch for the same reason as the Resend leg —
            # `KeychainError` on missing DSN, network errors during init,
            # SDK transport failures all need to be swallowed. Marker
            # line distinguishes Sentry failures from Resend failures.
            # Return WITHOUT record_fire — a failed capture must not open
            # the dedupe window (the next occurrence should retry).
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[sentry-capture-failed] {e!r}",
            )
            return

        try:
            alert_dedupe.record_fire(dedupe_key)
        except Exception as e:
            # record_fire is best-effort; if it fails, the next CRITICAL
            # within the intended window may produce an extra Sentry
            # event. That is the right failure mode per the fail-open
            # contract.
            _local_log(
                Severity.ERROR,
                "shared.error_log",
                f"[alert-dedupe-state-error] record_fire raised: {e!r}",
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

    Triple-fire per Op Stds §3 (§3.1 as amended 2026-07-03 — Sentry
    reclassified record→deduped-push, operator-ratified (option 1);
    rider: blueprint PR its-blueprint#55):
    - Smartsheet `ITS_Errors` row — written earlier by `log()` →
      `_smartsheet_log` (NOT here; that path is unconditional on
      WARN / ERROR / CRITICAL). The SOLE per-occurrence record.
    - Resend operator email — `_fire_resend_leg` (deduped push).
    - Sentry structured event — `_fire_sentry_leg` (deduped push).

    `correlation_id` is the shared UUID that ties the Smartsheet row,
    Resend email, and Sentry event together. The decorator generates it
    ONCE upstream and threads to both `log()` and here so all three legs
    share the identifier. Standalone callers (smoke tests, direct
    invocation) can omit it; a UUID is generated internally and only
    threaded to the two side-channel legs.

    `error_code` is the dedupe-key suffix threaded to BOTH push legs'
    dedupe gates — Resend on `f"{script}::{error_code}"`, Sentry on the
    namespaced `f"sentry::{script}::{error_code}"` (independent windows;
    see `_fire_sentry_leg` for why they must not share an entry).
    Defaults to `"uncaught_exception"` because today's only call site is
    the decorator's exception path.

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
    global _in_alert_critical
    if _in_alert_critical:
        # Reentrant CRITICAL raised from inside a leg → do not re-run EITHER leg
        # (prevents the asymmetric duplicate Sentry event; see the guard's def).
        return
    _in_alert_critical = True
    try:
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
        _fire_sentry_leg(script, message, exc_info, correlation_id, error_code)
    finally:
        _in_alert_critical = False


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
                msg = f"unhandled: {e}"
                # A3: log(CRITICAL) now fires the triple-fire alert path itself
                # — it mints + threads one correlation_id across the ITS_Errors
                # row, the Resend email, and the Sentry event. No separate
                # _alert_critical call (that would double-run the push legs —
                # a first-occurrence Sentry capture would double-fire before
                # its dedupe window opens).
                log(
                    Severity.CRITICAL,
                    script_name,
                    msg,
                    error_code="uncaught_exception",
                    exc_info=tb,
                )
                raise
        return wrapper  # type: ignore[return-value]
    return decorator
