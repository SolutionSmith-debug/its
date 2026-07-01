---
type: session_log
date: 2026-07-01
status: closed
workstream: field_ops
related_prs: [395, 396, 397, 398, 399, 400]
tags: [session_log, field-ops, p2.5, p2.6, manager-tier, capabilities, fieldops-sync, portal-poll, watchdog, launchd, cutover, deploy-auth, stale-base, adversarial-review, unified-create-flow, crew-convergence]
---

# Session — P2.5 cutover LIVE + P2.6 Manager tier + FF4/FF5 daemon hardening (PRs #395–#400)

Six PRs, all four-part verified, exec HEAD `34855cc` → `551aa72`. Cut the P2.5 job-tracker →
Smartsheet up-sync over to LIVE, shipped and deployed **P2.6 — Manager tier** (a third portal
role + crew standing-placement), and hardened two live daemons (`portal_poll`, `fieldops_sync`)
against alert-severity bugs surfaced by the newly-live traffic. Closed with a build-ready spec
for the next field-ops slice (unified job-create flow) rather than starting it cold.

## PRs landed (all four-part verified)

- **#395 `4acb98f` — fix(launchd): print real StandardOutPath/ErrorPath in install.sh load.**
  `install.sh load` was printing the unsubstituted `__ITS_HOME__` template placeholder in its
  post-load confirmation instead of the actual resolved log paths. Fixed via `plutil` to read the
  installed plist back. Cosmetic-but-operator-facing (the confirmation message is what a
  Successor-Operator reads to find the log).

- **#396 `569003c` — fix(field_ops): read shared Worker base-URL key under its owning
  safety_reports workstream.** Real config-dedup bug: `field_ops/fieldops_sync.py` was reading
  its own `field_ops.*` copy of the Worker base-URL ITS_Config key instead of the canonical
  `safety_reports.portal.worker_base_url` row every other portal-facing daemon reads. Two rows
  holding the same logical value could silently drift. Fixed to read the shared key; the
  redundant `field_ops.*` row was deleted from ITS_Config during this session's cutover.

- **#397 `95c7613` — feat(watchdog): track fieldops_sync in Check-C.** The live P2.5 mirror
  daemon (`fieldops_sync`) writes a Check-C freshness marker every cycle but `TRACKED_JOBS` never
  listed it — a silent daemon death would have gone undetected. Added `fieldops_sync` with an
  8-minute staleness window (mirroring the `safety_compile_now_poll` 90s→8-min high-frequency
  pattern); the 90s cadence is tunable via `field_ops.fieldops_sync.poll_interval_seconds`, called
  out in a code comment for an operator who widens it. Verified FRESH against the live marker
  immediately (the daemon was already running).

- **#398 `6654a41` — feat(p2.6): Manager tier — third portal role + cap.crew.assign +
  crew→job placement.** Adds `manager` (crew lead) between `submitter` (field PM) and `admin`
  (office): submitter's 8-cap floor + `cap.personnel.read` + `cap.personnel.manage` +
  **`cap.crew.assign`** (new, 19th capability), explicitly WITHHOLDING `cap.jobtracker.manage` so
  a manager cannot create jobs/tasks. Migration `0023` (pure-additive: role INSERT, capability
  grants, `personnel.current_job TEXT` column). New route `POST
  /api/fieldops/personnel/:id/assign` — send-free, atomic `EXISTS(active job)`-in-WHERE guard,
  mutation + conditional audit row in one D1 batch. SPA: Accounts 3-way role control, Personnel
  "Placed on" + Assign control. `portal_admin` CLI role support. Story: office creates jobs
  (admin), manager runs crews (manager), field PM submits (submitter). Orthogonality
  operator-locked: placement (`current_job`) and time entries are independent — logging time
  against a job doesn't require being placed on it.

  Adversarial review: `portal-worker-security-reviewer` CLEAN (12/13 clauses, W7 N/A — confirmed
  send-free, bound SQL, atomic guard, empirically-verified audit atomicity on a failed 422,
  fail-closed role vocabulary, end-to-end privilege separation). `ops-stds-enforcer` WARN→fixed:
  `portal_admin.py` docstring/hint strings hadn't been updated to include `manager` in the role
  enum — a fan-out miss on the same class CLAUDE.md already documents generically.

  **Held for operator.** Deploy required `wrangler d1 migrations apply --remote` for `0023`
  BEFORE `npm run deploy` (else a `manager` user resolves to the empty cap set — fail-closed
  blank tabs — and `/assign` 500s on the missing column). CC's shell had no
  `CLOUDFLARE_API_TOKEN` this session, so both steps were operator-terminal-only (see Decisions
  below). Operator ran the migration + deploy + live smoke and confirmed: `manager` sees
  Personnel + can assign crew (201), gets 403 on job-create/task-create/login-mint, cannot open
  the admin dashboard.

