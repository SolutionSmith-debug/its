---
type: session_log
date: 2026-07-11
status: active
related_prs: [532, 533, 534]
workstream: subcontracts
tags: [subcontracts, generation, deterministic, po-mirror, adr-0003, docx, group-by-state, bidirectional, overnight, autonomous]
---

# Subcontract generation — SC-S3a render core + SC-S3b editable .docx/.xlsx + group-by-state

## Purpose

Continue the deterministic subcontract-generation workstream (ADR-0003) past the SC-S1 foundation:
build the render core (SC-S3a) and the editable-document render (SC-S3b), and act on two operator
requests — **group subcontractors by STATE** (not region) and make the subcontractor database
**bidirectional** with its Smartsheet SoR. All ships dark. **NO AI in the generation path** (standing
operator directive).

## Landed (all four-part verified: MERGED · mergedAt · mergeCommit · main-CI SUCCESS)

### #532 — SC-S3a deterministic render core (money / governing-law / Layer-A gate) — `8e2a7f34`
- `subcontracts/money.py` (`cents_to_words` US-legal phrasing pinned to real corpus specimens; the
  SOV-sums-to-price guard), `governing_law.py` (job-site-state → jurisdiction, **fails closed**),
  `terms.py` (the Layer-A legal-review gate, faithful `po_materials/terms.py` fork), and
  `subcontract_generate.py` (`render_body_text`: SOV guard → Layer-A gate → strict token fill).
- **ops-stds BLOCK → fixed in-session:** `sov_mismatches` *raised* instead of fencing on a malformed
  `qty` (would crash the daemon cycle), and `sov_extended_cents` used `floor(x+0.5)` — the exact
  float-rounding divergence `po_generate._js_round` exists to avoid (breaks JS/Python HMAC agreement).
  Fixed to a fence-not-raise + a verbatim `_js_round` copy; re-review **CONFIRMED-RESOLVED** (byte-identical
  across 200k values; the bool-guard even closed a latent quiet-wrong-answer bug).

### #533 — subcontractors grouped by STATE + bidirectional column — `313bb251`
- Operator: *"group subcontractors by state like vendors by region"* + *"the subcontractor database
  should be bidirectional like the vendor DB."*
