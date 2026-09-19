// The armed composer: roster honesty (a preset without
// the recipe this run needs stays listed with a dashed 0 and the reason in
// words), law-6 price on the Run face, the POST body carrying the summon
// anchor (the node is born where the palette was), and the
// six dials: position 50 posts every engine's recipe raws, a moved
// dial changes what's sent and persists per workspace, a row that doesn't
// transmit is a badge with its reason, and a sweep pins slots with the
// seed held. The take count is per engine: a stepper on
// every roster row, several engines in one batch each with their own raws
// and their own fold, and a run bar that says the one-GPU truth in counts
// and observed time — never a predicted total. Armed with a parent it
// is the same surface as a refine: the source's prompt carried in, the
// roster gated on img2img, the external lane out.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUi } from "../state/ui";
import { MATRIX_UNREACHABLE, NO_RECORD } from "./badges";
import { Composer } from "./Composer";
import { SDXL, TABLE, TURBO, ZIMAGE } from "./dials.test";

const PRESETS = [
  {
    id: "region-only",
    display_name: "Region rails",
    description: "",
    modality: "image",
    workflows: { inpaint_modify: "wf/x.json" }, // no txt2img — dimmed, listed
    default_candidates: 4,
    inputs: [],
  },
  {
    ...ZIMAGE,
    display_name: "Z-Image raw",
    workflows: { txt2img: "wf/z.json" }, // no img2img — dimmed when refining
    // a raw knob no dial owns — the fold's, and the word only lives in there
    inputs: [
      ...ZIMAGE.inputs,
      { key: "sampler_name", label: "Sampler", type: "enum", default: "euler", choices: ["euler", "heun", "dpmpp_2m"], supported: true },
    ],
  },
  { ...SDXL, display_name: "SDXL raw" },
  { ...TURBO, display_name: "Sketch turbo" },
];

const SOURCE = {
  id: "n1",
  workspace_id: "w1",
  turn_id: "t1",
  slot_id: "s1",
  take: 1,
  payload: "image",
  verb: "run",
  prompt: "a rusted greatsword",
  engine: "raw-sdxl",
  seed: 42,
  duration_ms: 2200,
  committed_usd: null,
  charged_usd: null,
  set_aside: false,
  x: null,
  y: null,
  created_at: "2026-08-21T00:00:00Z",
  parents: [],
  turn_idx: 3,
  slot_pos: 1,
};

const PROVIDERS = {
  transport: "openrouter",
  key_present: true,
  fetched_at: null,
  model_count: 2,
  models: [
    {
      id: "openai/gpt-image-2",
      provider: "openrouter",
      label: "GPT Image 2",
      name: "GPT Image 2",
      supports_seed: false,
      pinned: true,
      source: "config",
      price_usd_per_image: 0.04,
    },
    {
      id: "some/unpriced",
      provider: "openrouter",
      label: "Unpriced Model",
      name: "Unpriced Model",
      supports_seed: false,
      pinned: false,
      source: "live",
      price_usd_per_image: null,
    },
  ],
};

// configs/engines.json's shape: every flag with its state. The
// roster badges and the FRAME warning read it; nothing guesses.
const v = (value: unknown, state = "verified", note = "n") => ({ value, state, note });
const ENGINES = {
  updated: "2026-08-11",
  engines: [
    {
      id: "sdxl-juggernaut-xi",
      name: "SDXL Juggernaut XI",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: {
        mask: v(true),
        seed: v(true),
        imageRef: v(true),
        styleRef: v(false),
        maxResolution: v(2048, "unverified", "SDXL native is 1024 — expect composition drift above it."),
        tokenLimit: v(77, "unverified"),
      },
    },
    {
      id: "z-image-base",
      name: "Z-Image Base",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: {
        mask: v(false),
        seed: v(true),
        imageRef: v(true),
        styleRef: v(false),
        maxResolution: v(2048, "unverified"),
        tokenLimit: v(null, "unverified"),
      },
    },
    {
      id: "z-image-turbo",
      name: "Z-Image Turbo",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: {
        mask: v(false),
        seed: v(true),
        imageRef: v(false),
        styleRef: v(false),
        maxResolution: v(2048, "unverified"),
        tokenLimit: v(null, "unverified"),
      },
    },
  ],
  external: {
    lane: "external",
    payload: "image",
    price: "",
    catalogue: "",
    defaults: {
      mask: v(false),
      imageRef: v(false),
      styleRef: v(false),
      seed: v(false, "unverified"),
      maxResolution: v(null, "unverified"),
      tokenLimit: v(null, "unverified"),
    },
  },
};

const WS = {
  id: "w1",
  name: "Hero Sword",
  slug: "hero-sword",
  viewport: {},
  dials: {},
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:00:00Z",
  turns: [],
  charged_usd: 0,
};

