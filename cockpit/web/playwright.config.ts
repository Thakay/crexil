import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import { defineConfig } from "@playwright/test";

// On a box without chromium's system deps, a user-space shim dir
// (built via `apt-get download libnss3 libnspr4 libasound2t64` + `dpkg -x`,
// no sudo needed) supplies libnss3/libnspr4/libasound2.
// `sudo apt-get install -y libnss3 libnspr4 libasound2t64` makes it
// removable; with the dir absent this block is a no-op.
const shim = path.join(homedir(), ".cache/ms-playwright/shim-libs");
if (existsSync(shim)) {
  process.env.LD_LIBRARY_PATH = [shim, process.env.LD_LIBRARY_PATH]
    .filter(Boolean)
    .join(":");
}

// E2E harness. Self-contained: boots the FastAPI app on :8123 with a
// throwaway DB under .staging/ and the built SPA from dist/ — run
// `npm run build` first. Leaves the runtime singletons alone: never touches
// the deployed container on :8000, and never opens the default store
// (cockpit/server/cockpit-v4.sqlite). That holds by construction, not by convention:
// COCKPIT_HARNESS_EXECUTORS swaps in scripted executors (app/harness.py) so
// a spec-driven Run can land/fail slots on command, and COMFYUI_URL points
// at a genuinely closed port so nothing in the process can reach the real
// :8188.
export default defineConfig({
  testDir: "./e2e",
  // One shared server, and its queue/bench state is process-global one-GPU
  // truth — parallel spec files would read each other's batches (the law
  // specs run live turns). Serialise; the suite is small.
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8123" },
  webServer: {
    command:
      "bash -c 'mkdir -p ../../.staging/e2e-harness ../../.staging/e2e-promote && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8123'",
    cwd: "../server",
    // readiness = "app booted" (/api answers only after the lifespan
    // completes). /api/health would 503 here by design: it reports engine
    // truth, and this harness points ComfyUI at a closed port.
    url: "http://127.0.0.1:8123/api",
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      COCKPIT_DB: "../../.staging/e2e-harness/cockpit-v4.sqlite",
      COCKPIT_STATIC_DIR: "../web/dist",
      COCKPIT_HARNESS_EXECUTORS: "1",
      COMFYUI_URL: "http://127.0.0.1:8199",
      // B4's bill re-asks come "over about a minute" in production (20 s
      // gaps); the trust-cases spec watches them through the 2 s polls, so
      // the gap is 4 s here — long enough to be seen, short enough to wait
      COCKPIT_BILL_CHECK_GAP_S: "4",
      // A rate limit with no provider window retries after 10 s in
      // production; 3 s here keeps the countdown watchable and the landing
      // inside a spec's patience. The card re-check runs every 15 s in
      // production; 1 s here so C2's automatic pickup is seen, and the
      // hold threshold stays the shipped 2.5 GB so the spec's numbers are
      // the product's.
      COCKPIT_RATE_LIMIT_BACKOFF_S: "3",
      COCKPIT_GPU_RECHECK_S: "1",
      // The promotion boundary, pointed inside .staging so the spec
      // writes a real asset tree without touching /mnt/d. The folder is
      // created by the command above, never by the app — validate_target's
      // whole point is that a missing root is refused, not built.
      PROMOTE_DIR: "../../.staging/e2e-promote",
      // The fence. `.staging` stands in for the promotion drive here,
      // so a spec can re-point to a sibling folder the way the artist can
      // on D: — and anything above it is refused, which is the case that
      // catches /tmp and /data being accepted.
      PROMOTE_ROOTS: "../../.staging",
    },
  },
});
