---
type: operations
date: 2026-05-29
status: active
related_prs: []
workstream: infrastructure
tags: [worktree, parallel-sessions, daemon, isolation]
---

# Worktree Discipline (parallel CC sessions)

Canonical procedure for running **concurrent Claude Code sessions** against the ITS
repos without the sessions colliding on each other's working tree, and without an
in-progress edit going live on the production daemon before it is reviewed.

Parallel sessions are now a normal mode of operation (a single day has run up to four
at once). This doc makes the worktree pattern that the execution repo already relies
on explicit, repeatable, and — for the first time — extended to the **blueprint repo**.

## Purpose

Two distinct hazards motivate worktrees. They are separate problems with one shared fix.

1. **The live daemon runs the `~/its` working tree directly.** The launchd intake
   daemon (`safety_reports/intake_poll.py`, 60 s cadence) executes whatever code is in
   the `~/its` checkout *right now*. An uncommitted edit saved in `~/its` goes live on
   the next cycle — there is no build/deploy step between "save" and "production." So
   `~/its` is **not** a scratchpad: editing code there is editing production.

2. **Parallel sessions sharing one checkout collide.** Two CC sessions in the same
   directory fight over `git checkout` / branch state, staged files, and the working
   tree. The exec repo has avoided this by giving each task its own `git worktree`
   (`~/its-f16`, `~/its-f02-f22`, `~/its-optimize`, …). The **blueprint repo had no
   such discipline** — it was a single shared checkout, and on 2026-05-29 two
   doctrine-touching sessions layered edits onto it; the collision was avoided only
   because the second session *noticed* and refused to stash over the first. Nothing
   structural prevented it. This doc closes that gap.

A `git worktree` gives each concurrent task its own working directory **and** its own
branch, all backed by the same `.git` object store. Sessions never see each other's
uncommitted files, and the production `~/its` checkout stays clean and on `main`.

## Procedure

### Exec-repo pattern (`~/its`)

For any task that touches execution-repo code or docs:

```bash
# 1. From the ~/its checkout (any branch is fine — worktree add reads the object store,
#    not the working tree), create an isolated worktree + branch off origin/main:
git -C ~/its fetch origin
git -C ~/its worktree add ~/its-<task> -b <branch> origin/main

# 2. Launch Claude Code ROOTED in the new worktree so the full agent registry +
#    the PreToolUse guard hooks load (they resolve relative to $CLAUDE_PROJECT_DIR):
#       cd ~/its-<task> && claude
```

**Import-path rule (load-bearing — this is the gotcha that bites).** The venv at
`~/its/.venv` is an *editable* install of the project (`pip install -e ~/its`). Its
import finder maps the `shared` and `safety_reports` packages to **`~/its`**, not to
your worktree. The resolution order (verified 2026-05-29) is:

```
sys.meta_path = [BuiltinImporter, FrozenImporter, PathFinder, _EditableFinder]
sys.path[0]   = ''   # == current working directory
```

`PathFinder` runs **before** `_EditableFinder`, and it consults the CWD (`sys.path[0]`)
then `PYTHONPATH`. The practical consequences:

- **Run commands from the worktree root** → `import shared` resolves to the worktree
  via the CWD entry. This is why `pytest` / `mypy` / `ruff` launched from inside
  `~/its-<task>` usually "just work."
- **Any command whose CWD is *not* the worktree** (a subprocess that `chdir`s, a tool
  with its own rootdir, a one-off `python -c` run from elsewhere) falls through to
  `_EditableFinder` and **silently imports `~/its`'s copy** — ImportError if your
  branch added a new module, or (worse, no error) the *stale* `~/its` version of a
  module you changed. This is the real failure this discipline exists to prevent.
- **Belt-and-suspenders:** prefix `PYTHONPATH=~/its-<task>` on any `python3` / `pytest`
  invocation. `PYTHONPATH` entries land on `sys.path` ahead of `site-packages`, so
  `PathFinder` resolves them before `_EditableFinder` (which sits later in `sys.meta_path`)
  ever runs — the worktree wins regardless of CWD (verified from a foreign CWD).

