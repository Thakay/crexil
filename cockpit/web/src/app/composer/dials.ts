// Dial resolution (under law 17). The six intent dials
// map a 0–100 position to each engine's OWN raw preset inputs through the
// authored table configs/dials.json (served as-is at /api/dials);
// the server stays dial-ignorant and only coerces to the preset's declared
// bounds. Pure — vitest exercises it with plain objects.
//
// Three rules carry the file:
//   1. Normalised across engines — one PROMPT GRIP of 50 sends each engine
//      its recipe value (position 50 = the verified recipe, pinned by
//      the server's test_dials.py), and the raws print underneath.
//   2. Every dial states its consequence in the table's own words. A
//      transmits=false row is a BADGE, never a filter (law 12): the engine
//      stays picked and follows its recipe, the note says why. Nothing
//      here predicts a number (law 17) — readouts are raws, states and notes.
//   3. The sweep spends four slots walking one value, everything else
//      held — the seed included, which locks for the sweep.
//
// Several engines in one batch: every resolver here takes ONE
// preset — the composer calls it once per stepped engine, and the print
// helpers at the bottom (rawLines, frameLines, sweepLines) turn those
// per-engine answers into the `SENDS FLUX 3.5 · SDXL 7.0` lines,
// one engine per line, an identical badge said once (law 1).
import type {
  InputSpec,
  Preset,
  ProviderModel,
  Dial,
  DialEngine,
  Dials,
  Engines,
} from "../../types";
import type { Recipe } from "./compose";

export type SliderId = "anchor-hold" | "prompt-grip" | "cook-time";
export type Aspect = "1:1" | "3:4" | "4:3" | "16:9";

/** One raw knob's value as the Advanced fold holds it. */
export type AdvancedValue = number | string;
/** The fold's explicit overrides only — preset id → input key → value. A
 *  key without one runs the dial's raw, or the recipe. */
export type AdvancedState = Record<string, Record<string, AdvancedValue>>;

/** What the artist set — positions, not raws, plus the fold's overrides.
 *  Persisted per workspace (reopening restores the dials) through
 *  PATCH …/view {dials}. */
export type DialState = {
  "anchor-hold": number;
  "prompt-grip": number;
  "cook-time": number;
  // `size` is the square-equivalent side; aspect applies by total pixel
  // area, then width/height snap to the presets' 64-px grid (dials.json).
  frame: { aspect: Aspect; size: number };
  seed: { locked: boolean; value: number };
  advanced: AdvancedState;
};

export const SLIDERS: readonly SliderId[] = [
  "anchor-hold",
  "prompt-grip",
  "cook-time",
];
export const ASPECTS: readonly Aspect[] = ["1:1", "3:4", "4:3", "16:9"];
const RATIO: Record<Aspect, number> = {
  "1:1": 1,
  "3:4": 3 / 4,
  "4:3": 4 / 3,
  "16:9": 16 / 9,
};
export const GRID = 64; // every preset declares its size handles on it
const SEED_ROLL = 1_000_000; // legible on a dial; the server accepts 2^53

export function rollSeed(): number {
  return Math.floor(Math.random() * SEED_ROLL);
}

export function clampPos(p: number): number {
  return Math.max(0, Math.min(100, Math.round(p)));
}

export function dialById(table: Dials, id: string): Dial | undefined {
  return table.dials.find((d) => d.id === id);
}

export function sweepSlots(table: Dials | undefined): number {
  return (table && dialById(table, "sweep-one-dial")?.sweep_slots) ?? 4;
}

export function defaultState(table: Dials): DialState {
  const d = table.position.default;
  const frame = dialById(table, "frame")?.default;
  return {
    "anchor-hold": d,
    "prompt-grip": d,
    "cook-time": d,
    frame: { aspect: "1:1", size: frame?.width ?? 1024 },
    seed: { locked: false, value: rollSeed() },
    advanced: {},
  };
}

