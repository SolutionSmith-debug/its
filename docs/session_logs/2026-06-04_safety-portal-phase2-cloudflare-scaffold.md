---
type: session_log
date: 2026-06-04
status: closed
related_prs: [158]
workstream: safety_portal
tags: [safety-portal, cloudflare, workers, hono, react, vite, svg-signature, d1, auth, typescript, phase2]
---

# Session log — Safety Portal Phase 2: Cloudflare scaffolding + minimal portal

Built the Safety Portal Phase 2 application layer — a self-contained Cloudflare Worker
serving a Vite + React 19 SPA with same-origin `/api/*` auth endpoints, hand-rolled
SVG-vector signature pad, and BRG/gold design system. Landed as PR #158. Zero Python
touched; Op Stds §14 preserved throughout.

## Commits / PRs landed

- **PR #158 — feat(safety-portal): Cloudflare scaffolding + minimal portal (Phase 2)** —
  squash `fe615db8`. New self-contained `safety_portal/` tree (39 files, +4395 lines).
  Single Cloudflare Worker (Hono framework) serves a Vite + React 19 SPA via Workers
  Static Assets with same-origin `/api/*` routes (login/session/logout). Auth: D1 `users`
  table (migrations/) + bcryptjs cost 10 + HMAC-signed session cookie (HttpOnly/SameSite=Lax/
  90-day; constant-time verify via `crypto.subtle`). Hand-rolled SVG-vector signature pad
  (Pointer Events API; canvas-lib alternatives emit raster and fail the mission's
  "vector not raster" requirement). Design system: BRG/gold, WCAG AA verified; gold
  decorative-only. 10 reference PDFs committed (Phase 4 source-of-truth, pulled from
  Box `ITS DATA/Safety Sheets/`). Evergreen logo fetched from their live site.

## CI runs / four-part verify

PR #158 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-05T01:40:00Z
- mergeCommit: fe615db8296d1c00aefe53af92d9e7ce73192032
- main CI on merge commit: SUCCESS
  - workflow: ci — run 26990163982 — conclusion: success
  - workflow: CodeQL — run 26990168266 — conclusion: success
  - workflow: CodeQL — run 26990163707 — conclusion: success

Local validation gate before merge (no Cloudflare token; local D1 via `wrangler dev --local`):

- TypeScript typecheck (client + worker split): clean
- `vite build` (SPA + Worker bundle): clean (added `compatibility_flags: ["nodejs_compat"]`
  because bcryptjs imports `node:crypto`)
- pytest: 121 tests collect clean; full pytest + ruff ran green in merge-commit CI `test` job
- ruff: All checks passed (Python files unmodified)
- main-branch CI on merge commit: SUCCESS

Local e2e via Miniflare (local D1): login → 200 + signed cookie; session w/ valid cookie → 200;
bad login → 401; no cookie → 401; tampered/flipped/garbage/empty cookie → 401 (HMAC integrity
verified); SPA shell + deep-link fallback → 200; logo asset → 200.

Playwright UI smoke: login form → home → JHA stub → drew a stroke → captured 414 chars of SVG
path data (M…L…), "Signature captured" status confirmed.

gitleaks (staged): no leaks found.

## Decisions made during session

1. **Workers Static Assets over Cloudflare Pages.** Alternative considered: Cloudflare Pages
   (named in blueprint topology §11, including the `*.pages.dev` domain convention). Rejected:
   a cloudflare-docs MCP research pass confirmed Cloudflare now recommends Workers Static Assets
   for new projects; Pages is on maintenance trajectory. The application code is deploy-agnostic.
   The Pages-vs-Workers choice (and the DNS question of whether a domain may already point to
   `pages.dev`) is flagged in `safety_portal/README.md` as a deploy-time operator decision —
   the blueprint topology cite is preserved as context rather than silently superseded.

2. **Deploy deferred to the token step; local-validate now.** Alternative considered: obtain a
   `CLOUDFLARE_API_TOKEN`, run `wrangler deploy`, provision D1/R2 live, validate against real
   Cloudflare infrastructure this session. Operator decision: local validation is sufficient
   for Phase 2 scaffolding; provisioning (D1/R2/Pages create, `secret put`, custom domain) and
   real-SSL validation are deferred to the explicit deploy session. Everything that can be
   verified without a token was verified.

