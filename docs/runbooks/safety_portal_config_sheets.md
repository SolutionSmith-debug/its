---
type: operations
date: 2026-06-03
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, smartsheet, safety-portal, picklist, tier-2]
---

# Runbook — Safety Portal config sheets (ITS_Active_Jobs + ITS_Forms_Catalog) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry, written for the **Successor-Operator**: a
trained operator who runs Claude Code and edits Smartsheet rows + reads alert
emails, but does **not** read code or touch secrets. The §42 code-reader
rationale lives in `scripts/migrations/build_its_active_jobs_sheet.py` /
`build_its_forms_catalog_sheet.py` (sheet schemas), the `seed_*` companions, and
`shared/picklist_validation.py` (the Active-column allowed set).

## Purpose

The Safety Portal reads exactly two Smartsheet sheets, both in **ITS —
Operations / Safety Portal**:

- **ITS_Active_Jobs** — the office-PM-maintained list of jobs the portal offers
  (home screen) and the Address that auto-fills each form's Work Location. Only
  rows with **Active = Active** appear.
- **ITS_Forms_Catalog** — which forms the portal offers. Only **Active = Active**
  rows are offered; **Form Code** must match the code form directory exactly.

This runbook covers the low-class faults a Successor-Operator can resolve and the
boundary where it escalates to Seth (Tier 3). Until the portal is built (Phase 4)
nothing reads these sheets, so most faults are non-urgent.

## Procedure

### Fault A — A migration failed or applied partially

**Symptom.** A build/seed migration errored, or a sheet exists with missing
columns / missing job or form rows.

**Check.** Open ITS — Operations / Safety Portal. Confirm both sheets exist with
their columns; open each and confirm the rows (6 jobs / 4 forms).

**Action (Tier-2, low-class).** Re-run the idempotent migration — it skips what
already exists and only adds what's missing:
> "Claude, re-run `scripts/migrations/build_its_active_jobs_sheet.py` then
> `seed_its_active_jobs.py` (and the forms_catalog pair), and report what was
> created vs skipped."

Re-running is safe: builds find-or-create by name; seeds key on Job ID / Form
Code and skip rows already present.

### Fault B — Picklist option / schema drift on the Active column

**Symptom.** `audit_picklist_drift` reports a mismatch on the **Active** column
of either sheet (allowed set is exactly `Active / Inactive / Archived`), or a
write is rejected with a picklist violation.

**Check + Action (Tier-2, low-class).** Have Claude run `audit_picklist_drift`;
if it surfaces a missing option, re-run the build migration (it re-asserts the
option set on create). Do **not** hand-add new options in the Smartsheet UI — the
three values are the contract the portal renders against.

### Fault C — A sheet was deleted, or the sheet_ids constant was reset

**Symptom.** A verify (or, post-Phase-4, the portal) can't find ITS_Active_Jobs /
ITS_Forms_Catalog; or `SHEET_ACTIVE_JOBS` / `SHEET_FORMS_CATALOG` reads 0.

**Action (Tier-2, low-class for the re-run; co-resolve for the file edit).**
Re-run build → flip the printed sheet ID into `shared/sheet_ids.py` → re-run
seed. Re-running the migration and reading the printed ID is operator-safe; the
ID **value** is operator-safe, but editing `shared/sheet_ids.py` is a code-file
edit — if unsure, **co-resolve with Seth** (it borders the code FIXED category).

### Fault D — A schema change the portal's read contract depends on

**Symptom.** A request to rename/retype a column the portal reads (Project Name,
Job ID, Address, Active, Form Code, Available For Jobs), or to add a new column.

**Action — ESCALATE to Seth (Tier 3).** This **touches code** (the portal's read
contract + the build schema + the picklist registry) — one of the four FIXED
high-capability-class categories. Do not change a column the portal reads.

**Both-rule (Op Stds §44):** re-running an idempotent migration or editing a job/
form **row** is low-class Tier-2; changing a **column/schema** or any **code/
doctrine** is high-class Tier-3 (co-resolve with Seth).

## Routine office-PM edits (NOT faults)

Adding/retiring a job (add a row / set `Active=Inactive`), filling an **Address**
(all jobs ship with Address BLANK — the office PM fills it from real job data;
never machine-invented), or adding a form row (e.g. a job-scoped `jha-bradley-v1`
with `Available For Jobs=bradley-1,bradley-2`) are normal office-PM edits in the
Smartsheet UI — not remediation. A new form row's **Form Code must match a real
code form directory**, or the portal can't render it.

## Owner

`@solutionsmith`. Sheet schemas, the seed sets, and the picklist registry are
code (Tier 3) — see `scripts/migrations/build_its_active_jobs_sheet.py`,
`build_its_forms_catalog_sheet.py`, the `seed_*` companions, and
`shared/picklist_validation.py`.
