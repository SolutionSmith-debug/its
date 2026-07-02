---
type: operations
date: 2026-07-01
status: active
related_prs: []
workstream: field_ops
tags: [enablement, a8, slice-t, field-pm, rbac, subcontractor, crew-create, time-scoping]
---

<!-- TODO(operator): register this doc in the §6a enablement-doc manifest once that artifact
exists (tracked OPEN in docs/tech_debt.md — "§6a enablement-doc DoD owed"). Same status as
docs/enablement/manager_tier.md. Do not fabricate a registration in the meantime. -->

# Enablement — The Subcontractor tier · Op Stds §6/A8

**Audience:** office admins who manage portal accounts + the field PMs themselves. **What changed:**
the **field-PM role is now shown as "Subcontractor"** everywhere in the portal (the Accounts role
dropdown, the account role badge, the Personnel account-role picker). This is a **name change only** —
existing field-PM accounts are unchanged, and everything they could do before still works. The tier
also gains one new self-service power: **Add crew**.

> **Under the hood (why it's just a rename):** the role's internal value is still `submitter`. That
> value is a deliberate safety default — if the system ever can't tell what role an account has, it
> falls back to the least-privileged tier. So we changed the *label*, not the *value* — no accounts
> need re-creating and no permissions change except the one addition below.

## What a Subcontractor can now do: Add crew

On the **My Tasks** page a subcontractor sees an **Add crew** box. It creates a **field-only** crew
member (name + optional trade) — someone who does NOT get a portal login — and **automatically places
them on the subcontractor's current job**. Use it when a sub brings extra hands to their site and you
want them on the crew list + time log without an office round-trip.

- The subcontractor **must be placed on a job first** (a manager/admin places them via the Job Tracker
  **Assign crew** control or the Personnel page). Until then, Add-crew shows *"You must be placed on a
  job."*
- The new crew member lands on the **subcontractor's own** job — a subcontractor can't put crew on some
  other job. (Moving crew between jobs stays a manager/admin action.)

## Time logging is scoped to their own crew

A subcontractor can log time **for themselves and for crew they added** — the time-log "For" picker
only offers those people. They can't log time for someone else's crew. Managers and admins are
unaffected — they still log time for anyone on the job.

## The tiers at a glance

| Can… | **Subcontractor** (field PM) | Manager (crew lead) | Admin (office) |
|---|:---:|:---:|:---:|
| Submit forms, log own/created-crew time, field actions | ✅ | ✅ | ✅ |
| See the Job Tracker (read) | ✅ | ✅ | ✅ |
| **Add field-only crew (auto-placed on their own job)** | ✅ | ✅ (fuller controls) | ✅ (fuller controls) |
| Log time for **anyone** on the job | — (only self + created crew) | ✅ | ✅ |
| See the **Personnel** tab / edit-link-retire others | — | ✅ | ✅ |
| Move crew between jobs, create/close jobs, mint logins | — | (assign only) | ✅ |

**Nothing you have to do:** existing field-PM accounts keep working; they'll just read "Subcontractor"
and gain the Add-crew box once they're placed on a job.
