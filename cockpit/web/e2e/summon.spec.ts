// Summon at cursor → armed composer → Run: a UI-triggered
// turn, proven over the scripted-executor harness. The load-bearing
// claim: the node is born WHERE THE PALETTE WAS. Screen
// geometry (fitView refits after the first landing) is never asserted —
// node transforms are read in flow coordinates, which a fresh workspace
// (zoom 1, origin 0) maps 1:1 from the right-click point.
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Slot = { id: string; pos: number; status: string };
type Detail = {
  turns: { id: string; anchor_x: number | null; anchor_y: number | null; slots: Slot[] }[];
};

const T = { timeout: 10_000 };

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
): Promise<void> {
  const r = await request.post(`/api/harness/slots/${slotId}/resolve`, {
    data: { outcome: "land" },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
}

const cell = (page: Page, pos: number) =>
  page.locator(`[data-slot-pos="${pos}"]`);

// The local lane is serial and process-global — never leave it gated
// (same guard as batch-laws.spec.ts).
test.afterEach(async ({ request }) => {
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

test("summon → compose → run: the node is born where the palette was", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `e2e-summon-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(
    page.getByText("Press N — or right-click — to summon"),
  ).toBeVisible(T);

  // right-click a known pane point — with a fresh viewport (zoom 1,
  // origin 0) its flow coords are exactly the container-relative offset
  const rf = await page.locator(".react-flow").boundingBox();
  expect(rf).not.toBeNull();
  const at = { x: rf!.x + 420, y: rf!.y + 260 };
  await page.mouse.click(at.x, at.y, { button: "right" });

  // the palette, at the cursor: entries needing a parent listed, never
  // hidden
  await expect(page.getByText("N · Summon at cursor")).toBeVisible();
  await expect(page.getByText("NEEDS 1 PARENT")).toHaveCount(2);
  await page.getByText("Image · new root").click();

  // armed composer carries the flow anchor. exact:true is case-sensitive —
  // the verb row carries a NEW IMAGE · ROOT chip, which a loose
  // getByText would also match.
  await expect(
    page.getByText("New image · root", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("✛ 420, 260")).toBeVisible();

  await page.getByLabel("Prompt", { exact: true }).fill("a blood-infused greatsword");
  // one number per engine: the default row steps down, Z-Image up
  await page.getByRole("spinbutton", { name: "Prop Sheet takes" }).fill("0");
  await page.getByRole("spinbutton", { name: "Raw · Z-Image Base takes" }).fill("2");
  await page.getByRole("button", { name: "Run · 2" }).click();

  // the grid auto-opens with both skeletons before any work lands
  await expect(cell(page, 0)).toHaveAttribute(
    "data-status",
    /queued|running/,
    T,
  );
  await expect(cell(page, 1)).toHaveAttribute(
    "data-status",
    /queued|running/,
    T,
  );

  // the turn was anchored at the summon point
  const d = await detail(request, wid);
  const turn = d.turns[d.turns.length - 1];
  expect(turn.anchor_x).toBeCloseTo(420, 0);
  expect(turn.anchor_y).toBeCloseTo(260, 0);

  // land both takes through the harness, close the grid, read the canvas
  for (const s of [...turn.slots].sort((a, b) => a.pos - b.pos))
    await resolve(request, s.id);
  await expect(cell(page, 0)).toHaveAttribute("data-status", "landed", T);
  await expect(cell(page, 1)).toHaveAttribute("data-status", "landed", T);
  await page.keyboard.press("g");

  // two cards, born at the anchor, stacked 180px per slot (fallbackPosition)
  await expect(page.locator(".react-flow__node")).toHaveCount(2, T);
  const spots = new Set<string>();
  for (const el of await page.locator(".react-flow__node").all()) {
    const m = /matrix\(1, 0, 0, 1, (-?[\d.]+), (-?[\d.]+)\)/.exec(
      await el.evaluate((n) => getComputedStyle(n).transform),
    );
    expect(m).not.toBeNull();
    spots.add(`${Math.round(Number(m![1]))},${Math.round(Number(m![2]))}`);
  }
  expect(spots).toEqual(new Set(["420,260", "420,440"]));
});

test("N summons at the canvas centre without a pointer; Esc closes; discard spends nothing", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `e2e-summon-kb-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(
    page.getByText("Press N — or right-click — to summon"),
  ).toBeVisible(T);

  await page.keyboard.press("n");
  await expect(page.getByText("N · Summon at cursor")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("N · Summon at cursor")).not.toBeVisible();

  // arm via keyboard end to end, then discard — no turn may exist
  await page.keyboard.press("n");
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("New image · root", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Prompt", { exact: true }).press("Escape");
  await expect(
    page.getByText("New image · root", { exact: true }),
  ).not.toBeVisible();
  expect((await detail(request, wid)).turns).toHaveLength(0);
});

// Arming on top of an armed run (the row's chip over the palette's run, or
// N over the chip's) must not strand Esc: the composer stays mounted, so a
// mount-time focus misses the re-arm and a focus-scoped handler would never
// hear the key. Esc is the composer's wherever focus sits.
test("arming again over an armed run leaves Esc working, both ways round", async ({
  page,
  request,
}) => {
  const wid = await createWorkspace(request, `e2e-summon-rearm-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(
    page.getByText("Press N — or right-click — to summon"),
  ).toBeVisible(T);
  const armed = page.getByText("New image · root", { exact: true });
  const rootVerb = page
    .getByRole("region", { name: "Verbs" })
    .locator('[data-verb="new-root"]');

  // N → Image · new root, then the row's chip on top of it
  await page.keyboard.press("n");
  await page.keyboard.press("Enter");
  await expect(armed).toBeVisible();
  await rootVerb.click();
  await expect(armed).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(armed).not.toBeVisible();

  // the chip first, then N over it (the caret sits in the prompt after an
  // arm, so a click elsewhere comes first — N is a letter in there, not a
  // key; the top bar, since the 720 px window leaves the armed dock no
  // canvas to click)
  await rootVerb.click();
  await expect(armed).toBeVisible();
  await expect(page.getByLabel("Prompt", { exact: true })).toBeFocused();
  await page.getByRole("heading", { level: 1 }).click();
  await page.keyboard.press("n");
  await expect(page.getByText("N · Summon at cursor")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByText("N · Summon at cursor")).not.toBeVisible();
  await expect(armed).toBeVisible();
  await expect(page.getByLabel("Prompt", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(armed).not.toBeVisible();
  expect((await detail(request, wid)).turns).toHaveLength(0);
});
