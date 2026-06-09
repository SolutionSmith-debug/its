---
type: session_log
date: 2026-06-09
status: closed
related_prs: [232, 233, 234, 235]
workstream: safety_portal
tags: [safety-portal, compile-now, orphaned-reports, week-sheet, intake, part-b, part-c, config-gated, live-state-correction, worktree-venv]
---

# Session log — Part B: Compile Now poller + Part C: Orphaned Reports reroute (PRs #232–#235)

Second half of the 2026-06-09 session. Continued `cc-brief_…hardening-and-compile-now…`:
Parts B (on-demand Compile Now daemon) and C (Orphaned Reports reroute for portal submissions
with an unknown/inactive job), plus an operator-requested rollup-placeholder pre-create fix.
PRs #232–#235 all four-part-verified. Part C is live on the running daemon as of main
`fbeef44`. The first-half session log (Parts D + A) is
[`2026-06-09_publish-ci-gate-hardening-and-part-a.md`](2026-06-09_publish-ci-gate-hardening-and-part-a.md).

## PRs landed

### PR #232 — Part B: on-demand Compile Now poller (`5b7affc`)

`weekly_generate` compiles the canonical Sat→Fri packet only on its Friday 14:00 launchd
fire. Part B adds `safety_reports/compile_now_poll.py` (launchd
`org.solutionsmith.its.compile-now-poll`, 90s) that compiles a TRIGGERED job-week on demand,
REUSING `weekly_generate._compile_job_week` — no second compile path.

Key implementation details:

- `weekly_generate._compile_job_week` gained an additive `selection` param (default `None` →
  behaviour-identical to the Friday fire; non-None = caller-supplied row-ID list for
  partial compile). The B1 `COL_COMPILE_NOW` checkbox is row-type-dependent: on a Rollup row
  it is the trigger; on a Submission row it opts that submission into the compile selection.
- `week_sheet.selected_submission_row_ids` collects checked Submission rows;
  `week_sheet.clear_compile_now` clears the trigger checkbox after dispatch.
- The existing compile path already self-clears the Rollup trigger on success
  (`upsert_rollup_row`) and leaves it set + routes to Review Queue on failure (raises before
  the upsert). The poller is therefore a thin frequent-polling wrapper, not a reimplementation.
- Single-flight via `fcntl` file lock. Fail-loud (raises on compile error; poller logs
  CRITICAL via `@its_error_log`). Capability-gated; `tests/test_capability_gating.py`
  updated. Watchdog Check-C marker `safety_compile_now_poll` registered.
- launchd plist (`scripts/launchd/org.solutionsmith.its.compile-now-poll.plist`) + install.sh
  interval wiring (default 90s); `plutil -lint` clean.
- DEFERRED (tech-debt): ITS_Daemon_Health heartbeat row for `compile_now_poll` — folds into
  the tracked `shared/heartbeat.py` extraction.

