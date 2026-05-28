#!/bin/bash
# Block CodeQL alert dismissal at the Bash PreToolUse layer.
#
# The codeql-fp-triager subagent is PROPOSE-ONLY: it surfaces candidate
# false positives with quoted evidence; a human applies the dismissal.
# This hook is the structural backstop so a misclassification can never
# silently dismiss a real alert. Listing and reading alerts (GET) is
# allowed; any code-scanning dismissal command is refused.
#
# Wired via the codeql-fp-triager agent frontmatter (hooks.PreToolUse,
# matcher: Bash). Mirrors the §38 git-guardrails precedent
# (.claude/hooks/block-dangerous-git.sh). Scoped to that one subagent —
# the operator's own session can still dismiss manually.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Block only a code-scanning DISMISSAL: must touch code-scanning AND
# carry a dismiss intent. GET list/read calls have neither and pass.
if echo "$COMMAND" | grep -qE "code-scanning" && echo "$COMMAND" | grep -qiE "dismiss"; then
  echo "BLOCKED: '$COMMAND' attempts a CodeQL alert dismissal. The codeql-fp-triager is propose-only — it surfaces candidate FPs with evidence; the operator applies dismissals manually. See .claude/agents/codeql-fp-triager.md." >&2
  exit 2
fi

exit 0
