// The region surface end to end: EDIT REGION on a landed
// take opens the mask surface over the canvas region (a place, not a
// modal — law 2), drawing declares a region, the four actions carry their
// own words, the precedence line prints the binding `nd3-v1` order (KEEP >
// DELETE > REPLACE > MODIFY), never KEEP > MODIFY > REPLACE, and
// closing keeps the drafts. The fan-out test drives the whole loop:
// draw → step an engine → FAN OUT → the surface KEEPS the canvas region
// (its own turn never auto-opens the grid) with a skeleton pane before any
// work → the harness lands the region take (ONE directive — pass mechanics
// are pytest's) → pick → CONTINUE HERE selects the winner on the canvas.
//
// Source nodes are seeded straight through app.db (the canvas/refine
// specs' route): the surface needs a landed take whose image bytes exist.
// The region turn itself runs live against the harness's region executor.
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

// A drawing surface needs its plate on screen: at the default 720px window
// the canvas region is ~190px tall and the
// stage column scrolls — right for real use, wrong for synthetic mouse
// strokes. The taller window tests the same behaviour without fighting the
// crush; the second test runs the surface at this height too and the app's
// scroll answer stays in the CSS.
test.use({ viewport: { width: 1280, height: 900 } });

const T = { timeout: 10_000 };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(HERE, "../../server");
const DB = path.resolve(HERE, "../../../.staging/e2e-harness/cockpit-v4.sqlite");

function seed(): Seed {
  const name = `regions-${Date.now()}`;
  const out = execSync(
    `uv run python ../../scripts/seed_canvas_fixture.py --db "${DB}" --name "${name}" --preset small`,
    { cwd: SERVER_DIR, encoding: "utf8" },
  );
  return JSON.parse(out.trim().split("\n").pop()!) as Seed;
}

const row = (page: Page) => page.getByRole("region", { name: "Verbs" });
const verb = (page: Page, id: string) =>
  row(page).locator(`[data-verb="${id}"]`);
const surface = (page: Page) =>
  page.getByRole("region", { name: "Edit region" });

/** One brush stroke across the stage (the seeded take is 64×64, shown 1:1).
 *  The 720px window leaves the canvas region short and the stage column
 *  scrolls (Regions.module.css) — bring the plate into the clip first. */
async function stroke(page: Page): Promise<void> {
  await page.locator(".konvajs-content").scrollIntoViewIfNeeded();
  const box = (await page.locator(".konvajs-content").boundingBox())!;
  await page.mouse.move(box.x + 8, box.y + 8);
  await page.mouse.down();
  await page.mouse.move(box.x + 40, box.y + 44, { steps: 4 });
  await page.mouse.up();
}

test("EDIT REGION opens the surface; drawing declares; actions carry their words; drafts survive a close", async ({
  page,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);

  // The chip is live and says its effect — no "not wired" left on it.
  await page.locator(".react-flow__node").first().click();
  await expect(row(page).getByText("Take 0a")).toBeVisible();
  await expect(verb(page, "edit-region")).toHaveAttribute(
    "aria-disabled",
    "false",
  );
  await expect(
    row(page).getByText("mask a part, keep the rest"),
  ).toBeVisible();
  await verb(page, "edit-region").click();

  // The surface, over the canvas region: header, capability truth, the gap.
  await expect(surface(page)).toBeVisible(T);
  await expect(surface(page).getByText("EDIT REGION")).toBeVisible();
  await expect(surface(page).getByText("on Take 0a")).toBeVisible();
  await expect(surface(page).getByText(/CAN MASK/)).toBeVisible(T);
  await expect(
    surface(page).getByText(/REGIONS YOU DRAW STAY WITH THIS TAKE/),
  ).toBeVisible();

  // Draw with no region declared: a region starts itself (modify) and the
  // stroke lands as its ink.
  await expect(
    surface(page).getByText("DRAW A REGION, THEN DECLARE WHAT IT MEANS"),
  ).toBeVisible();
  await expect(page.locator(".konvajs-content")).toBeVisible(T);
  await stroke(page);
  await expect(surface(page).getByText("R1 · MODIFY", { exact: true })).toBeVisible();
  await expect(surface(page).getByText("INK", { exact: true })).toBeVisible();
  // MODIFY carries a prompt of its own, falling back to the turn's.
  await expect(page.getByLabel("R1 prompt")).toBeVisible();

  // A declared REPLACE speaks for itself; KEEP locks and drops the prompt.
  await surface(page).getByRole("button", { name: "+ NEW REGION" }).click();
  await expect(surface(page).getByText("R2 · MODIFY", { exact: true })).toBeVisible();
  await page
    .getByLabel("R2 action")
    .selectOption("replace");
  await expect(surface(page).getByText("R2 · REPLACE", { exact: true })).toBeVisible();
  await page.getByLabel("R2 prompt").fill("clotting blood channel");
  await page.getByLabel("R2 action").selectOption("keep");
  await expect(surface(page).getByText("R2 · KEEP", { exact: true })).toBeVisible();
  await expect(page.getByLabel("R2 prompt")).toHaveCount(0);
  await expect(
    surface(page).getByText("LOCKED · NO PROMPT — it stays exactly as it is"),
  ).toBeVisible();

  // The rule, in the BINDING order (`nd3-v1`). KEEP>MODIFY>REPLACE is a
  // known wrong order and must not ship.
  await expect(
    surface(page).getByText(/KEEP BEATS DELETE BEATS REPLACE BEATS MODIFY/),
  ).toBeVisible();

  // ⌫ belongs to SET ASIDE only while no overlay holds the keys: with the
  // surface up it must set nothing aside — the top-bar count doesn't move.
  const asideBtn = page.getByRole("button", { name: /SET ASIDE · \d+/ });
  const asideBefore = await asideBtn.innerText();
  await page.keyboard.press("Backspace");
  await expect(asideBtn).toHaveText(asideBefore);

  // Esc closes the surface and keeps the drafts; reopening finds them.
  await page.keyboard.press("Escape");
  await expect(surface(page)).toHaveCount(0);
  await expect(asideBtn).toHaveText(asideBefore);
  await verb(page, "edit-region").click();
  await expect(surface(page).getByText("R1 · MODIFY", { exact: true })).toBeVisible(T);
  await expect(surface(page).getByText("R2 · KEEP", { exact: true })).toBeVisible();
  await expect(surface(page).getByText("INK", { exact: true })).toBeVisible();
});

