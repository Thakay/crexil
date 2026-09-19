// The DECIMATE plinth's pure half: the target handle, the force override,
// the run body, and what a landed child's two numbers say about the
// request that made it. No React; vitest exercises every line directly,
// the same split mesh.ts and regions/fanout.ts use.
//
// One fact shapes this whole file: the triangle target is a REQUEST the
// mesh may refuse. A textured mesh carries a machine UV atlas that cuts
// the surface into charts, and every chart boundary splits its vertices
// (~1.4× on a TRELLIS.2 mesh); a quadric simplifier locks attribute seams
// because collapsing them tears the texture mapping. Ask for 15,000 of
// 950,000 and you get ~79,000; raising the error budget barely moves it.
// Roughly 8–10% of source is the floor the atlas imposes, and no flag
// argues with it (funnel.py `decimate` carries the working). So this
// surface never promises the number: it asks for one, says plainly that
// the mesh answers, and the child prints what it reached — which is why
// `decimateOutcome` exists at all.
//
// The verb has a SECOND engine that answers the same question a different
// way: TRELLIS.2 does not simplify the finished mesh — it re-extracts from
// the voxel field the parent take kept, remeshing, re-unwrapping and
// re-baking. So it has no seam floor (the only route below gltfpack's
// ~8–10%), it re-bakes the texture rather than leaving it alone, it costs
// GPU seconds rather than CPU milliseconds, and it can only serve a
// TRELLIS.2 mesh that kept its field. Every one of those differences is a
// sentence on this surface rather than a surprise in the child — which is
// why the copy below is per engine and not per verb.
//
// Every string here is curated static text; the ranges mirror the runner's
// own whitelist, nothing is guessed. DECIMATE's two handles are
// deliberately not in the matrix's `params` spec: the target IS this verb's
// shape and `force` is a question about seams rather than a setting. If a
// later surface needs them published, the spec is where they go, and these
// constants become its readers.
import type { MeshHealth, Node, Turn } from "../../types";
import { MATRIX_NO_MESH, RUNNER_DOWN_FALLBACK, meshAnchor } from "./mesh";
import type { MeshRow } from "./mesh";

/** The runner's own bounds for `target_triangles`, mirrored for the face
 *  (`tools/3d/runner.py` KINDS.decimate → gltfpack). The server re-checks
 *  both; this is display truth, not the gate. */
export const DECIMATE_MIN = 100;
export const DECIMATE_MAX = 4_000_000;

/** A deterministic verb has one answer, so a second take would be a copy
 *  wearing the word "take" — the run route refuses it in those words and
 *  the stepper stops here rather than letting the artist find out. */
export const DECIMATE_MAX_TAKES = 1;

/** Which DECIMATE engines re-extract rather than simplify — mirroring the
 *  route's `MESH_KIND_INPUTS` (`app/mesh.py`), which is itself the runner's
 *  mirror. Display truth, not the gate: the run route re-checks that this
 *  mesh actually kept a field and refuses in its own words. */
export const FIELDS_ENGINES = ["trellis2-4b"];

export function reExtracts(engineId: string | null): boolean {
  return engineId !== null && FIELDS_ENGINES.includes(engineId);
}

/** The texture sizes the runner accepts for a re-bake (`KINDS.decimate →
 *  trellis2`). Only re-extraction has this handle at all: a simplifier
 *  leaves the texture it was given. */
export const TEXTURE_SIZES = [1024, 2048, 4096] as const;
export const TEXTURE_DEFAULT = 2048;

/** Why a re-extraction engine can't take THIS mesh — null when it can.
 *  The roster's other two dashing reasons are the engine's (it doesn't
 *  declare the verb) and the lane's (the runner is down); this one is the
 *  SOURCE's, and it is the only one the artist fixes by picking the other
 *  engine rather than by waiting. */
export function noFieldsReason(
  engineId: string,
  engineName: string,
  source: { mesh?: { fields_kept?: boolean } | null } | undefined,
): string | null {
  if (!reExtracts(engineId) || source === undefined) return null;
  if (source.mesh?.fields_kept === true) return null;
  return (
    `${engineName} rebuilds from the voxel field a take kept — this mesh ` +
    "has none, so gltfpack is the one that can shrink it"
  );
}

export const DECIMATE_ONE_TAKE_LINE =
  "One take: the same mesh and the same target give the same child every " +
  "time, so a second would be a copy, not another answer.";

/** gltfpack is a binary, not a model — no weights to load, no GPU. It still
 *  takes its turn on the local lane (one worker, inline — the scheduler
 *  serialises across executors), so this says what it costs without
 *  claiming it jumps the queue. */
