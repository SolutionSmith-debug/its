"""Safety Reports weekly send polling daemon — launchd-driven dispatcher.

Phase-5: discovers `WSR_human_review` rows with `Send Now` (immediate) OR
`Approve for Scheduled Send` (the Monday-≥07:00-Pacific batch) checked, runs the
F22 approval-attestation gate on the driving checkbox, stamps the verified approver
(Approved By/At), and dispatches each to `safety_reports.weekly_send.send_one_row`.
The poller has zero send capability of its own; it is an iterator + dispatcher. The
handler is the only place `graph_client.send_mail` is called.

launchd schedule
----------------

Single-cycle execution: `poll_once()` is the public API; the `__main__`
guard calls it exactly once and exits. launchd handles cadence via
`StartInterval` in `scripts/launchd/org.solutionsmith.its.weekly-send.plist`
(default 900 s = 15 min; sourced from ITS_Config row
`safety_reports.weekly_send.poll_interval_seconds` at install time).

Per-cycle behavior
------------------

  1. `polling_enabled` ITS_Config gate — false short-circuits the cycle.
     (Reusing the same key shape as intake: `safety_reports.weekly_send.polling_enabled`.)
  2. fcntl file lock at `~/its/state/weekly_send.lock` — skip-if-held.
     Prevents launchd-overlap collisions if a previous cycle took longer
     than the interval.
  3. Read `WSR_human_review` via `smartsheet_client.get_rows`. Filter
     client-side to rows that need send-attention:
       - `Send Now` = True OR `Approve for Scheduled Send` = True
       - Send Status in (PENDING, FAILED) — terminally-failed rows
         (Send Retry Count >= MAX_SEND_RETRIES, encoded in Notes per
         the schema-degradation contract in weekly_send.py) are
         filtered out.
  4. For each candidate row: pick the DRIVING approval column — `Send Now`
     dispatches immediately; `Approve for Scheduled Send` dispatches only
     inside the scheduled window (default Monday ≥07:00 Pacific). Then run
     the F22 approval-attestation gate (`approval_verification.verify_approval`)
     on that column — confirm the checkbox's current value was set by an
     authorized actor per Smartsheet cell history. A non-verified verdict
     BLOCKS that row (fail-closed) with a forensic `approval_unverified`
     event; other rows still dispatch. Verified rows are stamped with the
     approver identity (Approved By/At) and invoke
     `weekly_send.send_one_row(row_id)`. The handler returns a `SendResult`
     — the poller logs the outcome and continues. SmartsheetError raised by
     the handler caught per-row; the cycle continues to the next row.
  5. Write file heartbeat (`HEARTBEAT_PATH`).
  6. Write ITS_Daemon_Health row (PR #60 pattern; the heartbeat helpers were
     originally replicated verbatim from `intake_poll.py` per preservation-over-
     refactor. `intake_poll.py` is RETIRED 2026-06-05 — this is now the canonical
     copy, pending the `shared/heartbeat.py` extraction (tech-debt)).
  7. Write watchdog Check C marker (`safety_weekly_send_poll.last_run`).

Capability gating
-----------------

Zero AI capability. Inherits `graph_client` via the handler (which IS
allowed to send_mail). This module imports `safety_reports.weekly_send`
to dispatch — that transitively brings in graph_client.send_mail, which
is the intended capability for the send-side workstream. The AST gate
in `tests/test_capability_gating.py::SEND_SCRIPTS` forbids
`anthropic_client` / `anthropic` in both this file AND `weekly_send.py`.

Push-vs-Record Separation (Op Stds v11 §3.1)
--------------------------------------------

Each send is a PUSH; the poller drives discovery (RECORD-style scan)
and the handler performs the push. Failure-alert dedupe scope: 3-strike
CRITICAL from `weekly_send` is `alert_dedupe`-aware via the standard
`(script, error_code)` key.

Heartbeat consolidation (P0, 2026-06-28)
----------------------------------------

The ITS_Daemon_Health heartbeat helpers — once replicated VERBATIM here and in
`safety_reports/portal_poll.py` (the polling-daemon doctrine's 2nd-consumer
extraction trigger, Op Stds §14) — now live in `shared/heartbeat.py` as
`HeartbeatReporter`. This module keeps only the two test-mock seams
(`_write_heartbeat` / `_write_heartbeat_row`) as thin delegators to the
module-level `_heartbeat_reporter`; the per-daemon registration metadata is its
constructor config.
"""
from __future__ import annotations

