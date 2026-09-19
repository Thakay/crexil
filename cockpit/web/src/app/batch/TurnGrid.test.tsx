// The turn surface under test: honest cell states, frozen geometry
// across status flips, cell verbs wired to the right routes, lightbox on a
// landed cell. Fetch is stubbed (no msw); the Playwright suite
// owns the live-batch law tests.
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Node, Slot, WorkspaceDetail } from "../../types";
import { useUi } from "../state/ui";
import { TurnGrid } from "./TurnGrid";

function slot(over: Partial<Slot> & { id: string; pos: number }): Slot {
  return {
    turn_id: "t1",
    engine: "z-image-turbo",
    status: "queued",
    attempt: 0,
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
    started_at: null,
    finished_at: null,
    created_at: "2026-08-11T00:00:00+00:00",
    ...over,
  };
}

const NODE: Node = {
  id: "n-landed",
  workspace_id: "w1",
  turn_id: "t1",
  slot_id: "s-landed",
  take: 1,
  payload: "image",
  verb: "run",
  prompt: null,
  engine: "z-image-turbo",
  seed: 7,
  duration_ms: 4200,
  committed_usd: null,
  charged_usd: null,
  set_aside: false,
  x: null,
  y: null,
  created_at: "2026-08-11T00:00:01+00:00",
  parents: [],
  turn_idx: 0,
  slot_pos: 2,
};

const ASIDE_NODE: Node = {
  ...NODE,
  id: "n-aside",
  slot_id: "s-aside",
  set_aside: true,
  slot_pos: 6,
};

// a landed PAID take: its reroll face carries the price
const PAID_NODE: Node = {
  ...NODE,
  id: "n-paid",
  slot_id: "s-paid",
  engine: "openai/gpt-image-1",
  committed_usd: 0.06,
  charged_usd: 0.06,
  slot_pos: 8,
};

// One slot per status — the whole vocabulary on one grid.
const EIGHT: Slot[] = [
  slot({ id: "s-q", pos: 0 }),
  slot({
    id: "s-run",
    pos: 1,
    status: "running",
    attempt: 1,
    started_at: "2026-08-11T00:00:00+00:00",
  }),
  slot({ id: "s-landed", pos: 2, status: "landed", attempt: 1 }),
  slot({
    id: "s-fail",
    pos: 3,
    status: "failed",
    attempt: 1,
    cause: "z-image-turbo failed partway through the graph — the engine, not your prompt",
    detail: 'error: execution error: [{"exception_type": "OOM"}] (HTTP 500)',
    started_at: "2026-08-11T00:00:00+00:00",
    finished_at: "2026-08-11T00:00:04+00:00",
  }),
  slot({ id: "s-held", pos: 4, status: "held" }),
  slot({ id: "s-skip", pos: 5, status: "skipped" }),
  slot({ id: "s-aside", pos: 6, status: "set_aside", attempt: 1 }),
  slot({
    id: "s-late",
    pos: 7,
    status: "late",
    attempt: 1,
    engine: "openai/gpt-image-1",
    committed_usd: 0.06,
  }),
];

function workspace(slots: Slot[]): WorkspaceDetail {
  return {
    id: "w1",
    name: "Sword",
    slug: "sword",
    viewport: {},
    dials: {},
    created_at: "2026-08-11T00:00:00+00:00",
    updated_at: "2026-08-11T00:00:00+00:00",
    charged_usd: 0,
    turns: [
      {
        id: "t1",
        workspace_id: "w1",
        idx: 0,
        prompt: "a rusty sword",
        dials: {},
        anchor_x: null,
        anchor_y: null,
        created_at: "2026-08-11T00:00:00+00:00",
        slots,
      },
    ],
  };
}

