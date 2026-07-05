"""Field-Ops D1→Smartsheet job up-sync daemon (P2.5 Slice 5 — portal-as-writer).

Purpose
-------
The Mac-side mirror half of the job-tracker pivot ("D1 primary + dual Active-Jobs
mirror"). A job is created/edited/lifecycle-changed in the ITS Portal Job Tracker; the
Cloudflare Worker records it SEND-FREE in D1 (`origin='portal'`, `sync_state='pending'`)
and bumps a `mirror_version`. This launchd daemon pulls the dirty jobs and mirrors each UP
into BOTH ITS-owned Active-Jobs Smartsheets — the safety workspace's `ITS_Active_Jobs` and
the progress workspace's `ITS_Active_Jobs_Progress` — so those sheets become the downstream
source of truth every existing consumer reads. One writer ⇒ the two workstreams never drift
(§50 privileged code-actuation, §51 ITS-owned structured-SoR write-back).

Version-vector consistency (no cross-sheet 2-phase commit)
----------------------------------------------------------
D1 carries `mirror_version` + two watermarks (`safety_mirrored_version` /
`progress_mirrored_version`); a job is dirty when a watermark trails `mirror_version`. The
daemon writes each sheet independently (find-or-create by "Portal Job Key") and the commit
point is PER SHEET: after the safety upsert confirms it marks ONLY safety mirrored, THEN
attempts progress. A progress failure therefore leaves the job dirty with safety already
advanced; next cycle re-attempts both — safety's find-or-create no-ops on the existing row,
progress retries. The vector encodes exactly which sheet is behind (at-least-once,
idempotent effect; crash-safe).

Invariants
----------
- AI-FREE and customer-SEND-FREE (External Send Gate, FM Invariant 1): imports no
  `anthropic*` and no `graph_client.send_mail` / `resend` / `smtplib` / `email.mime`.
  Enrolled in tests/test_capability_gating.py GATED_SCRIPTS. The Smartsheet WRITE is the
  intended capability (SoR mirroring, NOT a customer send); the HTTP egress to OUR Worker
  goes through the F02-allowlisted `shared.portal_client`, so this module imports no raw
  network library.
- Kill-switch first (`@require_active`) + `@its_error_log` on the public entry. The runtime
  gate is `field_ops.fieldops_sync.sync_enabled` in ITS_Config (ARCH-1: the canonical gate,
  NOT a Daemon_Health checkbox). Ships **OFF** — the operator flips it at cutover, AFTER
  Slice 4's "Portal Job Key" column exists on BOTH sheets (else `add_rows` KeyErrors).
- Bearer privilege separation: authenticates with `ITS_PORTAL_FIELDOPS_TOKEN`
  (mirrors the Worker's `PORTAL_FIELDOPS_API_TOKEN`) — DISTINCT from portal_poll's internal
  token; neither can do the other's mutations.

Failure modes
-------------
- PAUSED / MAINTENANCE → `@require_active` exits cleanly.
- `sync_enabled=false` → short-circuit no-op (the shipped default; no log spam).
- Missing base URL or bearer → FAIL-CLOSED: do NOT sync; CRITICAL (won't self-heal) +
  ERROR heartbeat. 401 on pending-jobs → CRITICAL; other transport error → ERROR; both
  leave every job dirty for the next cycle.
- Per-job fence: `PicklistViolationError` / `SmartsheetValidationError` (permanent) → a
  `progress_reports` Review-Queue row (carrying the partial-commit state — which sheet failed and
  whether safety already mirrored), job left dirty; `PortalAuthError` on the mark-mirrored
  write-back (401, non-transient) → CRITICAL (`fieldops_mark_mirrored_unauthorized`, see runbook
  Symptom E), job left dirty; any other `SmartsheetError` / `PortalTransportError` (transient) →
  ERROR-logged, job left dirty. One bad job never kills the cycle.

Consumers
---------
- launchd `org.solutionsmith.its.fieldops-sync` (StartInterval; RunAtLoad).
- Watchdog Check C marker (`fieldops_sync`) + ITS_Daemon_Health row (via shared.heartbeat).
  The watchdog TRACKED_JOBS registration is deferred to the deploy session (the marker write
  here is forward-compatible and harmless if unregistered) — see docs/tech_debt.md.
"""
from __future__ import annotations

