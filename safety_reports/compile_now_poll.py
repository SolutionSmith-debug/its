"""On-demand "Compile Now" poller (Part B) — the fast path for the weekly safety packet.

`weekly_generate` compiles the canonical Sat→Fri packet, but only on its Friday 14:00
launchd fire (+ the watchdog catch-up). So an operator who checks **Compile Now** on a week
sheet's Rollup row otherwise waits until the next Friday run. This daemon polls every ~90 s
and compiles a TRIGGERED job-week within a minute or two — producing the SAME canonical
packet (it reuses `weekly_generate._compile_job_week`, never a second compile path).

Lifecycle (single-flight + fail-loud, reusing the existing compile's own behaviour):
  - A file lock (single-flight) keeps two overlapping cycles from double-compiling.
  - Per Active job's CURRENT week: read the Rollup row. ONLY if Compile Now is set → compile
    (on-demand; an unchecked job is skipped — this is NOT an auto-on-new-docs compiler).
  - The per-submission Compile Now boxes are the "include in this packet" SELECTION
    (default-all when none checked, Option 1); the compile narrows the packet to them.
  - SUCCESS: `_compile_job_week` APPENDS a new Rollup snapshot (append-only) + clears the
    Rollup trigger(s); we clear the submission selection too. FAILURE: `_compile_job_week`
    raises BEFORE the trigger clears, so the trigger + selection stay VISIBLY set (fail-loud)
    and the job routes to the Review Queue.

**Zero send. Zero AI.** Enrolled in `tests/test_capability_gating.py::GATED_SCRIPTS` alongside
`weekly_generate` (same deterministic-actuation gate). It calls the same dual-write as the
Friday run, so it NEVER touches WSR approval / email-body columns (`weekly_send` only sends
approved rows).
"""
from __future__ import annotations

import fcntl
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from safety_reports import week_sheet, weekly_generate
from shared import active_jobs, error_log, safety_week, smartsheet_client
from shared.active_jobs import ActiveJob
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.compile_now_poll"
WORKSTREAM = "safety_reports"
DEFAULT_TZ = "America/Los_Angeles"  # everything Pacific (Brief v6.1)

CFG_POLLING_ENABLED = "safety_reports.compile_now_poll.polling_enabled"
DEFAULT_POLLING_ENABLED = True

STATE_DIR = Path.home() / "its" / "state"
LOCK_PATH = STATE_DIR / "compile_now_poll.lock"

# Watchdog Check C marker — same pattern as the other daemons (preservation, §14).
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_compile_now_poll"


@dataclass
class CompileStats:
    """Summary of one poll_once() invocation."""
    jobs_scanned: int = 0
    triggered: int = 0
    compiled: int = 0
    errors: int = 0
    halted: str = ""


# ---- Config readers (replicated per preservation) -----------------------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _polling_enabled() -> bool:
    return _read_bool_setting(CFG_POLLING_ENABLED, DEFAULT_POLLING_ENABLED)


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


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this cycle (liveness). Never raises —
    observability must not break the daemon."""
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        (WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run").write_text(
            datetime.now(UTC).isoformat()
        )
    except OSError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- Per-job on-demand compile ------------------------------------------


def _compile_triggered_job(
    job: ActiveJob,
    week: safety_week.SafetyWeek,
    summary: weekly_generate.RunSummary,
    correlation_id: str,
) -> bool:
    """Compile ONE job's current week IFF its Rollup Compile-Now trigger is set. Returns
    True if a compile ran, False if the job was skipped (no trigger). Raises on a compile
    failure — the caller's per-job fence routes it to the Review Queue (fail-loud)."""
    sheet_id = week_sheet.ensure_week_sheet(job.project_name, week.start)
    # The Compile-Now trigger lives on a Rollup row; the placeholder Rollup is pre-created at
    # sheet creation so the checkbox exists even before the first compile. With append-only
    # Rollups (one immutable snapshot per compile), the operator may check the trigger on the
    # latest (or any) Rollup row, so we look across ALL of them.
    rollup_rows = week_sheet.list_rollup_rows(sheet_id)
    if not week_sheet.any_compile_now_requested(rollup_rows):
        return False  # on-demand only — an unchecked job is NOT auto-compiled

    submissions = week_sheet.list_submission_rows(sheet_id, active_only=True)
    selection = week_sheet.selected_submission_row_ids(submissions)
    # Reuse the EXISTING deterministic compile (no second compile path). selection or None
    # → default-all when no per-submission box is checked (Option 1).
    weekly_generate._compile_job_week(
        job, week, summary, correlation_id, selection=(selection or None)
    )
    # SUCCESS only (a failure raised above): _compile_job_week appended the new Rollup
    # snapshot and cleared the Rollup trigger(s) (clear_compile_now_on_rollups); clear the
    # per-submission selection too so it cannot narrow a later compile. A clear failure
    # RAISES → surfaced like any compile failure (fail-loud).
    week_sheet.clear_compile_now(sheet_id, selection)
    return True


def _poll_inside_lock() -> CompileStats:
    correlation_id = uuid.uuid4().hex[:12]
    stats = CompileStats()
    summary = weekly_generate.RunSummary()
    week = safety_week.week_bounds(datetime.now(ZoneInfo(DEFAULT_TZ)).date())

    for job in active_jobs.list_active_jobs():
        stats.jobs_scanned += 1
        try:
            if _compile_triggered_job(job, week, summary, correlation_id):
                stats.triggered += 1
                stats.compiled += 1
        except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never blocks the rest
            stats.errors += 1
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"compile-now failed for {job.project_name} (job {job.job_id}) "
                f"week {week.start}: {exc!r}",
                error_code="compile_now_poll.compile_failed",
                correlation_id=correlation_id,
            )
            # Fail-loud: the trigger stays SET (we never reached the clear); surface to the
            # Review Queue so the operator sees the un-run compile.
            weekly_generate._safe_review_queue(
                job, week, type(exc).__name__, correlation_id, summary
            )

    _write_watchdog_marker()
    return stats


@its_error_log(script_name=SCRIPT_NAME)
@require_active
def poll_once() -> CompileStats:
    """One on-demand-compile cycle. Single-shot (launchd handles the ~90 s cadence). The
    file lock makes it single-flight — an overlapping cycle (a slow compile) returns
    immediately rather than double-compiling the same job-week."""
    if not _polling_enabled():
        return CompileStats(halted="polling_disabled")
    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            return CompileStats(halted="locked")
        return _poll_inside_lock()


if __name__ == "__main__":  # pragma: no cover
    poll_once()