// Tolerant: a missing or malformed field falls back to its default — old
// workspace state never crashes the composer, it just resets that dial.
export function readState(raw: unknown, table: Dials): DialState {
  const base = defaultState(table);
  if (typeof raw !== "object" || raw === null) return base;
  const r = raw as Record<string, unknown>;
  const pos = (k: SliderId) =>
    typeof r[k] === "number" ? clampPos(r[k] as number) : base[k];
  const f = (r.frame ?? {}) as Record<string, unknown>;
  const sd = (r.seed ?? {}) as Record<string, unknown>;
  return {
    "anchor-hold": pos("anchor-hold"),
    "prompt-grip": pos("prompt-grip"),
    "cook-time": pos("cook-time"),
    frame: {
      aspect: ASPECTS.includes(f.aspect as Aspect)
        ? (f.aspect as Aspect)
        : base.frame.aspect,
      size: typeof f.size === "number" ? f.size : base.frame.size,
    },
    seed: {
      locked: typeof sd.locked === "boolean" ? sd.locked : false,
      value: typeof sd.value === "number" ? sd.value : base.seed.value,
    },
    advanced: readAdvanced(r.advanced),
  };
}

/** The fold's overrides as stored — anything that isn't a number or a
 *  string is dropped, so a hand-edited or older blob can't wedge the fold;
 *  whether a kept value still fits its spec is judged at the point of use. */
function readAdvanced(raw: unknown): AdvancedState {
  const out: AdvancedState = {};
  if (typeof raw !== "object" || raw === null) return out;
  for (const [pid, held] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof held !== "object" || held === null) continue;
    const kept: Record<string, AdvancedValue> = {};
    for (const [k, v] of Object.entries(held as Record<string, unknown>))
      if (typeof v === "number" || typeof v === "string") kept[k] = v;
    if (Object.keys(kept).length > 0) out[pid] = kept;
  }
  return out;
}

// The table keys engines by id; a preset names its engine as model_family.
export function engineOf(preset: Preset): string {
  return preset.model_family ?? preset.id;
}

export function specOf(preset: Preset, key: string): InputSpec | undefined {
  return preset.inputs.find((i) => i.key === key);
}

function specApplies(spec: InputSpec, recipe: Recipe): boolean {
  const caps = spec.capabilities;
  return !caps || caps.length === 0 || caps.includes(recipe);
}

/** Round to the row's step, at the step's own precision (0.05 → 2 dp). */
export function snap(raw: number, step: number | null | undefined): number {
  if (step === null || step === undefined || step <= 0) return raw;
  const decimals = Math.max(0, Math.ceil(-Math.log10(step)));
  return Number((Math.round(raw / step) * step).toFixed(decimals));
}

/** The row's anchors interpolated at `position`, snapped to its step;
 *  null when the row doesn't transmit. Anchors are taken as given — sense
 *  can invert (anchor-hold raws fall as the position rises). */
export function interpolate(row: DialEngine, position: number): number | null {
  const anchors = row.anchors;
  if (!row.transmits || !anchors || anchors.length === 0) return null;
  const p = clampPos(position);
  const first = anchors[0];
  const last = anchors[anchors.length - 1];
  let raw: number;
  if (p <= first[0]) raw = first[1];
  else if (p >= last[0]) raw = last[1];
  else {
    let i = 0;
    while (anchors[i + 1][0] < p) i++;
    const [p0, r0] = anchors[i];
    const [p1, r1] = anchors[i + 1];
    raw = r0 + ((p - p0) / (p1 - p0)) * (r1 - r0);
  }
  return snap(raw, row.step);
}

/** The preset's declared bounds win over the dial band (what you set
 *  is what runs, clamped visibly — the server clamps the same way). */
export function clampToSpec(spec: InputSpec | undefined, v: number): number {
  if (spec === undefined) return v;
  let out = v;
  if (typeof spec.min === "number") out = Math.max(spec.min, out);
  if (typeof spec.max === "number") out = Math.min(spec.max, out);
  return spec.type === "int" ? Math.round(out) : out;
}

export type Raw =
  | { kind: "sends"; key: string; value: number }
  | { kind: "held"; key: string; value: AdvancedValue; why: string } // the fold overrides the dial
  | { kind: "inert"; why: string } // no channel in this recipe (root run)
  | { kind: "badge"; why: string } // the engine follows its recipe — law 12
  | { kind: "none" }; // no local engine picked

