import { applyD1Migrations, env } from "cloudflare:test";
import type { D1Migration } from "cloudflare:test";

// Apply the real SQL migrations (all of migrations/) to the test D1 before the suite runs,
// so every test starts from the production schema. Idempotent: applyD1Migrations
// tracks applied migrations and skips ones already present. TEST_MIGRATIONS is the
// readD1Migrations() array passed through as a binding in vitest.config.ts.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// `env` in cloudflare:test is typed `Cloudflare.Env` (the wrangler-generated global
// namespace). This project hand-authors worker/types.ts and does not run cf-typegen,
// so we declare the test-visible bindings here — the Worker secrets + DB + the
// test-only TEST_MIGRATIONS the setup above consumes.
declare global {
  namespace Cloudflare {
    interface Env {
      DB: D1Database;
      TEST_MIGRATIONS: D1Migration[];
      SESSION_SIGNING_SECRET: string;
      HMAC_PAYLOAD_SECRET: string;
      PORTAL_INTERNAL_API_TOKEN: string;
      PORTAL_ADMIN_API_TOKEN: string;
      PORTAL_FIELDOPS_API_TOKEN: string;
      PORTAL_PO_API_TOKEN: string;
      PORTAL_CONFIG_API_TOKEN: string;
    }
  }
}
