---
type: operations
date: 2026-06-29
status: active
related_prs: []
workstream: progress_reports
tags: [runbook, successor-remediation, smartsheet, progress-reporting, picklist, tier-2]
---

# Runbook — Progress Reporting config sheets (WPR_human_review + ITS_Active_Jobs_Progress) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and edits Smartsheet rows + reads alert
emails, but does **not** read code or touch secrets. The §42 code-reader rationale
lives in `scripts/migrations/build_progress_reporting_workspace.py`,
`build_wpr_human_review_sheet.py`, `build_its_active_jobs_progress_sheet.py` (sheet
schemas), and `shared/picklist_validation.py` (the Active / Workstream / Send Status
allowed sets). The structural twin is `docs/runbooks/safety_portal_config_sheets.md`.

## Purpose

The Progress Reporting flow owns the standalone **ITS — Progress Reporting**
workspace and two cross-job sheets in its **Control** folder:

- **WPR_human_review** — one row per (Job, Week); the weekly progress report is
  reviewed + approved + sent from here (the twin of WSR_human_review).
- **ITS_Active_Jobs_Progress** — the progress workspace's own physical Active-Jobs
  sheet (the job-tracker-pivot second sheet); holds the **Progress Reports Contact /
  CC** recipients + a **Portal Job Key** bridge column. Only **Active = Active** rows
  feed the progress send.

This runbook covers the low-class faults a Successor-Operator can resolve and the
boundary where it escalates to Seth (Tier 3). Until the progress compile/send daemons
land (P4/P5) nothing reads these sheets at runtime, so most faults today are
**build-time / prerequisite**, not runtime — and non-urgent.

## Procedure

### Fault A — The workspace / a sheet wasn't built, or a build migration failed

**Symptom.** A build migration errored, or `shared/sheet_ids.py` still reads `0` for
`WORKSPACE_PROGRESS_REPORTING` / `FOLDER_PROGRESS_CONTROL` / `SHEET_WPR_HUMAN_REVIEW`
/ `SHEET_ACTIVE_JOBS_PROGRESS`, or a sheet exists with missing columns.

**Check.** Open the **ITS — Progress Reporting** workspace → **Control** folder.
Confirm both sheets exist with their columns.

**Action (Tier-2, low-class for the re-run; co-resolve for the file edit).** Re-run
the idempotent migrations in order — each find-or-creates by name and skips what
already exists:
> "Claude, re-run `scripts/migrations/build_progress_reporting_workspace.py`, then
> `build_wpr_human_review_sheet.py`, then `build_its_active_jobs_progress_sheet.py`,
> and report each printed id (workspace / folder / both sheets) and what was created
> vs skipped."

Then flip each printed id into `shared/sheet_ids.py` (replacing the `0`). Re-running
the migration and reading the printed id is operator-safe; the id **value** is
operator-safe, but editing `shared/sheet_ids.py` is a code-file edit — if unsure,
**co-resolve with Seth** (it borders the code FIXED category). Flipping
`SHEET_WPR_HUMAN_REVIEW` / `SHEET_ACTIVE_JOBS_PROGRESS` is what activates their
picklist-registry entries (the `if sheet_ids.SHEET_...:` guards).

### Fault B — An approver can't approve a progress report (the §46 prerequisite)

**Symptom.** A reviewer who can approve **safety** reports cannot approve a
**WPR_human_review** row, or every progress send is HELD with an empty-recipient /
unauthorized-approver signal.

**Why.** Per Op Stds **§46**, the authorized-approver set for the progress workspace
is resolved live from that workspace's **share membership** — sharing the workspace
IS granting approval authority. An approver who was only shared into the **Safety
Portal** workspace is *not* automatically a member of **ITS — Progress Reporting**,
and an empty resolved set **fails closed** (blocks all progress sends).

**Action (Tier-2, low-class).** **Re-share every current safety-workspace approver
into the ITS — Progress Reporting workspace** as an **individual USER share** (group
shares carry no email and don't count). This is the P5-blocking prerequisite and is a
normal Smartsheet sharing action — not a code change.
> "Claude, list the USER share emails of the Safety Portal workspace and of the
> Progress Reporting workspace, and report who is in the first but missing from the
> second."
Then add the missing people in the Smartsheet UI.

### Fault C — Picklist option / schema drift (Active / Workstream / Send Status)

**Symptom.** A write is rejected with a picklist violation, or `audit_picklist_drift`
reports a mismatch on **Active** (`Active / Inactive / Archived`), **Workstream**
(`progress` only on WPR), or **Send Status** (`PENDING / SENDING / SENT / FAILED /
HELD` on WPR).

**Check + Action (Tier-2, low-class).** Have Claude run `audit_picklist_drift`; if it
surfaces a missing option, re-run the relevant build migration (it re-asserts the
option set on create). Do **not** hand-add options in the Smartsheet UI — these value
sets are the contract the send machinery + the P1b contamination guard render against
(a `safety` tag on the progress sheet is itself a contamination signal).

### Fault D — A schema change the read/send contract depends on

**Symptom.** A request to rename/retype a column the flow reads/writes (Job / Project,
Job ID, Portal Job Key, Week Of, Email Body, Send Status, Workstream, Progress Reports
Contact \*, Active), or to add/remove such a column.

**Action — ESCALATE to Seth (Tier 3).** This **touches code** (the shared review
module, the SendConfig/DaemonConfig column bindings, the build schema, and the
picklist registry) — one of the four FIXED high-capability-class categories. Workspace
creation, any Send-Gate / doctrine question, and any code edit also escalate.

**Both-rule (Op Stds §44):** re-running an idempotent migration, re-sharing an
approver, or editing a job/recipient **row** is low-class Tier-2; changing a
**column/schema** or any **code/doctrine** is high-class Tier-3 (co-resolve with
Seth).

## Routine office-PM edits (NOT faults)

Adding/retiring a job (set `Active=Inactive`), filling an **Address**, or editing the
**Progress Reports Contact / CC** recipients on `ITS_Active_Jobs_Progress` are normal
office-PM edits in the Smartsheet UI — not remediation. (The **Portal Job Key** column
is daemon-written from Slice 5 onward — leave it to the mirror daemon; do not hand-edit
it.)

## Owner

`@solutionsmith`. Sheet schemas, the value sets, the shared review module, and the
config bindings are code (Tier 3) — see
`scripts/migrations/build_progress_reporting_workspace.py`,
`build_wpr_human_review_sheet.py`, `build_its_active_jobs_progress_sheet.py`,
`progress_reports/wpr_review.py`, and `shared/picklist_validation.py`.
