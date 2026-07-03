---
type: session_log
date: 2026-07-03
status: closed
workstream: field_ops
related_prs: [423, 424, 425, 426, 427, 428, 429, 430, 431, 432]
tags: [session_log, field-ops, sop-daily-form, checklist-retirement, material-receipts, design-refinement, optimization-sweep, form-definition-reviewer, portal-worker-security-reviewer, ops-stds-enforcer, append-only-versioning, w4-audit-atomicity, requirejobscope, wire-types, never-silent, held-pr]
---

# Session — SOP-Daily-Form redesign + Material Receipts + design-refinement + optimization sweep (PRs #423–#432)

Continuation of the 2026-07-02 arc (`2026-07-02_assigned-tasks-and-r-series-refinement.md`), covering
2026-07-02 evening through 2026-07-03 early morning. The operator rejected the checkbox daily
checklist outright and redirected the whole daily-report surface toward a new architecture: **the
Site Supervisor SOP itself, rendered as a guided fillable form.** That redesign shipped as four
sequential slices (D1–D4, #423/#424/#425/#427), interleaved with a two-slice Material Receipts
feature (M1/M2, #426/#428) that rides the same daily form, a frontend-design pass (#429), and a
three-slice optimization sweep (#430/#431/#432) closing out a 16-finding audit of the whole
Assigned-Tasks + SOP-form surface. All 10 PRs landed, all four-part verified. Unlike the prior
session's arc (zero Python touched), this one **does** touch Python — `safety_reports/form_pdf.py`
gained renderer support for the new `guidance`/`form_link` section types, the photo-grid swap, the
per-job requirements overlay, and the material-incident/receipts tables, across #423/#425/#427/#428.

## The pivot — checkbox checklist rejected, SOP-as-form chosen

The R-series program (prior session) had polished the checkbox daily-checklist engine end to end.
The operator's redirect discarded that UI model entirely in favor of rendering the actual Site
Supervisor SOP document as the daily form. Four operator-locked answers shaped the whole
redesign, all captured in `~/.claude/plans/spec_sop-daily-form.md` and executed literally:

1. **Fillable fields live under their SOP sections** — not a separate checklist bolted alongside
   the SOP text; the form *is* the SOP, byte-verbatim, with entry fields inline.
2. **JHA / Visitor Sign-In / Incident Report are real links with live filed-indicators** — not
   "mark done" checkboxes. Tapping one opens the actual form; a green Filed ✓ badge appears once
   it's been submitted for that job+date.
3. **Stays in the same `daily-report` form family** — the office weekly-compile pipeline
   (`weekly_generate.py`, family-based) is untouched; this is a definition/version change, not a
   new pipeline.
4. **The form definition itself stays admin-editable** — the per-job requirements overlay (D4)
   and the R4 admin Checklists area both exist so client-specific asks don't require a code change.

D1 (#423) built the new section types and cut `daily-report-v2` (all 110 SOP units byte-verified
against `~/Downloads/Site_Supervisor_SOP 2.docx`). D2 (#424) made the Daily tab render that
definition directly and retired the checkbox engine's UI surfaces (the engine itself stays —
inspections still use it). D3 (#425) and D4 (#427) then iterated the content: photo-count
language out, an upload field in; per-job custom requirements overlaid at render time.

## PRs landed — SOP-Daily-Form redesign (D1–D4)

- **#423 `87b6d2d` — D1: the SOP daily form (guidance/form_link section types +
  daily-report-v2).** New strictly-validated definition section types `guidance` (SOP prose,
  bullets bounded — a security NIT fixed pre-merge) and `form_link` (real deep links to
  JHA/Visitor-Sign-In/Incident-Report, no "mark done"). `daily-report-v2` (42 sections, 110
  byte-verified SOP units) supersedes v1 — v1 stays in-tree, append-only, both versions render
  and are test-locked. SPA renderer (callout styles + a `FormLinkAdapter` hook reserved for D2) and
  PDF renderer (`form_pdf.py`) gain matching support — headings + callouts only, documented scope.
  **Operator-confirm flagged, not yet resolved**: `required-content.json` gains a strict
  `daily-report` legal-floor entry (9 required keys, the v1∩v2 intersection so both versions
  satisfy it — strictly additive, nothing dropped) — marked `PENDING OPERATOR CONFIRMATION` in-file.
  Reviews (form-definition-reviewer / security / ops-stds): no BLOCKs.

  PR #423 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T22:30:45Z
  - mergeCommit: 87b6d2d
  - main CI on merge commit: SUCCESS

- **#424 `0e35606` — D2: the Daily tab IS the SOP form.** Date selector → the full SOP renders
  inline with fillable fields → real "Create JHA →" links carrying live Filed ✓ indicators →
  submits through the unchanged office pipeline. The checkbox daily checklist, the admin
  default-checklist editor, and the per-job daily-checklist editor are retired from the SPA
  (inspections keep the engine — only the *daily* UI surfaces go). **Two review BLOCKs fixed,
  both test-locked**: (1) **security** — the status endpoint is now per-job **ownership-scoped**
  (a subcontractor could otherwise probe other jobs' incident/JHA filing activity via the status
  route; fixed to 403 `forbidden_job`, admins unrestricted); (2) **regression** — **draft
  persistence**: tapping a form-link used to unmount the Daily tab and silently destroy the day's
  typed-but-unsaved work; drafts now persist per (job, date), restore on return, clear on submit.

  PR #424 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-02T23:30:35Z
  - mergeCommit: 0e35606
  - main CI on merge commit: SUCCESS

- **#425 `1f75993` — D3: photo minimum out, Site-photos upload in (daily-report-v3).** Operator-
  directed content edit. `daily-report-v3` cut — the **first live proof of the append-only-by-
  mechanism versioning discovery** made during this redesign (v2 stays untouched, verified in
  code). The D.12 "minimum 50 photos" language is removed (the *what-to-photograph* guidance stays
  verbatim; a related END-OF-DAY 50+ bullet fan-out is also fixed — flagged for veto if unwanted).
  A **Site photos** upload field takes its place, riding the existing Worker-bounds →
  §34-screen → PDF-grid pipeline with **zero pipeline code changes** (≤4 photos/field, ≤8/
  submission — the same honest bound as elsewhere in the system). Reviews CLEAN (form-definition,
  security). Deploy-only, no migration.

  PR #425 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T02:35:36Z
  - mergeCommit: 1f75993
  - main CI on merge commit: SUCCESS

- **#427 `13c3ed9` — D4: per-job daily-form requirements (0030, daily-report-v4).** Migration
  `0030`. Admins tailor the daily SOP form **per job** as client requirements develop: a
  "Job-specific requirements" editor on each job's detail (note / confirm / text / form-link items)
  renders inside every manager's daily form for that job and **files with the submission**
  (self-describing values, so historical PDFs stay stable even if the overlay later changes).
  Per-job ownership scoping on the read (same pattern as D2's status-endpoint fix); W4 atomic
  audits. `daily-report-v4` cut append-only (v3 untouched, semantic-diff-exact). Reviews:
  form-definition CLEAN, ops-stds CLEAN, security **WARN-only** (an accepted ceiling-TOCTOU class,
  consistent with the R1/R7 precedent from the prior session). Rebased over M1 (#426).

  PR #427 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T03:23:44Z
  - mergeCommit: 13c3ed9
  - main CI on merge commit: SUCCESS

## PRs landed — Material Receipts (M1/M2)

- **#426 `224273b` — M1: expected materials per job (0031).** Migration `0031`. Office adds
  **expected materials** per job — catalog-picked or free-text, at job creation or as the job
  develops. `job_expected_materials`: catalog-linked (nullable, validated ACTIVE) or free-text
  rows; status `expected|received|incident` (CHECK); update confined to `status='expected'` —
  a receipt record, once written, is not rewritable. Per-job ownership scoping on every non-admin
  read/action, mirroring the D2/D4 pattern; guard-in-WHERE on receive (a double-action 409s,
  exactly one audit row survives); `received_by` stores the account username, reads resolve
  display-name-only (the W9 posture from the prior session, reused here without prompting).
  Managers get a read-only view now; confirm-receipt is deliberately deferred to M2. Reviews:
  security CLEAN (no BLOCK/WARN), ops-stds CLEAN. **Operator action flagged**: apply `0031`
  before the next deploy.

  PR #426 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T03:10:45Z
  - mergeCommit: 224273b
  - main CI on merge commit: SUCCESS

- **#428 `10b5187` — M2: receipt flow in the daily form + material-incident form (v5).** Managers
  handle deliveries **inside the daily SOP form's D.13 section**: each pending expected-material
  row offers **Confirm receipt** (flips the row, records it in the filed report's Deliveries
  table) and **Report a problem →** (flags the row and deep-links a new **Material Incident
  Report**, prefilled with the material; photos ride the existing §34 pipeline; live Filed ✓).
  **Zero new mutation surfaces** — M2 reuses M1's already-reviewed receive/flag routes
  byte-identically; no migration. `daily-report-v5` cut append-only. `material-incident-v1` is a
  **new form identity** — **two operator-confirm flags left PENDING in-file**: the required-content
  strict floor (`material_description` + `issue` + `details` — the incident's evidentiary
  minimum), and the `category: progress` classification itself (commercial, not safety — explicitly
  flagged for operator veto in both the spec and the JSON comment, in case it should ride the
  safety stream instead). Reviews: security CLEAN, form-definition/ops-stds no-BLOCK
  (operator-confirm flags only).

  PR #428 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T04:08:18Z
  - mergeCommit: 10b5187
  - main CI on merge commit: SUCCESS

## PR landed — design-refinement pass

- **#429 `51244a5` — design-refinement: signage type, the day-rail, hazard callouts, rhythm.**
  Built via the `frontend-design` skill against `~/.claude/plans/design-refinement-plan.md`. Adds
  **Barlow Semi Condensed** (self-hosted woff2, 47.5 KB, OFL-licensed, zero CSP change) as the
  structural/signage face — headings, eyebrows, pills, tabs, table headers, buttons; body text
  stays the 17px system stack for field legibility. Introduces **the day-rail** — a slim BRG rail
  with gold-ticked phase eyebrows (7:30 AM → END OF DAY) that makes the daily SOP form's chronology
  visible; opt-in prop, every other form byte-identical (test-pinned). CRITICAL-RULE callouts get
  a hazard-tape stripe edge, used exactly once in the system. 8px-grid rhythm tokens, HomePage
  signage eyebrows, press/filed micro-interactions (reduced-motion respected); all text pairs
  AA-documented, gold stays decorative-only. **Both reviews CLEAN** (design, ops-stds).

  PR #429 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T04:38:53Z
  - mergeCommit: 51244a5
  - main CI on merge commit: SUCCESS

## PRs landed — the optimization sweep (16 accepted findings, 3 slices)

An audit workflow scoped the whole Assigned-Tasks + SOP-form surface against
`~/.claude/plans/optimization-plan.md`, producing 16 accepted findings (numbered #1–#16 in the
plan's punch table) split into three slices by file-overlap and risk. Slices 1 and 3-core built in
parallel worktrees; the plan's own dependency note serializes Slice 3's one medium-risk tail item
(#12) after Slice 2 — see Decisions and Open items below for why #12 did **not** land this arc.

- **#430 `1d8ff1a` — Opt Slice 1: migrations punch-list, doc hygiene, test-helper
  consolidation.** Adds the **pending-migrations punch-list** to `safety_portal/README.md` — one
  table (0023–0031, PR + applied-live status) + one canonical apply-and-deploy command block —
  closing the universal-lockout-class documentation gap (also fixes a real omission found in the
  process: D4's `0030` had shipped with no activation section). Collapses a duplicate tech-debt
  entry. Consolidates 25 worker test files onto a shared `test/helpers.ts`, import-mechanically,
  **−800 lines**, worker suite landing at **exactly 621** (per-file `it()`-count parity verified by
  review — the exact number is the proof nothing silently dropped a test). Reviews: regression
  CLEAN, ops-stds clean post-rebase. Docs + tests only, no runtime change.

  PR #430 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T04:57:44Z
  - mergeCommit: 1d8ff1a
  - main CI on merge commit: SUCCESS

- **#431 `9aa4fb9` — Opt Slice 3: scope-gate extraction, audit dedupe, hot-path batching, wire
  types.** Behavior-preserving worker consolidation. New `worker/fieldops_scope.ts` extracts the
  **triplicated ownership gate** (`resolveActorPersonnel` / `requireJob` / `requireJobScope`,
  parameterized on each module's intentionally-divergent bypass-cap set) out of
  `fieldops_checklist.ts`, `fieldops_daily_requirements.ts`, and `fieldops_expected_materials.ts` —
  the same per-job scoping pattern D2/D4/M1 each independently hand-rolled becomes one helper,
  error shapes byte-identical. A new `auditStmtIfChanged` helper collapses **33 hand-rolled
  conditional-audit literals** (the `changes()=1` guard pattern) across 9 write modules into one
  call site. The one true O(N) hot-path read (`/checklist/assigned`) is batched, 3N+2 round trips
  down to 4. New `worker/wire-types.ts` gives the Worker and SPA **one shape source** for the
  jobtracker/daily-form/expected-materials/checklist-assigned payloads — the DailyReportTab test
  fixture now type-checks against what the Worker actually sends, not a hand-maintained copy (SPA
  libs in `fieldops_jobtracker.ts`/`fieldops_daily_form.ts`/`fieldops_expected_materials.ts`
  re-export from it; `fieldops_checklist.ts`'s `AssignedInspectionsResponse` does **not** yet — see
  Open items). Reviews: security **no BLOCK** (byte-identical extraction verified per call site),
  ops-stds WARN-only, both fix-its completed in-PR. **The plan's #12 waterfall-tail item was NOT
  executed in this PR** — deferred, see Decisions.

  PR #431 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T05:23:35Z
  - mergeCommit: 9aa4fb9
  - main CI on merge commit: SUCCESS

- **#432 `d7ba70f` — Opt Slice 2: draft debounce+flush+photo-strip, static memo, admin
  route-split, dead code — closes the sweep.** Fixes **the audit's #1 finding**: D2's draft
  persistence (the fix that stopped a form-link tap from destroying a day's typed work) was
  serializing base64 photo data into `sessionStorage` on every keystroke — a real field-path bug,
  since a quota overflow would silently defeat the very protection D2 shipped. Fix is debounce +
  **flush at every loss-moment** (unmount, (job,date)-key change, elapsed timer) plus a documented
  **photo-strip trade**: drafts no longer carry photo bytes across a save/restore cycle (typed
  text is protected; in-progress photo attachments are not — an accepted, explicit trade, not a
  silent one). Adds a day-rail-aware static-section memo (20 static SOP sections stop re-rendering
  per keystroke) and an admin route-split (main chunk **−49 kB / −8%**; field views stay eager;
  chunk-load failure now surfaces a visible error + Retry, never-silent). Removes 129 lines of dead
  client API surface (grep-verified). One review BLOCK (a flaky test) fixed **deterministically** —
  6/6 repeat runs green, not a retry-loop band-aid.

  PR #432 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-07-03T05:32:50Z
  - mergeCommit: d7ba70f
  - main CI on merge commit: SUCCESS

## CI / four-part verify — session-level gate

All 10 merged PRs (#423–#432) independently returned `state=MERGED`, `mergedAt` non-null,
`mergeCommit.oid` present, and a `push: main` CI run on the merge commit with `conclusion: success`
— confirmed via `gh pr view --json state,mergedAt,mergeCommit` per PR and `gh run list --json
headSha,conclusion,workflowName` filtered to each merge SHA above. `git log --oneline
d6e5ce6..d7ba70f` matches the 10 merge commits exactly, newest-first.

Final integrated tree (main `d7ba70f`):

- worker vitest: **639**
- SPA vitest: **419**
- pytest: **2222 passed / 47 deselected**
- mypy: **246 source files clean**
- ruff: clean
- main-branch CI on merge commit `d7ba70f` (and on each of the other 9 merge commits above):
  **SUCCESS**

Unlike the prior arc, Python was touched this session: `safety_reports/form_pdf.py` gained
renderer support for `guidance`/`form_link` sections (#423), the photo-grid swap (#425), the
per-job requirements overlay (#427), and material-incident/receipts tables (#428), with matching
growth in `tests/test_form_definitions.py`, `tests/test_form_pdf.py`, and
`tests/test_render_smoke.py` at each step — `git diff --stat d6e5ce6..d7ba70f` shows 122 files
changed, +12445/−2769 lines overall.

## Decisions made during session

1. **The SOP-as-form architecture — four operator-locked answers, executed literally.** Fields
   live under their SOP sections (not a separate checklist); JHA/Visitor-Sign-In/Incident-Report
   are real links with live Filed ✓ indicators (not "mark done"); the daily form stays inside the
   same `daily-report` family so the office weekly-compile pipeline is untouched; the form
   definition itself stays admin-editable (D4's per-job overlay + R4's admin Checklists area from
   the prior session). All four constraints shaped D1–D4 and were verified, not assumed —
   `daily-report-v2`'s 110 SOP units are byte-checked against the source `.docx`.

2. **Append-only-by-mechanism versioning, discovered in D1 and exercised through v2→v5 in one
   arc.** D1 established that a definition version cut is append-only and mechanically enforced
   (the prior version stays in-tree, untouched, both render, both are test-locked). Once that
   mechanism existed, D3/D4/M2 each treated a content or feature change as "cut the next version"
   rather than "edit the current one" — `daily-report` went v2 (#423) → v3 (#425, photo language) →
   v4 (#427, per-job overlay) → v5 (#428, receipt flow), plus a brand-new `material-incident-v1`
   identity (#428), all in roughly 30 hours, with zero regressions to historical submissions/PDFs
   because the mechanism — not manual discipline — enforces it.

3. **Per-job ownership scoping generalized into a standard, then extracted into a shared helper.**
   D2's review BLOCK (a subcontractor could probe another job's status via the daily-form status
   route) established the pattern: any job-scoped read gets 403 `forbidden_job` for a non-admin
   viewer outside their placement. D4 and M1 each re-applied the same pattern independently on
   their own new routes. Opt Slice 3 (#431) then extracted all three hand-rolled instances into
   one `requireJobScope` helper in `worker/fieldops_scope.ts` — the standard was proven live across
   three independent call sites before being consolidated, not designed abstractly up front.

4. **The photo-strip is an honest trade, not a silent regression.** Fixing the #1 optimization
   finding (draft persistence serializing photo bytes per keystroke) required choosing between
   "keep photos in the debounced draft and accept the quota-overflow risk" and "strip photos from
   the draft and accept that an in-progress (unsaved) photo attachment doesn't survive a save/
   restore cycle." #432 chose the latter and documented it explicitly in the PR and in code — typed
   text is protected (the original point of D2's fix), photo state is not, and that boundary is
   named rather than discovered later as a bug report.

5. **The R5 hard-delete-vs-soft-cancel decision framework was reused as the analysis tool for D4's
   and M1's deactivate routes — and yielded the opposite answer.** R5 (prior session, #420) chose
   an atomic hard-delete for checklist-instance cancellation specifically because a retained,
   soft-cancelled row would poison the 0026 `UNIQUE(kind, job_id, assignee_personnel_id,
   instance_date)` dedupe key. D4's per-job requirement `deactivate` route and M1's expected-
   material `delete` route both apply the same test (does a retained dead row block a future
   legitimate insert?) and land on **soft-delete** instead: neither `0030` nor `0031` carries an
   equivalent tight UNIQUE constraint, and the higher-value property here is that a historical
   daily-report PDF keeps referencing a requirement/material that has since been removed from the
   live schema (self-describing values, in-code comments explicit: "no hard delete").

6. **Opt Slice-3's #12 waterfall tail deliberately deferred, not silently dropped.** The
   optimization plan named `DailyReportTab`'s 2-stage 6-fetch waterfall (a full jobs-page download
   just to learn the viewer's own job placement) as finding #12, tagged **medium risk** and
   scoped to serialize after Slice 2 with its own `/security-review` gate (it widens a
   capability-gated read — `cap.tasks.own` would start returning placement data that today rides
   `cap.jobtracker.read`). #431 shipped Slice 3's other six findings (#2, #4, #9, #11, #13, #14)
   but left #12 unbuilt this arc; `DailyReportTab.tsx` still calls `fetchJobList("active")` for
   placement. This is a scope cut recorded in the plan itself, not a regression — tracked as an
   open item below.

7. **`category: progress` for the new material-incident form — operator-vetoable, flagged
   in-file, not silently decided.** A material-delivery problem report reads as "progress/
   commercial," not "safety," so M2 classified it `category: progress` — but flagged the choice
   explicitly for operator override (both in the spec and in the shipped JSON's `PENDING OPERATOR
   CONFIRMATION` comment) in case the operator wants it in the safety stream instead. Paired with
   the required-content floor (`material_description` + `issue` + `details`), also PENDING.

## Open items / next session

- **Operator deploy queue: migrations `0030` + `0031`, then redeploy.** `safety_portal/README.md`'s
  punch-list (added by #430) tracks both as `☐ pending` — apply in order via `git pull` (always
  first) → `wrangler d1 migrations apply --remote` → `npm run deploy`. Neither D4's per-job
  requirements overlay nor M1/M2's material-receipts flow is live until this runs.
- **Four in-file `PENDING OPERATOR CONFIRMATION` flags await sign-off**, none blocking deploy:
  (1) the `daily-report` required-content legal-floor entry (#423, 9 keys, the v1∩v2
  intersection); (2) the `material-incident` required-content floor (#428,
  `material_description`+`issue`+`details`); (3) the `material-incident` `category: progress`
  classification itself (#428, operator-vetoable to safety); (4) D1's END-OF-DAY 50+ bullet
  fan-out fix (#425) — flag if the change was unwanted.
- **#415 (FF4, portal_poll transient-error severity) is still held**, carried over unchanged from
  the prior session — built, reviewed clean, deliberately unmerged pending Seth's alert-severity
  sign-off. Not touched this arc.
- **The wire-types SPA re-export follow-up.** `worker/wire-types.ts`'s own header comment records
  it: `fieldops_checklist.ts`'s `AssignedInspectionsResponse` is still a hand-maintained local
  type, not yet re-exporting from the single Worker-typed source — explicitly scoped as "a
  follow-up after Slice 2 lands" (#431's comment), and Slice 2 (#432) landed without picking it up.
  No tech-debt entry filed yet for this specific residual — worth adding one next session so it
  isn't rediscovered from scratch.
- **The optimization plan's #12 waterfall tail** (`DailyReportTab`'s 2-stage 6-fetch placement
  lookup) remains unbuilt — medium risk, needs its own `/security-review` gate per the plan
  (widens what a `cap.tasks.own`-only viewer's placement read exposes). Candidate for its own PR
  next session rather than folding into a future unrelated slice.
- **Checklist item-state photo capture** (render-half-only gap, `docs/tech_debt.md` entry from the
  prior session) is unchanged — still needs its own §34-image-class-screening design pass before
  any storage route ships. Not touched this arc.
- **Worktree cleanup** for the D1–D4/M1/M2/design-refinement/Opt1–3 branches, plus the still-open
  FF4 branch from the prior session.

## What was NOT touched

- **No external send path changed.** Invariant 1 intact — every new form (guidance/form_link
  sections, material-incident) still files through the unchanged, human-reviewed `/api/submit` →
  intake → approved-send pipeline. `weekly_generate.py`'s family-based compile is untouched by
  design (Decision 1).
- **No doctrine edits.** Op Stds v19 citations, §§, and invariants are unchanged this session.
- **The checklist engine itself (instances/item-states/reconcile) is untouched** — D2 retired only
  the *daily* checkbox UI surfaces; the inspection-assignment machinery from the prior session's
  R-series program is unaffected and still load-bearing for inspections.
- **The optimization plan's #12 waterfall tail, the historical form-definition registry split, and
  the deprecated daily-checklist Worker-surface removal** — all three are named in
  `~/.claude/plans/optimization-plan.md`'s "Needs-operator (gated)" section and were deliberately
  NOT built this session; they require an operator green-light (doctrine-adjacent or
  medium-risk) before any agent builds against them.
- **Photo capture (write half) for checklist item states** — unchanged from the prior session,
  still render-half-only.

## Cross-references

- Prior session (Assigned-Tasks feature + R-series refinement, PRs #406–#421, #415 held):
  `docs/session_logs/2026-07-02_assigned-tasks-and-r-series-refinement.md`
- Specs: `~/.claude/plans/spec_sop-daily-form.md` (D1–D4), `~/.claude/plans/spec_material-receipts.md`
  (M1/M2), `~/.claude/plans/sop-daily-checklist-content.md` (R-seed content contract, prior
  session), `~/.claude/plans/design-refinement-plan.md` (#429), `~/.claude/plans/optimization-plan.md`
  (#430/#431/#432, incl. the "Needs-operator" gated list and the #12 tail-item scope note)
- Op Stds v19 §14 (parameterize/preservation — the R5-framework-reused decision, the #12 deferral,
  the historical-registry-split gate), §30 (integration discipline), §34 (image-class screening —
  the still-open checklist photo-capture gate), §42/§43 (self-documentation + runbooks —
  `safety_portal/README.md`'s punch-list, `docs/runbooks/`), §50/§51 (D1-as-writer / SoR write-back
  — unaffected this session, no Smartsheet schema changes)
- Adversarial review rule (DoD): CLAUDE.md "Operational conventions — load-bearing" — every new
  trust-boundary surface this arc (D2's status-scoping fix, D4/M1's job-scoped routes, M2's reuse
  of M1's routes, #431's byte-identical extraction) got a security/ops-stds review before merge
- PR merge discipline: `docs/operations/pr_merge_discipline.md`; verifier agent:
  `.claude/agents/pr-landed-verifier.md`
- Tech-debt: `docs/tech_debt.md` — the checklist photo-capture gap and R-series deferrals from the
  prior session are unchanged; the wire-types re-export follow-up and the #12 waterfall tail are
  flagged above for a new entry next session
- Memory: `project_fieldops-portal-program` (SOP-Daily-Form redesign + Material Receipts +
  optimization-sweep sections to append)
