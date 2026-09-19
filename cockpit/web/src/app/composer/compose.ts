// Pure composer derivations: vitest exercises these
// without the query layer. Copy rules: cost lines are pre-generation
// ESTIMATES (the billed truth is per-response usage.cost); disabled
// reasons are plain words, mirroring the server's own 422 copy where one
// exists; the run bar says the one-GPU truth in counts and observed time,
// never a predicted total (law 17, law 13).
import type {
  Preset,
  ProviderModel,
  Headroom,
  QueueView,
} from "../../types";
import { elapsedLabel } from "../batch/grid";

export const MAX_TAKES = 16; // server cap in run_turn — the whole turn

// Which recipe a run needs is decided by its parents: a source
// image means the preset's img2img graph, none means txt2img. A preset
// without that recipe is DIMMED AND LISTED, never filtered out (law 12).
export type Recipe = "txt2img" | "img2img";

export function recipeFor(parents: readonly string[]): Recipe {
  return parents.length > 0 ? "img2img" : "txt2img";
}

export function hasRecipe(p: Preset, recipe: Recipe): boolean {
  return p.workflows[recipe] !== undefined;
}

export function noRecipeReason(recipe: Recipe): string {
  return `no ${recipe} recipe committed`;
}

/** Takes per stepped row — preset id or model id → count, zero rows
 *  absent. The roster's whole answer to "how many". */
export type Takes = ReadonlyMap<string, number>;

export function sumTakes(takes: Takes): number {
  let n = 0;
  for (const v of takes.values()) n += v;
  return n;
}

// External estimate: each stepped model's price times its takes, plus how
// many stepped models have no price at all. An unlisted price is stated,
// never guessed.
export function extEstimate(
  models: ProviderModel[],
  external: Takes,
): { known: number; unlisted: number } {
  let known = 0;
  let unlisted = 0;
  for (const m of models) {
    const n = external.get(m.id) ?? 0;
    if (n <= 0) continue;
    if (typeof m.price_usd_per_image === "number")
      known += m.price_usd_per_image * n;
    else unlisted += 1;
  }
  return { known, unlisted };
}

/** The footer's first line: total slots and the
 *  spend — the money only where money is — and the LOCAL ONLY switch
 *  named when it is on, since it is what made the spend zero. */
export function slotsLine(
  total: number,
  est: { known: number; unlisted: number },
  localOnly = false,
): string {
  const parts = [`${total} SLOT${total === 1 ? "" : "S"}`];
  parts.push(est.known > 0 ? `~$${est.known.toFixed(2)} THIS RUN` : "NO SPEND");
  if (est.unlisted > 0)
    parts.push(`${est.unlisted} PRICE${est.unlisted > 1 ? "S" : ""} UNLISTED`);
  if (localOnly) parts.push("LOCAL ONLY");
  return parts.join(" · ");
}

/** The plain sentence about how the batch will actually run (law 13):
 *  locals take turns on the one GPU, externals leave at once. Counts
 *  only — a wall-clock here would be this card's guess (law 17). */
export function gpuSentence(local: number, external: number): string {
  const locals =
    local === 1
      ? "1 local runs alone on the single GPU"
      : `${local} local run one after another on the single GPU`;
  const externals =
    external === 1
      ? "1 external goes out at once and usually lands first"
      : `the ${external} externals go out at once and usually land first`;
  if (local > 0 && external > 0) return `${locals}; ${externals}.`;
  if (local > 0) return `${locals}.`;
  if (external > 0)
    return `${externals[0].toUpperCase()}${externals.slice(1)}; nothing touches the GPU.`;
  return "Step an engine up — one number per row, never one for the run.";
}

/** What a new Run's locals queue behind, said in observed time from
 *  the live queue (the running take's elapsed is the server's clock, never
 *  an estimate). Null when nothing local is asked for, or the GPU is idle. */
export function busyLine(
  q: QueueView | undefined,
  local: number,
): string | null {
  if (q === undefined || local === 0 || q.depth === 0) return null;
  const running = q.lanes.local.slots.find((v) => v.status === "running");
  const queued = q.lanes.local.slots.filter((v) => v.status === "queued").length;
  let head = "GPU busy";
  if (running !== undefined) {
    head += ` — ${running.engine.toUpperCase()} running`;
    if (running.elapsed_ms !== null)
      head += ` ${elapsedLabel(running.elapsed_ms).toLowerCase()} so far`;
  }
  if (queued > 0) head += `, ${queued} queued`;
  return (
    `${head}; your ${local} local queue${local === 1 ? "s" : ""} behind ` +
    `${q.depth === 1 ? "it" : "them"}.`
  );
}

// The costed action carries its price on the button face (law 6), and a
// Run while running says what it queues behind, never replaces.
export function runLabel(
  total: number,
  est: { known: number },
  behind = 0,
): string {
  const price = est.known > 0 ? ` · ~$${est.known.toFixed(2)}` : "";
  const queue = behind > 0 ? ` · queues behind ${behind}` : "";
  return `Run · ${total}${price}${queue}`;
}

