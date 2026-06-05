import { Hono } from "hono";
import { createMiddleware } from "hono/factory";
import { setSignedCookie, getSignedCookie, deleteCookie } from "hono/cookie";
import type { Env, SessionClaims, Vars } from "./types";
import { validateUser, newSessionClaims } from "./auth";

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
//     serving). The Phase 5 email shim is a SEPARATE, capability-gated component;
//     keep this Worker send-free.
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
// Consumers: the SPA (src/) via same-origin fetch; the Miniflare local-dev runtime
//   (vite dev / wrangler dev). Later phases add /api/submit, /api/sync, etc.
// ─────────────────────────────────────────────────────────────────────────────

const COOKIE = "its_portal_session";
const MAX_AGE_S = 60 * 60 * 24 * 90; // 90-day session (safety-portal/mission.md §3 — long-lived, no idle timeout)

const app = new Hono<{ Bindings: Env; Variables: Vars }>();

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

// Unmatched /api/* → JSON 404 (never the SPA shell).
app.all("/api/*", (c) => c.json({ error: "not_found" }, 404));

// Everything else → the built SPA via the static-assets binding. With
// run_worker_first:["/api/*"] most non-API requests are served as assets before
// the Worker runs; this fallback covers the SPA shell where the Worker does run.
app.get("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
