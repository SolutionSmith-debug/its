"""Safety Reports intake polling daemon — launchd-driven Graph reader.

Replaces the prior Mail.app rule trigger for `safety_reports/intake.py`.
The Mail.app rule had a long-running operational tail (per
`docs/tech_debt.md`: the silent-disable pattern that motivated watchdog
Check F). This module reads the safety mailbox directly from Microsoft
Graph and invokes `intake.process_message` per unread message.

launchd schedule
----------------

Single-cycle execution: `poll_once()` is the public API; the `__main__`
guard calls it exactly once and exits. launchd handles the cadence via
StartInterval in `scripts/launchd/org.solutionsmith.its.safety-intake.plist`
(default 60 s; sourced from ITS_Config row
`safety_reports.intake.poll_interval_seconds` at install time).

Per-cycle behavior
------------------

  1. `polling_enabled` ITS_Config gate — false short-circuits the cycle.
  2. fcntl file lock at `~/its/state/safety_intake.lock` — skip-if-held.
     Prevents launchd-overlap collisions if a previous cycle took longer
     than the interval.
  3. `graph_client.list_inbox` with `unread_only` filter (top=50).
  4. For each unread message:
       a. Skip if message_id is in the local seen-set (defense in depth
          against double-fetch — the canonical idempotency guarantee is
          `mark_read` below, which the inbox query also respects, but
          the seen-set covers a race window in which a message was
          processed but `mark_read` failed).
       b. `intake.process_message(message_id)` runs the full pipeline.
       c. On success status (processed / review_queue / quarantined /
          skipped_swo_other): `graph_client.mark_read` commits the
          inbox-cursor advance.
       d. On `error` status: message stays unread, retried next cycle.
       e. Record the message_id + status + timestamp into the seen-set
          state file regardless of mark_read outcome (forensic record;
          1000-entry FIFO cap).
  5. Write heartbeat timestamp to `~/its/state/safety_intake_heartbeat.txt`.

Push-vs-record separation
-------------------------

Per Op Stds v11 §3.1: the seen-set file is forensic-record-only — losing it
does NOT cause double-processing because `mark_read` (the canonical push
checkpoint) on the prior message already kept it out of subsequent
`list_inbox` results. The seen-set is the defense-in-depth layer that
covers the narrow window between `process_message` returning and
`mark_read` committing. If the OS crashes between those two calls, the
next cycle's list_inbox will RE-deliver the message (still unread), but
the seen-set guards against re-running the pipeline on it. If we lose
both the seen-set AND the mark_read commit (e.g., disk corruption), the
pipeline's idempotency relies on the existing per-stage idempotency
(Daily Reports rows are NOT keyed on message_id, so a double-process
would create a duplicate row — operator inspection then deletion is the
recovery). The combination is acceptable for the operational risk
profile; a hardened idempotency layer (e.g., message_id → row_id index)
is documented in `docs/tech_debt.md` as out of scope for Phase 1.

Capability gating
-----------------

No customer-facing send capability. Imports `shared.graph_client` for
read-only methods + `mark_read` (which is an inbox-cursor write, not an
external transmission). Does NOT import `send_mail`. Enforced by
`tests/test_capability_gating.py` (GATED_SCRIPTS list) and
`tests/test_intake_capability_gating.py` (per-file AST scans).
"""
from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from safety_reports import intake
from shared import (
    circuit_breaker,
    error_log,
    graph_client,
    sheet_ids,
    smartsheet_client,
    state_io,
)
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "safety_reports.intake_poll"
WORKSTREAM = "safety_reports"

# ITS_Config keys (seeded by
# scripts/migrations/seed_safety_intake_polling_config.py).
CFG_POLLING_ENABLED = "safety_reports.intake.polling_enabled"
CFG_MAILBOX = "safety_reports.intake.mailbox"

# Defaults — used when the ITS_Config row is missing or unparseable. Each
# fallback is operationally safe: polling default ON (the daemon's whole
# purpose), default mailbox the sandbox value, default lookback covers the
# operational interval.
DEFAULT_POLLING_ENABLED = True
DEFAULT_MAILBOX = intake.DEFAULT_MAILBOX