// The live queue as the strip polls it: lane labels are server-sent (law
// 13 copy lives in api_v4), and the "queues behind" line reads the depth.
const LANES = {
  local: { label: "LOCAL · SEQUENTIAL, ONE GPU", depth: 0, slots: [] as unknown[] },
  external: { label: "EXTERNAL · CONCURRENT, PRICED", concurrency: 4, active: 0, slots: [] },
};
const IDLE_QUEUE = {
  seq: 1,
  now: "2026-08-24T00:00:00Z",
  depth: 0,
  lanes: LANES,
  held: [],
  late: [],
  benches: [], checks: [], retries: [], gpu: { reading: null, rechecking: false, manual_hold: false },
};
const RUNNING = {
  id: "s-run",
  turn_id: "t9",
  turn_idx: 9,
  workspace_id: "w1",
  engine: "raw-zimage",
  status: "running",
  attempt: 1,
  position: 0,
  elapsed_ms: 12_000,
  committed_usd: null,
  charged_usd: null,
  cancel_requested: false,
  awaiting: false,
  bench: null,
  created_at: "2026-08-24T00:00:00Z",
};
const BUSY_QUEUE = {
  ...IDLE_QUEUE,
  depth: 2,
  lanes: {
    ...LANES,
    local: {
      ...LANES.local,
      depth: 2,
      slots: [RUNNING, { ...RUNNING, id: "s-q", status: "queued", position: 1, elapsed_ms: null }],
    },
  },
};

// position 50 = the recipe, sent explicitly — the seed policy
// rides the turn, not the batch
const DEFAULT_INPUTS = { steps: 36, cfg: 4, width: 1024, height: 1024 };

// E6: a sheet with room to spare and a clear card (the default);
// a spec that wants the disarm or the warning hands in its own.
const HEADROOM = {
  gpu: null,
  gpu_hold_threshold_bytes: 2684354560,
  gpu_held: false,
  disk_free_bytes: 200 * 1024 * 1024 * 1024,
  disk_margin_bytes: 64 * 1024 * 1024,
  disk_path: "/data/staging",
  bytes_per_px: 1.5,
  bytes_per_px_source: "fallback",
  at: "2026-08-25T00:00:00+00:00",
};

function stub(
  ws: object = WS,
  queue: object = IDLE_QUEUE,
  engines: object | null = ENGINES,
  headroom: object = HEADROOM,
) {
  const posts: { path: string; body: unknown }[] = [];
  const patches: { path: string; body: { dials?: unknown } }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        posts.push({ path, body: await input.json().catch(() => null) });
        return Response.json({ turn: {}, seq: 1 }, { status: 201 });
      }
      if (input.method === "PATCH") {
        const body = (await input.json()) as { dials?: unknown };
        patches.push({ path, body });
        return Response.json({ ...WS, dials: body.dials ?? {} });
      }
      if (path === "/api/presets") return Response.json(PRESETS);
      if (path === "/api/providers") return Response.json(PROVIDERS);
      if (path === "/api/dials") return Response.json(TABLE);
      if (path === "/api/engines")
        return engines === null
          ? Response.json(
              { detail: "configs/engines.json is unreadable" },
              { status: 503 },
            )
          : Response.json(engines);
      if (path === "/api/queue") return Response.json(queue);
      if (path === "/api/headroom") return Response.json(headroom);
      if (path === "/api/workspaces/w1") return Response.json(ws);
      if (path === "/api/workspaces/w1/nodes")
        return Response.json([SOURCE]);
      return Response.json({}, { status: 404 });
    }),
  );
  return { posts, patches };
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Composer workspaceId="w1" />
    </QueryClientProvider>,
  );
}

const takesOf = (name: string) =>
  screen.getByRole("spinbutton", { name: `${name} takes` });
const findTakes = (name: string) =>
  screen.findByRole("spinbutton", { name: `${name} takes` });
const stepUp = (name: string) =>
  screen.getByRole("button", { name: `${name} takes +` });
const stepDown = (name: string) =>
  screen.getByRole("button", { name: `${name} takes −` });

type Body = {
  prompt: string;
  local: { preset_id: string; takes: number; inputs: unknown; take_overrides: unknown[] }[];
  external: { model_id: string; takes: number }[];
  seed: unknown;
  parents: string[];
  anchor: unknown;
  local_only: boolean;
};

beforeEach(() => {
  useUi.setState({ armed: { fx: 300.4, fy: 400.6, parents: [] } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ armed: null, localOnly: false });
});

test("E5: a prompt past a stepped engine's window is warned per engine as ABOUT, names the window's state, and Run stays armed", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  const prompt = await screen.findByLabelText("Prompt", { exact: true });
  const long = Array.from({ length: 60 }, (_, i) => `word${i}`).join(", ");
  fireEvent.change(prompt, { target: { value: long } });
  await findTakes("SDXL raw");
  // the default row is Z-Image raw (no pinned window): silence, not a guess
  expect(screen.queryByText(/TOKEN WINDOW/)).toBeNull();
  await user.click(stepUp("SDXL raw"));
  const warn = await screen.findByText(/SDXL RAW — READ ABOUT \d+ TOKENS AGAINST A 77-TOKEN WINDOW \(UNVERIFIED\)/);
  expect(warn).toHaveTextContent("1 OF YOUR 2 SLOTS");
  expect(warn).toHaveTextContent("NOTHING IS CUT HERE");
  expect(screen.getByRole("button", { name: /^Run · 2/ })).toBeEnabled();
  // shortened under the window: the line goes
  fireEvent.change(prompt, { target: { value: "a rusty sword" } });
  expect(screen.queryByText(/TOKEN WINDOW/)).toBeNull();
});

