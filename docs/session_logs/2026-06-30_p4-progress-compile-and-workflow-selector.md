---
type: session_log
date: 2026-06-30
status: closed
workstream: progress_reporting
related_prs: [372, 373, 375, 376]
tags: [session_log, p4, progress-reporting, workflow-selector, recategorize, form-builder, design-language, active-jobs, generate-core, live-daemon-corruption, editable-install, worktree-venv, parameterize, safety-portal, cloudflare-zone-scope, fork-agents]
---

# Session — P4 progress compile engine + form-builder workflow selector (PRs #372, #373, #375, #376)

Four PRs landed across two programs: the Safety Portal gained a config-driven workflow selector
(`recategorize` publish op, D1 migration 0020, Worker category validation, SPA workflow `<select>`)
and design-language button updates; the Progress Reporting program completed P4 via two slices —
`shared/active_jobs.py` parameterized into `ActiveJobsConfig` (Slice 1) and the deterministic
weekly-compile engine extracted as `safety_reports/generate_core.py` (Slice 2) plus a new
`progress_reports/progress_weekly_generate.py` instantiation. A live-daemon editable-install
corruption was caught mid-session and repaired. Fork subagents caught three real byte-identical bugs
in P4 Slice 2 that mocks had hidden.

## PRs landed (all four-part verified)

- **#372 `fad817b` — Form-builder workflow selector + `recategorize` publish op.**
  Config-driven `safety_portal/workflows.json` is the single source of truth for the workflow set;
  the TypeScript Worker reads it at build time and Python `shared/form_category.py` reads it at
  runtime — one definition, two consumers, no schema drift. New `recategorize` publish op flips a
  catalog parent's `category` column; it rides the existing §50 publish actuator and keeps
  `apply_publish` pure (no category-specific branching). Worker `validateCategory` added
  fail-closed (an unrecognised category is rejected before the D1 write). D1 migration 0020 adds
  the `category` column with backfill. SPA gains a Workflow `<select>` and a Change-workflow
  action. Two-agent review (form-definition-reviewer + portal-worker-security-reviewer): 1 BLOCK
  (README activation for migration 0020) + 5 WARNs, all folded before merge. §43 runbook
  `form_workflow_recategorize.md` shipped as DoD. Deployed live by operator after debugging a
  Cloudflare 403/7403 (see Decisions).

  PR #372 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-30T04:02:59Z
  - mergeCommit: fad817b95d6ec25c9d2727337b9872ad2289b8e0
  - main CI on merge commit: SUCCESS (run 28419417998)

- **#373 `7c8a616` — Design-language button refinements.**
  Accounts Delete button restyled to `.btn--retire` (red + gold border); Forms version-bump
  restyled to `.btn--edit` (green + gold). Folds into the site-wide #371 design-language
  standard (primary = green bg + white text + gold border; edit = green + gold; retire = red + gold;
  back/home = banner-extension canonical nav). Purely cosmetic — no logic, schema, or API changes.

  PR #373 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-30T15:26:56Z
  - mergeCommit: 7c8a61633a374ec173765f0681fc567799619095
  - main CI: SUCCESS (run 28455934832)

- **#375 `8be87f3` — P4 Slice 1: `shared/active_jobs.py` parameterized into `ActiveJobsConfig`.**
  `ActiveJobsConfig` binds `(sheet_id, contact_columns, label, ttl_seconds)`. SAFETY and PROGRESS
  bindings declared; per-binding TTL cache prevents cross-sheet mix-up (a progress caller cannot
  receive a cached safety result). Neutral `reports_contact_*` column aliases replace workstream-
  specific names so calling code is workstream-agnostic. Safety callers default to
  `SAFETY_ACTIVE_JOBS_CONFIG` — byte-identical behaviour to pre-parameterize. `ops-stds-enforcer`
  review: 1 routing-assertion WARN fixed in the PR (test missing the config argument in an
  assertion path); 0 blocks.

  PR #375 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-30T17:35:09Z
  - mergeCommit: 8be87f318a6f7ae018fb70eba1beaf7ac07f112f
  - main CI: SUCCESS (run 28463862183)