```bash
# Confirm imports resolve to the worktree, NOT ~/its, before trusting any test run:
PYTHONPATH=~/its-<task> python3 -c "import shared, safety_reports; print(shared.__file__)"
# MUST print  /Users/<you>/its-<task>/shared/__init__.py   (NOT  .../its/shared/...)

# Canonical verification gate from inside a worktree:
PYTHONPATH=~/its-<task> ruff check . \
  && PYTHONPATH=~/its-<task> mypy . \
  && PYTHONPATH=~/its-<task> pytest -q
```

### Blueprint-repo pattern (`~/its-blueprint`) — the new rule

The blueprint is markdown-native (doctrine, missions, references, scaffolds) — there is
**no venv / PYTHONPATH concern**. But it has the **same checkout-collision concern** as
the exec repo, and it had no isolation discipline until now.

```bash
git -C ~/its-blueprint fetch origin
git -C ~/its-blueprint worktree add ~/its-blueprint-<task> -b <branch> origin/main
#   cd ~/its-blueprint-<task> && claude
```

**Rule: never run two doctrine-touching sessions against the same blueprint checkout.**
Each doctrine/reference/scaffold-editing session gets its own blueprint worktree, or it
waits (see [the serialization alternative](#the-serialization-alternative)).

**Note — the blueprint's `.claude/` reads the exec checkout.** `~/its-blueprint/.claude/agents`
and `.../hooks` are committed **symlinks** into `../../its/.claude/{agents,hooks}` (a single
source of truth — there are no duplicate copies to drift). They resolve against the
`~/its` *working tree*, so the agent registry a blueprint session sees is whatever branch
`~/its` is currently checked out on. This is a second reason to keep `~/its` on `main`
(below): an off-main `~/its` means blueprint sessions silently load off-main agent
definitions.

**The symlink is relative and assumes the `~/`-level sibling layout** (`~/its-blueprint`
next to `~/its`). A blueprint worktree placed as a `~/`-sibling (`~/its-blueprint-<task>`)
resolves correctly, but a non-sibling location — or a clone of the blueprint *alone*
(e.g. a CI checkout or a fresh machine without `~/its` beside it) — leaves the symlink
**dangling**. Claude Code then finds no agents, and the propose-only guard hooks
(`block-codeql-dismiss`, `block-doc-reconciliation-write`, `block-doctrine-write`) silently
disappear — a **fail-open**, not fail-closed. So: keep blueprint worktrees as `~/`-level
siblings of `~/its`, and do not assume the agent/hook guards are present in a bare blueprint
clone. The full residual-risk analysis (clone fail-open, working-tree branch coupling,
unguarded `settings.json` copy) is in
[`docs/audits/2026-05-29_agent-workflow-audit.md`](../audits/2026-05-29_agent-workflow-audit.md).

### Keep the daemon checkout (`~/its`) on `main`

`~/its` is the production checkout (the daemon runs it) **and** the symlink target for the
blueprint's agent/hook registry. It should sit on `main`, clean, between tasks. After any
PR merges, return it to main:

```bash
git -C ~/its checkout main && git -C ~/its pull origin main
```

If `~/its` is parked on a feature branch (as it was on 2026-05-29 —
`close-out-f02-f22-tech-debt`), the daemon is running un-merged code and blueprint
sessions are reading un-merged agents. Do real work in a worktree, not in `~/its`.

### Cleanup (operator action — by design)

When a task's worktree is done and its branch is merged:

```bash
git -C ~/its worktree remove ~/its-<task> --force \
  && git -C ~/its branch -D <branch> \
  && git -C ~/its worktree prune
```

`--force` (on `worktree remove`) and `-D` (force branch delete) are required because the
branch is already merged (so git's safe-delete `-d` refuses it as "not merged into HEAD
of *this* checkout") and the worktree may hold a now-empty index. **These commands are
blocked by the `block-dangerous-git.sh` PreToolUse hook from inside a CC session** — that
hook refuses `git branch -D` and force operations on purpose. So **worktree cleanup is an
operator action run in a normal shell, not something a CC session performs.** This is the
direct reason stale worktrees (`its-f16`, `its-f02-f22`, the earlier `its-sweep`)
accumulate: the sessions that created them could not remove them.

## Examples

A real snapshot captured **mid-session** on 2026-05-29 (`git -C ~/its worktree list`) — by
session close `~/its` had returned to `main`, but the captured state is the instructive one:

```
/Users/sethsmith/its           36e429e [close-out-f02-f22-tech-debt]   <- daemon checkout, OFF main (should be on main)
/Users/sethsmith/its-f02-f22   ae7131c [session-log-f02-f22]           <- stale; needs operator cleanup
/Users/sethsmith/its-f16       57bdca8 [f16-session-log-final]         <- stale; needs operator cleanup
/Users/sethsmith/its-optimize  df83713 [worktree-discipline-and-audit] <- this session, isolated
```

Three things this illustrates: (1) the daemon checkout drifted off `main`; (2) two stale
worktrees linger because cleanup needs the operator; (3) the active task ran fully
isolated in its own worktree off `origin/main` — the pattern working as intended.

## Validation

Confirm isolation is correct before and during a session:

```bash
# 1. Each worktree is a sibling of the repo, never NESTED inside it:
git -C ~/its worktree list            # paths should be ~/its, ~/its-<task>, ... (all under ~/)

# 2. Your session's worktree is clean and on its own branch:
git -C ~/its-<task> status --short && git -C ~/its-<task> branch --show-current

# 3. Imports resolve to YOUR worktree (see import-path rule above):
PYTHONPATH=~/its-<task> python3 -c "import shared; print(shared.__file__)"

# 4. The daemon checkout is on main between tasks:
git -C ~/its branch --show-current     # expect: main
```

**Worktrees live as siblings (`~/its-<task>`), never nested inside the repo root.** A
nested worktree (e.g. `~/its/its-temp`) would sit inside the tracked tree and could be
picked up by repo-relative tooling or accidental `git add`. The repo's `.gitignore`
carries a defensive `/its-*/` guard against an accidentally-nested worktree, but the
convention is the real protection: keep them siblings.

## The serialization alternative

If you would rather not manage worktrees for a given burst of work, the fallback is
strict serialization:

> **One session per repo at a time. Land (merge) before starting the next.**

This trades parallelism for simplicity and is the right call when tasks are quick and
sequential, or when you are uncomfortable with worktree cleanup. Pick per-situation:
worktrees when you genuinely need concurrency; serialize when you don't. What you must
**not** do is run two sessions against one shared checkout — that is the collision this
doc exists to prevent.

## Guardrails — what's enforced vs. what's discipline

The protection here is **mostly discipline + this doc**, deliberately, not heavy tooling:

- **`block-dangerous-git.sh` does NOT cover worktree operations** (no `git worktree`
  pattern in its blocklist), and that is correct — `worktree add/remove/prune` are not
  inherently destructive the way force-push or `reset --hard` are. The one indirect
  interaction is that it blocks the force-delete *cleanup* step, which is why cleanup is
  an operator action (above).
- **A session-start "am I in a worktree / is `~/its` on `main`" warning** was considered
  and is **recommended-but-not-built**: a hard guard risks false friction (legitimate
  reasons to run from `~/its` exist — e.g. operator cleanup itself), and the failure mode
  is recoverable (a colliding session notices, as happened on 2026-05-29). The
  cost/benefit favors doc-over-tooling here. If the collision recurs *despite* this doc,
  revisit a lightweight, low-false-positive `SessionStart` notice. See the
  2026-05-29 agent/workflow audit (`docs/audits/2026-05-29_agent-workflow-audit.md`)
  for the full reasoning.

## Owner

`@solutionsmith`. The blueprint half of this discipline (the blueprint-repo perspective,
to live in the blueprint's own operations docs) is drafted in the 2026-05-29 audit and
lands in a **separate blueprint-rooted PR** once the blueprint's `close-out-f02-f22`
branch is merged — not in the PR that introduced this doc.
