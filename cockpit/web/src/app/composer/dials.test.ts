// Dial resolution, pure. The table rows here are the real ones from
// configs/dials.json (the server's test_dials.py pins them against the
// presets); the presets carry the real input specs. What's under test is
// the mapping's three promises: position 50 = the recipe, a row that
// doesn't transmit is a badge with its reason (law 12), and a sweep walks one
// key with the seed held.
import { expect, test } from "vitest";
import type { InputSpec, Preset, Dials, Engines } from "../../types";
import {
  HELD_WHY,
  askedFrame,
  clearAdvanced,
  foldChanged,
  foldChangedLine,
  foldHead,
  foldInputs,
  foldRowLine,
  foldRows,
  frameBounds,
  frameHead,
  frameLine,
  frameLines,
  frameWarning,
  frameWithHold,
  interpolate,
  rawLine,
  rawLines,
  readState,
  requestInputs,
  resolveFrame,
  resolveSlider,
  seedBadges,
  seedPolicy,
  setAdvanced,
  snap,
  sweepLine,
  sweepLines,
  sweepPositions,
  withHold,
  type DialState,
  type SliderId,
} from "./dials";

const EXT = { transmits: false, note: "External turns take the txt2img brief only — the slot follows the words." };

export const TABLE: Dials = {
  updated: "2026-08-13",
  position: { min: 0, max: 100, default: 50 },
  dials: [
    {
      id: "anchor-hold",
      label: "ANCHOR HOLD",
      effect: "How hard it holds your anchor. Higher stays closer to the reference; past 80 it mostly copies it.",
      engines: {
        "flux2-klein-4b": { transmits: false, note: "No committed klein img2img graph — image-anchored turns run on the SDXL rails today." },
        "z-image-turbo": { transmits: false, note: "Turbo is txt2img-only in this product — Base carries img2img for the family." },
        "z-image-base": { transmits: true, note: "Inverted.", input: "denoise", anchors: [[0, 1.0], [50, 0.55], [100, 0.1]], step: 0.05 },
        "sdxl-juggernaut-xi": { transmits: true, note: "Inverted.", input: "denoise", anchors: [[0, 1.0], [50, 0.5], [100, 0.1]], step: 0.05 },
        external: EXT,
      },
    },
    {
      id: "prompt-grip",
      label: "PROMPT GRIP",
      effect: "How literally it obeys the words. Low wanders off; high goes stiff and over-contrasted.",
      engines: {
        "flux2-klein-4b": { transmits: true, note: "", input: "guidance", anchors: [[0, 1.0], [50, 1.0], [100, 5.0]], step: 0.1 },
        "z-image-turbo": { transmits: false, note: "Turbo runs cfg 1.0 pinned in the graph (official card: CFG unsupported on the distill) — the dial doesn't transmit; the slot follows the words at its design point." },
        "z-image-base": { transmits: true, note: "", input: "cfg", anchors: [[0, 2.0], [50, 4.0], [100, 7.0]], step: 0.1 },
        "sdxl-juggernaut-xi": { transmits: true, note: "", input: "cfg", anchors: [[0, 2.5], [50, 5.0], [100, 9.0]], step: 0.5 },
        external: EXT,
      },
    },
    {
      id: "cook-time",
      label: "COOK TIME",
      effect: "How long each take runs. More is slower, not better — past the mark it stops changing.",
      engines: {
        "flux2-klein-4b": { transmits: true, note: "", input: "steps", anchors: [[0, 2], [50, 4], [100, 8]], step: 1 },
        "z-image-turbo": { transmits: true, note: "", input: "steps", anchors: [[0, 4], [50, 9], [100, 16]], step: 1 },
        "z-image-base": { transmits: true, note: "", input: "steps", anchors: [[0, 18], [50, 36], [100, 50]], step: 1 },
        "sdxl-juggernaut-xi": { transmits: true, note: "", input: "steps", anchors: [[0, 12], [50, 28], [100, 48]], step: 1 },
        external: EXT,
      },
    },
    {
      id: "frame",
      kind: "frame",
      label: "FRAME",
      effect: "Bigger costs GPU time, not money.",
      aspects: ["1:1", "3:4", "4:3", "16:9"],
      default: { width: 1024, height: 1024 },
      engines: {
        "flux2-klein-4b": { transmits: true, note: "", inputs: ["width", "height"] },
        "z-image-turbo": { transmits: true, note: "", inputs: ["width", "height"] },
        "z-image-base": { transmits: true, note: "", inputs: ["width", "height"] },
        "sdxl-juggernaut-xi": { transmits: true, note: "", inputs: ["width", "height"] },
        external: EXT,
      },
    },
    {
      id: "seed",
      kind: "seed",
      label: "SEED",
      effect: "Locked, so a dial change is the only thing that moves. Unlock and every take is a fresh roll.",
      engines: {
        "flux2-klein-4b": { transmits: true, note: "", input: "seed" },
        "z-image-turbo": { transmits: true, note: "", input: "seed" },
        "z-image-base": { transmits: true, note: "", input: "seed" },
        "sdxl-juggernaut-xi": { transmits: true, note: "", input: "seed" },
        external: { transmits: false, note: "Not honoured unless the model's providers.json pin says supports_seed." },
      },
    },
    {
      id: "sweep-one-dial",
      kind: "meta",
      label: "SWEEP ONE DIAL",
      effect: "Spends four of your slots walking one value, everything else held. This is how you learn a dial.",
      sweep_slots: 4,
      targets: ["anchor-hold", "prompt-grip", "cook-time"],
      engines: {},
    },
  ],
};

