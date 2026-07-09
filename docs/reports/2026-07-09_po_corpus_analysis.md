---
type: report
date: 2026-07-09
status: active
related_prs: []
workstream: null
tags: [purchase_orders, po_corpus, aug7_delivery, s0]
---

# Evergreen PO Corpus Analysis — the document model for the ITS PO generator (S0)

## Summary

Reverse-engineering of Evergreen's real purchase-order corpus to seed the ITS PO generator's
Smartsheet schema, D1 model, PDF template, and terms library (Aug-7 delivery program slice S0;
see `docs/2026-07-09_aug7_delivery_program.md`). One dominant Evergreen template ("Family A")
carries the build; PO numbering follows `{YYYY.NNN}.{site}.{supersede?}.{revision}` with an
explicit in-body supersession clause; **five distinct T&C regimes** are selected per
vendor/commodity; tax is keyed to the ship-to state; every Evergreen PO carries dual signature
blocks. Representative samples are committed at `docs/references/po_samples/` (3 filled PDFs +
the 2 blank template DOCX — `Purchase Order 2019.docx` is the verbatim source for the standard
17-clause T&C block, slice S3).

## Methodology

- Corpus (local Desktop only — the Box-mirror `Purchase Order Draft`/`Executed` folders are
  empty template clones): `~/Desktop/Evergreen project/zip project documents/`
  - `04_Purchase_Orders/Filled` — **96 files** (59 PDF, 36 DOCX, 1 XLSX; most POs are a
    DOCX/PDF pair: editable source + issued copy).
  - `06_Racking_Module_POs/Filled` — **77 files** (largely Chint POs + racking supply
    agreements, exhibits, certificates).
  - `Blank/` in each — identical template copies: `Purchase Order 2019.docx` (standard
    17-clause form) and `ESS_Field_Purchase_Order_Terms_and_Conditions.docx` (expanded
    21-article field/racking form, Exhibits A–F).
- Read pass over a representative sample (~12 files across vendors/jobs/commodities) via
  direct PDF/DOCX extraction, 2026-07-09. Adjacent `05_Subcontracts` consulted for vendor
  context only.

## Findings

### 1. Filename convention (document management, NOT the contractual identity)

`{JobTag}__{CategoryOrStatusTag}__{Descriptive} - {Vendor} - Purchase Order[ status].{ext}`
— e.g. `2025.364_Steger___Roxbury`, segment 2 = origin folder/version label (`V8._Chint`,
`stale`, `canceled`, `warrenty_POs`), trailing status ∈ {`canceled`, `redacted`, `Signed`,
`redlined`, ` 2` (warranty replacement)}. Status counts: canceled 22 · stale 21 · owner "PO
Form" 6 · redacted 4 · warranty 4 · redlined 1 · Signed 1.

**COLLISION WARNING:** the folder JobTag ≠ the contractual PO job number (folder
`2025.364_Steger___Roxbury` contains POs numbered `2024.334…`; folder `2024.112_Almon…`
contains `2023.109…`). The generator must source the PO number from a job record, never from
folder names.

### 2. Three document families

- **Family A — Evergreen (ESS) standard PO**: the primary template; fixed brand header
  (`Purchase Order / 100 Spectrum Center Dr. STE 1030` → newer docs `STE 570` / `Irvine, CA.
  92618 / PH 888-303-6424`). **Build the generator around this.**
- **Family B — Community Power Group owner-issued "PO Form"** (Rexel, Eaton, Ampacity):
  different issuer/layout, PO numbers `{state-or-project}-{seq}` (`IL-004`), one-line T&C
  (invoice routing). **Out of generator scope** — Evergreen only retains copies.
- **Family C — Evergreen page-1 + vendor quote appended** (e.g. B2 Sales, 12 pp): Family-A
  form + the vendor's quotation reproduced. Supported as attach-not-generate.

### 3. Family-A field inventory (the template)

