import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// ─────────────────────────────────────────────────────────────────────────────
// SPA render-smoke project (Phase-2 slice 3c, design brief C5 — the THIRD renderer).
//
// This is a SEPARATE, ISOLATED vitest config from vitest.config.ts on purpose.
// vitest.config.ts loads @cloudflare/vitest-pool-workers, which runs tests inside
// workerd — a runtime with NO DOM and NO jsdom, so it CANNOT render React. The
// SPA FormRenderer (the third leg of the render-smoke net, alongside the two
// Python renderers exercised by tests/test_render_smoke.py) must render in jsdom.
//
// Keeping it in its own config — rather than as a second entry in the workers
// config's `test.projects` — guarantees the cloudflareTest() plugin is NEVER
// loaded for these tests (it would try to take over the runtime) and that
// `npm test` (the workers pool) is byte-for-byte unchanged. The two suites run as
// two npm scripts: `npm test` (workers) and `npm run test:spa` (this file). Both
// run in CI.
//
// No cloudflare plugin here; @vitejs/plugin-react gives the JSX/TSX transform and
// Vite's import.meta.glob (which src/forms/registry.ts uses to bundle the form
// definitions + catalog manifest) resolves through the normal Vite pipeline.
// ─────────────────────────────────────────────────────────────────────────────
export default defineConfig({
  plugins: [react()],
  test: {
    name: "spa",
    environment: "jsdom",
    globals: true,
    // ONLY the SPA render-smoke suite. The worker tests under test/*.test.ts are
    // never matched here (they belong to vitest.config.ts / the workers pool).
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
