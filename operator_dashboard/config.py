"""Static configuration + observation roots for the operator dashboard.

The dashboard OBSERVES the live ITS daemon tree at ~/its. Every runtime
artifact it reads (watchdog markers, state JSON, logs, launchd plists) lives
under that tree, so these roots point at ~/its regardless of where the
dashboard code itself is checked out or deployed.

STATE_DIR / LOGS_DIR mirror the constants owned by the shared modules
(verified identical in shared.heartbeat.STATE_DIR and shared.error_log.LOG_DIR);
tests/test_operator_dashboard.py asserts that parity so a future move of those
constants fails loudly instead of silently reading the wrong tree. The
logic-bearing surfaces (watchdog tracked-jobs + windows, circuit_breaker
STATE_FILE, heartbeat paths) are imported live from their owning module at
read time rather than mirrored, so they can never drift.
"""
from __future__ import annotations

from pathlib import Path

HOST = "127.0.0.1"
PORT = 8484

ITS_HOME = Path.home() / "its"
STATE_DIR = ITS_HOME / "state"
LOGS_DIR = ITS_HOME / "logs"
LAUNCHD_DIR = ITS_HOME / "scripts" / "launchd"

# htmx polls each panel independently on this cadence (seconds).
PANEL_REFRESH_SECONDS = 15

# Local-file panels are cheap and read fresh every request. Only the
# Smartsheet-backed panels are TTL-cached — a full-sheet fetch is costly.
SMARTSHEET_TTL_SECONDS = 120

# `launchctl list` subprocess read timeout (seconds).
LAUNCHCTL_TIMEOUT_SECONDS = 5
