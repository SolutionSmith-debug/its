---
type: session_log
date: 2026-07-02
status: closed
workstream: field_ops
related_prs: [406, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 418, 419, 420, 421]
tags: [session_log, field-ops, assigned-tasks, checklist-engine, daily-checklist, inspection-library, subcontractor-tier, r-series, ux-refinement, design-language, portal-worker-security-reviewer, ops-stds-enforcer, autonomous-run, w4-audit-atomicity, never-silent, attribution, held-pr]
---

# Session — Assigned-Tasks feature (S1–S6+T) + R-series UX refinement program (PRs #406–#421, #415 held)

One continuous autonomous arc spanning 2026-07-01 evening through 2026-07-02 evening, operator
away for most of it. Two back-to-back programs: the 7-slice **Assigned-Tasks feature** (My Tasks,
daily checklist engine, loop-closure, Daily-Report rollup, inspection library, subcontractor tier
— #406–#412) landed first, followed by a design-polish pass (#413). The operator returned,
deployed, smoked, and found the daily checklist "missing" — a stale-Worker-deploy symptom, not a
feature gap — then issued a broader mandate: **"complete/refine essentially all aspects — it works
but it's clunky and half baked."** That triggered a 4-persona UX audit (~102 raw findings deduped
to ~60 canonical, captured in `~/.claude/plans/refinement-spec-r-series.md`) and the **R-series
refinement program**: R-seed (#414) + R1/R4/R2/R3/R5/R7 (#416–#421), six of seven planned slices
landed (R6 was satisfied structurally by R-seed, not built separately). A seventh PR, **#415
(FF4)**, was built, reviewed, and deliberately left **unmerged** — a daemon alert-severity posture
change the operator reserved for himself. 15 PRs landed, all four-part verified; zero Python files
touched anywhere in the merged range (`git diff --stat 780cacd..c350f09 -- '*.py'` → empty).

## The mandate — from "clunky and half baked" to a refinement program

The Assigned-Tasks feature (S1–S6+T) shipped complete and reviewed-clean by 2026-07-02 03:44
(#412's merge). The operator's return, deploy, and smoke surfaced a symptom that read like a
missing feature (the daily checklist wasn't showing) but traced to a stale-deploy gap — code had
merged to `main` faster than the Worker had been redeployed against it. Once resolved, the operator
did not stop at "it works" — the directive was a full completeness pass across the whole feature
surface. Rather than patch findings ad hoc, the response was a **4-persona audit** (manager daily
flow / admin authoring / subcontractor + tasks / cross-cutting) producing ~102 raw findings, deduped
to ~60 canonical items, written up as a full slice-by-slice spec with acceptance criteria, a
deferred/won't-do list, and four open questions for the operator — before any refinement code was
written. The spec's own diagnosis (§1, "why it reads half baked") names eight root-cause classes:
inverted/unbounded lists, dead-end authoring paths with no diagnosis, raw wire-code/enum vocabulary
in the UI, half-plumbed admin CRUD, silent failure and lying empty states, navigation dead ends,
split-brain admin surfaces, and trust/attribution gaps. Every R-slice below maps back to one or more
of these.

## PRs landed — Assigned-Tasks feature (7 slices, spec `~/.claude/plans/spec_assigned-tasks-tab.md`)

Build pattern held across all seven: a per-slice Workflow (build agent + `portal-worker-security-reviewer`
+ `ops-stds-enforcer` in an isolated worktree) → CC integrates the diff and fixes review findings →
gate check → squash-merge → four-part verify. All portal-only (D1 migrations + Worker + SPA);
pytest untouched per slice except where noted.

- **#406 `86a3b8c` — S1: My-Tasks tab + manager full task authority.** Migration `0025`.
  `GET /api/fieldops/tasks/mine` + new "My Tasks" HomePage card. Managers gain `cap.tasks.assign`
  (deliberately reverses the P2.6 "manager no-task-create" invariant, documented); a
  subcontractor-target guard blocks a manager from assigning to another manager/admin. Two review
  rounds: **W1** current-owner guard (a manager can only touch an unassigned or submitter-held
  task), **W2** double-link guard (`/personnel/:id/link` now rejects linking a username already
  linked to a different active personnel — closed a `/tasks/mine` cross-user leak since
  `personnel.username` isn't UNIQUE). Retire stays admin-only.

  PR #406 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-01T22:19:22Z
  - mergeCommit: 86a3b8c
  - main CI on merge commit: SUCCESS

- **#407 `60d84bd` — S2: checklist engine (schema + admin editor).** Migration `0026`
  (`checklist_templates`/`items`/`instances`/`item_states` + daily-default seed). The
  templates→instances engine and effective-merge read (default-minus-suppressed ∪ per-job
  additions); admin `DailyChecklistEditor` on the Job Tracker job detail. **W4 BLOCK** (first
  instance of the recurring audit-atomicity pattern, see below): the default-item-delete audit was
  reading `changes()` off the wrong statement in a 3-statement batch, so deleting an unsuppressed
  item wrote no audit row — reordered so the DELETE is the last mutation before the audit read;
  test asserts the row now exists.

  PR #407 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-01T22:43:02Z
  - mergeCommit: 60d84bd
  - main CI on merge commit: SUCCESS

- **#408 `7a8d2b7` — S3: daily-checklist generation + manual-attest completion.** No new
  migration. `generateDailyInstance` (Worker-on-read, manager-only, idempotent via
  `INSERT OR IGNORE` on the 0026 UNIQUE key) + ownership-scoped complete/uncomplete for
  `manual_attest` items. Reviewed CLEAN, no BLOCK — all 9 audited properties held on first pass.

  PR #408 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T01:38:10Z
  - mergeCommit: 7a8d2b7
  - main CI on merge commit: SUCCESS

- **#409 `5111bad` — S4: loop-closure + count/inspection completion.** No new migration.
  `reconcileFormLinked` auto-marks a checklist item done when a matching form submission exists for
  `(job, form_code family, instance_date)`; count items complete at/above `target_count`, else 400
  `below_target`. **1 BLOCK**: the below-target write recorded `value_num` with no paired audit
  row — a repeated below-target post silently overwrote the prior value with no trail. Fixed in
  the same batch (+test); a follow-up typecheck slip (variable used before declaration) from the
  fix itself was caught and corrected in a second commit.

  PR #409 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T02:07:34Z
  - mergeCommit: 5111bad
  - main CI on merge commit: SUCCESS

- **#410 `f6218b9` — S5: Daily-Report auto-rollup.** No new migration (`rolled_up_submission_uuid`
  pre-existed in 0026). `GET /checklist/mine/rollup-draft` assembles a best-effort `daily-report-v1`
  draft (crew, equipment, checklist outcomes) — narrative fields left blank, nothing fabricated;
  the manager reviews/edits/submits via the **unchanged** `/api/submit` path. Both reviewers
  confirmed the Invariant-1 boundary intact: the rollup only assembles values, filing still rides
  the existing human-reviewed submit→intake→approved-send pipeline. Reviewed CLEAN.

  PR #410 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T02:33:23Z
  - mergeCommit: f6218b9
  - main CI on merge commit: SUCCESS

- **#411 `1054110` — S6: generic-inspection library + assign.** No new migration (reuses 0026
  `generic_inspection`/`inspection` kinds). Library CRUD, `POST /checklist/assign` (validates
  template kind/assignee/job/due-date, dedups on job+date), `GET /checklist/assigned` for managers
  **and** subcontractors, reusing S3/S4's ownership-agnostic completion routes. **Second instance
  of the W4 audit-atomicity pattern**: the assign-instance INSERT ran standalone from its audit —
  a mid-request failure could orphan an un-audited, empty, permanently-uncompletable instance
  (poisoned by the UNIQUE key). Fixed by batching the INSERT + audit atomically, with the snapshot
  self-healing a partial instead of orphaning it.

  PR #411 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T03:11:17Z
  - mergeCommit: 1054110
  - main CI on merge commit: SUCCESS

- **#412 `02ca9af` — Slice T: subcontractor tier (final slice).** Migration `0027`
  (`cap.crew.create` grant + `personnel.created_by` column). Display-only role rename
  (`submitter` → "Subcontractor" in UI copy; the role **key**, API values, and `auth.ts`
  fail-safe default are unchanged — locked by a corruption test). `POST /api/fieldops/crew`
  creates a non-login roster person, server-resolves `current_job` (never client-supplied),
  stamps `created_by`. Time-logging scoped to {self, created_by=self} for a subcontractor without
  `cap.personnel.manage`. One accepted benign W5 (read-then-write staleness on `current_job`,
  documented in-code). This completes the Assigned-Tasks feature end to end.

  PR #412 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T03:44:16Z
  - mergeCommit: 02ca9af
  - main CI on merge commit: SUCCESS

## PR landed — design-language polish

- **#413 `a5f980b` — Design-language consistency pass + ¼-size chip-x.** Purely presentational
  (className/CSS only, adversarially reviewed CLEAN for zero logic/auth/data drift). Adds the
  missing `btn` base class to bare `btn--*` buttons; remaps an invisible page-context
  `btn--ghost` on the inspection picker and checklist done-state to `btn--secondary`; shrinks the
  oversized inline ✕ (the crew/CC remove-X) to a new `.chip-x` class at roughly a quarter the
  prior footprint.

  PR #413 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T13:21:59Z
  - mergeCommit: a5f980b
  - main CI on merge commit: SUCCESS

## PRs landed — R-series refinement program (spec `~/.claude/plans/refinement-spec-r-series.md`)

Execution order: **R-seed(0028) → R1(0029) → R2 ∥ R3 ∥ R4 (parallel worktrees, merged serially with
rebases) → R5 → R7.** Worker-touching slices (R1, R5, R7) got `portal-worker-security-reviewer` +
`ops-stds-enforcer`; SPA-only slices (R2, R3, R4) got a behavioral-regression/design review +
`ops-stds-enforcer`. R6 (daily-default content structure) was folded into R-seed — see Decisions.

- **#414 `4c8123d` — R-seed: real SOP checklist content (Mandatory D).** Migration `0028`
  replaces the placeholder daily-default with the **13 items from Evergreen's Site Supervisor SOP**
  (every item cites its SOP section, sourced from `~/Downloads/Site_Supervisor_SOP 2.docx`) and
  seeds **6 generic-inspection library templates** from the ER Safety Manual (Box "ER Safety Manual
  2025.pdf"). Sentinel + per-row guards make the migration re-apply-safe; idempotency concretely
  tested (all statements run twice in Miniflare, ids stable). Security review: one awareness note
  (title-as-key, inherited 0026 design — becomes its own tracked tech-debt item, see Open items).
  ops-stds: CLEAN, content matches the approved contract exactly.

  PR #414 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T14:34:52Z
  - mergeCommit: 4c8123d
  - main CI on merge commit: SUCCESS

- **#416 `545207d` — R1: worker contracts + errorCopy/labels foundation.** Migration `0029`
  (`template_title` snapshot + backfill). **Security fix**: own-only actors (`cap.tasks.own`) could
  previously flip any task's status regardless of ownership — now 403 `forbidden_task`.
  Open-first list ordering; assign-time 422s (empty template / form-linked without job+date /
  catalog-validated `form_code`); below-target acknowledge path (required note, distinct audit);
  reason-coded empty states; `assigned_by`/`project_name` context; Q3's on-or-before due-date
  closure for inspections; display-name-only `filed_by`/`rolled_up_by` attribution; hours bounds.
  New `errorCopy.ts` (every wire code → human copy) + `labels.ts`, wired into all four feature
  libs. Security review: **W9 fixed** — a raw-username fallback in the attribution path was
  replaced with display-name-only (tested); W5 TOCTOU accepted per existing precedent. ops-stds:
  a copy-consolidation gap (hand-rolled page translations not yet folded into the new map) was
  fixed before merge.

  PR #416 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T15:33:02Z
  - mergeCommit: 545207d
  - main CI on merge commit: SUCCESS

- **#417 `6b8e95a` — R4: consolidated admin Checklists area (Mandatory C).** SPA-only, no new
  routes/migration — drives the 15 existing worker routes. One "Checklists" admin area (default
  daily editor moved out of Job Tracker + the inspection library, with per-job add/remove/hide
  staying in Job Tracker via a cross-link). Shared `ChecklistItemForm` kills two verbatim
  duplicates; `form_code` becomes a catalog `<select>` (no more free-text typos); confirm-before-destroy
  on every delete path with blast-radius copy; read-only assignee-view preview. Regression review:
  one WARN fixed (per-job Remove wasn't confirm-gated, now is, test-asserted). ops-stds: CLEAN.

  PR #417 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T16:11:17Z
  - mergeCommit: 6b8e95a
  - main CI on merge commit: SUCCESS

- **#418 `491c9c0` — R2: My Tasks two-tab restructure + never-silent hardening (Mandatory A+B).**
  Two tabs ("Assigned tasks" / "Daily checklist") with reason-coded empty states; every fetch
  failure now surfaces a visible error with Retry — loading, empty, and error states are mutually
  exclusive everywhere on the page; a successful mutation whose refetch hiccups is never reported
  as "Update failed." Day-rollover guard at both render time (stale banner) and action time (a
  desktop tab open across midnight can't silently write onto yesterday's checklist). Regression
  review: 3 WARNs fixed (a sibling-task revert that clobbered an unrelated row's optimistic state,
  the action-time rollover guard, a tab-pinning bug). Playwright acceptance was deliberately
  covered as jsdom tests — no live Worker available in the worktree; flagged for the operator to
  smoke on deploy. ops-stds: CLEAN.

  PR #418 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T16:24:55Z
  - mergeCommit: 491c9c0
  - main CI on merge commit: SUCCESS

- **#419 `620d832` — R3: form-loop round trip + item interactions.** Checklist deep-link → form →
  submit → "Back to My Tasks" (no more dead-end at Home); phone back button walks views instead of
  exiting the portal; dirty-form confirm. Count items: numeric keypad, frozen-when-done with Undo,
  below-target acknowledge wired live. Attest items: photo-evidence **render half** (see Open
  items — capture half not built) + edit-note that never clobbers `photo_ref`. Dead deep-links now
  explain themselves instead of silently disabling. Regression review: no BLOCK — both WARNs were
  the S3/S4 integration wire-ups, completed in this same PR. ops-stds: CLEAN.

  PR #419 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T16:37:38Z
  - mergeCommit: 620d832
  - main CI on merge commit: SUCCESS

- **#420 `a889f2b` — R5: assignment lifecycle (list + cancel + guarded assign).** New
  `GET /checklist/instances` (admin outstanding-assignments list) + `POST
  /checklist/instance/:id/cancel` (`cap.checklist.manage`-gated). **Cancel is an atomic
  hard-delete + audit, not a soft-cancel** — soft-cancel was deliberately rejected because a
  cancelled-but-retained row would poison the 0026 UNIQUE dedupe key, blocking a clean
  cancel→re-assign. The four stuck-assignment classes (empty template, form-linked without
  job+date, unknown form code, duplicate double-tap) are now unreachable from the UI and still
  independently rejected server-side (R1's 422s). Security review: **CLEAN, no BLOCK/WARN**
  (live-tested 403 gating, row-bound query cap, double-cancel single-audit behavior, daily-kind
  404). ops-stds: CLEAN.

  PR #420 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T16:49:31Z
  - mergeCommit: a889f2b
  - main CI on merge commit: SUCCESS

- **#421 `c350f09` — R7: time/task attribution + HomePage grouping + final swallow closures
  (closes the refinement program).** "Me (<name>)" default for time entries (server-resolved
  personnel id, distinct from an explicit job-level/unassigned entry); Task + By columns on the
  time-entries table — **display-name-only**, never a raw username. Assignability-gated pickers
  (manager/no-login hints); `account_role` now exposed only to assign-capable viewers. HomePage
  regrouped into Daily forms / Field operations / Administration sections, "Checklists" rename,
  every card's gating locked by a test table. `.chip-x` gets a bigger hit area + two-step confirm.
  The last 8 silent-swallow sites on the feature closed — zero catch-into-empty remaining,
  grep-verified against the original A4 audit inventory. Security review: **1 BLOCK fixed**
  (a raw username was exposed in the Task/By columns — replaced with display-name-only, matching
  R1's W9 posture; re-enforces it as the standing attribution rule for the whole feature) + 1 WARN
  fixed (`account_role` gating). ops-stds: WARN only, accepted as an in-spec breadth judgment call
  (no BLOCK).

  PR #421 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T17:51:23Z
  - mergeCommit: c350f09
  - main CI on merge commit: SUCCESS

## PR held, not merged — #415 (FF4, portal_poll transient-error severity)

**`fix/ff4-portal-poll-transient` — WARN+skip on transient raw Smartsheet errors, HELD for
operator severity sign-off.** Built, tested, and reviewed to completion but **deliberately not
merged**: alert-severity posture on a live safety daemon is operator-owned, per the 2026-07-01
handoff. Before building, CC verified the forensic claim behind the brief against live HEAD
(forensic-class-#3 discipline — don't act on a stale current-state claim): the *named* bug (a
circuit-open false CRITICAL) was **already fixed by #399** the prior session. The residual gap FF4
actually closes is narrower — the Smartsheet circuit breaker only opens after 5 failures, so a
single-cycle blip (one 429/5xx) still raised a raw, uncaught `SmartsheetError` that propagated to
a misleading `CRITICAL uncaught_exception` triple-fire with no heartbeat. FF4 routes that raw-blip
case to the same WARN+skip path as the already-fixed circuit-open case, keeps Auth/Permission and
genuinely-missing creds as CRITICAL, and adds a `portal_config_transient` WARN for the
`polling_enabled` config read. ops-stds review: no BLOCK (one doc WARN — a runbook cause-text gap —
fixed in the PR). Gate on the PR's own branch: pytest 2143 passed, mypy clean, ruff clean. **Not
counted as landed** — no mergeCommit, no main-branch CI run exists for it; it stays an open PR
pending Seth's review.

## CI / four-part verify — session-level gate

All 15 merged PRs (#406–#414, #416–#421) independently returned `state=MERGED`, `mergedAt`
non-null, `mergeCommit.oid` present, and a `push: main` CI run on the merge commit with
`conclusion: success` — confirmed both via `gh pr view --json state,mergedAt,mergeCommit` and
`gh run list --json headSha,conclusion` filtered to `event=="push"` for every SHA above. #415 was
never merged and correctly has no merge commit / no main-branch CI run.

Final integrated tree (post-#421, main `c350f09`):

- pytest: **2143 passed / 47 deselected** (run on the FF4 branch — the only branch touching
  Python this session; main's Python surface was untouched by every merged PR, confirmed by
  `git diff --stat 780cacd..c350f09 -- '*.py'` returning empty)
- mypy: **0 errors / 246 source files**
- ruff: clean
- main-branch CI on merge commit `c350f09` (and on each of the other 14 merge commits above):
  **SUCCESS**

Worker/SPA test growth across the arc (from each PR's own gate line): worker vitest 423 (#406) →
433 (#407) → 446 (#408) → 460 (#409) → 469 (#410) → 480 (#411) → 498 (#412, untouched by #413) →
502 (#414) → 535 (#416, untouched by #417/#418/#419) → 542 (#420) → **546** (#421, final). SPA
vitest 157 (#406) → 162 (#407) → 165 (#408) → 168 (#409) → 173 (#410) → 184 (#411) → 191
(#412/#413, untouched by #414) → 221 (#417) → 266 (#418) → 294 (#419) → 310 (#420) → **349**
(#421, final).

## Recurring review catch — W4 audit-atomicity (S2, S4, S6)

The same defect class recurred three times across the Assigned-Tasks build: a mutation and its
audit row were written in **separate, non-atomic statements**, so a mid-request failure could land
the mutation without its audit trail (S2: a default-item delete with no audit row; S4: a
below-target count write silently overwriting `value_num` with no trail; S6: an assign-instance
INSERT that could orphan an un-audited, permanently-uncompletable instance under the UNIQUE key).
`portal-worker-security-reviewer` caught all three before merge; each fix batches the mutation and
its audit into one D1 statement and adds a test asserting the audit row exists. This is the same
"adversarial review is definition-of-done on a trust-boundary surface" discipline from CLAUDE.md
(forensic classes #9/#14) — three independent instances in one feature build is a strong signal
the pattern is worth naming explicitly rather than re-discovering per slice (see Open items).

## Decisions made during session

1. **Per-manager daily checklists, no de-confliction — operator decision, captured to memory.**
   The `checklist_instances UNIQUE(kind, job_id, assignee_personnel_id, instance_date)` key makes
   each placed manager on a job roll their **own** daily instance and, potentially, file their own
   Daily Report for the same job+date. This is intended, not a bug: the office reconciles
   duplicate Daily-Report submissions manually rather than engineering merge/dedupe logic.
   Recorded as `decision_daily-checklist-per-manager-no-deconfliction` (memory). One open nuance
   flagged for the operator: the S5 rollup-reconcile currently keys on `(job, date)`, so the first
   manager to file the Daily Report flips co-managers' checklists to "filed ✓" too — the likely-
   wanted fix is a **per-manager** rollup flag, pending confirmation (compatible with, not a
   reversal of, this decision).

2. **Four open-question defaults chosen in absentia, all flagged reversible.** With the operator
   away, R-seed/R1 needed answers to proceed. Q1 (SOP content) was answered concretely from
   `~/Downloads/Site_Supervisor_SOP 2.docx` + the Box ER Safety Manual — not a default, real
   content. The remaining three were CC judgment calls, documented in the spec's execution
   addendum for operator override on return: **Q2 (photo evidence) = (a)** optional photo on every
   check-type item, no migration; **Q3 (due-date semantics) = proposed default**, inspection
   form-linked items close on filing on-or-before the due date (daily unchanged); **Q4 (admin IA)
   = one Home card "Checklists"** (default daily + inspection library), per-job add/hide stays in
   Job Tracker with cross-links. None required a migration that would be costly to reverse.

3. **R5: hard-delete over soft-cancel for assignment cancellation.** A soft-cancel (status flag,
   row retained) was considered and rejected — a retained cancelled row would poison the 0026
   `UNIQUE(kind, job_id, assignee_personnel_id, instance_date)` dedupe key, permanently blocking a
   clean cancel-then-reassign for the same person/job/date. Cancel is instead an atomic hard-delete
   + audit row in one batch, tested including a cancel→re-assign round trip.

4. **Display-name-only attribution is now the standing rule for the whole feature, not a one-off
   fix.** First surfaced as a security fix in R1 (W9: a raw-username fallback in `filed_by`/
   `rolled_up_by` attribution), it recurred as an R7 review BLOCK on the Job Tracker time-entries
   Task/By columns — a raw username would have been exposed there too. Both instances were fixed
   before merge; the posture (never render a raw `username`, only the resolved display name) is
   now enforced feature-wide.

5. **R6 satisfied structurally by R-seed; the standalone content-module refactor was skipped
   (§14).** R6's stated goal was a content structure that lets real SOP data "drop in ... no code
   archaeology required." Once R-seed (#414) landed the real content directly via migration 0028,
   a separate data-module extraction would have refactored a one-time seed with no remaining
   consumer benefit — the admin authoring UI (R4) is the actual go-forward content-edit path.
   Preservation-over-refactor: don't build the structural layer once the concrete need it was
   solving is already met a different way.

6. **#415 (FF4) built and reviewed but deliberately held unmerged.** Daemon alert-severity
   posture (what counts as WARN vs. CRITICAL on a live production safety daemon) was scoped by the
   operator as his own call in the 2026-07-01 handoff, not an autonomous-session decision. CC
   completed the engineering work (including the brief-validator discipline of verifying the
   forensic claim against live HEAD before building) and stopped short of merging, leaving the PR
   open for Seth's sign-off rather than treating "reviewed clean" as license to land a severity
   change unattended.

7. **R2/R3/R4 built in parallel worktrees, merged serially with rebases.** Per the spec's
   dependency map, R2/R3/R4 touch disjoint files and can build concurrently once R1 lands the
   shared contracts; each was rebased onto the previous slice's merged `main` before its own merge
   (R4 → R2 → R3, per the gate-line progression) rather than merged as three independent PRs off
   a common stale base — avoiding the stale-base landing hazard already documented from the prior
   session (#399/#397 sequencing).

## Open items / next session

- **Checklist item-state photo capture is a render-half-only gap.** R3 (#419) ships the display of
  a `photo_ref` when present; nothing writes one. Capture needs its own §34-image-class-screening
  design pass (Op Stds v19 §34; `safety_reports/photo_screen.py` is the canonical instantiation for
  portal photos) **before** any storage route ships — explicitly flagged NOT autonomous-safe, needs
  an adversarial attacker/auditor/skeptic review as its own reviewed slice. Tracked in
  `docs/tech_debt.md` ("Checklist item-state photo CAPTURE," OPEN 2026-07-02).
- **Six named R-series deferrals (spec §3, Deferred #5–10)** — mid-day template re-sync into open
  instances, mid-day job-reassignment orphan-instance handling, scoped crew edit/retire + time
  amend/void UI, server-side completed-history cutoff, a full URL router (R3 ships minimal
  hash/history only), and a `task_assignments.due_date` column. All locked-decision scope cuts, not
  regressions — captured in `docs/tech_debt.md` so they aren't rediscovered as bugs.
- **Checklist template identity is title-keyed (0026 design).** An admin-authored template whose
  title exactly matches a future seed migration's title would silently merge items into it rather
  than creating a separate template. Low blast radius today (no known collision); fix is a stable
  `code`/slug column, deferred per §14 until the library grows past the seeded set.
- **3 of 5 granted-but-never-enforced capabilities remain open** (`cap.form.submit`,
  `cap.form.request`, `cap.inspection.job`) — `cap.tasks.assign` (S1) and `cap.checklist.manage`
  (S2/R1/R4/R5) are now RESOLVED and enforced; the remaining three still gate on `requireSession`/
  `requireRole('admin')` instead of their named capability.
- **Operator deploy queue accumulated across the arc**: migrations `0025` (#406) → `0026` (#407)
  → `0027` (#412) → `0028` (#414) → `0029` (#416), applied `--remote` **in order**, then
  `npm run deploy`. This is the deploy the operator's return-smoke already exercised once
  (surfacing the stale-Worker symptom that triggered the R-series mandate); confirm the SPA build
  served now reflects `c350f09` (R7, the final merged commit).
- **#415 (FF4) awaits Seth's severity-posture sign-off** — reviewed clean, not merged. Next session
  should either merge it as-is or discuss the accepted behavior note in the PR body (a
  `polling_enabled`-read blip now lets the daemon cycle proceed past the gate instead of aborting,
  typically self-resolving into the creds-transient skip one call later).
- **Worktree cleanup** for the R2/R3/R4/R5/R7/R-seed/R1/FF4 branches once #415 is resolved.
- **R2's Playwright acceptance criteria were covered as jsdom tests only** (no live Worker was
  available in the worktree) — flagged for the operator to run a live Playwright smoke against the
  deployed portal on next hands-on session.

## What was NOT touched

- **No Python file changed anywhere in the merged range.** `git diff --stat 780cacd..c350f09 --
  '*.py'` is empty; `shared/`, `safety_reports/`, `progress_reports/`, `field_ops/` Python modules
  are byte-identical to before this session. #415 is Python-only but unmerged.
- **No external send path changed.** Invariant 1 intact — S5's Daily-Report rollup assembles
  values only; filing still rides the unchanged, human-reviewed `/api/submit` → intake →
  approved-send pipeline. No new send/AI capability anywhere in the Worker.
- **No doctrine edits.** Op Stds v19 citations, §§, and invariants are unchanged; `docs/tech_debt.md`
  is the only docs-side edit this session (uncommitted in the working tree, left for this log's
  landing PR).
- **Photo capture (write half), the full URL router, the template code/slug column, the
  per-manager rollup-flag fix, and mid-day template re-sync/orphan-instance handling** were all
  scoped and deliberately NOT built this session (see Open items).
- **#415's severity-posture change is NOT live** — the daemon still runs the #399-only fix; the
  residual raw-transient-blip gap FF4 closes remains open in production until merged.

## Cross-references

- Prior session (P2.5 cutover + P2.6 Manager tier + FF4/FF5 hardening):
  `docs/session_logs/2026-07-01_manager-tier-ff4-ff5-cutover.md`
- Prior session (P2.5 cutover LIVE + P6 rollup + concurrency audit):
  `docs/session_logs/2026-07-01_p2.5-cutover-live-p6-rollup.md`
- Specs: `~/.claude/plans/spec_assigned-tasks-tab.md` (S1–S6+T),
  `~/.claude/plans/refinement-spec-r-series.md` (R-series, incl. the execution addendum recording
  Q1–Q4 defaults chosen in absentia), `~/.claude/plans/sop-daily-checklist-content.md` (R-seed
  content contract)
- Op Stds v19 §14 (parameterize/preservation — R6 fold-in, template-code deferral), §30 (integration
  discipline), §34 (image-class screening — the photo-capture gate), §42/§43 (self-documentation +
  runbooks: `docs/runbooks/fieldops_checklists.md`, `docs/runbooks/manager_tier.md`,
  `docs/runbooks/subcontractor_tier.md`), §50/§51 (D1-as-writer / SoR write-back — unaffected this
  session, no Smartsheet writes)
- Enablement docs: `docs/enablement/fieldops_checklists.md`, `docs/enablement/manager_tier.md`,
  `docs/enablement/subcontractor_tier.md`
- PR merge discipline: `docs/operations/pr_merge_discipline.md`; verifier agent:
  `.claude/agents/pr-landed-verifier.md`
- Adversarial review rule (DoD): CLAUDE.md "Operational conventions — load-bearing" (W4
  audit-atomicity recurrence is a direct instance; forensic classes #9/#14)
- Don't-act-on-a-stale-claim discipline: CLAUDE.md "What NOT to do" (forensic class #3) — applied
  by CC before building #415, confirming #399 already fixed the named bug
- Tech-debt: `docs/tech_debt.md` — photo-capture gap, R-series Deferred #5–10, template title-key
  collision, capability-enforcement fan-out (2 of 5 resolved this arc)
- Memory: `decision_daily-checklist-per-manager-no-deconfliction` (new this session),
  `project_fieldops-portal-program` (Assigned-Tasks + R-series sections to append)
