// The region surface's chrome: what it says (the binding
// precedence rule, the honest capability line), how a draft declares itself,
// the fan-out roster with its dashed-0 reasons (law 12), the
// attempt panes' faces and the commit bar. The Konva stage itself is
// e2e's — react-konva is mocked shallowly here, so these tests assert
// words and structure, never pixels. jsdom never fires the take image's
// onload, so the stage column shows its loading line throughout and FAN
// OUT stays disarmed on the pixels reason — that's expected, not a
// failure (the armed POST path is e2e's; the request body is
// fanout.test.ts's).
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import {
  DRAFTS_LINE,
  PRECEDENCE_LINE,
  SAME_REGIONS_LINE,
  UNCLAIMED_LINE,
} from "./model";
import { EXTERNALS_LINE, NO_RECIPES_REASON } from "./fanout";
import { useRegions } from "./store";

vi.mock("konva", () => ({ default: { Line: class {}, Rect: class {} } }));
vi.mock("react-konva", () => ({
  Stage: () => null,
  Layer: () => null,
  Line: () => null,
  Rect: () => null,
  Image: () => null,
}));

// The mocks above must land before the component pulls react-konva in.
const { RegionSurface } = await import("./RegionSurface");

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
  created_at: "2026-08-26T00:00:00Z",
  parents: [],
  turn_idx: 12,
  slot_pos: 1,
};

/** The fan turn's landed take (slot s9's current attempt). */
const FAN_NODE = {
  ...NODE,
  id: "n9",
  turn_id: "t9",
  slot_id: "s9",
  engine: "raw-sdxl",
  seed: 7,
  duration_ms: 19000,
  turn_idx: 13,
  slot_pos: 0,
};

const ENGINES = {
  engines: [
    {
      id: "sdxl-juggernaut-xi",
      name: "SDXL Juggernaut XI",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: { mask: { note: "", state: "verified", value: true } },
    },
    {
      id: "flux2-klein-4b",
      name: "FLUX.2 klein 4B",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: { mask: { note: "", state: "verified", value: false } },
    },
  ],
  external: {
    catalogue: "openrouter",
    defaults: {},
    lane: "external",
    payload: "image",
    price: "per response",
  },
  updated: "2026-08-26",
};

const PRESETS = [
  {
    id: "raw-sdxl",
    display_name: "Raw · SDXL Juggernaut",
    description: "",
    modality: "image",
    model_family: "sdxl-juggernaut-xi",
    workflows: {
      txt2img: "sdxl-txt2img@v1",
      inpaint_modify: "sdxl-inpaint@v1",
      inpaint_replace: "sdxl-replace@v1",
      remove: "lama-remove@v1",
    },
    default_candidates: 1,
    inputs: [],
  },
  {
    id: "raw-zimage",
    display_name: "Raw · Z-Image Turbo",
    description: "",
    modality: "image",
    model_family: "zimage-turbo",
    workflows: { txt2img: "zimage-txt2img@v1" },
    default_candidates: 1,
    inputs: [],
  },
];

function slot(patch: Record<string, unknown>) {
  return {
    id: "s9",
    turn_id: "t9",
    pos: 0,
    engine: "raw-sdxl",
    status: "landed",
    attempt: 1,
    seed: 7,
    committed_usd: null,
    charged_usd: null,
    cause: null,
    detail: null,
    retry_of: null,
    kind: null,
    job_ref: null,
    frame: null,
    facts: null,
    started_at: "2026-08-26T00:00:00Z",
    finished_at: "2026-08-26T00:00:19Z",
    created_at: "2026-08-26T00:00:00Z",
    ...patch,
  };
}

const QUEUE = {
  lanes: {
    local: {
      label: "LOCAL · SEQUENTIAL, ONE GPU",
      slots: [
        {
          id: "s10",
          engine: "raw-sdxl",
          turn_idx: 13,
          status: "running",
          elapsed_ms: 8000,
          passes: { current: 2, total: 4 },
          retry_of: null,
          cancel_requested: false,
          position: null,
          bench: null,
        },
      ],
    },
    external: { label: "EXTERNAL", slots: [] },
  },
  benches: [],
  held: [],
  checks: [],
  retries: [],
  gpu: { manual_hold: false },
};

function stub(opts: { enginesStatus?: number; fanSlots?: unknown[] } = {}) {
  const { enginesStatus = 200, fanSlots = [] } = opts;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === "/api/workspaces/w1/nodes")
        return Response.json([NODE, FAN_NODE]);
      if (path === "/api/engines")
        return enginesStatus === 200
          ? Response.json(ENGINES)
          : Response.json({ detail: "no matrix" }, { status: enginesStatus });
      if (path === "/api/presets") return Response.json(PRESETS);
      if (path === "/api/workspaces/w1")
        return Response.json({
          id: "w1",
          name: "w",
          created_at: "2026-08-26T00:00:00Z",
          turns: [
            {
              id: "t9",
              workspace_id: "w1",
              idx: 13,
              prompt: "a knight",
              dials: {},
              anchor_x: null,
              anchor_y: null,
              created_at: "2026-08-26T00:00:00Z",
              slots: fanSlots,
            },
          ],
        });
      if (path === "/api/queue") return Response.json(QUEUE);
      return Response.json({}, { status: 404 });
    }),
  );
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RegionSurface workspaceId="w1" nodeId="n1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useRegions.setState({
    drafts: {},
    activeKey: {},
    fanTurn: {},
    pendingFan: null,
    tool: "brush",
    brushSize: 36,
  });
  useUi.setState({ regionsNodeId: "n1", selectWanted: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ regionsNodeId: null, selectWanted: null });
});

