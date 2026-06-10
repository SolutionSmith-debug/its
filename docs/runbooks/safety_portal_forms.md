---
type: operations
date: 2026-06-05
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, smartsheet, safety-portal, tier-2, phase-4, forms]
---

# Runbook — Safety Portal forms (add / retire / update) (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator**. The §42 code-reader
rationale lives in `safety_portal/forms/README.md` (the definition contract) and
`safety_portal/forms/meta-schema.json`.

## How forms work (one paragraph)

Each form is **one JSON file** in `safety_portal/forms/` (the field/section layout,
transcribed faithfully from the source PDF in `safety_portal/reference_forms/`). The
**`ITS_Forms_Catalog`** Smartsheet sheet drives the portal dropdowns: a **parent**
row per form type, plus **variant** rows for the types that have variants (Equipment,
Toolbox Talk). The portal shows the parents; if the picked parent has variants, a 3rd
picklist appears. Only **Active** rows appear.

| Catalog column | Meaning |
|---|---|
| Form Name | Display label |
| Form Code | The definition key (the `<form_code>.json` file). For a *variant* parent (Equipment / Toolbox), the parent's Form Code is the parent key, not a definition. |
| **Parent Form Code** | Empty on a parent row; the parent's Form Code on a variant row. |
| **Variant Label** | The 3rd-picklist label (e.g. "Skid Steer"); empty on parent / no-variant forms. |
| Active | Active / Inactive / Archived — only **Active** appears in the portal. |
| Display Order | Ascending sort. |

## Tasks (low-class — Successor-Operator can do)

### Retire a form or variant
- Set the catalog row's **Active = Inactive** (temporarily off) or **Archived**
  (permanently). It leaves the portal dropdown on the next sync. Never delete the row.

### Add a new Toolbox topic or Equipment variant (needs a definition file → escalate the code part)
1. The **definition file** (`safety_portal/forms/<new-code>.json`) is a **code change**
   — escalate to Seth (or a Claude Code session) to author it from the source PDF.
2. Once the file exists, **you** add the catalog row: Form Name, **Form Code** =
   the new file's `form_code`, **Parent Form Code** = the parent (e.g. `toolbox-talk`),
   **Variant Label** = the picklist label, Active = Active. It appears next sync.

### Update a form's wording/fields
- Editing a `<form_code>.json` is a **code change** (escalate). After it lands, both
  the on-screen form and the PDF update automatically (single source of truth).

### Refresh the manual-fallback blank-form archive (after any `forms/*.json` change)
- Re-run `python scripts/generate_form_archive.py --upload` to regenerate the blank
  fillable PDFs + cover sheet and re-upload them to Box `00_Form_Archive`
  (version-on-conflict, so it updates in place — no duplicates). Without `--upload` it
  renders locally only. (Live upload is operator-gated — Tier-3/Seth.)

### Publish rejected — "required content missing" (legal floor, Op Stds §43)

**Symptom.** An admin's Publish fails in the Status Monitor (or the editor) with a reason
starting `Rejected: required content missing: …` — e.g. "must contain a 'signature_table'
section", "the mandatory legal/footer line \"REVIEW AND REVISE THE PLAN\" is absent", or
"needs at least 1 signature input". The form does **not** go live.

**Why.** Each form type carries a legal floor in `safety_portal/required-content.json` (a JHA
must keep its review-and-revise footer; an equipment form its lock/tag-out line; most forms a
signature mechanism). An edit that drops one is rejected at BOTH layers — the Worker enqueue
gate and the Mac daemon's authoritative re-check — by design, so a content edit can never ship
a legally-broken form.

**Low-class repair (Successor-Operator can do).** In the editor, re-open the failed publish
("Edit & re-publish"), restore the section or line the reason names, and re-publish. No code,
no config.

**Escalate to Seth (Tier 3) when** the requirement itself looks wrong — a form legitimately
should not carry the required content, or a brand-new form type needs its own entry. **Editing
`safety_portal/required-content.json` is the legal floor: a doctrine-adjacent,
high-capability-class change — never edit it at Tier 2.**

## Escalate to Seth (Tier 3) when

- Authoring or editing any `safety_portal/forms/*.json` (code).
- A form renders wrong, a PDF doesn't match the paper form, or a definition fails the
  validation check in CI.
- Anything touching the portal deploy, the renderer, secrets, or the send path.
