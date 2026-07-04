---
type: session_log
date: 2026-07-03
status: closed
workstream: field_ops
related_prs: [405, 434, 435, 436, 437, 438, 439, 440, 442, 443, 415, 444, 445, 446, 447, 448, 449, 450, 451, 452, 453, 454, 455, 456]
tags: [session_log, field-ops, safety_portal, complete-state-audit, unbounded-growth-audit, migration-guard, publish-daemon, sentry-reclassification, doctrine-rider, registry-split, delete-on-screen, photo-pool, section34-parity, toctou-fold, w4-audit-atomicity, requirejobscope, four-part-verify, held-pr, disclosed-staging-error, rebase-discipline, portal-worker-security-reviewer, ops-stds-enforcer, form-definition-reviewer, blueprint-doctrine-bump]
---

# Session — Complete-state hardening + unbounded-growth audit + design-table execution + live-report fixes (PRs #434–#456, blueprint #55)

Multi-day, extremely deep autonomous session under the operator's standing "work until a usage limit,
CC owns all merging, I complete operator steps when I get home" mandate. Continues the SOP-daily-form
arc (`2026-07-03_sop-daily-form-material-receipts-and-optimization-sweep.md`, #423–#432). The operator's
governing directive this session: *"take a thorough pass of the plans… surface any blockers we are
stuck on; if not, continue to complete our defined workflows end to end through Smartsheet; search for
optimization/efficiency/refinement/simplification; increase resiliency and progress toward a complete
state."* That produced four sequential arcs, each a multi-agent workflow (build + adversarial reviewers
in isolated worktrees) → CC fixes findings → exit-checked gates → squash-merge → four-part verify:

1. **Complete-state hardening** (CS1–CS4) — a 4-auditor survey answered the "end-to-end through Smartsheet"
   question and produced four resiliency slices, the highest-value being the publish-daemon migration guard.
2. **Unbounded-growth ("scale-crash") audit** — the operator asked *"are there other vulnerabilities of that
   class that would destroy us at scale?"* A 3-auditor sweep ranked 14 monotonic-accumulation surfaces by
   time-to-failure; the sub-2-year fuses became GS1/GS2 + the Sentry doctrine reclassification.
3. **Design-table execution** — the operator confirmed all recommendations on a decision table (D5-split,
   D6-deletions, G1 photo-capture, G2.3/2.5/2.6). Each built + reviewed + landed.
4. **Live-report fixes** — four operator messages from actually using the deployed portal: a photo
   flashes-then-disappears bug, daily-report role gating, a confirm-button toggle, and unlimited photos +
   a D.13 incident link (v6).

All 23 exec PRs + 1 blueprint doctrine PR landed and four-part verified. Both Python and TypeScript
surfaces touched; one blueprint doctrine version rider (v19.x) ratified by the operator mid-session.

## Full-session provenance (completeness anchor)

This was a single **multi-day, extremely long** session (the bulk executed by Claude Fable 5 before an
Opus 4.8 handoff for the final v6 finish + this close). Since the prior repo state (`780cacd`, #404), the
session landed PRs **#405–#456**, captured across **three** session logs written at natural arc boundaries —
this log is the authoritative completeness map so no PR is orphaned:

- **#405** — `c4386bd` URS-refine Personnel + Materials + JobTracker/badge polish (PR-3): the session's
  lead-in UI-consistency PR, landed before the first mid-session log's range began. Logged here for
  completeness; no non-obvious decision of its own.
- **#406–#421** → `2026-07-02_assigned-tasks-and-r-series-refinement.md` (Assigned-Tasks S1–S6+T, R-series;
  #415/FF4 opened held there and **merged this session** — see #415 below).
- **#423–#432** → `2026-07-03_sop-daily-form-material-receipts-and-optimization-sweep.md` (SOP-daily-form
  D1–D4, Material Receipts M1–M2, design-refinement, optimization sweep).
- **#434–#456** → **this log** (the four arcs below).
- **#422 / #433** are the two mid-session close-log PRs themselves (not re-listed as related work).
- **#441** was closed unmerged (the intended-held CS4b — see #440 below); **#415** (FF4) was opened during
  the assigned-tasks arc and merged here after the operator's severity sign-off.