test("LOCAL ONLY: the external lane goes inert with the one cause on it, the run bar names the switch, the POST carries it — and flipping it back hands the counts back", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  mount();

  await user.type(await screen.findByLabelText("Prompt"), "a sword, kept in");
  await user.click(stepUp("GPT Image 2"));
  await user.click(stepUp("GPT Image 2"));
  expect(screen.getByText("3 SLOTS · ~$0.08 THIS RUN")).toBeInTheDocument();

  act(() => useUi.setState({ localOnly: true }));
  expect(
    screen.getByText(/LOCAL ONLY is on: no take leaves this machine/),
  ).toBeInTheDocument();
  // rows listed, steppers off — never filtered (law 12); one cause, one message
  expect(takesOf("GPT Image 2")).toBeDisabled();
  expect(takesOf("Unpriced Model")).toBeDisabled();
  expect(screen.queryByText(/text brief only/)).not.toBeInTheDocument();
  expect(screen.getByText("1 SLOT · NO SPEND · LOCAL ONLY")).toBeInTheDocument();
  expect(screen.getByText("1 local runs alone on the single GPU.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run · 1" })).toBeEnabled();

  // off again: the two stepped takes were remembered, not thrown away
  act(() => useUi.setState({ localOnly: false }));
  expect(takesOf("GPT Image 2")).toBeEnabled();
  expect(takesOf("GPT Image 2")).toHaveValue(2);
  expect(screen.getByText("3 SLOTS · ~$0.08 THIS RUN")).toBeInTheDocument();

  act(() => useUi.setState({ localOnly: true }));
  await screen.findByRole("slider", { name: "COOK TIME" }); // dials arrived
  await user.click(screen.getByRole("button", { name: "Run · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local_only).toBe(true);
  expect(body.external).toEqual([]); // nothing stepped there rides along
  expect(body.local).toHaveLength(1);
});

test("armed chip shows the anchor; the roster is grouped and honest, one stepper per row; run needs a prompt", async () => {
  stub();
  mount();
  expect(screen.getByText("✛ 300, 401")).toBeInTheDocument();

  // default: the first preset that can run txt2img, at one take
  expect(await findTakes("Z-Image raw")).toHaveValue(1);
  expect(takesOf("SDXL raw")).toHaveValue(0);
  // the recipe-less preset is listed with a dashed 0 and the reason (law 12)
  expect(takesOf("Region rails")).toBeDisabled();
  expect(takesOf("Region rails")).toHaveValue(0);
  expect(screen.getByText("no txt2img recipe committed")).toBeInTheDocument();
  // the lanes are grouped by the physics that matter, in the server's words
  expect(await screen.findByText("LOCAL · SEQUENTIAL, ONE GPU")).toBeInTheDocument();
  expect(screen.getByText("EXTERNAL · CONCURRENT, PRICED")).toBeInTheDocument();

  expect(screen.getByText("1 SLOT · NO SPEND")).toBeInTheDocument();
  expect(screen.getByText("1 local runs alone on the single GPU.")).toBeInTheDocument();
  expect(screen.getByText("Needs a prompt.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run · 1" })).toBeDisabled();
});

test("costed run carries the price on the button face; POST carries the anchor, one batch per engine and the recipe raws", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  mount();

  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  await user.click(stepUp("GPT Image 2"));
  expect(screen.getByText("2 SLOTS · ~$0.04 THIS RUN")).toBeInTheDocument();
  expect(
    screen.getByText("1 local runs alone on the single GPU; 1 external goes out at once and usually lands first."),
  ).toBeInTheDocument();
  await screen.findByRole("slider", { name: "COOK TIME" }); // dials arrived

  const run = screen.getByRole("button", { name: "Run · 2 · ~$0.04" });
  expect(run).toBeEnabled();
  await user.click(run);

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/workspaces/w1/turns");
  expect(posts[0].body).toEqual({
    prompt: "a blood sword",
    local: [
      { preset_id: "raw-zimage", takes: 1, inputs: DEFAULT_INPUTS, take_overrides: [] },
    ],
    external: [{ model_id: "openai/gpt-image-2", takes: 1 }],
    seed: { mode: "random" },
    parents: [],
    anchor: { x: 300.4, y: 400.6 },
    local_only: false,
  });
  // success disarms — the composer folds back to its idle copy
  await waitFor(() => expect(useUi.getState().armed).toBeNull());
});

