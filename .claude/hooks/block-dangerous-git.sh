#!/bin/bash
# Block dangerous git commands at the Bash PreToolUse layer.
#
# ITS-customized version of mattpocock/skills git-guardrails-claude-code.
# Plain `git push <branch>` is ALLOWED (canonical PR workflow).
# `git branch -d` (safe delete) is ALLOWED (canonical post-merge cleanup).
# Force-push, delete-via-push, force-branch-delete, and destructive locals
# are BLOCKED. See CLAUDE.md "Skills usage" section for the carve-out rationale.
#
# Direct-push-to-main is intentionally NOT enforced here — that defense
# lives at the GitHub branch protection layer (server-side, authoritative).

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

DANGEROUS_PATTERNS=(
  "git push.*--force"
  "git push.* -f($|[[:space:]])"
  "git push.*--delete"
  "git push.* -d[[:space:]]"
  "git push[[:space:]]+[^[:space:]:]+[[:space:]]+:[^[:space:]:]+"
  "git reset --hard"
  "git clean -f"
  "git branch -D"
  "git checkout \\."
  "git restore \\."
)

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qE "$pattern"; then
    echo "BLOCKED: '$COMMAND' matches dangerous pattern '$pattern'. ITS guardrails block this command — operator must run it manually. See CLAUDE.md Skills usage section for carve-out rationale." >&2
    exit 2
  fi
done

exit 0
