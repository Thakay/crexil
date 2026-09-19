// The To-3D plinth's pure half: the mesh roster,
// the disarm reasons, the run bar's copy, the MeshRun body and where the
// mesh is born. No React; vitest exercises every line of it directly, the
// same split the region fan-out uses (regions/fanout.ts).
//
// Rules carried here:
//   law 12 — badge, never filter. When the runner isn't answering,
//        both engines stay listed with a dashed 0 and the SERVER's own
//        reason (`/mesh/health`.why) beneath them. The roster never
//        invents an engine the capability matrix hasn't published either,
//        and never hides one it has.
//   every string here is curated static text.
//   law 17 — counts and the one-GPU truth, never a predicted total. A mesh
//        is minutes, and the plinth says minutes without claiming how
//        many: the observed elapsed lives on the queue strip and the cell.
//
// Why the mesh roster reads the CAPABILITY MATRIX and not the presets: a
// mesh engine has no committed ComfyUI graph, so it has no preset to be
// listed as (api_v4_mesh._matrix_engines is the server's own mirror of
// this). The ids are `trellis2-4b` / `hunyuan3d-21`.
import type { Engine, Engines, MeshHealth, Node, Turn } from "../../types";
import { TURN_COL, fallbackPosition } from "../canvas/graph";
import { UNVERIFIED, capabilityBadges, type Badge } from "../composer/badges";

/** The server's own caps, mirrored for the face (api_v4_mesh.run_mesh_turn
 *  refuses past them in words — this is display truth, not the gate). */
export const MESH_MAX_PER_ENGINE = 4;
export const MESH_MAX_TAKES = 8;

/** Takes per stepped engine — engine id → count, zero rows absent. */
export type MeshTakes = ReadonlyMap<string, number>;

export type MeshRow = {
  engine: Engine;
  /** False = listed, dashed, with `reason` — never removed (law 12). */
  capable: boolean;
  reason: string | null;
  badges: Badge[];
};

/** The 3D verbs, in the artist's words (curated static text, one per
 *  kind the runner serves). The id is the wire's; this is the face's. The
 *  DECIMATE and TEXTURE plinths head their roster `ENGINES FOR: <action>`,
 *  and these are the actions. */
export const VERB_LABEL: Record<string, string> = {
  generate: "IMAGE → MESH",
  decimate: "DECIMATE",
  texture: "TEXTURE",
};

export function verbLabel(verb: string): string {
  return VERB_LABEL[verb] ?? verb.toUpperCase();
}

export const RUNNER_DOWN_FALLBACK =
  "the 3D engine runner isn't answering — the lane is down";

export const MATRIX_NO_MESH =
  "The capability matrix publishes no 3D engine, so the lane has no roster.";

/** The roster for ONE verb: every `payload: "3d"` row the matrix publishes,
 *  in its own order, whether or not it serves that verb. Health decides
 *  whether a row can be stepped, never whether it is drawn — a down runner
 *  is a dashed row with the reason on it, which is the whole of law 12 on this
 *  surface.
 *
 *  There is a second dashing reason and it is the more permanent one:
 *  an engine that does not DECLARE this verb (`verbs`, law 16) can never serve
 *  it, runner up or down. So it wins the row's one line — law 1's one cause,
 *  one message, and the cause the artist can act on (pick the other engine)
 *  rather than the one they can only wait out.
 *
 *  This is also why the To-3D plinth's roster is per verb too: gltfpack is
 *  a `payload: "3d"` engine, and a roster that ignored the verb would draw
 *  it as steppable there, which the run route refuses. A roster that offers
 *  what the route refuses is the failure law 12 is meant to prevent, not an
 *  instance of it. */
export function meshRoster(
  engines: Engines | undefined,
  health: MeshHealth | undefined,
  verb = "generate",
): MeshRow[] {
  if (engines === undefined) return [];
  const down = health !== undefined && !health.ok;
  const downWhy = down ? (health?.why ?? RUNNER_DOWN_FALLBACK) : null;
  const rows = engines.engines.filter((e) => e.payload === "3d");
  const serving = rows.filter((e) => (e.verbs ?? []).includes(verb));
  const names = serving.map((e) => e.name).join(" · ");
  return rows.map((e) => {
    const serves = (e.verbs ?? []).includes(verb);
    return {
      engine: e,
      capable: serves && !down,
      reason: !serves
        ? `${e.name} doesn't do ${verbLabel(verb).toLowerCase()}` +
          (names === "" ? " — no engine here does" : ` — ${names} does`)
        : downWhy,
      badges: capabilityBadges(e.capabilities),
    };
  });
}