- pytest: 1617 passed / 44 deselected
- mypy: clean (197 source files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #232 — four-part verify clean
- state: MERGED
- mergedAt: (2026-06-09)
- mergeCommit: 5b7affc
- main CI on merge commit: SUCCESS

---

### PR #233 — Rollup placeholder pre-create (`2b9e0b51`)

Operator-requested fix. `week_sheet.ensure_week_sheet` previously wrote the Rollup row only
at first compile (inside `weekly_generate._compile_job_week`). For a never-compiled week,
the Compile Now trigger checkbox was invisible until the Friday fire or a first partial
compile. PR #233 moves an empty Rollup row write to the `ensure_week_sheet` CREATE path
(best-effort — never aborts sheet creation; `compiled_at=""` keeps the no-new-docs skip
honest). The Compile Now checkbox is now visible immediately when a new week sheet is created.

- pytest: 1619 passed / 44 deselected
- mypy: clean (197 source files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #233 — four-part verify clean
- state: MERGED
- mergedAt: (2026-06-09)
- mergeCommit: 2b9e0b51
- main CI on merge commit: SUCCESS

---

### PR #234 — Part C: route job-orphan portal submissions to Orphaned Reports (`b4acee2`)

A portal submission whose Job ID is unknown (`job_not_found`) or not Active (`job_inactive`)
previously routed to the generic ITS_Review_Queue. Part C introduces a dedicated Orphaned
Reports sheet + Box folder path for these cases.

**Live-state correction:** the brief cited `intake.py:656` — the `resolve_project` call on
the EMAIL path. The live portal flow is `process_portal_submission`. The reroute was applied
at the correct branch points: `~line 1712` (`job_not_found`) and `~line 1719`
(`job_inactive`) in `process_portal_submission`, via a new `_portal_orphan` helper.

**C1 migration** — `scripts/migrations/build_orphaned_reports_sheet.py`: find-or-create the
Orphaned Reports sheet under `FOLDER_SAFETY_PORTAL`. `SHEET_ORPHANED_REPORTS` added to
`shared/sheet_ids.py` (defaults to `0` = OFF).

**C2** — Box folder for orphaned reports: find-or-create at runtime (same lazy pattern as
week folders).

**C3 reroute** — `_portal_orphan` renders the submission PDF (reuses `form_pdf`), files it
to Box with version-on-conflict, writes an Orphaned Reports sheet row (`Status=Pending`). A
structurally-bad submission (PDF render failure, Box failure) falls back to ITS_Review_Queue
with a note — it does not silently drain. An empty `job_id` (no job ID submitted at all) is
classified as `no_job_id` → stays in ITS_Review_Queue (not orphaned; needs human assessment).

**CONFIG-GATED:** `SHEET_ORPHANED_REPORTS` defaults to `0`. Until the operator activates by
setting it to the live sheet ID, the reroute is a no-op on the live intake path. Send-free
(`intake.py` stays in `GATED_SCRIPTS`).

- pytest: 1620 passed / 44 deselected
- mypy: clean (198 source files)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #234 — four-part verify clean
- state: MERGED
- mergedAt: (2026-06-09)
- mergeCommit: b4acee2
- main CI on merge commit: SUCCESS

---

### PR #235 — Part C activation (`fbeef44`)

Operator ran `python scripts/migrations/build_orphaned_reports_sheet.py` →
Orphaned Reports sheet created (`id=2577084374273924`). Then flipped
`SHEET_ORPHANED_REPORTS` from `0` to `2577084374273924` in `shared/sheet_ids.py`.

The first CI run FAILED: `test_orphan_falls_back_to_review_when_disabled` was written to
assert the fallback path when `SHEET_ORPHANED_REPORTS == 0`, relying on the module default.
Flipping the constant made the test red (the same "test couples to a live config-value
default" failure class identified during Part D). Fixed by pinning
`mocker.patch.object(intake.sheet_ids, "SHEET_ORPHANED_REPORTS", 0)` inside that test so
it validates the disabled-path in isolation regardless of the module's live value. CI then
clean. `~/its` synced to `fbeef44` — Part C is now LIVE on the running `portal_poll`/intake
daemon (picks up the new constant on next cycle).

- pytest: 1621 passed / 44 deselected
- mypy: clean (198 source files)
- ruff: clean
- portal: workerd 106 / jsdom 22 / typecheck clean (unchanged — Parts B/C are Python-only)
- main-branch CI on merge commit: SUCCESS

PR #235 — four-part verify clean
- state: MERGED
- mergedAt: (2026-06-09)
- mergeCommit: fbeef44
- main CI on merge commit: SUCCESS

---

## Overall final state (main `fbeef44`)

four-part verify clean (all 4 PRs: state=MERGED + mergedAt + mergeCommit + main-branch CI SUCCESS)

- pytest: 1621 passed / 44 deselected (integration `-m integration` excluded)
- mypy: clean (198 source files)
- ruff: clean
- portal: workerd 106 / jsdom 22 / typecheck clean
- main-branch CI on each merge commit: SUCCESS

## Decisions made during session

1. **Reuse `weekly_generate._compile_job_week` rather than a second compile path (Part B).**
   - Decision: extend `_compile_job_week` with an additive `selection` param and call it from
     the new poller.
   - Alternative considered: duplicate the compile logic in `compile_now_poll.py`.
   - Rationale: a second path doubles the surface area for compile-path drift and makes any
     future weekly_generate change a two-file update. The `selection=None` default keeps the
     Friday-fire behaviour-identical; no existing test required changes for the new param.

2. **Rollup trigger visibility fix placed in `ensure_week_sheet` CREATE path, not in the
   poller (PR #233).**
   - Decision: write the empty Rollup placeholder row at sheet-creation time (best-effort,
     never aborts intake).
   - Alternative considered: let the poller skip gracefully when no Rollup row exists and
     document the first-compile dependency.
   - Rationale: the operator discovered the UX friction during session — the checkbox must
     exist before the user opens the sheet, not only after the first Friday fire. Best-effort
     (never aborts) was the safe contract: sheet creation is intake-critical; the Rollup row
     is not.

3. **Live-state correction: Part C reroute in `process_portal_submission`, not
   `resolve_project` (email path).**
   - Decision: apply the reroute in `process_portal_submission` at the `job_not_found` and
     `job_inactive` branches.
   - Alternative considered: apply the reroute in `resolve_project` as the brief specified.
   - Rationale: the brief was written before the portal-transport pivot; `resolve_project` is
     the legacy EMAIL intake path (dormant for safety reports). The live portal flow enters
     `process_portal_submission` directly, bypassing `resolve_project`. Applying the reroute
     in the wrong branch would have left the live path unchanged.

4. **Empty `job_id` treated as `no_job_id` → stays in ITS_Review_Queue, not Orphaned
   Reports.**
   - Decision: guard `_portal_orphan` so a submission with no job ID at all does not get
     classified as an orphan.
   - Alternative considered: route `no_job_id` to Orphaned Reports alongside `job_not_found`
     / `job_inactive`.
   - Rationale: an empty job ID is structurally ambiguous — it might be a form-fill error,
     a test submission, or a malformed request. `job_not_found` and `job_inactive` are
     semantically meaningful: a job ID was supplied but the job doesn't exist or isn't
     Active. Routing the structurally-missing case to a human-review queue is the safer
     default.

5. **Config-gated activation (SHEET_ORPHANED_REPORTS=0) makes the Part C merge a no-op on
   the live intake path.**
   - Decision: merge Part C (PR #234) with the constant at 0 so the live daemon is
     unaffected, then activate via a separate PR (#235) after the migration runs.
   - Alternative considered: merge Part C with the sheet ID pre-populated.
   - Rationale: the migration creates the sheet and returns its ID; that ID is not known at
     PR-#234 commit time. The config-gated pattern also allows safe revert: setting
     `SHEET_ORPHANED_REPORTS` back to 0 disables the reroute without a code change.

6. **"Test couples to a live config-value default" — confirmed class, not a one-off (#235).**
   - Decision: pin the disabled-path via `mocker.patch.object` rather than relying on the
     module default; fix the test, not the approach.
   - Rationale: this is the same failure class caught in Part D (PR #222 and PR #227).
     Whenever a test exercises a "when disabled" branch, it must mock the feature flag rather
     than assuming the module default is 0 — because an activation PR will flip that default
     and red-CI the test. Pattern now repeated three times; worth the explicit entry.

## Open items / next session

- **Portal deploy (Part D timestamps + Part A):** `cd ~/its/safety_portal && npm run deploy`.
  The `fmtTime` fix (D3), stable-UUID (A1), and D1 prune cron (A3) are code-merged but not
  deployed. Operator action.
- **Load the compile-now daemon (Part B):** until loaded, watchdog Check C WARNs on the
  `safety_compile_now_poll` marker.
  ```
  bash ~/its/scripts/launchd/install.sh load org.solutionsmith.its.compile-now-poll
  ```
- **Orphaned Reports sheet column styling (cosmetic):** the C1 migration creates the sheet
  with default column widths. Cosmetic — sheet is fully functional. MINOR.
- **compile_now_poll ITS_Daemon_Health row (B3):** deferred into the tracked
  `shared/heartbeat.py` extraction.
- **req-8 / PR #225 (`incident-report-test` form):** still OPEN. If the test form is
  unwanted, close it; it is a clean form (`variant_label:null`) and would publish
  successfully if re-triggered.
- **CSP enforce flip** (carried from `2026-06-08_admin-dashboard-audit-and-security-hardening.md`):
  still held pending a live signature-capture smoke + zero console-violation confirm.
- **Stale worktrees** (`~/its-*` from prior sessions): operator cleanup; force-delete is
  hook-blocked in CC.

## What was NOT touched

- **`~/its-blueprint`:** exec-repo-only session. No doctrine, mission, brief, or reference
  files touched.
- **Invariant 1 (External Send Gate):** `intake.py` stays in `GATED_SCRIPTS`;
  `_portal_orphan` is intake-path code, no send capability. `tests/test_capability_gating.py`
  confirms.
- **Invariant 2 (Adversarial Input Handling):** no changes to untrusted-content tagging or
  anomaly-logger paths.
- **Part D and Part A of the brief:** completed in the first-half session (PRs #222, #224,
  #227, #228, #230). Not re-touched.
- **`weekly_generate.py` Friday-fire behaviour:** the `selection=None` default in
  `_compile_job_week` is behaviour-identical to the pre-PR state. No change to the weekly
  compile path.
- **`weekly_send.py` / `weekly_send_poll.py`:** not touched.
- **Email intake path (`resolve_project`):** the brief cited this path but Part C's reroute
  applies only to `process_portal_submission`. The email path is dormant for safety reports
  and was deliberately left unchanged.
- **Evergreen production tenant:** all intake-path changes activate against the mirror (same
  daemon) via the `SHEET_ORPHANED_REPORTS` constant.

## Worktree note

Parts B and C were built in per-task git worktrees, each with its own virtual environment.
The editable install (`pip install -e .`) in a worktree resolves imports to `~/its` (the root
checkout) even when `PYTHONPATH` points to the worktree — Python source edits in the worktree
are not visible to the worktree's own venv unless a separate venv is created inside the
worktree and the editable install is re-run there. See new memory entry
`reference_worktree-venv-for-python-source-edits`.

## Lessons captured to memory

- **`reference_worktree-venv-for-python-source-edits`** (new): worktrees for Python source
  edits require a dedicated venv + editable install inside the worktree; the shared editable
  install from `~/its` resolves imports there regardless of `PYTHONPATH`.
- **"Test couples to a live config-value default"** class confirmed for the third time (Part D
  PR #222, Part D PR #227, Part C PR #235). Pattern: any "when disabled" test must
  `mocker.patch.object` the feature flag rather than trusting the module default.

## Cross-references

- First-half session log (Parts D + A):
  [`2026-06-09_publish-ci-gate-hardening-and-part-a.md`](2026-06-09_publish-ci-gate-hardening-and-part-a.md)
- Prior session (Phase-2 Form Manager + publish pipeline, PRs #203–#218):
  [`2026-06-09_safety-portal-phase2-form-manager-publish-pipeline.md`](2026-06-09_safety-portal-phase2-form-manager-publish-pipeline.md)
- `safety_reports/compile_now_poll.py` — Part B poller (new file)
- `safety_reports/weekly_generate.py` — `_compile_job_week` additive `selection` param
- `safety_reports/week_sheet.py` — `selected_submission_row_ids`, `clear_compile_now`,
  `ensure_week_sheet` Rollup placeholder pre-create
- `safety_reports/intake.py` — `process_portal_submission` `_portal_orphan` reroute
- `scripts/migrations/build_orphaned_reports_sheet.py` — C1 migration (new file)
- `shared/sheet_ids.py` — `SHEET_ORPHANED_REPORTS` (new constant)
- `scripts/launchd/org.solutionsmith.its.compile-now-poll.plist` — Part B daemon plist
- `tests/test_capability_gating.py` — updated for `compile_now_poll.py`
- `docs/tech_debt.md` — compile_now_poll ITS_Daemon_Health row (B3) deferred
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- Op Stds v16 §1 (External Send Gate — intake stays in GATED_SCRIPTS)
- Op Stds v16 §43 (successor-remediation runbook; compile_now_poll + orphaned-reports
  activation to be documented there)