import fcntl
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from progress_reports import hours_log
from shared import (
    active_jobs_writer,
    circuit_breaker,
    error_log,
    keychain,
    picklist_validation,
    portal_client,
    review_queue,
    sheet_ids,
    smartsheet_client,
)
from shared.error_log import Severity, its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active

SCRIPT_NAME = "field_ops.fieldops_sync"
WORKSTREAM = "field_ops"

# ITS_Config keys.
CFG_SYNC_ENABLED = "field_ops.fieldops_sync.sync_enabled"
CFG_WORKER_BASE_URL = "safety_reports.portal.worker_base_url"  # shared with portal_poll
# The shared Worker base-URL key is owned by the safety_reports workstream (matches
# portal_poll) — read it there so no duplicate field_ops ITS_Config row is needed.
CFG_WORKER_BASE_URL_WORKSTREAM = "safety_reports"

# Keychain entry name (mirrors the Worker's PORTAL_FIELDOPS_API_TOKEN; the Mac-side name is
# distinct on purpose, and SEPARATE from portal_poll's ITS_PORTAL_INTERNAL_TOKEN).
KC_FIELDOPS_TOKEN = "ITS_PORTAL_FIELDOPS_TOKEN"  # noqa: S105 — Keychain entry NAME, not a secret

DEFAULT_SYNC_ENABLED = False  # ships OFF; operator flips it on at cutover (after Slice 4).
SYNC_INTERVAL_SECONDS = 300  # registration metadata; mirrors the plist StartInterval.

# P7 Slice 1 — the per-job Hours Log up-sync pass runs INSIDE this same daemon (one host, one
# lock, one heartbeat — no 4th daemon). Its OWN gate ships OFF so the pass is dark until the
# operator applies migration 0038 + deploys the Worker hours routes, then flips this on.
CFG_HOURS_ENABLED = "field_ops.fieldops_sync.hours_enabled"
DEFAULT_HOURS_ENABLED = False
_PACIFIC = ZoneInfo("America/Los_Angeles")  # tracker cells are the operator's wall-clock

# State paths. HEARTBEAT_ROW_STATE_PATH is SHARED with the other daemons — same JSON file,
# different daemon_name key (ARCH-2).
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_PATH = STATE_DIR / "fieldops_sync_heartbeat.txt"
LOCK_PATH = STATE_DIR / "fieldops_sync.lock"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"

DAEMON_NAME = "field_ops.fieldops_sync"

# A1 self-provision metadata (the ONLY per-daemon difference in the heartbeat helpers).
_REGISTRATION_SOURCE_ID = "Safety Portal Worker /api/internal/fieldops/pending-jobs"

# Shared ITS_Daemon_Health reporter for this daemon.
_heartbeat_reporter = HeartbeatReporter(
    script_name=SCRIPT_NAME,
    daemon_name=DAEMON_NAME,
    workstream=WORKSTREAM,
    liveness_path=HEARTBEAT_PATH,
    interval_seconds=SYNC_INTERVAL_SECONDS,
    source_id=_REGISTRATION_SOURCE_ID,
    row_state_path=HEARTBEAT_ROW_STATE_PATH,  # shared file — make the contract explicit
)

# Watchdog Check C marker (TRACKED_JOBS registration in scripts/watchdog.py is deferred to
# the deploy session — the marker write here is forward-compatible and harmless if
# unregistered; see docs/tech_debt.md). Mirrors portal_poll's pattern.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "fieldops_sync"


@dataclass(frozen=True)
class SyncStats:
    """Summary of one sync_once() invocation."""

    skipped_disabled: bool = False
    skipped_locked: bool = False
    halted_no_creds: bool = False
    scanned: int = 0
    mirrored: int = 0   # jobs whose BOTH sheets committed this cycle
    reviewed: int = 0   # jobs routed to the Review Queue (permanent failure)
    errors: int = 0     # transient per-job failures (left dirty) + skipped malformed rows
    # P7 hours pass (0 when hours_enabled is off — the shipped default).
    hours_mirrored: int = 0   # time entries whose Hours Log row committed this cycle
    hours_reviewed: int = 0   # entries/jobs routed to the Review Queue (permanent failure)
    hours_errors: int = 0     # transient hours failures (left unmirrored) + skipped malformed


# ---- Config readers (replicated per preservation, mirror portal_poll) ----------