import fcntl
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from safety_reports import weekly_send, wsr_review
from shared import (
    approval_verification,
    circuit_breaker,
    error_log,
    sheet_ids,
    smartsheet_client,
)
from shared.error_log import Severity, its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.weekly_send_poll"
WORKSTREAM = "safety_reports"

# ITS_Config keys.
CFG_POLLING_ENABLED = "safety_reports.weekly_send.polling_enabled"
CFG_POLL_INTERVAL = "safety_reports.weekly_send.poll_interval_seconds"

# F22 — approval-attestation gate. Approval authority = ITS — Safety Portal
# WORKSPACE membership: an approver is authorized iff they are a member of the
# workspace's share list (sharing the workspace IS granting send authority —
# Evergreen controls who can approve by who it shares with, with no per-email
# config to maintain across the evergreenmirror.com → evergreenrenewables.com
# cutover). The former `safety_reports.authorized_approvers` ITS_Config allowlist
# is retired. Phase-5: WSR has TWO human approval CHECKBOXes — `Approve for
# Scheduled Send` (the Monday batch) and `Send Now` (immediate, out-of-band). The
# F22 gate verifies the actor on whichever drove the dispatch is a workspace member.
APPROVAL_COLUMN_SCHEDULED = wsr_review.COL_APPROVE_SCHEDULED  # "Approve for Scheduled Send"
APPROVAL_COLUMN_SEND_NOW = wsr_review.COL_SEND_NOW            # "Send Now"

# Scheduled send window — `Approve for Scheduled Send` rows dispatch only when the
# poll cycle runs on/after this weekday + local time (default Monday 07:00 Pacific,
# ITS_Config-overridable). `Send Now` rows dispatch on every cycle.
CFG_SCHEDULED_SEND_LOCAL = "safety_reports.weekly_send.scheduled_send_local"
DEFAULT_SCHEDULED_SEND_LOCAL = "MON 07:00"
SEND_TZ = "America/Los_Angeles"  # everything Pacific

DEFAULT_POLLING_ENABLED = True
DEFAULT_POLL_INTERVAL = 900  # 15 minutes

# State paths. HEARTBEAT_ROW_STATE_PATH is SHARED with intake_poll —
# same JSON file, different daemon_name key. Per-instance file lock
# during read-modify-write per intake_poll convention.
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_PATH = STATE_DIR / "weekly_send_heartbeat.txt"
LOCK_PATH = STATE_DIR / "weekly_send.lock"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"

# Stable daemon-name identifier — primary key in ITS_Daemon_Health and
# the state-file dict key. Distinct from intake's `safety_reports.intake_poll`.
DAEMON_NAME = "safety_reports.weekly_send_poll"

# A1 self-provision metadata. Per-daemon values written ONCE to
# ITS_Daemon_Health when this daemon's row is absent (see
# `_create_heartbeat_row`); the per-cycle columns are filled by the very next
# `_write_heartbeat_row` update. These two constants are the ONLY per-daemon
# difference in the otherwise byte-identical heartbeat helpers — keep them OUT
# of the helper bodies so the verbatim-duplication invariant (and the future
# `shared/heartbeat.py` extraction) stays clean.
_REGISTRATION_INTERVAL_SECONDS = DEFAULT_POLL_INTERVAL  # 900s = 15-min cadence
_REGISTRATION_SOURCE_ID = f"WSR_human_review ({sheet_ids.SHEET_WSR_HUMAN_REVIEW})"

# Shared ITS_Daemon_Health reporter for this daemon. The per-daemon registration
# values are the ONLY heartbeat difference between daemons (see shared/heartbeat.py).
_heartbeat_reporter = HeartbeatReporter(
    script_name=SCRIPT_NAME,
    daemon_name=DAEMON_NAME,
    workstream=WORKSTREAM,
    liveness_path=HEARTBEAT_PATH,
    interval_seconds=_REGISTRATION_INTERVAL_SECONDS,
    source_id=_REGISTRATION_SOURCE_ID,
    row_state_path=HEARTBEAT_ROW_STATE_PATH,  # shared file — make the contract explicit
)

