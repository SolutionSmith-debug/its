---
type: session_log
date: 2026-07-19
status: closed
workstream: po_materials
related_prs: [618, 620, 621, 623, 624, 625]
tags: [po_materials, rfq, vendor-estimate, adr-0004, local-inference, external-send-gate, mirror-deploy, stacked-prs, materials-catalog]
---

# 2026-07-19 — RFQ / vendor-estimate lane: design → build (E1–E6 / R1–R4) → mirror go-live

## Purpose

Operator-directed, single long session: design the RFQ-generator + vendor-estimate-importer
capability, then — on an explicit operator override of the post-Aug-7 deferral — **build all ten
slices, land them as four stacked PRs, add a follow-on feature, and deploy the whole lane live-dark on
the mirror tenant** (intake + generation + send). The planning half (ADR-0004 + purchase-orders
mission v6) is the committed record; ADR-0004 is red-team-hardened. The AI extraction tiers were
deliberately left **dark and unvalidated** (see Open items).

## Commits landed (squash-merge SHAs on `main`)

- **`51e9a20` (#618)** — E1–E3 estimate importer core: `po_estimates` upload pool (`est:v1` HMAC,
  partial-unique live-sha dedupe→409), `estimate_poll` daemon (screen → doc-type-refuse invoices → Box
  → `Estimate_Log` → previews → `needs_review`), disposition SPA → existing `POST /api/po/drafts`.
  Both adversarial reviews returned **BLOCK**; all findings folded in before merge (server-side
  preview-evidence gate, prune retention, `_BearerRejectedError` re-raise + one-shot-flag persistence,
  real-child sandbox kill tests). Carries ADR-0004 + `evergreen_cutover_brief.md`.