def _read_str_setting(key: str, fallback: str, workstream: str | None = None) -> str:
    # workstream defaults to this daemon's WORKSTREAM (field_ops); pass an explicit owner for
    # a key owned by a different workstream (e.g. the shared safety_reports base-URL key).
    try:
        raw = smartsheet_client.get_setting(
            key, workstream=workstream if workstream is not None else WORKSTREAM
        )
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _sync_enabled() -> bool:
    return _read_bool_setting(CFG_SYNC_ENABLED, DEFAULT_SYNC_ENABLED)


def _hours_enabled() -> bool:
    return _read_bool_setting(CFG_HOURS_ENABLED, DEFAULT_HOURS_ENABLED)


# ---- Lock + heartbeat seams (mirror portal_poll) -------------------------------


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
    """Touch the Check C freshness marker for this run (mirror portal_poll)."""
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- Credential resolution (fail-CLOSED) ---------------------------------------


def _resolve_credentials() -> tuple[str, str] | None:
    """Resolve (base_url, bearer) fail-CLOSED. None if either is absent."""
    base_url = _read_str_setting(
        CFG_WORKER_BASE_URL, "", workstream=CFG_WORKER_BASE_URL_WORKSTREAM
    )
    try:
        bearer = keychain.get_secret(KC_FIELDOPS_TOKEN)
    except keychain.KeychainError:
        bearer = ""
    if not (base_url and bearer):
        return None
    return base_url, bearer


# ---- Public API ----------------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def sync_once() -> int:
    """Run one mirror cycle. Returns the number of jobs fully mirrored (BOTH sheets).

    SKELETON-superseded (P2.5 Slice 5): gate → fail-closed creds → GET pending-jobs →
    per-job dual-sheet find-or-create + per-sheet mark-mirrored → heartbeat + marker.
    launchd invokes this once per StartInterval; idempotent across crashes.
    """
    if not _sync_enabled():
        # Shipped default (OFF until cutover) — an intentional state, not an anomaly, so no
        # heartbeat/marker/log every cycle (would be 5-minute spam). The ITS_Config gate is
        # the operator's switch; flipping it on is the only thing that starts work.
        return 0

    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            error_log.log(
                Severity.INFO, SCRIPT_NAME,
                "another sync cycle holds the lock; skipping this cycle",
                error_code="sync_lock_held",
            )
            return 0
        return _sync_inside_lock().mirrored


