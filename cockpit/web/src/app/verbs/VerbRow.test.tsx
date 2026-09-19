// The row as a surface: what it says it will do, it does; what it
// can't do, it still shows and refuses to fire. Selection comes from the ui
// store (the canvas's ordered mirror), so these mount without React Flow.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import { VerbRow } from "./VerbRow";

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
  x: null,
  y: null,
  created_at: "2026-08-20T00:00:00Z",
  parents: [],
  turn_idx: 1,
  slot_pos: 1,
  promoted_at: null,
};

// The turn the node came from, as the detail serves it: the
// record's recipe reads the turn's applied raws, never the node's
const WS = {
  id: "w1",
  name: "Sword",
  slug: "sword",
  viewport: {},
  dials: {},
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
  charged_usd: 0,
  turns: [
    {
      id: "t1",
      workspace_id: "w1",
      idx: 1,
      prompt: "a knight",
      dials: {
        seed: { mode: "locked", value: 42 },
        engines: { "zimage-turbo": { steps: 9, cfg: 1, width: 1024, height: 1024 } },
      },
      anchor_x: null,
      anchor_y: null,
      created_at: "2026-08-20T00:00:00Z",
      slots: [],
    },
  ],
};

const PREVIEW = {
  node_id: "n1",
  asset_id: "a1",
  folder: "/mnt/d/PROMOTED/hero-sword",
  files: [
    {
      path: "/mnt/d/PROMOTED/hero-sword/a1.png",
      what: "the take, full resolution, byte for byte",
    },
    {
      path: "/mnt/d/PROMOTED/hero-sword/a1.json",
      what: "its manifest — engine, prompt, seed",
    },
  ],
  facts: "1b · ZIMAGE-TURBO · S 42",
  cost: "This take cost no money — it ran on your own card.",
  replayable: "exact: the submitted graph is verbatim",
  target: {
    path: "/mnt/d/PROMOTED",
    windows_path: "D:\\PROMOTED",
    ok: true,
    message: null,
    remedy: null,
    where: "on the Windows drive mounted at /mnt/d",
    source: "env",
    default_path: "/mnt/d/PROMOTED",
    roots: ["/mnt/d"],
    roots_said: "D:\\",
  },
  already: [],
};

// the feed the stub serves — a test may add an imported node beside n1
let NODES: object[] = [NODE];

function stub(postStatus = 200, detail?: string) {
  const posts: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        posts.push(path);
        return detail === undefined
          ? Response.json({}, { status: postStatus })
          : Response.json({ detail }, { status: postStatus });
      }
      if (path === "/api/workspaces/w1/nodes") return Response.json(NODES);
      if (path === "/api/workspaces/w1") return Response.json(WS);
      if (path === "/api/nodes/n1/promote-preview")
        return Response.json(PREVIEW);
      return Response.json({}, { status: 404 });
    }),
  );
  return posts;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <VerbRow workspaceId="w1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => useUi.setState({ selection: [], armWanted: null }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  NODES = [NODE];
  useUi.setState({
    selection: [],
    armWanted: null,
    armed: null,
    gridOpen: false,
    promoteNodeId: null,
    meshNodeId: null,
    lightboxNodeId: null,
  });
});

test("acting on nothing: root verb only, and firing it asks the canvas to arm", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  expect(screen.getByText("ACTING ON NOTHING")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /NEW IMAGE · ROOT/ }));
  expect(useUi.getState().armWanted).toEqual({ parents: [], sweep: null, prompt: null });
});

test("a selected node retargets the row and states what it knows", async () => {
  stub();
  useUi.setState({ selection: ["n1"] });
  mount();
  expect(await screen.findByText("Take 1b")).toBeInTheDocument();
  expect(screen.getByText("IMAGE · ZIMAGE-TURBO · SEEDED")).toBeInTheDocument();
  expect(
    screen.getByText(
      "⌫ SETS ASIDE · ⇧CLICK OR ⇧DRAG SELECTS MORE · ESC ACTS ON NOTHING",
    ),
  ).toBeInTheDocument();
});

