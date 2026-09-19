// The TEXTURE core: the reference picker (candidates and the
// ancestry default), every disarm reason, and the MeshRun serialization
// with its second parent. The honesty itself — Paint's 40k rebuild — is
// curated copy; what these tests hold is the machinery under it.
import { describe, expect, it } from "vitest";
import type { Engines, MeshHealth, Node, Turn } from "../../types";
import { TURN_COL } from "../canvas/graph";
import { meshRoster } from "./mesh";
import {
  NO_REFERENCE_LINE,
  TEXTURE_MAX_TAKES,
  buildTextureRun,
  imageAncestor,
  referenceCandidates,
  textureBarLine,
  textureGate,
  textureRunLabel,
} from "./texture";

const DOWN_WHY =
  "the 3D engine runner isn't answering — start it with " +
  "`systemctl --user start 3d-runner`";

function engineRow(
  id: string,
  name: string,
  verbs: string[],
): Engines["engines"][number] {
  return {
    id,
    name,
    lane: "local",
    payload: "3d",
    verbs,
    price: "free — local",
    note: null,
    capabilities: {},
  } as Engines["engines"][number];
}

function engines(): Engines {
  return {
    updated: "2026-08-30",
    engines: [
      engineRow("trellis2-4b", "TRELLIS.2 4B", ["generate", "decimate"]),
      engineRow("hunyuan3d-21", "Hunyuan3D 2.1", ["generate", "texture"]),
      engineRow("gltfpack", "gltfpack (meshopt)", ["decimate"]),
    ],
    external: {
      catalogue: "openrouter",
      defaults: {},
      lane: "external",
      payload: "image",
      price: "per response",
    },
  } as Engines;
}

const UP: MeshHealth = {
  ok: true,
  url: "http://host:8189",
  engines: ["gltfpack", "hunyuan3d-21", "trellis2-4b"],
  kinds: {
    generate: ["hunyuan3d-21", "trellis2-4b"],
    decimate: ["gltfpack", "trellis2-4b"],
    texture: ["hunyuan3d-21"],
  },
  why: null,
};
const DOWN: MeshHealth = { ...UP, ok: false, why: DOWN_WHY };

const ROSTER = () => meshRoster(engines(), UP, "texture");

function node(patch: Partial<Node> = {}): Node {
  return {
    id: "m1",
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "3d",
    verb: "to_3d",
    prompt: "a knight",
    engine: "trellis2-4b",
    seed: null,
    duration_ms: 120000,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: 400,
    y: 120,
    created_at: "2026-08-30T00:00:00Z",
    parents: ["src"],
    turn_idx: 4,
    slot_pos: 0,
    promoted_at: null,
    mesh: null,
    ...patch,
  } as Node;
}

const img = (id: string, patch: Partial<Node> = {}) =>
  node({ id, payload: "image", parents: [], ...patch });

describe("the roster for this verb", () => {
  it("keeps every 3D engine listed and dashes the ones that don't paint", () => {
    const rows = ROSTER();
    expect(rows.map((r) => r.engine.id)).toEqual([
      "trellis2-4b",
      "hunyuan3d-21",
      "gltfpack",
    ]);
    const hy = rows.find((r) => r.engine.id === "hunyuan3d-21");
    expect(hy?.capable).toBe(true);
    const tr = rows.find((r) => r.engine.id === "trellis2-4b");
    expect(tr?.capable).toBe(false);
    expect(tr?.reason).toMatch(/doesn't do texture/);
    expect(tr?.reason).toMatch(/Hunyuan3D 2.1 does/);
  });
});

describe("the reference candidates", () => {
  it("offers canvas image takes, newest turn first", () => {
    const nodes = [
      img("a", { turn_idx: 1 }),
      img("b", { turn_idx: 3 }),
      node({ id: "m", turn_idx: 2 }),          // the mesh is never a reference
      img("c", { turn_idx: 3, slot_pos: 1 }),
    ];
    expect(referenceCandidates(nodes).map((n) => n.id)).toEqual([
      "b",
      "c",
      "a",
    ]);
  });

  it("leaves set-aside takes out — the artist put them out of the way", () => {
    const nodes = [img("a"), img("b", { set_aside: true })];
    expect(referenceCandidates(nodes).map((n) => n.id)).toEqual(["a"]);
  });
});

describe("the ancestry default", () => {
  it("finds the image the mesh was generated from", () => {
    const nodes = [img("src"), node({ id: "m1", parents: ["src"] })];
    expect(imageAncestor(nodes[1], nodes)).toBe("src");
  });

  it("walks up through mesh-verb parents (a decimated mesh)", () => {
    const nodes = [
      img("src"),
      node({ id: "gen", parents: ["src"] }),
      node({ id: "dec", verb: "decimate", parents: ["gen"] }),
    ];
    expect(imageAncestor(nodes[2], nodes)).toBe("src");
  });

  it("answers null when the ancestor is gone or set aside — nothing is invented", () => {
    expect(imageAncestor(node({ parents: ["gone"] }), [node()])).toBeNull();
    const aside = [
      img("src", { set_aside: true }),
      node({ id: "m1", parents: ["src"] }),
    ];
    expect(imageAncestor(aside[1], aside)).toBeNull();
    expect(imageAncestor(undefined, [])).toBeNull();
  });
});

describe("the gate", () => {
  const gate = (over: Record<string, unknown> = {}) =>
    textureGate({
      roster: ROSTER(),
      rosterReady: true,
      engineId: "hunyuan3d-21",
      referenceId: "src",
      candidates: 2,
      health: UP,
      ...over,
    });

  it("fires once the engine and the reference are there", () => {
    expect(gate()).toBeNull();
  });

  it("waits for the roster rather than claiming the lane is empty", () => {
    expect(gate({ rosterReady: false })).toMatch(/isn't loaded yet/);
  });

  it("carries the server's own down-reason when the runner is silent", () => {
    const down = meshRoster(engines(), DOWN, "texture");
    expect(gate({ roster: down, health: DOWN })).toBe(DOWN_WHY);
  });

  it("asks for an engine, then a reference, each in its own words", () => {
    expect(gate({ engineId: null })).toMatch(/Step the engine/);
    expect(gate({ referenceId: null })).toMatch(/Pick the image/);
    expect(gate({ referenceId: null, candidates: 0 })).toBe(
      NO_REFERENCE_LINE,
    );
  });
});

describe("the run body", () => {
  it("carries the mesh as the parent and the reference as its own field", () => {
    const source = node();
    const body = buildTextureRun({
      source,
      engineId: "hunyuan3d-21",
      referenceId: "ref-1",
      turns: [] as Turn[],
    });
    expect(body.parents).toEqual(["m1"]);
    expect(body.kind).toBe("texture");
    expect(body.reference).toBe("ref-1");
    expect(body.mesh).toEqual([
      { engine_id: "hunyuan3d-21", takes: TEXTURE_MAX_TAKES, params: {} },
    ]);
    // born beside its parent, one column right — the refine rule
    expect(body.anchor).toEqual({ x: 400 + TURN_COL, y: 120 });
  });
});

describe("the bar", () => {
  it("counts one deterministic take and no spend", () => {
    expect(textureBarLine(null)).toBe("0 MESHES · 0 ENGINES · NO SPEND");
    expect(textureBarLine("hunyuan3d-21")).toBe(
      "1 MESH · 1 ENGINE · NO SPEND",
    );
    expect(textureRunLabel(null)).toBe("Texture · 0");
    expect(textureRunLabel("hunyuan3d-21")).toBe("Texture · 1");
  });
});
