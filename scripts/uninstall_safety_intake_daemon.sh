#!/usr/bin/env bash
#
# ITS — uninstall the Safety Reports polling daemon launchd agent.
#
# Idempotent: safe to run when the daemon is not installed (prints
# "not loaded" and exits 0). Leaves state files (~/its/state/) and log
# files (~/its/logs/launchd/) in place for forensic review; the
# operator removes them manually if needed.
set -euo pipefail

TARGET_DIR="${HOME}/Library/LaunchAgents"
UID_NUM="$(id -u)"

PLIST_NAME="org.solutionsmith.its.safety-intake.plist"
LABEL="org.solutionsmith.its.safety-intake"
TARGET_PATH="${TARGET_DIR}/${PLIST_NAME}"

if [[ -f "${TARGET_PATH}" ]]; then
    launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
    rm "${TARGET_PATH}"
    echo "uninstalled: ${LABEL}"
    echo "  removed plist: ${TARGET_PATH}"
    echo "  state + log files preserved under ~/its/state/ and ~/its/logs/launchd/"
else
    # Try bootout in case label exists but file is missing.
    if launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null; then
        echo "uninstalled: ${LABEL} (plist file was already missing)"
    else
        echo "not loaded: ${LABEL}"
    fi
fi