const SIZE = [
  { key: "width", type: "int" as const, default: 1024, min: 512, max: 2048, step: 64 },
  { key: "height", type: "int" as const, default: 1024, min: 512, max: 2048, step: 64 },
];

type Spec = {
  key: string;
  type?: InputSpec["type"];
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  capabilities?: InputSpec["capabilities"];
};

function preset(
  id: string,
  family: string,
  workflows: Record<string, string>,
  inputs: Spec[],
): Preset {
  const specs: Spec[] = [
    { key: "prompt", type: "text" },
    { key: "seed", type: "seed" },
    ...inputs,
    ...SIZE,
  ];
  return {
    id,
    display_name: id,
    description: "",
    modality: "image",
    workflows,
    default_candidates: 4,
    model_family: family,
    inputs: specs.map((i): InputSpec => ({ ...i, supported: true })),
  };
}

export const ZIMAGE = preset("raw-zimage", "z-image-base", { txt2img: "z", img2img: "zi" }, [
  { key: "steps", type: "int", default: 36, min: 8, max: 60 },
  { key: "cfg", type: "float", default: 4.0, min: 1.0, max: 10.0, step: 0.5 },
  { key: "denoise", type: "float", default: 0.55, min: 0.05, max: 1.0, step: 0.05, capabilities: ["img2img"] },
]);
export const SDXL = preset("raw-sdxl", "sdxl-juggernaut-xi", { txt2img: "s", img2img: "si" }, [
  { key: "steps", type: "int", default: 28, min: 1, max: 60 },
  { key: "cfg", type: "float", default: 5.0, min: 1.0, max: 15.0, step: 0.5 },
  { key: "denoise", type: "float", default: 0.5, min: 0.05, max: 1.0, step: 0.05, capabilities: ["img2img"] },
]);
export const TURBO = preset("sketch-zimage", "z-image-turbo", { txt2img: "t" }, [
  { key: "steps", type: "int", default: 9, min: 4, max: 16 },
]);
export const KLEIN = preset("raw-flux2", "flux2-klein-4b", { txt2img: "f" }, [
  { key: "steps", type: "int", default: 4, min: 1, max: 32 },
  { key: "guidance", type: "float", default: 1.0, min: 1.0, max: 10.0, step: 0.1 },
]);

const dial = (id: string) => TABLE.dials.find((d) => d.id === id)!;

function state(over: Partial<DialState> = {}): DialState {
  return {
    "anchor-hold": 50,
    "prompt-grip": 50,
    "cook-time": 50,
    frame: { aspect: "1:1", size: 1024 },
    seed: { locked: false, value: 44182 },
    advanced: {},
    ...over,
  };
}

test("position 50 sends every engine its own recipe value", () => {
  const grip = dial("prompt-grip");
  expect(resolveSlider(grip, ZIMAGE, "txt2img", 50)).toEqual({ kind: "sends", key: "cfg", value: 4 });
  expect(resolveSlider(grip, SDXL, "txt2img", 50)).toEqual({ kind: "sends", key: "cfg", value: 5 });
  expect(resolveSlider(grip, KLEIN, "txt2img", 50)).toEqual({ kind: "sends", key: "guidance", value: 1 });
  const cook = dial("cook-time");
  expect(resolveSlider(cook, ZIMAGE, "txt2img", 50)).toEqual({ kind: "sends", key: "steps", value: 36 });
  expect(resolveSlider(cook, TURBO, "txt2img", 50)).toEqual({ kind: "sends", key: "steps", value: 9 });
  const hold = dial("anchor-hold");
  expect(resolveSlider(hold, ZIMAGE, "img2img", 50)).toEqual({ kind: "sends", key: "denoise", value: 0.55 });
  expect(resolveSlider(hold, SDXL, "img2img", 50)).toEqual({ kind: "sends", key: "denoise", value: 0.5 });
});

