// Pure composer derivations: cost honesty (estimates per
// stepped take, unlisted stated never guessed), law-6 price on the button
// face, the "queues behind" line in observed time, the one-GPU sentence in
// counts (law 13, law 17), plain-words disabled reasons mirroring the
// server's 422 copy.
import { expect, test } from "vitest";
import type { ProviderModel, QueueView } from "../../types";
import type { Preset } from "../../types";
import {
  ASSUMED_PX,
  busyLine,
  diskWhy,
  errText,
  extEstimate,
  fmtBytes,
  gpuSentence,
  hasRecipe,
  noRecipeReason,
  recipeFor,
  runLabel,
  slotAreas,
  slotsLine,
  sumTakes,
  vramWarning,
  whyNot,
} from "./compose";

const model = (id: string, price: number | null): ProviderModel => ({
  id,
  provider: "openrouter",
  label: id,
  name: id,
  supports_seed: true,
  pinned: false,
  source: "live",
  price_usd_per_image: price,
  price_note: null,
  description: null,
  params: null,
  enabled: null,
});

test("extEstimate prices every stepped take and counts unlisted models", () => {
  const models = [model("a", 0.04), model("b", null), model("c", 0.1)];
  expect(
    extEstimate(models, new Map([["a", 2], ["b", 3], ["c", 1]])),
  ).toEqual({ known: 0.18, unlisted: 1 });
  expect(extEstimate(models, new Map([["a", 0]]))).toEqual({ known: 0, unlisted: 0 });
  expect(sumTakes(new Map([["a", 2], ["b", 3]]))).toBe(5);
});

test("the footer's slot line: the count, the money only where money is, unlisted said", () => {
  expect(slotsLine(9, { known: 0.12, unlisted: 0 })).toBe("9 SLOTS · ~$0.12 THIS RUN");
  expect(slotsLine(1, { known: 0, unlisted: 0 })).toBe("1 SLOT · NO SPEND");
  expect(slotsLine(3, { known: 0.04, unlisted: 2 })).toBe(
    "3 SLOTS · ~$0.04 THIS RUN · 2 PRICES UNLISTED",
  );
  expect(slotsLine(2, { known: 0, unlisted: 1 })).toBe("2 SLOTS · NO SPEND · 1 PRICE UNLISTED");
  // the switch is named when it is what zeroed the spend
  expect(slotsLine(1, { known: 0, unlisted: 0 }, true)).toBe("1 SLOT · NO SPEND · LOCAL ONLY");
});

test("the costed Run carries its price on the button face (law 6) and what it queues behind", () => {
  expect(runLabel(3, { known: 0 })).toBe("Run · 3");
  expect(runLabel(3, { known: 0.08 })).toBe("Run · 3 · ~$0.08");
  expect(runLabel(3, { known: 0.08 }, 2)).toBe("Run · 3 · ~$0.08 · queues behind 2");
  expect(runLabel(1, { known: 0 }, 1)).toBe("Run · 1 · queues behind 1");
});

test("the one-GPU sentence is counts, never a wall-clock (law 13, law 17)", () => {
  expect(gpuSentence(7, 2)).toBe(
    "7 local run one after another on the single GPU; the 2 externals go out at once and usually land first.",
  );
  expect(gpuSentence(1, 1)).toBe(
    "1 local runs alone on the single GPU; 1 external goes out at once and usually lands first.",
  );
  expect(gpuSentence(3, 0)).toBe("3 local run one after another on the single GPU.");
  expect(gpuSentence(0, 2)).toBe(
    "The 2 externals go out at once and usually land first; nothing touches the GPU.",
  );
  expect(gpuSentence(0, 0)).toBe(
    "Step an engine up — one number per row, never one for the run.",
  );
});

