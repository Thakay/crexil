// The set-aside tray against the real server: set-aside
// takes leave the canvas and sit in the tray with their
// seed and prompt; search narrows; restore is one press; purge — the one
// irreversible verb (law 7) — fires only after a hold, purges exactly what
// is shown, and leaves the children their lineage. Backfill runs over
// the scripted harness: every set-aside slot re-armed in one action.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };
type Slot = { id: string; pos: number; status: string };
type Turn = { id: string; slots: Slot[] };
type NodeRow = { id: string; parents: string[]; set_aside: boolean };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(preset: "small"): Seed {
  const name = `tray-${preset}-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset ${preset}`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

async function nodesOf(
  request: APIRequestContext,
  wid: string,
): Promise<NodeRow[]> {
  return (await (
    await request.get(`/api/workspaces/${wid}/nodes`)
  ).json()) as NodeRow[];
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

const tray = (page: Page) =>
  page.getByRole("complementary", { name: "Set-aside tray" });

// Drain the serial local lane (one GPU, process-global) so a failed run
// never starves the specs after this one — same shape as batch-laws.
test.afterEach(async ({ request }) => {
  for (let i = 0; i < 20; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting) await resolve(request, sid);
    const q = (await (await request.get("/api/queue")).json()) as {
      lanes: { local: { slots: unknown[] }; external: { slots: unknown[] } };
    };
    if (
      st.waiting.length +
        q.lanes.local.slots.length +
        q.lanes.external.slots.length ===
      0
    )
      return;
    await new Promise((r) => setTimeout(r, 100));
  }
});

test("the tray: off the canvas, still in the record — search, restore, and a held purge", async ({
  page,
  request,
}) => {
  const s = seed("small");
  const [a, , c, d] = s.node_ids;
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 1" }),
  ).toBeVisible();

  // T opens it; the entry is named like a card and carries its facts
  await page.keyboard.press("t");
  await expect(tray(page)).toBeVisible();
  await expect(tray(page).getByText("SET ASIDE · 1")).toBeVisible();
  const entryC = tray(page).locator(`[data-node-id="${c}"]`);
  await expect(entryC).toContainText("0c · Z-IMAGE-TURBO");
  await expect(entryC).toContainText("S 43");
  await expect(entryC).toContainText("a rusty sword");
  await expect(entryC.locator("img")).toBeVisible();

  // search narrows, and says so when nothing matches
  const search = tray(page).getByLabel("Search set-aside prompts");
  await search.fill("zzz");
  await expect(tray(page).getByText(/No set-aside take matches/)).toBeVisible();
  await expect(
    tray(page).getByRole("button", {
      name: "Hold to purge the set-aside takes shown",
    }),
  ).toBeDisabled();
  await search.fill("");

  // restore: back in the record as a canvas node (its fallback spot, slot
  // row c, sits below the fitted viewport — onlyRenderVisibleElements keeps
  // it out of the DOM, so the proof is the feed + the counts), the tray
  // is empty and says so
  await entryC.getByRole("button", { name: "RESTORE" }).click();
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 0" }),
  ).toBeVisible(T);
  expect(
    (await nodesOf(request, s.workspace_id)).find((n) => n.id === c)?.set_aside,
  ).toBe(false);
  await expect(tray(page).getByText("SET ASIDE · 0")).toBeVisible();
  await expect(tray(page).getByText(/Nothing set aside/)).toBeVisible();

  // set aside a (d's parent) through the API: the tray learns it from the
  // events poll, states the child, and the edge to it draws nothing
  await request.post(`/api/nodes/${a}/set-aside`);
  const entryA = tray(page).locator(`[data-node-id="${a}"]`);
  await expect(entryA).toContainText("1 CHILD", T);
  await expect(page.locator(`.react-flow__node[data-id="${a}"]`)).toHaveCount(0);
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
  const hold = tray(page).getByRole("button", {
    name: "Hold to purge the set-aside takes shown",
  });
  await expect(hold).toHaveText(/PURGE SET-ASIDE · 1 NODE · FREES \d+ (B|KB)/);
  await expect(
    tray(page).getByText(/One child loses its parent image/),
  ).toBeVisible();

  // a tap is nothing (law 7: held)
  await hold.click();
  await page.waitForTimeout(400);
  await expect(entryA).toBeVisible();
  expect((await nodesOf(request, s.workspace_id)).map((n) => n.id)).toContain(a);

  // a hold purges: gone from every surface, the child keeps its lineage
  await hold.hover();
  await page.mouse.down();
  await page.waitForTimeout(1300);
  await page.mouse.up();
  await expect(entryA).toHaveCount(0, T);
  await expect(tray(page).getByText(/Purged 1 · freed/)).toBeVisible();
  const nodes = await nodesOf(request, s.workspace_id);
  expect(nodes.map((n) => n.id)).not.toContain(a);
  expect(nodes.find((n) => n.id === d)?.parents).toEqual([a]);
  expect((await request.get(`/api/nodes/${a}/image`)).status()).toBe(404);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 0" }),
  ).toBeVisible();
});

test("backfill refills every set-aside slot in one action — fresh seeds, same footprints", async ({
  page,
  request,
}) => {
  const r = await request.post("/api/workspaces", {
    data: { name: `tray-backfill-${Date.now()}` },
  });
  expect(r.status(), await r.text()).toBe(201);
  const wid = ((await r.json()) as { id: string }).id;
  const run = await request.post(`/api/workspaces/${wid}/turns`, {
    data: { prompt: "a rusty sword", local: [{ preset_id: "raw-zimage", takes: 2 }] },
  });
  expect(run.status(), await run.text()).toBe(201);
  const turn = ((await run.json()) as { turn: Turn }).turn;
  for (const sl of turn.slots) await resolve(request, sl.id);
  await expect
    .poll(async () => (await nodesOf(request, wid)).length, T)
    .toBe(2);
  const first = await nodesOf(request, wid);
  for (const n of first) await request.post(`/api/nodes/${n.id}/set-aside`);

  await page.goto(`/#/w/${wid}`);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 2" }),
  ).toBeVisible(T);
  await page.keyboard.press("g"); // a resting turn doesn't auto-open its grid
  const btn = page.getByRole("button", {
    name: "BACKFILL 2 SET-ASIDE SLOTS · B",
  });
  await expect(btn).toBeVisible(T);
  await expect(page.getByText(/a batch of 2 stays a batch of 2/)).toBeVisible();

  // pre-post the landings (per-slot FIFO — race-free), then backfill
  for (const sl of turn.slots) await resolve(request, sl.id);
  await btn.click();
  for (const pos of [0, 1])
    await expect(page.locator(`[data-slot-pos="${pos}"]`)).toHaveAttribute(
      "data-status",
      "landed",
      T,
    );
  await expect.poll(async () => (await nodesOf(request, wid)).length, T).toBe(4);
  // the aside takes stay in the tray; the grid has nothing left to fill
  const aside = (await nodesOf(request, wid)).filter((n) => n.set_aside);
  expect(aside.map((n) => n.id).sort()).toEqual(first.map((n) => n.id).sort());
  await expect(page.getByRole("button", { name: /BACKFILL/ })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 2" }),
  ).toBeVisible();
});
