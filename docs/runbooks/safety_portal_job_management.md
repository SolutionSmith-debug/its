---
type: operations
date: 2026-06-05
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, smartsheet, safety-portal, tier-2, phase-3]
---

# Runbook — Safety Portal job management (add / retire jobs) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who edits Smartsheet rows and reads alert emails, but does **not**
read code or touch secrets. The §42 code-reader rationale lives in
`shared/active_jobs.py` (the Job-ID lookup) and
`scripts/migrations/extend_its_active_jobs_phase3.py` (the schema). Companion:
[safety_portal_config_sheets.md](safety_portal_config_sheets.md).

## Purpose

Jobs are added and retired entirely by editing **ITS_Active_Jobs** (ITS —
Operations / Safety Portal). Nothing else needs touching — the portal dropdown
syncs from this sheet (the sync ships in a later phase), and `intake.py` resolves
each submission to its job via the **Job ID**.

**Key columns (Phase 3):**

| Column | Meaning |
|---|---|
| **Job ID** | Auto-Number (e.g. `JOB-0001`) — the permanent key. **Smartsheet fills it automatically**; never type or change it. |
| Job Slug | Human-readable name (e.g. `bradley-1`). Was the old key; kept for readability. |
| Project Name | Display name on the portal dropdown. |
| Address | Full street address (auto-fills the form Work Location). |
| Stakeholder Name / Email / Phone | The client contact named in the weekly email body. |
| **Safety Reports Contact Email** | The **TO** recipient of the weekly safety email. **Required for Active jobs.** |
| **Active** | `Active` / `Inactive` / `Archived`. Only **Active** jobs appear in the portal. |

## Procedure

### Task A — Add a new job

1. Open **ITS_Active_Jobs** → use the **New Job** form (or add a row directly).
2. Fill: Project Name, Address, Stakeholder Name/Email/Phone, **Safety Reports
   Contact Email** (required), and set **Active = Active**.
3. **Do not touch Job ID** — Smartsheet assigns the next `JOB-####` automatically.
4. The job appears in the portal dropdown on the next sync (cron/manual).

> A job with **Active = Active but no Safety Reports Contact Email** will be
> flagged (the weekly send refuses an empty recipient). Fill the contact before
> forms accumulate.

### Task B — Retire a job

- Set **Active = Inactive** (temporarily off the dropdown) or **Archived**
  (permanently; the row stays as the historical record). The job leaves the portal
  dropdown on the next sync. **Never delete the row** — it is the history.

### Task C — One-time setup: the AUTO_NUMBER "Job ID" column

**When:** once, if the **Job ID** column is missing or is not an Auto-Number
column (it cannot be created by the migration — the Smartsheet API does not allow
creating Auto-Number columns).

1. In the Smartsheet UI, open **ITS_Active_Jobs** → add a column **right after
   Project Name**.
2. Name it **`Job ID`**, type **System Columns → Auto-Number**.
3. Format: prefix `JOB-`, 4-digit fill, starting number `1` (→ `JOB-0001`).
4. Existing rows backfill automatically (`JOB-0001`…). Done — the portal and
   `intake.py` now have a permanent key.

## Escalate to Seth (Tier 3) when

- A submission keeps landing in the **Review Queue** with reason
  `job_not_found` / `job_inactive` / `no_job_id` even though the job is Active with
  a Job ID — this is a code/sync issue (high-class), not a sheet edit.
- The **Job ID** auto-number values look duplicated or wrong, or a row lost its
  Job ID.
- Anything involving the portal deploy, secrets, the email send path, or code.