# Watchdog Check C marker — matches `TRACKED_JOBS` entry in scripts/watchdog.py.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_weekly_send_poll"

# Allowed cycle-status values written to ITS_Daemon_Health.Last_Cycle_Status.
# CIRCUIT_OPEN (F08) overrides the cycle status when the Smartsheet circuit
# breaker is OPEN (lock-free is_open() at the status-determination point); the
# heartbeat write itself runs under circuit_breaker.bypass() so the status can
# still land when Smartsheet is reachable.

# Send Status values the poller dispatches on. SENT rows are skipped (already done);
# HELD rows are skipped (operator-driven hold); SENDING rows are skipped — that is the
# LOAD-BEARING exclusion behind weekly_send's write-ahead marker: a row left in SENDING
# (a post-send SENT-stamp failure) must NEVER be re-dispatched, or the customer is
# double-sent. Do NOT add SENDING here. PENDING + FAILED rows are dispatch candidates.
DISPATCH_STATUSES = frozenset({weekly_send.STATUS_PENDING, weekly_send.STATUS_FAILED})


@dataclass(frozen=True)
class PollStats:
    """Summary of one poll_once() invocation. Returned for caller logging."""
    skipped_disabled: bool = False
    skipped_locked: bool = False
    rows_scanned: int = 0
    dispatched: int = 0
    sent: int = 0
    skipped: int = 0  # any "skipped_*" outcome
    failed: int = 0   # any send_failed / invalid_recipients outcome
    errors: int = 0   # per-row SmartsheetError exceptions inside the loop
    blocked: int = 0  # F22: rows whose approval failed attestation (not sent)


# ---- Config readers (replicated per preservation) -----------------------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        # F08: an OPEN breaker short-circuits this control-plane config read.
        # Fail open to the fallback so a degraded Smartsheet cannot crash the
        # cycle BEFORE it surfaces CIRCUIT_OPEN in its heartbeat — the data-plane
        # work still short-circuits, but the cycle runs to completion. Mirrors
        # the kill-switch read's own breaker-resilience. (Op Stds v16 §3.1.)
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _polling_enabled() -> bool:
    return _read_bool_setting(CFG_POLLING_ENABLED, DEFAULT_POLLING_ENABLED)


def _load_authorized_approvers() -> frozenset[str]:
    """Resolve the F22 authorized-approver set from ITS — Safety Portal WORKSPACE
    membership (workspace access == approval authority; see the F22 note above).

    Returns the lowercased member emails of the workspace's share list. An empty
    set (the workspace has no individual shares) is the legitimate fail-closed
    case, which `verify_approval` treats as EMPTY_ALLOWLIST (block all sends),
    NEVER fail-open. A SmartsheetError (auth / 500 / circuit-open) propagates to
    the `@its_error_log` CRITICAL path — a membership-read infra failure aborts
    the cycle loudly with zero sends, retried next cycle (fail-closed).
    """
    # F08 CONTRAST — deliberately NO try/except here. Unlike `_read_str_setting`
    # (which catches SmartsheetCircuitOpenError and fails OPEN to a scheduling
    # fallback), this is the SECURITY gate: a circuit-open / auth / 500 reading
    # the approver set MUST propagate (→ @its_error_log CRITICAL, cycle aborts,
    # zero sends) — fail-CLOSED. Do not add a fallback-to-empty / fail-open catch.
    return smartsheet_client.list_workspace_share_emails(
        sheet_ids.WORKSPACE_SAFETY_PORTAL
    )


# ---- State / lock helpers -----------------------------------------------


@contextmanager
def _file_lock(path: Path) -> Iterator[bool]:
    """Acquire exclusive non-blocking lock; yield True on success, False if held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001 — cleanup best-effort
            pass
        handle.close()


def _write_heartbeat() -> None:
    """Liveness file touch — thin delegator to the shared HeartbeatReporter.

    Kept as a module-level function because it is the canonical test mock seam
    (the suite patches this exact symbol). See shared/heartbeat.py (§42).
    """
    _heartbeat_reporter.write_liveness()


def _write_heartbeat_row(
    *,
    status: HeartbeatStatus,
    items_processed: int,
    error_summary: str | None = None,
    correlation_id: str | None = None,
    notes: str | None = None,
    daemon_name: str = DAEMON_NAME,
) -> None:
    """ITS_Daemon_Health per-cycle row update — thin delegator to the shared
    HeartbeatReporter (the canonical test mock seam; the suite patches this exact
    symbol). The ``daemon_name`` param is retained for signature back-compat and
    always resolves to this daemon. See shared/heartbeat.py (§42).
    """
    _heartbeat_reporter.write_row(
        status=status,
        items_processed=items_processed,
        error_summary=error_summary,
        correlation_id=correlation_id,
        notes=notes,
        daemon_name=daemon_name,
    )


# ---- Watchdog Check C marker --------------------------------------------


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this run."""
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- Row filtering -------------------------------------------------------


