"""Panels: Smartsheet circuit breaker, daemon liveness, and state locks.

All three read local state under ~/its/state. The lock panel uses a passive,
NON-MUTATING fcntl probe (open read-only, try a non-blocking lock, release
immediately) — it writes nothing to disk. Existence of a .lock sidecar is NOT
a held signal (state_io leaves the 0-byte file behind after every use), so a
flock probe is the only correct way to tell if a lock is held right now.
"""
from __future__ import annotations

import fcntl
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from operator_dashboard.config import STATE_DIR
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_INFO,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
    fmt_timedelta,
    worst_sev,
)


class CircuitBreakerSource(DataSource):
    panel_id = "circuit_breaker"
    title = "Smartsheet circuit breaker"

    def _fetch(self, detail: bool = False) -> PanelResult:
        cb: Any = importlib.import_module("shared.circuit_breaker")
        path: Path = cb.STATE_FILE
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                summary="no state file (CLOSED)",
                severity=SEV_OK,
                columns=["field", "value"],
                rows=[{"field": "state", "value": "CLOSED", "_sev": SEV_OK}],
            )
        state = str(data.get("state", "UNKNOWN"))
        try:
            secs = cb.seconds_open()
        except Exception:
            secs = None
        if state == "CLOSED":
            sev = SEV_OK
        elif state == "OPEN":
            sev = SEV_ERROR
        else:
            sev = SEV_WARN
        duration = "—" if secs is None else fmt_timedelta(timedelta(seconds=float(secs)))
        rows = [
            {"field": "state", "value": clean(state), "_sev": sev},
            {"field": "consecutive_failures", "value": clean(data.get("consecutive_failures")), "_sev": SEV_INFO},
            {"field": "opened_at", "value": clean(data.get("opened_at")), "_sev": SEV_INFO},
            {"field": "first_opened_at (episode)", "value": clean(data.get("first_opened_at")), "_sev": SEV_INFO},
            {"field": "open duration", "value": clean(duration), "_sev": sev},
        ]
        summary = state if state == "CLOSED" else f"{state} · {duration}"
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=summary,
            severity=sev,
            columns=["field", "value"],
            rows=rows,
        )


class HeartbeatsSource(DataSource):
    panel_id = "heartbeats"
    title = "Daemon status (local)"

    def _fetch(self, detail: bool = False) -> PanelResult:
        hb: Any = importlib.import_module("shared.heartbeat")
        state_dir: Path = hb.STATE_DIR
        row_state_path: Path = hb.HEARTBEAT_ROW_STATE_PATH

        cycles: dict[str, object] = {}
        try:
            cache = json.loads(row_state_path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            cache = {}
        if isinstance(cache, dict):
            for key, info in cache.items():
                if not (isinstance(info, dict) and "total_cycles" in info):
                    continue
                # Cache key is "<workstream>.<daemon>"; liveness files are
                # "<stem>_heartbeat.txt". The daemon segment and the file stem
                # are chosen independently per daemon and don't always agree by
                # a string rule (e.g. daemon 'weekly_send_poll' writes
                # 'weekly_send_heartbeat.txt'). Index cycles by the daemon
                # segment AND its '_poll'-stripped form so a liveness stem like
                # 'weekly_send' still resolves. A genuinely-divergent name
                # (e.g. retired 'safety_intake') falls back to '—'.
                cyc = info.get("total_cycles")
                daemon_part = str(key).split(".")[-1]
                cycles[daemon_part] = cyc
                if daemon_part.endswith("_poll"):
                    cycles.setdefault(daemon_part[: -len("_poll")], cyc)

        now = datetime.now(UTC)
        rows: list[dict[str, str]] = []
        files = sorted(state_dir.glob("*_heartbeat.txt"))
        for f in files:
            stem = f.name[: -len("_heartbeat.txt")]
            last, age = self._read_liveness(f, now)
            rows.append(
                {
                    "daemon": clean(stem),
                    "last heartbeat (UTC)": clean(last),
                    "age": clean(age),
                    "cycles": clean(cycles.get(stem, "—")),
                    "_sev": SEV_OK,
                }
            )
        # Freshness is judged by watchdog Check C (per-daemon windows), not
        # here — a bare liveness timestamp without the daemon's expected
        # interval can't be classified stale without false positives, so the
        # card stays neutral.
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{len(files)} daemons",
            severity=SEV_INFO,
            columns=["daemon", "last heartbeat (UTC)", "age", "cycles"],
            rows=rows,
        )

    def _read_liveness(self, path: Path, now: datetime) -> tuple[str, str]:
        try:
            raw = path.read_text().strip()
        except OSError:
            return "—", "—"
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError:
            return raw, "—"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.isoformat(), fmt_timedelta(now - ts)


class LocksSource(DataSource):
    panel_id = "locks"
    title = "State locks (fcntl probe)"

    def _probe(self, lock_path: Path) -> bool | None:
        # Open read-only; try a non-blocking exclusive lock. BlockingIOError
        # => held by a live process right now; success => release instantly
        # (LOCK_UN writes nothing). The dashboard runs in a separate process
        # from every daemon, so flock's self-referential exemption never
        # gives a false 'free'.
        try:
            fh = open(lock_path)
        except OSError:
            return None
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        except OSError:
            return None
        finally:
            fh.close()

    def _fetch(self, detail: bool = False) -> PanelResult:
        rows: list[dict[str, str]] = []
        worst = SEV_OK
        held_count = 0
        for lock_path in sorted(STATE_DIR.glob("*.lock")):
            held = self._probe(lock_path)
            if held is True:
                # A held lock is normal transiently (a daemon mid-write) —
                # amber, not red.
                state, sev = "HELD", SEV_WARN
                held_count += 1
            elif held is False:
                state, sev = "free", SEV_OK
            else:
                state, sev = "unknown", SEV_INFO
            worst = worst_sev(worst, sev)
            rows.append({"lock": clean(lock_path.name), "held": clean(state), "_sev": sev})
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{held_count} held / {len(rows)} locks",
            severity=worst if held_count else SEV_OK,
            columns=["lock", "held"],
            rows=rows,
        )
