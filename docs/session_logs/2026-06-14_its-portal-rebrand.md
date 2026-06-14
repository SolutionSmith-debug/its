---
type: session_log
date: 2026-06-14
status: closed
related_prs: [285]
workstream: safety_portal
tags: [session_log, safety_portal, rebrand, its-portal, frontend, appheader, maximalist-header, automation-safety-audit, deploy, revert-provision, mock-a, phase-b2]
---

# Session — feat(safety-portal): rebrand "Safety Portal" → "ITS Portal" (maximalist header, Phase A / B2)

Executed the desktop handoff bundle `~/Desktop/its-portal-rebrand-bundle` (CC brief v3, design-locked): a frontend-only rebrand of the user-facing SPA to "ITS Portal" with a maximalist deep-green brand header, then merged it and deployed it live to the mirror after proving (with an adversarial multi-lens audit) that the change cannot break any ITS automation.

## PRs landed

### PR #285 — feat(safety-portal): rebrand Safety Portal → "ITS Portal" (maximalist header, Phase A/B2) (merge `8162709`)

The user-facing product was renamed from "Evergreen Safety Portal" to **"ITS Portal"** and the header rebuilt as a maximalist deep-green field carrying a fixed brand lockup: `[Evergreen logo on a gold-bordered white plate] → [ITS crest: wreath + "order from growth" ribbon] → ["Portal" in gold-gradient script]`. Frontend + user-facing strings only — no backend, send-gate, adversarial-input, or transport code touched.

Thirteen files, +64/−32:

1. **`safety_portal/index.html`** — `<title>` "Evergreen Safety Portal" → "ITS Portal"; meta description → "ITS Portal — field safety forms and operational workflows for Evergreen Renewables."; favicon `<link>` `/evergreen-logo.svg` → `/favicon.png`; theme-color `#013D2B` unchanged.
2. **`safety_portal/src/components/AppHeader.tsx`** — the required `title: string` prop was **removed** (the brand is now fixed, not a per-page label). The component renders the Evergreen mark on a gold-bordered white plate + `its-portal-header.png` (the transparent ITS-crest/gold-script "Portal" lockup, `alt="ITS Portal"`). The optional `action` prop (Sign out / Back) and the existing `evergreen-logo.svg` `<img>` are retained.
3. **The 6 page files** (`LoginPage`, `HomePage`, `FormRequestPage`, `FormsPage`, `AccountsPage`, `FormFillPage`) — all **7** `<AppHeader>` call sites had `title=` removed. FormFillPage has two sites (submitted-view + fill-view); the fill view, whose label lived only in the header, gained an in-page `<h1 className="page__heading">New safety form</h1>` so wayfinding survives the title removal.
4. **`safety_portal/src/styles/tokens.css` + `global.css`** — added `--c-brg-900: #011d14` (deepest-green gradient floor); enriched `.app-header` (radial sheen over a deeper linear gradient, thicker gold bottom rule + gold top-hairline/inset frame via box-shadow), made it taller to seat the ~3:1 lockup, gold-bordered the Evergreen plate, added `.app-header__lockup`, removed the now-dead `.app-header__title`, and added `flex-wrap` + a `@media (max-width: 360px)` block so the brand scales/wraps without horizontal overflow on field phones.
5. **New `safety_portal/public/` assets** — `its-portal-header.png`, `its-logo.png`, `favicon.png`.

The prop removal is the key safety mechanic: with `tsconfig` `strict` + `noUnusedParameters`, any missed call site is a hard `tsc` error, so a half-applied change cannot compile — the whole rename lands atomically in one commit or not at all.

Gates (this change is frontend-only; the Python suite is untouched and identical):

- typecheck: clean (`tsc` across `tsconfig.json` / `.worker.json` / `.test.json`)
- vite build: OK — `index.html` + 3 new PNGs + `evergreen-logo.svg` all ship to `dist/client/`
- SPA vitest: 76 passed / 76 (`test:spa`)
- Worker vitest: 212 passed / 212 (`test`)
- pytest: 1,821 passed / 2 skipped · mypy: 0 errors / 201 source files · ruff: clean (Python `test` job — unaffected by this frontend-only change)
- main-branch CI on merge commit: SUCCESS (test, portal, secrets, CodeQL ×3)

PR #285 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-14T19:04:49Z
- mergeCommit: 8162709ca4f58bbbb412b350f30d357e9e395376
- main-branch CI on merge commit: SUCCESS (run 27508981546, workflow: ci)

## CI runs

- **PR #285 (pull_request + push double-trigger):** test, portal, secrets, CodeQL `Analyze` (actions / javascript-typescript / python) — all SUCCESS; `mergeStateStatus: CLEAN`, `mergeable: MERGEABLE`. No CodeQL infra-flake this run.
- **main @ `8162709` (post-merge push):** test, portal, secrets, Analyze ×3 — all SUCCESS (the four-part-verify step-4 gate).

## Decisions made during session