def _filter_dispatch_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return WSR rows that need send-attention this cycle.

    Filter rules (all must be true):
      - `Send Now` OR `Approve for Scheduled Send` is checked.
      - Send Status in {PENDING, FAILED} (SENT + HELD excluded).
      - If Send Status == FAILED, Notes-encoded retry count < MAX_SEND_RETRIES
        (terminally-failed rows need human resolution).

    The dispatch loop decides scheduled-vs-now TIMING + runs the F22 gate per row;
    this filter just reduces the per-cycle volume.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not (bool(row.get(APPROVAL_COLUMN_SEND_NOW)) or bool(row.get(APPROVAL_COLUMN_SCHEDULED))):
            continue
        status = row.get(wsr_review.COL_SEND_STATUS) or weekly_send.STATUS_PENDING
        if status not in DISPATCH_STATUSES:
            continue
        if status == weekly_send.STATUS_FAILED:
            if weekly_send._parse_retry_count(row.get(wsr_review.COL_NOTES)) >= weekly_send.MAX_SEND_RETRIES:
                continue
        out.append(row)
    return out


_WEEKDAY_MAP = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def _parse_scheduled_spec(spec: str) -> tuple[int, time]:
    """Parse `MON 07:00` → (0, time(7, 0)). Defaults on parse failure."""
    try:
        wd, hhmm = spec.strip().split()
        h, m = hhmm.split(":")
        return _WEEKDAY_MAP[wd.upper()], time(int(h), int(m))
    except (KeyError, ValueError):
        return 0, time(7, 0)


def _is_scheduled_window(now_local: datetime, spec: str) -> bool:
    """True iff `now_local` is on/after the configured weekday + time (e.g. Mon ≥07:00).

    `Approve for Scheduled Send` rows dispatch only inside this window; `Send Now`
    rows ignore it. A scheduled row stays PENDING until its first Monday-≥07:00 cycle
    (idempotent thereafter — Send Status flips to SENT and the filter drops it)."""
    weekday, tod = _parse_scheduled_spec(spec)
    return now_local.weekday() == weekday and now_local.timetz().replace(tzinfo=None) >= tod


# ---- F22 approval-attestation handling ----------------------------------

# Verdict reasons that warrant an operator wake-up (triple-fire), vs. those
# that get a forensic row only. UNAUTHORIZED_ACTOR is the security case;
# EMPTY_ALLOWLIST is a config/cutover failure blocking every send.
_WAKE_REASONS = frozenset({
    approval_verification.VerdictReason.UNAUTHORIZED_ACTOR,
    approval_verification.VerdictReason.EMPTY_ALLOWLIST,
})


def _handle_unverified(
    row_id: int, verdict: approval_verification.ApprovalVerdict
) -> None:
    """Record a blocked send for an unverified approval (fail-closed).

    Always writes a forensic ITS_Errors row (never silently swallowed — an
    unverified approval on a customer report is security-relevant). Wakes
    the operator via the triple-fire path (dedupe-gated on
    `(script, error_code)`) only for `_WAKE_REASONS`; a benign race
    (NOT_CURRENTLY_APPROVED) gets a WARN row and a transient read failure an
    ERROR row, neither paging.
    """
    reason = verdict.reason
    if reason in _WAKE_REASONS:
        severity: Severity = Severity.CRITICAL
    elif reason == approval_verification.VerdictReason.NOT_CURRENTLY_APPROVED:
        severity = Severity.WARN
    else:
        severity = Severity.ERROR

    # One correlation_id threaded across all triple-fire legs (Op Stds v14 §3)
    # so the ITS_Errors row, Resend email, and Sentry event share an identifier
    # for cross-leg pivoting — the same pattern @its_error_log and the
    # picklist_sync standalone caller use.
    correlation_id = str(uuid.uuid4())

    error_log.log(
        severity,
        SCRIPT_NAME,
        (
            f"approval attestation FAILED for row_id={row_id}; send BLOCKED "
            f"(fail-closed). reason={reason.value} actor={verdict.actor!r} "
            f"detail={verdict.detail}"
        ),
        error_code="approval_unverified",
        correlation_id=correlation_id,
    )

    # A3: paging is now driven by the severity of the log() call above —
    # log(CRITICAL) fires the triple-fire alert path itself. Because severity
    # is CRITICAL exactly when `reason in _WAKE_REASONS` (see the mapping
    # above), the operator is woken for precisely the wake reasons and not for
    # the WARN/ERROR (benign-race / transient-read-failure) reasons. No
    # explicit _alert_critical needed (it would double-fire the Sentry leg).
    # The log message already carries actor + detail for the page body.


