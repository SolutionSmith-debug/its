import { Hono } from "hono";
import { createMiddleware } from "hono/factory";
import type { Context } from "hono";
import { setSignedCookie, getSignedCookie, deleteCookie } from "hono/cookie";
import type { Env, SessionClaims, Vars } from "./types";
import { validateUser, newSessionClaims, hashPassword, normalizeUsername } from "./auth";

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
  return c.json({ user: { username: user.username } });
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
  try {
    const row = await c.env.DB
      .prepare("SELECT disabled FROM users WHERE username = ?")
      .bind(claims.username)
      .first<{ disabled: number }>();
    if (!row || row.disabled) {
      return c.json({ error: "revoked" }, 401);
    }
  } catch {
    return c.json({ error: "unauthenticated" }, 401);
  }

  c.set("session", claims);
  await next();
});

/** GET /api/session — who am I (used by the SPA on load to restore session). */
app.get("/api/session", requireSession, (c) => {
  const s = c.get("session");
  return c.json({ user: { username: s.username } });
});

/**
 * POST /api/logout — clear the session cookie (client-side invalidation only).
 *
 * Rationale: Phase 2 has no server-side session revocation (no D1 session table /
 * blocklist), and requireSession does not re-check that the user still exists — so
 * a stolen or deprovisioned-user cookie stays valid until iat+MAX_AGE_S. Accepted
 * gap: no real PMs exist until they're provisioned via the Phase 7 admin route,
 * which also introduces the session table for explicit invalidation +
 * deprovisioning. (safety-portal/brief.md §14 Phase 7.)
 */
app.post("/api/logout", (c) => {
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
    typeof values !== "object" || values === null
  ) {
    return c.json({ error: "invalid_submission" }, 400);
  }
  const job = await c.env.DB.prepare("SELECT 1 FROM jobs WHERE job_id=? AND active=1").bind(job_id).first();
  if (!job) return c.json({ error: "unknown_job" }, 422);
  const payload = JSON.stringify(values);
  if (payload.length > 1_000_000) return c.json({ error: "too_large" }, 413);
  // Fail closed on a misconfigured Worker: never sign with an undefined secret
  // (that would produce signatures the Mac side could never verify → silent loss).
  if (!c.env.HMAC_PAYLOAD_SECRET) return c.json({ error: "server_misconfigured" }, 503);
  // Sign the submission so the Mac-side portal_poll daemon can verify it before
  // intake files it. INSERT OR REPLACE resets box_verified=0 — an amended submission
  // re-enters the queue for re-filing.
  const hmac = await hmacHex(
    c.env.HMAC_PAYLOAD_SECRET,
    canonicalPayload({ submission_uuid, job_id, form_code, work_date, payload_json: payload }),
  );
  await c.env.DB
    .prepare(
      "INSERT OR REPLACE INTO submissions " +
        "(submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid, hmac, box_verified) " +
        "VALUES (?,?,?,?,?,?,?,0)",
    )
    .bind(submission_uuid, job_id, form_code, work_date, payload, amends_uuid, hmac)
    .run();
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
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  if (!username) return c.json({ error: "invalid_username" }, 400);
  const res = await c.env.DB
    .prepare("UPDATE users SET disabled=? WHERE username=?")
    .bind(value, username)
    .run();
  if ((res.meta?.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, username, disabled: value });
}

// POST /api/internal/admin/users — provision a new user (409 if it exists).
app.post("/api/internal/admin/users", requireAdminToken, async (c) => {
  let body: { username?: unknown; password?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const password = typeof body.password === "string" ? body.password : "";
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (password.length < 8 || password.length > 256) return c.json({ error: "invalid_password" }, 400);
  const exists = await c.env.DB.prepare("SELECT 1 FROM users WHERE username=?").bind(username).first();
  if (exists) return c.json({ error: "exists" }, 409);
  const password_hash = await hashPassword(password); // plaintext never stored/logged
  await c.env.DB
    .prepare("INSERT INTO users (username, password_hash) VALUES (?,?)")
    .bind(username, password_hash)
    .run();
  return c.json({ ok: true, username }, 201);
});

// POST /api/internal/admin/users/reset — re-hash an existing user's password (404 if absent).
app.post("/api/internal/admin/users/reset", requireAdminToken, async (c) => {
  let body: { username?: unknown; password?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  const username = normalizeUsername(typeof body.username === "string" ? body.username : "");
  const password = typeof body.password === "string" ? body.password : "";
  if (!username) return c.json({ error: "invalid_username" }, 400);
  if (password.length < 8 || password.length > 256) return c.json({ error: "invalid_password" }, 400);
  const password_hash = await hashPassword(password); // plaintext never stored/logged
  const res = await c.env.DB
    .prepare("UPDATE users SET password_hash=? WHERE username=?")
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
    .prepare("SELECT username, disabled, created_at FROM users ORDER BY username")
    .all<{ username: string; disabled: number; created_at: number }>();
  return c.json({ users: results });
});

// Unmatched /api/* → JSON 404 (never the SPA shell).
app.all("/api/*", (c) => c.json({ error: "not_found" }, 404));

// Everything else → the built SPA via the static-assets binding. With
// run_worker_first:["/api/*"] most non-API requests are served as assets before
// the Worker runs; this fallback covers the SPA shell where the Worker does run.
app.get("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
