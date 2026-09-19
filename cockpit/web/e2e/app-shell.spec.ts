import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Locator, type Page } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(): Seed {
  const name = `shell-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset small`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

test("API health answers and the shell mounts", async ({ page, request }) => {
  // Degraded is an answer (same shape, 503): the e2e stack points ComfyUI at
  // a closed port by construction (harness mode): health must say so,
  // not lie. The empty-mount failure mode stays guarded via preset_count.
  const health = await request.get("/api/health");
  expect([200, 503]).toContain(health.status());
  const body = (await health.json()) as {
    db: boolean;
    preset_count: number;
  };
  expect(body.db).toBe(true);
  expect(body.preset_count).toBeGreaterThan(0);

  await page.goto("/");
  await expect(page).toHaveTitle("Crexil");
  await expect(page.locator("#root")).not.toBeEmpty();
  // the bare URL mounts the shell itself: no hash, no detour
  await expect(
    page.getByRole("navigation", { name: "Workspaces" }),
  ).toBeVisible();
});

// Resizable islands, through a real pointer: the drag moves
// the edge live and persists nothing until release; the release lands in
// the browser (app-level, shell/layout.ts) and survives a reload; the keys
// and the double-click reset work without a pointer.
const width = async (l: Locator) => Math.round((await l.boundingBox())!.width);
const height = async (l: Locator) => Math.round((await l.boundingBox())!.height);
const layout = (page: Page) =>
  page.evaluate(() => localStorage.getItem("cockpit.layout"));

async function dragBy(page: Page, handle: Locator, dx: number, dy: number) {
  const b = (await handle.boundingBox())!;
  const x = b.x + b.width / 2;
  const y = b.y + b.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 3 });
  await page.mouse.move(x + dx, y + dy, { steps: 3 });
}

test("sidebar width: drag, persist, reload, keys, reset", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.getByRole("navigation", { name: "Workspaces" });
  const handle = page.getByRole("separator", { name: "Sidebar width" });
  await expect(sidebar).toBeVisible();
  expect(await width(sidebar)).toBe(232); // the default geometry

  await dragBy(page, handle, 88, 0);
  expect(await width(sidebar)).toBe(320); // the layout follows mid-drag …
  expect(await layout(page)).toBeNull(); // … and nothing is committed yet
  await page.mouse.up();
  expect(await layout(page)).toBe('{"sidebar":320}');

  await page.reload();
  await expect(sidebar).toBeVisible();
  expect(await width(sidebar)).toBe(320);

  await handle.focus();
  await page.keyboard.press("ArrowLeft"); // the seam moves the way the arrow points
  expect(await width(sidebar)).toBe(304);

  await handle.dblclick();
  expect(await width(sidebar)).toBe(232);
  expect(await layout(page)).toBeNull();
});

test("tray width and dock cap: the dock shrinks under its cap and the canvas takes the room", async ({
  page,
}) => {
  const s = seed();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();

  // tray: T opens it at its default 248; its handle sits on its left edge
  await page.keyboard.press("t");
  const tray = page.getByRole("complementary", { name: "Set-aside tray" });
  await expect(tray).toBeVisible();
  expect(await width(tray)).toBe(248);
  await dragBy(page, page.getByRole("separator", { name: "Tray width" }), -72, 0);
  await page.mouse.up();
  expect(await width(tray)).toBe(320);

  // dock: uncapped it takes what its content needs; dragging its top edge
  // down caps it, the row and composer scroll beneath, the canvas grows
  const dock = page.getByRole("group", { name: "Dock" });
  const pane = page.locator(".react-flow");
  const dockBefore = await height(dock);
  const paneBefore = await height(pane);
  await dragBy(page, page.getByRole("separator", { name: "Dock height" }), 0, 48);
  await page.mouse.up();
  expect(await height(dock)).toBe(dockBefore - 48);
  // the pane takes the room the dock gave up (±1: sub-pixel box rounding)
  expect(Math.abs((await height(pane)) - (paneBefore + 48))).toBeLessThanOrEqual(1);
  expect(JSON.parse((await layout(page))!)).toEqual({
    tray: 320,
    dock: dockBefore - 48,
  });

  // a click on the seam that never moves changes nothing — the cap is set
  // by a drag, never by a stray press
  await page.getByRole("separator", { name: "Dock height" }).click();
  expect(JSON.parse((await layout(page))!).dock).toBe(dockBefore - 48);

  await page.reload();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  expect(await height(dock)).toBe(dockBefore - 48);
});
