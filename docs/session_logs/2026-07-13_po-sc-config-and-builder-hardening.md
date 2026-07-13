---
title: "Session log — PO/SC config tabs, new-trade create, builder hardening, delete/prune"
date: 2026-07-13
author: Claude Code (Opus 4.8, 1M ctx)
---

# 2026-07-13 — PO/SC config + subcontract/PO builder hardening

## What shipped (all four-part-verified clean)

| PR | Summary | Merge commit |
|----|---------|--------------|
| #554 | PO/SC Config **tabs** (Purchase Order / Subcontract), reused `admin-tabs`; shared status monitor | `26f106d` |
| #556 | **Config-driven subcontract trade list** — `GET /api/subcontracts/trades` = manifest `trade_map` keys; static `TRADES` demoted to degraded-fetch fallback | `30a0089` |
| #557 | **"New article template"** = exhibit `create_profile` — mints a new trade + template (v1 pending); `_SUBCONTRACTOR_TRADE_VALUES` + `build_its_subcontractors.TRADE_OPTIONS` now **manifest-derived**; reused create_profile op → **no migration**; §43 runbook + live-Smartsheet-picklist step | `f1684b0` |
| #558 | Exhibit A "Work" cap **8000 → 100_000** (electrical template ~20k chars was unsaveable); sized to the config template cap | `fd07aeb` |
| #559 | **Required-field gates** — blank `owner_entity`/`project_name`/`trade` (sub) or `terms_profile_id` (PO) refused at **Generate** (422) + builder `validate()`, not silently fenced at render. Guard on `/generate`, NOT `parseDraftBody` (partial draft still saves) | `dbcea02` |
| #560 | **Delete-draft** (hard, draft-only, no orphaned lines) + **prune** stale `draft`/`canceled` rows at 90d — only never-generated (`sc_number/po_number IS NULL`), so an allocated number is never freed for reuse | `0cc4fb8` |

## Non-obvious decisions / lessons

- **#557 fan-out:** a "trade" is a 5-surface cross-system datum (SPA dropdown · manifest `trade_map`+template · `shared/picklist_validation._SUBCONTRACTOR_TRADE_VALUES` §51 gate · `build_its_subcontractors.TRADE_OPTIONS` · the LIVE ITS_Subcontractors Trades picklist column). Operator chose "full wire": derived the Python gate from the manifest (mirroring the terms-profile derivation); the one out-of-rail surface (the live Smartsheet Trades column) is a documented §43 Tier-2 operator step.
- **#559 boundary choice:** `parseDraftBody` gates BOTH save-draft and generate; requiring fields there would break partial-draft saving. Put the guard on the `/generate` route (mirroring the existing `governing_law_state` fail-closed). Adversarial review CLEAN.
- **#560 worker-security BLOCKER (caught + fixed):** the prune's `status IN ('draft','canceled')` swept a **generated-then-canceled** row too (cancel is allowed from queued/pending_review, so the row keeps its allocated `sc_number`/`po_number` + revision + Box PDF + ledger row). Deleting it frees the UNIQUE number/revision slot → a later generate could reuse the number → audit collision. Fix: `AND sc_number/po_number IS NULL` — prune only never-generated rows. The original test only seeded draft-canceled (NULL number), missing the case; added the generated-canceled survival test (reviewer proved it bites: reverting the guard → RED `expected 3 to be 2`). **Reinforces: adversarial review catches what unit tests structurally miss.**

## Diagnostics surfaced (operator-actionable, not code)
- Test subcontract `sc_id=2` **fenced** (`subcontract_render_failed: missing owner_entity`) — never filed; #559 prevents recurrence.
- **`ITS_Review_Queue` at the ~20,000-row Smartsheet cap** (`errorCode 5634`) — blocks fence/review recording system-wide; watchdog Check O evidently not rotating it. Investigate separately.

## Handoff
- **Deploy pending:** `cd ~/its/safety_portal && npm run deploy` (all six PRs are Worker/SPA; no migration). Operator had not deployed at session end.
- **Next-session brief:** `docs/cc-brief_per-job-sheets-and-po-enhancements.md` — three operator-requested features (per-job Smartsheet folder+sheet for subs+POs · PO attachment field (§34 trust boundary) · PO delivery-contact config autofill).

## Gate (representative; each PR verified independently)
- pytest: green (worker vitest peaked 1034 passed; spa vitest 648 passed)
- mypy: clean (360 source files)
- ruff: clean
- main-branch CI on each merge commit: SUCCESS