## The four arcs

### Arc 1 — Complete-state hardening (CS1–CS4b)

The operator's "thorough pass of the plans" directive was answered by a 4-auditor workflow whose synthesis
(`~/.claude/plans/complete-state-audit.md`) reached three findings: (a) a **live incident** — publish #434
had auto-deployed the Worker *ahead* of migrations 0030–0032, so the D4/D5/M1/M2 routes were 500ing live
until the operator applied them; (b) the two new artifacts (daily-report-v5, material-incident) resolve
`category=progress` but `progress_reports.intake_enabled` has no live row, so both file into the **SAFETY**
workspace/WSR today by built-dark design (a 6-step operator go-live sequence enumerated in the audit);
(c) **genuinely-stuck technical blockers = NONE.**

- **#437 `ac4fb3e` — CS2**: photo-payload 413 prevention (per-photo 280KB decoded ladder × 4 < the 1.8MB
  submit cap), an exact pre-network payload check, all seven photo-error copies made actionable, and a
  `pagehide` draft flush.
- **#438 `771854f` — CS1 (highest value)**: **the publish-daemon migration guard** — a two-site,
  refuse-loud-fenced, fail-CLOSED-at-CRITICAL gate that makes the #434-class deploy-ahead-of-migrations skew
  *structurally impossible*. Plus a daily-report `required_section_types` mount floor (enforced at both C3
  layers + FormEditor suppression) and purge/prune coverage for the two new tables — which caught a real
  live-cron breaker: **a 6-term UNION exceeds D1's compound-SELECT cap**, restructured to six per-table
  `NOT IN` predicates.
- **#439 `131fa84` — CS3 (refinement safe-set)**: HeartbeatReporter added to `compile_now_poll` +
  `publish_daemon`; the SPA **vendor-chunk split** (main 549→360KB, a cache-stable 190KB React vendor chunk,
  hash-stability *proven empirically* by a rebuild); tombstone deletions (`intake_poll.py`,
  `weekly_summary.py`, conditions re-verified live — and the pass caught that `check_doctrine_drift.py` would
  have crashed on the deleted path); a D1 Time-Travel restore note.
- **#440 `c919d25` — CS4a**: the #12 `tasks/mine` waterfall fix (viewer placement carried server-side, 7→6
  fetches), both tracked TOCTOU folds (with an 85-tests-pass-unmodified semantics *proof*), wire-type
  re-exports. **⚠ Disclosed staging error (the load-bearing lesson): CS4 Part B** — the enforcement of
  `cap.form.submit`/`cap.form.request` — was **intended to be held** for operator sign-off, but the review
  agents' `git add -A` had pre-staged the entire worktree before my selective Part-A commit, so Part B rode
  #440's squash. The deep security review + a role×cap lockout analysis had covered *both* parts (all three
  roles hold both caps → no ability lost), so no revert was required; disclosed to the operator, who chose
  to **keep** it.
- **#442 `f8ff753`**: tech-debt currency for the above (cap enforcement landed; `cap.inspection.job`
  deliberately ungated — no surface exists to gate).

### Arc 2 — Unbounded-growth ("scale-crash") audit → GS1/GS2/Sentry

The operator's *"what else would destroy us at scale?"* produced a 3-auditor sweep of every monotonic
surface (`~/.claude/plans/unbounded-growth-audit.md`): a 14-row time-bomb table ranked by time-to-failure.
The real fuses: **MEMORY.md was over its load cap and silently truncating NOW** (fixed with a compaction
pass, zero code); **ITS_Errors** hits Smartsheet's 20k-row cap → the forensic record dies *and* watchdog
Check B goes blind (~13 days under a failure storm); **Sentry's free quota** burns in ~3.5 days under a
storm; **the D1 prune cron is a single point of silent failure** → a dead prune at 20×20 = the 10GB wall =
total field-capture outage; and **the week-sheet capacity tripwire built in PR #326 was never wired** (zero
call sites).

