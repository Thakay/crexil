// Promote against the real server.
//
// The whole loop: the verb row's hairline, the plinth's facts before the
// hold, a click that spends nothing, the hold that writes a real PNG +
// manifest into a real folder, and the permanent mark the take wears after.
// Then D5 on the live face — the folder yanked out from under the cockpit,
// refused in its two halves, re-pointed from the plinth, and the same take
// promoted afterwards.
import { execSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const REPO = path.resolve(HERE, "../../..");
const DB = path.join(REPO, ".staging/e2e-harness/cockpit-v4.sqlite");
const PROMOTED = path.join(REPO, ".staging/e2e-promote");

function seed(): Seed {
  const name = `promote-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset small`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const plinth = (page: Page) => page.getByRole("region", { name: "Promote" });
const hold = (page: Page) =>
  plinth(page).getByRole("button", { name: /Hold to hold to promote/i });

/** Press and hold past HOLD_MS. The verb row shrinks-and-scrolls when the
 *  dock is over-full, and raw mouse events don't auto-scroll the way
 *  `.click()` does — so bring the button into view before taking its box,
 *  or the press lands on whatever is at those coordinates instead. */
async function holdIt(page: Page): Promise<void> {
  await hold(page).scrollIntoViewIfNeeded();
  const box = (await hold(page).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(1200);
  await page.mouse.up();
}

// The re-point is server state in `meta` and this harness serves every spec
// (workers: 1). Put it back on .env's default whatever this spec did.
test.afterEach(async ({ request }) => {
  await request.post("/api/promote-target", { data: { path: "" } });
});

test("promote: the words before the hold, the hold, and the mark after", async ({
  page,
  request,
}) => {
  const s = seed();
  const [a] = s.node_ids;
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);

  // select the take: the row retargets, and the two verbs that can't be
  // undone are below their own line
  await page.locator(`.react-flow__node`).first().click();
  await expect(page.getByText("CAN'T BE UNDONE")).toBeVisible(T);
  const chip = page.getByRole("button", { name: /^PROMOTE/ });
  await expect(chip).toHaveAttribute("aria-disabled", "false");

  // the plinth: where it goes, what lands there, what it cost, and the
  // sentence that says it can't be taken back
  await chip.click();
  await expect(plinth(page)).toBeVisible(T);
  await expect(plinth(page)).toContainText(PROMOTED);
  await expect(plinth(page)).toContainText("2 FILES");
  await expect(plinth(page)).toContainText(
    "This take cost no money — it ran on your own card.",
  );
  await expect(plinth(page)).toContainText("cannot be un-happened");
  await expect(plinth(page)).toContainText("HOLD TO PROMOTE");

  // looking writes nothing, and neither does a click — it is a HOLD
  const before = existsSync(PROMOTED) ? readdirSync(PROMOTED) : [];
  await hold(page).click();
  await page.waitForTimeout(300);
  expect(existsSync(PROMOTED) ? readdirSync(PROMOTED) : []).toEqual(before);

  await holdIt(page);
  await expect(plinth(page)).toContainText("PROMOTED", T);

  // a real pair on disk, under the workspace slug
  const ws = (await (
    await request.get(`/api/workspaces/${s.workspace_id}`)
  ).json()) as { slug: string };
  const folder = path.join(PROMOTED, ws.slug);
  const files = readdirSync(folder);
  const png = files.find((f) => f.endsWith(".png"))!;
  const assetId = png.replace(/\.png$/, "");
  expect(files).toContain(`${assetId}.json`);
  expect(files.filter((f) => f.startsWith(".promote-"))).toEqual([]);

  // the manifest is the full record, not a stub
  const m = JSON.parse(
    readFileSync(path.join(folder, `${assetId}.json`), "utf8"),
  ) as Record<string, unknown>;
  expect(m.manifest_version).toBe(4);
  expect(m.node_id).toBe(a);
  expect((m.workspace as { slug: string }).slug).toBe(ws.slug);
  expect(m.parent_node_ids).toEqual([]);
  expect(m.local_only).toBe(true);
  expect(m.replayable).toMatch(/^exact:/);
  expect((m.cost as Record<string, unknown>).charged_usd).toBeNull();
  // (the graph's own verbatim carry is test_api_v4_promote.py's — the seed
  // fixture writes landed rows directly, without one)

  // and the take wears the mark, on the canvas, permanently
  await expect(
    page.locator(".react-flow__node").first().getByText("PROMOTED"),
  ).toBeVisible(T);
  await page.reload();
  await expect(
    page.locator(".react-flow__node").first().getByText("PROMOTED"),
  ).toBeVisible(T);
});

test("the fence is on screen, and refuses a folder off the drive", async ({
  page,
  request,
}) => {
  const s = seed();
  // the harness's promote root sits under .staging; its parent stands in
  // for the drive, so a sibling folder is legal and anything above is not
  const sibling = path.join(REPO, ".staging/e2e-promote-sibling");
  mkdirSync(sibling, { recursive: true });
  const offDrive = mkdtempSync(path.join(tmpdir(), "promote-off-"));

  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").first().click();
  await page.getByRole("button", { name: /^PROMOTE/ }).click();
  await expect(plinth(page)).toBeVisible(T);

  // the fence is PRINTED, not just enforced — a fence you can't see is a
  // locked door
  await plinth(page).getByRole("button", { name: "CHANGE" }).click();
  await expect(plinth(page)).toContainText("Anywhere on", T);
  await expect(plinth(page)).toContainText(path.dirname(PROMOTED));

  // a real, existing, writable folder OFF the drive: refused in words.
  // Without the fence /tmp and /data would be accepted, and a promotion
  // under either is erased by a rebuild.
  const field = plinth(page).getByLabel("OR TYPE ITS PATH");
  await field.fill(offDrive);
  await plinth(page).getByRole("button", { name: "USE THIS FOLDER" }).click();
  await expect(plinth(page)).toContainText("is outside", T);
  await expect(plinth(page)).toContainText(
    "only promote onto the drive it was given",
  );
  expect(readdirSync(offDrive)).toEqual([]);
  // and it was never remembered
  const t1 = (await (
    await request.get("/api/promote-target")
  ).json()) as { path: string };
  expect(t1.path).toBe(PROMOTED);

  // a sibling ON the drive is legal, and a promotion lands there
  await field.fill(sibling);
  await plinth(page).getByRole("button", { name: "USE THIS FOLDER" }).click();
  await expect(plinth(page)).toContainText(sibling, T);
  await holdIt(page);
  await expect(plinth(page)).toContainText("PROMOTED", T);
  const ws = (await (
    await request.get(`/api/workspaces/${s.workspace_id}`)
  ).json()) as { slug: string };
  expect(readdirSync(path.join(sibling, ws.slug)).some((f) => f.endsWith(".png"))).toBe(
    true,
  );
  rmSync(sibling, { recursive: true, force: true });
  rmSync(offDrive, { recursive: true, force: true });
});

test("the picker walks the drive and hands back a handle, never a path", async ({
  page,
  request,
}) => {
  const s = seed();
  const sibling = path.join(REPO, ".staging/e2e-promote-sibling");
  mkdirSync(sibling, { recursive: true });

  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").first().click();
  await page.getByRole("button", { name: /^PROMOTE/ }).click();
  await expect(plinth(page)).toBeVisible(T);
  await plinth(page).getByRole("button", { name: "CHANGE" }).click();

  // the browser opens where the cockpit points today
  const browser = plinth(page).getByRole("group", { name: "Browse the drive" });
  await expect(browser).toBeVisible(T);
  await expect(browser).toContainText(PROMOTED, T);

  // UP is the drive — the fence root — and from a lone root there is no
  // further up; every folder on it is a row, the sibling among them
  await browser.getByRole("button", { name: "UP" }).click();
  await expect(browser).toContainText(path.dirname(PROMOTED), T);
  await expect(browser.getByRole("button", { name: "UP" })).toBeDisabled(T);
  await browser.getByRole("button", { name: "e2e-promote-sibling" }).click();
  await expect(browser).toContainText(sibling, T);

  // choosing hands the server's own handle back: the destination changes,
  // nothing was typed, and the field is still empty
  await browser.getByRole("button", { name: "POINT THE COCKPIT HERE" }).click();
  await expect(plinth(page)).toContainText(`${sibling} —`, T);
  await expect(plinth(page)).toContainText("Pointed here from the cockpit");
  const t = (await (
    await request.get("/api/promote-target")
  ).json()) as { path: string; source: string };
  expect(t.path).toBe(sibling);
  expect(t.source).toBe("set-here");

  // and a promotion lands there, under the workspace slug
  await holdIt(page);
  await expect(plinth(page)).toContainText("PROMOTED", T);
  const ws = (await (
    await request.get(`/api/workspaces/${s.workspace_id}`)
  ).json()) as { slug: string };
  expect(readdirSync(path.join(sibling, ws.slug)).some((f) => f.endsWith(".png"))).toBe(
    true,
  );
  rmSync(sibling, { recursive: true, force: true });
});

test("D5: the folder goes away, the plinth says so and offers to re-point", async ({
  page,
  request,
}) => {
  const s = seed();
  // point the cockpit at a folder that exists, then take it away — the
  // real D5 (a rename on the Windows side), reproduced from this side.
  // It lives ON the drive (the fence), which is also truer to the case:
  // the folder that vanishes is one the artist had been promoting into.
  const temp = mkdtempSync(path.join(path.dirname(PROMOTED), "promote-d5-"));
  const r = await request.post("/api/promote-target", {
    data: { path: temp },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  rmSync(temp, { recursive: true, force: true });

  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").first().click();
  await page.getByRole("button", { name: /^PROMOTE/ }).click();

  // the two halves, and NO hold at all — there is nothing to hold to
  await expect(plinth(page)).toContainText("isn't there", T);
  await expect(plinth(page)).toContainText(
    "point the cockpit at another folder",
  );
  await expect(hold(page)).toHaveCount(0);
  // nothing was created where the folder used to be (never guess)
  expect(existsSync(temp)).toBe(false);

  // re-point from the face, and the same take promotes
  await plinth(page).getByLabel("OR TYPE ITS PATH").fill(
    PROMOTED,
  );
  await plinth(page).getByRole("button", { name: "USE THIS FOLDER" }).click();
  // fixed: the two D5 halves are gone, the hold is back, and the re-point
  // control folds away with them — reachable again behind CHANGE
  await expect(plinth(page)).toContainText("HOLD TO PROMOTE", T);
  await expect(plinth(page)).not.toContainText("isn't there");
  await expect(
    plinth(page).getByRole("button", { name: "USE THIS FOLDER" }),
  ).toHaveCount(0);
  await plinth(page).getByRole("button", { name: "CHANGE" }).click();
  await expect(plinth(page)).toContainText("BACK TO .ENV");
  await plinth(page).getByRole("button", { name: "KEEP THIS ONE" }).click();

  await holdIt(page);
  await expect(plinth(page)).toContainText("PROMOTED", T);
});
