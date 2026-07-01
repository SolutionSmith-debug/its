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
import type { Env, Role, SessionClaims } from "./types";

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
  role: string;
  session_epoch: number;
  disabled: number;
}

export interface AuthedUser {
  id: number;
  username: string;
  role: Role;
  /** Live revocation epoch (slice 8a, audit #7) — snapshotted into the cookie at
   *  issue by newSessionClaims. */
  session_epoch: number;
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
    "SELECT id, username, password_hash, role, session_epoch, disabled FROM users WHERE username = ?",
  )
    .bind(username)
    .first<UserRow>();

  // Always compare (dummy hash if the row is missing) to avoid a timing oracle.
  const stored = row?.password_hash ?? DUMMY_HASH;
  const ok = await bcrypt.compare(password, stored);

  // Reject a missing, wrong-password, OR DISABLED user (PR-4). The disabled check is AFTER the
  // compare so a disabled account is not a username-enumeration timing oracle. requireSession's
  // per-request disabled check stays authoritative for already-live sessions.
  if (!row || !ok || row.disabled) return null;
  return { id: row.id, username: row.username, role: coerceRole(row.role), session_epoch: row.session_epoch };
}

/** Narrow a raw DB role string to the Role union, defaulting unknown → 'submitter'
 *  (fail-SAFE: an unexpected value must never be treated as 'admin' OR 'manager'). The
 *  roles-FK (migration 0013, superseding 0007's value-list CHECK) makes 'unknown'
 *  unreachable in practice; this is the belt to that suspenders so a future schema slip
 *  can't silently grant a privileged tier. Every recognized non-submitter tier must be
 *  matched EXPLICITLY here — an unlisted value fails safe to 'submitter', never upward. */
export function coerceRole(raw: string | null | undefined): Role {
  if (raw === "admin") return "admin";
  if (raw === "manager") return "manager";
  return "submitter";
}

/**
 * Resolve a user's role KEY to its granted capability SET (migration 0013's
 * `role_capabilities`), read fresh per request — the SAME change-effective-next-request
 * posture as `role` / `disabled` / `session_epoch` in requireSession.
 *
 * FAIL-CLOSED (Invariant 2; migration 0007's belt-to-suspenders, now load-bearing): a
 * null / missing role, an unknown role, a role with no grants, OR a D1 error all yield
 * the EMPTY set — never a privileged capability. Bound parameters only (Invariant 2).
 *
 * ORDER DEPENDENCY: migration 0013 (the `role_capabilities` table) MUST be live before
 * this resolver ships, or every resolve hits a missing table → empty caps → 401s.
 */
export async function resolveCapabilities(
  roleKey: string | null | undefined,
  db: Env["DB"],
): Promise<Set<string>> {
  if (!roleKey) return new Set();
  try {
    const { results } = await db
      .prepare("SELECT capability_key FROM role_capabilities WHERE role_key = ?")
      .bind(roleKey)
      .all<{ capability_key: string }>();
    return new Set((results ?? []).map((r) => r.capability_key));
  } catch {
    return new Set();
  }
}

/** Build the claims object placed (signed) into the session cookie. `epoch` snapshots
 *  the user's live session_epoch at issue (slice 8a, audit #7) so a later logout /
 *  password-change DB-side bump leaves this cookie's snapshot stale → rejected. */
export function newSessionClaims(user: AuthedUser): SessionClaims {
  return {
    sub: user.id,
    username: user.username,
    iat: Math.floor(Date.now() / 1000),
    epoch: user.session_epoch,
  };
}

/**
 * Hash a plaintext password with bcrypt cost 10 (the mission's locked factor).
 *
 * The BACKEND hashes: the operator passes plaintext over the bearer-gated admin
 * route at provision/reset; the plaintext is never stored, returned, or logged.
 * Same cost-10 caveat as `validateUser` (Workers Paid plan, Error 1102).
 */
export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, 10);
}

/**
 * Normalize + validate a portal username: `lastname.firstname`, lowercase ASCII
 * letters (with internal `'`/`-`), exactly one dot, length-capped. Returns the
 * normalized (trimmed + lowercased) username, or `null` if invalid.
 */
export function normalizeUsername(raw: string): string | null {
  const u = raw.trim().toLowerCase();
  if (u.length < 3 || u.length > 64) return null;
  if (!/^[a-z][a-z'-]*\.[a-z][a-z'-]*$/.test(u)) return null;
  return u;
}

/**
 * Parse a request-supplied role into the Role union. `undefined` → `dflt`
 * (caller-chosen; 'submitter' unless overridden — fail-SAFE: a missing role is
 * NEVER 'admin'). A recognized literal passes through; anything else → null so the
 * caller rejects it (400 invalid_role).
 *
 * §42 — lives here (beside normalizeUsername/coerceRole, the credential/role parsers)
 * rather than in index.ts so the field-ops WRITE modules can share it WITHOUT importing
 * index.ts (index.ts registers those modules → that import would be a runtime cycle; the
 * same constraint audit.ts documents). index.ts imports it from here.
 */
export function parseRole(value: unknown, dflt: Role = "submitter"): Role | null {
  if (value === undefined) return dflt;
  if (value === "admin" || value === "manager" || value === "submitter") return value;
  return null;
}