def _stamp_approval(row_id: int, verdict: approval_verification.ApprovalVerdict) -> None:
    """Stamp the verified approver identity onto the WSR row (Approved By/At).

    Best-effort AUDIT write — a stamp failure must NOT block a send the F22 gate
    already verified (the approval is real regardless of whether the stamp lands).
    Approved By takes the actor email; Approved At takes the deciding-event datetime —
    Pacific wall-clock in the ABSTRACT_DATETIME column (the verdict's UTC modified_at
    converted to local), falling back to now."""
    approved_at = wsr_review.to_wsr_datetime(verdict.modified_at)
    try:
        smartsheet_client.update_rows(
            sheet_ids.SHEET_WSR_HUMAN_REVIEW,
            [{
                "_row_id": row_id,
                wsr_review.COL_APPROVED_BY: verdict.actor or "",
                wsr_review.COL_APPROVED_AT: approved_at,
            }],
        )
    except Exception as exc:  # noqa: BLE001 — the stamp is best-effort AUDIT; it must
        # NEVER block a send the F22 gate already verified (any failure → WARN + proceed).
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"approval stamp failed for row_id={row_id} (non-fatal; send proceeds): {exc!r}",
            error_code="weekly_send_poll.stamp_failed",
        )


# ---- Public API ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> PollStats:
    """Run one poll cycle. Public API; idempotent across crashes."""
    if not _polling_enabled():
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            "polling disabled via ITS_Config; exiting cycle",
            error_code="polling_disabled",
        )
        return PollStats(skipped_disabled=True)

    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            error_log.log(
                Severity.INFO,
                SCRIPT_NAME,
                "another poll cycle holds the lock; skipping this cycle",
                error_code="poll_lock_held",
            )
            return PollStats(skipped_locked=True)
        return _poll_inside_lock()


