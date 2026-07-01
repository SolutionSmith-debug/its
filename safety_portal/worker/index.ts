import { Hono } from "hono";
import { createMiddleware } from "hono/factory";
import type { Context } from "hono";
import { setSignedCookie, getSignedCookie, deleteCookie } from "hono/cookie";
import type { Env, Role, SessionClaims, Vars } from "./types";
import type { FieldopsGates } from "./fieldops_gates";
import { registerPersonnelRoutes } from "./fieldops_personnel";
import { registerEquipmentRoutes } from "./fieldops_equipment";
import { registerJobTrackerRoutes } from "./fieldops_jobtracker";
import { registerMaterialsRoutes } from "./fieldops_materials";
import { auditStmt, isUniqueViolation } from "./audit";
import { registerTimeWriteRoutes } from "./fieldops_time_write";
import { registerJobWriteRoutes } from "./fieldops_job_write";
import { registerTaskWriteRoutes } from "./fieldops_task_write";
import { registerMyTasksRoutes } from "./fieldops_tasks";
import { registerChecklistRoutes } from "./fieldops_checklist";
import { registerEquipmentFieldWriteRoutes } from "./fieldops_equipment_write";
import { registerEquipmentRosterWriteRoutes } from "./fieldops_equipment_roster_write";
import { registerPersonnelWriteRoutes } from "./fieldops_personnel_write";
import { registerCrewAssignRoutes } from "./fieldops_crew_assign";
import { registerMaterialWriteRoutes } from "./fieldops_material_write";
import { registerProgressRollupRoutes } from "./fieldops_rollup";
import {
  validateUser,
  newSessionClaims,
  hashPassword,
  normalizeUsername,
  coerceRole,
  resolveCapabilities,
  parseRole,
} from "./auth";
import { validateCategory, validateDefinition, validateParentGrouping } from "./publishValidation";
import { pruneOldData } from "./prune";
import catalog from "../catalog.json";

// ─────────────────────────────────────────────────────────────────────────────
// ITS Safety Portal — Worker API (Phase 2)
//
// Purpose: the single Cloudflare Worker for the Safety Portal. Validates a portal
//   login against D1, issues/verifies an HMAC-signed session cookie, and serves the
//   built React SPA (static assets). Nothing else in Phase 2.
//
// Invariants:
//   - Invariant 1 (External Send Gate): ZERO external transmission — no email, no
//     third-party outbound, no AI step. The only fetch is c.env.ASSETS (asset
//     serving). Phase 5 keeps the Worker SEND-FREE by design: it signs + queues each
//     submission in D1 and serves it over an authenticated /api/internal/pending
//     endpoint; the Mac-side portal_poll daemon PULLS + files (the pull model —
//     decision_phase5-portal-transport). The Worker never sends.
//   - Invariant 2 (Adversarial Input Handling): all browser input is untrusted —
//     request bodies are type-checked + length-bounded; D1 access uses bound
//     parameters (no string interpolation); the session cookie is HttpOnly +
//     HMAC-signed (constant-time verify).
//
// Failure modes: stateless at this layer — Cloudflare owns the process lifecycle,
//   so there is no fail-open/closed posture to maintain here. A D1 error in
//   /api/login propagates and Hono returns 500 (login fails closed). bcrypt.compare
//   at cost 10 can exceed the Workers FREE-plan 10ms CPU cap (Error 1102) — the
//   deployed Worker must be on the Paid plan or swap to PBKDF2 (see README "Deploy").
//   Session validity is cookie-derived only: NO server-side revocation in Phase 2
//   (see the /api/logout rationale).
//
// Consumers: the SPA (src/) via same-origin fetch — /api/login, /api/session,
//   /api/logout, /api/jobs, /api/recent, /api/submit (signs + queues the submission).
//   The Mac-side portal_poll daemon via bearer-token /api/internal/pending (queue
//   drain) + /api/internal/mark-filed (the receipt) + /api/internal/sync (full-replace
//   push of the ITS_Active_Jobs set → the D1 dropdown cache).
// ─────────────────────────────────────────────────────────────────────────────

const COOKIE = "its_portal_session";
const MAX_AGE_S = 60 * 60 * 24 * 90; // 90-day session for submitters (field convenience)
// Admins get a 30-minute IDLE window (slice 8b, C10): a SLIDING cookie re-issued on each
// active request, so an idle (or captured) admin cookie dies at 30 min regardless. The SPA
// pings on activity to keep an actively-used session alive (and logs out proactively at idle);
// while a dirty form-editor draft is open it ADDS a bounded wall-clock keep-alive so unsaved
// work in a briefly-backgrounded tab isn't bounced mid-edit — but an abandoned editor still
// idles out at 30 min (the keep-alive is bounded to the idle window; the draft is client-cached).
const ADMIN_IDLE_S = 30 * 60;

const app = new Hono<{ Bindings: Env; Variables: Vars }>();

// ── Security response headers (audit 2026-06-08: #2 CSP, #3 clickjacking, #8–11) ─
// wrangler.jsonc sets run_worker_first:true so EVERY request runs the Worker — these
// reach the SPA document + static assets too (the platform otherwise serves them and
// bypasses Hono).
//
// CRITICAL (the 2026-06-08 hotfix): responses from c.env.ASSETS.fetch() have IMMUTABLE
// headers. Mutating them in place — which Hono's secureHeaders()/c.header() do — THROWS,
// and under run_worker_first:true that 500'd every static asset AND the SPA document
// (only the Hono-built /api/* responses, which have mutable headers, survived). So we
// RECONSTRUCT each response with a fresh, mutable Headers COPY and set ours on that.
// The copy preserves the asset's own content-type/etag/cache headers; we only ADD.
//
// CSP is ENFORCING (flipped 2026-06-08 after a clean browser smoke: admin login →
// dashboard → a form rendered WITH signature capture produced ZERO CSP violations). It
// shipped Report-Only for one cycle first so the smoke couldn't break the live SPA. The CSP allows
// React inline styles ('unsafe-inline' style-src) + the logo/inline-SVG signature
// (img-src 'self' data:); the built index.html has NO inline <script> → script-src 'self'.
// Cache-Control:no-store is /api/*-ONLY (the cacheable static assets keep their caching).
// script-src/connect-src allow Cloudflare's Web Analytics beacon (auto-injected at the
// edge: static.cloudflareinsights.com serves beacon.min.js, which POSTs RUM data to
// cloudflareinsights.com). Without these the enforcing CSP blocks the beacon → a console
// error every load. Cloudflare's own first-party CDN; everything else stays 'self'.
const CSP =
  "default-src 'self'; " +
  "script-src 'self' https://static.cloudflareinsights.com; " +
  "connect-src 'self' https://cloudflareinsights.com; " +
  "style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; " +
  "base-uri 'self'; frame-ancestors 'none'; form-action 'self'";
app.use("*", async (c, next) => {
  await next();
  const headers = new Headers(c.res.headers); // mutable copy — preserves Set-Cookie, etag, etc.
  headers.set("X-Frame-Options", "DENY");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  headers.set("Content-Security-Policy", CSP);
  if (new URL(c.req.url).pathname.startsWith("/api/")) headers.set("Cache-Control", "no-store");
  c.res = new Response(c.res.body, { status: c.res.status, statusText: c.res.statusText, headers });
});

// Global error handler (audit #1, defense-in-depth). Before this, an unguarded throw
// (e.g. a null-body deref) returned the runtime's bare 500. Return clean JSON with NO
// stack leak; logged to the Worker log (observability), NOT paged — a malformed unauth
// request must never Sentry-spam. This is the backstop BEHIND the per-handler
// body-shape guards added below.
app.onError((err, c) => {
  console.error("worker_unhandled", err instanceof Error ? err.message : String(err));
  return c.json({ error: "internal_error" }, 500);
});

// ── Phase 5 transport (pull model) — HMAC signing + internal-endpoint auth ──────

/**
 * Canonical payload for the submission HMAC. The Mac-side portal_poll daemon
 * recomputes this byte-for-byte (shared/portal_hmac.py) to verify integrity +
 * authenticity before intake trusts a pulled submission. ORDER + SEPARATOR are
 * load-bearing and mirrored on the Python side:
 *   submission_uuid \n job_id \n form_code \n work_date \n payload_json
 * payload_json is the EXACT stored JSON string, used verbatim on both sides.
 */
function canonicalPayload(p: {
  submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string;
}): string {
  return [p.submission_uuid, p.job_id, p.form_code, p.work_date, p.payload_json].join("\n");
}

/** HMAC-SHA256(secret, message) → lowercase hex. */
async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Length-independent constant-time compare: compares the SHA-256 digests, so the
 * loop runs over fixed 32-byte hashes and leaks NO length oracle on the bearer token
 * (a plain char-by-char compare with an early length-mismatch exit would).
 */
async function safeTokenEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const ua = new Uint8Array(da);
  const ub = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < ua.length; i++) diff |= ua[i] ^ ub[i];
  return diff === 0;
}

/** Bearer-token gate for /api/internal/* — the Mac-side portal_poll daemon's auth. */
const requireInternalToken = createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
  const auth = c.req.header("Authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  // Fail closed if the token isn't configured (missing secret → reject, never allow).
  if (!token || !c.env.PORTAL_INTERNAL_API_TOKEN || !(await safeTokenEqual(token, c.env.PORTAL_INTERNAL_API_TOKEN))) {
    return c.json({ error: "unauthorized" }, 401);
  }
  await next();
});

/**
 * Bearer-token gate for /api/internal/admin/* — operator user-provisioning.
 * SEPARATE secret from PORTAL_INTERNAL_API_TOKEN (privilege separation): the
 * portal_poll daemon's token must NOT be able to create / reset / disable users.
 * Same fail-closed-on-missing-secret + constant-time posture as requireInternalToken.
 */
const requireAdminToken = createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
  const auth = c.req.header("Authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token || !c.env.PORTAL_ADMIN_API_TOKEN || !(await safeTokenEqual(token, c.env.PORTAL_ADMIN_API_TOKEN))) {
    return c.json({ error: "unauthorized" }, 401);
  }
  await next();
});

/**
 * Bearer-token gate for /api/internal/fieldops/* — the Mac-side field-ops mirror daemon
 * (field_ops/fieldops_sync.py, P2.5). SEPARATE secret from PORTAL_INTERNAL_API_TOKEN and
 * PORTAL_ADMIN_API_TOKEN (privilege separation): the mirror daemon's token must NOT be able to
 * drain the submission queue (/api/internal/*) or provision users (/api/internal/admin/*), and
 * neither of those tokens may read/advance the job-mirror queue. Same fail-closed-on-missing-secret
 * + constant-time posture as requireInternalToken.
 */
const requireFieldopsToken = createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
  const auth = c.req.header("Authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token || !c.env.PORTAL_FIELDOPS_API_TOKEN || !(await safeTokenEqual(token, c.env.PORTAL_FIELDOPS_API_TOKEN))) {
    return c.json({ error: "unauthorized" }, 401);
  }
  await next();
});

/**
 * POST /api/login — validate credentials, issue a signed session cookie.
 * `secure` is conditional on HTTPS so login works over http://localhost in
 * `vite dev` while staying Secure on the deployed HTTPS origin.
 */
app.post("/api/login", async (c) => {
  let body: { username?: unknown; password?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }

  const username = typeof body.username === "string" ? body.username.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  // Bound the inputs; reject obviously-malformed before touching the DB.
  if (!username || !password || username.length > 128 || password.length > 256) {
    return c.json({ error: "invalid_credentials" }, 401);
  }

  const user = await validateUser(c.env, username, password);
  if (!user) return c.json({ error: "invalid_credentials" }, 401);

  const claims = newSessionClaims(user);
  await setSignedCookie(c, COOKIE, JSON.stringify(claims), c.env.SESSION_SIGNING_SECRET, {
    httpOnly: true,
    secure: new URL(c.req.url).protocol === "https:",
    sameSite: "Lax",
    path: "/",
    // Admins start on the short idle window immediately; submitters keep 90 days (8b/C10).
    maxAge: user.role === "admin" ? ADMIN_IDLE_S : MAX_AGE_S,
  });
  // `role` + `capabilities` let the SPA decide which tabs/actions to render. Display-only
  // hinting — every gated action is independently re-gated server-side (requireRole /
  // requireCapability). Caps resolved from D1 (migration 0013); FAIL-CLOSED on error.
  const capabilities = await resolveCapabilities(user.role, c.env.DB);
  return c.json({
    user: { username: user.username, role: user.role, capabilities: [...capabilities] },
  });
});