// ⌫ is SET ASIDE's keystroke: the tray drop destination's parity
// key. It fires what the chip fires, under the chip's gate.
test("⌫ sets the selected card aside, and the chip shows its key", async () => {
  const posts = stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();
  const chip = await screen.findByRole("button", { name: /SET ASIDE/ });
  expect(chip).toHaveTextContent("⌫");
  await user.keyboard("{Backspace}");
  await waitFor(() => expect(posts).toEqual(["/api/nodes/n1/set-aside"]));
});

test("⌫ is inert while the grid holds the keys, and takes a whole selection", async () => {
  const posts = stub();
  useUi.setState({ selection: ["n1"], gridOpen: true });
  const user = userEvent.setup();
  mount();
  await screen.findByText("Take 1b");
  await user.keyboard("{Backspace}");
  await new Promise((r) => setTimeout(r, 30));
  expect(posts).toEqual([]); // the grid's hovered-cell ⌫ owns the key now

  // two selected: the chip is live for both, and the key fires one
  // idempotent request per node — never a batch
  useUi.setState({ gridOpen: false });
  vi.unstubAllGlobals();
  const posts2: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        posts2.push(path);
        return Response.json({});
      }
      if (path === "/api/workspaces/w1/nodes")
        return Response.json([NODE, { ...NODE, id: "n2", slot_pos: 2 }]);
      return Response.json({}, { status: 404 });
    }),
  );
  cleanup();
  useUi.setState({ selection: ["n1", "n2"] });
  mount();
  await screen.findByText("2 nodes");
  expect(
    screen.getByRole("button", { name: /SET ASIDE/ }),
  ).toHaveTextContent("out of the way, not deleted");
  await user.keyboard("{Backspace}");
  await waitFor(() =>
    expect([...posts2].sort()).toEqual([
      "/api/nodes/n1/set-aside",
      "/api/nodes/n2/set-aside",
    ]),
  );
});

test("refine arms the composer on this take rather than firing a route", async () => {
  const posts = stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();

  // a refine still needs a prompt, an engine and a take count, so
  // the chip arms the composer — nothing is posted and nothing is spent
  await user.click(await screen.findByRole("button", { name: /REFINE/ }));
  expect(useUi.getState().armWanted).toEqual({ parents: ["n1"], sweep: null, prompt: null });
  expect(posts).toEqual([]);
  // VARY ONE DIAL: the same arm, with the sweep on ANCHOR HOLD,
  // still nothing posted, nothing spent, until Run
  await user.click(
    await screen.findByRole("button", { name: /VARY ONE DIAL/ }),
  );
  expect(useUi.getState().armWanted).toEqual({
    parents: ["n1"],
    sweep: "anchor-hold",
    prompt: null,
  });
  expect(posts).toEqual([]);
});

// TO 3D opens the mesh plinth in the dock rather than firing,
// like PROMOTE, and for the composer's reason: a run needs an engine and a
// take count. Nothing is queued by opening it, and a second press closes.
test("TO 3D opens its plinth on the take, and toggles shut", async () => {
  const posts = stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();
  const chip = await screen.findByRole("button", { name: /TO 3D/ });
  await user.click(chip);
  expect(useUi.getState().meshNodeId).toBe("n1");
  expect(posts).toEqual([]);
  await user.click(chip);
  expect(useUi.getState().meshNodeId).toBeNull();
});

test("a live verb fires its route; a blocked one is visible, reasoned, and inert", async () => {
  const posts = stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();

  const upscale = await screen.findByRole("button", { name: /UPSCALE/ });
  expect(upscale).toHaveAttribute("aria-disabled", "true");
  await user.click(upscale);
  expect(posts).toEqual([]); // nothing fired, nothing spent

  await user.click(screen.getByRole("button", { name: /REROLL/ }));
  await waitFor(() => expect(posts).toEqual(["/api/slots/s1/reroll"]));

  await user.click(screen.getByRole("button", { name: /SET ASIDE/ }));
  await waitFor(() => expect(posts).toContain("/api/nodes/n1/set-aside"));
});

