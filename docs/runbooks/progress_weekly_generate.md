---
type: operations
date: 2026-06-30
status: active
related_prs: []
workstream: progress_reports
tags: [runbook, successor-remediation, watchdog, progress_weekly_generate, tier-2]
---

# Runbook — progress_weekly_generate (the progress weekly compile) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (reads Smartsheet rows +
alert emails, not code). The §42 code-reader rationale lives in
`progress_reports/progress_weekly_generate.py` (the thin progress entry point) and
`safety_reports/generate_core.py` (`run_generate` — the shared deterministic compile core both
the safety and progress weekly compiles instantiate). This is the progress twin of
`safety_weekly_generate.md`; the two are identical in shape, different in workstream.

## What this controls

The **weekly progress-report generation**. Friday 14:30 Pacific (staggered 30 min after the
safety compile at 14:00), `progress_weekly_generate` compiles each **active progress job's**
Sat→Fri week of submitted progress-form PDFs into a Box packet and dual-writes a Rollup
snapshot row + a **PENDING `WPR_human_review` row** (the progress twin of safety's WSR). A human
later approves the WPR row and the progress send (P5) transmits. Wherever this runbook says
"the review row," read **`WPR_human_review`**.

If the Friday run is missed (host asleep / crash), the current recovery is the **manual re-run**
in Fault A below.

> **Wiring status (2026-06-30):** the compile WRITES its `progress_weekly_generate` watchdog
> marker, but the watchdog's automatic **Check-I catch-up** + **Check-C marker-staleness floor**
> are not yet extended to that slug (they track only `safety_weekly_generate` today). Until that
> **tracked fast-follow** lands, a missed progress Friday run surfaces as "no current-week WPR
> row," **not** a watchdog CRITICAL — recover it with the manual re-run (Fault A). Wiring the
> watchdog is a code change (Tier-3 / Seth). The CRITICAL-alert / `..._catchup_failed` symptoms
> below apply once that wiring lands.

## Fault A — generation missed AND catch-up failed

**Symptom.** A CRITICAL alert `[ITS CRITICAL] scripts.watchdog: progress_weekly_generate
catch-up FAILED …`, or an `ITS_Errors` row `Script=scripts.watchdog`,
`Error=progress_weekly_generate_catchup_failed`; OR `WPR_human_review` has **no row** for the
current week (`Week Of` = this week's Saturday) for a progress job that normally gets one.

**Check (read-only).** (1) `WPR_human_review` — is there a current-week row per active progress
job? (2) `ITS_Errors` — filter `Script=progress_reports.progress_weekly_generate` for the
underlying cause (e.g. `progress_weekly_generate.compile_failed` / `.compile_timeout`), note the
`Correlation_ID`. (3) `ITS_Config system.state` — `PAUSED` means catch-up is intentionally
skipped (nothing failed); `MAINTENANCE` means the operator page was deferred but the CRITICAL
record row still exists.

**Repair (Tier-2, low-class).** If `system.state` is unintentionally `PAUSED`, set it back to
`ACTIVE` in the Smartsheet UI and let the next watchdog pass catch up. Otherwise hand Claude:
*"the weekly progress generation didn't run for the week of `<Saturday>` and catch-up failed
(ITS_Errors correlation `<id>`); diagnose why `progress_weekly_generate` is erroring and re-run
generation for that week."* Re-running a specific week is **idempotent** (replaces an unapproved
draft; refuses an already-approved one) — always safe to ask for, no code/secret/send.

## Fault B — one job-week was fenced (timeout or memory)

The scheduled compile carries two per-job fences (in the shared `generate_core` loop). When one
fires, **that one** job-week routes to **ITS_Review_Queue** and is skipped; the others compile
normally. (A Compile-Now for a single job runs **unfenced** — a hang needs a manual process
kill, not a re-run.)

- **Timed out** — `ITS_Errors` `Error=progress_weekly_generate.compile_timeout`; a Review-Queue
  row `weekly compile failed … (CompileJobTimeoutError)`. **Repair:** re-run that job-week (it's
  resumable — the Rollup watermark is written last, so a re-run retries only the timed-out one).
  Raising `progress_reports.progress_weekly_generate.job_timeout_seconds` (ITS_Config) is a
  low-class tweak; a *persistent* need to raise it = a code issue → escalate.
- **Memory fence** — `ITS_Errors` `Error=progress_weekly_generate.compile_unexpected`,
  `CompileMemoryExceededError`; a Review-Queue row. **Repair:** if the week is genuinely large,
  raise `progress_reports.progress_weekly_generate.merge_memory_ceiling_bytes` (ITS_Config) with
  care, then re-run that job-week.

## Fault C — host compile-mutex contention

**Symptom.** `ITS_Errors` from `shared.compile_mutex`, `Error=compile_mutex.contended`,
message `… role='progress'`. **Meaning:** the safety compile (14:00) was still running when the
progress compile started (14:30). The progress compile is **fail-open** — it logged the WARN and
**ran anyway, unlocked**. No data lost; no action for a one-off. **Repair (Tier-2):** confirm the
progress run otherwise completed (its Rollup + WPR row landed). If it recurs every run, a compile
may be hung holding the lock — check for a stuck compile process and have it terminated.

## Escalate-to-Seth boundary (observable terms)

Escalate — do **not** attempt — when: catch-up failed **twice**; the failure/diagnosis names the
**External Send Gate**, **secrets/auth/Keychain**, **doctrine**, or needs a **code change**; a
**single variant/job** is mis-handled in a way re-running doesn't fix; or the symptom is **novel**.
Tier-2 here is exactly: re-run a stalled/fenced job-week, toggle an ITS_Config value, or un-PAUSE
`system.state`. Everything else escalates.

## Owner

`@solutionsmith`. New Tier-2-reachable failure modes get added here as Symptom → check → repair →
escalate blocks (Op Stds §43).