def _sync_inside_lock() -> SyncStats:
    """Body of sync_once running under the file lock."""
    creds = _resolve_credentials()
    if creds is None:
        # FAIL-CLOSED: missing base URL / bearer → do NOT sync. A MISCONFIG that will NOT
        # self-heal (unset ITS_Config base URL or a removed/rotated Keychain entry) → page
        # immediately (CRITICAL). No watchdog marker (let Check C go stale too).
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            (
                # Deliberately does NOT interpolate the ITS_Config key or the Keychain entry
                # NAME (naming secret-store entries in a log is a CodeQL clear-text trip).
                "fail-closed: missing field-ops portal credentials — the Worker base URL "
                "(ITS_Config) and/or the field-ops bearer Keychain entry are unset; NOT "
                "syncing until fixed (see docs/runbooks/fieldops_sync.md Symptom B)"
            ),
            error_code="fieldops_creds_missing",
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary="fail-closed: field-ops credentials missing")
        return SyncStats(halted_no_creds=True)

    base_url, bearer = creds
    try:
        jobs = portal_client.get_fieldops_pending_jobs(base_url, bearer)
    except portal_client.PortalAuthError as exc:
        # 401 — bad/rotated/missing bearer. A MISCONFIG that will NOT self-heal → page
        # immediately. Caught BEFORE PortalTransportError (its subclass).
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"pending-jobs fetch UNAUTHORIZED (401) — field-ops bearer rejected; up-sync "
            f"STOPPED until the token is fixed: {exc!r}",
            error_code="fieldops_pending_auth_failed",
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary="pending-jobs UNAUTHORIZED (401) — bearer rejected")
        return SyncStats(errors=1)
    except portal_client.PortalTransportError as exc:
        # Transport failure (Worker down / wrong base URL / network). Transient — every job
        # stays dirty and re-pulls next cycle (no silent loss). No watchdog marker (the
        # un-faked Check-C marker is the backstop).
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"failed to GET pending-jobs (jobs left dirty for next cycle): {exc!r}",
            error_code="fieldops_pending_fetch_failed",
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary=f"pending-jobs fetch failed: {type(exc).__name__}")
        return SyncStats(errors=1)

    counters = {"mirrored": 0, "reviewed": 0, "errors": 0}
    for job in jobs:
        _mirror_job(job, base_url, bearer, counters)

    # P7 hours pass — runs in THIS same daemon (one host, one lock, one heartbeat), reusing the
    # already-resolved creds. Gated OFF by default; its own per-entry/per-job fences + a top
    # outer-catch mean a hours failure NEVER aborts the job mirror above nor the heartbeat below.
    hours = {"mirrored": 0, "reviewed": 0, "errors": 0}
    if _hours_enabled():
        hours = _mirror_hours_pass(base_url, bearer)

    _write_heartbeat()
    total_errors = counters["errors"] + hours["errors"]
    total_reviewed = counters["reviewed"] + hours["reviewed"]
    if total_errors > 0:
        cycle_status: HeartbeatStatus = "DEGRADED"
    elif total_reviewed > 0:
        cycle_status = "WARN"
    else:
        cycle_status = "OK"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"

    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=counters["mirrored"] + hours["mirrored"],
            error_summary=(
                None
                if total_errors == 0 and total_reviewed == 0
                else f"errors={total_errors} reviewed={total_reviewed}"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat must never block
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"heartbeat write outer-catch tripped: {exc!r}",
            error_code="daemon_health_write_failed",
        )
    _write_watchdog_marker()

    error_log.log(
        Severity.INFO, SCRIPT_NAME,
        (
            f"sync cycle: scanned={len(jobs)} mirrored={counters['mirrored']} "
            f"reviewed={counters['reviewed']} errors={counters['errors']}; "
            f"hours mirrored={hours['mirrored']} reviewed={hours['reviewed']} "
            f"errors={hours['errors']}"
        ),
        error_code="sync_cycle_summary",
    )
    return SyncStats(
        scanned=len(jobs),
        mirrored=counters["mirrored"],
        reviewed=counters["reviewed"],
        errors=counters["errors"],
        hours_mirrored=hours["mirrored"],
        hours_reviewed=hours["reviewed"],
        hours_errors=hours["errors"],
    )


