---
type: operations
date: 2026-06-29
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, weekly_send, external-send-gate, workstream-guard, contamination, tier-2, tier-3]
---

# Runbook — Safety weekly-send Workstream guard (a row stuck HELD / "contamination") (Successor-Remediation, Op Stds §43)

`safety_reports/weekly_send.py` is the **send half of the External Send Gate** (FM v11
Invariant 1). As of P1b it carries a **cross-workstream contamination guard**: every
`WSR_human_review` row has a `Workstream` tag, and the sender refuses to transmit a row
whose tag is not `safety`. This runbook covers the two operator-visible symptoms the
guard produces.

## Symptom A — a WSR row is stuck `Send Status = HELD` with Notes `[HELD: workstream contamination …]`, and/or a CRITICAL alert `weekly_send.workstream_mismatch`

**What it means.** A `WSR_human_review` row was tagged for a **different** workstream than
`safety` (e.g. `Workstream = progress`), so the sender **blocked the send** (fail-closed) and
marked the row HELD. No email went out. This is the guard working: a wrong-workstream packet
nearly transmitted from the safety sender.

**Boundary — this is a FIXED high-capability-class event → escalate to Seth (the
Developer-Operator).** A contamination HELD touches the **External Send Gate**, one of the four
fixed high-class categories (FM v11 / Op Stds §44 "both-rule"): it escalates regardless of
documentation. The Successor-Operator does **NOT** self-repair it (do not clear the HELD, do not
re-tag the row, do not re-approve).

**What the Successor-Operator checks (read-only) before escalating:**
1. Open the HELD `WSR_human_review` row. Read the `Workstream` cell and the Notes
   `[HELD: workstream contamination row=… != sender 'safety']`.
2. Confirm this is the **safety** review sheet (`WSR_human_review`) — the safety sender only
   ever runs against it.
3. Do **not** change anything. Capture the row ID, the `Workstream` value, and the Job ID.

**Escalate to Seth** with that evidence. The likely causes are a code/config defect (a progress
row mis-filed onto the safety sheet, or a mis-bound `SendConfig`) — all code-change territory.

## Symptom B — a flood of WARN `weekly_send.workstream_absent`

**What it means.** Rows are reaching the sender with an **empty** `Workstream` tag. The sender
proceeds (back-compat fail-open) but WARNs. This is expected only **transiently** right after
P1b ships, for rows compiled before the column existed. A persistent flood means the
backfill migration never ran, or new rows aren't being seeded.

**This is a lower-class, documented Tier-2 repair** (a schema/config action, NOT a Send-Gate
change): re-run the one-shot column+backfill migration.
1. Confirm `~/its` is on the latest `main` (`git -C ~/its pull origin main`).
2. Preview: `python3 scripts/migrations/add_wsr_workstream_column.py` (no write).
3. Apply: `python3 scripts/migrations/add_wsr_workstream_column.py --commit`
   (creates the `Workstream` PICKLIST column if absent, backfills every blank row to `safety`).
4. Re-run `python scripts/smoke_test_weekly_send.py` — Stage 4 should now report the
   `Workstream` column present; the WARN flood stops on the next cycle.

If the migration errors (e.g. "exists but type … not PICKLIST"), that is a schema problem
→ **escalate to Seth** (Tier-3 schema fix).

> **Posture note (Seth-only — do NOT flip unilaterally).** Today a *genuinely* absent tag
> (a null/empty cell) takes the WARN-and-proceed path (bounded back-compat — see §42: the WSR
> sheet is single-workstream by construction). A *malformed* non-empty whitespace cell is
> already HARD-HELD. Tightening the genuinely-absent case to **fail-CLOSED** (HELD) in the
> post-backfill steady state is a **Send-Gate posture decision = high-capability-class →
> reserved for the Developer-Operator (Seth)**. The Successor-Operator must NOT change this
> posture; the current bounded fail-open is deliberate.

## Why the guard is shaped this way (pointer to §42)

The code-reader rationale lives in `safety_reports/weekly_send.py` (the `SendConfig` block and
the Stage-2b guard comment): every `SendConfig` field is required with no default (a default
would let a new workstream silently inherit safety's recipients/sheet/tag); the guard sits after
the SENT/HELD skip gates and before the write-ahead SENDING marker (a contaminated row never
enters the in-flight state); PRESENT-mismatch = CRITICAL+HELD, ABSENT = WARN+proceed.