3. **bcryptjs cost 10 as specified, with Workers CPU-cap caveat documented.** Alternative
   considered: swap to PBKDF2-SHA-256 at 100k iterations now to stay within the Workers FREE-plan
   10ms CPU cap. Rejected this session: the mission brief (Phase 2 §Q2) specifies bcrypt cost 10
   literally; substituting preemptively would deviate from the spec. The CPU-cap risk (Error 1102
   on the Free plan) is documented in `safety_portal/README.md` with the mitigation options
   (deploy on Paid plan OR swap to PBKDF2-SHA-256 @100k iters). Operator resolves at deploy
   time, not build time.

4. **Hand-rolled SVG-vector signature pad, rejecting canvas library alternatives.** Alternative
   considered: `signature_pad` npm package or similar canvas-based library (raster output).
   Rejected: the Safety Portal mission explicitly requires vector signature capture (SVG path
   data, not raster image). Canvas libs emit raster; hand-rolling with the Pointer Events API
   and `<path d="…">` accumulation satisfies the mission requirement with no external dependency.
   Playwright smoke confirmed 414 chars of SVG path data captured from a single stroke.

5. **Validation seed user (test.pm / portal-dev-2026) committed in migration 0002 with a
   do-not-apply-to-production marker.** Alternative considered: excluding the seed from the
   committed tree entirely; or committing without a production guard. Rejected both: the seed is
   required for local CI / validation D1 and must be in the tree; it must also never reach
   production. Resolution: migration 0002 is marked `-- DO NOT APPLY TO PRODUCTION` in the SQL
   comment header and documented as local/validation-only in `safety_portal/README.md`. This is
   an explicit non-secret (the value is dev-only; real user provisioning is a Phase 7 admin
   route + D1 session table).

6. **Adversarial multi-agent review before merge (ops-stds-enforcer + security + frontend +
   config dimensions + adversarial verify stage).** Result: 0 critical / 0 high / 0 confirmed
   blockers. Applied fixes before merge: §42 four-headed docstrings added to `worker/index.ts`
   and `worker/auth.ts`; session-revocation gap documented (no server-side revocation until
   Phase 7); future-`iat` session guard noted; `e.currentTarget.setPointerCapture` corrected
   in the signature pad; footer-string fidelity corrected; login input `maxLength` added;
   corrected a misleading "no nodejs_compat" inline comment. Send-gate verified: Worker imports
   only Hono + bcryptjs; only fetch is the bound `ASSETS` service binding — no `send_mail`,
   no Anthropic client, no SMTP path.

7. **`nodejs_compat` compatibility flag required.** bcryptjs internally imports `node:crypto`
   for `randomBytes`. Without `compatibility_flags: ["nodejs_compat"]` in `wrangler.toml`, the
   Worker bundle fails at runtime. Discovered during `vite build`; added to config and
   documented. This is a known Cloudflare Workers pattern for any package that uses Node.js
   built-ins.

8. **Form-catalog corpus diverges from blueprint's 4 named forms; flagged, not resolved.**
   The 10 reference PDFs committed include forms not in the blueprint's canonical list (adds
   "HSS&E Work Observation" + "Visitor Sign-In"; the blueprint lists "Daily Site Safety
   Worksheet" which is absent from the corpus). Alternative considered: reconcile the corpus
   to the blueprint list before committing. Rejected: form-catalog reconciliation is a Phase 4
   concern (the PDFs are source-of-truth for that phase); the mismatch is recorded in
   `docs/tech_debt.md` and in this log as a forward open item.

## What was NOT touched

- No Python files modified — Op Stds §14 preserved; ruff + pytest unchanged from prior state.
- Invariant 1 (External Send Gate) and Invariant 2 (Adversarial Input Handling) mechanics
  not touched; the Worker has no send capability and processes no external untrusted input
  in this phase.
- No Cloudflare resources provisioned (no `wrangler deploy`, no D1 create, no R2 create,
  no secret put) — local-only validation this session.
- The Python-side capability-gate test (`tests/test_capability_gating.py`) was not updated —
  the Safety Portal Worker is a TypeScript/Cloudflare surface; the AST gate applies to Python
  scripts only. A Worker-side equivalent gate is a Phase 5 deferred item.
- No launchd plists added — the portal runs its own hosting model (Cloudflare), not
  launchd-triggered scripts.
- The existing `safety_reports/intake.py` HMAC shim integration (portal-noreply → unified
  `safety@`, `X-ITS-Portal-HMAC` trust boundary) was not built — that is the next integration
  phase, following live deploy.
- `lint_doc_conventions.py` workstream set was not updated to include `safety_portal` (this
  is a pre-existing lint warning, not introduced by this session; carried forward as open item).

## Open items handed off

