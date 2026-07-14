#!/usr/bin/env bash
#
# ITS Operator Dashboard — Tailscale exposure helper (WS2 D1-3b).
#
# The dashboard binds 127.0.0.1:8484 (localhost only). To reach it from your other
# tailnet devices, `tailscale serve` proxies https://<this-host>.<tailnet>.ts.net
# (HTTPS 443) → localhost:8484. The dashboard's Origin allowlist
# (ITS_DASH_ALLOWED_ORIGINS) MUST include that exact origin or the browser's ACT
# POST is refused as cross-origin (localhost is always allowed).
#
# This script PRINTS the exact commands + the origin value to set — it exposes
# NOTHING by itself. Pass --apply to ALSO run `tailscale serve`.
#
# Usage:
#   ./tailscale_serve.sh          # print the serve command + origin + plist patch
#   ./tailscale_serve.sh --apply  # ALSO run `tailscale serve --bg 8484`
#
set -euo pipefail

PORT=8484
PLIST="${HOME}/Library/LaunchAgents/org.solutionsmith.its.dashboard.plist"

# Locate the tailscale CLI (Homebrew, or the Mac App Store app bundle).
TS="$(command -v tailscale || true)"
[[ -x "$TS" ]] || TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
[[ -x "$TS" ]] || { echo "error: tailscale CLI not found — install Tailscale first" >&2; exit 1; }

# Read this host's tailnet FQDN (trailing dot stripped).
dns="$("$TS" status --json 2>/dev/null \
  | /usr/bin/python3 -c 'import sys, json; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))' \
  || true)"
[[ -n "$dns" ]] || { echo "error: could not read this host's tailnet DNSName — is Tailscale up? (run: tailscale up)" >&2; exit 1; }

origin="https://${dns}"

cat <<EOF
Tailnet host : ${dns}
Served origin: ${origin}

1) Expose the dashboard over Tailscale (HTTPS 443 → localhost:${PORT}):
     tailscale serve --bg ${PORT}

2) Allow that origin — the dashboard refuses cross-origin ACT POSTs otherwise. A launchd
   service does NOT inherit your shell env, so set it in the INSTALLED plist, then reload:
     /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:ITS_DASH_ALLOWED_ORIGINS ${origin}" "${PLIST}"
     scripts/launchd/install.sh load org.solutionsmith.its.dashboard

   (For a manual \`python -m operator_dashboard\` run instead, just export it:
      export ITS_DASH_ALLOWED_ORIGINS="${origin}")
EOF

if [[ "${1:-}" == "--apply" ]]; then
    echo
    echo "--apply: running \`tailscale serve --bg ${PORT}\` ..."
    "$TS" serve --bg "${PORT}"
    echo "done. Now set ITS_DASH_ALLOWED_ORIGINS (step 2) and reload the dashboard."
fi
