// The record's words, pure. What is on the wire is said; what
// isn't is said to be absent, in the app's own grammar — never guessed.
import { expect, test } from "vitest";
import type { Node, Slot, Turn } from "../../types";
import {
  durationWord,
  knobLine,
  lineageLine,
  madeFrom,
  recallPrompt,
  recipe,
  recordRows,
  seedPolicyLine,
  thisTake,
} from "./record";

function slot(over: Partial<Slot> & { id: string; pos: number }): Slot {
  return {
    turn_id: "t1",
    engine: "z-image-turbo",
    status: "landed",
    attempt: 1,
    seed: 41,
    committed_usd: null,
    charged_usd: null,
    cause: null,
    detail: null,
    retry_of: null,
    kind: null,
    job_ref: null,
    frame: "1024x1024",
    facts: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-09-04T00:00:00Z",
    ...over,
  };
}

function node(over: Partial<Node> & { id: string }): Node {
  return {
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "image",
    verb: "run",
    prompt: "a rusty sword",
    engine: "z-image-turbo",
    seed: 41,
    duration_ms: 10_200,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: null,
    y: null,
    created_at: "2026-09-04T00:00:00Z",
    parents: [],
    turn_idx: 1,
    slot_pos: 0,
    promoted_at: null,
    ...over,
  };
}

const ROOT: Turn = {
  id: "t1",
  workspace_id: "w1",
  idx: 1,
  prompt: "a rusty sword",
  dials: {
    seed: { mode: "fixed", value: 41 },
    engines: {
      "z-image-turbo": {
        seed: { mode: "fixed", value: 41 },
        prompt: "a rusty sword",
        steps: 9,
        cfg: 1,
        sampler_name: "euler",
        scheduler: "simple",
        width: 1024,
        height: 1024,
      },
    },
  },
  anchor_x: null,
  anchor_y: null,
  created_at: "2026-09-04T00:00:00Z",
  slots: [slot({ id: "s1", pos: 0 }), slot({ id: "s2", pos: 1, seed: 42 })],
};

const A = node({ id: "a" });
const B = node({ id: "b", slot_id: "s2", seed: 42, slot_pos: 1 });
const IMPORTED = node({
  id: "imp",
  turn_id: null,
  slot_id: null,
  take: null,
  verb: "import",
  prompt: null,
  engine: null,
  seed: null,
  duration_ms: null,
  turn_idx: null,
  slot_pos: null,
});

