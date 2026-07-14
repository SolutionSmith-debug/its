---
type: operations
date: 2026-07-10
status: active
related_prs: []
workstream: global
tags: [runbook, successor-remediation, operator-dashboard, config-editor, class-a, pin, external-send-gate, tier-2, tier-3]
---

# Runbook — Operator Dashboard Class-A config editor (Successor-Remediation, Op Stds §43)

The **WS2 D1-2 config editor** is the ACT surface of the operator dashboard — a
loginless, **localhost-only** (`127.0.0.1:8484`, Tailscale-served) FastAPI page that
edits **Class-A** `ITS_Config` settings: pause/resume gates, tuning knobs, behavior/data
config. It has **one** mutating route (`POST /act/config`); everything else is read-only.

It writes **only to `ITS_Config`** — an internal system-of-record write (Op Stds §51),
**not** an external send. The External Send Gate (Invariant 1) stays with the daemons; this
editor cannot send anything. A runtime edit takes effect on the daemon's **next cycle**.

**Two controls guard every write:** the **operator PIN** (Keychain `ITS_OPERATOR_PIN`,
constant-time compare, **fails closed** — a missing/locked keychain DENIES) and an
**Origin allowlist** (localhost + `ITS_DASH_ALLOWED_ORIGINS`). Every applied edit and every
escalation writes a durable audit row to `ITS_Errors` (`error_code=config_audit`, WARN).

## Activation (Developer-Operator, one-time — HIGH-CLASS, not a Tier-2 step)

The PIN is a **secret** → provisioning/rotating it is a FIXED high-capability action (Op Stds
§44); only Seth does it, never a Tier-2 repair.

```bash
# provision the operator PIN (interactive -w prompts, no echo)
security add-generic-password -a "$USER" -s ITS_OPERATOR_PIN -w
# allow the Tailscale-served origin (comma-separated; localhost is always allowed)
export ITS_DASH_ALLOWED_ORIGINS="https://<host>.<tailnet>.ts.net"
python -m operator_dashboard         # http://127.0.0.1:8484  (then: tailscale serve 8484)
```

**Use a STRONG PIN (not a 4-digit).** The endpoint rate-limits wrong guesses, locks out
for 60s after 5 consecutive failures, and pages a CRITICAL (`config_pin_lockout`) — but a
strong PIN + that throttle are a **hard precondition before setting
`ITS_DASH_ALLOWED_ORIGINS` to any Tailscale origin** (a tailnet device can otherwise reach
the endpoint directly).

## Run as a launchd service + Tailscale (D1-3b)

The dashboard is a **long-running server**, not an interval daemon — so its plist is the ONE ITS
plist that sets `KeepAlive=true` (launchd restarts the server if it exits). It writes **no**
watchdog Check-C marker and is **not** in `TRACKED_JOBS`; liveness is launchd + the `/healthz`
endpoint. Install it (non-interval → the generic `install.sh` path, no interval arg):

```bash
scripts/launchd/install.sh load   org.solutionsmith.its.dashboard   # serves 127.0.0.1:8484
scripts/launchd/install.sh status org.solutionsmith.its.dashboard   # confirm it is running
```

**Tailscale exposure + the Origin allowlist** — the #1 activation stumble: a launchd service does
NOT inherit your shell env, so `ITS_DASH_ALLOWED_ORIGINS` must live in the *installed* plist. The
helper prints the exact commands + the origin for THIS host (it exposes nothing by itself):

```bash
operator_dashboard/tailscale_serve.sh          # prints: serve cmd + origin + plist patch
# then, from its output:
tailscale serve --bg 8484
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:ITS_DASH_ALLOWED_ORIGINS https://<host>.<tailnet>.ts.net" \
    ~/Library/LaunchAgents/org.solutionsmith.its.dashboard.plist
scripts/launchd/install.sh load org.solutionsmith.its.dashboard    # reload with the origin set
```

Localhost (`http://127.0.0.1:8484`) is always allowed even if the origin is blank; only the
Tailscale-served origin needs the env. **Ships dark**: loading the plist serves the read-only
panels + the (still PIN-gated, inert-until-provisioned) editor.

## Interval daemon edits (Class B · elevated · D1-3b)

A poll daemon's cadence is **baked into its launchd plist** at install time — not a hot-reload
`ITS_Config` edit (that is why `*.poll_interval_seconds` is deliberately NOT a Class-A editable
key). The **Interval daemons** panel changes it correctly: it updates the
`<ws>.<daemon>.poll_interval_seconds` `ITS_Config` row **and** re-installs the plist
(`install.sh load <label> <interval>`) so the new cadence takes effect. Label-allowlisted to the 8
interval daemons; interval bounds-validated (10..86400s); elevated-confirm (re-PIN + type the exact
daemon label). Every edit audits `config_interval_edited`.

