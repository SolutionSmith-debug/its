---
type: audit
date: 2026-06-02
status: active
related_prs: []
workstream: null
tags: [picklist-drift, registry, smartsheet, sdk-vs-live, phase-1-classification, adversarial-ingestion]
---

# Picklist Drift Reconcile — Phase 1 Classification (2026-06-02)

First-ever run of `scripts/audit_picklist_drift.py` (the C4 launchd plist landed
this session, so the weekly audit had never executed before tonight). This
document is the **verify-before-fix gate** for the reconcile: it classifies each
of the three drift findings as **active-break | latent | dormant**, fixes the
remediation direction, and names the mechanism — read-only, no live-sheet writes.

## Scope audited

`scripts/audit_picklist_drift.py --no-emit` compares `shared.picklist_validation.REGISTRY`
against the live sheets and reports (captured 2026-06-03T01:42Z):

```
[info] Audited 4 registered sheet(s).
[warn] 3 drift finding(s):
  - sheet=27291433258884 column='Workstream':  NOT PRESENT in live sheet
  - sheet=7243317526876036 column='Reason':     allowed-set mismatch
        (in live only: []; in registry only:
         ['header-soft-fail-trusted', 'project-out-of-scope', 'sender-pending-verification'])
  - sheet=8687740798324612 column='Disposition': NOT PRESENT in live sheet
```

## Foundation-invariant scope note

These sheets (ITS_Review_Queue, ITS_Quarantine) are the surfaces the
intake/triage and 6-layer adversarial-defense paths (FM v11 Invariant 2) write
*into*. This reconcile is **schema-only** — it adds missing picklist options so
legitimate disposition writes bucket correctly. It does **not** alter, relax, or
route around any defense layer, the quarantine decision logic, or the
review-queue routing. Invariant 1 (External Send Gate) is not implicated (all
writes are internal Smartsheet mutations).

## Status legend

- **active-break** — a code path is *writing now* and the live sheet *rejects* it.
- **latent** — a code path writes the value, the write *succeeds*, but the value
  isn't a first-class picklist option (no dropdown entry / pivot bucket).
- **dormant** — the REGISTRY declares the column but the live sheet lacks it
  **and** no code writes it (the registry over-declares ahead of a future writer).

## Findings

| # | Sheet · Column | Live state | Writer today | Class | Direction · mechanism |
|---|---|---|---|---|---|
| 1 | ITS_Review_Queue · `Reason` | PICKLIST, 9 options, **`validation: false`** (NOT restrict-to-dropdown); 3 enum values absent | **YES** — `safety_reports/intake.py:507,521,602,1135` emit `header-soft-fail-trusted` / `sender-pending-verification` / `project-out-of-scope` on live branches | **latent** | registry/enum canonical → **add 3 options to live** via an additive `ensure_picklist_options` helper (Phase 2) |
| 2 | ITS_Errors · `Workstream` | column **absent** | **none** — `shared/error_log.py:130-138` row payload has no `Workstream` key | **dormant** | DECISION (Phase 3a): add empty column **or** trim registry entry |
| 3 | ITS_Quarantine · `Disposition` | column **absent** | **none** — `shared/quarantine.py:log_quarantined_message` writes no `Disposition`; the value set is registered for a future write path that does not exist | **dormant** | DECISION (Phase 3a): add empty column **or** defer |

## Evidence per finding

### Finding #1 — `Reason` is LATENT, not an active break

The brief's table implied "active break? YES" because four live branches emit the
three values. Live inspection corrects the **severity**, not the direction:

- **`validation: false`** on the live `Reason` column (raw column dict via the
  SDK). Smartsheet only rejects unknown picklist values when the column is set to
  *restrict to dropdown values only* (`validation: true`). With validation off,
  the four `intake.py` branches' writes **succeed as free-text picklist values** —
  no server-side rejection. This matches `shared/review_queue.py:84-96`: *"Smartsheet
  accepts unknown picklist values as plain strings so writes succeed even before
  the UI add, but pivot views won't bucket them until then."*
