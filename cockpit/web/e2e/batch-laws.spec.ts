// The batch laws asserted against the REAL stack
// (server, scheduler, store, events poll, grid) with only the engines
// scripted. The webServer runs COCKPIT_HARNESS_EXECUTORS=1: every take
// blocks until a directive arrives via /api/harness (app/harness.py), so
// specs land/fail slots on command while the batch is genuinely mid-flight.
// Directives are per-slot FIFOs — pre-posting is race-free, a reroll's
// attempt 2 consumes the next one. UI truth rides the 2s polls, so law
// assertions use auto-retrying matchers with a 10s ceiling.
// The suite also covers the laws the cell face holds: the plain cause
// with the raw text behind DETAILS (3), GPU seconds for a local failure
// and dollars only on a paid one (5), free remedy first and the paid one
// last with its price (6), a retry visible while it runs (9), one line for
// one cause (1), and the held cell's own resume (2 + 4).
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Slot = { id: string; pos: number; engine: string; status: string };
type Turn = { id: string; idx: number; slots: Slot[] };
type Box = { x: number; y: number; width: number; height: number };

const T = { timeout: 10_000 };
const EXT_MODEL = "openai/gpt-image-2"; // providers.json pin: $0.04, enabled

async function createWorkspace(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const r = await request.post("/api/workspaces", { data: { name } });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { id: string }).id;
}

async function runTurn(
  request: APIRequestContext,
  wid: string,
  body: object,
): Promise<Turn> {
  const r = await request.post(`/api/workspaces/${wid}/turns`, {
    data: body,
  });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { turn: Turn }).turn;
}

