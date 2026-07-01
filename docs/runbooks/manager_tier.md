---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, field-ops, rbac, manager, crew-assign, tier-2, p2.6]
---

# Runbook — Manager tier + crew→job assignment (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (a trained operator who runs
Claude Code + reads Smartsheet rows and alert emails, but does **not** read code or touch
secrets). The §42 code-reader rationale lives in `safety_portal/migrations/0023_manager_role.sql`,
`safety_portal/worker/fieldops_crew_assign.ts`, and `safety_portal/worker/auth.ts`.

## Purpose

P2.6 adds a **third portal role, `manager`** (crew lead), between `submitter` (field PM) and
`admin` (office). A manager runs crews: creates **non-login** roster crew, edits personnel, logs
crew time, reads the Job Tracker, and **assigns / moves crew to a job** (a "who is where"
placement). A manager **cannot** create jobs or tasks, mint login accounts, or reach the admin
surface — those stay `admin`-only. The role is a pure DB grant (migration `0023`); the capability
model (migration `0013`) resolves a user's role to its capability set **fail-closed** on every
request.

## Who assigns a role

Only an **admin** sets a user's role — either in the portal **Accounts** page (the role dropdown
on each account) or with the operator CLI:

```
python -m safety_reports.portal_admin set-role <username> manager
```

(`add-user <username> --role manager` creates a brand-new manager login.) These are **low-class**
Tier-2 operations — no code, no secrets.

## Symptom → check → repair

### A. "A crew lead can't do manager things" (can't see Personnel, can't assign crew)

1. **Confirm the user's role.** `portal_admin list-users` (or the Accounts page) — is the user
   actually `manager`? If they're still `submitter`, set the role (above). **Low-class, Tier-2.**
2. **Confirm the role change took effect.** Role + capabilities are read **fresh per request**, so
   the change is effective on their next page load — have them reload / re-log-in. No cookie reset
   needed.
3. **If EVERY manager (and the role itself) is broken** — a user set to `manager` gets *no*
   capabilities (empty tabs, or 401) — the `manager` role / its grants are **missing from the live
   database**, i.e. **migration `0023` was never applied to the live D1** before the Worker
   deployed. This is the **deploy-order lockout class** and is **NOT a Tier-2 repair — escalate to
   Seth** (applying a migration + redeploying the Worker is code/deploy = high-capability-class).

### B. "A manager can do too much" — what's now expected vs. still a red flag

**Expected (NOT a fault): a manager creating / assigning / completing a TASK.** As of the S1
Assigned-Tasks build (`cap.tasks.assign`, migration `0025`) the task create/reassign routes accept
`cap.jobtracker.manage` **OR** `cap.tasks.assign`, so a manager can now create tasks, assign them,
and mark them complete — but only for **subcontractor-role (field-PM / `submitter`) accounts**.
This is the role working as designed; nothing to repair, nothing to escalate.

**Still should be impossible → escalate to Seth immediately.** A manager must NOT be able to:
create or close a **JOB** (job create / close / lifecycle / routing still gate on
`cap.jobtracker.manage`, admin-only), mint a login account, reach the admin dashboard, or **retire
personnel** (retire is admin-only after the S1 "manager-no-retire" fold-in). If you observe any of
those from a manager, treat it as a **security regression → escalate to Seth immediately** (do not
attempt a repair; it implies the grant matrix or an admin hard-check is wrong — code, high-class).

**Symptom (NOT a fault): a manager gets 403 `forbidden_target` or 403 `forbidden_task` on a task.**
Both are the subcontractor-target guard working as intended — a manager may only touch tasks owned by
subcontractors and may only assign to subcontractors:

- **403 `forbidden_target`** when a manager tries to **assign a task to a non-subcontractor account**
  (an admin or another manager). **Repair: pick a subcontractor (`submitter`) target.** Low-class,
  Tier-2; nothing to escalate.
- **403 `forbidden_task`** when a manager tries to **touch a task currently owned by an
  admin/manager**. **Repair: a manager only handles tasks owned by subcontractors — leave others'
  tasks to the office.** Low-class, Tier-2; nothing to escalate.

### C. "Assign crew to a job" fails

- **"That job is no longer active" / 422 `unknown_job`** — the job the manager picked is closed
  (`active=0`). Expected: only **active** jobs are assignable. Re-open the job (Job Tracker
  lifecycle → active) or pick an active one. **Low-class** (operator/manager action, no code).
- **The dropdown is empty** — there are no active jobs, or the active-jobs list (`/api/jobs`) isn't
  loading. Confirm active jobs exist. If jobs exist but the dropdown is empty for everyone, that's a
  portal fault → escalate.
- **Placement seems "wrong" vs. logged time** — this is **by design**: a crew member's *placement*
  (`current_job`) and their *time entries* are **orthogonal**. Someone placed on Job A can log a day
  against Job B without being reassigned. Nothing to repair.

### D. "A job's crew list is empty or looks wrong after the unified-create-flow update"

After the unified job-create flow (migration `0024`), a job's **crew = the people currently PLACED
on it** (`personnel.current_job`), **not** whoever has a task assigned. An existing job that had
task-assignment "crew" but nobody *placed* on it now shows an **empty crew list** until someone is
placed.

- **Repair (low-class, Tier-2):** open the job in the Job Tracker → **Assigned crew** → pick the
  person → **Add to crew** (or use the Personnel page **Assign** control). They appear in the crew
  list immediately; the **✕** next to a crew member removes (unassigns) them.
- **No data was lost** — those people still show in the job's **Tasks** list with their name; only
  the *crew* view changed to mean "placed on this job." Nothing to escalate — this is the intended
  convergence. (The detail-view **Assign crew** / **Assign equipment** controls are gated on
  `cap.crew.assign` / `cap.equipment.field`, so a manager sees them; add-task stays `admin`/office.)

### E. "Can't assign a task to a person" / "can't log time for a crew member"

A manager can assign / complete tasks for **subcontractor** accounts (item B); assigning a task to
**any** account and logging a crew member's time stay **office actions**
(`cap.jobtracker.manage` / `cap.time.log`). If the person who should be able to do it can't:

- **Add-task / reassign 422 `unknown_personnel`** — the chosen person isn't a valid roster member.
  Pick someone from the dropdown (the options are the job's placed crew — **place the crew first**).
  **Low-class.**
- **The assignee / "For" dropdown is empty** — the job has no crew placed yet. Place crew via
  **Assigned crew → Add to crew**, then the task-assignee and time-"For" dropdowns populate. **Low-class.**
- **"Where did the progress % go?"** — the job **progress-percentage bar was removed on purpose** (it
  reflected nothing). Track a job by its **lifecycle** (Active/Inactive/Archived) + its real tasks and
  logged time. Not a fault; nothing to repair.

## Escalate-to-Seth boundary (observable terms)

Escalate (do **not** self-repair) when: migration `0023` needs applying / the Worker needs
redeploying (every-manager-broken, item A.3); a manager can perform an admin-only action (item B);
or any capability grant looks wrong. Everything else here (set a user's role, re-open a job, explain
the placement/time orthogonality) is a **low-capability-class Tier-2** repair.
