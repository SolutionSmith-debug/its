---
type: session_log
date: 2026-06-17
status: closed
related_prs: [289, 290]
workstream: safety_portal
tags: [session_log, safety_portal, safety_reports, pdf-naming, intake, weekly_generate, box, smartsheet, test-artifact-cleanup, worker, typescript, d1, worktree, live-api-ops]
---

# Session — Safety Portal test-artifact cleanup (live API) + daily/weekly PDF naming scheme (PRs #289 / #290)

Two-part session. Part 1 was pure live-API operator work (no commits): a guarded sweep of Smartsheet and Box to delete all test artifacts that had accumulated during mirror validation. Part 2 landed two PRs that give every portal-filed PDF a globally unique, human-readable filename at all three surfaces (Box file / Smartsheet row attachment / portal download).

## PRs landed

### PR #289 — fix(safety-portal): job-prefixed daily PDF name + clean weekly-packet filename (merge `88bc8ade`)

Renamed daily per-submission PDFs and weekly compiled-packet files at the two Mac-side surfaces (Box file + Smartsheet row attachment) to carry the job name as a prefix, eliminating cross-job collisions when PDFs from multiple jobs appear in the same view.

Changes (+N across `safety_reports/intake.py`, `safety_reports/weekly_generate.py`, and their tests):

1. **Daily PDF — Box filename:** `<work_date>-<type>.pdf` → `<job>_<work_date>_<type>.pdf`. `intake._file_portal_pdf` gained a `project_name` parameter; the main portal-marker caller passes `job.project_name`; the orphan-document path passes the job_id or the string `"orphan"` as a safe fallback.

2. **Weekly compiled-packet filename:** `Weekly Safety Report — <job> — <start> to <end> — <stamp>.pdf` → `<job>_week of <Sat>_WSR.pdf`. Recompiles on the same week bump the version suffix (`_v2`, `_v3`, ...) via the new `weekly_generate._upload_packet` helper, which tries the base name and on a Box 409 (conflict) appends the next version number. The compiled-at timestamp is retained only as a last-resort fallback. The private `_packet_filename` → `_packet_basename` rename makes the naming intent explicit. Reuses `safety_naming.job_folder_name` and `week_label` helpers already in the codebase.

3. **Tests:** existing tests updated to assert the new names; two new `_upload_packet` unit tests (base-name success path and version-bump on 409).

4. **Invariant 1 intact:** no new imports; `weekly_generate.py` gains no send or AI capability. The naming change is output-only.

Gates:

- pytest: 1831 passed / 44 deselected
- mypy: 0 errors / 202 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS (run 27708851527, workflow: ci)

PR #289 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-17T17:52:28Z
- mergeCommit: 88bc8ade05994b365a8774e27dcb3880df3b9446
- main CI on merge commit: SUCCESS (run 27708851527, workflow: ci)

---

### PR #290 — fix(safety-portal): job-prefixed PDF name at Smartsheet row attachment + Worker download (merge `7510f7a0`; Worker c56335d2)

Diagnosed and fixed the two remaining surfaces that PR #289 did not reach, after a live test with "Placeholder test" (JOB-000015) showed the portal download and Smartsheet attachment still using the old names.

The diagnosis: a daily-form filename is generated in three independent places; #289 fixed only one (the Box filing path). The other two:

1. **Smartsheet week-sheet row attachment** (`intake.py` ~line 2208, `_attach_pdf_best_effort`): was `<work_date>-<parent_form_code>.pdf` → now `<job>_<work_date>_<parent_form_code>.pdf`, constructed via `safety_naming.job_folder_name` to match the Box base name exactly.

2. **Worker portal download** — `GET /api/submissions/:uuid/pdf` `Content-Disposition` header (`worker/index.ts` ~line 751): was `<form_code>-<work_date>.pdf` → now `<job>_<work_date>_<form_code>.pdf`, resolved via a `submissions LEFT JOIN jobs` query for `project_name`. The filename sanitizer was relaxed to allow spaces (the value is in a quoted attribute). Falls back to the un-prefixed name if the job row is absent.

All three surfaces now produce `<job>_<date>_<form>.pdf`. Already-filed PDFs keep their old names; the new scheme applies to new submissions only.