def _mirror_job(
    job: dict[str, Any], base_url: str, bearer: str, counters: dict[str, int]
) -> None:
    """Mirror one dirty job into BOTH sheets under a per-job fence; mutates `counters`.

    Per-sheet commit point: safety upsert → mark-mirrored(safety) → progress upsert →
    mark-mirrored(progress). A failure after the safety commit leaves the job dirty with
    safety already advanced (the version-vector self-heal).
    """
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        # A job with no key can't be mirrored / marked-mirrored — surface, don't dispatch.
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            "pending job missing job_id; skipping",
            error_code="fieldops_job_no_id",
        )
        return

    raw_mirror_version = job.get("mirror_version")
    if isinstance(raw_mirror_version, int):
        mirror_version = raw_mirror_version
    else:
        # Never-silent: a missing/malformed mirror_version (vs the Worker's monotonic-MAX
        # watermark) coerces to 0 → the job would stay permanently dirty (re-attempted every
        # cycle, never escalated). WARN so the cause is observable instead of an invisible loop.
        mirror_version = 0
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"job {job.get('job_id')!r} has a missing/malformed mirror_version "
            f"({raw_mirror_version!r}); coercing to 0 — it will stay dirty until a well-formed "
            f"version arrives (likely a Worker pending-jobs payload defect).",
            error_code="fieldops_mirror_version_malformed",
        )
    correlation_id = uuid.uuid4().hex[:12]
    # Track the dual-sheet commit point so a permanent failure's Review-Queue ticket records the
    # PARTIAL state (safety already mirrored vs nothing mirrored) — the operator's fix differs.
    mirrored_safety = False

    try:
        # ── SAFETY sheet ──────────────────────────────────────────────────────
        safety_row_id, canonical = active_jobs_writer.upsert_job(
            active_jobs_writer.SAFETY_WRITE_CONFIG, job
        )
        portal_client.mark_fieldops_jobs_mirrored(
            base_url, bearer,
            [{
                "job_id": job_id,
                "sheet": "safety",
                "mirrored_version": mirror_version,
                "row_id": safety_row_id,
                "canonical_job_id": canonical or None,
            }],
        )
        mirrored_safety = True
        # ── PROGRESS sheet ────────────────────────────────────────────────────
        # Commit point already advanced safety; if this raises, the job stays dirty with
        # only safety mirrored and next cycle re-attempts both (safety find-or-create no-ops).
        progress_row_id, _progress_canonical = active_jobs_writer.upsert_job(
            active_jobs_writer.PROGRESS_WRITE_CONFIG, job
        )
        portal_client.mark_fieldops_jobs_mirrored(
            base_url, bearer,
            [{
                "job_id": job_id,
                "sheet": "progress",
                "mirrored_version": mirror_version,
                "row_id": progress_row_id,
            }],
        )
        counters["mirrored"] += 1
        # §51 archive-on-closure — the job was just mirrored; if it is CLOSED
        # (lifecycle=archived) move its standing tracker sheets into the Archive
        # workspace. Fully fenced inside the helper (any failure WARNs + returns),
        # so a move failure NEVER un-does or fails the mirror above.
        if str(job.get("lifecycle") or "").strip().lower() == "archived":
            _archive_closed_job_trackers(
                job_id, str(job.get("project_name") or "").strip(), correlation_id
            )
    except (
        picklist_validation.PicklistViolationError,
        smartsheet_client.SmartsheetValidationError,
    ) as exc:
        # PERMANENT — the row will never succeed as-is (a bad lifecycle value, an HTTP-400
        # reject). Route to the Review Queue; leave the job dirty (the operator has a ticket).
        counters["reviewed"] += 1
        _route_to_review(job, job_id, exc, correlation_id, mirrored_safety=mirrored_safety)
    except portal_client.PortalAuthError:
        # 401 on mark-mirrored — the field-ops bearer was rejected while writing back the
        # watermark. NOT transient: a bad/rotated bearer will NOT self-heal → page (CRITICAL),
        # same posture as the pending-jobs 401. The sheet write itself already SUCCEEDED (upsert
        # is a Smartsheet write, not a Worker call), so only the Worker watermark is missing — the
        # job stays dirty and is safely re-attempted (find-or-create no-ops) once the bearer is
        # fixed. PortalAuthError is a PortalTransportError SUBCLASS, so this MUST precede the
        # transient clause below or a 401 would be mis-classified as a self-healing blip.
        counters["errors"] += 1
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"mark-mirrored UNAUTHORIZED (401) — field-ops bearer rejected during write-back for "
            f"job_id={job_id!r}; the sheet write landed but the Worker watermark did not, so the "
            f"job is left dirty (safe re-attempt once the bearer is fixed). "
            f"See docs/runbooks/fieldops_sync.md Symptom E.",
            error_code="fieldops_mark_mirrored_unauthorized",
            correlation_id=correlation_id,
        )
    except (smartsheet_client.SmartsheetError, portal_client.PortalTransportError) as exc:
        # TRANSIENT — leave the job dirty (do NOT mark-mirrored); next cycle retries.
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"transient failure mirroring job_id={job_id!r} (left dirty for next cycle): "
            f"{type(exc).__name__}: {exc!r}",
            error_code="fieldops_job_transient",
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never kills the cycle
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"unexpected failure mirroring job_id={job_id!r}: {type(exc).__name__}: {exc!r}",
            error_code="fieldops_job_unexpected",
            correlation_id=correlation_id,
        )


