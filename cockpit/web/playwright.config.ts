import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import { defineConfig } from "@playwright/test";

import {
  ARTIFACTS,
  DB,
  HARNESS_DIR,
  PORT,
  PROMOTE_SIBLING,
  PROMOTED,
  RUN_DIR,
  STAGING,
} from "./e2e/paths";

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

// No process this suite starts reads a checkout's .env (python-dotenv's own
// switch): the server and the seed scripts the specs run all inherit this
// environment, so a checkout's keys, agent, store and folders never reach
// them, whatever its .env holds; the server's settings are set below.
process.env.PYTHON_DOTENV_DISABLED = "1";

// The harness suite: every spec, against scripted executors. The crexil
// journeys also run against the isolated preview (playwright.preview.config.ts);
// a test that holds in only one of the two declares it by reading
// /api/health's `preview` and skipping with its reason. scripts/e2e.sh runs
// both suites offline, each in a fresh run folder.
//
// Self-contained: boots the FastAPI app on CREXIL_E2E_PORT (default 8123)
// with a throwaway store under the run folder (e2e/paths.ts), recreated at
// every boot, and the built SPA from dist/ — run `npm run build` first.
// It never touches the deployed container on :8000 and never opens the
// default store (cockpit/server/cockpit-v4.sqlite). That holds by
// construction, not by convention: COCKPIT_HARNESS_EXECUTORS swaps in
// scripted executors (app/harness.py) so a spec-driven Run can land/fail
// slots on command, and every runtime and provider URL points at port 9,
// a privileged port nothing in a user session listens on. The hosted
// catalogue therefore comes from providers.json alone, never the network.
export default defineConfig({
  testDir: "./e2e",
  // One shared server, and its queue/bench state is process-global one-GPU
  // truth — parallel spec files would read each other's batches (the law
  // specs run live turns). Serialise; the suite is small.
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    command:
      `bash -c 'rm -rf "${HARNESS_DIR}" "${STAGING}" "${PROMOTED}" "${PROMOTE_SIBLING}"` +
      ` && mkdir -p "${HARNESS_DIR}" "${STAGING}" "${PROMOTED}" "${ARTIFACTS}"` +
      ` && exec uv run uvicorn app.main:app --host 127.0.0.1 --port ${PORT}'`,
    cwd: "../server",
    // readiness = "app booted" (/api answers only after the lifespan
    // completes). /api/health would 503 here by design: it reports engine
    // truth, and this harness points ComfyUI at a closed port.
    url: `http://127.0.0.1:${PORT}/api`,
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      COCKPIT_DB: DB,
      COCKPIT_STAGING_DIR: STAGING,
      COCKPIT_STATIC_DIR: "../web/dist",
      COCKPIT_HARNESS_EXECUTORS: "1",
      COMFYUI_URL: "http://127.0.0.1:9",
      THREED_URL: "http://127.0.0.1:9",
      VOICE_RUNNER_URL: "http://127.0.0.1:9",
      OPENROUTER_URL: "http://127.0.0.1:9",
      // Keys and the voice director's agent are blank whatever the shell
      // exported: an empty variable is set.
      OPENROUTER_API_KEY: "",
      ELEVENLABS_VOICE_AGENT_ID: "",
      // ElevenLabs gets a placeholder key at a closed port, so its operations
      // are ready and a blind voice run has two engines; the scripted
      // executors never call ElevenLabs, and a real key never reaches here.
      ELEVENLABS_API_KEY: "harness-placeholder",
      ELEVENLABS_URL: "http://127.0.0.1:9",
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
      // The promotion boundary, pointed inside the run folder so the spec
      // writes a real asset tree without touching a real drive. The folder
      // is created by the command above, never by the app — validate_target's
      // whole point is that a missing root is refused, not built.
      PROMOTE_DIR: PROMOTED,
      // The fence. The run folder stands in for the promotion drive here,
      // so a spec can re-point to a sibling folder the way the artist can
      // on their drive — and anything above it is refused, which is the
      // case that catches /tmp and /data being accepted.
      PROMOTE_ROOTS: RUN_DIR,
    },
  },
});
