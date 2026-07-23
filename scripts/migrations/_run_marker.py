"""standup-in-progress marker — the wipe/stand-up half of the dashboard ACT fence.

The daemon-down guards in wipe_tenant.py and standup.py EXEMPT the operator
dashboard (`DASHBOARD_LABEL`) so its read-only panels stay observable over
Tailscale mid-run. That exemption is conditionally safe (2026-07-23 runtime
review): the dashboard's only tenant writes are 5 PIN-gated ACT verbs on
import-time-frozen sheet ids, and the hazard is a mid-run RESTART (KeepAlive /
DASH-12) re-importing HALF-FLIPPED constants and writing into rebuilt sheets
mid-seed. The fence: both tools write this marker for the duration of a
mutating run; the dashboard's ACT verbs + DASH-12 check it and 503 while a
FRESH marker exists (dashboard side tracked in its#677 — until that lands,
the safer manual posture is unloading the dashboard too).

Contract (keep in lock-step with its#677):
    path     ~/its/state/standup_in_progress
    shape    {"tool": "wipe_tenant"|"standup", "run_id": str,
              "started_utc": iso8601, "pid": int}
    removal  in a `finally` — a CRASHED run can leave it behind, so the
             dashboard fence must fail OPEN past a max-age (24h suggested);
             `rm` of a stale marker is always safe (display/fence data only).

Marker writes are best-effort: the fence is advisory defense-in-depth, and a
marker-write failure must never block the run it protects.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
import sys
from collections.abc import Iterator

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared import state_io  # noqa: E402

MARKER_PATH = pathlib.Path.home() / "its" / "state" / "standup_in_progress"

# The one launchd job the wipe/standup daemon-down guards EXEMPT (read-only
# panels + KeepAlive; see module docstring + its#677 for why that is safe
# only alongside this marker).
DASHBOARD_LABEL = "org.solutionsmith.its.dashboard"


@contextlib.contextmanager
def run_marker(tool: str, run_id: str) -> Iterator[None]:
    """Write the standup-in-progress marker for the duration of a mutating run."""
    try:
        state_io.atomic_write_json(MARKER_PATH, {
            "tool": tool,
            "run_id": run_id,
            "started_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "pid": os.getpid(),
        })
        print(f"[info] standup-in-progress marker written: {MARKER_PATH}")
    except OSError as exc:
        print(f"[WARN] marker_write_failed: {exc} — continuing (the marker is "
              "advisory defense-in-depth, never a run blocker).")
    try:
        yield
    finally:
        try:
            MARKER_PATH.unlink(missing_ok=True)
            print(f"[info] standup-in-progress marker removed: {MARKER_PATH}")
        except OSError as exc:
            print(f"[WARN] marker_remove_failed: {exc} — remove it by hand "
                  f"(`rm {MARKER_PATH}`); the dashboard fence fails OPEN past "
                  "its max-age either way.")
