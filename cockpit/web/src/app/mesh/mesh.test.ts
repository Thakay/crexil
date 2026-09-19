// The To-3D core: the roster's badge-never-filter derivation
// (law 12) with the runner up and down, the disarm reasons against the
// server's own caps, the run bar's counts, where the mesh is born, and the
// MeshRun serialization.
import { describe, expect, it } from "vitest";
import type { Engines, MeshHealth, Node, Turn } from "../../types";
import { TURN_COL } from "../canvas/graph";
import {
  MESH_MAX_PER_ENGINE,
  MESH_MAX_TAKES,
  MATRIX_NO_MESH,
  RUNNER_DOWN_FALLBACK,
  buildMeshRun,
  clearMeshKnobs,
  meshAnchor,
  meshBarLine,
  meshErr,
  meshFoldChanged,
  meshFoldCount,
  meshFoldHead,
  meshFoldRows,
  meshGate,
  meshKnobLine,
  meshKnobValue,
  meshKnobs,
  meshKnobsChanged,
  meshRoster,
  meshRunLabel,
  setMeshKnob,
  sumMeshTakes,
  type MeshKnobs,
  type MeshSeed,
} from "./mesh";

const DOWN_WHY =
  "the 3D engine runner isn't answering — start it with " +
  "`systemctl --user start 3d-runner`";

function engineRow(
  id: string,
  name: string,
  payload: "image" | "3d",
  // Which verbs the row declares (law 16). An image engine declares
  // none — the image lane's steps are presets, not engine-declared.
  verbs: string[] = payload === "3d" ? ["generate"] : [],
  // The published knob spec, keyed by verb. Empty by default: most
  // rows in these tests are about the roster, not the fold.
  params: Record<string, Record<string, unknown>> = {},
): Engines["engines"][number] {
  return {
    id,
    name,
    lane: "local",
    payload,
    verbs,
    params,
    price: "free — local GPU time",
    note: null,
    capabilities: {
      seed: { value: true, state: "verified", note: "" },
      imageRef: { value: true, state: "verified", note: "" },
      mask: { value: false, state: "verified", note: "" },
    },
  } as Engines["engines"][number];
}