/** Verify the signed session cookie; 401 on absent/tampered/expired. */
const requireSession = createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
  // getSignedCookie returns the value if the HMAC verifies (constant-time, via
  // crypto.subtle.verify), false if tampered, undefined if absent — all falsy here.
  const raw = await getSignedCookie(c, c.env.SESSION_SIGNING_SECRET, COOKIE);
  if (!raw) return c.json({ error: "unauthenticated" }, 401);

  let claims: SessionClaims;
  try {
    claims = JSON.parse(raw) as SessionClaims;
  } catch {
    return c.json({ error: "bad_session" }, 401);
  }
  if (typeof claims.iat !== "number") {
    return c.json({ error: "bad_session" }, 401);
  }
  // Reject expired AND future-dated (negative age) sessions — the latter guards a
  // clock-skew / forged-iat edge even though forging iat needs the signing key.
  const ageS = Math.floor(Date.now() / 1000) - claims.iat;
  if (ageS < 0 || ageS > MAX_AGE_S) {
    return c.json({ error: "expired" }, 401);
  }

  // Phase-7 revocation: the session is cookie-derived, but a disabled (or deleted)
  // user must be locked out immediately. Per-request D1 lookup by username
  // (negligible at this scale). FAIL-CLOSED: a missing/disabled user → 401, and any
  // D1 error → 401 too (a DB blip must neither grant access nor crash the request).
  // ORDER DEPENDENCY: migration 0006 (users.disabled) must be live BEFORE this
  // deploys, else this read errors and 401s every session — see README activation.
  // Read `role` in the SAME per-request lookup as `disabled` (migration 0007 adds
  // the column; ORDER DEPENDENCY: it must be live before this deploys). Role is
  // authoritative from D1 here — NOT from the cookie — so a demotion is effective
  // immediately. coerceRole fails safe (unknown → 'submitter', never 'admin').
  // Read `session_epoch` (slice 8a, audit #7) in the SAME lookup — one SELECT returns
  // `disabled + role + session_epoch` (migration 0009 adds the column; ORDER
  // DEPENDENCY: it must be live before this deploys). The epoch is the captured-cookie
  // kill switch: logout / password-change increment the DB column, so an outstanding
  // cookie's snapshot falls BEHIND and is rejected here. A pre-#7 cookie carries NO
  // epoch claim → treated as 0 (== the column DEFAULT), so existing sessions survive.
  let role: Role;
  try {
    const row = await c.env.DB
      .prepare("SELECT disabled, role, session_epoch FROM users WHERE username = ?")
      .bind(claims.username)
      .first<{ disabled: number; role: string; session_epoch: number }>();
    if (!row || row.disabled) {
      return c.json({ error: "revoked" }, 401);
    }
    // Stale-epoch ⇒ revoked. `?? 0` keeps a pre-#7 (no-epoch-claim) cookie valid.
    if ((claims.epoch ?? 0) < row.session_epoch) {
      return c.json({ error: "revoked" }, 401);
    }
    role = coerceRole(row.role);
  } catch {
    return c.json({ error: "unauthenticated" }, 401);
  }

  // Admin 30-min idle timeout (slice 8b, C10) — a SLIDING window. An admin cookie idle
  // past ADMIN_IDLE_S is rejected (a captured admin cookie dies at 30 min); an ACTIVE admin
  // request SLIDES the window by re-issuing the cookie with a fresh iat + 30-min maxAge.
  // Submitters keep the 90-day session. (The MAX_AGE check above already ran; this is the
  // tighter admin window on top, and is authoritative regardless of the cookie's maxAge —
  // a captured cookie whose browser-maxAge was tampered still dies via this iat check.)
  let sessionClaims = claims;
  if (role === "admin") {
    if (ageS > ADMIN_IDLE_S) return c.json({ error: "idle" }, 401);
    sessionClaims = { ...claims, iat: Math.floor(Date.now() / 1000) };
    await setSignedCookie(c, COOKIE, JSON.stringify(sessionClaims), c.env.SESSION_SIGNING_SECRET, {
      httpOnly: true,
      secure: new URL(c.req.url).protocol === "https:",
      sameSite: "Lax",
      path: "/",
      maxAge: ADMIN_IDLE_S,
    });
  }

  c.set("session", sessionClaims);
  c.set("role", role);
  // Resolve the role KEY → capability SET (migration 0013) in the SAME
  // change-effective-next-request posture as `role`. resolveCapabilities is FAIL-CLOSED
  // (unknown role / D1 error → empty set, never privileged). ORDER DEPENDENCY: migration
  // 0013's role_capabilities must be live before this deploys (mirror of 0006/0007/0009).
  c.set("capabilities", await resolveCapabilities(role, c.env.DB));
  await next();
});

/**
 * Session+role gate for the in-app admin surface (/api/admin/*). MUST chain AFTER
 * requireSession, which sets the per-request `role` from D1. A non-admin session
 * → 403 (authenticated but unauthorized). This is the REAL gate for the admin UI —
 * the SPA hiding the admin tabs is never the boundary (Invariant 2: never trust the
 * client). SEPARATE from requireAdminToken: that is a bearer secret for the operator
 * CLI's /api/internal/admin/*; this is a logged-in admin acting in the browser.
 */
const requireRole = (role: Role) =>
  createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
    if (c.get("role") !== role) return c.json({ error: "forbidden" }, 403);
    await next();
  });

/**
 * Fine-grained capability gate (migration 0013). MUST chain AFTER requireSession, which
 * resolves the per-request capability SET from D1. A session lacking the capability → 403.
 * Field-ops READ/field actions gate on capability; admin-surface actions gate on requireRole.
 * FAIL-CLOSED: an empty/missing capability set (unknown role, or a D1 blip in
 * resolveCapabilities → empty Set) → 403. Never trust the client (Invariant 2) — the SPA
 * hiding a card is hinting, this is the boundary. (Was deferred in P0 until its first consumer.)
 */
const requireCapability = (cap: string) =>
  createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
    if (!c.get("capabilities").has(cap)) return c.json({ error: "forbidden" }, 403);
    await next();
  });

/**
 * OR-capability gate — authorizes if the session holds ANY of `caps`. Chains AFTER requireSession
 * (same as requireCapability). FAIL-CLOSED identically: an empty/missing capability set → none
 * match → 403. Used where a route accepts more than one capability (e.g. task create/assign accepts
 * cap.jobtracker.manage OR cap.tasks.assign). A finer-grained per-target guard (e.g. the
 * subcontractor-target check) lives IN the handler, which reads c.get("capabilities") directly.
 */
const requireAnyCapability = (caps: readonly string[]) =>
  createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
    const held = c.get("capabilities");
    if (!caps.some((cap) => held.has(cap))) return c.json({ error: "forbidden" }, 403);
    await next();
  });

// Field-ops READ layer (P2.2). Each tab owns its own route module; the gates are passed IN so
// the per-tab modules never import index.ts (no import cycle). Registered here (before the SPA
// catch-all). In Brief 0 these are no-op stubs; Briefs A/B/C implement them.
const fieldopsGates: FieldopsGates = { requireSession, requireCapability, requireAnyCapability };
registerPersonnelRoutes(app, fieldopsGates);
registerPersonnelWriteRoutes(app, fieldopsGates);
registerEquipmentRoutes(app, fieldopsGates);
registerJobTrackerRoutes(app, fieldopsGates);
registerMaterialsRoutes(app, fieldopsGates);
// — Assigned-Tasks tab (P4 S1) "My Tasks" read (cap.tasks.own) —
registerMyTasksRoutes(app, fieldopsGates);
// — Assigned-Tasks tab (P4 S2) checklist engine + per-job template editor (cap.checklist.manage) —
registerChecklistRoutes(app, fieldopsGates);
// — field-ops WRITE routes (P2.3); send-free D1 mutations, capability-gated, audit-batched —
registerTimeWriteRoutes(app, fieldopsGates);
registerJobWriteRoutes(app, fieldopsGates);
registerTaskWriteRoutes(app, fieldopsGates);
registerEquipmentFieldWriteRoutes(app, fieldopsGates);
registerEquipmentRosterWriteRoutes(app, fieldopsGates);
// — P2.6 crew→job placement (cap.crew.assign; Manager + admin), send-free D1 mutation —
registerCrewAssignRoutes(app, fieldopsGates);
registerMaterialWriteRoutes(app, fieldopsGates);
// — P6 progress rollup read (bearer-gated /api/internal/*, NOT a session gate) —
registerProgressRollupRoutes(app, requireInternalToken);

/** GET /api/session — who am I (used by the SPA on load to restore session). Returns
 *  the live role (from requireSession's per-request D1 read), so a demotion drops the
 *  admin tabs on the next session refresh. */
app.get("/api/session", requireSession, (c) => {
  const s = c.get("session");
  return c.json({
    user: { username: s.username, role: c.get("role"), capabilities: [...c.get("capabilities")] },
  });
});

/**
 * POST /api/logout — clear the session cookie AND server-side revoke it.
 *
 * Slice 8a (audit #7): logout now bumps users.session_epoch, so the just-cleared
 * cookie (which snapshotted the OLD epoch at issue) is now stale and rejected by
 * requireSession on any subsequent request — closing the audit's "logout is
 * client-side only / a captured cookie stays valid to iat+90d" gap. The epoch bump is
 * keyed on the username read from the (verified-signed) cookie; a garbage/absent
 * cookie or a D1 blip still clears the cookie and returns ok (logout must never fail
 * closed — the worst case is a no-op bump, never a stuck-logged-in user).
 */
app.post("/api/logout", async (c) => {
  // Best-effort epoch bump. getSignedCookie returns the value only if the HMAC
  // verifies, so we never bump on a forged username. Any error here is swallowed —
  // the cookie clear below is the contract; the bump is the revocation hardening.
  try {
    const raw = await getSignedCookie(c, c.env.SESSION_SIGNING_SECRET, COOKIE);
    if (raw) {
      const claims = JSON.parse(raw) as SessionClaims;
      if (typeof claims.username === "string") {
        await c.env.DB
          .prepare("UPDATE users SET session_epoch = session_epoch + 1 WHERE username = ?")
          .bind(claims.username)
          .run();
      }
    }
  } catch {
    // swallow — logout still clears the cookie below regardless
  }
  deleteCookie(c, COOKIE, { path: "/" });
  return c.json({ ok: true });
});

/** GET /api/jobs — Active jobs for the dropdown (from D1; the portal never reads Smartsheet). */
app.get("/api/jobs", requireSession, async (c) => {
  const { results } = await c.env.DB
    .prepare("SELECT job_id, project_name FROM jobs WHERE active = 1 ORDER BY project_name")
    .all<{ job_id: string; project_name: string }>();
  return c.json({ jobs: results });
});

/** GET /api/recent?job=&form=&date= — the latest prior submission for Amend prefill. */
app.get("/api/recent", requireSession, async (c) => {
  const job = c.req.query("job") ?? "";
  const form = c.req.query("form") ?? "";
  const date = c.req.query("date") ?? "";
  if (!job || !form || !date) return c.json({ submission: null });
  const row = await c.env.DB
    .prepare(
      "SELECT submission_uuid, payload_json FROM submissions " +
        "WHERE job_id=? AND form_code=? AND work_date=? ORDER BY created_at DESC LIMIT 1",
    )
    .bind(job, form, date)
    .first<{ submission_uuid: string; payload_json: string }>();
  if (!row) return c.json({ submission: null });
  return c.json({
    submission: { submission_uuid: row.submission_uuid, values: JSON.parse(row.payload_json) },
  });
});

/**
 * POST /api/submit — accept a structured submission, cache it in D1 (Amend
 * prefill), and return success.
 *
 * INVARIANT 1: this Worker still performs ZERO external transmission. The Phase-5
 * email shim (portal-noreply@ → safety@, HMAC-signed) is a SEPARATE component that
 * forwards this payload to intake.py; it is NOT wired here. INVARIANT 2: the body
 * is type-checked + length-bounded; the job_id is verified against D1.
 */
// ── Photo values (PR-1, 2026-06-12) ─────────────────────────────────────────────
// D1-inline transport (owner decision 2026-06-12): site photos ride payload_json as
// base64 JPEG/PNG inside `values`, so the canonicalPayload HMAC covers them with ZERO
// signing changes (regression-locked in test/photos.test.ts). The Worker enforces
// SHAPE/BOUNDS only — Invariant 2's trust boundary stays Mac-side (§34 screening in
// intake, PR-2) before any Box upload or render. worker/types.ts "No R2" stance is
// preserved: D1 remains the transient queue; Box remains the system of record; the
// daily prune already cleans filed rows. Never log photo bytes.
const PHOTO_MAX_PER_FIELD = 4;
const PHOTO_MAX_PER_SUBMISSION = 8;
const PHOTO_MAX_BYTES = 400_000; // decoded bytes, per photo (client targets ≤ this)
// Pre-photos cap was 1_000_000 (audit #1 era). D1 row practical ceiling is ~2MB;
// 1_800_000 leaves headroom for the non-photo values + SQL row overhead.
const PAYLOAD_MAX = 1_800_000;
const B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;

function b64DecodedLen(s: string): number {
  const pad = s.endsWith("==") ? 2 : s.endsWith("=") ? 1 : 0;
  return Math.floor((s.length * 3) / 4) - pad;
}
/** First decoded bytes must be JPEG (FF D8 FF) or PNG (89 50 4E 47). */
function photoMagicOk(b64: string): boolean {
  let head: string;
  try {
    head = atob(b64.slice(0, 8));
  } catch {
    return false;
  }
  if (head.length < 4) return false;
  const b = [head.charCodeAt(0), head.charCodeAt(1), head.charCodeAt(2), head.charCodeAt(3)];
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return true; // JPEG
  return b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47; // PNG
}
const PHOTO_KEYS = ["data", "name", "taken_at", "gps"] as const;
/** Exact-shape detection ({data,name,taken_at,gps}, all strings) so table-row arrays
 *  (Record<colKey,string>[]) are never misread as photo arrays. */
