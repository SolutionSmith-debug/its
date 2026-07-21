"""Panel: watchdog scheduled-job markers (a read-only mirror of Check C).

The real watchdog module owns the tracked-job list, per-job freshness
windows, and the marker directory. We import it lazily and read those
constants so this panel can never drift from what the watchdog actually
checks. A failed import degrades only this panel (fail-soft base wrapper).
"""
from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from operator_dashboard.config import ITS_HOME
from operator_dashboard.sources.base import (
    SEV_INFO,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
    fmt_timedelta,
    worst_sev,
)
from operator_dashboard.system_map import NODE_BY_MARKER


class WatchdogChecksSource(DataSource):
    panel_id = "watchdog"
    title = "Watchdog markers (Check C)"

    def _fetch(self, detail: bool = False) -> PanelResult:
        # `scripts/` is not a Python package (no __init__; absent from the
        # editable-install package list), so `import scripts.watchdog` resolves
        # only when a tree root is on sys.path. Pin it to the OBSERVATION root
        # (~/its) at sys.path[0] — CWD-independent, and always the LIVE tree's
        # tracked-jobs/windows/marker-dir, not whatever tree happens to be on
        # the path. A failed import still degrades only this panel (base wrapper).
        its_home = str(ITS_HOME)
        if its_home not in sys.path:
            sys.path.insert(0, its_home)
        wd: Any = importlib.import_module("scripts.watchdog")
        marker_dir: Path = wd.WATCHDOG_MARKER_DIR
        tracked: list[str] = list(wd.TRACKED_JOBS)
        windows: dict[str, timedelta] = dict(wd.TRACKED_JOB_WINDOWS)
        default_window: timedelta = wd.DEFAULT_TRACKED_JOB_WINDOW

        now = datetime.now(UTC)
        rows: list[dict[str, str]] = []
        worst = SEV_OK
        stale = 0
        for job in tracked:
            window = windows.get(job, default_window)
            last_run, age, sev, state = self._read_marker(
                marker_dir / f"{job}.last_run", now, window
            )
            if sev != SEV_OK:
                stale += 1
            worst = worst_sev(worst, sev)
            row = {
                "job": clean(job),
                "last run (UTC)": clean(last_run),
                "age": clean(age),
                "window": clean(fmt_timedelta(window)),
                "state": clean(state),
                "_sev": sev,
            }
            # Deep-link the marker to its system-map node, like the launchd and
            # heartbeat panels do. A stale marker is the first thing an operator
            # sees; the node rail is where the runbook + gate live.
            node_id = NODE_BY_MARKER.get(job)
            if node_id:
                row["_link_job"] = f"/system?focus={node_id}"
            rows.append(row)
        # The watchdog's own marker is intentionally NOT tracked (a daemon
        # can't detect its own death) — surface it as informational only.
        self_run, self_age, _, self_state = self._read_marker(
            marker_dir / "watchdog.last_run", now, default_window
        )
        rows.append(
            {
                "job": clean("watchdog (self)"),
                "last run (UTC)": clean(self_run),
                "age": clean(self_age),
                "window": clean("—"),
                "state": clean("ran" if self_state in ("fresh", "stale") else self_state),
                "_sev": SEV_INFO,
            }
        )
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{stale} stale / {len(tracked)} tracked",
            severity=worst,
            columns=["job", "last run (UTC)", "age", "window", "state"],
            rows=rows,
        )

    def _read_marker(
        self, marker: Path, now: datetime, window: timedelta
    ) -> tuple[str, str, str, str]:
        try:
            raw = marker.read_text().strip()
        except FileNotFoundError:
            return "—", "—", SEV_WARN, "missing"
        except OSError:
            return "—", "—", SEV_WARN, "unreadable"
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError:
            return raw, "—", SEV_WARN, "unparseable"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age = now - ts
        if age > window:
            return ts.isoformat(), fmt_timedelta(age), SEV_WARN, "stale"
        return ts.isoformat(), fmt_timedelta(age), SEV_OK, "fresh"
