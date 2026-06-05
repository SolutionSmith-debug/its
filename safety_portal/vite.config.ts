import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cloudflare } from "@cloudflare/vite-plugin";

// The cloudflare() plugin reads wrangler.jsonc: it runs the Worker (worker/index.ts)
// inside the Vite dev server and serves the SPA, with D1/R2 simulated locally
// (Miniflare). `vite dev` / `vite build` / `vite preview` need NO Cloudflare token.
export default defineConfig({
  plugins: [react(), cloudflare()],
});
