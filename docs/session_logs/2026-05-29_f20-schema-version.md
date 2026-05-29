---
type: session_log
date: 2026-05-29
status: closed
workstream: safety_reports
related_prs: [129]
tags: [finding-f20, schema-version, fail-loud, op-stds-42, weekly-generate, send-gate, brief-deviation, ops-stds-enforcer, worktree, parallel-session]
---

# 2026-05-29 — F20: schema-version enforcement in `weekly_generate._load_tool_schema`

Session focus: close finding **F20** — `weekly_generate._load_tool_schema()` loaded `schemas/safety_weekly_generate.json` and projected `name`/`description`/`input_schema` to the Anthropic tools shape without ever reading the `version` key, so a wrong/stale schema would load silently and yield a structurally-wrong WPR draft with no signal.

PR: [#129](https://github.com/SolutionSmith-debug/its/pull/129) — squash-merged 2026-05-29T20:12:58Z, merge commit `dc8f08d98716a6e27b52edcbd48cebb352959b6f`. **Four-part PR-landed verify clean** (`pr-landed-verifier`): state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on the merge commit = SUCCESS (`ci` run 26659838674 + `CodeQL` run 26659838212, both completed/success).

Verification gates (local, pre-push, re-run post-rebase):
- pytest: 1145 passed / 0 skipped / 20 deselected
- mypy: 0 errors / 140 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

## Commits landed

- `dc8f08d` (squash) `fix(weekly-generate): enforce schema version + pre-flight abort on drift (F20)` — adds `_EXPECTED_SCHEMA_VERSION = "0.1.0"` + validation in `_load_tool_schema()` (raises on mismatch/missing), a run-level pre-flight `_load_tool_schema()` call in `_run_pipeline`, and 4 tests. Touches only `safety_reports/weekly_generate.py` + `tests/test_weekly_generate.py`.

## CI runs

- PR #129 checks: `test` ×2 (pass, ~56s), `Analyze (actions)` (pass, 39s), `Analyze (python)` (pass, 56s), `CodeQL` (pass) — all green.
- Merge-commit `dc8f08d` on main: `ci` run [26659838674](https://github.com/SolutionSmith-debug/its/actions/runs/26659838674) (completed/success) + CodeQL "Push on main" run [26659838212](https://github.com/SolutionSmith-debug/its/actions/runs/26659838212) (completed/success).

## Decisions made during session

- **Naming — validated the existing `version` key as-is; rejected the rename to `schema_version`.** Q8's ruling used `schema_version` as shorthand, but the live schema file (and `schemas/_example_schema.json` and `schemas/README.md`) all use `version`. Renaming would have widened the diff to ≥3 files for cosmetic gain *and contradicted the documented `schemas/README.md` convention*. Operator-confirmed via `AskUserQuestion`. Smallest blast radius; matches doctrine.
- **Scope — F20 only; rejected folding in F21.** F21 (numeric `maximum` bounds on the 6 integer incident-count fields + a numeric-range check in `shared/anomaly_logger.check()`) is a real adjacent finding — `brief-validator` confirmed the integer fields carry `minimum: 0` but no `maximum`, and `anomaly_logger._walk` has no numeric branch. Kept the PR focused per Q8's original scoping. Operator-confirmed. (Handed off below.)
- **Fail-LOUD reach — added a run-level pre-flight abort; rejected ship-as-is.** This was a *third* decision that surfaced from the `ops-stds-enforcer` review (W2), not from the brief. The raw F20 fix (validation only inside `_load_tool_schema()`) is correct but its `ValueError` is swallowed by the per-project `except Exception` fence in `_run_pipeline` (line ~1195) → per-project ERROR log + `GENERATION_FAILED` placeholder, **no CRITICAL page**, N noisy rows for one root cause. A drifted schema is a system-level precondition identical for every project, so — operator-confirmed via `AskUserQuestion` — added a pre-flight `_load_tool_schema()` call at the top of `_run_pipeline` (after the model/threshold reads, before the project loop). Raised there, outside the fence, the `ValueError` propagates to `@its_error_log` → CRITICAL triple-fire + run abort, and no Anthropic credit is spent on a run that would fail every project. Deliberately mirrors the **existing empty-reviewer-chain precondition abort** in the same function (lines ~1141–1150) — preservation-consistent, not a novel structure. The per-project `_handle_standard_project` still re-loads the schema (belt-and-suspenders); the double-read is a sub-ms local file read and was judged an acceptable cost vs. threading the schema object through three function signatures.
- **§42 rationale placed in the `_load_tool_schema` docstring + the constant comment + the pre-flight comment.** Each explains the WHY: fail-LOUD vs. the fail-open kill switch (Op Stds v14 §1), caught pre-send, lockstep-bump guidance.

## Process notes

- `brief-validator` confirmed all core F20 claims against `9a4e8c0`; the one stale anchor was claim 7 (the test file is **795 lines**, not ~236; the schema test is `test_schema_file_loads_and_projects_to_tool_shape` at line 599, not `test_project_tool_use_*` near 236). Did not derail — re-anchored before editing.
- `ops-stds-enforcer` ran twice: first pass WARN (W1 pre-existing module-docstring gap, §14 carve-out → no action; W2 fail-loud reach → acted on). Second pass after the pre-flight fix: **CLEAN, 0 blocks / 0 warnings, 7 clauses checked.**
- **Parallel-session rebase:** OBS-1 (#127, #128 — CLAUDE.md + README.md FM v9 / Op Stds v14 citation sweep) landed on main mid-session (a shared-`.git` fetch from the `~/its-obs1` worktree updated `origin/main`). Zero file overlap with F20 as the brief predicted; rebased F20 onto the updated main, re-ran gates green, then merged. Linear history preserved.
- **Branch cleanup:** `gh pr merge --delete-branch` failed its *local* post-merge step (`fatal: 'main' is already used by worktree at ~/its` — main is checked out in the daemon worktree, can't be switched here). The GitHub-side merge succeeded; the remote feature branch was deleted separately via `gh api -X DELETE .../git/refs/heads/f20-schema-version` (gh-side delete, the allowed carve-out — `git push --delete` is hook-blocked).

## Open items handed off

- **F21 (numeric upper bounds + anomaly-logger range check)** — still open, ~1hr. Suggested wording for the planning checklist: *"F21: add a sane `maximum` to the 6 integer incident-count fields in `schemas/safety_weekly_generate.json` (`lost_time_accidents`, `lost_work_days`, `job_transfer_or_restriction`, `near_misses`, `other_recordable_cases`, `first_aid_cases`) and a numeric-range branch in `shared/anomaly_logger._walk` so a prompt-injected count like `99999` is flagged. Schema integer fields currently carry `minimum: 0` but no `maximum`; `anomaly_logger` has no numeric check today."*
- **Operator worktree cleanup:** from `~/its`, run `git worktree remove ../its-f20 --force && git worktree prune` after this session (force-delete is hook-blocked from inside CC).

## What was NOT touched

- `schemas/safety_weekly_generate.json` — deliberately unchanged (validated the `version` key as-is; no rename, no `maximum` bounds — that's F21).
- `schemas/_example_schema.json`, `schemas/README.md` — unchanged (would only have been touched by the rejected rename option).
- `shared/anomaly_logger.py` — unchanged (that's F21's surface).
- The real schema file in tests — never mutated; the 3 version tests + the abort test use a `tmp_path`/`monkeypatch` fixture (`_write_fixture_schema`).
- The `weekly_generate.py` module-level docstring — left at its pre-four-heading form (§14 retrofit carve-out; not a new `shared/*` module, so the §42 four-heading requirement doesn't trigger).

## Lessons captured to memory

- No new memory file warranted. The reusable patterns here (four-part verify ritual, the per-project-fence-swallows-the-raise gotcha, the gh-side branch-delete carve-out, the shared-`.git` parallel-rebase) are already covered by `docs/operations/pr_merge_discipline.md`, `docs/operations/worktree_discipline.md`, and the existing `exec-host-worktree-daemon-topology` memory. The F21 hand-off is the durable artifact (above).
