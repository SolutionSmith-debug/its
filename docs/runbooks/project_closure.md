---
type: operations
date: 2026-07-23
status: active
related_prs: []
workstream: null
tags: [runbook, successor-remediation, job-closure, archive-on-closure, lifecycle, tier-2, smartsheet, box, d1]
---

# Runbook — Close or archive a project (what actually happens today) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (edits Smartsheet rows, uses the
portal admin pages, reads alert emails — does **not** read code or touch secrets). This runbook
documents **current behavior only** — what closing a job does *today*, not what a fuller closure
policy might someday do. The policy question (what *should* happen to the other per-job surfaces)
is a separate proposal pending planning-project ratification — see
`docs/reports/2026-07-23_project_closure_policy_proposal.md`.

The §42 code-reader rationale lives in `field_ops/fieldops_sync.py`
(`_archive_closed_job_trackers`) and `safety_portal/worker/fieldops_job_write.ts` (the lifecycle
route). Companions: [safety_portal_job_management.md](safety_portal_job_management.md) (add/retire
jobs), [hours_log_sync.md](hours_log_sync.md) (Fault F — the archive-move repair),
[fieldops_sync.md](fieldops_sync.md) (the mirror daemon).

## Purpose

One page answering: *"a project is finished — what do I do, and what does the system actually do?"*

The honest summary: **closing a job today is mostly a passive act.** Flipping a job off `Active`
makes it drop out of dropdowns, compiles, and intake — but almost everything the job ever produced
**stays exactly where it is**. Exactly one automated archival exists: a job explicitly set to
**Archived** has its four standing progress tracker sheets moved into `ITS — Archive / Closed
Projects`. Nothing else moves, anywhere.

## Where a job's lifecycle is set (this matters)

- **Portal-created jobs** (any job made in the portal — the normal case): the **portal Job Tracker
  page** is the *authoritative* lifecycle writer. An admin with the job-management capability
  selects **Active / Inactive / Archived** in the job's lifecycle selector. Do **not** flip the
  `Active` cell in `ITS_Active_Jobs` for these jobs — the mirror daemon **overwrites the sheet from
  the portal** on the job's next portal edit, so a sheet-side flip silently un-does itself.
- **Sheet-created (legacy) jobs** (rows added directly in `ITS_Active_Jobs` that never went through
  the portal): the sheet-side `Active` flip is the lever, per
  [safety_portal_job_management.md](safety_portal_job_management.md) Task B. Note for these jobs a
  sheet-side value of `Archived` behaves identically to `Inactive` — it can **never** trigger the
  tracker archive move (that automation only sees portal-origin jobs).

> **Display quirk (do not mistake for a failed write).** After a page reload, the portal's
> lifecycle selector for an **Archived** job displays **"Inactive"** — the detail view re-derives
> the selector from a coarser status field. The archive state is still stored and still drives the
> tracker move. Confirm an archive via its *effects* (the `Active` cell in `ITS_Active_Jobs`, or
> the tracker sheets appearing under Closed Projects), not the selector.

## Procedure

### Task A — Normal close: set the job **Inactive**

Use for a finished, paused, or on-hold job. Everything below is passive drop-out; it is all
reversible by setting the job back to Active.

What actually happens:

1. **Portal dropdowns**: the job leaves the submission dropdown (only Active jobs are served), so
   crews can no longer file against it.
2. **Late submissions are refused, not lost**: a submission that still names the job (e.g. queued
   before the flip) routes to the **Orphaned Reports** surface (or the Review Queue) for operator
   disposition. The field user still sees "received" — the refusal is operator-facing.
3. **Weekly compiles skip it**: both the safety and progress weekly generators iterate **Active**
   jobs only. No further WSR/WPR rows, packets, or week sheets are produced for the job.
4. **Mirror rows stay**: the job's rows in `ITS_Active_Jobs` and `ITS_Active_Jobs_Progress` remain
   in-sheet with `Active = Inactive` — the historical record. **Never delete the row.**
5. **D1 hygiene (automatic, delayed)**: once the job is inactive, each of its already-filed
   portal submission rows is pruned from the Worker's D1 cache when that row is 30+ days past
   its own filing date — old filings go on the next daily run, recent ones age out
   individually (Box + the week sheet remain the record). The D1 job row itself is deleted
   only if the job holds no records at all. This prune is monitored by watchdog Check V.

What does **not** happen: no sheet is moved or archived, no Box folder changes, no flat-log or
review rows change. See "What closure leaves in place" below.

### Task B — Permanent close: set the job **Archived**

Use for a job that is done for good. Archived does **everything Inactive does**, plus the one
automated archival in the system:

- On the job's next mirror cycle (the daemon runs continuously; allow a few minutes), the four
  standing progress tracker sheets that exist for the job —
  **`<Job> — Hours Log`**, **`<Job> — Equipment`**, **`<Job> — Material List`**, and
  **`<Job> — Material Incidents`** — are **moved** (pure relocation, never deleted) from the job's
  per-job folder in `ITS — Progress Reporting` into **`ITS — Archive / Closed Projects`**.
  Trackers that were never created for the job are simply skipped.