def _poll_inside_lock() -> PollStats:
    """Body of poll_once running under the file lock."""
    try:
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_WSR_HUMAN_REVIEW)
    except smartsheet_client.SmartsheetError as exc:
        error_log.log(
            Severity.ERROR,
            SCRIPT_NAME,
            f"failed to read WSR_human_review: {exc!r}",
            error_code="weekly_send_poll.read_failed",
        )
        # Still write watchdog marker so Check C doesn't fire on a transient
        # read failure (the daemon DID run, it just got nothing).
        _write_heartbeat()
        # F08: this early-return bypasses the normal-path CIRCUIT_OPEN status
        # override, so apply it here too — a scan short-circuited by an OPEN
        # breaker surfaces CIRCUIT_OPEN, not a generic ERROR. A genuine
        # non-breaker read failure still surfaces ERROR.
        breaker_open = circuit_breaker.is_open()
        read_fail_status: HeartbeatStatus = "CIRCUIT_OPEN" if breaker_open else "ERROR"
        _write_heartbeat_row(
            status=read_fail_status,
            items_processed=0,
            error_summary=(
                None
                if breaker_open
                else f"read failed: {type(exc).__name__}: {exc!r}"
            ),
        )
        _write_watchdog_marker()
        return PollStats(errors=1)

    candidates = _filter_dispatch_candidates(rows)
    authorized_actors = _load_authorized_approvers()
    counters = {
        "dispatched": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
        "blocked": 0,
    }

    now_local = datetime.now(ZoneInfo(SEND_TZ))
    scheduled_spec = _read_str_setting(CFG_SCHEDULED_SEND_LOCAL, DEFAULT_SCHEDULED_SEND_LOCAL)

    for row in candidates:
        row_id = row["_row_id"]

        # Pick the DRIVING approval column. `Send Now` (immediate) takes precedence;
        # else `Approve for Scheduled Send`, but only inside the scheduled window
        # (default Monday ≥07:00 Pacific) — otherwise the scheduled row waits.
        if bool(row.get(APPROVAL_COLUMN_SEND_NOW)):
            approval_column = APPROVAL_COLUMN_SEND_NOW
        elif _is_scheduled_window(now_local, scheduled_spec):
            approval_column = APPROVAL_COLUMN_SCHEDULED
        else:
            counters["skipped"] += 1  # approved-for-scheduled, not yet the window
            continue

        # F22: approval-attestation gate (fail-CLOSED) on the DRIVING column. Verify
        # the checkbox's current value was set by an authorized actor per Smartsheet
        # cell history BEFORE handing the row to the send process. A non-verified
        # verdict blocks THIS row only; other rows still dispatch.
        verdict = approval_verification.verify_approval(
            sheet_ids.SHEET_WSR_HUMAN_REVIEW,
            row_id,
            approval_column,
            authorized_actors=authorized_actors,
        )
        if not verdict.verified:
            counters["blocked"] += 1
            _handle_unverified(row_id, verdict)
            continue

        # Stamp the verified approver identity (Approved By/At) — best-effort audit.
        _stamp_approval(row_id, verdict)

        counters["dispatched"] += 1
        try:
            result = weekly_send.send_one_row(row_id)
        except smartsheet_client.SmartsheetError as exc:
            counters["errors"] += 1
            error_log.log(
                Severity.ERROR,
                SCRIPT_NAME,
                (
                    f"per-row SmartsheetError dispatching row_id={row_id}: {exc!r}"
                ),
                error_code="weekly_send_poll.dispatch_failed",
            )
            continue
        except Exception as exc:  # noqa: BLE001 — per-row fence
            counters["errors"] += 1
            error_log.log(
                Severity.ERROR,
                SCRIPT_NAME,
                (
                    f"per-row unexpected exception dispatching row_id={row_id}: "
                    f"{type(exc).__name__}: {exc!r}"
                ),
                error_code="weekly_send_poll.dispatch_failed",
            )
            continue

        if result.status == "sent":
            counters["sent"] += 1
        elif result.status.startswith("skipped"):
            counters["skipped"] += 1
        else:
            counters["failed"] += 1

    _write_heartbeat()

    # Determine cycle status. A blocked (unverified-approval) row is
    # security-relevant → at least WARN.
    if counters["errors"] > 0:
        cycle_status: HeartbeatStatus = "DEGRADED"
    elif counters["failed"] > 0 or counters["blocked"] > 0:
        cycle_status = "WARN"
    else:
        cycle_status = "OK"

    # F08: a degraded Smartsheet overrides the cycle status. Lock-free local
    # read (no Smartsheet call), safe even when the breaker is OPEN.
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"

    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=counters["dispatched"],
            error_summary=(
                None
                if counters["errors"] == 0
                and counters["failed"] == 0
                and counters["blocked"] == 0
                else (
                    f"errors={counters['errors']} failed={counters['failed']} "
                    f"blocked={counters['blocked']}"
                )
            ),
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat must never block
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"heartbeat write outer-catch tripped: {exc!r}",
            error_code="daemon_health_write_failed",
        )

    _write_watchdog_marker()

    error_log.log(
        Severity.INFO,
        SCRIPT_NAME,
        (
            f"poll cycle: scanned={len(rows)} dispatched={counters['dispatched']} "
            f"sent={counters['sent']} skipped={counters['skipped']} "
            f"failed={counters['failed']} errors={counters['errors']} "
            f"blocked={counters['blocked']}"
        ),
        error_code="poll_cycle_summary",
    )
    return PollStats(
        rows_scanned=len(rows),
        dispatched=counters["dispatched"],
        sent=counters["sent"],
        skipped=counters["skipped"],
        failed=counters["failed"],
        errors=counters["errors"],
        blocked=counters["blocked"],
    )


if __name__ == "__main__":
    poll_once()
