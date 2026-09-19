// The canvas against the real server + seeded landed nodes. The
// harness can't land real turns (executors point at closed ports), so
// scripts/seed_canvas_fixture.py writes through app.db.Database before the
// page loads. Harness DB accumulates across runs — names stay unique.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(preset: "small" | "perf200"): Seed {
  const name = `canvas-${preset}-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset ${preset}`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

test("cards render with thumbs, typed edge + honest legend, select/Esc", async ({
  page,
}) => {
  const s = seed("small");
  await page.goto(`/#/w/${s.workspace_id}`);

  // three of the four landed nodes draw — the x/y-NULL one takes the
  // client fallback layout; the set-aside one is in the tray, not on the
  // canvas (a place, not a delete: the top bar counts it)
  const nodes = page.locator(".react-flow__node");
  await expect(nodes).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 1" }),
  ).toBeVisible();

  // cards carry thumbnails from the /thumb route, never full-res
  const img = page.locator('.react-flow__node img[src*="/thumb"]').first();
  await expect(img).toBeVisible();
  expect(
    await img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
  ).toBeGreaterThan(0);

  // one lineage edge (a → d), and the legend lists only what's on canvas
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  const legend = page
    .locator(".react-flow__panel")
    .filter({ hasText: "Edge types" });
  await expect(legend.getByText("IMAGE")).toBeVisible();
  await expect(legend.getByText("MESH")).toHaveCount(0);

  // click selects (ACTING ON pill); Esc clears.
  // Scoped to the card: the dock carries its own ACTING ON chip.
  const pill = page.locator(".react-flow__node").getByText("ACTING ON");
  await nodes.first().click();
  await expect(pill).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(pill).toHaveCount(0);
});

test("drag persists and the viewport restores across reload", async ({
  page,
}) => {
  const s = seed("small");
  await page.goto(`/#/w/${s.workspace_id}`);
  const node = page.locator(`.react-flow__node[data-id="${s.node_ids[0]}"]`);
  const ref = page.locator(`.react-flow__node[data-id="${s.node_ids[3]}"]`);
  await expect(node).toBeVisible();

  // pin the viewport first: pan a little on empty pane so PATCH /view lands
  // (the reload below then has a saved viewport to restore)
  const pane = await page.locator(".react-flow__pane").boundingBox();
  const px = pane!.x + pane!.width - 60;
  const py = pane!.y + pane!.height - 60;
  await page.mouse.move(px, py);
  await page.mouse.down();
  await page.mouse.move(px + 30, py + 20, { steps: 4 });
  await page.mouse.up();
  await page.waitForResponse(
    (r) => r.url().includes("/view") && r.request().method() === "PATCH",
  );

  const before = await node.boundingBox();
  const refBefore = await ref.boundingBox();
  await node.hover();
  await page.mouse.down();
  await page.mouse.move(
    before!.x + before!.width / 2 + 120,
    before!.y + before!.height / 2 + 60,
    { steps: 8 },
  );
  await page.mouse.up();
  await page.waitForResponse(
    (r) => r.url().includes(`/nodes/${s.node_ids[0]}/position`) && r.ok(),
  );
  // what the artist sees post-drag (RF's drag threshold eats the first mouse
  // step, so the exact delta is RF's business — persistence is ours)
  const dragged = await node.boundingBox();
  expect(dragged!.x - before!.x).toBeGreaterThan(80); // it really moved

  await page.reload();
  const after = await page
    .locator(`.react-flow__node[data-id="${s.node_ids[0]}"]`)
    .boundingBox();
  const refAfter = await ref.boundingBox();
  // the dragged position survived the reload (POST → DB → list roundtrip)…
  expect(Math.abs(after!.x - dragged!.x)).toBeLessThan(2);
  expect(Math.abs(after!.y - dragged!.y)).toBeLessThan(2);
  // …and the untouched node sits exactly where it was: viewport restored
  expect(Math.abs(refAfter!.x - refBefore!.x)).toBeLessThan(2);
  expect(Math.abs(refAfter!.y - refBefore!.y)).toBeLessThan(2);
});
