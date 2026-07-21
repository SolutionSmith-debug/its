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

Scan-failure reporting (2026-07-21)
-----------------------------------
The trigger scan runs ~3 Smartsheet calls for EVERY Active job in EVERY served workstream every
~90 s, so a flaky sheet used to write one ERROR row per job per cycle. It now emits AT MOST ONE
summarized row per pass, with two escalations layered on: a CYCLE counter
(`compile_now_scan_sustained`) fires CRITICAL once a majority-failing cycle repeats, and a per-JOB
ledger (`compile_now_job_scan_sustained`) fires CRITICAL for a single job whose sheet stays
unreachable. BOTH escalations re-fire on a geometric ladder (threshold, 2×, 4×, 8× … consecutive
cycles); every cycle in between still records its row, at ERROR, because an open CRITICAL is never
rotatable and a per-cycle CRITICAL on a 90 s daemon is thousands of unreclaimable rows a day
(`_is_escalation_cycle`). Every summarized row (ERROR and CRITICAL alike) carries
the CAUSE — the distinct exception types plus a bounded sample of their messages — because the
scan is fenced with a bare `except Exception`, so a code regression would otherwise be reported in
exactly the words of a transient Smartsheet incident (`_cause_clause`). A failed ITS_Active_Jobs
read — which contributes zero scanned jobs and used to leave a clean OK heartbeat — counts as a
failing cycle and is named in the summary row (`shared.active_jobs.last_read_failed`); such a
BLIND cycle skips the per-job ledger write entirely, since it scanned nothing and a write would
wipe the counts. See `_record_scan_outcome`.

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
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from progress_reports import progress_weekly_generate
from safety_reports import generate_core, week_sheet, weekly_generate
from shared import (
    active_jobs,
    circuit_breaker,
    error_log,
    safety_week,
    smartsheet_client,
    state_io,
    sustained_failure,
)
from shared.active_jobs import ActiveJob
from shared.error_log import Severity, its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

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

# #336 — the ONLY ITS_Config key this daemon resolves at runtime is the per-workstream
# derived gate `<workstream>.compile_now_poll.polling_enabled` (read under that workstream,
# default True). Built by iterating COMPILE_CONFIGS so a future served workstream is covered
# automatically. Declared here for the startup observability pass (resolve_and_log).
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(
        f"{cfg.workstream}.compile_now_poll.polling_enabled",
        cfg.workstream,
        DEFAULT_POLLING_ENABLED,
        "bool",
    )
    for cfg in COMPILE_CONFIGS
]

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
    # Scan-phase failures only (the routine per-job trigger scan) — the subset of `errors`
    # the per-pass summary + the sustained-outage predicate below are computed from.
    scan_failures: int = 0
    # Served workstreams whose ITS_Active_Jobs read FAILED this cycle. Those workstreams
    # contribute zero scanned jobs, so without this the cycle looks clean.
    active_jobs_read_failures: int = 0


# ---- Scan-failure summarization + sustained-outage escalation ------------
#
# WHY (2026-07-21 forensic): the per-job `except _ScanFailedError` branch wrote ONE
# Severity.ERROR ITS_Errors row PER JOB PER FAILING CYCLE — 31 rows in a day at 10-20 jobs
# every 90s — with no dedupe, no summarization, and no escalation, so a real sustained
# outage looked exactly like flake and never reached a CRITICAL-keyed fire surface.

#: A cycle counts as FAILING once at least this fraction of the jobs it SCANNED failed their
#: trigger scan. Deliberately a fraction, not all-or-any: an all-jobs-failed predicate never
#: fires during a sustained PARTIAL outage (18 of 20 jobs failing forever) and structurally
#: cannot fire when jobs_scanned == 0; an any-job-failed predicate makes essentially every
#: cycle failing under a mild 1-in-3 flake and would escalate to CRITICAL in ~7.5 minutes
#: because one sheet is slow.
SCAN_FAILURE_CYCLE_FRACTION = 0.5

#: Failing jobs named individually in the pass summary before it degrades to "…and N more".
SCAN_SUMMARY_SAMPLE = 5

