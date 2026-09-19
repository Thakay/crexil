// Fault injection reproduces the six trust-critical cases and asserts
// their treatments against the REAL stack (server, scheduler, store,
// events poll, grid, composer) with only the engines scripted
// (app/harness.py, COCKPIT_HARNESS_EXECUTORS).
//
//   A5a  blank local output        here — a black plate through the real
//                                  detector; the face absolves the prompt;
//                                  RUN AGAIN · SAME SEED keeps the seed
//   B4   charged, nothing returned here — the kind, the dollars, the bill
//                                  re-asked three times visibly (never a
//                                  re-generate), the receipt first and the
//                                  paid retry last, never the default
//   C6   cancel mid-batch          here — two presses, the three acts said
//                                  with real numbers before the stop lands
//   E5   prompt over token limit   here — warned per engine before Run,
//                                  never refused; the server keeps it whole
//   D5   broken promote target     cockpit/server/tests/test_promote.py, and
//                                  e2e/promote.spec.ts on the live face
//   F5   drag released over nothing e2e/drag.spec.ts ("released over
//                                  nothing is a clean no-op")
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Slot = { id: string; pos: number; engine: string; status: string; seed: number | null };
type Turn = { id: string; idx: number; slots: Slot[] };
type Box = { x: number; y: number; width: number; height: number };

const T = { timeout: 10_000 };
const EXT_MODEL = "openai/gpt-image-2"; // providers.json pin: $0.04, enabled

async function createWorkspace(request: APIRequestContext, name: string): Promise<string> {
  const r = await request.post("/api/workspaces", { data: { name } });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { id: string }).id;
}

async function runTurn(request: APIRequestContext, wid: string, body: object): Promise<Turn> {
  const r = await request.post(`/api/workspaces/${wid}/turns`, { data: body });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { turn: Turn }).turn;
}

