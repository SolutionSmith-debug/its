---
type: session_log
date: 2026-06-05
status: closed
related_prs: [164]
workstream: safety_portal
tags: [safety-portal, forms, meta-schema, form-definitions, migration, jsonschema, phase4]
---

# Session log — Safety Portal Phase 4 PR 1: forms foundation

Built the form-definitions foundation for the Safety Portal — the single contract consumed
by both renderers (TypeScript display runtime and Python reportlab PDF). Eleven form
definitions faithfully transcribed from the 10 reference PDFs, a JSON-Schema meta-schema
as the single source of truth, a live ITS_Forms_Catalog migration to a parent+variant
model, a full jsonschema-backed test suite (49 tests), and a §43 successor runbook.
Landed as PR #164.

## Commits / PRs landed

- **PR #164 — feat(safety-portal): Phase 4 PR 1 — form-definitions foundation** —
  squash `940999e` on main. New files: `safety_portal/forms/meta-schema.json`,
  eleven `safety_portal/forms/*.json` definitions, `safety_portal/forms/README.md`,
  `scripts/migrations/extend_its_forms_catalog_parent_variant.py`,
  `tests/test_form_definitions.py`, `docs/runbooks/safety_portal_forms.md`. Modified:
  `pyproject.toml` (+jsonschema, +types-jsonschema). Details per artifact:

  - **`safety_portal/forms/meta-schema.json`** (NEW): JSON-Schema (Draft 2020-12)
    contract — the single source of truth for form definitions, consumed by BOTH the
    TypeScript display renderer (Phase 4 PR 2) and the Python reportlab renderer
    (Phase 4 PR 3) so they cannot drift from each other. Defines seven section types:
    `header` / `repeating_table` / `signature_table` (one column with `input:signature`
    → SVG) / `checklist` (grouped, per-item `kind` of `numeric` / `circle_one` / `text`,
    per-item `scale` override, `comment`) / `freeform` / `static_text` (legal/mandatory,
    non-editable) / `content_blocks`. Envelope keys `work_date` and `job` are
    runtime-bound (not in definitions); all other structure is static.

  - **`safety_portal/forms/*.json` — 11 form definitions** (NEW): Faithfully
    transcribed from the 10 reference PDFs; no invented fields. Obvious typos
    corrected (e.g., JHA "Crem Members" → "Crew Members"). Legal text baked in
    verbatim — JHA footer "IF CONDITIONS CHANGE…REVIEW AND REVISE THE PLAN.";
    equipment forms "ALWAYS lock/tag-out unsafe equipment." Forms:

    - `jha-v1` — Job Hazard Analysis
    - `equipment-telehandler-v1` — 64-item tri-state (OK / NO / N/A) across 4 groups
    - `equipment-skid-steer-v1` — binary (OK / NOT-OK) + fuel circle-one + numeric hours
    - `visitor-sign-in-v1` — visitor sign-in sheet
    - `hsse-work-observation-v1` — 11 assessment categories tri-state; Section 2 mixed
      Yes/No and Yes/No/N/A per source (per-item `scale` override); Section 3;
      corrective-action repeating table
    - `toolbox-talk-heat-illness-v1`, `toolbox-talk-silica-v1`,
      `toolbox-talk-ppe-v1`, `toolbox-talk-fall-protection-v1`,
      `toolbox-talk-struck-by-v1` — five toolbox talk topics with verbatim
      `content_blocks` content + crew sign-in signature table

  - **`safety_portal/forms/README.md`** (NEW): Contract documentation — section types
    reference, envelope-key convention (`work_date` + `job` are runtime-bound),
    catalog↔definition mapping, and variant naming convention.

  - **`scripts/migrations/extend_its_forms_catalog_parent_variant.py`** (NEW): Live
    migration. Adds `Parent Form Code` and `Variant Label` TEXT columns to
    ITS_Forms_Catalog; reconciles to 5 parent rows + 7 variant rows via idempotent
    upsert + prune via REST; drops the old flat seed including `daily-site-safety-v1`.
    Applied to sandbox this session and verified — ITS_Forms_Catalog shows 5 parents +
    7 variants.

  - **`tests/test_form_definitions.py`** (NEW): Validates all 11 definitions against
    the meta-schema via `jsonschema` + per-form invariants (signature table presence,
    legal text, field count, section structure). 49 tests, all passing.

  - **`docs/runbooks/safety_portal_forms.md`** (NEW): §43 successor runbook —
    procedures to add a new form, retire a form, and update an existing definition,
    with the escalate-to-Seth boundary for schema changes.

  - **`pyproject.toml`** (MODIFIED): +`jsonschema` (runtime deps) +`types-jsonschema`
    (dev deps).