#: DISTINCT failure details quoted verbatim in the pass summary (the exception TYPE names are
#: ALWAYS all listed — they are short and they are what tells a Smartsheet outage apart from a
#: code regression). Without a cause the summary says "20/20 jobs failed" and nothing else, so a
#: TypeError from a schema change reads exactly like a 500 and lands the operator on the
#: "wait, it self-heals" runbook branch. PR #608's summarizer (`shared/required_config.py`) is
#: the precedent: it emits the distinct exception types with the collapsed count.
#: The sampled messages are ONE PER DISTINCT TYPE, most-common type first — see `_cause_clause`
#: for why the selection is not alphabetical-by-message.
SCAN_CAUSE_SAMPLE = 2

#: Per-detail character ceiling in the summary row — a repr'd SDK error can be very long and the
#: row must stay readable in the Smartsheet cell.
SCAN_CAUSE_DETAIL_MAX = 240

#: Consecutive failing cycles ONE job must accumulate before its own CRITICAL — ~30 min at
#: the 90s cadence, well past any transient. This covers the case the cycle-level fraction
#: cannot see: a single job's week sheet 500ing for hours while every other job scans fine.
JOB_SCAN_CRITICAL_THRESHOLD = 20

#: Geometric re-notify base for BOTH escalations: past its threshold, a streak re-fires
#: CRITICAL only at threshold × 2ⁿ (threshold, 2×, 4×, 8× …). See `_is_escalation_cycle`.
ESCALATION_LADDER_FACTOR = 2

# Cycle-level escalation, on the shared 5-consecutive-cycles threshold (~7.5 min at 90s —
# the fast-daemon cadence class).
_SCAN_FAILS = sustained_failure.SustainedFailureCounter(
    STATE_DIR / "compile_now_scan_failures.json",
    SCRIPT_NAME,
    "compile_now_scan_counter_failed",
)


@dataclass(frozen=True)
class _ScanFailure:
    """One job whose routine trigger scan failed this cycle (collected, not logged inline)."""

    workstream: str
    project_name: str
    job_id: str
    detail: str
    #: Class name of the UNDERLYING exception (the `_ScanFailedError`'s `__cause__`) — the one
    #: token that separates "Smartsheet is 500ing" from "a schema change broke the scan code".
    #: Always carried into the pass summary; see `_cause_clause`.
    exc_type: str

    @property
    def key(self) -> str:
        """Ledger key — workstream-qualified because the two Active-Jobs sheets number
        their Job IDs independently, so a bare job_id can collide across workstreams."""
        return f"{self.workstream}:{self.job_id}"

    @property
    def label(self) -> str:
        return f"[{self.workstream}] {self.project_name} ({self.job_id})"