- **Provision + deploy (operator, needs CLOUDFLARE_API_TOKEN).** Steps: create D1 database,
  create R2 bucket (if needed), run `wrangler d1 migrations apply`, `wrangler secret put
  SESSION_SECRET`, `wrangler deploy`. Operator resolves Pages vs. Workers Static Assets
  at deploy time (see `safety_portal/README.md` deploy section and Decision 1 above).
- **bcrypt CPU-cap resolution at deploy time.** On the Cloudflare Free plan, bcrypt cost 10
  can exceed the 10ms CPU limit (Error 1102). Options: upgrade to Paid, or swap to
  PBKDF2-SHA-256 @100k iterations. Operator decides when provisioning (see `safety_portal/README.md`).
- **Worker-side capability-gate equivalent (Phase 5).** The Python AST gate
  (`tests/test_capability_gating.py`) does not cover the TypeScript Worker. A TypeScript-level
  import check (or a CI step using `grep` over `worker/`) should gate the send path before
  the Worker gains any send capability in Phase 5.
- **Form-catalog reconciliation before Phase 4.** The 10 committed PDFs include forms not in
  the blueprint's canonical 4-form list; "Daily Site Safety Worksheet" is absent from the
  corpus. Reconcile before Phase 4 form-rendering work begins.
- **Session revocation + deprovisioning (Phase 7).** No server-side session revocation is
  built; cookies are HMAC-valid until the 90-day expiry. Phase 7 adds an admin route + D1
  session table. Documented in `safety_portal/README.md`.
- **Frontend build/lint CI step deferred.** A `pnpm run typecheck && pnpm run build` CI job
  covering the `safety_portal/` tree would catch TypeScript regressions earlier. Deferred;
  tracked in `docs/tech_debt.md`.
- **Add `safety_portal` to `lint_doc_conventions.py`'s workstream set.** Pre-existing lint
  warning; now a more pressing gap given this session added 39 Safety Portal files.
- **`safety_portal/README.md` as deploy runbook.** The README documents Pages-vs-Workers,
  bcrypt caveat, production migration guard, and secret provisioning. Review and update this
  document at the start of the deploy session.

## Gotchas worth recording for future sessions

- **`nodejs_compat` flag is required for bcryptjs.** Any Cloudflare Worker that imports
  bcryptjs (or any package using `node:crypto`) needs `compatibility_flags: ["nodejs_compat"]`
  in `wrangler.toml`. Without it, the bundle fails at runtime, not at build time.
- **Canvas libs emit raster, not vector.** `signature_pad` and similar packages capture to
  `<canvas>` and export PNG/JPEG or Base64-encoded raster. The Safety Portal mission requires
  SVG path data (vector). The hand-rolled Pointer Events + `<path d>` accumulation in
  `safety_portal/client/components/SignaturePad.tsx` is the only solution that satisfies this
  requirement.
- **Migration 0002 is local-only.** The seed row (`test.pm` / `portal-dev-2026`) is in the
  committed tree but must not be applied to production D1. The `-- DO NOT APPLY TO PRODUCTION`
  header is the gate; the deploy runbook in `safety_portal/README.md` explicitly lists
  migration 0001 only for production apply.
- **Four-part verify pattern for TypeScript PRs.** The four-part verify legs still apply
  (state/mergedAt/mergeCommit/main-CI), but leg 3 of the local gate (pytest/mypy/ruff) covers
  only the Python surface. TypeScript verification (typecheck + vite build + e2e) is the
  Safety Portal–specific equivalent and must be run explicitly before merge.

## Lessons captured to memory

- Memory entry `session-2026-06-03-picklist-e1-state.md` remains the prior-state anchor; this
  session is a new phase (Safety Portal Phase 2, different surface). No update to prior memory
  entries needed — this log is the forward anchor for Phase 2 state.
- The Workers Static Assets vs. Pages decision (Decision 1) is an operator-visible open item;
  the blueprint topology cite (`../its-blueprint/workstreams/safety-portal/mission.md` §11)
  may need a version-bump in a future planning session to reflect the Cloudflare recommendation
  shift.

## Cross-references

- Prior safety_portal session log:
  [`2026-06-03_safety-portal-config-sheets-and-alignment-audit.md`](2026-06-03_safety-portal-config-sheets-and-alignment-audit.md)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1
- Op Stds v16 §14 (preservation-over-refactor; zero Python touched)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD; applied to Worker)
- `docs/operations/pr_merge_discipline.md` — four-part verify; adapted for TypeScript surface
- `docs/tech_debt.md` — form-catalog corpus mismatch (new); frontend CI step (new)
- `safety_portal/README.md` — deploy runbook + bcrypt caveat + migration guard
- `safety_portal/migrations/` — 0001 (schema), 0002 (local seed only)
