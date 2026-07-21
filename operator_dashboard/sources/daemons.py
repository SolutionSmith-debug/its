"""Panel: launchd daemon status via `launchctl list` (read-only)."""
from __future__ import annotations

import re
import signal
import subprocess
from datetime import timedelta

from operator_dashboard.config import LAUNCHCTL_TIMEOUT_SECONDS, LAUNCHD_DIR
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
    fmt_timedelta,
    worst_sev,
)

LABEL_PREFIX = "org.solutionsmith.its."

# `ps -o etime=` renders [[dd-]hh:]mm:ss.
_ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def _signal_name(num: int) -> str:
    try:
        return signal.Signals(num).name
    except ValueError:
        # A signal number this Python doesn't know (or a non-signal negative
        # status) still gets a label rather than an exception.
        return str(num)


def _last_exit_display(status: str, running: bool) -> tuple[str, str]:
    """Return (cell text, tooltip) for the 'last exit' column.

    A RUNNING daemon's last-exit describes the PREVIOUS instance, so a raw
    negative value ("-15") reads as an alarm when it is only "the prior run was
    signalled". Relabel it neutrally — the label deliberately does NOT claim the
    signal was a deliberate restart, since an external or accidental SIGTERM is
    indistinguishable here — and keep the raw number in the tooltip. A positive
    exit code is a real prior failure and stays raw.
    """
    if not running:
        return status, ""
    try:
        code = int(status)
    except ValueError:
        return status, ""
    if code >= 0:
        return status, ""
    name = _signal_name(-code)
    return (
        f"signal ({name})",
        f"raw launchctl last-exit {code} (previous instance, killed by {name})",
    )


def _parse_etime(etime: str) -> timedelta | None:
    m = _ETIME_RE.match(etime.strip())
    if not m:
        return None
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    return timedelta(days=days, hours=hours, minutes=mins, seconds=secs)


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

    def _uptime_by_pid(self, pids: list[str]) -> dict[str, str]:
        # ONE batched call for every running pid — this panel is htmx-polled every
        # few seconds, so a per-row subprocess would be a fork storm. Read-only:
        # fixed argv, no shell, bounded timeout. `ps -p` with an empty list is an
        # error on Darwin, so skip the call entirely when nothing is running.
        if not pids:
            return {}
        # The guard covers ONLY the subprocess boundary (`ps` missing, timing
        # out, or writing non-UTF8 bytes). Uptime is decoration, so a broken
        # `ps` must never cost the panel its real content — but a programming
        # bug in the parse loop below must NOT be swallowed into a permanent
        # silent "—" on a panel whose whole job is "never silent";
        # DataSource.fetch() already fails the panel soft AND visibly for that.
        try:
            proc = subprocess.run(
                ["ps", "-o", "pid=,etime=", "-p", ",".join(pids)],
                capture_output=True,
                text=True,
                timeout=LAUNCHCTL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            return {}
        out: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            pid, etime = parts
            td = _parse_etime(etime)
            out[pid] = fmt_timedelta(td) if td is not None else etime
        return out

    def _fetch(self, detail: bool = False) -> PanelResult:
        live = self._launchctl_table()
        uptimes = self._uptime_by_pid(
            [pid for pid, _ in live.values() if pid not in ("-", "")]
        )
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
            exit_text, exit_title = _last_exit_display(status, state == "running")
            row = {
                "daemon": clean(short),
                "pid": clean(pid),
                "uptime": clean(uptimes.get(pid, "—")),
                "last exit": clean(exit_text),
                "state": clean(state),
                "_sev": sev,
            }
            if exit_title:
                row["_title_last exit"] = clean(exit_title)
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
            columns=["daemon", "pid", "uptime", "last exit", "state"],
            rows=rows,
        )