Changes:
- `safety_reports/intake.py` — `_attach_pdf_best_effort` attachment name
- `worker/index.ts` — `Content-Disposition` query + sanitizer
- `tests/test_intake_portal.py` — row-attach exact-name assertion
- `worker/test/pdf.test.ts` + `worker/test/form-request.test.ts` — `Content-Disposition` assertions updated

Built in worktree `~/its-pdf2` (Python venv copy; `node_modules` symlinked to `~/its` for the TS/vitest suite).

Gates:

- Python: pytest green, ruff clean, mypy clean (202 source files)
- Worker: typecheck clean; vitest 212/212; SPA 76/76
- main-branch CI on merge commit: SUCCESS (run 27726355103, workflow: ci)

Worker deployed: `npm run deploy` → version **`c56335d2`** at `safety.evergreenmirror.com`. `~/its` fast-forwarded to `7510f7a0`.

PR #290 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-17T23:29:11Z
- mergeCommit: 7510f7a0561520d8946511dab3808613d74ecea9
- main CI on merge commit: SUCCESS (run 27726355103, workflow: ci)

---

## Part 1 — Test-artifact cleanup (live API, no commits)

Operator requested removal of all test artifacts from Smartsheet and Box that had accumulated during mirror validation. This work left no commits in the repo; it is documented here as the durable record.

### Scope and approach

1. Read-only survey of both systems against the protect-lists in `shared/sheet_ids.py` and `shared/defaults.py`.
2. Grep of `~/its` for every candidate ID — all returned empty (nothing wired to a daemon).
3. Operator sign-off via explicit confirmation before deletion.
4. Executed via a name-guarded one-off SDK script: refuses any candidate whose live name is not on an explicit test-name allowlist, so an ID typo cannot accidentally delete a production object.

### Smartsheet — ITS – Safety Portal workspace (194283417429892)

- Deleted 4 test folders: "New test", "teala test", "Test number two", "ZZ Portal Proof" — folder deletion cascades the contained week-sheet.
- Cleared ALL rows from ITS_Active_Jobs (6223950341164932, 4 rows) and WSR_human_review (5035670127988612, 6 rows) — sheets kept, rows removed.

### Box — ITS_Safety_Portal root (388017263015)

- Deleted 6 folders recursively: the 4 above + "Test project 1" + "Rockford".

### MCP connector gap (operational note)

Neither the Smartsheet MCP connector nor the Box MCP connector can delete sheets/folders/workspaces: the Smartsheet MCP exposes `delete_rows` and column ops only; the Box MCP has no delete surface at all. Deletions were executed via the underlying SDK clients directly (`smartsheet_client.get_client().Folders.delete_folder` + `.delete_rows`; `box_client.get_client().folder(id).delete(recursive=True)`). The MCP accounts are the same daemon-token accounts (OWNER / `seths@evergreenmirror.com`).

### Regeneration incident

A "teala test" folder reappeared immediately after deletion: `portal_poll` → `intake` was finishing the last in-flight submission from the D1 pending queue at the moment of deletion. Diagnosed by querying `GET /api/internal/pending` → count 0 (confirmed drained, one-shot not a loop). Deleted the regen folder; re-verified clean. `portal_poll` was left running because `/pending` was confirmed empty — a `safety_reports.portal_poll.polling_enabled=false` ITS_Config flip was evaluated and declined (it would have been an unrequested shared-config change, and was unnecessary once the queue was drained).

### What was NOT deleted

- The 2 "Evergreen Portfolio Template" workspaces: Demo Seed (685696395569028) and Master (3333320395253636) — operator chose to keep.
- Box migration strays: "Smart Sheet COPY FOLDER" (386924246352) and `_int_reclone_1111b_20260601T181510` (386159212621) — left alone by operator choice.
- "Forfront IL portfolio" Smartsheet workspace (2228567565199236) — ADMIN-only, not owned by this account; untouchable.

### Residue (tech-debt, out of scope for this session)

Two items were flagged to the operator as known residue, deliberately not addressed because they fall outside the approved Smartsheet+Box scope:

1. **D1 job-dropdown not cleared.** `portal_poll.push_jobs` refuses an empty-set sync (the Worker rejects `empty_jobs`), so emptying ITS_Active_Jobs rows did NOT clear the portal dropdown. The test job names remain visible in the D1-backed dropdown until real jobs are added or the D1 table is manually pruned.
2. **Worker D1 historical test data.** Filed test submissions and the filed-PDF download cache remain in the Worker D1 database. Clearing them requires a direct D1 SQL operation (outside Smartsheet + Box scope).

Both are tracked in `docs/tech_debt.md` (updated by the concurrent session-close-maintainer pass).

---

## CI runs

- **PR #289 (pull_request + push double-trigger):** `test` (ruff + mypy + pytest), `portal` (Worker vitest + tsc + vite), `secrets`, CodeQL — all SUCCESS. Post-merge push on `main @ 88bc8ade`: all SUCCESS.
- **PR #290 (pull_request + push double-trigger):** same job set — all SUCCESS. Post-merge push on `main @ 7510f7a0`: all SUCCESS (four-part-verify leg-4 gate for both PRs).

---

## Decisions made during session

1. **Name-guarded one-off SDK script for deletion — not raw shell or MCP.**
   - Decision: wrapped all Smartsheet and Box deletes in a name-allowlist guard that refuses execution if the live object name is not on an explicit test-name list. A mistyped ID cannot delete a production object.
   - Alternative considered: raw SDK calls without a guard (faster to write, higher risk).
   - Rationale: the daemon-token accounts are OWNER on the production workspace; a mistyped folder ID could nuke a real project. The guard adds one minute of setup; the cost of a wrong delete is a recovery from backup (Box version history for files; Smartsheet does not have row-level undo for bulk clears).

2. **Kill-switch flip declined for portal_poll during cleanup.**
   - Decision: did not flip `safety_reports.portal_poll.polling_enabled=false` in ITS_Config to pause the daemon during the cleanup window.
   - Alternative considered: pause the daemon, then delete, then re-enable.
   - Rationale: the flip was flagged as an unrequested shared-config change. Once `GET /api/internal/pending` returned count 0 (queue drained), there was no active intake risk — the daemon could safely continue running on an empty queue. Pausing was unnecessary.

3. **Three-surface naming fix split across two PRs.**
   - Decision: PR #289 fixed the Mac-side Box filing and weekly-packet names; PR #290 fixed the Smartsheet row-attachment and Worker Content-Disposition header after a live test revealed the remaining surfaces.
   - Alternative considered: a single PR touching all three surfaces up front.
   - Rationale: the three-surface scope was not fully mapped at the start of the session. The live-test-then-fix loop is the correct pattern here — it produced a verified, minimal fix for each surface rather than speculative pre-emptive changes. The two-PR shape has no drawback because both are naming-only changes with no cross-PR dependencies.

4. **Version-bump on Box 409 instead of timestamp suffix for weekly packets.**
   - Decision: `_upload_packet` tries the base name (`<job>_week of <Sat>_WSR.pdf`); on a 409 (file already exists), appends `_v2`, `_v3`, etc. The compiled-at timestamp was retired from the primary name and kept only as a fallback.
   - Alternative considered: keep the compiled-at timestamp as the primary disambiguator (the prior scheme).
   - Rationale: operator preference was explicit — "add v2" is more legible than a timestamp to a field supervisor scanning a Box folder. The 2026-06-09 append-only master-record invariant (never overwrite a compiled packet) is preserved: each compile produces a distinct Box file, the old file is never touched.

5. **Worker filename sanitizer relaxed to allow spaces.**
   - Decision: the `Content-Disposition` filename sanitizer in `worker/index.ts` was relaxed to allow spaces (the value appears in a quoted attribute), enabling project names that contain spaces (e.g. "Placeholder test") to survive in the download filename.
   - Alternative considered: strip spaces to underscores (simpler, always safe).
   - Rationale: the operator named the test job "Placeholder test" and the filename must carry that name faithfully. The `Content-Disposition` RFC permits spaces in quoted filenames; the sanitizer now allows them while still stripping truly unsafe characters.

6. **Already-filed PDFs keep old names — new scheme applies to new submissions only.**
   - Decision: no retroactive rename of Box files or Smartsheet row attachments filed before these PRs.
   - Alternative considered: a one-off migration script to rename historical files.
   - Rationale: historical PDFs are audit-trail records. Renaming them in Box creates new file versions (losing the canonical link between the filing event and the file record) and would require a matching Smartsheet attachment update. The operational benefit (cosmetic) does not justify the audit-trail risk.