class _JobScanLedger:
    """Per-job consecutive scan-failure counts, one JSON map under ``~/its/state/``.

    Deliberately PRIVATE to this daemon instead of a `shared/` abstraction:
    `shared/sustained_failure.py` was extracted with FOUR immediate consumers, and a keyed
    variant with one consumer is thin against preservation-over-refactor (§14). Convergence
    candidates if a second daemon ever needs per-item escalation: `estimate_poll._load_flags`
    (its one-shot per-row refusal map) and `portal_poll`'s bad-HMAC flagging.

    ONE locked read-modify-write per cycle rather than a per-key record/reset/sweep API: this
    daemon scans 10-20 jobs every 90s, so per-key calls would take the sidecar flock and
    rewrite the file N times a cycle to express what one write expresses.

    Only CURRENTLY-FAILING keys are persisted, which makes reset-on-success and
    sweep-of-departed-jobs both structural (a key absent from `failed_keys` does not survive
    the write) and bounds the file to the failing set. The corollary is a HARD CONTRACT on the
    caller: `apply()` must only be called for a cycle that actually scanned every served
    workstream's jobs. A blind cycle (ITS_Active_Jobs read failed) passing its empty failure
    set here would silently wipe the counts, so `_record_scan_outcome` skips the write
    entirely on such a cycle.
    """

    def __init__(self, path: Path, script_name: str, counter_error_code: str) -> None:
        self._path = path
        self._script = script_name
        self._counter_error_code = counter_error_code

    def apply(self, failed_keys: set[str]) -> dict[str, int]:
        """Bump each failing key, drop every other key; return the new per-key counts.

        A state error degrades every failing key to 1 with a WARN — never page off a state
        glitch (mirrors `SustainedFailureCounter.record`).
        """
        try:
            with state_io.with_path_lock(self._path):
                previous = self._read()
                counts = {key: previous.get(key, 0) + 1 for key in failed_keys}
                state_io.atomic_write_json(self._path, {"counts": counts})
                return counts
        except Exception as exc:  # noqa: BLE001 — ledger is best-effort, like the shared counter
            error_log.log(
                Severity.WARN, self._script,
                f"per-job scan ledger write failed (treating each as #1): {exc!r}",
                error_code=self._counter_error_code,
            )
            return dict.fromkeys(failed_keys, 1)

    def _read(self) -> dict[str, int]:
        """Load the persisted counts; ANY unusable state reads as empty (never raises)."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text()).get("counts")
        except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        # `isinstance(True, int)` is True and a negative count is nonsense — both would
        # round-trip into a real consecutive-cycle count, so the guard rejects them.
        return {
            str(k): v
            for k, v in raw.items()
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        }


_JOB_LEDGER = _JobScanLedger(
    STATE_DIR / "compile_now_job_scan_failures.json",
    SCRIPT_NAME,
    "compile_now_job_scan_ledger_failed",
)


# ---- Config readers (replicated per preservation) -----------------------


def _read_str_setting(key: str, workstream: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=workstream)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    except smartsheet_client.SmartsheetError as exc:
        # Transient read failure (timeout / 5xx) — a single-cycle blip must not
        # escape to @its_error_log as a spurious CRITICAL. WARN + fall open to
        # the fallback, same disposition as the circuit-open branch above.
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"config read failed for {key}: {exc!r} — using fallback {fallback!r}",
            error_code="config_read_error",
        )
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


class _ScanFailedError(Exception):
    """Internal: the routine trigger scan (ensure_week_sheet / list_rollup_rows) failed
    BEFORE the Compile-Now trigger was confirmed set. Raised so the caller's per-job fence
    can distinguish a transient blip while scanning an (almost always) UNTRIGGERED job from
    a real failure of an operator-requested compile — the former logs `scan_failed` and does
    NOT seed a Review-Queue row (it was feeding a review backlog); the latter keeps the
    fail-loud `compile_failed` + Review-Queue behaviour."""


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
    (fail-loud). A failure BEFORE the trigger is confirmed set raises `_ScanFailedError`
    instead (scan phase — no Review-Queue row)."""
    # SCAN PHASE — reads that happen for EVERY Active job each cycle, before we know whether
    # this job is triggered at all. Fenced separately: a transient Smartsheet error here is a
    # routine-scan blip, not a failed operator-requested compile.
    try:
        sheet_id = week_sheet.ensure_week_sheet(
            config.week_sheet_config, job.project_name, week.start
        )
        # The Compile-Now trigger lives on a Rollup row; the placeholder Rollup is pre-created at
        # sheet creation so the checkbox exists even before the first compile. With append-only
        # Rollups (one immutable snapshot per compile), the operator may check the trigger on the
        # latest (or any) Rollup row, so we look across ALL of them.
        rollup_rows = week_sheet.list_rollup_rows(sheet_id)
        triggered = week_sheet.any_compile_now_requested(rollup_rows)
    except Exception as exc:  # noqa: BLE001 — re-raised typed; the caller fences per job
        raise _ScanFailedError(f"trigger scan failed: {exc!r}") from exc
    if not triggered:
        return False  # on-demand only — an unchecked job is NOT auto-compiled
    # TRIGGER CONFIRMED — from here on, any failure is a real un-run compile (fail-loud:
    # compile_failed + Review-Queue row, trigger stays set).

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


def _cycle_is_failing(stats: CompileStats) -> bool:
    """Whether this cycle counts toward the sustained-outage counter (see
    SCAN_FAILURE_CYCLE_FRACTION for why the predicate is a fraction)."""
    if stats.active_jobs_read_failures:
        # A workstream whose job list could not be read scanned nothing at all — the
        # fraction below cannot express that, and it is the worse outage of the two.
        return True
    if stats.jobs_scanned == 0:
        return False
    return stats.scan_failures / stats.jobs_scanned >= SCAN_FAILURE_CYCLE_FRACTION


