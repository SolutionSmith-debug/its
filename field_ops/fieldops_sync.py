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
  `progress_reports` Review-Queue row, job left dirty; any other `SmartsheetError` /
  `PortalTransportError` (transient) → ERROR-logged, job left dirty. One bad job never kills
  the cycle.

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

from shared import (
    active_jobs_writer,
    circuit_breaker,
    error_log,
    keychain,
    picklist_validation,
    portal_client,
    review_queue,
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

    _write_heartbeat()
    if counters["errors"] > 0:
        cycle_status: HeartbeatStatus = "DEGRADED"
    elif counters["reviewed"] > 0:
        cycle_status = "WARN"
    else:
        cycle_status = "OK"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"

    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=counters["mirrored"],
            error_summary=(
                None
                if counters["errors"] == 0 and counters["reviewed"] == 0
                else f"errors={counters['errors']} reviewed={counters['reviewed']}"
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
            f"reviewed={counters['reviewed']} errors={counters['errors']}"
        ),
        error_code="sync_cycle_summary",
    )
    return SyncStats(
        scanned=len(jobs),
        mirrored=counters["mirrored"],
        reviewed=counters["reviewed"],
        errors=counters["errors"],
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
    except (
        picklist_validation.PicklistViolationError,
        smartsheet_client.SmartsheetValidationError,
    ) as exc:
        # PERMANENT — the row will never succeed as-is (a bad lifecycle value, an HTTP-400
        # reject). Route to the Review Queue; leave the job dirty (the operator has a ticket).
        counters["reviewed"] += 1
        _route_to_review(job, job_id, exc, correlation_id)
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


def _route_to_review(
    job: dict[str, Any], job_id: str, exc: Exception, correlation_id: str
) -> None:
    """Route a permanently-failed job to ITS_Review_Queue (workstream progress_reports)."""
    review_queue.add(
        workstream="progress_reports",
        summary=(
            f"field-ops up-sync: PERMANENT failure mirroring job {job_id!r} "
            f"({type(exc).__name__}) — left dirty, needs operator fix"
        ),
        payload={
            "job_id": job_id,
            "project_name": job.get("project_name"),
            "lifecycle": job.get("lifecycle"),
            "error": f"{type(exc).__name__}: {exc!r}",
        },
        sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
        reason=review_queue.ReviewReason.POLICY_EDGE,
        severity=Severity.WARN,
        source_file=job_id,
    )
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"job_id={job_id!r} routed to Review Queue (permanent): {type(exc).__name__}: {exc!r}",
        error_code="fieldops_job_permanent",
        correlation_id=correlation_id,
    )


if __name__ == "__main__":
    sync_once()