# Per-cycle inbox window. Graph caps at 1000; 50 is comfortable headroom
# for the construction-workflow tempo of safety mail (typically 10-30
# messages/day across all crews at peak).
LIST_INBOX_TOP = 50

# Seen-set state file. JSON object keyed by Graph message_id; trimmed to
# the most recent SEEN_CAP entries via FIFO on the embedded timestamp.
STATE_DIR = Path.home() / "its" / "state"
SEEN_PATH = STATE_DIR / "safety_intake_processed.json"
HEARTBEAT_PATH = STATE_DIR / "safety_intake_heartbeat.txt"
LOCK_PATH = STATE_DIR / "safety_intake.lock"
SEEN_CAP = 1000

# Heartbeat row state file. JSON object keyed by daemon_name with shape
# {daemon_name: {"row_id": int, "total_cycles": int}}. Persists the
# ITS_Daemon_Health row-id (PR #59.5 ARCH-2) so each cycle skips the
# find_row_by_primary lookup, and the lifetime monotonic total_cycles
# counter (PR #59.5 ARCH-3) so we don't read-before-write the column
# per cycle. Survives process restarts (launchd-poll-once architecture);
# in-memory cache would not.
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"

# Stable daemon-name identifier — primary key in ITS_Daemon_Health and
# the state-file dict key. Hardcoded here because intake_poll is the only
# consumer right now. When a second daemon adopts the same pattern
# (PR #60 territory) this becomes a constructor argument on a shared
# helper extracted to shared/heartbeat.py.
DAEMON_NAME = "safety_reports.intake_poll"

# A1 self-provision metadata. Per-daemon values written ONCE to
# ITS_Daemon_Health when this daemon's row is absent (see
# `_create_heartbeat_row`); the per-cycle columns are filled by the very next
# `_write_heartbeat_row` update. These two constants are the ONLY per-daemon
# difference in the otherwise byte-identical heartbeat helpers — keep them OUT
# of the helper bodies so the verbatim-duplication invariant (and the future
# `shared/heartbeat.py` extraction) stays clean.
_REGISTRATION_INTERVAL_SECONDS = 60  # 60s launchd cadence
_REGISTRATION_SOURCE_ID = DEFAULT_MAILBOX  # the polled safety mailbox

# Watchdog Check C marker — matches the TRACKED_JOBS entry in
# scripts/watchdog.py. Mirrors the weekly_send_poll pattern
# (safety_reports/weekly_send_poll.py). The slug, the TRACKED_JOBS entry, and
# the {slug}.last_run filename the watchdog reads MUST stay identical or the
# watchdog tracks a marker nothing writes → permanent false WARN.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_intake"

# Allowed cycle-status values written to ITS_Daemon_Health.Last_Cycle_Status.
# OK / WARN / ERROR are the canonical severity ladder; SKIPPED covers the
# polling_disabled / lock_held early-exit branches if they ever reach the
# heartbeat write (currently they don't — included for completeness so a
# future caller can use it without changing the function); skipped_swo_other
# is a pass-through to surface the SWO/Other category-skip status in
# operator-facing reports without forcing the operator to grep Notes.
# CIRCUIT_OPEN (F08) overrides the cycle status when the Smartsheet circuit
# breaker is OPEN — set from a lock-free circuit_breaker.is_open() check at the
# status-determination point; the heartbeat write itself runs under
# circuit_breaker.bypass() so the status can still land when Smartsheet is
# reachable.
HeartbeatStatus = Literal[
    "OK", "WARN", "ERROR", "SKIPPED", "skipped_swo_other", "CIRCUIT_OPEN"
]

# Process result statuses that should result in `mark_read`. Mirror of the
# `success` statuses produced by `intake.process_message`. `error` is the
# only status that leaves the message unread for retry.
MARK_READ_STATUSES = frozenset({
    "processed",
    "review_queue",
    "quarantined",
    "skipped_swo_other",
})


