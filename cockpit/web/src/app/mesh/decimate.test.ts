// The DECIMATE core: where the target field starts, every disarm
// reason against the server's own caps, the MeshRun serialization, and the
// one thing this verb exists to be honest about — that the target is a
// REQUEST, so a child's two numbers have to be readable together.
import { describe, expect, it } from "vitest";
import type { Engines, MeshHealth, Node, Turn } from "../../types";
import { TURN_COL } from "../canvas/graph";
import { MATRIX_NO_MESH, RUNNER_DOWN_FALLBACK, meshRoster } from "./mesh";
import {
  DECIMATE_MAX,
  DECIMATE_MAX_TAKES,
  DECIMATE_MIN,
  buildDecimateRun,
  decimateBarLine,
  decimateDefault,
  decimateGate,
  decimateOutcome,
  decimateRunLabel,
  noFieldsReason,
  reExtracts,
} from "./decimate";

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
    updated: "2026-08-29",
    engines: [
      engineRow("trellis2-4b", "TRELLIS.2 4B", ["generate"]),
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
  engines: ["gltfpack", "trellis2-4b"],
  kinds: { generate: ["trellis2-4b"], decimate: ["gltfpack"] },
  why: null,
};
const DOWN: MeshHealth = { ...UP, ok: false, why: DOWN_WHY };

const ROSTER = () => meshRoster(engines(), UP, "decimate");

function mesh(patch: Partial<Node> = {}): Node {
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
    created_at: "2026-08-29T00:00:00Z",
    parents: ["src"],
    turn_idx: 4,
    slot_pos: 0,
    promoted_at: null,
    mesh: null,
    ...patch,
  } as Node;
}

const gate = (over: Record<string, unknown> = {}) =>
  decimateGate({
    roster: ROSTER(),
    rosterReady: true,
    engineId: "gltfpack",
    target: 95_000,
    parentTriangles: 952_326,
    health: UP,
    ...over,
  });

describe("where the field starts", () => {
  // a tenth is about where a machine-atlas mesh floors anyway, so the
  // opening ask is one the mesh can usually answer
  it("opens at a tenth of the parent, rounded like a typed number", () => {
    expect(decimateDefault(952_326)).toBe(95_000);
    expect(decimateDefault(406_304)).toBe(41_000);
    expect(decimateDefault(40_000)).toBe(4_000);
  });

  it("never opens outside the runner's own bounds", () => {
    expect(decimateDefault(500)).toBe(DECIMATE_MIN);
    expect(decimateDefault(900_000_000)).toBe(DECIMATE_MAX);
  });

  // law 12: there is nothing to take a tenth OF, so nothing is invented — the
  // artist types a number and the gate says so until they do
  it("stays empty when the parent's count was never recorded", () => {
    expect(decimateDefault(null)).toBeNull();
    expect(decimateDefault(0)).toBeNull();
  });
});

