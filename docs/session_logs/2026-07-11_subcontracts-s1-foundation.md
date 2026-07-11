---
type: session_log
date: 2026-07-11
status: active
related_prs: [529]
workstream: subcontracts
tags: [subcontracts, generation, deterministic, po-mirror, adr-0003, corpus-analysis, overnight, autonomous]
---

# Subcontract generation workstream — SC-S1 foundation (deterministic, PO-mirror)

## Purpose

Stand up a NEW workstream: deterministic generation of Evergreen solar-construction **subcontract
packages** (a bigger, wet-signature cousin of the PO). Scoped in one autonomous overnight from a full
corpus parse → design (ADR) → build (SC-S1, dark). **Operator directive: NO AI in the generation path** —
the AI-assisted Exhibit A drafting is an explicitly-parked future capability we do not lean on.

## Pre-flight findings — the corpus (Workflow fan-out)

Parsed `~/Desktop/Evergreen project/zip project documents/05_Subcontracts` (382 files / 7 projects;
`Blank/` templates + `Filled/` 368 executed) via a 5-dimension corpus-analysis Workflow + a 4-surface PO
reuse-map Workflow. Files are iCloud dataless by default (`brctl download` materializes; the operator
bulk-downloaded the Filled set). Decisive findings:

- A **package** = **Subcontract** (27-article body, ~99% fixed — fill-points ONLY the preamble
  parties/date + §2.1 price words+figure + signature; **Contractor is CONSTANT "Evergreen Renewables
  LLC"**) + **Exhibit A** (6-article; Art I/III/IV/VI deterministic, **Art II "The Work" =
  operator-authored trade-templated**) + **SOV** (derived single-line echo of §2.1) + a **fixed Annex
  kit C–K copied in** (not authored). Each .docx+.pdf.
- Money is Contract-Price-first, **integer cents**, no tax/shipping. ITS adds value: §2.1 words derived
  from cents (the corpus shipped a real "nine cents/$…00" mismatch); SOV-sums-to-price + price-words
  gates; canonical entity strings (kills "BG Wing, LLC, LLC" + 5-form Prime-Contractor drift).
- **Owner-entity fan-out** is the #1 complexity multiplier (one sub → one subcontract per SPV; 3-tier
  Project → Owner-SPV → Subcontractor; Bonacci = 190 files). Two template families (long form dominant +
  a 4-article short form). Governing law hard-coded VA vs per-state lien-waiver annexes = a latent
  jurisdiction field. COI evidence lives OUTSIDE the corpus.
- **Anticipatory scaffolding already existed**: `cap.subcontracts.manage`, `VALID_WORKSTREAMS`,
  `SlaTier.SUBCONTRACT_DRAFT`, watchdog Check A, `FOLDER_HR_SUBCONTRACTS`, commented capability-gating
  stubs — SC-S1 fills that frame.

## Design (ADR-0003)

Deterministic, PO-mirror, ~80% reuse. Four defaulted decisions (flagged for the operator): (1)
`governing_law_state` parameterized (default VA), a legal decision; (2) **COI compliance gate PARKED
BLOCKED** (unseen SoR — pointer only, no gate); (3) standalone `ITS — Subcontracts` workspace (§46); (4)
long-form first (short-form / AI-Article-II / e-signature / lender-consent parked). Reuse: `merge_pdfs` +
`weekly_send` + `_js_round` + `form_pdf` primitives AS-IS; parameterize numbering/vendors/po_log/terms +
the totals-guard → `sov_mismatches`+`price_words_mismatch` + the **workstream-aware actuator** (the one
real "zero-route" gap, S2); fork-new the D1 tables + `worker/subcontract.ts` + `subcontracts/` package.

## Code changes (PR #529, MERGED — dark)

- **Data model** — migrations 0049 `subcontractors`+counter, 0050 `subcontracts`+`sov_lines`, 0051 cap
  grant (HMAC `sub:v1`, `executed` terminal, price_basis, retainage_bp, governing_law_state, owner_entity,
  operator-authored `exhibit_a_work_text`). Validated against sqlite3 + the vitest Miniflare D1 harness.
- **Python** (`subcontracts/`, faithful PO forks) — numbering / subcontract_log / subcontract_naming /
  subcontractors (§51 sync).
- **Smartsheet builders** (staged, operator-run) — workspace + ITS_Subcontractors (SoR) + Subcontract_Log
  (mirror) + Subcontract_Pending_Review (WSR schema-twin).
- **shared/** — sheet_ids placeholder-0 constants + picklist_validation parity registration.
- **+29 tests** — round-trip/collision + builder↔registry parity + WSR-twin + the COL_* title-parity guard.

The 8 module forks were produced by a Workflow (one agent per module from its exact PO template), then
integrated + tested + reviewed here — the "contained fork" pattern (agents fork, I own schema + integration
+ correctness).

## Verification

- Worker vitest **945 pass** / Python `pytest tests/test_subcontract_*` **29 pass** / mypy 339 files clean /
  ruff clean.
- The three D1 migrations apply cleanly through the full chain (sqlite3 + Miniflare).

## Adversarial review (ops-stds-enforcer) — a real bug caught

- **BLOCK (fixed):** `subcontract_log.COL_SC_PDF` was `"SC PDF"` but the builder titles the column
  `"Subcontract PDF"` — a fork drift that would `KeyError`-crash the first subcontract filing (S4). Fixed +
  added a **title-parity regression test** (every `COL_*` ∈ builder `COLUMN_SCHEMA` titles), proven to RED
  on the drift and GREEN after (prove-the-control-bites, HOUSE_REFLEXES §2).
- Warnings (deferred): run `sdk-integration-test-scaffold` on `subcontractors.upsert_subcontractor` before
  S4 wires a live daemon (§30, inherited PO-fork debt); docstring headings precedent-consistent. Everything
  else CLEAN (§14 faithful forks, §35 picklists, §51 SoR discipline, §46, §45, data model, 0051 capability).

## CI fan-out diagnosed + fixed

Migration 0051's `cap.subcontracts.manage` grant broke a config-editor test on main that ASSUMED the cap
was ungranted ("subcontracts placeholder → 403"). The cap is now held → the request passes the cap check
and 400s at artifact-lookup (the placeholder has no artifacts until S2). Updated the test to the new
reality (400 `invalid_artifact`) + added a submitter-403 test so the cap gate stays covered. The test's own
comment had anticipated this exact transition.

## Merge verification quartet (four-part)

```
PR #529 (SC-S1):
- state=MERGED
- mergedAt=2026-07-11T01:39:34Z (non-null)
- mergeCommit.oid=3413b14a473d6e895f866dbc627eb846a7d27262 (present)
- main-branch CI on merge commit: SUCCESS (all checks)
```

## Out-of-scope notes / next slices

- **SC-S2** — config artifacts into the placeholder + workstream-aware actuator **[high-class → stage]** +
  seed the body + trade templates from `scratchpad/subk_seed/`.
- **SC-S3** — `worker/subcontract.ts` + `subcontract_generate.py` + the two guards + `merge_pdfs` package
  assembly (**adversarial review is DoD** — Worker trust boundary + HMAC).
- **SC-S4** — review + send binding (dark); **SC-S5** — SPA builder.

## Operator-side actions remaining (ships DARK)

Apply migrations 0049→0051 to live D1; run the 4 Smartsheet builders; flip the printed ids into
`sheet_ids.py`. Then SC-S2/S3 land the config + generation. See `docs/adr/0003-subcontract-generation-workflow.md`
and the `subcontracts-workflow` auto-memory for the full plan.
