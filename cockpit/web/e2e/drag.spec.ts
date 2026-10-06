// The drag layer against the real server, failure case F5 included.
// One gesture, three verbs by destination: a lift lights every target
// with its effect in words; the canvas takes a move and the dock follows;
// another card pairs the two in the dock (no anchor channels yet, and
// the row says so); the tray sets aside — its zone while closed, its open
// column while open. A release over nothing puts the card back and sends
// nothing. ⌫ is the tray destination's keystroke on the selected card.
//
// Same seeding route as canvas.spec: landed nodes written through app.db.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { DB } from "./paths";

type Seed = { workspace_id: string; node_ids: string[] };

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");

function seed(): Seed {
  const name = `drag-small-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset small`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const card = (page: Page, id: string) =>
  page.locator(`.react-flow__node[data-id="${id}"]`);
const ring = (page: Page, id: string) => card(page, id).locator("[data-drag]");
const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const verb = (page: Page, id: string) =>
  row(page).locator(`[data-verb="${id}"]`);
const legend = (page: Page) => page.locator('[aria-label="Drop destinations"]');
const zone = (page: Page) => page.locator('[aria-label="Set-aside drop zone"]');

async function open(page: Page, wid: string) {
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
}

// Geometry note: the verb row grows with the selection (one node ≈ 230px,
// two ≈ 260px of chips), the canvas region shrinks to match, and in this
// 720px-tall window only the TOP row of cards (a, d) stays inside the pane
// — onlyRenderVisibleElements unmounts b the moment two cards are acting.
// So the drops below lift a or d, and "nothing set aside" is proven by the
// top-bar count + the request log, never by a card count.

// Every POST the page sends — the proof that a no-op sent nothing and a
// verb sent exactly its route. (PATCH /view rides auto-pan and is noise.)
function posts(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") seen.push(new URL(r.url()).pathname);
  });
  return seen;
}

// Lift a card: press on it and move past RF's drag threshold, so the
// destinations light before the pointer goes anywhere. Returns the box
// the card was lifted from.
async function lift(page: Page, id: string) {
  const box = (await card(page, id).boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 6, cy + 6, { steps: 3 });
  await expect(legend(page)).toBeVisible();
  return box;
}