describe("the gate", () => {
  it("fires once the roster, the engine and a workable target are there", () => {
    expect(gate()).toBeNull();
  });

  it("waits for the roster rather than claiming the lane is empty", () => {
    expect(gate({ rosterReady: false })).toMatch(/isn't loaded yet/);
  });

  it("says the matrix publishes no 3D engine when it publishes none", () => {
    expect(gate({ roster: [] })).toBe(MATRIX_NO_MESH);
  });

  // one cause, one message (law 1): the rows carry the runner's sentence and
  // so does the button, because it is why the button can't fire
  it("passes the server's own words through when the runner is down", () => {
    const roster = meshRoster(engines(), DOWN, "decimate");
    expect(gate({ roster, health: DOWN })).toBe(DOWN_WHY);
    expect(gate({ roster, health: { ...DOWN, why: null } })).toBe(
      RUNNER_DOWN_FALLBACK,
    );
  });

  it("says so plainly when no engine here does this verb at all", () => {
    const roster = meshRoster(engines(), UP, "texture");
    expect(gate({ roster, health: UP })).toBe("No engine here does decimate.");
  });

  it("asks for an engine, then for a number", () => {
    expect(gate({ engineId: null })).toMatch(/above zero takes/);
    expect(gate({ target: null })).toMatch(/Type a target/);
  });

  // mirrors the runner's whitelist (_int_in(100, 4_000_000)); the server
  // re-checks it, so this is the face saying it first
  it("holds the runner's bounds, and names them", () => {
    expect(gate({ target: 99 })).toMatch(/between 100 and 4,000,000/);
    expect(gate({ target: 4_000_001 })).toMatch(/asks for 4,000,001/);
  });

  // decimate only removes — asking for more triangles than the mesh has is
  // not a small target, it is the wrong verb
  it("refuses a target at or above the parent's own count", () => {
    expect(gate({ target: 952_326 })).toMatch(/decimate only removes/);
    expect(gate({ target: 1_000_000 })).toMatch(/has 952,326 triangles/);
    // ...and cannot judge what it wasn't told
    expect(gate({ parentTriangles: null })).toBeNull();
  });
});

describe("the run", () => {
  it("posts one take of the decimate verb, with the target and the override", () => {
    const body = buildDecimateRun({
      source: mesh(),
      engineId: "gltfpack",
      target: 95_000,
      force: false,
      textureSize: 2048,
      turns: [] as Turn[],
    });
    expect(body.kind).toBe("decimate");
    expect(body.parents).toEqual(["m1"]);
    expect(body.mesh).toEqual([
      {
        engine_id: "gltfpack",
        takes: 1,
        params: { target_triangles: 95_000, force: false },
      },
    ]);
    // beside its parent, one column pitch right — the rule every refine uses
    expect(body.anchor).toEqual({ x: 400 + TURN_COL, y: 120 });
  });

  // false goes out explicitly: the runner reads it as "don't pass -sa", and
  // the as-run record then says the override was declined rather than unset,
  // which is what lets a child print an un-forced count AS un-forced
  it("sends the override even when it is off", () => {
    const off = buildDecimateRun({
      source: mesh(),
      engineId: "gltfpack",
      target: 10,
      force: false,
      textureSize: 2048,
      turns: [],
    });
    expect(off.mesh[0].params.force).toBe(false);
    const on = buildDecimateRun({
      source: mesh(),
      engineId: "gltfpack",
      target: 10,
      force: true,
      textureSize: 2048,
      turns: [],
    });
    expect(on.mesh[0].params.force).toBe(true);
  });

  it("caps at one take, because a second would be a copy", () => {
    expect(DECIMATE_MAX_TAKES).toBe(1);
    expect(decimateBarLine("gltfpack")).toBe("1 MESH · 1 ENGINE · NO SPEND");
    expect(decimateBarLine(null)).toBe("0 MESHES · 0 ENGINES · NO SPEND");
    expect(decimateRunLabel("gltfpack")).toBe("Decimate · 1");
    expect(decimateRunLabel(null)).toBe("Decimate · 0");
  });
});

describe("what the child says about the request that made it", () => {
  // the measured example, 2026-08-29: ask for 15,000 of 952,326
  // and a TRELLIS.2 mesh answers 79,170, because its UV atlas locks the
  // seams. The count alone would be honest about the artefact and silent
  // about the refusal.
  it("states the refusal when the mesh would not go that far", () => {
    expect(
      decimateOutcome({
        triangles: 79_170,
        asked_triangles: 15_000,
        forced: false,
      }),
    ).toBe("ASKED 15,000 — THIS MESH WOULD NOT GO BELOW 79,170");
  });

  // Hunyuan's 40k-face output hits its targets exactly; the ask is still
  // printed, and the count beside it shows it was honoured
  it("records the ask alone when the mesh met it", () => {
    expect(
      decimateOutcome({ triangles: 4_000, asked_triangles: 4_000 }),
    ).toBe("ASKED 4,000");
    // a ratio lands near the ask, not on it — 2% is that arithmetic
    expect(
      decimateOutcome({ triangles: 4_070, asked_triangles: 4_000 }),
    ).toBe("ASKED 4,000");
    expect(
      decimateOutcome({ triangles: 4_500, asked_triangles: 4_000 }),
    ).toMatch(/WOULD NOT GO BELOW 4,500/);
  });

  // a forced count is a true number attached to a mesh that paid for it —
  // the word is on the line so it is never read as the un-forced one
  it("says FORCED when the count was taken rather than allowed", () => {
    expect(
      decimateOutcome({
        triangles: 13_309,
        asked_triangles: 13_309,
        forced: true,
      }),
    ).toBe("FORCED TO 13,309");
    expect(
      decimateOutcome({
        triangles: 20_000,
        asked_triangles: 13_309,
        forced: true,
      }),
    ).toBe("FORCED TO 13,309 — IT REACHED 20,000");
  });

  it("says nothing at all about a mesh that was never decimated", () => {
    expect(decimateOutcome({ triangles: 999_588 })).toBeNull();
    expect(decimateOutcome({})).toBeNull();
    // a record that lost its count still reports what was asked
    expect(decimateOutcome({ asked_triangles: 15_000 })).toBe("ASKED 15,000");
  });
});

// --- the second engine answers the same question differently ---

describe("re-extraction", () => {
  const kept = (on: boolean) =>
    mesh({ mesh: { fields_kept: on } as Node["mesh"] });

  it("knows which engines rebuild rather than simplify", () => {
    expect(reExtracts("trellis2-4b")).toBe(true);
    expect(reExtracts("gltfpack")).toBe(false);
    expect(reExtracts(null)).toBe(false);
  });

  // The third dashing reason, and the only one about THIS mesh. It names
  // gltfpack because that is the move the artist makes next — a row that
  // only said "no" would leave them looking for the reason.
  it("dashes the re-extractor on a mesh that kept no field, and says why", () => {
    const why = noFieldsReason("trellis2-4b", "TRELLIS.2 4B", kept(false));
    expect(why).toMatch(/rebuilds from the voxel field/);
    expect(why).toMatch(/gltfpack/);
  });

  it("says nothing about a mesh that kept one, or about the simplifier", () => {
    expect(noFieldsReason("trellis2-4b", "TRELLIS.2 4B", kept(true))).toBeNull();
    expect(noFieldsReason("gltfpack", "gltfpack", kept(false))).toBeNull();
    // and it claims nothing before the node has loaded
    expect(noFieldsReason("trellis2-4b", "TRELLIS.2 4B", undefined)).toBeNull();
  });

  // Each engine's whitelist is its own: the runner refuses an
  // unknown parameter BY NAME, so sending the other engine's handle would
  // be a refusal the artist never asked for.
  it("sends each engine only the handle it has", () => {
    const ex = buildDecimateRun({
      source: mesh(),
      engineId: "trellis2-4b",
      target: 15_000,
      force: true,
      textureSize: 4096,
      turns: [],
    });
    expect(ex.mesh[0].params).toEqual({
      target_triangles: 15_000,
      texture_size: 4096,
    });
    const pack = buildDecimateRun({
      source: mesh(),
      engineId: "gltfpack",
      target: 15_000,
      force: true,
      textureSize: 4096,
      turns: [],
    });
    expect(pack.mesh[0].params).toEqual({
      target_triangles: 15_000,
      force: true,
    });
  });

  // "would not go below" is a sentence about seams a simplifier cannot
  // collapse. Re-extraction has none, so the same overshoot means something
  // else and must not borrow the words.
  it("reports an overshoot as the remesher landing near, not as a refusal", () => {
    const over = { asked_triangles: 15_000, triangles: 79_170, forced: false };
    expect(decimateOutcome({ ...over, engine: "gltfpack" })).toMatch(
      /WOULD NOT GO BELOW/,
    );
    expect(decimateOutcome({ ...over, engine: "trellis2" })).toBe(
      "ASKED 15,000 — IT CAME OUT AT 79,170",
    );
    // and the ordinary landing reads the same either way
    expect(
      decimateOutcome({
        asked_triangles: 15_000,
        triangles: 14_689,
        engine: "trellis2",
      }),
    ).toBe("ASKED 15,000");
  });
});
