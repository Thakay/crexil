// The lightbox's mesh face. `meshFactsLine` is the pure half (what
// the funnel measured, in the mono register), so it is exercised directly;
// the mounted overlay is proven in e2e/mesh.spec.ts.
// The viewer's pure halves join it: the mode state, the truth line
// under the facts, the poster's line and bar, the corner header. The stage
// itself needs WebGL and is proven mounted in e2e/mesh.spec.ts, rendering
// live on the real mesh.
import { expect, test } from "vitest";
import type { Node } from "../../types";
import {
  INITIAL_VIEW,
  loadFraction,
  loadingLine,
  nextView,
  truthLine,
  viewerHeader,
} from "./Lightbox";
// a mesh fact lives with the mesh (mesh/facts.ts)
import { meshFactsLine } from "../mesh/facts";

function mesh(over: Partial<NonNullable<Node["mesh"]>> = {}): Node {
  return {
    id: "m",
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "3d",
    verb: "to_3d",
    prompt: null,
    engine: "trellis2-4b",
    seed: 7,
    duration_ms: 167000,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: null,
    y: null,
    created_at: "2026-08-27T00:00:00Z",
    parents: ["src"],
    turn_idx: 4,
    slot_pos: 0,
    mesh: {
      engine: "trellis2",
      fields_kept: false,
      runner_job_id: "job-abc123",
      triangles: 999588,
      vertices: 691815,
      watertight: false,
      textured: true,
      file_mb: 33.8,
      forced: false,
      has_turntable: true,
      ...over,
    },
  };
}

test("the caption is what the funnel measured, in reading order", () => {
  expect(meshFactsLine(mesh())).toBe(
    "TRELLIS2 · 999,588 TRIS · 691,815 VERTS · TEXTURED · OPEN SURFACES · 33.8 MB",
  );
});

test("open surfaces are a fact, not a verdict — TRELLIS.2 makes them", () => {
  // funnel.validate() records watertight:false and deliberately does not
  // fail it; the caption says what is there and judges nothing
  expect(meshFactsLine(mesh({ watertight: true }))).toContain("WATERTIGHT");
  expect(meshFactsLine(mesh({ watertight: false }))).toContain("OPEN SURFACES");
  expect(meshFactsLine(mesh({ textured: false }))).toContain("UNTEXTURED");
});

test("a fact the record doesn't carry is left out, never guessed (law 12)", () => {
  const sparse = meshFactsLine(
    mesh({ triangles: null, vertices: null, watertight: null,
           textured: null, file_mb: null }),
  );
  expect(sparse).toBe("TRELLIS2");
  expect(sparse).not.toMatch(/0 TRIS|UNTEXTURED|WATERTIGHT/);
});

test("an image has no mesh facts to state", () => {
  const img = { ...mesh(), payload: "image" as const, mesh: null };
  expect(meshFactsLine(img)).toBe("");
});

test("the modes are view state: toggles, an exclusive surface, presets that re-fire", () => {
  // the defaults are the opening view: textured, backfaces drawn, grid on
  expect(INITIAL_VIEW).toMatchObject({
    surface: "textured",
    cull: false,
    normals: false,
    wireframe: false,
    grid: true,
  });
  let v = nextView(INITIAL_VIEW, "clay");
  expect(v.surface).toBe("clay");
  expect(nextView(v, "clay").surface).toBe("textured");
  v = nextView(v, "cull");
  expect(v.cull).toBe(true);
  expect(nextView(v, "cull").cull).toBe(false);
  expect(nextView(v, "normals").normals).toBe(true);
  expect(nextView(v, "wireframe").wireframe).toBe(true);
  expect(nextView(v, "grid").grid).toBe(false);
  // a preset is a request the stage applies once — the counter is what
  // makes the same preset asked twice apply twice
  const below = nextView(INITIAL_VIEW, "below");
  expect(below.preset).toBe("below");
  expect(nextView(below, "below").presetSeq).toBe(below.presetSeq + 1);
  expect(nextView(below, "reset").preset).toBe("reset");
});

test("the truth line explains the last mode touched, and the underside honestly", () => {
  const m = mesh();
  expect(truthLine(m, INITIAL_VIEW, false)).toMatch(
    /^Open surfaces — cull backfaces and orbit underneath/,
  );
  expect(truthLine(mesh({ watertight: true }), INITIAL_VIEW, false)).toMatch(
    /^Watertight/,
  );
  // a record that doesn't say is not judged (law 12)
  expect(truthLine(mesh({ watertight: null }), INITIAL_VIEW, false)).toBe(
    "This is the artefact that promotes; nothing here is decimated.",
  );
  expect(truthLine(m, nextView(INITIAL_VIEW, "clay"), false)).toMatch(
    /^Clay drops the baked texture/,
  );
  expect(truthLine(m, nextView(INITIAL_VIEW, "normals"), false)).toMatch(
    /^Facing as colour/,
  );
  expect(truthLine(m, nextView(INITIAL_VIEW, "wireframe"), false)).toMatch(
    /^Topology/,
  );
  // grid is on by default — the second press turns it back on, and that
  // is the one explained; turning a mode OFF explains nothing
  const gridOff = nextView(INITIAL_VIEW, "grid");
  expect(truthLine(m, gridOff, false)).toMatch(/^Open surfaces/);
  expect(truthLine(m, nextView(gridOff, "grid"), false)).toMatch(
    /^Ground and scale/,
  );
  // the pair that changes a promote decision: underneath, culled — and
  // the camera's actual side decides it, never a chip
  const culled = nextView(INITIAL_VIEW, "cull");
  expect(truthLine(m, culled, true)).toMatch(
    /the hole is where the shell never closed/,
  );
  expect(truthLine(mesh({ watertight: true }), culled, true)).toMatch(
    /would show as a hole/,
  );
  expect(truthLine(m, culled, false)).toMatch(/^Backfaces culled/);
  // underneath with backfaces drawn: the shell looks closed — said, so it
  // isn't believed
  expect(truthLine(m, INITIAL_VIEW, true)).toMatch(/looks closed/);
});

test("the poster's line and bar come from the record and the bytes", () => {
  expect(loadingLine(mesh())).toBe("LOADING THE MESH · 33.8 MB");
  expect(loadingLine(mesh({ file_mb: null }))).toBe("LOADING THE MESH");
  expect(loadFraction(5_000_000, 10_000_000, 33.8)).toBe(0.5);
  // no Content-Length: the record's own size stands in
  expect(loadFraction(16_900_000, 0, 33.8)).toBeCloseTo(0.5);
  expect(loadFraction(1, 0, null)).toBeNull();
  expect(loadFraction(20, 10, null)).toBe(1);
});

test("the corner names the take, the engine and where it came from", () => {
  const src = {
    ...mesh(),
    id: "src",
    payload: "image" as const,
    mesh: null,
    turn_idx: 0,
    slot_pos: 0,
  };
  expect(viewerHeader(mesh(), [src, mesh()])).toEqual({
    take: "Take 4a",
    facts: "3D · TRELLIS2 · FROM Take 0a",
  });
  // a parent that isn't in the feed is not named; an engine the record
  // doesn't carry is not either
  expect(viewerHeader(mesh(), [mesh()]).facts).toBe("3D · TRELLIS2");
  expect(viewerHeader(mesh({ engine: null }), []).facts).toBe("3D");
});