export function sumMeshTakes(takes: MeshTakes): number {
  let n = 0;
  for (const v of takes.values()) n += v;
  return n;
}

// ── the Advanced fold (the composer fold's grammar) ──────────
//
// The three handles it carries: RESOLUTION TIER · TEXTURE SIZE ·
// SEED. Paint views, steps and guidance stay hidden — they move little and
// cost minutes, and a handle that changes nothing an artist can see is a
// handle that wastes their attention.
//
// Everything drawn here is READ off the capability matrix's `params` spec
// (`configs/engines.json`), never authored in this file. That is the whole
// reason the spec exists: the runner's whitelist is one carrier away, so a
// range typed into the web would be a second copy of a fact and would drift.
// Seed is the exception and deliberately so: it is a run-level field
// with a capability flag of its own, not a param the runner whitelists.

/** One row of the fold, as the matrix publishes it. A row with
 *  `supported: false` is DRAWN and dashed with its reason — an artist who
 *  came looking for a texture size on Hunyuan learns the atlas is fixed,
 *  rather than finding a gap where TRELLIS.2 has a control (law 12). */
export type MeshKnob = {
  key: string;
  label: string;
  effect: string;
  choices: (string | number)[];
  fallback: string | number | null;
  unverified: boolean;
  supported: boolean;
  why: string | null;
};

/** Set knobs, per engine: engine id → {param key: value}. Only what the
 *  artist actually changed lives here; an untouched knob is absent and the
 *  engine's own default governs (the hidden good default is the
 *  engine's, and restating it here would be a second copy of it). */
export type MeshKnobs = ReadonlyMap<string, Readonly<Record<string, string | number>>>;

/** The fold's rows for one engine and one verb — matrix order, which is
 *  authoring order. Empty when the engine publishes no spec for this verb,
 *  and the fold then says so rather than drawing an empty box. */
export function meshKnobs(engine: Engine, verb = "generate"): MeshKnob[] {
  const spec = (engine.params ?? {})[verb] ?? {};
  return Object.entries(spec).map(([key, p]) => ({
    key,
    label: p.label,
    effect: p.effect ?? "",
    // the matrix is authored JSON, so a choice arrives untyped; anything
    // that isn't a value a control can carry is dropped rather than drawn
    // as an option the artist can pick and the runner would refuse
    choices: (p.choices ?? []).filter(
      (c): c is string | number =>
        typeof c === "string" || typeof c === "number",
    ),
    fallback:
      typeof p.default === "string" || typeof p.default === "number"
        ? p.default
        : null,
    unverified: p.state === "unverified",
    supported: p.supported !== false,
    why: p.why ?? null,
  }));
}

/** What this engine will actually be sent for a knob — the artist's value,
 *  or null when they haven't touched it. Null is meaningful: it is the
 *  difference between "send 2048" and "say nothing and let the engine use
 *  its own 2048", and only the second survives an engine changing its mind. */
export function meshKnobValue(
  knobs: MeshKnobs,
  engineId: string,
  key: string,
): string | number | null {
  const v = knobs.get(engineId)?.[key];
  return v === undefined ? null : v;
}

/** Set or clear one knob. Clearing removes the key entirely rather than
 *  writing the default back, so RESET means "don't send it" (nothing is
 *  rewritten under the artist). */
export function setMeshKnob(
  knobs: MeshKnobs,
  engineId: string,
  key: string,
  value: string | number | null,
): MeshKnobs {
  const next = new Map(knobs);
  const row = { ...(next.get(engineId) ?? {}) };
  if (value === null) delete row[key];
  else row[key] = value;
  if (Object.keys(row).length === 0) next.delete(engineId);
  else next.set(engineId, row);
  return next;
}

export function clearMeshKnobs(knobs: MeshKnobs, engineId: string): MeshKnobs {
  const next = new Map(knobs);
  next.delete(engineId);
  return next;
}

/** How many of this engine's knobs the artist has moved — the fold's
 *  collapsed count, the composer fold's `N CHANGED FROM DEFAULT`. */
export function meshKnobsChanged(knobs: MeshKnobs, engineId: string): number {
  return Object.keys(knobs.get(engineId) ?? {}).length;
}

