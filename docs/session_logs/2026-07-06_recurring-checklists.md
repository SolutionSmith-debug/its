---
type: session_log
date: 2026-07-06
status: complete
workstream: field_ops
related_prs: [476]
tags: [session-log, field_ops, safety_portal, checklists, recurring, worker-cron, feature-flag, adversarial-review, stale-claim, autonomous]
---

# Session 2026-07-06 (autonomous) — Recurring checklists per job (#16, PR #476)

Autonomous execution of the `field-ops-next-session-brief` §2A (operator: "execute the brief in
extended autonomous mode, using agents, sticking to best practices and lessons learned"). One
substantial feature landed; the brief's other buildables were found to be blocked, stale, or
operator-parked and were surfaced rather than force-built.

## Commits landed

- `3f6c07d` — **#476 feat(fieldops): recurring checklists per job (#16) — cadence generator,
  dark-gated** (squash of two commits on `feat/recurring-checklists`):
  - `1ad1f1e` — the feature (migration 0040 + `worker/fieldops_recurrence.ts` engine + assign-route
    recurring branch + `GET /checklist/recurrences` + `POST /checklist/recurrence/:id/deactivate` +
    SPA controls + dark Worker-var gate + §43 runbook + README Activation).
  - the review-fold commit — template-empty auto-stop + calendar-anchor validation.

Four-part verify clean: state=MERGED · mergedAt=2026-07-06T01:30:51Z · mergeCommit=`3f6c07d6` ·
main-CI on the merge commit SUCCESS (`test`, `portal`, `secrets`, and non-required CodeQL all green).

## What it does

An admin makes an inspection assignment **recurring**: the existing Assign form gains a "Recurring
checklist" checkbox → cadence (daily/weekly/biweekly/monthly) + a "generates off of" anchor date. A
per-job generator (`checklist_recurrences`, migration 0040) spawns the assignee's `kind='inspection'`
instance on each cadence date — the **same** instance shape a one-shot assign creates, so it surfaces
in the Assigned-Tasks tab + the admin Outstanding list with zero new read code.

## Non-obvious decisions (the why)

1. **Generation mechanism = the existing Worker cron, NOT a Mac daemon.** The brief floated "a Worker
   cron OR the `portal_poll` Mac daemon." The portal already has a daily `scheduled()` cron
   (`wrangler.jsonc triggers.crons` 09:00 UTC) that today only prunes. Extending it keeps the feature
   **fully contained to the portal** — no new daemon, no Python, no launchd, no Smartsheet/Box, no
   worktree-venv risk. This was the single load-bearing design choice; it made 2A a clean TS-only build.
2. **Storage = a new `checklist_recurrences` table, not columns on "the assignment."** There is no
   standing assignment entity — `POST /checklist/assign` creates a one-shot instance and returns.
   `checklist_instances` deliberately carries no `template_id` (0029). A recurrence is a *definition*
   the generator reads, so it gets its own table.
3. **Idempotency reuses the EXISTING dedupe key.** Each spawn is `INSERT OR IGNORE` on the existing
   `UNIQUE(kind, job_id, assignee_personnel_id, instance_date)` (0026) + a per-recurrence
   `last_generated_date` watermark. A double-run or a crash before the watermark advance never
   double-spawns; the snapshot self-heals on `itemCount==0`. Catch-up after a cron gap is bounded to
   45 days (older dates dropped + logged — never a flood).
4. **Dark gate = a Worker `var`, not an ITS_Config row.** For a portal feature the operator-visible,
   in-repo gate is `RECURRING_CHECKLISTS_ENABLED` in `wrangler.jsonc` (default `"false"`) — flipped by
   editing the file + `npm run deploy`. This sidesteps the "dark-shipped gate has no row to flip"
   phantom-row problem (the 2026-07-05 equipment/materials gate-absence pain). Dark behavior is
   never-silent: cron no-ops, the assign route returns `400 recurring_disabled`, the SPA hides the
   controls; the one-shot path is byte-identical when dark.
5. **No cadence CHECK on the D1 table (Worker-validated instead).** The operator asked for an
   "extensible" cadence set. A CHECK would make adding a cadence a table-rebuild (0020/0032 precedent);
   validating in the Worker's `RECURRENCE_CADENCES` set makes it code-only. A malformed cadence is
   rejected `400` before it reaches the table.
6. **§14 preservation over DRY.** The one-shot assign handler was left untouched; a new module
   (`fieldops_recurrence.ts`) + a DB-handle audit twin (`auditStmtIfChangedDb`, for the cron which has
   no Hono Context) were *added* rather than refactoring the security-reviewed assign path for shared
   helpers. `ops-stds-enforcer` confirmed this is the doctrine-correct tradeoff (the duplicated
   snapshot SELECT is the acceptable cost), not a violation.

## Adversarial review (trust-boundary DoD) + folds

- **ops-stds-enforcer: CLEAN** — Inv1 (genuinely send-free; the cron is D1-only, no AI), Inv2, §14
  (one-shot path byte-identical when dark, proven by synthetic tests), §42, §51 (no SoR surface),
  §43 runbook, config never-silent, doc conventions, display-name-only attribution.
- **portal-worker-security-reviewer: WARN → resolved.** Every security dimension clean (bound SQL, W4
  mutation+audit atomicity, fail-closed cap gates, input bounds, no leakage, migration order,
  D1-as-primary). Two WARNs folded before push:
  - **Never-silent gap:** a template emptied/deleted *after* a recurrence is defined would spawn empty,
    permanently-open instances forever. Fixed by mirroring the job-closure auto-stop — the generator
    re-checks the template's item count each pass and, on zero, auto-stops + audits
    `checklist_recurrence_autostop` reason `template_empty`. No junk is ever spawned.
  - **Anchor calendar validity:** `ANCHOR_DATE_RE` is shape-only; `"2026-13-32"` passed and silently
    never generated. The assign route now round-trips the anchor through `isRealCalendarDate()` and
    `400`s `invalid_anchor_date`.
  - (A third WARN — re-define runs ~45 bounded, admin-gated D1 round-trips inline — was accepted as-is:
    bounded + self-inflicted, not a DoS.)

## Deliberately NOT built (surfaced, not force-built)

Best-practice discipline (slot into roadmap, don't build blocked/parked/ad-hoc, surface blockers):

- **§2B checklist→progress-report logging** — needs the operator's Seam-A-vs-B decision + crosses §51
  doctrine + needs the `progress@` mailbox (its#460). Not autonomous-safe.
- **§3 M3 (Material Incidents)** — the brief's "NEXT PRIMARY BUILD," but it sits on the **§51-blocked**
  M2 Material List (`materials_enabled` dark pending Seth's §51 rider) and has its own §51 SoR-mirror
  dimension. Building it now would reproduce the §51-drift BLOCK that P7-S1 and M2 hit → recommend
  building it *after* Seth ratifies the §51 materials rider (draft:
  `docs/audits/2026-07-05_section51-materials-rider-proposal.md`).
- **#336 REQUIRED_CONFIG** — genuinely unblocked, but operator-parked to phase 1.6; a partial sweep
  would be the "half-baked" outcome the operator has objected to. Available on green-light.

## Stale-claim correction (trust live code over the brief)

`brief-validator`, run to validate the follow-on **PR-6 (Form Request month/form filter)** before
building it, found **PR-6 already shipped** in PR #280 (`ff00308`, merged 2026-06-13) — the whole
Job→Month→FormType cascade is live on main. The next-session-brief §7 line, the
`project_form-request-month-filter-pr6` memory, and the MEMORY.md index all falsely said "NOT built."
All three corrected. Validating first avoided a duplicate build — the "zero grep hits beats confident
memory" reflex in action.

## Gates

- pytest: not run — **zero Python touched** this session (TS/Worker/D1/SPA only).
- mypy: not run — no Python touched.
- ruff: not run — no Python touched.
- worker vitest: 828 passed. SPA vitest: 560 passed. typecheck clean. production build OK.
- main-branch CI on merge commit `3f6c07d6`: SUCCESS.

## Operator deploy (ships DARK — landing changed nothing live)

`git -C ~/its pull origin main` → apply pending migrations `--remote` (0030–**0040**, the README
punch-list = the single apply-all-then-deploy source) → `cd safety_portal && npm run deploy`. Then to
**activate**: set `wrangler.jsonc` `RECURRING_CHECKLISTS_ENABLED:"true"` → `npm run deploy` →
live-smoke per the README "Recurring checklists per job (#16) — 0040" Activation section. §43 repair
guidance: `docs/runbooks/fieldops_checklists.md`.