def _archive_closed_job_trackers(
    job_id: str, project_name: str, correlation_id: str
) -> None:
    """§51 archive-on-closure — MOVE a closed job's standing tracker sheets into the
    Archive workspace's "Closed Projects" folder.

    Called ONLY for a job whose lifecycle is `archived`, right after it mirrors. Best-
    effort + fully fenced: ANY failure WARNs (`fieldops_archive_on_closure_failed`) and
    returns — a move failure must NEVER fail the mirror (the job is already mirrored +
    mark-synced back to the Worker). NEVER deletes rows or sheets; a pure relocation.

    Idempotent by construction: it resolves the tracker sheet find-or-create-FREE in the
    SOURCE (per-job PROGRESS) folder. Once a sheet has been moved out of that folder it is
    no longer found there → this returns without a second move (the natural idempotency —
    the same daemon may re-see an already-archived job on a later dirty cycle).

    Today the ONLY standing tracker is the per-job `<Job> — Hours Log` (P7 Slice 1).
    Equipment / Materials trackers are P7 Slices 2 / 3 — NOT built yet; extend this helper
    (resolve + move each) when they land.

    Edge case (by design, not handled here): if an archived job later receives NEW hours,
    the hours pass would find-or-CREATE a fresh `<Job> — Hours Log` back in the active
    PROGRESS folder (this helper only moves what exists at closure time). Archived/closed
    jobs are not expected to receive new hours; note that new hours flow through the SEPARATE
    pending-hours queue (`_mirror_hours_pass`), NOT the job-dirty list that drives this helper,
    so a fresh sheet would re-archive only when the JOB itself is next re-dirtied (edited) —
    not automatically.
    """
    try:
        # Resolve the per-job folder in the PROGRESS workspace WITHOUT creating it — the
        # SAME folder the Hours Log / week sheets live in (identical name via safety_naming).
        folder = smartsheet_client.find_folder_by_name_in_workspace(
            sheet_ids.WORKSPACE_PROGRESS_REPORTING, hours_log._folder_name(project_name)
        )
        if folder is None:
            # No per-job folder → nothing was ever created for this job → nothing to archive.
            return
        # Resolve the Hours Log sheet WITHOUT creating it. None ⇒ already moved (prior cycle)
        # OR never existed — either way there is nothing to move.
        sid = smartsheet_client.find_sheet_by_name_in_folder(
            folder, hours_log.hours_log_sheet_name(project_name)
        )
        if sid is None:
            return
        smartsheet_client.move_sheet_to_folder(
            sid, sheet_ids.FOLDER_ARCHIVE_CLOSED_PROJECTS
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; a move failure never fails the mirror
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"archive-on-closure move failed for job_id={job_id!r} "
            f"(project_name={project_name!r}); the job is already mirrored + mark-synced so this "
            f"never fails the mirror — but the job is now CLEAN, so the move does NOT auto-retry: "
            f"the Hours Log stays (never lost/deleted) in the active PROGRESS folder until the job "
            f"is next re-dirtied (edited) or an operator moves it manually "
            f"(docs/runbooks/hours_log_sync.md Fault F). {type(exc).__name__}: {exc!r}",
            error_code="fieldops_archive_on_closure_failed",
            correlation_id=correlation_id,
        )
        return


def _route_to_review(
    job: dict[str, Any], job_id: str, exc: Exception, correlation_id: str,
    *, mirrored_safety: bool,
) -> None:
    """Route a permanently-failed job to ITS_Review_Queue (workstream progress_reports).

    `mirrored_safety` records the dual-sheet PARTIAL-commit state at the point of failure so the
    operator's ticket says WHERE the job stands: if the safety sheet already mirrored, only the
    PROGRESS sheet failed (safety is live + correct); otherwise nothing mirrored yet (the failure
    was on the safety sheet). The remediation differs between the two, so name it explicitly.
    """
    failed_sheet = "progress" if mirrored_safety else "safety"
    partial = (
        "safety sheet already mirrored — only the progress sheet failed"
        if mirrored_safety
        else "nothing mirrored yet — failed on the safety sheet"
    )
    review_queue.add(
        workstream="progress_reports",
        summary=(
            f"field-ops up-sync: PERMANENT failure mirroring job {job_id!r} on the {failed_sheet} "
            f"sheet ({type(exc).__name__}) — {partial}; left dirty, needs operator fix"
        ),
        payload={
            "job_id": job_id,
            "project_name": job.get("project_name"),
            "lifecycle": job.get("lifecycle"),
            "failed_sheet": failed_sheet,
            "safety_mirrored": mirrored_safety,
            "error": f"{type(exc).__name__}: {exc!r}",
        },
        sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
        reason=review_queue.ReviewReason.POLICY_EDGE,
        severity=Severity.WARN,
        source_file=job_id,
    )
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"job_id={job_id!r} routed to Review Queue (permanent, failed on the {failed_sheet} "
        f"sheet): {type(exc).__name__}: {exc!r}",
        error_code="fieldops_job_permanent",
        correlation_id=correlation_id,
    )


# ---- P7 Hours Log up-sync pass (Track 2, Slice 1) --------------------------------


