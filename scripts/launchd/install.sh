#!/usr/bin/env bash
#
# ITS launchd helper — load, unload, and inspect ITS LaunchAgents.
#
# Substitutes __ITS_HOME__ → "$HOME/its" when copying the plist into
# ~/Library/LaunchAgents/. The plist files in this directory stay
# generic; the installed copies have concrete paths.
#
# Usage:
#   ./install.sh load    <plist>     # substitute, copy, bootstrap
#   ./install.sh unload  <plist>     # bootout, remove from LaunchAgents
#   ./install.sh status  [<plist>]   # show ITS jobs (all if no arg)
#   ./install.sh dry-run <plist>     # print resolved plist to stdout
#
# <plist> can be the filename (with or without .plist) or the label.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ITS_HOME="${HOME}/its"
TARGET_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${ITS_HOME}/logs/launchd"
UID_NUM="$(id -u)"

usage() {
    cat <<EOF
usage: $0 {load|unload|status|dry-run} [plist]

  load     <plist>   substitute __ITS_HOME__ and bootstrap into launchd
  unload   <plist>   bootout and remove from ~/Library/LaunchAgents/
  status   [plist]   list loaded ITS jobs (or one if specified)
  dry-run  <plist>   print the resolved plist content to stdout
EOF
    exit 1
}

resolve_plist_name() {
    local name="$1"
    # Strip a trailing .plist if present, then add it back — normalizes input.
    name="${name%.plist}.plist"
    echo "$name"
}

cmd_load() {
    local plist; plist="$(resolve_plist_name "$1")"
    local src="${SRC_DIR}/${plist}"
    local dst="${TARGET_DIR}/${plist}"
    local label="${plist%.plist}"

    [[ -f "$src" ]] || { echo "error: $src not found" >&2; exit 1; }

    mkdir -p "$TARGET_DIR" "$LOG_DIR"

    # Substitute __ITS_HOME__ with the real $HOME/its.
    sed "s|__ITS_HOME__|${ITS_HOME}|g" "$src" > "$dst"

    # Validate before loading — plutil catches XML/typing errors early.
    if ! plutil -lint "$dst" >/dev/null; then
        echo "error: $dst failed plutil -lint; not loading" >&2
        rm -f "$dst"
        exit 1
    fi

    # Unload first if already loaded; ignore errors.
    launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true

    launchctl bootstrap "gui/${UID_NUM}" "$dst"
    echo "loaded: ${label}"
    echo "  plist:  $dst"
    echo "  stdout: ${LOG_DIR}/${label##*.}.out.log"
    echo "  stderr: ${LOG_DIR}/${label##*.}.err.log"
}

cmd_unload() {
    local plist; plist="$(resolve_plist_name "$1")"
    local dst="${TARGET_DIR}/${plist}"
    local label="${plist%.plist}"

    if [[ -f "$dst" ]]; then
        launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
        rm "$dst"
        echo "unloaded: ${label}"
    else
        # Try bootout in case label exists but file is missing.
        launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null && \
            echo "unloaded: ${label} (file was missing)" || \
            echo "not loaded: ${label}"
    fi
}

cmd_status() {
    if [[ $# -gt 0 ]]; then
        local plist; plist="$(resolve_plist_name "$1")"
        local label="${plist%.plist}"
        if launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1; then
            launchctl print "gui/${UID_NUM}/${label}" | \
                grep -E "^\s+(state|pid|last exit code|path|program)" | \
                sed 's/^[[:space:]]*//'
        else
            echo "not loaded: ${label}"
        fi
    else
        local out
        out="$(launchctl list 2>/dev/null | grep -E "\sorg\.solutionsmith\.its\." || true)"
        if [[ -z "$out" ]]; then
            echo "no ITS jobs loaded"
        else
            printf '%-8s %-8s %s\n' "PID" "EXIT" "LABEL"
            echo "$out"
        fi
    fi
}

cmd_dry_run() {
    local plist; plist="$(resolve_plist_name "$1")"
    local src="${SRC_DIR}/${plist}"
    [[ -f "$src" ]] || { echo "error: $src not found" >&2; exit 1; }
    sed "s|__ITS_HOME__|${ITS_HOME}|g" "$src"
}

main() {
    local cmd="${1:-}"
    case "$cmd" in
        load)    [[ $# -ge 2 ]] || usage; cmd_load    "$2" ;;
        unload)  [[ $# -ge 2 ]] || usage; cmd_unload  "$2" ;;
        status)  shift; cmd_status "$@" ;;
        dry-run) [[ $# -ge 2 ]] || usage; cmd_dry_run "$2" ;;
        *) usage ;;
    esac
}

main "$@"