test("armed on a parent: the source's prompt is carried in and the roster narrows", async () => {
  stub();
  useUi.setState({ armed: { fx: 700, fy: 120, parents: ["n1"] } });
  mount();

  expect(await screen.findByText("Refine · from a take")).toBeInTheDocument();
  expect(screen.getByText("Born beside its parent")).toBeInTheDocument();
  // named once the node feed lands — until then the chip says SOURCE, it
  // does not invent a take number
  expect(await screen.findByText("Take 3b")).toBeInTheDocument();
  // same prompt, new seeds — carried in, still editable
  expect(await screen.findByLabelText("Prompt")).toHaveValue(
    "a rusted greatsword",
  );

  // the engine that CAN refine is stepped; the txt2img-only ones are listed
  // at a dashed 0 and say which recipe they are missing (law 12)
  expect(await findTakes("SDXL raw")).toHaveValue(1);
  expect(takesOf("Z-Image raw")).toBeDisabled();
  expect(screen.getAllByText("no img2img recipe committed")).toHaveLength(3);

  // the external lane stays listed but can't take part, once, on the lane
  expect(
    screen.getByText(/External models take a text brief only/),
  ).toBeInTheDocument();
  expect(takesOf("GPT Image 2")).toBeDisabled();
});

test("a refine POSTs its parent, no external model, and a real denoise", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  useUi.setState({ armed: { fx: 700, fy: 120, parents: ["n1"] } });
  mount();

  await waitFor(() =>
    expect(screen.getByLabelText("Prompt")).toHaveValue("a rusted greatsword"),
  );
  // ANCHOR HOLD has a channel on a refine: the SDXL recipe's 0.5 at 50
  expect(await screen.findByText("SENDS DENOISE 0.5")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({
    prompt: "a rusted greatsword",
    local: [
      {
        preset_id: "raw-sdxl",
        takes: 1,
        inputs: { denoise: 0.5, cfg: 5, steps: 28, width: 1024, height: 1024 },
        take_overrides: [],
      },
    ],
    external: [],
    seed: { mode: "random" },
    parents: ["n1"],
    anchor: { x: 700, y: 120 },
    local_only: false,
  });
});

test("Esc discards the run from anywhere — the body, a stepper, the prompt — and a re-arm puts the caret back in the prompt", async () => {
  stub();
  mount();
  const prompt = await screen.findByLabelText("Prompt");
  await screen.findByRole("slider", { name: "COOK TIME" });
  expect(prompt).toHaveFocus();

  // a second arm without a disarm (the chip over the palette's run): the
  // component stays up, the caret comes back to the prompt all the same
  stepUp("SDXL raw").focus();
  expect(prompt).not.toHaveFocus();
  useUi.setState({ armed: { fx: 10, fy: 20, parents: [] } });
  await waitFor(() => expect(prompt).toHaveFocus());

  // Esc with focus outside the dock entirely (what the chip and the
  // closing palette leave behind)
  (document.activeElement as HTMLElement | null)?.blur();
  fireEvent.keyDown(document.body, { key: "Escape", code: "Escape" });
  expect(useUi.getState().armed).toBeNull();

  // and from inside a form field, where a global hotkey would normally stand down
  useUi.setState({ armed: { fx: 10, fy: 20, parents: [] } });
  const again = await screen.findByLabelText("Prompt");
  await waitFor(() => expect(again).toHaveFocus());
  fireEvent.keyDown(again, { key: "Escape", code: "Escape" });
  expect(useUi.getState().armed).toBeNull();
});

test("an overlay that is up owns Esc: the palette over an armed run dismisses alone", async () => {
  stub();
  mount();
  await screen.findByLabelText("Prompt");
  act(() => useUi.setState({ summonAt: { sx: 1, sy: 1, fx: 1, fy: 1 } }));
  fireEvent.keyDown(document.body, { key: "Escape", code: "Escape" });
  expect(useUi.getState().armed).not.toBeNull(); // radix closes the palette; the run stays
  act(() => useUi.setState({ summonAt: null }));
  fireEvent.keyDown(document.body, { key: "Escape", code: "Escape" });
  expect(useUi.getState().armed).toBeNull();
});

test("an unlisted price is stated, never guessed", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  await screen.findByRole("slider", { name: "COOK TIME" });
  await user.click(stepUp("Unpriced Model"));
  expect(screen.getByText("2 SLOTS · NO SPEND · 1 PRICE UNLISTED")).toBeInTheDocument();
  expect(screen.getAllByText("unlisted").length).toBeGreaterThan(0);
});

// ── one number per engine ───────────────────────────────────────────────────

