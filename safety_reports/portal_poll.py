"""Safety Portal pull-model polling daemon (Phase 5) — the Mac-side queue drain.

The puller half of decision_phase5-portal-transport. The Cloudflare Worker signs +
queues each portal submission send-free in D1; this launchd daemon drains the queue:

    GET /api/internal/pending  (shared.portal_client)
      → per row: recompute the canonical HMAC (shared.portal_hmac) and constant-time
        compare to the row's `hmac`. HMAC FAIL = reject (anomaly-log + Review-Queue
        flag, security_flag=True) and NEVER hand to intake — the downgrade defense.
      → on verify pass: safety_reports.intake.process_portal_submission(row)
      → on a DRAIN outcome: POST /api/internal/mark-filed (the receipt).

Fail-CLOSED: if the bearer token, the HMAC secret, or the Worker base URL is
missing, the cycle does NOT poll — it logs + halts (a silent no-op that drops
submissions is forbidden, CLAUDE.md "never silent").

Capability gating (Invariant 1): this daemon is ENROLLED in
tests/test_capability_gating.py::GATED_SCRIPTS — it must NOT import any external-send
capability (send_mail / resend / smtplib / email.mime). It pulls + files only; the
HTTP egress lives in the audited shared.portal_client (F02 allowlist), so this module
itself imports no network library. This is the whole point of the pull model: the
Python puller is INSIDE the AST capability gate the TS Worker was outside of.

launchd schedule
----------------
Single-cycle: `poll_once()` is the public API; `__main__` calls it once and exits.
launchd handles the ~60 s cadence via StartInterval (sourced from ITS_Config
`safety_reports.portal_poll.poll_interval_seconds` at install time).

Per-cycle behavior (mirrors the canonical weekly_send_poll pattern)
------------------------------------------------------------------
  1. `polling_enabled` ITS_Config gate — false short-circuits.
  2. fcntl file lock — skip-if-held (launchd-overlap guard).
  3. Fail-closed credential resolution (bearer + HMAC secret + base URL).
  4. GET pending; per-row HMAC verify → dispatch → receipt; per-row fence.
  5. seen-set (state file) — fast-path re-receipt for an already-filed UUID whose
     mark-filed was lost, and one-shot flagging for a rejected (bad-HMAC) UUID, so
     neither re-files nor re-spams the Review Queue every cycle.
  6. Heartbeat file + ITS_Daemon_Health row + watchdog Check C marker.
  No @its_error_log CRITICAL-spam on a clean empty poll (only real failures log).

Tech-debt: the heartbeat-row helpers are replicated VERBATIM from weekly_send_poll
(itself from the retired intake_poll) per preservation-over-refactor (Op Stds §14).
portal_poll is now the 2nd LIVE consumer — the shared/heartbeat.py extraction
(tracked in docs/tech_debt.md) approaches its trigger but is deferred until the
portal flow is live-validated. Inline duplication keeps this ship focused.
"""
from __future__ import annotations

import base64
import fcntl
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from safety_reports import intake
from shared import (
    active_jobs,
    anomaly_logger,
    box_client,
    circuit_breaker,
    error_log,
    keychain,
    portal_client,
    portal_hmac,
    review_queue,
    sheet_ids,
    smartsheet_client,
    state_io,
)
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.portal_poll"
WORKSTREAM = "safety_reports"

# ITS_Config keys.
CFG_POLLING_ENABLED = "safety_reports.portal_poll.polling_enabled"
CFG_POLL_INTERVAL = "safety_reports.portal_poll.poll_interval_seconds"
CFG_WORKER_BASE_URL = "safety_reports.portal.worker_base_url"

# Keychain entry names (mirror the Worker's PORTAL_INTERNAL_API_TOKEN +
# HMAC_PAYLOAD_SECRET; the Mac-side names are distinct on purpose).
KC_BEARER = "ITS_PORTAL_INTERNAL_TOKEN"  # noqa: S105 — Keychain entry NAME, not a secret
KC_HMAC_SECRET = "ITS_PORTAL_HMAC_SECRET"  # noqa: S105 — Keychain entry NAME, not a secret

DEFAULT_POLLING_ENABLED = True
DEFAULT_POLL_INTERVAL = 60  # 60 s
PENDING_LIMIT = 50  # Worker caps at 200; 50 drains a normal backlog per cycle.
MAX_SEEN = 2000  # cap the seen-set file (oldest entries are harmless dead weight).