test("a group refused for one reason states it once, not once per chip", async () => {
  stub();
  useUi.setState({ selection: ["n1"] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === "/api/workspaces/w1/nodes")
        return Response.json([{ ...NODE, payload: "3d", engine: "trellis2" }]);
      return Response.json({}, { status: 404 });
    }),
  );
  mount();
  expect(await screen.findByText("NOT FOR THIS NODE")).toBeInTheDocument();
  expect(
    screen.getAllByText("this node is a mesh — that verb works on images"),
  ).toHaveLength(1);
  // and every refused verb is still on screen, by name
  for (const label of ["REFINE", "EDIT REGION", "VARY ONE DIAL", "UPSCALE"])
    expect(screen.getByText(label)).toBeInTheDocument();
});

test("a refusal shows the server's own words, cost included", async () => {
  stub(
    409,
    "preset 'zimage-turbo' is no longer configured — the old take stays " +
      "and nothing was spent",
  );
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /REROLL/ }));
  expect(
    await screen.findByText(/the old take stays and nothing was spent/),
  ).toBeInTheDocument();
});

// --- the hairline, the plinth, and the words before the hold ---

test("the two verbs that can't be undone sit below their own line", async () => {
  stub();
  useUi.setState({ selection: ["n1"] });
  mount();
  await screen.findByText("Take 1b");
  expect(screen.getByText("CAN'T BE UNDONE")).toBeInTheDocument();
  // PROMOTE is live; PURGE is there too, dashed, pointing at the tray
  expect(
    screen.getByRole("button", { name: /PROMOTE/ }),
  ).toHaveAttribute("aria-disabled", "false");
  expect(screen.getByRole("button", { name: /PURGE/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );
});

test("PROMOTE opens the plinth — files, cost and the consequence, then the hold", async () => {
  const posts = stub();
  const user = userEvent.setup();
  useUi.setState({ selection: ["n1"] });
  mount();
  await screen.findByText("Take 1b");
  await user.click(screen.getByRole("button", { name: /PROMOTE/ }));

  expect(
    await screen.findByText("/mnt/d/PROMOTED/hero-sword/a1.png"),
  ).toBeInTheDocument();
  expect(screen.getByText("2 FILES")).toBeInTheDocument();
  // the artist's spelling leads, the cockpit's own is kept beside it
  expect(
    screen.getByText("D:\\PROMOTED — on the Windows drive mounted at /mnt/d"),
  ).toBeInTheDocument();
  expect(screen.getByText("/mnt/d/PROMOTED in here")).toBeInTheDocument();
  expect(
    screen.getByText("This take cost no money — it ran on your own card."),
  ).toBeInTheDocument();
  expect(screen.getByText(/cannot be un-happened/)).toBeInTheDocument();
  // and nothing has been written by looking
  expect(posts).toEqual([]);

  // the hold is a hold: a plain click spends nothing
  await user.click(screen.getByRole("button", { name: /Hold to hold/i }));
  expect(posts).toEqual([]);
});

test("a broken destination replaces the hold with its words and the re-point control", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === "/api/workspaces/w1/nodes") return Response.json(NODES);
      if (path === "/api/workspaces/w1") return Response.json(WS);
      if (path === "/api/nodes/n1/promote-preview")
        return Response.json({
          ...PREVIEW,
          target: {
            ...PREVIEW.target,
            ok: false,
            message: "the promote target /mnt/d/PROMOTED isn't there",
            remedy: "put the folder back, or point the cockpit at another folder",
          },
        });
      return Response.json({}, { status: 404 });
    }),
  );
  const user = userEvent.setup();
  useUi.setState({ selection: ["n1"] });
  mount();
  await screen.findByText("Take 1b");
  await user.click(screen.getByRole("button", { name: /PROMOTE/ }));

  expect(
    await screen.findByText(
      "the promote target /mnt/d/PROMOTED isn't there",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "put the folder back, or point the cockpit at another folder",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "USE THIS FOLDER" }),
  ).toBeInTheDocument();
  // and the fence is on screen beside it — where a re-point MAY go
  expect(screen.getByText(/Anywhere on D:\\/)).toBeInTheDocument();
  // no hold at all while the target is broken — there is nothing to hold to
  expect(
    screen.queryByRole("button", { name: /Hold to hold/i }),
  ).not.toBeInTheDocument();
  // and BACK TO .ENV only appears once something was actually pointed here
  expect(screen.queryByText("BACK TO .ENV")).not.toBeInTheDocument();
});

