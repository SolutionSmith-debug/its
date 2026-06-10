"""ITS daemon-health heartbeat reporter — the single source for ITS_Daemon_Health writes.

Purpose
-------
The per-cycle ITS_Daemon_Health row write (the operator-visibility surface, Op Stds §32) plus
the liveness-file touch, extracted VERBATIM from the copies that were replicated across
``safety_reports/{portal_poll, weekly_send_poll}`` (originally from the retired ``intake_poll``).
The §14 extraction threshold — ≥4 real consumers — is met: portal_poll, weekly_send_poll,
compile_now_poll, publish_daemon. ``HeartbeatReporter`` encapsulates the per-daemon config
(script name, daemon name, liveness path, registration metadata); the row-id cache file
(``heartbeat_row_ids.json``) is SHARED across all daemons (ARCH-2) and stays a module constant.

This is a preservation extraction (Op Stds §14): the method bodies are the byte-for-byte logic
of the prior per-daemon functions (verified AST-identical across the two copies before
extraction), with the per-daemon module globals replaced by ``self.*`` attributes. The daemons
keep thin ``_write_heartbeat`` / ``_write_heartbeat_row`` wrappers that delegate here, so their
call sites and tests are unchanged.

Invariants
----------
- Heartbeat NEVER blocks the daemon's primary work: every Smartsheet failure is caught + logged
  to ITS_Errors (``error_code='daemon_health_write_failed'``); the method returns and the cycle
  proceeds (project CLAUDE.md "Heartbeat write must NEVER block daemon primary work").
- ARCH-1: the Enabled column is report-filter metadata only; the canonical runtime gate is
  ITS_Config ``<workstream>.<daemon>.polling_enabled`` (not touched here).
- ARCH-2: the row-id cache (``heartbeat_row_ids.json``) is SHARED across daemons; writes go
  through ``shared.state_io`` atomic-write under a sidecar ``.lock``.
- ARCH-3: Total Cycles is lifetime-monotonic, NOT daily-reset.
- A1 self-provision: a missing row is find-or-created (under ``circuit_breaker.bypass()``) so a
  newly-added daemon never goes dark on the operator surface.

Failure modes
-------------
- Smartsheet unreachable / row missing / lock timeout → logged WARN to ITS_Errors, method
  returns (the cycle continues). A self-provision create failure → ``None`` row id → this cycle's
  write is skipped; the next cycle retries.

Consumers
---------
- safety_reports/portal_poll.py, weekly_send_poll.py, compile_now_poll.py, publish_daemon.py
  (each constructs one module-level ``HeartbeatReporter``).
- tests/test_heartbeat.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from shared import circuit_breaker, error_log, sheet_ids, smartsheet_client, state_io
from shared.error_log import Severity

# Per-cycle status vocabulary (was a per-daemon Literal). These are the restrict-to-dropdown
# values in ITS_Daemon_Health."Last Cycle Status".
HeartbeatStatus = Literal["OK", "WARN", "DEGRADED", "ERROR", "CIRCUIT_OPEN", "PAUSED"]

# ARCH-2: the row-id cache is SHARED across all daemons (one file, daemon_name-keyed). Path +
# semantics are unchanged by this extraction.
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"


class HeartbeatReporter:
    """Per-daemon ITS_Daemon_Health reporter. Construct one at daemon module load with the
    daemon's config; call ``write_liveness()`` + ``write_row(...)`` once per cycle. All Smartsheet
    failures are caught internally — the daemon's primary work is never blocked."""

    def __init__(
        self,
        *,
        script_name: str,
        daemon_name: str,
        workstream: str,
        liveness_path: Path,
        interval_seconds: int,
        source_id: str,
    ) -> None:
        self.script_name = script_name
        self.daemon_name = daemon_name
        self.workstream = workstream
        self.liveness_path = liveness_path
        self.interval_seconds = interval_seconds
        self.source_id = source_id

    # ---- liveness file ------------------------------------------------------
    def write_liveness(self) -> None:
        """Overwrite the liveness file with the current UTC ISO timestamp."""
        state_io.atomic_write_text(self.liveness_path, datetime.now(UTC).isoformat())

    # ---- heartbeat-row state cache (ITS_Daemon_Health) ----------------------
    def _load_row_state(self) -> dict[str, Any] | None:
        """Read ``{daemon_name: {row_id, total_cycles}}`` from the state file."""
        if not HEARTBEAT_ROW_STATE_PATH.exists():
            return None
        try:
            raw = HEARTBEAT_ROW_STATE_PATH.read_text()
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        entry = parsed.get(self.daemon_name)
        if not isinstance(entry, dict):
            return None
        row_id = entry.get("row_id")
        total_cycles = entry.get("total_cycles")
        if not isinstance(row_id, int) or not isinstance(total_cycles, int):
            return None
        return {"row_id": row_id, "total_cycles": total_cycles}

    def _persist_row_state(self, row_id: int, total_cycles: int) -> None:
        """Atomically merge ``{daemon_name: {row_id, total_cycles}}`` into the state file.

        Shared-file lock contract: the read-modify-write triple runs under
        ``state_io.with_path_lock``. Lock-timeout fails open: log WARN + skip (next cycle re-tries).
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
                current[self.daemon_name] = {"row_id": row_id, "total_cycles": total_cycles}
                state_io.atomic_write_json(HEARTBEAT_ROW_STATE_PATH, current)
        except state_io.StateLockTimeoutError:
            error_log.log(
                Severity.WARN,
                self.script_name,
                f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries",
                error_code="daemon_health_write_failed",
            )

    def _invalidate_row_state(self) -> None:
        """Remove this daemon's entry from the state file (forces re-lookup).

        Same shared-file lock contract as ``_persist_row_state``. Lock-timeout fails open: the
        stale cache resurfaces and re-resolves on the next cycle's 404.
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
                parsed.pop(self.daemon_name, None)
                state_io.atomic_write_json(HEARTBEAT_ROW_STATE_PATH, parsed)
        except state_io.StateLockTimeoutError:
            error_log.log(
                Severity.WARN,
                self.script_name,
                f"could not acquire lock on {HEARTBEAT_ROW_STATE_PATH} after retries (invalidate)",
                error_code="daemon_health_write_failed",
            )

    def _create_row(self) -> int | None:
        """Self-provision this daemon's ITS_Daemon_Health row (A1, find-or-create).

        Called by ``_resolve_row_id`` when no row exists for this daemon's primary key, so a
        newly-added daemon registers its own operator-visibility row instead of going dark.
        Writes the registration columns only; ``last_cycle_status`` and the other per-cycle
        columns are filled by the ``write_row`` update that runs immediately after, in the same
        cycle. Deliberately omits a ``last_cycle_status`` seed so the create can't be rejected by
        a restrict-to-dropdown PICKLIST and so the first status the operator sees is a real one.
        Registers ``Enabled=True`` — a self-provisioning daemon is by definition already running,
        and the operator health report filters on ``Enabled=true``, so a live daemon must register
        enabled to be visible.

        ID-keyed via ``smartsheet_client.add_row_by_id`` for column-rename stability (sheet_ids).

        Heartbeat-never-blocks contract: any failure is logged to ITS_Errors
        (``error_code='daemon_health_write_failed'``) and returns None — the caller then skips this
        cycle's heartbeat write and the next cycle retries. Runs under ``circuit_breaker.bypass()``
        so an OPEN Smartsheet breaker doesn't stop a daemon from registering its visibility row.
        """
        cols = sheet_ids.DAEMON_HEALTH_COLUMNS
        payload: dict[int, Any] = {
            cols["daemon_name"]: self.daemon_name,
            cols["workstream"]: self.workstream,
            cols["enabled"]: True,
            cols["interval_seconds"]: self.interval_seconds,
            cols["source_id"]: self.source_id,
        }
        try:
            with circuit_breaker.bypass():
                return smartsheet_client.add_row_by_id(sheet_ids.SHEET_DAEMON_HEALTH, payload)
        except smartsheet_client.SmartsheetError as exc:
            self._log_failure(f"self-provision create failed: {exc!r}")
            return None
        except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
            self._log_failure(f"self-provision unexpected: {exc!r}")
            return None

    def _resolve_row_id(self) -> int | None:
        """Return the ITS_Daemon_Health row id for this daemon, find-or-create.

        Cache hit → cached id. Else ``find_row_by_primary``; on a hit, cache and return. On a
        miss, self-provision the row (A1) so the daemon is never dark on the operator surface,
        with a week_folder-style post-create race re-find. Returns None only when the create
        itself failed (already logged) — the caller skips this cycle and retries next.
        """
        state = self._load_row_state()
        if state is not None:
            return state["row_id"]
        row = smartsheet_client.find_row_by_primary(
            sheet_ids.SHEET_DAEMON_HEALTH,
            sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
            self.daemon_name,
        )
        if row is not None:
            row_id = int(row["_row_id"])
            self._persist_row_state(row_id, total_cycles=0)
            return row_id
        # A1 self-provision: no row for this daemon's primary key — create one so the daemon
        # registers its own visibility row instead of going dark every cycle.
        created_id = self._create_row()
        if created_id is None:
            return None
        # Race-safety re-find (mirror week_folder.py). The racer this guards is NOT two concurrent
        # cycles (the fcntl lock + launchd one-shot serialize a daemon's own cycles) — it is a
        # manual operator/seeder hand-creating the row between our create and this re-find
        # (Smartsheet enforces no primary-key uniqueness). Belt-and-suspenders: adopt the first
        # match, WARN, leave the duplicate for operator cleanup. Bounded blast radius: one row.
        post_find = smartsheet_client.find_row_by_primary(
            sheet_ids.SHEET_DAEMON_HEALTH,
            sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
            self.daemon_name,
        )
        row_id = created_id
        if post_find is not None and int(post_find["_row_id"]) != created_id:
            error_log.log(
                Severity.WARN,
                self.script_name,
                (
                    f"Duplicate ITS_Daemon_Health rows for daemon={self.daemon_name!r}; "
                    f"using first match {post_find['_row_id']}, manual cleanup "
                    f"needed for row {created_id}."
                ),
                error_code="daemon_health_race_duplicate",
            )
            row_id = int(post_find["_row_id"])
        self._persist_row_state(row_id, total_cycles=0)
        return row_id

    def write_row(
        self,
        *,
        status: HeartbeatStatus,
        items_processed: int,
        error_summary: str | None = None,
        correlation_id: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Write one row to ITS_Daemon_Health summarizing this cycle.

        ARCH-1/ARCH-2/ARCH-3 semantics: the Enabled column is filter metadata only; the row id is
        cached; total_cycles is lifetime-monotonic. Failures are internally caught + logged; the
        daemon's primary work is never blocked by a heartbeat-write failure.

        Pass ``error_summary=""`` (empty string) to CLEAR a prior cycle's error summary on a clean
        cycle — None leaves the cell untouched, "" overwrites it blank.
        """
        cols = sheet_ids.DAEMON_HEALTH_COLUMNS

        state = self._load_row_state()
        if state is None:
            try:
                row_id = self._resolve_row_id()
            except smartsheet_client.SmartsheetError as exc:
                self._log_failure(f"row-id lookup failed: {exc!r}")
                return
            if row_id is None:
                # A1: _resolve self-provisions a missing row, so a None here means the create
                # itself failed (already logged) — skip this cycle's write; next cycle retries.
                self._log_failure("row id unresolved after self-provision attempt — skipping write")
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
            # Bypass the breaker for this control-plane write so a CIRCUIT_OPEN status can still
            # land when Smartsheet is reachable — the already-once-per-cycle heartbeat write, no
            # new hammering.
            with circuit_breaker.bypass():
                smartsheet_client.update_row_cells_by_id(sheet_ids.SHEET_DAEMON_HEALTH, row_id, cells)
        except smartsheet_client.SmartsheetNotFoundError:
            self._invalidate_row_state()
            self._log_failure(f"row {row_id} not found — cache invalidated")
            return
        except smartsheet_client.SmartsheetError as exc:
            self._log_failure(f"SmartsheetError: {exc!r}")
            return
        except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
            self._log_failure(f"unexpected: {exc!r}")
            return

        self._persist_row_state(row_id, new_total)

    def _log_failure(self, detail: str) -> None:
        """Log a heartbeat-write failure to ITS_Errors with the standard error code."""
        error_log.log(
            Severity.WARN,
            self.script_name,
            f"heartbeat write for daemon={self.daemon_name!r} failed: {detail}",
            error_code="daemon_health_write_failed",
        )