export function resolveSlider(
  dial: Dial,
  preset: Preset | null,
  recipe: Recipe,
  position: number,
): Raw {
  if (preset === null) return { kind: "none" };
  const row = dial.engines[engineOf(preset)];
  if (row === undefined)
    return {
      kind: "badge",
      why: `${preset.display_name} has no row in the dial table — it follows its recipe`,
    };
  if (!row.transmits || !row.input) return { kind: "badge", why: row.note };
  const spec = specOf(preset, row.input);
  if (spec === undefined)
    return {
      kind: "badge",
      why: `${preset.display_name} declares no ${row.input} handle — it follows its recipe`,
    };
  if (!specApplies(spec, recipe))
    return {
      kind: "inert",
      why:
        recipe === "txt2img"
          ? "inert on a root run — there is no anchor to hold"
          : `${row.input} doesn't apply to a ${recipe} run`,
    };
  const raw = interpolate(row, position);
  if (raw === null) return { kind: "badge", why: row.note };
  return { kind: "sends", key: row.input, value: clampToSpec(spec, raw) };
}

export type FrameRaw =
  | {
      kind: "sends";
      keys: [string, string];
      width: number;
      height: number;
      snapped: boolean; // the preset's bounds moved it off the asked size
      held?: string[]; // sides the Advanced fold sets instead
    }
  | { kind: "badge"; why: string }
  | { kind: "none" };

function onGrid(v: number): number {
  return Math.max(GRID, Math.round(v / GRID) * GRID);
}

/** Aspect by total pixel area, then both sides on the 64-px grid inside
 *  the preset's bounds (dials.json frame.mapping). */
export function resolveFrame(
  dial: Dial,
  preset: Preset | null,
  frame: DialState["frame"],
): FrameRaw {
  if (preset === null) return { kind: "none" };
  const row = dial.engines[engineOf(preset)];
  if (row === undefined || !row.transmits || !row.inputs || row.inputs.length < 2)
    return {
      kind: "badge",
      why: row?.note ?? `${preset.display_name} has no row in the dial table — it renders at its recipe's size`,
    };
  const [wk, hk] = row.inputs;
  const ws = specOf(preset, wk);
  const hs = specOf(preset, hk);
  if (ws === undefined || hs === undefined)
    return {
      kind: "badge",
      why: `${preset.display_name} declares no ${wk}/${hk} handles — it renders at its recipe's size`,
    };
  const area = frame.size * frame.size;
  const r = RATIO[frame.aspect];
  const w = onGrid(Math.sqrt(area * r));
  const h = onGrid(Math.sqrt(area / r));
  const cw = clampToSpec(ws, w);
  const ch = clampToSpec(hs, h);
  return {
    kind: "sends",
    keys: [wk, hk],
    width: cw,
    height: ch,
    snapped: cw !== w || ch !== h,
  };
}

/** The FRAME slider's range: the stepped presets' own width bounds (raw
 *  bounds live in presets/*.json, never in the dial table). With
 *  several engines the slider spans their union and each engine clamps
 *  visibly on its own line; none picked = the family's usual span. */
export function frameBounds(presets: readonly Preset[]): { min: number; max: number } {
  const specs = presets.map((p) => specOf(p, "width"));
  const mins = specs.map((ws) => ws?.min).filter((v): v is number => typeof v === "number");
  const maxs = specs.map((ws) => ws?.max).filter((v): v is number => typeof v === "number");
  return {
    min: mins.length > 0 ? Math.min(...mins) : 512,
    max: maxs.length > 0 ? Math.max(...maxs) : 2048,
  };
}

/** The frame as asked — aspect by area on the 64-px grid, before any
 *  engine's bounds: what the dial's head shows when several engines each
 *  clamp it their own way below. */
export function askedFrame(frame: DialState["frame"]): { width: number; height: number } {
  const area = frame.size * frame.size;
  const r = RATIO[frame.aspect];
  return { width: onGrid(Math.sqrt(area * r)), height: onGrid(Math.sqrt(area / r)) };
}

/** Which engines a frame this big may strain, and how confidently — worded
 *  at run time from engines.json (law 12). Silent up to the preset's own
 *  verified default; never a guess when the matrix hasn't arrived. */