export const DECIMATE_SECONDS_LINE =
  "gltfpack is a CPU tool — under a second on a million triangles. It takes " +
  "the local lane's turn like any take and gives it straight back.";

/** Re-extraction's own cost line. It IS on the card, unlike gltfpack, so it
 *  serialises against every other heavy job — but it loads no weights, which
 *  is why it is seconds rather than the minutes a fresh take costs. */
export const EXTRACT_SECONDS_LINE =
  "Re-extraction runs on the GPU and takes the local lane's turn: about " +
  "12 s at 15,000 triangles, about 45 s at a million (measured). No model " +
  "loads — the field is already here, which is the whole saving against " +
  "generating the mesh again.";

/** The handle's own line: what the number means, before it is typed. */
export const DECIMATE_TARGET_LINE =
  "A request, not a setting — the mesh answers with what its UV seams allow, " +
  "and the child prints the count it reached.";

/** And the same line for the engine that has no seams to be locked by. Said
 *  plainly rather than by omission: an artist who read the gltfpack sentence
 *  first needs to be told this one is different. */
export const EXTRACT_TARGET_LINE =
  "This one lands where you ask — re-extraction remeshes from scratch, so " +
  "there is no seam floor to refuse at. Measured: 15,000 asked, 14,689 " +
  "reached, where gltfpack on the same mesh stopped at 79,170.";

/** What the verb touches, per engine — law 3's register. gltfpack's answer
 *  is "nothing but topology"; re-extraction's is the opposite and it would
 *  be a lie to leave the artist reading the first one. */
export const DECIMATE_TOUCHES_LINE =
  "Topology only — the texture, the materials and the shape stay as they " +
  "are. The parent is untouched; this lands a second, lighter mesh beside " +
  "it.";

export const EXTRACT_TOUCHES_LINE =
  "A fresh bake, not a thinning — the mesh is rebuilt from the voxel field " +
  "this take kept, so its topology, its UVs and its texture are all new. " +
  "The parent is untouched; this lands a second mesh beside it.";

/** The texture handle's line — the thing re-extraction can do that no
 *  simplifier can, said where the handle is. */
export const EXTRACT_TEXTURE_LINE =
  "The texture is baked again at this size. 4096 doubles the bake; 1024 is " +
  "the one to take when the mesh is going into a crowd.";

// The two faces of the override. `-sa` is not a quality dial with a bad
// setting; it is a different question, so both states get a sentence rather
// than one state getting a warning.
export const FORCE_OFF_LINE =
  "Off: the mesh stops where its seams stop. A machine-atlas mesh floors " +
  "around a tenth of its triangles — you get that, with the texture intact.";

export const FORCE_ON_LINE =
  "On: gltfpack hits the number exactly by collapsing seams the texture " +
  "needs. Measured on a TRELLIS.2 prop: 952,326 → 13,309 gave shattered " +
  "facets and texture smeared into noise. Take it when the count is a hard " +
  "budget and the mesh is going to a DCC anyway.";

/** Rounded to two significant figures — a target is typed by a person, and
 *  95,000 is a number a person would have typed. */
function round2(n: number): number {
  if (n <= 0) return 0;
  const mag = 10 ** (Math.floor(Math.log10(n)) - 1);
  return Math.round(n / mag) * mag;
}

/** Where the field starts: a tenth of the parent — about where a machine
 *  atlas stops anyway, so the opening ask is one the mesh can usually
 *  answer. Null when the parent's count was never recorded: there is
 *  nothing to take a tenth OF, and inventing one would be the guess law 12
 *  forbids. The artist types a number instead. */
export function decimateDefault(parentTriangles: number | null): number | null {
  if (parentTriangles === null || parentTriangles <= 0) return null;
  const tenth = round2(parentTriangles / 10);
  return Math.max(DECIMATE_MIN, Math.min(DECIMATE_MAX, tenth));
}

/** Why RUN is disarmed, in words — null means it can fire. Mirrors the
 *  server's refusals; the server re-checks every one of them. */