- **#376 `34855cc` — P4 Slice 2: `generate_core.py` extraction + `progress_weekly_generate.py`
  instantiation.**
  `safety_reports/generate_core.py` is the deterministic weekly-compile engine, driven by
  `GenerateConfig`; `weekly_generate.py` is now a thin SAFETY binding with safety-bound aliases
  so `compile_now_poll` and `watchdog` callers are untouched (byte-identical, 31 tests
  re-targeted). `progress_reports/progress_weekly_generate.py` is the progress instantiation —
  routes to `WPR_human_review`, `ITS_Active_Jobs_Progress`, progress Box root, and a
  progress-specific compile mutex: no cross-workstream mix-up possible. GATED capability
  enrollment: `generate_core` in `GATED_SCRIPTS` (no `anthropic` / `graph_client` / `send_mail`
  imports); `progress_weekly_generate` in `GATED_SCRIPTS`. §43 runbook
  `progress_weekly_generate.md` shipped. Staggered launchd plist (Fri 14:30, offset from Safety
  Fri 14:00). `smoke_test_progress_generate.py` and `test_progress_weekly_generate.py` added.
  `ops-stds-enforcer`: 3 doc WARNs folded; 0 blocks. Live smokes (safety byte-identical + progress
  routing) both green. Fork agents caught 3 real byte-identical bugs before merge (see Decisions).

  PR #376 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-30T18:24:15Z
  - mergeCommit: 34855cc101a19644ba6dad61d90f1c1afe829b18
  - main CI: SUCCESS (run 28466767330)

## CI / four-part verify

All four PRs returned state=MERGED, mergedAt non-null, mergeCommit.oid present, and main-branch
CI SUCCESS on the merge commit.

Final integrated tree (P4 Slice 2 merged, full Python suite):

- pytest: **2024 passed**
- mypy: **0 errors / 233 source files**
- ruff: **clean**
- main-branch CI on merge commit `34855cc`: **SUCCESS**

## Live-daemon corruption: caught and repaired

A `cp -R .venv` worktree created earlier in the session had repointed the LIVE editable-install
finder MAPPING at the worktree's Python sources. As a result `portal_poll` → `intake` →
`shared.form_category` was importing UNCOMMITTED worktree code on every 60-second daemon cycle
for approximately 70 minutes. The corruption is IMMEDIATE at copy time (not at worktree delete
time), and is masked by the fact that the daemon's working directory is `~/its` — a bare `-c`
import resolves the editable install finder, not the cwd.

Repaired by:
1. `pip install -e ~/its --no-deps --force-reinstall` (restores the live editable-install finder
   to point at `~/its` sources, not the worktree).
2. The worktree venv was rebuilt FRESH (not via `cp -R`).

The `cp -R .venv` danger is documented in memory
(`reference_worktree-venv-for-python-source-edits`) and in CLAUDE.md's "What NOT to do" section.
This incident is a second data point confirming that the corruption class is live-environment
subtle — a unit-test pass or a `python -c` import check cannot detect it because both resolve
against the (now-contaminated) editable install.

## Decisions made during session

1. **`workflows.json` as single source for Worker + Python.** Two alternative designs were
   considered: (a) duplicate the workflow list in `shared/form_category.py` and in a TypeScript
   const; (b) a canonical JSON file read by both. Choice (b) prevents drift between the server-
   validation surface (Worker) and the intake-routing surface (Python). The file is
   version-controlled; changes to the workflow set require one edit in one place. The Worker reads
   it at build time (Vite bundle step), Python at import time.

2. **`recategorize` rides the §50 publish actuator unchanged.** Alternative considered: add a
   category-specific branch inside `apply_publish`. Rejected — `apply_publish` is a pure
   operation dispatcher; adding category-specific branching would have made it a partially-
   categorical router. `recategorize` is a first-class publish op that carries its own logic
   (column flip + validation) and dispatches cleanly via the existing actuator.