- **#447 `d66f966` — GS2 prune observability**: stage isolation (one throw no longer skips later stages),
  a `prune_meta` heartbeat (migration 0033), a bearer-gated `/api/internal/prune-status`, **watchdog Check
  V** (stale-WARN / failed-stage + >6GB CRITICAL / transport-INFO), and retention riders — with a build-time
  catch: the two new guard tables have **NULLABLE `job_id`**, one NULL row would poison the `NOT IN` into
  never-deleting-anything (`IS NOT NULL` filters + a regression test).
- **#448 `3fc1e3a` — GS1**: **Check O** row-cap rotation for ITS_Errors + ITS_Review_Queue (terminal-only,
  conservative — open CRITICALs + ESCALATED never touched); the dormant `sheet_capacity` tripwire finally
  wired into the week-sheet find-or-create (advisory, never blocks a compile); a 100MB packet-size forecast
  WARN ahead of the 150MB HELD wall. **Slice 4a (Sentry-leg dedupe) was STRIPPED before landing** — the
  ops-stds review correctly BLOCKed it: Op Stds §3.1 canonically names Sentry a fires-every-time *record*
  leg, and reclassifying it is a §44 fixed doctrine-class escalation. Surfaced to the operator as a decision.
- **#449 `aaf7a8e` + blueprint #55 `9b31703` — Sentry reclassification (operator-ratified)**: the operator
  chose option 1. Sentry becomes a dedupe-subject *push* leg (its own namespaced `sentry::` window, each leg
  opened only by its own successful delivery), ITS_Errors stays the sole always-write record. The doctrine
  rider took the **lighter v19.x-amendment path** (dated Authority note, `version:19` untouched) per the
  doc's own v16.x precedent, rather than a v20 bump that would invalidate every "Op Stds v19" citation.

### Arc 3 — Design-table execution (operator confirmed all recommendations)

- **#445 `9035e7c` — D6**: deleted 4 dead routes (`/checklist/mine` — a live junk-data writer — + its
  rollup-draft, the `/close` alias, `/progress`), inspection engine boundary-verified intact, +3 contracts
  ported so live routes keep coverage.
- **#446 `15c0ea6` — D5 registry split** (operator: *"absolutely need to split the registry — that would
  very quickly crash our website"*): form-definition versions were bundled into the SPA main chunk, growing
  ~25KB per SOP edit forever. Now current+previous versions eager, historical lazy — main chunk −60.7KB and
  the growth curve is FLAT. Key verified fact: **no production surface renders a historical version today**
  (amend uses the current definition), so nothing user-facing changed.
- **#450 `c3f3f36` — G2.6**: task due-dates + overdue pills (0035), one shared `TaskDue` definition, CS4a's
  folded WHEREs verified byte-untouched.
- **#451 `9ce2fc3` — G2.3 correction epic**: scoped crew edit/retire (`created_by`-bound, conservative 409
  guards) + **non-destructive time amend/void** (head-only, atomically TOCTOU-folded; void = 0h + required
  reason; all reads collapse to chain heads). Schema finding: the amend chain **already existed** in 0015, so
  0034 is just the missing index. Also closed a pre-existing hole — the create route accepted a raw
  `amends_uuid` unvalidated.
- **#452 `eedf7a6` — G1 item photos (Option D, ratified)**: checklist-item photo evidence with **full §34
  screening parity** (adversarially attested: same module, same layers, single implementation), **no serving
  route ever**, and **delete-on-screen** (the operator's own sharper posture — D1 holds bytes only while
  pending; Box holds the permanent record). Box-first-then-post-back ordering so no crash window destroys the
  only copy; structural one-photo unique index; domain-separated HMAC.
- **#453 `4922ff6` — G2.5 URL router**: path-based deep-linkable pages (`/jobs/JOB-000018` is shareable),
  proven to need **zero Worker changes** by a live probe; the R3 dirty-guard byte-preserved.

### Arc 4 — Live-report fixes (from the operator using the deployed portal)

