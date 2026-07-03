---
type: operations
date: 2026-06-30
status: active
related_prs: [383, 384, 385]
workstream: field_ops
tags: [enablement, a8, p2.5, office-pm, job-tracker, smartsheet, lifecycle]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest. No concrete §6a
manifest / capability-registry file exists in this exec repo yet (it is referenced as a
definition-of-done obligation in the blueprint `workstreams/progress-reporting/mission.md`
and tracked OPEN in `docs/tech_debt.md` — "§6a enablement-doc DoD owed"). Once that manifest
artifact exists (or its blueprint home is confirmed), add the `portal_job_creation` capability
entry there. Do not fabricate a registration in the meantime. -->

# Office-PM Guide — Creating jobs in the ITS Portal (P2.5)

**Audience:** the Evergreen office project manager (PM) who maintains the job list and the
report-routing contacts. No code or Smartsheet formula knowledge required. This is the
plain-language companion to the successor-operator runbook
[`docs/runbooks/fieldops_job_write.md`](../runbooks/fieldops_job_write.md).

## Purpose — what changed and why

**Job creation now lives in the ITS Portal.** Use the **Job Tracker → "New job"** form (admin
sign-in; it is gated to the job-manage permission) to create and manage jobs. You no longer add a
new job by hand-typing a row into the `ITS_Active_Jobs` Smartsheet.

**Why:** one form is now the single place a job's routing lives — the project, its site, the
stakeholder, the **Safety Reports** contact + CCs, and the **Progress Reports** contact + CCs.
The portal writes that one set of facts **up** into **both** report workspaces' Active-Jobs
sheets (the Safety `ITS_Active_Jobs` sheet and the Progress `ITS_Active_Jobs_Progress` sheet), so
the safety side and the progress side can never drift out of sync. The job still appears in the
portal exactly as before; you just enter it once.

