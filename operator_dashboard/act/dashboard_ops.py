"""Class-B restart-dashboard verb (DASH-12): restart the dashboard's OWN daemon.

This verb DELIBERATELY crosses the "a service must not stop itself via its own
UI" self-exclusion that `daemon_ops.controllable_labels()` enforces — the
operator pre-authorized this one narrowly-scoped exception (tech-debt DASH-12)
because dashboard code changes land on `origin/main` but the live KeepAlive
process keeps serving the old code until someone runs `launchctl kickstart -k`
from a terminal. Restart-ONLY: this verb never pulls, never deploys, never
touches any other label. It is a NEW verb on the dashboard's own fixed label —
`daemon_ops`' allowlist still excludes the dashboard, unchanged.

The classic self-restart footgun: a naive `subprocess.run("launchctl kickstart
-k …")` kills THIS process before the HTTP response flushes, and a child in the
same session/process-group can die with its parent's SIGTERM. So the spawn is
fully DETACHED — `/bin/sh -c 'sleep 1; exec launchctl kickstart -k …'` with
`start_new_session=True` and closed stdio — and the audit row is written
BEFORE the spawn (the durable record must exist even though this process is
about to die). launchd's KeepAlive brings the dashboard back with fresh code;
`/healthz` confirms boot integrity.

§43: symptom + Tier-2 repair in docs/runbooks/operator_dashboard_config_editor.md.
"""
from __future__ import annotations

import importlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DASHBOARD_LABEL = "org.solutionsmith.its.dashboard"
# Long enough for the outcome partial to flush to the browser; short enough
# that the restart feels immediate.
RESTART_DELAY_SECONDS = 1


@dataclass
class RestartOutcome:
    kind: str  # ok | error (also CSS status class + test assertion)
    message: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def restart_dashboard(operator: str) -> RestartOutcome:
    """Schedule a detached self-restart. The elevated-confirm ceremony is
    verified by the router before this runs."""
    # Audit FIRST: this process is about to be SIGTERM'd, so the durable record
    # cannot wait for a post-restart hook.
    _audit(operator)
    cmd = (
        f"sleep {RESTART_DELAY_SECONDS}; "
        f"exec launchctl kickstart -k gui/{os.getuid()}/{DASHBOARD_LABEL}"
    )
    try:
        subprocess.Popen(
            ["/bin/sh", "-c", cmd],
            start_new_session=True,  # survives this process's SIGTERM
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as exc:
        return RestartOutcome(
            "error", f"could not spawn the detached restart: {type(exc).__name__}: {exc}"
        )
    return RestartOutcome(
        "ok",
        f"restart scheduled — the dashboard drops in ~{RESTART_DELAY_SECONDS}s and launchd "
        "KeepAlive brings it back on the current ~/its code. Reload this page in a few "
        "seconds; /healthz confirms the boot.",
    )


def _audit(operator: str) -> None:
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.dashboard_restart",
            f"dashboard self-restart requested by {operator} (elevated-confirm; detached "
            f"kickstart -k {DASHBOARD_LABEL}) at {ts}",
            error_code="dashboard_restart_requested",
            alert=False,
        )
    except Exception:
        pass
