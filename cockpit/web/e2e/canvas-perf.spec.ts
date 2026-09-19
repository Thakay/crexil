// Performance budget: onlyRenderVisibleElements is ON and actually culls. The
// seed pins the workspace viewport at zoom 1 (no fitView), so a 220×180
// world grid of 200 cards leaves only the top-left handful on screen.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

test("200 landed nodes: the API serves all, the canvas culls to the viewport", async ({
  page,
  request,
}) => {
  const name = `canvas-perf200-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset perf200`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  const s = JSON.parse(out.trim().split("\n").pop()!) as Seed;
  expect(s.node_ids.length).toBe(200);

  const api = await request.get(`/api/workspaces/${s.workspace_id}/nodes`);
  expect(api.ok()).toBe(true);
  expect((await api.json()).length).toBe(200);

  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  // culling: far fewer DOM cards than data rows (zoom 1, pinned viewport)
  await expect
    .poll(async () => page.locator(".react-flow__node").count(), {
      timeout: 5_000,
    })
    .toBeLessThan(150);
  expect(await page.locator(".react-flow__node").count()).toBeGreaterThan(0);
});