test("between anchors is linear, snapped to the row's step at its own precision", () => {
  const zg = dial("prompt-grip").engines["z-image-base"];
  expect(interpolate(zg, 0)).toBe(2);
  expect(interpolate(zg, 100)).toBe(7);
  expect(interpolate(zg, 75)).toBe(5.5);
  expect(interpolate(zg, 66)).toBe(5); // 4.96 → 5.0, printed as 5
  const sg = dial("prompt-grip").engines["sdxl-juggernaut-xi"];
  expect(interpolate(sg, 60)).toBe(6); // 5.8 snaps to the 0.5 step
  expect(interpolate(dial("cook-time").engines["flux2-klein-4b"], 62)).toBe(5);
  expect(snap(0.372, 0.05)).toBe(0.35);
  expect(snap(4.96, 0.1)).toBe(5);
  expect(snap(22.88, 1)).toBe(23);
  // out-of-range positions clamp to the band's ends
  expect(interpolate(zg, 140)).toBe(7);
  expect(interpolate(zg, -3)).toBe(2);
});

test("anchor hold is inverted — raws fall as the hold rises, floor 0.1 not 0", () => {
  const zh = dial("anchor-hold").engines["z-image-base"];
  expect(interpolate(zh, 0)).toBe(1);
  expect(interpolate(zh, 80)).toBe(0.3); // "past 80 it mostly copies it"
  expect(interpolate(zh, 100)).toBe(0.1);
});

test("a row that doesn't transmit is a badge with its reason; a root run has no anchor (law 12)", () => {
  const grip = dial("prompt-grip");
  const turbo = resolveSlider(grip, TURBO, "txt2img", 80);
  expect(turbo.kind).toBe("badge");
  if (turbo.kind === "badge") expect(turbo.why).toMatch(/cfg 1\.0 pinned/);
  // the sketch preset stays picked — it just follows its recipe
  const hold = resolveSlider(dial("anchor-hold"), ZIMAGE, "txt2img", 80);
  expect(hold).toEqual({ kind: "inert", why: "inert on a root run — there is no anchor to hold" });
  // an engine the table doesn't know is said, not guessed
  const stranger = { ...KLEIN, id: "mystery", model_family: "mystery-9b", display_name: "Mystery" };
  const r = resolveSlider(grip, stranger, "txt2img", 50);
  expect(r.kind).toBe("badge");
  if (r.kind === "badge") expect(r.why).toMatch(/no row in the dial table/);
  expect(resolveSlider(grip, null, "txt2img", 50)).toEqual({ kind: "none" });
});

test("the preset's own bounds clamp the band — a narrower recipe wins", () => {
  const tight: Preset = {
    ...KLEIN,
    inputs: KLEIN.inputs.map((i) => (i.key === "steps" ? { ...i, max: 6 } : i)),
  };
  expect(resolveSlider(dial("cook-time"), tight, "txt2img", 100)).toEqual({ kind: "sends", key: "steps", value: 6 });
});

test("frame: aspect by area on the 64-px grid, inside the preset's bounds", () => {
  const f = dial("frame");
  expect(resolveFrame(f, ZIMAGE, { aspect: "1:1", size: 1024 })).toMatchObject({ width: 1024, height: 1024, snapped: false });
  expect(resolveFrame(f, ZIMAGE, { aspect: "3:4", size: 1024 })).toMatchObject({ width: 896, height: 1152 });
  expect(resolveFrame(f, ZIMAGE, { aspect: "16:9", size: 1024 })).toMatchObject({ width: 1344, height: 768 });
  const small: Preset = {
    ...ZIMAGE,
    inputs: ZIMAGE.inputs.map((i) => (i.key === "width" || i.key === "height" ? { ...i, max: 1536 } : i)),
  };
  expect(resolveFrame(f, small, { aspect: "1:1", size: 2048 })).toMatchObject({ width: 1536, height: 1536, snapped: true });
  expect(frameBounds([small])).toEqual({ min: 512, max: 1536 });
  expect(frameBounds([])).toEqual({ min: 512, max: 2048 });
  // several engines: the slider spans their union, each clamps on its own line
  expect(frameBounds([small, ZIMAGE])).toEqual({ min: 512, max: 2048 });
  expect(askedFrame({ aspect: "3:4", size: 1024 })).toEqual({ width: 896, height: 1152 });
});