export function frameWarning(
  engines: Engines | undefined,
  preset: Preset | null,
  frame: FrameRaw,
): string | null {
  if (preset === null || frame.kind !== "sends") return null;
  const verified = Math.max(
    Number(specOf(preset, frame.keys[0])?.default ?? 1024),
    Number(specOf(preset, frame.keys[1])?.default ?? 1024),
  );
  const long = Math.max(frame.width, frame.height);
  if (long <= verified) return null;
  if (engines === undefined)
    return `above ${verified} — the capability matrix hasn't arrived, so which engines this strains can't be said yet`;
  const e = engines.engines.find((x) => x.id === engineOf(preset));
  const cap = e?.capabilities.maxResolution;
  if (e === undefined || cap === undefined)
    return `${preset.display_name}: no size ceiling on record — above ${verified} is unverified`;
  const state = cap.state.toUpperCase();
  if (typeof cap.value === "number" && long > cap.value)
    return `${e.name}: above its ${cap.value} ceiling (${state}) — ${cap.note}`;
  return `${e.name}: ${state} above ${verified} — ${cap.note}`;
}

/** The positions walking one dial: 16-point steps with the current value
 *  in the middle of the walk (second of four), shifted as a block to stay
 *  inside 0–100. The spread is a judgement: wide enough that a few-step
 *  distill's steps land on distinct integers, narrow enough to stay near
 *  what the artist set. The count is the takes stepper's (dials.json
 *  `sweep_slots` is only its default); a walk too long for the band
 *  shrinks its step instead of running off the end. */
export const SWEEP_STEP = 16;
export const SWEEP_MIN = 2; // one take walks nothing

export function sweepPositions(p: number, n = 4, step = SWEEP_STEP): number[] {
  const count = Math.max(SWEEP_MIN, Math.floor(n));
  const s = Math.max(1, Math.min(step, Math.floor(100 / (count - 1))));
  const span = s * (count - 1);
  const below = Math.floor((count - 1) / 2); // steps taken before the current
  const start = Math.max(0, Math.min(clampPos(p) - s * below, 100 - span));
  return Array.from({ length: count }, (_, i) => start + s * i);
}

export type SeedPolicy =
  | { mode: "random" }
  | { mode: "locked"; value: number };

export function seedPolicy(
  seed: DialState["seed"],
  sweeping: boolean,
): SeedPolicy {
  return seed.locked || sweeping
    ? { mode: "locked", value: seed.value }
    : { mode: "random" };
}

/** Who can't honour a seed lock:
 *  each stepped local engine's own row, and stepped external models whose
 *  pin doesn't say supports_seed — counted in SLOTS, since a model stepped
 *  to 3 is three slots ignoring it. The request still records what was
 *  sent. */
export function seedBadges(args: {
  dial: Dial;
  presets: readonly Preset[];
  models: ProviderModel[];
  external: ReadonlyMap<string, number>;
  locked: boolean;
}): string[] {
  const out: string[] = [];
  for (const preset of args.presets) {
    const row = args.dial.engines[engineOf(preset)];
    if (row !== undefined && !row.transmits)
      out.push(`${preset.display_name}: ${row.note}`);
  }
  if (args.locked) {
    const deaf = args.models.filter(
      (m) => (args.external.get(m.id) ?? 0) > 0 && !m.supports_seed,
    );
    const slots = deaf.reduce((n, m) => n + (args.external.get(m.id) ?? 0), 0);
    if (slots > 0)
      out.push(
        `${slots} SLOT${slots > 1 ? "S IGNORE" : " IGNORES"} ` +
          `THE LOCK — ${deaf.map((m) => m.label.toUpperCase()).join(", ")}`,
      );
  }
  return out;
}

export type RunInputs = {
  inputs: Record<string, unknown>;
  take_overrides: Record<string, number>[];
};

/** One engine's half of the run request: every transmitting raw for this
 *  preset and recipe and — when sweeping — one override per slot moving
 *  the swept key. Anything that doesn't transmit is simply absent, so the
 *  engine runs its recipe. The seed policy is the TURN's, not the
 *  batch's (`seedPolicy`, sent once beside the batches). */