# PR-4 Part A — request-driven PDF cache servicing pass.
PDF_REQUEST_LIMIT = 25  # Worker caps at 100; 25 services a normal request backlog.
# Raw bytes per chunk BEFORE base64. 700 KB raw → ~933 KB base64, comfortably under
# the Worker's 1 MB decoded-chunk ceiling and D1's ~2 MB per-row practical limit.
PDF_CHUNK_BYTES = 700_000
# Recover the Box file id from a `https://app.box.com/file/<id>` link (the shape
# intake._box_link produces). Same pattern as weekly_send._box_file_id.
_BOX_FILE_LINK_RE = re.compile(r"/file/(\d+)")

# State paths. HEARTBEAT_ROW_STATE_PATH is SHARED with the other daemons —
# same JSON file, different daemon_name key.
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_PATH = STATE_DIR / "portal_poll_heartbeat.txt"
LOCK_PATH = STATE_DIR / "portal_poll.lock"
SEEN_PATH = STATE_DIR / "portal_poll_seen.json"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"
# Consecutive pending-fetch failures before a TRANSIENT transport error escalates ERROR →
# CRITICAL. The puller runs every ~60s, so 5 ≈ a 5-minute sustained filing outage — pages
# promptly instead of waiting for the next daily watchdog. A one-off blip stays ERROR and the
# counter resets on the next successful fetch. (Auth / missing-creds page IMMEDIATELY — they
# never self-heal — so they don't go through this counter.)
FETCH_FAIL_STATE_PATH = STATE_DIR / "portal_poll_fetch_failures.json"
FETCH_FAIL_CRITICAL_THRESHOLD = 5

DAEMON_NAME = "safety_reports.portal_poll"

# A1 self-provision metadata (the ONLY per-daemon difference in the otherwise
# byte-identical heartbeat helpers — kept OUT of the helper bodies so the
# verbatim-duplication invariant + the future shared/heartbeat.py extraction stay clean).
_REGISTRATION_INTERVAL_SECONDS = DEFAULT_POLL_INTERVAL
_REGISTRATION_SOURCE_ID = "Safety Portal Worker /api/internal/pending"

# Watchdog Check C marker (TRACKED_JOBS registration in scripts/watchdog.py is
# deferred to the deploy session — see docs/tech_debt.md; the marker write here is
# forward-compatible and harmless if unregistered).
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_portal_poll"

HeartbeatStatus = Literal[
    "OK", "WARN", "ERROR", "DEGRADED", "SKIPPED", "CIRCUIT_OPEN"
]

# intake return statuses that mean "stop serving this row" — post the receipt.
# 'error' is the ONLY non-drain status (transient → re-pull retries).
DRAIN_STATUSES = frozenset({"processed", "already_filed", "review_queue"})


@dataclass(frozen=True)
class PollStats:
    """Summary of one poll_once() invocation."""
    skipped_disabled: bool = False
    skipped_locked: bool = False
    halted_no_creds: bool = False
    scanned: int = 0
    filed: int = 0      # processed + already_filed
    reviewed: int = 0   # review_queue (flagged + drained)
    rejected: int = 0   # HMAC verify failures (never filed)
    remarked: int = 0   # seen-as-filed rows whose mark-filed was re-posted
    errors: int = 0     # transient intake errors + per-row exceptions (NOT drained)
    pdf_serviced: int = 0  # PR-4: request-driven PDF caches uploaded this cycle


# ---- Box helper (PR-4 Part A) -------------------------------------------


def _box_file_id(link: str) -> str | None:
    """Recover the Box file id from a `_box_link` URL. None if not present.

    Copy of weekly_send._box_file_id (Op Stds §14 preservation). Used only to
    backstop a missing box_file_id on a re-served already-filed row — the canonical
    id rides on ProcessResult.box_file_id from intake.
    """
    m = _BOX_FILE_LINK_RE.search(link or "")
    return m.group(1) if m else None


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


def _write_heartbeat() -> None:
    """Overwrite the heartbeat file with the current UTC ISO timestamp."""
    state_io.atomic_write_text(HEARTBEAT_PATH, datetime.now(UTC).isoformat())


# ---- Consecutive pending-fetch failure counter (sustained-outage escalation) ----