const slot = (over: Partial<QueueView["lanes"]["local"]["slots"][number]>) => ({
  id: "s",
  turn_id: "t",
  turn_idx: 3,
  workspace_id: "w",
  engine: "raw-zimage",
  status: "queued" as const,
  attempt: 1,
  retry_of: null,
  position: 1,
  elapsed_ms: null,
  committed_usd: null,
  charged_usd: null,
  cancel_requested: false,
  awaiting: false,
  bench: null,
  created_at: "2026-08-24T00:00:00Z",
  ...over,
});
const queue = (depth: number, slots: ReturnType<typeof slot>[]): QueueView => ({
  seq: 1,
  now: "2026-08-24T00:00:00Z",
  depth,
  lanes: {
    local: { label: "LOCAL · SEQUENTIAL, ONE GPU", depth, slots },
    external: { label: "EXTERNAL · CONCURRENT, PRICED", concurrency: 4, active: 0, slots: [] },
  },
  held: [],
  late: [],
  benches: [],
  checks: [],
  retries: [],
  gpu: { reading: null, rechecking: false, manual_hold: false },
});

test("the busy line is the live queue in observed time — and silent when nothing local waits", () => {
  const busy = queue(2, [
    slot({ id: "r", status: "running", position: 0, elapsed_ms: 72_000 }),
    slot({ id: "q", position: 1 }),
  ]);
  expect(busyLine(busy, 3)).toBe(
    "GPU busy — RAW-ZIMAGE running 1m 12s so far, 1 queued; your 3 local queue behind them.",
  );
  expect(busyLine(queue(1, [slot({ id: "r", status: "running", position: 0, elapsed_ms: 500 })]), 1)).toBe(
    "GPU busy — RAW-ZIMAGE running 0s so far; your 1 local queues behind it.",
  );
  // a queued row without a running one (the lane benched): still said, no time invented
  expect(busyLine(queue(1, [slot({ id: "q" })]), 1)).toBe(
    "GPU busy, 1 queued; your 1 local queues behind it.",
  );
  expect(busyLine(queue(0, []), 3)).toBeNull();
  expect(busyLine(busy, 0)).toBeNull(); // externals never queue behind the GPU
  expect(busyLine(undefined, 3)).toBeNull(); // the queue hasn't arrived — no claim
});

const preset = (id: string, workflows: Record<string, string>): Preset =>
  ({
    id,
    display_name: id,
    description: "",
    modality: "image",
    workflows,
    default_candidates: 1,
    inputs: [],
  }) as unknown as Preset;

test("the run's parents decide which recipe the roster needs", () => {
  expect(recipeFor([])).toBe("txt2img");
  expect(recipeFor(["n1"])).toBe("img2img");
  const both = preset("both", { txt2img: "a", img2img: "b" });
  const textOnly = preset("text", { txt2img: "a" });
  expect(hasRecipe(both, "img2img")).toBe(true);
  expect(hasRecipe(textOnly, "img2img")).toBe(false);
  // the dimmed row names the recipe it is missing, never just "unavailable"
  expect(noRecipeReason("img2img")).toBe("no img2img recipe committed");
});

test("whyNot gives plain words in server order, null when runnable", () => {
  const ok = {
    prompt: "a sword",
    localTakes: 1,
    external: 0,
    recipe: "txt2img" as const,
    localReady: 2,
  };
  expect(whyNot(ok)).toBeNull();
  expect(whyNot({ ...ok, localTakes: 0 })).toBe(
    "A run needs at least one take.",
  );
  expect(whyNot({ ...ok, localTakes: 17 })).toBe("A run caps at 16 takes.");
  expect(whyNot({ ...ok, localTakes: 9, external: 8 })).toBe("A run caps at 16 takes.");
  expect(whyNot({ ...ok, prompt: "  " })).toBe("Needs a prompt.");
  // externals alone need no local engine
  expect(whyNot({ ...ok, localTakes: 0, external: 2 })).toBeNull();
});

test("a refine with no engine to run on blames the roster, not the artist", () => {
  const args = {
    prompt: "a sword",
    localTakes: 0,
    external: 0,
    recipe: "img2img" as const,
  };
  expect(whyNot({ ...args, localReady: 0 })).toBe(
    "No local engine has an img2img recipe committed, so nothing here can " +
      "run this yet.",
  );
  // an unloaded roster is not an empty one — no claim either way
  expect(whyNot({ ...args, localReady: null })).toBe(
    "The engine roster isn't loaded yet.",
  );
});