def _is_escalation_cycle(consecutive: int, threshold: int) -> bool:
    """True on the threshold-CROSSING cycle and then only at threshold × 2ⁿ (2×, 4×, 8× …).

    WHY A LADDER AND NOT EVERY CYCLE. Both escalations here are per-occurrence records on a
    ~90 s daemon, so "CRITICAL every cycle past the threshold" is ~960 rows/day per failing
    job (measured: a 3-job total outage produced 263 `ITS_Errors` rows in two hours; a
    20-job/6-dead deployment ~5,760 rows/day). An open CRITICAL is NEVER terminal —
    `shared.errors_rotation.errors_row_is_terminal` excludes it — so watchdog Check O and the
    dashboard clear verb cannot reclaim those rows at ANY floor. `ITS_Errors` reached 19,975
    of its 20,000-row hard cap on 2026-07-13 and fired a "NOTHING is deletable" lockout
    twice; an unbounded CRITICAL stream re-opens that by the one route rotation cannot
    rescue, and buries the real open CRITICALs that watchdog Check B and the dashboard
    Open-CRITICALs panel exist to surface.

    WHY DOCTRINE IS STILL SATISFIED. §3.1's per-occurrence RECORD mandate is met in full —
    every failing cycle still writes its own `ITS_Errors` row; the ladder only decides that
    row's SEVERITY. A genuine sustained outage still escalates to CRITICAL on the crossing
    cycle and still RE-notifies on a widening interval (so a hours-long outage wakes the
    operator more than once), while every intermediate row stays terminal and therefore
    reclaimable. The push legs were already `alert_dedupe`-bounded on (script, error_code);
    this bounds the record leg the same way in spirit.

    CONVERGENCE TARGET: `shared.sustained_failure` — this is deliberately a small private
    helper rather than a shared one because an identical ladder is landing there on a
    parallel branch; converge onto it once both land (`docs/tech_debt.md`).
    """
    if threshold <= 0 or consecutive < threshold:
        return False
    multiple, remainder = divmod(consecutive, threshold)
    if remainder:
        return False
    # `multiple` >= 1 here, so the power-of-`FACTOR` test is a plain repeated divide.
    while multiple % ESCALATION_LADDER_FACTOR == 0:
        multiple //= ESCALATION_LADDER_FACTOR
    return multiple == 1


def _next_escalation_cycle(consecutive: int, threshold: int) -> int:
    """The next ladder rung STRICTLY above `consecutive` — quoted in the intermediate ERROR
    rows so the operator can see the outage is known and when it will page again."""
    if threshold <= 0:
        return 0
    rung = threshold
    while rung <= consecutive:
        rung *= ESCALATION_LADDER_FACTOR
    return rung


