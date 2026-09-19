// The roster's two rules under test: filtered by PAYLOAD ONLY,
// and nothing is ever hidden (law 12). The reasons are asserted as
// text because the reason IS the feature — a dimmed chip with no words is
// the failure this treatment exists to prevent.
import { expect, test } from "vitest";
import type { Node } from "../../types";
import { factsLine, resolveRow } from "./verbs";

function node(over: Partial<Node> = {}): Node {
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
    created_at: "2026-08-20T00:00:00Z",
    parents: [],
    turn_idx: 1,
    slot_pos: 1,
    ...over,
  };
}

const ids = (chips: { id: string }[]) => chips.map((c) => c.id);
const chip = (row: ReturnType<typeof resolveRow>, id: string) =>
  [...row.chips, ...row.irreversible, ...row.notForThis].find(
    (c) => c.id === id,
  );

test("nothing selected falls back to a live root verb", () => {
  const row = resolveRow([], [node()]);
  expect(row.target).toBeNull();
  expect(ids(row.chips)).toEqual(["new-root"]);
  expect(row.chips[0].live).toBe(true);
  expect(row.notForThis).toHaveLength(0);
});

test("an image node offers the image roster; unwired verbs stay visible with a reason", () => {
  const row = resolveRow(["n1"], [node()]);
  expect(row.target?.label).toBe("Take 1b");
  // reroll + set-aside fire; an unwired verb (upscale) cannot, and says so
  expect(chip(row, "reroll")?.live).toBe(true);
  expect(chip(row, "set-aside")?.live).toBe(true);
  // refine runs, and states what it does rather than what's missing
  expect(chip(row, "refine")?.live).toBe(true);
  expect(chip(row, "refine")?.line).toBe("same prompt, new seeds");
  // the sweep runs, and says what it spends
  expect(chip(row, "vary-dial")?.live).toBe(true);
  expect(chip(row, "vary-dial")?.line).toMatch(/one dial walked/);
  // the region surface opens from here, and the chip says its effect
  expect(chip(row, "edit-region")?.live).toBe(true);
  expect(chip(row, "edit-region")?.line).toBe("mask a part, keep the rest");
  expect(chip(row, "upscale")?.line).toMatch(/no upscale recipe/);
  // the 3D lane runs inside the cockpit, so the chip states its
  // effect rather than naming a gap
  expect(chip(row, "to-3d")?.live).toBe(true);
  expect(chip(row, "to-3d")?.line).toBe("turn this take into a mesh");
  // nothing was filtered away — every image verb is on screen somewhere,
  // the two that can't be undone below the hairline
  expect(ids(row.chips)).toEqual([
    "refine",
    "edit-region",
    "vary-dial",
    "upscale",
    "to-3d",
    "inspect",
    "reroll",
    "set-aside",
  ]);
  expect(ids(row.irreversible)).toEqual(["promote", "purge"]);
});

test("promote is live on one take, below the hairline, and says what it does", () => {
  const row = resolveRow(["n1"], [node()]);
  expect(ids(row.chips)).not.toContain("promote");
  expect(chip(row, "promote")?.live).toBe(true);
  expect(chip(row, "promote")?.line).toMatch(/asset tree/);
  expect(chip(row, "promote")?.line).toMatch(/manifest/);
  // one at a time, and the reason is about manifests, not about a rule id
  const two = resolveRow(
    ["n1", "n2"],
    [node(), node({ id: "n2", slot_pos: 2 })],
  );
  expect(chip(two, "promote")?.live).toBe(false);
  expect(chip(two, "promote")?.line).toMatch(/one take at a time/);
  // a mesh promotes too — meshes get their own `_3d/` convention
  const mesh = resolveRow(["n1"], [node({ payload: "3d" })]);
  expect(chip(mesh, "promote")?.live).toBe(true);
  // and the tier is a cut across the row, not a third bucket: on a mesh
  // the image-only verbs still drop to NOT FOR THIS NODE
  expect(ids(mesh.irreversible)).toEqual(["promote", "purge"]);
});

