# ITS Safety Portal

Web portal for Evergreen field PMs to submit daily safety paperwork directly —
replacing the inbox-and-PDF path. Field PMs log in, fill structured forms, capture
signatures on-screen, and (later phases) submit into the existing `safety_reports`
intake pipeline.

- **Planning docs (canonical):** `../../its-blueprint/workstreams/safety-portal/mission.md`
  (v1) + `brief.md`. Decisions Q1–Q10 are locked there.
- **This is the TypeScript / Cloudflare workstream** — it does **not** follow the
  Python `safety_reports/` shape.

> **Phase 2 scaffold (this directory).** Deployable Cloudflare skeleton + a minimal,
> themed, end-to-end slice: **login → home → one hard-coded JHA stub**. No submission,
> no PDF, no email, no Smartsheet — those land in later phases (see
> [What's stubbed](#whats-stubbed--out-of-scope)).

---

## Pending live activation (operator punch-list)

One table, one command block — the consolidated view of which shipped D1 migrations are
applied on the **live** D1 vs still pending. This exists because a Worker deployed ahead of
its migration fail-closes `resolveCapabilities` → the universal-lockout class (2026-06-28).

| Migration | Slice | PR | Applied live? |
|---|---|---|---|
| `0023_manager_role` | P2.6 Manager tier — [section](#manager-tier--third-portal-role-p26--0023) | #398 | ✅ |
| `0024_index_personnel_current_job` | Unified job-create — [section](#unified-job-create-flow--crew-converges-on-placement-0024) | #402 | ✅ |
| `0025_manager_task_assign` | S1 Assigned-Tasks — [section](#assigned-tasks--manager-task-authority-0025--checklist-engine-0026) | #406 | ✅ 2026-07-02 |
| `0026_checklist_engine` | S2 checklist engine — [section](#assigned-tasks--manager-task-authority-0025--checklist-engine-0026) | #407 | ✅ 2026-07-02 |
| `0027_subcontractor_crew_create` | Slice T subcontractor tier — [section](#subcontractor-tier--scoped-crew-create--time-scoping-0027) | #412 | ✅ 2026-07-02 |
| `0028_sop_checklist_content` | SOP content seed — [section](#sop-checklist-content-seed-0028) | #414 | ✅ (R-series deploy) |
| `0029_checklist_instance_template_title` | R1 worker contracts — [section](#assigned-tasks-r1--instance-template-title-0029--worker-contract-fixes) | #416 | ✅ (R-series deploy) |
| `0030_job_daily_requirements` | D4 per-job daily-form requirements — [section](#per-job-daily-form-requirements-d4--0030) | #427 | ☐ pending |
| `0031_job_expected_materials` | M1 expected materials — [section](#expected-materials--per-job-receipt-list-material-receipts-m1--0031) | #426 | ☐ pending |
| `0032_job_daily_requirements_kinds` | D5 requirement kinds (number/date/select) — [section](#requirement-kinds-widened-d5--0032) | #435 | ☐ pending |

Canonical apply-and-deploy sequence (applies **all** pending migrations, in order — never a
subset):

```bash
cd ~/its && git pull origin main   # ALWAYS first — the stale-migrations-list lockout class
cd safety_portal
npx wrangler d1 migrations apply its-safety-portal-db --remote
npm run deploy
```

Each linked per-slice **Activation** section carries that slice's post-deploy live smoke.

> **Convention:** every future slice that ships a migration adds one row here (unchecked) in
> the same PR; the operator flips it to ✅ (with the date) at cutover. Rows older than `0023`
> predate this table and are all long since applied — see the per-slice sections below.

---

## Architecture

A **single Cloudflare Worker** serves the built React SPA (static assets) **and**
handles same-origin `/api/*` routes — zero CORS, one deployment unit.

| Layer | Tech |
|---|---|
| Frontend | Vite + React 19 (`src/`, `index.html`) |
| Backend | Cloudflare Worker via **Hono** (`worker/`) |
| Bundler | `@cloudflare/vite-plugin` (runs the Worker in dev, builds both for deploy) |
| Auth | D1 `users` table + `bcryptjs` (cost 10); HMAC-signed session cookie |
| Database | Cloudflare D1 (`migrations/`) |
| PDF storage | **Box** (system of record). No R2 — under Option-B render the Worker never holds a PDF; `intake.py` renders + stores it in Box. |

> **Deploy target (historical note):** live as a **Workers + Static Assets** deploy at
> `https://safety.evergreenmirror.com` since 2026-06-08 (the pre-first-deploy Workers-vs-Pages
> reconciliation resolved to Workers — Pages is in maintenance mode). Note `custom_domain: true`
> in `wrangler.jsonc` disables the `*.workers.dev` URL on deploy (error 1042).

---

## Layout

```
safety_portal/
  index.html              # SPA entry (Vite root)
  src/                    # React SPA (client)
    pages/                # LoginPage, HomePage, JhaStubPage
    components/           # AppHeader, SignaturePad (SVG-vector capture)
    lib/                  # api.ts (fetch wrappers), auth.tsx (AuthContext)
    styles/               # tokens.css (design system), global.css
  worker/                 # Cloudflare Worker (Hono): /api/login, /api/session, /api/logout
  migrations/             # D1 schema + seed (0001 users, 0002 validation user)
  public/                 # static assets (evergreen-logo.svg)
  reference_forms/        # the 10 source PDFs — Phase-4 source-of-truth (see its README)
  wrangler.jsonc          # Worker + assets + D1 bindings (NO secrets; no R2 — PDFs live in Box)
  vite.config.ts · package.json · tsconfig*.json
  .dev.vars.example       # local secret template (copy to .dev.vars, gitignored)
```

---

## Local development (no Cloudflare token required)

`vite dev` / `wrangler dev` run fully on Miniflare with D1 simulated locally —
**no Cloudflare account or token needed.**

```bash
cd safety_portal
npm install

# 1. Local signing secret (gitignored)
cp .dev.vars.example .dev.vars
#    then put a real value in it:  openssl rand -base64 48

# 2. Apply migrations to the LOCAL D1 (creates users + seeds the validation user)
npm run db:migrate:local

# 3. Run it
npm run dev            # Vite dev server (HMR) — http://localhost:5173
#   …or a production-like local serve of the built Worker + assets:
npm run build && npx wrangler dev --local   # http://localhost:8787
```

### Seeded validation credential (local / validation only)

```
username:  test.pm
password:  portal-dev-2026
```

This is a **throwaway, documented dev credential** seeded by `migrations/0002`. It
unlocks only a local/validation D1 that does not exist in production. **Do not apply
`0002` to production** — real field PMs are provisioned via the Phase 7 admin route.

### Useful scripts

```bash
npm run typecheck       # tsc for client + worker (strict)
npm run build           # vite build -> dist/client (SPA) + dist/<name> (Worker)
npm run db:query:local "SELECT * FROM users;"
```

---

## Deploy (operator — requires CLOUDFLARE_API_TOKEN)

Deferred this session (built + validated locally first). When ready, with a token
scoped to **Workers + D1 edit** exported as `CLOUDFLARE_API_TOKEN`
(+ `CLOUDFLARE_ACCOUNT_ID`):

```bash
cd safety_portal
npx wrangler d1 create its-safety-portal-db          # -> paste database_id into wrangler.jsonc
npx wrangler d1 migrations apply its-safety-portal-db --remote   # users + seed (validation env)
npx wrangler secret put SESSION_SIGNING_SECRET       # paste `openssl rand -base64 48`
npm run deploy                                       # vite build && wrangler deploy
#   -> https://its-safety-portal.<account>.workers.dev
```

Then attach the custom domain `safety.evergreenmirror.com` (dashboard / `routes`) —
see [the reconciliation note](#deploy-target-workers-static-assets-vs-pages-reconciliation).

> **Plan caveat (bcryptjs):** a cost-10 `bcrypt.compare` can exceed the Workers **Free**
> plan's 10 ms CPU limit (Error 1102). The deployed Worker must be on the **Paid** plan,
> or swap `worker/auth.ts` to Web-Crypto **PBKDF2-SHA-256 @100k iters** (the documented
> Workers-constrained substitute for bcrypt). Honoring the mission's literal "bcrypt cost
> 10" is why bcryptjs is used here.

### Production hardening — operator cutover steps (Part-A findings)

The in-code hardening (**A1** idempotent submit id, **A3** daily D1 prune cron) ships in the
Worker and needs no operator action. Two items require the Cloudflare **dashboard/account** at
cutover — do them on the production account (a fresh account defaults to the **Free** plan):

- **A5 — Workers plan go/no-go (BLOCKER).** `/api/login` runs `bcrypt.compare` at cost 10,
  which can exceed the Workers **Free** 10 ms CPU cap (Error 1102) → a total login outage.
  **Confirm the production Worker is on the Workers Paid plan before go-live.** If Paid is not
  available, the documented Workers-constrained substitute is **PBKDF2-SHA-256 @100k iters** in
  `worker/auth.ts` — but that changes the mission-locked "bcrypt cost 10" parameter and needs a
  password-rehash migration, so it is **developer + doctrine work, not a cutover toggle** (surface
  to Seth; do not swap silently).

- **A2 — rate limiting (add at cutover).** Nothing throttles `/api/login` (brute-force + bcrypt
  CPU-cost amplification) or `/api/*` (unbounded). Add Cloudflare **rate-limiting rules** in the
  dashboard (Security → WAF → Rate limiting rules): a tight rule on `/api/login` (e.g. ~5 req /
  10 s per IP → block ~10 min) and a looser blanket rule on `/api/*`. The in-code alternative is
  the Workers **`ratelimit` binding** (`wrangler.jsonc` + per-route `.limit()`), reproducible +
  testable in-repo — adopt it if it is GA for the account at deploy time; until then the
  dashboard rule is the cutover step (re-create it on any new account).

### Secrets

All secrets are Workers Secrets / `.dev.vars` — **never committed**. Phase 2 needs only
`SESSION_SIGNING_SECRET`. Later phases add `HMAC_PAYLOAD_SECRET`,
`PORTAL_INTERNAL_API_TOKEN` (the poller's bearer), and `PORTAL_ADMIN_API_TOKEN`
(the operator-only admin bearer — **separate** so the poller's token can't provision
users) with macOS Keychain mirrors per ITS convention.

---

## Phase 7 — operator user provisioning + session revocation

Users are **operator-provisioned** (NOT self-service; no user-role model — brief §4).
The operator passes plaintext over a bearer-gated admin channel; the **backend
bcrypt-hashes** (cost 10) before write — plaintext is never stored, returned, or logged.

**Routes** (`/api/internal/admin/*`, gated by `requireAdminToken` = `PORTAL_ADMIN_API_TOKEN`,
which is **separate** from the poller's `PORTAL_INTERNAL_API_TOKEN`):
`POST users` (provision, 409 if exists) · `POST users/reset` · `POST users/disable` ·
`POST users/enable` · `GET users` (no hashes).

**Revocation:** `requireSession` reads `users.disabled` per request (migration 0006) and
401s a disabled/deleted user immediately — fail-closed (a D1 error also → 401). The
cookie stays valid cryptographically, but the lookup gates it.

**Operator CLI** (Mac, not a daemon): `python -m safety_reports.portal_admin <cmd>`
— `add-user <lastname.firstname>` / `reset-password <u>` / `disable-user <u>` /
`enable-user <u>` / `list-users`. Reads the Worker URL from ITS_Config + the admin
bearer from Keychain `ITS_PORTAL_ADMIN_TOKEN`; passwords via `getpass` (confirmed twice).

### Activation punch-list (operator — needs Cloudflare/Keychain auth)

The Worker/admin/migration code sits **inert** in the repo until activated. The
Box-409 fix + sheet-styling (PRs G/I, Python-only) activate on a plain `~/its` pull;
the admin route needs:

1. Set `PORTAL_ADMIN_API_TOKEN` (Worker secret) + `ITS_PORTAL_ADMIN_TOKEN` (Keychain),
   **byte-equal** (`openssl rand -hex 32`; `wrangler secret put` + `security add-generic-password -U -a "$USER" -s ITS_PORTAL_ADMIN_TOKEN -w`).
2. Apply migration **0006** to live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the
   `requireSession` disabled-read errors and 401s every session.
3. **Redeploy** the Worker (`npm run deploy`) — activates the admin routes + revocation.
4. Provision real users: `python -m safety_reports.portal_admin add-user lastname.firstname`.
5. (Optional) custom domain — see PR-J's `wrangler.jsonc` `routes` (dashboard add or `wrangler deploy`).

> **This is the secrets/auth boundary** — review the admin diff before activating.

---

## Admin dashboard (Phase 1 — role model + in-app account management)

Adds an in-browser admin surface for the two admins (CEO + head PM) on top of the
operator CLI above. **Migration 0007** adds `users.role` (`submitter` default | `admin`)
+ an `audit_log` table.

**Role is read fresh from D1 per request** (`requireSession` now `SELECT`s `disabled, role`),
**not** baked into the cookie — a demotion takes effect on the next request (same reasoning
as the per-request `disabled` check). `/api/login` + `/api/session` return the role so the
SPA can show/hide the admin tabs; that is display-only — every admin route is re-gated
server-side by `requireRole("admin")`.

**In-app surface** (`/api/admin/*`, gated by `requireSession` + `requireRole("admin")` —
SESSION+role, distinct from the bearer `/api/internal/admin/*`): `GET users` ·
`POST users` (create, role selectable) · `POST users/credentials` (edit username/password —
self-edit clears the cookie → re-login) · `POST users/role` (change role) ·
`POST users/delete`. Each mutation + its `audit_log` row run in one atomic D1 batch.

**Last-admin guard** (operator's call, ON): the session routes refuse to demote / delete the
**only enabled admin** (`409 last_admin`). The bearer operator routes are deliberately **NOT**
guarded — they are the break-glass path *out* of a zero-admin lockout (see below).

**Tab 1 "filled out as" (submit-as)** is a separate later slice — not in this PR.

**CLI:** `portal_admin add-user <u> --role admin` bootstraps an admin; `portal_admin set-role
<u> submitter|admin` is break-glass for the role model.

### Activation (operator — needs Cloudflare/Keychain auth; on the LIVE portal)

Mirrors the Phase-7 punch-list. The `worker_base_url` already points at the custom domain —
do **not** re-point.

1. Apply migration **0007** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the
   `requireSession` `role`-read errors and (fail-closed) 401s every session. **ORDER-CRITICAL**,
   same rule as 0006.
2. **Redeploy** (`npm run deploy`).
3. **Regression-check the LIVE portal:** existing users still log in + submit (role defaults
   `submitter`; existing accounts keep access; the admin routes are additive). Do not regress.
4. Provision the two admins:
   `portal_admin add-user stephens.jacob --role admin` and `… finkhousen.ben --role admin`
   (password = username at provision; no forced change).

> **This is the secrets/auth + impersonation boundary** — review the diff before activating.

### Session revocation (slice 8a — `users.session_epoch`, deferred audit #7)

Real logout / password-change revocation. **Migration 0009** adds `users.session_epoch`
(monotonic counter, `DEFAULT 0`); the epoch is snapshotted into the session cookie at login
and re-read per request in `requireSession` (folded into the same `disabled + role` SELECT).
A cookie whose epoch is **behind** the DB epoch is rejected (`401 revoked`); **logout** and
**password-change** (both the bearer reset and the in-app credentials route) increment the
column, so an outstanding/captured cookie dies on its next request. A pre-#7 cookie (no epoch
claim) is treated as `0`, so existing sessions survive the migration.

#### Activation (operator — secrets/auth boundary; escalates to the Developer-Operator)

1. Apply migration **0009** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the
   `requireSession` `session_epoch`-read errors and (fail-closed) 401s every session.
   **ORDER-CRITICAL**, same rule as 0006/0007.
2. **Redeploy** (`npm run deploy`) — activates the epoch check + the logout / password bumps.
3. **Regression-check the LIVE portal:** existing users still log in + submit (pre-#7 cookies
   survive; epoch defaults `0`); after a logout, re-using the old cookie is rejected (`401`).

> Out of scope here: the admin 30-minute idle timeout (slice 8b).

### Form editor publish queue (slice 3a — `publish_requests`)

**Migration 0010** adds the send-free `publish_requests` queue. The admin Forms editor's
Publish calls `POST /api/admin/publish`, which VALIDATES the composed definition
server-side (closed vocabulary + reserved-key denylist + cross-section-unique keys +
hard bounds) and, only if valid, ENQUEUES a row — it never commits or deploys. The Mac
publish daemon (slice 3b) is the sole privileged actuator (mirrors the External Send
Gate). `GET /api/admin/publish-status` is the monitor read view.

#### Activation (operator — secrets/auth + deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0010** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else
   `/api/admin/publish` errors on the missing table. **ORDER-CRITICAL**, same rule as 0006/0007/0009.
2. **Redeploy** (`npm run deploy`) — activates the enqueue + status routes (still inert for
   PMs without the Mac publish daemon, which is the privileged actuator that lands forms).

> Out of scope here: the Mac publish daemon (slice 3b) + the editor UI (slices 4–6).

### Request-driven canonical PDF download (PR-4 Part A — `0011`)

**Migration 0011** adds the PDF-cache columns on `submissions` (`pdf_requested`,
`box_file_id`, `pdf_ready_at`) + the `filed_pdfs` chunk table. A field PM (or the
attributee / an admin) clicks "Make available for download"; `POST
/api/submissions/:uuid/request-pdf` sets `pdf_requested=1`. The Worker holds NO Box
creds and is SEND-FREE: the Mac-side portal_poll daemon GETs the serviceable set
(`GET /api/internal/pdf-requests`), downloads the filed PDF from Box, base64-chunks it,
and POSTs the chunks (`POST /api/internal/filed-pdf`); `GET /api/submissions/:uuid/pdf`
reassembles the D1 chunks and serves the byte-identical Box copy as an attachment.

> **Superseded by PR-5 (Form Request, `0012`) below.** As of PR-5 the access model is
> **requester-bound, not ownership-gated**: a non-admin must hold a live `pdf_requests`
> row for *this* account to download (a different account — even the actor/attributee —
> gets **404**), and the prune is **two-stage** (strip payload at 90d, delete the row 30d
> after the job goes inactive; chunks evicted when no live request references them) rather
> than a flat 24h-from-`pdf_ready_at` sweep. The PR-5 section is the single source of truth.

#### Activation (operator — secrets/auth + deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0011** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the new
   PDF routes error on the missing columns/table. **ORDER-CRITICAL**, same rule as
   0006/0007/0009/0010.
2. **Redeploy** (`npm run deploy`) — activates the request/status/pdf + internal
   pdf-requests/filed-pdf routes (still inert until the Mac portal_poll PDF-cache pass
   runs, which is what populates the chunks).

> Out of scope here: the Mac portal_poll PDF-cache pass + the SPA download button (sibling surfaces).

### Form Request browse + requester-bound PDF (PR-5 — `0012`)

**Migration 0012** adds the `pdf_requests` table (one row per `(submission_uuid, account)` —
downloads are **requester-bound**, 24h). PR-5 adds the in-portal "Form Request" flow: any
authenticated account browses an **ACTIVE** job's filed forms (`GET /api/filed?job_id=…`) and
batch-requests their PDFs (`POST /api/request-pdfs`, ≤20/batch); each request upserts a
`pdf_requests` row, the Mac PDF-cache pass services only forms with a **live** request
(`GET /api/internal/pdf-requests` now requires one), and `GET /api/submissions/:uuid/pdf` is
re-gated so only the **requesting** account (or an admin) may download within 24h — a different
account, even the original submitter, gets **404** (no enumeration). Prune is now two-stage:
strip `payload_json` at 90d (keep the metadata row so a filed form stays browseable while its job
is active), delete the row 30d after the job goes **inactive**; `pdf_requests` expire at 24h and
their chunks are evicted. The Worker remains SEND-FREE (no Box creds, no egress).

#### Activation (operator — secrets/auth + deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0012** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else `/api/filed`,
   `/api/request-pdfs`, the requester-bound `/status` + `/pdf`, and the updated
   `/api/internal/pdf-requests` error on the missing `pdf_requests` table. **ORDER-CRITICAL**,
   same rule as 0006/0007/0009/0010/0011.
2. **Redeploy** (`npm run deploy`) — activates the Form Request routes + the requester-bound
   re-gate. The Mac portal_poll PDF-cache pass already services any live request.

> Out of scope here: the Mac portal_poll PDF-cache pass (sibling surface, unchanged — it reads
> `GET /api/internal/pdf-requests`, which now returns only live-requested forms).

### Field-Ops schema + split-brain fence (P2.1 — `0014`–`0017`)

**Migrations 0014–0016** port the URS-Marine field-ops tables into D1 (clients, personnel,
equipment, task_assignments, equipment_location, time_entries, inspections, equipment_logs +
additive ALTERs to `jobs`/`equipment`). **Migration 0017** adds `jobs.origin` / `sync_state` /
`canonical_job_id` — the split-brain fence: a portal-CREATED job (`origin='portal'`) must NOT be
deactivated by the Smartsheet down-sync (`/api/internal/sync`), which only deactivates
`origin='smartsheet'` jobs absent from the payload. The field-ops integrity-bar tables
(`time_entries`/`task_assignments`/`inspections`) are D1-PRIMARY operational SoR, so `prune` now
protects any job holding them from deletion.

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migrations **0014–0017** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the scoped
   `/api/internal/sync` deactivation 500s on the missing `origin` column and the field-ops-aware
   `prune` 500s on the missing tables. **ORDER-CRITICAL**, same rule as 0006/0007/0009/0010/0011/0012.
2. **Redeploy** (`npm run deploy`) — activates the origin-scoped down-sync + the field-ops-aware prune.

> The field-ops READ/WRITE routes + the Mac mirror daemon (`field_ops/fieldops_sync`) that promotes
> portal jobs into `ITS_Active_Jobs` land in later P2 slices — these migrations are inert until then.

### P3 Materials catalog (M1 — `0019`)

**Migration 0019** adds the `material_catalog` table — the datasheet-backed material TYPE vocabulary
(36 operator-approved types seeded inline) the per-job Material List draws from (manifest model, M2).
A plain reference table: admin CRUD gated `cap.materials.manage`; retire is a soft-delete (`active=0`)
so a receipt/incident referencing a `catalog_id` keeps its target. Read gated `cap.materials.receive`.
Both capabilities are already seeded in 0013 — 0019 seeds no capability vocabulary.

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0019** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else `GET /api/fieldops/materials`,
   `POST /api/fieldops/material`, `…/:id/update`, `…/:id/delete` 500 on the missing `material_catalog`
   table. **ORDER-CRITICAL**, same rule as 0013/0015/0016.
2. **Redeploy** (`npm run deploy`) — activates the catalog CRUD routes + the Materials admin page.

### Form workflow selector (Phase-2 — `0020`)

**Migration 0020** rebuilds `publish_requests` to add a `category` column and extend the `op`
CHECK with the new `recategorize` op (the form-builder **workflow selector** — a form's workflow,
today `safety` / `progress`, is chosen at create and changeable afterwards). The registry of valid
workflows is `safety_portal/workflows.json`, read by both the Worker and Python. No FK
dependencies; existing rows carry `category` as NULL.

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0020** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else `POST /api/admin/publish`,
   `GET /api/admin/publish-request`, and `POST /api/internal/publish/claim` 500 on the missing
   `category` column (the INSERT + both SELECTs name it). **ORDER-CRITICAL**, same rule as 0010.
   (Always `git pull` `~/its` to latest `main` BEFORE `wrangler d1 migrations apply` — the
   stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the `recategorize` op + the Workflow selector in the
   Forms editor.

### Manager tier — third portal role (P2.6 — `0023`)

**Migration 0023** seeds a third role `manager` (crew lead), a new capability `cap.crew.assign`
(granted to `manager` + `admin`), the manager's 11 grants (submitter's 8 + `cap.personnel.read` +
`cap.personnel.manage` + `cap.crew.assign`), and adds `personnel.current_job` (the crew→job
placement). The role is a pure INSERT — migration 0013 already replaced 0007's role CHECK with an FK
to `roles(key)`, so seeding the role satisfies it (no `users` rebuild). New Worker route
`POST /api/fieldops/personnel/:id/assign` (cap.crew.assign). See `docs/runbooks/manager_tier.md`
(§43) + `docs/enablement/manager_tier.md` (§6/A8).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0023** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else a user set to
   `manager` resolves to the EMPTY capability set (fail-closed) → blank tabs / 401, and
   `POST /api/fieldops/personnel/:id/assign` 500s on the missing `personnel.current_job` column.
   **ORDER-CRITICAL**, same rule as 0013/0020. (Always `git pull` `~/its` to latest `main` BEFORE
   `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the `manager` role vocabulary, the Accounts 3-way
   role control, the crew-assign route, and the Personnel "Assign" control.
3. **Smoke** (`wrangler dev` or live): set a user to `manager` (Accounts page or
   `portal_admin set-role <u> manager`); confirm they see Personnel + can assign crew (201), but
   get 403 on job-create / task-create / login-mint, and cannot open the admin dashboard.

### Unified job-create flow — crew converges on placement (`0024`)

**Migration 0024** adds `idx_personnel_current_job` on `personnel(current_job)`. This backs the
**crew-convergence** change: a job's "crew" (both the Job Tracker LIST card and the DETAIL view)
now MEANS the people currently **placed** on it (`personnel.current_job`, from 0023), NOT the
distinct assignees of its `task_assignments`. The Job Tracker detail view gains reusable
**Assign crew** (`cap.crew.assign`) and **Assign equipment** (`cap.equipment.field`) controls, and
creating a job routes into its detail with a "finish setting up" nudge — all reusing the already
security-reviewed `assign` / equipment-`location` routes (no new routes).

**SEMANTICS SHIFT (call out to the operator):** after this deploys, an existing job that had
task-assignment "crew" but nobody *placed* on it shows an EMPTY crew list until someone is placed
(via the new Assign-crew control or the Personnel page). No data is lost — those task assignments
still appear in the job's TASKS list with their assignee. This is the intended convergence.

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0024** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`). The index is additive and
   `IF NOT EXISTS`, so a stale deploy won't hard-fail (the crew query is correct without the index,
   just slower); still apply-before-deploy per the standing rule. (Always `git pull` `~/its` to
   latest `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the crew-convergence query + the detail-view
   Assign-crew / Assign-equipment controls + the create nudge.
3. **Smoke** (live): create a job → assign a person + a piece of equipment + a task → all three show
   on the job; the person's "Placed on" (Personnel page) shows the job.

### Assigned-Tasks — manager task authority (`0025`) + checklist engine (`0026`)

**Migration 0025** grants the `manager` role `cap.tasks.assign` — managers can now create / assign /
complete tasks (only to subcontractor-role accounts, guarded), and the task create/reassign routes gate
on `cap.jobtracker.manage` OR `cap.tasks.assign`. **Migration 0026** adds the checklist-engine tables
(`checklist_templates` / `checklist_items` / `checklist_instances` / `checklist_item_states`) + seeds the
`daily_default` template from `daily-report-v1.json`; the admin per-job checklist editor + template routes
read them (`cap.checklist.manage`, admin-only).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migrations **0025 then 0026** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`). 0026's tables are read by every
   checklist route (`GET /api/fieldops/checklist/*`), so a premature deploy 500s them; 0025 grants the
   manager cap that the re-gated task routes accept. Both are additive + guarded (`IF NOT EXISTS` +
   NOT-EXISTS-guarded seed), so a stale re-apply is safe. (Always `git pull` `~/its` to latest `main`
   BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the "My Tasks" tab, the manager task controls, and the
   admin Daily-checklist editor on the Job Tracker job detail.
3. **Smoke** (live): a manager creates/assigns a task to a subcontractor (not to an admin/manager);
   the "My Tasks" tab shows a user's assigned tasks; an admin edits the default checklist + adds/removes
   a per-job item on a job's detail.

_Historical note (D2, 2026-07): the daily-checklist SPA surfaces described above were retired by the
SOP daily form (see "SOP daily form — the Daily tab IS the form" below); the engine + tables stay for
assigned inspections._

### Subcontractor tier — scoped crew-create + time scoping (`0027`)

**Migration 0027** adds one capability `cap.crew.create` (granted to `submitter` + `admin`) and the
`personnel.created_by` provenance column. The `submitter` tier is re-presented to users as
**"Subcontractor"** — a **DISPLAY-LABEL-ONLY** rename: the role **KEY stays `'submitter'`** (the
security-load-bearing fail-safe default in `worker/auth.ts` — "unknown → submitter, never upward"), so
NO role/vocabulary row changes and the grant matrix is preserved. A subcontractor keeps all 8 of its
0013 caps + gains `cap.crew.create`. New Worker route `POST /api/fieldops/crew` (`cap.crew.create`)
creates a **NON-LOGIN** roster person auto-placed on the ACTOR's own current job (`created_by` stamped;
422 `not_placed` if the actor isn't placed; any account/login payload → 400 `login_not_allowed`).
`GET /api/fieldops/crew/mine` backs the subcontractor time-log picker. The time-entry route now SCOPES a
subcontractor (`cap.time.log` WITHOUT `cap.personnel.manage`) to logging only for their OWN linked
personnel OR a person they created (`created_by = them`) → else 403 `forbidden_personnel`;
managers/admins stay unrestricted. See `docs/runbooks/subcontractor_tier.md` (§43) +
`docs/enablement/subcontractor_tier.md` (§6/A8).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0027** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else `POST /api/fieldops/crew`
   403s every caller (fail-closed empty cap) and the crew-create INSERT + the time-scoping SELECT 500
   on the missing `created_by` column. **ORDER-CRITICAL**, same rule as 0013/0023/0025. (Always
   `git pull` `~/its` to latest `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list
   lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the "Subcontractor" display label, the My-Tasks
   Add-crew control, the scoped crew-create route, and the subcontractor time-log picker/scoping.
3. **Smoke** (live): set a user to `submitter`, place them on a job (Personnel → Assign, or a manager
   places them); they see **Add crew** on My Tasks → add a field-only helper (lands on their job); the
   helper appears in their time-log "For" picker; logging time for a stranger they didn't create is
   refused (403). An unplaced subcontractor gets a "must be placed on a job" message. A manager/admin is
   unaffected (full job-crew picker, no scoping).

### SOP checklist content seed (`0028`)

**Migration 0028** is CONTENT-ONLY (no schema change, no new routes): it replaces the migration-0026
placeholder `daily_default` items with the **13-item Site-Supervisor-SOP daily checklist**
(`Site_Supervisor_SOP 2.docx` — incl. the count-50 site-photos item, the count-2 CM check-ins item,
and the two form_linked items: `jha` + the `Daily Field Report filed` capstone) and seeds the S6
inspection library with **6 `generic_inspection` templates** from the ER Safety Manual
(Box 2265234453251): Excavation/Trench, Scaffold, Crane & Rigging, Aerial Lift/MEWP,
Ladder & Fall-Gear, Hot-Work/Welding. Guarded + idempotent: the delete+reseed runs only while the
`daily_default` lacks the `'Daily Field Report filed'` sentinel item, and every INSERT is
NOT-EXISTS-guarded (an admin-created same-title library template is never duplicated).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0028** to the live D1
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) **BEFORE** the next redeploy
   per the standing rule — though this one is **LOW-RISK either order** (content-only: the deployed
   Worker renders the new rows exactly like the old ones). (Always `git pull` `~/its` to latest
   `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **No redeploy required** — the change is data. Notes:
   - **Already-generated daily instances keep their snapshot** (S3 snapshots items at generation);
     the new 13-item default takes effect on the **next day's roll** of each manager's checklist.
   - **Per-job overrides authored against the OLD placeholder items are cleared** by the migration's
     orphan-marker cleanup (suppression markers pointing at deleted default item ids); per-job ADDED
     items survive. Re-author any wanted suppressions against the new items via the Job Tracker
     checklist editor.
3. **Smoke** (live): admin → Job Tracker → a job's Daily-checklist editor shows the 13 SOP items in
   order (photos item target 50, check-ins target 2); the inspection library lists the 6 seeded
   templates; a placed manager's next-day "My Tasks" daily checklist rolls the new items.

_Historical note (D2, 2026-07): the 0028 daily_default rows are now DORMANT — the SOP content lives in
the `daily-report-v2` form definition and the Daily tab renders it as a form (see "SOP daily form —
the Daily tab IS the form" below). The 6 inspection-library templates stay live._

### Assigned-Tasks R1 — instance template title (`0029`) + worker contract fixes

**Migration 0029** adds `checklist_instances.template_title` — the assigned inspection template's
title, SNAPSHOTTED at assign time (same lineage rule as the item snapshot: renaming/deleting the
library template never mutates an in-flight instance) — and best-effort BACKFILLS existing
`kind='inspection'` instances through the item-snapshot lineage (`source_item_id` →
`checklist_items.template_id` → `checklist_templates.title`); instances whose lineage no longer
resolves stay NULL (the UI falls back to "Inspection #id"). Ships with the R1 worker contract pass:
task-status ownership guard (403 `forbidden_task`), open-first list ordering, assign-time
validation (`empty_template` / `job_and_date_required` / catalog-checked `unknown_form_code`),
below-target acknowledge (`note_required`), `/checklist/mine` reason codes + `/tasks/mine` `linked`,
Q3 on-or-before due-date reconcile for inspections, `filed_by`/`rolled_up_by` attribution, and
required-bounded time-entry hours (422 `invalid_hours`).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0029** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else `POST
   /api/fieldops/checklist/assign` and `GET /api/fieldops/checklist/assigned` 500 on the missing
   `template_title` column. **ORDER-CRITICAL**, same rule as 0026. (Always `git pull` `~/its` to
   latest `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the R1 contract (ownership guard, ordering, assign
   validation, reasons/attribution fields, hours bounds).
3. **Smoke** (live): assign an inspection → the assignee's My Tasks card shows the template's title;
   a subcontractor flipping another person's task gets a permission message; a time entry without
   hours is refused.

### SOP daily form — the Daily tab IS the form (D1 `daily-report-v2` + D2 — no migration)

**No migration.** D1 shipped the `daily-report-v2` definition (the full Site-Supervisor SOP as
`guidance`/`form_link` sections with the DFR data fields interleaved) + catalog bump +
`launch:"daily-tab"`. **D2** makes the My-Tasks **Daily tab the form itself**: date selector
(Pacific today default, past dates show the filed state first) + the v2 form rendered inline
(job from the manager's placement via the Job Tracker viewer data; crew/equipment/prepared_by
prefilled best-effort from the job detail) + `form_link` deep-links riding the existing openForm
machinery with live "Filed ✓ \<time> by \<name>" indicators from the NEW read-only endpoint
`GET /api/fieldops/daily-form/status?job_id&date` (`cap.tasks.own`; latest submission per parent
family via the S4 family match; display-name-only attribution). Filing goes through the unchanged
send-free `/api/submit` → Mac intake → weekly packet.

**RETIRED SPA surfaces (D2):** the R2 checkbox daily checklist (DailyChecklistSection), the admin
"Default daily checklist" editor (Checklists page — now inspections-only), the Job-Tracker per-job
Daily-checklist editor + its cross-link, and the Daily Report's entry in the Submit-a-Form CREATE
picker (`launch:"daily-tab"` parents are hidden there; Form Request / download / history surfaces
untouched). **The checklist ENGINE + all its Worker routes stay** (assigned inspections use them;
§14/§49) — the daily generation route (`/checklist/mine`) simply has no SPA caller anymore
(deprecation note in `worker/fieldops_checklist.ts`). Daily content edits now happen in the
**form definition** via the form builder / publish pipeline.

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. **No D1 migration** — the `checklist_templates` `daily_default` rows (0026/0028) stay in place,
   dormant for the daily flow (inspections keep their tables live).
2. **Redeploy** (`npm run deploy`) — activates the status endpoint + the rebuilt Daily tab + the
   retirements in one step (SPA assets + Worker deploy together).
3. **Smoke** (live): a placed manager's My Tasks → Daily report shows the date selector + the SOP
   form with crew/equipment prefilled; "Create Job Hazard Analysis" opens the JHA prefilled and,
   after filing it, the button shows "Filed ✓" on return; submitting the daily report flips the
   "Daily report filed ✓" banner; the Submit-a-Form picker no longer lists Daily Field Report; the
   Checklists page shows no "Default daily checklist" area; a Job Tracker job detail shows no
   Daily-checklist editor; an assigned inspection still renders and auto-checks.

### Per-job daily-form requirements (D4 — `0030`)

**Migration 0030** adds `job_daily_requirements` — the admin-authored **additive overlay** of
per-job requirement items (kinds: `note` / `confirm` / `text` / `form_link`) that the portal
fetches at render time and injects into the daily form's `job_requirements` section. Answers
file WITH the submission (`values.job_requirements`, self-describing), so filed PDFs stay
stable regardless of later requirement edits. Authoring is `cap.checklist.manage` (admin);
the tab read (`GET /api/fieldops/daily-form/requirements`) is `cap.tasks.own` with the same
per-job ownership scope as `/api/fieldops/daily-form/status`. Full detail in the migration
header + `docs/runbooks/fieldops_checklists.md` (§ per-job daily-form requirements).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0030** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else the
   daily-requirements routes 500 on the missing `job_daily_requirements` table.
   **ORDER-CRITICAL**, same rule as 0026. (Always `git pull` `~/its` to latest `main` BEFORE
   `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the requirements routes + the admin editor +
   the `job_requirements` section in the daily form (SPA + Worker deploy together).
3. **Smoke** (live): an admin adds a requirement to a job (Job Tracker job detail); a manager
   placed on that job sees it rendered in the Daily tab's form and their answer files with the
   submission; a manager on another job does NOT see it.

### Requirement kinds widened (D5 — `0032`)

**Migration 0032** rebuilds `job_daily_requirements` (SQLite can't widen a CHECK in place — the
`0020` rebuild precedent) to extend the requirement-kind vocabulary from four to seven:
`note` / `confirm` / `text` / `form_link` plus **`number`** (numeric answer), **`date`**
(calendar-date answer), and **`select`** (pick-one from an admin-authored option list — the new
`options` column, a JSON array bounded route-side to 1–20 non-empty choices of ≤120 chars;
NULL for every other kind). Existing rows are preserved (options copied NULL). Answers still
file as the self-describing `values.job_requirements = [{label, kind, response}]` strings, so
the filed-PDF rendering (generic label→response rows) needed **no change**. Photo is
deliberately excluded — an untrusted image upload needs the §34 image-class screening design
first (see `docs/tech_debt.md`, "Checklist item-state photo CAPTURE"). Full detail in the
migration header + `docs/runbooks/fieldops_checklists.md` (Symptom F).

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migrations to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — the one command applies
   **`0030` then `0031` then `0032` in sequence** (none are live yet; 0030 creates the 4-kind
   table and 0032 immediately rebuilds it to the 7-kind + `options` shape — zero rows to copy).
   **ORDER-CRITICAL**, same rule as 0026: a Worker serving the new kinds against a pre-0032
   table 500s on the missing `options` column. (Always `git pull` `~/its` to latest `main`
   BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the 7-kind validation + the options editor +
   the number/date/select rendering (SPA + Worker deploy together).
3. **Smoke** (live): an admin adds a **Choice** requirement with two options to a job (Job
   Tracker job detail) plus a **Number** and a **Date** one; a manager placed on that job sees
   a dropdown / numeric input / date picker in the Daily tab's "Job-specific requirements",
   answers all three, files — the filed PDF shows the three label → answer rows.

### Expected materials — per-job receipt list (Material receipts M1 — `0031`)

**Migration 0031** adds `job_expected_materials` — the per-job list of what materials a job is
expecting (recorded by the office at job creation or as the job develops), which managers later
confirm receipt against. One row per expected arrival: catalog-picked (`material_id` → the 0019
`material_catalog`, validated ACTIVE at write) or free-text (`description` required);
qty/unit/expected-date; `status ∈ expected|received|incident` with `received_at`/`received_by`
stamped by the receive/flag routes (`received_by` stores the account username; reads resolve the
personnel **display name only** — W9). Expectation CRUD is `cap.materials.manage` (the Job
Tracker job-detail "Expected materials" section); the read + `POST …/:id/receive` /
`…/:id/flag-incident` are `cap.materials.receive` with the **per-job ownership scope** (a
non-admin only touches the job they're placed on). Both capabilities were already seeded
(0013/0023) — 0031 seeds no capability vocabulary. The receive/flag routes are wired into the
daily form (D.13 deliveries) + the material-incident form in **M2**; in M1 the admin section +
read surface carry the state. *(Numbering: `0030` belongs to the D4 slice, built in parallel —
both are additive, so apply order is safe.)*

#### Activation (operator — deploy boundary; escalates to the Developer-Operator)

1. Apply migration **0031** to the live D1 **BEFORE** the redeploy
   (`npx wrangler d1 migrations apply its-safety-portal-db --remote`) — else
   `GET /api/fieldops/expected-materials`, `POST /api/fieldops/expected-material` (+ `…/update`,
   `…/seq`, `…/delete`, `…/receive`, `…/flag-incident`) 500 on the missing
   `job_expected_materials` table. **ORDER-CRITICAL**, same rule as 0019. (Always `git pull`
   `~/its` to latest `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list
   lockout class.)
2. **Redeploy** (`npm run deploy`) — activates the expected-materials routes + the Job Tracker
   "Expected materials" section (SPA + Worker deploy together).
3. **Smoke** (live): an admin opens a job's detail → "Expected materials" → adds one from the
   catalog and one free-text; a manager placed on that job sees the read-only list (and a manager
   on another job does NOT — 403 `forbidden_job` in the network tab); the Materials Catalog page
   shows the cross-note pointing at the Job Tracker.

### Lockout recovery (break-glass) — escalate to the Developer-Operator

If both admins are ever locked out (e.g. passwords lost, or both disabled), recovery runs
through the bearer CLI — **which reads the Keychain admin bearer (`ITS_PORTAL_ADMIN_TOKEN`),
so it is a high-capability (secrets/auth) operation that escalates to Seth**, not a Tier-2
repair: `portal_admin set-role <u> admin` / `enable-user <u>` / `reset-password <u>`. These
bearer routes have **no** last-admin guard precisely so they can restore an admin when the UI
can't. See `docs/runbooks/safety_portal_admin_dashboard.md`.

### Testing

Worker logic (the role gate, account CRUD, last-admin guard, self-edit re-auth, bearer
break-glass, audit rows) is tested with **`@cloudflare/vitest-pool-workers`** — the tests run
in **workerd (the real runtime) against a Miniflare D1** with the real migrations applied, not
mocks (`test/admin.test.ts`). `npm test` runs them; CI runs them in the `portal` job
(`npm ci` → `npm run typecheck` → `npm test`). The Python `test` job does not cover the
Worker TS, so this job is what makes the four-part "main-CI green" verify meaningful for the
auth code.

---

## Security posture (Phase 2)

- **Invariant 1 — External Send Gate:** the Worker performs **zero external
  transmission** (no email, no third-party outbound, no AI step). It only validates a
  login, signs/verifies a session cookie, and serves the SPA. The Phase 5 email shim is a
  separate, capability-gated component. *(Known gap, blueprint Decision-4-equivalent: the
  Python AST capability-gate does not reach the TS Worker; a Worker-side equivalent is
  Phase 5 work — out of scope here because the Worker is send-free.)*
- **Invariant 2 — Adversarial Input Handling:** all browser input is untrusted — request
  bodies are type-checked and length-bounded; D1 access uses bound parameters (no string
  interpolation); the session cookie is HttpOnly + signed (HMAC-SHA256 via `crypto.subtle`,
  constant-time verify — a tampered cookie is rejected).
- **Session model (accepted Phase-2 gap):** sessions are cookie-derived with **no
  server-side revocation** — `/api/logout` clears the client cookie only, and
  `requireSession` does not re-check that the user still exists, so a stolen or
  deprovisioned-user cookie stays valid until `iat + 90 days`. Acceptable because no real
  PMs exist until they're provisioned via the **Phase 7 admin route**, which adds the D1
  session table for explicit invalidation/deprovisioning.

> **Types:** `worker/types.ts` is the hand-authored source of truth for the `Env` bindings.
> `npm run cf-typegen` (`wrangler types`) is optional — no tsconfig depends on its generated
> `worker-configuration.d.ts` in Phase 2, and a fresh clone typechecks without it.

---

## What's stubbed / out of scope

Phase 2 is the skeleton + one form stub. **Not built here** (later phases per `brief.md` §14):

- Generic form runtime (`_runtime/` renderer + pdf_renderer) and per-form `form.ts` — **Phase 4**.
- The other nine forms (see `reference_forms/`) — **Phase 4**.
- Sync Worker (cron + Smartsheet webhook), D1 mirror tables — **Phase 3**.
- Submission pipeline: Python PDF render (Box-stored), the pull-model `portal_poll` daemon, `intake.py` portal branch — **Phase 5**.
- `/admin` route, user CRUD, per-user password scheme (Q2b) — **Phase 7**.
- JHA Weekly Compliance Rollup — **Phase 5/6**. (No R2 — PDFs live in Box.)

The JHA view is a **hard-coded stub** that mirrors the real layout to validate the stack;
it does not submit.
