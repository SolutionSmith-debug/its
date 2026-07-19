# Vendor-estimate templates (ADR-0004 E4 — Tier-1 template grammar)

Data-driven per-vendor layouts for the deterministic extraction tier
(`po_materials/estimate_parse.parse_with_template`). **Adding a vendor = adding a
YAML file here** — templates are pure DATA (`yaml.safe_load`), never exec'd
(ADR-0004 decision 1). A malformed file is skipped with a logged WARNING; it never
hides the other templates.

How a template is applied (see `estimate_parse.py` for the code contract):

1. `load_vendor_templates()` compiles every `*.yaml` in this directory (sorted).
2. A template CLAIMS a document only when **all** `match` regexes hit the page-1
   text. First claiming template wins (ladder wiring in `estimate_poll`).
3. Line rows are parsed per text line via `lines.pattern`; rows the pattern does
   not claim are checked against `lines.skip` (dropped) then
   `lines.section_pattern` (become the running section label). **A row without a
   parseable qty + unit price is never a $0 line** — the pattern's `qty` /
   `unit_price` groups are mandatory for a row to become a line item.
4. `check_math` re-verifies every line (`qty / uom_divisor × unit_cost ==
   extended`, ECMA half-up `_js_round`) and the doc totals. Failures set
   `math_flags` + `needs_review` — they never block; the human disposition accept
   is the fidelity control (decision 3).
5. Zero parsed lines ⇒ the template returns None and the ladder falls through
   (generic table → Tier-2 local LLM → Tier-3 manual).

## Grammar

```yaml
name: platt                 # required — template id (filename convention: <name>.yaml)
vendor_name: Platt          # required — the vendor_name stamped on results
doc_type: quote             # optional — quote|estimate|proposal (default quote)

match:                      # required — regexes; ALL must hit page-1 text (re.I|re.M)
  - 'PLATT'

fields:                     # optional header-field extractors, first capture group wins,
  quote_number: '...'       # searched over the FULL document text
  revision_label: '...'
  quote_date: '...'         # raw capture normalized via date_formats

date_formats:               # optional strptime formats for quote_date
  - '%m/%d/%Y'              # (default: %m/%d/%Y, %m/%d/%y, %Y-%m-%d)

lines:
  pattern: '...'            # named groups: qty + unit_price REQUIRED for a row to
                            # become a line; optional: description, part_number,
                            # uom, extended, line_no
  section_pattern: '...'    # optional — group 'section' (or group 1) sets the
                            # running section label for subsequent lines
  skip:                     # optional — rows matching any are dropped (stock
    - '^\s*MFR:'            # notes, continuation lines)

uom_divisors:               # optional — per-UOM extended-price divisor:
  M: 1000                   # extended = qty / divisor × unit_price.
                            # 'M' = per-thousand (distributor wire pricing).
                            # Unlisted UOMs divide by 1.

totals:                     # optional doc-total extractors (first capture group,
  subtotal: '...'           # money like '1,234.56'); allowed keys:
  tax: '...'                # subtotal, tax, freight, misc, grand_total
  grand_total: '...'
```

All regexes compile with `re.I | re.M`. Money captures go through
`estimate_parse.to_cents` (Decimal ROUND_HALF_UP quantize-to-cents); qty accepts
thousands commas.

## Discipline

- Synthetic replicas of each vendor layout live in `tests/test_estimate_parse.py`
  — **no real vendor bytes in the repo**. Update the replica when you tune a
  template.
- The offline corpus eval (`scripts/eval_estimate_ladder.py`, slice E6) is the
  acceptance gate for template changes against the real corpus on the operator's
  machine.
