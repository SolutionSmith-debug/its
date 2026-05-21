#!/usr/bin/env bash
#
# ITS — install the Safety Reports polling daemon as a launchd agent.
#
# What it does (idempotent):
#   1. Reads `safety_reports.intake.poll_interval_seconds` from ITS_Config
#      via Smartsheet API (token from macOS Keychain). Falls back to 60s
#      if the row is missing or unparseable.
#   2. Substitutes __ITS_HOME__ and __POLL_INTERVAL_SECONDS__ in the
#      bundled plist template and writes the resolved plist to
#      ~/Library/LaunchAgents/.
#   3. `launchctl bootout` (best-effort, in case a prior version is
#      loaded) then `launchctl bootstrap` to apply the new interval.
#   4. Verifies with `launchctl print` that the job is loaded and prints
#      a summary line + log paths for the operator.
#
# Re-running the installer is the supported way to change the poll
# interval: update the ITS_Config row, run this script, the new interval
# takes effect immediately (the running daemon does not hot-reload).
#
# Uninstall via `scripts/uninstall_safety_intake_daemon.sh`.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ITS_HOME="${HOME}/its"
TARGET_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${ITS_HOME}/logs/launchd"
STATE_DIR="${ITS_HOME}/state"
UID_NUM="$(id -u)"

PLIST_NAME="org.solutionsmith.its.safety-intake.plist"
LABEL="org.solutionsmith.its.safety-intake"
TEMPLATE_PATH="${SRC_DIR}/launchd/${PLIST_NAME}"
TARGET_PATH="${TARGET_DIR}/${PLIST_NAME}"

DEFAULT_INTERVAL=60

if [[ ! -f "${TEMPLATE_PATH}" ]]; then
    echo "error: plist template not found at ${TEMPLATE_PATH}" >&2
    exit 1
fi

# Read the poll interval from ITS_Config via a Python one-liner so we can
# reuse `shared.smartsheet_client` + `shared.keychain` (which the rest of
# ITS already trusts for credentials). Falls back to DEFAULT_INTERVAL on
# any error — the daemon still installs with a safe cadence even if
# Smartsheet is unreachable at install time.
read_interval() {
    "${ITS_HOME}/.venv/bin/python" <<'PYEOF' 2>/dev/null || echo ""
import sys
sys.path.insert(0, ".")
try:
    from shared import smartsheet_client
    raw = smartsheet_client.get_setting(
        "safety_reports.intake.poll_interval_seconds",
        workstream="safety_reports",
    )
    interval = int(str(raw).strip())
    if interval < 1:
        raise ValueError(f"interval too small: {interval}")
    print(interval)
except Exception as exc:
    print(f"# read_interval failed: {exc!r}", file=sys.stderr)
PYEOF
}

cd "${ITS_HOME}"
INTERVAL="$(read_interval || true)"
if ! [[ "${INTERVAL}" =~ ^[0-9]+$ ]]; then
    echo "warning: could not read poll_interval_seconds from ITS_Config; falling back to ${DEFAULT_INTERVAL}s" >&2
    INTERVAL="${DEFAULT_INTERVAL}"
fi

mkdir -p "${TARGET_DIR}" "${LOG_DIR}" "${STATE_DIR}"

# Substitute both placeholders. We do two seds rather than one chained
# expression so a failure on either substitution surfaces as a missing-
# placeholder marker in the resolved file (`plutil -lint` catches it
# below).
sed -e "s|__ITS_HOME__|${ITS_HOME}|g" \
    -e "s|__POLL_INTERVAL_SECONDS__|${INTERVAL}|g" \
    "${TEMPLATE_PATH}" > "${TARGET_PATH}"

# Validate before loading — plutil catches XML / typing errors early.
if ! plutil -lint "${TARGET_PATH}" >/dev/null; then
    echo "error: ${TARGET_PATH} failed plutil -lint; not loading" >&2
    rm -f "${TARGET_PATH}"
    exit 1
fi

# Unload first if already loaded; ignore errors.
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true

launchctl bootstrap "gui/${UID_NUM}" "${TARGET_PATH}"

# Verify the job is present in launchctl's listing.
if ! launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
    echo "error: ${LABEL} did not load successfully; see ${LOG_DIR}/safety_intake_poll.err.log" >&2
    exit 1
fi

echo "installed: ${LABEL}"
echo "  plist:             ${TARGET_PATH}"
echo "  interval:          ${INTERVAL}s"
echo "  stdout log:        ${LOG_DIR}/safety_intake_poll.out.log"
echo "  stderr log:        ${LOG_DIR}/safety_intake_poll.err.log"
echo "  heartbeat path:    ${STATE_DIR}/safety_intake_heartbeat.txt"
echo "  seen-set path:     ${STATE_DIR}/safety_intake_processed.json"
echo "  lock path:         ${STATE_DIR}/safety_intake.lock"
echo
echo "next poll cycle fires within ${INTERVAL}s. Tail the stderr log to confirm."
