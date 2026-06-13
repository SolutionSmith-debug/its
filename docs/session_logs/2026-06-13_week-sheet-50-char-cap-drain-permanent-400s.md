---
type: session_log
date: 2026-06-13
status: closed
related_prs: [283]
workstream: safety_portal
tags: [session_log, safety_portal, safety_reports, week-sheet, smartsheet-validation, intake, portal-poll, live-diagnosis, live-smoke, 50-char-cap, permanent-400, drain-review-queue]
---

# Session — fix(safety-portal): bound week-sheet name to Smartsheet's 50-char cap + drain permanent 400s

Live diagnosis of a stuck portal submission (JOB-000013) traced to an unbounded sheet-name compose in `week_sheet.py` and a mis-classification of HTTP 400 as transient in `intake.py`. Fix: truncate the project prefix at compose time, promote HTTP 400 to a new typed `SmartsheetValidationError`, drain that class to the Review Queue (permanent, never retried), and ship a §43 runbook row.

## PRs landed

### PR #283 — fix(safety-portal): bound week-sheet name to Smartsmith's 50-char cap + drain permanent 400s (merge `e75c5a7`)

A field-PM submitted a Safety Portal form for JOB-000013 (project name "I don't know project name Montgomery", 36 chars). The per-job Smartsheet folder was created but no week-of sheet, no Box folder, and no rendered form PDF appeared. Diagnosis: `week_sheet.week_sheet_name` composes `"<project> — week of <Sat>"` (fixed 21-char suffix) with no length cap → 36-char project name → 57-char sheet name → `create_sheet_in_folder` returned HTTP 400 errorCode 1041 ("sheet.name must be 50 characters or less"). The per-job folder is created before the week sheet, so everything downstream (sheet, Box mirror-tree folder, PDF render, submission row) was unreached. Secondary bug: `intake.process_portal_submission` mis-classified the 400 as a transient `SmartsheetError` → status="error" → the submission re-pulled on every 60s cycle, spamming ITS_Errors indefinitely.

Three code changes plus a runbook:

1. **`safety_reports/week_sheet.py`** — `SHEET_NAME_MAX = 50`; `week_sheet_name` truncates the project prefix to `SHEET_NAME_MAX - len(WEEK_SUFFIX)`, preserving the week-label suffix whole. Names at or below 50 chars are byte-identical to the old result, so existing sheets are matched on find-or-create without re-creates.

2. **`shared/smartsheet_client.py`** — new typed `SmartsheetValidationError` (subclasses `SmartsheetError`; `shouldRetry = False`) raised on HTTP 400 in both translate paths (SDK response + REST error-dict). Callers that need to distinguish permanent structural failures from transient ones now have a typed hook.

3. **`safety_reports/intake.py`** — `process_portal_submission` catches `SmartsheetValidationError` before the generic `SmartsheetError` handler, drains it to the Review Queue (`reason=smartsheet_validation`) and returns without re-queuing. The generic `SmartsheetError` path is unchanged (transient, retries).

