---
type: reference
status: proposed
workstream: docs
tags: [doctrine, op-stds-v19, section-51, materials, m2, proposal, seth-owned]
---

# PROPOSED — §51 Material List reconciliation: ratification-ready rider draft + honest decision framing

**Status: PROPOSAL for Developer-Operator (Seth). NOT doctrine.** Complements (does NOT duplicate) the
tech-debt entry added by #471 — *"M2 Material List — one-way-up MVP diverges from §51's bidirectional
model [OPEN 2026-07-05 — Seth reconciliation needed]"* (`docs/tech_debt.md`). That entry records the
divergence; this doc adds a **ratification-ready draft** for the rider path AND an honest statement of why
that path might not clear the bar. **Blocks flipping `field_ops.fieldops_sync.materials_enabled=true`**
(created 2026-07-05, value `false`, visible for the flip — the row + block are documented in ITS_Config).

## The discrepancy

Canonical Op Stds **v19 §51** (`~/its-blueprint/doctrine/operational-standards.md`, line ~847) names the
Material List as **"bidirectional with split column ownership — the operator owns content columns, the
field owns delivery columns, neither side's write overwrites the other's."** Shipped **M2 (#470, exec
`f7f3764`)** is a **one-way-up snapshot** (portal is sole author; `progress_reports/material_list.py`
mirrors UP; no `smartsheet_row_id`, no down-sync). An operator editing the Smartsheet Material List
directly has **no path back to D1**. This was an operator-ratified Option A in-session, not a doctrine change.

## The honest crux (do NOT rubber-stamp — the #471 caution)

A first-pass argument is "one-way-up is a *strict subset* of the bidirectional guards → clean v19.x rider."
**That is contestable, and #471 flagged why:** the bidirectional model *promises the operator can edit
content columns as an input to the system.* One-way-up **removes that capability**. So the question turns
on how §51's v20 trigger — *"recharacterization of a mechanism's protective claim"* — is read:

- **Reading A (→ v19.x rider is defensible).** "Protective claim" = the SECURITY/SAFETY guarantees
  (send-free, AI-free, non-clobbering column-scoped write, never-`delete_rows`, archive-on-closure,
  find-or-create margin-check). **One-way-up preserves every one of these** — it is strictly *more*
  conservative (never writes operator-owned columns, never reads operator edits back). Removing the
  *editability feature* is a capability deferral, not a protection weakening. → phased-delivery rider.
- **Reading B (→ possibly a v20 recharacterization).** The bidirectional split-ownership *is* the
  mechanism's promised contract to the operator (a two-way SoR they can edit). Dropping it changes **what
  the mechanism promises its user**, which §51's own trigger language can be read to cover. → not a rider;
  a v20 bump or an explicit §51 clause edit.

**This is a genuine doctrine-interpretation call reserved to Seth (§44), not mechanical.** The drift-checker
won't catch it; neither reading is obviously wrong.

## The three decision paths (Seth picks one)

1. **v19.x phased-delivery rider** (Reading A) — bless one-way-up now, bidirectional M2b later; paste the
   draft below into operational-standards.md, tag-absorb at v19.x; then flip `materials_enabled=true`.
2. **Reconfirm one-way-up as the PERMANENT Material-List posture** — amend §51's clause to drop
   "bidirectional" for the Material List (itself a protective-claim recharacterization → likely a **v20**
   edit, cleaner than a rider). Then enable. Choose if the operator-edit direction is not actually wanted.
3. **Require bidirectional M2b before enabling** — leave `materials_enabled=false`, build M2b
   (`smartsheet_row_id` + a down-sync receive path + split column ownership) to match the literal §51
   clause first. Highest cost; choose only if operator-editable materials are imminently needed.

Until one lands, `materials_enabled` stays `false`.

## Draft rider text — path 1 only (ratification-ready IF Seth judges Reading A)

> **v19.x amendment rider (2026-07-05, verified against exec main `f7f3764` = M2 ship / #470): §51
> Material List — phased delivery (one-way-up MVP now, bidirectional receive as a future M2b).** §51 names
> the Material List as "bidirectional with split column ownership." That end state is delivered in **two
> phases**. **M2 (shipped, #470)** is a **one-way-up snapshot** inside `field_ops.fieldops_sync` — the
> per-job Material List re-projects UP into the ITS-owned `<Job> — Material List` sheet each cycle
> (non-clobbering, column-scoped, never-`delete_rows`, archive-on-closure, A5 row-cap watchdog), with **no
> operator-content receive direction**. **M2b (future)** adds the bidirectional split-ownership receive
> when a Material-List operator-edit workflow actually exists. **Protective-claim analysis (the rider's
> load-bearing claim):** every §51 *protective* guard (send-free + AI-free GATED daemon, `WALKED_ROOTS`,
> allowlisted `portal_client` egress, validated find-or-create + A1 margin-check, non-clobbering write,
> never-delete + archive-on-closure) is met by shipped M2; the one-way phase is strictly *more*
> conservative than the constrained bidirectional posture. The **removed capability** — the operator's
> ability to edit content columns as a system input — is a **feature deferral, not a protection weakening**;
> the split-ownership non-clobber guarantee is preserved intact for M2b (it has no surface to bind to until
> the receive workflow ships). **Why a rider and not a v20 bump:** no `§N` is added/removed/renumbered and
> no *protective* claim is weakened; only the Material-List clause's *delivery* is clarified as phased — the
> same "does not weaken a protective claim" test the 2026-07-04 low-volume-log rider + the 2026-07-03 Sentry
> rider applied. Exec realization: `field_ops/fieldops_sync.py` material-list pass +
> `progress_reports/material_list.py` (#470); the tracker ships DARK (`materials_enabled=false`) until ratified.

## Provenance

- Divergence + the v20-vs-rider caution: #471 tech-debt entry (`docs/tech_debt.md`); P7/M2 handoff item 3.
- Canonical §51: `~/its-blueprint/doctrine/operational-standards.md` line ~847.
- Rider precedent: the 2026-07-04 low-volume-log rider (same file, line ~859); the 2026-07-03 Sentry rider.
- Exec: M2 shipped `f7f3764` (#470); `materials_enabled` ITS_Config row created 2026-07-05 (value `false`).