test("several engines in one batch: every dial prints each engine's own numbers, one fold per engine, and the POST carries a batch per engine", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  mount();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  await screen.findByRole("slider", { name: "COOK TIME" });
  await user.click(stepUp("SDXL raw"));
  await user.click(stepUp("SDXL raw"));
  expect(takesOf("SDXL raw")).toHaveValue(2);

  // the `SENDS FLUX 3.5 · SDXL 7.0` print: one line per engine, named
  expect(screen.getByText("Z-IMAGE RAW · SENDS CFG 4")).toBeInTheDocument();
  expect(screen.getByText("SDXL RAW · SENDS CFG 5")).toBeInTheDocument();
  expect(screen.getByText("Z-IMAGE RAW · SENDS STEPS 36")).toBeInTheDocument();
  expect(screen.getByText("SDXL RAW · SENDS STEPS 28")).toBeInTheDocument();
  expect(screen.getByText("Z-IMAGE RAW · SENDS WIDTH 1024 · HEIGHT 1024")).toBeInTheDocument();
  // an identical badge is said once (law 1): both are inert on a root run
  expect(screen.getAllByText("inert on a root run — there is no anchor to hold")).toHaveLength(1);
  // one fold per engine, each named and counted on its own
  expect(
    screen.getByRole("button", { name: /ADVANCED · Z-IMAGE RAW · 5 ENGINE-SPECIFIC SETTINGS/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /ADVANCED · SDXL RAW · 4 ENGINE-SPECIFIC SETTINGS/ }),
  ).toBeInTheDocument();

  // the run bar counts and says how the batch will run — no wall-clock
  expect(screen.getByText("3 SLOTS · NO SPEND")).toBeInTheDocument();
  expect(screen.getByText("3 local run one after another on the single GPU.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run · 3" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local).toEqual([
    { preset_id: "raw-zimage", takes: 1, inputs: DEFAULT_INPUTS, take_overrides: [] },
    {
      preset_id: "raw-sdxl",
      takes: 2,
      inputs: { cfg: 5, steps: 28, width: 1024, height: 1024 },
      take_overrides: [],
    },
  ]);
  expect(body.external).toEqual([]);
});

test("an external row's takes are priced per take; every local row at 0 is an honest external-only run", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  mount();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  await screen.findByRole("slider", { name: "COOK TIME" });
  await user.click(stepUp("GPT Image 2"));
  await user.click(stepUp("GPT Image 2"));
  expect(takesOf("GPT Image 2")).toHaveValue(2);
  expect(screen.getByText("3 SLOTS · ~$0.08 THIS RUN")).toBeInTheDocument();
  expect(screen.getByText("2 EXTERNAL SLOTS TAKE THE WORDS ONLY — DIALS DON'T REACH THEM")).toBeInTheDocument();

  // the default local row stepped down: nothing re-picks it underneath the artist
  await user.click(stepDown("Z-Image raw"));
  expect(takesOf("Z-Image raw")).toHaveValue(0);
  expect(screen.getByText("2 SLOTS · ~$0.08 THIS RUN")).toBeInTheDocument();
  expect(
    screen.getByText("The 2 externals go out at once and usually land first; nothing touches the GPU."),
  ).toBeInTheDocument();
  expect(screen.getAllByText("— · NO LOCAL ENGINE PICKED").length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: "Run · 2 · ~$0.08" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local).toEqual([]);
  expect(body.external).toEqual([{ model_id: "openai/gpt-image-2", takes: 2 }]);
});

test("a busy GPU is said in observed time, and Run says what it queues behind", async () => {
  stub(WS, BUSY_QUEUE);
  const user = userEvent.setup();
  mount();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  expect(
    await screen.findByText(
      "GPU busy — RAW-ZIMAGE running 12s so far, 1 queued; your 1 local queues behind them.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run · 1 · queues behind 2" })).toBeEnabled();
  // externals never queue behind the GPU: with no local row stepped the line goes
  await user.click(stepUp("GPT Image 2"));
  await user.click(stepDown("Z-Image raw"));
  expect(screen.queryByText(/GPU busy/)).toBeNull();
  expect(screen.getByRole("button", { name: "Run · 1 · ~$0.04" })).toBeEnabled();
});

// ── the six dials ───────────────────────────────────────────────────────────

test("six dials, each with its permanent effect line and the picked engine's raws; no sampler above the fold", async () => {
  stub();
  const { container } = mount();
  await screen.findByRole("slider", { name: "ANCHOR HOLD" }); // the table and the workspace arrived
  const dials = screen.getByRole("region", { name: "Dials" });
  for (const label of ["ANCHOR HOLD", "PROMPT GRIP", "COOK TIME"])
    expect(screen.getByRole("slider", { name: label })).toHaveAttribute("type", "range");
  expect(screen.getByLabelText("Frame size")).toHaveAttribute("type", "range");
  expect(screen.getByLabelText("Seed")).toHaveAttribute("type", "number");
  expect(screen.getByRole("radiogroup", { name: "Sweep target" })).toBeInTheDocument();
  for (const d of TABLE.dials) expect(dials).toHaveTextContent(d.effect);
  // position 50 prints the recipe (Z-Image raw is the one stepped engine — unnamed)
  expect(screen.getByText("SENDS CFG 4")).toBeInTheDocument();
  expect(screen.getByText("SENDS STEPS 36")).toBeInTheDocument();
  expect(screen.getByText("SENDS WIDTH 1024 · HEIGHT 1024")).toBeInTheDocument();
  expect(screen.getByText("FRESH ROLL PER TAKE")).toBeInTheDocument();
  // a root run has no anchor — the dial stays, the raw print reads —
  expect(screen.getByText("inert on a root run — there is no anchor to hold")).toBeInTheDocument();
  // the word never appears above the Advanced fold
  expect(container.textContent?.toLowerCase()).not.toContain("sampler");
});

