#!/bin/bash
# Block a Cloudflare deploy / remote-D1 migration issued from a ~/its checkout that
# is ON main and BEHIND origin/main (PreToolUse Bash layer).
#
# Forensic class #2 (deploy/D1 footguns). Deploying — or running `d1 migrations
# list/apply` — from a stale main caused the 2026-06-28 universal portal lockout: a
# 25-commit-behind ~/its reported "No migrations to apply" while the deployed Worker
# expected the newer tables → resolveCapabilities fail-closed → every account locked
# out. Precise scope (only when ~/its is on main AND behind, no network fetch) keeps
# false positives near zero. The fix/override is `git -C ~/its pull origin main`
# (or run the deploy manually outside Claude Code if intentional).
#
# CC-session-scoped: launchd daemons run outside a CC session, so this never affects
# them. The load-bearing companion is the custom_domain footgun note in
# safety_portal/wrangler.jsonc.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Only the live-Worker-affecting commands.
case "$COMMAND" in
  *"wrangler deploy"*|*"npm run deploy"*|*"wrangler d1 migrations apply"*|*"wrangler d1 migrations list"*|*"wrangler versions deploy"*) ;;
  *) exit 0 ;;
esac

ITS="$HOME/its"
branch=$(git -C "$ITS" branch --show-current 2>/dev/null)
[ "$branch" = "main" ] || exit 0   # only guard the canonical live tree on main

behind=$(git -C "$ITS" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [ "${behind:-0}" -gt 0 ]; then
  echo "BLOCKED: $ITS is on main but ${behind} commit(s) behind origin/main. A Cloudflare deploy / D1 migration from a stale main caused the 2026-06-28 universal portal lockout. Run 'git -C $ITS pull origin main' first (or run the deploy manually outside Claude Code if this is intentional). Forensic class #2." >&2
  exit 2
fi
exit 0
