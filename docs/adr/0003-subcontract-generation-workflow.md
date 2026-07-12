---
type: reference
date: 2026-07-10
status: active
related_prs: []
workstream: subcontracts
tags: [adr, subcontracts, generation, deterministic, po-mirror, section-46, section-51, external-send-gate, legal-guardrail]
---

# ADR-0003 — Subcontract generation workflow (deterministic, PO-mirror)

**Status:** Accepted / building. This ADR records the design decided from a full parse of the
Evergreen subcontract corpus (`~/Desktop/Evergreen project/zip project documents/05_Subcontracts`,
382 files / 7 projects) + a component reuse-map of the live Purchase-Order workstream. The workflow is
built as a near-mirror of PO, ships **dark**, and is deployed/activated by the operator.

## Context

Evergreen executes solar-construction **subcontracts** — a bigger, wet-signature cousin of the PO. A
subcontract **package** = **Subcontract Agreement** (a 27-article fixed legal body) + **Exhibit A**
(scope of work) + a **Schedule of Values (SOV)** + a **fixed Annex kit (C–K)**. The `subcontracts`
workstream tier was pre-provisioned across the codebase (`cap.subcontracts.manage` in the config
registry, `VALID_WORKSTREAMS`, `SlaTier.SUBCONTRACT_DRAFT=48h`, watchdog Check A, `FOLDER_HR_SUBCONTRACTS`,
commented capability-gating stubs) — this fills that frame.

### What the corpus showed
- The **Subcontract body is ~99% fixed boilerplate** (27 articles byte-identical to the template). The
  only fill-points: the preamble parties/date, §2.1 Contract Price (words + figure), and the signature
  entity. Contractor is a **constant** ("Evergreen Renewables LLC").
- Only **two documents are authored** (Subcontract + Exhibit A); the Annexes are a **fixed kit copied
  in**, not re-authored per sub.
- **Exhibit A** = a fixed 6-article skeleton (Art I/III/IV/VI deterministic) whose only variable section
  is **Article II "The Work"** (trade-specific scope).
- The **SOV is a derived single-line echo** of the §2.1 price (`Scope | Value | Total`), not a price
  driver. Money model is Contract-Price-first.
- The **owner-entity fan-out** is the #1 complexity multiplier: one subcontractor → one subcontract per
  SPV (a 3-tier Project → Owner-SPV → Subcontractor model).
- Two template families: a dominant **27-article long form** (6/7 projects) and a **4-article short
  form** (KSI monthly). Governing law is hard-coded Virginia in the body while lien-waiver annexes are
  per-state statute — a latent jurisdiction field.

## Decisions

1. **Fully deterministic — NO AI in the generation path.** (Operator directive, 2026-07-10; matches the
   grain of ITS — the Anthropic narrative core was retired from `weekly_generate`.) Article II "The Work"
   is **operator-authored, trade-templated** — the operator picks a trade → gets that trade's standard
   Article II as an editable starting point (a versioned scope-template config artifact) → edits it.
   ITS does the deterministic ~95% (Articles I/III/IV/VI + the header fields + the SOV + the price-words
   derivation + package assembly + the correctness gates). **AI-assisted Article II drafting is an
   explicitly-parked future capability we do not lean on.**

2. **Mirror the PO two-store data model.** D1 authoritative for documents; Smartsheet mirrors. For the
   party registry the polarity flips (Smartsheet SoR, D1 cache — §51). New D1 tables `subcontractors`
   (0049, ← po_vendors), `subcontracts` + `sov_lines` (0050, ← purchase_orders/po_line_items), capability
   grant (0051, ← 0044). New Smartsheet `ITS_Subcontractors` (SoR) / `Subcontract_Log` (mirror) /
   `Subcontract_Pending_Review` (WSR twin). Money is **integer cents**, no floats, no tax/shipping.

3. **ITS adds value the manual process can't:** (a) §2.1 price WORDS are **derived from integer cents**
   (num2words) so words always == figures — the corpus shipped a real "nine cents / $…00" mismatch;
   (b) **SOV-sums-to-price** + **price-words==figures** correctness gates (the PO totals-guard pattern,
   render-time re-derive-vs-signed); (c) canonical entity strings kill the "BG Wing, LLC, LLC" and
   5-form Prime-Contractor drift.