- **`1bac78c` (#620)** — E4–E6 extraction ladder: Tier-1 deterministic pdfplumber + data-driven vendor
  templates (Platt M-divisor), Tier-2 **local Ollama** (`shared/ollama_client.py`, localhost-only,
  schema-constrained) + Vision OCR, Tier-0 xlsx quote-form round-trip, `scripts/eval_estimate_ladder.py`
  offline corpus harness. Reviews PASS + 2 folded fixes (ollama redirect-refusal; screener external-rel
  scope so bare hyperlinks stay clean).
- **`568b4f7` (#621)** — R1–R2 RFQ generator core: SPA composer + `worker/rfq.ts` (`rfq:v1`, 3-way
  bearer separation) + `rfq_poll` (per-vendor price-free RFQ PDF → Box → one `RFQ_Pending_Review` row
  tagged `po_materials_rfq`). Reviews PASS + fixes (vendor re-check at generate; flag-persistence
  parity; 401 test). rfq:v1 HMAC parity hand-verified byte-identical TS↔Python.
- **`c8775f9` (#623)** — R3–R4 send lane + round-trip: `rfq_send`/`rfq_send_poll` (F22 against
  `WORKSPACE_PURCHASE_ORDERS`) binding the shared `weekly_send` engine via a new opt-in
  `extra_attachments` sequence seam (every existing send binding regression-verified byte-identical);
  the verified Tier-0 form auto-binds an uploaded estimate to its RFQ. Send review PASS; Worker review
  **BLOCK** → fixed (auto-bind gated on the estimate's own status + a `job_no` cross-check).
- **`89f8f0d` (#624)** — deploy round-trip: seeded the 3 built Smartsheet sheet IDs into
  `shared/sheet_ids.py` + fixed a real builder bug (two RFQ_Pending_Review column descriptions were
  260 chars, over Smartsheet's 250-char column-description cap, errorCode 1041).
- **`5727f7f` (#625)** — RFQ builder gains the **materials-catalog line picker**, mirroring the PO
  builder (reuses the existing `GET /api/po/materials` route + `catalogLineFields`; price-free;
  free-text preserved). SPA-only, no Worker change.

Blueprint (`its-blueprint`): `227e69b` — purchase-orders **mission v5 → v6** reviving the RFQ scope as
a local-only sub-lane (§10), satisfying the mission's own stated v6 trigger.

## CI runs

- Every PR verified via its **pull_request** run + the four-part landing ritual. Push-triggered CI is
  **flaky on this repo** — GitHub silently dropped the `push:main` event on several merge commits; the
  operator's **#619** added a `workflow_dispatch` "re-cover" trigger, and main-branch verification was
  taken from the pull_request run + the manual re-cover run (both green) rather than the (missing) push
  run. This is the recurring "CI double-triggers / a run can go missing" class — trust run-level
  conclusion + `mergeStateStatus`, never a single trigger.
- Final test posture across the stack: pytest **3574 → 3808 passed**, mypy clean (438 files), worker
  vitest **1119**, SPA **666**, `check_doctrine_drift --strict` exit 0.

## Decisions made during the session

- **Post-Aug-7 deferral was OVERRIDDEN by the operator.** The approved plan slotted the build as
  post-delivery (slot-into-roadmap); the operator green-lit building it now for velocity ("live within
  a day"). Recorded so the reversal is legible; the blueprint mission bump captures the scope revival.
- **Local-only extraction ladder — no cloud AI in the lane** (Tier-0 xlsx → Tier-1 templates → Tier-2
  local Ollama → Tier-3 human), preserving the "sole live Anthropic consumer is `intake.py`" invariant.
  Chosen over reusing the `intake.py` cloud pattern so vendor pricing never leaves the host.
- **Two bearer tokens, not one** (`ITS_PORTAL_ESTIMATE_TOKEN` vs `ITS_PORTAL_RFQ_TOKEN`) — the
  first red-team finding; the estimate daemon is the highest-exposure process (decodes hostile bytes)
  and must not reach the RFQ send-lane control surface. Overrode the first architect pass.
- **Automated gates prove consistency, not fidelity.** The honest framing folded into the ADR + the
  code: math/schema/recompute all check internal arithmetic; the **human side-by-side accept is the
  sole fidelity control**, hardened server-side (no accept of extracted lines without a loaded preview).
- **Stacked-PR strategy with union-rebase.** A→B→C→D each rebased onto the prior merge; the full A+B+C
  test suite was the correctness backstop for each union resolution (a dropped-lane resolution goes
  red). Chosen over one mega-PR (unreviewable) and over parallel siblings (they share every registry).
- **PR-A opened fresh (#618) rather than force-pushing #615** — the branch predated #614's `system_map`
  parity teeth and rebasing rewrote history; a fresh branch avoided a force-push (guardrail-reserved).
- **RFQ materials picker replicates PoBuilder inline rather than extracting a shared component** — the
  minimal faithful change; reuses `fetchPoMaterials`/`catalogLineFields` verbatim, no Worker change.
- **Mirror deploy: intake + send taken LIVE; extraction tiers left DARK.** Caught + fixed a real gap —
  `PORTAL_ESTIMATE_API_TOKEN`/`PORTAL_RFQ_API_TOKEN` were missing from the Worker (would have 401'd the
  daemons); set them from Keychain and confirmed the full auth matrix. Fail-closed send smoke passed
  (gate-off no-op even when the send daemon is loaded) before flipping the send gate.

## Open items handed off

- **Tier-1/Tier-2 local-model extraction is BUILT BUT UNVALIDATED — do NOT flip `tier1_enabled`/
  `tier2_enabled`/`ocr_enabled` until qualified.** Nobody has `ollama pull`'d a model on the production
  MacBook (the 18 GB M2 per the host-migration runbook — distinct from the dev box) or run a real
  vendor PDF through it. Run `scripts/eval_estimate_ladder.py` against the real `~/Desktop/Evergreen
  project/Z. Quotes 1` corpus on the production hardware and review tier coverage + math pass-rate
  first. Until then the importer is a screen-file-and-human-disposition (Tier-3) tool.
- **`verify_cutover.py` VC-02 will now flag `rfq-send` as dark-loaded** (same as `po-send` after its
  go-live) — drop `rfq-send` (and reconcile `po-send`) from `DARK_UNLOADED_LABELS`. Small follow-up PR.
- **Operator dashboard integration of the RFQ/estimate lane** — surface the 3 new daemons
  (estimate-poll/rfq-poll/rfq-send), the 3 new sheets, and the new config gates across every dashboard
  location (daemon panels, Class-A config editor gate rows, log-tail sources, `/system` map live-join,
  `/troubleshoot` tree, error/review-queue workstream filters). Queued as the next session's task.
- **RFQ send positive-path smoke** — the fail-closed (negative) smokes passed; a real approved-row send
  to a stand-in vendor mailbox on the mirror was not exercised (no RFQ generated/approved yet).

## What was NOT touched (deliberate)

- **Production tenant.** All deploy work targeted the **mirror** (`evergreenmirror.com` D1 + Worker +
  Smartsheet). The Aug-3 production cutover is unaffected.
- **The AI extraction tiers** — left dark (above).
- **The shared `weekly_send` engine's existing bindings** — the sequence-attachment seam is opt-in;
  safety/progress/po/subcontract send paths are byte-identical (regression-verified).
- **The kill switch / other workstreams' gates.**

## Lessons captured to memory

- `project_rfq-estimate-lane-designed.md` + `MEMORY.md` index updated: DESIGNED → **BUILT + all 4 PRs
  merged (dark), runtime deploy done on the mirror**, with the do-not-re-build warning and the
  deploy/validation state (secrets minted, sheets built, gates flipped, tiers unvalidated).
- Reinforced: push-CI events are droppable here → verify via pull_request + the #619 re-cover, never a
  single trigger; and the column-description 250-char cap is a sibling of the sheet-name 50-char cap.