const QUEUE = {
  seq: 1,
  now: "2026-08-11T00:00:05+00:00",
  depth: 0,
  lanes: {
    local: { label: "LOCAL · SEQUENTIAL, ONE GPU", depth: 0, slots: [] },
    external: {
      label: "EXTERNAL · CONCURRENT, PRICED",
      concurrency: 4,
      active: 0,
      slots: [],
    },
  },
  // the held slot's bench, as the queue view reports it (SlotView.bench)
  held: [
    {
      id: "s-held",
      turn_id: "t1",
      turn_idx: 0,
      workspace_id: "w1",
      engine: "z-image-turbo",
      status: "held",
      attempt: 0,
      retry_of: null,
      position: null,
      elapsed_ms: null,
      committed_usd: null,
      charged_usd: null,
      cancel_requested: false,
      awaiting: false,
      bench: "engine:z-image-turbo",
      created_at: "2026-08-11T00:00:00+00:00",
    },
  ],
  late: [],
  benches: [{ scope: "engine:z-image-turbo", kind: "model_down", cause: "z-image-turbo is down" }],
  checks: [] as { slot_id: string; ask: number; of: number }[],
  retries: [],
  gpu: { reading: null, rechecking: false, manual_hold: false },
};

// POST bodies, in order (the verbs that re-spend carry the switch)
const bodies: unknown[] = [];

function stubApi(
  nodes: Node[],
  backfill: object = { rerolled: 1, refused: [] },
) {
  const posts: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = input.url;
      if (input.method === "POST") {
        const path = new URL(url).pathname;
        posts.push(path);
        bodies.push(await input.json().catch(() => null));
        if (path.endsWith("/backfill")) return Response.json(backfill);
        return Response.json({ ok: true });
      }
      if (url.includes("/nodes")) return Response.json(nodes);
      if (url.includes("/queue")) return Response.json(QUEUE);
      throw new Error(`unstubbed fetch: ${url}`);
    }),
  );
  return posts;
}

function mount(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  bodies.length = 0;
  useUi.setState({
    gridOpen: false,
    viewedTurnId: null,
    lightboxNodeId: null,
    localOnly: false,
    armWanted: null,
  });
});

test("a paid cell's reroll carries its price on the face (law 6); under LOCAL ONLY it is inert with the reason, and reroll + backfill carry the switch", async () => {
  const NINE: Slot[] = [
    ...EIGHT,
    slot({
      id: "s-paid",
      pos: 8,
      status: "landed",
      attempt: 1,
      engine: "openai/gpt-image-1",
      committed_usd: 0.06,
      charged_usd: 0.06,
    }),
  ];
  const posts = stubApi([NODE, ASIDE_NODE, PAID_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(NINE)} />);

  await screen.findByRole("button", { name: /Open take 0i full-res/ });
  // the paid face names its last quote as an estimate; free cells say none
  expect(screen.getByRole("button", { name: "REROLL R · ~$0.06" })).toBeEnabled();
  expect(screen.getAllByRole("button", { name: "REROLL R" })).toHaveLength(3);

  act(() => useUi.setState({ localOnly: true }));
  const gated = screen.getByRole("button", { name: "REROLL · LOCAL ONLY" });
  expect(gated).toBeDisabled();
  expect(gated).toHaveAttribute("title", expect.stringMatching(/LOCAL ONLY is on/));
  expect(screen.queryByRole("button", { name: "REROLL R · ~$0.06" })).not.toBeInTheDocument();
  // the free reroll and the backfill go out carrying the switch
  await user.click(screen.getAllByRole("button", { name: "REROLL R" })[0]);
  await user.click(screen.getByRole("button", { name: /BACKFILL 1 SET-ASIDE SLOT/ }));
  await waitFor(() =>
    expect(posts).toEqual([
      "/api/slots/s-landed/reroll",
      "/api/turns/t1/backfill",
    ]),
  );
  // reroll carries keep_seed too; backfill is the switch alone
  expect(bodies).toEqual([
    { local_only: true, keep_seed: false },
    { local_only: true },
  ]);

  act(() => useUi.setState({ localOnly: false }));
  expect(screen.getByRole("button", { name: "REROLL R · ~$0.06" })).toBeEnabled();
});