async function resolve(
  request: APIRequestContext,
  slotId: string,
  directive: object = { outcome: "land" },
): Promise<void> {
  const r = await request.post(`/api/harness/slots/${slotId}/resolve`, {
    data: directive,
  });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function nodeCount(
  request: APIRequestContext,
  wid: string,
): Promise<number> {
  const r = await request.get(`/api/workspaces/${wid}/nodes`);
  return ((await r.json()) as unknown[]).length;
}

const cell = (page: Page, pos: number) =>
  page.locator(`[data-slot-pos="${pos}"]`);

async function expectStatus(
  page: Page,
  pos: number,
  status: string | RegExp,
): Promise<void> {
  await expect(cell(page, pos)).toHaveAttribute("data-status", status, T);
}

// Law 8's rendered form: capture every cell's box once the grid is whole,
// re-capture after each state flip — nothing may move or resize, ever.
async function cellBoxes(page: Page, n: number): Promise<Box[]> {
  const boxes: Box[] = [];
  for (let pos = 0; pos < n; pos++) {
    const b = await cell(page, pos).boundingBox();
    expect(b, `cell ${pos} must be visible`).not.toBeNull();
    boxes.push(b!);
  }
  return boxes;
}

function expectFrozen(before: Box[], after: Box[]): void {
  expect(after.length).toBe(before.length);
  for (let i = 0; i < before.length; i++) {
    for (const k of ["x", "y", "width", "height"] as const) {
      expect(
        Math.abs(after[i][k] - before[i][k]),
        `cell ${i} ${k} moved`,
      ).toBeLessThan(1);
    }
  }
}

// The local lane is serial and process-global (one GPU): a test that dies
// with slots still gated would starve every later test's local takes. Drain
// to quiescence — land the waiting, clear benches, cancel the never-started.
test.afterEach(async ({ request }) => {
  for (let i = 0; i < 20; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting)
      await resolve(request, sid, { outcome: "land" });
    const q = (await (await request.get("/api/queue")).json()) as {
      benches: { scope: string }[];
      held: { id: string }[];
      late: { id: string }[];
      lanes: {
        local: { slots: { id: string; status: string }[] };
        external: { slots: { id: string; status: string }[] };
      };
    };
    for (const b of q.benches)
      await request.post("/api/unbench", { data: { scope: b.scope } });
    const parked = [
      ...q.held,
      ...q.lanes.local.slots.filter((s) => s.status === "queued"),
      ...q.lanes.external.slots.filter((s) => s.status === "queued"),
    ];
    for (const s of parked) await request.post(`/api/slots/${s.id}/cancel`);
    const busy =
      st.waiting.length +
      q.benches.length +
      q.held.length +
      q.late.length +
      q.lanes.local.slots.length +
      q.lanes.external.slots.length;
    if (busy === 0) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("harness did not drain — later tests would starve");
});

test("skeletons before work, frozen geometry, per-slot failure, honest money", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `laws-a-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "law test A",
    local: [{ preset_id: "raw-zimage", takes: 3 }],
    external: [{ model_id: EXT_MODEL, takes: 1 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  expect(turn.slots.length).toBe(4);

  // the grid surfaces itself whole: all four cells present, none has
  // produced anything (no directive has been issued yet)
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(4, T);
  const statuses = await page
    .locator("[data-slot-pos]")
    .evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
  for (const s of statuses) expect(["queued", "running"]).toContain(s);
  // the queued local skeleton names what it waits for (law 13's spirit)
  await expect(cell(page, 2)).toContainText("waiting for the GPU");

  const before = await cellBoxes(page, 4);

  // external lands FIRST — out of order, into its own frozen cell, with the
  // money badge; the local lane hasn't produced anything yet
  await resolve(request, byPos.get(3)!.id, {
    outcome: "land",
    charged_usd: 0.04,
  });
  await expectStatus(page, 3, "landed");
  await expect(cell(page, 3)).toContainText("$0.04");
  await expectStatus(page, 0, /queued|running/);
  expectFrozen(before, await cellBoxes(page, 4));

  // local slot 0 lands — thumb renders from /thumb, geometry untouched
  await resolve(request, byPos.get(0)!.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  const thumb = cell(page, 0).locator('img[src*="/thumb"]');
  await expect(thumb).toBeVisible(T);
  await expect
    .poll(async () =>
      thumb.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    )
    .toBeGreaterThan(0);
  expectFrozen(before, await cellBoxes(page, 4));

  // slot 1 dies, a per-slot failure in plain words: the cause is
  // the scheduler's plain line for the kind (law 3), what the engine said
  // sits behind DETAILS, line 2 is GPU time — never money on a local slot
  // (law 5) — and the next move is a free button (laws 4 + 6)
  await resolve(request, byPos.get(1)!.id, {
    outcome: "fail",
    kind: "error",
    message: "scripted: the engine gave nothing back (HTTP 500)",
  });
  await expectStatus(page, 1, "failed");
  await expect(cell(page, 1)).toContainText("SLOT FAILED");
  await expect(cell(page, 1)).toContainText(
    "raw-zimage failed partway through the graph — the engine, not your prompt",
  );
  await expect(cell(page, 1)).toContainText("of GPU time spent");
  await expect(cell(page, 1)).not.toContainText("charged");
  const details = cell(page, 1).locator("details");
  await expect(details).toHaveCount(1);
  await expect(details).toHaveJSProperty("open", false);
  await expect(details.locator("pre")).toHaveText(
    /scripted: the engine gave nothing back \(HTTP 500\)/,
  );
  // nothing outside the affordance carries the code
  const face = await cell(page, 1).evaluate((el) => {
    const c = el.cloneNode(true) as HTMLElement;
    c.querySelector("details")?.remove();
    return c.textContent ?? "";
  });
  expect(face).not.toMatch(/HTTP|500/);
  await expect(cell(page, 1).getByRole("button", { name: "REROLL R" }))
    .toBeVisible();

  // law 1: the failure stopped nothing — the serial lane moves on to slot 2
  await expectStatus(page, 2, /queued|running/);
  await resolve(request, byPos.get(2)!.id, { outcome: "land" });
  await expectStatus(page, 2, "landed");

  // the whole ride never moved a pixel of grid chrome (law 8)
  expectFrozen(before, await cellBoxes(page, 4));
  const header = page.locator(`section[aria-label="Turn ${turn.idx} grid"]`);
  await expect(header).toContainText("3 LANDED · 1 FAILED", T);
  await expect(header).toContainText("ONE GPU, LOCALS TAKE TURNS");

  // money badges only where money moved: no $ on any local cell, in any state
  for (const pos of [0, 1, 2])
    await expect(cell(page, pos).getByText(/\$\d/)).toHaveCount(0);
});

test("nothing is silently lost: reroll keeps the old take, set aside is a place", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `laws-b-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "law test B",
    local: [{ preset_id: "raw-zimage", takes: 1 }],
  });
  const slotId = turn.slots[0].id;
  // goto BEFORE resolving: the grid auto-opens for an ACTIVE turn — land
  // first and the page would (rightly) open on a resting workspace, gridless
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(1, T);
  await resolve(request, slotId, { outcome: "land", color: [40, 80, 120] });
  await expectStatus(page, 0, "landed");
  await expect.poll(() => nodeCount(request, wid), T).toBe(1);

  // reroll in place: same slot; the old take keeps its node (law 7)
  await cell(page, 0).hover();
  await cell(page, 0).getByRole("button", { name: "REROLL R" }).click();
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, slotId, { outcome: "land", color: [120, 80, 40] });
  await expectStatus(page, 0, "landed");
  await expect.poll(() => nodeCount(request, wid), T).toBe(2);

  // set aside is a place, not a delete: the slot keeps its footprint
  await cell(page, 0).hover();
  await cell(page, 0).getByRole("button", { name: "SET ASIDE ⌫" }).click();
  await expectStatus(page, 0, "set_aside");
  await expect(cell(page, 0)).toContainText("Slot stays. Nothing moved.");
  expect(await nodeCount(request, wid)).toBe(2); // nothing deleted

  // and restore is one press away
  await cell(page, 0).getByRole("button", { name: "RESTORE ⌘Z" }).click();
  await expectStatus(page, 0, "landed");
  expect(await nodeCount(request, wid)).toBe(2);
});

