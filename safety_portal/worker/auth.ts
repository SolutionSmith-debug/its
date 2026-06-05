/**
 * Safety Portal — credential validation + session-claims construction (Worker).
 *
 * Purpose: D1-backed username/password verification for /api/login, plus the
 *   session-claims factory used to mint the signed cookie.
 * Invariants: D1 queries MUST use bound parameters (Invariant 2 — no string
 *   interpolation); this module performs no external transmission (Invariant 1);
 *   DUMMY_HASH must stay a real, parseable bcrypt hash or the anti-enumeration
 *   compare degrades.
 * Failure modes: a D1 error propagates to the caller (login fails closed — Hono
 *   500). bcrypt.compare at cost 10 can exceed the Workers FREE-plan 10ms CPU cap
 *   (Error 1102) — see README "Deploy" for the Paid-plan / PBKDF2 resolution.
 * Consumers: worker/index.ts (POST /api/login); the Phase 7 admin route (future).
 */
import bcrypt from "bcryptjs";
import type { Env, SessionClaims } from "./types";

/**
 * A valid bcrypt (cost 10) hash of a random throwaway string. We run a compare
 * against THIS when the username is not found, so a present vs. absent user take
 * similar time — closing the username-enumeration timing oracle. It must be a
 * real, parseable bcrypt hash or bcrypt.compare() short-circuits.
 * (Verifies nothing — no plaintext anywhere produces it intentionally.)
 */
const DUMMY_HASH = "$2b$10$KmVI8UrJ/3t3wQbACO0HxueQCbFUZt44aO0IhSVPo3Zv3oIqT6f6W";

interface UserRow {
  id: number;
  username: string;
  password_hash: string;
}

export interface AuthedUser {
  id: number;
  username: string;
}

/**
 * Verify a username/password against the D1 `users` table.
 *
 * Hashing: the mission (Q2) locks "bcrypt cost factor 10". `bcryptjs` is pure-JS
 * (no native addon) and honors that literally; it imports node:crypto for salt
 * generation, so the Worker enables `nodejs_compat` (see wrangler.jsonc).
 * bcrypt.compare is constant-time w.r.t. the stored hash. DEPLOY-TIME CAVEAT: a cost-10 compare
 * can exceed the Workers FREE plan's 10ms CPU cap (Error 1102) — the validation /
 * production Worker must run on the Paid plan, or swap this to Web-Crypto
 * PBKDF2-SHA-256 @100k iters (the documented Workers-constrained substitute).
 * See safety_portal/README.md.
 */
export async function validateUser(
  env: Env,
  username: string,
  password: string,
): Promise<AuthedUser | null> {
  const row = await env.DB.prepare(
    "SELECT id, username, password_hash FROM users WHERE username = ?",
  )
    .bind(username)
    .first<UserRow>();

  // Always compare (dummy hash if the row is missing) to avoid a timing oracle.
  const stored = row?.password_hash ?? DUMMY_HASH;
  const ok = await bcrypt.compare(password, stored);

  if (!row || !ok) return null;
  return { id: row.id, username: row.username };
}

/** Build the claims object placed (signed) into the session cookie. */
export function newSessionClaims(user: AuthedUser): SessionClaims {
  return { sub: user.id, username: user.username, iat: Math.floor(Date.now() / 1000) };
}
