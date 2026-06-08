---
type: session_log
date: 2026-06-08
status: closed
related_prs: [193, 194, 195, 197]
workstream: safety_portal
tags: [admin-dashboard, role-model, submit-as, form-archive, security-audit, hardening, phase-2-grill]
---

# Session log — Safety Portal Admin Dashboard (Phase 1) + security audit + post-audit hardening

A long session: built + activated the **Admin Safety Dashboard Phase 1** (4 PRs), ran an
**adversarial security audit** of the live mirror, executed the **post-audit hardening** (this
PR), and **grilled out the Phase-2 form-editor design** (a feeder brief). Parallelized heavily
with background agents + Workflow orchestration.

## What landed (Phase 1 — all four-part verified)

| PR | Scope | Merge commit |
|----|-------|------|
| **#193** | GLOBAL: submission confirmation shows the job, not just the date (all users) | `4c672b0` |
| **#194** (PR-L) | Fillable blank-form archive → Box `00_Form_Archive` (manual-fallback) | `9d998d2` |
| **#195** | Role foundation + Tab 2 account management + `@cloudflare/vitest-pool-workers` + a Node CI job | `584cc60` |
| **#197** | Tab 1 "filled out as" submit-as with dual-attribution | `21bd909` |

**Four-part verify (each PR, at merge):** `state=MERGED` / `mergedAt` set / `mergeCommit.oid` present / main-branch CI (ci + CodeQL) **SUCCESS** on the merge commit. PR #195's CodeQL flagged a HIGH `py/clear-text-logging` alert (#14) on `portal_admin.py` `cmd_list_users` — a confirmed **reincarnation of the already-dismissed #13** (the admin bearer taints the `admin_request` return; the listed username/role/flag carry no secret). Operator dismissed #14; PR merged clean.

### Key Phase-1 design decisions (built)
- **Role is read fresh from D1 per request** (`requireSession` SELECTs `disabled, role`), NOT baked into the cookie → a demotion is effective immediately. `requireRole("admin")` gates the in-app `/api/admin/*` surface (distinct from the bearer `/api/internal/admin/*` operator CLI).
- **Last-admin guard is ATOMIC** — the "only enabled admin?" count subquery lives inside the demote/delete `WHERE`, with a `changes()`-conditional audit row, so it can't be raced into stranding zero admins.
- **Submit-as** records dual attribution (true actor + attributed) on the submission row + an `audit_log` `submit_as` event; the **canonical HMAC payload + `/api/internal/pending` columns are UNCHANGED**, so `portal_poll`/`intake`/downstream are byte-unchanged.
- **CI now covers the Worker TS** (the Python `test` job never did) via a `portal` job (`npm ci` → `tsc` ×3 incl. a `tsconfig.test.json` → `vitest` against real workerd + Miniflare D1).

### Activation (operator-gated, completed on the mirror)
Migrations **0007** (`users.role` + `audit_log`) + **0008** (`submissions.actor_username`/`submitted_as`) applied to the live D1 **before** `npm run deploy` (order-critical — caught a deploy-before-migrate mishap where a multi-line paste split `--remote`, briefly 401-ing the live portal until the remote migration landed). Two admins provisioned `role=admin` (`stephens.jacob`, `finkhousen.ben`). End-to-end live smoke green: admin login → `role:admin`, `/api/session` role, `/api/admin/users` 200 with cookie / 401 without.

## Security audit (grey-box adversarial, live mirror)

6 attack dimensions × verify-each, via the Workflow tool. **Core posture HELD:** injection 0/4 (bound params; no ReDoS; no JSON/header injection), no auth bypass (HMAC cookie unforgeable), no privilege escalation, and the atomic last-admin guard survived a live TOCTOU race. 12 findings, all real, none critical — see `docs/tech_debt.md` (2026-06-08 audit entry).

## Post-audit hardening (THIS PR)

Closes the 11 code/config findings; defers **#7** (session-epoch revocation) to Phase-2. Perimeter hardening only — **Worker stays SEND-FREE; no migration**:
- **#1** null/non-object body → 400 (per-handler body-shape guard on all 12 handlers + global `app.onError`, no stack leak, no Sentry-page on unauth noise).
- **#4** `values:[]` → 400 (`Array.isArray` reject).
- **#2/#3/#8–11** security headers via Hono `secureHeaders()` + `run_worker_first:true` (reaches the SPA document): XFO:DENY, nosniff, Referrer-Policy, HSTS, `Cache-Control:no-store` on `/api/*`, and **CSP REPORT-ONLY** (loosened for React inline styles + the inline-SVG signature; built `index.html` has no inline script).
- **#5/#6** concurrency error codes: UNIQUE-race → 409 (not 500); delete/demote `changes()==0` disambiguated to 404 vs 409 `last_admin` (guard unchanged).
- **Rider:** AccountsPage edit-login editor closes on a no-change Submit.

42 vitest tests (real workerd + D1). **Activation remaining (operator-gated):** `npm run deploy` + a live re-probe of the audit vectors + **flip CSP from Report-Only to enforcing after a signature-capture smoke** (load SPA → log in → render a form with signature → submit → zero CSP console violations). Confirm Cloudflare isn't already emitting HSTS (avoid duplication).

## Phase-2 form editor — grilled + brief drafted (NOT built)

A full grill resolved the Phase-2 "Tab 3" form editor + Session Hardening design: git-source-of-truth + a git-committed catalog manifest; closed vocabulary (editor = safety boundary); 4 ops (create-blank / **Edit** = version-bump + auto-swap-and-retire / **Add-version** = clone-as-template → rename → parallel / Delete = retire); fully-automatic Mac-gated publish pipeline + status monitor; Box historical archive as a DR fallback; role-aware session hardening (5-min admin idle + #7). Captured in the Phase-2 design brief (feeder for the canonical mission, which lands in the blueprint per doctrine). A red-team Workflow surfaced 3 real architectural gaps (amend-continuity across version bumps; the SPA-vs-Mac renderer skew window; the cloud-deploy-doesn't-land-the-file-on-the-Mac chain) to fold into the brief.

## Notes for next session
- The hardening **deploy is held** for the operator (CSP enforce-flip needs a human smoke).
- Worktrees to clean (operator; force-delete is hook-blocked in CC): `~/its-admin`, `~/its-formarchive`, `~/its-submitas`, `~/its-harden`, `~/its-brief`; `~/its` should be pulled to `main`.
- Phase-2 is brief-only; building it needs the operator's `⚙ CONFIRM` decisions + doctrine (Session B) + live deploys.