## CI runs / four-part verify

PR #164 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-05T15:25:49Z
- mergeCommit: 940999eeef88e7786884edc46768f4ba8c1c3ab2
- main CI on merge commit: SUCCESS (ci + Push on main + "Graph Update: pip" all success)

Per-session local validation gate before merge:

- pytest: 49 passed (form-definition suite) / remaining suite green
- mypy: 0 errors / 166 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

`ops-stds-enforcer` pre-merge: CLEAN — zero findings. Additive changes only, no send
path touched, one-shot migration, §30 / §14 / §41 / §42 all clear.

## How the definitions were built

Two parallel PDF-analysis workflows (one agent thread per form type) extracted the exact
field / section / checklist / signature / legal-text specifications from the 10 source
PDFs. Structural definitions were then authored from those specs. The five Toolbox Talk
topic content blocks were extracted via a second content-extraction workflow. The
meta-schema gained a per-item `scale` override mid-build to faithfully represent
HSS&E Section 2's mixed Yes/No vs. Yes/No/N/A questions in source order — a structural
requirement not visible until the actual PDF fields were inspected column by column.

## Decisions made during session

1. **Canonical source = the 10 reference PDFs; no invention.** Alternative considered:
   synthesize "reasonable" fields for form types not fully legible in the PDFs. Rejected:
   the forms are legal documents (OSHA-adjacent); invented fields would create a mismatch
   between the portal form and the paper form used in the field. Every field in every
   definition is traceable to a cell on the source PDF.

2. **V1 catalog: Daily Site Safety OUT; Visitor Sign-In + HSS&E Work Observation IN.**
   Rationale per brief §0: the operator's decision was that the Daily Site Safety form
   does not meet the bar for the v1 catalog. Visitor Sign-In and HSS&E Work Observation
   replace it. The prior flat seed row (`daily-site-safety-v1`) is pruned by the
   migration.

3. **Parent + variant model (Option B for the catalog structure).** Alternatives
   considered: flat catalog (one row per form, no parent/variant distinction); or
   variant as a free-text tag on a flat row. Rejected: Equipment forms (×2 variants)
   and Toolbox Talks (×5 variants) share structure and a single parent select on the
   portal. The parent+variant model with a third picklist column cleanly supports that
   — a single portal dropdown selects the parent, a conditional second dropdown selects
   the variant. Flat catalog would require client-side disambiguation logic.

4. **Render = Option B (one definition consumed by two renderers).** The definition
   is the contract; the TypeScript display runtime (PR 2) and the Python reportlab
   renderer (PR 3) both parse the same JSON. Alternative considered: Option A (two
   separate definition stores, one per renderer). Rejected: two stores drift. A change
   to a form's fields would require editing two files in two different formats, with
   no enforcement of parity. The meta-schema + shared definition file makes drift
   impossible — if a section type is not in the schema, neither renderer can reference
   it.

