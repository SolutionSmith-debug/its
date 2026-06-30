---
type: session_log
date: 2026-06-30
status: closed
workstream: progress_reporting
related_prs: [379, 380, 381]
tags: [session_log, p5, progress-reporting, progress-send, recipient-health, watchdog, external-send-gate, invariant-1, parameterize, recipient-routing, held-row-scan, approver-drift, check-i-catchup, ops-stds-enforcer, byte-identical, job-tracker-pivot]
---

# Session — P5 progress SEND half + operability guards (PRs #379, #380, #381)

Built the SEND half of the External Send Gate (FM v11 Invariant 1) for the progress
workstream — the twin of safety's `weekly_send`/`weekly_send_poll` — plus two shared
operability guards over both review sheets. Three PRs, each four-part verified. The
`ops-stds-enforcer` adversarial review caught a **real BLOCK in each shared-infra PR**
(a §3.1 record/push confusion in #380; a partial-read state-loss in #381) that mocks
structurally could not — both fixed, regression-tested, and re-confirmed RESOLVED before
merge. Parameterize-not-clone (Op Stds §14) throughout; safety SEND decision byte-identical.

## PRs landed (all four-part verified)

- **#379 `7a16383` — P5 core: `progress_send` + `progress_send_poll`.**
  Thin instantiations of the shared engine, NOT clones. `progress_reports/progress_send.py`
  binds a `SendConfig` (`workstream_tag="progress"`, `PROGRESS_ACTIVE_JOBS_CONFIG`, `wpr_review`,
  label "Weekly Progress Report"); the resolver reads the workstream-neutral
  `reports_contact_email` alias with a **stakeholder fallback** (the one recipient-policy
  difference from safety), CC = CC 1–5. `progress_reports/progress_send_poll.py` binds a
  `DaemonConfig` over `send_poll_core` — polls `WPR_human_review`, F22 against the **Progress
  Reporting** workspace, dispatches `progress_send.send_one_row`. Added a required no-default
  `SendConfig.active_jobs_config` field to `weekly_send.py` (safety binds
  `SAFETY_ACTIVE_JOBS_CONFIG` = the prior default → **byte-identical**) so a progress send can
  only ever resolve a progress recipient (the P4-Slice-1 cross-wiring trap). SEND_SCRIPTS
  enrollment for both; hardened interval plist (RunAtLoad=true); `install.sh` interval mapping
  (workstream derived from key prefix); `smoke_test_progress_send.py` (with an F22 §46
  approver-set probe); §43 runbook `progress_send.md`. Fixed a latent P4 bug:
  `review_queue.VALID_WORKSTREAMS` lacked `progress_reports` (the progress compile's per-job
  fence would have `ValueError`'d).

  PR #379 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-30T (squash `7a16383`)
  - mergeCommit: 7a16383
  - main CI on merge commit: SUCCESS

- **#380 `d4e4da1` — P5 guard: `shared/recipient_health.py` (never-silent recipient HELD).**
  Built once over both the safety (WSR) and progress (WPR) send paths: a no-recipient HELD now
  also files a queryable `ITS_Review_Queue` record (tagged with the caller's workstream, SLA
  `4h`, reason `policy-edge`), so the HELD is not silent. `weekly_send.py`'s two
  `held_no_recipient` branches route through a new `_held_no_recipient()` — byte-identical SEND
  decision (still HELD, no transmit), additive observability only. The belt-and-suspenders CC
  re-filter now WARNs (`weekly_send.cc_dropped_malformed`) if it ever strips a malformed CC.
  §43: `safety_weekly_send.md` Symptom C.

  PR #380 — four-part verify clean
  - state: MERGED
  - mergeCommit: d4e4da1
  - main CI on merge commit: SUCCESS

- **#381 `583810c` — P5 watchdog: Check-I/C progress wiring + HELD-row & approver-drift scans.**
  Check-C staleness: `TRACKED_JOBS` + windows for `progress_weekly_generate` (8-day) +
  `progress_send_poll` (30-min). Check-I generalized via a `_CatchupTarget` — safety wrapper
  byte-identical, new `progress_weekly_generate` catch-up re-fires `generate_core.run_generate`.
  **Check T** (new): WSR + WPR rows stuck HELD past 24h → WARN (daily catch-all backstop;
  age-thresholded via a first-seen state file). **Check U** (new): F22 send-approver set per send
  workspace → WARN on empty (sends fail-closed-blocked, §46) or changed-since-baseline.

  PR #381 — four-part verify clean
  - state: MERGED
  - mergeCommit: 583810c
  - main CI on merge commit: SUCCESS

## CI / four-part verify

All three PRs returned state=MERGED, mergedAt non-null, mergeCommit.oid present, and main-branch
ci.yml (test/portal/secrets) SUCCESS on the merge commit. Final integrated tree (post-#381):

- pytest: full suite green (2052 at #380; +15 watchdog tests at #381)
- mypy: clean
- ruff: clean
- main-branch CI on merge commits 7a16383 / d4e4da1 / 583810c: SUCCESS

## Adversarial review — two real BLOCKs caught + fixed

`ops-stds-enforcer` reviewed each Invariant-1 / shared-infra diff. It earned its keep twice:

1. **#380 §3.1 BLOCK** — the first design used `alert_dedupe.should_fire`/`record_fire` (a
   PUSH-only dedupe primitive) to gate two RECORD writes (the Review-Queue row + an ERROR log).
   Doctrine §3.1 (Push-vs-Record Separation) + `alert_dedupe`'s own docstring forbid gating a
   record; and a `Severity.ERROR` log never pages anyway. **Fix:** dropped `alert_dedupe`
   entirely; `recipient_health` files only a Review-Queue record, de-duplicated by OPEN-ROW
   STATE (skip if an open row for this `(workstream,row_id)` already exists) — record
   idempotency, not push suppression. Also a §42 BLOCK (the new module lacked the four canonical
   headings). Both re-reviewed RESOLVED. A malformed-Payload fail-soft parse test was added per
   the reviewer's follow-up note.

2. **#381 Check-T state-loss BLOCK** — `_update_held_first_seen` pruned every key absent from
   this round's `held_now`, but `held_now` only held keys from sheets that read SUCCESSFULLY, so
   a transient read failure on one sheet silently reset the OTHER sheet's 24h staleness clock
   (a stale HELD could then never WARN). The reviewer reproduced it. **Fix:** prune only keys
   whose sheet was scanned successfully this round (mirrors Check U's baseline merge); a
   regression test reproduces the exact scenario. The reviewer also flagged a pre-existing WARN
   my diff doubled — `_fire_generate_catchup` read `drafts_written`/`drafts_failed`/
   `aborted_empty_chain`, keys `generate_core.run_generate` never produces (it returns
   `RunSummary.__dict__`: `packets_compiled`/`wsr_written`/`errors_per_job`). Fixed to read the
   real counters; removed the dead empty-chain branch. Re-reviewed RESOLVED.

The recurring lesson (forensic classes #9/#14): mocks structurally cannot find injection,
double-send windows, fail-open misconfig, or partial-failure state-loss — adversarial review
repeatedly does. Both BLOCKs were in code whose entire purpose is "never silent."

## Decisions made during session

1. **`recipient_health` is a §3.1 RECORD leg, not a push-deduped alert.** The operator's
   "Review-Queue row + dedupe-gated alert" intent, made §3.1-compliant: a queryable record
   (always written), de-duplicated by open-row idempotency (not the push primitive), surfaced
   via Check A's stale-queue WARN. A real operator PAGE, if ever wanted, is a separate CRITICAL
   push leg (the only thing §3.1 permits `alert_dedupe` to gate) — a deliberate severity-posture
   decision, not built here. Docs reframed from "operator alert" to "queryable record, not a page."

2. **Stakeholder fallback is the one progress-vs-safety recipient difference, and is logged.**
   Progress TO = the progress reports-contact with a stakeholder fallback; safety never falls
   back (its stakeholder is off the envelope). The fallback is logged INFO
   (`progress_send.stakeholder_fallback_used`) so a cleared contact column silently redirecting a
   report to a different inbox is observable (never-silent).

3. **Check-I generalized, not cloned.** A `_CatchupTarget` (slug, review_sheet_id, refire, label)
   + a generic `_check_generate_catchup`; the safety entry is a thin byte-identical wrapper. The
   progress refire calls `generate_core.run_generate(PROGRESS_GENERATE_CONFIG)` directly
   (decorator-free, MAINTENANCE-runnable, send-free + AI-free).

4. **Watchdog `TRACKED_JOBS` wiring deferred from #379 to #381**, exactly as P4 deferred
   `progress_weekly_generate`. The progress daemons WARN on Check-C until the operator loads
   their plists at cutover (register + load together).

5. **The intended job-creation workflow is the job-tracker → Smartsheet pivot (P2.5), not a
   manual Smartsheet seed.** The operator clarified mid-session: an Evergreen office employee
   will create a "new job" in the ITS Portal, and that form seeds BOTH `ITS_Active_Jobs` (safety)
   and `ITS_Active_Jobs_Progress` (progress) via the up-sync mirror daemon. This is P2.5 /
   Thread B (operator-locked, §50/§51-unblocked, NOT yet built). P5's send half is agnostic about
   how the Active-Jobs row is populated — it reads `ITS_Active_Jobs_Progress` regardless — so P5
   needs no change for the pivot. The Smartsheet-UI seed described for a live progress e2e is a
   throwaway validation shim only; the real end-to-end smoke is best run through P2.5's flow.

## Live validation

- **Safety smoke (gates the shared-infra merge)** ran green against the PR3 code from the
  `~/its-p5` worktree — kill switch ACTIVE, ITS_Config + Graph reachable, all 11 WSR columns
  present, daemon-health reachable, dispatch filter intact: confirms `recipient_health` +
  `active_jobs_config` don't perturb the safety module's load or environment. The transmit-proof
  half (a real test-row send) + the full progress e2e are deferred to P2.5's portal job-creation
  flow per Decision 5.
- `ITS_Active_Jobs_Progress` confirmed empty (0 rows) → the empty `WPR_human_review` is the
  correct un-primed state, not a defect.

## Open items / next session

- **P2.5 — job-tracker → Smartsheet up-sync** is the next build: the portal "new job" form
  (parallel Safety + Progress contact/CC blocks, "Same as safety" copy) + the dual-sheet mirror
  daemon (`field_ops/fieldops_sync.py` + `shared/active_jobs_writer.py`). Makes the operator's
  intended workflow real and unblocks a true progress e2e. Plan:
  `~/.claude/plans/ok-we-are-going-scalable-flamingo.md`.
- **Progress cutover (operator):** §46 re-share approvers into the Progress Reporting workspace;
  set the progress `ITS_Config` rows (`progress_reports.intake_enabled`,
  `progress_reports.progress_send.{from_mailbox,polling_enabled}`,
  `progress_reports.box.portal_root_folder_id`); add `progress_reports` to the live
  `ITS_Review_Queue` Workstream picklist; load the `progress-generate` + `progress-send` plists
  (`install.sh load`) — Check-C WARNs until they're loaded.
- **P6 — progress rollup numbers** (`/api/internal/progress-rollup` + `form_pdf` rollup render),
  after the e2e is exercisable.
- Worktree cleanup: `~/its-p5`, `~/its-p5-wd`, `~/its-p5-docs` (+ prior-session worktrees) —
  operator-run `git worktree remove`.

## What was NOT touched

- No external send fired. Invariant 1 intact: `progress_send`/`progress_send_poll` AI-free
  (SEND_SCRIPTS); `recipient_health` adds no send/AI capability; the watchdog Check-I re-fires
  generation only (GATED, send-free).
- Safety SEND decision byte-identical — the only safety-observable deltas are additive: a
  Review-Queue record on a no-recipient HELD (#380) and a corrected (previously wrong) Check-I
  INFO summary (#381). Existing safety send/poll tests unchanged except the no-recipient HELD
  tests (now assert the record fires) and the Check-I summary assertion.
- No Smartsheet schema migrations. No D1 / Worker changes. No doctrine edits.
- The job-tracker pivot (P2.5) was scoped + confirmed, NOT built.

## Cross-references

- Prior session (P4 compile): `docs/session_logs/2026-06-30_p4-progress-compile-and-workflow-selector.md`
- Op Stds v19 §3.1 (push-vs-record), §14 (parameterize-not-clone), §42 (self-documentation),
  §43 (runbooks: `progress_send.md`, `safety_weekly_send.md` Symptom C), §46
  (workspace-membership = approval authority), §50/§51 (P2.5 doctrine-unblock)
- Capability gating: `tests/test_capability_gating.py` (SEND_SCRIPTS: progress_send, progress_send_poll)
- Runbooks: `docs/runbooks/progress_send.md`, `docs/runbooks/safety_weekly_send.md`
- Plans: `~/.claude/plans/let-s-go-with-option-greedy-fiddle.md` (P5/P6),
  `~/.claude/plans/ok-we-are-going-scalable-flamingo.md` (P2.5 job-tracker pivot)
- Memory to update: `project_fieldops-portal-program` (Stage-2 Thread-A P5 done; P2.5 = next)