export function decimateGate(args: {
  roster: readonly MeshRow[];
  rosterReady: boolean;
  engineId: string | null;
  target: number | null;
  parentTriangles: number | null;
  health: MeshHealth | undefined;
}): string | null {
  if (!args.rosterReady) return "The engine roster isn't loaded yet.";
  if (args.roster.length === 0) return MATRIX_NO_MESH;
  if (!args.roster.some((r) => r.capable)) {
    // whichever row carries it, the reason is already worded once — say the
    // same sentence here rather than a second one about the same cause
    if (args.health !== undefined && !args.health.ok)
      return args.health.why ?? RUNNER_DOWN_FALLBACK;
    return "No engine here does decimate.";
  }
  if (args.engineId === null) return "Step the engine above zero takes.";
  if (args.target === null) return "Type a target triangle count.";
  if (args.target < DECIMATE_MIN || args.target > DECIMATE_MAX)
    return (
      `The target has to be between ${DECIMATE_MIN.toLocaleString("en-US")} ` +
      `and ${DECIMATE_MAX.toLocaleString("en-US")} triangles — this asks ` +
      `for ${args.target.toLocaleString("en-US")}.`
    );
  if (
    args.parentTriangles !== null &&
    args.target >= args.parentTriangles
  )
    return (
      `This mesh has ${args.parentTriangles.toLocaleString("en-US")} ` +
      "triangles — decimate only removes, so ask for fewer."
    );
  return null;
}

/** The run bar's one line — the same shape the To-3D plinth's carries, and
 *  the same NO SPEND: a local tool costs GPU-or-CPU time, never money. */
export function decimateBarLine(engineId: string | null): string {
  return engineId === null
    ? "0 MESHES · 0 ENGINES · NO SPEND"
    : "1 MESH · 1 ENGINE · NO SPEND";
}

export function decimateRunLabel(engineId: string | null): string {
  return `Decimate · ${engineId === null ? 0 : 1}`;
}

/** The request body (MeshRun) for the decimate verb.
 *
 *  `force` goes out even when false. The runner reads a false switch as
 *  "don't pass the flag", and the as-run record then says the override was
 *  considered and declined — which is what lets a child's facts line print
 *  an un-forced count as un-forced rather than as unknown. */
export function buildDecimateRun(args: {
  source: Node;
  engineId: string;
  target: number;
  force: boolean;
  textureSize: number;
  turns: readonly Turn[];
}): {
  parents: string[];
  kind: string;
  mesh: {
    engine_id: string;
    takes: number;
    params: Record<string, number | boolean>;
  }[];
  seed: { mode: "random" };
  anchor: { x: number; y: number };
} {
  // One verb, two engines, two whitelists. Sending `force` to the
  // re-extractor would be refused by name at the runner's door, and sending
  // `texture_size` to gltfpack likewise — so the body is built per engine
  // rather than per verb.
  const params: Record<string, number | boolean> = reExtracts(args.engineId)
    ? { target_triangles: args.target, texture_size: args.textureSize }
    : { target_triangles: args.target, force: args.force };
  return {
    parents: [args.source.id],
    kind: "decimate",
    mesh: [
      {
        engine_id: args.engineId,
        takes: DECIMATE_MAX_TAKES,
        params,
      },
    ],
    // The route sends no seed for a seedless kind and records none; the
    // field is the envelope's, not this verb's, so it carries the default.
    seed: { mode: "random" as const },
    anchor: meshAnchor(args.source, args.turns),
  };
}

/** How close counts as met. gltfpack simplifies to a ratio, so it lands
 *  near the ask rather than on it even when nothing refuses — 2% is the
 *  width of that arithmetic, not a quality judgement. */
const MET_TOLERANCE = 1.02;

/** What a landed child's two numbers say about the request that made it —
 *  null for any node that was not decimated (nothing was asked, so there is
 *  nothing to report). This is the whole reason `asked_triangles` is
 *  projected: a line printing only the count would be honest about the
 *  artefact and silent about the refusal. */
export function decimateOutcome(m: {
  triangles?: number | null;
  asked_triangles?: number | null;
  forced?: boolean;
  engine?: string | null;
}): string | null {
  const asked = m.asked_triangles;
  if (asked == null) return null;
  const n = (v: number) => v.toLocaleString("en-US");
  const got = m.triangles;
  if (got == null) return `ASKED ${n(asked)}`;
  const missed = got > Math.round(asked * MET_TOLERANCE);
  if (m.forced === true)
    return missed
      ? `FORCED TO ${n(asked)} — IT REACHED ${n(got)}`
      : `FORCED TO ${n(asked)}`;
  // The refusal sentence is gltfpack's — it is about seams a simplifier
  // cannot collapse. Re-extraction has none, so a miss there is the
  // remesher landing near the ask, not the mesh refusing. The
  // engine name is the runner's, which is what the record carries.
  if (missed)
    return m.engine === "trellis2"
      ? `ASKED ${n(asked)} — IT CAME OUT AT ${n(got)}`
      : `ASKED ${n(asked)} — THIS MESH WOULD NOT GO BELOW ${n(got)}`;
  return `ASKED ${n(asked)}`;
}