| Field | Notes / variance |
|---|---|
| Brand header | Fixed; suite drift STE 1030 (older) → STE 570 (newer) |
| DATE | `M/D/YYYY` |
| PO NUMBER | See §4 |
| SHIP TO | Project name + full site street address |
| DELIVERY CONTACT | Person + phone + email; recurring: Sam Rigney (619) 599-6536 samr@; Sheb Stephens (910) 728-1037 shebr@ (Oregon) |
| Seller / Supplier | Vendor name + 1–4-line address block |
| Line-items table | Column set VARIES — see §5 |
| Subtotal | Always |
| "Description of materials purchased" | Often "see attached / see below / see breakdown attached" |
| Sales Tax | Toggle text: `Tax Exempt` / `included` / computed `9% IL Sales Tax` / `Oregon has 0% State Tax` — driven by ship-to state |
| Scope of Work | Label; sometimes free text |
| Total | — |
| Delivery instructions | Free text (24-h coordination notes, requested windows); a duplicated appearance in extraction is a merged-text-box layout artifact, not two fields |
| Payment Terms | Free-text paragraph, highly variable (deposit schedules, invoice routing) |
| Invoice routing | `invoices@evergreenrenewables.com` cc `benf@`, `tealap@`, `tiffanym@` |
| T&C statement | `THIS PURCHASE ORDER IS SUBJECT TO THE TERMS AND CONDITIONS AS FOLLOWS:` + one regime block (§6) |
| Signature blocks | DUAL — `Purchaser – Evergreen Renewables LLC` (older blanks: `E.S.S. LLC`) and `Seller / Supplier`, each Date / NAME/TITLE / SIGNATURE |

Always present: header, DATE, PO NUMBER, SHIP TO, DELIVERY CONTACT, Seller, ≥1 line item +
Description, Subtotal/Total, T&C statement, dual signatures. Conditional: per-unit-cost column,
explicit tax line, SOW text, payment-terms paragraph, requested delivery date, supersession
clause.

### 4. PO-number scheme

