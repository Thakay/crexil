// The DECIMATE plinth as a surface: it opens on a mesh take, prints
// the parent's own numbers, arms the target at a tenth of them, keeps the
// generators on screen dashed with the reason (law 12: badge, never filter),
// posts one take of the decimate verb, and states the force trade in both
// of its states before anything is queued.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import { DecimatePlinth } from "./DecimatePlinth";

// a landed TRELLIS.2 mesh, a measured artefact
const NODE = {
  id: "m1",
  workspace_id: "w1",
  turn_id: "t1",
  slot_id: "s1",
  take: 1,
  payload: "3d",
  verb: "to_3d",
  prompt: "a knight",
  engine: "trellis2-4b",
  seed: 424242,
  duration_ms: 94000,
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
  mesh: {
    engine: "trellis2",
    runner_job_id: "job-abc",
    triangles: 952326,
    vertices: 664517,
    watertight: false,
    textured: true,
    file_mb: 33.8,
    has_turntable: true,
    asked_triangles: null,
    forced: false,
    fields_kept: false,
  },
};

function engine(
  id: string,
  name: string,
  verbs: string[],
): Record<string, unknown> {
  return {
    id,
    name,
    lane: "local",
    payload: "3d",
    verbs,
    price: "free — local",
    note: null,
    capabilities: {},
  };
}

const ENGINES = {
  updated: "2026-08-29",
  engines: [
    engine("trellis2-4b", "TRELLIS.2 4B", ["generate", "decimate"]),
    engine("hunyuan3d-21", "Hunyuan3D 2.1", ["generate"]),
    engine("gltfpack", "gltfpack (meshopt)", ["decimate"]),
  ],
  external: {
    catalogue: "openrouter",
    defaults: {},
    lane: "external",
    payload: "image",
    price: "per response",
  },
};

const DOWN_WHY =
  "the 3D engine runner isn't answering — start it with " +
  "`systemctl --user start 3d-runner`";

function stub({
  up = true,
  postStatus = 201,
  detail,
  node = NODE,
}: {
  up?: boolean;
  postStatus?: number;
  detail?: string;
  node?: unknown;
} = {}) {
  const posts: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        posts.push({ path, body: await input.json() });
        return detail === undefined
          ? Response.json({ turn: {}, seq: 1 }, { status: postStatus })
          : Response.json({ detail }, { status: postStatus });
      }
      if (path === "/api/workspaces/w1/nodes") return Response.json([node]);
      if (path === "/api/workspaces/w1")
        return Response.json({ id: "w1", name: "w", turns: [] });
      if (path === "/api/engines") return Response.json(ENGINES);
      if (path === "/api/mesh/health")
        return Response.json({
          ok: up,
          url: "http://host:8189",
          engines: ["gltfpack", "hunyuan3d-21", "trellis2-4b"],
          kinds: {
            generate: ["hunyuan3d-21", "trellis2-4b"],
            decimate: ["gltfpack"],
          },
          why: up ? null : DOWN_WHY,
        });
      return Response.json({}, { status: 404 });
    }),
  );
  return posts;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DecimatePlinth nodeId="m1" workspaceId="w1" />
    </QueryClientProvider>,
  );
}

const target = () => screen.getByRole("spinbutton", { name: "TARGET TRIANGLES" });

beforeEach(() => useUi.setState({ decimateNodeId: "m1" }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ decimateNodeId: null });
});

test("it names its parent and prints the parent's own numbers", async () => {
  stub();
  mount();
  expect(await screen.findByText("Take 4a")).toBeInTheDocument();
  // the target below is measured against these — a handle asking for a
  // triangle count with no count in sight is a number typed into the dark
  expect(screen.getByText(/952,326 TRIS/)).toBeInTheDocument();
  expect(screen.getByText(/33.8 MB/)).toBeInTheDocument();
  // and what this verb does not touch, said rather than left to be inferred
  expect(screen.getByText(/Topology only/)).toBeInTheDocument();
});