- **#399 `7e44d73` — fix(portal_poll): WARN (not CRITICAL) on a transient Smartsheet
  circuit-open.** Observed live 2026-07-01: a ~5-minute DNS/network blip opened the Smartsheet
  circuit breaker; `portal_poll`'s base-URL config read swallowed `SmartsheetCircuitOpenError`
  into an empty string, `_resolve_credentials` returned `None`, and the daemon fired CRITICAL
  `portal_creds_missing` — a false alarm (the creds were fine; Smartsheet was just briefly
  unreachable and self-healed). Fix: `_resolve_credentials` gains a third state,
  `CREDS_TRANSIENT`, returned only when the base-URL read hits a caught circuit-open. The caller
  WARNs `portal_creds_transient` and skips the cycle (no watchdog marker written, so a *sustained*
  outage still trips the Check-C staleness floor — no false-negative for a genuine misconfig).
  4 unit tests lock the 3-state resolver.

  Adversarial review: `ops-stds-enforcer` CLEAN on the severity-posture change (14 clauses) — also
  caught a stale-base landing hazard pre-commit (see Decisions below), fixed by rebasing.

- **#400 `551aa72` — fix(fieldops_sync): 401-on-mark-mirrored → CRITICAL + partial-commit
  review context.** Two fixes to the live job up-sync daemon now that it mirrors real jobs in
  production. **FF-A:** a 401 on `mark_fieldops_jobs_mirrored` (rejected/rotated field-ops bearer)
  was falling into the generic transient `PortalTransportError` retry clause — logged ERROR and
  retried forever, never paging. `PortalAuthError` (a `PortalTransportError` subclass) now gets an
  earlier explicit `except` → CRITICAL `fieldops_mark_mirrored_unauthorized`, matching the
  pending-jobs 401 posture used elsewhere; the sheet upsert already landed by that point, so the
  job stays dirty and safely re-attempts (find-or-create no-ops) once the token is fixed. **FF-B:**
  `_route_to_review` now records `mirrored_safety` + `failed_sheet` in the Review-Queue payload, so
  the operator can tell from the row alone whether a partial-commit failure happened before or
  after the safety-sheet write. Explicitly skipped (deliberate, not forgotten — see tech_debt):
  the `active_jobs_writer` re-find-after-create race (hard-to-hit, idempotent) and the
  `_ENROLLMENT_SUFFIXES += "_sync.py"` capability-gating item (cascades and breaks the
  pre-existing `picklist_sync.py` meta-test — needs its own PR first).

  Adversarial review: `ops-stds-enforcer` — a doc-pointer copy-paste caught pre-merge (the
  CRITICAL message's runbook pointer had been copied from `portal_poll`'s equivalent alert and
  still pointed at `docs/runbooks/portal_poll.md` instead of `fieldops_sync.md`); fixed, and a new
  Symptom E added to `docs/runbooks/fieldops_sync.md`.

## CI / four-part verify

All six PRs returned `state=MERGED`, `mergedAt` non-null, `mergeCommit.oid` present, and
main-branch CI (test/portal/secrets) SUCCESS on the merge commit.

