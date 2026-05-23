# 2026-05-23 — Post-1111B canonical cutover (re-clone strategy)

PR: TBD (will be filled in post-merge). Squash-merge commit + timestamp also TBD.

Closes the loop on the 1111B blueprint absorbed in PR #67 and materialized in PR #70. Replaces the 6 legacy 1111A-derived project clones with fresh clones of 1111B (the canonical blueprint), archives the legacy folders for audit trail, and resolves all 4 `(post-1111B)` TODO markers from PR #67.

## Purpose

Close the 1111B thread end-to-end:

1. Move the 6 legacy 1111A-derived clones (Bradley 1, Bradley 2, Brimfield 1, Brimfield 2, Huntley, Rockford) out of `ITS DATA` root into an archive area (`ITS DATA / 99. Legacy 1111A Clones`), renamed `<Project> (legacy 1111A)`.
2. Clone the canonical 1111B blueprint (folder `383696567483`, materialized in PR #70) six times into `ITS DATA`, named with each project's display name (the names the legacy clones used to occupy). Bradley 1 had no demo data in Box — its Smartsheet 43-row DFR backfill is the demo data and is unaffected by this cutover.
3. Verify each new clone against the 1111B blueprint (267 descendants; all 131 RENAME_MAP target names present).
4. Swap the 6 IDs in `shared/defaults.py BOX_PROJECT_FOLDERS` so production code points at the new clones.
5. Migrate `safety_reports/intake.py BOX_SUBPATH_BY_CATEGORY` to the zero-padded numeric prefixes.
6. Verify `box_migration/parse_job_v3.py` regexes against the canonical 1111B name pattern (no extension needed — `\d+` already matches both single-digit `1.` and zero-padded `01.`).
7. Remove all 4 `(post-1111B)` TODO markers from PR #67.

## Gating decisions resolved (per the revised brief)

- **1111B inspection finding** from PR #71 was a Box UI view-setting artifact, not a 1111B defect. API confirmed clean folder names. Operator visually re-confirms post-cutover by clicking the Name column header.
- **Project-clone strategy = Re-clone from 1111B.** Bradley 1's Box footprint was verified empty of demo data before the brief was rewritten (demo lives in Smartsheet, not Box). Demo-data preservation was the original reason for the rename-in-place recommendation; with no Box demo data to preserve, re-clone is the cleaner option.
- **Cutover window** acknowledged by operator: the running intake_poll daemon's legacy code continues to look for the OLD letter-prefix Box paths until the operator deploys this PR's merged code. Risk window ≤ one poll cycle (60s for intake_poll); real production traffic is low-volume Friday-evening/weekend so the gap is small.

## Code changes

### New file
- **`scripts/migrations/reclone_projects_from_1111b.py`** — three-phase idempotent migration:
  - **Phase A**: ensure `ITS DATA / 99. Legacy 1111A Clones` exists; move + rename the 6 legacy folders into it (suffix `(legacy 1111A)`).
  - **Phase B**: clone 1111B 6× under `ITS DATA` with canonical project names.
  - **Phase C**: verify each new clone matches 1111B blueprint (descendant count + every `RENAME_MAP` target name present).
  - Emits mapping JSON at `~/its/logs/migrations/reclone_1111b_folder_ids.json` with `{old_id, new_id, status}` per project.
  - CLI: default (full cutover), `--project <slug>`, `--dry-run`, `--verify-only`.
  - Inline-replicated helpers (`copy_with_lock_retry`, `wait_for_deep_copy_complete`, `_find_child`, `_is_lock_error`, `_count_child_folders`) from `box_build_1111b_blueprint.py` per Op Stds v11 §14 preservation-over-refactor.

### New tests
- **`tests/test_reclone_projects_from_1111b.py`** — 19 unit tests covering the cutover flow with mock Box client. Module-level invariants (6-project count, slug↔name mapping, 267 descendant constant, legacy suffix format); `ensure_legacy_archive_folder` (create / existing / dry-run); `archive_one_legacy` (move-and-rename, already-archived skip, legacy-missing WARN, dry-run non-mutating); `reclone_one_project` (clone-when-missing, existing-matches skip, existing-mismatched refuses, dry-run non-mutating); `verify_only_one_project`; `verify_clone` (pass + fail branches).
- **`tests/test_reclone_projects_integration.py`** — 1 gated `pytest -m integration` test against a **disposable parent** under `ITS DATA` (NOT against real ITS DATA root) per Op Stds v11 §30. Clones 1111B → disposable, waits for deep-copy, verifies compliance, deletes recursively in finally.

### Migrated files
- **`safety_reports/intake.py BOX_SUBPATH_BY_CATEGORY`** — letter-prefix path segments migrated to zero-padded numeric:
  - `A. Onsite Reporting & Tracking` → `01. Onsite Reporting & Tracking`
  - `A. Safety Plan & Reports` → `01. Safety Plan & Reports`
  - `B. Project Reports & Trackers` → `02. Project Reports & Trackers`
  - `D. JSA's` → `04. JSAs` (typo fix on apostrophe + zero-pad)
  - `E. Tool Box Talks` → `05. Tool Box Talks`
  - `D. Inspection Reports` → `04. Inspection Reports`
  - `(post-1111B)` TODO marker removed.
- **`shared/defaults.py BOX_PROJECT_FOLDERS`** — 6 folder IDs swapped from legacy 1111A clones to new 1111B clones. Mapping JSON inline below.
- **`box_migration/parse_job_v3.py`** — `(post-1111B)` TODO removed; verified regex compatibility (no extension needed — `\d+` matches both legacy single-digit and zero-padded prefixes). Replacement comment documents the verification.
- **`shared/sheet_ids.py FOLDER_PROJECT_*`** — `(post-1111B)` TODO removed; replacement comment clarifies these are SMARTSHEET folder IDs (not Box) and stay unchanged across the cutover.
- **`safety_reports/weekly_generate.py`** — forward-looking `(post-1111B)` TODO removed (this module doesn't do Box folder lookups today; the marker was speculative).
- **`CLAUDE.md`** — `shared/defaults.py` row notes BOX_PROJECT_FOLDERS now references 1111B-derived clones post-cutover.
- **`README.md`** — Phase 1 status row updated to reflect R3 + Box 1111B + Trusted_Contacts work landed; remaining Phase 1.4 deliverables (picklist-hardening + attachment screening).

## Folder-ID mapping (post-cutover)

Inline copy of `~/its/logs/migrations/reclone_1111b_folder_ids.json` produced by the migration:

```json
{
  "bradley_1": {
    "old_id": "383299029178",
    "new_id": "383795291728",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_bradley_1_report.txt"
  },
  "bradley_2": {
    "old_id": "383298229322",
    "new_id": "383795215056",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_bradley_2_report.txt"
  },
  "brimfield_1": {
    "old_id": "383303174342",
    "new_id": "383796013268",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_brimfield_1_report.txt"
  },
  "brimfield_2": {
    "old_id": "383303695163",
    "new_id": "383792793376",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_brimfield_2_report.txt"
  },
  "huntley": {
    "old_id": "383302259414",
    "new_id": "383796738311",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_huntley_report.txt"
  },
  "rockford": {
    "old_id": "383305112425",
    "new_id": "383794509507",
    "status": "cutover_complete",
    "verify_passed": true,
    "compliance_report": "/Users/sethsmith/its/logs/migrations/reclone_project_rockford_report.txt"
  }
}
```

All 6 projects: `status=cutover_complete`, `verify_passed=true`. Each new clone has 267 descendants matching 1111B. Per-project compliance reports under `~/its/logs/migrations/`.

## Verification

| Stage         | Result                                                                                  |
|---------------|-----------------------------------------------------------------------------------------|
| pytest -q     | **1004 passed / 1 skipped / 16 deselected** (+55 from baseline 949; PR #72 trusted-contacts + PR #73 picklist-hardening + my reclone tests). |
| mypy .        | **Success: no issues found in 123 source files**.                                       |
| ruff check .  | **All checks passed**.                                                                   |
| TODO grep     | `grep -rE "TODO\(post-1111B\)" --include="*.py" .` → **zero matches** ✓                  |
| 6 clones verified | **PASS** per cutover script's in-loop verification — 267 descendants each, all 131 RENAME_MAP target names present. |
| CI            | Pre-existing red on main (PRs #71/#72/#73 all "failure" — Linux-no-Keychain pattern noted in `project-phase1-status` memory). My PR inherits the same — not introduced by this work. |

## Decisions made during session

- **Combined Phase 1 (clone) + Phase 3 (archive) into one script in correct execution order.** Box requires unique names within a folder; the brief's section order (clone first) would conflict with the existing legacy folders. The script archives legacy folders FIRST (moves them out of `ITS DATA` root), then clones 1111B fresh under the canonical names. Both phases are idempotent + retry-aware so partial-run recovery works.
- **Mapping JSON emission deferred to script completion.** Brief Phase 1 step 2 specifies emitting the JSON at end of run. If the run is killed mid-flight (e.g., bash timeout), the JSON is not written. Mitigation for this run: the script's per-project log lines record each new folder_id, so the operator (or a follow-on tool) can reconstruct the mapping from the log if needed.
- **`parse_job_v3.py` regex set required no extension.** The active-signature regexes use `^\d+\.` which matches both `1. Portfolio X` and `01. Portfolio X`. Verified live against the canonical 1111B top-level names (test in commit). Just removed the TODO + added a "verified" replacement comment.
- **`shared/sheet_ids.py FOLDER_PROJECT_*` are SMARTSHEET folder IDs, not Box.** The PR #67 TODO was misplaced — Smartsheet folder structure is independent of Box's blueprint cutover. Removed the TODO; replacement comment clarifies the type.
- **Live cutover ran in background.** Bash tool's 10-min ceiling vs. ~9 min/clone × 6 = ~54 min total means a single bash invocation cannot complete all 6. The script is idempotent so partial runs resume safely; multiple bash invocations covered the full set.

## CI runs

- TBD post-PR open.

## Subtleties found mid-implementation

- **Untracked `shared/picklist_validation.py` in working tree** triggers a local `ruff N818` error (`PicklistViolation` should be named with an Error suffix). The file is NOT in main (untracked artifact from a parallel in-flight workstream beyond PR #72). CI won't see it (fresh clone). My code is ruff-clean against the staged + tracked file set.
- **mypy duplicate-module error** would have triggered for `from scripts.migrations.X import …` in tests. Same workaround as PR #70 + #72 tests: `sys.path.insert(scripts/migrations/)` + bare import. Suppresses E402 via `# noqa`.
- **Box deep-copy is async at multiple levels** — top-level children populate first (~3-4 min for 14 children), then descendants continue populating for several more minutes until all 267 are present. The script polls every 15s until full descendant count is reached before declaring success + verifying.

## What's NOT touched

- `1111A (Copy for new projects)` template — untouched, stays in `ITS DATA` as historical reference.
- The 6 legacy 1111A-derived clones — **archived**, not deleted. Live at `ITS DATA / 99. Legacy 1111A Clones / <Project> (legacy 1111A)`.
- The 1111B template (folder `383696567483`) — unchanged (it's the source of the new clones).
- Parallel ITS_Trusted_Contacts workstream files — landed via PR #72 already; this PR doesn't touch them.
- `shared/picklist_validation.py` (untracked working-tree file from a different parallel workstream) — left alone.
- Sub-decisions from PR #67's session log (Engineering Gen naming, Templates consolidation, Field/06 vs Portfolio/12 Closeout merge) — separate PRs if/when resolved.

## Operator-side actions remaining (post-merge, not blocking)

1. Visually inspect new project clones in Box UI; click Name column header to confirm zero-padded sort displays correctly.
2. Decide whether to re-run Bradley 1's DFR backfill against the new clone (Smartsheet demo data persists; Bradley 1's Box footprint had no demo files to begin with — the backfill is Smartsheet-only).
3. Decide future deletion timing for `99. Legacy 1111A Clones` (recommend keeping ≥ 30 days as audit trail).

## Baseline state at session close

- `main` at PR #70 merge commit (TBD updated post-PR-merge); branched off `06337bd` (PR #73 picklist-hardening merge).
- pytest **1004 passed / 1 skipped / 16 deselected**. mypy **0 / 123**. ruff **clean**.
- 6 new project clones live at `ITS DATA / <Project>`, each with 267 descendants, all 131 RENAME_MAP target names verified.
- 6 legacy clones archived at `ITS DATA / 99. Legacy 1111A Clones / <Project> (legacy 1111A)` (folder ID `383792548667` for the archive parent).
- Zero `TODO(post-1111B)` markers remaining in the repo.
- `BOX_PROJECT_FOLDERS` now references the 6 new 1111B-derived clones (mapping above).
- `BOX_SUBPATH_BY_CATEGORY` in `safety_reports/intake.py` migrated to zero-padded numeric prefixes.

## Sequencing context

Closes the 1111B thread end-to-end: PR #67 (design absorb) → PR #70 (blueprint build) → this PR (canonical cutover + code migration). With the cutover done, the Phase 1.4 security hardening cluster's remaining two deliverables (picklist-hardening + attachment screening) become the next focus, plus any sub-decisions from PR #67 that the operator wants to action.