function isPhotoItem(x: unknown): x is Record<(typeof PHOTO_KEYS)[number], string> {
  if (typeof x !== "object" || x === null || Array.isArray(x)) return false;
  const o = x as Record<string, unknown>;
  const keys = Object.keys(o);
  return keys.length === PHOTO_KEYS.length && PHOTO_KEYS.every((k) => typeof o[k] === "string");
}
/** null = OK; string = machine reason for a 400 invalid_photo. */
function validatePhotoValues(values: Record<string, unknown>): string | null {
  let total = 0;
  for (const v of Object.values(values)) {
    if (!Array.isArray(v) || v.length === 0 || !v.some(isPhotoItem)) continue;
    if (!v.every(isPhotoItem)) return "mixed_photo_array";
    if (v.length > PHOTO_MAX_PER_FIELD) return "too_many_photos_in_field";
    for (const p of v) {
      if (p.name.length > 100 || p.taken_at.length > 40 || p.gps.length > 64) return "photo_meta_too_long";
      if (p.data.length === 0 || p.data.length % 4 !== 0 || !B64_RE.test(p.data)) return "photo_not_base64";
      if (b64DecodedLen(p.data) > PHOTO_MAX_BYTES) return "photo_too_large";
      if (!photoMagicOk(p.data)) return "photo_bad_magic";
      total += 1;
      if (total > PHOTO_MAX_PER_SUBMISSION) return "too_many_photos";
    }
  }
  return null;
}

app.post("/api/submit", requireSession, async (c) => {
  let body: Record<string, unknown>;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const str = (k: string) => (typeof body[k] === "string" ? (body[k] as string) : "");
  const job_id = str("job_id");
  const form_code = str("form_code");
  const work_date = str("work_date");
  const submission_uuid = str("submission_uuid");
  const amends_uuid = typeof body.amends_uuid === "string" ? body.amends_uuid : null;
  const values = body.values;
  if (
    !job_id || !form_code || !work_date || !submission_uuid ||
    job_id.length > 64 || form_code.length > 64 || work_date.length > 10 || submission_uuid.length > 64 ||
    (amends_uuid !== null && amends_uuid.length > 64) ||
    typeof values !== "object" || values === null || Array.isArray(values)
  ) {
    return c.json({ error: "invalid_submission" }, 400);
  }
  const job = await c.env.DB.prepare("SELECT 1 FROM jobs WHERE job_id=? AND active=1").bind(job_id).first();
  if (!job) return c.json({ error: "unknown_job" }, 422);
  // Photo bounds/shape gate (PR-1) — see validatePhotoValues above. Returns the machine
  // reason in `detail` (never the bytes) so the SPA can show a useful message.
  const photoErr = validatePhotoValues(values as Record<string, unknown>);
  if (photoErr) return c.json({ error: "invalid_photo", detail: photoErr }, 400);
  const payload = JSON.stringify(values);
  if (payload.length > PAYLOAD_MAX) return c.json({ error: "too_large" }, 413);

  // ── Submit-as ("filled out as") dual-attribution ──────────────────────────
  // The TRUE actor is the authenticated session user — always recorded, never
  // dropped (safety/audit invariant). `submitted_as` is the OPTIONAL attributed
  // account; absent or === actor means a normal self-submit. A non-self value is a
  // privileged impersonation and the SERVER is the gate (Invariant 2 — the SPA
  // hiding the selector for submitters is never the boundary):
  //   - it REQUIRES the live D1 role be 'admin' (set by requireSession), else 403;
  //   - the target must be a real, ENABLED account, else 422 (never attribute to a
  //     non-existent / locked user).
  const actor = c.get("session").username;
  const requestedAs = typeof body.submitted_as === "string" ? body.submitted_as : "";
  let attributed = actor; // default: self-submit
  const isSubmitAs = requestedAs !== "" && normalizeUsername(requestedAs) !== actor;
  if (isSubmitAs) {
    // Forging submitted_as as a non-admin is REJECTED outright — a submitter must
    // never be able to attribute a submission to someone else.
    if (c.get("role") !== "admin") return c.json({ error: "forbidden" }, 403);
    const target = normalizeUsername(requestedAs);
    if (!target) return c.json({ error: "unknown_attributed_user" }, 422);
    const row = await c.env.DB
      .prepare("SELECT disabled FROM users WHERE username=?")
      .bind(target)
      .first<{ disabled: number }>();
    if (!row || row.disabled) return c.json({ error: "unknown_attributed_user" }, 422);
    attributed = target;
  }

  // Fail closed on a misconfigured Worker: never sign with an undefined secret
  // (that would produce signatures the Mac side could never verify → silent loss).
  if (!c.env.HMAC_PAYLOAD_SECRET) return c.json({ error: "server_misconfigured" }, 503);
  // Sign the submission so the Mac-side portal_poll daemon can verify it before
  // intake files it. The SPA mints a FRESH uuid per amendment (useSubmissionId), so a same-uuid
  // re-submit is the designed lost-ACK RETRY, not an amendment; the M1 guard below rejects a
  // cross-actor uuid reuse and audits a filed/changed same-actor replace. INSERT OR REPLACE resets
  // box_verified=0 so a retry re-queues for filing. CRITICAL: the canonicalPayload (HMAC input) is
  // UNCHANGED by submit-as — actor_username/submitted_as are NOT part of it — so the
  // stored hmac is byte-identical to a normal submit and portal_poll's recompute still
  // verifies. (Regression-locked in test/submit-as.test.ts.)
  const hmac = await hmacHex(
    c.env.HMAC_PAYLOAD_SECRET,
    canonicalPayload({ submission_uuid, job_id, form_code, work_date, payload_json: payload }),
  );
  // The submission INSERT carries the two attribution columns (always written; on a
  // self-submit both equal `actor`). On a REAL submit-as we also write an audit_log
  // row in the SAME D1 batch, so the impersonation record can never land without its
  // security-log entry (atomic — mirrors the /api/admin/* mutate+audit pattern).
  // M1 (PR-4): an INSERT OR REPLACE silently overwrites any prior row for this uuid. Read it
  // first — a DIFFERENT actor reusing the uuid is never legitimate (409); a SAME-actor re-submit is
  // the designed retry (proceed) but is AUDITED when the prior row was already filed
  // (box_verified=1) or the payload changed (the filed PDF would then diverge from the new D1 row).
  const existing = await c.env.DB
    .prepare("SELECT actor_username, payload_json, box_verified FROM submissions WHERE submission_uuid=?")
    .bind(submission_uuid)
    .first<{ actor_username: string; payload_json: string; box_verified: number }>();
  if (existing && existing.actor_username !== actor) {
    return c.json({ error: "uuid_conflict" }, 409);
  }
  const isReplace =
    existing !== null && (existing.box_verified === 1 || existing.payload_json !== payload);

  const insertStmt = c.env.DB
    .prepare(
      "INSERT OR REPLACE INTO submissions " +
        "(submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid, hmac, box_verified, " +
        "actor_username, submitted_as) " +
        "VALUES (?,?,?,?,?,?,?,0,?,?)",
    )
    .bind(submission_uuid, job_id, form_code, work_date, payload, amends_uuid, hmac, actor, attributed);
  const stmts = [insertStmt];
  if (isSubmitAs) stmts.push(auditStmt(c, actor, "submit_as", attributed, { submission_uuid, job_id }));
  if (isReplace) {
    stmts.push(auditStmt(c, actor, "submission_replace", attributed, {
      submission_uuid, job_id,
      was_filed: existing!.box_verified === 1,
      payload_changed: existing!.payload_json !== payload,
    }));
  }
  if (stmts.length > 1) {
    await c.env.DB.batch(stmts);
  } else {
    await insertStmt.run();
  }
  return c.json({ ok: true, status: "submitted", submission_uuid });
});

// ─────────────────────────────────────────────────────────────────────────────
// Request-driven canonical PDF download (PR-4 Part A).
//
// Owner decision: the PM's downloadable copy IS the Box-filed copy, byte-identical
// (NO browser render). It is request-driven — nothing is cached until the user clicks
// "Make available for download". The Worker is SEND-FREE and holds NO Box creds: the
// Mac-side portal_poll daemon fetches the filed PDF from Box (by box_file_id),
// base64-chunks it, and POSTs the chunks to D1 (POST /api/internal/filed-pdf); GET
// /pdf reassembles the D1 chunks and serves the bytes. Cached chunks expire 24h past
// pdf_ready_at (prune.ts) and are re-requestable.
//
// ACCESS (Part A): the session username must equal submissions.actor_username (the
// TRUE actor who hit submit) OR submissions.submitted_as (the attributed account), OR
// the session role is 'admin'. EVERYONE ELSE → 404 (no enumeration), NOT 403. A row
// that does not exist is likewise 404 — the two are indistinguishable to the caller.
// ─────────────────────────────────────────────────────────────────────────────

/** The PDF-cache ownership row shape (the columns the 3 session routes select). */
interface PdfOwnRow {
  actor_username: string | null;
  submitted_as: string | null;
}
/**
 * Ownership gate for the session+ownership PDF routes. An admin sees any row; a
 * non-admin must be the true actor OR the attributed account. A missing row (null)
 * fails — the caller returns 404 (no 403, no enumeration).
 */
function ownsRow(row: PdfOwnRow | null, c: Context<{ Bindings: Env; Variables: Vars }>): boolean {
  if (!row) return false;
  if (c.get("role") === "admin") return true;
  const me = c.get("session").username;
  return row.actor_username === me || row.submitted_as === me;
}

/** Decode a base64 string to bytes (no length validation here — callers bound it). */
function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * POST /api/submissions/:uuid/request-pdf — mark a filed submission for caching.
 * Flips pdf_requested 0→1 (idempotent: a second request is a no-op). Audits ONLY the
 * real flip. Returns whether the cache is already ready. A rejected (box_verified=-1)
 * row is treated as not-found (404) — there is no PDF to serve.
 */
app.post("/api/submissions/:uuid/request-pdf", requireSession, async (c) => {
  const uuid = c.req.param("uuid");
  if (!uuid || uuid.length > 64) return c.json({ error: "not_found" }, 404);
  const row = await c.env.DB
    .prepare(
      "SELECT actor_username, submitted_as, job_id, box_verified, pdf_ready_at FROM submissions WHERE submission_uuid=?",
    )
    .bind(uuid)
    .first<{ actor_username: string | null; submitted_as: string | null; job_id: string; box_verified: number; pdf_ready_at: number | null }>();
  // Not found, not owned, or a rejected (bad-HMAC terminal) row → 404, no enumeration.
  if (!ownsRow(row, c) || row!.box_verified === -1) return c.json({ error: "not_found" }, 404);

  // changes() > 0 ⟺ a real 0→1 flip (the WHERE pdf_requested=0 makes a repeat a no-op);
  // audit ONLY on the flip via the changes()=1 conditional insert (same atomic-batch
  // pattern as mark-rejected). The flag-set + its audit run in ONE D1 batch.
  const res = await c.env.DB.batch([
    c.env.DB.prepare(
      "UPDATE submissions SET pdf_requested=1 WHERE submission_uuid=? AND pdf_requested=0",
    ).bind(uuid),
    c.env.DB.prepare(
      "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?,?,?,? WHERE changes()=1",
    ).bind(c.get("session").username, "request_pdf", null, JSON.stringify({ job_id: row!.job_id })),
    // PR-5: downloads are REQUESTER-BOUND — the submitter's request is the first
    // pdf_requests row (one row per submission+account). Re-request refreshes the 24h
    // window. submissions.pdf_requested stays as the legacy flag; pdf_requests is now the
    // authority for who may download and the Mac-serviceable set.
    c.env.DB.prepare(
      "INSERT INTO pdf_requests (submission_uuid, account, requested_at) VALUES (?,?,unixepoch()) " +
        "ON CONFLICT(submission_uuid, account) DO UPDATE SET requested_at=unixepoch(), ready_at=NULL",
    ).bind(uuid, c.get("session").username),
  ]);
  void res;
  // ready = the cache is already populated (pdf_ready_at set AND a chunk row exists).
  const chunk = row!.pdf_ready_at !== null
    ? await c.env.DB.prepare("SELECT 1 FROM filed_pdfs WHERE submission_uuid=? LIMIT 1").bind(uuid).first()
    : null;
  return c.json({ ok: true, ready: row!.pdf_ready_at !== null && chunk !== null });
});

/**
 * GET /api/submissions/:uuid/status — the SPA's 5s poll. Reports whether the user has
 * requested caching, whether the cache is ready to download, and when it expires.
 */
