---
type: runbook
workstream: field_ops
capability: material_catalog (P3 Materials M1)
audience: Successor-Operator
status: skeleton
---

# Runbook — Materials Catalog admin (`material_catalog`)

> §43 successor-remediation skeleton. The polished §6a operator/user PDF guide + manifest
> registration land with P2 (the §6a enablement-doc program); this is the in-repo skeleton.

## What it is

The **Materials Catalog** is the admin-editable list of material **types** (the datasheet-backed
vocabulary the per-job Material List draws from). It is a portal surface (D1 only — no Smartsheet,
no send): the **Materials Catalog** card on the home, gated by `cap.materials.manage` (admin-only).
Reads are gated `cap.materials.receive` (field PMs can browse types when receiving). Retire is a
**soft-delete** (`active=0`) — a type is never hard-deleted, so receipts/incidents that reference a
`catalog_id` keep their target.

## Symptoms → low-class repair (Tier-2)

| Symptom | Check | Repair |
|---|---|---|
| "Materials Catalog" card missing from the home | The account lacks `cap.materials.manage` | Grant the cap (admin role already holds it); confirm the user is an admin account |
| Add / edit returns "forbidden" (403) | The acting account lost `cap.materials.manage` | Re-confirm the account's role/capabilities in Accounts |
| A retired type still appears in pickers | Soft-retire sets `active=0`; default reads exclude it | Confirm the row's `active=0`; the receive picker reads active-only — no repair needed |
| A type was retired by mistake | No "un-retire" button in the UI yet | **Escalate** (a re-activate is a code/UI change) |

## Escalate to Seth (Developer-Operator) — high-capability-class

- Any change to the catalog **schema** or a need to **hard-delete** / re-activate a type (code).
- A capability-vocabulary change (`cap.materials.*` is seeded in migration 0013 — Seth-only).
- Anything touching the migration / the seed data (`0019_material_catalog.sql`).

## Expected materials — per-job receipt list (Material receipts M1)

The **Expected materials** section on a job's detail (Job Tracker) is the per-job list of what
that job is waiting on (`job_expected_materials`, migration 0031). The office
(`cap.materials.manage`, admin) adds rows — picked from this catalog or typed free-text — with
qty/unit/expected date, edits them while still *Expected*, reorders, and removes (a soft
deactivate; history is kept). Managers and field PMs (`cap.materials.receive`) see the list
**read-only** on their own job; their receive action (confirm receipt / flag a delivery problem)
arrives through the **daily form in M2** — the Worker routes for it already exist
(`…/receive`, `…/flag-incident`) and are per-job ownership-scoped.

### Symptoms → low-class repair (Tier-2)

| Symptom | Check | Repair |
|---|---|---|
| "Expected materials" section missing from a job's detail | The account holds neither `cap.materials.manage` nor `cap.materials.receive` | Confirm the account's role in Accounts (all three roles hold `receive`; only admin holds `manage`) |
| A manager sees "Failed to load expected materials" on a job (403 `forbidden_job` in the network tab) | Non-admins only read the job they are **placed on** (`personnel.current_job`) | Check the person's placement on the Personnel page / job crew — place them on the job (this is the designed scope, not a fault) |
| "already_actioned" (409) when confirming a receipt | The row was already received / flagged by someone else | No repair — the first action won; the row shows who and when |
| A row can't be edited ("not_editable", 409) | Received/incident rows are receipt **records** — content edits are locked | Expected behavior. If the record itself is wrong, escalate |
| The catalog picker fails but free-text add works | The catalog read failed (transient) | Retry; if persistent check the Materials Catalog page loads |

### Escalate to Seth (Developer-Operator) — high-capability-class

- Correcting a **received/incident row's recorded facts** (stamps, status un-flip) — a data/code change.
- Anything touching migration `0031_job_expected_materials.sql` or the status model.
- The M2 daily-form receipt flow + material-incident form (not built yet — do not improvise).

## Notes

- The authoritative monetary value of a material comes from the **per-job Material List line** (M2),
  not the catalog's optional `unit_cost` reference field.
- Per-job expectations live on each job's detail in the **Job Tracker** ("Expected materials");
  this catalog stays the type vocabulary those rows pick from.