- **Mock A over Mock B (header typography).** Operator confirmed the flowing gold-gradient script "Portal" (`mock-A-script.png`) over the engraved-caps alternative (`mock-B-engraved-caps.png`). This was already the design-locked choice in brief v3; the operator re-confirmed it.
- **Phase B2 (rebrand-only) over B1 (build the top-level nav now).** The brief left B1/B2 open. Chose B2 — rename + maximalist header only — and deferred the `WorkflowTabs.tsx` / shared-layout-shell / AdminTabs-as-sub-tabs refactor until workflow #2's shape is known. Rationale: smaller blast radius, matches preservation-over-refactor (Op Stds §14) and the operator's explicit revertability ask; Phase A is identical either way.
- **Verify-before-touch automation-safety audit.** Per the operator's "verify the rebrand won't break automation" gate, ran a 5-lens adversarial Workflow (Python daemons / Worker backend / send-gate authority / test-CI / runtime-deploy), each lens re-checked by a skeptic. Verdict: **SAFE** — all 5 lenses `safe`, all 5 refutations held (`missed_break: none found`). The daemons ↔ Worker contract is `/api/internal/*` JSON + HMAC computed over data-only fields (submission_uuid/job_id/form_code/work_date/payload_json); nothing in `shared/**`, `safety_reports/**`, `scripts/**`, or the Worker reads the page title, favicon, logo, or any brand string. A grep-proof confirmed zero DO-NOT-TOUCH names appear in the diff.
- **Audit caveat resolved, not carried.** The audit's "67-byte placeholder PNGs" note was an audit agent's own throwaway scratch (real assets weren't in place when it ran) — `main` was confirmed pristine and the real assets (6.3K/77K/133K, verified dimensions) were used. The "atomicity" caveat is satisfied because the rename is one commit.
- **dev-server CSP blank-render is a pre-existing dev-only quirk, not a regression.** `vite dev` rendered blank because the Worker's `script-src 'self'` CSP blocks Vite's inline HMR preamble (cascading into `@vitejs/plugin-react can't detect preamble`). This pre-dates the change (the Worker CSP was untouched) and does not affect production (external hashed scripts). Visual verification was therefore done against the production build served statically, and later against the live deploy.
- **typecheck is the lint gate.** No eslint config exists in `safety_portal/`; `npm run typecheck` (clean) satisfies the brief's "lint clean" DoD.
- **Merged without `--delete-branch`.** Kept the feature branch + worktree as part of the revert net (operator asked to preserve revertability through merge + deploy).
- **Deployed with a recorded rollback anchor.** Captured the live Worker version before deploying so `wrangler rollback` can revert the deployment independent of git.

## Open items handed off

- **Deferred cleanup (operator-run, once the live site is confirmed stable):** remove the worktree `~/its-portal-rebrand` and branch `feat/its-portal-rebrand` (the `docs/session-log-its-portal-rebrand` branch/worktree too after this log merges); `git branch -D` is hook-blocked, so use `git update-ref -d refs/heads/<branch>` after a PR=MERGED check (see [[reference_git-branch-cleanup-hook-bypass]]); delete the backup tag `backup/pre-its-portal-rebrand` and the tarball when no longer wanted.
- **Phase B (top-level "Safety Forms" workflow nav)** remains a future pass — build `WorkflowTabs.tsx` + lift `AppHeader` into a shared `App.tsx` layout shell + demote `AdminTabs` to sub-tabs when workflow #2 is specified.
- **Reference assets kept beside the bundle (not committed):** `its-logo-full.png` (full canonical mark incl. outer frame + "Integrated Technical System" line — letterhead/large uses); `mock-B-engraved-caps.png` (alt "Portal" typography, a swap option if ever desired). The crest source of truth is Canva design `DAHKnvZM10M`.

## What was NOT touched

- No Python (`shared/**`, `safety_reports/**`, `scripts/**`), no Worker TypeScript (`safety_portal/worker/**`, `src/lib/**`), no D1 migrations, no HMAC, no API route paths.
- No DO-NOT-TOUCH load-bearing names: the `ITS — Safety Portal` Smartsheet workspace (the F22 send-gate authority = its share list), `wrangler.jsonc` `"name": "its-safety-portal"` + route `safety.evergreenmirror.com`, `package.json` `"name"`, the Box mirror tree, daemon names — all byte-identical (grep-proof clean).
- `evergreen-logo.svg` retained (AppHeader still uses it); the favicon swap to `favicon.png` left no dangling reference.
- External Send Gate (Invariant 1) and the 6-layer adversarial-input handling (Invariant 2): unchanged — the rebrand adds no send or ingestion code.
- Phase B1 (the nav/layout refactor): deliberately not built (see decisions).

## Lessons captured to memory

- **`project_safety_portal_state.md`** + **`MEMORY.md`** index updated to record the rebrand as MERGED + DEPLOYED LIVE (main `8162709`, Worker `9436d4fe`), with the full revert recipe preserved verbatim (deploy rollback to `d57666e4`; code revert `8162709` / tag `1b011f8` / tarball / worktree). Takeaway for a fresh session: the live mirror now serves "ITS Portal"; the safety-portal internal names (workspace/wrangler/package/Box) are unchanged and remain load-bearing.
- No new standalone memory was warranted — the change exercised existing, already-recorded patterns: worktree isolation ([[exec-host-worktree-daemon-topology]]), the Cloudflare custom-domain/workers.dev gotcha context, and the squash-merge branch-cleanup rule ([[reference_git-branch-cleanup-hook-bypass]]).

## Deploy

`cd safety_portal && npm run deploy` → live Worker version **`9436d4fe-db7a-4112-96e5-77b47701c15f`** at `safety.evergreenmirror.com` (6 assets uploaded incl. the 3 new PNGs). Pre-deploy rollback anchor: version `d57666e4-aae7-4b26-a547-22e28097031e`. Live smoke: `/`, `/favicon.png`, `/its-portal-header.png`, `/its-logo.png`, `/evergreen-logo.svg` all HTTP 200; served `<title>ITS Portal</title>` + favicon `/favicon.png`; Playwright desktop screenshot of the live site confirms the maximalist header renders per Mock A. One benign console error: the Cloudflare Web-Analytics beacon blocked by `script-src 'self'` (app unaffected; same as the 2026-06-08 CSP note).