- **No live rows currently carry the three values** (2 rows total in the sandbox
  ITS_Review_Queue; the new branches haven't been exercised yet) — so there is no
  in-flight data to repair, only a forward-looking option-add.
- **The only ITS_Errors row referencing this** is the audit's own `picklist_drift`
  finding (`Script = scripts.audit_picklist_drift`), **not** a runtime
  `SmartsheetError`/1xxx rejection on a Review_Queue write. Confirmed by scanning
  all 186 ITS_Errors rows: zero Reason/picklist write-rejections.

**Impact if left:** operator review dropdown won't list the three reasons and
pivot/report views won't bucket them as first-class options. Worth remediating
(it is the documented-but-undone operator step at `review_queue.py:84-96`, and it
clears the recurring weekly audit WARN), but it is **not** data-loss and **not**
urgent. Remediation proceeds in Phase 2.

### Finding #2 — `Workstream` on ITS_Errors is DORMANT

- Live ITS_Errors has 11 columns; **no `Workstream`** among them.
- `shared/error_log.py:130-138` builds the row dict with
  `Error / Timestamp / Severity / Script / Message / Traceback / Correlation_ID` —
  **no `Workstream` key**. No other writer supplies it.
- `shared/picklist_validation.py:147` registers `SHEET_ERRORS → "Workstream" →
  _WORKSTREAM_VALUES_GLOBAL`. The registry **over-declares**: it anticipates a
  workstream-tagged error row that no code writes today.

### Finding #3 — `Disposition` on ITS_Quarantine is DORMANT

- Live ITS_Quarantine has 11 columns; **no `Disposition`** among them. (It *does*
  have a `Workstream` PICKLIST whose catch-all is `other`, consistent with the
  REGISTRY — that one is not drifting.)
- `shared/quarantine.py:38` is `class QuarantineReason` (the *why-quarantined*
  codes), written into `Notes` as `[reason: <code>]`, **not** a `Disposition`
  column. `log_quarantined_message` writes no `Disposition`.
- The disposition value set (`RELEASE`/`DELETE`/`ESCALATE`) lives in
  `shared/picklist_validation.py:96-98` (`_QUARANTINE_DISPOSITION_VALUES`),
  registered against `SHEET_QUARANTINE → "Disposition"` so that *"writes from
  shared/quarantine.py (when it grows a disposition write path) are validated
  client-side pre-conversion."* That write path **does not exist yet** — this is a
  forward-looking registration, exactly like finding #2.

## Brief-claim corrections (verified at `ba2c833`)

Surfaced by `brief-validator`; recorded so downstream work uses the real shapes:

1. The audit's dry-run flag is **`--no-emit`**, not `--no-error-log`.
2. `shared/picklist_validation.py`: `PicklistViolationError` is defined at **:44**
   (raised at :231); the REGISTRY block (:144-163) registers more than the drifting
   columns (Review_Queue also has `SLA Tier`/`Workstream`/`Status`/`Severity`).
3. `shared/quarantine.py:38` is `class QuarantineReason` (intake codes), **not**
   "dispositions"; the disposition set is in `picklist_validation.py:96-98`.
4. `shared/smartsheet_client.py` **already has** `update_column_options` (`:715`) and
   `list_columns_with_options` (`:668`). `update_column_options` is **REPLACE-style**
   (PUT replaces the whole options array), so Phase 2 needs an **additive** wrapper
   on top of it (read current → union with missing → write union), never a bare
   replace.

## Owner / next action

- **Phase 2 (authorized):** remediate finding #1 — add the three options to the
  live ITS_Review_Queue `Reason` picklist via a new additive, idempotent,
  no-removal `shared.smartsheet_client.ensure_picklist_options` helper (+ §30
  integration test, §42 docstring). Preview then apply; re-run the audit to confirm
  the `Reason` finding clears; log an ITS_Errors INFO. Findings #2/#3 are expected
  to remain (they are Phase 3).
- **Phase 3 (recommend-only — Seth decides):** 3a direction for the two dormant
  columns (add empty column vs trim registry); 3b the systemic gap (no automated
  registry→live apply — automate via an additive `--apply` mode vs document the
  manual step). Do not auto-execute.