export function requestInputs(args: {
  table: Dials;
  preset: Preset;
  recipe: Recipe;
  state: DialState;
  sweep: SliderId | null;
  count?: number; // slots the sweep spends; the table's default when absent
}): RunInputs {
  const { table, preset, recipe, state, sweep } = args;
  const count = args.count ?? sweepSlots(table);
  const inputs: Record<string, unknown> = {};
  for (const id of SLIDERS) {
    const dial = dialById(table, id);
    if (dial === undefined) continue;
    const r = resolveSlider(dial, preset, recipe, state[id]);
    if (r.kind === "sends") inputs[r.key] = r.value;
  }
  const frameDial = dialById(table, "frame");
  if (frameDial !== undefined) {
    const f = resolveFrame(frameDial, preset, state.frame);
    if (f.kind === "sends") {
      inputs[f.keys[0]] = f.width;
      inputs[f.keys[1]] = f.height;
    }
  }
  // The fold's overrides lie over the dials' raws: a dial-owned
  // key set there sends the fold's value, every other raw knob joins.
  Object.assign(
    inputs,
    foldInputs(foldRows({ table, preset, recipe, state, sweep })),
  );
  const swept = sweep === null ? undefined : dialById(table, sweep);
  const take_overrides =
    swept === undefined || sweep === null
      ? []
      : sweepPositions(state[sweep], count).map((p) => {
          const r = resolveSlider(swept, preset, recipe, p);
          return r.kind === "sends" ? { [r.key]: r.value } : {};
        });
  return { inputs, take_overrides };
}

// ── the Advanced fold ───────────────────────────────────────────────
//
// The fold holds the raw knobs: every preset input the dials don't own,
// and the dials' own keys at their FULL declared range — the extremes the
// dial bands stop short of (dials.json semantics; the bounds' home is the
// preset). Contents are per engine, per verb: the picked preset's
// specs that apply to this recipe. Only explicit overrides are stored,
// beside the positions. What was typed is kept as typed and
// clamped VISIBLY at the point of use, the way the server clamps.
// The word "sampler" lives only inside the fold — nothing above it says it.

/** The prompt box and the SEED dial own these — never fold rows. */
const OWNED_ELSEWHERE: ReadonlySet<string> = new Set(["prompt", "seed"]);

export function foldSpecs(preset: Preset, recipe: Recipe): InputSpec[] {
  return preset.inputs.filter(
    (i) =>
      !OWNED_ELSEWHERE.has(i.key) && i.type !== "seed" && specApplies(i, recipe),
  );
}

export type FoldRow = {
  spec: InputSpec;
  label: string;
  /** The dial that sends this key for the picked engine in this recipe. */
  dial: { id: string; label: string } | null;
  /** What runs without the fold: the dial's raw, else the recipe default. */
  baseline: AdvancedValue | null;
  /** What the fold holds, exactly as set. */
  stored: AdvancedValue | null;
  /** What would be sent for it — clamped to the spec; null when nothing can be. */
  sent: AdvancedValue | null;
  changed: boolean;
  /** Why the row can't take part right now, in words — null when it can. */
  inert: string | null;
};

/** Which keys the dials send for this preset and recipe, with the raw each
 *  sends at the current position — the fold's baseline for those rows. */
function dialOwners(
  table: Dials,
  preset: Preset,
  recipe: Recipe,
  state: DialState,
): Map<string, { id: string; label: string; baseline: number }> {
  const out = new Map<string, { id: string; label: string; baseline: number }>();
  for (const id of SLIDERS) {
    const dial = dialById(table, id);
    if (dial === undefined) continue;
    const r = resolveSlider(dial, preset, recipe, state[id]);
    if (r.kind === "sends")
      out.set(r.key, { id, label: dial.label, baseline: r.value });
  }
  const frameDial = dialById(table, "frame");
  if (frameDial !== undefined) {
    const f = resolveFrame(frameDial, preset, state.frame);
    if (f.kind === "sends") {
      out.set(f.keys[0], { id: "frame", label: frameDial.label, baseline: f.width });
      out.set(f.keys[1], { id: "frame", label: frameDial.label, baseline: f.height });
    }
  }
  return out;
}

/** A stored value as the spec would send it: numbers clamped to the
 *  declared bounds (ints rounded), an enum only when it is still a choice,
 *  text as is. Null = nothing sendable — and the row says why. */
export function sendable(
  spec: InputSpec,
  v: AdvancedValue | null,
): AdvancedValue | null {
  if (v === null) return null;
  switch (spec.type) {
    case "int":
    case "float": {
      const n = typeof v === "number" ? v : Number(v);
      return Number.isFinite(n) ? clampToSpec(spec, n) : null;
    }
    case "enum":
      return typeof v === "string" && (spec.choices ?? []).includes(v) ? v : null;
    case "text":
      return typeof v === "string" ? v : null;
    default:
      return null;
  }
}