app.get("/api/submissions/:uuid/status", requireSession, async (c) => {
  const uuid = c.req.param("uuid");
  if (!uuid || uuid.length > 64) return c.json({ error: "not_found" }, 404);
  const row = await c.env.DB
    .prepare("SELECT actor_username, submitted_as, box_verified, pdf_ready_at FROM submissions WHERE submission_uuid=?")
    .bind(uuid)
    .first<{ actor_username: string | null; submitted_as: string | null; box_verified: number; pdf_ready_at: number | null }>();
  if (!row || row.box_verified === -1) return c.json({ error: "not_found" }, 404);

  // PR-5: REQUESTER-CENTRIC body. `requested` + the 24h `expires_at` come from THIS account's
  // own live pdf_requests row; `ready` additionally needs the cache populated.
  const me = c.get("session").username;
  const pr = await c.env.DB
    .prepare("SELECT requested_at FROM pdf_requests WHERE submission_uuid=? AND account=? AND requested_at > unixepoch()-86400")
    .bind(uuid, me)
    .first<{ requested_at: number }>();
  // Gate (no row-data enumeration): only an admin, the owner/attributee, or a live requester
  // may poll; everyone else gets the same 404 as an unknown uuid, leaking no row contents. (A
  // benign timing residual remains — an existing-but-unauthorized uuid does the second read —
  // matching /pdf; accepted, since UUIDs are unguessable.)
  const ownerOrAdmin = c.get("role") === "admin" || row.actor_username === me || row.submitted_as === me;
  if (!ownerOrAdmin && pr === null) return c.json({ error: "not_found" }, 404);
  const chunk = row.pdf_ready_at !== null
    ? await c.env.DB.prepare("SELECT 1 FROM filed_pdfs WHERE submission_uuid=? LIMIT 1").bind(uuid).first()
    : null;
  const cacheReady = row.pdf_ready_at !== null && chunk !== null;
  const requested = pr !== null;
  const ready = cacheReady && (requested || c.get("role") === "admin");
  const expires_at = pr ? pr.requested_at + 86_400 : null;
  return c.json({ requested, ready, expires_at });
});

/**
 * GET /api/submissions/:uuid/pdf — reassemble the cached chunks and serve the canonical
 * PDF as an attachment. 404 if not owned / not found / not yet cached. The Response is
 * BUILT DIRECTLY (a Hono-built Response with mutable headers) — never mutate an
 * ASSETS.fetch() response (the immutable-headers gotcha); the outer middleware re-wraps
 * it, preserving Content-Type/Content-Disposition and adding Cache-Control:no-store.
 */
app.get("/api/submissions/:uuid/pdf", requireSession, async (c) => {
  const uuid = c.req.param("uuid");
  if (!uuid || uuid.length > 64) return c.json({ error: "not_found" }, 404);
  const row = await c.env.DB
    .prepare(
      "SELECT s.box_verified, s.form_code, s.work_date, s.pdf_ready_at, j.project_name " +
        "FROM submissions s LEFT JOIN jobs j ON j.job_id = s.job_id WHERE s.submission_uuid=?",
    )
    .bind(uuid)
    .first<{ box_verified: number; form_code: string; work_date: string; pdf_ready_at: number | null; project_name: string | null }>();
  if (!row || row.box_verified === -1) return c.json({ error: "not_found" }, 404);
  // PR-5: REQUESTER-BOUND. Admins always; otherwise the session account must hold a LIVE
  // pdf_requests row (requested within 24h) for this uuid. A DIFFERENT authenticated account —
  // even the actor/attributee who never requested — gets 404 (the staged PDF is private to
  // its requester; no enumeration).
  if (c.get("role") !== "admin") {
    const pr = await c.env.DB
      .prepare("SELECT 1 FROM pdf_requests WHERE submission_uuid=? AND account=? AND requested_at > unixepoch()-86400")
      .bind(uuid, c.get("session").username)
      .first();
    if (!pr) return c.json({ error: "not_found" }, 404);
  }
  if (row!.pdf_ready_at === null) return c.json({ error: "not_ready" }, 404);

  const { results } = await c.env.DB
    .prepare("SELECT chunk_b64 FROM filed_pdfs WHERE submission_uuid=? ORDER BY chunk_index")
    .bind(uuid)
    .all<{ chunk_b64: string }>();
  if (!results || results.length === 0) return c.json({ error: "not_ready" }, 404);

  // Decode each chunk to bytes, then concat into a single Uint8Array (the original PDF).
  const parts = results.map((r) => b64ToBytes(r.chunk_b64));
  const total = parts.reduce((n, p) => n + p.length, 0);
  const bytes = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    bytes.set(p, off);
    off += p.length;
  }
  // Job-prefixed <job>_<work_date>_<form>.pdf to match the Box-filed naming scheme
  // (2026-06-17). Spaces are allowed (the header value is quoted); the rest is sanitized to
  // a safe set so the filename can never break the Content-Disposition header. Falls back to
  // the unprefixed name when the job row is gone (LEFT JOIN → project_name null).
  const jobName = (row!.project_name ?? "").trim();
  const safe = `${jobName ? jobName + "_" : ""}${row!.work_date}_${row!.form_code}.pdf`
    .replace(/[^A-Za-z0-9._ -]/g, "");
  return new Response(bytes, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `attachment; filename="${safe}"`,
    },
  });
});

/**
 * GET /api/filed?job_id=… — PR-5 browse: an ACTIVE job's filed forms with THIS account's
 * per-row request/ready state. requireSession. 404 unless the job is active (browse is
 * scoped to active jobs). Metadata only — no payloads, no PDFs.
 */
app.get("/api/filed", requireSession, async (c) => {
  const job_id = c.req.query("job_id") ?? "";
  if (!job_id || job_id.length > 64) return c.json({ error: "not_found" }, 404);
  // PR-6 optional cascade filters. Empty-string ("?month=") is treated as ABSENT (no filter,
  // no 400). month → the work-month (substr of work_date); form_code → the exact form code.
  const month = c.req.query("month") || undefined;
  if (month !== undefined && !/^\d{4}-\d{2}$/.test(month)) {
    return c.json({ error: "bad_request", detail: "month" }, 400);
  }
  const form_code = c.req.query("form_code") || undefined;
  if (form_code !== undefined && form_code.length > 64) {
    return c.json({ error: "bad_request", detail: "form_code" }, 400);
  }
  const active = await c.env.DB
    .prepare("SELECT 1 FROM jobs WHERE job_id=? AND active=1")
    .bind(job_id)
    .first();
  if (!active) return c.json({ error: "not_found" }, 404);
  // LEFT JOIN this account's LIVE request (the 24h window is in the ON clause, so an expired
  // request stops matching). ready = the cache is populated AND this account has a live request.
  // month/form_code add BOUND WHERE terms only; the per-account join, ordering, defensive
  // LIMIT 500, and response shape are unchanged from PR-5. Bind order follows placeholder order:
  // [account (the JOIN's pr.account=?), job_id, month?, form_code?].
  const where =
    "WHERE s.job_id=? AND s.box_verified=1" +
    (month !== undefined ? " AND substr(s.work_date,1,7) = ?" : "") +
    (form_code !== undefined ? " AND s.form_code = ?" : "");
  const binds: unknown[] = [c.get("session").username, job_id];
  if (month !== undefined) binds.push(month);
  if (form_code !== undefined) binds.push(form_code);
  const { results } = await c.env.DB
    .prepare(
      "SELECT s.submission_uuid, s.form_code, s.work_date, s.filed_at, " +
        "(s.pdf_ready_at IS NOT NULL) AS cache_ready, (pr.requested_at IS NOT NULL) AS requested " +
        "FROM submissions s " +
        "LEFT JOIN pdf_requests pr ON pr.submission_uuid=s.submission_uuid AND pr.account=? " +
        "AND pr.requested_at > unixepoch()-86400 " +
        where + " " +
        "ORDER BY s.filed_at DESC, s.created_at DESC LIMIT 500",
    )
    .bind(...binds)
    .all<{ submission_uuid: string; form_code: string; work_date: string; filed_at: number | null; cache_ready: number; requested: number }>();
  const filed = (results ?? []).map((r) => ({
    submission_uuid: r.submission_uuid,
    form_code: r.form_code,
    work_date: r.work_date,
    filed_at: r.filed_at,
    requested: r.requested === 1,
    ready: r.cache_ready === 1 && r.requested === 1,
  }));
  return c.json({ filed });
});

/**
 * GET /api/filed/months?job_id=… — PR-6 cascade source for the Form Request page. Returns
 * the work-months that actually have filed forms (newest-first, each with a count) and the
 * distinct form codes present for the job, so a year-long job's hundreds of filed forms don't
 * dump in one flat 500-capped table. requireSession. 404 unless the job is active (same guard
 * + {error:"not_found"} shape as /api/filed — no enumeration). Job-scoped aggregates only; no
 * per-account state leaks (unlike /api/filed's per-row request/ready flags).
 */
app.get("/api/filed/months", requireSession, async (c) => {
  const job_id = c.req.query("job_id") ?? "";
  if (!job_id || job_id.length > 64) return c.json({ error: "not_found" }, 404);
  const active = await c.env.DB
    .prepare("SELECT 1 FROM jobs WHERE job_id=? AND active=1")
    .bind(job_id)
    .first();
  if (!active) return c.json({ error: "not_found" }, 404);
  const monthsRes = await c.env.DB
    .prepare(
      "SELECT substr(work_date,1,7) AS month, COUNT(*) AS count " +
        "FROM submissions WHERE job_id=? AND box_verified=1 " +
        "GROUP BY month ORDER BY month DESC",
    )
    .bind(job_id)
    .all<{ month: string; count: number }>();
  const codesRes = await c.env.DB
    .prepare("SELECT DISTINCT form_code FROM submissions WHERE job_id=? AND box_verified=1 ORDER BY form_code")
    .bind(job_id)
    .all<{ form_code: string }>();
  return c.json({
    months: (monthsRes.results ?? []).map((r) => ({ month: r.month, count: r.count })),
    form_codes: (codesRes.results ?? []).map((r) => r.form_code),
  });
});

/**
 * POST /api/request-pdfs — PR-5 batch request. requireSession. Body { uuids: string[] }
 * (cap 20). For each uuid that is a FILED submission on an ACTIVE job, upsert a pdf_requests
 * row for the session account (refreshing the 24h window). Any authenticated account may
 * request any active-job filed form (mirrors the submit model); the download is then bound
 * to THIS requester. ONE audit row per batch. Returns { requested: <count upserted> }.
 */
app.post("/api/request-pdfs", requireSession, async (c) => {
  let body: Record<string, unknown>;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const raw = body.uuids;
  if (!Array.isArray(raw) || raw.length === 0) return c.json({ error: "bad_request", detail: "uuids" }, 400);
  if (raw.length > 20) return c.json({ error: "too_many", detail: "max 20 per batch" }, 400);
  const clean = [...new Set(raw.filter((u): u is string => typeof u === "string" && u.length > 0 && u.length <= 64))];
  if (clean.length === 0) return c.json({ requested: 0 });
  // Only FILED submissions on ACTIVE jobs are requestable.
  const placeholders = clean.map(() => "?").join(",");
  const { results } = await c.env.DB
    .prepare(
      "SELECT s.submission_uuid FROM submissions s JOIN jobs j ON j.job_id=s.job_id " +
        `WHERE s.submission_uuid IN (${placeholders}) AND s.box_verified=1 AND j.active=1`,
    )
    .bind(...clean)
    .all<{ submission_uuid: string }>();
  const valid = (results ?? []).map((r) => r.submission_uuid);
  if (valid.length === 0) return c.json({ requested: 0 });
  const account = c.get("session").username;
  const stmts = valid.map((u) =>
    c.env.DB.prepare(
      "INSERT INTO pdf_requests (submission_uuid, account, requested_at) VALUES (?,?,unixepoch()) " +
        "ON CONFLICT(submission_uuid, account) DO UPDATE SET requested_at=unixepoch(), ready_at=NULL",
    ).bind(u, account),
  );
  stmts.push(auditStmt(c, account, "request_pdfs", null, { count: valid.length, uuids: valid }));
  await c.env.DB.batch(stmts);
  return c.json({ requested: valid.length });
});

/**
 * GET /api/internal/pending — the queue drain for the Mac-side portal_poll daemon.
 * Returns unfiled submissions (box_verified=0) oldest-first, each with the Worker's
 * HMAC so the daemon verifies integrity before intake files it. Bearer-token gated.
 */
app.get("/api/internal/pending", requireInternalToken, async (c) => {
  const limit = Math.min(Number(c.req.query("limit")) || 50, 200);
  const { results } = await c.env.DB
    .prepare(
      "SELECT submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid, hmac, created_at " +
        "FROM submissions WHERE box_verified = 0 ORDER BY created_at ASC LIMIT ?",
    )
    .bind(limit)
    .all();
  return c.json({ pending: results });
});

/**
 * POST /api/internal/mark-filed — the receipt. intake calls this after it files a
 * submission to Smartsheet + Box; flips box_verified=1 so the queue drains and the
 * portal can show "received & filed." Idempotent. Bearer-token gated.
 */
