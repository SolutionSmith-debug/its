#!/bin/bash
# Block writes to version-gated doctrine at the Edit|Write PreToolUse layer.
#
# The session-close-maintainer subagent edits living docs (info-gap,
# memory-archive, tech-debt) directly, but `doctrine/` is version-gated
# and requires explicit operator approval. The agent's prompt already
# says "ask once before touching doctrine" — this hook is the structural
# backstop so a misfire can't silently rewrite an invariant.
#
# Wired via the session-close-maintainer agent frontmatter
# (hooks.PreToolUse, matcher: Edit|Write). Mirrors the §38 git-guardrails
# precedent. Matches any path under a doctrine/ directory in either repo.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path')

if echo "$FILE_PATH" | grep -qE "/doctrine/"; then
  echo "BLOCKED: write to '$FILE_PATH' targets version-gated doctrine. The session-close-maintainer must not edit doctrine/ without explicit operator approval — surface the proposed change as a diff for the operator instead. See .claude/agents/session-close-maintainer.md." >&2
  exit 2
fi

exit 0
