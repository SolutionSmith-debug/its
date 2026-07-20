---
type: session_log
date: 2026-07-20
status: closed
workstream: po_materials
related_prs: [629]
tags: [po_materials, safety_portal, spa, rfq, vendor-estimate, tabs, router, disposition, adversarial-review]
---

# 2026-07-20 — Fold RFQs + Vendor Estimates into a Purchase-Orders tab hub (#629)

## Purpose

Operator directive: "fold the RFQ and vendor estimate into the purchase orders workflow as
tabs; the option to make a PO from an estimate should be an option when creating a new PO
while still being able to edit/add/modify after processing." SPA-only; a parallel workstream
was touching the operator dashboard, so this session confined itself to `safety_portal/src/**`
and built in a per-task worktree (`~/its-po-tabs`).

## What landed (PR #629, squash `bd4c0e3`)

- **New `PurchaseOrdersPage` hub** — one `PageShell` + the canonical `.admin-tabs` strip
  (Purchase Orders / RFQs / Vendor Estimates). Panels mount on first visit and then stay
  mounted (`hidden`) so a half-built PO wizard or RFQ survives tab flips (the FieldOpsMyTasks
  keep-alive precedent). App renders ONE shared branch for the three routed views so React
  never remounts the hub across tab navigation — this identity guarantee is documented at the
  branch and is what keeps wizard state alive.
- **Router** — canonical nested paths `/purchase-orders`, `/purchase-orders/rfqs`,
  `/purchase-orders/estimates`. The pre-fold `/estimates` + `/rfqs` remain PARSE-ONLY aliases
  (cold loads normalize via App's entry `replaceState`). View keys and `VIEW_CAPS` unchanged —
  zero capability drift; the round-trip law stays on canonical paths (locked in
  `router.test.ts`).
- **New-PO-from-estimate** — the Orders tracker's "New PO from a vendor estimate" lists
  reviewable estimates on demand; picking one opens the DISPOSITION screen on the Estimates
  tab (the ADR-0004 decision-3 fidelity gate remains the ONLY estimate→PO path — the fold adds
  navigation, never a bypass). A successful import flips back to Orders with the minted draft
  OPEN in the builder, fully editable (lines / attachments / terms) before Generate.
- **Home** — the Vendor Estimates + RFQs cards fold into the single Purchase Orders card
  (gates unchanged; the card copy names the folded lanes).
- `EstimatesPage` / `RfqBuilderPage` / `EstimateDispositionPage` / `PoBuilderPage` became
  shell-less tab panels; the disposition's `onClose` now carries the imported PO id
  STRUCTURALLY (`onClose(notice, importedPoId)`), not just in banner copy.

## Adversarial review (the non-obvious decision record)

A 28-agent multi-lens review workflow (react-state / router-url / adr-gates / ux-tests, every
finding refuter-verified 2×) confirmed 5 findings, all fixed in the second commit:

1. **CRITICAL — `key={openId}` on `EstimateDispositionPage` is LOAD-BEARING.** The new
   cross-tab `reviewRequest` created the first-ever path that retargets `openId` while a
   disposition instance is mounted (keep-alive panels). Without a remount, estimate A's
   loaded-preview evidence, manual Tier-3 lines, ship-to state, and site phase carried into
   estimate B's import — a decision-3 fidelity-gate bypass the Worker's server-side twin
   cannot catch (it counts previews that EXIST, not pages the reviewer viewed). Fixed with the
   key (fresh mount + fresh gate per estimate) and locked by a hub test **proven to red-light
   with the key removed** (inject → fail → revert).
2. CI-blocking `noUnusedLocals` violation in the new hub test — caught because my earlier
   "typecheck clean" run predated the test file (§55.1 lesson re-learned: re-run the exact CI
   gate after every file you add).
3. Import handoff could silently clobber a mid-edit builder → `window.confirm` guard (R3
   discard precedent); declining keeps the work and the import stays findable in the tracker.
4. The from-estimate picker stayed open with a stale list after its own round-trip → closed on
   handoff.
5. Stacked `h2.page__heading` elements — RFQ builder + disposition sub-faces demoted to `h3`.

## Verification

- pytest: not run locally (SPA-only change; `test` job green on main CI)
- mypy: not run locally (no Python touched)
- Portal gate: `npm run typecheck` (3 tsconfigs) clean · SPA vitest 675 passed · worker
  vitest 1119 passed · `vite build` clean
- Live smoke: `wrangler dev --local` + seeded admin — tabs on design language, URL flips,
  legacy `/estimates` cold-load normalizes, picker + cross-tab jumps work, deep link survives
  login. (Vite `dev` mode is unusable for this — its inline preamble trips the Worker CSP;
  `wrangler dev --local` against the built assets is the smoke path. Worktree quirk: vite's
  workspace-root detection fails through a worktree's `.git` FILE, denying the Worker's
  `../po_materials/terms/*.md?raw` imports — needed a temporary `server.fs.allow` during the
  vite-dev attempt; not committed.)
- main-branch CI on merge commit `bd4c0e3`: SUCCESS (four-part verify clean — state=MERGED ·
  mergedAt non-null · mergeCommit present · main CI green)

## Same-day follow-ups (operator-driven)

- **"Changes not visible after deploy" (operator report):** diagnosed, NOT a deploy failure.
  The operator's 11:26 `npm run deploy` landed (Cloudflare version list + live asset hashes
  proved it); the visible staleness was the short post-deploy edge-propagation window — the
  Cloudflare edge serves a cached HTML `HIT` for ~a minute after deploy (a `no-cache` request
  gets fresh HTML immediately; plain requests converge on their own). Reproduced and watched
  it converge after the second deploy below. Practical guidance: after `npm run deploy`, give
  the edge a minute (or hard-refresh, which sends `no-cache`) before concluding anything.
- **RFQ vendor quick-add (PR #630, squash `8309d5d`, four-part clean, DEPLOYED live):**
  operator ask — "the RFQ needs vendor, free text not just a pick list." Shipped as free-text
  ENTRY that lands as a REAL directory row: the RFQ builder's "+ New vendor (not in the
  list)" (name + required quote-contact email) calls the EXISTING `POST /api/po/vendors`
  (atomic key mint, `origin=portal`, §51 up-sync) and joins the minted key to the RFQ — never
  a keyless vendor, because the send lane resolves recipients from `ITS_Vendors` by Vendor
  Key (ADR-0004 decision 9). Adversarial review (12-agent) confirmed + fixed: a never-silent
  violation at the 12-vendor cap (create-then-silently-not-join under a success banner — now
  refused BEFORE the create, select frozen while in flight, locked by a proven-to-bite test),
  a rejecting-side email test gap, and a WCAG 2.5.3 aria-label mismatch. Process note: a
  `git checkout <file>` after a prove-it-bites injection wiped the uncommitted fixes (the
  documented footgun — re-applied; use cp-backups or patch-revert next time).