- pytest: 2137 passed (at #400; incremental growth across #395–#400)
- mypy: clean, no issues
- ruff: clean
- main-branch CI on merge commits 4acb98f / 569003c / 95c7613 / 6654a41 / 7e44d73 / 551aa72:
  SUCCESS

## Decisions made during session

1. **Deploy capability is not a stable CC-environment fact.** A 2026-06-08 info-gap note said "CC
   can run `npm run deploy` in auto-mode." This session's CC shell had no `CLOUDFLARE_API_TOKEN`
   at all — both `npm run deploy` and `wrangler d1 migrations apply --remote` were
   operator-terminal-only. Every PR that needed a deploy (#398) was built, tested, and reviewed to
   completion, then held with explicit numbered activation steps in the PR body rather than
   assumed-mergeable. Rule going forward: check `CLOUDFLARE_API_TOKEN` / `wrangler whoami` at
   session start rather than trusting a prior session's note either direction.

2. **Stale-base landing hazard, caught live, not just theorized.** FF4's (#399) worktree was cut
   before #397 (watchdog Check-C `fieldops_sync`) landed on `origin/main`; without a rebase, the
   diff would have falsely appeared to revert #397's watchdog change. `ops-stds-enforcer` caught
   it pre-commit; fix was `git pull --ff-only origin main` into the worktree before the final
   commit. A concrete instance of the general "stale checkout" class already in CLAUDE.md — now
   also called out as a Gotcha in the unified-create-flow spec for the next session.

3. **New reusable trap: doc-pointer copy-paste across sibling daemons.** FF5's (#400) new
   CRITICAL alert was drafted by pattern-matching `portal_poll`'s existing 401-CRITICAL alert; the
   code logic was correctly adapted but the runbook-pointer string in the message was not — it
   still named `portal_poll`'s runbook. Caught by review, not by any test (a prose string, not
   logic). Rule: grep a copy-pasted alert/log message for the source module's name before
   committing.

4. **Crew-convergence finding, spun into a dedicated spec rather than folded into P2.6.** While
   scoping the next field-ops slice (bundling task-create + crew-assign + equipment-assign into
   the portal's "New job" flow), found that the job-list and job-detail crew queries in
   `fieldops_jobtracker.ts` compute crew from `task_assignments`, not from P2.6's new
   `personnel.current_job` — a person placed via the new assign route would not show up as crew.
   Operator locked the resolution (crew converges on `current_job`; `task_assignments`/`tasks`
   queries stay untouched — §14 preservation) and a full build-ready spec was written rather than
   just noting the gap: `~/.claude/plans/spec_unified-job-create-flow.md`. NOT built this session.

## Live validation

- Post-`git pull ~/its`: `portal_poll` and `fieldops_sync` daemons confirmed cycling with zero
  errors on the pulled code (no restart needed for the non-deploy PRs — pure Python daemon fixes).
- Post-P2.6-deploy operator smoke: manager-role login confirmed — Personnel visible, crew-assign
  works (201), job/task-create and login-mint both 403, admin dashboard inaccessible.
- **P2.5 end-to-end confirmed live:** `JOB-000017` verified mirrored into both `ITS_Active_Jobs`
  and `ITS_Active_Jobs_Progress` after the cutover steps below.

## Operator cutover executed this session

1. `git -C ~/its pull origin main` — daemon-only fixes (#395/#396/#397/#399/#400), no deploy
   required; confirmed healthy post-pull.
2. `wrangler d1 migrations apply its-safety-portal-db --remote` for migration `0023`, then
   `npm run deploy` — both from the operator's own terminal (see Decision 1).
3. `fieldops-sync` launchd job reloaded at its 90s interval.
4. Duplicate `field_ops.*` `worker_base_url` ITS_Config row (made redundant by #396) deleted.
5. Workstream picklist gained `field_ops` + `progress_reports` options via `update_column`
   through the Smartsheet MCP (`Picklist_Sync_Config` confirmed empty — nothing auto-reverts a
   manual add; flagged in info-gap §6 for future awareness if that config is ever populated).

## Open items / next session

- **Unified job-create flow** — fully spec'd, not built: `~/.claude/plans/spec_unified-job-create-flow.md`.
  Three slices (worker crew-query convergence + migration; SPA detail-view assign controls; SPA
  create-flow nudge), locked scope (materials deferred to M2, per-control capability gating).
- **P2.5 fast-follows still OPEN** (re-evaluated and deliberately deferred again by FF5): the
  `active_jobs_writer` re-find-after-create race, and `_ENROLLMENT_SUFFIXES += "_sync.py"`
  (needs `picklist_sync.py` capability-gating enrollment first, in a separate PR).
- **install.sh interval-help-text stale** — `usage()` + header comment list only 3 of the 5
  interval daemons the resolution logic actually supports (missing `progress-send` and
  `fieldops-sync`). Trivial docs-only fix, tracked in `docs/tech_debt.md`.
- Session-close maintenance for this session updates `claude-code-info-gap.md` §5/§6/§8 +
  `memory-archive.md` §G49 + this log — see the session-close-maintainer output for the full diff
  list (docs-only, left uncommitted per this session's directive not to commit in the live
  `~/its` tree).

## What was NOT touched

- No external send fired. `fieldops_sync` and `portal_poll` remain send-free, AI-free daemons;
  neither PR touched the External Send Gate surface.
- No changes to `safety_reports/weekly_send*` or `progress_reports/progress_send*`.
- Unified job-create flow: scoped and spec'd only, zero code written.
- No doctrine edits (`its-blueprint/doctrine/*` untouched).

## Cross-references

- Prior session (P2.5 cutover + P6 rollup): `docs/session_logs/2026-07-01_p2.5-cutover-live-p6-rollup.md`
- P2.5 build: `docs/session_logs/2026-06-30_p2.5-job-tracker-upsync.md`,
  `docs/session_logs/2026-06-30_p2.5-slice6-portal-number.md`
- Op Stds v19 §14 (parameterize/preservation), §42 (self-documentation), §43 (runbooks:
  `manager_tier.md`, `fieldops_sync.md`), §50 (code-actuation gate — P2.6 deploy pattern), §51
  (SoR write-back — P2.5's job-tracker→Active-Jobs write)
- §6/A8 enablement: `docs/enablement/manager_tier.md`
- Memory-archive: `memory-archive.md` §G49 (full detail — deploy-auth constraint, stale-base
  hazard, doc-pointer copy-paste class, crew-convergence finding)
- Spec for next slice: `~/.claude/plans/spec_unified-job-create-flow.md`
- Memory to update: `project_fieldops-portal-program` (already updated this session per the
  session-close-maintainer's survey — not duplicated here)
