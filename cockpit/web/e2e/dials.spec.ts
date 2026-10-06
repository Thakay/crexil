// The six dials against the real server, over the scripted-
// executor harness. Two claims under test: (1) VARY ONE DIAL on a take arms
// a sweep that Run turns into ONE turn of four slots sharing one seed, and
// the composer said what it would walk before anything was spent; (2) dial
// positions belong to the workspace: moved, they survive a reload.
//
// Every dial's plain-effect line is on screen while armed, and the
// word "sampler" is nowhere in the dock. Nodes seed through app.db
// like the verb and refine specs: the harness executors point at closed
// ports, so a landed source can't come from a real engine here.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { DB } from "./paths";

type Seed = { workspace_id: string; node_ids: string[] };
type Slot = { id: string; pos: number; seed: number | null; engine: string };
type Detail = { turns: { id: string; slots: Slot[] }[] };
type Dial = { id: string; effect: string };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");

function seed(preset: "small" | "verbs"): Seed {
  const name = `dials-${preset}-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset ${preset}`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
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

const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const verb = (page: Page, id: string) =>
  row(page).locator(`[data-verb="${id}"]`);
const dials = (page: Page) => page.getByRole("region", { name: "Dials" });

// The local lane is serial and process-global — never leave it gated.
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

test("VARY ONE DIAL → run: one turn, four slots, one seed — said before it was spent", async ({
  page,
  request,
}) => {
  const s = seed("small");
  const wid = s.workspace_id;
  const table = (await (await request.get("/api/dials")).json()) as {
    dials: Dial[];
  };
  expect(table.dials.map((d) => d.id)).toEqual([
    "anchor-hold",
    "prompt-grip",
    "cook-time",
    "frame",
    "seed",
    "sweep-one-dial",
  ]);

  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.locator(".react-flow__node").first().click();
  await expect(row(page).getByText("Take 0a")).toBeVisible();
  await expect(verb(page, "vary-dial")).toHaveAttribute("aria-disabled", "false");
  await verb(page, "vary-dial").click();

  // armed on that take as a sweep, ANCHOR HOLD lit first
  await expect(page.getByText("Sweep · from a take")).toBeVisible(T);
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue("a rusty sword", T);
  // the first engine that can refine is stepped, at the sweep's default walk of 4
  const propSheet = page.getByRole("spinbutton", { name: "Prop Sheet takes" });
  await expect(propSheet).toBeEnabled();
  await expect(propSheet).toHaveValue("4");
  await expect(
    page.getByRole("radiogroup", { name: "Sweep target" }).getByRole("radio", { name: "ANCHOR HOLD" }),
  ).toHaveAttribute("aria-checked", "true");

  // every dial states its effect, permanently; no "sampler" above the fold
  for (const d of table.dials) await expect(dials(page)).toContainText(d.effect);
  const dockText = (await page.locator("main, body").first().textContent()) ?? "";
  expect(dockText.toLowerCase()).not.toContain("sampler");

  // the walk is stated in raws before Run — moved onto Z-Image Base, four slots
  await propSheet.fill("0");
  await page.getByRole("spinbutton", { name: "Raw · Z-Image Base takes" }).fill("4");
  await expect(dials(page)).toContainText(
    "WALKS ANCHOR HOLD 34 · 50 · 66 · 82 → DENOISE 0.7 · 0.55 · 0.4 · 0.25",
  );
  await expect(dials(page)).toContainText("LOCKED FOR THE SWEEP");

  const before = (await detail(request, wid)).turns.length;
  await page.getByRole("button", { name: "Run · 4" }).click();
  await expect
    .poll(async () => (await detail(request, wid)).turns.length, T)
    .toBe(before + 1);
  const d = await detail(request, wid);
  const turn = d.turns[d.turns.length - 1];
  expect(turn.slots).toHaveLength(4);
  expect(turn.slots.map((sl) => sl.engine)).toEqual(Array(4).fill("raw-zimage"));
  // one seed to every take — the dial is the only thing that moves
  const seeds = new Set(turn.slots.map((sl) => sl.seed));
  expect(seeds.size).toBe(1);
  for (const sl of turn.slots) await resolve(request, sl.id);
});

test("dial positions belong to the workspace: moved, they survive a reload", async ({
  page,
}) => {
  const s = seed("small");
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);

  await page.keyboard.press("n");
  await page.getByText("Image · new root").click();
  await expect(page.getByText("New image · root", { exact: true })).toBeVisible(T);
  const grip = page.getByRole("slider", { name: "PROMPT GRIP" });
  await expect(grip).toHaveValue("50", T);
  await grip.fill("80");
  await expect(grip).toHaveValue("80");
  await page.getByRole("radio", { name: "16:9" }).click();
  await expect(dials(page)).toContainText("1344 × 768");

  // Esc discards the run — not the dials, which flush to the workspace
  await page.keyboard.press("Escape");
  await expect(page.getByText("New image · root", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.keyboard.press("n");
  await page.getByText("Image · new root").click();
  await expect(page.getByRole("slider", { name: "PROMPT GRIP" })).toHaveValue("80", T);
  await expect(dials(page)).toContainText("1344 × 768");
  await page.keyboard.press("Escape");
});

// The Advanced fold against the real presets: collapsed with its
// count and nothing above it says sampler; opened, a raw knob rides
// the run's inputs beside the dials' raws, and belongs to the workspace.
test("the Advanced fold: collapsed and counted, no sampler above it; a raw knob rides the run and survives a reload", async ({
  page,
  request,
}) => {
  const s = seed("small");
  const wid = s.workspace_id;
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.keyboard.press("n");
  await page.getByText("Image · new root").click();
  await expect(page.getByText("New image · root", { exact: true })).toBeVisible(T);
  await page.getByLabel("Prompt", { exact: true }).fill("a rusty sword, raw knobs");

  // prop-sheet is the first preset: six settings on a root run,
  // with the negative prompt listed as unsupported
  const fold = page.getByRole("region", { name: "Advanced" });
  const head = fold.getByRole("button", { name: /ADVANCED · 6 ENGINE-SPECIFIC SETTINGS/ });
  await expect(head).toHaveAttribute("aria-expanded", "false", T);
  await expect(head).toContainText("0 CHANGED FROM DEFAULT");
  const dockText = (await page.locator("main, body").first().textContent()) ?? "";
  expect(dockText.toLowerCase()).not.toContain("sampler");

  await head.click();
  const sampler = fold.getByRole("combobox", { name: "Sampler" });
  await expect(sampler).toHaveValue("euler");
  await sampler.selectOption("dpmpp_2m");
  await expect(head).toContainText("1 CHANGED FROM DEFAULT");
  await expect(fold).toContainText("NO CHANNEL ON THIS ENGINE");
  await expect(fold).toContainText("guidance-distilled");

  // the run carries the raw knob beside the dials' own raws
  const before = (await detail(request, wid)).turns.length;
  const posted = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().includes("/turns"),
  );
  await page.getByRole("button", { name: "Run · 1" }).click();
  const body = (await posted).postDataJSON() as {
    local: { preset_id: string; inputs: Record<string, unknown> }[];
  };
  expect(body.local).toHaveLength(1);
  expect(body.local[0].preset_id).toBe("prop-sheet");
  expect(body.local[0].inputs.sampler_name).toBe("dpmpp_2m");
  expect(body.local[0].inputs.steps).toBe(4); // COOK TIME at 50 — klein's recipe
  // land the take, so the grid doesn't surface over the canvas on reload
  await expect
    .poll(async () => (await detail(request, wid)).turns.length, T)
    .toBe(before + 1);
  const d = await detail(request, wid);
  for (const sl of d.turns[d.turns.length - 1].slots) await resolve(request, sl.id);

  // the overrides belong to the workspace: back after a reload, counted
  // while the fold is still collapsed
  await page.reload();
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.keyboard.press("n");
  await page.getByText("Image · new root").click();
  await expect(
    page.getByRole("button", { name: /ADVANCED · 6 ENGINE-SPECIFIC SETTINGS/ }),
  ).toContainText("1 CHANGED FROM DEFAULT", T);
  await page.keyboard.press("Escape");
});