// Why Run is disabled, in plain words — null means it can fire. When no
// engine can run this recipe, the reason is the ROSTER's, not the artist's:
// say that nothing here can run it rather than telling them to step up
// something that isn't there (law 12). `localReady: null` = the roster
// hasn't arrived, which is not the same as it being empty — claiming
// "nothing can refine" during a load is exactly the guess law 12 forbids.
export function whyNot(args: {
  prompt: string;
  localTakes: number;
  external: number;
  recipe: Recipe;
  localReady: number | null;
  /** E6: the drive can't hold the takes. The sentence, or null. */
  disk?: string | null;
}): string | null {
  const total = args.localTakes + args.external;
  if (total < 1) {
    if (args.localReady === null) return "The engine roster isn't loaded yet.";
    if (args.localReady === 0)
      return (
        `No local engine has an ${args.recipe} recipe committed, so nothing ` +
        "here can run this yet."
      );
    return "A run needs at least one take.";
  }
  if (total > MAX_TAKES) return `A run caps at ${MAX_TAKES} takes.`;
  if (args.prompt.trim().length === 0) return "Needs a prompt.";
  if (args.disk) return args.disk;
  return null;
}

// ── E6: free VRAM and free disk at Run ──────────────────────────────────────
//
// The server's headroom sheet states the facts — the card as it is now
// (ComfyUI's reading; null when it can't say), the staging drive's free
// bytes, and this card's own bytes-per-pixel — and the composer does the
// arithmetic from the frames it knows. Disk DISARMS with the numbers
// (E6: what the slots need against what the drive has free); a foreign
// hold on the card WARNS and never disarms: without a
// per-card profile (law 17) whether a take still fits beside the hold is a
// guess, and the runtime's C2 treatment owns what happens if it doesn't.

/** The pixels each slot will write: a local take's frame from its raws
 *  (a swept take's own override wins), else the stand-in the server also
 *  assumes — an external model renders at the provider's default, a
 *  refine at its source's size. */
export const ASSUMED_PX = 1024 * 1024;

export function slotAreas(
  local: readonly {
    takes: number;
    inputs: Record<string, unknown>;
    take_overrides: Record<string, unknown>[];
  }[],
  externalTakes: number,
): number[] {
  const out: number[] = [];
  for (const b of local) {
    for (let i = 0; i < b.takes; i++) {
      const raw = { ...b.inputs, ...(b.take_overrides[i] ?? {}) };
      const w = raw.width;
      const h = raw.height;
      out.push(
        typeof w === "number" && typeof h === "number" && w > 0 && h > 0
          ? w * h
          : ASSUMED_PX,
      );
    }
  }
  for (let i = 0; i < externalTakes; i++) out.push(ASSUMED_PX);
  return out;
}

export function fmtBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(n / (1 << 20)))} MB`;
}

/** The disarm line, or null when the drive can hold the run (or the sheet
 *  hasn't arrived — silence is not a claim). Mirrors the server's 422. */
export function diskWhy(h: Headroom | undefined, areas: readonly number[]): string | null {
  if (h === undefined || areas.length === 0) return null;
  const needed = areas.reduce((t, a) => t + a, 0) * h.bytes_per_px;
  if (h.disk_free_bytes >= needed + h.disk_margin_bytes) return null;
  const n = areas.length;
  return (
    `${n} slot${n === 1 ? "" : "s"} need about ${fmtBytes(needed)} and the ` +
    `cockpit's drive has ${fmtBytes(h.disk_free_bytes)} free — free up about ` +
    `${fmtBytes(needed + h.disk_margin_bytes - h.disk_free_bytes)} first.`
  );
}

/** The card-held warning for a run with local takes, in E2's register —
 *  said, never gating: Run stays armed. Null when the card reads clear,
 *  can't be read, or nothing local is asked for. */
export function vramWarning(h: Headroom | undefined, localTakes: number): string | null {
  if (h === undefined || h.gpu === null || !h.gpu_held || localTakes < 1) return null;
  const held = (h.gpu.foreign_bytes / (1 << 30)).toFixed(1);
  const total = Math.round(h.gpu.total_bytes / (1 << 30));
  return (
    `SOMETHING ELSE HOLDS ${held} GB OF THE ${total} GB CARD — YOUR ` +
    `${localTakes} LOCAL TAKE${localTakes === 1 ? "" : "S"} MAY NOT GET A TURN ` +
    "UNTIL IT LETS GO. NOTHING IS BLOCKED HERE — RUN AS IS, OR CLOSE IT FIRST."
  );
}

// The external lane while a source image is attached: OpenRouter image
// models take a text brief, not our pixels. Stated once, on the lane.
export const EXTERNAL_NO_SOURCE =
  "External models take a text brief only — they can't read this source image.";

// The external lane under the LOCAL ONLY switch: the rows stay
// listed and inert with the one cause on the lane; the request carries the
// switch and the server refuses a run that pairs it with an external batch.
export const EXTERNAL_LOCAL_ONLY =
  "LOCAL ONLY is on: no take leaves this machine. Flip the switch in the top bar to send takes out.";

export function errText(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  return "The run didn't start — the server refused it.";
}