async function resolve(request: APIRequestContext, slotId: string, directive: object = { outcome: "land" }): Promise<void> {
  const r = await request.post(`/api/harness/slots/${slotId}/resolve`, { data: directive });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function slotOf(request: APIRequestContext, wid: string, slotId: string): Promise<Slot & { kind: string | null; job_ref: string | null; charged_usd: number | null }> {
  const d = (await (await request.get(`/api/workspaces/${wid}`)).json()) as {
    turns: { slots: (Slot & { kind: string | null; job_ref: string | null; charged_usd: number | null })[] }[];
  };
  for (const t of d.turns) for (const s of t.slots) if (s.id === slotId) return s;
  throw new Error(`no slot ${slotId}`);
}

// The harness ledger accumulates across runs and the route pages at 500:
// read this workspace's events only, cursor by cursor, to the end.
async function events(request: APIRequestContext, wid: string, type: string, slotId: string): Promise<Record<string, unknown>[]> {
  const out: Record<string, unknown>[] = [];
  let since = 0;
  for (;;) {
    const r = (await (await request.get(`/api/events?since=${since}&workspace_id=${wid}&limit=500`)).json()) as {
      events: { seq: number; type: string; slot_id: string | null; payload: Record<string, unknown> }[];
      seq: number;
    };
    if (r.events.length === 0) return out;
    for (const e of r.events) if (e.type === type && e.slot_id === slotId) out.push(e.payload);
    since = r.seq;
  }
}

async function nodeCount(request: APIRequestContext, wid: string): Promise<number> {
  const r = await request.get(`/api/workspaces/${wid}/nodes`);
  return ((await r.json()) as unknown[]).length;
}

const cell = (page: Page, pos: number) => page.locator(`[data-slot-pos="${pos}"]`);

async function expectStatus(page: Page, pos: number, status: string | RegExp): Promise<void> {
  await expect(cell(page, pos)).toHaveAttribute("data-status", status, T);
}

async function cellBoxes(page: Page, n: number): Promise<Box[]> {
  const boxes: Box[] = [];
  for (let pos = 0; pos < n; pos++) {
    // The panes' own scroll is added back: on a short window a click into a
    // cell scrolls the grid pane, and scrolling is not movement.
    const b = await cell(page, pos).evaluate((el) => {
      let x = 0;
      let y = 0;
      for (let s = el.parentElement; s; s = s.parentElement) {
        x += s.scrollLeft;
        y += s.scrollTop;
      }
      const r = el.getBoundingClientRect();
      return { x: r.x + x, y: r.y + y, width: r.width, height: r.height };
    });
    expect(b.width > 0 && b.height > 0, `cell ${pos} must be visible`).toBe(true);
    boxes.push(b);
  }
  return boxes;
}

function expectFrozen(before: Box[], after: Box[]): void {
  expect(after.length).toBe(before.length);
  for (let i = 0; i < before.length; i++)
    for (const k of ["x", "y", "width", "height"] as const)
      expect(Math.abs(after[i][k] - before[i][k]), `cell ${i} ${k} moved`).toBeLessThan(1);
}

// The local lane is serial and process-global (one GPU): drain to
// quiescence after every test so a later spec's takes never starve. A bill
// check in flight is waited out (three asks at the harness gap), never cut.
test.afterEach(async ({ request }) => {
  for (let i = 0; i < 80; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as { waiting: string[] };
    for (const sid of st.waiting) await resolve(request, sid, { outcome: "land" });
    const q = (await (await request.get("/api/queue")).json()) as {
      benches: { scope: string }[];
      held: { id: string }[];
      late: { id: string }[];
      checks: { slot_id: string }[];
      lanes: { local: { slots: { id: string; status: string }[] }; external: { slots: { id: string; status: string }[] } };
    };
    for (const b of q.benches) await request.post("/api/unbench", { data: { scope: b.scope } });
    const parked = [
      ...q.held,
      ...q.lanes.local.slots.filter((s) => s.status === "queued"),
      ...q.lanes.external.slots.filter((s) => s.status === "queued"),
    ];
    for (const s of parked) await request.post(`/api/slots/${s.id}/cancel`);
    const busy =
      st.waiting.length + q.benches.length + q.held.length + q.late.length + q.checks.length +
      q.lanes.local.slots.length + q.lanes.external.slots.length;
    if (busy === 0) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("harness did not drain — later tests would starve");
});

test("A5a: a blank frame fails red with the prompt absolved, the bytes kept, and RUN AGAIN · SAME SEED keeps the seed", async ({ page, request }) => {
  const wid = await createWorkspace(request, `trust-a5a-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "trust A5a",
    local: [{ preset_id: "raw-zimage", takes: 2 }],
  });
  const [s0, s1] = turn.slots;
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(2, T);
  const before = await cellBoxes(page, 2);

  // the engine "succeeds" with a black plate — the REAL detector fails it
  await resolve(request, s0.id, { outcome: "land", color: [0, 0, 0] });
  await expectStatus(page, 0, "failed");
  await expect(cell(page, 0)).toContainText("raw-zimage finished, but the frame came back empty — that's the engine, not your prompt");
  await expect(cell(page, 0)).toContainText("of GPU time spent");
  await expect(cell(page, 0)).not.toContainText("charged");
  // the empty frame is kept and named (law 7), behind DETAILS (law 3)
  await expect(cell(page, 0).locator("details pre")).toHaveText(/every pixel of the 64×64 frame is black/);
  await expect(cell(page, 0).locator("details pre")).toHaveText(/the empty frame is kept at .*-blank\.png/);
  // the next move: same seed first, both free; nothing paid, nothing framed paid
  const buttons = cell(page, 0).locator("button[data-remedy]");
  await expect(buttons).toHaveText(["RUN AGAIN · SAME SEED", "REROLL · NEW SEED"]);
  await expect(buttons.first()).not.toHaveAttribute("data-paid", "1");
  expect(await buttons.first().evaluate((b) => b.className)).toMatch(/actFramed/);
  // nothing else was touched: no bench, the lane moved on to slot 2
  await expectStatus(page, 1, /queued|running/);
  const failed = await slotOf(request, wid, s0.id);
  expect(failed.kind).toBe("blank");
  const seed = failed.seed;
  expect(typeof seed).toBe("number");
  expect(await nodeCount(request, wid)).toBe(0);

  // the remedy: same seed, attempt 2, and this time it lands
  await cell(page, 0).getByRole("button", { name: "RUN AGAIN · SAME SEED" }).click();
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, s1.id, { outcome: "land" });
  await resolve(request, s0.id, { outcome: "land", color: [40, 80, 120] });
  await expectStatus(page, 0, "landed");
  const again = await slotOf(request, wid, s0.id);
  expect(again.seed).toBe(seed);
  expect(again.kind).toBeNull();
  await expect(cell(page, 0)).not.toContainText("empty");
  expectFrozen(before, await cellBoxes(page, 2));
});

test("B4: charged but nothing returned — the amount on line 2, the bill re-asked three times visibly, the receipt first and the paid retry last", async ({ page, request }) => {
  const wid = await createWorkspace(request, `trust-b4-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "trust B4 brief",
    local: [{ preset_id: "raw-flux2", takes: 1 }],
    external: [{ model_id: EXT_MODEL, takes: 2 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  const paid = byPos.get(1)!;
  const silent = byPos.get(2)!;
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(3, T);
  const before = await cellBoxes(page, 3);

  // the provider bills and returns nothing; its record answers on ask 2
  // with a different amount — the ledger follows the record, once
  await resolve(request, paid.id, {
    outcome: "fail",
    kind: "charged_blank",
    message: "billed 0.04 USD but the response carried no image",
    charged_usd: 0.04,
    job_id: "gen-trust-b4",
    bill: [null, null, 0.05], // answered on the third ask — the check stays watchable
  });
  await expectStatus(page, 1, "failed");
  await expect(cell(page, 1)).toContainText("openai/gpt-image-2 took the job and billed it, but no image came back");
  await expect(cell(page, 1)).toContainText("$0.04 was charged");
  // the one automatic action, visible while it runs (law 9) — a re-ask,
  // never a re-generate: the executor is asked for nothing new
  await expect(cell(page, 1).locator("[data-bill-check]")).toContainText(/ASKING OPENAI\/GPT-IMAGE-2 ABOUT THE BILL · [123] OF 3/, T);
  const st = (await (await request.get("/api/harness/state")).json()) as { waiting: string[] };
  expect(st.waiting).not.toContain(paid.id);
  // the buttons: receipt first (framed), free refill, paid last with its
  // price and never framed (law 6)
  const buttons = cell(page, 1).locator("button[data-remedy]");
  await expect(buttons).toHaveText(["COPY RECEIPT", "RUN LOCALLY · FREE", "REROLL R · ~$0.04"]);
  await expect(buttons.last()).toHaveAttribute("data-paid", "1");
  expect(await buttons.first().evaluate((b) => b.className)).toMatch(/actFramed/);
  expect(await buttons.last().evaluate((b) => b.className)).not.toMatch(/actFramed/);
  // the receipt: time · provider · job id · amount · prompt · seed, pasteable
  const receipt = cell(page, 1).locator("pre[data-receipt]");
  await expect(receipt).toHaveText(/CHARGED, NOTHING RETURNED — receipt/);
  await expect(receipt).toHaveText(/provider  openai\/gpt-image-2 via OpenRouter/);
  await expect(receipt).toHaveText(/job id    gen-trust-b4/);
  await expect(receipt).toHaveText(/amount    \$0\.04 charged — the response's figure/);
  await expect(receipt).toHaveText(/prompt    trust B4 brief/);
  await expect(receipt).toHaveText(/seed      (\d+|none sent)/); // the pin decides whether a seed went out
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await buttons.first().click();
  await expect(buttons.first()).toHaveText("COPIED");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("job id    gen-trust-b4");
  // the record: three asks, then the provider's word corrected the ledger
  await expect(cell(page, 1).locator("[data-bill-check]")).toHaveCount(0, { timeout: 20_000 });
  await expect.poll(() => events(request, wid, "slot.charge_verified", paid.id), T).toHaveLength(1);
  expect(await events(request, wid, "slot.bill_check", paid.id)).toHaveLength(3);
  const row = await slotOf(request, wid, paid.id);
  expect(row.kind).toBe("charged_blank");
  expect(row.job_ref).toBe("gen-trust-b4");
  expect(row.charged_usd).toBe(0.05);
  await expect(cell(page, 1)).toContainText("$0.05 was charged", T);

  // the other paid take: billed, no image, and the record never answers —
  // three asks, then the amount stays as reported (charge_unverified)
  await resolve(request, silent.id, {
    outcome: "fail",
    kind: "charged_blank",
    message: "billed, no image",
    charged_usd: 0.04,
    job_id: "gen-trust-silent",
    bill: [],
  });
  await expectStatus(page, 2, "failed");
  await expect.poll(() => events(request, wid, "slot.charge_unverified", silent.id), { timeout: 25_000 }).toHaveLength(1);
  expect((await events(request, wid, "slot.charge_unverified", silent.id))[0]).toMatchObject({ reason: "bill_check", asks: 3 });
  expect(await events(request, wid, "slot.bill_check", silent.id)).toHaveLength(3);
  expect((await slotOf(request, wid, silent.id)).charged_usd).toBe(0.04);
  // one cause, one line (law 1) — and the local slot never heard of it
  await expect(page.getByRole("list", { name: "Turn notices" })).toContainText("slots 2 · 3 failed for one reason");
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, byPos.get(0)!.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  expectFrozen(before, await cellBoxes(page, 3));
});

test("C6: cancel mid-batch is two presses — the three acts said with real numbers first, then landed stay, queued drop free, the running local is abandoned, the paid ride lands late into its cell", async ({ page, request }) => {
  const wid = await createWorkspace(request, `trust-c6-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "trust C6",
    local: [{ preset_id: "raw-flux2", takes: 2 }],
    external: [{ model_id: EXT_MODEL, takes: 2 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(4, T);
  const before = await cellBoxes(page, 4);
  // one paid take lands; the other rides on; a local runs; a local waits
  await resolve(request, byPos.get(2)!.id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 2, "landed");
  await expectStatus(page, 0, "running");
  await expectStatus(page, 1, "queued");
  await expectStatus(page, 3, "running");

  // first press: nothing stops; the breath is in the footer with the numbers
  await page.getByRole("button", { name: "CANCEL TURN" }).click();
  const notices = page.getByRole("list", { name: "Turn notices" });
  await expect(notices.locator("[data-stop-line]")).toHaveText([
    "Stopping now. Slot 3 is yours and stays on the canvas.",
    "openai/gpt-image-2's 1 take is already paid at ~$0.04 and will land anyway.",
    /The 1 take ahead costs nothing to drop\. The running raw-flux2 take is abandoned — .* of GPU time lost\./,
  ]);
  await expect(page.getByRole("button", { name: "STOP THE QUEUE" })).toBeVisible();
  await expectStatus(page, 0, "running");
  await expectStatus(page, 1, "queued");
  expectFrozen(before, await cellBoxes(page, 4));
  // KEEP GOING (Esc) stands it down — nothing happened
  await page.keyboard.press("Escape");
  await expect(notices.locator("[data-stop-line]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "CANCEL TURN" })).toBeVisible();
  await expectStatus(page, 0, "running");

  // the stop: landed stays, queued drops free, running local abandoned
  // with its seconds, the paid ride rests late with its amount
  await page.getByRole("button", { name: "CANCEL TURN" }).click();
  await page.getByRole("button", { name: "STOP THE QUEUE" }).click();
  await expectStatus(page, 2, "landed");
  await expectStatus(page, 1, "skipped");
  await expect(cell(page, 1)).toContainText("no GPU time spent");
  await expectStatus(page, 0, "failed");
  await expect(cell(page, 0)).toContainText("cancelled");
  await expect(cell(page, 0)).toContainText("of GPU time spent");
  await expectStatus(page, 3, "late");
  await expect(cell(page, 3)).toContainText("LATE · ALREADY PAID");
  await expect(cell(page, 3)).toContainText("~$0.04");
  expect(await nodeCount(request, wid)).toBe(1);
  // the ride delivers after all: into its own cell (laws 7 + 8)
  await resolve(request, byPos.get(3)!.id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 3, "landed");
  await expect(cell(page, 3)).toContainText("$0.04");
  expect(await nodeCount(request, wid)).toBe(2);
  expectFrozen(before, await cellBoxes(page, 4));
});

test("E5: a prompt past an engine's token window is warned per engine before Run — as ABOUT, with the window's state — and Run stays armed; the server keeps the prompt whole", async ({ page, request }) => {
  const wid = await createWorkspace(request, `trust-e5-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(page.getByText("Press N — or right-click — to summon")).toBeVisible(T);
  await page.locator(".react-flow").click({ button: "right", position: { x: 300, y: 200 } });
  await page.getByText("Image · new root").click();
  const prompt = page.getByLabel("Prompt", { exact: true });
  const long = Array.from({ length: 70 }, (_, i) => `token${i}`).join(", ");
  await prompt.fill(long);
  // the default row (Prop Sheet, klein: no pinned window) says nothing
  await expect(page.locator("[data-token-warning]")).toHaveCount(0);
  await page.getByRole("button", { name: "Raw · SDXL Juggernaut takes +" }).click();
  const warn = page.locator('[data-token-warning="Raw · SDXL Juggernaut"]');
  await expect(warn).toHaveText(/1 OF YOUR 2 SLOTS — RAW · SDXL JUGGERNAUT — READ ABOUT \d+ TOKENS AGAINST A 77-TOKEN WINDOW \(UNVERIFIED\)/);
  await expect(warn).toContainText("NOTHING IS CUT HERE");
  const run = page.getByRole("button", { name: /^Run · 2/ });
  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.locator("[data-slot-pos]")).toHaveCount(2, T);
  // never truncated: the turn carries the whole prompt
  const d = (await (await request.get(`/api/workspaces/${wid}`)).json()) as {
    turns: { prompt: string; slots: Slot[] }[];
  };
  expect(d.turns[0].prompt).toBe(long);
  for (const s of d.turns[0].slots) await resolve(request, s.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  await expectStatus(page, 1, "landed");
});