- **#454 `63371ee` — photo flashes-then-disappears** (urgent): root cause proven fail-on-main/pass-with-fix
  — the camera/file picker *blurs* the page; refocus triggers the tab's wake-refresh, which re-applies the
  **photo-stripped** sessionStorage draft over live values, wiping the photo ~1s after upload. Fix: the
  draft-apply branch overlays live photo-key values last via functional `setValues`. Photos were UI-lost
  only (never reached the Worker).
- **#455 `8a84281` — daily-report role gating + confirm-toggle**: ground-truth found — a placed **admin was
  locked out** (the tab content gated on `role==='manager'` exactly as complained) AND a placed
  **subcontractor could file a daily report directly** (nothing role-gated the daily family server-side).
  Both fixed, server-side primary (`requireDailyReportRole`, closed vocabulary, no new cap). Confirm buttons
  became true toggles — the fix lives in the shared scale renderer (accepted WARN: incident-report/HSSE scale
  buttons gain the same clear-on-reclick; sane everywhere).
- **#456 `3006d43` — daily-report v6 (unlimited photos + D.13 incident link)**: the inline 4-photo field is
  payload-budgeted (~1.8MB submit cap → why the limit was 4), so an **"Add more photos"** button uploads each
  additional photo *individually* into a pre-submit §34-screened **pool** (migration 0037, mirroring the G1
  item-photo machinery: Option D, delete-on-screen, no serving route). Submissions carry only tiny references;
  the Worker claims them atomically; an amendment transfers the filed report's claims (amends target
  server-verified). Caps **folded into the INSERT** so a burst can't fill D1 (the review BLOCK, fixed). The
  pool screens *before* the submission fetch so the common case files same-cycle. Plus a **"Report a material
  incident →"** form_link under D.13.

## CI runs

