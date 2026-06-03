---
type: session_log
date: 2026-06-03
status: closed
related_prs: [151, 152, 153]
workstream: infrastructure
tags: [picklist-drift, project-routing, e1-cutover, smartsheet, worktree-discipline, four-part-verify]
---

# Session log — Phase 3a/3b decisions + E1 cutover (continuation of 2026-06-02)

Continuation of the 2026-06-02 session. Picked up the three deferred decisions
(D1/D2/D3 from the picklist-drift + E1 handoff) and resolved all three within
the session. Three PRs merged and deployed to `~/its`; `~/its` now at `9ff87ea`.

## Commits / PRs landed

- **PR #151 — Phase 3a: add dormant picklist columns (D1=ADD)** — squash
  `0c73cb4`. New `shared/smartsheet_client.create_picklist_column` (additive
  column-create, §42 docstring, `@_breaker_guard`; the create-side complement to
  `ensure_picklist_options`) + idempotent `scripts/migrations/add_dormant_picklist_columns.py`
  (preview-default, `--commit`; options sourced from `picklist_validation.REGISTRY`).
  LIVE: created `ITS_Errors·Workstream` (col 368377473568644) and
  `ITS_Quarantine·Disposition` (col 8535753050328964) as PICKLIST with registry
  options. tech_debt 3a entry → RESOLVED.

- **PR #152 — E1 cutover (D3=NOW)** — squash `f30f0ee`. Flipped
  `shared/sheet_ids.py:85 SHEET_PROJECT_ROUTING = 3500842291253124`. LIVE: built +
  seeded ITS_Project_Routing (6 BOX_PROJECT_FOLDERS rows); `get_folder_id` reads
  from the sheet. `sheet_unwired` fixture added so the pre-cutover-fallback unit
  test simulates the unwired state (constant flip broke its `== 0` premise).
  BOX_PROJECT_FOLDERS retained as dict fallback; removal deferred to post-Evergreen-prod.

- **PR #153 — Phase 3b: automated drift apply (D2=AUTOMATE)** — squash `9ff87ea`.
  Added `--apply` (dry-run default) and `--apply --commit` to
  `scripts/audit_picklist_drift.py`, built on `ensure_picklist_options`. Additive
  and option-only (missing/wrong-typed column → log+skip, not crash). Prune out of
  scope for v1 (parity with the helper). §30 live test + real-registry live smoke
  (0 adds/0 skips). Runbook `--apply` flow made real (was contingent); additive
  option drift on an existing column reclassified Tier-2. tech_debt 3b entry →
  RESOLVED.

## CI runs / four-part verify

PR #152 (E1 cutover) — four-part verify clean:
- state: MERGED · mergedAt: 2026-06-03T15:35:57Z · mergeCommit: f30f0eeb538b3cf0d234e4f713c7d1b7573aa2b2
- main CI on merge commit: SUCCESS (run 26895495311 — test/secrets/Analyze(python)/Analyze(actions) all success)

PR #153 (Phase 3b) — four-part verify clean:
- state: MERGED · mergedAt: 2026-06-03T15:40:05Z · mergeCommit: 9ff87ea9f2f7026999664d1cc3be615ee33cb82d
- main CI on merge commit: SUCCESS (run 26895735464 — all success)

