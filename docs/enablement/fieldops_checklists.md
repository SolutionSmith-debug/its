---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [enablement, a8, checklist, daily-checklist, inspection-library, assigned-tasks, office-pm, manager]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest once that artifact
exists (tracked OPEN in docs/tech_debt.md — "§6a enablement-doc DoD owed"). Same status as
docs/enablement/manager_tier.md. Do not fabricate a registration in the meantime. -->

# Enablement — Field-Ops checklists: daily Progress Report + assigned inspections · Op Stds §6/A8

**Audience:** office admins + managers. **What this is:** the portal's Assigned-Tasks tab ("My Tasks")
now carries structured **checklists**, on top of the one-off tasks. There are two kinds.

## 1. The daily "Progress Report" checklist (managers)

A **manager placed on a job** sees a **Today's checklist** section in My Tasks — a short daily
SOP checklist for that job/day. Items can be:

- a **form to file** (e.g. the Daily Field Report) — a button deep-links to the form pre-filled with the
  job + date; it **auto-checks** once filed (no double-entry);
- a **manual check** (with an optional note);
- a **count** (e.g. "≥ 3 anchor points") — type the number and Record.

When every item is done, a **"Review & file Daily Report"** button assembles a draft Daily Report from
the day's data (crew, equipment, which forms were filed) for the manager to review, edit, and file the
usual way. **Nothing sends automatically** — the manager confirms and files, exactly like any other form.

The daily checklist's **default items** (and per-job tweaks) are edited by an admin on the **Job Tracker
job detail** page.

## 2. Assigned inspection checklists (managers + subcontractors)

Admins can build a **library of reusable inspection checklists** and hand them to a specific person.

**Admin — author the library:** Home → **Inspection checklists**. Create a checklist (give it a title),
click it to add items (the same four item types as above), and it's saved for re-use.

**Admin — assign one:** on the same page, use **"Assign an inspection checklist"** — pick a checklist +
a person (only people with a portal login are listed), optionally a **job** and a **due date**, then
**Assign**. It shows up in that person's My Tasks under **"Assigned inspections"**.

- Assign the **same** checklist to as many people as you like.
- Assigning it again to the **same person for the same job + due date** is prevented (it's already there);
  use a different due date or leave the existing one. Assigning with **no** job/date always creates a
  fresh copy.
- If an inspection has a **form-to-file item**, assign it **with a job and a due date** so the item can
  auto-check when the form is filed.

**The assignee (manager OR subcontractor):** opens My Tasks → **Assigned inspections** → works each item
with the same controls as the daily checklist (check, count, or file-the-form deep-link). It marks
complete once every item is done.

## Who sees what

- **Subcontractor (field PM):** their one-off tasks + any inspections assigned to them.
- **Manager (crew lead):** the above **plus** the daily Progress Report checklist for the job they're
  placed on.
- **Admin (office):** authors the daily default + the inspection library, and assigns inspections.

## Common questions

- **"A manager's daily checklist isn't showing."** They must be role **manager**, **linked** to a roster
  person, and **placed on a job**. See the runbook (`docs/runbooks/fieldops_checklists.md`, Symptom A).
- **"A form item won't check off."** Someone must file that form for the same job + date; it auto-checks
  on the next open. It can't be checked by hand (that prevents faking loop-closure).
- **"The person isn't in the assign dropdown."** They have no portal login linked to their roster record —
  link it on the Personnel page first.