test("purge stays in the row and says where it fires — the tray, held", () => {
  const row = resolveRow(["n1"], [node()]);
  expect(chip(row, "purge")?.live).toBe(false);
  expect(chip(row, "purge")?.line).toMatch(/set it aside first/);
  expect(chip(row, "purge")?.line).toMatch(/tray/);
  // a set-aside selection (the tray's own nodes) could fire it
  const aside = resolveRow(["n1"], [node({ set_aside: true })]);
  expect(chip(aside, "purge")?.live).toBe(true);
  // a mesh keeps it too — purge is payload-agnostic like reroll
  const mesh = resolveRow(["n1"], [node({ payload: "3d" })]);
  expect(ids(mesh.irreversible)).toContain("purge");
});

test("TO 3D takes one source, and says why rather than counting at you", () => {
  const nodes = [node({ id: "a" }), node({ id: "b" })];
  const row = resolveRow(["a", "b"], nodes);
  expect(chip(row, "to-3d")?.live).toBe(false);
  expect(chip(row, "to-3d")?.line).toMatch(
    /one image per mesh — each take is conditioned on a single source/,
  );
});

test("a mesh drops the image verbs into NOT FOR THIS NODE, never out of sight", () => {
  const row = resolveRow(["n1"], [node({ payload: "3d", engine: "trellis2" })]);
  expect(ids(row.notForThis)).toEqual([
    "refine",
    "edit-region",
    "vary-dial",
    "upscale",
    "to-3d",
  ]);
  expect(chip(row, "refine")?.line).toMatch(/mesh — that verb works on images/);
  // payload-agnostic verbs stay in the live row
  expect(chip(row, "reroll")?.live).toBe(true);
});

test("INSPECT is live on one take of either kind, wears V, and takes one at a time", () => {
  // an image: the full-res lightbox already works, so the verb applies —
  // scoping it to meshes would make law 12 print "not for this node" on
  // an image, which is false
  const image = resolveRow(["n1"], [node()]);
  expect(chip(image, "inspect")?.live).toBe(true);
  expect(chip(image, "inspect")?.key).toBe("V");
  expect(ids(image.notForThis)).not.toContain("inspect");
  // a mesh: the viewer, beside REROLL in the live row
  const mesh = resolveRow(["n1"], [node({ payload: "3d", engine: "trellis2" })]);
  expect(ids(mesh.chips)).toEqual([
    "decimate",
    "texture",
    "inspect",
    "reroll",
    "set-aside",
  ]);
  expect(chip(mesh, "inspect")?.line).toMatch(/orbits/);
  // one lightbox, one take
  const two = resolveRow(["a", "b"], [node({ id: "a" }), node({ id: "b" })]);
  expect(chip(two, "inspect")?.live).toBe(false);
  expect(chip(two, "inspect")?.line).toBe("acts on one node — 2 are selected");
});

test("DECIMATE is a mesh verb — live on a mesh, dashed on an image", () => {
  // the verb's whole point is that its PARENT is a mesh (law 16's first
  // mesh-in verb), so on an image it is law 12's other tier: not hidden,
  // dropped to NOT FOR THIS NODE with the reason in the artist's words
  const image = resolveRow(["n1"], [node()]);
  expect(ids(image.chips)).not.toContain("decimate");
  expect(ids(image.notForThis)).toContain("decimate");
  expect(chip(image, "decimate")?.line).toBe(
    "this node is an image — that verb works on meshes",
  );

  const mesh = resolveRow(["n1"], [node({ payload: "3d", engine: "trellis2" })]);
  expect(chip(mesh, "decimate")?.live).toBe(true);
  expect(chip(mesh, "decimate")?.line).toMatch(/fewer triangles/);
  // it is NOT below the hairline: the child is a node a second click sets
  // aside, and the parent is untouched
  expect(ids(mesh.irreversible)).not.toContain("decimate");

  // one mesh at a time — the tool takes a single GLB
  const two = resolveRow(
    ["a", "b"],
    [node({ id: "a", payload: "3d" }), node({ id: "b", payload: "3d" })],
  );
  expect(chip(two, "decimate")?.live).toBe(false);
  expect(chip(two, "decimate")?.line).toMatch(/one mesh per decimate/);
});

