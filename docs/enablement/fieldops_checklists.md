---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [enablement, a8, daily-report, sop-daily-form, inspection-library, assigned-tasks, office-pm, manager]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest once that artifact
exists (tracked OPEN in docs/tech_debt.md — "§6a enablement-doc DoD owed"). Same status as
docs/enablement/manager_tier.md. Do not fabricate a registration in the meantime. -->

# Enablement — Field-Ops daily report (SOP form) + assigned inspections · Op Stds §6/A8

**Audience:** office admins + managers. **What this is:** the portal's "My Tasks" page carries two
kinds of structured daily work on top of the one-off tasks: the manager's **Daily report** (the SOP
form) and **assigned inspection checklists**.

## 1. The Daily report (managers) — the SOP form, filled in place

A **manager placed on a job** opens My Tasks → the **Daily report** tab and gets the whole daily
flow on one screen:

- a **date selector** at the top (defaults to today; pick a past date to see what was filed);
- the **full Site-Supervisor SOP**, rendered as a form — every section's guidance text with its
  fill-in fields directly underneath (weather, manpower, PPE confirmation, QC spot checks, photos
  count, end-of-day, and so on);
- **"Create …" buttons** where the SOP calls for another form (JHA, Visitor Sign-In, Incident
  Report) — tapping one opens that form pre-filled with the job + date, and once it's filed the
  button shows **"Filed ✓ \<time> by \<name>"** so the manager can see at a glance what's done;
- the **crew and equipment tables arrive pre-filled** from the Job Tracker (best-effort — if that
  lookup fails the tables just start blank);
- **Submit daily report** files it exactly like any other form (the office confirms it once filed;
  it lands in the weekly packet as before). Filing again the same day amends or adds — the tab
  shows a **"Daily report filed ✓"** banner once one exists for the selected date.

**Editing the daily content** (changing the SOP text, adding/removing a question): it lives in the
**Daily Field Report form definition** — Home → **Forms** (the form builder) → Daily Field Report →
edit → publish. There is no separate daily-checklist editor anymore; what the form says IS what the
manager sees. (The old checkbox daily checklist, the "Default daily checklist" editor, and the
per-job checklist editor on the Job Tracker were retired when this shipped.)

Note: the Daily Report no longer appears in the **Submit a form** picker — it's filed from the
Daily report tab. The office still retrieves filed dailies from **Form Request** as always.

## 2. Assigned inspection checklists (managers + subcontractors)

Admins can build a **library of reusable inspection checklists** and hand them to a specific person.

**Admin — author the library:** Home → **Checklists**. Create a checklist (give it a title),
click it to add items (manual check, count, or form-to-file), and it's saved for re-use.

**Admin — assign one:** on the same page, use **"Assign an inspection checklist"** — pick a checklist +
a person, optionally a **job** and a **due date**, then **Assign**. It shows up in that person's My
Tasks under **"Assigned inspections"**.

- Assign the **same** checklist to as many people as you like.
- Assigning it again to the **same person for the same job + due date** is prevented (it's already there);
  use a different due date or leave the existing one. Assigning with **no** job/date always creates a
  fresh copy.
- If an inspection has a **form-to-file item**, assign it **with a job and a due date** so the item can
  auto-check when the form is filed.

**The assignee (manager OR subcontractor):** opens My Tasks → **Assigned inspections** → works each item
(check, count, or file-the-form deep-link). It marks complete once every item is done.

## Who sees what

- **Subcontractor (field PM):** their one-off tasks + any inspections assigned to them.
- **Manager (crew lead):** the above **plus** the Daily report tab for the job they're placed on.
- **Admin (office):** edits the Daily Field Report form definition (Forms), authors the inspection
  library, and assigns inspections (Checklists).

## Common questions

- **"A manager's Daily report tab says they're not placed."** They must be role **manager**, **linked**
  to a roster person, and **placed on a job**. See the runbook
  (`docs/runbooks/fieldops_checklists.md`, Symptom A).
- **"We filed a JHA but the button still doesn't say Filed ✓."** It must be for the same job + date the
  tab shows; tap Refresh. See the runbook, Symptom B.
- **"How do I change what the daily report asks?"** Edit the Daily Field Report form definition in the
  form builder (Forms) and publish — that's the single source of the daily content now.
- **"The person isn't in the assign dropdown."** They have no roster record — add/link them on the
  Personnel page first.
