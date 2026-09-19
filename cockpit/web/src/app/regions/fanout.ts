// The fan-out half of the region surface: the mask-capable roster with a
// takes stepper per engine, the RegionRun the surface posts, the disarm
// reasons, and the attempt panes' pure copy. No React; vitest exercises
// everything but the canvas export directly.
//
// Rules carried here:
//   law 12 — badge, never filter: an incapable preset stays listed
//        with a dashed 0 and its reason in words (the gating negative
//        lives on THIS roster's reason line); externals are one
//        structural lane line, not per-model rows (the wire has no field
//        for them).
//   every string is curated static text. Per-pane captions that judge
//        an attempt ("held the fuller…") are something a no-LLM
//        cockpit cannot write; panes carry facts only.
import type { Preset, Engines } from "../../types";
import { NO_RECORD, UNVERIFIED } from "../composer/badges";
import { engineOf } from "../composer/dials";
import {
  exportMaskB64,
  hasInk,
  type RegionAction,
  type RegionDraft,
} from "./model";

/** Client mirror of regionrun.check_region_capable — the recipes a preset
 *  must have committed to take region turns (regions.PASS_CAPABILITIES).
 *  Display truth only: the server re-checks and refuses in words. */
export const REGION_RECIPE_KEYS = [
  "inpaint_modify",
  "inpaint_replace",
  "remove",
] as const;

export type RosterRow = {
  preset: Preset;
  capable: boolean;
  /** The dashed 0's reason, incapable rows only (the badge grammar). */
  reason: string | null;
  /** The capable row's state word: null = verified plain; UNVERIFIED /
   *  NO CAPABILITY RECORD worn beside the name (law 12). */
  stateWord: string | null;
};

export const EXTERNALS_LINE =
  "EXTERNALS DON'T TAKE REGION TURNS — REGION RUNS STAY ON THE LOCAL RAILS";

export const NO_RECIPES_REASON = "NO MASK RECIPES COMMITTED";

/** The roster: every local image preset, capable first, none hidden.
 *  Capable = the region recipes are committed on the preset (the server's
 *  own gate); the matrix supplies the verification state on top. */
export function regionRoster(
  presets: readonly Preset[],
  engines: Engines | undefined,
): RosterRow[] {
  const rows = presets
    .filter((p) => p.modality === "image")
    .map((p): RosterRow => {
      const missing = REGION_RECIPE_KEYS.filter(
        (k) => p.workflows[k] === undefined,
      );
      if (missing.length > 0)
        return { preset: p, capable: false, reason: NO_RECIPES_REASON, stateWord: null };
      const row =
        engines === undefined
          ? undefined
          : engines.engines.find((e) => e.id === engineOf(p));
      const stateWord =
        engines === undefined
          ? null // matrix not arrived — silence is not a claim
          : row === undefined
            ? NO_RECORD
            : row.capabilities.mask?.state === "unverified"
              ? UNVERIFIED
              : null;
      return { preset: p, capable: true, reason: null, stateWord };
    });
  return [...rows.filter((r) => r.capable), ...rows.filter((r) => !r.capable)];
}

/** Why FAN OUT is disarmed, in words — null means armed. Mirrors the
 *  composer's runWhy register; the server re-checks every one. */
export function fanGate(args: {
  drafts: readonly RegionDraft[];
  counts: ReadonlyMap<string, number>;
  rosterReady: boolean;
  imageReady: boolean;
}): string | null {
  if (!args.rosterReady) return "The engine roster isn't loaded yet.";
  if (!args.imageReady) return "The take's pixels haven't loaded yet.";
  if (args.drafts.length === 0) return "Draw at least one region first.";
  const bare = args.drafts.findIndex((d) => !hasInk(d));
  if (bare !== -1)
    return `R${bare + 1} has no ink yet — draw it or remove the row.`;
  let takes = 0;
  for (const n of args.counts.values()) takes += n;
  if (takes === 0) return "Step at least one engine above zero takes.";
  return null;
}

/** The run bar's one line. Counts only — durations are observed elsewhere,
 *  never predicted (law 17). */
export function fanBarLine(counts: ReadonlyMap<string, number>): string {
  let takes = 0;
  let engines = 0;
  for (const n of counts.values())
    if (n > 0) {
      takes += n;
      engines += 1;
    }
  const t = takes === 1 ? "1 TAKE" : `${takes} TAKES`;
  const e = engines === 1 ? "1 ENGINE" : `${engines} ENGINES`;
  return `${t} · ${e} · LOCALS IN SERIES, ONE GPU`;
}

/** The request body (RegionRun): drafts in creation order — list order
 *  IS z-order (nd3-v1 later-wins) — prompts only where the action takes
 *  one, masks exported flat (white on black) at natural size. Seed policy
 *  is random for now (the surface has no seed control). */
export function buildRegionRun(args: {
  drafts: readonly RegionDraft[];
  natural: { w: number; h: number };
  counts: ReadonlyMap<string, number>;
  prompt: string;
  source: { id: string; x: number | null; y: number | null };
}): {
  parents: string[];
  prompt: string | null;
  regions: {
    name: string;
    action: RegionAction;
    prompt: string | null;
    mask_b64: string;
  }[];
  local: { preset_id: string; takes: number; inputs: Record<string, never> }[];
  seed: { mode: "random" };
  anchor?: { x: number; y: number };
} {
  const prompt = args.prompt.trim();
  const body = {
    parents: [args.source.id],
    prompt: prompt === "" ? null : prompt,
    regions: args.drafts.map((d) => ({
      name: d.name,
      action: d.action,
      prompt:
        (d.action === "modify" || d.action === "replace") &&
        d.prompt.trim() !== ""
          ? d.prompt.trim()
          : null,
      mask_b64: exportMaskB64(d, args.natural),
    })),
    local: [...args.counts]
      .filter(([, takes]) => takes > 0)
      .map(([preset_id, takes]) => ({ preset_id, takes, inputs: {} as Record<string, never> })),
    seed: { mode: "random" as const },
  };
  return args.source.x !== null && args.source.y !== null
    ? { ...body, anchor: { x: args.source.x, y: args.source.y } }
    : body;
}

// --- the attempts panel's curated copy -----------------------------------

export function attemptsHeader(n: number): string {
  return n === 1 ? "1 ATTEMPT · SAME MASK" : `${n} ATTEMPTS · SAME MASK`;
}

export const ATTEMPTS_HINT =
  "D DIFF OVERLAY · R REGION-ONLY VIEW · ⇧CLICK KEEPS SEVERAL";

export const STAY_LINE = "THE REST STAY ON THE CANVAS AS A BRANCH";
export const IRREVERSIBLE_LINE =
  "NOTHING HERE IS IRREVERSIBLE — PROMOTE IS ELSEWHERE";

export type PaneView = "plain" | "diff" | "region";

/** The commit bar's summary of the picked panes, facts only. */
export function continueLabel(
  picked: readonly { engine: string; label: string }[],
): string {
  if (picked.length === 0) return "PICK AN ATTEMPT TO CONTINUE FROM";
  if (picked.length === 1)
    return `CONTINUE FROM ${picked[0].engine.toUpperCase()} · ${picked[0].label}`;
  return `CONTINUE FROM ${picked.length} TAKES`;
}