// law 12, and the reason the verbs list exists at all
test("the generators stay on the roster, dashed, told apart from the tool", async () => {
  stub();
  mount();
  expect(await screen.findByText("gltfpack (meshopt)")).toBeInTheDocument();
  expect(screen.getByText("Hunyuan3D 2.1")).toBeInTheDocument();
  // TRELLIS.2 declares this verb too, so the pure generator is the
  // one carrying the "doesn't do decimate" line — and the sentence names
  // BOTH engines that do, which is the whole point of naming them
  const why = screen.getByText(/Hunyuan3D 2\.1 doesn't do decimate/);
  expect(why.textContent).toMatch(/gltfpack \(meshopt\)/);
  expect(why.textContent).toMatch(/TRELLIS\.2 4B/);
  // the roster heads the action
  expect(screen.getByText(/ENGINES FOR:/)).toHaveTextContent("DECIMATE");
});

test("it arms on the tool at one take, with the target a tenth of the parent", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("spinbutton", { name: "gltfpack (meshopt) takes" }),
    ).toHaveValue(1),
  );
  expect(target()).toHaveValue(95000);
  expect(screen.getByText(/OF 952,326/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Decimate · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/workspaces/w1/mesh-turns");
  expect(posts[0].body).toMatchObject({
    parents: ["m1"],
    kind: "decimate",
    mesh: [
      {
        engine_id: "gltfpack",
        takes: 1,
        params: { target_triangles: 95000, force: false },
      },
    ],
  });
});

test("a typed target survives the parent query, and rides the run", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() => expect(target()).toHaveValue(95000));
  await user.clear(target());
  await user.type(target(), "15000");
  await user.click(screen.getByRole("button", { name: "Decimate · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(
    (posts[0].body as { mesh: { params: { target_triangles: number } }[] })
      .mesh[0].params.target_triangles,
  ).toBe(15000);
});

// the whole point: the number is a request, and the artist is
// told so before they type, not after the child lands
test("the target says it is a request, not a setting", async () => {
  stub();
  mount();
  expect(
    await screen.findByText(/A request, not a setting/),
  ).toBeInTheDocument();
});

test("the force override is off, and says which trade each state makes", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  const force = await screen.findByRole("checkbox", {
    name: "FORCE THE EXACT COUNT",
  });
  expect(force).not.toBeChecked();
  expect(screen.getByText(/the mesh stops where its seams stop/)).toBeInTheDocument();

  await user.click(force);
  expect(force).toBeChecked();
  // the consequence, in the measurement that showed it
  expect(screen.getByText(/952,326 → 13,309/)).toBeInTheDocument();
  expect(screen.getByText(/shattered/)).toBeInTheDocument();
});

test("a forced run posts the override", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() => expect(target()).toHaveValue(95000));
  await user.click(
    screen.getByRole("checkbox", { name: "FORCE THE EXACT COUNT" }),
  );
  await user.click(screen.getByRole("button", { name: "Decimate · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(
    (posts[0].body as { mesh: { params: { force: boolean } }[] }).mesh[0].params
      .force,
  ).toBe(true);
});

test("a target at or above the parent's count disarms, in words", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() => expect(target()).toHaveValue(95000));
  await user.clear(target());
  await user.type(target(), "952326");
  expect(
    await screen.findByText(/decimate only removes, so ask for fewer/),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Decimate · 1" })).toBeDisabled();
});

test("a mesh whose count was never recorded asks for a number instead of guessing", async () => {
  stub({ node: { ...NODE, mesh: { ...NODE.mesh, triangles: null } } });
  mount();
  await waitFor(() => expect(target()).toHaveValue(null));
  expect(
    await screen.findByText("Type a target triangle count."),
  ).toBeInTheDocument();
});

