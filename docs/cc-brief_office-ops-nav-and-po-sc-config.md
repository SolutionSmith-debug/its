---
type: brief
date: 2026-07-12
status: active
workstream: safety_portal
tags: [brief, next-session, spa, nav, office-operations, po-sc-config, subcontracts, config-editor]
---

# Next-session brief — "Office Operations" nav section + "PO/SC Configuration" (subcontract terms/legalese editor)

Two SPA features, both mirror existing surfaces, both requested by the operator 2026-07-12. **Do Feature A
first** (mechanical, low-risk), then Feature B. Neither needs a new Worker route (the backend is ready).

## Context — where things stand (verify against live HEAD first, HOUSE_REFLEXES §1)

The subcontract generator is **100% built + DEPLOYED LIVE** to the `seths@evergreenmirror.com` sandbox
tenant (merged four-part #529–#540; the fold ran 2026-07-12). The `subcontract_poll` daemon is loaded, its
three gates are `true`, and it is syncing (`down=24` subcontractors → D1). Sheet ids are flipped + committed
on `main` (sandbox values: `WORKSPACE_SUBCONTRACTS=6073264716965764`, `SHEET_ITS_SUBCONTRACTORS=2107762140991364`,
`SHEET_SUBCONTRACT_LOG=1195034345951108`, `SHEET_SUBCONTRACT_PENDING_REVIEW=7950433787006852`). The
`standard_subcontract` v1 terms are `legal_review: cleared`. `openpyxl` is installed in the live `~/its/.venv`.

Both features below are **SPA-only** — the config-editor backend already supports the `subcontracts`
workstream (see Feature B), and every route the pages call already exists.

---

## Feature A — "Office Operations" home-nav section (mechanical; do FIRST)

Reorganize `safety_portal/src/pages/HomePage.tsx`. Today there are three sections in `HOME_SECTIONS`
(`forms` "Daily forms", `field` "Field operations", `admin` "Administration"); each `HOME_CARDS` entry
carries a `section` field, and **array order = display order** within a section's two-wide grid
(top-to-bottom, left-to-right).

**Add a new section `office` (heading "Office operations") BETWEEN `field` and `admin`.** Move six cards
from `admin` → `office`, in **exactly this order** (reorder them in the `HOME_CARDS` array):

1. Purchase Orders
2. Subcontracts
3. Checklists
4. Materials Catalog
5. Vendors
6. Subcontractors

**Administration keeps** (in this order): **PO/SC Configuration** (renamed — see below), Forms, Accounts.

Exact edits:
- `HomeSectionKey` type (and `HomeNav` if it enumerates sections) — add `"office"`.
- `HOME_SECTIONS` — insert `{ key: "office", heading: "Office operations" }` between the `field` and
  `admin` entries.
- `HOME_CARDS` — change the six cards' `section: "admin"` → `section: "office"`, and reorder the array so
  they appear in the order above; leave PO/SC Configuration + Forms + Accounts as `section: "admin"`.
- Rename the config card `title: "PO Configuration"` → `title: "PO/SC Configuration"`.
- **Styling: NO change** — the operator explicitly wants the same neat button size + the two-wide
  side-by-side grid. This is a section-assignment + reorder change only; do not touch the card component
  or the `dash-grid` layout.
- Test (`src/pages/__tests__/HomePage.test.tsx`, mirror existing): assert the six cards render under
  "Office operations", the three under "Administration", the section order (field → office → admin), and
  cap-gating unchanged (a submitter with no admin caps sees an empty office/admin section = nothing).

Router note: this needs **no** `router.ts` change — the underlying view keys are unchanged; only the
home-card grouping + one card title move.

---

## Feature B — "PO/SC Configuration": the subcontract terms/legalese editor

Replace the **existing "coming soon" placeholder** in `PoConfigPage.tsx` (lines ~862–871: the disabled
`"Edit subcontracts (coming soon)"` button, comment *"provisioned placeholder — the SAME editor serves it
later"*) with the real subcontract config editor, and rename the page to **"PO/SC Configuration"**.

**The backend is fully ready** — `safety_portal/worker/config.ts` `CONFIG_REGISTRY.subcontracts` is LIVE
(not a placeholder): `cap: "cap.subcontracts.manage"`, artifacts `contractor` (json — the Evergreen prime
identity), `payment_terms` (json — §2.5 retention defaults), `terms` (the 27-article body library). All
`CONFIG_OPS` (`edit` / `add_version` / `set_current` / `create_profile`) work for it, and the
workstream-aware Mac actuator (`po_materials/config_apply.py` / `config_actuator.py`, SC-S2) commits →
CI → deploys subcontract config edits. The serve routes exist from SC-S3c: `GET /api/subcontracts/config`
(contractor + payment_terms), `GET /api/subcontracts/terms` (+ `/:id/text`, `/:id/versions`).

The UI work (in `PoConfigPage.tsx`, mirror the existing PO purchaser/tax/terms sections but with
`workstream: "subcontracts"`):
- Rename the page `<h1>`/title + the module doc comment PO → "PO/SC" (it edits both workstreams now).
- **Contractor identity** (json) — an editable section like the PO Purchaser block: fields from
  `GET /api/subcontracts/config` `.contractor` (entity, address_lines, phone, signature_entity,
  prime_contractor_default) → `submitConfigEdit({ workstream: "subcontracts", artifact_key: "contractor",
  op: "edit", payload })`.
- **Payment terms** (json) — the §2.5 retention defaults (retainage_bp, retainage_reduced_bp,
  retainage_reduction_at_pct) → `artifact_key: "payment_terms"`, `op: "edit"`.
- **Subcontract terms library** (terms) — mirror the PO terms section verbatim: list profiles from
  `GET /api/subcontracts/terms`, edit-text pre-fill via `GET /api/subcontracts/terms/:id/text`,
  `add_version` / `set_current` (make-current — the **Layer-A legal gate**, sets `legal_review: cleared`) /
  `create_profile`, all via `submitConfigEdit({ workstream: "subcontracts", artifact_key: "terms", op,
  payload })`. The status monitor at the bottom of the page already covers all workstreams (it queries the
  config_requests queue generically) — confirm it shows subcontracts rows.
- **"Legalese" — the Exhibit A templates (design decision for the session):** the operator said "terms AND
  legalese". The Exhibit A skeleton + per-trade Art II templates live in `subcontracts/exhibit/` (sha-pinned,
  NOT currently a `CONFIG_REGISTRY` artifact). Options: (a) v1 = terms + contractor + payment_terms only,
  defer Exhibit A editing; (b) add an `exhibit` artifact to the subcontracts registry + a serve/edit route +
  an editor block (bigger — a new artifact kind, since it's a manifest + skeleton + N trade files). Recommend
  (a) for this slice, note (b) as a follow-on. Confirm with the operator.
- Update `src/pages/__tests__/PoConfigPage.test.tsx` — the test at ~line 289 currently asserts the
  placeholder is "present + disabled"; replace with real-editor assertions (subcontract terms list renders,
  a make-current queues a `subcontracts`/`terms`/`set_current` config edit). HOUSE_REFLEXES §5: assert
  shape/round-trip, NEVER pin live config content or an absolute version.

## Discipline
- SPA-only; reuse the existing config-editor components + `submitConfigEdit` (already generic over
  `workstream`) — do NOT clone the PO logic, parameterize it (§14). `src/lib/subcontracts.ts` already has
  `fetchSubcontractConfig` / `fetchTerms` / `fetchTermsText` / `fetchTermsVersions`.
- The make-current → `legal_review: cleared` on subcontract terms is a **§50 privileged code-actuation with
  a legal gate** — verify the workstream-aware actuator handles a subcontracts `terms`/`set_current` end to
  end (it did for PO; SC-S2 made it workstream-aware). A live-smoke (edit a subcontract terms version →
  make-current → the actuator commits + deploys, `legal_review` flips) is DoD before trusting it.
- Adversarial review: a general-purpose correctness reviewer on the config-editor wiring (route/body-shape
  match, cap-gating on `cap.subcontracts.manage`, the make-current legal-gate flow). No new Worker route ⇒
  likely no `portal-worker-security-reviewer` pass needed, but confirm no `config.ts` change is required.
- Gate: `npm run typecheck` (3 tsconfigs) + SPA vitest + `vite build`; then the config-editor live-smoke.

## Files
- `safety_portal/src/pages/HomePage.tsx` (Feature A + the card rename)
- `safety_portal/src/pages/PoConfigPage.tsx` (Feature B — replace the placeholder; rename)
- `safety_portal/src/pages/__tests__/HomePage.test.tsx`, `.../PoConfigPage.test.tsx`
- `safety_portal/src/lib/subcontracts.ts` (the fetchers exist; add a subcontract-config submit only if the
  generic `submitConfigEdit` from `po.ts`/`config` isn't already importable — it is: reuse it)
- VERIFY only (no change expected): `safety_portal/worker/config.ts` (`subcontracts` registry is already live)

## Related
- `project_subcontracts-workflow` memory (the generator is 100% built + deployed).
- `project_config-editor-build` memory + `decision_phase2-form-editor` (the §50 config-editor rail this rides).
- The 2026-07-12 session log (`docs/session_logs/2026-07-12_subcontract-generator-complete-*.md`).
