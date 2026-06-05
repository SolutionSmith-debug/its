// Cloudflare bindings + shared types for the Safety Portal Worker.
// Bindings are declared in wrangler.jsonc; @cloudflare/workers-types supplies
// the D1Database / R2Bucket / Fetcher globals.

export interface Env {
  /** D1 database. Phase 2: `users` table. Later phases: submissions + mirrors. */
  DB: D1Database;
  /** R2 bucket for generated PDFs. Provisioned in Phase 2, first used in Phase 5. */
  PDF_BUCKET: R2Bucket;
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
}

/** Claims signed (NOT encrypted) into the session cookie. Keep minimal — readable by the holder. */
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
}
