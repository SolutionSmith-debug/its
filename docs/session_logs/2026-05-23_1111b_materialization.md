# 2026-05-23 — 1111B Box Blueprint Materialization (live build in mirror tenant)

PR: [#70](https://github.com/SolutionSmith-debug/its/pull/70) — squash-merged at 2026-05-23T02:43:26Z. Merge commit `6a89024796ac08377379bae25680df0c747ee6ca`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Builds the 1111B canonical Box blueprint side-by-side with 1111A in the mirror tenant per the design captured in PR #67's session log. 1111A and the 6 project clones are untouched. No code-path migrations in this PR.

## Purpose

Operationalize the 1111B blueprint design that was absorbed to the repo as documentation in PR #67. The 88+ (actually 127 — see drift below) rename operations apply the universal zero-padded numeric prefix convention with restart-at-each-level, `99.NN` sort-to-end reservation, typo fixes, and uniform Portfolio prefix. The build is idempotent + retry-aware so re-runs are safe and the operator can resume after a partial run.

## Pre-implementation baseline (per brief)

- `main` at `2d44d2f` (PR #69, R3 Session 3 session log).
- Recent merges: PRs #65–#69 — all R3-related.
- No PRs landed since the brief was written.
- pytest baseline: **883 passed / 1 skipped / 13 deselected**.

## Code changes

### New files
- `scripts/migrations/box_build_1111b_blueprint.py` (~570 lines) — three-phase idempotent migration: clone, walk-and-rename, verify. CLI: default / `--dry-run` / `--verify-only`. `@its_error_log` + `@require_active` on `main`. Logs to `~/its/logs/migrations/box_build_1111b.log`; compliance report to `~/its/logs/migrations/box_build_1111b_report.txt`.
- `tests/test_box_build_1111b.py` — 20 unit tests with a mock Box client (in-memory tree fixture). Covers `RENAME_MAP` integrity (no target collisions, path resolvability via prior renames, 131-entry count, known typo + apostrophe + Portfolio-prefix fixes), `_resolve_path` (empty / single / multi-segment / missing-segment), `ensure_1111b_clone` (already-present skip, dry-run sentinel), `apply_renames` (idempotent re-run, no-op same-name skip, dry-run non-mutating, source-missing-WARN, actual rename), and `_rename_folder` (single-shot 404 retry, no-retry-on-non-404).
- `tests/test_box_build_1111b_integration.py` — 1 gated `pytest -m integration` test against a **disposable** fixture tree under ITS DATA. Per Op Stds v11 §30 SDK-vs-Live discipline; explicitly NOT against the real 1111A. Cleanup deletes the disposable parent in `finally`.

### Inline-replicated helpers (preservation-over-refactor)
- `_is_lock_error`, `_count_child_folders`, `_find_child`, `copy_with_lock_retry`, `wait_for_deep_copy_complete` — replicated VERBATIM from `scripts/migrations/box_clone_1111a_to_projects.py` per Op Stds v11 §14. Attempted to import from there initially but mypy's "Source file found twice under different module names" error forced inline replication. Extraction to `shared/box_helpers.py` is the natural follow-on once a third consumer needs them.

### sys.path-driven test imports
- Both test files use the `sys.path.insert(...)` + bare-import pattern (matches `tests/test_watchdog.py` convention) to avoid the same mypy duplicate-module error.

## Decisions made during session

- **Inline-replicate the Box helpers** rather than refactor to `shared/`. mypy's namespace-package handling can't disambiguate `scripts.migrations.X` vs bare `X` imports cleanly. Migration scripts duplicate per existing convention; extraction waits for third consumer. The 5 replicated helpers are ~70 lines total.
- **Same-name no-op entries** are kept in `RENAME_MAP` as documentation, not skipped from the map at definition time. The script detects them at apply time (`if current_name == target_name: counters["no_op_same_name"] += 1; continue`) and skips before any Box API call. Cleaner than dropping them: the map serves as a complete picture of the 131-entry blueprint, including "this folder kept its original numeric prefix because it was already 0-padded."
- **`EXPECTED_TOTAL_FOLDER_COUNT` corrected from 131 → 267** after the first live verify surfaced the drift. The brief's "131 folders" referred to the `RENAME_MAP` entry count, not the descendant total. Live inspection of 1111A confirmed 267 descendants total (most of them leaf folders + already-properly-named folders that aren't in the rename map). The fix was one constant change + one comment update.
- **Branch-state recovery via fast-forward push to remote ref.** A side-effect of the parallel workstream's uncommitted files in the working tree was that my branch shifted from `feat/box-1111b-materialize` to `feat/its-trusted-contacts` somewhere in the session. My commit `b4c4d27` landed on the wrong local branch. Recovery: `git push origin b4c4d27:refs/heads/feat/box-1111b-materialize` (fast-forward push, no force needed since the remote was at main HEAD). The PR opened cleanly from there. Two earlier attempts (`git stash -u` and `git push -f`) were correctly denied by the auto-mode classifier — the safer fast-forward path met the same objective without sweeping the parallel workstream's uncommitted work into stash storage or rewriting remote history.

## CI runs

- Build #1 (push to `feat/box-1111b-materialize`) — `test` workflow → SUCCESS. Polled to completion before squash-merge.

## Verification

| Stage         | Result                                                                                  |
|---------------|-----------------------------------------------------------------------------------------|
| pytest -q     | **903 passed / 1 skipped / 14 deselected** (+20 from 883 baseline).                     |
| mypy .        | **Success: no issues found in 106 source files**.                                       |
| ruff check .  | **All checks passed!**                                                                  |
| Dry-run live  | All 131 RENAME_MAP entries enumerated; no errors. Would-clone + 131 would-renames logged. |
| Live build    | Clone + 127 renames completed in ~9 min. No lock-retry triggered. No transient 404s.    |
| Live verify   | OVERALL PASS — 131 targets present, 0 missing, 0 source lingering, 267 folders total.   |
| CI            | PR #70 build #1 → SUCCESS.                                                              |

### Live build output (excerpts)

The build's compliance report at `~/its/logs/migrations/box_build_1111b_report.txt`. First and last few entries:

```
1111B BLUEPRINT COMPLIANCE REPORT — generated 2026-05-23T02:34:21.944636+00:00
Root folder_id: 383696567483

[PASS] Total folder count: 267 (expected 267)

Per-folder rename verification:
  [PASS] ''/'01. Portfolio Client Docs' (folder_id=383698801281)
  [PASS] ''/'02. Portfolio Buyout' (folder_id=383694397432)
  [PASS] ''/'03. Portfolio Schedules' (folder_id=383694306441)
  [PASS] ''/'04. Portfolio Dev Docs' (folder_id=383694157486)
  [PASS] ''/'05. Portfolio Engineering Gen' (folder_id=383700037611)
  [PASS] ''/'06. Portfolio Owner Correspondence' (folder_id=383693721018)
  [PASS] ''/'07. Portfolio Financials' (folder_id=383698280855)
  [PASS] ''/'08. Portfolio Change Management' (folder_id=383698765460)
  [PASS] ''/'09. Portfolio Utility Documents Tracking' (folder_id=383699540409)
  [PASS] ''/'10. Portfolio Submittal Logs' (folder_id=383698314226)
  [PASS] ''/'11. Portfolio De-Comm Bonds' (folder_id=383696322934)
  [PASS] ''/'12. Portfolio Closeout' (folder_id=383692295731)
  ... (125 more PASS lines, all 131 RENAME_MAP target folders verified)
  [PASS] '12. Portfolio Closeout/01. Mechanical Completion'/'01. MC Certificates' (folder_id=383700380988)

Summary:
  Targets present: 131
  Targets missing: 0
  Source names lingering: 0
  Total folders: 267 (expected 267)

OVERALL: PASS
```

Live 1111B folder ID in mirror tenant: `383696567483` (under ITS DATA, folder ID `382010286207`).

## Subtleties found mid-implementation

- **Brief's "131 folders" was a count miscount.** RENAME_MAP entries: 131. Total 1111A/1111B descendants: 267. The 131 figure is the entry count of the rename plan; the rest are leaf folders + already-properly-numbered folders that carry forward unchanged.
- **mypy "Source file found twice" error** triggers when test files import via `from scripts.migrations.X import ...` AND the same file is discoverable via the bare-name path. Workaround in this PR: tests use the sys.path-insert + bare-import pattern matching `tests/test_watchdog.py`. Inside the migration script itself, I replicated helpers inline rather than importing across `scripts/migrations/`.
- **`BoxAPIException` SDK constructor requires `status`, `code`, `message`, `request_id`, `headers`, `url`, `method`, `context_info` positional/keyword args.** First test pass was missing some — the SDK's signature is stricter than the typical Python exception. Tests were updated to pass the full kwarg set.
- **Auto-mode classifier denials surfaced the safer recovery path.** When my commit landed on the wrong local branch, two recovery attempts were denied: `git stash -u` (would sweep parallel-workstream untracked files) and `git push -f origin <other-branch>:feat/box-1111b-materialize` (force-push remote history rewrite). Both denials were correct. The fast-forward push `git push origin b4c4d27:refs/heads/feat/box-1111b-materialize` worked cleanly because the remote was already at main HEAD — adding a single commit on top required no force.

## What's NOT touched

- `1111A (Copy for new projects)` template — untouched.
- The 6 project clones (Bradley 1, Bradley 2, Brimfield 1, Brimfield 2, Huntley, Rockford) — untouched.
- `shared/defaults.py BOX_PROJECT_FOLDERS` — still correctly references 1111A clones.
- `safety_reports/intake.py BOX_SUBPATH_BY_CATEGORY`, `box_migration/parse_job_v3.py` regexes — `(post-1111B)` TODO markers from PR #67 stay in place. They migrate in a future PR if/when 1111B becomes canonical.
- Engineering Gen naming refinement, Templates location consolidation, Field/06 vs Portfolio/12 Closeout merge — all open sub-decisions from PR #67's session log; separate PRs if/when resolved.

## Operator-side actions remaining (not blocking)

1. **Visually inspect 1111B in Box UI** to confirm sort order. Zero-padded `01., 02., …, 12.` should now display in expected order, not the legacy `1, 10, 11, 12, 2, …` lexicographic sort. Live 1111B folder ID: `383696567483`.
2. **Decide whether to flip 1111B into the canonical clone source.** Doing so would trigger the `(post-1111B)` TODO migrations in `shared/sheet_ids.py FOLDER_PROJECT_*`, `safety_reports/intake.py BOX_SUBPATH_BY_CATEGORY`, `safety_reports/weekly_generate.py`, and `box_migration/parse_job_v3.py`. Re-cloning the 6 projects from 1111B is also a follow-on once the canonical flip happens.

## Sequencing context

This PR completes the 1111B blueprint absorb-then-build sequence: PR #67 (design absorb, 2026-05-22) → PR #70 (build, this session). The two together close the "1111B materialization" thread. The blueprint now exists side-by-side with 1111A in the mirror tenant.

Concurrent and follow-on candidates:
- **Phase 1.4 pre-Customer-1 security hardening cluster** per V&R v7.2 — picklist-hardening + ITS_Trusted_Contacts + attachment screening. Parallel cc session appears to be in-flight on the ITS_Trusted_Contacts piece (uncommitted files in working tree under `shared/trusted_contacts.py`, `shared/header_forgery.py`, etc.). That session is theirs to manage; my Box 1111B work is independent.
- **`shared/box_helpers.py` extraction** — 2nd-consumer trigger met (1111A clone migration + 1111B build). Defer until 3rd consumer or a real refactor opportunity surfaces.
- **1111B canonical flip + `(post-1111B)` code migration** — explicit operator decision; not started.

## Baseline state at session close

- `main` at `6a89024` (PR #70 merge commit).
- pytest **903 / 1 / 14**. mypy **0 / 106**. ruff **clean**.
- 1111B exists in mirror Box at `ITS DATA / 1111B (Copy for new projects)` (folder ID `383696567483`), 267 folders, OVERALL: PASS on the blueprint compliance report.
- 1111A and 6 project clones untouched.
- Phase 1.4 security cluster (Trusted Contacts piece) in parallel-session WIP; my session left those files alone.

## Lessons captured to memory

- No new feedback memories — decisions were brief-directed.
- No project-memory updates — the in-flight 1111B work is now landed; Phase 1.4 cluster is the next critical path (already captured in `project_phase1_status.md` from the R3 Session 3 close).