test("a moved dial changes the raw print and what is POSTed, and persists per workspace", async () => {
  const { posts, patches } = stub();
  const user = userEvent.setup();
  mount();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");

  const grip = await screen.findByRole("slider", { name: "PROMPT GRIP" });
  fireEvent.change(grip, { target: { value: "75" } });
  expect(screen.getByText("SENDS CFG 5.5")).toBeInTheDocument();
  // FRAME: a 3:4 frame keeps the area and lands on the 64-px grid
  await user.click(screen.getByRole("radio", { name: "3:4" }));
  expect(screen.getByText("896 × 1152")).toBeInTheDocument();
  // SEED: locked carries the value to every take
  fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "44182" } });
  await user.click(screen.getByRole("button", { name: "Lock seed" }));
  expect(screen.getByText("LOCKED · 44182 TO EVERY TAKE")).toBeInTheDocument();

  // positions reach the workspace, debounced
  await waitFor(() => expect(patches.length).toBeGreaterThan(0), { timeout: 3000 });
  const last = patches[patches.length - 1];
  expect(last.path).toBe("/api/workspaces/w1/view");
  expect(last.body.dials).toMatchObject({
    "prompt-grip": 75,
    frame: { aspect: "3:4", size: 1024 },
    seed: { locked: true, value: 44182 },
  });

  await user.click(screen.getByRole("button", { name: "Run · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local[0].inputs).toEqual({
    steps: 36,
    cfg: 5.5,
    width: 896,
    height: 1152,
  });
  expect(body.seed).toEqual({ mode: "locked", value: 44182 });
});

test("remembered positions come back with the workspace", async () => {
  stub({ ...WS, dials: { "cook-time": 80, seed: { locked: true, value: 7 } } });
  mount();
  expect(await screen.findByRole("slider", { name: "COOK TIME" })).toHaveValue("80");
  expect(screen.getByText("SENDS STEPS 44")).toBeInTheDocument(); // 36 + 0.6·14
  expect(screen.getByText("LOCKED · 7 TO EVERY TAKE")).toBeInTheDocument();
});