PR #151 (Phase 3a) — four-part verify clean AFTER CI re-run:
- state: MERGED · mergedAt: 2026-06-03T15:35:43Z · mergeCommit: 0c73cb4d991a73b226a468e2c07516460f9a724e
- main CI on merge commit: initially CANCELLED (concurrency — #152 push superseded it 14s later); re-ran via `gh run rerun 26895484315` → conclusion=success (test: success, secrets: success) on sha 0c73cb4d. Leg 4 satisfied.

Per-branch local gate before merge:

| PR | pytest | mypy | ruff |
|----|--------|------|------|
| #151 | 1298 passed / 29 deselected | clean | clean |
| #152 | 1287 passed / 28 deselected | clean | clean |
| #153 | 1294 passed / 30 deselected | clean | clean |

Live post-deploy re-confirmation (all green): audit "No drift findings";
`get_folder_id('Bradley 1')` = 383795291728 read from the sheet (E1 LIVE);
`--apply` preview 0 adds / 0 skips.

## Decisions made during session

1. **D1 = ADD (Phase 3a: add the two DORMANT columns rather than trim the registry
   or defer).** Alternative considered: TRIM (the brief's default lean — remove the
   two over-declared registry entries, zero columns touched). Rejected: trimming
   removes the operator-visible dropdown on a schema the code already produces
   values for; adding the column is the safer, forward-compatible path. Seth's call.
   Review-driven hardening (M1): idempotency made title-AND-type — a pre-existing
   column with the wrong type raises rather than silently skipping, preventing a
   quiet schema mismatch.

2. **D2 = AUTOMATE (Phase 3b: build the `--apply` flag on `audit_picklist_drift.py`
   rather than document-only).** Alternative considered: document-only (the 06-02
   brief's conservative option; CC recommendation was to build). The ship-and-leave
   gap that let the `Reason` drift go undetected without an apply path was the
   deciding factor. Scope boundary: additive + option-only in v1; prune explicitly
   out of scope (matches `ensure_picklist_options`).

3. **D3 = NOW (E1 cutover: build + seed + flip this session, not deferred).**
   Alternative considered: DEFER — skip Section C entirely; pre-cutover the path is
   pure `BOX_PROJECT_FOLDERS` dict-passthrough (zero behavior change), so waiting
   costs nothing. Chosen NOW because the migration code already shipped inert in
   #149 (06-02, `SHEET_PROJECT_ROUTING == 0`) and completing the cutover this
   session — build the sheet (id 3500842291253124), seed 6 rows from the dict, flip
   the constant, verify the reader reads the sheet — is low-risk and avoids leaving
   E1 half-done. The sheet was built + seeded THIS session (not 06-02). Corrected
   docstring ordering across build/seed/project_routing: `seed` READS
   `SHEET_PROJECT_ROUTING` and raises on the 0 placeholder, so the canonical cutover
   order is build→flip→seed→verify (not build→seed→flip as originally documented).

4. **`brief-validator` pre-check confirmed all code-shape claims before any edits.**
   Fast-forward-pulled `~/its` from `46a5c9a` → `5d25b47` at session start (PRs
   #148/#149/#150 were merged-but-unpulled); venv (Python 3.13.1) survived the
   machine update.

5. **Batch-merge concurrency lesson.** Merging #151 and #152 within 14s of each other
   triggered GitHub Actions concurrency-cancellation — #151's main-CI was cancelled
   (not failed) when #152's push superseded it. Leg 4 was satisfied by explicitly
   re-running `gh run rerun 26895484315`. Separately: branch protection requires
   up-to-date-with-main, so #153 needed `gh pr update-branch` + a CI re-pass before
   merge; repo auto-merge is disabled (`--auto` is a no-op here).

6. **Worktree PYTHONPATH resolution confirmed.** `PYTHONPATH=<worktree>` wins over
   the editable `__editable__.its` finder — the open question in
   `docs/operations/worktree_discipline.md` is now empirically resolved. Relevant
   for any multi-worktree session importing `shared/`.

7. **Multi-worktree pre-merge conflict check.** `git merge-tree` pre-verified all
   three branches land conflict-free in any order (#151/#152 share tech_debt.md +
   reconcile runbook in disjoint regions; #151/#152 ↔ #153 share no files).

## What was NOT touched

- No Invariant 1 or Invariant 2 mechanics changed — all work is schema-additive
  (new columns, flipped constant, new CLI flag). No send-path or AI-path touched.
- `BOX_PROJECT_FOLDERS` dict NOT removed from `shared/defaults.py` — retained as
  fallback; deferred to post-Evergreen-prod.
- Prune path NOT built in Phase 3b v1 — explicitly out of scope.
- `picklist-sync`, `watchdog`, and `weekly-generate` launchd agents NOT loaded
  (daemon survey found them on disk but not running; flagged as operator follow-up).

## Open items handed off

- **Load deferred daemons:** `picklist-sync`, `watchdog`, `weekly-generate` are on
  disk but not loaded. Operator to run `launchctl load` for each (or re-run
  `install.sh`) and confirm they appear in `ITS_Daemon_Health`.
- **Clean ~14 stale worktrees** (`~/its-3a`, `~/its-3b`, `~/its-e1cut`, and
  remainder from the 06-02 cluster). Force-delete is hook-blocked inside CC;
  operator must run from a plain shell: `git worktree remove --force <path>` for
  each, then `git worktree prune`.
- **BOX_PROJECT_FOLDERS dict removal** — deferred to post-Evergreen-production
  confirmation that the sheet is the stable path.

## Gotchas worth recording for future sessions

- **Review subagent path pinning.** One review workflow's synthesizer subagent
  re-verified findings against `~/its` (the main tree) instead of the `~/its-3b`
  worktree and wrongly declared the real committed diff "phantom." Fix: pin all
  review-subagent reads to the worktree path explicitly; tell the synthesizer the
  committed branch diff is ground truth, not the main-tree state.
- **Concurrency cancellation is not a failure.** A cancelled CI run (leg 4) requires
  an explicit re-run; the four-part verify discipline demands leg 4 be satisfied
  even when the cancellation is benign. Do not treat CANCELLED as PASSED.

## Lessons captured to memory

- `session-2026-06-03-picklist-e1-state.md` — updated to reflect all three PRs
  MERGED, E1 LIVE, and the remaining operator follow-ups (daemon loading + worktree
  cleanup).
- `worktree_discipline.md` open question on `PYTHONPATH` vs editable finder —
  resolved empirically this session; update the doc in a follow-on pass.

## Cross-references

- Predecessor session log:
  [`2026-06-02_e1-and-picklist-drift-reconcile.md`](2026-06-02_e1-and-picklist-drift-reconcile.md)
- Op Stds v16 §30 (SDK-vs-Live integration test discipline)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD)
- `docs/operations/worktree_discipline.md` — PYTHONPATH finding
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg-4 re-run pattern
- `docs/tech_debt.md` — Phase 3a/3b entries now RESOLVED