4. **Maximal reuse (§14 preservation-over-refactor).** REUSE-AS-IS: `form_pdf.merge_pdfs` (the
   multi-doc package assembler), the `weekly_send` engine (already parameterized), `_js_round`/integer
   cents, the `form_pdf` brand primitives, the config-editor queue. PARAMETERIZE: `numbering.py`,
   `vendors.py` (§51 sync → `subcontractors.py`), `po_log.py`, the `terms.py` loader (base-dir), the
   Mac config actuator (make it **workstream-aware** — the one real "zero-route-changes" gap). FORK-NEW:
   the D1 tables, `worker/subcontract.ts`, the `subcontracts/` Python package, the 3 Smartsheet builders.

5. **Governing law is a parameterized field** (`governing_law_state`, default `VA`), surfaced as a legal
   decision, NOT auto-filled into the body.

6. **The insurance/COI compliance gate is PARKED BLOCKED** — the COI evidence lives outside the corpus
   (an unseen source-of-truth). The subcontractor registry carries a `coi_reference` **pointer only**;
   no compliance-blocking logic is built against data we can't see (the "don't build against an unseen
   SoR" rule). The contractual obligation (Article 20) is preserved in the body; enforcement is a future
   slice fed by the real COI SoR.

7. **Long-form (27-article) first.** Short-form (KSI monthly) is a `template_family` the data model
   supports but a later slice builds. The "Subcontractor Assignment" collateral-assignment consent
   (lender doc) and the sub-returned annex forms (D/F/G/H COI/lien-waiver/verified-list) are out of the
   generator's scope.

8. **Wet-signature execution state.** The status machine adds `executed` after `sent` (the corpus '_FE'
   Fully-Executed marker) — a subcontract is countersigned, unlike a PO. E-signature is out of scope
   (generate-for-signature).

9. **Attach-kind (negotiated-MSA) profiles render a one-page reference, not a fence.** A subcontractor
   on a negotiated Master Subcontract Agreement uses an `attach`-kind terms profile (`negotiated_msa`).
   Its `Subcontract.docx` is a **one-page reference** — the standard body's VERBATIM preamble + §2.1
   Contract Price + the profile's fixed manifest `render_line` + the standard signature block
   (`subcontracts/terms/attach_reference.md`, sha-pinned) — rendered IN PLACE OF the 27-article body,
   with Exhibit A + Annex C still rendered as their own package files (the package stays 3 files).
   Because the reference body is built from ONLY verbatim standard fragments + the fixed render_line
   (no independently-drafted terms), it carries **no per-version legal_review gate** — the render_line
   and the reference scaffold are legally reviewed once at ADR/PR time, and the binding terms live in
   the external MSA. This SUPERSEDES the initial "emit ONLY the reference line" stub framing (operator
   directive, 2026-07-12): the generated document is self-describing rather than a bare line.

## Invariants preserved
- **External Send Gate (Invariant 1):** two-process — `subcontract_generate` has zero send, `subcontract_send`
  has zero AI. Ships dark. Send/execution approval = §46 workspace membership + F22, Mac-side.
- **Adversarial input (Invariant 2):** HMAC domain `sub:v1` (never replayable as a PO/submission); every
  Worker body shape-guarded + `?`-bound; mutation+audit atomic (W4).

## Slices
- **SC-S1** — data model (0049/0050/0051) + `subcontracts/` party/numbering/log/naming modules + the 3
  Smartsheet build scripts (staged). *(this ADR's first PR)*
- **SC-S2** — config artifacts (`contractor`/`subcontract_body`/`exhibit_trade_templates`/`payment_terms`/
  `annex_kit`) into the CONFIG_REGISTRY placeholder + the workstream-aware actuator **[build-time deploy
  = high-class → staged]** + corpus seed.
- **SC-S3** — generation (`worker/subcontract.ts` + `subcontract_generate.py` + the two guards + package
  assembly) → generates a draft package.
- **SC-S4** — review + send binding (dark).
- **SC-S5** — SPA subcontract builder page.

## Consequences
The workflow ships dark; the operator applies the migrations (0049→0051), runs the Smartsheet builders,
seeds the config, and flips the gates. Because it mirrors PO, the operator's PO mental model transfers
directly. The parked items (short-form, COI gate, AI-assisted Article II, e-signature) are first-class
future slices, not silent gaps.