test("all eight slot states render their honest copy in pos order", async () => {
  stubApi([NODE, ASIDE_NODE]);
  mount(<TurnGrid workspace={workspace(EIGHT)} />);

  expect(screen.getByText("QUEUED · SLOT 1")).toBeInTheDocument();
  expect(await screen.findByText(/^RUNNING · /)).toBeInTheDocument();
  expect(
    await screen.findByRole("button", { name: /Open take 0c full-res/ }),
  ).toBeInTheDocument();
  expect(screen.getByText("SLOT FAILED")).toBeInTheDocument();
  expect(
    screen.getByText(/z-image-turbo failed partway through the graph/),
  ).toBeInTheDocument();
  // law 5: a local failure's line 2 is GPU seconds and takes, never money
  expect(screen.getByText("4s of GPU time spent · 2 takes still queued")).toBeInTheDocument();
  expect(screen.getByText("HELD")).toBeInTheDocument();
  expect(screen.getByText("SKIPPED")).toBeInTheDocument();
  expect(screen.getByText("SET ASIDE")).toBeInTheDocument();
  expect(screen.getByText("Slot stays. Nothing moved.")).toBeInTheDocument();
  expect(screen.getByText("LATE · ALREADY PAID")).toBeInTheDocument();
  expect(screen.getByText("$0.06")).toBeInTheDocument(); // money badge
  // the one-GPU sentence is in the header readout (law 13)
  expect(screen.getByText(/ONE GPU, LOCALS TAKE TURNS/)).toBeInTheDocument();
  // pos order is DOM order — geometry comes from pos, not status
  const grid = screen.getByRole("grid");
  const order = [...grid.querySelectorAll("[data-slot-pos]")].map((el) =>
    el.getAttribute("data-slot-pos"),
  );
  expect(order).toEqual(["0", "1", "2", "3", "4", "5", "6", "7"]);
});

test("a status flip changes a cell in place — never the geometry (law 8)", async () => {
  stubApi([NODE, ASIDE_NODE]);
  const view = mount(<TurnGrid workspace={workspace(EIGHT)} />);
  const before = [
    ...screen.getByRole("grid").querySelectorAll("[data-slot-pos]"),
  ].map((el) => el.getAttribute("data-slot-pos"));

  // the queued slot lands: same footprint, new face
  const flipped = EIGHT.map((s) =>
    s.id === "s-q" ? { ...s, status: "landed" as const, attempt: 1 } : s,
  );
  view.rerender(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <TurnGrid workspace={workspace(flipped)} />
    </QueryClientProvider>,
  );
  const after = [
    ...screen.getByRole("grid").querySelectorAll("[data-slot-pos]"),
  ].map((el) => el.getAttribute("data-slot-pos"));
  expect(after).toEqual(before);
  expect(screen.queryByText("QUEUED · SLOT 1")).not.toBeInTheDocument();
});

test("cell verbs hit their routes: reroll, set aside, restore, cancel", async () => {
  const posts = stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);

  await screen.findByRole("button", { name: /Open take 0c full-res/ });
  await user.click(screen.getAllByRole("button", { name: "REROLL R" })[0]);
  await user.click(screen.getByRole("button", { name: "SET ASIDE ⌫" }));
  await user.click(screen.getByRole("button", { name: "RESTORE ⌘Z" }));
  await user.click(screen.getByRole("button", { name: "Cancel slot 1" }));
  expect(posts).toEqual([
    "/api/slots/s-landed/reroll",
    "/api/nodes/n-landed/set-aside",
    "/api/nodes/n-aside/restore",
    "/api/slots/s-q/cancel",
  ]);
});

test("an active turn offers cancel with the one-breath copy; the stop is two presses (C6) and KEEP GOING sends nothing", async () => {
  const posts = stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  expect(
    screen.getByText(/queued drop free/),
  ).toBeInTheDocument();
  // first press: armed, said in the footer with the numbers, nothing sent
  await user.click(screen.getByRole("button", { name: "CANCEL TURN" }));
  expect(posts).not.toContain("/api/turns/t1/cancel");
  const notices = screen.getByRole("list", { name: "Turn notices" });
  // EIGHT: one landed (slot 3), a queued + a held take ahead, a running
  // local; the late paid take is already resting — not "in flight"
  expect(notices).toHaveTextContent("Stopping now. Slot 3 is yours and stays on the canvas.");
  expect(notices).not.toHaveTextContent(/paid at/);
  expect(notices).toHaveTextContent(/The 2 takes ahead cost nothing to drop\. The running z-image-turbo take is abandoned — \d+s of GPU time lost\./);
  // KEEP GOING stands it down — the breath goes, the first button is back
  await user.click(screen.getByRole("button", { name: "KEEP GOING · ESC" }));
  expect(screen.queryByRole("list", { name: "Turn notices" })).toBeNull();
  expect(posts).not.toContain("/api/turns/t1/cancel");
  // second press stops
  await user.click(screen.getByRole("button", { name: "CANCEL TURN" }));
  await user.click(screen.getByRole("button", { name: "STOP THE QUEUE" }));
  expect(posts).toContain("/api/turns/t1/cancel");
});

