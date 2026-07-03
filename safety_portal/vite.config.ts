import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cloudflare } from "@cloudflare/vite-plugin";

// The cloudflare() plugin reads wrangler.jsonc: it runs the Worker (worker/index.ts)
// inside the Vite dev server and serves the SPA, with D1 simulated locally
// (Miniflare). `vite dev` / `vite build` / `vite preview` need NO Cloudflare token.
export default defineConfig({
  plugins: [react(), cloudflare()],
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
