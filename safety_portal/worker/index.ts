import { Hono } from "hono";
import { createMiddleware } from "hono/factory";
import type { Context } from "hono";
import { setSignedCookie, getSignedCookie, deleteCookie } from "hono/cookie";
import type { Env, Role, SessionClaims, Vars } from "./types";
import { validateUser, newSessionClaims, hashPassword, normalizeUsername, coerceRole } from "./auth";

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
const MAX_AGE_S = 60 * 60 * 24 * 90; // 90-day session (safety-portal/mission.md §3 — long-lived, no idle timeout)

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

/** True if a D1 error is a UNIQUE-constraint violation. Lets the create/rename routes
 *  map a lost check-then-act race (the second writer hits UNIQUE) to a clean 409
 *  instead of letting it bubble to a 500 (audit #5). */
function isUniqueViolation(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e);
  return /UNIQUE constraint failed/i.test(msg);
}

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
    maxAge: MAX_AGE_S,
  });
  // `role` lets the SPA decide whether to render the admin tabs. It is display-only
  // hinting — every admin action is independently re-gated server-side (requireRole).
  return c.json({ user: { username: user.username, role: user.role } });
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

  c.set("session", claims);
  c.set("role", role);
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

/** GET /api/session — who am I (used by the SPA on load to restore session). Returns
 *  the live role (from requireSession's per-request D1 read), so a demotion drops the
 *  admin tabs on the next session refresh. */
app.get("/api/session", requireSession, (c) => {
  const s = c.get("session");
  return c.json({ user: { username: s.username, role: c.get("role") } });
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
  const payload = JSON.stringify(values);
  if (payload.length > 1_000_000) return c.json({ error: "too_large" }, 413);

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
  // intake files it. INSERT OR REPLACE resets box_verified=0 — an amended submission
  // re-enters the queue for re-filing. CRITICAL: the canonicalPayload (HMAC input) is
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
  const insertStmt = c.env.DB
    .prepare(
      "INSERT OR REPLACE INTO submissions " +
        "(submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid, hmac, box_verified, " +
        "actor_username, submitted_as) " +
        "VALUES (?,?,?,?,?,?,?,0,?,?)",
    )
    .bind(submission_uuid, job_id, form_code, work_date, payload, amends_uuid, hmac, actor, attributed);
  if (isSubmitAs) {
    await c.env.DB.batch([
      insertStmt,
      auditStmt(c, actor, "submit_as", attributed, { submission_uuid, job_id }),
    ]);
  } else {
    await insertStmt.run();
  }
  return c.json({ ok: true, status: "submitted", submission_uuid });
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
  if (!submission_uuid || submission_uuid.length > 64) return c.json({ error: "invalid" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE submissions SET box_verified=1, filed_at=unixepoch(), box_link=? WHERE submission_uuid=?")
    .bind(box_link, submission_uuid)
    .run();
  return c.json({ ok: true, found: (res.meta?.changes ?? 0) > 0 });
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

  // One atomic batch: upsert every supplied row, then deactivate any active D1
  // job_id NOT in the payload (the NOT-IN list is bound, never interpolated).
  const ids = jobs.map((j) => j.job_id);
  const statements = [
    ...jobs.map((j) =>
      c.env.DB.prepare(
        "INSERT INTO jobs (job_id, project_name, active) VALUES (?,?,?) " +
          "ON CONFLICT(job_id) DO UPDATE SET project_name=excluded.project_name, active=excluded.active",
      ).bind(j.job_id, j.project_name, j.active),
    ),
    c.env.DB.prepare(
      `UPDATE jobs SET active=0 WHERE active=1 AND job_id NOT IN (${ids.map(() => "?").join(",")})`,
    ).bind(...ids),
  ];
  const results = await c.env.DB.batch(statements);
  const deactivated = results[results.length - 1]?.meta?.changes ?? 0;
  return c.json({ ok: true, upserted: jobs.length, deactivated });
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
function parseRole(value: unknown, dflt: Role = "submitter"): Role | null {
  if (value === undefined) return dflt;
  if (value === "admin" || value === "submitter") return value;
  return null;
}

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

/** Build (not execute) the audit_log INSERT — included in the mutation's batch so
 *  the record is atomic with the change it describes. `detail` is JSON-encoded. */
function auditStmt(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  actor: string,
  action: string,
  target: string | null,
  detail: Record<string, unknown> | null,
) {
  return c.env.DB
    .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) VALUES (?,?,?,?)")
    .bind(actor, action, target, detail === null ? null : JSON.stringify(detail));
}

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

// Unmatched /api/* → JSON 404 (never the SPA shell).
app.all("/api/*", (c) => c.json({ error: "not_found" }, 404));

// Everything else → the built SPA via the static-assets binding. With
// run_worker_first:["/api/*"] most non-API requests are served as assets before
// the Worker runs; this fallback covers the SPA shell where the Worker does run.
app.get("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