3. **Cloudflare 403/7403 on live deploy — zone scope vs. account scope.** The deploy token had
   account-level permissions for Workers + D1 but lacked zone-level permissions for
   Workers-Routes and DNS needed for the custom-domain route. The token scope is the
   distinguishing signal: account-scoped tokens can deploy Workers but cannot manage the
   zone-level routing table that maps `safety.evergreenmirror.com` to the Worker. Fix: the
   operator added zone-level Workers-Routes + DNS edit permissions to the token. Not a wrong-
   account problem; not a D1 permission problem. Filed to memory as a diagnostic reference for
   future custom-domain deploy 7403 errors.

4. **Fork agents to catch byte-identical bugs in P4 Slice 2.** Three real defects were found
   only because fork subagents ran the extraction in parallel against a spec and compared
   outputs before merge:
   - **Dropped `selection` Compile-Now narrowing**: the progress binding omitted the
     `selection` parameter that narrows a Compile-Now run to specific jobs; the safety binding
     carries it and the progress one must too.
   - **`_read_int_setting` bypassing the mockable `_read_str_setting` seam**: the integer-
     setting helper was reading directly from `ITS_Config` rather than delegating to
     `_read_str_setting`, bypassing the testable indirection that mocks rely on.
   - **Packet attached to review-row twice instead of rollup-row + review-row**: the Box PDF
     attachment call was being routed to `WSR_human_review` twice, with `Rollup` rows receiving
     no attachment. This is undetectable by unit tests because both calls target Smartsheet
     row IDs that are mocked to the same stub.
   All three were fixed in the same PR before merge. No separate follow-up required.

5. **Per-binding TTL cache on `active_jobs.py` is load-bearing, not cosmetic.** The naive
   implementation would use a single module-level cache keyed only by job ID. A progress caller
   and a safety caller querying the same job ID from different physical sheets would collide on
   a shared cache and one would return stale data from the other's sheet. The per-binding TTL
   (keyed by `ActiveJobsConfig` binding identity) prevents this; a cache hit is only valid for
   the same binding. This is the class of cross-workstream mix-up that the parameterize program
   is explicitly designed to prevent.

6. **Safety compile stagger (Fri 14:00) + Progress stagger (Fri 14:30).** Both compile daemons
   target the same Friday window. Running them at the same minute would cause mutex contention
   (compile_mutex) on every Friday. A 30-minute offset is sufficient: safety compiles are
   typically complete within 5–10 minutes; the progress compile starts only after the safety
   window is clear. The 30-minute offset is encoded in the launchd plist, not a config value —
   changing it requires a plist reload, which is a deliberate operator action.

7. **Worktree venv rebuilt FRESH after `cp -R` corruption.** The alternative of restoring via
   `pip install --force-reinstall` only would leave uncertainty about what other finder state
   the copy had contaminated. A fresh venv construction (`python -m venv .venv-wt && pip install
   -e ~/its --no-deps`) is the only clean recovery path. The `cp -R` venv pattern is now
   treated as hard-prohibited (CLAUDE.md "What NOT to do"); safe alternative is fresh-venv
   construction.

## Open items / next session

- **Watchdog Check-I / Check-C wiring for `progress_weekly_generate` slug.** Today's safety
  watchdog checks (Check I = Friday-crash catch-up; Check C = scheduled-jobs marker staleness)
  are wired only to the `weekly_generate` slug. The progress compile daemon needs its own
  marker slug in `TRACKED_JOBS` and a corresponding Check-I entry. Approximately 30-line
  `watchdog.py` change; no blocking dependency on P5.