test("A5a: a blank cell's RUN AGAIN · SAME SEED rerolls with keep_seed; the plain reroll doesn't", async () => {
  const posts = stubApi([]);
  const user = userEvent.setup();
  const blank = slot({
    id: "s-blank",
    pos: 0,
    status: "failed",
    attempt: 1,
    kind: "blank",
    cause: "z-image-turbo finished, but the frame came back empty — that's the engine, not your prompt",
    detail: "the empty frame is kept at /x/blank.png",
    started_at: "2026-08-11T00:00:00+00:00",
    finished_at: "2026-08-11T00:00:04+00:00",
  });
  mount(<TurnGrid workspace={workspace([blank])} />);
  expect(screen.getByText(/not your prompt/)).toBeInTheDocument();
  const before = bodies.length;
  await user.click(screen.getByRole("button", { name: "RUN AGAIN · SAME SEED" }));
  await user.click(screen.getByRole("button", { name: "REROLL · NEW SEED" }));
  expect(posts.slice(-2)).toEqual([
    "/api/slots/s-blank/reroll",
    "/api/slots/s-blank/reroll",
  ]);
  expect(bodies.slice(before)).toEqual([
    { local_only: false, keep_seed: true },
    { local_only: false, keep_seed: false },
  ]);
});

test("B4: a charged-but-blank cell says the bill is being asked, offers the receipt first, and keeps it selectable behind DETAILS", async () => {
  const posts = stubApi([]);
  const user = userEvent.setup();
  const b4 = slot({
    id: "s-b4",
    pos: 0,
    engine: "openai/gpt-image-1",
    status: "failed",
    attempt: 1,
    kind: "charged_blank",
    job_ref: "gen-abc",
    committed_usd: 0.06,
    charged_usd: 0.06,
    seed: null,
    finished_at: "2026-08-11T00:00:09+00:00",
    cause: "openai/gpt-image-1 took the job and billed it, but no image came back",
    detail: "charged_blank: billed 0.06 USD but the response carried no image",
  });
  QUEUE.checks = [{ slot_id: "s-b4", ask: 1, of: 3 }];
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  try {
    mount(<TurnGrid workspace={workspace([b4])} />);
    expect(screen.getByText("$0.06 was charged")).toBeInTheDocument();
    await screen.findByText("ASKING OPENAI/GPT-IMAGE-1 ABOUT THE BILL · 1 OF 3");
    const buttons = screen.getAllByRole("button").filter((b) => b.dataset.remedy);
    expect(buttons.map((b) => b.textContent)).toEqual([
      "COPY RECEIPT",
      "RUN LOCALLY · FREE",
      "REROLL R · ~$0.06",
    ]);
    expect(buttons[2].dataset.paid).toBe("1");
    const pre = document.querySelector("pre[data-receipt]");
    expect(pre?.textContent).toContain("job id    gen-abc");
    expect(pre?.textContent).toContain("prompt    a rusty sword");
    await user.click(buttons[0]);
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("job id    gen-abc"));
    await screen.findByText("COPIED");
    expect(posts).toEqual([]); // the receipt is a read; nothing was sent
  } finally {
    QUEUE.checks = [];
  }
});

test("clicking a landed cell opens the lightbox — cells stay thumbs", async () => {
  stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  await screen.findByText("0c · 4S"); // node data landed → thumb in place
  const open = screen.getByRole("button", {
    name: /Open take 0c full-res/,
  });
  const img = open.querySelector("img");
  expect(img).not.toBeNull();
  expect(img!.src).toContain("/api/nodes/n-landed/thumb");
  await user.click(open);
  expect(useUi.getState().lightboxNodeId).toBe("n-landed");
});

test("keystroke parity: R rerolls the hovered landed cell", async () => {
  const posts = stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  const open = await screen.findByRole("button", {
    name: /Open take 0c full-res/,
  });
  await user.hover(open);
  await user.keyboard("r");
  expect(posts).toEqual(["/api/slots/s-landed/reroll"]);
});