function same(a: AdvancedValue | null, b: AdvancedValue | null): boolean {
  if (a === null || b === null) return a === b;
  return typeof a === "number" || typeof b === "number"
    ? Number(a) === Number(b)
    : a === b;
}

export function foldRows(args: {
  table: Dials;
  preset: Preset;
  recipe: Recipe;
  state: DialState;
  sweep: SliderId | null;
}): FoldRow[] {
  const { table, preset, recipe, state, sweep } = args;
  const owners = dialOwners(table, preset, recipe, state);
  // `advanced` is optional-tolerant on purpose: a state built before the
  // fold existed (or by a test) still resolves — with nothing overridden.
  const held = state.advanced?.[preset.id] ?? {};
  return foldSpecs(preset, recipe).map((spec) => {
    const owner = owners.get(spec.key) ?? null;
    const baseline =
      owner !== null
        ? owner.baseline
        : typeof spec.default === "number" || typeof spec.default === "string"
          ? spec.default
          : null;
    const stored = held[spec.key] ?? null;
    let inert: string | null = null;
    if (!spec.supported) inert = spec.reason ?? "no channel on this engine";
    else if (sweep !== null && owner?.id === sweep)
      inert = `the sweep walks ${owner.label} — this one comes from the dial`;
    const sent = inert !== null ? null : sendable(spec, stored);
    return {
      spec,
      label: spec.label ?? spec.key,
      dial: owner === null ? null : { id: owner.id, label: owner.label },
      baseline,
      stored,
      sent,
      changed: sent !== null && !same(sent, baseline),
      inert,
    };
  });
}

/** The fold's half of the request: every sendable override, by key. */
export function foldInputs(rows: FoldRow[]): Record<string, AdvancedValue> {
  const out: Record<string, AdvancedValue> = {};
  for (const r of rows) if (r.sent !== null) out[r.spec.key] = r.sent;
  return out;
}

export function foldChanged(rows: FoldRow[]): number {
  return rows.filter((r) => r.changed).length;
}

/** One override set (or, with null, cleared) — an empty map disappears so
 *  a workspace that never opened the fold stores nothing for it. */
export function setAdvanced(
  state: DialState,
  presetId: string,
  key: string,
  value: AdvancedValue | null,
): DialState {
  const held = { ...(state.advanced[presetId] ?? {}) };
  if (value === null) delete held[key];
  else held[key] = value;
  const advanced = { ...state.advanced };
  if (Object.keys(held).length === 0) delete advanced[presetId];
  else advanced[presetId] = held;
  return { ...state, advanced };
}

export function clearAdvanced(state: DialState, presetId: string): DialState {
  const advanced = { ...state.advanced };
  delete advanced[presetId];
  return { ...state, advanced };
}

export const HELD_WHY =
  "set in the Advanced fold — the dial is overridden until it's reset there";

/** A dial's raw print once the fold speaks for its key: the value sent is
 *  the fold's, and the control says so instead of pretending to send. */
export function withHold(raw: Raw, rows: FoldRow[]): Raw {
  if (raw.kind !== "sends") return raw;
  const row = rows.find((r) => r.spec.key === raw.key);
  if (row === undefined || row.sent === null) return raw;
  return { kind: "held", key: raw.key, value: row.sent, why: HELD_WHY };
}

export function frameWithHold(frame: FrameRaw, rows: FoldRow[]): FrameRaw {
  if (frame.kind !== "sends") return frame;
  const held: string[] = [];
  let { width, height } = frame;
  for (const r of rows) {
    if (typeof r.sent !== "number") continue;
    if (r.spec.key === frame.keys[0]) {
      width = r.sent;
      held.push(r.spec.key);
    } else if (r.spec.key === frame.keys[1]) {
      height = r.sent;
      held.push(r.spec.key);
    }
  }
  return held.length === 0 ? frame : { ...frame, width, height, held };
}

// ── readout copy: raws and badges as the control prints them ───────────────

