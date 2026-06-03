---
type: operations
date: 2026-06-02
status: active
related_prs: []
workstream: infrastructure
tags: [runbook, successor-remediation, smartsheet, picklist, audit, tier-2]
---

# Runbook — Weekly picklist audit reports drift (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (runs Claude
Code, reads Smartsheet rows + alert emails; does not read code or touch secrets).
The §42 code-reader rationale lives in `scripts/audit_picklist_drift.py`,
`shared/picklist_validation.py` (the `REGISTRY`), and
`shared/smartsheet_client.py::ensure_picklist_options`.

## Purpose

What to do when the **weekly picklist audit** (`safety_picklist_audit`, Sunday)
reports a drift between the code `REGISTRY` (the canonical allowed-value sets) and
a live Smartsheet picklist. Two shapes of finding exist, and they have very
different dispositions — most are **escalate-to-Seth decisions**, not low-class
repairs.

## Procedure

### Symptom

- An ITS_Errors row with `Error = picklist_drift`, `Script =
  scripts.audit_picklist_drift`, `Severity = WARN`, whose `Message` reads either:
  - **option-set mismatch** — `column='X': allowed-set mismatch (in live only:
    [...]; in registry only: [...])`, **or**
  - **missing column** — `column='X': NOT PRESENT in live sheet`.

### What the Successor-Operator checks

1. **Which shape is it?** Read the `Message`. "allowed-set mismatch" is an
   *option* drift on an existing column; "NOT PRESENT" is a *missing column*.
2. **Which direction does the mismatch go?** For an option mismatch, note whether
   values are *in registry only* (code knows a value the live sheet lacks — the
   common, additive case) or *in live only* (the live sheet has an extra value
   the code doesn't — a removal question, which is **always Seth's**).

### The Claude prompt or UI action

- **Additive option drift, registry-canonical (e.g. ITS_Review_Queue `Reason`):**
  the fix is to *add* the missing options to the live picklist — additive, never
  removing. This was done for `Reason` on 2026-06-02 via
  `smartsheet_client.ensure_picklist_options` (additive, idempotent, no-removal;
  validated by `tests/test_smartsheet_client_integration.py`). **Today this helper
  is invoked by the Developer-Operator** (a short Python call against the live
  SDK), so it currently **escalates to Seth**. An operator-friendly
  `audit_picklist_drift.py --apply` mode is a *pending decision* (see the
  classification doc Phase 3b); **if/when it lands**, the Tier-2 action becomes:

  > "Claude, the weekly picklist audit reports an additive `Reason` option drift.
  > Run `scripts/audit_picklist_drift.py --apply --dry-run`, show me the proposed
  > additions, and after I confirm, run it for real and re-run the audit to
  > confirm the finding clears."

- **Missing column, or any "in live only" removal:** **do not** add or remove a
  column. Whether to add the empty column to the sheet or to trim the `REGISTRY`
  is a schema/canonical-doc decision → escalate.

### Escalate-to-Seth condition

Escalate (Tier 3) when **any** of:

- The finding is a **missing column** (`NOT PRESENT`) — adding a column is a
  sandbox schema change and trimming the registry is a **doctrine/code** edit
  (route registry edits via `doc-reconciliation-auditor`); both are Seth's.
- The mismatch has values **in live only** (a *removal* question — removals are
  reference-checked and never additive-safe by default).
- The additive `--apply` operator path does **not yet exist** (pending the
  Phase 3b decision) — until then the additive reconcile itself is run by Seth.
- Any change to `picklist_validation.REGISTRY` (code) is implied.

Both-rule (Op Stds §44): confirming the finding and (once `--apply` exists)
running an additive, dry-run-previewed, no-removal option-add is low-class Tier-2;
column creation, registry edits, and removals are high-class Tier-3.

## Owner

`@solutionsmith`. The audit, the REGISTRY, and `ensure_picklist_options` are code
(Tier 3). The 2026-06-02 `Reason` reconcile + this runbook landed together;
findings #2 (ITS_Errors `Workstream`) and #3 (ITS_Quarantine `Disposition`) were
classified **dormant** (registry over-declares; no live writer) and await the
Phase 3a add-column-vs-trim-registry decision.
