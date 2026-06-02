---
type: operations
date: 2026-06-01
status: active
related_prs: []
workstream: infrastructure
tags: [runbook, successor-remediation, circuit-breaker, alerts-cap, tier-2]
---

# Runbook — Smartsheet circuit breaker + alerts-per-hour cap (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry (Op Stds v16 §43) for the **F08** Smartsheet
circuit breaker and the **F09** alerts-per-hour cap. Written for the
**Successor-Operator**: a trained operator who runs Claude Code and reads
Smartsheet rows + alert emails, but does **not** read code. Claude loads this
entry to drive a Tier-2 repair; the operator sees the Smartsheet/alert evidence
and approves. The §42 code-reader rationale lives in the
`shared/circuit_breaker.py` module docstring and the in-code comments at the
wiring sites — complements, not substitutes.

## Purpose

What to do when ITS reports that **Smartsheet looks degraded** (the circuit
breaker is OPEN) or that **operator alerts are being rate-capped** (a brownout
storm). In the healthy case neither fires and there is **no operator action**:
the breaker auto-recovers after a short cooldown, and the cap self-clears when
its hour rolls over. This runbook is for when one of them is stuck or the
underlying cause needs clearing.

## Procedure — Circuit breaker OPEN (Tier-2 by default)

### Symptom

One of:

- In **ITS_Daemon_Health**, a daemon's `Last Cycle Status` reads
  `CIRCUIT_OPEN` (instead of OK / WARN / DEGRADED).
- (Once PR 2 lands) a watchdog **WARN** that the Smartsheet circuit breaker
  has been OPEN longer than the prolonged-open threshold (default 10 min),
  naming the `opened_at` time.
- Smartsheet operations across multiple daemons start failing fast with a
  "circuit breaker OPEN — short-circuiting" message in ITS_Errors.

### What the Successor-Operator checks

1. **Is Smartsheet itself reachable?** Open the Smartsheet web UI and load any
   ITS sheet (ITS_Config, ITS_Daemon_Health). This is the single most useful
   check — it splits the two cases:
   - **Smartsheet is down / slow** → the breaker is doing its job (sparing the
     degraded service). Usually **no action** — it will probe and recover on
     its own once Smartsheet is healthy. The `CIRCUIT_OPEN` status may not even
     be landing in ITS_Daemon_Health during a true outage (that write is a
     Smartsheet call too) — the local watchdog check (PR 2) + Sentry are the
     out-of-band signals.
   - **Smartsheet is healthy in the UI but the breaker stays OPEN** → something
     is wrong on the ITS side, not the service. Proceed to the action below.
2. **ITS_Errors** — filter for the underlying Smartsheet failures that tripped
   it (e.g. `Script = safety_reports.intake_poll`, recent `SmartsheetError` /
   rate-limit rows). These records are reliably present whenever Smartsheet is
   reachable — **including the cooldown-after-recovery window** — because the
   ITS_Errors write bypasses the breaker (§3.1 forensic surface). Note whether
   they look like a transient incident (now passed) or a persistent
   auth/permission problem.

### The Claude prompt or UI action

- **Smartsheet is healthy and you just want ITS to resume now** (don't wait out
  the ~5-min cooldown): ask Claude to clear the breaker:

  > "Claude, Smartsheet is back up but the ITS circuit breaker is still OPEN.
  > Please clear it so the daemons resume."

  Claude deletes the local breaker state file (`rm ~/its/state/circuit_breaker.json`)
  — a missing file means CLOSED — or simply confirms the cooldown has elapsed.
  This is a **low-capability-class** action (clear a stuck local state file; no
  code, no secret, no external send).
- **The breaker keeps re-opening against a healthy Smartsheet** → that is not a
  service outage; see escalation.

### Escalate-to-Seth condition

Stop and escalate to the Developer-Operator (Seth, Tier 3) when **any** of:

- The breaker **re-opens repeatedly** against a Smartsheet that is healthy in
  the UI (points to an ITS code/SDK problem, not an incident).
- The underlying cause is in a **high-capability-class** category — the
  External Send Gate, **secrets / auth / Keychain**, **doctrine**, or it
  **requires a code change** to fix.
- The symptom is **novel** — it does not match this entry.

## Procedure — Alerts-per-hour cap reached (Tier-2 by default)

### Symptom

- A one-shot operator email **`[ITS] alert-rate cap reached — further alerts
  suppressed`**, or later **`[ITS] alert-rate-cap window summary`** ("N alerts
  were suppressed").
- The operator inbox suddenly goes quiet during what was clearly an incident.

### What the Successor-Operator checks

1. **ITS_Errors + Sentry** — the cap only bounds the *email* fan-out; **every
   alert was still recorded** (Op Stds v16 §3.1). Read the underlying storm
   there: which `Script` / `Error` is flapping, and how many rows.
2. Confirm it is a genuine storm (many distinct errors / a flapping daemon),
   not a single repeated alert (per-key dedupe already handles that).

### The Claude prompt or UI action

- Hand the root cause to Claude:

  > "Claude, the ITS alert-rate cap was reached. ITS_Errors shows
  > `<Script / Error>` flapping. Please diagnose and clear the underlying
  > problem."

  Clearing the **root error** is the fix; the cap itself needs no intervention
  — it self-clears when the rolling hour rolls over. Diagnosing/clearing a
  flapping daemon's root error is Tier-2 to the extent the fix is
  low-capability-class (re-run a daemon, toggle a config value, clear a stuck
  lock).

### Escalate-to-Seth condition

Escalate (Tier 3) when the storm's root cause is **high-capability-class** —
auth / secrets / Keychain, the External Send Gate, doctrine, or a code change —
or the storm is **novel**.

## Escape hatch — three layers (which works when)

If the breaker ever misbehaves, there are three independent levers, in order of
preference:

1. **Wait for auto-recovery.** OPEN automatically transitions to a single probe
   after the cooldown (default 5 min); a successful probe closes it. **Works
   whenever Smartsheet itself recovers** — no operator action.
2. **`rm ~/its/state/circuit_breaker.json`.** Deleting the local state file
   resets the breaker to CLOSED. **Always works — including during a total
   Smartsheet outage** (it touches only the local file). This is the
   most-reliable manual lever.
3. **Set ITS_Config `circuit_breaker.enabled = false`** (workstream `global`).
   The guard becomes a pass-through, so calls hit Smartsheet directly and
   surface the **real** errors instead of short-circuiting. **Works only when
   Smartsheet config reads succeed**; if the config row is unreadable the
   breaker falls back to **enabled** (the safe default), so this lever is *not*
   reliable during a Smartsheet outage — use lever 2 then.

Re-enable the breaker (`circuit_breaker.enabled = true`, or just leave the
default) once the investigation is done; disabling it removes the
incident-protection it exists to provide.

## Owner

`@solutionsmith`. New circuit-breaker / alert-cap failure modes that become
Tier-2-reachable should be added here as additional Symptom → checks → action →
escalate blocks, per Op Stds §43.