Every PR landed via the standard merge + **four-part verify** ritual (state=MERGED, `mergedAt` non-null,
`mergeCommit.oid` present, main-branch CI on the merge commit = SUCCESS). All 23 exec PRs passed leg 4
(`ci success` on the squash commit); blueprint #55 passed its frontmatter/crossref lints. Representative
final gate (v6 #456, the largest): typecheck clean · worker vitest 772 · SPA vitest 544 · pytest 2364 ·
mypy 244 files clean · ruff clean.

## Decisions made during session

- **CS4 Part B kept, not reverted** (operator). Rode #440's squash via a disclosed reviewer-`git add -A`
  staging error; the lockout analysis proved no ability was lost, so a revert would have been pure churn.
  *Rejected alternative:* surgical revert of the gate hunks — declined once the operator confirmed keep.
- **Sentry reclassification via the lighter v19.x rider, not a v20 bump.** A v20 would have invalidated the
  exec CLAUDE.md's "v19 is governing" anchor and every in-code "Op Stds v19" citation for a two-paragraph
  change; the doc's own v16.x precedent (dated Authority note, no major bump) fit because the *protective
  claim* is unchanged (operator still paged; ITS_Errors still carries the complete record). *Rejected:* v20.
- **G1 photos: Option D (on-file ✓, no serving route) + delete-on-screen**, not Option B (in-app thumbnails).
  The operator reasoned that previous daily reports are viewable via Form Request and Box is the permanent
  record, so portal-side photo bytes need not persist at all — sharper than any retention window. *Rejected:*
  Option A (cloud-screen, instant display — permanently weaker screening + a doctrine amendment) and the
  30/90-day retention options.
- **D5 registry split: keep current+previous eager, lazy the rest**, reversing the documented C1/C9
  bundle-everything guarantee — safe because no production surface renders a historical version (amend uses
  the current definition, worker-verified). *Rejected:* accept the ~25KB/edit unbounded growth.
- **Daily-report gating by session role, not a new capability.** All three roles hold `cap.tasks.own`, so a
  cap couldn't express "manager/admin only"; a two-role capability would recreate the vestigial-cap class.
  *Rejected:* a new `cap.daily.file`.
- **v6 additional photos as a pool of references, not inline payload.** The 1.8MB submit cap makes >4 inline
  photos structurally impossible; the pool mirrors the just-landed G1 machinery. *Rejected:* raising the
  per-photo budget (would still cap out) or a multipart submission (new untrusted-parse surface).
- **G2.1/G2.2 dropped as obsolete** — the D-series killed the checklist engine's daily flow, so mid-day
  template re-sync and orphaned-instance-on-reassignment no longer have a problem to solve. G2.4 deferred; G3
  gated on the inspection library actually growing.

## Open items handed off (operator)

The operator completes all deploy/Smartsheet steps. Suggested Master-Checklist wording:

- **Consolidated deploy (clears the whole stack):** `git -C ~/its pull origin main` → `cd ~/its/safety_portal
  && npx wrangler d1 migrations apply its-safety-portal-db --remote` (applies **0030–0037** in order) →
  `npm run deploy`. The new publish-daemon guard (#438) will correctly HALT any publish until these are
  applied — that is the guard working, and the queued publish self-completes after.
- **The 6-step progress go-live sequence** (`complete-state-audit.md` Part 1 A2) — the real "through
  Smartsheet" unlock; until run, daily-report-v6 + material-incident file into the SAFETY workspace.
- **Mandatory live smokes** (prove-the-control-bites): the photo-stays check (#454); a **synthetic malicious
  photo must red-light** for both G1 item photos (#452) and v6 additional photos (#456); one clean
  round-trip each; the capacity tripwire (#448) on the next weekly cycle; the two new daemon-health rows
  (#439) self-provision.
- **Delete the orphaned `feat/cs4b-vestigial-caps` branch on origin** (hook-blocked for CC):
  `gh api -X DELETE repos/SolutionSmith-debug/its/git/refs/heads/feat/cs4b-vestigial-caps`.

## What was NOT touched

- **No external-send path changed** — Invariant 1 intact across every PR; the portal stays send-free, all
  new alert categories ride the two-process gate.
- **The inline 4-photo daily field** — v6 left it byte-untouched; additional photos are a separate pool.
- **The checklist inspection engine** — D6 deleted only the daily-flow-exclusive routes; every S6/R5
  inspection surface + `reconcileFormLinked`'s inspection path verified intact.
- **`progress_reports.intake_enabled`** — deliberately left absent (built-dark) so the two progress artifacts
  ride the safety chain until the operator's go-live steps.
- **CS4 Part B was NOT reverted** — kept per operator decision after disclosure.
- **`cap.inspection.job`** — deliberately left ungated; no surface exists to gate it.

## Lessons captured to memory

- **`project_fieldops-portal-program.md`** — updated through all four arcs (complete-state slices, the
  growth-audit fixes, the Sentry reclassification, the design table, and the live-report fixes incl. v6).
- **Rebase discipline (recurred several times this session):** a `cmd | tail` pipe **swallows the rebase
  exit code** — always check `$?` on its own line, never trust a piped rebase to have succeeded. And in a
  reviewed worktree the review agents' `git add -A` pre-stages everything, so the safe pattern is
  **wip-commit → rebase → reset**, never a selective `git add` after review (the CS4b disclosed-staging-error
  root cause).
- **Multi-surface fan-out held again:** a "fixed in one place" claim is the recurring incomplete bug — the D1
  UNION-cap fix, the NULL-poisoned `NOT IN`, the three photo-delivery surfaces, and the picklist-REGISTRY
  parity all reinforce enumerate-ALL-implementations-first.
- **A textually-clean auto-merge is not semantically proven** — the v6 rebase auto-merged three overlapping
  prior PRs (photo-fix, gating, toggle) with zero conflicts; the full gate on the *rebased* tree is what
  actually proved the combination sound. Never land a clean-rebase without re-gating.
- **Adversarial review repeatedly caught what unit tests structurally cannot** — the racy pool caps (v6
  BLOCK), the doctrine-class Sentry reclassification (GS1 BLOCK), the §34-bypass reasoning (G1), the raw
  `amends_uuid` hole (G2.3). The prove-the-control-bites + fold-guards-into-WHERE patterns are now house
  reflexes.
