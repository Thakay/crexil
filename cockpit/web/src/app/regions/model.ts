// The region surface's pure core.
// Draft model, tinting order, the precedence rule in words, the honest
// mask-capability readout, and mask rasterisation. No React; vitest
// exercises everything but the canvas rasteriser directly.
//
// Two rules carry the file:
//   `nd3-v1` — overlap precedence is KEEP > DELETE > REPLACE > MODIFY.
//        THIS order is binding (DELETE sits between KEEP and REPLACE, never
//        left out); model.test.ts pins it.
//   law 12 — the capability line is derived from the matrix with its
//        verification state on it; unverified wears the word, an absent
//        matrix prints nothing (silence is not a claim).
// Every string here is curated static text.
import type { Engine, Engines } from "../../types";
import { MATRIX_UNREACHABLE, UNVERIFIED } from "../composer/badges";

export type RegionAction = "keep" | "modify" | "replace" | "delete";

/** Select order: mildest reading first. */
export const ACTIONS: readonly RegionAction[] = [
  "keep",
  "modify",
  "replace",
  "delete",
];

/** One stroke/shape in NATURAL image coordinates (committed at stroke
 *  end), so a commit survives the surface reopening at a different fit
 *  scale. `size` is the brush diameter in natural px. Strokes are consumed
 *  at commit: rasterised into the draft's one bitmap, never kept (an
 *  append-only stroke list would grow without bound and stack
 *  per-shape opacity toward opaque over a BINARY mask). */
export type Ink =
  | { tool: "brush" | "erase"; points: number[]; size: number }
  | { tool: "rect"; x: number; y: number; w: number; h: number }
  | { tool: "lasso"; points: number[] };

export type DrawTool = "brush" | "rect" | "lasso" | "erase";

/** A draft's whole ink is ONE offscreen bitmap at natural size —
 *  white-on-transparent; erase is destination-out, so it REMOVES. The
 *  canvas mutates in place: `inkRev` is the re-render trigger. `inked`
 *  rides "any draw stroke committed" (the list row's INK mark); the
 *  binding emptiness check stays server-side (regions.is_empty, 422 in
 *  words). */
export type RegionDraft = {
  key: string;
  name: string;
  action: RegionAction;
  prompt: string;
  mask: HTMLCanvasElement | null;
  inkRev: number;
  inked: boolean;
  visible: boolean;
};

/** Region tint per action, in the app palette: keep = good, modify =
 *  accent, replace = waiting/money's gold, delete = the irreversible red. */
export const ACTION_COLORS: Record<RegionAction, string> = {
  keep: "#5FC08C",
  modify: "#7FB2FF",
  replace: "#D2A24C",
  delete: "#DB6A57",
};

/** The rule, verbatim where it matters (surfaced in the UI). The
 *  binding order is the `nd3-v1` policy's. */
export const PRECEDENCE_LINE =
  "WHERE REGIONS OVERLAP: KEEP BEATS DELETE BEATS REPLACE BEATS MODIFY · " +
  "ND3-V1 — LATER WINS WITHIN AN ACTION";
export const UNCLAIMED_LINE =
  "UNMASKED AREA REGENERATES UNDER THE TURN PROMPT ONLY WHEN A KEEP EXISTS";
export const SAME_REGIONS_LINE =
  "EVERY ENGINE GETS THE SAME REGIONS AND THE SAME PRECEDENCE";

/** The surface's one summon line. */
export const DRAW_HINT = "DRAW A REGION, THEN DECLARE WHAT IT MEANS";

/** Drafts stay browser-side compose state until a run consumes them
 *  (masks ride the request and become lineage rows, the region-turns
 *  route's contract); the surface says so beside the capability line. */
export const DRAFTS_LINE =
  "REGIONS YOU DRAW STAY WITH THIS TAKE FOR THIS BROWSER SESSION — " +
  "a fan out records them with its turn";

/** The plain-words effect per action — the select's own lines. */
export function actionLine(action: RegionAction): string {
  switch (action) {
    case "keep":
      return "KEEP — preserved verbatim, byte for byte";
    case "modify":
      return "MODIFY — reworked under its own prompt, or the turn's";
    case "replace":
      return "REPLACE — discarded and regenerated under its prompt";
    case "delete":
      return "DELETE — removed, filled from what's around it";
  }
}

/** Why a KEEP or DELETE row has no prompt field. */
export function promptlessLine(action: "keep" | "delete"): string {
  return action === "keep"
    ? "LOCKED · NO PROMPT — it stays exactly as it is"
    : "NO PROMPT — the fill comes from the surrounding image";
}

/** R1, R2…: the row headings, by list position. */
export function regionLabel(index: number): string {
  return `R${index + 1}`;
}

/** A fresh draft's name. */
export function nextName(drafts: readonly RegionDraft[]): string {
  return `region ${drafts.length + 1}`;
}

export function hasInk(draft: RegionDraft): boolean {
  return draft.inked;
}

/** Paint order for the tinted preview: weakest action first so the
 *  strongest paints on top — the per-pixel claim the policy resolves, told
 *  visually. Within an action, later-created paints later (later wins). */