async function centre(page: Page, sel: ReturnType<Page["locator"]>) {
  const b = (await sel.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

test("a lift lights the three destinations in words; the canvas takes the move and the dock follows", async ({
  page,
}) => {
  const s = seed();
  const [a, b] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  const before = await lift(page, a);
  // the words: every destination, its effect — and the honest one
  for (const line of [
    "EMPTY CANVAS",
    "the dock follows",
    "ONTO ANOTHER NODE",
    "anchor channels aren't built yet",
    "SET-ASIDE TRAY",
    "off the canvas, still in the record — no confirmation needed",
  ])
    await expect(legend(page)).toContainText(line);
  // the rings: this card lifted, every other card a target, the tray's
  // zone up at the canvas edge (the tray is closed)
  await expect(ring(page, a)).toHaveAttribute("data-drag", "lifted");
  await expect(ring(page, b)).toHaveAttribute("data-drag", "target");
  await expect(zone(page)).toBeVisible();
  await expect(zone(page)).toContainText("SET-ASIDE TRAY");

  // drop on empty pane, well inside it (away from RF's auto-pan band)
  const pane = (await page.locator(".react-flow__pane").boundingBox())!;
  await page.mouse.move(pane.x + 90, pane.y + pane.height - 90, { steps: 8 });
  await expect(legend(page).locator('[data-kind="canvas"]')).toHaveAttribute(
    "data-over",
    "true",
  );
  await page.mouse.up();
  await page.waitForResponse(
    (r) => r.url().includes(`/nodes/${a}/position`) && r.ok(),
  );
  await expect(legend(page)).toHaveCount(0);
  await expect(zone(page)).toHaveCount(0);
  const after = (await card(page, a).boundingBox())!;
  expect(
    Math.abs(after.x - before.x) + Math.abs(after.y - before.y),
  ).toBeGreaterThan(100); // it really moved
  await expect(row(page).getByText("Take 0a")).toBeVisible(); // the dock followed
  expect(sent.filter((p) => !p.endsWith("/position"))).toEqual([]);
});

test("onto another card: the card goes back, the two pair in the dock, and the row says what a second parent needs", async ({
  page,
}) => {
  const s = seed();
  const [a, , , d] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  const before = await lift(page, d);
  const aBefore = (await card(page, a).boundingBox())!;
  const to = await centre(page, card(page, a));
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await expect(ring(page, a)).toHaveAttribute("data-drag", "over");
  await expect(legend(page).locator('[data-kind="node"]')).toHaveAttribute(
    "data-over",
    "true",
  );
  await page.mouse.up();

  // back where it was lifted from — relative to the target, which never
  // moves, so a viewport nudge can't fake a move either way (selection
  // grows a card from its top-left corner: x/y hold)
  await expect(legend(page)).toHaveCount(0);
  const after = (await card(page, d).boundingBox())!;
  const aAfter = (await card(page, a).boundingBox())!;
  expect(Math.abs(after.x - aAfter.x - (before.x - aBefore.x))).toBeLessThan(2);
  expect(Math.abs(after.y - aAfter.y - (before.y - aBefore.y))).toBeLessThan(2);
  // the two-parent state: both acting, the one-source reason stated
  await expect(row(page).getByText("2 nodes")).toBeVisible();
  await expect(
    row(page).getByText(/IMAGE · 2 SELECTED · PARENT ORDER AS SELECTED/),
  ).toBeVisible();
  await expect(page.locator(".react-flow__node").getByText("ACTING ON")).toHaveCount(2);
  await expect(verb(page, "refine")).toContainText("one source image per refine");
  // lineage untouched, and nothing left the browser
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  expect(sent).toEqual([]);
});

test("onto the tray: set aside — the zone while closed, the open column while open", async ({
  page,
}) => {
  const s = seed();
  const [a, b] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  await lift(page, a);
  const to = await centre(page, zone(page));
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await expect(zone(page)).toHaveAttribute("data-over", "true");
  await expect(legend(page).locator('[data-kind="tray"]')).toHaveAttribute(
    "data-over",
    "true",
  );
  await page.mouse.up();
  await expect(card(page, a)).toHaveCount(0, T);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 2" }),
  ).toBeVisible();
  expect(sent).toEqual([`/api/nodes/${a}/set-aside`]); // no position POST

  // tray open: the column is the destination, the zone stands down
  await page.keyboard.press("t");
  const tray = page.getByRole("complementary", { name: "Set-aside tray" });
  await expect(tray).toBeVisible();
  await lift(page, b);
  await expect(zone(page)).toHaveCount(0);
  await expect(tray).toHaveAttribute("data-drag", "target");
  const onTray = await centre(page, tray.getByText("SET ASIDE · 2"));
  await page.mouse.move(onTray.x, onTray.y, { steps: 10 });
  await expect(tray).toHaveAttribute("data-drag", "over");
  await page.mouse.up();
  await expect(card(page, b)).toHaveCount(0, T);
  await expect(tray.locator(`[data-node-id="${b}"]`)).toBeVisible();
  await expect(tray.getByText("SET ASIDE · 3")).toBeVisible();
});

test("released over nothing is a clean no-op (F5): back where it was, nothing sent", async ({
  page,
}) => {
  const s = seed();
  const [a, , , d] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  const before = await lift(page, d);
  const aBefore = (await card(page, a).boundingBox())!;
  const to = await centre(page, row(page)); // the dock — no destination
  await page.mouse.move(to.x, to.y, { steps: 6 });
  await expect(legend(page).locator("[data-over]")).toHaveCount(0);
  await page.mouse.up();

  await expect(legend(page)).toHaveCount(0);
  const after = (await card(page, d).boundingBox())!;
  const aAfter = (await card(page, a).boundingBox())!;
  expect(Math.abs(after.x - aAfter.x - (before.x - aBefore.x))).toBeLessThan(2);
  expect(Math.abs(after.y - aAfter.y - (before.y - aBefore.y))).toBeLessThan(2);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 1" }),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(sent).toEqual([]);
});

test("⌫ on the selected card is the tray's keystroke — and takes a whole selection", async ({
  page,
}) => {
  const s = seed();
  const [a, b, , d] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  // nothing selected: nothing happens
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(200);
  expect(sent).toEqual([]);

  await card(page, d).click();
  await expect(row(page).getByText("Take 1a")).toBeVisible();
  await expect(verb(page, "set-aside")).toContainText("⌫");
  await page.keyboard.press("Backspace");
  await expect(card(page, d)).toHaveCount(0, T);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 2" }),
  ).toBeVisible();
  expect(sent).toEqual([`/api/nodes/${d}/set-aside`]);

  // two selected: the chip is live for both, and the key fires one
  // request per node — refusals would be per node, never per batch
  await card(page, a).click();
  await card(page, b).click({ modifiers: ["Shift"] });
  await expect(row(page).getByText("2 nodes")).toBeVisible();
  await expect(verb(page, "set-aside")).toContainText(
    "out of the way, not deleted",
  );
  await page.keyboard.press("Backspace");
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 4" }),
  ).toBeVisible(T);
  expect(sent.slice(1).sort()).toEqual(
    [a, b].sort().map((id) => `/api/nodes/${id}/set-aside`),
  );
});

test("⇧drag sweeps a rubber band, and the tray takes the whole lift", async ({
  page,
}) => {
  const s = seed();
  const [a, , , d] = s.node_ids;
  await open(page, s.workspace_id);
  const sent = posts(page);

  // sweep a thin band through the top row, from empty pane left of a into
  // d: partial mode, touching is catching, and b on the row below stays
  // out. Every pointer position keeps >40px from the pane's edges — inside
  // that band RF auto-pans the viewport, and the cards slide out from
  // under the box while the pointer holds still.
  const aBox = (await card(page, a).boundingBox())!;
  const dBox = (await card(page, d).boundingBox())!;
  await page.keyboard.down("Shift");
  await expect(page.locator(".react-flow__pane.selection")).toBeVisible();
  await page.mouse.move(aBox.x - 40, aBox.y + 22);
  await page.mouse.down();
  await page.mouse.move(dBox.x + dBox.width / 2, aBox.y + 44, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(row(page).getByText("2 nodes")).toBeVisible();
  await expect(row(page).getByText("2 SELECTED · PARENT ORDER AS SELECTED")).toBeVisible();

  // lift one of them: the whole selection rides, the legend counts it,
  // the tray is live for all of it — pairing stays one at a time
  await lift(page, a);
  await expect(legend(page)).toContainText("LIFTED 2 CARDS");
  await expect(legend(page)).toContainText(
    "all 2 off the canvas, still in the record",
  );
  await expect(legend(page)).toContainText(
    "hands over one at a time — 2 are lifted",
  );
  const to = await centre(page, zone(page));
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await expect(zone(page)).toHaveAttribute("data-over", "true");
  await page.mouse.up();

  await expect(card(page, a)).toHaveCount(0, T);
  await expect(card(page, d)).toHaveCount(0, T);
  await expect(
    page.getByRole("button", { name: "SET ASIDE · 3" }),
  ).toBeVisible();
  expect(sent.sort()).toEqual(
    [a, d].sort().map((id) => `/api/nodes/${id}/set-aside`),
  ); // no position POST — a tray drop is a verb, not a move
});
