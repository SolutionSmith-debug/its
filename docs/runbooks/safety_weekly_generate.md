---
type: operations
date: 2026-06-01
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, watchdog, weekly_generate, tier-2]
---

# Runbook — weekly_generate catch-up (Successor-Remediation, Op Stds §43)

The first §43 successor-remediation entry in this repo (Op Stds v16 §43 —
"Successor-Remediation Documentation Discipline"). It is written for the
**Successor-Operator**: a trained operator who runs Claude Code and reads
Smartsheet rows and alert emails, but does **not** read code. Claude loads
this entry to drive a Tier-2 repair; the operator sees the Smartsheet/alert
evidence and approves. The §42 code-reader rationale for the same capability
lives in the `scripts/watchdog.py` module docstring (Check I) and the
in-code comment above `_check_weekly_generate_catchup` — the two are
complements, not substitutes.

## Purpose

What to do when the **weekly safety-report generation** misses its Friday
run and the watchdog's automatic catch-up either has not yet recovered it or
has failed. The catch-up (watchdog Check I) re-fires a missed Friday
`weekly_generate` run on the next daily watchdog pass. When it **succeeds**,
the recovery is silent — a weekly report draft simply appears in
`WPR_Pending_Review` a day or so late, with no alert and **no operator
action required**. This runbook is for the **failure** case.

## Procedure

### Symptom

One of:

- A **CRITICAL alert email** with a subject like
  `[ITS CRITICAL] scripts.watchdog: weekly_generate catch-up FAILED for week
  YYYY-MM-DD …`.
- In the **ITS_Errors** sheet: a row with `Severity = CRITICAL`,
  `Script = scripts.watchdog`, `Error = weekly_generate_catchup_failed`
  (note its `Correlation_ID`).
- In the **ITS_Daemon_Health** / watchdog status: the daily watchdog WARNs
  that the scheduled job `safety_weekly_generate` is stale (the marker-file
  staleness check), and the current week's report draft never appears.
- The **WPR_Pending_Review** sheet has **no row** for the current week
  (`Week` column = this week's Monday) for one or more projects that
  normally receive a weekly report.

### What the Successor-Operator checks

1. **WPR_Pending_Review** — is there a row for the current week (`Week` =
   this Monday) for each active project? A row whose `Notes` contains
   `[GENERATION_FAILED: …]` means generation *ran* but that one project
   errored (a different, narrower problem than a total miss).
2. **ITS_Errors** — filter `Script = scripts.watchdog`,
   `Error = weekly_generate_catchup_failed`; read the `Message` and note the
   `Correlation_ID`. Then filter `Script = safety_reports.weekly_generate`
   for the **underlying** cause on the same day (e.g. `Error = smartsheet_error`
   or `weekly_generate.project_failed`).
3. **ITS_Config `system.state`** — is it `PAUSED` or `MAINTENANCE`?
   - `PAUSED` → catch-up is intentionally skipped; nothing failed.
   - `MAINTENANCE` → catch-up still runs, but the operator **page** was
     deferred by design; the CRITICAL **record** row is still in ITS_Errors.

### The Claude prompt or UI action

- If `system.state` is `PAUSED` **unintentionally**: in the Smartsheet UI,
  set the ITS_Config `system.state` row back to `ACTIVE`, then ask Claude to
  confirm the next watchdog run catches up. (Direct UI cell edit; no code.)
- Otherwise, hand the diagnosis to Claude:

  > "Claude, the weekly safety-report generation didn't run for the week of
  > `<Monday's date>` and the watchdog catch-up failed (ITS_Errors
  > correlation `<id>`). Please diagnose why `weekly_generate` is erroring
  > and re-run the generation for that week."

  Re-running the generation for a specific week is a **low-capability-class**
  action (re-run a stale job; no code, no secret, no external send) — Claude
  drives it; the operator approves. The re-run is idempotent: it replaces an
  unapproved draft and refuses to touch an already-approved one, so it is
  always safe to ask for.

### Escalate-to-Seth condition

Stop and escalate to the Developer-Operator (Seth, Tier 3) when **any** of:

- The catch-up has failed **twice** (two `weekly_generate_catchup_failed`
  CRITICAL rows across separate watchdog runs).
- The failure or Claude's diagnosis names the **External Send Gate**, any
  **secret / auth / Keychain** category, **doctrine**, or **requires a code
  change** to fix why generation errors.
- The symptom is **novel** — it does not match this entry.

Both-rule (Op Stds §44): "weekly generation missed, catch-up succeeded" is
low-class / documented (Tier 2, usually silent — no action). "Catch-up
fails, or generation is erroring for an unknown reason" is **novel or
high-class → Tier 3.** Re-running generation is Tier-2; fixing *why* it
errors may be high-class (code) and is Tier-3.

## Owner

`@solutionsmith`. New `weekly_generate` failure modes that become
Tier-2-reachable should be added to this entry as additional Symptom →
checks → action → escalate blocks, per Op Stds §43.
