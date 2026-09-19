// The high-probability failure cases on a one-GPU workstation, driven against
// the REAL stack (server, scheduler, store, events poll, grid, strip,
// composer) with only the engines and the card scripted
// (app/harness.py, COCKPIT_HARNESS_EXECUTORS).
//
//   A1   out of video memory     — fails red AT ITS FRAME, no automatic
//                                  retry; RETRY AT the record's smaller
//                                  frame leads (same seed) and the cell
//                                  keeps that size
//   A11  nondeterministic OOM    — the engine held that size before: the
//                                  face absolves the size, RETRY AS-IS leads
//   A9   repeat failure          — the second failure of one engine in one
//                                  turn benches it FOR THIS TURN, holds the
//                                  rest, says it once in the footer with
//                                  TRY ONCE MORE; other engines keep running
//   B2   rate limited            — gold, attempt i of n, counting down on
//                                  the provider's clock, GIVE UP in reach;
//                                  nothing charged
//   C2   GPU held by another program — an OOM under a foreign hold pauses
//                                  the local lane only, names a SIZE (never a
//                                  program), externals land; CHECK AGAIN /
//                                  HOLD THE QUEUE; the lane picks itself up
//                                  when the card frees
//   E6   free VRAM + disk at Run — the run bar disarms on a drive that can't
//                                  hold the takes, with the numbers; warns on
//                                  a held card without disarming
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Slot = { id: string; pos: number; engine: string; status: string; seed: number | null };
type Turn = { id: string; idx: number; slots: Slot[] };
type Box = { x: number; y: number; width: number; height: number };
type SlotWire = Slot & {
  kind: string | null;
  frame: string | null;
  facts: Record<string, unknown> | null;
  attempt: number;
  retry_of: string | null;
};

const T = { timeout: 10_000 };
const EXT_MODEL = "openai/gpt-image-2"; // providers.json pin: $0.04, enabled
const GB = 1 << 30;

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

