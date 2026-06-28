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

## Notes

- The authoritative monetary value of a material comes from the **per-job Material List line** (M2),
  not the catalog's optional `unit_cost` reference field.