def _record_fetch_failure() -> int:
    """Increment + persist the consecutive pending-fetch failure counter; return the new count.

    Used ONLY for transient transport failures (auth / missing-creds page immediately). On any
    state error, returns 1 (treat as a single failure — do NOT page off a state glitch; the
    un-faked watchdog Check-C marker is the reliable backstop for a sustained outage)."""
    try:
        with state_io.with_path_lock(FETCH_FAIL_STATE_PATH):
            count = 0
            if FETCH_FAIL_STATE_PATH.exists():
                try:
                    count = int(json.loads(FETCH_FAIL_STATE_PATH.read_text()).get("count", 0))
                except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                    count = 0
            count += 1
            state_io.atomic_write_json(FETCH_FAIL_STATE_PATH, {"count": count})
            return count
    except Exception as exc:  # noqa: BLE001 — counter is best-effort; Check C is the backstop
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"fetch-failure counter write failed (treating as #1): {exc!r}",
            error_code="portal_fetch_counter_failed",
        )
        return 1


def _reset_fetch_failures() -> None:
    """Zero the consecutive-failure counter after a successful pending fetch. Best-effort: a
    reset failure only risks one spurious CRITICAL next cycle, never a missed outage."""
    try:
        with state_io.with_path_lock(FETCH_FAIL_STATE_PATH):
            if FETCH_FAIL_STATE_PATH.exists():
                state_io.atomic_write_json(FETCH_FAIL_STATE_PATH, {"count": 0})
    except Exception:  # noqa: BLE001 — best-effort reset
        pass


# ---- Seen-set (idempotency defense-in-depth) ----------------------------


def _load_seen() -> dict[str, dict[str, Any]]:
    """Load the seen-set `{uuid: {status, box_link}}`. {} on any read error."""
    if not SEEN_PATH.exists():
        return {}
    try:
        parsed = json.loads(SEEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _persist_seen(seen: dict[str, dict[str, Any]]) -> None:
    """Atomically persist the seen-set, capped to the most-recent MAX_SEEN entries.

    Lock-timeout fails OPEN (log WARN + skip): a lost seen-set only costs a
    redundant intake call next cycle (the week-sheet UUID check is the real dedupe
    authority), never a double-file. Caps to bound the file (oldest = dead weight
    once the Worker has drained the row)."""
    if len(seen) > MAX_SEEN:
        seen = dict(list(seen.items())[-MAX_SEEN:])
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with state_io.with_path_lock(SEEN_PATH):
            state_io.atomic_write_json(SEEN_PATH, seen)
    except state_io.StateLockTimeoutError:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not acquire lock on {SEEN_PATH} after retries; seen-set not persisted",
            error_code="portal_seen_persist_failed",
        )


# ---- Heartbeat-row state cache (ITS_Daemon_Health) ----------------------
# Replicated VERBATIM from weekly_send_poll per preservation-over-refactor (Op
# Stds §14); the shared/heartbeat.py extraction is the tracked tech-debt follow-on.


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
    """Atomically merge `{daemon_name: {row_id, total_cycles}}` into the state file."""
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
            Severity.WARN, SCRIPT_NAME,
            f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries",
            error_code="daemon_health_write_failed",
        )


def _invalidate_heartbeat_row_state(daemon_name: str) -> None:
    """Remove a daemon's entry from the state file (forces re-lookup)."""
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
            Severity.WARN, SCRIPT_NAME,
            f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries (invalidate)",
            error_code="daemon_health_write_failed",
        )


def _create_heartbeat_row(daemon_name: str) -> int | None:
    """Self-provision this daemon's ITS_Daemon_Health row (A1, find-or-create)."""
    cols = sheet_ids.DAEMON_HEALTH_COLUMNS
    payload: dict[int, Any] = {
        cols["daemon_name"]: daemon_name,
        cols["workstream"]: WORKSTREAM,
        cols["enabled"]: True,
        cols["interval_seconds"]: _REGISTRATION_INTERVAL_SECONDS,
        cols["source_id"]: _REGISTRATION_SOURCE_ID,
    }
    try:
        with circuit_breaker.bypass():
            return smartsheet_client.add_row_by_id(
                sheet_ids.SHEET_DAEMON_HEALTH, payload
            )
    except smartsheet_client.SmartsheetError as exc:
        _log_heartbeat_failure(daemon_name, f"self-provision create failed: {exc!r}")
        return None
    except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
        _log_heartbeat_failure(daemon_name, f"self-provision unexpected: {exc!r}")
        return None


