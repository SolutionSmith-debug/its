---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, checklist, daily-checklist, inspection-library, assigned-tasks, tier-2, tier-3]
---

# Runbook — Field-Ops checklists (daily "Progress Report" + assigned inspections) (Successor-Remediation, Op Stds §43)

The Assigned-Tasks feature runs **one checklist engine** (portal-owned Cloudflare Worker + D1,
migration `0026_checklist_engine.sql`, send-free) with two surfaces:

- **Daily "Progress Report" checklist** — MANAGER-ONLY. Generated Worker-on-read when a placed
  manager opens **My Tasks**; one instance per (job, placed-manager, Pacific day). Rolls up into
  the Daily Report form on completion (the manager reviews + files).
- **Assigned inspections (S6)** — an admin authors a **library** of inspection checklists
  ("Inspection checklists" home card, `cap.checklist.manage`) and **assigns** one to a manager or
  subcontractor. It appears in that person's **My Tasks** tab under "Assigned inspections".

Both are the same item engine: item types `manual_attest` (check + note), `count` (value ≥ N),
`form_linked` / `inspection` (deep-link to a form; **auto-closes** when a matching submission for
the item's (job, date) exists — never a manual check).

**No daemon, no send path.** Generation + loop-closure run in the Worker on each read; filing a
rolled-up Daily Report goes through the normal `/api/submit` path (Invariant 1: human-in-loop, the
Mac intake files it). Nothing here transmits externally.

---

## Symptom A — "My daily checklist is empty / doesn't appear" (a manager)

**What it means.** The daily section only renders for an account that is **all three of**:
(1) role `manager`, (2) linked to an **active** personnel row (personnel.username == the login), and
(3) that personnel is **placed on a job** (personnel.current_job set). Miss any one → no daily
section (by design; a submitter or an unplaced manager sees only their one-off tasks + any assigned
inspections).

**Low-class repair (Tier-2):**
1. Confirm the account's **role is manager** (Accounts page).
2. Confirm the person is **linked**: Personnel page → the roster row shows the account username.
3. Confirm they are **placed on a job** (current_job set) — place them via the Job Tracker / Personnel
   "current job" control.
4. Have them reload **My Tasks**. The instance is created on read.

**Escalate to Seth if:** all three hold and the section is still empty, OR the daily default checklist
itself is empty (admin authoring, below).

## Symptom B — "A form item won't auto-check" (daily OR assigned inspection)

**What it means.** A `form_linked` / `inspection` item closes only when a **submission exists for the
item's (job, form-family, date)**. It is NOT manually checkable (a manual complete returns
`auto_close_only`). Common causes: the form wasn't filed for the **same job + work-date** as the
checklist instance; or (assigned inspections) the inspection was assigned with **no job or no due
date**, so there is no (job, date) to match against.

**Low-class repair (Tier-2):**
1. Confirm a submission for that job + form + the instance's date actually exists (Form Request page).
2. If it does, have the person **reload My Tasks** — auto-close runs on read.
3. For an **assigned inspection** with a `form_linked` item: re-assign it **with a job AND a due date**
   (an inspection assigned with neither can never auto-close a form item — that's an authoring choice,
   not a bug). Delete the old assignment (leave it, it's harmless) and re-assign from the Inspection
   checklists page.

**Escalate to Seth if:** the submission provably exists for the right job+form+date and the item stays
open after a reload (a Worker loop-closure regression — code change, high-class).

## Symptom C — "I can't assign an inspection" (admin)

**What it means.** The **Assign** control (Inspection checklists page) validates: the checklist is a
real library template; the **person is an active roster person with a login** (only login-linked people
are offered — a person with no account could never see the assignment); the job (if chosen) exists; the
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

## Symptom D — the admin library/assign flow (reference, not a fault)

1. **Home → Inspection checklists** (visible to `cap.checklist.manage` = admin).
2. **Create** a checklist (title) → it appears in the Library list.
3. **Click a checklist** to open its item editor → add items (label + type; form_code for
   form_linked/inspection; target N for count) → Remove to delete an item; **Delete** to remove the whole
   checklist (already-assigned instances keep working — they snapshot their items).
4. **Assign an inspection checklist** (bottom of the page): pick the checklist + a login-linked person,
   optionally a job + due date → **Assign**. It lands in that person's My Tasks "Assigned inspections".

The **daily default** checklist + per-job overrides are edited on the **Job Tracker job detail**
(also `cap.checklist.manage`), not here.

---

## Boundary (Op Stds §44 both-rule)

The repairs above (checking role/link/placement, confirming a submission exists, re-assigning, reading
the admin flow) are **documented + low-capability-class → Tier-2**. Anything that needs a **code change**
(loop-closure not firing on a provably-matching submission; the assign/CRUD routes 500ing on valid input;
the daily default seed missing) is a **FIXED high-capability-class (code) event → escalate to Seth**. The
checklist engine is send-free and D1-only — there is **no External Send Gate leg here** (the Daily Report
rollup files through the normal human-approved submit path).
