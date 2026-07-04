---
type: operations
date: 2026-07-03
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, watchdog, compile_now_poll, cross-workstream, progress_reports, tier-2]
---

# Runbook — compile_now_poll (the on-demand "Compile Now" poller) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (reads Smartsheet rows + alert
emails, not code). The §42 code-reader rationale lives in `safety_reports/compile_now_poll.py`. This
covers the **fast path** for the weekly packet; the scheduled Friday compile has its own runbooks
(`safety_weekly_generate.md`, `progress_weekly_generate.md`) — this daemon reuses the SAME compile
they do, just on demand.

## What this controls

ONE launchd daemon (`org.solutionsmith.its.compile-now-poll`, ~90 s cadence) that watches **every
served workstream's** week sheets and, when an operator checks **Compile Now** on a week sheet's
**Rollup** row, compiles that job's current Sat→Fri week within a minute or two — the SAME canonical
packet the Friday run produces, dual-writing a Rollup snapshot + a **PENDING review row** (safety →
`WSR_human_review`; progress → `WPR_human_review`). It never sends and never approves — a human still
approves the review row before any send.

**Served workstreams (2026-07-03): safety AND progress.** One daemon, one plist, one
`ITS_Daemon_Health` row (`safety_reports.compile_now_poll`), one Check-C watchdog marker
(`safety_compile_now_poll`) — it iterates both workstreams' configs each cycle (§14
parameterize-not-clone). Each has its own on/off gate:
`safety_reports.compile_now_poll.polling_enabled` and
`progress_reports.compile_now_poll.polling_enabled` (both default ON when the row is absent).

## Fault A — checked "Compile Now" but nothing happened

**Symptom.** An operator checked **Compile Now** on a week sheet's Rollup row, waited a few minutes,
and no new Rollup snapshot / review row appeared — the checkbox is **still set**.

**Check (read-only).** (1) `ITS_Config` — is the served workstream's gate ON?
`safety_reports.compile_now_poll.polling_enabled` for a safety week sheet;
`progress_reports.compile_now_poll.polling_enabled` for a progress one. A `false` (or an
operator-set OFF) means this daemon skips that workstream — the checkbox waits until the Friday run.
(2) `ITS_Config system.state` — `PAUSED` / `MAINTENANCE` halt the daemon entirely (the whole system
is paused). (3) `ITS_Daemon_Health` row `safety_reports.compile_now_poll` — is `Last Cycle At`
recent (within a few minutes)? A stale timestamp = the daemon isn't cycling (see Fault C /
escalate). (4) Confirm the box was checked on the **Rollup** row (the "compile now" trigger), not
only on individual Submission rows (those are the "include in the packet" selection).

**Repair (Tier-2, low-class).** If the workstream gate is unintentionally OFF, set the
`<workstream>.compile_now_poll.polling_enabled` row to `true` in the Smartsheet UI and wait one
cycle (~90 s). If `system.state` is unintentionally `PAUSED`, set it back to `ACTIVE`. If the daemon
is cycling and the gate is ON but the compile still doesn't run, hand Claude: *"a Compile-Now on
`<project> — week of <Saturday>` isn't compiling; the daemon is alive and the gate is on — diagnose."*

## Fault B — a Compile-Now compile FAILED (fail-loud)

**Symptom.** `ITS_Errors` row `Script=safety_reports.compile_now_poll`,
`Error=compile_now_poll.compile_failed` (the message names the workstream + project + job + week),
AND an **ITS_Review_Queue** row `weekly compile failed for <project> …`. The **Compile Now trigger
stays SET** on purpose (fail-loud — a cleared trigger would hide the failure).

**Check (read-only).** (1) The `ITS_Errors` message + `Correlation_ID` — the failing project/job and
the underlying cause. (2) The Review-Queue row tagged with the compile's workstream
(`safety_reports` or `progress_reports`) — its `Reason` / payload.

**Repair (Tier-2, low-class).** The failure is transient by design (Box/Smartsheet hiccup). Because
the trigger is still set, the next daemon cycle **retries automatically** — often it clears on its
own. If it keeps failing, hand Claude the `ITS_Errors` correlation id: *"Compile-Now keeps failing
for `<project>` week `<Saturday>` (correlation `<id>`); diagnose and re-run."* Re-running a compile
is idempotent (appends a fresh packet/Rollup, replaces an unapproved draft). No code/secret/send.

## Fault C — the daemon isn't cycling / a stuck single-flight lock

**Symptom.** `ITS_Daemon_Health` row `safety_reports.compile_now_poll` has a **stale** `Last Cycle
At` (many minutes old), OR every cycle's notes read `halted=locked`. **Meaning:** either the daemon
process is wedged, or a previous, still-running compile holds the single-flight lock
(`~/its/state/compile_now_poll.lock`) and each new cycle backs off rather than double-compiling.

**Important — Compile-Now runs UNFENCED.** Unlike the scheduled Friday compile (per-job timeout +
memory fences), an on-demand compile has **no timeout**. A genuinely hung compile will hold the lock
indefinitely; the fix is a **manual process kill** (Tier-3), not a re-run.

**Check (read-only).** (1) Is `Last Cycle At` advancing at all, or frozen? (2) Are OTHER daemons'
`ITS_Daemon_Health` rows also stale (→ host-level problem, see the watchdog/UptimeRobot path) or is
only this one frozen? (3) Watchdog Check-C should already have WARNed on the stale
`safety_compile_now_poll` marker.

**Repair.** A transient `halted=locked` for one or two cycles (a legitimately long compile in
progress) is **normal** — wait. If the lock is held for many minutes with no progress, or the daemon
row is frozen, this is a **hung/stuck daemon** → **escalate to Seth** (killing a process / clearing a
stuck lock file is a host operation, not a Smartsheet toggle).

## Escalate-to-Seth boundary (observable terms)

Escalate — do **not** attempt — when: the daemon's `ITS_Daemon_Health` row is **frozen** (hung
process) or a compile has **held the lock for many minutes**; the failure/diagnosis names the
**External Send Gate**, **secrets/auth/Keychain**, **doctrine**, or needs a **code change**; a
Compile-Now failure **persists after** the automatic retries; or the symptom is **novel**. Tier-2
here is exactly: flip a `<workstream>.compile_now_poll.polling_enabled` value, un-PAUSE
`system.state`, or ask Claude to re-run a failed/idempotent compile. Anything that requires killing a
process, clearing the lock file, or touching code escalates.

## Owner

`@solutionsmith`. New Tier-2-reachable failure modes get added here as Symptom → check → repair →
escalate blocks (Op Stds §43).