def _resolve_heartbeat_row_id(daemon_name: str) -> int | None:
    """Return the ITS_Daemon_Health row id for `daemon_name`, find-or-create."""
    state = _load_heartbeat_row_state(daemon_name)
    if state is not None:
        return state["row_id"]
    row = smartsheet_client.find_row_by_primary(
        sheet_ids.SHEET_DAEMON_HEALTH,
        sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
        daemon_name,
    )
    if row is not None:
        row_id = int(row["_row_id"])
        _persist_heartbeat_row_state(daemon_name, row_id, total_cycles=0)
        return row_id
    created_id = _create_heartbeat_row(daemon_name)
    if created_id is None:
        return None
    post_find = smartsheet_client.find_row_by_primary(
        sheet_ids.SHEET_DAEMON_HEALTH,
        sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
        daemon_name,
    )
    row_id = created_id
    if post_find is not None and int(post_find["_row_id"]) != created_id:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            (
                f"Duplicate ITS_Daemon_Health rows for daemon={daemon_name!r}; "
                f"using first match {post_find['_row_id']}, manual cleanup "
                f"needed for row {created_id}."
            ),
            error_code="daemon_health_race_duplicate",
        )
        row_id = int(post_find["_row_id"])
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
    """Write one row to ITS_Daemon_Health summarizing this cycle. Never blocks
    the daemon's primary work — all failures are caught + logged."""
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
                "row id unresolved after self-provision attempt — skipping write",
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
        with circuit_breaker.bypass():
            smartsheet_client.update_row_cells_by_id(
                sheet_ids.SHEET_DAEMON_HEALTH, row_id, cells
            )
    except smartsheet_client.SmartsheetNotFoundError:
        _invalidate_heartbeat_row_state(daemon_name)
        _log_heartbeat_failure(daemon_name, f"row {row_id} not found — cache invalidated")
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
        Severity.WARN, SCRIPT_NAME,
        f"heartbeat write for daemon={daemon_name!r} failed: {detail}",
        error_code="daemon_health_write_failed",
    )


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this run."""
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


# ---- HMAC verification --------------------------------------------------


def _verify_row_hmac(row: dict[str, Any], provided_hmac: str, secret: str) -> bool:
    """Recompute the canonical HMAC for a pulled row and constant-time compare to
    `provided_hmac`. The downgrade defense: a row that fails is rejected, never
    filed. payload_json is used VERBATIM (re-serializing would change the bytes).

    `provided_hmac` is passed SEPARATELY (not read from `row`) so the HMAC value
    stays isolated to this verification step and never travels inside the row dict
    into intake or any log line — both better hygiene (an integrity tag has no
    business downstream) and it keeps CodeQL's clear-text-logging taint off the
    submission fields the daemon logs."""
    return portal_hmac.verify(
        secret,
        provided_hmac,
        submission_uuid=str(row.get("submission_uuid") or ""),
        job_id=str(row.get("job_id") or ""),
        form_code=str(row.get("form_code") or ""),
        work_date=str(row.get("work_date") or ""),
        payload_json=str(row.get("payload_json") or ""),
    )


def _handle_hmac_failure(
    row: dict[str, Any], correlation_id: str, *, base_url: str, bearer: str
) -> None:
    """Reject a bad-HMAC row: anomaly-log + Review-Queue (security_flag) + CRITICAL.

    NEVER handed to intake (downgrade defense) and NEVER mark-filed (the row stays
    in D1 for forensics). The caller records the UUID in the seen-set as 'rejected'
    so subsequent cycles skip re-flagging (no 60 s Review-Queue spam)."""
    submission_uuid = str(row.get("submission_uuid") or "")
    # Tripwire (Invariant 2, Layer 5) — record the suspicious output pattern.
    anomaly_logger.check({"portal_hmac_failure": submission_uuid, "job_id": row.get("job_id")})
    review_queue.add(
        workstream=WORKSTREAM,
        summary=(
            f"portal: HMAC verification FAILED for submission {submission_uuid} "
            f"(job_id={row.get('job_id')!r}) — rejected, NOT filed"
        ),
        payload={
            "submission_uuid": submission_uuid,
            "job_id": row.get("job_id"),
            "form_code": row.get("form_code"),
            "work_date": row.get("work_date"),
            # The HMAC value is deliberately NOT recorded — it is signature
            # material, isolated to verification; the submission_uuid + the
            # CRITICAL alert are the forensic handle, and the raw row stays in D1.
        },
        sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
        reason=review_queue.ReviewReason.SECURITY_TRIGGER,
        severity=Severity.CRITICAL,
        source_file=submission_uuid,
        security_flag=True,
    )
    error_log.log(
        Severity.CRITICAL, SCRIPT_NAME,
        (
            f"portal HMAC FAIL submission_uuid={submission_uuid} job_id={row.get('job_id')!r} "
            f"— rejected, not filed (downgrade defense)"
        ),
        error_code="portal_hmac_failure",
        correlation_id=correlation_id,
    )
    # M4 (PR-4): flip the row to box_verified=-1 (terminal) so /pending stops re-serving it every
    # cycle forever. The seen-set 'rejected' fast-path remains as belt-and-suspenders. Best-effort:
    # a transport failure just re-pulls (+ re-flags, seen-set-suppressed) next cycle, not a loss.
    try:
        portal_client.mark_rejected(
            base_url, bearer, submission_uuid=submission_uuid, reason="HMAC verification failed"
        )
    except portal_client.PortalTransportError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"portal could not mark submission {submission_uuid} rejected: {exc!r}",
            error_code="portal_mark_rejected_failed", correlation_id=correlation_id,
        )


# ---- Public API ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> PollStats:
    """Run one poll cycle. Public API; idempotent across crashes."""
    if not _polling_enabled():
        error_log.log(
            Severity.INFO, SCRIPT_NAME,
            "polling disabled via ITS_Config; exiting cycle",
            error_code="polling_disabled",
        )
        return PollStats(skipped_disabled=True)

    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            error_log.log(
                Severity.INFO, SCRIPT_NAME,
                "another poll cycle holds the lock; skipping this cycle",
                error_code="poll_lock_held",
            )
            return PollStats(skipped_locked=True)
        return _poll_inside_lock()


@dataclass(frozen=True)
class _PortalCreds:
    """Resolved portal credentials with NAMED fields.

    Deliberately a dataclass, NOT a `(base_url, bearer, secret)` tuple: tuple
    unpacking is taint-imprecise (CodeQL can't tell which element is the secret,
    so unpacking taints `base_url` from `bearer` and the false taint then rides
    the Worker request into the response → every logged submission field). With
    named fields CodeQL is field-sensitive, so `base_url` never inherits the
    bearer/secret taint. Also just clearer than positional unpacking.
    """

    base_url: str
    bearer: str
    secret: str


def _resolve_credentials() -> _PortalCreds | None:
    """Resolve portal credentials fail-CLOSED. None if any is absent."""
    base_url = _read_str_setting(CFG_WORKER_BASE_URL, "")
    try:
        bearer = keychain.get_secret(KC_BEARER)
        secret = keychain.get_secret(KC_HMAC_SECRET)
    except keychain.KeychainError:
        bearer = secret = ""
    if not (base_url and bearer and secret):
        return None
    return _PortalCreds(base_url=base_url, bearer=bearer, secret=secret)


def _push_active_jobs(base_url: str, bearer: str) -> None:
    """Full-replace push of the ITS_Active_Jobs set → the Worker's D1 dropdown cache.

    The pull model's symmetric write-leg: each cycle the Mac tells the Worker the
    current job set so the portal's job dropdown stays current (a job created via
    the ITS_Active_Jobs form appears within one cycle). Send-free — control-plane
    to OUR OWN Worker via the F02-allowlisted portal_client, NOT a customer send
    (outside the External Send Gate, Invariant 1).

    REFUSES to push an empty set: `active_jobs.list_all_jobs()` returns [] on a
    Smartsheet read miss, and pushing [] would deactivate the entire dropdown. So a
    transient Smartsheet outage is a no-op here, not a wipe (belt-and-suspenders
    with the Worker's own empty_jobs rejection).
    """
    all_jobs = active_jobs.list_all_jobs()
    if not all_jobs:
        return  # read miss / genuinely-empty sheet → skip, never wipe the dropdown
    payload = [
        {"job_id": j.job_id, "project_name": j.project_name, "active": 1 if j.is_active else 0}
        for j in all_jobs
    ]
    result = portal_client.push_jobs(base_url, bearer, payload)
    error_log.log(
        Severity.INFO, SCRIPT_NAME,
        f"job sync: upserted={result.get('upserted')} deactivated={result.get('deactivated')}",
        error_code="portal_job_sync_ok",
    )


def _poll_inside_lock() -> PollStats:
    """Body of poll_once running under the file lock."""
    creds = _resolve_credentials()
    if creds is None:
        # FAIL-CLOSED: missing bearer / HMAC secret / base URL → do NOT poll. This is a
        # MISCONFIG that will NOT self-heal (a removed/rotated Keychain entry or an unset
        # Worker base URL) and STOPS all filing → page immediately (CRITICAL). And do NOT
        # write the watchdog freshness marker: a cycle that never polled must let Check C go
        # stale, so a sustained no-creds state ALSO surfaces via the staleness floor (the
        # marker was previously written here, which masked the outage from Check C).
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            (
                # Deliberately does NOT interpolate the ITS_Config key or the
                # Keychain entry NAMES — naming secret-store entries in a log is
                # both a CodeQL clear-text-logging trip and poor hygiene. The
                # operator looks them up in the §43 runbook.
                "fail-closed: missing portal credentials — the Worker base URL "
                "(ITS_Config) and/or the bearer + HMAC-secret Keychain entries are unset; "
                "NOT polling and filing is STOPPED until fixed (see safety_reports/README.md §43)"
            ),
            error_code="portal_creds_missing",
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary="fail-closed: portal credentials missing")
        return PollStats(halted_no_creds=True)

    try:
        rows = portal_client.get_pending(creds.base_url, creds.bearer, limit=PENDING_LIMIT)
    except portal_client.PortalAuthError as exc:
        # 401 — bad/rotated/missing bearer. A MISCONFIG that will NOT self-heal and STOPS all
        # filing → page immediately (CRITICAL). No watchdog marker (let Check C go stale too).
        # Caught BEFORE PortalTransportError (its subclass) so auth never reads as transient.
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"portal pending fetch UNAUTHORIZED (401) — bearer token rejected; filing is "
            f"STOPPED until the token is fixed: {exc!r}",
            error_code="portal_pending_auth_failed", exc_info=repr(exc),
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary="pending fetch UNAUTHORIZED (401) — bearer rejected")
        return PollStats(errors=1)
    except portal_client.PortalTransportError as exc:
        # Transport failure (Worker down / wrong base URL / network). A one-off blip is ERROR
        # and self-heals; a SUSTAINED outage (>= threshold consecutive cycles) escalates to
        # CRITICAL — filing is stopped and that must PAGE, not just WARN at the next daily
        # watchdog. No watchdog marker either way (the un-faked Check-C marker is the backstop).
        n = _record_fetch_failure()
        sustained = n >= FETCH_FAIL_CRITICAL_THRESHOLD
        error_log.log(
            Severity.CRITICAL if sustained else Severity.ERROR, SCRIPT_NAME,
            f"failed to GET pending (consecutive failure #{n}"
            + (f", SUSTAINED >={FETCH_FAIL_CRITICAL_THRESHOLD} cycles — filing STOPPED" if sustained else "")
            + f"): {exc!r}",
            error_code="portal_pending_fetch_failed",
        )
        _write_heartbeat()
        _write_heartbeat_row(status="ERROR", items_processed=0,
                             error_summary=f"pending fetch failed (#{n}): {type(exc).__name__}")
        return PollStats(errors=1)

    # Fetch succeeded → clear the consecutive-failure counter (a recovered blip never
    # accumulates toward the CRITICAL threshold).
    _reset_fetch_failures()

    seen = _load_seen()
    counters = {
        "filed": 0, "reviewed": 0, "rejected": 0, "remarked": 0, "errors": 0,
        "pdf_serviced": 0,
    }

    for row in rows:
        # Split the HMAC off the row IMMEDIATELY: it is verified separately and
        # never travels into intake or any log line (the `clean` dict is what the
        # rest of the cycle sees). Keeps signature material isolated to verify.
        provided_hmac = str(row.get("hmac") or "")
        clean = {k: v for k, v in row.items() if k != "hmac"}
        try:
            _process_row(
                clean, provided_hmac, creds.base_url, creds.bearer, creds.secret,
                seen, counters,
            )
        except Exception as exc:  # noqa: BLE001 — per-row fence; one bad row never kills the cycle
            counters["errors"] += 1
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                (
                    f"per-row unexpected exception submission_uuid="
                    f"{clean.get('submission_uuid')!r}: {type(exc).__name__}: {exc!r}"
                ),
                error_code="portal_row_unexpected",
            )

    _persist_seen(seen)

    # Best-effort request-driven PDF cache servicing (PR-4 Part A). Placed AFTER the
    # intake drain + _persist_seen so a PDF-service failure can NEVER affect filing,
    # and FENCED identically to the job-sync below (a failure WARNs, never blocks the
    # pull). The pass is idempotent end-to-end (per-chunk INSERT OR REPLACE Worker-side),
    # so a skipped/failed cycle self-heals on the next.
    try:
        counters["pdf_serviced"] += _service_pdf_requests(creds.base_url, creds.bearer)
    except Exception as exc:  # noqa: BLE001 — best-effort; must not block intake filing
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"PDF-request servicing failed (intake unaffected): {type(exc).__name__}: {exc!r}",
            error_code="portal_pdf_service_failed",
        )

    # Best-effort job-set sync (ITS_Active_Jobs → the Worker's D1 dropdown cache).
    # FENCED so a sync failure never affects the intake drain above; the sync is
    # idempotent (full-replace), so a skipped/failed cycle self-heals next time.
    try:
        _push_active_jobs(creds.base_url, creds.bearer)
    except Exception as exc:  # noqa: BLE001 — best-effort; must not block intake filing
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"job sync push failed (intake unaffected): {type(exc).__name__}: {exc!r}",
            error_code="portal_job_sync_failed",
        )

    _write_heartbeat()

    if counters["errors"] > 0:
        cycle_status: HeartbeatStatus = "DEGRADED"
    elif counters["rejected"] > 0 or counters["reviewed"] > 0:
        cycle_status = "WARN"
    else:
        cycle_status = "OK"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"

    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=counters["filed"],
            error_summary=(
                None
                if counters["errors"] == 0 and counters["rejected"] == 0
                else f"errors={counters['errors']} rejected={counters['rejected']}"
            ),
            notes=(
                f"pdf_serviced={counters['pdf_serviced']}"
                if counters["pdf_serviced"] else None
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
            f"poll cycle: scanned={len(rows)} filed={counters['filed']} "
            f"reviewed={counters['reviewed']} rejected={counters['rejected']} "
            f"remarked={counters['remarked']} errors={counters['errors']} "
            f"pdf_serviced={counters['pdf_serviced']}"
        ),
        error_code="poll_cycle_summary",
    )
    return PollStats(
        scanned=len(rows),
        filed=counters["filed"],
        reviewed=counters["reviewed"],
        rejected=counters["rejected"],
        remarked=counters["remarked"],
        errors=counters["errors"],
        pdf_serviced=counters["pdf_serviced"],
    )


def _process_row(
    row: dict[str, Any],
    provided_hmac: str,
    base_url: str,
    bearer: str,
    secret: str,
    seen: dict[str, dict[str, Any]],
    counters: dict[str, int],
) -> None:
    """Verify + dispatch + receipt one pulled row. `row` is HMAC-free (the caller
    split the signature off into `provided_hmac`); mutates `seen` + `counters`."""
    submission_uuid = str(row.get("submission_uuid") or "")
    if not submission_uuid:
        # A row with no UUID can't be receipted/deduped — flag, don't dispatch.
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            "pulled row missing submission_uuid; skipping",
            error_code="portal_row_no_uuid",
        )
        return

    rec = seen.get(submission_uuid)
    if rec is not None:
        if rec.get("status") == "rejected":
            # Already flagged a bad-HMAC row in a PRIOR cycle (the seen-set
            # prevents Review-Queue spam on re-pulls). The first rejection did the
            # anomaly-log + Review-Queue + CRITICAL via _handle_hmac_failure; this
            # repeat is intentionally silent + never dispatched/drained.
            return
        if rec.get("status") == "filed":
            # Already filed but the row is being served again → the mark-filed
            # receipt was lost. Re-post it (no re-file) to drain the queue. Carry
            # box_file_id (PR-4) so the cache handle survives the re-post; fall back
            # to parsing the link for a seen-set record written before PR-4.
            box_link = str(rec.get("box_link") or "")
            box_file_id = str(rec.get("box_file_id") or "") or _box_file_id(box_link)
            if portal_client.mark_filed(
                base_url, bearer, submission_uuid=submission_uuid,
                box_link=box_link, box_file_id=box_file_id or None,
            ):
                counters["remarked"] += 1
            return

    correlation_id = uuid.uuid4().hex[:12]

    # Downgrade defense: verify the HMAC BEFORE intake ever sees the row.
    if not _verify_row_hmac(row, provided_hmac, secret):
        _handle_hmac_failure(row, correlation_id, base_url=base_url, bearer=bearer)
        seen[submission_uuid] = {"status": "rejected"}
        counters["rejected"] += 1
        return

    result = intake.process_portal_submission(row)

    if result.status == "error":
        # TRANSIENT — do NOT mark-filed, do NOT record seen; re-pull retries.
        counters["errors"] += 1
        return

    if result.status in DRAIN_STATUSES:
        box_link = result.box_link or ""
        box_file_id = result.box_file_id or ""
        marked = portal_client.mark_filed(
            base_url, bearer, submission_uuid=submission_uuid, box_link=box_link,
            box_file_id=box_file_id or None,
        )
        # Record as filed so a future re-serve (lost receipt) re-posts without re-filing.
        # box_file_id rides too so the re-post (843-851) re-carries the cache handle.
        seen[submission_uuid] = {
            "status": "filed", "box_link": box_link, "box_file_id": box_file_id,
        }
        if result.status == "review_queue":
            counters["reviewed"] += 1
        else:
            counters["filed"] += 1
        if not marked:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                (
                    f"mark-filed returned found=False for submission_uuid={submission_uuid} "
                    f"(status={result.status}); Worker had no matching row"
                ),
                error_code="portal_mark_filed_not_found",
                correlation_id=result.correlation_id,
            )


# ---- PR-4 Part A: request-driven PDF cache servicing ---------------------


def _service_pdf_requests(base_url: str, bearer: str) -> int:
    """Service request-driven PDF caches → returns the count of items serviced.

    A user who clicked "make available for download" flips pdf_requested=1 on a
    FILED submission; the Worker exposes those rows (box_file_id known, not yet
    cached) at GET /api/internal/pdf-requests. For each, this fetches the canonical
    filed PDF from Box (by box_file_id), base64-chunks it, and POSTs each chunk to
    POST /api/internal/filed-pdf — the Worker reassembles + serves the PM the
    byte-identical Box-filed copy.

    BEST-EFFORT + PER-ITEM FENCED: the whole pass is wrapped by the caller's
    try/except (a total failure WARNs, never blocks the intake pull); inside, one bad
    item (missing id / Box fetch error / upload error) is logged + skipped so it never
    aborts servicing the rest. Idempotent end-to-end — the Worker INSERT-OR-REPLACEs
    each chunk and a re-pulled request after a lost ack is a no-op.

    Box fetch is generation-side (already used by intake); the HTTPS post-back rides
    the F02-allowlisted portal_client. No send capability, no LLM (Invariant 1).
    """
    rows = portal_client.get_pdf_requests(base_url, bearer, limit=PDF_REQUEST_LIMIT)
    serviced = 0
    for row in rows:
        submission_uuid = str(row.get("submission_uuid") or "")
        box_file_id = str(row.get("box_file_id") or "")
        if not submission_uuid or not box_file_id:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"pdf-request row missing submission_uuid/box_file_id; skipping "
                f"(submission_uuid={submission_uuid!r})",
                error_code="portal_pdf_request_malformed",
            )
            continue
        try:
            pdf = box_client.download_file(box_file_id)
            if not pdf:
                # A zero-byte filed PDF is a DATA error (a render/upload that produced
                # nothing), not a chunk to ship — an empty chunk_b64 would only be 400'd by
                # the Worker. Surface it as a WARN skip; the request stays unready and the
                # operator can re-file. (Never the silent empty-chunk the Worker rejects.)
                error_log.log(
                    Severity.WARN, SCRIPT_NAME,
                    f"pdf-request: Box file {box_file_id} returned 0 bytes; skipping "
                    f"(submission_uuid={submission_uuid!r})",
                    error_code="portal_pdf_empty_file",
                )
                continue
            chunks = [
                pdf[i:i + PDF_CHUNK_BYTES] for i in range(0, len(pdf), PDF_CHUNK_BYTES)
            ]
            total = len(chunks)
            for index, chunk in enumerate(chunks):
                portal_client.upload_filed_pdf(
                    base_url, bearer,
                    submission_uuid=submission_uuid,
                    chunk_index=index,
                    chunk_total=total,
                    chunk_b64=base64.b64encode(chunk).decode(),
                )
            serviced += 1
        except Exception as exc:  # noqa: BLE001 — per-item fence; one bad item never aborts the pass
            # NEVER interpolate PDF bytes / chunk_b64 into the log line.
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"pdf-request servicing failed for submission_uuid={submission_uuid}: "
                f"{type(exc).__name__}: {exc!r}",
                error_code="portal_pdf_request_item_failed",
            )
    return serviced


if __name__ == "__main__":
    poll_once()
