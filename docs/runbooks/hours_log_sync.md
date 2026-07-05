---
type: operations
date: 2026-07-04
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, fieldops_sync, hours-log, tier-2, track-2, archive-on-closure]
---

# Runbook — Hours Log up-sync (P7, the `fieldops_sync` hours pass) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (reads Smartsheet rows + alert
emails, not code). The §42 code-reader rationale lives in `field_ops/fieldops_sync.py`
(`_mirror_hours_pass`) and `progress_reports/hours_log.py`. This is the hours-pass companion to the
job-mirror runbook `fieldops_sync.md`; the two share one daemon, one lock, one heartbeat.

## What this controls

A **pass inside the existing `fieldops_sync` daemon** (5-min launchd) that mirrors each crew time
entry (D1) UP into the job's standing **`<Job> — Hours Log`** Smartsheet in the `ITS — Progress
Reporting` workspace. One-way-up, send-free + AI-free. Append-only; an amend appends its own row +
flips the prior to `Superseded`; NEVER deletes. Gated by `field_ops.fieldops_sync.hours_enabled`
(ITS_Config, Workstream `field_ops`) — SHIPPED OFF; the operator flips it on at cutover (after
migration `0038` is applied + the Worker is deployed).

## Fault A — enabled the hours pass but no Hours Log rows appear

**Symptom.** `hours_enabled=true` but a job's `<Job> — Hours Log` sheet stays empty (or a new time
entry never shows up) after a few minutes.

**Check (read-only).** (1) `ITS_Config` — `field_ops.fieldops_sync.hours_enabled=true` AND
`field_ops.fieldops_sync.sync_enabled=true` (the hours pass runs INSIDE the same daemon; if the
daemon's master `sync_enabled` is off, nothing runs). (2) `ITS_Config system.state` — `PAUSED`/
`MAINTENANCE` halt the whole daemon. (3) `ITS_Daemon_Health` row `field_ops.fieldops_sync` — is
`Last Cycle At` recent? A stale timestamp = the daemon isn't cycling (host issue → escalate). (4)
`ITS_Errors` `Script=field_ops.fieldops_sync` — any `fieldops_hours_*` rows (see Faults B–D). (5)
Did a crew actually log a time entry for that job in the portal? No entries → an empty Hours Log is
correct.

**Repair (Tier-2, low-class).** Flip `hours_enabled` (and/or `sync_enabled`) to `true` in the
Smartsheet UI and wait one cycle (~5 min); un-PAUSE `system.state` if needed. If the daemon is
cycling, both gates are on, and entries exist but still don't mirror, hand Claude: *"the
fieldops_sync hours pass is enabled and the daemon is alive but a logged time entry for `<job>`
isn't reaching its Hours Log — diagnose."*

## Fault B — a hours mirror PERMANENTLY failed (Review Queue)

**Symptom.** `ITS_Errors` `Script=field_ops.fieldops_sync`, `Error=fieldops_hours_permanent`, AND an
**ITS_Review_Queue** row (Workstream `progress_reports`) `field-ops Hours Log up-sync: PERMANENT
failure …`. The entry is left **unmirrored** (its `mirrored_at` stays NULL) so it re-attempts once
the cause is fixed.

**Check (read-only).** The Review-Queue row's `payload` names the `phase` (`ensure-sheet` or
`upsert`), the `job_id`/`project_name`, the `entry_uuid`, and the error class (e.g. a Smartsheet
HTTP-400 reject or a sheet-name overflow).

**Repair (Tier-2, low-class).** The mirror is idempotent (find-or-create by `Entry UUID`), so once
the cause is resolved the next cycle re-mirrors automatically. If it needs a nudge, hand Claude the
correlation id: *"Hours Log mirror keeps permanently failing for `<job>` entry `<uuid>`
(`<phase>`) — diagnose and re-run."* No code/secret/send for a Tier-2 fix.

## Fault C — hours-pending / mark-mirrored UNAUTHORIZED (401)

**Symptom.** `ITS_Errors` CRITICAL `Error=fieldops_hours_pending_auth_failed` or
`fieldops_hours_mark_mirrored_unauthorized` — the field-ops bearer was rejected. For the
mark-mirrored case the rows ARE filed to the Hours Log; only the D1 watermark didn't advance, so
they re-mirror idempotently (harmless) once the bearer is fixed.

**This is a secrets/auth fault → ESCALATE to Seth.** Do not attempt a Tier-2 fix (the field-ops
bearer / Keychain / Worker secret are high-capability-class). Same token as the job-mirror pass, so
a 401 here usually means the whole `fieldops_sync` daemon is 401ing (see `fieldops_sync.md` Symptom
B/E).

## Fault D — "amend prior missing" WARN

**Symptom.** `ITS_Errors` WARN `Error=fieldops_hours_amend_prior_missing`. **Meaning:** an amended
time entry mirrored before the entry it amends (out-of-order). The amend's OWN row is written
correctly; only the prior row's `Superseded` flip was skipped. **Self-heals** — no action; the
compile-time rollup already collapses amend chains, and the prior arrives on a later cycle.

## Fault E — Hours Log nearing the Smartsheet row cap (period-split needed)

**Symptom.** `ITS_Errors` WARN `Error=hours_log_row_cap_warn` + an **ITS_Review_Queue** row
(Workstream `progress_reports`) `Hours Log '<Job> — Hours Log' nearing the Smartsheet row cap …`.
**Meaning:** a standing Hours Log has grown near the ~20k Smartsheet per-sheet cap. This is the §51
A5 row-cap watchdog working as designed — the single-standing-sheet model period-splits **at the
cap**, not on a calendar (2026-07-04 v19.x rider).

**Repair (Tier-2, low-class).** **Period-split the sheet — NEVER delete rows:** rename/archive the
full `<Job> — Hours Log` (e.g. to `<Job> — Hours Log (through <date>)`) and let the daemon
find-or-create a fresh `<Job> — Hours Log` on its next entry. (This row-cap period-split is for a
STILL-ACTIVE job; the separate archive-on-closure automation `its#462` — Fault F below — moves a
CLOSED job's tracker to the Archive workspace.) The WARN threshold is
`progress_reports.hours_log.row_cap_warn_threshold` (ITS_Config, default 15000) — nudging it is a
low-class tweak; a recurring need to split at high volume is expected, not a fault.

