// The pure derivation layer: labels, fallback layout, typed edges.
// No React Flow mounts (RF needs a provider + ResizeObserver; jsdom has
// neither) — Canvas wiring is proven in e2e/canvas.spec.ts.
import { describe, expect, test } from "vitest";
import type { Node, Turn } from "../../types";
import {
  CARD_W,
  deriveEdges,
  edgeTypesPresent,
  fallbackPosition,
  hasPlate,
  nodeLabel,
  onCanvas,
  toFlowNodes,
} from "./graph";

function mkNode(over: Partial<Node>): Node {
  return {
    id: "n1",
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "image",
    verb: "run",
    prompt: null,
    engine: "z-image-turbo",
    seed: 7,
    duration_ms: null,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: null,
    y: null,
    created_at: "2026-07-30T00:00:00Z",
    parents: [],
    turn_idx: 12,
    slot_pos: 1,
    ...over,
  };
}

function mkTurn(over: Partial<Turn>): Turn {
  return {
    id: "t1",
    workspace_id: "w1",
    idx: 12,
    prompt: null,
    dials: {},
    anchor_x: null,
    anchor_y: null,
    created_at: "2026-07-30T00:00:00Z",
    slots: [],
    ...over,
  };
}

describe("nodeLabel", () => {
  test("slot letters, engine uppercased — 12b · Z-IMAGE-TURBO", () => {
    expect(nodeLabel(mkNode({}))).toBe("12b · Z-IMAGE-TURBO");
    expect(nodeLabel(mkNode({ slot_pos: 0, turn_idx: 0 }))).toBe(
      "0a · Z-IMAGE-TURBO",
    );
  });

  test("the take shows once a cell has more than one", () => {
    // the first take of a cell stays unnumbered — no chrome on the common
    // card; its rerolls name themselves, which is what tells them apart
    expect(nodeLabel(mkNode({ take: 1 }))).toBe("12b · Z-IMAGE-TURBO");
    expect(nodeLabel(mkNode({ take: 2 }))).toBe("12b · 2 · Z-IMAGE-TURBO");
    expect(nodeLabel(mkNode({ take: null }))).toBe("12b · Z-IMAGE-TURBO");
  });

  test("turnless nodes read IMPORT", () => {
    expect(
      nodeLabel(
        mkNode({ turn_id: null, slot_id: null, turn_idx: null, slot_pos: null }),
      ),
    ).toBe("IMPORT");
  });
});

describe("fallbackPosition", () => {
  test("turn anchor wins; slot_pos stacks rows", () => {
    const turns = new Map([
      ["t1", mkTurn({ anchor_x: 400, anchor_y: 100 })],
    ]);
    expect(fallbackPosition(mkNode({ slot_pos: 0 }), turns)).toEqual({
      x: 400,
      y: 100,
    });
    expect(fallbackPosition(mkNode({ slot_pos: 2 }), turns)).toEqual({
      x: 400,
      y: 460,
    });
  });

  test("no anchor: deterministic 220px column per turn", () => {
    const turns = new Map([["t1", mkTurn({})]]);
    expect(fallbackPosition(mkNode({ turn_idx: 0, slot_pos: 0 }), turns))
      .toEqual({ x: 80, y: 80 });
    expect(fallbackPosition(mkNode({ turn_idx: 3, slot_pos: 1 }), turns))
      .toEqual({ x: 740, y: 260 });
  });

  // A reroll keeps the old take (law 7), so one slot holds several
  // nodes — they must never come out at the same point, or the newest is
  // invisible until it is dragged off the older one.
  test("takes of one slot fan right instead of stacking", () => {
    const turns = new Map([["t1", mkTurn({ anchor_x: 400, anchor_y: 100 })]]);
    const at = (take: number | null) =>
      fallbackPosition(mkNode({ slot_pos: 1, take }), turns);
    expect(at(1)).toEqual({ x: 400, y: 280 });
    expect(at(2)).toEqual({ x: 620, y: 280 });
    expect(at(3)).toEqual({ x: 840, y: 280 });
    // an imported node has no attempt at all — it keeps the anchor column
    expect(at(null)).toEqual({ x: 400, y: 280 });
  });

  test("no two landed nodes of a turn share a point", () => {
    const turns = [mkTurn({ anchor_x: 400, anchor_y: 100 })];
    const nodes = [
      mkNode({ id: "a1", slot_pos: 0, take: 1 }),
      mkNode({ id: "a2", slot_pos: 0, take: 2 }), // reroll of slot a
      mkNode({ id: "b1", slot_pos: 1, take: 1 }),
      mkNode({ id: "b2", slot_pos: 1, take: 2 }), // backfill of slot b
    ];
    const spots = nodes.map((n) => {
      const { x, y } = fallbackPosition(n, new Map([["t1", turns[0]]]));
      return `${x},${y}`;
    });
    expect(new Set(spots).size).toBe(nodes.length);
  });
});