- **P5 (progress send): `job.reports_contact_email` + `PROGRESS_ACTIVE_JOBS_CONFIG`.** The
  progress send daemon must resolve recipients from `ITS_Active_Jobs_Progress` via
  `PROGRESS_ACTIVE_JOBS_CONFIG` (not the safety sheet). The neutral `reports_contact_*` column
  aliases in `ActiveJobsConfig` are the bridge; P5 must pass the config explicitly — it must
  not default to the safety binding. This is the key guard against cross-workstream recipient
  contamination in the send path.

- **Operator activation queue (no code required):**
  - `git -C ~/its pull origin main` to fast-forward the live tree to `34855cc`.
  - Load the progress-generate plist at the progress cutover (Fri 14:30 schedule; plist is in
    `scripts/launchd/` as a template).
  - §46 re-share: approvers must be shared into the Progress workspace before P5 send goes live
    (prerequisite — without this, F22 approval-attestation cannot resolve the approver list).

- **Portal design-language: Back/Home buttons.** The site-wide button standard (#371 + #373) is
  not yet applied to the Submit-a-Form and Form-Request back/home navigation elements. Cosmetic;
  fold in whenever convenient. Tracked in memory `reference_portal-button-design-language`.

- **Standing carry-forwards:**
  - P2.4 mirror daemon — BLOCKED (no Smartsheet access); unchanged.
  - Issue #336 (REQUIRED_CONFIG startup-logging) — not in scope this session.
  - A5 (watchdog Check O) — not in scope; deferred.

## What was NOT touched

- No doctrine edits (Op Stds, Foundation Mission, mission files). #372 §43 runbook is
  execution-repo only.
- No changes to existing safety compile logic. `generate_core.py` is an extraction of
  `weekly_generate.py`'s engine; the extracted function is byte-identical to pre-extraction.
  All 31 re-targeted tests pass on the same logic.
- No external send paths changed. Invariant 1 intact: `generate_core.py` and
  `progress_weekly_generate.py` are in `GATED_SCRIPTS` with zero `anthropic` / `graph_client` /
  `send_mail` / `resend` / `smtplib` imports; capability-gating tests cover both.
- No Smartsheet schema migrations. P4 Slices 1–2 are Python-only.
- No D1 changes beyond migration 0020 (already in #372, the portal PR).
- No change to `compile_mutex.py` (landed #354 in Stage 1, prior session; used here unchanged).
- No change to `portal_poll.py`, `weekly_send.py`, or `weekly_send_poll.py`.
- No new launchd plists loaded mid-session. The progress-generate plist exists as a template;
  it is loaded by the operator at the progress cutover.
- P3, P5, P6 phases of the Progress Reporting program — not in scope.
- No blueprint planning-layer edits.

## Cross-references

- Prior session (Stage 1 parameterize complete): `docs/session_logs/2026-06-30_stage1-parameterize-complete.md`
- Prior session (Stage-0 foundation + Personnel CRUD): `docs/session_logs/2026-06-29_field-ops-progress-stage0-foundation-landed.md`
- Worktree venv danger: memory `reference_worktree-venv-for-python-source-edits`; CLAUDE.md "What NOT to do"
- PR merge discipline: `docs/operations/pr_merge_discipline.md`
- Op Stds v19 §50 (D1 code-actuation gate; `recategorize` rides the actuator), §43 (successor runbooks: `form_workflow_recategorize.md`, `progress_weekly_generate.md`), §14 (parameterize-not-clone §14 deviation rationale)
- Capability gating: `tests/test_capability_gating.py` (GATED_SCRIPTS enrollment for `generate_core`, `progress_weekly_generate`)
- Button design-language standard: memory `reference_portal-button-design-language`
- Portal PDF three delivery surfaces (not touched, but referenced as a reminder): memory `reference_pdf-three-delivery-surfaces`
- Memory entries to update: `project_fieldops-portal-program` (Stage 2 P2 done; P4 Slice 1 + Slice 2 done; open items: watchdog wiring, P5 prereqs); `reference_worktree-venv-for-python-source-edits` (second confirmed corruption incident, fresh-venv-only rule reinforced)