export function rawLine(raw: Raw): string {
  switch (raw.kind) {
    case "sends":
      return `SENDS ${raw.key.toUpperCase()} ${raw.value}`;
    case "held":
      return `ADVANCED SETS ${raw.key.toUpperCase()} ${raw.value}`;
    case "inert":
      return "—";
    case "badge":
      return "FOLLOWS ITS RECIPE";
    case "none":
      return "— · NO LOCAL ENGINE PICKED";
  }
}

export function frameLine(frame: FrameRaw): string {
  if (frame.kind === "sends")
    return (
      `SENDS ${frame.keys[0].toUpperCase()} ${frame.width} · ` +
      `${frame.keys[1].toUpperCase()} ${frame.height}` +
      (frame.snapped && !frame.held?.length ? " · SNAPPED TO THE PRESET'S BOUNDS" : "") +
      (frame.held ?? []).map((k) => ` · ${k.toUpperCase()} FROM ADVANCED`).join("")
    );
  return rawLine(frame);
}

export const FOLD_LEAD = "Nothing in here changes what the picture is of.";

/** The fold's collapsed row. With several engines in the batch there is
 *  one fold per engine and each names its own; alone, the row
 *  carries no engine name. */
export function foldHead(rows: FoldRow[] | null, name?: string): string {
  if (rows === null) return "ADVANCED · NO LOCAL ENGINE PICKED";
  const who = name === undefined ? "" : ` · ${name.toUpperCase()}`;
  return `ADVANCED${who} · ${rows.length} ENGINE-SPECIFIC SETTING${rows.length === 1 ? "" : "S"}`;
}

export function foldChangedLine(n: number): string {
  return `${n} CHANGED FROM DEFAULT`;
}

function showValue(v: AdvancedValue | null): string {
  if (v === null) return "—";
  if (typeof v === "number") return String(v);
  return v === "" ? "EMPTY" : `"${v}"`;
}

function rawSpan(spec: InputSpec): string {
  const lo = typeof spec.min === "number" ? spec.min : null;
  const hi = typeof spec.max === "number" ? spec.max : null;
  if (lo === null && hi === null) return "";
  return ` · RAW ${lo ?? "…"}–${hi ?? "…"}`;
}

/** A row's own readout under its control: where its value comes from and
 *  what will be sent, in the same words the dials use. */
export function foldRowLine(row: FoldRow): string {
  const { spec, dial, baseline, stored, sent } = row;
  if (row.inert !== null)
    return spec.supported
      ? "FOLLOWS THE DIAL — THE SWEEP WALKS IT"
      : "NO CHANNEL ON THIS ENGINE";
  const def = `DEFAULT ${showValue(baseline)}`;
  const clamped =
    sent !== null && stored !== null && !same(sent, stored)
      ? ` · CLAMPED FROM ${showValue(stored)}`
      : "";
  if (stored !== null && sent === null)
    return `${showValue(stored)} IS NOT A CHOICE ON THIS ENGINE — RESET IT · ${def}`;
  if (sent !== null)
    return dial === null
      ? `SENDS ${showValue(sent)}${clamped} · ${def}`
      : `SENDS ${showValue(sent)}${clamped} · OVERRIDES ${dial.label} ${showValue(baseline)}`;
  return dial === null
    ? `${def}${rawSpan(spec)}`
    : `${dial.label} SENDS ${showValue(baseline)}${rawSpan(spec)}`;
}

// ── several engines in one batch: one line per stepped engine ──────────────
//
// The roster's stepped rows are the dials' audience: each resolver above
// answers for one preset, and these fold the answers into the
// `SENDS FLUX 3.5 · SDXL 7.0` print as one line per engine,
// named by the roster row it is. One engine prints unnamed.
// Engines that all say the same non-sending thing (every engine
// is inert on a root run's ANCHOR HOLD) say it once: one cause, one
// message (law 1).

export type PrintLine = { text: string; muted: boolean; why: string | null };

export function engineName(preset: Preset): string {
  return preset.display_name.toUpperCase();
}