5. **Per-item `scale` override added to the meta-schema mid-build.** The initial
   schema modeled all checklist items within a section as sharing the section-level
   scale (Yes/No, Yes/No/N/A, tri-state). HSS&E Section 2 mixes Yes/No and Yes/No/N/A
   items in source order — they cannot be split into separate sections without losing
   the original visual grouping. The override allows a specific item to declare its own
   scale when it differs from the section default. Alternative considered: split Section
   2 into two sections by scale type. Rejected: this would reorder items relative to
   the source PDF, breaking field-PM familiarity.

## What was NOT touched

- Invariant 1 (External Send Gate) mechanics unchanged. No generation or send scripts
  modified.
- Invariant 2 (Adversarial Input Handling) mechanics unchanged. Form definitions are
  static JSON authored by the developer, not external input.
- `intake.py`, `weekly_generate.py`, `weekly_send.py` not touched — the form renderer
  integration is Phase 5 scope.
- No launchd plists added or modified.
- No doctrine or blueprint files touched.
- TypeScript display runtime not built — Phase 4 PR 2.
- Python reportlab renderer not built — Phase 4 PR 3.
- `lint_doc_conventions.py` workstream set not updated (pre-existing gap; carried
  forward).

## Open items handed off

- **Phase 4 PR 2 — TypeScript display runtime** (`safety_portal/src/`): generic
  definition-driven renderer; 3 archetypes (checklist, repeating table, signature
  table); form-type and variant dropdowns; multi-row SVG signatures; amend prefill;
  structured-data emit; replaces the hard-coded JHA stub.

- **Phase 4 PR 3 — Python reportlab renderer**: render-parity with the TS display +
  legal-invariant enforcement + SVG signature embedding; per-form parity tests; invoked
  by Phase 5 intake (Option B).

- **Job-specific JHA variant content:** The parent+variant mechanism is built and the
  catalog supports variants. Specific JHA variants (e.g., per trade or job type) are
  added later as additional variant rows — no code change required.

- **Box retention floor confirmation:** Verify Box retention policy against OSHA and
  applicable state minimums before Phase 5 go-live. Carried item.

- **Operator UI steps (carried from PR #160 / PR #162):**
  - Add the `Job ID` AUTO_NUMBER column in the Smartsheet UI on ITS_Active_Jobs
    (prefix `JOB-`, 4-digit fill, start 1).
  - Create the "New Job" Smartsheet form on ITS_Active_Jobs.

- **D1 dropdown sync (A.1.4):** Deferred to Phase 2 deploy session (portal D1 does
  not yet exist). Carried from PR #160.

- **Fill 6 Address cells in ITS_Active_Jobs** — PM fills manually; carried from the
  2026-06-04 session.

- **Blueprint pushes and Phase 2 deploy:** Carried forward. Still pending
  CLOUDFLARE_API_TOKEN provisioning session.

- **Phase 5 (submission pipeline + compile + send):** Separate session, brief Part B.
  Includes HMAC-verified portal-marker branch in `intake.py`, PDF rendering via PR 3,
  and `weekly_generate` / `weekly_send` integration.

## Cross-references

- Immediately prior safety_portal session log (Phase 3 contacts amendment):
  [`2026-06-05_safety-portal-phase3-contacts-amendment.md`](2026-06-05_safety-portal-phase3-contacts-amendment.md)
- Prior safety_portal session log (Phase 3 job model):
  [`2026-06-05_safety-portal-phase3-job-model.md`](2026-06-05_safety-portal-phase3-job-model.md)
- Prior safety_portal session log (Phase 2 Cloudflare scaffold):
  [`2026-06-04_safety-portal-phase2-cloudflare-scaffold.md`](2026-06-04_safety-portal-phase2-cloudflare-scaffold.md)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD; runbook
  `docs/runbooks/safety_portal_forms.md` ships as part of definition-of-done)
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `safety_portal/forms/meta-schema.json` — the authoritative contract for all form
  definitions; both renderers validate against it
- `scripts/migrations/extend_its_forms_catalog_parent_variant.py` — live migration;
  applied and verified against sandbox ITS_Forms_Catalog
