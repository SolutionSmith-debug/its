---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, checklist, daily-report, sop-daily-form, inspection-library, assigned-tasks, tier-2, tier-3]
---

# Runbook — Field-Ops daily report (SOP form) + assigned inspections (Successor-Remediation, Op Stds §43)

Two related surfaces live on the portal's **My Tasks** page:

- **Daily report (SOP daily form — D1/D2, 2026-07)** — MANAGER-ONLY. The **Daily report tab IS the
  form**: a date selector + the current `daily-report` definition (`daily-report-v3` since D3,
  2026-07-02 — the catalog's current version) rendered inline (the full
  Site-Supervisor SOP text with the data fields under each section), pre-filled with the manager's
  placed job, crew, and equipment. "Create JHA / Visitor Sign-In / Incident Report" buttons
  deep-link into those forms and show a live **"Filed ✓ \<time> by \<name>"** indicator once one is
  filed for that job + date (`GET /api/fieldops/daily-form/status`). Filing goes through the normal
  send-free `/api/submit` path → Mac intake → Box/Smartsheet weekly packet — UNCHANGED.
  **The daily content is edited in the FORM DEFINITION** (Forms → the form builder → Daily Field
  Report), not in any checklist editor. The old checkbox daily checklist, the admin "Default daily
  checklist" editor, and the Job-Tracker per-job checklist editor are **retired**.
- **Assigned inspections (S6)** — an admin authors a **library** of inspection checklists
  ("Checklists" home card, `cap.checklist.manage`) and **assigns** one to a manager or
  subcontractor. It appears in that person's **My Tasks** tab under "Assigned inspections". This is
  the checklist ENGINE's remaining consumer (portal Worker + D1, migration `0026`, send-free):
  item types `manual_attest` (check + note), `count` (value ≥ N), `form_linked` / `inspection`
  (deep-link to a form; **auto-closes** when a matching submission for the item's (job, date)
  exists — never a manual check).

**No daemon, no send path.** Everything here is Worker + D1 reads/writes; filing a daily report or
a linked form goes through the normal `/api/submit` path (Invariant 1: human-in-loop, the Mac
intake files it). Nothing here transmits externally.

---

## Symptom A — "My Daily report tab says I'm not placed / doesn't show the form" (a manager)

**What it means.** The Daily report form renders only for an account that is **all three of**:
(1) role `manager`, (2) linked to an **active** personnel row (personnel.username == the login), and
(3) that personnel is **placed on a job** (personnel.current_job set). Miss any one → the tab shows
an explanatory message instead of the form (by design; a subcontractor or an unplaced manager sees
only their one-off tasks + any assigned inspections). The placement is read from the Job Tracker
viewer data, so the manager also needs `cap.jobtracker.read` (managers hold it by default).

**Low-class repair (Tier-2):**
1. Confirm the account's **role is manager** (Accounts page).
2. Confirm the person is **linked**: Personnel page → the roster row shows the account username.
3. Confirm they are **placed on a job** (current_job set) — place them via the Job Tracker / Personnel
   "current job" control.
4. Have them reload **My Tasks** (or tap Refresh). The tab resolves placement on load.

**Escalate to Seth if:** all three hold and the tab still shows an explanatory message or a
persistent load error after Retry.

## Symptom B — "The 'Filed ✓' indicator or the filed banner doesn't show" (Daily report tab)

**What it means.** The indicators come from `GET /api/fieldops/daily-form/status`, which reports the
**latest submission per form family for the SAME job + SAME date** the tab shows. Common causes: the
linked form was filed for a **different job or work-date**; or the tab simply hasn't refreshed since
the filing.

**Low-class repair (Tier-2):**
1. Confirm the submission exists for that job + form + date (Form Request page).
2. Tap **Refresh** on My Tasks (the indicators refetch), or re-open the tab.
3. If the "by \<name>" part is missing but the time shows: the filing account has no roster link —
   that's cosmetic attribution, not a fault (link the account on the Personnel page if wanted).

**Escalate to Seth if:** a submission provably exists for the right job+form+date and the indicator
stays absent after a refresh (a status-endpoint family-match regression — code change, high-class).

## Symptom C — "A form item won't auto-check" (assigned inspections)

**What it means.** An inspection's `form_linked` / `inspection` item closes only when a **submission
exists for the item's (job, form-family, on-or-before the due date)**. It is NOT manually checkable
(a manual complete returns `auto_close_only`). Common causes: the form wasn't filed for the **same
job** as the inspection; or the inspection was assigned with **no job or no due date**, so there is
no (job, date) to match against.

**Low-class repair (Tier-2):**
1. Confirm a submission for that job + form actually exists (Form Request page).
2. If it does, have the person **reload My Tasks** — auto-close runs on read.
3. For an inspection with a `form_linked` item: re-assign it **with a job AND a due date**
   (an inspection assigned with neither can never auto-close a form item — that's an authoring choice,
   not a bug). Delete the old assignment (leave it, it's harmless) and re-assign from the Checklists page.

**Escalate to Seth if:** the submission provably exists for the right job+form+date and the item stays
open after a reload (a Worker loop-closure regression — code change, high-class).

## Symptom D — "I can't assign an inspection" (admin)

**What it means.** The **Assign** control (Checklists page) validates: the checklist is a
real library template; the person is an **active roster person**; the job (if chosen) exists; the
due date (if chosen) is `YYYY-MM-DD`. It also **dedupes an exact repeat**: assigning the SAME checklist
to the SAME person for the SAME job + due date twice returns "already assigned for this job + date".

**Low-class repair (Tier-2):**
- **"already assigned for this job + date"** — the assignment already exists; it's in the person's My
  Tasks. Change the due date or leave the existing one. (Assignments with **no** job/date always create a
  fresh one — repeats are allowed there by design.)
- **"Pick a checklist / Pick a person"** — a required select is empty; choose both.
- **Person not in the dropdown** — they have no **login-linked personnel** row. Link their account to a
  roster person (Personnel page) first.

**Escalate to Seth if:** a valid assignment (real template, active login-linked person, valid job/date)
returns a 500 or nothing happens (a Worker regression, high-class).

## Symptom E — "The daily report's questions/text are wrong or need to change" (reference, not a fault)

The daily content lives in the **current `daily-report` form definition** (`daily-report-v3` since
D3, 2026-07-02 — the 50-photo minimum removed, a "Site photos" upload added), edited exactly like any other
form: Home → **Forms** (the form builder, `cap.admin.formbuilder`) → Daily Field Report → edit →
publish. The publish pipeline (Worker validation → Mac daemon actuator) applies it; already-filed
submissions keep the version they were filed under (definitions are append-only). There is **no
checklist template to edit** — the old "Default daily checklist" editor (Checklists page) and the
per-job checklist editor (Job Tracker detail) were retired with D2.

---

## Boundary (Op Stds §44 both-rule)

The repairs above (checking role/link/placement, confirming a submission exists, refreshing,
re-assigning, editing the form definition through the normal form-builder flow) are **documented +
low-capability-class → Tier-2**. Anything that needs a **code change** (the status endpoint or
loop-closure not matching a provably-correct submission; the assign/CRUD routes 500ing on valid
input) is a **FIXED high-capability-class (code) event → escalate to Seth**. Everything here is
send-free and D1-only — there is **no External Send Gate leg** (the daily report files through the
normal human-approved submit path).