> **One important interim note up front:** the piece that copies portal jobs **up into the
> Smartsheets** is not switched on yet. See [What's NOT live yet](#whats-not-live-yet-pending)
> at the bottom — read it before you rely on a portal-created job showing up in the sheets or in
> a weekly report.

## Data dictionary — the "New job" form fields

Every field below is on the create form. **Only the Project name is required**; everything
else is optional but recommended (the report routing only works if the contacts are filled in).

| Form field | Required? | Rules | What it feeds downstream |
|---|---|---|---|
| **Job ID** | — (assigned) | **You do not type this.** When you click Create, the portal assigns the next number automatically — `JOB-000017`, `JOB-000018`, … — and shows it to you on the confirmation. | The job's one permanent identifier — the **same number everywhere**: the portal, **both** Active-Jobs sheets' "Job ID" column, every weekly safety/progress report, and the Box folders. There is no separate typed code and no second auto-number — **one job, one number**. (It is also written into the **"Portal Job Key"** column on both sheets, which carries the same value — that column is how the two sheets and the safety/progress pipelines recognise the same job.) |
| **Project name** | Yes | 1–256 characters. | The **Project Name** column on both sheets; shown throughout the portal and on reports. |
| **Address** | No | Up to 512 characters. | Job-site address, recorded on the job for reference. |
| **Stakeholder — name / email / phone** | No | Name ≤256, email ≤320 (must look like an email if filled), phone ≤40. | Recorded on the job as the owner/stakeholder of record. **Note:** the stakeholder is *not* auto-CC'd on the weekly safety/progress emails — those go to the report contacts + CCs below. |
| **Safety Reports — contact name / email** | No (but required for safety routing) | Name ≤256, email ≤320 (email-shaped). | Writes the **Safety Reports Contact** name + email on `ITS_Active_Jobs`. The weekly **safety** report email is addressed **TO** this contact. |
| **Safety CC** (up to 5) | No | Up to 5 email addresses, each email-shaped. | Becomes **CC 1–5** on `ITS_Active_Jobs`. Everyone here is CC'd on the weekly safety report. |
| **Progress Reports — contact name / email** | No (but required for progress routing) | Name ≤256, email ≤320 (email-shaped). | Writes the **Progress Reports Contact** name + email on `ITS_Active_Jobs_Progress`. The weekly **progress** report email is addressed **TO** this contact. |
| **Progress CC** (up to 5) | No | Up to 5 email addresses, each email-shaped. | Becomes **CC 1–5** on `ITS_Active_Jobs_Progress`. |
| **"Same as safety"** button | — | One click. Copies the Safety Reports contact + CCs into the Progress Reports block. | A shortcut for the common case where the same people get both reports. After copying you can still edit the progress block independently — it does not stay linked. |
| **Client name** (optional client) | No | 1–256 characters (with optional contact/phone/email). | Links a client record to the job in the portal. This is portal bookkeeping; it is separate from the report-routing contacts above. |
| **Progress %** | No | 0–100. | The job's progress bar in the portal. It is **not** mirrored to the Smartsheets and does **not** trigger a re-sync. |

**Lifecycle is not on the create form.** Every new job starts **Active**. You change the state
later from the job's **Manage** view (next section).

## Lifecycle: Active / Inactive / Archived

Open a job in the Job Tracker and use the **Lifecycle** selector (it replaces the old plain
"Close" button). It sets the **"Active"** column on **both** Active-Jobs sheets to the matching
value:

| You select | Sheet "Active" column | What it means operationally |
|---|---|---|
| **Active** | `Active` | The job is live. It appears on the safety and progress **form dropdowns** (field crews can submit against it), and the **weekly compile** runs for it. |
| **Inactive** | `Inactive` | The job **drops off the form dropdowns** (no new submissions) and the weekly compile skips it. The row stays in the sheet. Use this when work is paused or wrapped up but you may revisit it. |
| **Archived** | `Archived` | Same effect on dropdowns and compile as Inactive, but it is the long-term resting state — the job stays visible under the **Archived filter** in the portal/sheet. Use this for finished jobs you want out of the way. |

Anything other than **Active** is treated as "not active" downstream (a blank status is also
treated as not active — the system is deny-by-default). You only ever pick one of the three; the
portal handles the rest of the bookkeeping behind the scenes.

## Setting up a new job — assign crew, equipment, tasks, and expected materials

Creating a job is step one. Right after you click **Create**, the portal opens the new job's
**detail view** with a **"Finish setting up JOB-######"** banner so you can get it ready in one
sitting. (You can also do all of this any time later by opening any job from the Job Tracker — the
same controls live on every job's detail view.)

On the job detail view:

- **Assign crew** — under **Assigned crew**, pick a person from the dropdown and click **Add to
  crew**. That person is now *placed* on this job (their Personnel-page "Placed on" shows
  `JOB-######`). Click the **✕** next to a crew member to remove them. **A job's crew = the people
  currently placed on it.** Placing someone on a job does **not** lock their time — a person placed
  on Job A can still log a day against Job B.
- **Assign equipment** — under **Equipment on site**, pick a piece of equipment and click **Move
  here**. It now shows on this job (and drops off whatever job it was on before).
- **Add tasks (and assign them to a person)** — under **Manage job**, use **Add a task** for the
  job's deliverables / to-dos. The **Assign to** dropdown next to it lets you hand the task to one of
  the job's crew as you create it. On each task in the **Tasks** list, an assignee dropdown lets you
  **reassign** it to a different crew member or set it back to **— unassigned —** at any time. (The
  people offered are the crew currently placed on the job, so place your crew first.)
- **Log time for a person** — under **Time entries**, the **For** dropdown lets you record time
  against a specific crew member (not just "yourself"), and the **Task** dropdown ties it to a
  specific task or leaves it job-level.
- **Record expected materials** — under **Expected materials**, list what the job is waiting on:
  click **+ Add expected material**, pick a type **from the catalog** (the Materials Catalog
  vocabulary) or switch to **Custom (free text)** and describe it, add a quantity/unit and an
  expected date if you know them, then **Add**. You can add these at job creation or any time as
  the job develops; rows can be edited while still *Expected*, reordered with ▲/▼, and removed
  (removal keeps the history). Managers and field PMs on the job see this list read-only — the
  step where they **confirm each delivery arrived** (or flag a damaged/short delivery) lands in
  the daily report in an upcoming phase; each confirmed row will then show **Received**, when, and
  by whom.

**Who can do what (permissions):** assigning crew needs the *crew-assign* permission, moving
equipment needs the *equipment-field* permission, adding/assigning tasks needs the *job-manage*
permission, logging time needs the *time-log* permission, and editing the expected-materials list
needs the *materials-manage* permission (office/admin; everyone with *materials-receive* — all
roles — can view it on their own job). A **Manager** (crew lead) can assign crew and move
equipment but **not** add or assign tasks or create jobs — the office keeps job/task creation.
You only see the controls your account may use.

> **No progress %:** the old job "progress percentage" bar has been **removed** everywhere — it was a
> meaningless guess. Job status is tracked by **lifecycle** (Active / Inactive / Archived) and by the
> real tasks + time on the job, not by a made-up number.
>
> **Materials:** the per-job **expected materials** list is live on every job's detail (see the
> setup step above). The field-side receipt confirmation inside the daily report — and the
> material-incident report — arrive in the next materials phase (M2).

## The golden rule — don't hand-edit portal jobs in Smartsheet

Once the up-sync is live (see below), a job you created in the portal is **owned by ITS**, not by
you, inside the Active-Jobs Smartsheets:

- **Do NOT edit a portal-origin row directly in `ITS_Active_Jobs` or `ITS_Active_Jobs_Progress`.**
  The mirror daemon writes those rows from the portal's data. A change you type into a sheet cell
  is **not authoritative** — it will be overwritten the next time that job changes in the portal.
- **How to tell a portal-origin row:** it has a value in the **"Portal Job Key"** column (the
  assigned `JOB-######`, the same as its Job ID). Legacy rows have a **blank** Portal Job Key.
- **To change a portal job** — contacts, CCs, address, or status — make the change **in the
  portal**: the **"Edit routing/contacts"** form for the contacts, and the **Lifecycle** selector
  for Active / Inactive / Archived. The change flows up to both sheets on the next sync.

**Your legacy jobs stay yours.** Any job you created/maintained directly in `ITS_Active_Jobs`
before this change (a row with a **blank** Portal Job Key) remains **operator-editable in
Smartsheet** exactly as before. The mirror daemon never touches those rows — it only writes rows
keyed by Portal Job Key — and the existing portal sync continues to mirror them down to the portal
dropdown as it always has.

In short: **portal jobs → manage in the portal; legacy Smartsheet jobs → manage in Smartsheet.**
Don't cross the streams.

## What's NOT live yet (pending)

The piece that copies portal-created jobs **up into the Smartsheets** — the mirror daemon
(`field_ops/fieldops_sync`, plan "Slice 5") — is **not running yet**, and it ships with its sync
switch (`field_ops.fieldops_sync.sync_enabled` in ITS_Config) **OFF** until the operator (Seth)
formally cuts over.

**What that means for you right now:**

- A job you create in the portal **works fully in the portal** (field crews can log time, tasks,
  submissions against it).
- But until cutover it will **not appear** in `ITS_Active_Jobs` or `ITS_Active_Jobs_Progress`, and
  therefore not in the weekly safety/progress reports. The portal holds it as "pending" mirror.
- So during this interim, if a job must show up in the Active-Jobs sheets / weekly reports
  **before cutover**, continue using the existing Smartsheet job-entry path for it, and coordinate
  the switch-over with Seth.

This is expected, not a fault — see the runbook
[`docs/runbooks/fieldops_job_write.md`](../runbooks/fieldops_job_write.md) ("portal-origin jobs
stuck pending"). Once Seth turns the sync flag on, portal-created jobs flow into both sheets
automatically and this guide's golden rule takes full effect.

## Owner

`@solutionsmith`. This guide is part of the §6/A8 enablement-doc program (operator/PM-facing
manuals). The polished distributable PDF version lands before the 20-job cutover; this in-repo
version is the source of truth for its content.