test("an engine that doesn't transmit a dial keeps it as a badge with the reason (law 12)", async () => {
  stub();
  const user = userEvent.setup();
  mount();
  await screen.findByRole("slider", { name: "COOK TIME" });
  // swap the batch onto the sketch preset alone
  await user.click(stepUp("Sketch turbo"));
  await user.click(stepDown("Z-Image raw"));
  const grip = screen.getByRole("slider", { name: "PROMPT GRIP" }).closest("[data-dial]")!;
  expect(grip).toHaveTextContent("FOLLOWS ITS RECIPE");
  expect(grip).toHaveTextContent(/cfg 1\.0 pinned in the graph/);
  // and the dial is still there to move — the slot follows its recipe
  expect(screen.getByRole("slider", { name: "PROMPT GRIP" })).toBeEnabled();
  expect(screen.getByText("SENDS STEPS 9")).toBeInTheDocument();
  // the external lane is said once, on the dials' head, not six times
  await user.click(stepUp("GPT Image 2"));
  expect(screen.getAllByText(/TAKE THE WORDS ONLY — DIALS DON'T REACH THEM/)).toHaveLength(1);
});

test("VARY ONE DIAL arms a sweep: the stepped row walks four slots, the seed held, one override per slot", async () => {
  const { posts } = stub();
  const user = userEvent.setup();
  useUi.setState({ armed: { fx: 700, fy: 120, parents: ["n1"], sweep: "anchor-hold" } });
  mount();

  expect(await screen.findByText("Sweep · from a take")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByLabelText("Prompt")).toHaveValue("a rusted greatsword"),
  );
  await screen.findByRole("slider", { name: "COOK TIME" });
  // the row's number is the walk while sweeping — the table's default, 2 up to the cap
  const takes = await findTakes("SDXL raw");
  expect(takes).toBeEnabled();
  expect(takes).toHaveValue(4);
  expect(takes).toHaveAttribute("min", "2");
  expect(screen.getByText(/each stepped engine walks the sweep — 2 to 16 slots each/)).toBeInTheDocument();
  expect(screen.getByText(/LOCKED FOR THE SWEEP/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Lock seed" })).toBeDisabled();
  expect(
    screen.getByText("WALKS ANCHOR HOLD 34 · 50 · 66 · 82 → DENOISE 0.65 · 0.5 · 0.35 · 0.25"),
  ).toBeInTheDocument();
  expect(screen.getByText(/· SWEEPS ANCHOR HOLD/)).toBeInTheDocument();

  // the target can be changed on the dial itself
  await user.click(screen.getByRole("radio", { name: "COOK TIME" }));
  expect(
    screen.getByText("WALKS COOK TIME 34 · 50 · 66 · 82 → STEPS 23 · 28 · 34 · 41"),
  ).toBeInTheDocument();

  // and the count is the row's stepper: six slots walk six values
  fireEvent.change(takes, { target: { value: "6" } });
  expect(screen.getByText("6 SLOTS")).toBeInTheDocument();
  expect(
    screen.getByText("WALKS COOK TIME 18 · 34 · 50 · 66 · 82 · 98 → STEPS 18 · 23 · 28 · 34 · 41 · 47"),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Run · 6" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local).toHaveLength(1);
  expect(body.local[0].preset_id).toBe("raw-sdxl");
  expect(body.local[0].takes).toBe(6);
  expect(body.parents).toEqual(["n1"]);
  expect(body.seed).toMatchObject({ mode: "locked" });
  expect(body.local[0].take_overrides).toEqual([
    { steps: 18 },
    { steps: 23 },
    { steps: 28 },
    { steps: 34 },
    { steps: 41 },
    { steps: 47 },
  ]);
});

test("the sweep switched off hands the take count back; stepping below the walk's floor leaves the batch", async () => {
  stub();
  const user = userEvent.setup();
  useUi.setState({ armed: { fx: 700, fy: 120, parents: [], sweep: "prompt-grip" } });
  mount();
  expect(await screen.findByText("Sweep · new root")).toBeInTheDocument();
  await screen.findByRole("slider", { name: "COOK TIME" });
  expect(takesOf("Z-Image raw")).toHaveValue(4);
  await user.click(await screen.findByRole("radio", { name: "OFF" }));
  expect(screen.getByText("New image · root")).toBeInTheDocument();
  expect(takesOf("Z-Image raw")).toHaveValue(1);
  expect(screen.getByText(/^OFF · EVERY TAKE RUNS THE DIALS AS SET/)).toBeInTheDocument();

  // back on: the walk again — and − below its floor of 2 drops the engine
  await user.click(screen.getByRole("radio", { name: "PROMPT GRIP" }));
  expect(takesOf("Z-Image raw")).toHaveValue(4);
  await user.click(stepDown("Z-Image raw"));
  await user.click(stepDown("Z-Image raw"));
  expect(takesOf("Z-Image raw")).toHaveValue(2);
  await user.click(stepDown("Z-Image raw"));
  expect(takesOf("Z-Image raw")).toHaveValue(0);
  expect(screen.getByText("A run needs at least one take.")).toBeInTheDocument();
});

test("no dial table: the composer says so and sends nothing for the dials", async () => {
  const { posts } = stub();
  type Fetch = (input: Request) => Promise<Response>;
  const fetchFn = globalThis.fetch as unknown as ReturnType<typeof vi.fn<Fetch>>;
  const real = fetchFn.getMockImplementation() as Fetch;
  fetchFn.mockImplementation(async (input: Request) =>
    new URL(input.url).pathname === "/api/dials"
      ? Response.json({ detail: "configs/dials.json is unreadable" }, { status: 503 })
      : real(input),
  );
  const user = userEvent.setup();
  mount();
  expect(await screen.findByText(/Dial table unreachable/)).toBeInTheDocument();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  await user.click(screen.getByRole("button", { name: "Run · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  const body = posts[0].body as Body;
  expect(body.local).toEqual([
    { preset_id: "raw-zimage", takes: 1, inputs: {}, take_overrides: [] },
  ]);
  expect(body.seed).toEqual({ mode: "random" });
});

// ── the Advanced fold ───────────────────────────────────────────────────────

test("the Advanced fold: collapsed with its count and no sampler above it; opened, the raw knobs are sent, counted, held on the dial and persisted", async () => {
  const { posts, patches } = stub();
  const user = userEvent.setup();
  const { container } = mount();
  await user.type(await screen.findByLabelText("Prompt"), "a blood sword");
  await screen.findByRole("slider", { name: "COOK TIME" });

  // Z-Image raw on a root run: steps · cfg · width · height · sampler
  const head = screen.getByRole("button", { name: /ADVANCED · 5 ENGINE-SPECIFIC SETTINGS/ });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(head).toHaveTextContent("Nothing in here changes what the picture is of.");
  expect(head).toHaveTextContent("0 CHANGED FROM DEFAULT");
  expect(screen.queryByRole("combobox", { name: "Sampler" })).toBeNull();
  expect(container.textContent?.toLowerCase()).not.toContain("sampler");

  await user.click(head);
  expect(head).toHaveAttribute("aria-expanded", "true");
  const sampler = screen.getByRole("combobox", { name: "Sampler" });
  expect(sampler).toHaveValue("euler");
  // a dial-owned key at its full raw range: the dial's raw is the baseline,
  // the preset's ceiling (60) is past where the band (50) stops
  const steps = screen.getByRole("spinbutton", { name: "steps" });
  expect(steps).toHaveValue(36);
  expect(steps).toHaveAttribute("max", "60");
  expect(screen.getByText("COOK TIME SENDS 36 · RAW 8–60")).toBeInTheDocument();

  await user.selectOptions(sampler, "heun");
  expect(head).toHaveTextContent("1 CHANGED FROM DEFAULT");
  fireEvent.change(steps, { target: { value: "60" } });
  expect(head).toHaveTextContent("2 CHANGED FROM DEFAULT");
  // the dial says the fold speaks for its key now
  const cook = screen.getByRole("slider", { name: "COOK TIME" }).closest("[data-dial]")!;
  expect(cook).toHaveTextContent("ADVANCED SETS STEPS 60");
  expect(screen.getByText("SENDS 60 · OVERRIDES COOK TIME 36")).toBeInTheDocument();

  // persisted beside the positions
  await waitFor(() => expect(patches.length).toBeGreaterThan(0), { timeout: 3000 });
  expect(patches[patches.length - 1].body.dials).toMatchObject({
    advanced: { "raw-zimage": { sampler_name: "heun", steps: 60 } },
  });

  await user.click(screen.getByRole("button", { name: "Run · 1" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].body as Body).local[0].inputs).toEqual({
    ...DEFAULT_INPUTS,
    steps: 60,
    sampler_name: "heun",
  });
});

test("remembered overrides come back with the workspace and count while collapsed; RESET hands the key back to the dial; a second engine brings its own fold", async () => {
  stub({ ...WS, dials: { advanced: { "raw-zimage": { steps: 8, sampler_name: "heun" } } } });
  const user = userEvent.setup();
  mount();
  await screen.findByRole("slider", { name: "COOK TIME" });
  const head = screen.getByRole("button", { name: /ADVANCED · 5 ENGINE-SPECIFIC SETTINGS/ });
  expect(head).toHaveTextContent("2 CHANGED FROM DEFAULT");
  expect(screen.getByText("ADVANCED SETS STEPS 8")).toBeInTheDocument();

  await user.click(head);
  await user.click(screen.getByRole("button", { name: "Reset steps" }));
  expect(head).toHaveTextContent("1 CHANGED FROM DEFAULT");
  expect(screen.getByText("SENDS STEPS 36")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "RESET ALL TO DEFAULT" }));
  expect(head).toHaveTextContent("0 CHANGED FROM DEFAULT");
  expect(screen.getByRole("combobox", { name: "Sampler" })).toHaveValue("euler");

  // another engine stepped in: its fold is its own, per engine — both named now
  await user.click(stepUp("Sketch turbo"));
  expect(
    screen.getByRole("button", { name: /ADVANCED · SKETCH TURBO · 3 ENGINE-SPECIFIC SETTINGS/ }),
  ).toHaveTextContent("0 CHANGED FROM DEFAULT");
  expect(
    screen.getByRole("button", { name: /ADVANCED · Z-IMAGE RAW · 5 ENGINE-SPECIFIC SETTINGS/ }),
  ).toBeInTheDocument();
});

test("every roster row carries its engine's capability badges with the state on them (law 12)", async () => {
  stub();
  mount();
  await findTakes("SDXL raw");
  // verified channels plain, the unverified ceilings wearing the word —
  // from the matrix, per engine, in roster order
  expect(
    await screen.findByText(
      "SEED · REF · MASK · UP TO 2048 UNVERIFIED · 77 TOKENS UNVERIFIED",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("SEED · REF · UP TO 2048 UNVERIFIED")).toBeInTheDocument();
  // turbo: no img2img graph, no region recipe — verified absences stay off
  // the row (the refine roster's reason line owns the one that gates)
  expect(screen.getByText("SEED · UP TO 2048 UNVERIFIED")).toBeInTheDocument();
  // a preset whose engine the matrix doesn't know is never guessed at
  expect(screen.getByText(NO_RECORD)).toBeInTheDocument();
  // every external row: the pin says no seed, the lane says it hasn't
  // observed that either
  expect(screen.getAllByText("NO SEED UNVERIFIED")).toHaveLength(2);
});

test("capability matrix unreachable: said once on the roster, and no row claims anything", async () => {
  stub(WS, IDLE_QUEUE, null);
  mount();
  await findTakes("SDXL raw");
  expect(await screen.findByText(MATRIX_UNREACHABLE)).toBeInTheDocument();
  expect(screen.queryByText(/UNVERIFIED/)).toBeNull();
  expect(screen.queryByText(NO_RECORD)).toBeNull();
  // the rows are still there, steppers live — the matrix gates nothing
  expect(takesOf("SDXL raw")).toBeEnabled();
});

test("a root arm can bring its own brief — seeded once, then the artist's", async () => {
  stub();
  const user = userEvent.setup();
  useUi.setState({ armed: { fx: 1, fy: 2, parents: [], prompt: "a brief carried in" } });
  mount();
  const prompt = await screen.findByLabelText("Prompt");
  expect(prompt).toHaveValue("a brief carried in");
  await user.type(prompt, ", sharpened");
  expect(prompt).toHaveValue("a brief carried in, sharpened");
  // the same arm re-rendering never clobbers what was typed
  useUi.setState({ armed: useUi.getState().armed });
  await waitFor(() => expect(prompt).toHaveValue("a brief carried in, sharpened"));
});