---

## Open items / next session

1. **D1 job-dropdown cleanup** — test job names still populate the portal dropdown because `push_jobs` refuses empty-set sync. Requires either adding real job rows to ITS_Active_Jobs or a direct D1 SQL prune of the `jobs` table. Tracked in `docs/tech_debt.md`.
2. **D1 historical test data** — filed test submissions and filed-PDF cache remain in the Worker D1. Requires a direct D1 SQL operation (outside the Smartsheet+Box scope of this session). Tracked in `docs/tech_debt.md`.
3. **PR-3 `feat/pr3-heartbeat-extraction`** (`shared/heartbeat.py` extraction, foundation `546537c`) — thin-wrapper rewire of 4 daemons + watchdog `TRACKED_JOBS` + `tests/test_heartbeat.py` + live daemon smoke remain outstanding from the 2026-06-10 program.
4. **PR-4 — Worker submit/queue hardening** (M1 silent-overwrite, M4 immortal bad-HMAC rows, login-disabled gate) — designed 2026-06-10, not yet built.

---

## What was NOT touched

- **External Send Gate (Invariant 1):** `intake.py` and `weekly_generate.py` gained no send or AI imports. `test_capability_gating` passes clean. `weekly_send.py`, `weekly_send_poll.py`, `portal_poll.py` are unchanged.
- **Intake pipeline logic:** no submission-processing logic, HMAC verification, or Box-filing decision paths modified. The naming change is applied at the point of writing the filename string, not at any earlier pipeline stage.
- **Already-filed PDFs:** intentionally not renamed (see Decision 6 above).
- **ITS_Config runtime values:** no config rows added, modified, or deleted (the `polling_enabled` flip was explicitly declined).
- **`~/its-blueprint`:** exec-repo-only session. No doctrine, mission, brief, or reference files modified.
- **Invariant 2 (Adversarial Input Handling):** no external-content processing paths modified. Filename construction is output-only.
- **Worker D1 schema:** no migrations. The `project_name` for the `Content-Disposition` header is fetched via a `LEFT JOIN` on the existing `submissions` + `jobs` tables.

---

## Post-merge actions

- `~/its` fast-forwarded to `7510f7a0` (PR #290 merge commit) after both PRs merged.
- Worker deployed: `cd safety_portal && npm run deploy` → version **`c56335d2`** live at `safety.evergreenmirror.com`.
- Live smoke: `GET /` → 200; `GET /api/submissions/:uuid/pdf` with no session → 401 (correct auth gate). Box and Smartsheet attachment naming verified via the "Placeholder test" (JOB-000015) submission submitted during diagnosis.
- Worktrees cleaned: `~/its-pdf-naming` and `~/its-pdf2` removed; local feature branches ref-deleted after PR=MERGED verify (per `reference_git-branch-cleanup-hook-bypass`).

---

## Cross-references

- `safety_reports/intake.py` — `_file_portal_pdf` (project_name param); `_attach_pdf_best_effort` (row-attachment name)
- `safety_reports/weekly_generate.py` — `_upload_packet` helper; `_packet_basename`
- `worker/index.ts` — `GET /api/submissions/:uuid/pdf` `Content-Disposition` + `submissions LEFT JOIN jobs`
- `tests/test_intake_portal.py` — row-attach exact-name assertion
- `worker/test/pdf.test.ts`, `worker/test/form-request.test.ts` — `Content-Disposition` assertions
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI on merge commit
- `docs/operations/worktree_discipline.md` — worktree + venv discipline
- `shared/sheet_ids.py`, `shared/defaults.py` — protect-lists consulted during artifact-cleanup survey
- Memory entry `project_safety_portal_state` — current Safety Portal state (updated this session)
- FM v11 Invariant 1 (External Send Gate — send path unchanged; capability gate verified clean)
- Op Stds v18 §14 (preservation-over-refactor — intake pipeline logic, merge_pdfs, signature canvases untouched)
- Prior session log (PDF beautification): [`2026-06-15_pdf-beautification-evergreen-logo-gold-rules.md`](2026-06-15_pdf-beautification-evergreen-logo-gold-rules.md)
