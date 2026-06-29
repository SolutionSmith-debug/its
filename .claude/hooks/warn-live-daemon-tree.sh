#!/bin/bash
# SessionStart — advisory topology notice (forensic class #5: worktree-on-live-tree).
#
# The live launchd daemons execute the ~/its working tree from disk every ~60s, so any
# uncommitted Python-source edit there goes live within a cycle, and committing in ~/its
# mid-cycle can strand the publish daemon on a feature branch. If this session is rooted
# at the live ~/its tree, surface a reminder to use a per-task worktree (+ its own venv)
# for Python-source edits. Advisory ONLY — SessionStart cannot block; this prints context
# and always exits 0 (the doc worktree_discipline.md reserved exactly this lightweight,
# low-false-positive surface).

cwd="$(pwd -P 2>/dev/null)"
its="$(cd "$HOME/its" 2>/dev/null && pwd -P)"
[ -n "$its" ] && [ "$cwd" = "$its" ] || exit 0

branch=$(git -C "$its" branch --show-current 2>/dev/null)
cat <<EOF
NOTE (ITS topology): this session is rooted at the LIVE daemon tree $its (branch: ${branch:-?}).
The launchd daemons run this tree from disk every ~60s — uncommitted Python edits go live, and
committing here mid-cycle can strand the publish daemon. For any Python-SOURCE edit, use a per-task
worktree off origin/main with its OWN venv:
  git worktree add -b feat/<task> ../its-<task> origin/main
  cp -R .venv ../its-<task>/.venv-wt && (cd ../its-<task> && .venv-wt/bin/pip install -e . --no-deps)
Docs-only edits are fine here. See docs/operations/worktree_discipline.md.
EOF
exit 0
