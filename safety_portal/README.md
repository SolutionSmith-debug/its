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

### Deploy target: Workers Static Assets vs Pages (reconciliation)

The blueprint topology (`brief.md` §11, authored 2026-05-25) names a **Cloudflare
Pages** project and a `*.pages.dev` URL. Since then, Cloudflare's guidance changed:
**Workers Static Assets is the recommended path for new full-stack projects; Pages is
in maintenance mode** ("If you are starting a new project, use Workers instead of
Pages" — Cloudflare docs). This scaffold therefore uses the **Workers + Static Assets**
shape (`wrangler.jsonc` `assets` binding, `wrangler deploy`).

**Operator decision pending at deploy time** (deploy was deferred this session):

- **Workers path (this scaffold):** free URL is `https://its-safety-portal.<account>.workers.dev`
  (workers.dev, **not** pages.dev). The custom domain `safety.evergreenmirror.com` attaches
  to the Worker as a Custom Domain (auto CNAME + Universal SSL) — but any DNS already wired
  `CNAME → its-safety-portal.pages.dev` would need re-pointing to the Worker.
- **Pages path (blueprint-literal):** keep `*.pages.dev`; convert `worker/index.ts` to a
  `functions/` directory and deploy with `wrangler pages deploy ./dist/client`. The
  application code is otherwise identical.

This is a topology-reconciliation item to confirm with the operator / fold back into the
blueprint before/at first deploy. The application code is deploy-mechanism-agnostic.

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

> Out of scope here: the admin 5-minute idle timeout (slice 8b).

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