// ── the fan-out loop, live against the harness region executor ────────────

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

// The local lane is serial and process-global (one GPU): a test that dies
// with slots still gated would starve every later test's local takes —
// batch-laws' drain rule, applied to the fan-out's region takes.
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

test("fan out: draw → step SDXL → run → skeleton pane → land → CONTINUE HERE selects the winner", async ({
  page,
  request,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);
  await page.locator(".react-flow__node").first().click();
  await verb(page, "edit-region").click();
  await expect(surface(page)).toBeVisible(T);

  // Draw one region (starts as MODIFY) and give it a prompt.
  await expect(page.locator(".konvajs-content")).toBeVisible(T);
  await stroke(page);
  await expect(
    surface(page).getByText("R1 · MODIFY", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("R1 prompt").fill("a tighter leather wrap");

  // One engine, deterministically: zero every capable row, step SDXL to 1.
  const fan = surface(page).locator('[aria-label="Fan out"]');
  const steppers = fan.getByRole("spinbutton");
  const n = await steppers.count();
  for (let i = 0; i < n; i++) {
    const el = steppers.nth(i);
    if (!(await el.isDisabled())) await el.fill("0");
  }
  await fan
    .getByRole("spinbutton", { name: "Raw · SDXL Juggernaut takes", exact: true })
    .fill("1");
  await expect(
    fan.getByText("1 TAKE · 1 ENGINE · LOCALS IN SERIES, ONE GPU"),
  ).toBeVisible();

  // The incapable stay listed with the reason (badge, never filter) and
  // externals are one structural line.
  await expect(fan.getByText("NO MASK RECIPES COMMITTED").first()).toBeVisible();
  await expect(
    fan.getByText(/EXTERNALS DON'T TAKE REGION TURNS/),
  ).toBeVisible();

  // FAN OUT. The surface KEEPS the canvas region — its own turn must not
  // auto-open the grid — and the pane exists before any work lands (law).
  const go = fan.getByRole("button", { name: "FAN OUT" });
  await expect(go).toBeEnabled(T);
  await go.click();
  const attempts = surface(page).locator('[aria-label="Attempts"]');
  await expect(attempts.getByText("1 ATTEMPT · SAME MASK")).toBeVisible(T);
  await expect(attempts.getByText("RAW-SDXL", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: /Turn( \d+)? grid/ }),
  ).toHaveCount(0);

  // The harness holds the region take as ONE directive; land it.
  await expect
    .poll(
      async () =>
        ((await (await request.get("/api/harness/state")).json()) as {
          waiting: string[];
        }).waiting.length,
      T,
    )
    .toBeGreaterThan(0);
  const st = (await (await request.get("/api/harness/state")).json()) as {
    waiting: string[];
  };
  for (const sid of st.waiting) await resolve(request, sid);

  // The pane lands with its facts; pick it and continue.
  await expect(attempts.getByText(/✓/)).toBeVisible(T);
  await attempts.getByText("RAW-SDXL", { exact: true }).click();
  await expect(attempts.getByText(/CONTINUE FROM RAW-SDXL/)).toBeVisible();
  await attempts.getByRole("button", { name: "CONTINUE HERE" }).click();
  await expect(surface(page)).toHaveCount(0);
  // The winner is the acting-on selection — the verb row names it.
  await expect(row(page).getByText("Take 2a")).toBeVisible(T);
});

test("the grid and the region surface are exclusive — one canvas overlay at a time", async ({
  page,
}) => {
  const s = seed();
  await page.goto(`/#/w/${s.workspace_id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible(T);

  await page.locator(".react-flow__node").first().click();
  await verb(page, "edit-region").click();
  await expect(surface(page)).toBeVisible(T);

  // G summons the grid; the region surface stands down (drafts kept).
  await page.keyboard.press("g");
  await expect(surface(page)).toHaveCount(0);
  await expect(page.getByRole("region", { name: /Turn( \d+)? grid/ })).toBeVisible(T);

  // And back: opening the surface closes the grid.
  await verb(page, "edit-region").click();
  await expect(surface(page)).toBeVisible(T);
  await expect(page.getByRole("region", { name: /Turn( \d+)? grid/ })).toHaveCount(0);
});