test("errText surfaces the server's own words when it spoke", () => {
  expect(errText({ detail: "a run caps at 16 takes" })).toBe(
    "a run caps at 16 takes",
  );
  expect(errText(undefined)).toBe(
    "The run didn't start — the server refused it.",
  );
  expect(errText({ detail: [{ msg: "validation" }] })).toBe(
    "The run didn't start — the server refused it.",
  );
});

// ── E6: free VRAM and free disk at Run ──────────────────────────────────────

const SHEET = {
  gpu: null,
  gpu_hold_threshold_bytes: 2.5 * 1024 ** 3,
  gpu_held: false,
  disk_free_bytes: 40 * 1024 * 1024,
  disk_margin_bytes: 0,
  disk_path: "/data/staging",
  bytes_per_px: 1.5,
  bytes_per_px_source: "fallback" as const,
  at: "2026-08-25T00:00:00+00:00",
};

test("E6: slot areas come from each take's own frame, a swept take's override winning, else the stand-in", () => {
  const areas = slotAreas(
    [
      { takes: 2, inputs: { width: 1536, height: 1536 }, take_overrides: [{}, { width: 1024, height: 1024 }] },
      { takes: 1, inputs: { steps: 4 }, take_overrides: [] },
    ],
    2,
  );
  expect(areas).toEqual([1536 * 1536, 1024 * 1024, ASSUMED_PX, ASSUMED_PX, ASSUMED_PX]);
});

test("E6: the drive that can't hold the run disarms with the numbers; one that can says nothing", () => {
  // 9 takes at 2048² × 1.5 B/px ≈ 54 MB against 40 MB free
  const big = Array(9).fill(2048 * 2048);
  expect(diskWhy(SHEET, big)).toBe(
    "9 slots need about 54 MB and the cockpit's drive has 40 MB free — free up about 14 MB first.",
  );
  expect(diskWhy(SHEET, [1024 * 1024])).toBeNull();
  // the margin counts
  expect(diskWhy({ ...SHEET, disk_margin_bytes: 39 * 1024 * 1024 }, [1024 * 1024])).toMatch(/^1 slot needs? about 2 MB/);
  // no sheet yet, or nothing asked: silence is not a claim
  expect(diskWhy(undefined, big)).toBeNull();
  expect(diskWhy(SHEET, [])).toBeNull();
  expect(fmtBytes(1.5 * 1024 ** 3)).toBe("1.5 GB");
  expect(fmtBytes(300)).toBe("1 MB");
  // and Run is disarmed by it — after the roster's own reasons
  expect(whyNot({ prompt: "p", localTakes: 1, external: 0, recipe: "txt2img", localReady: 3, disk: "no room" })).toBe("no room");
  expect(whyNot({ prompt: "", localTakes: 1, external: 0, recipe: "txt2img", localReady: 3, disk: "no room" })).toBe("Needs a prompt.");
  expect(whyNot({ prompt: "p", localTakes: 1, external: 0, recipe: "txt2img", localReady: 3, disk: null })).toBeNull();
});

test("E6: a held card warns in E2's register for local takes only, and never disarms", () => {
  const held = {
    ...SHEET,
    gpu: { total_bytes: 24 * 1024 ** 3, free_bytes: 16 * 1024 ** 3, comfy_bytes: 2 * 1024 ** 3, foreign_bytes: Math.round(5.8 * 1024 ** 3), name: "card" },
    gpu_held: true,
  };
  expect(vramWarning(held, 3)).toBe(
    "SOMETHING ELSE HOLDS 5.8 GB OF THE 24 GB CARD — YOUR 3 LOCAL TAKES MAY NOT GET A TURN UNTIL IT LETS GO. NOTHING IS BLOCKED HERE — RUN AS IS, OR CLOSE IT FIRST.",
  );
  expect(vramWarning(held, 1)).toContain("YOUR 1 LOCAL TAKE MAY");
  expect(vramWarning(held, 0)).toBeNull(); // nothing touches the GPU
  expect(vramWarning({ ...held, gpu_held: false }, 3)).toBeNull();
  expect(vramWarning({ ...held, gpu: null, gpu_held: false }, 3)).toBeNull(); // the card couldn't be read
  expect(vramWarning(undefined, 3)).toBeNull();
});