## Fault F — a closed job's Hours Log didn't move to Closed Projects (archive-on-closure)

**Symptom.** A job was closed (its `lifecycle` went to `archived`) but its `<Job> — Hours Log` sheet
is still sitting in the per-job folder under `ITS — Progress Reporting`, not in the **Closed
Projects** folder of the `ITS — Archive` workspace. There may be an `ITS_Errors` WARN
`Script=field_ops.fieldops_sync`, `Error=fieldops_archive_on_closure_failed`.

**What it is (design).** When `fieldops_sync` mirrors a job whose `lifecycle=archived` (§51
archive-on-closure), it MOVES the job's standing tracker sheets — today only the `<Job> — Hours Log`
— into the Archive workspace's Closed Projects folder. It is a pure **relocation** (never a delete:
the sheet, rows, and history are preserved) and it is **best-effort** — a move failure WARNs and is
left for the next dirty cycle; it never fails or un-does the mirror itself. It is idempotent: once the
sheet is moved out of the source folder it is no longer found there, so a re-seen archived job is a
no-op.

**Check (read-only).** (1) Is the job actually `archived` in `ITS_Active_Jobs`? A still-active job is
correctly NOT archived. (2) `ITS_Errors` `Error=fieldops_archive_on_closure_failed` — the WARN names
the `job_id` / `project_name` and the underlying error (e.g. a transient Smartsheet 5xx, or a
permission error on the Archive workspace / Closed Projects folder). (3) Is the sheet ALREADY in
Closed Projects? If so this is a stale observation — it self-healed.

**Repair (Tier-2, low-class).** The move re-attempts every time the job re-appears dirty, so first
just **re-run `fieldops_sync`** (or wait a cycle) — a transient failure clears itself. If it keeps
WARNing, hand Claude the `job_id`: *"the fieldops_sync archive-on-closure move keeps failing for
`<job>` — its Hours Log isn't reaching Closed Projects; diagnose."* A one-off manual move of the
single sheet in the Smartsheet UI (drag `<Job> — Hours Log` into `ITS — Archive / Closed Projects`)
is also low-class and harmless — the daemon then finds nothing to move.

**Escalate-to-Seth boundary.** Anything touching the **move method itself** (`move_sheet_to_folder`),
the archive hook, the workspace/folder IDs, or the Archive-workspace **permissions/sharing** is a
**code / secrets change → high-class → escalate**. Repeated failures after the cause looks fixed, or a
novel symptom, escalate.

## Escalate-to-Seth boundary (observable terms)

Escalate — do **not** attempt — when: the failure names **secrets/auth/Keychain** (Fault C), the
**External Send Gate**, **doctrine**, or needs a **code change**; the `field_ops.fieldops_sync`
daemon row is **frozen** (hung/host issue); a permanent failure **persists** after the cause looks
fixed; or the symptom is **novel**. Tier-2 here is exactly: flip `hours_enabled` / `sync_enabled`,
un-PAUSE `system.state`, or ask Claude to re-run an idempotent re-mirror.

## Owner

`@solutionsmith`. New Tier-2-reachable failure modes get added here as Symptom → check → repair →
escalate blocks (Op Stds §43).
