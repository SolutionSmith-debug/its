// Cloudflare bindings + shared types for the Safety Portal Worker.
// Bindings are declared in wrangler.jsonc; @cloudflare/workers-types supplies
// the D1Database / Fetcher globals.
//
// No R2: under Box-as-System-of-Record + Option-B render the Worker never holds a
// PDF (intake.py renders + stores it in Box). Removed 2026-06-05.

export interface Env {
  /** D1 database. Phase 2: `users` table. Later phases: submissions + mirrors. */
  DB: D1Database;
  /** Static-asset fetcher (the built SPA). Bound via assets.binding in wrangler.jsonc. */
  ASSETS: Fetcher;
  /** HMAC key for signing session cookies. Workers Secret / .dev.vars — never committed. */
  SESSION_SIGNING_SECRET: string;
  /**
   * HMAC-SHA256 key the Worker signs each submission with at /api/submit (Phase 5
   * pull model). The Mac-side portal_poll daemon verifies it (mirrored into the
   * macOS Keychain as ITS_PORTAL_HMAC_SECRET). Workers Secret / .dev.vars — never committed.
   */
  HMAC_PAYLOAD_SECRET: string;
  /**
   * Bearer token the Mac-side portal_poll daemon presents to /api/internal/* (the
   * queue drain + the receipt). Mirrored into the Keychain as ITS_PORTAL_INTERNAL_TOKEN.
   * Workers Secret / .dev.vars — never committed.
   */
  PORTAL_INTERNAL_API_TOKEN: string;
  /**
   * OPERATOR-ONLY bearer token for the /api/internal/admin/* user-provisioning
   * routes — SEPARATE from PORTAL_INTERNAL_API_TOKEN so the portal_poll daemon's
   * token cannot create / reset / disable users (privilege separation). Mirrored
   * into the Keychain as ITS_PORTAL_ADMIN_TOKEN. Workers Secret / .dev.vars —
   * never committed.
   */
  PORTAL_ADMIN_API_TOKEN: string;
}

/** A portal user's authorization role. 'submitter' is the default for every field
 *  PM; 'admin' unlocks the dashboard (account management + submit-as). */
export type Role = "submitter" | "admin";

/** Claims signed (NOT encrypted) into the session cookie. Keep minimal — readable by
 *  the holder. Deliberately NO role here: role is read fresh from D1 per request
 *  (see requireSession), so a demotion takes effect immediately rather than waiting
 *  for the 90-day cookie to expire — same reasoning as the per-request `disabled`
 *  check. A stale signed role in the cookie would be a privilege-escalation footgun. */
export interface SessionClaims {
  /** users.id of the authenticated portal user. */
  sub: number;
  /** username (lastname.firstname) — display only. */
  username: string;
  /** issued-at, unix seconds. */
  iat: number;
}

/** Hono per-request variables. */
export interface Vars {
  session: SessionClaims;
  /** The acting user's role, read fresh from D1 by requireSession on every request
   *  (NOT from the cookie). requireRole() and the submit-as gate read this. */
  role: Role;
}