def _cause_clause(failures: list[_ScanFailure]) -> str:
    """The WHY of the summary row: every distinct exception type, plus a bounded verbatim
    sample of the messages.

    Load-bearing, not decorative. `_compile_triggered_job` fences the scan with a BARE
    `except Exception`, so a genuine code regression (TypeError / AttributeError / KeyError
    after a schema change) arrives here indistinguishable from a Smartsheet 500 — and a
    causeless "20/20 scanned jobs failed" row escalates as `compile_now_scan_sustained`,
    whose runbook branch tells the operator this class self-heals. Carrying the cause is what
    keeps a real bug from hiding inside the transient classification.
    """
    if not failures:
        return ""
    types = sorted({f.exc_type for f in failures})

    # SELECTION: one representative message per distinct exception TYPE, types ordered by
    # descending failure count then name. NOT alphabetical-by-message, which is arbitrary and
    # can drop the decisive quote — a lone TypeError among Smartsheet 500s loses the coin flip
    # to whichever message happens to sort first, and both slots can be spent on two spellings
    # of the SAME cause. One-per-type guarantees the quotes are diverse; frequency ordering
    # puts the dominant failure mode first. Deterministic throughout — every tie breaks on the
    # sorted name/message, so the same cycle always renders the same row.
    per_type: dict[str, dict[str, int]] = {}
    for failure in failures:
        bucket = per_type.setdefault(failure.exc_type, {})
        bucket[failure.detail] = bucket.get(failure.detail, 0) + 1
    ranked = sorted(per_type.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))

    representatives: list[str] = []
    for _exc_type, messages in ranked:
        # Within a type, the most frequent message (alphabetical tie-break).
        pick = min(messages.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if pick not in representatives:  # defensive: two types could share a detail string
            representatives.append(pick)

    quoted = [
        d if len(d) <= SCAN_CAUSE_DETAIL_MAX else d[:SCAN_CAUSE_DETAIL_MAX] + "…"
        for d in representatives[:SCAN_CAUSE_SAMPLE]
    ]
    clause = f"cause(s): {', '.join(types)}"
    if quoted:
        clause += f" — {' | '.join(quoted)}"
    dropped = len({f.detail for f in failures}) - len(quoted)
    if dropped > 0:
        clause += f" (+{dropped} more distinct message(s))"
    return clause


def _scan_summary_message(
    stats: CompileStats,
    failures: list[_ScanFailure],
    week: safety_week.SafetyWeek,
    consecutive: int,
) -> str:
    named = ", ".join(f.label for f in failures[:SCAN_SUMMARY_SAMPLE])
    if len(failures) > SCAN_SUMMARY_SAMPLE:
        named += f", …and {len(failures) - SCAN_SUMMARY_SAMPLE} more"
    parts: list[str] = []
    if stats.jobs_scanned == 0 and stats.active_jobs_read_failures:
        # A pure ITS_Active_Jobs outage scanned nothing, so the fraction clause would read
        # "0/0 scanned jobs" — and "how many of how many jobs" is the FIRST thing the runbook
        # tells the operator to read off this row. Lead with the actual cause instead.
        parts.append(
            f"compile-now trigger scan scanned NO jobs, week {week.start} — ITS_Active_Jobs "
            f"read FAILED for {stats.active_jobs_read_failures} served workstream(s)"
        )
    else:
        parts.append(
            f"compile-now trigger scan failed for {stats.scan_failures}/{stats.jobs_scanned} "
            f"scanned jobs, week {week.start}"
        )
        if stats.active_jobs_read_failures:
            parts.append(
                f"ITS_Active_Jobs read FAILED for {stats.active_jobs_read_failures} served "
                "workstream(s) — those jobs were never scanned"
            )
    if named:
        parts.append(f"failing: {named}")
    causes = _cause_clause(failures)
    if causes:
        parts.append(causes)
    parts.append(f"{consecutive} consecutive failing cycle(s)")
    return "; ".join(parts)


def _record_scan_outcome(
    stats: CompileStats,
    failures: list[_ScanFailure],
    week: safety_week.SafetyWeek,
    correlation_id: str,
) -> None:
    """Emit AT MOST ONE ITS_Errors row per pass for the routine trigger scan, plus the two
    sustained-outage escalations.

    Summarizing is the PR #608 pattern: §3.1's per-occurrence record mandate is honoured —
    every failing cycle still writes its own row — while collapsing per-item noise into one
    per-pass row is what keeps a chronically flaky sheet legible instead of burying the log.
    The cost is forensic granularity above SCAN_SUMMARY_SAMPLE failing jobs: the row names
    the first few and counts the rest.

    SEVERITY, not the row itself, is what the escalation ladder decides: a streak escalates
    to CRITICAL on the threshold-crossing cycle and re-notifies at 2×/4×/8×, and records
    ERROR in between so the rows stay terminal and reclaimable. See `_is_escalation_cycle`.
    """
    failing_cycle = _cycle_is_failing(stats)
    if failing_cycle:
        consecutive = _SCAN_FAILS.record()
    else:
        _SCAN_FAILS.reset()
        consecutive = 0
    sustained = failing_cycle and consecutive >= sustained_failure.DEFAULT_CRITICAL_THRESHOLD

    if failures or stats.active_jobs_read_failures:
        message = _scan_summary_message(stats, failures, week, consecutive)
        if sustained and _is_escalation_cycle(
            consecutive, sustained_failure.DEFAULT_CRITICAL_THRESHOLD
        ):
            error_log.log(
                Severity.CRITICAL, SCRIPT_NAME,
                f"{message} — SUSTAINED compile-now scan outage; triggered weeks are NOT "
                "compiling. See docs/runbooks/compile_now_poll.md",
                error_code="compile_now_scan_sustained",
                correlation_id=correlation_id,
            )
        elif sustained:
            # Past the threshold but between ladder rungs: still a per-occurrence row (§3.1),
            # just a TERMINAL one so rotation can reclaim it. See `_is_escalation_cycle`.
            nxt = _next_escalation_cycle(
                consecutive, sustained_failure.DEFAULT_CRITICAL_THRESHOLD
            )
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"{message} — SUSTAINED compile-now scan outage, ALREADY escalated CRITICAL "
                f"as compile_now_scan_sustained; next CRITICAL at {nxt} consecutive failing "
                "cycle(s). See docs/runbooks/compile_now_poll.md",
                error_code="compile_now_poll.scan_failed",
                correlation_id=correlation_id,
            )
        else:
            error_log.log(
                Severity.ERROR, SCRIPT_NAME, message,
                error_code="compile_now_poll.scan_failed",
                correlation_id=correlation_id,
            )

    if stats.active_jobs_read_failures:
        # BLIND CYCLE — a served workstream's job list could not be read, so its jobs were
        # never scanned and their absence from `failures` means nothing. Writing the ledger
        # here would DROP every one of their counts (only currently-failing keys survive a
        # write), so a routine Active-Jobs blip would reset the per-job counters and a
        # permanently-dead week sheet could never reach JOB_SCAN_CRITICAL_THRESHOLD — a blip
        # every 10 cycles caps every count at 9. Leave the file untouched; the CYCLE counter
        # (which this cycle already incremented) is what covers the outage itself.
        return

    counts = _JOB_LEDGER.apply({f.key for f in failures})
    if sustained and len(failures) > SCAN_SUMMARY_SAMPLE:
        # Duplicate-storm bound, deliberately NOT keyed on `sustained` alone. The fraction is
        # trivially reached on a small deployment (1 of 2 jobs failing IS >= 50%), so
        # suppressing on `sustained` alone would mean ONE renamed job folder fires the
        # "wait — this self-heals" cycle CRITICAL forever and NEVER the per-job "rename it
        # back" one. Suppress only when the pass row could not name the failing jobs anyway
        # (more of them than SCAN_SUMMARY_SAMPLE), which is also what bounds the per-job rows
        # to at most SCAN_SUMMARY_SAMPLE per cycle.
        return
    # The row's PREMISE must match what actually happened this cycle — it is what the operator
    # routes on. Narrowing the suppression above made this loop reachable when EVERY scanned
    # job is failing (on a <=SCAN_SUMMARY_SAMPLE-job deployment a total Smartsheet outage is
    # also "1 of 1 jobs dead for 20 cycles"), and there "while other jobs scan fine" is FALSE
    # — it would send the operator to Fault E's rename-back repair for a platform incident.
    # Reachable here only with `active_jobs_read_failures == 0` (the blind-cycle guard returned
    # above), so `jobs_scanned` really is the full served set.
    others_scanned_fine = stats.scan_failures < stats.jobs_scanned
    if others_scanned_fine:
        premise = (
            "while other jobs scan fine — this job's week sheet is unreachable and a "
            "Compile Now on it will never run"
        )
    else:
        premise = (
            f"as did EVERY other scanned job ({stats.scan_failures}/{stats.jobs_scanned}) "
            "— this is a BROAD scan outage, NOT a per-job fault; work the "
            "compile_now_scan_sustained row first and do not rename anything"
        )
    for failure in sorted(failures, key=lambda f: f.key):
        count = counts.get(failure.key, 0)
        if count < JOB_SCAN_CRITICAL_THRESHOLD:
            continue
        message = (
            f"{failure.label} compile-now trigger scan has failed {count} consecutive "
            f"cycles (week {week.start}) {premise}. See "
            f"docs/runbooks/compile_now_poll.md: {failure.detail}"
        )
        if _is_escalation_cycle(count, JOB_SCAN_CRITICAL_THRESHOLD):
            error_log.log(
                Severity.CRITICAL, SCRIPT_NAME, message,
                error_code="compile_now_job_scan_sustained",
                correlation_id=correlation_id,
            )
        else:
            # Between ladder rungs — per-occurrence row kept, severity dropped so the row is
            # TERMINAL and reclaimable. See `_is_escalation_cycle`.
            nxt = _next_escalation_cycle(count, JOB_SCAN_CRITICAL_THRESHOLD)
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"{message} (ALREADY escalated CRITICAL as compile_now_job_scan_sustained; "
                f"next CRITICAL at {nxt} consecutive cycle(s))",
                error_code="compile_now_poll.job_scan_failed",
                correlation_id=correlation_id,
            )


