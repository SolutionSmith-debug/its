"""Safety Reports weekly send polling daemon — launchd-driven dispatcher.

Discovers approved `WPR_Pending_Review` rows and dispatches each to
`safety_reports.weekly_send.send_one_row`. The poller has zero send
capability of its own; it is an iterator + dispatcher. The handler is
the only place `graph_client.send_mail` is called.

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
  3. Read `WPR_Pending_Review` via `smartsheet_client.get_rows`. Filter
     client-side to rows that need send-attention:
       - Approved for Send = True
       - Send Status in (PENDING, FAILED) — terminally-failed rows
         (Send Retry Count >= MAX_SEND_RETRIES, encoded in Notes per
         the schema-degradation contract in weekly_send.py) are
         filtered out.
  4. For each candidate row: first run the F22 approval-attestation gate
     (`approval_verification.verify_approval`) — confirm the approval
     cell's current value was set by an authorized actor per Smartsheet
     cell history. A non-verified verdict BLOCKS that row (fail-closed)
     with a forensic `approval_unverified` event and the cycle moves on;
     other rows still dispatch. Verified rows invoke
     `weekly_send.send_one_row(row_id)`. The handler returns a `SendResult`
     — the poller logs the outcome and continues. SmartsheetError raised by
     the handler caught per-row; the cycle continues to the next row.
  5. Write file heartbeat (`HEARTBEAT_PATH`).
  6. Write ITS_Daemon_Health row (PR #60 pattern; helpers replicated
     verbatim from `intake_poll.py` per preservation-over-refactor
     until weekly_send stabilizes through 1-2 real Friday cycles).
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

Tech-debt context (2026-05-23)
-------------------------------

The heartbeat helpers (`_load_heartbeat_row_state`,
`_persist_heartbeat_row_state`, `_invalidate_heartbeat_row_state`,
`_resolve_heartbeat_row_id`, `_write_heartbeat`, `_write_heartbeat_row`,
`_log_heartbeat_failure`) are replicated VERBATIM from
`safety_reports/intake_poll.py`. The polling-daemon doctrine
(Op Stds v11 §14, preservation-over-refactor) flags 2nd-consumer as
the extraction trigger; this is that 2nd consumer. Extraction to
`shared/heartbeat.py` is tracked in `docs/tech_debt.md` — deferred to
the consolidation PR after weekly_send stabilizes through 1-2 real
Friday cycles. Inline duplication keeps this ship focused.
"""
from __future__ import annotations

import fcntl
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from safety_reports import weekly_send
from shared import (
    approval_verification,
    circuit_breaker,
    error_log,
    sheet_ids,
    smartsheet_client,
    state_io,
)
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.weekly_send_poll"
WORKSTREAM = "safety_reports"

# ITS_Config keys.
CFG_POLLING_ENABLED = "safety_reports.weekly_send.polling_enabled"
CFG_POLL_INTERVAL = "safety_reports.weekly_send.poll_interval_seconds"

# F22 — approval-attestation gate. The authorized-approver allowlist lives in
# ITS_Config (config-driven so it can be swapped at the evergreenmirror.com →
# evergreenrenewables.com cutover without a code change — see
# docs/operations/cutover_checklist.md). APPROVAL_COLUMN is the same CHECKBOX
# the dispatch filter reads via `bool(row.get("Approved for Send"))`.
CFG_AUTHORIZED_APPROVERS = "safety_reports.authorized_approvers"
APPROVAL_COLUMN = "Approved for Send"

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

# Watchdog Check C marker — matches `TRACKED_JOBS` entry in scripts/watchdog.py.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_weekly_send_poll"

# Allowed cycle-status values written to ITS_Daemon_Health.Last_Cycle_Status.
# CIRCUIT_OPEN (F08) overrides the cycle status when the Smartsheet circuit
# breaker is OPEN (lock-free is_open() at the status-determination point); the
# heartbeat write itself runs under circuit_breaker.bypass() so the status can
# still land when Smartsheet is reachable.
HeartbeatStatus = Literal[
    "OK", "WARN", "ERROR", "DEGRADED", "SKIPPED", "CIRCUIT_OPEN"
]

