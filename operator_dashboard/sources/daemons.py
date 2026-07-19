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
                if pid not in ("-", ""):
                    # A live pid = healthy NOW. `status` is the PREVIOUS instance's exit
                    # and is informational (shown in "last exit") — a graceful restart /
                    # reboot leaves a signal exit like -15 (SIGTERM), which is NOT a current
                    # fault. KeepAlive servers (the dashboard) always carry a prior -15 after
                    # any restart. Running-first: a running daemon is never ERROR on last-exit.
                    state, sev = "running", SEV_OK
                elif status != "0":
                    # Loaded but NOT running AND the last run exited non-zero — it exited /
                    # crashed and did not restart. (An interval/calendar daemon shows this
                    # between fires until its next clean run; picklist-audit's exit 1 is its
                    # by-design "drift found" signal, cleared on the next clean audit.)
                    state, sev = f"exited {status}", SEV_ERROR
                else:
                    state, sev = "idle", SEV_OK
            worst = worst_sev(worst, sev)
            row = {
                "daemon": clean(short),
                "pid": clean(pid),
                "last exit": clean(status),
                "state": clean(state),
                "_sev": sev,
            }
            # Deep link into the system map when this label has a node there.
            from operator_dashboard.system_map import NODE_BY_LAUNCHD_LABEL

            node_id = NODE_BY_LAUNCHD_LABEL.get(label)
            if node_id:
                row["_link_daemon"] = f"/system?focus={node_id}"
            rows.append(row)
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{len(live)}/{len(labels)} loaded",
            severity=worst,
            columns=["daemon", "pid", "last exit", "state"],
            rows=rows,
        )