async function card(request: APIRequestContext, foreignGb: number): Promise<void> {
  const r = await request.post("/api/harness/gpu", {
    data: { total_bytes: 24 * GB, free_bytes: Math.round(22 * GB - foreignGb * GB), comfy_bytes: 2 * GB, foreign_bytes: Math.round(foreignGb * GB) },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function slotOf(request: APIRequestContext, wid: string, slotId: string): Promise<SlotWire> {
  const d = (await (await request.get(`/api/workspaces/${wid}`)).json()) as { turns: { slots: SlotWire[] }[] };
  for (const t of d.turns) for (const s of t.slots) if (s.id === slotId) return s;
  throw new Error(`no slot ${slotId}`);
}

async function nodeCount(request: APIRequestContext, wid: string): Promise<number> {
  const r = await request.get(`/api/workspaces/${wid}/nodes`);
  return ((await r.json()) as unknown[]).length;
}

const OOM = {
  outcome: "fail",
  kind: "oom",
  message: "execution error: CUDA out of memory. Tried to allocate 2.00 GiB",
  detail: '{"exception_type": "torch.OutOfMemoryError", "exception_message": "CUDA out of memory"}',
};

const cell = (page: Page, pos: number) => page.locator(`[data-slot-pos="${pos}"]`);

// "1152x1152" (the wire) → "1152 × 1152" (the face); the record decides the
// number: the harness DB accumulates across specs, and every earlier local
// run landed at some frame, so the smaller offer is whatever this engine has
// landed below the request — asserted from the facts, never assumed.
const words = (key: string) => key.replace("x", " × ");
const area = (key: string) => key.split("x").map(Number).reduce((a, b) => a * b, 1);
// a frame no other spec runs, so "held before" is this spec's own doing
const ODD = { width: 1472, height: 1408 };
const ODD_KEY = `${ODD.width}x${ODD.height}`;
// A1's request must be a size NO test ever lands at request-size: the
// harness DB accumulates across runs and A11's landed ODD row would
// otherwise flip a later run's A1 verdict to "held that size before".
// (A1's own retry lands at the smaller offer, below this — never at it.)
const A1_ODD = { width: 1344, height: 1216 };
const A1_KEY = `${A1_ODD.width}x${A1_ODD.height}`;

async function expectStatus(page: Page, pos: number, status: string | RegExp): Promise<void> {
  await expect(cell(page, pos)).toHaveAttribute("data-status", status, T);
}

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
  for (let i = 0; i < before.length; i++)
    for (const k of ["x", "y", "width", "height"] as const)
      expect(Math.abs(after[i][k] - before[i][k]), `cell ${i} ${k} moved`).toBeLessThan(1);
}

// The local lane is serial and process-global (one GPU): drain to
// quiescence after every test so a later spec's takes never starve — and
// put the scripted card back to clear so no later OOM reads as C2.
test.afterEach(async ({ request }) => {
  await card(request, 0);
  for (let i = 0; i < 80; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as { waiting: string[] };
    for (const sid of st.waiting) await resolve(request, sid, { outcome: "land" });
    const q = (await (await request.get("/api/queue")).json()) as {
      benches: { scope: string }[];
      held: { id: string }[];
      late: { id: string }[];
      checks: { slot_id: string }[];
      retries: { slot_id: string }[];
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
      st.waiting.length + q.benches.length + q.held.length + q.late.length + q.checks.length + q.retries.length +
      q.lanes.local.slots.length + q.lanes.external.slots.length;
    if (busy === 0) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("harness did not drain — later tests would starve");
});

test("A1: an OOM fails red at its frame with no automatic retry; RETRY AT the smaller frame leads, keeps the seed, and the cell keeps that size", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-a1-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "hw A1",
    local: [{ preset_id: "raw-zimage", takes: 1, inputs: A1_ODD }],
  });
  const [s0] = turn.slots;
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(1, T);
  const before = await cellBoxes(page, 1);

  await resolve(request, s0.id, OOM);
  await expectStatus(page, 0, "failed");
  await expect(cell(page, 0)).toContainText(`raw-zimage ran out of video memory at ${words(A1_KEY)} — a smaller frame is the sure fix`);
  await expect(cell(page, 0)).toContainText("of GPU time spent");
  await expect(cell(page, 0)).not.toContainText("charged");
  const failed = await slotOf(request, wid, s0.id);
  expect(failed.kind).toBe("oom");
  expect(failed.frame).toBe(A1_KEY);
  expect(failed.facts?.held_before).toBeNull();
  const small = failed.facts?.smaller as string;
  expect(small).toMatch(/^\d+x\d+$/);
  expect(area(small)).toBeLessThan(area(A1_KEY));
  expect(failed.attempt).toBe(1); // the artist chooses — nothing retried
  const seed = failed.seed;
  // the record's offer leads (framed), retry-as-is second, both free
  const buttons = cell(page, 0).locator("button[data-remedy]");
  await expect(buttons).toHaveText([`RETRY AT ${words(small)}`, "RETRY AS-IS · SAME SEED"]);
  expect(await buttons.first().evaluate((b) => b.className)).toMatch(/actFramed/);
  await expect(buttons.first()).not.toHaveAttribute("data-paid", "1");

  await buttons.first().click();
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, s0.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  const again = await slotOf(request, wid, s0.id);
  expect(again.seed).toBe(seed);
  expect(again.frame).toBe(small);
  expect(again.kind).toBeNull();
  expect(await nodeCount(request, wid)).toBe(1);
  expectFrozen(before, await cellBoxes(page, 1));
});

test("A11: an OOM at a size this engine held before absolves the size and leads with RETRY AS-IS", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-a11-${Date.now()}`);
  const first = await runTurn(request, wid, {
    prompt: "hw A11 first",
    local: [{ preset_id: "raw-zimage", takes: 1, inputs: ODD }],
  });
  await resolve(request, first.slots[0].id, { outcome: "land" });
  const turn = await runTurn(request, wid, {
    prompt: "hw A11 again",
    local: [{ preset_id: "raw-zimage", takes: 1, inputs: ODD }],
  });
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(1, T);
  await resolve(request, turn.slots[0].id, OOM);
  await expectStatus(page, 0, "failed");
  await expect(cell(page, 0)).toContainText(`though it held that size on turn ${first.idx} — the size isn't the problem`);
  const failed = await slotOf(request, wid, turn.slots[0].id);
  expect((failed.facts?.held_before as { turn_idx: number }).turn_idx).toBe(first.idx);
  const small = failed.facts?.smaller as string;
  const buttons = cell(page, 0).locator("button[data-remedy]");
  await expect(buttons).toHaveText(["RETRY AS-IS · SAME SEED", `RETRY AT ${words(small)}`]);
  expect(await buttons.first().evaluate((b) => b.className)).toMatch(/actFramed/);
  await buttons.first().click();
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, turn.slots[0].id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  expect((await slotOf(request, wid, turn.slots[0].id)).frame).toBe(ODD_KEY);
});

