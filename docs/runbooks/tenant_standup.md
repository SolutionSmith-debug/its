---
type: operations
date: 2026-07-23
status: active
related_prs: []
workstream: infrastructure
tags: [runbook, successor-remediation, cutover, standup, wipe, tier-2]
---

# Runbook — Tenant wipe / stand-up / finish lifecycle (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and reads Smartsheet rows + alert emails,
but does **not** read code or touch secrets. The §42 code-reader rationale lives
in `scripts/migrations/wipe_tenant.py`, `scripts/migrations/standup.py`, and
`scripts/migrations/sheet_ids_regen.py`.

> **The whole lifecycle is Developer-Operator (Seth) territory by default.**
> A tenant wipe is the most destructive operation in the repo, the stand-up
> rewrites source files, and the landing PR + fleet reload touch code and the
> External Send Gate posture — all FIXED high-capability classes (§44). What a
> Successor-Operator CAN safely do here is bounded to the *observation and
> resume* symptoms below; anything not listed escalates.

## The lifecycle, in one picture

```
wipe_tenant.py --commit          # dump-before-delete → deletes the sandbox tenant
        │                        # (dump lands at ~/its/logs/migrations/prewipe_<UTC>/)
standup.py [--dump DIR]          # builders + auto-FLIP + seeds + restore, ~40 stages
        │                        # writes standup_state.json + standup_<runid>.log beside the dump
(landing PR merges; ~/its pulled)
        │
standup.py finish [--posture dark]   # preconditions → state cleanup → fleet reload
                                     # → heartbeat wait → error sweep → gate report
                                     # → dashboard restart LAST
```

Every stage is idempotent; every failure prints a resume hint. The run state
(`standup_state.json`) lets `--resume` restart at the first incomplete stage.

## Symptoms and repairs

### Symptom 1 — a stand-up stage failed and printed a resume hint

**What you see:** `[abort] stage '<name>' failed (…): …` followed by
`Fix, then resume: python3 scripts/migrations/standup.py --resume`.

**Tier-2 repair (low-class):** transient Smartsheet/Box errors self-heal on
re-run — run the printed `--resume` command from the same directory. Every
builder is find-or-create idempotent; re-running a completed stage makes zero
writes. If the SAME stage fails twice with the same error, stop and escalate.

**Escalate to Seth when:** the failure names `expected_unresolved` (a builder
did not create what it should have), any `AMBIGUOUS`/duplicate-name refusal
(the tenant has drifted — never "fix" by renaming things in the Smartsheet UI),
or any error mentioning `sheet_ids`, git, or a PR.

### Symptom 2 — `--resume` refuses

**What you see:** `--resume: the recorded run is COMPLETE …` or
`supplied flags conflict with the recorded run …`.

**Tier-2 repair:** the refusal text says exactly what to do — a completed run
needs no resume; a flag conflict means re-run with the flags the refusal names
(the recorded ones). Never work around a refusal with `--start-at` unless Seth
names the stage.

### Symptom 3 — `finish` refuses a precondition

**What you see:** `[abort] finish precondition failed: …` naming git state,
`sheet_ids_regen --check`, or loaded daemons.

**Tier-2 repair:** for "not on main / tree differs from origin/main":
`git -C ~/its checkout main && git -C ~/its pull origin main`, then re-run
`finish`. For "daemons still loaded": the printed `launchctl bootout` lines,
then re-run. For a `sheet_ids_regen --check` MISMATCH: **escalate — do not
re-run `--write` yourself** (the landing PR may not have merged; flipping ID
surfaces is a code change).

### Symptom 4 — `finish` names heartbeat laggards after the reload

**What you see:** `heartbeat_wait: N daemon(s) not yet fresh: <names>` after
the bounded wait.

**Tier-2 repair:** wait one more interval (most daemons are 60–120s; the
weekly/15-min daemons are the usual laggards and are named as such). Re-run
`standup.py finish --verify-only` (re-runs only the read-only checks; no
reload). If a daemon never heartbeats: check its row in ITS_Daemon_Health and
its log under `~/its/logs/`; a missing ROW after a rebuild usually means the
daemon has not completed its first cycle yet. Still absent after ~30 min →
escalate with the daemon name + log tail.

### Symptom 5 — the post-reload error sweep shows CRITICALs

**What you see:** the `finish` error sweep lists rows from ITS_Errors dated
today with `Severity=CRITICAL`.

**Tier-2 repair:** none in-place — a CRITICAL right after a rebuild means a
daemon cannot reach something the rebuild should have provided. Copy the
printed (Script, Error) summary lines into the escalation. Do NOT clear or
mark-resolve the rows first (they are the evidence).

### Symptom 6 — the gate-flip report shows a gate at a different posture than pre-wipe

**What you see:** the `finish` gate report prints, per `*_enabled` row:
pre-wipe dump value vs live value, with the row's full Description inline.

**Tier-2 repair:** NONE. The report is deliberately read-only. Every gate flip
is at minimum a config decision and every send-adjacent gate is a FIXED
high-capability class — read the Description (an in-cell "do NOT set true
until …" is doctrine per §44), then escalate the list to Seth, who flips.

### Symptom 7 — the dashboard ACT buttons refuse with "stand-up in progress"

**What you see:** dashboard ACT verbs (config edit, error resolve, …) refuse
while the fence marker is fresh. This is CORRECT during a wipe→rebuild window —
the marker is set by `wipe_tenant --commit`, refreshed by every `standup.py`
run, and cleared ONLY when a stand-up run COMPLETES (an aborted run stays
fenced across the fix→resume gap, on purpose — PR #674).

**Tier-2 repair:** if a run genuinely crashed and no rebuild is in progress,
the fence fails OPEN by itself after its max-age (6h). To unfence now:
`rm ~/its/state/standup_in_progress.json` — the file is a fence marker, not
data. Never delete it while a wipe or stand-up is actually running.

## Boundaries (the both-rule, §44)

Escalate — never act — on: running `wipe_tenant.py --commit` (ALWAYS
Seth-attended); any `--posture full` finish (loads the send-dispatch daemons —
External Send Gate); any gate flip the report suggests; editing
`shared/sheet_ids.py` or re-running `sheet_ids_regen.py --write` by hand;
anything touching the landing PR, git conflicts, or Keychain.

## Notes for the Developer-Operator

- The wipe/stand-up daemon-down guards EXEMPT `org.solutionsmith.its.dashboard`
  so the read-only panels stay observable over Tailscale mid-run; its ACT verbs
  are fenced by the stand-up marker (`operator_dashboard/auth.py`, PR #674 —
  fail-open past 6h so a crashed run never bricks the dashboard).
- `finish --posture dark` (default) excludes ALL send-dispatch plists
  (po-send, rfq-send, subcontract-send, weekly-send, progress-send). Loading
  them is `--posture full` + a typed confirmation, or per-plist by hand — a
  FIXED External-Send-Gate action either way.
- The per-run transcript (`standup_<runid>.log`) and `standup_state.json` sit
  beside the dump — attach both to any escalation.
