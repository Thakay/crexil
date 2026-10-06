// Where one e2e run keeps its throwaway state, shared by the Playwright
// configs and the specs. scripts/e2e.sh points CREXIL_E2E_DIR at a fresh
// folder per run; without it a run lives in the checkout's .staging/e2e,
// which each suite's server clears before it boots. Nothing here ever names
// the live store, the live promote drive or the persistent .preview folder.
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const REPO = path.resolve(HERE, "../../..");
export const RUN_DIR = path.resolve(
  process.env.CREXIL_E2E_DIR || path.join(REPO, ".staging/e2e"),
);
// The servers delete and recreate folders under RUN_DIR, so a run folder
// must be one this harness made: the default, or a crexil-e2e* temp folder.
if (
  process.env.CREXIL_E2E_DIR &&
  !path.basename(RUN_DIR).startsWith("crexil-e2e")
) {
  throw new Error(
    `CREXIL_E2E_DIR must name a crexil-e2e* folder, not ${RUN_DIR}`,
  );
}

export const PORT = Number(process.env.CREXIL_E2E_PORT || 8123);

// The harness suite: its store, staging and the promote drive's stand-ins.
// The fence is RUN_DIR itself, so a sibling of the promote folder is on the
// drive and anything above RUN_DIR (such as /tmp) is off it.
export const HARNESS_DIR = path.join(RUN_DIR, "e2e-harness");
export const DB = path.join(HARNESS_DIR, "cockpit-v4.sqlite");
export const STAGING = path.join(RUN_DIR, "e2e-staging");
export const PROMOTED = path.join(RUN_DIR, "e2e-promote");
export const PROMOTE_SIBLING = path.join(RUN_DIR, "e2e-promote-sibling");

// The preview suite runs the isolated preview with this folder as its repo
// root, so its forced .preview state lands here and never in the checkout.
export const PREVIEW_ROOT = path.join(RUN_DIR, "preview-root");

// Evidence a spec chooses to keep (screenshots, verification records).
export const ARTIFACTS = path.join(RUN_DIR, "artifacts");
export const artifact = (name: string) => path.join(ARTIFACTS, name);