test("A9: the second failure of one engine in one turn benches it for this turn only — one footer line, the rest held, TRY ONCE MORE resumes; the other engine keeps running", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-a9-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "hw A9",
    local: [
      { preset_id: "raw-zimage", takes: 4 },
      { preset_id: "raw-flux2", takes: 1 },
    ],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(5, T);
  const before = await cellBoxes(page, 5);

  await resolve(request, byPos.get(0)!.id, { outcome: "fail", kind: "error", message: "boom 1" });
  await expectStatus(page, 0, "failed");
  await expectStatus(page, 1, "running");
  await resolve(request, byPos.get(1)!.id, { outcome: "fail", kind: "error", message: "boom 2" });
  await expectStatus(page, 1, "failed");
  // the rest of raw-zimage is held for this turn; raw-flux2 runs on
  await expectStatus(page, 2, "held");
  await expectStatus(page, 3, "held");
  await expectStatus(page, 4, "running");
  await expect(cell(page, 2)).toContainText("raw-zimage is benched for this turn — this take is kept, not dropped");
  await expect(cell(page, 2).locator("button[data-remedy]")).toHaveText(["TRY ONCE MORE"]);
  // one cause, one message — the footer, with the move
  const notices = page.getByRole("list", { name: "Turn notices" });
  const line = notices.locator('[data-bench-notice="repeat"]');
  await expect(line).toHaveCount(1);
  await expect(line).toContainText("raw-zimage failed on slot 1, and again on slot 2 — benched for this turn; its remaining takes here are held, not dropped");
  // the strip says it is this turn's bench, not the engine's
  await expect(page.locator('[data-bench="repeat"]')).toContainText("RAW-ZIMAGE BENCHED · THIS TURN ONLY");
  expectFrozen(before, await cellBoxes(page, 5));
  await resolve(request, byPos.get(4)!.id, { outcome: "land" });
  await expectStatus(page, 4, "landed");

  // TRY ONCE MORE: the held takes resume, this time they land
  await line.getByRole("button", { name: "TRY ONCE MORE" }).click();
  await expectStatus(page, 2, /queued|running/);
  await expectStatus(page, 3, /queued|running/);
  await expect(line).toHaveCount(0);
  await resolve(request, byPos.get(2)!.id, { outcome: "land" });
  await resolve(request, byPos.get(3)!.id, { outcome: "land" });
  await expectStatus(page, 2, "landed");
  await expectStatus(page, 3, "landed");
  expectFrozen(before, await cellBoxes(page, 5));
});

test("B2: a rate-limited take retries on the provider's clock — gold, attempt i of n, counting down, nothing charged, GIVE UP in reach", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-b2-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "hw B2 brief",
    external: [{ model_id: EXT_MODEL, takes: 2 }],
  });
  const [s0, s1] = turn.slots;
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(2, T);
  const before = await cellBoxes(page, 2);

  // the provider names its window: the cell counts down on it
  await resolve(request, s0.id, { outcome: "fail", kind: "rate_limit", message: "slow down (HTTP 429)", retry_after_s: 6 });
  await expectStatus(page, 0, "queued");
  await expect(cell(page, 0)).toContainText("RETRYING · ATTEMPT 2 OF 5", T);
  await expect(cell(page, 0)).toContainText("openai/gpt-image-2 is holding us at the door — retrying on its clock; nothing is charged until an image lands · next try in");
  await expect(cell(page, 0)).toContainText("Nothing needed from you.");
  await expect(cell(page, 0)).not.toContainText("charged $");
  const giveUp = cell(page, 0).getByRole("button", { name: "GIVE UP ON THIS SLOT" });
  await expect(giveUp).toBeVisible();
  // its sibling is unaffected — the permit was released for it
  await resolve(request, s1.id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 1, "landed");
  // the second attempt fires on the window and lands with no scar
  await expectStatus(page, 0, "running");
  await resolve(request, s0.id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 0, "landed");
  await expect(cell(page, 0)).not.toContainText("RETRYING");
  const landed = await slotOf(request, wid, s0.id);
  expect(landed.attempt).toBe(2);
  expectFrozen(before, await cellBoxes(page, 2));

  // and the give-up: skipped free, nothing spent
  const second = await runTurn(request, wid, { prompt: "hw B2 give up", external: [{ model_id: EXT_MODEL, takes: 1 }] });
  await expect(page.locator("[data-slot-pos]")).toHaveCount(1, T);
  await resolve(request, second.slots[0].id, { outcome: "fail", kind: "rate_limit", message: "429", retry_after_s: 30 });
  await cell(page, 0).getByRole("button", { name: "GIVE UP ON THIS SLOT" }).click();
  await expectStatus(page, 0, "skipped");
  await expect(cell(page, 0)).toContainText("nothing was charged");
});