/** The fold's collapsed head. ONE fold for the run, not one per engine
 *  (the composer draws one per preset because each is a separate recipe;
 *  here the seed is run-level and would have to be duplicated into every
 *  engine's fold, where changing it in one and not the other is a lie the
 *  surface can't tell). Inside, the seed sits alone and each stepped
 *  engine's knobs are grouped under its name. */
export function meshFoldHead(settings: number): string {
  return `ADVANCED · ${settings} SETTING${settings === 1 ? "" : "S"}`;
}

export function meshFoldChangedLine(n: number): string {
  return `${n} CHANGED FROM DEFAULT`;
}

/** Every row the open fold will draw: the seed, plus each stepped engine's
 *  published knobs. Unsupported rows count — they are drawn and dashed, so
 *  a head that left them out would undercount what the artist sees (law 12). */
export function meshFoldRows(
  engines: Engines | undefined,
  counts: MeshTakes,
  verb = "generate",
): { engine: Engine; knobs: MeshKnob[] }[] {
  if (engines === undefined) return [];
  return engines.engines
    .filter((e) => (counts.get(e.id) ?? 0) > 0)
    .map((e) => ({ engine: e, knobs: meshKnobs(e, verb) }))
    .filter((g) => g.knobs.length > 0);
}

/** The head's count and the changed count, for the collapsed row. The seed
 *  is one setting and counts as changed when it is locked — locking it is
 *  the departure from the default, and the fold's job is to say how far the
 *  run has moved from the recipe. */
export function meshFoldCount(
  groups: readonly { knobs: MeshKnob[] }[],
): number {
  return 1 + groups.reduce((n, g) => n + g.knobs.length, 0);
}

export function meshFoldChanged(
  groups: readonly { engine: Engine }[],
  knobs: MeshKnobs,
  seed: MeshSeed,
): number {
  const n = groups.reduce(
    (acc, g) => acc + meshKnobsChanged(knobs, g.engine.id),
    0,
  );
  return n + (seed.locked ? 1 : 0);
}

/** The composer's fold promises the picture won't change; a mesh fold has to
 *  promise something else, because the shape IS what these knobs move. What
 *  survives is the honest half: the mesh is still of the same thing. */
export const MESH_FOLD_LEAD =
  "Nothing in here changes what the mesh is of — only how finely it is built.";

/** A row's readout, under its control: what gets sent, and what happens if
 *  nothing is (the raw, in the composer fold's words). An unverified range
 *  says so on the same line rather than in a badge elsewhere: it is a fact
 *  about THIS control, and law 12 wants it where the hand is. */
export function meshKnobLine(
  knob: MeshKnob,
  value: string | number | null,
): string {
  if (!knob.supported) return "NO HANDLE ON THIS ENGINE";
  const def =
    knob.fallback === null
      ? "THE ENGINE'S OWN DEFAULT"
      : `DEFAULT ${knob.fallback}`;
  const state = knob.unverified ? ` · RANGE ${UNVERIFIED}` : "";
  return value === null
    ? `${def}${state}`
    : `SENDS ${value} · ${def}${state}`;
}

// ── the seed (the third handle) ─────────────────────────────────────────
//
// Not a `params` key: the runner takes a seed as its own field and both mesh
// engines publish `seed: verified` in the matrix, so this rides the run's
// seed POLICY exactly as the composer's SEED dial does.

export type MeshSeed = { locked: boolean; value: number };

export const MESH_SEED_DEFAULT: MeshSeed = { locked: false, value: 424242 };

export const MESH_SEED_LINE =
  "Unlocked, every take rolls its own number and the takes differ. Locked, " +
  "the same image and the same handles give the same mesh again — which is " +
  "how you change one knob and see only that knob's difference.";

/** The run's seed field. A locked seed with an unusable number falls back to
 *  random rather than sending a value the route would refuse in words. */
export function meshSeedPolicy(
  seed: MeshSeed,
): { mode: "random" } | { mode: "locked"; value: number } {
  if (!seed.locked) return { mode: "random" };
  const v = Math.floor(seed.value);
  if (!Number.isFinite(v) || v < 0) return { mode: "random" };
  return { mode: "locked", value: v };
}

/** Why RUN is disarmed, in words — null means it can fire. Mirrors the
 *  server's refusals; the server re-checks every one of them. */
