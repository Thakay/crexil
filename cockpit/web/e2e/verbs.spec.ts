// The verb row against the real server. Selection is the whole
// targeting state: click retargets, shift-click adds a second parent,
// Esc drops back to root verbs. The row never filters (law 12) — a verb for
// another payload drops to NOT FOR THIS NODE with its reason, and a verb the
// cockpit can't fire yet stays in the row, dashed, saying why.
//
// Same seeding route as the canvas spec: the harness server's executors point
// at closed ports, so landed nodes are written through app.db first.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(preset: "small" | "verbs"): Seed {
  const name = `verbs-${preset}-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset ${preset}`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const verb = (page: Page, id: string) =>
  row(page).locator(`[data-verb="${id}"]`);

// The grid auto-opens over the canvas when a workspace has a live turn; these
// workspaces are all landed, but G is the documented toggle either way.
async function openCanvas(page: Page, wid: string) {
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
}

test("click retargets, shift-click adds a second parent, Esc acts on nothing", async ({
  page,
}) => {
  const s = seed("small");
  await openCanvas(page, s.workspace_id);

  // acting on nothing: root verbs, and the row says so
  await expect(row(page).getByText("ACTING ON NOTHING")).toBeVisible();
  await expect(verb(page, "new-root")).toBeVisible();

  const nodes = page.locator(".react-flow__node");
  await nodes.first().click();
  await expect(row(page).getByText("Take 0a")).toBeVisible();
  await expect(
    row(page).getByText(/IMAGE · Z-IMAGE-TURBO · SEEDED/),
  ).toBeVisible();

  // a second parent, one verb — and the single-node verbs say why they can't
  await nodes.nth(1).click({ modifiers: ["Shift"] });
  await expect(row(page).getByText("2 nodes")).toBeVisible();
  await expect(verb(page, "reroll")).toContainText(
    "acts on one node — 2 are selected",
  );

  await page.keyboard.press("Escape");
  await expect(row(page).getByText("ACTING ON NOTHING")).toBeVisible();
  await expect(verb(page, "new-root")).toBeVisible();
});

test("the row never filters: wrong payload is grouped, unwired verbs state why", async ({
  page,
}) => {
  const s = seed("verbs");
  await openCanvas(page, s.workspace_id);
  const [imageId, meshId] = s.node_ids;

  await page.locator(`.react-flow__node[data-id="${imageId}"]`).click();
  // for this node, but not yet buildable — visible, dashed, reason in words
  // (refine is live; upscale has no committed recipe)
  await expect(verb(page, "upscale")).toContainText("not wired yet");
  await expect(verb(page, "upscale")).toHaveAttribute("aria-disabled", "true");
  await expect(verb(page, "refine")).toContainText("same prompt, new seeds");
  await expect(verb(page, "to-3d")).toBeVisible();
  // the mesh-only verbs (DECIMATE, TEXTURE) stay on screen for an
  // image too: grouped, one cause said once (law 1), never filtered (law 12)
  await expect(row(page).getByText("NOT FOR THIS NODE")).toHaveCount(1);
  await expect(
    row(page).getByText("this node is an image — that verb works on meshes"),
  ).toHaveCount(1);

  // the mesh keeps every image verb on screen — in the dashed group
  await page.locator(`.react-flow__node[data-id="${meshId}"]`).click();
  await expect(row(page).getByText("NOT FOR THIS NODE")).toBeVisible();
  // one cause, one message (law 1) — five refused verbs, the reason once
  await expect(
    row(page).getByText("this node is a mesh — that verb works on images"),
  ).toHaveCount(1);
  await expect(verb(page, "refine")).toBeVisible();
  await expect(verb(page, "edit-region")).toBeVisible();
  await expect(verb(page, "upscale")).toBeVisible();
});

test("live verbs fire: set aside parks a take in the tray, root arms the composer", async ({
  page,
}) => {
  const s = seed("small");
  await openCanvas(page, s.workspace_id);

  // the seeded set-aside node (c) is not a card: it lives in the tray
  // (off the canvas, still in the record; restore is tray.spec's)
  await expect(
    page.locator(`.react-flow__node[data-id="${s.node_ids[2]}"]`),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 1" }),
  ).toBeVisible();

  // SET ASIDE from the row: the card leaves the canvas, the count follows,
  // and PURGE stays in the row pointing at the tray
  const b = page.locator(`.react-flow__node[data-id="${s.node_ids[1]}"]`);
  await b.click();
  await expect(verb(page, "purge")).toContainText("set it aside first");
  await expect(verb(page, "purge")).toHaveAttribute("aria-disabled", "true");
  await verb(page, "set-aside").click();
  await expect(b).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 2" }),
  ).toBeVisible();

  // root verb with nothing selected arms the composer at the canvas centre
  await page.keyboard.press("Escape");
  await expect(row(page).getByText("ACTING ON NOTHING")).toBeVisible();
  await verb(page, "new-root").click();
  await expect(page.getByText("Born where the palette was")).toBeVisible();
});