def _fmt_epoch_date(epoch: Any) -> str:
    """Epoch seconds → Pacific 'YYYY-MM-DD' (the operator's work day). '' on missing/malformed."""
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        return ""
    try:
        return datetime.fromtimestamp(epoch, _PACIFIC).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _fmt_epoch_time(epoch: Any) -> str:
    """Epoch seconds → Pacific 'HH:MM' (the Work Date column carries the day). '' when unset."""
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        return ""
    try:
        return datetime.fromtimestamp(epoch, _PACIFIC).strftime("%H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _fmt_epoch_dt(epoch: Any) -> str:
    """Epoch seconds → Pacific ISO datetime (the Recorded At server-time column). '' when unset."""
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        return ""
    try:
        return datetime.fromtimestamp(epoch, _PACIFIC).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _fmt_hours(hours: Any) -> str:
    """Field-reported hours → a trimmed string ('' when unset)."""
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        return ""
    return f"{hours:g}"


def _group_hours_by_job(
    entries: list[dict[str, Any]],
) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """Group pending hours rows by job_id → (project_name, [rows]). Skips a row missing its
    uuid / job_id / project_name (a data anomaly that can't be foldered) — never silent."""
    by_job: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for e in entries:
        entry_uuid = str(e.get("uuid") or "").strip()
        job_id = str(e.get("job_id") or "").strip()
        project = str(e.get("project_name") or "").strip()
        if not entry_uuid or not job_id or not project:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"hours entry skipped — missing uuid/job_id/project_name "
                f"(uuid={e.get('uuid')!r} job_id={e.get('job_id')!r})",
                error_code="fieldops_hours_row_malformed",
            )
            continue
        by_job.setdefault(job_id, (project, []))[1].append(e)
    return by_job