describe("toFlowNodes", () => {
  test("persisted x/y wins over the fallback", () => {
    const [n] = toFlowNodes([mkNode({ x: 12.5, y: -3 })], [mkTurn({})]);
    expect(n.position).toEqual({ x: 12.5, y: -3 });
    expect(n.initialWidth).toBe(CARD_W);
    expect(n.deletable).toBe(false);
  });

  test("footer word is the payload, no dims invented; set-aside takes never reach the canvas", () => {
    const feed = [
      mkNode({ id: "a" }),
      mkNode({ id: "b", set_aside: true }),
      mkNode({ id: "c", payload: "3d", parents: ["b"] }),
    ];
    const drawn = onCanvas(feed);
    const rows = toFlowNodes(drawn, [mkTurn({})]);
    expect(rows.map((r) => [r.id, r.data.right])).toEqual([
      ["a", "IMAGE"],
      ["c", "MESH"],
    ]);
    // the edge to a set-aside parent draws nothing — the child keeps its
    // lineage row, the card simply isn't there (same as a purged parent)
    expect(deriveEdges(drawn)).toEqual([]);
    expect(edgeTypesPresent(drawn)).toEqual([]);
  });

  test("thumbs only for image payloads — a mesh card never fetches one", () => {
    const rows = toFlowNodes(
      [mkNode({ id: "a" }), mkNode({ id: "b", payload: "3d" })],
      [mkTurn({})],
    );
    expect(rows[0].data.thumb).toBe("/api/nodes/a/thumb");
    expect(rows[1].data.thumb).toBeNull();
  });

  test("turnless nodes stack by their own ordinal, not a shared spot", () => {
    const rows = toFlowNodes(
      [
        mkNode({ id: "a", turn_id: null, turn_idx: null, slot_pos: null }),
        mkNode({ id: "b", turn_id: null, turn_idx: null, slot_pos: null }),
      ],
      [],
    );
    expect(rows[0].position.y).not.toBe(rows[1].position.y);
  });
});

describe("deriveEdges", () => {
  test("edge type = parent payload; stable ids", () => {
    const nodes = [
      mkNode({ id: "img" }),
      mkNode({ id: "mesh", payload: "3d" }),
      mkNode({ id: "child", parents: ["img", "mesh"] }),
    ];
    const edges = deriveEdges(nodes);
    expect(edges.map((e) => e.id)).toEqual(["img->child", "mesh->child"]);
    expect(edges[0].style?.stroke).toBe("var(--accent)"); // IMAGE
    expect(edges[1].style?.stroke).toBe("var(--good)"); // MESH
    expect(edges.every((e) => e.selectable === false)).toBe(true);
  });

  test("a purged/absent parent draws no edge", () => {
    expect(deriveEdges([mkNode({ parents: ["gone"] })])).toEqual([]);
  });
});

describe("edgeTypesPresent", () => {
  test("legend lists only what the canvas carries, IMAGE before MESH", () => {
    expect(edgeTypesPresent([mkNode({})])).toEqual([]);
    const nodes = [
      mkNode({ id: "img" }),
      mkNode({ id: "mesh", payload: "3d" }),
      mkNode({ id: "c1", parents: ["mesh"] }),
      mkNode({ id: "c2", parents: ["img"] }),
    ];
    expect(edgeTypesPresent(nodes).map((k) => k.label)).toEqual([
      "IMAGE",
      "MESH",
    ]);
  });
});

// ── the 3D result node body ──────────────────────────────────────────────

const MESH_FACTS = {
  engine: "trellis2",
  runner_job_id: "job-abc123",
  triangles: 999588,
  vertices: 691815,
  watertight: false,
  textured: true,
  file_mb: 33.8,
  has_turntable: true,
  forced: false,
  fields_kept: false,
};

test("a mesh with a turntable draws a plate, off the same thumb route", () => {
  const [card] = toFlowNodes(
    [mkNode({ id: "m", payload: "3d", mesh: MESH_FACTS })],
    [],
  );
  expect(hasPlate(mkNode({ id: "m", payload: "3d", mesh: MESH_FACTS }))).toBe(
    true,
  );
  expect(card.data.thumb).toBe("/api/nodes/m/thumb");
  // the footer still names the payload — the plate replaced the WORD on the
  // plate, not the strip's honest label
  expect(card.data.right).toBe("MESH");
});

test("a mesh with no turntable asks for nothing and keeps its word", () => {
  // law 12: the card stays drawn and selectable; ImageNode falls back to the
  // plain MESH word rather than firing a request that can only 404
  const bare = mkNode({
    id: "m",
    payload: "3d",
    mesh: { ...MESH_FACTS, has_turntable: false },
  });
  expect(hasPlate(bare)).toBe(false);
  expect(toFlowNodes([bare], [])[0].data.thumb).toBeNull();
  // ...and so does one that landed before the facts existed at all
  expect(hasPlate(mkNode({ id: "m", payload: "3d" }))).toBe(false);
});

test("an image never consults the mesh facts", () => {
  expect(hasPlate(mkNode({ id: "i", payload: "image" }))).toBe(true);
  expect(toFlowNodes([mkNode({ id: "i" })], [])[0].data.thumb).toBe(
    "/api/nodes/i/thumb",
  );
});
