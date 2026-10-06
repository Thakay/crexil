import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxies the isolated preview by default. An explicit target can select
// another development server; the preview script serves its own built SPA.
// Prod: `npm run build` output is served by FastAPI itself (one port).
// Build stamp: the docker web stage sets VITE_BUILD_ID to the git rev and
// the app reads import.meta.env.VITE_BUILD_ID (Vite exposes VITE_* natively;
// a plain dev build reads "dev"). The footer compares this bundle id with the
// server's build id and warns on mismatch (deployment drift).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.COCKPIT_DEV_API || "http://127.0.0.1:8010",
        ws: true,
      },
    },
  },
  build: { outDir: "dist" },
});
