// The To-3D plinth as a surface: it opens on a take, states that
// the image is the whole input, steps engines, posts a MeshRun, and —
// when the runner is down — keeps both rows on screen with the reason
// (law 12: badge, never filter) while refusing to fire.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import { MeshPlinth } from "./MeshPlinth";

const NODE = {
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
  x: 400,
  y: 120,
  created_at: "2026-08-27T00:00:00Z",
  parents: [],
  turn_idx: 1,
  slot_pos: 1,
  promoted_at: null,
};

function engine(
  id: string,
  name: string,
  payload: "image" | "3d",
  // The verbs the row declares (law 16). The roster dashes an engine
  // that doesn't declare the plinth's verb, so a 3D fixture that means to
  // be steppable here has to say it generates.
  verbs: string[] = payload === "3d" ? ["generate"] : [],
  // The knob spec the Advanced fold draws from. Authored in the
  // matrix, never in the web — so a fixture that wants a fold says so here.
  params: Record<string, Record<string, unknown>> = {},
) {
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
  };
}

const TRELLIS_PARAMS = {
  generate: {
    pipeline_type: {
      label: "RESOLUTION TIER",
      type: "enum",
      choices: ["512", "1024_cascade"],
      default: "1024_cascade",
      state: "unverified",
      effect: "The cascade refines on the bigger grid.",
    },
  },
};

// the dashed row: Hunyuan's atlas size is fixed inside the pipeline (law 12 —
// it is listed with the reason, never left out)
const HUNYUAN_PARAMS = {
  generate: {
    texture_size: {
      label: "TEXTURE SIZE",
      supported: false,
      why: "Paint bakes its atlas at a size fixed inside the pipeline.",
    },
  },
};

const ENGINES = {
  updated: "2026-08-27",
  engines: [
    engine("zimage-turbo", "Z-Image Turbo", "image"),
    engine("trellis2-4b", "TRELLIS.2 4B", "3d", ["generate"], TRELLIS_PARAMS),
    engine("hunyuan3d-21", "Hunyuan3D 2.1", "3d", ["generate"], HUNYUAN_PARAMS),
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
}: { up?: boolean; postStatus?: number; detail?: string } = {}) {
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
      if (path === "/api/workspaces/w1/nodes") return Response.json([NODE]);
      if (path === "/api/workspaces/w1")
        return Response.json({ id: "w1", name: "w", turns: [] });
      if (path === "/api/engines") return Response.json(ENGINES);
      if (path === "/api/mesh/health")
        return Response.json({
          ok: up,
          url: "http://host:8189",
          engines: ["hunyuan3d-21", "trellis2-4b"],
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
      <MeshPlinth nodeId="n1" workspaceId="w1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => useUi.setState({ meshNodeId: "n1" }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ meshNodeId: null });
});

test("it names its source and says the image is the whole input", async () => {
  stub();
  mount();
  expect(await screen.findByText("Take 1b")).toBeInTheDocument();
  expect(
    screen.getByText(/The image is the input — no prompt, no dials, no frame/),
  ).toBeInTheDocument();
  // no prompt control exists on this surface at all
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("the roster is the matrix's 3D rows, and only those", async () => {
  stub();
  mount();
  expect(await screen.findByText("TRELLIS.2 4B")).toBeInTheDocument();
  expect(screen.getByText("Hunyuan3D 2.1")).toBeInTheDocument();
  expect(screen.queryByText("Z-Image Turbo")).not.toBeInTheDocument();
});

test("it arms at one take on the first engine, and posts what the roster shows", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  const trellis = await screen.findByRole("spinbutton", {
    name: "TRELLIS.2 4B takes",
  });
  await waitFor(() => expect(trellis).toHaveValue(1));
  await user.click(
    screen.getByRole("button", { name: "Hunyuan3D 2.1 takes +" }),
  );
  await user.click(screen.getByRole("button", { name: "To 3D · 2" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/workspaces/w1/mesh-turns");
  expect(posts[0].body).toEqual({
    parents: ["n1"],
    kind: "generate",
    mesh: [
      { engine_id: "trellis2-4b", takes: 1, params: {} },
      { engine_id: "hunyuan3d-21", takes: 1, params: {} },
    ],
    seed: { mode: "random" },
    // one column pitch right of the dragged source (400,120)
    anchor: { x: 620, y: 120 },
  });
  // a launched run closes the plinth — the grid is the next surface
  await waitFor(() => expect(useUi.getState().meshNodeId).toBeNull());
});

test("the stepper stops at the 3D lane's lower cap", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  const plus = await screen.findByRole("button", {
    name: "TRELLIS.2 4B takes +",
  });
  for (let i = 0; i < 6; i++) await user.click(plus);
  expect(
    screen.getByRole("spinbutton", { name: "TRELLIS.2 4B takes" }),
  ).toHaveValue(4);
  expect(plus).toBeDisabled();
});

// law 12 — the row that cannot run is the row that says why
test("a down runner dims both rows with its own words and disarms Run", async () => {
  stub({ up: false });
  mount();
  expect(await screen.findByText("TRELLIS.2 4B")).toBeInTheDocument();
  expect(screen.getByText("Hunyuan3D 2.1")).toBeInTheDocument();
  expect(screen.getAllByText(DOWN_WHY).length).toBeGreaterThanOrEqual(1);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /To 3D/ })).toBeDisabled(),
  );
  expect(
    screen.getByRole("spinbutton", { name: "TRELLIS.2 4B takes" }),
  ).toBeDisabled();
});