function perEngine(
  entries: { preset: Preset; text: string; muted: boolean; why: string | null }[],
): PrintLine[] {
  if (entries.length <= 1)
    return entries.map(({ text, muted, why }) => ({ text, muted, why }));
  const first = entries[0];
  const same = entries.every(
    (e) => e.why !== null && e.why === first.why && e.text === first.text,
  );
  if (same) return [{ text: first.text, muted: first.muted, why: first.why }];
  return entries.map((e) => ({
    text: `${engineName(e.preset)} · ${e.text}`,
    muted: e.muted,
    why: e.why === null ? null : `${e.preset.display_name}: ${e.why}`,
  }));
}

function rawWhy(raw: Raw): string | null {
  return raw.kind === "badge" || raw.kind === "inert" || raw.kind === "held"
    ? raw.why
    : null;
}

/** A slider's print for the stepped engines — `[]` means none is. */
export function rawLines(entries: { preset: Preset; raw: Raw }[]): PrintLine[] {
  if (entries.length === 0)
    return [{ text: rawLine({ kind: "none" }), muted: true, why: null }];
  return perEngine(
    entries.map(({ preset, raw }) => ({
      preset,
      text: rawLine(raw),
      muted: raw.kind === "badge" || raw.kind === "held",
      why: rawWhy(raw),
    })),
  );
}

export function frameLines(
  entries: { preset: Preset; frame: FrameRaw }[],
): PrintLine[] {
  if (entries.length === 0)
    return [{ text: rawLine({ kind: "none" }), muted: true, why: null }];
  return perEngine(
    entries.map(({ preset, frame }) => ({
      preset,
      text: frameLine(frame),
      muted: frame.kind !== "sends",
      why: frame.kind === "badge" ? frame.why : null,
    })),
  );
}

/** The FRAME dial's head: one engine shows what it renders; several show
 *  the asked size, each engine's own on its line beneath. */
export function frameHead(
  entries: { preset: Preset; frame: FrameRaw }[],
  asked: DialState["frame"],
): string {
  if (entries.length === 1) {
    const f = entries[0].frame;
    return f.kind === "sends" ? `${f.width} × ${f.height}` : "—";
  }
  if (entries.length === 0) return "—";
  const g = askedFrame(asked);
  return `${g.width} × ${g.height}`;
}

/** The sweep's readout per stepped engine, each walking its own row's
 *  count. One engine reads unnamed. */
export function sweepLines(args: {
  table: Dials;
  presets: readonly Preset[];
  recipe: Recipe;
  state: DialState;
  sweep: SliderId | null;
  counts: ReadonlyMap<string, number>;
}): PrintLine[] {
  const { table, presets, recipe, state, sweep, counts } = args;
  if (sweep === null || presets.length === 0)
    return [
      {
        text: sweepLine({ table, preset: presets[0] ?? null, recipe, state, sweep }),
        muted: false,
        why: null,
      },
    ];
  return perEngine(
    presets.map((preset) => ({
      preset,
      text: sweepLine({ table, preset, recipe, state, sweep, count: counts.get(preset.id) }),
      muted: false,
      why: null,
    })),
  );
}

export function seedLine(seed: DialState["seed"], sweeping: boolean): string {
  if (sweeping) return `LOCKED FOR THE SWEEP · ${seed.value} TO EVERY TAKE`;
  return seed.locked
    ? `LOCKED · ${seed.value} TO EVERY TAKE`
    : "FRESH ROLL PER TAKE";
}

/** The sweep's own readout: the positions it walks and what they send. */
export function sweepLine(args: {
  table: Dials;
  preset: Preset | null;
  recipe: Recipe;
  state: DialState;
  sweep: SliderId | null;
  count?: number;
}): string {
  const { table, preset, recipe, state, sweep } = args;
  if (sweep === null) return "OFF · EVERY TAKE RUNS THE DIALS AS SET";
  const dial = dialById(table, sweep);
  if (dial === undefined) return "—";
  const positions = sweepPositions(state[sweep], args.count ?? sweepSlots(table));
  const raws = positions.map((p) => resolveSlider(dial, preset, recipe, p));
  const walk = `WALKS ${dial.label} ${positions.join(" · ")}`;
  const first = raws[0];
  if (first.kind === "sends")
    return `${walk} → ${first.key.toUpperCase()} ${raws
      .map((r) => (r.kind === "sends" ? String(r.value) : "—"))
      .join(" · ")}`;
  if (first.kind === "none") return `${walk} · ${rawLine(first)}`;
  return `${walk} · ${rawLine(first)} — ${first.why}`;
}
