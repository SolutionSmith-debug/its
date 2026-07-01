---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [enablement, a8, p2.6, office-pm, rbac, manager, crew-assign]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest once that artifact
exists (tracked OPEN in docs/tech_debt.md — "§6a enablement-doc DoD owed"). Same status as
docs/enablement/portal_job_creation.md. Do not fabricate a registration in the meantime. -->

# Enablement — The Manager role (crew leads) · Op Stds §6/A8

**Audience:** office admins who manage portal accounts. **What changed:** the portal now has a
**third role, `manager`**, in addition to `submitter` (field PM) and `admin` (office). Use it for a
**crew lead** — someone who runs crews day-to-day but is not office/admin.

## The three roles at a glance

| Can… | Submitter (field PM) | **Manager (crew lead)** | Admin (office) |
|---|:---:|:---:|:---:|
| Submit safety/progress forms, log time, field actions | ✅ | ✅ | ✅ |
| See the Job Tracker (read) | ✅ | ✅ | ✅ |
| See the **Personnel** tab | — | ✅ | ✅ |
| Add/edit **non-login** crew, retire, link | — | ✅ | ✅ |
| **Assign / move crew to a job** ("who is where") | — | ✅ | ✅ |
| **Create / close jobs, create tasks** | — | — | ✅ |
| **Create login accounts, set roles** | — | — | ✅ |
| Admin dashboard, submit-as, form builder | — | — | ✅ |

One line: **office creates jobs (admin) · manager runs crews (manager) · field PM submits
(submitter).**

## How to make someone a manager

1. **Portal → Accounts.** On the person's account, pick **Manager** in the role dropdown. The change
   takes effect on their next page load.
2. **Or the CLI** (operator): `python -m safety_reports.portal_admin set-role <username> manager`
   (existing account) / `add-user <username> --role manager` (new login).

Only an **admin** can set roles or mint logins. A manager can create *non-login* crew (roster
entries for time-tracking) but **cannot** create login accounts — that stays with the office.

## Crew → job assignment ("who is where")

On the **Personnel** tab a manager (or admin) sees each person's **Placed on** job and an **Assign**
button. Assign picks an **active** job from the dropdown; **Unassign** clears the placement. This is
the crew member's *standing placement* — where they're currently working.

**Important — placement and logged time are independent.** Assigning someone to Job A does **not**
force their time to Job A: they can still log a day against any active job (say Job B) without being
reassigned. Placement answers "who is where right now"; time entries record "what work happened
where." Neither constrains the other.

## What a manager deliberately can't do

Create or close jobs, create tasks, mint or disable login accounts, change roles, open the admin
dashboard, or submit-as another user. If a crew lead needs any of those, an admin does it. This
keeps job creation and credential/role management as an office (admin) power.