- Rationale: a subcontract's governing law is **per-state**, so the registry groups by the 2-letter USPS
  STATE (= the subcontract's `governing_law_state`), not the coarse vendor region.
- region→state across five surfaces (set-equal, test-pinned): migration **0052** (a **table-rebuild** —
  the repo has no `DROP COLUMN` precedent and D1's support is unreliable; matches the 0032/0046/0048
  idiom), the sheet builder `STATE_OPTIONS` (50 states + DC), `picklist_validation._SUBCONTRACTOR_STATE_VALUES`,
  `subcontracts/subcontractors.py` `COL_STATE`, and the roster+seeder.
- **Bidirectional = the existing §51 machinery** (`build_down_sync_payload` / `upsert_subcontractor` +
  the D1 `origin/sync_state/mirror_version/mirrored_version` watermark, built in SC-S1); this PR only
  renames the mirrored column, so `state` now flows both ways (a state edit on either the Smartsheet SoR
  or the portal reflects on the other). The **sync-runner daemon** that pumps it is SC-S3c (below).
- Roster/seeder states are **project-inferred** (West→OR, Midwest→IL, East→MD; 4 Seasons blank),
  flagged for operator confirmation. A **3-way parity test** locks builder == picklist ==
  `governing_law._STATE_NAMES` (a State the resolver rejects would fence every subcontract for that firm;
  proven RED on a simulated drop). Migration reviewed clean (portal-worker-security-reviewer); README
  activation punch-list backfilled 0047–0052.

### #534 — SC-S3b editable .docx / .xlsx render (operator: NOT PDF) — `eb8ccd9b`
- Operator: the deliverables must be **editable Office files** (clauses / SOV line values hand-adjustable),
  not flat PDF. New `subcontracts/subcontract_docx.py`: `render_subcontract_docx` (body → `.docx`, gates
  first), `render_sov_xlsx` (Annex C SOV → `.xlsx`), `render_package`. Capability-gated (enrolled in
  GATED_SCRIPTS); `openpyxl` added (`python-docx` already present).
- **ops-stds BLOCK → fixed in-session:** the "deterministic" claim was FALSE for the `.xlsx` — openpyxl
  stamps wall-clock into BOTH each ZIP member's local-header `date_time` AND `docProps/core.xml`'s
  `<dcterms:modified>` (overwriting the pinned `wb.properties.modified`). This breaks the §47 idempotent
  Box-filing SC-S3c will rely on. Proven: 5 renders with delays → 4 distinct hashes. `_normalize_ooxml_clock`
  now pins both sources (incl. a `core.xml` content rewrite) on BOTH formats → 1 hash. Test strengthened
  to byte-identity **plus** a direct `dcterms:modified == pinned-date` assertion (equality alone
  false-passes within one second). Re-review **CONFIRMED-RESOLVED** (reconstructed the pre-fix
  non-determinism; 123 subcontract tests green).

## Gate (final, on `eb8ccd9`)
- pytest: full subcontract suite + capability gating green (123 subcontract-scoped + 27 gating)
- mypy: clean on `subcontracts/`
- ruff: clean
- main-branch CI on merge commits: #532 SUCCESS · #533 SUCCESS · #534 SUCCESS (all four-part clean)

## NOT built this session — remaining SC-S3/roadmap (for operator fold + a focused next session)

**Why not overnight:** the subcontract Smartsheet sheets are all dark (`sheet_ids` = 0). Per the operator's
standing *mandatory-live-smoke-before-merge* rule, the SC-S3c daemon+worker (a ~2,800-line trust boundary
over money + legal + HMAC + D1 writes) can't be smoked until the sheets are built+flipped at fold — so it's
smoke-and-adjust fold work, not an overnight auto-land. The SC-S3b determinism BLOCK is a concrete
illustration of how subtle that trust-boundary correctness is.

### Operator fold sequence (subcontract foundation, ships DARK)
1. `git -C ~/its pull origin main`
2. `wrangler d1 migrations apply its-safety-portal-db --remote` (0049–0052) + `npm run deploy`
3. `build_subcontracts_workspace.py` → flip `WORKSPACE_SUBCONTRACTS`
4. `build_its_subcontractors_sheet.py` → flip `SHEET_ITS_SUBCONTRACTORS` (now a **State** column)
5. `build_subcontract_log_sheet.py` → flip `SHEET_SUBCONTRACT_LOG`
6. `build_subcontract_pending_review_sheet.py` → flip `SHEET_SUBCONTRACT_PENDING_REVIEW`
7. `seed_its_subcontractors.py` → seed 24 firms (state project-inferred — confirm)
8. Make-current the `standard_subcontract` v1 terms (its `legal_review` is **pending** — the Layer-A gate
   fences every live render until the operator legally attests it via the config editor's make-current)

### SC-S3c (the smokeable capstone + bidirectional sync runner) — faithful PO mirror, leave OPEN for operator smoke
- `safety_portal/worker/subcontract.ts` ← `worker/po.ts`: HMAC domain **sub:v1**; subcontractor CRUD +
  `/internal/subcontractors-sync` (down) + `/internal/subcontractors-dirty` (up) = the bidirectional
  runner's worker half; subcontract draft/generate (SOV canonical JSON + sub:v1 sign → queued);
  `/internal/pending` drain + `/internal/mark-filed`. Wire into `worker/index.ts` under a new
  `requireSubcontractToken` tier (migration 0051 already grants `cap.subcontracts.manage`).
- `subcontracts/subcontract_poll.py` ← `po_materials/po_poll.py`: drafts pass (pull → recompute sub:v1
  HMAC → `subcontract_docx.render_package` → Box file → `subcontract_log` write → review row → mark-filed);
  subcontractor down/up-sync passes (**the §51 bidirectional runner** — calls the S1 builders); status pass;
  heartbeat + per-pass ITS_Config gates (ship false); launchd plist.
- `subcontracts/subcontract_review.py` ← `po_materials/po_review.py`: WSR-twin thin re-export
  (WORKSTREAM_TAG='subcontracts'; `SHEET_SUBCONTRACT_PENDING_REVIEW`; Notes-encoded subcontract_id/number).
- Reviews (DoD): `portal-worker-security-reviewer` (subcontract.ts + migrations) + `ops-stds-enforcer`.

### SC-S3b Exhibit A (follow-on) — blocked on the `exhibit_trade_templates` config artifact (an SC-S2 gap)
Fixed Art I/III/IV/VI text + the trade-templated Art II scaffold — NOT invented ad-hoc.

### SC-S5 — SPA subcontractor admin page grouped by STATE (mirrors the vendor page grouped-by-region)

## Notes
- The ROADMAP framed Subcontracts as a **post-Aug-7-delivery** build ("first post-delivery builds"); the
  operator pulled it forward.
- Both S3a and S3b shipped a real correctness BLOCK caught by adversarial review that unit tests could not
  find (money fence/rounding; OOXML wall-clock determinism) — reaffirms *adversarial-review-is-DoD* on any
  trust-boundary / legal-document surface.
