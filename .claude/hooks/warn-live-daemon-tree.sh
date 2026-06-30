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
worktree off origin/main with its OWN FRESH venv:
  git worktree add -b feat/<task> ../its-<task> origin/main
  cd ../its-<task> && python3 -m venv .venv-wt && .venv-wt/bin/pip install -e '.[dev]'
Do NOT 'cp -R .venv' — a copied venv's bin/pip keeps a shebang pointing at ~/its/.venv, so
'.venv-wt/bin/pip install' silently repoints the LIVE editable install (corrupts the daemons).
Verify isolation: '.venv-wt/bin/pip show its' must say its-<task>; '~/its/.venv' must be unchanged.
Docs-only edits are fine here. See docs/operations/worktree_discipline.md.
EOF
exit 0