app.post("/api/internal/mark-filed", requireInternalToken, async (c) => {
  let body: Record<string, unknown>;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const submission_uuid = typeof body.submission_uuid === "string" ? body.submission_uuid : "";
  const box_link = typeof body.box_link === "string" ? body.box_link.slice(0, 2000) : null;
  // box_file_id (PR-4): the filed Box file id the pdf-cache pass downloads + chunks. The
  // daemon supplies it on the receipt; bounded like box_link. NULL when not supplied.
  const boxFileId = typeof body.box_file_id === "string" ? body.box_file_id.slice(0, 200) : null;
  if (!submission_uuid || submission_uuid.length > 64) return c.json({ error: "invalid" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE submissions SET box_verified=1, filed_at=unixepoch(), box_link=?, box_file_id=? WHERE submission_uuid=?")
    .bind(box_link, boxFileId, submission_uuid)
    .run();
  return c.json({ ok: true, found: (res.meta?.changes ?? 0) > 0 });
});

/**
 * POST /api/internal/mark-rejected — terminal state (M4, PR-4) for a submission the Mac side
 * refuses to file (a bad-HMAC row). Without this, a box_verified=0 row is re-served by /pending
 * EVERY cycle forever. Sets box_verified=-1 (terminal — /pending selects =0, so it drops out) on
 * an UNFILED row only; records the reason in audit_log (changes()=1 so a no-op write logs nothing).
 * prune.ts deletes rejected rows after 30d. Idempotent. Bearer-token gated.
 */
app.post("/api/internal/mark-rejected", requireInternalToken, async (c) => {
  let body: Record<string, unknown>;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const submission_uuid = typeof body.submission_uuid === "string" ? body.submission_uuid : "";
  const reason = typeof body.reason === "string" ? body.reason.slice(0, 2000) : null;
  if (!submission_uuid || submission_uuid.length > 64) return c.json({ error: "invalid" }, 400);
  const res = await c.env.DB.batch([
    c.env.DB.prepare(
      "UPDATE submissions SET box_verified=-1, filed_at=unixepoch() WHERE submission_uuid=? AND box_verified=0",
    ).bind(submission_uuid),
    c.env.DB.prepare(
      "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?,?,?,? WHERE changes()=1",
    ).bind("portal_poll", "submission_rejected", null, JSON.stringify({ submission_uuid, reason })),
  ]);
  return c.json({ ok: true, found: (res[0]?.meta?.changes ?? 0) > 0 });
});

// ── PR-4 Part A: the canonical-PDF cache servicing endpoints (Mac portal_poll pass) ──
// Both bearer-gated (requireInternalToken — the daemon token, NOT the admin one). The
// daemon GETs the serviceable set, downloads each from Box, base64-chunks it, and POSTs
// the chunks here. Idempotent: a re-served request after a lost receipt is a no-op.
const MAX_CHUNKS = 8;
const CHUNK_B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;
const CHUNK_DECODED_MAX = 1_000_000;
// Cap the b64 STRING length before the O(n) regex scan so an oversized chunk_b64 is
// rejected in O(1) without traversing the whole string (defence-in-depth DoS guard).
const MAX_CHUNK_B64_LEN = Math.ceil((CHUNK_DECODED_MAX * 4) / 3) + 4; // ~1,333,338

/**
 * GET /api/internal/pdf-requests — the serviceable set for the Mac pdf-cache pass:
 * filed rows with a LIVE pdf_requests row (someone requested within 24h), not yet cached
 * (pdf_ready_at IS NULL), and filed (box_file_id IS NOT NULL — a Box file to download).
 * Oldest-first.
 * Returns a NAMED field (never a bare array — portal_client._request rejects non-object
 * JSON). Bearer-token gated.
 */
app.get("/api/internal/pdf-requests", requireInternalToken, async (c) => {
  const limit = Math.min(Math.max(parseInt(c.req.query("limit") || "25", 10) || 25, 1), 100);
  const { results } = await c.env.DB
    .prepare(
      "SELECT s.submission_uuid, s.box_file_id, s.form_code, s.work_date FROM submissions s " +
        "WHERE s.pdf_ready_at IS NULL AND s.box_file_id IS NOT NULL AND s.box_verified=1 " +
        "AND EXISTS (SELECT 1 FROM pdf_requests pr WHERE pr.submission_uuid=s.submission_uuid " +
        "AND pr.requested_at > unixepoch()-86400) " +
        "ORDER BY s.filed_at LIMIT ?",
    )
    .bind(limit)
    .all();
  return c.json({ pdf_requests: results });
});

/**
 * POST /api/internal/filed-pdf — idempotent chunk upload. The daemon POSTs each
 * base64 chunk (index + total + bytes); when the row count reaches chunk_total the
 * cache is complete and pdf_ready_at is stamped. INSERT OR REPLACE makes a re-POST of
 * the same chunk a no-op. If pdf_ready_at is already set the upload is a no-op
 * (idempotent — already cached). Bearer-token gated.
 */
app.post("/api/internal/filed-pdf", requireInternalToken, async (c) => {
  let body: Record<string, unknown>;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const submission_uuid = typeof body.submission_uuid === "string" ? body.submission_uuid : "";
  const chunk_index = body.chunk_index;
  const chunk_total = body.chunk_total;
  const chunk_b64 = body.chunk_b64;
  // Type + bounds validation (Invariant 2: all daemon input is untrusted too).
  if (!submission_uuid || submission_uuid.length > 64) {
    return c.json({ error: "invalid_chunk", detail: "submission_uuid" }, 400);
  }
  if (typeof chunk_index !== "number" || !Number.isInteger(chunk_index) || chunk_index < 0) {
    return c.json({ error: "invalid_chunk", detail: "chunk_index" }, 400);
  }
  if (typeof chunk_total !== "number" || !Number.isInteger(chunk_total) || chunk_total < 1 || chunk_total > MAX_CHUNKS) {
    return c.json({ error: "invalid_chunk", detail: "chunk_total" }, 400);
  }
  if (chunk_index >= chunk_total) {
    return c.json({ error: "invalid_chunk", detail: "chunk_index_range" }, 400);
  }
  if (
    typeof chunk_b64 !== "string" ||
    chunk_b64.length === 0 ||
    chunk_b64.length > MAX_CHUNK_B64_LEN ||
    !CHUNK_B64_RE.test(chunk_b64)
  ) {
    return c.json({ error: "invalid_chunk", detail: "chunk_b64" }, 400);
  }
  if (b64DecodedLen(chunk_b64) > CHUNK_DECODED_MAX) {
    return c.json({ error: "invalid_chunk", detail: "chunk_too_large" }, 400);
  }

  // The row must exist AND be filed (box_verified=1) — never cache an unfiled / rejected row.
  const row = await c.env.DB
    .prepare("SELECT box_verified, pdf_ready_at FROM submissions WHERE submission_uuid=?")
    .bind(submission_uuid)
    .first<{ box_verified: number; pdf_ready_at: number | null }>();
  if (!row || row.box_verified !== 1) return c.json({ error: "not_found" }, 404);
  // Already cached → idempotent no-op (a re-served request after a lost receipt).
  if (row.pdf_ready_at !== null) return c.json({ ok: true, ready: true, stored: false });

  await c.env.DB
    .prepare(
      "INSERT OR REPLACE INTO filed_pdfs (submission_uuid, chunk_index, chunk_total, chunk_b64) VALUES (?,?,?,?)",
    )
    .bind(submission_uuid, chunk_index, chunk_total, chunk_b64)
    .run();
  // Completion is gated on a CONSISTENT, GAP-FREE set — not a bare COUNT===chunk_total,
  // which a buggy/forged daemon could satisfy with the wrong indices (e.g. {0,1,5} for
  // chunk_total=3) and make GET /pdf serve a silently-truncated PDF as the canonical
  // record. Require: all chunks agree on chunk_total (totals===1), there are exactly t
  // chunks (n===t), and the highest index is t-1 (maxidx===t-1). Distinct indices (the
  // PRIMARY KEY) with count t and max t-1 ⇒ exactly {0..t-1}, i.e. gap-free.
  const agg = await c.env.DB
    .prepare(
      "SELECT COUNT(*) AS n, COUNT(DISTINCT chunk_total) AS totals, MAX(chunk_total) AS t, MAX(chunk_index) AS maxidx FROM filed_pdfs WHERE submission_uuid=?",
    )
    .bind(submission_uuid)
    .first<{ n: number; totals: number; t: number; maxidx: number }>();
  const n = agg?.n ?? 0;
  const complete = agg?.totals === 1 && n === agg.t && agg.maxidx === agg.t - 1;
  if (complete) {
    // Stamp ready once, only on the first completion (the WHERE pdf_ready_at IS NULL
    // guard keeps a racing duplicate completion idempotent).
    await c.env.DB
      .prepare("UPDATE submissions SET pdf_ready_at=unixepoch() WHERE submission_uuid=? AND pdf_ready_at IS NULL")
      .bind(submission_uuid)
      .run();
  }
  return c.json({ ok: true, ready: complete, stored: true, received: n });
});

/**
 * POST /api/internal/sync — full-replace sync of the active-job set from the Mac
 * side (portal_poll reads ITS_Active_Jobs and POSTs the COMPLETE set each cycle).
 * Bearer-token gated. This is the write-leg counterpart to GET /api/jobs (which
 * the SPA reads): Smartsheet is the source of truth, D1 is the dropdown cache.
 *
 * Body: { jobs: [{ job_id, project_name, active }] } — the complete ITS_Active_Jobs
 * set, each row carrying its own active flag (1/0). The payload is AUTHORITATIVE:
 * any D1 job_id ABSENT from it is deactivated (active=0) so a job removed/archived
 * in Smartsheet drops off the dropdown. We never DELETE (submissions reference
 * job_id — deactivate, don't orphan). Upserts + the single reconcile run in ONE
 * atomic D1 batch.
 *
 * INVARIANT 1: still ZERO external transmission — this only writes D1; the Mac side
 * initiated the request, the Worker sends nothing outward. INVARIANT 2: every row
 * is type-checked + length-bounded, all D1 access is parameter-bound, the batch is
 * size-capped, and an EMPTY payload is rejected (it would otherwise wipe the whole
 * dropdown — a Smartsheet read miss on the Mac side must never reach here as []).
 */
app.post("/api/internal/sync", requireInternalToken, async (c) => {
  let body: { jobs?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const raw = body.jobs;
  if (!Array.isArray(raw)) return c.json({ error: "invalid_jobs" }, 400);
  if (raw.length === 0) return c.json({ error: "empty_jobs" }, 400); // never wipe the dropdown
  if (raw.length > 5000) return c.json({ error: "too_many_jobs" }, 413);

  // Validate + normalize every row up front; reject the WHOLE batch on any bad row
  // (a partial sync would silently desync the dropdown).
  const jobs: { job_id: string; project_name: string; active: number }[] = [];
  const seen = new Set<string>();
  for (const r of raw) {
    if (typeof r !== "object" || r === null) return c.json({ error: "invalid_row" }, 400);
    const row = r as Record<string, unknown>;
    const job_id = typeof row.job_id === "string" ? row.job_id : "";
    const project_name = typeof row.project_name === "string" ? row.project_name : "";
    const active = row.active === 1 || row.active === true ? 1 : 0;
    if (!job_id || job_id.length > 64 || !project_name || project_name.length > 256) {
      return c.json({ error: "invalid_row" }, 400);
    }
    if (seen.has(job_id)) return c.json({ error: "duplicate_job_id" }, 400);
    seen.add(job_id);
    jobs.push({ job_id, project_name, active });
  }

  // P2.5 canonical-aware pre-pass: once the mirror daemon promotes a portal job, the SAFETY sheet
  // assigns it a JOB-#### and list_all_jobs() pushes that JOB-#### here — but the D1 row is
  // origin='portal' keyed by the TYPED job_id, so a naive ON CONFLICT(job_id) would MISS and INSERT
  // a duplicate origin='smartsheet' row (a persistent ghost in the dropdown). Drop any pushed row
  // whose job_id equals a portal row's canonical_job_id (the safety read-back) from the UPSERT set.
  // The DEACTIVATION still uses the FULL pushed id list: those canonical JOB-####s correspond to
  // origin='portal' rows (never origin='smartsheet'), so they're inert in the smartsheet-scoped
  // sweep, and keeping them keeps the bound NOT-IN list non-empty + the sweep correct.
  const canonRows = await c.env.DB
    .prepare("SELECT canonical_job_id FROM jobs WHERE origin='portal' AND canonical_job_id IS NOT NULL")
    .all<{ canonical_job_id: string }>();
  const canonical = new Set((canonRows.results ?? []).map((r) => r.canonical_job_id));
  const toUpsert = jobs.filter((j) => !canonical.has(j.job_id));

  // One atomic batch: upsert every NON-canonical supplied row, then deactivate any active D1
  // job_id NOT in the (full) payload (the NOT-IN list is bound, never interpolated).
  const ids = jobs.map((j) => j.job_id);
  const statements = [
    ...toUpsert.map((j) =>
      c.env.DB.prepare(
        "INSERT INTO jobs (job_id, project_name, active) VALUES (?,?,?) " +
          "ON CONFLICT(job_id) DO UPDATE SET project_name=excluded.project_name, active=excluded.active",
      ).bind(j.job_id, j.project_name, j.active),
    ),
    c.env.DB.prepare(
      // origin fence (migration 0017): only smartsheet-origin jobs participate in the
      // full-replace deactivation. A portal-CREATED job (origin='portal') is absent from the
      // Smartsheet payload until the mirror daemon promotes it, so it must never be deactivated here.
      // ORDER DEPENDENCY: migration 0017 (the `origin` column) must be live BEFORE this Worker
      // deploys, else this UPDATE 500s on an unknown column (mirror of the 0007/0009 activation rule).
      `UPDATE jobs SET active=0 WHERE active=1 AND origin='smartsheet' AND job_id NOT IN (${ids.map(() => "?").join(",")})`,
    ).bind(...ids),
  ];
  const results = await c.env.DB.batch(statements);
  const deactivated = results[results.length - 1]?.meta?.changes ?? 0;
  return c.json({ ok: true, upserted: toUpsert.length, deactivated });
});

/**
 * Field-ops job-mirror queue — /api/internal/fieldops/* (requireFieldopsToken, the mirror
 * daemon's OWN secret; privilege-separated from the portal_poll + admin tokens). P2.5 up-sync.
 *
 * GET /pending-jobs — dirty portal jobs (origin='portal' AND sync_state='pending'): the full SoR
 * payload + the version vector + cached Smartsheet row ids the daemon needs to find-or-create a row
 * in BOTH Active-Jobs sheets. Read-only; bound SQL; capped at 200 rows/cycle (the daemon drains
 * across cycles). CC arrays are returned parsed (JSON → string[]).
 */
const FIELDOPS_PENDING_CAP = 200;
app.get("/api/internal/fieldops/pending-jobs", requireFieldopsToken, async (c) => {
  const rows = await c.env.DB
    .prepare(
      `SELECT job_id, project_name, lifecycle, address,
              stakeholder_name, stakeholder_email, stakeholder_phone,
              safety_contact_name, safety_contact_email, safety_cc,
              progress_contact_name, progress_contact_email, progress_cc,
              mirror_version, safety_mirrored_version, progress_mirrored_version,
              safety_row_id, progress_row_id, canonical_job_id
         FROM jobs
        WHERE origin='portal' AND sync_state='pending'
        ORDER BY mirror_version ASC, job_id ASC
        LIMIT ?1`,
    )
    .bind(FIELDOPS_PENDING_CAP)
    .all<Record<string, unknown>>();
  const parseCcJson = (v: unknown): string[] => {
    if (typeof v !== "string" || !v) return [];
    try {
      const a = JSON.parse(v);
      return Array.isArray(a) ? a.filter((x) => typeof x === "string") : [];
    } catch {
      return [];
    }
  };
  const jobs = (rows.results ?? []).map((r) => ({
    ...r,
    safety_cc: parseCcJson(r.safety_cc),
    progress_cc: parseCcJson(r.progress_cc),
  }));
  return c.json({ jobs });
});

/**
 * POST /jobs-mark-mirrored — the daemon's per-sheet commit point. Body:
 *   { updates: [{ job_id, sheet: 'safety'|'progress', mirrored_version, row_id, canonical_job_id? }] }
 * For each update: MONOTONICALLY advance ONLY that sheet's watermark (MAX, so a stale/replayed call
 * can never regress it), cache that sheet's row_id, and — for the SAFETY sheet only — write back the
 * canonical_job_id (the sheet's read-back JOB-####, COALESCE so a null never erases it). Then flip
 * sync_state→'synced' IFF both watermarks have reached mirror_version (else it stays 'pending' and
 * the job is re-attempted next cycle — the partial-failure self-heal). One atomic batch + a single
 * summary audit row. row-set is disjoint from the down-sync (origin='portal'), so no write conflict.
 */
app.post("/api/internal/fieldops/jobs-mark-mirrored", requireFieldopsToken, async (c) => {
  let body: { updates?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const raw = body.updates;
  if (!Array.isArray(raw)) return c.json({ error: "invalid_updates" }, 400);
  if (raw.length === 0) return c.json({ error: "empty_updates" }, 400);
  if (raw.length > FIELDOPS_PENDING_CAP) return c.json({ error: "too_many_updates" }, 413);

  const statements = [];
  const touched: string[] = [];
  for (const u of raw) {
    if (typeof u !== "object" || u === null || Array.isArray(u)) return c.json({ error: "invalid_update" }, 400);
    const row = u as Record<string, unknown>;
    const jobId = typeof row.job_id === "string" ? row.job_id : "";
    const sheet = row.sheet === "safety" || row.sheet === "progress" ? row.sheet : "";
    const version = typeof row.mirrored_version === "number" && Number.isInteger(row.mirrored_version) ? row.mirrored_version : -1;
    const rowId = typeof row.row_id === "number" && Number.isInteger(row.row_id) ? row.row_id : null;
    const canonical = typeof row.canonical_job_id === "string" && row.canonical_job_id ? row.canonical_job_id : null;
    if (!jobId || jobId.length > 64 || !sheet || version < 0 || rowId === null) {
      return c.json({ error: "invalid_update" }, 400);
    }
    if (sheet === "safety") {
      statements.push(
        c.env.DB
          .prepare(
            "UPDATE jobs SET safety_mirrored_version=MAX(safety_mirrored_version, ?2), safety_row_id=?3, " +
              "canonical_job_id=COALESCE(?4, canonical_job_id) WHERE job_id=?1 AND origin='portal'",
          )
          .bind(jobId, version, rowId, canonical),
      );
    } else {
      statements.push(
        c.env.DB
          .prepare(
            "UPDATE jobs SET progress_mirrored_version=MAX(progress_mirrored_version, ?2), progress_row_id=?3 " +
              "WHERE job_id=?1 AND origin='portal'",
          )
          .bind(jobId, version, rowId),
      );
    }
    // Flip the dirty flag only when BOTH sheets have caught up to the current mirror_version.
    statements.push(
      c.env.DB
        .prepare(
          "UPDATE jobs SET sync_state=CASE WHEN safety_mirrored_version>=mirror_version " +
            "AND progress_mirrored_version>=mirror_version THEN 'synced' ELSE 'pending' END " +
            "WHERE job_id=?1 AND origin='portal'",
        )
        .bind(jobId),
    );
    touched.push(`${jobId}:${sheet}`);
  }
  // One summary audit row for the whole batch (system actor — token-gated daemon, no session).
  statements.push(
    c.env.DB
      .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) VALUES (?1,?2,?3,?4)")
      .bind("system:fieldops_sync", "jobs_mark_mirrored", "", JSON.stringify({ count: touched.length, touched: touched.slice(0, 50) })),
  );
  await c.env.DB.batch(statements);
  return c.json({ ok: true, updated: raw.length });
});

/**
 * Operator user provisioning — /api/internal/admin/* (requireAdminToken, the
 * operator-only secret). The operator passes PLAINTEXT over this bearer-gated
 * channel; the BACKEND bcrypt-hashes (cost 10) before write — plaintext is never
 * stored, returned, or logged. NOT a self-service UI and NO user-role model; these
 * are operator-run endpoints driven by the Mac `portal_admin` CLI (brief §4).
 */
async function setUserDisabled(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  value: 0 | 1,
): Promise<Response> {
  let body: { username?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  if (!username) return c.json({ error: "invalid_username" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE users SET disabled=? WHERE username=?")
    .bind(value, username)
    .run();
  if ((res.meta?.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, username, disabled: value });
}

/** Validate an optional `role` body field. undefined → `dflt`; 'admin'/'submitter'
 *  → that value; anything else → null (caller returns 400 invalid_role). Never
 *  coerces a junk value to a privilege — an unknown role is rejected, not defaulted. */
// POST /api/internal/admin/users — provision a new user (409 if it exists). Accepts
// an optional `role` (default 'submitter') so the operator can bootstrap the two
// admins via `portal_admin add-user --role admin`.
app.post("/api/internal/admin/users", requireAdminToken, async (c) => {
  let body: { username?: unknown; password?: unknown; role?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const password = typeof body.password === "string" ? body.password : "";
  const role = parseRole(body.role);
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (password.length < 8 || password.length > 256) return c.json({ error: "invalid_password" }, 400);
  if (role === null) return c.json({ error: "invalid_role" }, 400);
  const exists = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(username).first();
  if (exists) return c.json({ error: "exists" }, 409);
  const password_hash = await hashPassword(password); // plaintext never stored/logged
  try {
    await c.env.DB
      .prepare("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)")
      .bind(username, password_hash, role)
      .run();
  } catch (e) {
    // Race backstop (audit #5): concurrent create → UNIQUE violation → 409, not 500.
    if (isUniqueViolation(e)) return c.json({ error: "exists" }, 409);
    throw e;
  }
  return c.json({ ok: true, username, role }, 201);
});

// POST /api/internal/admin/users/role — set an existing user's role (404 if absent).
// Operator break-glass for the role model (e.g. restore an admin the UI demoted).
// NO last-admin guard here on purpose: the CLI is the recovery path *out* of a
// zero-admin lockout, so it must never refuse on admin-count grounds.
app.post("/api/internal/admin/users/role", requireAdminToken, async (c) => {
  let body: { username?: unknown; role?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const role = parseRole(body.role, "submitter");
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (body.role === undefined || role === null) return c.json({ error: "invalid_role" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE users SET role=? WHERE username=?")
    .bind(role, username)
    .run();
  if ((res.meta?.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, username, role });
});

// POST /api/internal/admin/users/reset — re-hash an existing user's password (404 if absent).
app.post("/api/internal/admin/users/reset", requireAdminToken, async (c) => {
  let body: { username?: unknown; password?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const password = typeof body.password === "string" ? body.password : "";
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (password.length < 8 || password.length > 256) return c.json({ error: "invalid_password" }, 400);
  const password_hash = await hashPassword(password); // plaintext never stored/logged
  // Slice 8a (audit #7): a password change BUMPS session_epoch in the SAME UPDATE, so
  // every outstanding cookie for this user is revoked on its next request.
  const res = await c.env.DB
    .prepare("UPDATE users SET password_hash=?, session_epoch = session_epoch + 1 WHERE username=?")
    .bind(password_hash, username)
    .run();
  if ((res.meta?.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, username });
});

// POST /api/internal/admin/users/disable — disabled=1; /enable — disabled=0.
app.post("/api/internal/admin/users/disable", requireAdminToken, (c) => setUserDisabled(c, 1));
app.post("/api/internal/admin/users/enable", requireAdminToken, (c) => setUserDisabled(c, 0));

// GET /api/internal/admin/users — list users (NO password hashes).
app.get("/api/internal/admin/users", requireAdminToken, async (c) => {
  const { results } = await c.env.DB
    .prepare("SELECT username, role, disabled, created_at FROM users ORDER BY username")
    .all<{ username: string; role: string; disabled: number; created_at: number }>();
  return c.json({ users: results });
});

// POST /api/internal/admin/purge-job — operator hard-delete of a job + ALL its D1 rows
// (submissions, the filed_pdfs PDF cache, and pdf_requests). This is the explicit operator
// path the daemon /api/internal/sync deliberately CANNOT take: sync refuses an empty job set
// (so a transient empty ITS_Active_Jobs read can never wipe the dropdown), which means a
// fully-removed/test job otherwise lingers active=1 forever. D1 is a transport cache — Box +
// the week sheet remain the system of record; this only clears the local copy. One atomic
// batch (cascade children before parents) + an audit_log entry. Idempotent: an unknown job_id
// returns ok:true, found:false with zero counts.
app.post("/api/internal/admin/purge-job", requireAdminToken, async (c) => {
  let body: { job_id?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const job_id = typeof body.job_id === "string" ? body.job_id.trim() : "";
  if (!job_id || job_id.length > 64) return c.json({ error: "invalid_job_id" }, 400);
  // Full literal SQL (NO template interpolation) so the bound `?` is the only dynamic input:
  // job_id is always parameterized, never concatenated — and there is no string-built query for
  // CodeQL's injection sink to flag. The cascade deletes children (filed_pdfs, pdf_requests via
  // the submissions subquery) BEFORE the parents (submissions, then jobs).
  const results = await c.env.DB.batch([
    c.env.DB
      .prepare("DELETE FROM filed_pdfs WHERE submission_uuid IN (SELECT submission_uuid FROM submissions WHERE job_id = ?)")
      .bind(job_id),
    c.env.DB
      .prepare("DELETE FROM pdf_requests WHERE submission_uuid IN (SELECT submission_uuid FROM submissions WHERE job_id = ?)")
      .bind(job_id),
    c.env.DB.prepare("DELETE FROM submissions WHERE job_id = ?").bind(job_id),
    c.env.DB.prepare("DELETE FROM jobs WHERE job_id = ?").bind(job_id),
    c.env.DB
      .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) VALUES (?,?,?,?)")
      .bind("operator-cli", "purge-job", job_id, "hard-delete job + D1 cache"),
  ]);
  const pdfChunks = results[0]?.meta?.changes ?? 0;
  const pdfRequests = results[1]?.meta?.changes ?? 0;
  const submissions = results[2]?.meta?.changes ?? 0;
  const job = results[3]?.meta?.changes ?? 0;
  return c.json({ ok: true, found: job > 0, job_id, job_deleted: job, submissions, pdfChunks, pdfRequests });
});

// ─────────────────────────────────────────────────────────────────────────────
// In-app admin surface — /api/admin/* (requireSession + requireRole("admin")).
//
// This is the SESSION+ROLE-gated counterpart to the bearer /api/internal/admin/*
// operator-CLI routes above. A logged-in admin (the CEO / head PM) manages accounts
// from the browser; every route is re-gated server-side (the SPA hiding tabs is NOT
// the boundary — Invariant 2). Each mutation + its audit_log row run in ONE atomic
// D1 batch, so an account change can never land without its security-log entry.
// Nothing here transmits anything externally (Invariant 1) — D1 writes only.
// ─────────────────────────────────────────────────────────────────────────────

interface TargetRow { username: string; role: string; disabled: number }

/**
 * SQL guard fragment for the last-admin protection (operator's call, Q2 = ON).
 *
 * Appended to the demote/delete WHERE so the "is this the only ENABLED admin?" test
 * is evaluated ATOMICALLY inside the mutation: the count subquery sees the row's
 * pre-mutation state at write time. This is deliberately NOT a separate pre-SELECT —
 * a check-then-act pair is a TOCTOU race (two concurrent demotes/deletes could both
 * read count=2, both pass, and strand zero admins). With the guard inline, each
 * UPDATE/DELETE re-evaluates the count and at most one matches a row; the loser
 * matches 0 rows (meta.changes==0 ⇒ the caller returns 409 last_admin).
 *
 * Only an ENABLED admin target is guarded — a disabled admin isn't a functioning
 * admin to protect (matches the count's `disabled=0`). The bearer break-glass routes
 * are deliberately NOT guarded (they are the recovery path out of a zero-admin state).
 */
function lastAdminGuardClause(target: TargetRow): string {
  return target.role === "admin" && !target.disabled
    ? " AND (SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0) > 1"
    : "";
}

const adminGate = [requireSession, requireRole("admin")] as const;

// GET /api/admin/users — list all accounts (username, role, disabled, created_at). No hashes.
app.get("/api/admin/users", ...adminGate, async (c) => {
  const { results } = await c.env.DB
    .prepare("SELECT username, role, disabled, created_at FROM users ORDER BY username")
    .all<{ username: string; role: string; disabled: number; created_at: number }>();
  return c.json({ users: results });
});

// POST /api/admin/users — create an account (role selectable; 409 if it exists).
app.post("/api/admin/users", ...adminGate, async (c) => {
  let body: { username?: unknown; password?: unknown; role?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const password = typeof body.password === "string" ? body.password : "";
  const role = parseRole(body.role);
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (password.length < 8 || password.length > 256) return c.json({ error: "invalid_password" }, 400);
  if (role === null) return c.json({ error: "invalid_role" }, 400);
  const exists = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(username).first();
  if (exists) return c.json({ error: "exists" }, 409);
  const password_hash = await hashPassword(password); // plaintext never stored/logged
  try {
    await c.env.DB.batch([
      c.env.DB.prepare("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)")
        .bind(username, password_hash, role),
      auditStmt(c, c.get("session").username, "user_create", username, { role }),
    ]);
  } catch (e) {
    // Lost the check-then-act race (a concurrent create of the same username) → the
    // UNIQUE constraint fires here. Map to 409, not a bubbled 500 (audit #5). The
    // `if (exists)` pre-check above is the cheap path; this is the race backstop.
    if (isUniqueViolation(e)) return c.json({ error: "exists" }, 409);
    throw e;
  }
  return c.json({ ok: true, username, role }, 201);
});

// POST /api/admin/users/credentials — edit a login: new_username and/or new_password
// (own or any other account). Editing YOUR OWN login re-issues the session (the
// cookie is cleared → the SPA forces a re-login with the new credentials).
app.post("/api/admin/users/credentials", ...adminGate, async (c) => {
  let body: { username?: unknown; new_username?: unknown; new_password?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  if (!username) return c.json({ error: "invalid_username" }, 400);
  const hasNewUsername = body.new_username !== undefined;
  const hasNewPassword = body.new_password !== undefined;
  if (!hasNewUsername && !hasNewPassword) return c.json({ error: "no_changes" }, 400);

  const target = await c.env.DB
    .prepare("SELECT username, role, disabled FROM users WHERE username=?")
    .bind(username)
    .first<TargetRow>();
  if (!target) return c.json({ error: "not_found" }, 404);

  const sets: string[] = [];
  const binds: unknown[] = [];
  let renamedTo: string | null = null;

  if (hasNewUsername) {
    const nu = normalizeUsername(typeof body.new_username === "string" ? body.new_username : "");
    if (!nu) return c.json({ error: "invalid_new_username" }, 400);
    if (nu !== target.username) {
      const taken = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(nu).first();
      if (taken) return c.json({ error: "username_taken" }, 409);
      sets.push("username=?");
      binds.push(nu);
      renamedTo = nu;
    }
  }
  if (hasNewPassword) {
    const np = typeof body.new_password === "string" ? body.new_password : "";
    if (np.length < 8 || np.length > 256) return c.json({ error: "invalid_password" }, 400);
    sets.push("password_hash=?");
    binds.push(await hashPassword(np)); // plaintext never stored/logged
    // Slice 8a (audit #7): a password change BUMPS session_epoch, revoking every
    // outstanding cookie for the target on its next request. No bind param (literal
    // SET), so the binds-order ↔ sets-order alignment for the placeholders is intact.
    sets.push("session_epoch = session_epoch + 1");
  }
  if (sets.length === 0) return c.json({ error: "no_changes" }, 400); // new_username == current, no password

  try {
    await c.env.DB.batch([
      c.env.DB.prepare(`UPDATE users SET ${sets.join(", ")} WHERE username=?`).bind(...binds, target.username),
      auditStmt(c, c.get("session").username, "user_edit", target.username, {
        username_changed: renamedTo !== null,
        renamed_to: renamedTo,
        password_changed: hasNewPassword,
      }),
    ]);
  } catch (e) {
    // A concurrent rename into the same target username loses the UNIQUE race → 409
    // (audit #5; the `taken` pre-check above is the cheap path, this is the backstop).
    if (isUniqueViolation(e)) return c.json({ error: "username_taken" }, 409);
    throw e;
  }

  // Self-edit → re-auth. A username change already invalidates the cookie (the
  // per-request lookup is by the OLD username); a password change does not, so we
  // clear it explicitly. Either way the SPA lands on the login screen.
  if (target.username === c.get("session").username) {
    deleteCookie(c, COOKIE, { path: "/" });
    return c.json({ ok: true, reauth: true });
  }
  return c.json({ ok: true, username: renamedTo ?? target.username });
});

// POST /api/admin/users/role — change an account's role (submitter ⇄ admin).
// Last-admin guard: cannot demote the only enabled admin (Q2). Self-demote re-auths.
app.post("/api/admin/users/role", ...adminGate, async (c) => {
  let body: { username?: unknown; role?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const role = parseRole(body.role, "submitter");
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (body.role === undefined || role === null) return c.json({ error: "invalid_role" }, 400);

  const target = await c.env.DB
    .prepare("SELECT username, role, disabled FROM users WHERE username=?")
    .bind(username)
    .first<TargetRow>();
  if (!target) return c.json({ error: "not_found" }, 404);
  if (target.role === role) return c.json({ ok: true, username, role, changed: false });

  // Atomic demote: the last-admin guard lives in the UPDATE's WHERE (see
  // lastAdminGuardClause) so it can't race a concurrent demote. The audit row is
  // inserted ONLY when the UPDATE matched — `changes()` reflects the prior statement
  // within the batch's single transaction, so mutation+audit stay atomic and no audit
  // is written for a guard-blocked attempt.
  const res = await c.env.DB.batch([
    c.env.DB.prepare(`UPDATE users SET role=? WHERE username=?${lastAdminGuardClause(target)}`)
      .bind(role, target.username),
    c.env.DB.prepare(
      "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?,?,?,? WHERE changes()=1",
    ).bind(c.get("session").username, "role_change", target.username, JSON.stringify({ from: target.role, to: role })),
  ]);
  // changes==0 is overloaded: the atomic last-admin guard blocked it, OR a concurrent
  // delete removed the row after our load. Re-check existence so the code is honest
  // (audit #6): 404 if gone, 409 last_admin if genuinely still the last enabled admin.
  if ((res[0]?.meta?.changes ?? 0) === 0) {
    const still = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(target.username).first();
    return still ? c.json({ error: "last_admin" }, 409) : c.json({ error: "not_found" }, 404);
  }

  if (target.username === c.get("session").username) {
    deleteCookie(c, COOKIE, { path: "/" });
    return c.json({ ok: true, reauth: true });
  }
  return c.json({ ok: true, username, role, changed: true });
});

// POST /api/admin/users/delete — delete an account. Last-admin guard applies.
// Self-delete is permitted (unless it strands no admin) and re-auths the caller.
app.post("/api/admin/users/delete", ...adminGate, async (c) => {
  let body: { username?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  // JSON `null`/arrays/scalars PARSE fine but aren't objects; dereferencing body.x on
  // them threw → bare 500 (audit #1). Require a plain object (the `as unknown` cast
  // dodges the no-overlap check on the typed body var).
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  if (!username) return c.json({ error: "invalid_username" }, 400);

  const target = await c.env.DB
    .prepare("SELECT username, role, disabled FROM users WHERE username=?")
    .bind(username)
    .first<TargetRow>();
  if (!target) return c.json({ error: "not_found" }, 404);

  // Atomic delete: same in-WHERE last-admin guard + changes()-conditional audit as
  // the role route — the count subquery sees the pre-delete state, so concurrent
  // deletes/demotes can't both strand the last enabled admin.
  const res = await c.env.DB.batch([
    c.env.DB.prepare(`DELETE FROM users WHERE username=?${lastAdminGuardClause(target)}`)
      .bind(target.username),
    c.env.DB.prepare(
      "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?,?,?,? WHERE changes()=1",
    ).bind(c.get("session").username, "user_delete", target.username, JSON.stringify({ role: target.role })),
  ]);
  // Same overloaded changes==0 as the role route (audit #6): guard-blocked (still the
  // last enabled admin) vs already-deleted by a concurrent request. 404 if gone.
  if ((res[0]?.meta?.changes ?? 0) === 0) {
    const still = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(target.username).first();
    return still ? c.json({ error: "last_admin" }, 409) : c.json({ error: "not_found" }, 404);
  }

  if (target.username === c.get("session").username) {
    deleteCookie(c, COOKIE, { path: "/" });
    return c.json({ ok: true, reauth: true });
  }
  return c.json({ ok: true, username: target.username });
});

// ── Form editor publish pipeline (Phase 2, slice 3a) ───────────────────────────
// SEND-FREE: POST /api/admin/publish VALIDATES the composed definition server-side
// (publishValidation, design C3) and, only if valid, ENQUEUES a publish_requests row.
// It NEVER commits or deploys — the Mac daemon (slice 3b) is the sole privileged
// actuator (mirrors the External Send Gate: the cloud can only queue). create / edit /
// add_version carry a composed definition; delete / rollback flip the manifest at
// actuation and carry only the target.
const PUBLISH_OPS = new Set(["create", "edit", "add_version", "delete", "rollback", "recategorize"]);
// A publish still in flight, for per-parent serialization (C8) — archived | failed are
// terminal. 'live' still blocks (the Box-archive stage is pending). A crashed publish no
// longer wedges a parent forever: the Worker's LEASE_TTL_S makes a stale lease re-claimable
// (pending/claim), and the Mac daemon's stale-row sweep (publish_daemon._sweep_stale_rows)
// stamps any non-terminal row stalled past STALE_RECLAIM_S to failed('stale_reclaimed') — both
// added in PR-2 to MAKE THIS TRUE (it previously described a daemon watchdog that did not exist).
const NON_TERMINAL_STATUSES = "('queued','validated','tested','merged','live')";

app.post("/api/admin/publish", ...adminGate, async (c) => {
  let body: {
    op?: unknown; identity?: unknown; parent_form_code?: unknown;
    target_form_code?: unknown; definition?: unknown; category?: unknown;
  };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const op = typeof body.op === "string" ? body.op : "";
  if (!PUBLISH_OPS.has(op)) return c.json({ error: "invalid_op" }, 400);
  const identity = typeof body.identity === "string" ? body.identity : "";
  const parent = typeof body.parent_form_code === "string" ? body.parent_form_code : "";
  if (!/^[a-z0-9-]+$/.test(identity)) return c.json({ error: "invalid_identity" }, 400);
  if (!/^[a-z0-9-]+$/.test(parent)) return c.json({ error: "invalid_parent_form_code" }, 400);

  // Workflow category — REQUIRED for recategorize; OPTIONAL for create (absent → apply_publish
  // defaults the new parent to safety; the SPA selector always sends it). Validated against the
  // workflows.json registry (mirrors apply_publish's re-check). The other ops ignore it → null.
  let category: string | null = null;
  if (op === "recategorize" || (op === "create" && body.category !== undefined)) {
    const cat = validateCategory(body.category);
    if (!cat.ok) return c.json({ error: "invalid_category", reason: cat.reason }, 400);
    category = body.category as string;
  }

  // create/edit/add_version carry a composed definition → server-side validate it (C3).
  // delete/rollback carry only the target (the daemon flips the manifest at actuation).
  let definitionJson: string | null = null;
  let targetFormCode: string | null =
    typeof body.target_form_code === "string" ? body.target_form_code : null;
  if (op === "create" || op === "edit" || op === "add_version") {
    const result = validateDefinition(body.definition, { identity, parentFormCode: parent });
    if (!result.ok) return c.json({ error: "invalid_definition", reason: result.reason }, 400);
    // Catalog-level parent-grouping guard: create/add_version add a NEW form to the parent,
    // which must not mix a standalone form with variants (edit bumps an existing identity →
    // grouping unchanged). Mirrors apply_publish; the daemon re-checks vs live git HEAD.
    if (op !== "edit") {
      const grouping = validateParentGrouping(
        catalog, parent, (body.definition as { variant_label?: string | null }).variant_label,
      );
      if (!grouping.ok) return c.json({ error: "invalid_definition", reason: grouping.reason }, 400);
    }
    targetFormCode = (body.definition as { form_code: string }).form_code;
    definitionJson = JSON.stringify(body.definition);
  } else if (targetFormCode !== null && !/^[a-z0-9-]+-v[0-9]+$/.test(targetFormCode)) {
    return c.json({ error: "invalid_target_form_code" }, 400);
  }

  // Per-parent serialization (C8): reject a 2nd publish while one is in flight.
  const inflight = await c.env.DB
    .prepare(`SELECT id FROM publish_requests WHERE parent_form_code=? AND status IN ${NON_TERMINAL_STATUSES} LIMIT 1`)
    .bind(parent)
    .first();
  if (inflight) return c.json({ error: "publish_in_progress" }, 409);

  const res = await c.env.DB.batch([
    c.env.DB.prepare(
      "INSERT INTO publish_requests (requested_by, op, parent_form_code, identity, target_form_code, definition_json, category) VALUES (?,?,?,?,?,?,?)",
    ).bind(c.get("session").username, op, parent, identity, targetFormCode, definitionJson, category),
    auditStmt(c, c.get("session").username, "form_publish", identity, {
      op, target_form_code: targetFormCode, ...(category !== null ? { category } : {}),
    }),
  ]);
  return c.json({ ok: true, id: res[0]?.meta?.last_row_id ?? null, status: "queued" }, 201);
});

// GET /api/admin/publish-status — the status-monitor read view (most-recent first).
// Send-free read of the publish_requests state machine for the admin dashboard stepper.
app.get("/api/admin/publish-status", ...adminGate, async (c) => {
  const { results } = await c.env.DB
    .prepare(
      "SELECT id, created_at, updated_at, requested_by, op, parent_form_code, identity, " +
        "target_form_code, status, failed_stage, failure_reason FROM publish_requests ORDER BY id DESC LIMIT 50",
    )
    .all();
  return c.json({ requests: results });
});

// POST /api/admin/publish-dismiss — clear TERMINAL (archived | failed) requests from the
// monitor. Send-free; only finished rows are removed — an in-flight publish is never
// touched (the form files + audit_log remain the record). Returns the count cleared.
app.post("/api/admin/publish-dismiss", ...adminGate, async (c) => {
  const res = await c.env.DB
    .prepare("DELETE FROM publish_requests WHERE status IN ('archived', 'failed')")
    .run();
  return c.json({ ok: true, cleared: res.meta?.changes ?? 0 });
});

// GET /api/admin/publish-request?id=N — fetch ONE request's full record INCLUDING the
// composed definition_json, so a FAILED publish can be re-opened in the editor and fixed
// instead of losing the work. Send-free read.
app.get("/api/admin/publish-request", ...adminGate, async (c) => {
  const id = Number(c.req.query("id"));
  if (!Number.isInteger(id) || id <= 0) return c.json({ error: "invalid_id" }, 400);
  const row = await c.env.DB
    .prepare(
      "SELECT id, op, parent_form_code, identity, target_form_code, status, definition_json, category " +
        "FROM publish_requests WHERE id = ?",
    )
    .bind(id)
    .first();
  if (!row) return c.json({ error: "not_found" }, 404);
  return c.json({ request: row });
});

// ── Publish daemon interface (Phase 2, slice 3b) ───────────────────────────────
// The Mac publish daemon's bearer-gated queue interface: pull queued requests, ATOMICALLY
// LEASE one (so two daemon runs can't actuate the same row), and STAMP the state machine
// as it commits / deploys. The daemon is the sole privileged actuator; the Worker only
// exposes the queue (send-free). Same PORTAL_INTERNAL_API_TOKEN as the portal_poll daemon.
const PUBLISH_STATUSES = new Set(["queued", "validated", "tested", "merged", "live", "archived", "failed"]);

// Lease TTL (PR-2): a claimed-but-stalled row (the daemon died after claim, before any stamp)
// becomes re-claimable once its lease is older than this. Must exceed the daemon's CI wait +
// deploy slack so a legitimately in-progress publish is never stolen. 30 min.
const LEASE_TTL_S = 30 * 60;

// Legal predecessors per stamp target (PR-2): the stamp endpoint only advances a row whose
// CURRENT status is a legal predecessor of the requested status. Blocks a forged / out-of-order
// stamp on the shared internal token (an archived→queued revert, a queued→archived skip) and a
// re-stamp of a terminal row. 'queued' is absent (the initial state is never a stamp target);
// 'live' accepts 'tested' (the daemon folds the merge into its tested stage) OR 'merged';
// 'failed' accepts any non-terminal state.
const LEGAL_PREDECESSORS: Record<string, string[]> = {
  validated: ["queued"],
  tested: ["validated"],
  merged: ["tested"],
  live: ["tested", "merged"],
  archived: ["live"],
  failed: ["queued", "validated", "tested", "merged", "live"],
};

// GET /api/internal/publish/pending — claimable rows (queued + unleased OR stale-leased), oldest-first.
app.get("/api/internal/publish/pending", requireInternalToken, async (c) => {
  const limit = Math.min(Number(c.req.query("limit")) || 20, 100);
  const { results } = await c.env.DB
    .prepare(
      "SELECT id, created_at, requested_by, op, parent_form_code, identity, target_form_code, definition_json " +
        "FROM publish_requests WHERE status='queued' AND (lease_owner IS NULL OR lease_at < unixepoch() - ?) " +
        "ORDER BY id ASC LIMIT ?",
    )
    .bind(LEASE_TTL_S, limit)
    .all();
  return c.json({ pending: results });
});

// POST /api/internal/publish/claim — ATOMICALLY lease a queued row for one daemon run.
// { id, lease_owner } leases ONLY if still queued AND (unleased OR its lease is stale past
// LEASE_TTL_S — takeover of a dead daemon's lease). Two LIVE runs still can't both actuate.
// Returns the full row (incl. definition_json) when claimed.
app.post("/api/internal/publish/claim", requireInternalToken, async (c) => {
  let body: Record<string, unknown>;
  try { body = await c.req.json(); } catch { return c.json({ error: "bad_request" }, 400); }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const id = typeof body.id === "number" && Number.isInteger(body.id) ? body.id : 0;
  const lease_owner = typeof body.lease_owner === "string" ? body.lease_owner.slice(0, 128) : "";
  if (!id || !lease_owner) return c.json({ error: "invalid" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE publish_requests SET lease_owner=?, lease_at=unixepoch() WHERE id=? AND status='queued' AND (lease_owner IS NULL OR lease_at < unixepoch() - ?)")
    .bind(lease_owner, id, LEASE_TTL_S)
    .run();
  if ((res.meta?.changes ?? 0) === 0) return c.json({ ok: true, claimed: false });
  const request = await c.env.DB
    .prepare("SELECT id, op, parent_form_code, identity, target_form_code, definition_json, category, status FROM publish_requests WHERE id=?")
    .bind(id)
    .first();
  return c.json({ ok: true, claimed: true, request });
});

// POST /api/internal/publish/stamp — advance the state machine. { id, status,
// failed_stage?, failure_reason? }. The daemon stamps validated→tested→merged→live→
// archived, or failed (with stage + reason) on any error. failed_stage/reason are kept
// ONLY for a failed stamp (cleared otherwise).
app.post("/api/internal/publish/stamp", requireInternalToken, async (c) => {
  let body: Record<string, unknown>;
  try { body = await c.req.json(); } catch { return c.json({ error: "bad_request" }, 400); }
  if (typeof body !== "object" || (body as unknown) === null || Array.isArray(body)) {
    return c.json({ error: "bad_request" }, 400);
  }
  const id = typeof body.id === "number" && Number.isInteger(body.id) ? body.id : 0;
  const status = typeof body.status === "string" ? body.status : "";
  if (!id || !PUBLISH_STATUSES.has(status)) return c.json({ error: "invalid" }, 400);
  const failed = status === "failed";
  const failed_stage = failed && typeof body.failed_stage === "string" ? body.failed_stage.slice(0, 64) : null;
  const failure_reason = failed && typeof body.failure_reason === "string" ? body.failure_reason.slice(0, 2000) : null;
  // State-machine guard (PR-2): only advance a row whose CURRENT status is a legal predecessor
  // of the requested status — blocks a forged / out-of-order stamp on the shared internal token.
  const preds = LEGAL_PREDECESSORS[status];
  if (!preds) return c.json({ error: "invalid" }, 400); // 'queued' is never a stamp target
  const placeholders = preds.map(() => "?").join(",");
  const res = await c.env.DB
    .prepare(
      "UPDATE publish_requests SET status=?, failed_stage=?, failure_reason=?, updated_at=unixepoch() " +
        `WHERE id=? AND status IN (${placeholders})`,
    )
    .bind(status, failed_stage, failure_reason, id, ...preds)
    .run();
  if ((res.meta?.changes ?? 0) === 0) {
    // changes==0 is overloaded: the row is gone, OR its current status isn't a legal predecessor
    // of `status` (a forged / out-of-order stamp). Re-read for an honest reason; the row was NOT
    // advanced either way. 200 + found:false keeps the daemon's stamp contract (it never makes an
    // illegal transition, so it never sees this; a forger is simply rejected).
    const row = await c.env.DB.prepare("SELECT status FROM publish_requests WHERE id=?").bind(id).first<{ status: string }>();
    if (!row) return c.json({ ok: true, found: false });
    return c.json({ ok: true, found: false, reason: `illegal transition ${row.status} -> ${status}` });
  }
  return c.json({ ok: true, found: true });
});

// GET /api/internal/publish/stuck?older_than=<sec> — non-terminal rows whose updated_at is older
// than the cutoff (a publish that crashed mid-actuation, or a stalled stage). The Mac daemon's
// stale-row sweep (publish_daemon._sweep_stale_rows) reclaims these by stamping
// failed('stale_reclaimed') so they stop wedging the parent's C8 in-flight check. Bearer-gated.
app.get("/api/internal/publish/stuck", requireInternalToken, async (c) => {
  const olderThan = Math.min(Math.max(Number(c.req.query("older_than")) || 0, 0), 86400);
  const { results } = await c.env.DB
    .prepare(
      "SELECT id, status, lease_owner, lease_at, updated_at, op, parent_form_code, identity " +
        `FROM publish_requests WHERE status IN ${NON_TERMINAL_STATUSES} AND updated_at < unixepoch() - ? ` +
        "ORDER BY id ASC LIMIT 50",
    )
    .bind(olderThan)
    .all();
  return c.json({ stuck: results });
});

// Unmatched /api/* → JSON 404 (never the SPA shell).
app.all("/api/*", (c) => c.json({ error: "not_found" }, 404));

// Everything else → the built SPA via the static-assets binding. With
// run_worker_first:["/api/*"] most non-API requests are served as assets before
// the Worker runs; this fallback covers the SPA shell where the Worker does run.
app.get("*", (c) => c.env.ASSETS.fetch(c.req.raw));

// ── scheduled (A3): the daily cron (wrangler.jsonc triggers.crons) prunes the D1 store.
// SEND-FREE like every other path (Invariant 1) — it only deletes aged local rows. A prune
// failure is logged via observability and does not affect the fetch path.
const scheduled: ExportedHandlerScheduledHandler<Env> = async (_controller, env) => {
  const pruned = await pruneOldData(env.DB, Math.floor(Date.now() / 1000));
  console.log(
    `prune: stripped ${pruned.stripped} payload(s), removed ${pruned.submissions} inactive-job + ` +
      `${pruned.rejected} rejected submission(s) + ${pruned.audit} audit row(s) + ` +
      `${pruned.pdfRequests} pdf request(s) + ${pruned.pdfChunks} pdf chunk(s) + ${pruned.jobs} empty job(s); ` +
      `D1 size ${pruned.dbSizeBytes} bytes`,
  );
};

export default { fetch: app.fetch, scheduled } satisfies ExportedHandler<Env>;
