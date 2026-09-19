// The TEXTURE plinth's pure half (law 16): the reference
// picker, the disarm reasons, the run body, and the honesty lines. No React;
// vitest exercises every line directly, the same split mesh.ts and
// decimate.ts use.
//
// Two facts shape this file, both measured (bench 2026-08-30):
//
// **The verb has TWO parents.** The acted-on mesh is the shape; a reference
// image is the appearance — re-texture the same shape from a re-painted
// concept, shape locked, appearance branching. The reference is a real
// parent node (the child records both), picked here because the verb row's
// selection names only the mesh.
//
// **The child carries Paint's topology, never its parent's.** Hunyuan3D-
// Paint simplifies its input to 40,000 faces and re-unwraps with xatlas
// before painting — upstream's own hard default, and skipping it cost 25+
// minutes of single-threaded unwrap on a 970k mesh before painting even
// began. So this surface says plainly: the child is an honest 40k repaint,
// its facts line prints ITS count, and a texture-at-full-density is not
// offered because it does not honestly exist at interactive cost.
//
// every string here is curated static text.
// law 12 — nothing is promised the runner would refuse.
import type { MeshHealth, Node, Turn } from "../../types";
import { MATRIX_NO_MESH, RUNNER_DOWN_FALLBACK, meshAnchor } from "./mesh";
import type { MeshRow } from "./mesh";

/** Deterministic, like decimate: Paint hard-seeds its diffusion, so the
 *  same (mesh, reference) pair gives the same child every time. */
export const TEXTURE_MAX_TAKES = 1;

export const TEXTURE_TOUCHES_LINE =
  "A repaint, not an edit — the mesh is rebuilt at 40,000 faces with fresh " +
  "UVs, then painted from the reference: albedo, metallic and roughness, " +
  "all new. The parent is untouched; the repainted child lands beside it.";

export const TEXTURE_REFERENCE_LINE =
  "The reference is the appearance — the shape stays this mesh's. Repaint " +
  "the concept, then repaint the mesh from it: shape locked, look branching.";

export const TEXTURE_ONE_TAKE_LINE =
  "One take: Paint seeds itself, so the same mesh and the same reference " +
  "give the same child — a different look comes from a different reference.";

/** Cost, in the register the other plinths use — observed, not promised. */
export const TEXTURE_MINUTES_LINE =
  "Painting runs on the GPU and takes the local lane's turn — about a " +
  "minute and a half end to end (load, six views, bake; measured), and the " +
  "heaviest VRAM step in the lane.";

export const NO_REFERENCE_LINE =
  "Pick the image to paint from — this workspace has no image takes yet, " +
  "so there is nothing to point the painter at.";

/** Image nodes this workspace can offer as the appearance parent — canvas
 *  takes only (a set-aside take is out of the way by the artist's own
 *  hand), newest turn first so the repainted concept the artist just made
 *  is the first thing under the pointer. */
export function referenceCandidates(nodes: readonly Node[]): Node[] {
  return nodes
    .filter((n) => n.payload === "image" && !n.set_aside)
    .sort(
      (a, b) =>
        (b.turn_idx ?? -1) - (a.turn_idx ?? -1) ||
        (a.slot_pos ?? 0) - (b.slot_pos ?? 0),
    );
}

/** The mesh's own image ancestor — the take it was generated from, walked
 *  up through mesh-verb parents (a decimated mesh's parent is a mesh). The
 *  natural default: repainting from the original reference reproduces the
 *  look Paint would have given at generate time, and swapping is the verb's
 *  point. Null when no ancestor is offerable — the artist picks by hand. */
export function imageAncestor(
  source: Node | undefined,
  nodes: readonly Node[],
): string | null {
  if (source === undefined) return null;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const seen = new Set<string>();
  let frontier = source.parents;
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const pid of frontier) {
      if (seen.has(pid)) continue;
      seen.add(pid);
      const p = byId.get(pid);
      if (p === undefined) continue;
      if (p.payload === "image" && !p.set_aside) return p.id;
      next.push(...p.parents);
    }
    frontier = next;
  }
  return null;
}

/** Why RUN is disarmed, in words — null means it can fire. Mirrors the
 *  server's refusals; the server re-checks every one of them. */
export function textureGate(args: {
  roster: readonly MeshRow[];
  rosterReady: boolean;
  engineId: string | null;
  referenceId: string | null;
  candidates: number;
  health: MeshHealth | undefined;
}): string | null {
  if (!args.rosterReady) return "The engine roster isn't loaded yet.";
  if (args.roster.length === 0) return MATRIX_NO_MESH;
  if (!args.roster.some((r) => r.capable)) {
    if (args.health !== undefined && !args.health.ok)
      return args.health.why ?? RUNNER_DOWN_FALLBACK;
    return "No engine here does texture.";
  }
  if (args.engineId === null) return "Step the engine above zero takes.";
  if (args.candidates === 0) return NO_REFERENCE_LINE;
  if (args.referenceId === null) return "Pick the image to paint from.";
  return null;
}

export function textureBarLine(engineId: string | null): string {
  return engineId === null
    ? "0 MESHES · 0 ENGINES · NO SPEND"
    : "1 MESH · 1 ENGINE · NO SPEND";
}

export function textureRunLabel(engineId: string | null): string {
  return `Texture · ${engineId === null ? 0 : 1}`;
}

/** The request body (MeshRun) for the texture verb: the mesh in
 *  `parents`, the appearance in `reference` — its own field because the two
 *  parents mean different things, and the child records both, in that
 *  order. No params (Paint runs its own defaults). */
export function buildTextureRun(args: {
  source: Node;
  engineId: string;
  referenceId: string;
  turns: readonly Turn[];
}): {
  parents: string[];
  kind: string;
  reference: string;
  mesh: { engine_id: string; takes: number; params: Record<string, never> }[];
  seed: { mode: "random" };
  anchor: { x: number; y: number };
} {
  return {
    parents: [args.source.id],
    kind: "texture",
    reference: args.referenceId,
    mesh: [
      {
        engine_id: args.engineId,
        takes: TEXTURE_MAX_TAKES,
        params: {} as Record<string, never>,
      },
    ],
    // the route sends no seed for a seedless kind and records none; the
    // field is the envelope's, not this verb's, so it carries the default
    seed: { mode: "random" as const },
    anchor: meshAnchor(args.source, args.turns),
  };
}
