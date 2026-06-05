# Safety Portal form definitions

One JSON file per form/variant here is the **single source of truth** for that
form, consumed by **both** renderers so they cannot drift:

- the **TS portal display runtime** (`safety_portal/src/`, Phase 4 PR 2) imports the
  JSON and renders the on-screen fillable form;
- the **Python reportlab PDF renderer** (`safety_reports/`, Phase 4 PR 3 / invoked by
  Phase 5 intake) reads the *same* JSON and renders the render-parity PDF.

Every field/section is transcribed **faithfully from the source PDF** in
`safety_portal/reference_forms/` — no invention. Obvious source typos are noted but
the digital label is corrected (e.g. JHA "Crem Members" → "Crew Members").

`meta-schema.json` is the contract (JSON Schema, Draft 2020-12). `tests/test_form_definitions.py`
validates every definition against it (run on every push).

## Catalog ↔ definition

`ITS_Forms_Catalog` (Smartsheet) drives the dropdowns: a **parent** row per form
type (`Parent Form Code = ""`), plus a **variant** row per child (`Parent Form Code`
= the parent's `Form Code`, `Variant Label` = the 3rd-picklist label). A no-variant
parent's `Form Code` IS the definition (`jha-v1`); a variant parent's `Form Code` is
the parent key (`equipment-preinspection`) and the variants carry the definition
codes. The runtime: pick a parent → if variant rows exist, show the 3rd picklist →
load the chosen `Form Code`'s `<form_code>.json`.

## Envelope vs form fields

Every submission carries envelope fields the runtime supplies — `job` (from the job
dropdown), `work_date` (PM-set; the Sat→Fri bucketing key; **no submission timestamp
is shown** — Q4), `form_code`/`variant`, and the client-generated submission UUID
(+ amend flag). Definition fields with the **reserved keys `work_date` and `job`** are
bound to the envelope (the runtime pre-fills + the renderer places them in the source
position); all other field keys are form-specific.

## Section types (see meta-schema.json)

| `type` | use |
|---|---|
| `header` | a group of `fields` (input: text/textarea/date/time/number/select/signature) |
| `repeating_table` | add-row table (JHA hazards, Visitor log, HSS&E corrective actions) |
| `signature_table` | multi-row capture; exactly one column is `input: signature` (SVG path data) |
| `checklist` | grouped items; each group has a `scale` (e.g. `["OK","NO","N/A"]`) + optional per-item `kind` (`numeric`/`circle_one`/`text`), `scale` override, `comment` |
| `freeform` | a labeled textarea |
| `static_text` | mandatory/legal text rendered verbatim, non-editable (footers, lock/tag-out) |
| `content_blocks` | static topic content (Toolbox Talk body), non-editable |

## Adding / retiring / updating a form

- **New topic / equipment variant** = a new `<form_code>.json` here + a new variant
  row in `ITS_Forms_Catalog` (Parent Form Code = the parent). Not a new top-level form.
- **Retire** = set the catalog row `Active = Retired/Inactive`.
- **Update** = edit the `<form_code>.json` (bump `version`) — both renderers pick it up.
- See `docs/runbooks/safety_portal_forms.md` (the §43 operator runbook).