test("one dead engine benches itself and never stops the others; resume is visible", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `laws-c-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "law test C",
    local: [{ preset_id: "raw-zimage", takes: 2 }],
    external: [{ model_id: EXT_MODEL, takes: 1 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(3, T);
  const before = await cellBoxes(page, 3);

  // the running raw-zimage take dies with an engine-scoped kind: the engine
  // benches, its remaining take parks HELD: gold, waiting, not dead
  await resolve(request, byPos.get(0)!.id, {
    outcome: "fail",
    kind: "model_down",
    message: "scripted: the model is down",
  });
  await expectStatus(page, 0, "failed");
  await expectStatus(page, 1, "held");
  await expect(cell(page, 1)).toContainText("resumes on unbench");
  await expect(cell(page, 1)).toContainText("kept, not dropped");

  // law 1: the other engine keeps landing regardless
  await resolve(request, byPos.get(2)!.id, {
    outcome: "land",
    charged_usd: 0.04,
  });
  await expectStatus(page, 2, "landed");

  // law 2: the pause is a queue state with a VISIBLE resume control — in
  // the strip, and (law 4) on the held cell itself
  await expect(page.getByText("ENGINE:RAW-ZIMAGE BENCHED")).toBeVisible(T);
  await expect(page.getByRole("button", { name: "UNBENCH", exact: true }))
    .toBeVisible();
  await cell(page, 1).getByRole("button", { name: "UNBENCH · RESUME" }).click();
  await expectStatus(page, 1, /queued|running/);
  await resolve(request, byPos.get(1)!.id, { outcome: "land" });
  await expectStatus(page, 1, "landed");

  expectFrozen(before, await cellBoxes(page, 3));
});

test("cancel is one breath: landed stay, queued drop free, nothing vanishes", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `laws-d-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "law test D",
    local: [{ preset_id: "raw-flux2", takes: 2 }], // immune to test C's raw-zimage bench history
  });
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(2, T);
  const before = await cellBoxes(page, 2);

  // the breath, with the amounts real: one queued behind the running take
  await expectStatus(page, 0, "running");
  await expectStatus(page, 1, "queued");
  await expect(page.getByText("1 queued drop free")).toBeVisible(T);
  // C6: the stop is two presses. The first says the breath with
  // the numbers, the second stops (e2e/trust-cases.spec.ts owns the breath)
  await page.getByRole("button", { name: "CANCEL TURN" }).click();
  await page.getByRole("button", { name: "STOP THE QUEUE" }).click();

  // running ends "cancelled", queued becomes SKIPPED — rerollable, not gone
  await expectStatus(page, 0, "failed");
  await expect(cell(page, 0)).toContainText("cancelled");
  await expect(cell(page, 0)).toContainText("of GPU time spent");
  await expect(cell(page, 1)).toContainText("no GPU time spent");
  await expectStatus(page, 1, "skipped");
  await expect(cell(page, 1)).toContainText("left unrun — reroll to run it");
  await expect(cell(page, 1).getByRole("button", { name: "REROLL R" }))
    .toBeVisible();

  expectFrozen(before, await cellBoxes(page, 2));
  expect(await nodeCount(request, wid)).toBe(0); // nothing to lose, none lost
});


