import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cloudflare } from "@cloudflare/vite-plugin";
import { eagerFormDefinitionsPlugin } from "./vite-plugin-eager-forms";

// The cloudflare() plugin reads wrangler.jsonc: it runs the Worker (worker/index.ts)
// inside the Vite dev server and serves the SPA, with D1 simulated locally
// (Miniflare). `vite dev` / `vite build` / `vite preview` need NO Cloudflare token.
//
// eagerFormDefinitionsPlugin serves `virtual:eager-form-definitions` to
// src/forms/registry.ts (registry split, operator-approved 2026-07-03): active
// current+previous definitions bundled eagerly, every other shipped version as its own
// lazy chunk. Data-driven from catalog.json on EVERY build, so the publish daemon's
// auto-publishes need no code change. The Worker never imports the virtual id — zero
// effect on the worker build.
export default defineConfig({
  plugins: [react(), cloudflare(), eagerFormDefinitionsPlugin()],
  build: {
    // R4-F7 vendor-chunk split: pin react/react-dom/scheduler into their own chunk so a
    // definition-only publish (the common auto-publish: catalog.json + forms/*.json →
    // src/forms/registry.ts) stops rotating the ENTIRE ~549KB main-chunk hash — field
    // phones re-download only the app chunk, not the never-changing React runtime.
    // Vite 8 is rolldown-based: the object form of rollupOptions.output.manualChunks was
    // REMOVED (function form deprecated); rolldown's codeSplitting.groups is the
    // supported mechanism. The test matches only node_modules React packages, so the
    // Worker environment build (no React) is untouched.
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/,
            },
          ],
        },
      },
    },
  },
});