// law 12 on the door: both rows stay, the button doesn't fire, the server's
// own sentence is what the artist reads
test("a down runner dashes the tool and refuses to fire, in the server's words", async () => {
  stub({ up: false });
  mount();
  expect(await screen.findByText("gltfpack (meshopt)")).toBeInTheDocument();
  expect(screen.getAllByText(DOWN_WHY).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Decimate · 0" })).toBeDisabled();
});

test("a refused run shows the server's words and keeps the plinth open", async () => {
  const detail =
    "decimate is deterministic — the same mesh and the same handles give " +
    "the same result every time, so more than one take of it would be " +
    "copies; ask for one";
  stub({ postStatus: 422, detail });
  const user = userEvent.setup();
  mount();
  await waitFor(() => expect(target()).toHaveValue(95000));
  await user.click(screen.getByRole("button", { name: "Decimate · 1" }));
  expect(await screen.findByText(detail)).toBeInTheDocument();
  expect(useUi.getState().decimateNodeId).toBe("m1");
});

test("the bar states the seconds and the shared lane, and predicts no total", async () => {
  stub();
  mount();
  expect(await screen.findByText(/gltfpack is a CPU tool/)).toBeInTheDocument();
  expect(screen.getByText(/takes the local lane's turn/)).toBeInTheDocument();
  // one take, and why — a second would be a copy, not another answer
  expect(
    screen.getByText(/would be a copy, not another answer/),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByText("1 MESH · 1 ENGINE · NO SPEND"),
    ).toBeInTheDocument(),
  );
  // no predicted total anywhere on the bar (law 17)
  expect(screen.queryByText(/about \d+ (second|minute)s/)).toBeNull();
});

// --- one verb, two engines, and the mesh decides which can serve it ---

// the same take, but this one kept its voxel field
const KEPT = { ...NODE, mesh: { ...NODE.mesh, fields_kept: true } };

test("TRELLIS.2 stays on screen, dashed, when the mesh kept no field", async () => {
  stub();
  mount();
  // it DECLARES decimate, so it is not the "doesn't do decimate" row — the
  // reason is this mesh's, and it names the move the artist makes next
  const row = await screen.findByText(/rebuilds from the voxel field/);
  expect(row.textContent).toMatch(/gltfpack/);
  // and the armed engine is still the one that can actually run
  expect(
    await screen.findByRole("button", { name: /Decimate · 1/ }),
  ).toBeEnabled();
});

test("a kept field arms the engine with no floor, and the handles follow it", async () => {
  const user = userEvent.setup();
  stub({ node: KEPT });
  mount();
  // Roster order arms the first capable row, and on a mesh that
  // kept its field that is TRELLIS.2 — which is the right default here for a
  // reason beyond ordering: it is the only one of the two that can go below
  // the ~8–10% seam floor.
  await waitFor(() =>
    expect(
      screen.getByRole("spinbutton", { name: "TRELLIS.2 4B takes" }),
    ).toHaveValue(1),
  );
  // re-extraction gets the handle IT has, and the copy that would be a lie
  // for it is not on screen
  expect(screen.getByLabelText("TEXTURE SIZE")).toBeInTheDocument();
  expect(screen.queryByLabelText("FORCE THE EXACT COUNT")).toBeNull();
  expect(screen.getByText(/A fresh bake, not a thinning/)).toBeInTheDocument();
  expect(screen.queryByText(/Topology only/)).toBeNull();

  // and stepping the simplifier swaps every one of them back — a control the
  // runner would refuse by name is never offered to the engine that lacks it
  await user.click(
    screen.getByRole("button", { name: "gltfpack (meshopt) takes +" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("FORCE THE EXACT COUNT")).toBeInTheDocument(),
  );
  expect(screen.queryByLabelText("TEXTURE SIZE")).toBeNull();
  expect(screen.getByText(/Topology only/)).toBeInTheDocument();
});

test("it posts the re-extractor's own params, never the simplifier's", async () => {
  const user = userEvent.setup();
  const posts = stub({ node: KEPT });
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("spinbutton", { name: "TRELLIS.2 4B takes" }),
    ).toHaveValue(1),
  );
  await user.selectOptions(screen.getByLabelText("TEXTURE SIZE"), "4096");
  await user.click(screen.getByRole("button", { name: /Decimate · 1/ }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as {
    kind: string;
    mesh: { engine_id: string; params: Record<string, unknown> }[];
  };
  expect(body.kind).toBe("decimate");
  expect(body.mesh[0].engine_id).toBe("trellis2-4b");
  expect(body.mesh[0].params).toEqual({
    target_triangles: 95_000,
    texture_size: 4096,
  });
});