test("backfill re-arms the set-aside slots in one action, and B fires it too", async () => {
  const posts = stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  const btn = screen.getByRole("button", {
    name: "BACKFILL 1 SET-ASIDE SLOT · B",
  });
  expect(
    screen.getByText(/a batch of 8 stays a batch of 8/),
  ).toBeInTheDocument();
  await user.click(btn);
  await waitFor(() => expect(posts).toEqual(["/api/turns/t1/backfill"]));
  await user.keyboard("b");
  await waitFor(() => expect(posts).toHaveLength(2));
  // no set-aside slot → no button, no key
  cleanup();
  stubApi([NODE]);
  mount(
    <TurnGrid
      workspace={workspace(EIGHT.filter((sl) => sl.status !== "set_aside"))}
    />,
  );
  expect(screen.queryByRole("button", { name: /BACKFILL/ })).toBeNull();
});

test("backfill refusals are per slot, in the server's words, and stop nothing (law 1)", async () => {
  stubApi([NODE, ASIDE_NODE], {
    rerolled: 0,
    refused: [
      {
        slot_id: "s-aside",
        pos: 6,
        reason:
          "preset 'z-image-turbo' is no longer configured — the old take stays and nothing was spent; restore the preset to reroll",
      },
    ],
  });
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  await user.click(screen.getByRole("button", { name: /BACKFILL 1/ }));
  expect(
    await screen.findByText(/slot 7 stays set aside: preset 'z-image-turbo'/),
  ).toBeInTheDocument();
});

// ── the laws on the face ────────────────────────────────────────────────────

test("law 3: the face is the plain cause; the raw engine text sits behind a collapsed DETAILS", async () => {
  stubApi([NODE, ASIDE_NODE]);
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  const cell = screen.getByRole("grid").querySelector('[data-slot-pos="3"]')!;
  const details = cell.querySelector("details")!;
  expect(details).not.toBeNull();
  expect(details.open).toBe(false);
  expect(details.querySelector("summary")).toHaveTextContent("DETAILS");
  expect(details.querySelector("pre")).toHaveTextContent("(HTTP 500)");
  // nothing outside the details carries the code
  const face = cell.cloneNode(true) as HTMLElement;
  face.querySelector("details")!.remove();
  expect(face.textContent).not.toMatch(/HTTP|500|\{/);
  // a failure without detail has no affordance to open
  const bare = screen.getByRole("grid").querySelector('[data-slot-pos="5"]')!;
  expect(bare.querySelector("details")).toBeNull();
});

test("laws 4 + 6: a failed paid cell offers RUN LOCALLY free and first, the paid reroll last with its price and unframed; RUN LOCALLY arms the composer with the brief", async () => {
  const PAID_FAIL: Slot[] = [
    slot({
      id: "s-pf",
      pos: 0,
      status: "failed",
      attempt: 1,
      engine: "openai/gpt-image-1",
      committed_usd: 0.06,
      cause: "openai/gpt-image-1 wouldn't run this prompt — their filter, not a fault at your end",
    }),
  ];
  stubApi([]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(PAID_FAIL)} />);
  const cell = screen.getByRole("grid").querySelector('[data-slot-pos="0"]')!;
  const buttons = [...cell.querySelectorAll("button")].map((b) => b.textContent);
  expect(buttons).toEqual(["RUN LOCALLY · FREE", "REROLL R · ~$0.06"]);
  const free = screen.getByRole("button", { name: "RUN LOCALLY · FREE" });
  const paid = screen.getByRole("button", { name: "REROLL R · ~$0.06" });
  expect(free.className).toMatch(/actFramed/);
  expect(paid.className).not.toMatch(/actFramed/);
  expect(paid).toHaveAttribute("data-paid", "1");
  // law 5 on a paid failure: money is named
  expect(screen.getByText("nothing was charged")).toBeInTheDocument();
  await user.click(free);
  expect(useUi.getState().armWanted).toEqual({
    parents: [],
    sweep: null,
    prompt: "a rusty sword",
  });
});

test("laws 2 + 4: a held cell carries its own resume — UNBENCH posts the queue's bench scope", async () => {
  const posts = stubApi([NODE, ASIDE_NODE]);
  const user = userEvent.setup();
  mount(<TurnGrid workspace={workspace(EIGHT)} />);
  // the bench scope arrives with the queue poll; the button is there before
  const btn = screen.getByRole("button", { name: "UNBENCH · RESUME" });
  await waitFor(() =>
    expect(
      screen.getByText("waiting costs nothing — resumes on unbench"),
    ).toBeInTheDocument(),
  );
  await user.click(btn);
  await waitFor(() => expect(posts).toContain("/api/unbench"));
  expect(bodies).toContainEqual({ scope: "engine:z-image-turbo" });
});