# Send Status values the poller dispatches on. SENT rows are skipped
# (already done); HELD rows are skipped (operator-driven hold; reserved
# for future use); PENDING + FAILED rows are dispatch candidates.
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
    """Read the F22 authorized-approver allowlist from ITS_Config.

    A missing row → empty set (the legitimate "not yet seeded" / cutover
    case), which `verify_approval` treats as fail-closed (block all sends),
    NEVER fail-open. Parsing is delegated to
    `approval_verification.parse_authorized_actors` so the comma-separated
    value has a single interpretation. A non-NotFound SmartsheetError (auth
    / 500) propagates to the `@its_error_log` CRITICAL path — a config-read
    infra failure aborts the cycle loudly with zero sends, retried next
    cycle (fail-closed, and consistent with `_read_str_setting`'s posture).
    """
    try:
        raw = smartsheet_client.get_setting(
            CFG_AUTHORIZED_APPROVERS, workstream=WORKSTREAM
        )
    except smartsheet_client.SmartsheetNotFoundError:
        raw = None
    return approval_verification.parse_authorized_actors(raw)


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
    """Overwrite the heartbeat file with the current UTC ISO timestamp."""
    state_io.atomic_write_text(HEARTBEAT_PATH, datetime.now(UTC).isoformat())


# ---- Heartbeat-row state cache (ITS_Daemon_Health) ----------------------
# Replicated verbatim from intake_poll per preservation-over-refactor;
# extraction to shared/heartbeat.py is the tech-debt follow-on after
# weekly_send stabilizes through 1-2 real Friday cycles.


def _load_heartbeat_row_state(daemon_name: str) -> dict[str, Any] | None:
    """Read `{daemon_name: {row_id, total_cycles}}` from the state file."""
    if not HEARTBEAT_ROW_STATE_PATH.exists():
        return None
    try:
        raw = HEARTBEAT_ROW_STATE_PATH.read_text()
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    entry = parsed.get(daemon_name)
    if not isinstance(entry, dict):
        return None
    row_id = entry.get("row_id")
    total_cycles = entry.get("total_cycles")
    if not isinstance(row_id, int) or not isinstance(total_cycles, int):
        return None
    return {"row_id": row_id, "total_cycles": total_cycles}


def _persist_heartbeat_row_state(
    daemon_name: str, row_id: int, total_cycles: int
) -> None:
    """Atomically merge `{daemon_name: {row_id, total_cycles}}` into the state file.

    Shared-file lock contract per intake_poll: read-modify-write triple
    runs under `state_io.with_path_lock`. Lock-timeout fails open: log
    WARN + skip (next cycle re-tries).
    """
    HEARTBEAT_ROW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with state_io.with_path_lock(HEARTBEAT_ROW_STATE_PATH):
            current: dict[str, Any] = {}
            if HEARTBEAT_ROW_STATE_PATH.exists():
                try:
                    parsed = json.loads(HEARTBEAT_ROW_STATE_PATH.read_text())
                    if isinstance(parsed, dict):
                        current = parsed
                except (OSError, json.JSONDecodeError):
                    current = {}
            current[daemon_name] = {"row_id": row_id, "total_cycles": total_cycles}
            state_io.atomic_write_json(HEARTBEAT_ROW_STATE_PATH, current)
    except state_io.StateLockTimeoutError:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries",
            error_code="daemon_health_write_failed",
        )


def _invalidate_heartbeat_row_state(daemon_name: str) -> None:
    """Remove a daemon's entry from the state file (forces re-lookup).

    Same shared-file lock contract as `_persist_heartbeat_row_state`.
    Lock-timeout fails open: stale cache resurfaces and re-resolves on
    next cycle's 404.
    """
    if not HEARTBEAT_ROW_STATE_PATH.exists():
        return
    try:
        with state_io.with_path_lock(HEARTBEAT_ROW_STATE_PATH):
            try:
                parsed = json.loads(HEARTBEAT_ROW_STATE_PATH.read_text())
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(parsed, dict):
                return
            parsed.pop(daemon_name, None)
            state_io.atomic_write_json(HEARTBEAT_ROW_STATE_PATH, parsed)
    except state_io.StateLockTimeoutError:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries (invalidate)",
            error_code="daemon_health_write_failed",
        )


def _resolve_heartbeat_row_id(daemon_name: str) -> int | None:
    """Look up the ITS_Daemon_Health row id for `daemon_name`. Caches on first hit."""
    state = _load_heartbeat_row_state(daemon_name)
    if state is not None:
        return state["row_id"]
    row = smartsheet_client.find_row_by_primary(
        sheet_ids.SHEET_DAEMON_HEALTH,
        sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
        daemon_name,
    )
    if row is None:
        return None
    row_id = int(row["_row_id"])
    _persist_heartbeat_row_state(daemon_name, row_id, total_cycles=0)
    return row_id


