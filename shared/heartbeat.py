"""shared.heartbeat — ITS_Daemon_Health heartbeat reporter (consolidated).

§42 (why this module exists):

Every polling daemon writes a per-cycle row to the **ITS_Daemon_Health** sheet —
the canonical operator-visibility surface (Op Stds §3.1 + §32). The eight helpers
that implement this (`_write_heartbeat` liveness touch + the seven row-state /
find-or-create / update / failure-log helpers) were, until this module,
**replicated VERBATIM** across `safety_reports/portal_poll.py` and
`safety_reports/weekly_send_poll.py` (AST-logic-identical; only the docstrings had
drifted). That duplication was flagged tech-debt — every fix had to land in N
places, and a drift between copies is an operator-visibility hazard.

`HeartbeatReporter` consolidates the eight into one place. The **only** per-daemon
difference (the A1 self-provision registration metadata — daemon name, liveness
file, interval, source id) is now constructor config, not body text. Each daemon
keeps the two public seams (`_write_heartbeat`, `_write_heartbeat_row`) as thin
delegators because those are the symbols the test suites patch.

Invariants preserved byte-for-byte from the originals:

- **ARCH-1** — the ITS_Daemon_Health ``Enabled`` checkbox is report-filter metadata
  only; the runtime gate is ``<workstream>.<daemon>.polling_enabled`` in ITS_Config.
  A self-provisioned row registers ``Enabled=True`` (a live daemon must be visible).
- **ARCH-2** — the row-id cache (`{daemon_name: {row_id, total_cycles}}`) persists to
  a **shared** ``~/its/state/heartbeat_row_ids.json``, keyed by ``daemon_name``;
  the read-modify-write triple runs under ``state_io.with_path_lock`` (sidecar
  ``.lock``), fail-open on lock timeout (extra Smartsheet lookups are acceptable;
  a missed heartbeat is not).
- **ARCH-3** — ``Total Cycles`` is lifetime monotonic, never daily-reset.
- **Heartbeat-never-blocks** — every write path is broad-except-isolated and logs to
  ITS_Errors (``error_code='daemon_health_write_failed'``); a heartbeat failure
  never propagates into the daemon's primary work. Control-plane writes run under
  ``circuit_breaker.bypass()`` so a ``CIRCUIT_OPEN`` status can still land while
  Smartsheet is reachable.

Failure modes (all isolated — a heartbeat fault never reaches primary work):

- **Lock timeout** on ``heartbeat_row_ids.json`` → fail-open: log WARN and proceed
  (an extra Smartsheet find-or-create is acceptable; a missed heartbeat is not).
- **Row missing (SmartsheetNotFoundError)** on update → invalidate the cached row id
  and re-resolve via find-or-create on the next write.
- **SmartsheetError / unexpected exception** on any write → broad-except, log to
  ITS_Errors (``error_code='daemon_health_write_failed'``), skip this cycle's row
  write; the daemon's primary work continues.
- **Self-provision (row create) failure** → log WARN and continue without a row id;
  the next cycle retries the find-or-create.

Consumers:

- ``safety_reports.portal_poll`` (intake PULL daemon)
- ``safety_reports.weekly_send_poll`` (send-dispatch daemon)
- ``field_ops.fieldops_sync`` (P2.5 job-mirror up-sync daemon)
- ``progress_reports.progress_send_poll`` (P5 progress send-dispatch daemon)
- ``safety_reports.compile_now_poll`` (on-demand compile poller; R4-F1)
- ``safety_reports.publish_daemon`` (form-publish actuator; R4-F1)
- ``po_materials.po_poll`` (Purchase-Order pull daemon; PO S4)

Any new polling daemon should construct its own ``HeartbeatReporter`` (passing its
registration metadata + ``row_state_path=HEARTBEAT_ROW_STATE_PATH``) and add itself
to this Consumers list.

This is neither a send nor an AI capability — no Invariant-1 gating change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from shared import circuit_breaker, error_log, sheet_ids, smartsheet_client, state_io
from shared.error_log import Severity

# Shared ITS_Daemon_Health row-id cache — ONE JSON file across all daemons,
# keyed by daemon_name (ARCH-2). Default; a reporter may be pointed elsewhere.
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"

# Allowed cycle-status values written to ITS_Daemon_Health.Last_Cycle_Status.
# CIRCUIT_OPEN (F08) is set by a daemon when the Smartsheet circuit breaker is OPEN.
HeartbeatStatus = Literal["OK", "WARN", "ERROR", "DEGRADED", "SKIPPED", "CIRCUIT_OPEN"]


class HeartbeatReporter:
    """Per-daemon ITS_Daemon_Health reporter.

    Construct one module-level instance per daemon with that daemon's registration
    metadata; call `write_liveness()` and `write_row(...)` each cycle. All writes
    are side-effect-caught — they never block the daemon's primary work.
    """

    def __init__(
        self,
        *,
        script_name: str,
        daemon_name: str,
        workstream: str,
        liveness_path: Path,
        interval_seconds: int,
        source_id: str,
        row_state_path: Path = HEARTBEAT_ROW_STATE_PATH,
    ) -> None:
        self.script_name = script_name
        self.daemon_name = daemon_name
        self.workstream = workstream
        self.liveness_path = liveness_path
        self.interval_seconds = interval_seconds
        self.source_id = source_id
        self.row_state_path = row_state_path

    # ---- liveness file ---------------------------------------------------

    def write_liveness(self) -> None:
        """Overwrite the heartbeat file with the current UTC ISO timestamp."""
        state_io.atomic_write_text(self.liveness_path, datetime.now(UTC).isoformat())

    # ---- row-id state cache (ARCH-2) -------------------------------------

    def _load_row_state(self, daemon_name: str) -> dict[str, Any] | None:
        """Read `{daemon_name: {row_id, total_cycles}}` from the state file."""
        if not self.row_state_path.exists():
            return None
        try:
            raw = self.row_state_path.read_text()
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

    def _persist_row_state(
        self, daemon_name: str, row_id: int, total_cycles: int
    ) -> None:
        """Atomically merge `{daemon_name: {row_id, total_cycles}}` into the file.

        Shared-file lock contract: read-modify-write triple runs under
        `state_io.with_path_lock`. Lock-timeout fails open: log WARN + skip
        (next cycle re-tries).
        """
        self.row_state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with state_io.with_path_lock(self.row_state_path):
                current: dict[str, Any] = {}
                if self.row_state_path.exists():
                    try:
                        parsed = json.loads(self.row_state_path.read_text())
                        if isinstance(parsed, dict):
                            current = parsed
                    except (OSError, json.JSONDecodeError):
                        current = {}
                current[daemon_name] = {"row_id": row_id, "total_cycles": total_cycles}
                state_io.atomic_write_json(self.row_state_path, current)
        except state_io.StateLockTimeoutError:
            error_log.log(
                Severity.WARN,
                self.script_name,
                f"could not acquire lock on {self.row_state_path} after retries",
                error_code="daemon_health_write_failed",
            )

    def _invalidate_row_state(self, daemon_name: str) -> None:
        """Remove a daemon's entry from the state file (forces re-lookup).

        Same shared-file lock contract as `_persist_row_state`. Lock-timeout
        fails open: stale cache resurfaces and re-resolves on next cycle's 404.
        """
        if not self.row_state_path.exists():
            return
        try:
            with state_io.with_path_lock(self.row_state_path):
                try:
                    parsed = json.loads(self.row_state_path.read_text())
                except (OSError, json.JSONDecodeError):
                    return
                if not isinstance(parsed, dict):
                    return
                parsed.pop(daemon_name, None)
                state_io.atomic_write_json(self.row_state_path, parsed)
        except state_io.StateLockTimeoutError:
            error_log.log(
                Severity.WARN,
                self.script_name,
                f"could not acquire lock on {self.row_state_path} after retries (invalidate)",
                error_code="daemon_health_write_failed",
            )

    # ---- find-or-create the ITS_Daemon_Health row (A1) -------------------

    def _create_row(self, daemon_name: str) -> int | None:
        """Self-provision this daemon's ITS_Daemon_Health row (A1, find-or-create).

        Called by `_resolve_row_id` when no row exists for this daemon's primary
        key, so a newly-added daemon registers its own operator-visibility row
        instead of going dark. Writes the registration columns only; the per-cycle
        columns are filled by the `write_row` update that runs immediately after,
        in the same cycle. Deliberately omits a `last_cycle_status` seed so the
        create can't be rejected by a restrict-to-dropdown PICKLIST and so the
        first status the operator sees is a real one. Registers `Enabled=True`
        (ARCH-1): a self-provisioning daemon is by definition already running and
        the operator health report filters on `Enabled=true`.

        ID-keyed via `add_row_by_id` for column-rename stability. Heartbeat-never-
        blocks: any failure is logged (`daemon_health_write_failed`) and returns
        None — the caller skips this cycle's write and the next cycle retries. Runs
        under `circuit_breaker.bypass()` so an OPEN breaker can't stop a daemon from
        registering its visibility row.
        """
        cols = sheet_ids.DAEMON_HEALTH_COLUMNS
        payload: dict[int, Any] = {
            cols["daemon_name"]: daemon_name,
            cols["workstream"]: self.workstream,
            cols["enabled"]: True,
            cols["interval_seconds"]: self.interval_seconds,
            cols["source_id"]: self.source_id,
        }
        try:
            with circuit_breaker.bypass():
                return smartsheet_client.add_row_by_id(
                    sheet_ids.SHEET_DAEMON_HEALTH, payload
                )
        except smartsheet_client.SmartsheetError as exc:
            self._log_failure(daemon_name, f"self-provision create failed: {exc!r}")
            return None
        except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
            self._log_failure(daemon_name, f"self-provision unexpected: {exc!r}")
            return None

    def _resolve_row_id(self, daemon_name: str) -> int | None:
        """Return the ITS_Daemon_Health row id for `daemon_name`, find-or-create.

        Cache hit → cached id. Else `find_row_by_primary`; on a hit, cache and
        return. On a miss, self-provision the row (A1) so the daemon is never dark
        on the operator surface, with a week_folder-style post-create race re-find.
        Returns None only when the create itself failed (already logged) — the
        caller skips this cycle and retries next.
        """
        state = self._load_row_state(daemon_name)
        if state is not None:
            return state["row_id"]
        row = smartsheet_client.find_row_by_primary(
            sheet_ids.SHEET_DAEMON_HEALTH,
            sheet_ids.DAEMON_HEALTH_COLUMNS["daemon_name"],
            daemon_name,
        )
        if row is not None:
            row_id = int(row["_row_id"])
            self._persist_row_state(daemon_name, row_id, total_cycles=0)
            return row_id
        # A1 self-provision: no row for this daemon's primary key — create one so
        # the daemon registers its own visibility row instead of going dark.
        created_id = self._create_row(daemon_name)
        if created_id is None:
            return None
        # Race-safety re-find (mirror week_folder.py). The per-cycle fcntl lock +
        # launchd one-shot model serialize a single daemon's own cycles, so the
        # racer this guards is a manual operator/seeder hand-creating the row
        # between our create and this re-find (Smartsheet enforces no primary-key
        # uniqueness). Adopt the first match, WARN, leave the duplicate for
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
                self.script_name,
                (
                    f"Duplicate ITS_Daemon_Health rows for daemon={daemon_name!r}; "
                    f"using first match {post_find['_row_id']}, manual cleanup "
                    f"needed for row {created_id}."
                ),
                error_code="daemon_health_race_duplicate",
            )
            row_id = int(post_find["_row_id"])
        self._persist_row_state(daemon_name, row_id, total_cycles=0)
        return row_id

    # ---- per-cycle row update --------------------------------------------

    def write_row(
        self,
        *,
        status: HeartbeatStatus,
        items_processed: int,
        error_summary: str | None = None,
        correlation_id: str | None = None,
        notes: str | None = None,
        daemon_name: str | None = None,
    ) -> None:
        """Write one row to ITS_Daemon_Health summarizing this cycle.

        ARCH-1/ARCH-2/ARCH-3 semantics: Enabled column is filter metadata only;
        row-id cached; total_cycles is lifetime monotonic. Failures internally
        caught and logged; the daemon's primary work is never blocked by a
        heartbeat-write failure. `daemon_name` defaults to this reporter's daemon.
        """
        if daemon_name is None:
            daemon_name = self.daemon_name
        cols = sheet_ids.DAEMON_HEALTH_COLUMNS

        state = self._load_row_state(daemon_name)
        if state is None:
            try:
                row_id = self._resolve_row_id(daemon_name)
            except smartsheet_client.SmartsheetError as exc:
                self._log_failure(daemon_name, f"row-id lookup failed: {exc!r}")
                return
            if row_id is None:
                # A1: _resolve now self-provisions a missing row, so a None here
                # means the create itself failed (already logged with its own
                # detail) — skip this cycle's write; next cycle retries.
                self._log_failure(
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
            # F08: bypass the breaker for this control-plane write so a
            # CIRCUIT_OPEN status can still land when Smartsheet is reachable —
            # the already-once-per-cycle heartbeat write, no new hammering.
            with circuit_breaker.bypass():
                smartsheet_client.update_row_cells_by_id(
                    sheet_ids.SHEET_DAEMON_HEALTH, row_id, cells
                )
        except smartsheet_client.SmartsheetNotFoundError:
            self._invalidate_row_state(daemon_name)
            self._log_failure(
                daemon_name,
                f"row {row_id} not found — cache invalidated",
            )
            return
        except smartsheet_client.SmartsheetError as exc:
            self._log_failure(daemon_name, f"SmartsheetError: {exc!r}")
            return
        except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
            self._log_failure(daemon_name, f"unexpected: {exc!r}")
            return

        self._persist_row_state(daemon_name, row_id, new_total)

    # ---- failure logging -------------------------------------------------

    def _log_failure(self, daemon_name: str, detail: str) -> None:
        """Log a heartbeat-write failure to ITS_Errors with the standard code."""
        error_log.log(
            Severity.WARN,
            self.script_name,
            f"heartbeat write for daemon={daemon_name!r} failed: {detail}",
            error_code="daemon_health_write_failed",
        )