@dataclass(frozen=True)
class PollStats:
    """Summary of one poll_once() invocation. Returned for caller logging."""
    skipped_disabled: bool = False
    skipped_locked: bool = False
    messages_fetched: int = 0
    messages_processed: int = 0
    messages_skipped_seen: int = 0
    messages_marked_read: int = 0
    errors: int = 0


# ---- Config readers ------------------------------------------------------


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


# ---- State helpers -------------------------------------------------------


@contextmanager
def _file_lock(path: Path) -> Iterator[bool]:
    """Acquire an exclusive non-blocking lock on `path`.

    Yields True if the lock was acquired, False if another process holds
    it. Returning False is the documented skip-if-held behavior (NOT an
    error) — it means a prior poll cycle is still running and the current
    invocation should exit cleanly to avoid two daemons hammering Graph.
    The contextmanager handles unlock + file close in either case.
    """
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
        if acquired:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                # Best-effort unlock; the file close below releases it
                # too via kernel-level cleanup if flock_un raises.
                pass
        handle.close()


def _load_seen() -> dict[str, dict[str, str]]:
    """Load the seen-set state file. Empty dict on missing / corrupt file."""
    if not SEEN_PATH.exists():
        return {}
    try:
        raw = SEEN_PATH.read_text()
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Drop entries whose value shape isn't what we expect — defensive
    # against a corrupted file rather than crashing the poll cycle.
    clean: dict[str, dict[str, str]] = {}
    for k, v in parsed.items():
        if isinstance(k, str) and isinstance(v, dict):
            clean[k] = {ks: str(vs) for ks, vs in v.items() if isinstance(ks, str)}
    return clean


def _record_seen(
    seen: dict[str, dict[str, str]],
    message_id: str,
    status: str,
) -> None:
    """Record one message_id → status in the seen-set; trim to SEEN_CAP.

    FIFO trim by the embedded timestamp value. The trim runs every call
    rather than on a watermark to keep the on-disk file size bounded
    even if the cap-reduction logic ever has a bug — small cost (<1ms
    for a 1000-entry dict).
    """
    seen[message_id] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": status,
    }
    if len(seen) > SEEN_CAP:
        # Keep most-recent SEEN_CAP entries.
        sorted_items = sorted(seen.items(), key=lambda kv: kv[1].get("timestamp", ""))
        keep = dict(sorted_items[-SEEN_CAP:])
        seen.clear()
        seen.update(keep)
    state_io.atomic_write_json(SEEN_PATH, seen)


def _write_heartbeat() -> None:
    """Overwrite the heartbeat file with the current UTC ISO timestamp.

    Local-filesystem signal of daemon liveness. Cheap, always works (no
    Smartsheet round trip), survives Smartsheet outages. Pairs with the
    Smartsheet-row heartbeat written by `_write_heartbeat_row` — the file
    is the watchdog signal; the row is the operator-visible status.
    """
    state_io.atomic_write_text(HEARTBEAT_PATH, datetime.now(UTC).isoformat())


# ---- Watchdog Check C marker --------------------------------------------


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this cycle.

    Mirrors weekly_send_poll._write_watchdog_marker. Fail-soft per Op Stds
    §3.1: a marker-write failure must not fail the poll cycle — the cycle's
    real work (row writes, mark-read, heartbeat) already succeeded. The
    watchdog will WARN on a stale marker, which is the correct signal if
    this keeps failing.
    """
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


# ---- Heartbeat-row state cache (ITS_Daemon_Health) ----------------------


def _load_heartbeat_row_state(daemon_name: str) -> dict[str, Any] | None:
    """Read `{daemon_name: {row_id, total_cycles}}` from the state file.

    Returns None if the file is missing, unreadable, or doesn't have an
    entry for this daemon. Callers handle the None case by re-resolving
    the row ID via `smartsheet_client.find_row_by_primary` and starting
    the lifetime counter at 0.
    """
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

    The file is SHARED with weekly_send_poll, so the read-modify-write
    triple runs under `state_io.with_path_lock` against the sidecar
    `{HEARTBEAT_ROW_STATE_PATH}.lock`. Lock-timeout fails open: log WARN
    + skip this cycle's write per the heartbeat-never-blocks-daemon
    contract (CLAUDE.md operator-visibility surface). Preserves entries
    for other daemons so the same file can be shared by future polling
    consumers.
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
    """Remove a daemon's entry from the state file. Used on 404 to force
    a re-lookup via find_row_by_primary on the next cycle.

    Same shared-file lock contract as `_persist_heartbeat_row_state`.
    Lock-timeout fails open: log WARN + skip (the stale cache entry just
    resurfaces on the next cycle and re-resolves on its own 404).
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