test("the header names the surface and the take, and Esc's promise is stated", async () => {
  stub();
  mount();
  expect(screen.getByText("EDIT REGION")).toBeInTheDocument();
  expect(await screen.findByText("on Take 12b")).toBeInTheDocument();
  expect(
    screen.getByText("ESC CLOSES · REGIONS STAY WITH THE TAKE"),
  ).toBeInTheDocument();
  // The four tools, brush pressed by default; the brush size labelled.
  for (const word of ["BRUSH", "RECT", "LASSO", "ERASE"])
    expect(screen.getByText(word)).toBeInTheDocument();
  expect(screen.getByText("BRUSH 36 PX")).toBeInTheDocument();
});

test("the capability line reads the matrix and the drafts line is stated (law 12)", async () => {
  stub();
  mount();
  expect(
    await screen.findByText(
      "1 OF YOUR 2 LOCAL IMAGE ENGINES CAN MASK · SDXL JUGGERNAUT XI",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText(DRAFTS_LINE)).toBeInTheDocument();
});

test("an unreachable matrix is said in the one shared line (law 1)", async () => {
  stub({ enginesStatus: 503 });
  mount();
  expect(
    await screen.findByText(
      "Capability matrix unreachable — no row can say what it can do.",
    ),
  ).toBeInTheDocument();
});

test("a draft declares itself: action words, prompt only where one applies", async () => {
  stub();
  mount();
  expect(
    screen.getByText("DRAW A REGION, THEN DECLARE WHAT IT MEANS"),
  ).toBeInTheDocument();

  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "+ NEW REGION" }));
  expect(screen.getByText("R1 · MODIFY")).toBeInTheDocument();
  expect(screen.getByText("NO INK YET")).toBeInTheDocument();
  // MODIFY carries its own prompt, falling back to the turn's.
  expect(screen.getByLabelText("R1 prompt")).toHaveAttribute(
    "placeholder",
    "region prompt — empty falls back to the turn prompt",
  );

  // KEEP: locked, no prompt field, the reason in words.
  await user.selectOptions(screen.getByLabelText("R1 action"), "keep");
  expect(screen.getByText("R1 · KEEP")).toBeInTheDocument();
  expect(screen.queryByLabelText("R1 prompt")).toBeNull();
  expect(
    screen.getByText("LOCKED · NO PROMPT — it stays exactly as it is"),
  ).toBeInTheDocument();

  // DELETE: no prompt either — the fill comes from around it.
  await user.selectOptions(screen.getByLabelText("R1 action"), "delete");
  expect(
    screen.getByText("NO PROMPT — the fill comes from the surrounding image"),
  ).toBeInTheDocument();

  // A second draft numbers itself and both rows stand.
  await user.click(screen.getByRole("button", { name: "+ NEW REGION" }));
  expect(screen.getByText("R2 · MODIFY")).toBeInTheDocument();

  // Removing R2 leaves R1 (drafts are compose state — no purge ritual).
  await user.click(screen.getByRole("button", { name: "Remove R2" }));
  expect(screen.queryByText("R2 · MODIFY")).toBeNull();
  expect(screen.getByText("R1 · DELETE")).toBeInTheDocument();
});

test("the footer prints the BINDING precedence order — the policy's", () => {
  stub();
  mount();
  expect(screen.getByText(PRECEDENCE_LINE)).toBeInTheDocument();
  expect(screen.getByText(UNCLAIMED_LINE)).toBeInTheDocument();
  expect(screen.getByText(SAME_REGIONS_LINE)).toBeInTheDocument();
});

test("the header ✕ closes the surface — the drafts stay in the store", async () => {
  stub();
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "+ NEW REGION" }));
  await user.click(
    screen.getByRole("button", { name: "Close the region surface" }),
  );
  expect(useUi.getState().regionsNodeId).toBeNull();
  expect(useRegions.getState().drafts.n1).toHaveLength(1);
});

// ── the fan-out roster, gate and panes ─────────────────────────────────────

test("the roster badges, never filters: capable rows step, the incapable wear a dashed 0 and the reason", async () => {
  stub();
  mount();
  // Capable first, seeded at one take (the source's engine can't mask, so
  // the first capable engine starts the count).
  const sdxl = await screen.findByLabelText("Raw · SDXL Juggernaut takes");
  expect(sdxl).toHaveValue(1);
  expect(sdxl).not.toBeDisabled();
  // The incapable row stays listed — disabled stepper, reason in words.
  const zimage = screen.getByLabelText("Raw · Z-Image Turbo takes");
  expect(zimage).toBeDisabled();
  expect(zimage).toHaveValue(0);
  expect(screen.getByText(NO_RECIPES_REASON)).toBeInTheDocument();
  // Externals are one structural line, not per-model rows.
  expect(screen.getByText(EXTERNALS_LINE)).toBeInTheDocument();
});

