// The provenance record against the real server, on every
// mount: the grid's turn header (the words and the recipe, once above the
// cells), the ACTING ON head (all three rows), the image lightbox's caption
// (beside the pixels). The honest-blank case is an imported node; the
// plural case is a shift-selection. P recalls the words into a root arm
// through `armed.prompt`, its second caller. Read-only otherwise: nothing
// here edits a prompt, and the turn's recipe is worn as the turn's.
//
// Same seeding route as the canvas spec: the harness executors point at
// closed ports, so landed nodes are written through app.db first — the
// `provenance` preset carries REAL turn dials (the run route's own shape).
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

type Seed = { workspace_id: string; node_ids: string[] };

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");
const SHOTS = path.resolve(HERE, "../../../.staging/e2e-harness/shots");

function seed(): Seed {
  const name = `provenance-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset provenance`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const PROMPT =
  "a rusty longsword resting on a stone table, worn leather grip, overcast light from a high window, shallow depth of field";
const REFINE_PROMPT = "the same sword, the blade chipped and the grip re-wrapped";

const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const record = (page: Page, scope: "take" | "turn") =>
  page.locator(`[aria-label="Provenance"][data-scope="${scope}"]`);
const card = (page: Page, id: string) =>
  page.locator(`.react-flow__node[data-id="${id}"]`);

async function openCanvas(page: Page, wid: string) {
  await page.goto(`/#/w/${wid}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
}

test("the ACTING ON head carries the record: the words, the turn's recipe as the turn's, this take", async ({
  page,
}) => {
  const s = seed();
  const [a, , , d, a2, imp] = s.node_ids;
  await openCanvas(page, s.workspace_id);
  await page.setViewportSize({ width: 1440, height: 900 });

  // acting on nothing: no record
  await expect(row(page).getByText("ACTING ON NOTHING")).toBeVisible();
  await expect(record(page, "take")).toHaveCount(0);

  // the root take: every row, the recipe printed as raws and worn as the turn's
  await card(page, a).click();
  await expect(row(page).getByText("Take 0a")).toBeVisible();
  const rec = record(page, "take");
  await expect(rec.getByText(PROMPT)).toBeVisible();
  await expect(
    rec.getByText("Z-IMAGE-TURBO · 1024×1024 · STEPS 9 · CFG 1 · SAMPLER EULER · SCHEDULER SIMPLE"),
  ).toBeVisible();
  await expect(
    rec.getByText("FLUX2-KLEIN-4B · 1024×1024 · STEPS 4 · GUIDANCE 1 · SAMPLER EULER"),
  ).toBeVisible();
  await expect(rec.getByText("SEED 41 · SPREAD BY SLOT · FROM TEXT · A ROOT")).toBeVisible();
  await expect(rec.getByText(/THE TURN'S DIALS — a swept or retried cell/)).toBeVisible();
  await expect(rec.getByText("SEED 41 · Z-IMAGE-TURBO · 10.2 S · RUN · NO SPEND")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "provenance-head-take.png") });

  // the refined child: its lineage names the parent by its take
  await card(page, d).click();
  await expect(row(page).getByText("Take 1a")).toBeVisible();
  await expect(rec.getByText(REFINE_PROMPT)).toBeVisible();
  await expect(rec.getByText(/DENOISE 0.55/)).toBeVisible();
  await expect(rec.getByText("SEEDS ROLLED PER TAKE · REFINE OF TAKE 0A")).toBeVisible();
  await expect(
    rec.getByText("SEED 44 · Z-IMAGE-TURBO · 8.4 S · REFINE OF TAKE 0A · 1024×1024 · NO SPEND"),
  ).toBeVisible();

  // the second take in cell a says which take it is
  await card(page, a2).click();
  await expect(rec.getByText(/TAKE 2 IN THIS CELL/)).toBeVisible();

  // the imported node: the honest blank, in the facts line's own register,
  // and no door — there are no words to recall
  await card(page, imp).click();
  await expect(row(page).getByText("Imported node", { exact: true })).toBeVisible();
  await expect(rec.getByText(/^IMPORTED · NO PROMPT — this node came in from a file/)).toBeVisible();
  await expect(rec.getByText(/^NO TURN — an imported node ran nothing/)).toBeVisible();
  await expect(rec.getByText("NO SEED · IMPORTED · NO SPEND")).toBeVisible();
  await expect(rec.getByRole("button", { name: "USE THIS PROMPT · P" })).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "provenance-head-imported.png") });

  // three selected across two turns: the plural answer, never the first's
  await card(page, a).click();
  await card(page, d).click({ modifiers: ["Shift"] });
  await card(page, a2).click({ modifiers: ["Shift"] });
  await expect(row(page).getByText("3 nodes")).toBeVisible();
  await expect(rec.getByText("3 SELECTED · 2 DIFFERENT PROMPTS — pick one to read its words")).toBeVisible();
  await expect(rec.getByText("2 TURNS AMONG THE 3 SELECTED — pick one to read its recipe")).toBeVisible();
  await expect(rec.getByText("3 TAKES · SEEDS 41 · 44 · 45")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "provenance-head-multi.png") });
});

test("P recalls the words into a root arm — the composer opens carrying them, nothing spent", async ({
  page,
}) => {
  const s = seed();
  const [a] = s.node_ids;
  await openCanvas(page, s.workspace_id);
  await card(page, a).click();
  await expect(record(page, "take").getByText(PROMPT)).toBeVisible();

  await page.keyboard.press("p");
  await expect(page.getByText("New image · root", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue(PROMPT);
  await page.keyboard.press("Escape");
  await expect(page.getByText("New image · root", { exact: true })).toHaveCount(0);

  // the chip is the same door
  await record(page, "take").getByRole("button", { name: "USE THIS PROMPT · P" }).click();
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue(PROMPT);
});

test("the grid's turn header carries the turn's words and recipe once above the cells; the lightbox captions the take beside the pixels", async ({
  page,
}) => {
  const s = seed();
  const [a] = s.node_ids;
  await openCanvas(page, s.workspace_id);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.keyboard.press("g"); // a resting turn doesn't auto-open its grid
  const grid = page.getByRole("region", { name: "Turn 1 grid" });
  await expect(grid).toBeVisible();
  const turnRec = record(page, "turn");
  await expect(turnRec.getByText(REFINE_PROMPT)).toBeVisible();
  await expect(turnRec.getByText("THIS TURN'S DIALS, AS SENT")).toBeVisible();
  await expect(turnRec.getByText("SEEDS ROLLED PER TAKE · REFINE OF TAKE 0A")).toBeVisible();
  // turn rows only — a take's facts live on its cell and in the head
  await expect(turnRec.locator('[data-row="THIS TAKE"]')).toHaveCount(0);
  // the earlier turn, by the pager
  await page.getByRole("button", { name: "Earlier turn" }).click();
  await expect(page.getByRole("region", { name: "Turn 0 grid" })).toBeVisible();
  await expect(turnRec.getByText(PROMPT)).toBeVisible();
  await expect(turnRec.getByText("SEED 41 · SPREAD BY SLOT · FROM TEXT · A ROOT")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "provenance-grid-header.png") });
  // P from the grid arms the same root run
  await page.keyboard.press("p");
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue(PROMPT);
  await page.keyboard.press("Escape"); // disarms; the grid stays
  await page.keyboard.press("Escape"); // closes the grid
  await expect(grid).toHaveCount(0);

  // the lightbox: the record beside the pixels, its own clicks its own
  await card(page, a).click();
  await page.keyboard.press("v");
  const box = page.getByRole("dialog", { name: "Full-resolution take" });
  await expect(box).toBeVisible();
  await expect(box.getByText("TAKE 0A · FULL RES")).toBeVisible();
  const boxRec = box.locator('[aria-label="Provenance"]');
  await expect(boxRec.getByText(PROMPT)).toBeVisible();
  await expect(boxRec.getByText("SEED 41 · Z-IMAGE-TURBO · 10.2 S · RUN · NO SPEND")).toBeVisible();
  await expect(box.getByText("FULL RES · CLICK OR ESC CLOSES")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "provenance-lightbox.png") });
  await boxRec.getByText(PROMPT).click(); // content, not backdrop
  await expect(box).toBeVisible();
  // its recall closes the box first, then arms
  await boxRec.getByRole("button", { name: "USE THIS PROMPT · P" }).click();
  await expect(box).toHaveCount(0);
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue(PROMPT);
});