export function meshGate(args: {
  roster: readonly MeshRow[];
  rosterReady: boolean;
  counts: MeshTakes;
  health: MeshHealth | undefined;
}): string | null {
  if (!args.rosterReady) return "The engine roster isn't loaded yet.";
  if (args.roster.length === 0) return MATRIX_NO_MESH;
  if (args.health !== undefined && !args.health.ok)
    // one cause, one message (law 1) — the rows already carry it, and it is
    // the reason the button can't fire, so it is said here too
    return args.health.why ?? RUNNER_DOWN_FALLBACK;
  const total = sumMeshTakes(args.counts);
  if (total === 0) return "Step an engine above zero takes.";
  if (total > MESH_MAX_TAKES)
    return `A mesh run caps at ${MESH_MAX_TAKES} takes — this asks for ${total}.`;
  return null;
}

/** The run bar's one line: counts, and the fact that decides how long the
 *  artist waits — meshes take the GPU one at a time, in minutes. No
 *  predicted total (law 17); the queue strip's clock is the observed truth. */
export function meshBarLine(counts: MeshTakes): string {
  let takes = 0;
  let engines = 0;
  for (const n of counts.values())
    if (n > 0) {
      takes += n;
      engines += 1;
    }
  const t = `${takes} MESH${takes === 1 ? "" : "ES"}`;
  const e = `${engines} ENGINE${engines === 1 ? "" : "S"}`;
  return `${t} · ${e} · NO SPEND`;
}

/** The plain sentence under the counts — law 13's register, applied to a
 *  lane whose unit is minutes. Locals share the one GPU, and a mesh queues
 *  behind an image take exactly as an image take does (law 14). */
export const MESH_MINUTES_LINE =
  "Meshes take the single GPU one at a time, in minutes — they queue behind " +
  "any image take already on it, and it behind them.";

export function meshRunLabel(counts: MeshTakes): string {
  const total = sumMeshTakes(counts);
  return `To 3D · ${total}`;
}

/** Where the mesh is born: BESIDE ITS SOURCE, one column pitch right —
 *  the rule the arm bridge applies to a refine (Canvas.tsx), computed from
 *  the same two facts the canvas draws from, so the plinth needs no seam
 *  into React Flow. A dragged source has its own persisted point; one that
 *  was never dragged sits where the fallback layout put it. */
export function meshAnchor(
  source: Node,
  turns: readonly Turn[],
): { x: number; y: number } {
  const at =
    source.x !== null && source.y !== null
      ? { x: source.x, y: source.y }
      : fallbackPosition(source, new Map(turns.map((t) => [t.id, t])));
  return { x: at.x + TURN_COL, y: at.y };
}

/** The request body (MeshRun): one source, one row per stepped engine.
 *
 *  It carries each engine's own set knobs, and a seed the artist may lock.
 *  Only knobs that were TOUCHED travel: an untouched fold sends `{}` and
 *  every engine runs the recipe it ships with. The runner whitelists each
 *  key by name for that (kind, engine) pair and refuses an unknown one in
 *  words, so nothing here reaches argv raw. */
export function buildMeshRun(args: {
  source: Node;
  counts: MeshTakes;
  turns: readonly Turn[];
  knobs?: MeshKnobs;
  seed?: MeshSeed;
}): {
  parents: string[];
  kind: string;
  mesh: {
    engine_id: string;
    takes: number;
    params: Record<string, string | number>;
  }[];
  seed: { mode: "random" } | { mode: "locked"; value: number };
  anchor: { x: number; y: number };
} {
  const knobs = args.knobs ?? new Map();
  return {
    parents: [args.source.id],
    // Law 16: the plinth is the image -> mesh verb. Named rather than
    // left to the server's default, so the request says which verb it is.
    kind: "generate",
    mesh: [...args.counts]
      .filter(([, takes]) => takes > 0)
      .map(([engine_id, takes]) => ({
        engine_id,
        takes,
        params: { ...(knobs.get(engine_id) ?? {}) },
      })),
    seed: meshSeedPolicy(args.seed ?? MESH_SEED_DEFAULT),
    anchor: meshAnchor(args.source, args.turns),
  };
}

/** The server's refusals are already plain words with the cost stated —
 *  pass them through verbatim. The fallback carries the same two facts
 *  (law 3: no status codes; law 5: say what it cost). */
export function meshErr(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  return "The 3D run didn't start — nothing was queued and nothing was spent.";
}