test("C2: an OOM under a foreign hold pauses the local lane only — a size, never a name; externals land; CHECK AGAIN and HOLD THE QUEUE; the lane picks itself up when the card frees", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-c2-${Date.now()}`);
  const turn = await runTurn(request, wid, {
    prompt: "hw C2",
    local: [{ preset_id: "raw-zimage", takes: 2, inputs: { width: 1536, height: 1536 } }],
    external: [{ model_id: EXT_MODEL, takes: 1 }],
  });
  const byPos = new Map(turn.slots.map((s) => [s.pos, s]));
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator("[data-slot-pos]")).toHaveCount(3, T);
  const before = await cellBoxes(page, 3);

  // something else takes 5.8 GB of the card, then the take runs out of memory
  await card(request, 5.8);
  await resolve(request, byPos.get(0)!.id, OOM);
  await expectStatus(page, 0, "held");
  await expectStatus(page, 1, "held");
  await expect(cell(page, 0)).toContainText("HELD · THE CARD IS TAKEN");
  await expect(cell(page, 0)).toContainText("something else holds the card — this take waits its turn, kept, not dropped");
  await expect(cell(page, 0).locator("button[data-remedy]")).toHaveText(["CHECK AGAIN", "HOLD THE QUEUE"]);
  // one message, in the footer, naming the size — no program is named
  const notices = page.getByRole("list", { name: "Turn notices" });
  const line = notices.locator('[data-bench-notice="gpu_held"]');
  await expect(line).toHaveCount(1);
  await expect(line).toContainText("something else is holding 5.8 GB of the 24 GB card — the local engines can't get a turn until it lets go; external takes carry on, and the queue picks itself up when the card is free");
  await expect(line).not.toContainText(/blender|chrome|game/i);
  // the OOM'd take is a visible retry, not a failure — the record says so
  const held = await slotOf(request, wid, byPos.get(0)!.id);
  expect(held.retry_of).toBe("gpu_held");
  expect(held.kind).toBeNull();
  // the strip: a held lane, gold, with the look-again
  await expect(page.locator('[data-bench="gpu_held"]')).toContainText("LOCAL LANE HELD · THE CARD IS TAKEN");
  // externals carry on
  await resolve(request, byPos.get(2)!.id, { outcome: "land", charged_usd: 0.04 });
  await expectStatus(page, 2, "landed");
  expectFrozen(before, await cellBoxes(page, 3));

  // CHECK AGAIN while it is still held: nothing moves
  await line.getByRole("button", { name: "CHECK AGAIN" }).click();
  await expectStatus(page, 0, "held");
  // HOLD THE QUEUE: the automatic pickup stops — a free card no longer
  // resumes anything on its own; the face now offers the explicit resume
  await line.getByRole("button", { name: "HOLD THE QUEUE" }).click();
  await expect(line.getByRole("button", { name: "UNBENCH · RESUME" })).toBeVisible(T);
  await card(request, 0);
  await page.waitForTimeout(2500); // past two re-check ticks (1 s under playwright)
  await expectStatus(page, 0, "held");
  // CHECK AGAIN now frees it (the artist asked): the lane resumes, the
  // OOM'd take runs again as attempt 2 and lands
  await line.getByRole("button", { name: "CHECK AGAIN" }).click();
  await expectStatus(page, 0, /queued|running/);
  await expect(line).toHaveCount(0);
  await resolve(request, byPos.get(0)!.id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
  await resolve(request, byPos.get(1)!.id, { outcome: "land" });
  await expectStatus(page, 1, "landed");
  expect((await slotOf(request, wid, byPos.get(0)!.id)).attempt).toBe(2);
  expectFrozen(before, await cellBoxes(page, 3));

  // the automatic pickup: held again, the card frees, nobody presses anything
  const next = await runTurn(request, wid, {
    prompt: "hw C2 auto",
    local: [{ preset_id: "raw-zimage", takes: 1, inputs: { width: 1024, height: 1024 } }],
  });
  await expect(page.locator("[data-slot-pos]")).toHaveCount(1, T);
  await card(request, 6);
  await resolve(request, next.slots[0].id, OOM);
  await expectStatus(page, 0, "held");
  await card(request, 0.3);
  await expectStatus(page, 0, /queued|running/);
  await resolve(request, next.slots[0].id, { outcome: "land" });
  await expectStatus(page, 0, "landed");
});

test("E6: at Run the drive that can't hold the takes disarms with the numbers, and a held card warns without disarming", async ({ page, request }) => {
  const wid = await createWorkspace(request, `hw-e6-${Date.now()}`);
  // the sheet is the server's — this spec hands the composer a drive with
  // 40 MB free and a card 5.8 GB of which is held by something else
  await page.route("**/api/headroom", (route) =>
    route.fulfill({
      json: {
        gpu: { total_bytes: 24 * GB, free_bytes: 16 * GB, comfy_bytes: 2 * GB, foreign_bytes: Math.round(5.8 * GB), name: "harness card" },
        gpu_hold_threshold_bytes: 2.5 * GB,
        gpu_held: true,
        disk_free_bytes: 40 * 1024 * 1024,
        disk_margin_bytes: 0,
        disk_path: "/data/staging",
        bytes_per_px: 1.5,
        bytes_per_px_source: "fallback",
        at: new Date().toISOString(),
      },
    }),
  );
  await page.goto(`/#/w/${wid}`);
  await expect(page.getByText("Press N — or right-click — to summon")).toBeVisible(T);
  await page.locator(".react-flow").click({ button: "right", position: { x: 300, y: 200 } });
  await page.getByText("Image · new root").click();
  await page.getByLabel("Prompt", { exact: true }).fill("hw E6");
  // one 1024² take ≈ 1.5 MB: fits; the card warning is said, Run stays armed
  const vram = page.locator('[data-headroom-warning="vram"]');
  await expect(vram).toHaveText(/SOMETHING ELSE HOLDS 5\.8 GB OF THE 24 GB CARD — YOUR 1 LOCAL TAKE MAY NOT GET A TURN UNTIL IT LETS GO\. NOTHING IS BLOCKED HERE/);
  const run = page.getByRole("button", { name: /^Run · 1/ });
  await expect(run).toBeEnabled();
  await expect(page.locator('[data-headroom-warning="disk"]')).toHaveCount(0);
  // step the default row to 16: 16 × 1024² × 1.5 B/px = 24 MB… still fits
  // at 40 MB free; a 2048² frame puts a take at 6 MB — 16 of them is 96 MB
  const plus = page.getByRole("button", { name: "Prop Sheet takes +" });
  for (let i = 0; i < 15; i++) await plus.click();
  await expect(page.getByRole("button", { name: /^Run · 16/ })).toBeEnabled();
  // FRAME to its top: every take grows; the drive can't hold the run
  const frame = page.getByRole("slider", { name: "FRAME" });
  await frame.focus();
  await page.keyboard.press("End");
  const disk = page.locator('[data-headroom-warning="disk"]');
  await expect(disk).toHaveText(/16 slots need about \d+ MB and the cockpit's drive has 40 MB free — free up about \d+ MB first\./, T);
  await expect(page.getByRole("button", { name: /^Run · 16/ })).toBeDisabled();
  // back down: the drive holds it again, Run re-arms
  await page.keyboard.press("Home");
  await expect(disk).toHaveCount(0, T);
  await expect(page.getByRole("button", { name: /^Run · 16/ })).toBeEnabled();
  await page.keyboard.press("Escape");
});
