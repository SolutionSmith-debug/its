---
type: operations
date: 2026-06-02
status: active
related_prs: []
workstream: safety_reports
tags: [runbook, successor-remediation, smartsheet, box, project-onboarding, tier-2]
---

# Runbook — Project not routed to a Box folder (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and edits Smartsheet rows + reads alert
emails, but does **not** read code or touch secrets. The §42 code-reader
rationale lives in `shared.project_routing` (the resolution + fallback logic)
and `safety_reports/intake.py::upload_attachments_to_box` (the consumer).

## Purpose

What to do when a project's **safety-report attachments aren't landing in Box**
because ITS can't resolve that project's Box folder. ITS resolves a project →
Box folder from the **ITS_Project_Routing** sheet (one row per project); if the
project has no Active row there, it falls back to a hardcoded developer table
(`BOX_PROJECT_FOLDERS`), and if that's empty too, the upload is skipped and
recorded as an error on the report row.

**Onboarding a project = adding one row to ITS_Project_Routing.** That is a
**low-capability-class** Tier-2 repair (re-seed a row — same class as adding a
trusted contact). It is **not** any of the four FIXED high-class categories
(Send Gate, secrets, doctrine, code), so the Successor-Operator can do it,
**provided the correct Box folder ID is known** (see escalate condition).

## Procedure

### Symptom

Two distinct signals, one hard and one soft:

- **HARD (attachments dropped).** On a safety-report row in the Daily Reports
  sheet, the attachment/upload field carries an error like
  `no Box folder for project 'Maplewood 3' (ITS_Project_Routing +
  BOX_PROJECT_FOLDERS fallback both empty)`. The report still files; only its
  **attachments** failed to upload.
- **SOFT (running on the fallback — onboarding gap).** In ITS_Errors / logs, a
  WARN like `project_routing: 'Maplewood 3' resolved from the hardcoded
  BOX_PROJECT_FOLDERS fallback, not ITS_Project_Routing — add it to the sheet.`
  Here uploads still **work** (the hardcoded table covered it), but the project
  is not yet in the operator-editable sheet. Fixing it is the same action and
  prevents the project from silently breaking when the fallback is eventually
  retired.

### What the Successor-Operator checks

1. **Open ITS_Project_Routing** (ITS — System / 01 — Config). Find the row whose
   **Project Name** *exactly* matches the project in the error (match is
   case- and spacing-sensitive — `Maplewood 3` ≠ `maplewood 3` ≠ `Maplewood  3`).
2. **No row?** The project was never onboarded → you will **add** one (below).
3. **Row exists but `Active` is unchecked?** It was retired (perhaps by mistake).
   Re-check `Active` if the project is live again.
4. **Row exists, Active, but `Box Folder ID` looks wrong/empty?** That's the
   field to correct — it must be the project's Box folder ID under *ITS DATA*
   (an opaque numeric string; do not reformat or add spaces).

### The Claude prompt or UI action

You can do this directly in the Smartsheet UI, or have Claude do it. To add a
project (you must already know the project's Box folder ID — see escalate):

> "Claude, add a row to ITS_Project_Routing: Project Name = `Maplewood 3`,
> Box Folder ID = `<the Box folder id>`, Active = checked, Notes = `onboarded
> <today's date> by <me>`. Then confirm it reads back and that
> `shared.project_routing.get_folder_id('Maplewood 3')` resolves to that ID."

To re-activate or fix an existing row, point Claude at that row instead. ITS
picks up the change on the **next intake cycle** (≤ a minute; the routing read
is cached 60 s). To repair attachments that already failed, the operator
re-sends the original report email so intake re-processes it.

### Escalate-to-Seth condition

Escalate (Tier 3) when **any** of these holds:

- **You don't know the project's Box folder ID, or the project's Box folder
  doesn't exist yet.** Creating the project's Box folder structure is a
  developer provisioning step (the `reclone_projects_from_1111b.py` migration),
  **not** a row edit. Adding a routing row that points at a nonexistent folder
  won't fix uploads.
- **Attachments still fail after a correct, Active row is in place** (verified
  reading back). That points past the sheet — a resolution/upload bug, which is
  a **code change** (FIXED high-class category).
- The fix would require editing `BOX_PROJECT_FOLDERS` or any other code/doctrine
  — always Seth.

Both-rule (Op Stds §44): adding/fixing a routing **row** is low-class Tier-2;
provisioning the Box **folder** or changing resolution **code** is high-class
Tier-3.

## Owner

`@solutionsmith`. The sheet schema (Project Name primary, Box Folder ID, Active
CHECKBOX, Notes) and the resolution/fallback logic are code (Tier 3) — see
`scripts/migrations/build_its_project_routing_sheet.py` and
`shared/project_routing.py`.