def _create_heartbeat_row(daemon_name: str) -> int | None:
    """Self-provision this daemon's ITS_Daemon_Health row (A1, find-or-create).

    Called by `_resolve_heartbeat_row_id` when no row exists for this daemon's
    primary key, so a newly-added daemon registers its own operator-visibility
    row instead of going dark (the 2026-06-02 `weekly_send_poll`-was-dark gap).
    Writes the registration columns only; `last_cycle_status` and the other
    per-cycle columns are filled by the `_write_heartbeat_row` update that runs
    immediately after, in the same cycle. Deliberately omits a
    `last_cycle_status` seed so the create can't be rejected by a
    restrict-to-dropdown PICKLIST and so the first status the operator sees is
    a real one. Registers `Enabled=True` — NOT the schema doc's seed-time
    `Enabled=false`-then-flip convention: a self-provisioning daemon is by
    definition already running, and the operator health report filters on
    `Enabled=true`, so a live daemon must register enabled to be visible (the
    whole point of self-provision). (The blueprint schema doc's manual-seed
    convention predates this self-provision model — flagged for a references-pass.)

    ID-keyed via `smartsheet_client.add_row_by_id` for the same
    column-rename-stability reason as the update path (`sheet_ids.py`).

    Heartbeat-never-blocks contract: any failure is logged to ITS_Errors
    (`error_code='daemon_health_write_failed'`) and returns None — the caller
    then skips this cycle's heartbeat write and the next cycle retries. Runs
    under `circuit_breaker.bypass()` so an OPEN Smartsheet breaker doesn't stop
    a daemon from registering its visibility row (one extra control-plane call
    per resurrection, no new hammering — same rationale as the update write).
    """
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
    """Return the ITS_Daemon_Health row id for `daemon_name`, find-or-create.

    Cache hit → cached id. Else `find_row_by_primary`; on a hit, cache and
    return. On a miss, self-provision the row (A1) so the daemon is never dark
    on the operator surface, with a week_folder-style post-create race re-find.
    Returns None only when the create itself failed (already logged) — the
    caller skips this cycle and retries next.
    """
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
    # A1 self-provision: no row for this daemon's primary key — create one so
    # the daemon registers its own visibility row instead of logging "seeder
    # needed" and going dark every cycle.
    created_id = _create_heartbeat_row(daemon_name)
    if created_id is None:
        return None
    # Race-safety re-find (mirror week_folder.py). The per-cycle fcntl lock +
    # launchd one-shot model already serialize a single daemon's own cycles, so
    # the racer this guards is NOT two concurrent cycles — it is a manual
    # operator/seeder hand-creating the row between our create and this re-find
    # (Smartsheet enforces no primary-key uniqueness, so that would duplicate).
    # Belt-and-suspenders: adopt the first match, WARN, leave the duplicate for
    # operator cleanup. Bounded blast radius: one extra row.
    post_find = smartsheet_client.find_row_by_primary(
        sheet_ids.SHEET_DAEMON_HEALTH,
        sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
        daemon_name,
    )
    row_id = created_id
    if post_find is not None and int(post_find["_row_id"]) != created_id:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
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
    """Write one row to ITS_Daemon_Health summarizing this cycle.

    ARCH-1 (PR #59.5): The `Enabled` column on ITS_Daemon_Health is
    report-filter metadata and is NOT read by this function. The runtime
    on/off decision lives in ITS_Config (`safety_reports.intake.polling_enabled`).
    One canonical runtime gate, one operator-facing filter flag, no overlap.

    ARCH-2 (PR #59.5): Row-id is looked up via `_resolve_heartbeat_row_id`
    which prefers the cache file at `~/its/state/heartbeat_row_ids.json`.
    On 404 during update (row was deleted / re-seeded), the cache is
    invalidated and the next cycle re-resolves.

    ARCH-3 (PR #59.5): `total_cycles` is a LIFETIME monotonic counter
    persisted alongside the row_id in the same state file. Read +
    incremented + written each cycle without ever reading the column
    back from Smartsheet. The Smartsheet column title may still read
    "Total Cycles Today" (UI-only rename is a separate cleanup); the
    semantics here are lifetime.

    Failure handling: catches SmartsheetError + any other exception
    internally, logs to ITS_Errors with `error_code='daemon_health_write_failed'`,
    returns None. Caller's catch-all is defense in depth.
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
            # A1: _resolve now self-provisions a missing row, so a None here
            # means the create itself failed (already logged with its own
            # detail) — skip this cycle's write; next cycle retries.
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
        # F08: bypass the breaker for this control-plane write so a CIRCUIT_OPEN
        # status can still land when Smartsheet is reachable. This is the
        # already-once-per-cycle heartbeat write, so it adds no new hammering.
        with circuit_breaker.bypass():
            smartsheet_client.update_row_cells_by_id(
                sheet_ids.SHEET_DAEMON_HEALTH, row_id, cells
            )
    except smartsheet_client.SmartsheetNotFoundError:
        # Row was deleted / re-seeded; invalidate cache so next cycle
        # re-resolves via find_row_by_primary. Don't retry inline —
        # one stale row write per resurrection is acceptable.
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
    """Log a heartbeat-write failure to ITS_Errors with a stable error code.

    Operator search path: grep ITS_Errors for `daemon_health_write_failed`
    or filter the sheet on that error_code. The detail string carries the
    underlying exception class + message for triage. Logging itself is
    failure-isolated by `shared/error_log.py`'s existing guards.
    """
    error_log.log(
        Severity.WARN,
        SCRIPT_NAME,
        f"heartbeat write for daemon={daemon_name!r} failed: {detail}",
        error_code="daemon_health_write_failed",
    )


# ---- Inbox helpers -------------------------------------------------------


def _list_unread(mailbox: str, *, top: int = LIST_INBOX_TOP) -> list[dict[str, Any]]:
    """Return unread messages from `mailbox`'s Inbox folder.

    Wraps `graph_client.list_inbox` with the `isRead eq false` $filter.
    Graph supports compound `$filter` but doesn't let you mix `$filter`
    with the `since` shortcut in `graph_client.list_inbox`, so we use the
    `fields` parameter to project just `id` (we don't need anything
    else; `process_message` re-fetches the full message).
    """
    # graph_client.list_inbox supports a `since` filter but not a
    # canned `unread_only` flag, so we filter post-fetch on `isRead`.
    # The list endpoint returns the field by default; we project it
    # explicitly to be sure.
    raw = graph_client.list_inbox(
        mailbox, top=top, fields=["id", "isRead", "receivedDateTime"]
    )
    return [m for m in raw if not m.get("isRead", False)]


# ---- Public API ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> PollStats:
    """Run one poll cycle. Public API; idempotent across crashes.

    Returns a PollStats summary regardless of outcome. Unhandled exceptions
    propagate to the `@its_error_log` decorator (so they land in
    ITS_Errors + the triple-fire alert path) and then re-raise so launchd
    sees a non-zero exit and the operator notices via the log files.

    `poll_once` is decorated with `@require_active` which honors the
    global system.state kill switch. The per-workstream
    `polling_enabled` ITS_Config row is a finer-grained gate read inside
    the function body (after the kill-switch check) so the operator can
    halt just safety intake without taking down the rest of ITS.
    """
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
            # Another poll cycle is in flight; exit cleanly.
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
    mailbox = _read_str_setting(CFG_MAILBOX, DEFAULT_MAILBOX)
    messages = _list_unread(mailbox)
    seen = _load_seen()

    stats = PollStats(messages_fetched=len(messages))
    counters = {
        "processed": stats.messages_processed,
        "skipped_seen": stats.messages_skipped_seen,
        "marked_read": stats.messages_marked_read,
        "errors": stats.errors,
    }

    for msg in messages:
        message_id = msg.get("id")
        if not isinstance(message_id, str):
            continue
        if message_id in seen:
            counters["skipped_seen"] += 1
            continue

        result = intake.process_message(message_id, mailbox=mailbox)
        counters["processed"] += 1

        if result.status in MARK_READ_STATUSES:
            try:
                graph_client.mark_read(mailbox, message_id)
                counters["marked_read"] += 1
            except graph_client.GraphError as exc:
                # Don't fail the loop on mark_read errors — the message
                # was processed (row written, etc.). Next cycle will
                # re-list the message; the seen-set guard prevents
                # double-processing.
                counters["errors"] += 1
                error_log.log(
                    Severity.WARN,
                    SCRIPT_NAME,
                    f"mark_read failed for message_id={message_id}: {exc!r}",
                    error_code="mark_read_failed",
                    correlation_id=result.correlation_id,
                )

        if result.status == "error":
            counters["errors"] += 1

        _record_seen(seen, message_id, result.status)

    _write_heartbeat()
    # Rationale: the watchdog marker is written ONLY on a completed cycle, NOT
    # on the polling-disabled or lock-held skip paths in poll_once(). intake_poll
    # runs every 60s; a persistently disabled or perpetually lock-held poller is
    # a state the watchdog SHOULD eventually surface (Check C goes stale → WARN).
    # This deliberately diverges from weekly_send_poll, whose SkippedWeeklyOther
    # path is a normal, expected weekly state that must keep the marker fresh.
    # Here, "not running" is exactly what we want detected. A zero-message cycle
    # still reaches here and is a valid "poller alive" signal.
    # Reference: audit F17 (its-blueprint/audits/2026-05-25_forensic-audit.md).
    _write_watchdog_marker()

    final = PollStats(
        messages_fetched=stats.messages_fetched,
        messages_processed=counters["processed"],
        messages_skipped_seen=counters["skipped_seen"],
        messages_marked_read=counters["marked_read"],
        errors=counters["errors"],
    )

    # Heartbeat-row write happens AFTER the local file heartbeat + counters
    # are finalized so the row reflects the same cycle. Wrapped in a
    # belt-and-suspenders catch-all: _write_heartbeat_row already swallows
    # SmartsheetError + Exception internally and logs to ITS_Errors, but
    # the outer try/except guarantees a heartbeat failure NEVER blocks
    # the daemon's primary work (the cycle's INFO summary still emits;
    # the cycle still returns success to launchd).
    # F08: surface a degraded Smartsheet via the heartbeat. Lock-free local
    # read (no Smartsheet call) so it's safe even when the breaker is OPEN.
    cycle_status: HeartbeatStatus = "OK" if final.errors == 0 else "WARN"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"
    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=final.messages_processed,
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat must never block the daemon
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"heartbeat write outer-catch tripped: {exc!r}",
            error_code="daemon_health_write_failed",
        )

    error_log.log(
        Severity.INFO,
        SCRIPT_NAME,
        (
            f"poll cycle: fetched={final.messages_fetched} "
            f"processed={final.messages_processed} "
            f"skipped_seen={final.messages_skipped_seen} "
            f"marked_read={final.messages_marked_read} "
            f"errors={final.errors}"
        ),
        error_code="poll_cycle_summary",
    )
    return final


if __name__ == "__main__":
    poll_once()