Caveats you must know:

- **Portal-origin jobs only.** Typing `Archived` into `ITS_Active_Jobs` for a sheet-created job
  changes the cell and nothing else — the move automation structurally never sees it.
- **Best-effort, no auto-retry.** If a move fails (e.g. a transient Smartsheet error), the system
  WARNs (`fieldops_archive_on_closure_failed` in `ITS_Errors`) and does **not** retry on its own —
  the job is already marked synced. The guaranteed repair is a one-off manual drag of the sheet
  into `ITS — Archive / Closed Projects` — see [hours_log_sync.md](hours_log_sync.md) **Fault F**.
- **Everything else stays.** Week sheets, the per-job folder itself, review rows, procurement
  sheets, Box — all unmoved (next section).
- **Field data after archival re-creates trackers.** If hours/equipment/material data somehow
  arrives for an archived job, the mirror passes find-or-create a **fresh** tracker back in the
  active progress folder. It re-archives only when the job row itself is next edited — not
  automatically. Archived jobs are not expected to receive new field data; treat a re-appearing
  tracker as a signal someone is still filing against a closed job.

> **Observation (2026-07-23):** the archive move has never yet fired against live data — the
> `Closed Projects` folder has never received a sheet, and the underlying move helper's live
> API smoke is still on the operator queue. If you perform the first live archival, check
> `ITS_Errors` afterward for `fieldops_archive_on_closure_failed` and verify the trackers landed
> (Validation below).

### Task C — What closure leaves in place (deliberate + known gaps)

Nothing below is touched by Inactive **or** Archived. Retention-in-place is the current de-facto
policy; which parts should change is exactly what the closure-policy proposal is for.

| Surface | Where it stays |
|---|---|
| Safety week sheets + per-job folder | `ITS — Safety Portal` workspace, in place |
| Progress week sheets + the per-job folder shell | `ITS — Progress Reporting` (only the 4 tracker *sheets* move on Archived; the folder and week sheets stay) |
| WSR / WPR human-review rows | Their review sheets, in place (send history) |
| `ITS_Active_Jobs` / `ITS_Active_Jobs_Progress` rows | In-sheet, flagged Inactive/Archived (by design — the history; never delete) |
| Per-job "Purchase Orders" / "RFQs" / "Subcontracts" sheets | The PO + Subcontracts workspaces' Jobs folders, in place |
| PO_Log / RFQ_Log / Estimate_Log / Subcontract_Log rows + procurement review rows | Flat ledgers, in place (live commercial records, retained by design) |
| The job's entire Box tree (week PDFs, packets, photos, PO/RFQ/quote/subcontract files) | Box, in place — no move/archive primitive even exists for Box today |
| D1 field-ops records (time entries, tasks, equipment history, checklists, …) | D1, retained (payroll-grade source records; only the guarded prune/purge paths above ever remove anything) |

### Task D — Removing a job entirely (destructive — NOT closure; escalate)

"Make this job disappear everywhere" is a **manual, three-system, destructive** operation —
removal, not archival — and it is **not a Tier-2 action**. Escalate to Seth. For the
Developer-Operator, the known footgun (HOUSE_REFLEXES §7):

1. **Delete the job's `ITS_Active_Jobs` row FIRST.** If the row still exists when the portal job
   is purged, the down-sync **re-creates** the job in D1 as a sheet-origin row within a minute.
2. Then purge the portal side (`purge-job`) — an atomic, audited **D1-only** cascade. It
   deliberately touches nothing outside D1.
3. Everything else — the Smartsheet per-job folders/week sheets/tracker sheets/log rows, and all
   Box files — is manual cleanup in each system's UI. No automation spans the three systems.

## Validation

- **Inactive took**: the job is gone from the portal submission dropdown; the next weekly compile
  produces no new WSR/WPR row for it; its `ITS_Active_Jobs` rows read `Inactive`.
- **Archived took**: additionally, each tracker sheet the job had now appears under
  `ITS — Archive / Closed Projects`, and `ITS_Errors` has no
  `fieldops_archive_on_closure_failed` WARN for the job. (Remember the selector display quirk —
  validate by effects, not the dropdown.)

## Escalate to Seth (Tier 3) when

- Any tracker failed to move and the Fault-F manual drag doesn't resolve it, or the WARN recurs —
  the move method, the archive hook, workspace/folder IDs, and Archive-workspace permissions are
  all high-class (code / config identity).
- You need a job **removed** (Task D) — destructive, three-system, Developer-Operator only.
- Anything that would *extend* archival beyond the four trackers (week sheets, Box, procurement,
  D1 export) — that is a doctrine-level policy change (§51), not an operational tweak.

## Owner

`@solutionsmith`. Update this runbook when the closure-policy proposal is ratified or the trigger
semantics change (both tracked in `docs/reports/2026-07-23_project_closure_policy_proposal.md`).