test("law 9: a retrying slot says so while queued and while running", async () => {
  const RETRY: Slot[] = [
    slot({
      id: "s-rq",
      pos: 0,
      status: "queued",
      attempt: 1,
      retry_of: "rate_limit",
      engine: "openai/gpt-image-1",
      committed_usd: 0.06,
    }),
    slot({
      id: "s-rr",
      pos: 1,
      status: "running",
      attempt: 2,
      retry_of: "unreachable",
      started_at: "2026-08-11T00:00:00+00:00",
    }),
  ];
  stubApi([]);
  mount(<TurnGrid workspace={workspace(RETRY)} />);
  expect(screen.getByText("RETRYING · ATTEMPT 2")).toBeInTheDocument();
  expect(screen.getByText(/rate limited — trying once more/)).toBeInTheDocument();
  expect(await screen.findByText(/^RETRYING · ATTEMPT 2 · /)).toBeInTheDocument();
  expect(screen.getByText(/couldn't be reached — trying once more/)).toBeInTheDocument();
  expect(screen.queryByText(/^RUNNING · /)).not.toBeInTheDocument();
});

test("law 1: several cells dead of one cause are said once in the header; each keeps its face", async () => {
  const cause = "OpenRouter can't be reached — the network, most likely";
  const THREE: Slot[] = [
    slot({ id: "a", pos: 0, status: "failed", attempt: 1, engine: "openai/gpt-image-1", cause }),
    slot({ id: "b", pos: 1, status: "failed", attempt: 1, engine: "openai/gpt-image-1", cause }),
    slot({ id: "c", pos: 2, status: "failed", attempt: 1, engine: "openai/gpt-image-1", cause }),
    slot({ id: "d", pos: 3, status: "failed", attempt: 1, cause: "z-image-turbo went silent — no result came back in time", started_at: "2026-08-11T00:00:00+00:00" }),
  ];
  stubApi([]);
  mount(<TurnGrid workspace={workspace(THREE)} />);
  const notices = screen.getByRole("list", { name: "Turn notices" });
  expect(notices).toHaveTextContent(`slots 1 · 2 · 3 failed for one reason — ${cause}`);
  expect(notices.querySelectorAll("li")).toHaveLength(1); // the lone cause isn't folded
  expect(screen.getAllByText(cause)).toHaveLength(3); // the three faces, whole
});

// The turn's record rides the header (the words once above
// the cells, the recipe as the turn's) and P recalls the words.
test("the header carries the turn's words and recipe, and P arms a root run with the words", async () => {
  stubApi([NODE]);
  const user = userEvent.setup();
  const ws = workspace(EIGHT);
  ws.turns[0].dials = {
    seed: { mode: "random" },
    engines: { "z-image-turbo": { steps: 9, cfg: 1, width: 1024, height: 1024, sampler_name: "euler" } },
  };
  mount(<TurnGrid workspace={ws} />);
  await screen.findByRole("button", { name: /Open take 0c full-res/ });
  const record = screen.getByRole("group", { name: "Provenance" });
  expect(record).toHaveAttribute("data-scope", "turn");
  expect(screen.getByText("a rusty sword")).toBeInTheDocument();
  expect(screen.getByText("1024×1024 · STEPS 9 · CFG 1 · SAMPLER EULER")).toBeInTheDocument();
  expect(screen.getByText("SEEDS ROLLED PER TAKE · FROM TEXT · A ROOT")).toBeInTheDocument();
  expect(screen.getByText("THIS TURN'S DIALS, AS SENT")).toBeInTheDocument();
  // turn rows only: the take's facts belong to the cell and the head
  expect(screen.queryByText("THIS TAKE")).not.toBeInTheDocument();
  await user.keyboard("p");
  expect(useUi.getState().armWanted).toEqual({ parents: [], sweep: null, prompt: "a rusty sword" });
  useUi.setState({ armWanted: null });
  await user.click(screen.getByRole("button", { name: "USE THIS PROMPT · P" }));
  expect(useUi.getState().armWanted).toEqual({ parents: [], sweep: null, prompt: "a rusty sword" });
});
