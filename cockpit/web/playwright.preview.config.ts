import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import { defineConfig } from "@playwright/test";

import { ARTIFACTS, PORT, PREVIEW_ROOT, REPO } from "./e2e/paths";

// Chromium's user-space system-library shim, as in playwright.config.ts.
const shim = path.join(homedir(), ".cache/ms-playwright/shim-libs");
if (existsSync(shim)) {
  process.env.LD_LIBRARY_PATH = [shim, process.env.LD_LIBRARY_PATH]
    .filter(Boolean)
    .join(":");
}

// The preview suite: the crexil journeys against the isolated preview
// (CREXIL_PREVIEW=1), the build an artist without a runtime sees. A
// preview forces its writable state under <repo root>/.preview and ignores
// path overrides, so this server runs with the run folder's preview-root as
// its repo root and reads presets, workflows, configs and providers from the
// checkout. Every boot starts from an empty preview, builds the preview
// bundle into it, and never touches the checkout's own .preview. Specs that
// need scripted executors skip themselves here by reading /api/health.
const PREVIEW = path.join(PREVIEW_ROOT, ".preview");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/crexil*.spec.ts",
  // One server and one store, as in the harness suite.
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    command:
      `bash -c 'rm -rf "${PREVIEW_ROOT}"` +
      ` && mkdir -p "${PREVIEW}/state" "${PREVIEW}/staging" "${PREVIEW}/promote" "${ARTIFACTS}"` +
      ` && (cd ../web && VITE_CREXIL_PREVIEW=1 npx vite build --outDir "${PREVIEW}/static" --emptyOutDir --logLevel warn)` +
      ` && exec uv run uvicorn app.main:app --host 127.0.0.1 --port ${PORT}'`,
    cwd: "../server",
    // readiness = "app booted"; /api/health answers 503 in a preview by design.
    url: `http://127.0.0.1:${PORT}/api`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      CREXIL_PREVIEW: "1",
      COCKPIT_REPO_ROOT: PREVIEW_ROOT,
      COCKPIT_PRESETS_DIR: path.join(REPO, "presets"),
      COCKPIT_WORKFLOWS_DIR: path.join(REPO, "workflows"),
      COCKPIT_CONFIGS_DIR: path.join(REPO, "configs"),
      COCKPIT_PROVIDERS_FILE: path.join(REPO, "providers.json"),
    },
  },
});
