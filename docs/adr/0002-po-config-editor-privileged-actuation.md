---
type: reference
date: 2026-07-10
status: active
related_prs: [506, 508, 509, 510, 512]
workstream: po_materials
tags: [adr, purchase-orders, config, terms, tax, purchaser, section-50, privileged-actuation, legal-guardrail, fully-automatic]
---

# ADR-0002 — PO config editor as a §50 privileged code-actuation (fully-automatic, C12 = A)

**Status:** Accepted / in force. The read-only view shipped first (PR #506), then the full edit
vertical shipped and was activated on the mirror the same day (2026-07-10): the send-free cloud queue
(#508), the Mac `config_actuator` daemon (#509), the generic SPA editor (#510), and the activation
fixes (#512). **The actuator runs FULLY-AUTOMATIC — the form-editor's "C12 = A": validate → commit to
a branch → open a PR → auto-merge on green CI → auto-deploy.** This **supersedes this ADR's own initial
recommendation of propose-mode** (actuator opens a PR, a human merges it): the operator's live
2026-07-10 decision selected fully-automatic actuation with high guard-rails for all three artifacts
(purchaser, tax, terms), matching the form-editor precedent (`decision_phase2-form-editor`). The
superseded propose-mode design is preserved below so the record shows what was recommended and why it
changed.

## Context

Three config classes print on every purchase order and are today edited only by the developer,
directly in version-controlled files:

- **Purchaser identity** — `po_materials/config/purchaser.json` (entity, address, phone, invoice
  routing). Versioned (`config_version`). Entity + STE 570 address are pending Evergreen's written
  confirmation.
- **Ship-to tax table** — `po_materials/config/tax.json` (rates in **basis points**, integer-only;
  `state_names`). On the **money path**: the Worker's generate handler and the Mac render/totals
  assert both consume it, and the render-time assert catches version skew.
- **Terms-library profiles** — `po_materials/terms/*.md` + `manifest.json`. The `.md` versions are
  **sha256-pinned and immutable**; a change is a NEW version, never an in-place edit. Legal-reviewed
  T&C text.

PR #506 added a **read-only** admin view (`PoConfigPage`, cap.po.manage) that renders all three from
the existing `GET /api/po/config` + `GET /api/po/terms` routes. That closed the *visibility* gap.
The open question was the **edit** path: the operator asked for a "terms/purchaser/tax editor" and
approved it as a §50 privileged-code-actuation with legal guardrails.

## Decision

Build the edit as a **§50 privileged code-actuation** reusing the established form-editor pattern
(`decision_phase2-form-editor`: git-source + manifest, Mac-daemon actuator), with these bindings:

1. **Portal side is send-free and actuation-free.** The SPA edit form POSTs a *change request* that
   the Worker writes to a D1 queue table (`config_requests`, migration 0045) — exactly like the PO
   queue. The Worker performs **no git, no deploy, no file write**. Body-shape guards + bound SQL as
   for every write route.
2. **Mac-side actuator runs FULLY-AUTOMATIC (C12 = A).** The `po_materials/config_actuator.py` daemon
   pulls pending change requests, **re-validates hard against live HEAD** (tax: integer bp `0..10000`,
   valid 2-letter state codes; purchaser: required fields + RFC-ish routing email; terms: immutable
   new-version rules — the same rules the Worker enqueue gate applied, re-checked because HEAD may have
   moved since enqueue), writes the JSON / new terms file, **bumps `config_version`**, commits to a
   `config/req-<id>` branch, opens a PR, and — **on green CI** — **auto-merges and auto-deploys**. No
   human merges the PR.

   > **Superseded initial recommendation — propose-mode.** This ADR first recommended that the
   > actuator STOP at "open a PR" and make **the operator merging that PR the human-in-loop review
   > point**, reserving fully-automatic (C12 = A) as a later per-class opt-in because legal T&C and
   > money-path tax carry more downside than a form definition. The operator's live 2026-07-10
   > decision superseded this: fully-automatic for all three, because the human-in-loop and the
   > guard-rails below are load-bearing **without** a human PR-merge.

   **What carries the safety instead of a human PR-merge (the "high guard-rails" of C12 = A):**
   - The operator **initiates** every edit in the portal (nothing actuates unsolicited), the
     cap.po.manage capability is required, and the dark `ITS_Config` gate must be on.
   - **Two independent hard-validation layers** — the Worker enqueue gate (`worker/config.ts`) AND the
     actuator's authoritative live-HEAD re-validation (`config_apply.py`), kept in lockstep.
   - **Auto-merge only on GREEN CI** — the full test gate (integer-bp money math, terms immutability,
     capability gating) must pass, exactly as a human-reviewed PR would require. *(This is precisely
     why a CI test that hard-pins the live config content is a merge-blocker for the edit path, and
     why such pins are converted to shape/round-trip assertions — the fix that carries this ADR's PR.)*
   - **Money path:** the render-time totals assert catches any tax version skew between the Worker and
     the Mac renderer (defense in depth on a tax edit).
   - **Terms:** the legal-review gate (item 3) keeps a new clause version INERT until an operator
     explicitly clears it — so an auto-merged terms edit cannot reach a live PO on its own.
3. **Terms text is the hardest guardrail (two-layer legal gate).** A terms `.md` is sha256-immutable,
   so an edit MINTS A NEW VERSION file (`standard_17_v2.md`), never mutates `_v1`. **Layer B (built):**
   the actuator writes the new version with `legal_review: "pending"` and **leaves `current_version`
   UNCHANGED** — the new text is inert; no PO renders it until an operator both clears legal review and
   repoints `current_version`. **Layer A (BUILT, slice T2):** the render-side loader refusal is now
   enforced in `terms._version_entry` — a version whose `legal_review != "cleared"` raises at render
   (fencing the PO via the Review Queue), so a mis-bumped `current_version` can't render un-cleared
   text. Both shipped versions were backfilled to `cleared` in the same change (the spec's activation
   order — gate + backfill ship together, else every live PO would fence). The `set_current`
   make-current op (confirmable "I've reviewed this — make it live") is the activation path. Purchaser
   + tax are lower-sensitivity and rely on the two validation layers + green CI.
4. **Ships dark-gated.** `ITS_Config` gate `po_materials.config_actuator.polling_enabled`, seeded
   `false` in the same change (the "seed the gate row" reflex), so activation is a visible cell-flip.

## Why the build waited for an operator-present session (the §44 boundary)

The actuator **git-commits legal T&C and money-path tax config**, and code changes are one of the four
FIXED high-capability-class categories (§44) that co-resolve with the developer. The first live
actuation of a new code-deploy category is exactly where the form-editor precedent ("first ITS
per-category code-deploy clearance") says the operator should be present — so at PR #506 only the
**read-only** view shipped, and the edit/actuator waited. That operator-present build then happened on
2026-07-10: the Developer-Operator watched the first live actuation on the mirror (token provisioned,
daemon loaded via `install.sh`, gate flipped true), which is what cleared fully-automatic for this
class. Fully-automatic is the *steady-state* mode; the operator-present requirement was about the
*first* clearance, now satisfied.

## Consequences

- **Read-only (done, #506):** the office can *see* every PO's identity/tax/terms and catch a wrong
  value by eye.
- **Edit vertical (done, #508–#510; activated on the mirror #512):** the D1 request table + migration
  (0045), the send-free Worker request-queue routes (cap.po.manage), the `config_actuator.py`
  fully-automatic daemon, the terms new-version + legal-review-pending machinery (Layer B), the dark
  gates, tests, adversarial review, and the §43 successor-remediation runbook entries.
- **Follow-ups:** CE-1 (`publish_daemon._fail` redact parity with the actuator's §54 redaction) —
  tracked in `docs/tech_debt.md`. (CE-2, the render-side Layer-A legal_review refusal, is now BUILT —
  slice T2 — together with the `set_current` make-current activation flow.)
- **Guardrails preserved:** integer-bp money math, sha256 terms immutability, the legal-review gate,
  two-layer hard validation, green-CI-before-auto-merge, never-silent validation.

## Alternatives considered

- **Propose-mode (actuator opens a PR; a human merges it).** This ADR's **initial recommendation**,
  **superseded** by the operator's live 2026-07-10 decision (see Decision item 2). Fully-automatic with
  high guard-rails + the terms legal gate was chosen instead, matching the form-editor C12 = A
  precedent; the human-in-loop is the operator initiating the edit + the legal gate, not a PR merge.
- **Direct D1 config store (no git).** Rejected: the render/totals path and the terms renderer read
  from the git tree; a D1-only store would fork the source of truth and lose the sha256 pinning + PR
  review that make the terms/money change auditable.