def _write_heartbeat_row(
    *,
    status: HeartbeatStatus,
    items_processed: int,
    error_summary: str | None = None,
    correlation_id: str | None = None,
    notes: str | None = None,
    daemon_name: str = DAEMON_NAME,
) -> None:
    """Write one row to ITS_Daemon_Health summarizing this cycle.

    Same ARCH-1/ARCH-2/ARCH-3 semantics as intake_poll (PR #59.5):
    Enabled column is filter metadata only; row-id cached; total_cycles
    is lifetime monotonic. Failures internally caught and logged; the
    daemon's primary work is never blocked by heartbeat-write failures.
    """
    cols = sheet_ids.DAEMON_HEALTH_COLUMNS

    state = _load_heartbeat_row_state(daemon_name)
    if state is None:
        try:
            row_id = _resolve_heartbeat_row_id(daemon_name)
        except smartsheet_client.SmartsheetError as exc:
            _log_heartbeat_failure(daemon_name, f"row-id lookup failed: {exc!r}")
            return
        if row_id is None:
            _log_heartbeat_failure(
                daemon_name,
                "no ITS_Daemon_Health row with this primary key — seeder needed",
            )
            return
        total_cycles = 0
    else:
        row_id = state["row_id"]
        total_cycles = state["total_cycles"]

    new_total = total_cycles + 1

    cells: dict[int, Any] = {
        cols["last_heartbeat"]: datetime.now(UTC).isoformat(),
        cols["last_cycle_status"]: status,
        cols["last_cycle_items_processed"]: items_processed,
        cols["total_cycles"]: new_total,
    }
    if error_summary is not None:
        cells[cols["last_error_summary"]] = error_summary
    if correlation_id is not None:
        cells[cols["last_error_correlation_id"]] = correlation_id
    if notes is not None:
        cells[cols["notes"]] = notes

    try:
        # F08: bypass the breaker for this control-plane write so a CIRCUIT_OPEN
        # status can still land when Smartsheet is reachable — the
        # already-once-per-cycle heartbeat write, no new hammering.
        with circuit_breaker.bypass():
            smartsheet_client.update_row_cells_by_id(
                sheet_ids.SHEET_DAEMON_HEALTH, row_id, cells
            )
    except smartsheet_client.SmartsheetNotFoundError:
        _invalidate_heartbeat_row_state(daemon_name)
        _log_heartbeat_failure(
            daemon_name,
            f"row {row_id} not found — cache invalidated",
        )
        return
    except smartsheet_client.SmartsheetError as exc:
        _log_heartbeat_failure(daemon_name, f"SmartsheetError: {exc!r}")
        return
    except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
        _log_heartbeat_failure(daemon_name, f"unexpected: {exc!r}")
        return

    _persist_heartbeat_row_state(daemon_name, row_id, new_total)


def _log_heartbeat_failure(daemon_name: str, detail: str) -> None:
    """Log a heartbeat-write failure to ITS_Errors with the standard error code."""
    error_log.log(
        Severity.WARN,
        SCRIPT_NAME,
        f"heartbeat write for daemon={daemon_name!r} failed: {detail}",
        error_code="daemon_health_write_failed",
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
    """Return rows that need send-attention this cycle.

    Filter rules (all must be true):
      - Approved for Send is truthy.
      - Send Status in {PENDING, FAILED}.
      - If Send Status == FAILED, Notes-encoded retry count <
        MAX_SEND_RETRIES (skip terminally-failed rows — they need human
        resolution).

    The handler still re-checks the state on each dispatch (race-tolerant);
    this filter is just to reduce the dispatch volume per cycle.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not bool(row.get("Approved for Send")):
            continue
        status = row.get("Send Status") or weekly_send.STATUS_PENDING
        if status not in DISPATCH_STATUSES:
            continue
        if status == weekly_send.STATUS_FAILED:
            retry_count = weekly_send._parse_retry_count(row.get("Notes"))
            if retry_count >= weekly_send.MAX_SEND_RETRIES:
                continue
        out.append(row)
    return out


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
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_WPR_PENDING_REVIEW)
    except smartsheet_client.SmartsheetError as exc:
        error_log.log(
            Severity.ERROR,
            SCRIPT_NAME,
            f"failed to read WPR_Pending_Review: {exc!r}",
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

    for row in candidates:
        row_id = row["_row_id"]

        # F22: approval-attestation gate (fail-CLOSED). Verify the approval
        # cell's current value was set by an authorized actor per Smartsheet
        # cell history BEFORE handing the row to the send process. A
        # non-verified verdict blocks THIS row only; other rows still
        # dispatch.
        verdict = approval_verification.verify_approval(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            row_id,
            APPROVAL_COLUMN,
            authorized_actors=authorized_actors,
        )
        if not verdict.verified:
            counters["blocked"] += 1
            _handle_unverified(row_id, verdict)
            continue

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
