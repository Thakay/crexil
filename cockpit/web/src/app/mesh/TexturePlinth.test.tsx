// The TEXTURE plinth as a surface: it opens on a mesh take, states
// the 40k rebuild before anything is queued, keeps the non-painters on
// screen dashed (law 12), offers the workspace's image takes as the second
// parent with the mesh's own source pre-picked, and posts one take of the
// texture verb with the reference in its own field.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import { TexturePlinth } from "./TexturePlinth";

const MESH = {
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
  created_at: "2026-08-30T00:00:00Z",
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
    fields_kept: true,
  },
};

const image = (id: string, turn_idx: number, prompt: string) => ({
  ...MESH,
  id,
  payload: "image",
  prompt,
  engine: "flux2-klein-4b",
  parents: [],
  turn_idx,
  mesh: null,
});

// the mesh's own source (turn 1) and a newer repainted concept (turn 6)
const NODES = [
  MESH,
  image("src", 1, "a knight"),
  image("gilded", 6, "the knight, gilded"),
];

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
  updated: "2026-08-30",
  engines: [
    engine("trellis2-4b", "TRELLIS.2 4B", ["generate", "decimate"]),
    engine("hunyuan3d-21", "Hunyuan3D 2.1", ["generate", "texture"]),
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

function stub({ nodes = NODES }: { nodes?: unknown[] } = {}) {
  const posts: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        posts.push({ path, body: await input.json() });
        return Response.json({ turn: {}, seq: 1 }, { status: 201 });
      }
      if (path === "/api/workspaces/w1/nodes") return Response.json(nodes);
      if (path === "/api/workspaces/w1")
        return Response.json({ id: "w1", name: "w", turns: [] });
      if (path === "/api/engines") return Response.json(ENGINES);
      if (path === "/api/mesh/health")
        return Response.json({
          ok: true,
          url: "http://host:8189",
          engines: ["gltfpack", "hunyuan3d-21", "trellis2-4b"],
          kinds: {
            generate: ["hunyuan3d-21", "trellis2-4b"],
            decimate: ["gltfpack", "trellis2-4b"],
            texture: ["hunyuan3d-21"],
          },
          why: null,
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
      <TexturePlinth nodeId="m1" workspaceId="w1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => useUi.setState({ textureNodeId: "m1" }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ textureNodeId: null });
});

test("it names its parent and says what the repaint does to it", async () => {
  stub();
  mount();
  expect(await screen.findByText("Take 4a")).toBeInTheDocument();
  // the count that will NOT survive the repaint, shown for exactly that
  // reason — and the rebuild stated before anything is queued
  expect(screen.getByText(/952,326 TRIS/)).toBeInTheDocument();
  expect(screen.getByText(/rebuilt at 40,000 faces/)).toBeInTheDocument();
});

test("the non-painters stay on the roster, dashed with the one that does", async () => {
  stub();
  mount();
  expect(await screen.findByText("Hunyuan3D 2.1")).toBeInTheDocument();
  const why = screen.getByText(/TRELLIS\.2 4B doesn't do texture/);
  expect(why.textContent).toMatch(/Hunyuan3D 2\.1 does/);
  expect(screen.getByText(/ENGINES FOR:/)).toHaveTextContent("TEXTURE");
});

test("it arms on the painter with the mesh's own source pre-picked, and posts the pair", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("spinbutton", { name: "Hunyuan3D 2.1 takes" }),
    ).toHaveValue(1),
  );
  // the ancestry default: the take this mesh was generated from
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Take 1a/, pressed: true }),
    ).toBeInTheDocument(),
  );

  await user.click(screen.getByRole("button", { name: "Texture · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/workspaces/w1/mesh-turns");
  expect(posts[0].body).toMatchObject({
    parents: ["m1"],
    kind: "texture",
    reference: "src",
    mesh: [{ engine_id: "hunyuan3d-21", takes: 1, params: {} }],
  });
});

test("picking another reference wins over the default and rides the body", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Take 1a/, pressed: true }),
    ).toBeInTheDocument(),
  );
  // the newer concept sits first in the strip (newest turn first)
  const strip = screen.getAllByRole("button", { name: /Take \d/ });
  expect(strip[0]).toHaveTextContent("Take 6a");
  await user.click(strip[0]);
  await user.click(screen.getByRole("button", { name: "Texture · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({ reference: "gilded" });
});

test("with no image takes it says why RUN can't fire, in words", async () => {
  stub({ nodes: [MESH] });
  mount();
  expect(
    await screen.findByText(/no image takes yet/),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Texture · 1" }),
    ).toBeDisabled(),
  );
});