test("the bar states the minutes and the one GPU, and predicts no total", async () => {
  stub();
  mount();
  expect(
    await screen.findByText(
      /Meshes take the single GPU one at a time, in minutes/,
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByText("1 MESH · 1 ENGINE · NO SPEND")).toBeInTheDocument(),
  );
});

test("a refused run shows the server's words and keeps the plinth open", async () => {
  stub({ postStatus: 422, detail: "a mesh run caps at 8 takes" });
  const user = userEvent.setup();
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("spinbutton", { name: "TRELLIS.2 4B takes" }),
    ).toHaveValue(1),
  );
  await user.click(screen.getByRole("button", { name: "To 3D · 1" }));
  expect(
    await screen.findByText("a mesh run caps at 8 takes"),
  ).toBeInTheDocument();
  expect(useUi.getState().meshNodeId).toBe("n1");
});

test("Esc closes it and nothing is queued", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await screen.findByText("TRELLIS.2 4B");
  await user.keyboard("{Escape}");
  expect(useUi.getState().meshNodeId).toBeNull();
  expect(posts).toEqual([]);
});

// ── the Advanced fold ──────────────────────────────────────────────────

test("the fold is collapsed on arm and counts the settings behind it", async () => {
  stub();
  mount();
  // armed at one take on TRELLIS.2 — one knob, plus the run's seed
  const head = await screen.findByRole("button", { name: /ADVANCED/ });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(head).toHaveTextContent("ADVANCED · 2 SETTINGS");
  expect(head).toHaveTextContent("0 CHANGED FROM DEFAULT");
  // The line is inside the fold, so nothing about the tier is on screen yet
  expect(screen.queryByLabelText("RESOLUTION TIER")).not.toBeInTheDocument();
});

test("an opened knob states its effect and what it will send", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /ADVANCED/ }));
  expect(screen.getByLabelText("RESOLUTION TIER")).toBeInTheDocument();
  // the plain-effect line is permanently visible, never a tooltip
  expect(
    screen.getByText("The cascade refines on the bigger grid."),
  ).toBeInTheDocument();
  // untouched: it says what happens if nothing is sent, and that the range
  // has not been run (law 12)
  expect(
    screen.getByText("DEFAULT 1024_cascade · RANGE UNVERIFIED"),
  ).toBeInTheDocument();
});

test("a set knob rides the run, and the head counts it", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /ADVANCED/ }));
  await user.selectOptions(screen.getByLabelText("RESOLUTION TIER"), "512");
  expect(
    screen.getByText("SENDS 512 · DEFAULT 1024_cascade · RANGE UNVERIFIED"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /ADVANCED/ }),
  ).toHaveTextContent("1 CHANGED FROM DEFAULT");

  await user.click(screen.getByRole("button", { name: "To 3D · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].body as { mesh: unknown[] }).mesh).toEqual([
    {
      engine_id: "trellis2-4b",
      takes: 1,
      params: { pipeline_type: "512" },
    },
  ]);
});

// RESET means "send nothing", not "send the default" — the engine's own
// default governs, and it is free to change.
test("RESET takes the knob out of the request entirely", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /ADVANCED/ }));
  await user.selectOptions(screen.getByLabelText("RESOLUTION TIER"), "512");
  await user.click(screen.getByRole("button", { name: "Reset RESOLUTION TIER" }));
  await user.click(screen.getByRole("button", { name: "To 3D · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].body as { mesh: { params: unknown }[] }).mesh[0].params).toEqual(
    {},
  );
});

test("a locked seed goes out as the run's policy, and unlocked rolls", async () => {
  const posts = stub();
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /ADVANCED/ }));
  expect(screen.getByText("A NEW NUMBER PER TAKE")).toBeInTheDocument();
  await user.click(screen.getByLabelText("LOCK"));
  const seed = screen.getByLabelText("Seed value");
  await user.clear(seed);
  await user.type(seed, "77");
  await user.click(screen.getByRole("button", { name: "To 3D · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].body as { seed: unknown }).seed).toEqual({
    mode: "locked",
    value: 77,
  });
});

// law 12 on this surface: the handle Hunyuan hasn't got is DRAWN, dashed, with
// the reason — an artist who found a texture size on TRELLIS.2 and none here
// learns why instead of wondering whether the app forgot it.
test("a handle an engine lacks is listed with its reason, not hidden", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  await user.click(
    await screen.findByRole("button", { name: "Hunyuan3D 2.1 takes +" }),
  );
  await user.click(screen.getByRole("button", { name: /ADVANCED/ }));
  expect(screen.getByText("TEXTURE SIZE")).toBeInTheDocument();
  expect(screen.getByText("NO HANDLE ON THIS ENGINE")).toBeInTheDocument();
  expect(
    screen.getByText("Paint bakes its atlas at a size fixed inside the pipeline."),
  ).toBeInTheDocument();
  // and it is not a control the artist can touch
  expect(screen.queryByLabelText("TEXTURE SIZE")).not.toBeInTheDocument();
});