test("TEXTURE is a mesh verb with the same two tiers", () => {
  // dashed on an image — the mesh IS the shape parent; the image an artist
  // might reach for belongs in the plinth's reference strip instead
  const image = resolveRow(["n1"], [node()]);
  expect(ids(image.chips)).not.toContain("texture");
  expect(ids(image.notForThis)).toContain("texture");

  const mesh = resolveRow(["n1"], [node({ payload: "3d", engine: "trellis2" })]);
  expect(chip(mesh, "texture")?.live).toBe(true);
  expect(chip(mesh, "texture")?.line).toMatch(/repaint this mesh/);
  // the child is a node a second click sets aside — not below the hairline
  expect(ids(mesh.irreversible)).not.toContain("texture");

  const two = resolveRow(
    ["a", "b"],
    [node({ id: "a", payload: "3d" }), node({ id: "b", payload: "3d" })],
  );
  expect(chip(two, "texture")?.live).toBe(false);
  expect(chip(two, "texture")?.line).toMatch(/one mesh per repaint/);
});

test("set aside and restore are the same slot, never both at once", () => {
  const live = resolveRow(["n1"], [node()]);
  expect(ids(live.chips)).toContain("set-aside");
  expect(ids(live.chips)).not.toContain("restore");

  const aside = resolveRow(["n1"], [node({ set_aside: true })]);
  expect(ids(aside.chips)).toContain("restore");
  expect(ids(aside.chips)).not.toContain("set-aside");
  expect(aside.target?.facts).toMatch(/SET ASIDE/);
});

test("two parents: order is click order, and single-node verbs say why they can't fire", () => {
  const nodes = [node({ id: "a" }), node({ id: "b" })];
  const row = resolveRow(["b", "a"], nodes);
  expect(row.target?.nodes.map((n) => n.id)).toEqual(["b", "a"]);
  expect(row.target?.label).toBe("2 nodes");
  expect(row.target?.facts).toBe(
    "IMAGE · 2 SELECTED · PARENT ORDER AS SELECTED",
  );
  expect(chip(row, "reroll")?.live).toBe(false);
  expect(chip(row, "reroll")?.line).toBe("acts on one node — 2 are selected");
  // refine takes ONE source, and says why rather than counting at them
  expect(chip(row, "refine")?.live).toBe(false);
  expect(chip(row, "refine")?.line).toMatch(
    /one source image per refine — the recipes take a single parent/,
  );
  // set aside takes the whole selection
  expect(chip(row, "set-aside")?.live).toBe(true);
  expect(chip(row, "set-aside")?.line).toBe("out of the way, not deleted");
});

test("a mixed selection says so rather than guessing a kind", () => {
  const nodes = [node({ id: "a" }), node({ id: "b", payload: "3d" })];
  const row = resolveRow(["a", "b"], nodes);
  expect(row.target?.facts).toMatch(/MIXED KINDS/);
  expect(chip(row, "refine")?.line).toBe(
    "the two selected nodes aren't the same kind",
  );
});

test("an imported node has no cell to re-run, and the row says that", () => {
  const row = resolveRow(
    ["n1"],
    [node({ slot_id: null, turn_id: null, turn_idx: null, engine: null })],
  );
  expect(row.target?.label).toBe("Imported node");
  expect(chip(row, "reroll")?.live).toBe(false);
  expect(chip(row, "reroll")?.line).toMatch(/didn't come from a slot/);
});

test("the facts line states only what the node records", () => {
  expect(factsLine([node()])).toBe("IMAGE · ZIMAGE-TURBO · SEEDED");
  expect(factsLine([node({ seed: null })])).toMatch(/NO SEED/);
  // no frame size, no maskability — those aren't stored, so they aren't shown
  expect(factsLine([node()])).not.toMatch(/1024|MASK/);
});

test("a stale selection id (node purged under it) simply isn't a target", () => {
  const row = resolveRow(["gone"], [node()]);
  expect(row.target).toBeNull();
  expect(ids(row.chips)).toEqual(["new-root"]);
});
