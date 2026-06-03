---
type: operations
date: 2026-06-02
status: active
related_prs: []
workstream: infrastructure
tags: [runbook, successor-remediation, smartsheet, token, secrets, tier-2]
---

# Runbook — Smartsheet token cannot write (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and reads Smartsheet rows + alert emails,
but does **not** read code or touch secrets. The §42 code-reader rationale lives
in `shared.smartsheet_client.verify_write_capability` and
`scripts/watchdog.py::_check_token_write_capability`.

## Purpose

What to do when the daily watchdog reports that **`ITS_SMARTSHEET_TOKEN` can read
but cannot write** (B2 — Check L). A read-only or mis-scoped token (e.g. after a
botched rotation) passes every read and would otherwise fail **silently** at the
first real daemon write — a mid-cycle 401 that is hard to trace. The watchdog's
write-capability probe turns that into a LOUD daily CRITICAL. The fix — rotating
or re-scoping the token — is a **secrets / auth** operation, which is a **FIXED
high-capability-class category**: it **always escalates to the Developer-Operator
(Seth)**. The Successor-Operator's job here is to **recognize and confirm** the
symptom, then escalate — NOT to touch the token.

## Procedure

### Symptom

- A **CRITICAL alert email** with a subject like
  `[ITS CRITICAL] scripts.watchdog: ITS_SMARTSHEET_TOKEN cannot write …`.
- In **ITS_Errors**: a `Severity = CRITICAL`, `Script = scripts.watchdog` row
  whose message contains `cannot write (read-only or mis-scoped?)`.
- Daemons may also be quietly failing their own writes: **ITS_Daemon_Health**
  rows going stale (Last Heartbeat not advancing) and/or repeated
  `daemon_health_write_failed` WARNs in ITS_Errors.

### What the Successor-Operator checks

1. **Is it the write-capability CRITICAL, or a transient?** The probe is precise:
   a `cannot write` CRITICAL means the token was *rejected on a write* (401/403).
   A `token write-probe inconclusive (transient …)` **WARN** or a `skipped —
   circuit breaker OPEN` **INFO** is NOT this fault — those are Smartsheet-outage
   noise; wait for the next watchdog run. Only the **CRITICAL** is the real token
   problem.
2. **Recent token change?** Note whether a Smartsheet token rotation / API-key
   change happened recently (this is the usual trigger). You do not need access
   to the token — just whether a change occurred, for Seth's context.
3. **ITS_Config `system.state`** — `MAINTENANCE` defers the page (the CRITICAL
   record still lands in ITS_Errors). If you are seeing the *record* but no
   *email*, MAINTENANCE is why; the fault is still real.

### The Claude prompt or UI action

There is **no low-class repair** for this fault — it is a secrets/auth issue.
Do **not** attempt to edit the token, the Keychain, or any auth config. Hand the
confirmation to the operator and escalate:

> "Claude, the watchdog is reporting `ITS_SMARTSHEET_TOKEN cannot write`
> (CRITICAL in ITS_Errors). Please confirm it is the write-capability probe
> (not a transient/outage), summarize when the token last changed, and draft the
> escalation to Seth — the token needs rotating or re-scoping to read-write."

### Escalate-to-Seth condition

**Always.** Smartsheet token / Keychain / auth is one of the four FIXED
high-capability-class categories (Op Stds §44): the Successor-Operator confirms
and escalates; **Seth rotates/re-scopes the token** and (per §39 per-customer-fork
security setup) re-seeds it in Keychain. A `cannot write` CRITICAL that recurs
after a claimed fix is still Seth's — do not loop on it.

Both-rule (Op Stds §44): "recognize + confirm + escalate" is the Tier-2 action;
the repair itself (secrets) is high-class → Tier 3.

## Owner

`@solutionsmith`. If the probe's footprint (one `_its_write_probe_*` sheet
created + deleted per daily watchdog run, in the Config folder) ever needs
adjusting, that is a code change (Tier 3).
