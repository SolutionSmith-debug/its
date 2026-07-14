"""Panel: launchd daemon status via `launchctl list` (read-only)."""
from __future__ import annotations

import subprocess

from operator_dashboard.config import LAUNCHCTL_TIMEOUT_SECONDS, LAUNCHD_DIR
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
    worst_sev,
)

LABEL_PREFIX = "org.solutionsmith.its."


class DaemonStatusSource(DataSource):
    panel_id = "daemons"
    title = "launchd daemons"

    def _plist_labels(self) -> list[str]:
        # The plist filenames ARE the job labels (verified). Glob the live
        # launchd dir so the panel tracks new daemons without a code change;
        # the generic template.plist has no org.solutionsmith.its.* stem so
        # the glob naturally excludes it.
        return [p.stem for p in LAUNCHD_DIR.glob("org.solutionsmith.its.*.plist")]

    def _launchctl_table(self) -> dict[str, tuple[str, str]]:
        # Read-only: fixed argv, no shell, bounded timeout. Output is
        # PID<TAB>Status<TAB>Label; PID is a decimal when running or '-' when
        # loaded-but-idle; Status is the last exit code (0 = clean).
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=LAUNCHCTL_TIMEOUT_SECONDS,
            check=False,
        )
        table: dict[str, tuple[str, str]] = {}
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            pid, status, label = parts
            if label.startswith(LABEL_PREFIX):
                table[label] = (pid, status)
        return table

    def _fetch(self, detail: bool = False) -> PanelResult:
        live = self._launchctl_table()
        labels = sorted(set(self._plist_labels()) | set(live))
        rows: list[dict[str, str]] = []
        worst = SEV_OK
        for label in labels:
            short = label[len(LABEL_PREFIX):] if label.startswith(LABEL_PREFIX) else label
            if label not in live:
                pid, status, state, sev = "—", "—", "NOT LOADED", SEV_WARN
            else:
                pid, status = live[label]
                if status != "0":
                    state, sev = f"exit {status}", SEV_ERROR
                elif pid not in ("-", ""):
                    state, sev = "running", SEV_OK
                else:
                    state, sev = "idle", SEV_OK
            worst = worst_sev(worst, sev)
            rows.append(
                {
                    "daemon": clean(short),
                    "pid": clean(pid),
                    "last exit": clean(status),
                    "state": clean(state),
                    "_sev": sev,
                }
            )
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{len(live)}/{len(labels)} loaded",
            severity=worst,
            columns=["daemon", "pid", "last exit", "state"],
            rows=rows,
        )
