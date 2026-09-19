// Capability honesty on the roster (law 12),
// against the real server and the committed capability matrix
// (`configs/engines.json`, served as authored by GET /api/engines).
// The claim under test: every roster row wears its engine's flags with
// their verification state — verified channels plain, anything unverified
// saying so — and nothing is guessed: the external rows say their seed
// truth is unobserved, and the matrix's own states decide the words.
// Nothing is run, so nothing gates the serial lane.
import { expect, test, type APIRequestContext } from "@playwright/test";

type Cap = { value: unknown; state: "verified" | "unverified" };
type Matrix = {
  engines: { id: string; capabilities: Record<string, Cap> }[];
  external: { defaults: Record<string, Cap> };
};

const T = { timeout: 10_000 };

async function createWorkspace(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const r = await request.post("/api/workspaces", { data: { name } });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { id: string }).id;
}

test("every roster row wears its engine's capability badges with their verification state", async ({
  page,
  request,
}) => {
  // The words follow the matrix as authored today — a flag that flips as
  // real use proves it changes the line here too, on purpose.
  const m = (await (await request.get("/api/engines")).json()) as Matrix;
  const sdxl = m.engines.find((e) => e.id === "sdxl-juggernaut-xi")!;
  expect(sdxl.capabilities.mask).toMatchObject({ value: true, state: "verified" });
  expect(sdxl.capabilities.maxResolution).toMatchObject({ value: 2048, state: "unverified" });
  expect(sdxl.capabilities.tokenLimit).toMatchObject({ value: 77, state: "unverified" });
  const klein = m.engines.find((e) => e.id === "flux2-klein-4b")!;
  // klein's ref-edit + mask channels are verified, with dated evidence
  expect(klein.capabilities.imageRef).toMatchObject({ value: true, state: "verified" });
  expect(klein.capabilities.mask).toMatchObject({ value: true, state: "verified" });
  expect(m.external.defaults.seed).toMatchObject({ value: false, state: "unverified" });

  const wid = await createWorkspace(request, `e2e-capability-${Date.now()}`);
  await page.goto(`/#/w/${wid}`);
  await expect(
    page.getByText("Press N — or right-click — to summon"),
  ).toBeVisible(T);
  await page.locator(".react-flow").click({ button: "right", position: { x: 300, y: 200 } });
  await page.getByText("Image · new root").click();
  await expect(page.getByText("New image · root", { exact: true })).toBeVisible(T);

  const roster = page.getByRole("group", { name: "Local engines" });
  // SDXL: three verified channels plain, two ceilings the matrix hasn't
  // exercised — each wearing the word
  await expect(
    roster.getByText("SEED · REF · MASK · UP TO 2048 UNVERIFIED · 77 TOKENS UNVERIFIED"),
  ).toBeVisible(T);
  // klein (Raw + Prop Sheet share the engine) wears its verified
  // channels; Turbo's verified absences stay off the row, not "NO REF"
  await expect(
    roster.getByText("SEED · REF · MASK · UP TO 2048 UNVERIFIED", { exact: true }),
  ).toHaveCount(2);
  await expect(
    roster.getByText("SEED · REF · MASK · UP TO 1024 UNVERIFIED", { exact: true }),
  ).toBeVisible();
  await expect(
    roster.getByText("SEED · UP TO 2048 UNVERIFIED", { exact: true }),
  ).toBeVisible();
  await expect(
    roster.getByText("SEED · REF · UP TO 2048 UNVERIFIED", { exact: true }),
  ).toBeVisible();
  // no row says a verified thing the matrix doesn't
  await expect(roster.getByText(/NO REF|NO MASK|NO CAPABILITY RECORD/)).toHaveCount(0);

  // the external rows: each model's seed truth is its providers.json pin
  // (`supports_seed`) at the lane's authored state — unobserved either
  // way — so a pinned seed reads SEED UNVERIFIED, the rest NO SEED
  // UNVERIFIED, and no row is silent
  const cat = (await (await request.get("/api/providers")).json()) as {
    models: { supports_seed: boolean }[];
  };
  const pinnedSeed = cat.models.filter((x) => x.supports_seed).length;
  const ext = page.getByRole("group", { name: "External models" });
  await expect(ext.getByRole("spinbutton", { name: "GPT Image 2 takes" })).toBeVisible(T);
  await expect(ext.getByRole("spinbutton")).toHaveCount(cat.models.length);
  await expect(ext.getByText("NO SEED UNVERIFIED")).toHaveCount(cat.models.length - pinnedSeed);
  await expect(ext.getByText("SEED UNVERIFIED", { exact: true })).toHaveCount(pinnedSeed);
  await expect(ext.getByText(/^SEED$/)).toHaveCount(0);

  // Esc spends nothing
  await page.keyboard.press("Escape");
  await expect(page.getByText("New image · root", { exact: true })).toHaveCount(0);
});
