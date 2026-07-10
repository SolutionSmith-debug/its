---
type: reference
date: 2026-07-10
status: proposed
related_prs: [506]
workstream: po_materials
tags: [adr, purchase-orders, config, terms, tax, purchaser, section-50, privileged-actuation, legal-guardrail, deferred]
---

# ADR-0002 — PO config editor as a §50 privileged code-actuation (deferred, propose-mode)

**Status:** Proposed — the read-only view shipped (PR #506); the EDIT/actuator is deliberately
deferred to an operator-present session (see "Why deferred"). This ADR records the intended design
so that session builds it, not reinvents it.

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
the existing `GET /api/po/config` + `GET /api/po/terms` routes. That closes the *visibility* gap.
The open question is the **edit** path: the operator asked for a "terms/purchaser/tax editor" and
approved it as a §50 privileged-code-actuation with legal guardrails.

## Decision

Build the edit as a **§50 privileged code-actuation** reusing the established form-editor pattern
(`decision_phase2-form-editor`: git-source + manifest, Mac-daemon actuator), with these bindings:

1. **Portal side is send-free and actuation-free.** The SPA edit form POSTs a *change request* that
   the Worker writes to a new D1 queue table (e.g. `po_config_requests`) — exactly like the PO
   queue. The Worker performs **no git, no deploy, no file write**. Body-shape guards + bound SQL as
   for every write route.
2. **Mac-side actuator validates, then commits to a BRANCH and opens a PR — it does NOT auto-deploy
   (propose-mode default).** A new daemon (`po_materials/config_actuator.py`) pulls pending change
   requests, validates hard (tax: integer bp `0..10000`, valid 2-letter state codes; purchaser:
   required fields, RFC-ish email on routing), writes the JSON, **bumps `config_version`**, commits
   to a `po-config/req-<id>` branch, and opens a PR. **The operator merging that PR is the
   human-in-loop review point** — the legal/money change is never live until a human merges + the
   deploy runs. (Auto-merge/auto-deploy — the form-editor's "C12 = A" fully-automatic mode — is a
   later operator opt-in per class, NOT the default here, because legal T&C and money-path tax carry
   more downside than a form definition.)
3. **Terms text is the hardest guardrail.** A terms `.md` is sha256-immutable, so an edit MINTS A
   NEW VERSION file (`standard_17_v2.md`), never mutates `_v1`. The new version is **not usable on a
   PO until an explicit operator legal-review flag** clears it (a manifest field the generate path
   checks). Purchaser + tax are lower-sensitivity and need only the propose-mode PR review.
4. **Ships dark-gated.** New `ITS_Config` gate(s) under `po_materials.config_actuator.*`, seeded
   `false` in the same change (the "seed the gate row" reflex), so activation is a visible cell-flip.

## Why deferred (the §44 boundary)

The actuator **git-commits legal T&C and money-path tax config**. Code changes are one of the four
FIXED high-capability-class categories (§44) that co-resolve with the developer, and the first live
actuation of a new code-deploy category is exactly where the form-editor precedent ("first ITS
per-category code-deploy clearance") says the operator should be present. Autonomously shipping a
portal→git→deploy path for legal/money content overnight, with no one watching the first commit,
would cross that line. The **read-only** view has none of that exposure and delivers the majority of
the value now; the actuator waits for an operator-present build.

## Consequences

- **Now:** the office can *see* every PO's identity/tax/terms and catch a wrong value by eye (#506).
- **Next (operator-present):** build items 1–4 above — the D1 request table + migration, the Worker
  request-queue route (cap.po.manage, send-free), the `config_actuator.py` daemon (propose-mode),
  the terms new-version + legal-review-flag machinery, the dark gates, tests, adversarial review, and
  the §43 successor-remediation runbook entry.
- **Guardrails preserved:** integer-bp money math, sha256 terms immutability, legal review gate,
  human-merge before deploy, never-silent validation.

## Alternatives considered

- **Direct D1 config store (no git).** Rejected: the render/totals path and the terms renderer read
  from the git tree; a D1-only store would fork the source of truth and lose the sha256 pinning + PR
  review that make the terms/money change auditable.
- **Fully-automatic actuation (form-editor "C12 = A").** Rejected as the *default* for legal/money
  content; left as a per-class operator opt-in once the propose-mode path has a track record.