test("a big frame's warning is worded from the matrix's own state — never guessed", () => {
  const f = dial("frame");
  const ok = resolveFrame(f, SDXL, { aspect: "1:1", size: 1024 });
  const big = resolveFrame(f, SDXL, { aspect: "1:1", size: 1536 });
  expect(frameWarning(undefined, SDXL, ok)).toBeNull();
  expect(frameWarning(undefined, SDXL, big)).toMatch(/matrix hasn't arrived/);
  const engines: Engines = {
    updated: "2026-08-11",
    engines: [
      {
        id: "sdxl-juggernaut-xi",
        name: "SDXL Juggernaut XI",
        lane: "local",
        payload: "image",
        verbs: [],
        params: {},
        price: "free",
        capabilities: {
          maxResolution: { value: 2048, state: "unverified", note: "SDXL native is 1024 — expect composition drift above it." },
        },
      },
    ],
    external: { lane: "external", payload: "image", price: "", catalogue: "", defaults: {} },
  };
  expect(frameWarning(engines, SDXL, big)).toBe(
    "SDXL Juggernaut XI: UNVERIFIED above 1024 — SDXL native is 1024 — expect composition drift above it.",
  );
  const low = { ...engines, engines: [{ ...engines.engines[0], capabilities: { maxResolution: { value: 1280, state: "verified" as const, note: "Measured." } } }] };
  expect(frameWarning(low, SDXL, big)).toBe("SDXL Juggernaut XI: above its 1280 ceiling (VERIFIED) — Measured.");
  expect(frameWarning(engines, ZIMAGE, big)).toMatch(/no size ceiling on record/);
});

test("the sweep walks 16-point steps around the current value, held inside 0–100", () => {
  expect(sweepPositions(50)).toEqual([34, 50, 66, 82]);
  expect(sweepPositions(62)).toEqual([46, 62, 78, 94]);
  expect(sweepPositions(95)).toEqual([52, 68, 84, 100]);
  expect(sweepPositions(5)).toEqual([0, 16, 32, 48]);
  expect(sweepPositions(50, 3, 10)).toEqual([40, 50, 60]);
  // the count is the artist's: two walks up from here, five sit around it,
  // and a walk too long for the band shrinks its step instead of leaving it
  expect(sweepPositions(50, 2)).toEqual([50, 66]);
  expect(sweepPositions(50, 5)).toEqual([18, 34, 50, 66, 82]);
  expect(sweepPositions(50, 8)).toEqual([2, 16, 30, 44, 58, 72, 86, 100]);
  expect(sweepPositions(50, 16)).toHaveLength(16);
  expect(Math.max(...sweepPositions(50, 16))).toBeLessThanOrEqual(100);
  expect(sweepPositions(50, 1)).toEqual([50, 66]); // one take walks nothing
});

test("the seed: fresh per take unless locked — and a sweep always locks it", () => {
  expect(seedPolicy({ locked: false, value: 9 }, false)).toEqual({ mode: "random" });
  expect(seedPolicy({ locked: true, value: 9 }, false)).toEqual({ mode: "locked", value: 9 });
  expect(seedPolicy({ locked: false, value: 9 }, true)).toEqual({ mode: "locked", value: 9 });
  const models = [
    { id: "a/deaf", provider: "x", label: "Deaf Model", name: "", supports_seed: false, pinned: true, source: "config" as const },
    { id: "b/hears", provider: "x", label: "Hears", name: "", supports_seed: true, pinned: true, source: "config" as const },
  ];
  const stepped = new Map([["a/deaf", 1], ["b/hears", 1]]);
  expect(
    seedBadges({ dial: dial("seed"), presets: [ZIMAGE], models, external: stepped, locked: true }),
  ).toEqual(["1 SLOT IGNORES THE LOCK — DEAF MODEL"]);
  // a deaf model stepped to three is three slots ignoring it
  expect(
    seedBadges({ dial: dial("seed"), presets: [ZIMAGE, SDXL], models, external: new Map([["a/deaf", 3]]), locked: true }),
  ).toEqual(["3 SLOTS IGNORE THE LOCK — DEAF MODEL"]);
  expect(
    seedBadges({ dial: dial("seed"), presets: [ZIMAGE], models, external: new Map([["a/deaf", 1]]), locked: false }),
  ).toEqual([]);
});

test("the request half: every transmitting raw and one override per swept slot — the seed policy is the turn's", () => {
  const root = requestInputs({ table: TABLE, preset: ZIMAGE, recipe: "txt2img", state: state(), sweep: null });
  expect(root).toEqual({
    inputs: { steps: 36, cfg: 4, width: 1024, height: 1024 },
    take_overrides: [],
  });
  const refine = requestInputs({ table: TABLE, preset: SDXL, recipe: "img2img", state: state({ "anchor-hold": 80 }), sweep: null });
  expect(refine.inputs).toEqual({ denoise: 0.25, cfg: 5, steps: 28, width: 1024, height: 1024 });

  const sweep = requestInputs({ table: TABLE, preset: ZIMAGE, recipe: "txt2img", state: state(), sweep: "prompt-grip" });
  expect(sweep.inputs).not.toHaveProperty("seed");
  expect(sweep.take_overrides).toEqual([{ cfg: 3.4 }, { cfg: 4 }, { cfg: 5 }, { cfg: 5.9 }]);
  const hold = requestInputs({ table: TABLE, preset: SDXL, recipe: "img2img", state: state(), sweep: "anchor-hold" });
  expect(hold.take_overrides).toEqual([{ denoise: 0.65 }, { denoise: 0.5 }, { denoise: 0.35 }, { denoise: 0.25 }]);
  const six = requestInputs({ table: TABLE, preset: ZIMAGE, recipe: "txt2img", state: state(), sweep: "cook-time", count: 6 });
  expect(six.take_overrides).toEqual([{ steps: 24 }, { steps: 30 }, { steps: 36 }, { steps: 40 }, { steps: 45 }, { steps: 49 }]);
  // an engine that doesn't transmit the swept dial: four empty overrides —
  // the takes follow the recipe, and the readout says so (badge, never filter)
  const deaf = requestInputs({ table: TABLE, preset: TURBO, recipe: "txt2img", state: state(), sweep: "prompt-grip" });
  expect(deaf.take_overrides).toEqual([{}, {}, {}, {}]);
  expect(sweepLine({ table: TABLE, preset: TURBO, recipe: "txt2img", state: state(), sweep: "prompt-grip" })).toMatch(
    /^WALKS PROMPT GRIP 34 · 50 · 66 · 82 · FOLLOWS ITS RECIPE — Turbo runs cfg 1\.0/,
  );
  expect(sweepLine({ table: TABLE, preset: ZIMAGE, recipe: "txt2img", state: state(), sweep: "prompt-grip" })).toBe(
    "WALKS PROMPT GRIP 34 · 50 · 66 · 82 → CFG 3.4 · 4 · 5 · 5.9",
  );
  expect(sweepLine({ table: TABLE, preset: ZIMAGE, recipe: "txt2img", state: state(), sweep: null })).toMatch(/^OFF/);
});

test("persisted state is read tolerantly — garbage resets a dial, never the composer", () => {
  const fresh = readState(null, TABLE);
  expect(fresh["prompt-grip"]).toBe(50);
  expect(fresh.frame).toEqual({ aspect: "1:1", size: 1024 });
  expect(fresh.seed.locked).toBe(false);
  const partial = readState(
    { "prompt-grip": 140, "cook-time": "hot", frame: { aspect: "9:16", size: 1536 }, seed: { locked: true, value: 7 } },
    TABLE,
  );
  expect(partial["prompt-grip"]).toBe(100);
  expect(partial["cook-time"]).toBe(50);
  expect(partial.frame).toEqual({ aspect: "1:1", size: 1536 });
  expect(partial.seed).toEqual({ locked: true, value: 7 });
});

// ── the Advanced fold: every other input, the dials' keys at their
// full raw range, explicit overrides only ────────────────────────────────────

const SAMPLER: InputSpec = {
  key: "sampler_name", label: "Sampler", type: "enum", default: "dpmpp_2m",
  choices: ["euler", "dpmpp_2m", "heun"], supported: true,
};
const SCHEDULER: InputSpec = {
  key: "scheduler", label: "Scheduler", type: "enum", default: "karras",
  choices: ["normal", "karras"], supported: true,
};
const NEGATIVE: InputSpec = {
  key: "negative_prompt", label: "Negative prompt", type: "text",
  default: "blurry, low quality", supported: true,
};
const NO_NEGATIVE: InputSpec = {
  key: "negative_prompt", label: "Negative prompt", type: "text", supported: false,
  reason: "FLUX.2 klein is guidance-distilled: it has no CFG/negative-prompt channel.",
};
const SDXL_FULL: Preset = { ...SDXL, inputs: [...SDXL.inputs, NEGATIVE, SAMPLER, SCHEDULER] };
const KLEIN_FULL: Preset = { ...KLEIN, inputs: [...KLEIN.inputs, NO_NEGATIVE, SAMPLER] };

const fold = (
  preset: Preset,
  recipe: "txt2img" | "img2img",
  over: Partial<DialState> = {},
  sweep: SliderId | null = null,
) => foldRows({ table: TABLE, preset, recipe, state: state(over), sweep });
const rowOf = (rows: ReturnType<typeof foldRows>, key: string) =>
  rows.find((r) => r.spec.key === key)!;

test("the fold lists every other input, per engine, per verb — the dials' keys at their full raw range", () => {
  const rows = fold(SDXL_FULL, "txt2img");
  expect(rows.map((r) => r.spec.key)).toEqual([
    "steps", "cfg", "width", "height", "negative_prompt", "sampler_name", "scheduler",
  ]);
  // a dial-owned key carries its dial and the dial's raw as the baseline —
  // and the preset's own ceiling, past where the band (48) stops
  const steps = rowOf(rows, "steps");
  expect(steps.dial).toEqual({ id: "cook-time", label: "COOK TIME" });
  expect(steps.baseline).toBe(28);
  expect(steps.spec.max).toBe(60);
  expect(foldRowLine(steps)).toBe("COOK TIME SENDS 28 · RAW 1–60");
  expect(rowOf(rows, "width").dial).toEqual({ id: "frame", label: "FRAME" });
  // every other input: the recipe default is the baseline
  const sampler = rowOf(rows, "sampler_name");
  expect(sampler.dial).toBeNull();
  expect(sampler.baseline).toBe("dpmpp_2m");
  expect(foldRowLine(sampler)).toBe('DEFAULT "dpmpp_2m"');
  expect(foldChanged(rows)).toBe(0);
  expect(foldHead(rows)).toBe("ADVANCED · 7 ENGINE-SPECIFIC SETTINGS");
  expect(foldHead(null)).toBe("ADVANCED · NO LOCAL ENGINE PICKED");
  // per verb: a refine adds the denoise row, ANCHOR HOLD's key
  const refine = fold(SDXL_FULL, "img2img");
  expect(rowOf(refine, "denoise").dial).toEqual({ id: "anchor-hold", label: "ANCHOR HOLD" });
  expect(rowOf(refine, "denoise").baseline).toBe(0.5);
  // an unsupported handle is listed with its reason, never dropped (law 12)
  const klein = fold(KLEIN_FULL, "txt2img");
  const neg = rowOf(klein, "negative_prompt");
  expect(neg.inert).toMatch(/guidance-distilled/);
  expect(foldRowLine(neg)).toBe("NO CHANNEL ON THIS ENGINE");
  // the prompt box and the SEED dial own theirs
  expect(klein.map((r) => r.spec.key)).not.toContain("prompt");
  expect(klein.map((r) => r.spec.key)).not.toContain("seed");
});

test("an override is kept as typed, sent clamped, and counted only when it changes what would run", () => {
  const over = {
    advanced: {
      "raw-sdxl": { steps: 999, sampler_name: "heun", cfg: 5, scheduler: "nope", negative_prompt: "" },
    },
  };
  const rows = fold(SDXL_FULL, "txt2img", over);
  const steps = rowOf(rows, "steps");
  expect(steps.stored).toBe(999);
  expect(steps.sent).toBe(60); // the preset's ceiling, visibly
  expect(steps.changed).toBe(true);
  expect(foldRowLine(steps)).toBe("SENDS 60 · CLAMPED FROM 999 · OVERRIDES COOK TIME 28");
  const sampler = rowOf(rows, "sampler_name");
  expect(sampler.changed).toBe(true);
  expect(foldRowLine(sampler)).toBe('SENDS "heun" · DEFAULT "dpmpp_2m"');
  // equal to the dial's raw: stored and sent, but no change
  const cfg = rowOf(rows, "cfg");
  expect(cfg.sent).toBe(5);
  expect(cfg.changed).toBe(false);
  // a remembered enum that is no longer a choice is said, not sent
  const sched = rowOf(rows, "scheduler");
  expect(sched.sent).toBeNull();
  expect(sched.changed).toBe(false);
  expect(foldRowLine(sched)).toBe('"nope" IS NOT A CHOICE ON THIS ENGINE — RESET IT · DEFAULT "karras"');
  // an emptied negative prompt is a change from its default
  const neg = rowOf(rows, "negative_prompt");
  expect(neg.sent).toBe("");
  expect(neg.changed).toBe(true);
  expect(foldRowLine(neg)).toBe('SENDS EMPTY · DEFAULT "blurry, low quality"');
  expect(foldChanged(rows)).toBe(3);
  expect(foldInputs(rows)).toEqual({ steps: 60, sampler_name: "heun", cfg: 5, negative_prompt: "" });
  // the dial's own print says the fold speaks for its key
  const raw = withHold(resolveSlider(dial("cook-time"), SDXL_FULL, "txt2img", 50), rows);
  expect(raw).toEqual({ kind: "held", key: "steps", value: 60, why: HELD_WHY });
  expect(rawLine(raw)).toBe("ADVANCED SETS STEPS 60");
  expect(withHold(resolveSlider(dial("prompt-grip"), SDXL_FULL, "txt2img", 50), rows)).toMatchObject({ kind: "held", value: 5 });
  // the dial moved under a held key: the fold still holds — and now counts,
  // because the dial alone would send 2.5
  const moved = fold(SDXL_FULL, "txt2img", { ...over, "prompt-grip": 0 });
  expect(rowOf(moved, "cfg").changed).toBe(true);
  expect(foldRowLine(rowOf(moved, "cfg"))).toBe("SENDS 5 · OVERRIDES PROMPT GRIP 2.5");
});

test("the fold rides the request; the swept dial's key is the sweep's, not the fold's", () => {
  const over = { advanced: { "raw-sdxl": { steps: 60, sampler_name: "heun" } } };
  const plain = requestInputs({ table: TABLE, preset: SDXL_FULL, recipe: "txt2img", state: state(over), sweep: null });
  expect(plain.inputs).toEqual({
    steps: 60, cfg: 5, width: 1024, height: 1024, sampler_name: "heun",
  });
  const sweep = requestInputs({ table: TABLE, preset: SDXL_FULL, recipe: "txt2img", state: state(over), sweep: "cook-time" });
  expect(sweep.inputs.steps).toBe(28); // the dial's — the fold's 60 stands aside
  expect(sweep.inputs.sampler_name).toBe("heun");
  expect(sweep.take_overrides).toEqual([{ steps: 23 }, { steps: 28 }, { steps: 34 }, { steps: 41 }]);
  const rows = fold(SDXL_FULL, "txt2img", over, "cook-time");
  const steps = rowOf(rows, "steps");
  expect(steps.inert).toBe("the sweep walks COOK TIME — this one comes from the dial");
  expect(steps.sent).toBeNull();
  expect(foldRowLine(steps)).toBe("FOLLOWS THE DIAL — THE SWEEP WALKS IT");
  expect(foldChanged(rows)).toBe(1); // the sampler
  // an override on an unsupported handle never travels
  const deaf = requestInputs({
    table: TABLE, preset: KLEIN_FULL, recipe: "txt2img",
    state: state({ advanced: { "raw-flux2": { negative_prompt: "text", steps: 32 } } }), sweep: null,
  });
  expect(deaf.inputs).toEqual({ steps: 32, guidance: 1, width: 1024, height: 1024 });
  // the frame prints the fold's side
  const wide = fold(SDXL_FULL, "txt2img", { advanced: { "raw-sdxl": { width: 1536 } } });
  const frame = frameWithHold(resolveFrame(dial("frame"), SDXL_FULL, { aspect: "1:1", size: 1024 }), wide);
  expect(frame).toMatchObject({ kind: "sends", width: 1536, height: 1024, held: ["width"] });
  expect(frameLine(frame)).toBe("SENDS WIDTH 1536 · HEIGHT 1024 · WIDTH FROM ADVANCED");
});

test("overrides are set, reset and cleared per preset, and read back tolerantly", () => {
  let st = state();
  st = setAdvanced(st, "raw-sdxl", "steps", 60);
  st = setAdvanced(st, "raw-sdxl", "sampler_name", "heun");
  st = setAdvanced(st, "raw-flux2", "steps", 32);
  expect(st.advanced).toEqual({ "raw-sdxl": { steps: 60, sampler_name: "heun" }, "raw-flux2": { steps: 32 } });
  st = setAdvanced(st, "raw-sdxl", "steps", null);
  expect(st.advanced["raw-sdxl"]).toEqual({ sampler_name: "heun" });
  st = setAdvanced(st, "raw-sdxl", "sampler_name", null);
  expect(st.advanced).toEqual({ "raw-flux2": { steps: 32 } }); // an emptied map disappears
  expect(clearAdvanced(st, "raw-flux2").advanced).toEqual({});
  const read = readState(
    { advanced: { "raw-sdxl": { steps: 60, sampler_name: "heun", junk: { a: 1 }, flag: true }, "raw-flux2": "nope", empty: {} } },
    TABLE,
  );
  expect(read.advanced).toEqual({ "raw-sdxl": { steps: 60, sampler_name: "heun" } });
  expect(readState(null, TABLE).advanced).toEqual({});
  expect(foldChangedLine(2)).toBe("2 CHANGED FROM DEFAULT");
});

// ── several engines in one batch: one line per stepped engine ──────────────

test("several engines print one line each, named by their roster row; an identical badge is said once (law 1)", () => {
  const grip = dial("prompt-grip");
  const entries = [ZIMAGE, SDXL, TURBO].map((p) => ({ preset: p, raw: resolveSlider(grip, p, "txt2img", 50) }));
  expect(rawLines(entries)).toEqual([
    { text: "RAW-ZIMAGE · SENDS CFG 4", muted: false, why: null },
    { text: "RAW-SDXL · SENDS CFG 5", muted: false, why: null },
    { text: "SKETCH-ZIMAGE · FOLLOWS ITS RECIPE", muted: true, why: expect.stringMatching(/^sketch-zimage: Turbo runs cfg 1\.0/) },
  ]);
  // one engine prints unnamed
  expect(rawLines(entries.slice(0, 1))).toEqual([{ text: "SENDS CFG 4", muted: false, why: null }]);
  expect(rawLines([])).toEqual([{ text: "— · NO LOCAL ENGINE PICKED", muted: true, why: null }]);
  // every engine inert on a root run's ANCHOR HOLD: one cause, one message
  const hold = dial("anchor-hold");
  const inert = [ZIMAGE, SDXL].map((p) => ({ preset: p, raw: resolveSlider(hold, p, "txt2img", 50) }));
  expect(rawLines(inert)).toEqual([{ text: "—", muted: false, why: "inert on a root run — there is no anchor to hold" }]);

  // the frame: the head shows the asked size, each engine its own clamp
  const f = dial("frame");
  const small: Preset = {
    ...SDXL,
    inputs: SDXL.inputs.map((i) => (i.key === "width" || i.key === "height" ? { ...i, max: 1024 } : i)),
  };
  const frames = [ZIMAGE, small].map((p) => ({ preset: p, frame: resolveFrame(f, p, { aspect: "16:9", size: 1024 }) }));
  expect(frameHead(frames, { aspect: "16:9", size: 1024 })).toBe("1344 × 768");
  expect(frameHead(frames.slice(0, 1), { aspect: "16:9", size: 1024 })).toBe("1344 × 768");
  expect(frameHead([], { aspect: "16:9", size: 1024 })).toBe("—");
  expect(frameLines(frames).map((l) => l.text)).toEqual([
    "RAW-ZIMAGE · SENDS WIDTH 1344 · HEIGHT 768",
    "RAW-SDXL · SENDS WIDTH 1024 · HEIGHT 768 · SNAPPED TO THE PRESET'S BOUNDS",
  ]);

  // the sweep: each engine walks its own row's count
  const lines = sweepLines({
    table: TABLE, presets: [ZIMAGE, SDXL], recipe: "txt2img", state: state(), sweep: "cook-time",
    counts: new Map([["raw-zimage", 4], ["raw-sdxl", 2]]),
  });
  expect(lines.map((l) => l.text)).toEqual([
    "RAW-ZIMAGE · WALKS COOK TIME 34 · 50 · 66 · 82 → STEPS 30 · 36 · 40 · 45",
    "RAW-SDXL · WALKS COOK TIME 50 · 66 → STEPS 28 · 34",
  ]);
  expect(sweepLines({ table: TABLE, presets: [], recipe: "txt2img", state: state(), sweep: null, counts: new Map() })).toEqual([
    { text: "OFF · EVERY TAKE RUNS THE DIALS AS SET", muted: false, why: null },
  ]);
  // one fold per engine, each naming its own
  expect(foldHead(fold(SDXL_FULL, "txt2img"), "Raw · SDXL Juggernaut")).toBe(
    "ADVANCED · RAW · SDXL JUGGERNAUT · 7 ENGINE-SPECIFIC SETTINGS",
  );
});