def _mirror_hours_pass(base_url: str, bearer: str) -> dict[str, int]:
    """Mirror unmirrored crew time entries UP into per-job Hours Log sheets.

    Returns {mirrored, reviewed, errors}. Never raises — the caller runs it after the job mirror,
    so a hours failure must never abort the cycle. Per-job (sheet) + per-entry fences; a permanent
    failure routes to the Review Queue; a transient one leaves the entry unmirrored (mirrored_at
    stays NULL) for the next cycle. mark-mirrored is the LAST step (crash-safe: a crash before it
    re-mirrors idempotently — the sheet find-or-create by Entry UUID no-ops).
    """
    out = {"mirrored": 0, "reviewed": 0, "errors": 0}
    try:
        entries = portal_client.get_fieldops_pending_hours(base_url, bearer)
    except portal_client.PortalAuthError as exc:
        # 401 on the SAME bearer that just drained pending-jobs — surface (bad/rotated token) but
        # do not crash: the job pass may have succeeded before a mid-cycle rotation.
        out["errors"] += 1
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"hours-pending fetch UNAUTHORIZED (401) — field-ops bearer rejected; hours up-sync "
            f"skipped this cycle: {exc!r}",
            error_code="fieldops_hours_pending_auth_failed",
        )
        return out
    except portal_client.PortalTransportError as exc:
        out["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"hours-pending fetch failed (entries left unmirrored for next cycle): {exc!r}",
            error_code="fieldops_hours_pending_fetch_failed",
        )
        return out

    succeeded: list[str] = []
    for job_id, (project_name, rows) in _group_hours_by_job(entries).items():
        correlation_id = uuid.uuid4().hex[:12]
        try:
            sheet_id = hours_log.ensure_hours_log_sheet(project_name)
        except (
            picklist_validation.PicklistViolationError,
            smartsheet_client.SmartsheetValidationError,
        ) as exc:
            out["reviewed"] += 1
            _route_hours_to_review(job_id, project_name, exc, correlation_id, phase="ensure-sheet")
            continue
        except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never kills the pass
            out["errors"] += 1
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"transient failure ensuring Hours Log sheet for {project_name!r} "
                f"(job {job_id}); entries left unmirrored: {type(exc).__name__}: {exc!r}",
                error_code="fieldops_hours_sheet_transient",
                correlation_id=correlation_id,
            )
            continue

        for e in rows:
            entry_uuid = str(e["uuid"]).strip()
            try:
                hours_log.upsert_entry_row(
                    sheet_id,
                    entry_uuid=entry_uuid,
                    work_date=(
                        _fmt_epoch_date(e.get("work_started_at"))
                        or _fmt_epoch_date(e.get("created_at"))
                    ),
                    personnel=str(e.get("personnel_name") or "").strip(),
                    hours=_fmt_hours(e.get("hours")),
                    started=_fmt_epoch_time(e.get("work_started_at")),
                    ended=_fmt_epoch_time(e.get("work_ended_at")),
                    notes=str(e.get("notes") or "").strip(),
                    recorded_at=_fmt_epoch_dt(e.get("created_at")),
                )
                amends = str(e.get("amends_uuid") or "").strip()
                if amends and not hours_log.supersede_entry_row(sheet_id, amends, entry_uuid):
                    # The amend names an entry we never mirrored — surface, do NOT block the amend
                    # (its own Active row is written; the prior may arrive later, out of order).
                    error_log.log(
                        Severity.WARN, SCRIPT_NAME,
                        f"hours amend {entry_uuid!r} names prior {amends!r} not yet on the Hours "
                        f"Log for {project_name!r} — amend row written, prior left unmarked",
                        error_code="fieldops_hours_amend_prior_missing",
                        correlation_id=correlation_id,
                    )
                succeeded.append(entry_uuid)
            except (
                picklist_validation.PicklistViolationError,
                smartsheet_client.SmartsheetValidationError,
            ) as exc:
                out["reviewed"] += 1
                _route_hours_to_review(
                    job_id, project_name, exc, correlation_id, phase="upsert", entry_uuid=entry_uuid
                )
            except Exception as exc:  # noqa: BLE001 — per-entry fence
                out["errors"] += 1
                error_log.log(
                    Severity.ERROR, SCRIPT_NAME,
                    f"transient failure mirroring hours entry {entry_uuid!r} for {project_name!r} "
                    f"(left unmirrored): {type(exc).__name__}: {exc!r}",
                    error_code="fieldops_hours_entry_transient",
                    correlation_id=correlation_id,
                )

        # §51 A5 row-cap watchdog (SoR-safe, refined per the 2026-07-04 v19.x rider): once per job
        # after its upserts, WARN + Review-Queue an operator period-split as the standing sheet
        # nears the row cap. Advisory — check_row_cap owns its try/except, so it never raises here.
        hours_log.check_row_cap(sheet_id, hours_log.hours_log_sheet_name(project_name))

    if succeeded:
        try:
            portal_client.mark_fieldops_hours_mirrored(base_url, bearer, succeeded)
            out["mirrored"] += len(succeeded)
        except portal_client.PortalAuthError as exc:
            out["errors"] += 1
            error_log.log(
                Severity.CRITICAL, SCRIPT_NAME,
                f"hours mark-mirrored UNAUTHORIZED (401) — {len(succeeded)} entries filed to the "
                f"Hours Log but the D1 watermark did not advance; safe re-mirror (idempotent "
                f"find-or-create) once the bearer is fixed: {exc!r}",
                error_code="fieldops_hours_mark_mirrored_unauthorized",
            )
        except portal_client.PortalTransportError as exc:
            out["errors"] += 1
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"hours mark-mirrored failed for {len(succeeded)} entries (filed to the Hours Log; "
                f"re-mirrored idempotently next cycle): {exc!r}",
                error_code="fieldops_hours_mark_mirrored_failed",
            )
    return out


def _route_hours_to_review(
    job_id: str, project_name: str, exc: Exception, correlation_id: str,
    *, phase: str, entry_uuid: str | None = None,
) -> None:
    """Route a PERMANENTLY-failed hours mirror to ITS_Review_Queue (workstream progress_reports)."""
    review_queue.add(
        workstream="progress_reports",
        summary=(
            f"field-ops Hours Log up-sync: PERMANENT failure ({phase}) for job {job_id!r} "
            f"({project_name!r}, {type(exc).__name__})"
            + (f" entry {entry_uuid!r}" if entry_uuid else "")
            + " — left unmirrored, needs operator fix"
        ),
        payload={
            "job_id": job_id,
            "project_name": project_name,
            "phase": phase,
            "entry_uuid": entry_uuid,
            "error": f"{type(exc).__name__}: {exc!r}",
        },
        sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
        reason=review_queue.ReviewReason.POLICY_EDGE,
        severity=Severity.WARN,
        source_file=entry_uuid or job_id,
    )
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"hours mirror routed to Review Queue (permanent, {phase}) job={job_id!r} "
        f"entry={entry_uuid!r}: {type(exc).__name__}: {exc!r}",
        error_code="fieldops_hours_permanent",
        correlation_id=correlation_id,
    )


if __name__ == "__main__":
    sync_once()