function engines(): Engines {
  return {
    updated: "2026-08-27",
    engines: [
      engineRow("zimage-turbo", "Z-Image Turbo", "image"),
      engineRow("trellis2-4b", "TRELLIS.2 4B", "3d"),
      engineRow("hunyuan3d-21", "Hunyuan3D 2.1", "3d"),
      engineRow("gltfpack", "gltfpack (meshopt)", "3d", ["decimate"]),
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
    decimate: ["gltfpack"],
  },
  why: null,
};
const DOWN: MeshHealth = { ...UP, ok: false, why: DOWN_WHY };

function node(patch: Partial<Node> = {}): Node {
  return {
    id: "n1",
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "image",
    verb: "run",
    prompt: "a knight",
    engine: "zimage-turbo",
    seed: 42,
    duration_ms: 2200,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: null,
    y: null,
    created_at: "2026-08-27T00:00:00Z",
    parents: [],
    turn_idx: 1,
    slot_pos: 0,
    promoted_at: null,
    ...patch,
  } as Node;
}

describe("the roster", () => {
  it("lists every matrix 3D row, in its order, whatever the verb", () => {
    const rows = meshRoster(engines(), UP);
    expect(rows.map((r) => r.engine.id)).toEqual([
      "trellis2-4b",
      "hunyuan3d-21",
      "gltfpack",
    ]);
    //...but only the ones that DECLARE the verb can be stepped (law 16)
    expect(rows.filter((r) => r.capable).map((r) => r.engine.id)).toEqual([
      "trellis2-4b",
      "hunyuan3d-21",
    ]);
  });

  // The second dashing reason, and the more permanent one. gltfpack
  // is a payload-3d engine that cannot generate; without the verbs list it
  // would be drawn as steppable here and the run route would refuse it: a
  // roster offering what the route refuses is the failure law 12 prevents.
  it("dashes an engine that doesn't serve this verb, and names who does", () => {
    const gltf = meshRoster(engines(), UP).find(
      (r) => r.engine.id === "gltfpack",
    );
    expect(gltf?.capable).toBe(false);
    expect(gltf?.reason).toBe(
      "gltfpack (meshopt) doesn't do image → mesh — " +
        "TRELLIS.2 4B · Hunyuan3D 2.1 does",
    );

    // and the mirror: on the decimate verb the generators are the dashed ones
    const dec = meshRoster(engines(), UP, "decimate");
    expect(dec.filter((r) => r.capable).map((r) => r.engine.id)).toEqual([
      "gltfpack",
    ]);
    expect(dec[0].reason).toBe(
      "TRELLIS.2 4B doesn't do decimate — gltfpack (meshopt) does",
    );
  });

  // law 1: one cause, one message — and the cause the artist can act on
  it("prefers the verb reason over the runner's, since only one is actionable", () => {
    const gltf = meshRoster(engines(), DOWN).find(
      (r) => r.engine.id === "gltfpack",
    );
    expect(gltf?.reason).toMatch(/doesn't do image → mesh/);
    expect(gltf?.reason).not.toBe(DOWN_WHY);
  });

  // law 12 — the whole point of this surface's honesty
  it("keeps every row when the runner is down, dashed, with the server's words", () => {
    const rows = meshRoster(engines(), DOWN);
    expect(rows).toHaveLength(3);
    expect(rows.every((r) => !r.capable)).toBe(true);
    expect(rows[0].reason).toBe(DOWN_WHY);
  });

  it("falls back to its own sentence when a down runner names no reason", () => {
    const rows = meshRoster(engines(), { ...DOWN, why: null });
    expect(rows[0].reason).toBe(RUNNER_DOWN_FALLBACK);
  });

  // health that hasn't arrived is not a claim either way
  it("draws the serving rows live while health is still in flight", () => {
    const rows = meshRoster(engines(), undefined);
    expect(rows.filter((r) => r.capable)).toHaveLength(2);
  });

  it("is empty before the matrix arrives — nothing is invented", () => {
    expect(meshRoster(undefined, UP)).toEqual([]);
  });

  it("wears the matrix's badges through the shared grammar", () => {
    const rows = meshRoster(engines(), UP);
    // verified true prints the word; verified false prints nothing
    expect(rows[0].badges.map((b) => b.text)).toEqual(["SEED", "REF"]);
  });
});

describe("the gate", () => {
  const roster = meshRoster(engines(), UP);
  const one = new Map([["trellis2-4b", 1]]);

  it("arms on one take of one engine", () => {
    expect(
      meshGate({ roster, rosterReady: true, counts: one, health: UP }),
    ).toBeNull();
  });

  it("waits for the roster rather than claiming it is empty", () => {
    expect(
      meshGate({ roster: [], rosterReady: false, counts: one, health: UP }),
    ).toBe("The engine roster isn't loaded yet.");
  });

  it("says so when the matrix publishes no mesh engine", () => {
    expect(
      meshGate({ roster: [], rosterReady: true, counts: one, health: UP }),
    ).toBe(MATRIX_NO_MESH);
  });

  it("carries the runner's own reason when the lane is down", () => {
    expect(
      meshGate({ roster, rosterReady: true, counts: one, health: DOWN }),
    ).toBe(DOWN_WHY);
  });

  it("needs a stepped engine", () => {
    expect(
      meshGate({ roster, rosterReady: true, counts: new Map(), health: UP }),
    ).toBe("Step an engine above zero takes.");
  });

  // the server's own cap, mirrored on the face (api_v4_mesh caps at 8)
  it("refuses more than the run cap, with the number asked for", () => {
    const counts = new Map([
      ["trellis2-4b", 4],
      ["hunyuan3d-21", 4],
    ]);
    expect(
      meshGate({ roster, rosterReady: true, counts, health: UP }),
    ).toBeNull();
    expect(sumMeshTakes(counts)).toBe(MESH_MAX_TAKES);
    const over = new Map([...counts, ["trellis2-4b", 5]]);
    expect(meshGate({ roster, rosterReady: true, counts: over, health: UP })).toBe(
      "A mesh run caps at 8 takes — this asks for 9.",
    );
  });

  it("caps a single engine at the server's per-batch max", () => {
    expect(MESH_MAX_PER_ENGINE).toBe(4);
  });
});

describe("the run bar", () => {
  it("counts meshes and engines, and never predicts a duration (law 17)", () => {
    expect(meshBarLine(new Map([["trellis2-4b", 1]]))).toBe(
      "1 MESH · 1 ENGINE · NO SPEND",
    );
    expect(
      meshBarLine(
        new Map([
          ["trellis2-4b", 2],
          ["hunyuan3d-21", 1],
        ]),
      ),
    ).toBe("3 MESHES · 2 ENGINES · NO SPEND");
    expect(meshBarLine(new Map())).toBe("0 MESHES · 0 ENGINES · NO SPEND");
  });

  it("puts the count on the button face", () => {
    expect(meshRunLabel(new Map([["trellis2-4b", 2]]))).toBe("To 3D · 2");
  });
});

describe("where the mesh is born", () => {
  const turns = [
    { id: "t1", anchor_x: 500, anchor_y: 300 } as Turn,
  ];

  it("sits one column right of a dragged source", () => {
    expect(meshAnchor(node({ x: 1000, y: 40 }), turns)).toEqual({
      x: 1000 + TURN_COL,
      y: 40,
    });
  });

  it("reads the fallback layout when the source was never dragged", () => {
    // the same point the canvas draws the card at, plus one pitch
    expect(meshAnchor(node(), turns)).toEqual({ x: 500 + TURN_COL, y: 300 });
  });
});

describe("the request body", () => {
  it("names one source, one row per stepped engine, and drops the zeroes", () => {
    const body = buildMeshRun({
      source: node({ x: 100, y: 200 }),
      counts: new Map([
        ["trellis2-4b", 2],
        ["hunyuan3d-21", 0],
      ]),
      turns: [],
    });
    expect(body.parents).toEqual(["n1"]);
    expect(body.mesh).toEqual([
      { engine_id: "trellis2-4b", takes: 2, params: {} },
    ]);
    expect(body.anchor).toEqual({ x: 100 + TURN_COL, y: 200 });
  });

  // An untouched fold sends nothing, and every engine runs the recipe it
  // ships with.
  it("sends empty params and a random seed when nothing was touched", () => {
    const body = buildMeshRun({
      source: node(),
      counts: new Map([["hunyuan3d-21", 1]]),
      turns: [],
    });
    expect(body.mesh[0].params).toEqual({});
    expect(body.seed).toEqual({ mode: "random" });
  });

  it("carries each engine's own knobs, and only for engines in the run", () => {
    const body = buildMeshRun({
      source: node(),
      counts: new Map([
        ["trellis2-4b", 1],
        ["hunyuan3d-21", 0],
      ]),
      turns: [],
      knobs: new Map<string, Record<string, string | number>>([
        ["trellis2-4b", { pipeline_type: "512" }],
        // stepped to zero — its settings must not ride along on another row
        ["hunyuan3d-21", { octree_resolution: 512 }],
      ]),
    });
    expect(body.mesh).toEqual([
      { engine_id: "trellis2-4b", takes: 1, params: { pipeline_type: "512" } },
    ]);
  });

  it("locks the seed when the artist asks, and rolls otherwise", () => {
    const run = (seed: MeshSeed) =>
      buildMeshRun({
        source: node(),
        counts: new Map([["trellis2-4b", 1]]),
        turns: [],
        seed,
      }).seed;
    expect(run({ locked: true, value: 7 })).toEqual({
      mode: "locked",
      value: 7,
    });
    expect(run({ locked: false, value: 7 })).toEqual({ mode: "random" });
    // a number the route would refuse falls back rather than being sent
    expect(run({ locked: true, value: -1 })).toEqual({ mode: "random" });
  });
});

// ── the Advanced fold ──────────────────────────────────────────────────
const GEN_PARAMS = {
  generate: {
    pipeline_type: {
      label: "RESOLUTION TIER",
      type: "enum",
      choices: ["512", "1024_cascade"],
      default: "1024_cascade",
      state: "unverified",
      effect: "how fine the grid is",
    },
    texture_size: {
      label: "TEXTURE SIZE",
      type: "enum",
      choices: [1024, 2048],
      default: 2048,
      state: "verified",
      effect: "the bake size",
    },
  },
};

const DASHED_PARAMS = {
  generate: {
    texture_size: {
      label: "TEXTURE SIZE",
      supported: false,
      why: "the atlas is fixed inside the pipeline",
    },
  },
};

function foldEngines(): Engines {
  const e = engines();
  return {
    ...e,
    engines: [
      engineRow("trellis2-4b", "TRELLIS.2 4B", "3d", ["generate"], GEN_PARAMS),
      engineRow("hunyuan3d-21", "Hunyuan3D 2.1", "3d", ["generate"],
                DASHED_PARAMS),
    ],
  };
}

describe("the Advanced fold", () => {
  it("reads its rows off the matrix, never from the web's own table", () => {
    const rows = meshKnobs(foldEngines().engines[0]);
    expect(rows.map((r) => r.key)).toEqual(["pipeline_type", "texture_size"]);
    expect(rows[0].label).toBe("RESOLUTION TIER");
    expect(rows[0].choices).toEqual(["512", "1024_cascade"]);
    expect(rows[0].fallback).toBe("1024_cascade");
    expect(rows[0].unverified).toBe(true);
    expect(rows[1].unverified).toBe(false);
  });

  // law 12 on this surface: a handle the engine hasn't got is DRAWN and dashed
  // with its reason, so an artist who came looking for it learns why rather
  // than finding a gap where the other engine has a control.
  it("keeps an unsupported handle, with its words", () => {
    const [row] = meshKnobs(foldEngines().engines[1]);
    expect(row.supported).toBe(false);
    expect(row.why).toBe("the atlas is fixed inside the pipeline");
    expect(meshKnobLine(row, null)).toBe("NO HANDLE ON THIS ENGINE");
  });

  it("draws only the engines that are in the run", () => {
    const groups = meshFoldRows(foldEngines(), new Map([["trellis2-4b", 1]]));
    expect(groups.map((g) => g.engine.id)).toEqual(["trellis2-4b"]);
    // the seed is the run's own setting, so it counts beside the knobs
    expect(meshFoldCount(groups)).toBe(3);
    expect(meshFoldHead(3)).toBe("ADVANCED · 3 SETTINGS");
  });

  it("counts a locked seed and every moved knob as changed", () => {
    const groups = meshFoldRows(foldEngines(), new Map([["trellis2-4b", 1]]));
    const none = new Map();
    expect(meshFoldChanged(groups, none, { locked: false, value: 1 })).toBe(0);
    expect(meshFoldChanged(groups, none, { locked: true, value: 1 })).toBe(1);
    const set = setMeshKnob(none, "trellis2-4b", "pipeline_type", "512");
    expect(meshFoldChanged(groups, set, { locked: true, value: 1 })).toBe(2);
  });

  // the line says what will be sent. Untouched, it says what happens if
  // nothing is — and an unbenched range says so at the control (law 12).
  it("says what it sends, what the default is, and whether it is proven", () => {
    const [tier, texture] = meshKnobs(foldEngines().engines[0]);
    expect(meshKnobLine(tier, null)).toBe(
      "DEFAULT 1024_cascade · RANGE UNVERIFIED",
    );
    expect(meshKnobLine(tier, "512")).toBe(
      "SENDS 512 · DEFAULT 1024_cascade · RANGE UNVERIFIED",
    );
    expect(meshKnobLine(texture, 1024)).toBe("SENDS 1024 · DEFAULT 2048");
  });

  // RESET means "don't send it", not "send the default back" — the engine's
  // own default is the one that governs, and it may change.
  it("clears a knob back to absent rather than to its default", () => {
    let k: MeshKnobs = new Map();
    k = setMeshKnob(k, "trellis2-4b", "texture_size", 1024);
    expect(meshKnobValue(k, "trellis2-4b", "texture_size")).toBe(1024);
    expect(meshKnobsChanged(k, "trellis2-4b")).toBe(1);
    k = setMeshKnob(k, "trellis2-4b", "texture_size", null);
    expect(meshKnobValue(k, "trellis2-4b", "texture_size")).toBeNull();
    expect(k.has("trellis2-4b")).toBe(false);
  });

  it("resets one engine without touching the other", () => {
    let k: MeshKnobs = new Map();
    k = setMeshKnob(k, "trellis2-4b", "pipeline_type", "512");
    k = setMeshKnob(k, "hunyuan3d-21", "octree_resolution", 512);
    k = clearMeshKnobs(k, "trellis2-4b");
    expect(k.has("trellis2-4b")).toBe(false);
    expect(meshKnobValue(k, "hunyuan3d-21", "octree_resolution")).toBe(512);
  });
});

describe("refusals", () => {
  it("passes the server's own words through", () => {
    expect(meshErr({ detail: "a mesh run caps at 8 takes" })).toBe(
      "a mesh run caps at 8 takes",
    );
  });

  it("falls back to the two facts law 3 and law 5 want", () => {
    expect(meshErr(new Error("boom"))).toBe(
      "The 3D run didn't start — nothing was queued and nothing was spent.",
    );
  });
});
