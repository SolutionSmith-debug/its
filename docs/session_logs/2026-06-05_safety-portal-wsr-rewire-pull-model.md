---
type: session_log
date: 2026-06-05
status: closed
related_prs: [173, 174, 175, 176]
workstream: safety_portal
tags: [safety-portal, wsr, pull-model, portal-poll, intake, weekly-generate, weekly-send, hmac, box, phase5, codeql]
---

# Session log — Safety Portal WSR rewire (Phase-5 Python pull model, PRs #173–#176)

Completed the **whole Python side of the Safety Portal pull model** — the "future
WSR-rewire PR" prior sessions kept deferring. Four reviewable PRs, each four-part
verified + CodeQL-clean, take the flow end-to-end on the Python side:
portal → Worker (already on main) → **portal_poll** (pull + HMAC-verify) → **intake**
(render + Box-file + week-sheet) → **weekly_generate** (deterministic compile →
Rollup + WSR dual-write) → **weekly_send** (WSR → Graph send). **Everything is
code-complete + NOT-live-verified** — the live end-to-end smoke (real Worker deploy +
secrets) is the NEXT (deploy) session. This **corrects #170's framing**, which listed
the rewire as next-session/deploy-gated work — most of it is now done here.

All work was done in a `git worktree` (`~/its-portal-rewire`), never the live `~/its`
daemon tree, and `~/its` was deliberately NOT pulled (the live daemons keep running the
old code until the deploy session pulls + reloads — satisfying "landing on main must
not change what the running daemon executes").

## PRs landed (all four-part verified + CodeQL-clean)

### PR #173 — Portal infra (`df3f748`)
`shared/portal_client.py` (Worker HTTP transport: get_pending / mark_filed; F02-
allowlisted), `box_client` `upload_bytes` + `get_or_create_folder`, `form_pdf.load_definition`,
`safety_reports/week_sheet.py` (Saturday-week sheet, columns-via-API), `prompts/snippets/invariant-restatement.md`.
- pytest: 1382 passed / 0 skipped / 35 deselected
- mypy: 0 errors / 176 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

### PR #174 — Pull consumers (`bdb9f8f`)
`intake.process_portal_submission` (dedupe via week-sheet UUID, deny-by-default job
resolution, payload validation, deterministic render, Box filing to existing category
subfolders / ITS fallback, amend-supersede, poison-message-safe drain policy) +
`safety_reports/portal_poll.py` (fail-closed creds, per-row HMAC verify→reject-never-file,
mark-filed receipt, seen-set, daemon-health/heartbeat/watchdog, capability-gated) +
§43 runbook.
- pytest: 1442 passed / 0 skipped / 35 deselected
- mypy: 0 errors / 179 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

### PR #175 — Deterministic weekly compile (`49b393d`)
`weekly_generate` rewritten (Anthropic narrative core retired): gather per-submission
PDFs → `merge_pdfs` → ITS-prefixed Box week folder → dual-write Rollup snapshot +
`WSR_human_review` row. `wsr_review.py` added; `week_sheet` rollup helpers; WSR Send-Status
picklist registered; orphaned prompt/schema deleted; §43 compile runbook.
- pytest: 1432 passed / 0 skipped / 34 deselected
- mypy: 0 errors / 181 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

### PR #176 — Weekly send WSR repoint + cleanup (`e628044`)
`weekly_send` + `weekly_send_poll` repointed WPR→WSR (recipients resolved from
`ITS_Active_Jobs` at send time, compiled PDF attached, HELD/FAILED policy, F22 on the
driving Send-Now/Scheduled checkbox + approver stamp). `active_jobs` job_slug dropped;
watchdog Check-I repointed to WSR; WPR decommission-by-doc; CLAUDE.md/README/tech_debt
reconciled.
- pytest: 1422 passed / 0 skipped / 34 deselected
- mypy: 0 errors / 181 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

## Key decisions (resolved with the owner this session)

- **Box model (owner override of the brief).** Per-submission PDFs file into the job's
  EXISTING category subfolders (JHAs / Toolbox Talks / Inspection Reports), named
  `<work_date>-<type>.pdf`; the compiled WSR packet files into an auto-created
  **`ITS`-prefixed** week folder (operator rule: all ITS-auto-created Box folders start
  with `ITS`). This overrode the brief's "new canonical mirror under canonical_job_path"
  framing.
- **Week sheet** = a new per-(job, Saturday-week) sheet, columns built via the SDK on
  create (no template; deploy-safe), in the project's Field Reports folder.
- **Dedupe** = the week-sheet Submission-UUID check is the authority (survives a wiped
  state file); the portal_poll seen-set is a defense-in-depth fast-path.
- **Poison-message policy** (intake → portal_poll): processed/already_filed/review_queue
  → DRAIN (mark-filed); transient `error` → NOT drained (re-pull retries). A permanent
  refusal (unknown job, malformed payload) drains with a Review-Queue record (re-pull
  can't fix it); the operator re-files from the payload.
- **CodeQL FP traced to root (PR2).** A `py/clear-text-logging-sensitive-data` HIGH
  alert was traced via the SARIF code-flow to tuple-unpacking taint: `_resolve_credentials`
  returned `(base_url, bearer, secret)` as a tuple, and CodeQL propagated the `bearer`
  secret-taint onto `base_url`, which rode the Worker request into the response → every
  logged submission field. Fixed cleanly with a named-field `_PortalCreds` dataclass
  (field-sensitive → no taint bleed) + isolating the HMAC to verification only. Genuine
  hygiene, not a suppression.

## Adversarial review

Each PR got a multi-dimension adversarial review (dimensions → per-finding verify)
before merge. Real findings fixed include: PR2 `upload_bytes` catch-all + bounded
untrusted-response errors; PR3 **critical** blank-`Submitted At` silent-skip (now forces
recompile + WARN) + the `packets_compiled` counter; PR4 `_stamp_approval` broadened to
never block a verified send + `_mark_held` explicit outcome status + the stale-WPR
error message/docstring. Test-coverage gaps closed throughout.

## Not done / follow-ups (tracked in docs/tech_debt.md)

- **Live verification** — the full chain (real Worker deploy + secrets) is the deploy
  session. Every PR is marked NOT-live-verified.
- **WPR final removal** — no live runtime code references `SHEET_WPR_PENDING_REVIEW`;
  the constant + picklist entry + the catch-up smoke remain until the operator deletes
  the WPR sheet (trivial follow-up).
- **portal_poll watchdog registration** — `portal_poll` writes a watchdog Check-C marker
  but is not yet in `TRACKED_JOBS` (deferred to the deploy session when the daemon is
  loaded; an unregistered marker is harmless).
- **Box file-attach to the WSR row** — `weekly_send` sets the Compiled-PDF link cell;
  Smartsheet file-attach deferred (no client method; the link is one-click reviewable).
- **shared/heartbeat.py extraction** — portal_poll is now the 2nd live consumer of the
  verbatim-replicated heartbeat helpers (still under the ≥4-reuse deferral, §14).

## Deploy-session checklist (next chat)

1. Deploy the Worker; seed Keychain `ITS_PORTAL_INTERNAL_TOKEN` + `ITS_PORTAL_HMAC_SECRET`
   and the ITS_Config `safety_reports.portal.worker_base_url`.
2. Run the §30 integration suites (`pytest -m integration`) against the sandbox.
3. Load the `portal_poll` launchd job; add it to watchdog `TRACKED_JOBS`.
4. Pull + reload `~/its`; live smoke portal → … → WSR → send.
5. Operator-manual: unload the retired safety-intake launchd job; delete the WPR sheet +
   the "Job Slug" column; build the WSR-side compile-now / scheduled-send ITS_Config rows.