test("retargeting the selection closes the plinth — its words named one take", async () => {
  stub();
  const user = userEvent.setup();
  useUi.setState({ selection: ["n1"] });
  mount();
  await screen.findByText("Take 1b");
  await user.click(screen.getByRole("button", { name: /PROMOTE/ }));
  await screen.findByText("2 FILES");

  useUi.getState().setSelection([]);
  await waitFor(() =>
    expect(screen.queryByText("2 FILES")).not.toBeInTheDocument(),
  );
  expect(useUi.getState().promoteNodeId).toBeNull();
});

test("INSPECT opens the lightbox on the selected take, and V is its key", async () => {
  stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();
  expect(await screen.findByText("Take 1b")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /^INSPECT/ }));
  expect(useUi.getState().lightboxNodeId).toBe("n1");
  // the key does the same — but not while an overlay (the lightbox itself)
  // holds the keys
  useUi.setState({ lightboxNodeId: "other" });
  await user.keyboard("v");
  expect(useUi.getState().lightboxNodeId).toBe("other");
  useUi.setState({ lightboxNodeId: null });
  await user.keyboard("v");
  await waitFor(() => expect(useUi.getState().lightboxNodeId).toBe("n1"));
});

// The record under the head: the words, the turn's recipe
// said as the turn's, this take's own facts; P recalls the words into a
// fresh root arm, the same door as the failed paid cell's free remedy.
test("a selected take's record: three rows, the recipe worn as the turn's, and P recalls the words", async () => {
  stub();
  useUi.setState({ selection: ["n1"] });
  const user = userEvent.setup();
  mount();
  const record = await screen.findByRole("group", { name: "Provenance" });
  expect(record).toHaveAttribute("data-scope", "take");
  expect(screen.getByText("a knight")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByText("1024×1024 · STEPS 9 · CFG 1")).toBeInTheDocument(),
  );
  expect(screen.getByText("SEED LOCKED AT 42 · EVERY TAKE · FROM TEXT · A ROOT")).toBeInTheDocument();
  expect(screen.getByText(/THE TURN'S DIALS — a swept or retried cell/)).toBeInTheDocument();
  expect(screen.getByText("SEED 42 · ZIMAGE-TURBO · 2.2 S · RUN · NO SPEND")).toBeInTheDocument();
  // the door: the chip and the key both arm a root run carrying the words
  await user.click(screen.getByRole("button", { name: "USE THIS PROMPT · P" }));
  expect(useUi.getState().armWanted).toEqual({ parents: [], sweep: null, prompt: "a knight" });
  useUi.setState({ armWanted: null });
  await user.keyboard("p");
  expect(useUi.getState().armWanted).toEqual({ parents: [], sweep: null, prompt: "a knight" });
  // not while an overlay owns the keys
  useUi.setState({ armWanted: null, gridOpen: true });
  await user.keyboard("p");
  expect(useUi.getState().armWanted).toBeNull();
});

test("an imported node's record is the honest blank, in the facts line's own words, with no door", async () => {
  NODES = [
    NODE,
    {
      ...NODE,
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
    },
  ];
  stub();
  useUi.setState({ selection: ["imp"] });
  mount();
  expect(await screen.findByText("Imported node")).toBeInTheDocument();
  expect(screen.getByText(/^IMPORTED · NO PROMPT — this node came in from a file/)).toBeInTheDocument();
  expect(screen.getByText(/^NO TURN — an imported node ran nothing/)).toBeInTheDocument();
  expect(screen.getByText("NO SEED · IMPORTED · NO SPEND")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "USE THIS PROMPT · P" })).not.toBeInTheDocument();
  // and with both selected, different words recall nothing — said so
  useUi.setState({ selection: ["n1", "imp"] });
  expect(await screen.findByText("2 nodes")).toBeInTheDocument();
  expect(screen.getByText("2 SELECTED · 2 DIFFERENT PROMPTS — pick one to read its words")).toBeInTheDocument();
  expect(screen.getByText("2 TURNS AMONG THE 2 SELECTED — pick one to read its recipe")).toBeInTheDocument();
});
