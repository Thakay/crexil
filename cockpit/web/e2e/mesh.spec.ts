// The To-3D lane end to end: TO 3D on a landed image take opens
// the mesh plinth in the dock (a place, not a modal — law 2), the roster
// is the capability matrix's two 3D engines with steppers, RUN posts the
// mesh turn, and the take lands as a MESH node on the canvas beside its
// source. The summon palette's `3D · from an image` reaches the same
// plinth.
//
// The mesh turn runs live against the harness's mesh executor (the fourth
// scripted stand-in, `payload: "3d"` — it returns a real minimal GLB, and
// a real animated turntable, so the node BODY is exercised
// here rather than only its fallback). The spec opens that GLB in the real
// viewer: an empty scene, read honestly. The mount is proven here;
// rendering a real mesh needs a real engine and is not covered here.
// Source nodes are seeded straight through app.db, the canvas/refine/region
// specs' route: the plinth needs a landed take whose image bytes exist.
//
// The lane closes at the far end here too: the same landed mesh is
// promoted through the real plinth, and the GLB, its manifest and its
// orbit are read back off the e2e promote tree.
import { execSync } from "node:child_process";
import { readFileSync, readdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(): Seed {
  const name = `mesh-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset small`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const verb = (page: Page, id: string) =>
  row(page).locator(`[data-verb="${id}"]`);
const plinth = (page: Page) => page.getByRole("region", { name: "To 3D" });

async function resolve(
  request: APIRequestContext,
  slotId: string,
  directive: Record<string, unknown> = { outcome: "land" },
): Promise<void> {
  const r = await request.post(`/api/harness/slots/${slotId}/resolve`, {
    data: directive,
  });
  expect(r.ok(), await r.text()).toBeTruthy();
}

// The local lane is serial and process-global (one GPU) — a mesh take left
// gated would starve every later spec's local takes. Batch-laws' drain
// rule, applied to the 3D lane.
test.afterEach(async ({ request }) => {
  for (let i = 0; i < 20; i++) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting) await resolve(request, sid);
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

test("TO 3D opens the plinth, states the lane, and Esc queues nothing", async ({
  page,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.locator(".react-flow__node").first().click();

  // The chip is live and states its
  // effect rather than a gap.
  const chip = verb(page, "to-3d");
  await expect(chip).toHaveAttribute("aria-disabled", "false");
  await expect(chip).toContainText("turn this take into a mesh");
  await chip.click();
  await expect(plinth(page)).toBeVisible(T);

  // The image is the whole input — this surface has no prompt at all.
  await expect(
    plinth(page).getByText(/The image is the input — no prompt, no dials/),
  ).toBeVisible();
  await expect(plinth(page).getByRole("textbox")).toHaveCount(0);

  // The roster IS the capability matrix's 3D rows, and only those.
  // exact: gltfpack's dashed reason names TRELLIS.2 too
  await expect(
    plinth(page).getByText("TRELLIS.2 4B", { exact: true }),
  ).toBeVisible(T);
  await expect(
    plinth(page).getByText("Hunyuan3D 2.1", { exact: true }),
  ).toBeVisible();
  await expect(plinth(page).getByText("Z-Image Turbo")).toHaveCount(0);

  // Minutes, not seconds, and no predicted total.
  await expect(
    plinth(page).getByText(/Meshes take the single GPU one at a time/),
  ).toBeVisible();

  // Esc closes it; nothing was queued, so the turn count hasn't moved.
  const readout = page.getByText(/turn \d+ · \d+ nodes/);
  // textContent, not innerText: the readout is uppercased in CSS, and
  // innerText would compare the rendered case against the DOM's.
  const before = await readout.textContent();
  await page.keyboard.press("Escape");
  await expect(plinth(page)).toHaveCount(0);
  await expect(readout).toHaveText(before);
});

test("the summon palette's 3D entry reaches the same plinth", async ({
  page,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.locator(".react-flow__node").first().click();
  await page.keyboard.press("n");
  const mesh = page.getByText("3D · from an image");
  await expect(mesh).toBeVisible(T);
  await mesh.click();
  await expect(plinth(page)).toBeVisible(T);
  // it opened its own surface, not the composer
  await expect(page.getByLabel("Prompt")).toHaveCount(0);
});

test("run: step an engine → the mesh lands as a MESH node beside its source", async ({
  page,
  request,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  const meshCards = page.locator('.react-flow__node:has-text("MESH")');
  await expect(meshCards).toHaveCount(0);

  await page.locator(".react-flow__node").first().click();
  await verb(page, "to-3d").click();
  await expect(plinth(page)).toBeVisible(T);

  // Armed at one take on the first engine; step the second up too.
  const trellis = plinth(page).getByRole("spinbutton", {
    name: "TRELLIS.2 4B takes",
  });
  await expect(trellis).toHaveValue("1", T);
  await plinth(page)
    .getByRole("button", { name: "Hunyuan3D 2.1 takes +" })
    .click();
  await expect(
    plinth(page).getByText("2 MESHES · 2 ENGINES · NO SPEND"),
  ).toBeVisible();

  const go = plinth(page).getByRole("button", { name: "To 3D · 2" });
  await expect(go).toBeEnabled();
  await go.click();

  // Launching closes the plinth and the grid takes the canvas region
  // (a new turn surfaces its own grid) with skeletons before any work.
  await expect(plinth(page)).toHaveCount(0, T);
  await expect(
    page.getByRole("region", { name: /Turn( \d+)? grid/ }),
  ).toBeVisible(T);

  // The 3D lane's unit is MINUTES, and the cells say so while they
  // wait — one running on the GPU, one behind it. No ETA, no bar, no
  // predicted total: the fact, and the observed clock beside it.
  const grid = page.getByRole("region", { name: /Turn( \d+)? grid/ });
  await expect(
    grid.getByText("The mesh engine holds the GPU for minutes. Nothing is stuck."),
  ).toBeVisible(T);
  await expect(grid.getByText(/waiting for the GPU · minutes, not seconds/)).toBeVisible(T);

  // Land both takes through the harness's mesh executor.
  for (let landed = 0; landed < 2; ) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting) {
      await resolve(request, sid);
      landed += 1;
    }
    if (landed < 2) await new Promise((r) => setTimeout(r, 200));
  }

  // Two MESH cards on the canvas — the payload abstraction the row names:
  // a 3d node draws with the mesh edge kind, and with a real
  // BODY: its plate is frame 0 of the engine's own turntable, fetched
  // through /thumb exactly as an image's is.
  // grid away, canvas back — G TOGGLES, so press it only once the grid is
  // actually up (this one was already open above); pressing blind races
  // the turn's auto-open and opens the grid over the cards instead.
  await expect(grid).toBeVisible(T);
  await page.keyboard.press("g");
  await expect(grid).toHaveCount(0, T);
  await expect(meshCards).toHaveCount(2, T);
  const plate = meshCards.first().locator("img");
  await expect(plate).toBeVisible(T);
  await expect(plate).toHaveAttribute("src", /\/api\/nodes\/.+\/thumb$/);
  // it decoded — a broken plate has no intrinsic size
  await expect
    .poll(async () => plate.evaluate((el: HTMLImageElement) => el.naturalWidth))
    .toBeGreaterThan(0);

  // The mesh's lightbox is the VIEWER: a real
  // GLB canvas loading the artefact itself through /image, the STAGE chrome
  // around it. The harness GLB is an empty scene, so what is proven here is
  // the mount and the honest state for it (a real mesh needs a real
  // engine); the turntable stays the fallback face.
  await meshCards.first().dblclick();
  const box = page.getByRole("dialog", { name: "Mesh viewer" });
  await expect(box).toBeVisible(T);
  await expect(box.getByText("MESH VIEWER")).toBeVisible();
  await expect(
    box.getByText("ESC OR THE BACKDROP CLOSES · THE FRAME DOESN'T"),
  ).toBeVisible();
  // the R3F canvas mounted, and the loader read the empty file for what it is
  await expect(box.locator("canvas")).toBeVisible(T);
  await expect(
    box.getByText("THE FILE PARSED · THERE IS NO GEOMETRY IN IT"),
  ).toBeVisible(T);
  // the facts line the caption always carried, and the corner that names
  // the take, its engine and its source
  await expect(box.getByText(/^HARNESS · 0 TRIS/)).toBeVisible();
  await expect(box.getByText(/^3D · HARNESS · FROM Take/)).toBeVisible();
  // the mode row is there, the whole list, every mode wearing its letter,
  // dimmed, because they act on geometry that isn't here
  const modes = box.getByRole("toolbar", { name: "View modes" });
  for (const m of [
    "RESET VIEW",
    "FROM BELOW",
    "TEXTURED",
    "CLAY",
    "BACKFACE CULL",
    "NORMALS",
    "WIREFRAME",
    "GRID",
  ])
    await expect(modes.getByText(m)).toBeVisible();
  await expect(modes.getByRole("button", { name: /^GRID/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  // the close rule: a click in the frame does not close it
  await box.locator("canvas").click({ position: { x: 20, y: 20 } });
  await expect(box).toBeVisible();
  // the fallback for every dead case: the turntable
  await box.getByRole("button", { name: "SHOW THE TURNTABLE" }).click();
  await expect(box.locator("img")).toHaveAttribute(
    "src",
    /\/api\/nodes\/.+\/turntable$/,
  );
  await expect(box.getByText(/THE ENGINE'S OWN RENDER/)).toBeVisible();
  // Esc closes
  await page.keyboard.press("Escape");
  await expect(box).toHaveCount(0, T);

  // And the verb row treats a mesh as a mesh: the image verbs drop to NOT
  // FOR THIS NODE rather than vanishing (law 12), and the row
  // carries INSPECT V, a canvas mesh's keyboard path to the viewer.
  await meshCards.first().click();
  await expect(row(page).getByText("NOT FOR THIS NODE")).toBeVisible(T);
  await expect(
    row(page).locator('[data-verb="to-3d"]').first(),
  ).toBeVisible();
  await expect(verb(page, "inspect")).toHaveAttribute("aria-disabled", "false");
  await page.keyboard.press("v");
  await expect(box).toBeVisible(T);
  // and the backdrop closes — the gutter beside the frame
  await box.click({ position: { x: 6, y: 300 } });
  await expect(box).toHaveCount(0, T);
});

// The lane's far end: a cockpit mesh reaching the artist's tree.
// Promote writes a mesh under `_3d/` as a GLB, with its manifest and its
// orbit beside it, never as `<asset_id>.png`. The whole loop is here
// because the promotion is irreversible and the FILE ON DISK is the only
// proof that matters.
test("a mesh promotes into _3d/ as a GLB, with its orbit beside it", async ({
  page,
  request,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);

  // one mesh, landed through the harness's mesh executor
  await page.locator(".react-flow__node").first().click();
  await verb(page, "to-3d").click();
  await expect(plinth(page)).toBeVisible(T);
  await plinth(page).getByRole("button", { name: "To 3D · 1" }).click();
  await expect(plinth(page)).toHaveCount(0, T);
  for (let landed = 0; landed < 1; ) {
    const st = (await (await request.get("/api/harness/state")).json()) as {
      waiting: string[];
    };
    for (const sid of st.waiting) {
      await resolve(request, sid);
      landed += 1;
    }
    if (landed < 1) await new Promise((r) => setTimeout(r, 200));
  }
  // G TOGGLES, so wait for the auto-open before pressing it away: pressing
  // blind races the landing and opens the grid instead, leaving it over the
  // canvas cards this test then clicks.
  const grid = page.getByRole("region", { name: /Turn \d+ grid/ });
  await expect(grid).toBeVisible(T);
  await page.keyboard.press("g");
  await expect(grid).toHaveCount(0, T);
  const meshCards = page.locator('.react-flow__node:has-text("MESH")');
  await expect(meshCards).toHaveCount(1, T);

  // the plinth speaks the payload's own words before the hold: three files,
  // and the mesh named as a mesh
  await meshCards.first().click();
  const promoteChip = page.getByRole("button", { name: /^PROMOTE/ });
  await expect(promoteChip).toHaveAttribute("aria-disabled", "false");
  await promoteChip.click();
  const pl = page.getByRole("region", { name: "Promote" });
  await expect(pl).toBeVisible(T);
  await expect(pl).toContainText("_3d/");
  await expect(pl).toContainText("4 FILES");
  await expect(pl).toContainText("the mesh, exactly the file the engine wrote");
  await expect(pl).toContainText("turntable render");

  const holdBtn = pl.getByRole("button", { name: /Hold to hold to promote/i });
  await holdBtn.scrollIntoViewIfNeeded();
  const box = (await holdBtn.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(1200);
  await page.mouse.up();
  await expect(pl).toContainText("PROMOTED", T);

  // and on disk: a real GLB under _3d/<slug>/, its manifest, its orbit
  const ws = (await (
    await request.get(`/api/workspaces/${s.workspace_id}`)
  ).json()) as { slug: string };
  const folder = path.resolve(HERE, "../../../.staging/e2e-promote/_3d", ws.slug);
  const files = readdirSync(folder);
  const glb = files.find((f) => f.endsWith(".glb"))!;
  expect(glb, `no GLB in ${files.join(", ")}`).toBeTruthy();
  const assetId = glb.replace(/\.glb$/, "");
  expect(files).toContain(`${assetId}.json`);
  expect(files).toContain(`${assetId}.turntable.webp`);
  expect(files.some((f) => f === `${assetId}.png`)).toBe(false);
  expect(readFileSync(path.join(folder, glb)).byteLength).toBeGreaterThan(0);

  // the manifest carries the mesh's own facts, and claims nothing else
  const m = JSON.parse(
    readFileSync(path.join(folder, `${assetId}.json`), "utf8"),
  ) as Record<string, unknown>;
  expect(m.payload).toBe("3d");
  expect(m.resolution).toBeNull();            // a GLB has no resolution
  expect(m.replayable).toMatch(/^close:/);    // the third replay tier
  expect((m.model as { files: unknown[] }).files).toEqual([]);
  const mesh = m.mesh as { turntable: string; engine: string };
  expect(mesh.turntable).toBe(`${assetId}.turntable.webp`);
  expect(mesh.engine).toBeTruthy();

  rmSync(folder, { recursive: true, force: true });
});