test("a paid failure: plain cause, dollars stated, free remedy first and the paid one last; one line for one cause; a retry says so while it runs", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `laws-e-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "law test E",
    local: [{ preset_id: "raw-flux2", takes: 1 }],
    external: [{ model_id: EXT_MODEL, takes: 3 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  const ext = [1, 2, 3].map((pos) => byPos.get(pos)!);
  expect(ext.every((s) => s.engine === EXT_MODEL)).toBe(true);
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(4, T);
  const before = await cellBoxes(page, 4);

  // two paid takes refused by the provider's filter, same words each time
  for (const s of ext.slice(0, 2))
    await resolve(request, s.id, {
      outcome: "fail",
      kind: "moderation",
      message: "flagged by the provider (HTTP 403)",
      detail: '{"code": 403, "policy": "scripted"}',
    });
  await expectStatus(page, 1, "failed");
  await expectStatus(page, 2, "failed");

  // law 3: the face absolves the artist in plain words; the raw text waits
  // behind DETAILS
  await expect(cell(page, 1)).toContainText(
    "wouldn't run this prompt — their filter, not a fault at your end",
  );
  await expect(cell(page, 1).locator("details pre")).toHaveText(/HTTP 403/);
  await expect(cell(page, 1).locator("details pre")).toHaveText(/"policy"/);
  // law 5: money was in play, so line 2 says what happened to it
  await expect(cell(page, 1)).toContainText("nothing was charged");
  // law 6: the free move first and framed, the costed one last with its
  // price on its face and never the default
  const buttons = cell(page, 1).locator("button");
  await expect(buttons).toHaveText(["RUN LOCALLY · FREE", "REROLL R · ~$0.04"]);
  await expect(buttons.last()).toHaveAttribute("data-paid", "1");
  expect(await buttons.first().evaluate((b) => b.className)).toMatch(/actFramed/);
  expect(await buttons.last().evaluate((b) => b.className)).not.toMatch(/actFramed/);
  // law 1: two cells dead of one cause — said once, up in the header
  const notices = page.getByRole("list", { name: "Turn notices" });
  await expect(notices).toContainText("slots 2 · 3 failed for one reason");
  await expect(notices.locator("li")).toHaveCount(1);

  // law 9: the third paid take is rate limited — the scheduler retries it
  // once, visibly: the cell says RETRYING with the attempt while the second
  // attempt waits on the engine, and lands with no scar on its face
  await resolve(request, ext[2].id, {
    outcome: "fail",
    kind: "rate_limit",
    message: "slow down (HTTP 429)",
  });
  await expect(cell(page, 3)).toContainText("RETRYING · ATTEMPT 2", T);
  await expect(cell(page, 3)).toContainText("rate limited");
  await resolve(request, ext[2].id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 3, "landed");
  await expect(cell(page, 3)).toContainText("$0.04");
  await expect(cell(page, 3)).not.toContainText("RETRYING");

  // the same on the GPU lane: an unreachable ComfyUI retries once, and the
  // queue strip's running row says RETRY while the second attempt runs
  await expectStatus(page, 0, "running");
  await resolve(request, byPos.get(0)!.id, {
    outcome: "fail",
    kind: "unreachable",
    message: "connection refused",
  });
  await expect(cell(page, 0)).toContainText("RETRYING · ATTEMPT 2", T);
  await expect(page.getByText(/RAW-FLUX2 · T\d+ · .* · RETRY/)).toBeVisible(T);

  // the free remedy arms the composer with this turn's brief — nothing is
  // posted, nothing spent, Esc discards it
  await cell(page, 1).getByRole("button", { name: "RUN LOCALLY · FREE" }).click();
  const prompt = page.getByLabel("Prompt", { exact: true });
  await expect(prompt).toHaveValue("law test E", T);
  await page.keyboard.press("Escape");
  await expect(prompt).toHaveCount(0);

  // the second attempt lands like any other take (law 1: nothing else
  // was touched by any of it)
  await resolve(request, byPos.get(0)!.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  await expect(cell(page, 0)).not.toContainText("RETRYING");
  expectFrozen(before, await cellBoxes(page, 4));
});