test("the recipe prints the turn's applied raws, the frame folded, the lineage and the policy last", () => {
  const r = recipe(ROOT, [A], "turn");
  expect(r.blank).toBe(false);
  expect(r.lines).toEqual([
    "1024×1024 · STEPS 9 · CFG 1 · SAMPLER EULER · SCHEDULER SIMPLE",
    "SEED 41 · SPREAD BY SLOT · FROM TEXT · A ROOT",
  ]);
  expect(r.note).toBe("THIS TURN'S DIALS, AS SENT");
  // a take-scoped mount wears the divergence honestly: Slot carries no dials
  expect(recipe(ROOT, [A], "take").note).toMatch(/THE TURN'S DIALS — a swept or retried cell/);
});

test("knobLine skips lineage and identity keys, caps long strings, flattens a mesh engine's params", () => {
  expect(
    knobLine({
      seed: {}, prompt: "x", capability: "img2img", parents: ["p"],
      source_image_sha256: "abc", denoise: 0.55, steps: 36,
      negative_prompt: "blurry, low quality, watermark, text, and a great deal more than fits",
    }),
  ).toBe('DENOISE 0.55 · STEPS 36 · NEGATIVE PROMPT "blurry, low quality, watermark, text, and a gre…"');
  expect(
    knobLine({ capability: "to_3d", kind: "generate", engine_id: "trellis2", params: { resolution: 512, ss_steps: 12 } }),
  ).toBe("RESOLUTION 512 · SS STEPS 12");
});

test("several engines get one line each, prefixed; none at all is said as none", () => {
  const two: Turn = {
    ...ROOT,
    dials: {
      seed: { mode: "random" },
      engines: {
        "z-image-turbo": { steps: 9, cfg: 1 },
        "flux2-klein-4b": { steps: 4, guidance: 1 },
      },
    },
  };
  expect(recipe(two, [], "turn").lines).toEqual([
    "Z-IMAGE-TURBO · STEPS 9 · CFG 1",
    "FLUX2-KLEIN-4B · STEPS 4 · GUIDANCE 1",
    "SEEDS ROLLED PER TAKE · FROM TEXT · A ROOT",
  ]);
  const external: Turn = {
    ...ROOT,
    dials: { seed: { mode: "random" }, engines: {} },
    slots: [slot({ id: "x", pos: 0, engine: "openai/gpt-image-1" })],
  };
  const r = recipe(external, [], "turn");
  expect(r.blank).toBe(true);
  expect(r.lines[0]).toMatch(/EXTERNAL · THE MODEL'S OWN DEFAULTS/);
  expect(recipe({ ...ROOT, dials: {} }, [], "turn").lines[0]).toBe("NO RAWS RECORDED ON THIS TURN");
});

test("the lineage names the parent by its take, a purged one honestly, and a mesh's kind", () => {
  const refine: Turn = {
    ...ROOT,
    dials: { seed: { mode: "random" }, capability: "img2img", parents: ["a"], engines: {} },
  };
  expect(lineageLine(refine, [A])).toBe("REFINE OF TAKE 1A");
  expect(lineageLine(refine, [])).toBe("REFINE OF A PURGED PARENT");
  const region: Turn = {
    ...ROOT,
    dials: { capability: "region", parents: ["a"], region: { policy: "nd3-v1" } },
  };
  expect(lineageLine(region, [A])).toBe("REGION EDIT OF TAKE 1A · ND3-V1");
  const mesh: Turn = {
    ...ROOT,
    dials: { capability: "to_3d", kind: "texture", parents: ["a", "b"] },
  };
  expect(lineageLine(mesh, [A, B])).toBe("TEXTURE · TAKE 1A FROM TAKE 1B");
  expect(seedPolicyLine({ mode: "locked", value: 7 })).toBe("SEED LOCKED AT 7 · EVERY TAKE");
  expect(seedPolicyLine(undefined)).toBe("SEEDS ROLLED PER TAKE");
  const localOnly: Turn = { ...ROOT, dials: { ...ROOT.dials, local_only: true } };
  expect(recipe(localOnly, [], "turn").lines[1]).toMatch(/· LOCAL ONLY$/);
});

test("MADE FROM: the words once, the imported node's honest blank, a region edit's words per pass", () => {
  expect(madeFrom(ROOT, [A])).toMatchObject({ lines: ["a rusty sword"], blank: false });
  expect(madeFrom(ROOT, [])).toMatchObject({ lines: ["a rusty sword"], blank: false });
  const imp = madeFrom(undefined, [IMPORTED]);
  expect(imp.blank).toBe(true);
  expect(imp.lines[0]).toMatch(/^IMPORTED · NO PROMPT — this node came in from a file/);
  const region: Turn = {
    ...ROOT,
    prompt: "",
    dials: {
      capability: "region",
      region: {
        passes: [
          { kind: "replace", prompt: "a brass handle" },
          { kind: "keep", prompt: null },
        ],
      },
    },
  };
  const r = madeFrom(region, []);
  expect(r.lines).toEqual(["REPLACE · a brass handle"]);
  expect(r.note).toMatch(/A REGION EDIT/);
  const silent: Turn = { ...region, dials: { capability: "region", region: { passes: [{ kind: "keep" }] } } };
  expect(madeFrom(silent, []).lines[0]).toMatch(/every pass of this region edit was a KEEP or a DELETE/);
  const mesh: Turn = { ...ROOT, prompt: null, dials: { capability: "to_3d" } };
  expect(madeFrom(mesh, []).lines[0]).toMatch(/a mesh is made from its source image/);
});

test("several selected: one prompt said once, different prompts recall nothing, turns apart say so", () => {
  const same = madeFrom(ROOT, [A, B]);
  expect(same).toMatchObject({ lines: ["a rusty sword"], blank: false });
  expect(same.note).toBe("SAID ONCE — ALL 2 SELECTED SHARE IT");
  expect(recallPrompt(ROOT, [A, B])).toBe("a rusty sword");
  const C = node({ id: "c", prompt: "refine the blade", turn_id: "t2" });
  const differ = madeFrom(ROOT, [A, C]);
  expect(differ.blank).toBe(true);
  expect(differ.lines[0]).toBe("2 SELECTED · 2 DIFFERENT PROMPTS — pick one to read its words");
  expect(recallPrompt(ROOT, [A, C])).toBeNull();
  const apart = recipe(undefined, [], "take", [A, C]);
  expect(apart.blank).toBe(true);
  expect(apart.lines[0]).toBe("2 TURNS AMONG THE 2 SELECTED — pick one to read its recipe");
  expect(thisTake([A, B], ROOT, [A, B]).lines).toEqual(["2 TAKES · SEEDS 41 · 42"]);
  expect(thisTake([A, B, A, B, A], ROOT, []).lines[0]).toMatch(/5 TAKES — pick one/);
});

test("THIS TAKE: seed · engine · seconds · verb of parent · frame · spend, and its own frame when retried", () => {
  expect(thisTake([A], ROOT, [A]).lines).toEqual([
    "SEED 41 · Z-IMAGE-TURBO · 10.2 S · RUN · 1024×1024 · NO SPEND",
  ]);
  const refined = node({
    id: "d", verb: "refine", parents: ["a"], slot_id: "s2", take: 2,
    charged_usd: 0.06, duration_ms: 167_000, promoted_at: "2026-09-04T00:00:00Z",
  });
  expect(thisTake([refined], ROOT, [A]).lines).toEqual([
    "SEED 41 · Z-IMAGE-TURBO · 2 MIN 47 S · REFINE OF TAKE 1A · 1024×1024 · TAKE 2 IN THIS CELL · $0.06 · PROMOTED",
  ]);
  // A retried frame is the one per-take raw the wire carries: named,
  // and named as not the turn's
  const smaller: Turn = { ...ROOT, slots: [slot({ id: "s1", pos: 0, frame: "768x768" })] };
  const r = thisTake([A], smaller, [A]);
  expect(r.lines[0]).toContain("768×768");
  expect(r.note).toBe("RAN AT 768×768 — NOT THE TURN'S FRAME (a retry at a smaller size)");
  const imp = thisTake([IMPORTED], undefined, []);
  expect(imp.lines).toEqual(["NO SEED · IMPORTED · NO SPEND"]);
  const textured = node({ id: "tx", payload: "3d", verb: "texture", parents: ["a", "b"], seed: null, duration_ms: 51_000, engine: "hunyuan3d-21", slot_id: null });
  expect(thisTake([textured], undefined, [A, B]).lines[0]).toBe(
    "NO SEED · HUNYUAN3D-21 · 51 S · TEXTURE OF TAKE 1A FROM TAKE 1B · NO SPEND",
  );
  expect(durationWord(null)).toBeNull();
  expect(durationWord(950)).toBe("1 S");
});

test("recordRows: the grid's mount is the turn's two rows, a take's is three; a loading detail is said as such", () => {
  expect(recordRows({ scope: "turn", turn: ROOT, nodes: [], all: [] }).map((r) => r.name)).toEqual([
    "MADE FROM",
    "THE RECIPE",
  ]);
  const rows = recordRows({ scope: "take", turn: ROOT, nodes: [A], all: [A] });
  expect(rows.map((r) => r.name)).toEqual(["MADE FROM", "THE RECIPE", "THIS TAKE"]);
  expect(recordRows({ scope: "take", turn: undefined, nodes: [A], all: [A] })[1].lines[0]).toBe(
    "THE TURN'S RECORD IS ON ITS WAY",
  );
  expect(recordRows({ scope: "take", turn: undefined, nodes: [IMPORTED], all: [] })[1].lines[0]).toMatch(/^NO TURN/);
});