def _poll_inside_lock(
    active_configs: tuple[generate_core.GenerateConfig, ...],
) -> CompileStats:
    correlation_id = uuid.uuid4().hex[:12]
    stats = CompileStats()
    summary = generate_core.RunSummary()
    week = safety_week.week_bounds(datetime.now(ZoneInfo(DEFAULT_TZ)).date())
    scan_failures: list[_ScanFailure] = []

    for config in active_configs:
        jobs = active_jobs.list_active_jobs(config.active_jobs_config)
        if active_jobs.last_read_failed(config.active_jobs_config):
            # `_load_jobs` turns a read failure into a stdlib WARN + an empty (cached) list,
            # so this workstream's jobs are silently absent from the whole cycle — the
            # daemon's worst failure mode, and previously invisible in ITS_Errors (a clean
            # OK heartbeat through an ITS_Active_Jobs outage).
            stats.active_jobs_read_failures += 1
            stats.errors += 1
        for job in jobs:
            stats.jobs_scanned += 1
            try:
                if _compile_triggered_job(config, job, week, summary, correlation_id):
                    stats.triggered += 1
                    stats.compiled += 1
            except _ScanFailedError as exc:
                # Scan-phase failure — the trigger was NEVER confirmed set. A transient blip
                # while routinely scanning an (almost always) untriggered job: collected for
                # the ONE per-pass summary row below (never logged per job — that was 31 rows
                # a day), and NOT seeded to the Review Queue (there is no un-run operator
                # request to review; the next ~90 s cycle rescans). Mislabeling this as
                # compile_failed fed a multi-hundred-row review backlog during outages.
                stats.errors += 1
                stats.scan_failures += 1
                # `_compile_triggered_job` raises `... from exc`, so __cause__ is the real
                # error; fall back to the wrapper's own type if the chain is ever absent.
                cause = exc.__cause__ or exc
                scan_failures.append(
                    _ScanFailure(
                        config.workstream, job.project_name, job.job_id,
                        str(exc), type(cause).__name__,
                    )
                )
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

    # LAST, deliberately: this is the only step in the cycle that performs CRITICAL
    # triple-fire egress (Resend + Sentry) on a sustained cycle. Every helper it calls is
    # broad-caught, but if `error_log.log` itself ever raises, running it BEFORE the two
    # liveness surfaces would cost the cycle its ITS_Daemon_Health heartbeat AND its Check-C
    # watchdog marker — i.e. an alerting failure would masquerade as a dead daemon. Not
    # fenced: an escalation that cannot be recorded must reach @its_error_log, and by here
    # both liveness writes have already landed.
    _record_scan_outcome(stats, scan_failures, week, correlation_id)
    return stats


@its_error_log(script_name=SCRIPT_NAME)
@require_active
def poll_once() -> CompileStats:
    """One on-demand-compile cycle across all ENABLED workstreams. Single-shot (launchd handles the
    ~90 s cadence). The file lock makes it single-flight — an overlapping cycle (a slow compile)
    returns immediately rather than double-compiling the same job-week. When NO workstream is
    enabled, halts before taking the lock or writing a heartbeat."""
    # #336 startup observability (after @require_active, fail-open). Additive to the runtime
    # _polling_enabled reads below (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    active_configs = tuple(c for c in COMPILE_CONFIGS if _polling_enabled(c))
    if not active_configs:
        return CompileStats(halted="polling_disabled")
    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            return CompileStats(halted="locked")
        return _poll_inside_lock(active_configs)


if __name__ == "__main__":  # pragma: no cover
    poll_once()
