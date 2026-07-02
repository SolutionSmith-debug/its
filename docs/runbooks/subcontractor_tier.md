---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, field-ops, rbac, subcontractor, crew-create, time-scoping, tier-2, slice-t]
---

# Runbook — Subcontractor tier (scoped crew-create + time scoping) · Op Stds §43

A §43 successor-remediation entry for the **Successor-Operator** (a trained operator who runs Claude
Code + reads Smartsheet rows and alert emails, but does **not** read code or touch secrets). The §42
code-reader rationale lives in `safety_portal/migrations/0027_subcontractor_crew_create.sql`,
`safety_portal/worker/fieldops_crew_write.ts`, `safety_portal/worker/fieldops_time_write.ts`, and
`safety_portal/worker/auth.ts`.

## Purpose

The field-PM tier is now shown to users as **"Subcontractor"**. This is a **display-label rename
only** — under the hood the role **KEY is still `submitter`** (a deliberate, security-load-bearing
default: an unrecognized role always falls back to `submitter`, never to a privileged tier). A
subcontractor keeps everything a submitter could always do and gains ONE new, deliberately narrow
power: **Add crew** — creating a *field-only* (non-login) crew member who is automatically placed on
the subcontractor's own current job. Time logging for those created crew (and for themselves) is
allowed; logging time for anyone else is refused.

## Who assigns the role

Only an **admin** sets a user's role — the portal **Accounts** page dropdown (now labeled
"Subcontractor / Manager / Admin") or the CLI: `python -m safety_reports.portal_admin set-role
<username> submitter`. **Low-class** Tier-2 (no code, no secrets).

## Symptom → check → repair

### A. "A subcontractor can't add crew" / "Add crew is missing"

1. **Is the user placed on a job?** The Add-crew control refuses (message: *"You must be placed on a
   job before you can add crew"* / 422 `not_placed`) when the subcontractor isn't currently placed on
   a job. **Repair (low-class, Tier-2):** place them on a job — a manager/admin opens the job in the
   Job Tracker → **Assign crew**, or the Personnel page **Assign** control. Once placed, Add-crew works
   and the new crew lands on that job. Nothing to escalate.
2. **Is the control missing entirely?** It's gated on `cap.crew.create`. A user set to `submitter`
   should have it. If NO subcontractor has it (control missing for everyone set to `submitter`), the
   grant is **missing from the live database** — migration `0027` was never applied before the Worker
   deployed (the deploy-order lockout class). **NOT a Tier-2 repair — escalate to Seth** (apply
   migration + redeploy = code/deploy, high-class).

### B. "A subcontractor added crew on the wrong job"

The new crew member is **always** placed on the subcontractor's OWN current job — the subcontractor
cannot choose a different job (by design; that's a manager/admin power, `cap.crew.assign`). If the job
is wrong, the subcontractor was placed on the wrong job. **Repair (low-class):** a manager/admin
re-places the subcontractor (and/or the crew member) via the Job Tracker Assign-crew control. Nothing
to escalate.

### C. "A subcontractor gets 403 when logging time for a crew member" (`forbidden_personnel`)

This is the time-scoping working as intended: a subcontractor may log time only for **themselves** or a
crew member **they created**. Logging time for a stranger (someone a manager/admin or another
subcontractor created) is refused. **Repair (low-class):** the subcontractor logs time only for their
own crew; time for other people is the office's / a manager's job. Nothing to escalate. (Their time-log
"For" picker only offers self + created crew, so this 403 mostly appears if someone crafts a request by
hand — expected, not a fault.)

### D. "A subcontractor can do too much" → escalate to Seth immediately

A subcontractor must **NOT** be able to: mint a **login account** (Add-crew is field-only — any
account/password/role payload is refused, 400 `login_not_allowed`); **edit / link / unlink / retire**
someone else's personnel (those stay `cap.personnel.manage`, manager/admin-only → 403); **place crew on
any job but their own**; log time for people they didn't create; or reach the Personnel tab / admin
dashboard. If you observe any of these from a subcontractor, treat it as a **security regression →
escalate to Seth immediately** (do not self-repair; it implies the grant matrix or a server guard is
wrong — code, high-class).

## Escalate-to-Seth boundary (observable terms)

Escalate (do **not** self-repair) when: migration `0027` needs applying / the Worker needs redeploying
(Add-crew missing for every subcontractor, item A.2); a subcontractor can perform a manager/admin-only
action (item D); or any capability grant looks wrong. Everything else here (place a user on a job, set a
user's role, explain the self+created time-scoping) is a **low-capability-class Tier-2** repair.
