"""On-demand "Compile Now" poller (Part B) — the fast path for the weekly packet, ALL workstreams.

Each workstream's `weekly_generate` (safety Friday 14:00, progress Friday 14:30) compiles the
canonical Sat→Fri packet, but only on its scheduled launchd fire (+ the watchdog catch-up). So an
operator who checks **Compile Now** on a week sheet's Rollup row otherwise waits until the next
Friday run. This ONE daemon polls every ~90 s and compiles a TRIGGERED job-week within a minute or
two — producing the SAME canonical packet as the scheduled run (it reuses
`generate_core._compile_job_week`, never a second compile path).

Cross-workstream (§14 parameterize-not-clone)
---------------------------------------------
Rather than clone this daemon per workstream (a second plist, a second heartbeat row, a second
Check-C marker), it iterates a tuple of `generate_core.GenerateConfig` — one per workstream
(`COMPILE_CONFIGS`). Each config already carries every workstream-variant knob the compile needs
(the week-sheet config, the Active-Jobs sheet, the review sheet, the Box root, the workstream tag).
The per-job compile primitive is the SAME shared `generate_core._compile_job_week(config, …)` the
scheduled `weekly_generate` / `progress_weekly_generate` drive — so on-demand and scheduled compiles
are byte-identical per workstream. The daemon-level machinery (the single-flight lock, the ONE
ITS_Daemon_Health heartbeat row, the ONE Check-C watchdog marker) stays shared: it is ONE daemon on
ONE plist, reporting aggregate stats across the workstreams it serves.

WHY this leaf daemon may import `progress_reports` (an exception to the week_sheet.py rule): the
`week_sheet` module keeps `PROGRESS_WEEK_SHEET_CONFIG` in `safety_reports` precisely so that
widely-imported module never drags in `progress_reports`. This module is the opposite case — a
LEAF daemon nothing else imports (grep-verified: only its launchd entry point + tests), so importing
`progress_weekly_generate.PROGRESS_GENERATE_CONFIG` here creates no cycle and no broad coupling. It
is the cross-workstream compile-now orchestrator, structurally like `intake.py` routing both
workstreams — except intake reaches progress via `week_sheet`, while this daemon needs the full
`GenerateConfig`, which (binding `wpr_review` + the rollup provider) can only live in
`progress_reports`.

Lifecycle (single-flight + fail-loud, reusing the existing compile's own behaviour):
  - A file lock (single-flight) keeps two overlapping cycles from double-compiling.
  - For each ENABLED workstream config, per Active job's CURRENT week: read the Rollup row. ONLY if
    Compile Now is set → compile (on-demand; an unchecked job is skipped — this is NOT an
    auto-on-new-docs compiler).
  - The per-submission Compile Now boxes are the "include in this packet" SELECTION
    (default-all when none checked, Option 1); the compile narrows the packet to them.
  - SUCCESS: `_compile_job_week` APPENDS a new Rollup snapshot (append-only) + clears the
    Rollup trigger(s); we clear the submission selection too. FAILURE: `_compile_job_week`
    raises BEFORE the trigger clears, so the trigger + selection stay VISIBLY set (fail-loud)
    and the job routes to the Review Queue.

Per-workstream on/off gate: `<workstream>.compile_now_poll.polling_enabled` (default True), read
under that workstream. Safety's key is the pre-existing
`safety_reports.compile_now_poll.polling_enabled` (backward-compatible); progress adds
`progress_reports.compile_now_poll.polling_enabled`. A workstream toggled OFF is skipped; the daemon
still serves the others. All workstreams OFF → the cycle halts before taking the lock or writing a
heartbeat (identical to the pre-generalization "polling_disabled" behaviour).

**Zero send. Zero AI.** Enrolled in `tests/test_capability_gating.py::GATED_SCRIPTS` alongside
`weekly_generate` + `progress_weekly_generate` (same deterministic-actuation gate). It calls the same
dual-write as the scheduled runs, so it NEVER touches WSR/WPR approval / email-body columns
(`weekly_send` / `progress_send` only send approved rows). Successor-remediation runbook:
`docs/runbooks/compile_now_poll.md` (Op Stds §43).
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

from progress_reports import progress_weekly_generate
from safety_reports import generate_core, week_sheet, weekly_generate
from shared import active_jobs, circuit_breaker, error_log, safety_week, smartsheet_client
from shared.active_jobs import ActiveJob
from shared.error_log import Severity, its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.compile_now_poll"
# The daemon's HOME workstream — the tag for its ONE ITS_Daemon_Health row, its Check-C watchdog
# marker, and its @its_error_log attribution. NOT the compile scope: the workstreams it COMPILES
# are `COMPILE_CONFIGS` below (safety + progress). The daemon lives in safety_reports/ and keeps
# `safety_reports` as its home for backward-compatible observability (one plist, one health row).
WORKSTREAM = "safety_reports"
DEFAULT_TZ = "America/Los_Angeles"  # everything Pacific (Brief v6.1)

# The workstream compile bindings this ONE daemon serves (§14 parameterize-not-clone). Each is the
# SAME `GenerateConfig` the scheduled weekly compile drives, so on-demand == scheduled per workstream.
# Adding a future workstream = append its GenerateConfig here (+ a per-workstream polling row); no new
# plist, heartbeat, or marker.
COMPILE_CONFIGS: tuple[generate_core.GenerateConfig, ...] = (
    weekly_generate.SAFETY_GENERATE_CONFIG,
    progress_weekly_generate.PROGRESS_GENERATE_CONFIG,
)

DEFAULT_POLLING_ENABLED = True

STATE_DIR = Path.home() / "its" / "state"
LOCK_PATH = STATE_DIR / "compile_now_poll.lock"

# ITS_Daemon_Health heartbeat (R4-F1 — closes the deferred Part-B B3 self-provision row).
# HEARTBEAT_ROW_STATE_PATH is SHARED with the other daemons — same JSON file, different
# daemon_name key (ARCH-2). POLL_INTERVAL_SECONDS mirrors the plist StartInterval.
HEARTBEAT_PATH = STATE_DIR / "compile_now_poll_heartbeat.txt"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"
DAEMON_NAME = "safety_reports.compile_now_poll"
POLL_INTERVAL_SECONDS = 90

# A1 self-provision metadata (the ONLY per-daemon difference in the heartbeat helpers).
_REGISTRATION_SOURCE_ID = "Week-sheet Rollup 'Compile Now' triggers (Smartsheet, all workstreams)"

# Shared ITS_Daemon_Health reporter for this daemon (mirrors fieldops_sync / portal_poll).
_heartbeat_reporter = HeartbeatReporter(
    script_name=SCRIPT_NAME,
    daemon_name=DAEMON_NAME,
    workstream=WORKSTREAM,
    liveness_path=HEARTBEAT_PATH,
    interval_seconds=POLL_INTERVAL_SECONDS,
    source_id=_REGISTRATION_SOURCE_ID,
    row_state_path=HEARTBEAT_ROW_STATE_PATH,  # shared file — make the contract explicit
)

# Watchdog Check C marker — same pattern as the other daemons (preservation, §14). ONE marker for
# the ONE daemon (NOT per-workstream): the daemon is alive iff it writes this each cycle.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_compile_now_poll"


@dataclass
class CompileStats:
    """Summary of one poll_once() invocation (aggregate across all served workstreams)."""
    jobs_scanned: int = 0
    triggered: int = 0
    compiled: int = 0
    errors: int = 0
    halted: str = ""


# ---- Config readers (replicated per preservation) -----------------------


def _read_str_setting(key: str, workstream: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=workstream)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, workstream: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, workstream, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _polling_enabled(config: generate_core.GenerateConfig) -> bool:
    """Per-workstream runtime gate: `<workstream>.compile_now_poll.polling_enabled`, read under
    that workstream (default True). Safety resolves the pre-existing
    `safety_reports.compile_now_poll.polling_enabled` key (backward compatible)."""
    key = f"{config.workstream}.compile_now_poll.polling_enabled"
    return _read_bool_setting(key, config.workstream, DEFAULT_POLLING_ENABLED)


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
) -> None:
    """ITS_Daemon_Health per-cycle row update — thin delegator to the shared
    HeartbeatReporter (the canonical test mock seam). See shared/heartbeat.py (§42)."""
    _heartbeat_reporter.write_row(
        status=status,
        items_processed=items_processed,
        error_summary=error_summary,
        correlation_id=correlation_id,
        notes=notes,
    )


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
    config: generate_core.GenerateConfig,
    job: ActiveJob,
    week: safety_week.SafetyWeek,
    summary: generate_core.RunSummary,
    correlation_id: str,
) -> bool:
    """Compile ONE job's current week IFF its Rollup Compile-Now trigger is set, for the given
    workstream `config`. Returns True if a compile ran, False if the job was skipped (no trigger).
    Raises on a compile failure — the caller's per-job fence routes it to the Review Queue
    (fail-loud)."""
    sheet_id = week_sheet.ensure_week_sheet(
        config.week_sheet_config, job.project_name, week.start
    )
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
    # → default-all when no per-submission box is checked (Option 1). The `config` binds this
    # compile to the right workstream (week sheet, Box root, review sheet, workstream tag).
    # A6 deviation: unguarded — no SIGALRM fence, no memory ceiling (vs the scheduled run's
    # compile_core.run_per_job). Single-job, operator-triggered, lower OOM risk; a hung
    # Compile-Now needs a manual process kill.
    generate_core._compile_job_week(
        config, job, week, summary, correlation_id, selection=(selection or None)
    )
    # SUCCESS only (a failure raised above): _compile_job_week appended the new Rollup
    # snapshot and cleared the Rollup trigger(s) (clear_compile_now_on_rollups); clear the
    # per-submission selection too so it cannot narrow a later compile. A clear failure
    # RAISES → surfaced like any compile failure (fail-loud).
    week_sheet.clear_compile_now(sheet_id, selection)
    return True


def _poll_inside_lock(
    active_configs: tuple[generate_core.GenerateConfig, ...],
) -> CompileStats:
    correlation_id = uuid.uuid4().hex[:12]
    stats = CompileStats()
    summary = generate_core.RunSummary()
    week = safety_week.week_bounds(datetime.now(ZoneInfo(DEFAULT_TZ)).date())

    for config in active_configs:
        for job in active_jobs.list_active_jobs(config.active_jobs_config):
            stats.jobs_scanned += 1
            try:
                if _compile_triggered_job(config, job, week, summary, correlation_id):
                    stats.triggered += 1
                    stats.compiled += 1
            except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never blocks the rest
                stats.errors += 1
                error_log.log(
                    Severity.ERROR, SCRIPT_NAME,
                    f"[{config.workstream}] compile-now failed for {job.project_name} "
                    f"(job {job.job_id}) week {week.start}: {exc!r}",
                    error_code="compile_now_poll.compile_failed",
                    correlation_id=correlation_id,
                )
                # Fail-loud: the trigger stays SET (we never reached the clear); surface to the
                # Review Queue (tagged with the compile's workstream) so the operator sees the
                # un-run compile.
                generate_core._safe_review_queue(
                    config, job, week, type(exc).__name__, correlation_id, summary
                )

    _write_heartbeat()
    if stats.errors > 0:
        cycle_status: HeartbeatStatus = "DEGRADED"
    else:
        cycle_status = "OK"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"

    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=stats.compiled,
            error_summary=(None if stats.errors == 0 else f"errors={stats.errors}"),
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat must never block
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"heartbeat write outer-catch tripped: {exc!r}",
            error_code="daemon_health_write_failed",
        )
    _write_watchdog_marker()
    return stats


@its_error_log(script_name=SCRIPT_NAME)
@require_active
def poll_once() -> CompileStats:
    """One on-demand-compile cycle across all ENABLED workstreams. Single-shot (launchd handles the
    ~90 s cadence). The file lock makes it single-flight — an overlapping cycle (a slow compile)
    returns immediately rather than double-compiling the same job-week. When NO workstream is
    enabled, halts before taking the lock or writing a heartbeat."""
    active_configs = tuple(c for c in COMPILE_CONFIGS if _polling_enabled(c))
    if not active_configs:
        return CompileStats(halted="polling_disabled")
    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            return CompileStats(halted="locked")
        return _poll_inside_lock(active_configs)


if __name__ == "__main__":  # pragma: no cover
    poll_once()