`{ProjectJobNo = YYYY.NNN}.{site/phase index}.[supersede index].{version/revision}` — observed
`2025.364.1.2`, `2025.358.1.2.11` (supersedes `2025.358.1.1.11`), `2023.126.2.20`,
`2024.334.2.15`. The final segment matches the filename `V#` (V11→.11, V20→.20). Supersession
increments the supersede segment with an explicit in-body clause ("issued to supersede and
replace the previously issued PO #… rendering the prior PO null and void"). Lifecycle implied
by the corpus: `draft → stale/superseded → issued → Signed → (canceled | warranty-replacement)`.

### 5. Line-item column variants (three)

- **Default:** `[Part#/SKU] · Pieces (qty) · Per Unit Cost · Description · Subtotal Amounts`;
  tax/shipping may appear as their own rows (Chint: `SALES-TAX`, `SHIP-ACC`, `Ship-B-USA`).
- **Lump-sum / steel:** `Price Breakdown · Unit · Description · Subtotal Amounts` + Shipping
  row + Total Price.
- **Per-watt / modules (VSUN):** `Order Size Watts · Panels · Pallets · price per watt ·
  Description · (w) · Subtotal Amounts` (e.g. 7,764,880 W × $0.27 = $2,096,517.60).

### 6. T&C regimes (five — the "generic language" library)

1. **Standard 17-clause "Purchase Order 2019"** (domestic equipment/services — Also Energy,
   W.O Grubb, American Steel): preceded by a 4-item ADDITIONAL INSTRUCTIONS list ("Reference
   this PO on all invoices… Ship F.O.B. job site…"). Clauses incl. pay-when-paid Payments,
   Indemnity, 30-day cure, Liens, Optional Cancellation, Insurance ($500k WC / $1M GL / $500k
   auto, Purchaser+Owner additional insured), governing law = state where Project is located.
   **Verbatim source: `docs/references/po_samples/Purchase Order 2019.docx`** (blank still says
   "E.S.S. LLC"; filled docs say "Evergreen Renewables LLC"). → terms library default.
2. **Expanded 21-article "ESS Field PO"** + Exhibits A–F (racking/module field POs): 50%
   deposit/net-30, 2-yr Work Warranty, Serial Defect (25%), O&M manual deliverable, LDs
   0.25%/wk cap 5%, Virginia law/Fairfax venue; Purchaser = "E.S.S. LLC d/b/a Evergreen Solar
   Services, a Virginia LLC". Source: `docs/references/po_samples/
   ESS_Field_Purchase_Order_Terms_and_Conditions.docx`. → reserved id, deferred.
3. **Vendor-specific short inline terms** (Chint/CPS 8-bullet: FOB Shipping Point, CPS standard
   T&C apply, 10-yr/5-yr/2-yr warranty tiers, tax/install excluded). → `chint_vendor_v1`.
4. **Negotiated multi-page GTC attachments** (VSUN ~20 pp: Buyer = Evergreen Renewables LLC,
   8000 Towers Crescent Dr, Vienna VA; Guarantor = Coast Energy DevCo, LLC; DDP Incoterms;
   cancellation-fee schedule; delivery/payment LDs; Munich Re warranty insurance; wire
   instructions). → attach-not-generate (`negotiated_gtc`).
5. **CPG owner-form one-liner** (invoice routing only). → out of scope (Family B).

### 7. Vendor landscape (seed set for `ITS_Vendors`)

| Vendor | Commodity | Address seen | Form |
|---|---|---|---|
| Chint Power Systems (CPS) | String inverters, FlexOM | 2801 N State Hwy 78 Ste 100, Wylie TX | Evergreen (dominant, ~30 POs/folder) |
| VSUN Solar USA Inc | PV modules (per-watt) | 909 Corporate Way, Fremont CA | Evergreen + negotiated GTC |
| Also Energy | Monitoring / DAS | 5400 Airport Blvd Ste 100, Boulder CO | Evergreen |
| B2 Sales | Switchgear / distribution | 1866 N Carlsbad St, Orange CA | Evergreen + appended quote |
| American Steel | Galvanized I-beams & plates | 525 S Sequoia Pkwy, Canby OR | Evergreen |
| W.O Grubb Crane Rental | Crane / storage / logistics | 5120 Route 1, N Chesterfield VA | Evergreen |
| Ampacity, LLC (ATI) | Racking | 305 Dela Vina Ave, Monterey CA | CPG owner form |
| Rexel | Transformers, switchboard | 8428 Lee Hwy, Fairfax VA | CPG owner form |
| Eaton | MV switchgear | (via Rexel/CPG) | CPG owner form |
| Community Power Group, LLC | Owner/developer (issuer) | Washington DC | own form |
| Coast Energy DevCo, LLC | Guarantor (VSUN deals) | El Segundo CA | GTC party |

Regions track jobs: Oregon/West (2023.126 Kendall, 2024.112 Almon/Lomaside/Perrydale),
Illinois/Midwest (2025.108 Bonacci, 2025.364 Steger), PA/MD/VA East (2025.358 Keystone,
Roxbury) — and tax handling tracks region (OR 0%, IL 9%).

### 8. Entity + branding drift (generator must version this)

`E.S.S. LLC` (blank templates, field-PO T&C) vs `Evergreen Renewables LLC` (filled POs; the
D5 locked decision) vs `E.S.S. LLC d/b/a Evergreen Solar Services` (21-article regime); Irvine
STE 1030 → STE 570; a VA registered address in the VSUN GTC. Purchaser identity is a
**versioned config** (`po_materials/config/purchaser.json`), never hard-coded; entity
confirmation with Evergreen is a pre-cutover checklist line.

## Recommendations

1. Model Family A only; support Family C via attachments; exclude Family B.
2. Schema axes: Job (JobNo YYYY.NNN, sites, region, tax rule) · PO (number, date, revision,
   supersedes, status) · Vendor (name, address, contact, region, categories, default terms
   profile) · Ship-To/Delivery-Contact · Line items (3 column variants; tax/ship rows) ·
   Totals (subtotal, tax mode/amount, shipping, total) · Terms profile · Payment paragraph ·
   Dual signatures.
3. Terms library: transcribe regime 1 VERBATIM from the committed blank DOCX (S3; operator
   legal review before first live send); regime 3 as `chint_vendor_v1`; regime 4 attach-kind;
   regime 2 reserved/deferred.
4. Golden-sample gate (S8): re-key one committed sample PO in the builder and compare the
   render side-by-side before the first live send.
5. Recurring delivery contacts + the invoice cc-list belong in lookup/config, not free text.

## Appendix

Committed samples (`docs/references/po_samples/`):
- `2023.126_Oregon_-_Kendall__V4._CPS-Chint__Apricus - chint - Purchase Order.pdf` — Family A,
  default columns, tax-as-line-item, Chint short terms.
- `2023.126_Oregon_-_Kendall__V20._American_Steel__American Steel - PO Purchase Order-
  Apricus.pdf` — Family A, lump-sum/price-breakdown columns, OR 0% tax, standard 17-clause.
- `2023.126_Oregon_-_Kendall__2._Buyout__Lincoln -B2 Sales - Purchase Order.pdf` — Family C
  (quote appended).
- `Purchase Order 2019.docx` — blank standard form; **verbatim 17-clause T&C source**.
- `ESS_Field_Purchase_Order_Terms_and_Conditions.docx` — blank 21-article field form
  (Exhibits A–F), reserved/deferred regime.
Not committed (size): VSUN per-watt POs are 13–14 MB scans (`2025.358_Keystone__…VSUN…`);
per-watt variant documented in §5. Full corpus remains on the Desktop paths in Methodology.