test("the turn prompt arrives carried in from the source take", async () => {
  stub();
  mount();
  await screen.findByText("on Take 12b"); // the node has arrived
  expect(screen.getByLabelText("Turn prompt")).toHaveValue("a knight");
});

test("FAN OUT is disarmed in words while nothing is drawn (and jsdom never loads pixels)", async () => {
  stub();
  mount();
  const btn = await screen.findByRole("button", { name: "FAN OUT" });
  expect(btn).toBeDisabled();
  // jsdom never fires the image's onload, so once the roster settles the
  // pixels reason leads — the same register the drawn-nothing reason uses.
  expect(
    await screen.findByText("The take's pixels haven't loaded yet."),
  ).toBeInTheDocument();
});

test("the attempt panes render the fan turn's slots side by side", async () => {
  stub({
    fanSlots: [
      slot({}),
      slot({ id: "s10", pos: 1, status: "running", seed: null }),
      slot({
        id: "s11",
        pos: 2,
        engine: "raw-qwen-edit",
        status: "failed",
        cause: "raw-qwen-edit failed partway through the graph — the engine, not your prompt",
        kind: "error",
        seed: null,
      }),
    ],
  });
  useRegions.setState({ fanTurn: { n1: "t9" } });
  mount();
  expect(await screen.findByText("3 ATTEMPTS · SAME MASK")).toBeInTheDocument();
  // The landed pane: engine, ✓ duration, seed — facts only.
  expect(screen.getByText("✓ 19S · S 7")).toBeInTheDocument();
  // The running pane reads the queue view: elapsed + pass position.
  expect(screen.getByText("RUNNING · 8S · PASS 2 OF 4")).toBeInTheDocument();
  // The failed pane: the plain cause and its free next move (law 4).
  expect(
    screen.getByText(
      "raw-qwen-edit failed partway through the graph — the engine, not your prompt",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "REROLL · NEW SEED" }),
  ).toBeInTheDocument();
  // The commit truths under the panes.
  expect(screen.getByText("THE REST STAY ON THE CANVAS AS A BRANCH")).toBeInTheDocument();
  expect(
    screen.getByText("NOTHING HERE IS IRREVERSIBLE — PROMOTE IS ELSEWHERE"),
  ).toBeInTheDocument();
});

test("CONTINUE HERE hands the picked take to the canvas as the selection and closes", async () => {
  stub({ fanSlots: [slot({})] });
  useRegions.setState({ fanTurn: { n1: "t9" } });
  mount();
  const user = userEvent.setup();
  await screen.findByText("1 ATTEMPT · SAME MASK");
  const go = screen.getByRole("button", { name: "CONTINUE HERE" });
  expect(go).toBeDisabled();
  expect(
    screen.getByText("PICK AN ATTEMPT TO CONTINUE FROM"),
  ).toBeInTheDocument();
  // Pick the landed pane (click its engine name — the pane is the target).
  await user.click(screen.getByText("RAW-SDXL"));
  expect(screen.getByText("CONTINUE FROM RAW-SDXL · 13a")).toBeInTheDocument();
  await user.click(go);
  expect(useUi.getState().selectWanted).toEqual(["n9"]);
  expect(useUi.getState().regionsNodeId).toBeNull();
  // KEEP ALL's promise held: nothing was set aside, nothing deleted — the
  // drafts and the fan turn pointer survive for the next open.
  expect(useRegions.getState().fanTurn.n1).toBe("t9");
});

test("same-region strokes land in ONE bitmap — no per-stroke layers to stack", () => {
  const ctx = {
    globalCompositeOperation: "source-over",
    fillStyle: "",
    strokeStyle: "",
    lineCap: "",
    lineJoin: "",
    lineWidth: 0,
    fillRect: () => {},
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    closePath: () => {},
    fill: () => {},
    stroke: () => {},
  };
  const spy = vi
    .spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  const st = useRegions.getState();
  const key = st.addDraft("n1");
  const natural = { w: 100, h: 100 };
  st.commitInk("n1", key, { tool: "rect", x: 0, y: 0, w: 5, h: 5 }, natural);
  const after1 = useRegions.getState().drafts.n1[0];
  st.commitInk("n1", key, { tool: "rect", x: 4, y: 4, w: 5, h: 5 }, natural);
  const after2 = useRegions.getState().drafts.n1[0];
  expect(after1.mask).not.toBeNull();
  expect(after2.mask).toBe(after1.mask); // the same one canvas, mutated
  expect(after2.inkRev).toBe(2);
  expect(after2.inked).toBe(true);
  // An erase rasterises into the SAME bitmap too — nothing appends.
  st.commitInk(
    "n1",
    key,
    { tool: "erase", points: [1, 1, 2, 2], size: 4 },
    natural,
  );
  expect(useRegions.getState().drafts.n1[0].mask).toBe(after1.mask);
  spy.mockRestore();
});
