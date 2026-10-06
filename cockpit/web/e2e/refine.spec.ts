// Refine end to end, over the scripted-executor harness.
// The claim under test is that the whole path is real: selecting a take and
// firing REFINE arms the composer on that take, Run posts it as a plain
// parent, the server builds the preset's img2img recipe, and the landing
// arrives on the canvas as a CHILD — an edge from the take it refined.
//
// The two honesty clauses ride along (law 12): an engine with no
// img2img recipe stays listed and dimmed with the reason, and the external
// lane stays listed but says it can't read a source image. Nothing
// here filters a roster.
//
// Nodes are seeded straight through app.db (same route as the canvas and
// verb specs): the harness server's real executors point at closed ports,
// so a landed source can't come from a real engine here.
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
type Node = { id: string; verb: string; parents: string[] };
type Slot = { id: string; pos: number };
type Detail = { turns: { id: string; slots: Slot[] }[] };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");

function seed(preset: "small" | "verbs"): Seed {
  const name = `refine-${preset}-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset ${preset}`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

async function nodes(request: APIRequestContext, wid: string): Promise<Node[]> {
  const r = await request.get(`/api/workspaces/${wid}/nodes`);
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()) as Node[];
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

test("select a take → REFINE → run: the landing is that take's child", async ({
  page,
  request,
}) => {
  const s = seed("small");
  const wid = s.workspace_id;
  const source = s.node_ids[0]; // turn 0 slot a, landed at (160, 120)
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  const before = (await nodes(request, wid)).length;

  await page.locator(".react-flow__node").first().click();
  await expect(row(page).getByText("Take 0a")).toBeVisible();
  await verb(page, "refine").click();

  // armed on that take, born beside it: 160 + one card (158) + the gap (62)
  await expect(page.getByText("Refine · from a take")).toBeVisible(T);
  await expect(page.getByText("✛ 380, 120")).toBeVisible();
  await expect(page.getByText("Born beside its parent")).toBeVisible();
  // "same prompt, new seeds" — the source's own prompt arrives carried in
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue("a rusty sword", T);

  // roster honesty: a txt2img-only engine is listed, dimmed, and says which
  // recipe it lacks (klein refines as ref-edit; Sketch/Turbo is
  // the one without an img2img graph); the external lane is listed
  // and says why it's out
  await expect(
    page.getByRole("spinbutton", { name: "Raw · FLUX.2 klein takes" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("spinbutton", { name: "Sketch · Z-Image Turbo takes" }),
  ).toBeDisabled();
  await expect(
    page.getByText("no img2img recipe committed").first(),
  ).toBeVisible();
  await expect(
    page.getByText(/External models take a text brief only/),
  ).toBeVisible();

  await page.getByRole("spinbutton", { name: "Prop Sheet takes" }).fill("0");
  await page.getByRole("spinbutton", { name: "Raw · Z-Image Base takes" }).fill("1");
  const turnsBefore = (await detail(request, wid)).turns.length;
  await page.getByRole("button", { name: "Run · 1" }).click();

  // The Run POST is the browser's — wait for the turn to exist before
  // resolving, or the directive lands on the seeded turn's dead slot and
  // the refine take waits forever (an observed race).
  await expect
    .poll(async () => (await detail(request, wid)).turns.length, T)
    .toBe(turnsBefore + 1);
  const d = await detail(request, wid);
  const turn = d.turns[d.turns.length - 1];
  await resolve(request, turn.slots[0].id);

  await expect
    .poll(async () => (await nodes(request, wid)).length, T)
    .toBe(before + 1);
  const landed = (await nodes(request, wid)).find(
    (n) => !s.node_ids.includes(n.id),
  )!;
  expect(landed.verb).toBe("refine");
  expect(landed.parents).toEqual([source]); // lineage, not a second root

  // and the canvas draws it as a child: an edge from the source to it
  await page.keyboard.press("g"); // the grid auto-opened on the new turn
  await expect(
    page.locator(`.react-flow__edge[data-id="${source}->${landed.id}"]`),
  ).toBeAttached(T);
});

test("a mesh can't be a source, and both surfaces say so instead of offering it", async ({
  page,
}) => {
  const s = seed("verbs");
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(2, T);

  // the mesh: REFINE drops to NOT FOR THIS NODE with the payload reason
  await page.locator(".react-flow__node").nth(1).click();
  await expect(row(page).getByText("NOT FOR THIS NODE")).toBeVisible(T);
  await expect(
    row(page).getByText("this node is a mesh — that verb works on images"),
  ).toBeVisible();

  // and the summon palette does not go live on it either — it keeps saying
  // it needs a parent, rather than arming a run the server would refuse
  await page.keyboard.press("n");
  await expect(page.getByText("N · Summon at cursor")).toBeVisible();
  await expect(page.getByText("NEEDS 1 PARENT")).toHaveCount(2);
  await page.keyboard.press("Escape");

  // the image, selected, does light both up — each named by the take it
  // would read (the 3D row is live; it opens its own plinth)
  await page.locator(".react-flow__node").first().click();
  await page.keyboard.press("n");
  await expect(page.getByText("FROM Take 0a")).toHaveCount(2, T);
});