const PAINT_TIER: Record<RegionAction, number> = {
  modify: 0,
  replace: 1,
  delete: 2,
  keep: 3,
};

export function layerOrder(
  drafts: readonly RegionDraft[],
): readonly RegionDraft[] {
  return [...drafts].sort(
    (a, b) =>
      PAINT_TIER[a.action] - PAINT_TIER[b.action] ||
      drafts.indexOf(a) - drafts.indexOf(b),
  );
}

/** Fit a natural image size into a viewport, never upscaling. */
export function fitScale(
  natural: { w: number; h: number },
  viewport: { w: number; h: number },
): number {
  return Math.min(viewport.w / natural.w, viewport.h / natural.h, 1);
}

// --- the honest capability readout (law 12) --------------------------

function maskCapable(e: Engine): boolean {
  return e.capabilities.mask?.value === true;
}

function maskUnverified(e: Engine): boolean {
  return e.capabilities.mask?.state === "unverified";
}

/** The line under the header: who can take these regions, read from the
 *  matrix. Locals only: the fan-out roster is where every engine
 *  stands with its dashed 0 and reason; this line is the summary.
 *  `null` = matrix not arrived yet, print nothing. */
export function maskCapabilityLine(
  engines: Engines | undefined,
  unreachable: boolean,
): string | null {
  if (unreachable) return MATRIX_UNREACHABLE;
  if (engines === undefined) return null;
  const locals = engines.engines.filter(
    (e) => e.lane === "local" && e.payload === "image",
  );
  if (locals.length === 0) return null;
  const capable = locals.filter(maskCapable);
  if (capable.length === 0)
    return `NONE OF YOUR ${locals.length} LOCAL IMAGE ENGINES CAN MASK YET`;
  const names = capable
    .map((e) =>
      maskUnverified(e)
        ? `${e.name.toUpperCase()} ${UNVERIFIED}`
        : e.name.toUpperCase(),
    )
    .join(" · ");
  return (
    `${capable.length} OF YOUR ${locals.length} LOCAL IMAGE ENGINES ` +
    `CAN MASK · ${names}`
  );
}

// --- mask rasterisation ---------------------------------------------------
// The draft's bitmap is white-on-TRANSPARENT: a draw paints opaque white,
// an erase punches alpha out (destination-out) — so erase truly removes and
// nothing accumulates. Every canvas op guards a null 2D context (jsdom has
// none); vitest stubs getContext where raster behavior is asserted.

/** Apply one ink move to the draft bitmap's context (natural coords). */
export function applyInk(ctx: CanvasRenderingContext2D, ink: Ink): void {
  const erase = ink.tool === "erase";
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  ctx.fillStyle = ctx.strokeStyle = "#fff";
  if (ink.tool === "rect") {
    ctx.fillRect(ink.x, ink.y, ink.w, ink.h);
  } else if (ink.tool === "lasso") {
    const p = ink.points;
    if (p.length < 6) return; // fewer than 3 points encloses nothing
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]);
    ctx.closePath();
    ctx.fill();
  } else {
    // brush / erase: a round-capped polyline; a single point still dots
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = ink.size;
    const p = ink.points;
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    if (p.length === 2) ctx.lineTo(p[0], p[1]);
    for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]);
    ctx.stroke();
  }
  ctx.globalCompositeOperation = "source-over";
}

/** Rasterise one committed stroke into the draft's bitmap, creating it at
 *  natural size on first ink. Returns the canvas (the store keeps it and
 *  bumps `inkRev` — the canvas itself mutates in place). */
export function rasterize(
  mask: HTMLCanvasElement | null,
  ink: Ink,
  natural: { w: number; h: number },
): HTMLCanvasElement {
  const c = mask ?? document.createElement("canvas");
  if (mask === null) {
    c.width = natural.w;
    c.height = natural.h;
  }
  const ctx = c.getContext("2d");
  if (ctx !== null) applyInk(ctx, ink);
  return c;
}

/** The run payload: the draft's bitmap flattened onto black — the
 *  white-on-black PNG regions.load_mask binarizes — as bare base64
 *  (`RegionSpec.mask_b64`). */
export function exportMaskB64(
  draft: RegionDraft,
  natural: { w: number; h: number },
): string {
  const c = document.createElement("canvas");
  c.width = natural.w;
  c.height = natural.h;
  const ctx = c.getContext("2d");
  if (ctx !== null) {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, c.width, c.height);
    if (draft.mask !== null) ctx.drawImage(draft.mask, 0, 0);
  }
  const url = c.toDataURL("image/png");
  return url.slice(url.indexOf(",") + 1);
}

/** The display tint: the bitmap recoloured in one composite (white where
 *  inked → the action's colour, transparent elsewhere) — one Konva.Image
 *  per region, so same-region strokes can never stack toward opaque. */
export function tintCanvas(
  mask: HTMLCanvasElement,
  color: string,
): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = mask.width;
  c.height = mask.height;
  const ctx = c.getContext("2d");
  if (ctx !== null) {
    ctx.drawImage(mask, 0, 0);
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, c.width, c.height);
  }
  return c;
}