**Symptoms & Tier-2 repairs:**

- **"… is not an editable interval daemon"** — the label is not one of the 8 allowlisted interval
  daemons (the dashboard itself, `watchdog`, `weekly-generate`, or a typo). The allowlist is working.
  **Tier-2:** pick a listed daemon; a non-interval daemon's schedule is a plist edit (Seth).
- **"no ITS_Config row for … — seed it first"** — that daemon has no `poll_interval_seconds` row.
  Seeding a **missing** row is **high-class → escalate to Seth** (same rule as the config editor).
- **"must be 10..86400 seconds"** — out of bounds. **Tier-2:** pick a value in range.
- **"ITS_Config updated to Ns but plist reinstall failed (exit …)"** — the row was written but
  `install.sh load` failed (a durable `config_interval_reinstall_desync` WARN is recorded). **Tier-2:**
  re-run the exact command it prints (`install.sh load <label> <interval>`) from `~/its`; if it keeps
  failing (plutil / launchctl error), **escalate**. The daemon keeps its OLD cadence until the reinstall
  succeeds.

## Acceptance smoke (Developer-Operator — the DoD live toggle)

Prove the write path end-to-end on the **mirror** (needs the PIN provisioned above):

1. `GET /config` renders the editor with current live values.
2. Toggle `safety_reports.intake.box_filing_enabled` (a plain Class-A gate) `true → false`
   with the PIN → the outcome shows **applied**; confirm the `ITS_Config` `Value` cell flipped.
3. Confirm the intake daemon honors it next cycle (its `#336 REQUIRED_CONFIG` startup log
   / behavior reflects the new value).
4. Confirm the `ITS_Errors` audit row: `error_code=config_audit`, Severity `WARN`, message
   `config edit applied: safety_reports.intake.box_filing_enabled [safety_reports] ... by <user>`.
5. Toggle it back `false → true`.

## Symptoms & Tier-2 repairs

**"denied: operator PIN not provisioned (ITS_OPERATOR_PIN)"** — no PIN in Keychain. This is
the **fail-closed** default. Repair is **high-class** (provision the secret) → **escalate to
Seth**; a Tier-2 operator does not create secrets.

**"keychain is locked — run `security unlock-keychain`"** — common after reboot. **Tier-2, low-class:**
run `security unlock-keychain` on the Mac, then retry. If it recurs, escalate.

**"incorrect PIN"** — wrong PIN typed. Re-enter. (Attempts are audited `config_denied`.)

**"too many failed attempts — temporarily locked out"** — 5+ wrong PINs tripped the
brute-force lockout and paged a CRITICAL `config_pin_lockout`. **Tier-2:** wait 60s, retry
with the correct PIN. **If you did NOT cause it**, treat the CRITICAL as a possible
brute-force against a Tailscale-exposed dashboard → **escalate to Seth.**

**"refused: origin '…' is not allowed"** — the browser's Origin isn't allowlisted (usually the
Tailscale hostname). **Tier-2, low-class:** set `ITS_DASH_ALLOWED_ORIGINS` to the exact served
origin and restart the app. A cross-origin *attacker* hitting this is the control working — not a fault.

**"… is not an editable Class-A key"** — the setting is intentionally read-only (Class B/C/E, e.g.
`external_send_gate`, `system.state`, `config_actuator.polling_enabled`, any `*.poll_interval_seconds`),
OR has no seeded `ITS_Config` row. Editing a non-Class-A key is **out of scope** (D1-3 / Seth); a
*missing* row is seeded by Seth. **Escalate.**

**"turning ON … is a dark→live activation — routed to the escalate path, NOT applied"** — this is
**by design** for send-poller gates (`weekly_send.polling_enabled`, `po_send.polling_enabled`, the
`po_poll.*` gates). Turning one **on** is a first activation with go-live preconditions and, for
`po_send`, the **External Send Gate** — a FIXED high-capability class. **Escalate to Seth**; do not
work around it. **Pausing** (turning a gate off) is always available here and is a Tier-2 action.

**"rejected: must be …"** — a validator caught a bad value (out of range / wrong type / bad format).
**Tier-2, low-class:** fix the value to match the stated rule and retry. Nothing was written.

**"write failed: …" / "could not read current value: …"** — a Smartsheet error (often the circuit
breaker OPEN, or a token issue). **Tier-2:** check the dashboard's circuit-breaker panel; once the
breaker closes, retry. A persistent auth/token error is **high-class** → escalate.

## Boundary (always escalate to the Developer-Operator, Seth)

PIN provisioning/rotation (secret) · any **send-poller activation** (External Send Gate) · editing a
**Class-B/C** key · seeding a **missing** `ITS_Config` row · any **code** change. These are the FIXED
high-capability classes (Op Stds §44); they never get a Tier-2 self-repair.