4. **`safety_reports/README.md`** — §43 runbook row: symptom (Review Queue row `reason=smartsheet_validation`), low-class repair steps (shorten the job's project name in ITS_Active_Jobs to ≤29 chars, re-file the submission via `poll_once()`), and explicit Tier-3 escalate-to-Seth boundary (any `SmartsheetValidationError` not caused by sheet-name length).

Live smoke (mirror): the stuck submission `51ecb7cc-e0d9-4e52-aa96-a133bf6066fe` was filed end-to-end after unloading the launchd daemon, running `poll_once()` from a worktree venv with the patched code, and confirming: week sheet "I don't know project name Mon — week of 2026-06-13" (50 chars, Smartsheet id 3271853182242692), Box mirror-tree PDF at `https://app.box.com/file/2283463171068`, submission row "2026-06-13 — Visitor Sign-In", D1 queue drained via `mark-filed`. Live daemon reloaded and confirmed cycling clean (scanned=0 errors=0) after fast-forwarding `~/its` to the merge commit.

- pytest: 1823 passed / 44 deselected (3 skipped in merge-commit CI run)
- mypy: 0 errors / 201 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

PR #283 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-13T18:59:21Z
- mergeCommit: e75c5a7367aa59be1f2305852fe838159b0254e6
- main CI on merge commit: SUCCESS (run 27476063282, workflow: ci)

## Decisions made during session

1. **Truncate the project prefix, not the week-label suffix.**
   - Decision: when the composed sheet name exceeds 50 chars, truncate the project prefix (left side), keeping the `" — week of <Sat>"` suffix whole.
   - Alternative considered: truncate from the right (clipping the week label) or truncate symmetrically.
   - Rationale: the per-job Smartsheet folder already carries the full project name; within that folder, the week label is the only part that disambiguates rows across weeks. Truncating the week label would cause find-or-create to collide across two different weeks for any job whose truncated prefix is identical. Truncating the prefix loses no identity that is not already present in the folder context, and the truncation is deterministic (same prefix → same truncated prefix → find-or-create matches correctly on the second pull).

2. **Classify HTTP 400 as a typed PERMANENT error (`SmartsheetValidationError`, `shouldRetry = False`).**
   - Decision: promote HTTP 400 to a new typed exception rather than leaving it folded under the generic `SmartsheetError` (which is treated as transient and retried).
   - Alternative considered: catch the raw `SmartsheetError` in `intake.py` and inspect `error_code == 1041` inline without a new type.
   - Rationale: HTTP 400 from Smartsheet is structurally a permanent failure — the request is malformed or violates a server-side constraint, and re-sending it will always produce the same result. Keeping it under the generic transient exception caused the infinite 60s re-pull loop and ITS_Errors spam observed in the live incident. A typed `SmartsheetValidationError` (a) gives callers a typed hook without error-code inspection at each call site, (b) documents the permanent/transient boundary in the shared client's exception hierarchy, and (c) generalises correctly to any future HTTP 400 from Smartsheet that is not sheet-name-specific.

3. **Live smoke before `~/its` fast-forward: unload daemon → `poll_once()` from worktree venv → reload.**
   - Decision: run the end-to-end smoke by unloading the launchd daemon, executing `poll_once()` from a worktree-pinned venv with the patched code, verifying the filing artifacts (sheet, Box PDF, submission row, D1 mark-filed), then reloading the daemon against the merge commit.
   - Alternative considered: reload the daemon immediately after merging and let it pick up the stuck submission on its next natural 60s cycle.
   - Rationale: the fix changes the find-or-create key for week sheets (the truncated prefix). A live smoke with controlled single-submission execution confirms the new key resolves correctly and the filing is complete before restoring continuous operation. Because `mark-filed` is posted on success, the reloaded daemon sees an empty `/pending` queue — there is no split-brain risk from the intermediate unload window. This matches the live smoke discipline established for any fix that changes a find-or-create path (Op Stds v18 §30).

4. **Drain `SmartsheetValidationError` to Review Queue (not CRITICAL + abort).**
   - Decision: route a permanent-400 in `process_portal_submission` to the Review Queue with `reason=smartsheet_validation` and return cleanly, rather than firing a CRITICAL and aborting.
   - Alternative considered: treat the error as CRITICAL (immediate operator alert) and leave the submission in the "error" state pending manual intervention.
   - Rationale: the submission itself is not lost — it remains in the D1 queue unfiled; the Review Queue row gives the operator an actionable item (shorten the project name, re-file). A CRITICAL is appropriate for system faults; a structural validation failure caused by a job-configuration choice is an operator-remediable condition, not a system fault. The §43 runbook documents the repair path clearly.

## Open items / next session

1. **`feat/pr3-heartbeat-extraction` (PR-3 shared/heartbeat.py extraction, foundation `546537c`).**
   The `HeartbeatReporter` class is committed on the branch; the thin-wrapper rewire of the 4 daemons + watchdog `TRACKED_JOBS` + `tests/test_heartbeat.py` + mandatory live daemon smoke remain. Still unblocked; carry to next session.

2. **PR-4 — Worker submit/queue hardening (M1 silent-overwrite, M4 immortal bad-HMAC rows, login-disabled gate).**
   Designed in the 2026-06-10 session; all edit points located. Not yet built. Next execution-side session should build this in a worktree.

3. **Deploy Worker with PRs #279 + #280 (`npm run deploy`).**
   Both PRs are merged to main but the Worker has not been redeployed. Form Builder photo-input fix (#279) and Form Request month-year + form-type filter (#280) are inert on the live Worker until the operator runs `npm run deploy`.

4. **7 CLOSED-unmerged publish/scratch branches: pruning (from 2026-06-12 session, still open).**
   Retained conservatively. Operator to confirm which (if any) are safe to delete permanently.

5. **Blueprint co-resolution: mission v4→v5 doctrine flag (two-mode weekly-send transport).**
   Carry to next planning-side session.

## What was NOT touched

- **`~/its-blueprint`**: exec-repo-only session. No doctrine, mission, brief, or reference files modified.
- **Invariant 1 (External Send Gate)**: no send path modified. The fix is confined to the intake pipeline's sheet-creation and error-classification logic; `weekly_send.py` and `portal_poll.py`'s mark-filed path are unchanged.
- **Invariant 2 (Adversarial Input Handling)**: no external-content processing paths modified. The truncation is applied to the project name sourced from `ITS_Active_Jobs` (internal Smartsheet), not from untrusted portal input.
- **Worker (Cloudflare TypeScript)**: fix is Python-only. No Worker changes.
- **`tests/test_capability_gating.py`**: no new generation or send scripts added.
- **Form definitions, catalog.json, required-content.json**: no form definitions modified.
- **Evergreen production tenant**: fix applies to the mirror environment. Production cutover deferred.

## Cross-references

- Memory entry `project_safety_portal_state` — current Safety Portal state; PRs #279/#280 pending deploy noted.
- Memory entry `session-2026-06-10-agent-opt-portal-hardening` — prior session state for PR-3/PR-4 resume points.
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI on merge commit.
- `docs/operations/worktree_discipline.md` — worktree + cloned venv discipline used for the live smoke.
- Prior session log (PR-5 Form Request + PR-3 Graph upload-session): [`2026-06-12_pr5-form-request-pr3-graph-upload-tree-cleanup.md`](2026-06-12_pr5-form-request-pr3-graph-upload-tree-cleanup.md)
- `safety_reports/week_sheet.py` — `SHEET_NAME_MAX`, `week_sheet_name` truncation
- `shared/smartsheet_client.py` — `SmartsheetValidationError` (HTTP 400, `shouldRetry = False`)
- `safety_reports/intake.py` — `process_portal_submission` drain path for `SmartsheetValidationError`
- `safety_reports/README.md` — §43 runbook row: symptom / low-class repair / Tier-3 boundary
- Op Stds v18 §30 (SDK-vs-Live discipline; live smoke requirement for find-or-create path changes)
- Op Stds v18 §43 (successor-remediation runbook — `safety_reports/README.md` entry)
- FM v11 Invariant 1 (External Send Gate — send path unchanged)
