// The money corner against the real stack, over the
// scripted-executor harness. Two claims: (1) LOCAL ONLY is a switch the
// requests carry and the SERVER proves — the roster goes inert with the
// cause on the lane, the run records the flag, and the same flag with an
// external batch never becomes a turn; (2) the corner's totals are CHARGED
// truth from the ledger — unmoved by a committed quote, moved by the billed
// amount when a paid take lands. The e2e DB accumulates across runs, so
// totals are asserted as deltas from what the server reports first.
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Slot = { id: string; pos: number; status: string };
type Turn = { id: string; dials: { local_only?: boolean }; slots: Slot[] };
type Detail = { turns: Turn[] };

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

async function detail(
  request: APIRequestContext,
  wid: string,
): Promise<Detail> {
  const r = await request.get(`/api/workspaces/${wid}`);
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()) as Detail;
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

async function today(request: APIRequestContext, tz: number): Promise<number> {
  const r = await request.get(`/api/costs?tz=${tz}`);
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { today_usd: number }).today_usd;
}

const cell = (page: Page, pos: number) =>
  page.locator(`[data-slot-pos="${pos}"]`);
const localOnly = (page: Page) =>
  page.getByRole("switch", { name: "LOCAL ONLY" });
const totals = (page: Page) =>
  page.getByRole("status", { name: "Charged totals" });

// The local lane is serial and process-global — never leave it gated
// (same guard as batch-laws.spec.ts). The switch is browser-persisted, so
// it is turned off here too: a later spec must not inherit it.
test.afterEach(async ({ page, request }) => {
  await page.evaluate(() => localStorage.removeItem("cockpit.localOnly"));
  for (let i = 0; i < 20; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting) await resolve(request, sid);
    const q = (await (await request.get("/api/queue")).json()) as {
      lanes: { local: { slots: { id: string; status: string }[] } };
      held: { id: string }[];
    };
    const parked = [
      ...q.held,
      ...q.lanes.local.slots.filter((s) => s.status === "queued"),
    ];
    for (const s of parked) await request.post(`/api/slots/${s.id}/cancel`);
    if (st.waiting.length + parked.length === 0) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("harness did not drain — later tests would starve");
});

test("LOCAL ONLY: the switch persists, the roster goes inert, the run carries it, the server proves it", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `e2e-costs-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(localOnly(page)).toHaveAttribute("aria-checked", "false", T);
  await localOnly(page).click();
  await expect(localOnly(page)).toHaveAttribute("aria-checked", "true");
  await page.reload();
  await expect(localOnly(page)).toHaveAttribute("aria-checked", "true", T);

  // arm from the keyboard; the external lane says the one cause, its
  // rows stay listed with their steppers off (badge, never filter)
  await page.locator(".react-flow").click();
  await page.keyboard.press("n");
  await page.getByText("Image · new root").click();
  await expect(
    page.getByText("New image · root", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/LOCAL ONLY is on: no take leaves/)).toBeVisible();
  await expect(
    page.getByRole("spinbutton", { name: "GPT Image 2 takes" }),
  ).toBeDisabled();
  await page.getByLabel("Prompt", { exact: true }).fill("a greatsword, kept in");
  await expect(page.getByText("1 SLOT · NO SPEND · LOCAL ONLY")).toBeVisible();
  await page.getByRole("button", { name: "Run · 1" }).click();
  await expect(cell(page, 0)).toHaveAttribute("data-status", /queued|running/, T);

  // the turn records the flag (the manifest's local-only field)
  const d = await detail(request, wid);
  expect(d.turns[d.turns.length - 1].dials.local_only).toBe(true);

  // the proof is the server's: the same flag with an external batch is
  // refused in words and no turn exists for it
  const r = await request.post(`/api/workspaces/${wid}/turns`, {
    data: {
      prompt: "x",
      local_only: true,
      external: [{ model_id: EXT_MODEL, takes: 1 }],
    },
  });
  expect(r.status()).toBe(422);
  expect(await r.text()).toContain("LOCAL ONLY");
  expect((await detail(request, wid)).turns).toHaveLength(1);

  for (const s of d.turns[0].slots) await resolve(request, s.id);
  await expect(cell(page, 0)).toHaveAttribute("data-status", "landed", T);
});

test("the corner is charged truth: unmoved by a quote, moved by the bill when a paid take lands", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `e2e-costs-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  const tz = await page.evaluate(() => -new Date().getTimezoneOffset());
  const before = await today(request, tz);
  await expect(totals(page)).toContainText(`TODAY $${before.toFixed(2)}`, T);

  // a paid take in flight: the pin's $0.04 is committed, not charged
  const r = await request.post(`/api/workspaces/${wid}/turns`, {
    data: { prompt: "a priced sword", external: [{ model_id: EXT_MODEL, takes: 1 }] },
  });
  expect(r.status(), await r.text()).toBe(201);
  const turn = ((await r.json()) as { turn: Turn }).turn;
  await expect(cell(page, 0)).toHaveAttribute("data-status", /queued|running/, T);
  expect(await today(request, tz)).toBe(before);
  await expect(totals(page)).toContainText(`TODAY $${before.toFixed(2)}`);

  // the bill lands (the provider says 0.03, not the 0.04 quote) — the
  // corner moves by what was CHARGED, within the poll's window
  await resolve(request, turn.slots[0].id, { outcome: "land", charged_usd: 0.03 });
  await expect(cell(page, 0)).toHaveAttribute("data-status", "landed", T);
  await expect(totals(page)).toContainText(
    `TODAY $${(before + 0.03).toFixed(2)}`,
    T,
  );
  expect(await today(request, tz)).toBeCloseTo(before + 0.03, 6);
});
