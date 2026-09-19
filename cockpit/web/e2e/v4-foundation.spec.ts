// Foundation + shell, against the real server + throwaway v4 DB
// (playwright webServer). The harness DB accumulates across runs — workspace
// names must be unique per run.
import { expect, test } from "@playwright/test";

test("tokens, self-hosted Plex, deep link, workspace round-trip", async ({
  page,
}) => {
  await page.goto("/#/w"); // deep link through StaticFiles(html=True)

  // the canvas background proves tokens.css is live
  await expect(page.locator("body")).toHaveCSS(
    "background-color",
    "rgb(8, 9, 11)",
  );

  // both self-hosted Plex faces load from the bundle. fonts.load (not
  // fonts.ready) — ready can resolve before render-initiated loads begin in
  // headless chromium; load() forces the fetch and proves the woff2 parse.
  const fonts = await page.evaluate(async () => {
    await Promise.all([
      document.fonts.load('13px "IBM Plex Sans"'),
      document.fonts.load('10px "IBM Plex Mono"'),
    ]);
    return {
      sans: document.fonts.check('13px "IBM Plex Sans"'),
      mono: document.fonts.check('10px "IBM Plex Mono"'),
    };
  });
  expect(fonts.sans).toBe(true);
  expect(fonts.mono).toBe(true);

  // create → sidebar list → open in shell, through the typed client
  const name = `e2e-${Date.now()}`;
  await page.getByLabel("New workspace").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  const row = page.getByRole("link", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page).toHaveURL(/#\/w\/.+/);

  // the workstation shell around the opened workspace
  await expect(page.getByRole("heading", { name })).toBeVisible(); // top bar
  await expect(page.getByText(/turn 0 · 0 nodes/i)).toBeVisible();
  await expect(page.getByText(/Empty canvas/i)).toBeVisible();
  await expect(page.getByText(/One GPU — local takes run one at a time/)).toBeVisible(); // law 13, queue strip
});

test("rename moves the name everywhere and leaves the slug alone", async ({
  page,
}) => {
  // The slug is the promote folder: an asset renamed after its first
  // promotion must not strand the files already under the old one, so this
  // rides the real PATCH rather than a stub.
  await page.goto("/#/w");
  const name = `e2e-rename-${Date.now()}`;
  await page.getByLabel("New workspace").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("link", { name: new RegExp(name) }).click();
  await expect(page).toHaveURL(/#\/w\/.+/);

  const slug = await page
    .getByRole("link", { name: new RegExp(name) })
    .locator("span")
    .first()
    .textContent();

  await page.getByRole("button", { name: "Rename" }).click();
  const renamed = `${name}-mk2`;
  await page.getByLabel(`Rename ${name}`).fill(renamed);
  await page.getByRole("button", { name: "Save" }).click();

  // the row AND the top bar carry the new name; the slug is untouched
  await expect(page.getByRole("heading", { name: renamed })).toBeVisible();
  await expect(page.getByText(slug!, { exact: true })).toBeVisible();
});

test("sidebar collapses by keyboard and toggle", async ({ page }) => {
  await page.goto("/#/w");
  const sidebar = page.getByRole("navigation", { name: "Workspaces" });
  await expect(sidebar).toBeVisible();

  await page.keyboard.press("Control+\\");
  await expect(sidebar).not.toBeVisible();

  await page.getByRole("button", { name: "Toggle sidebar" }).click();
  await expect(sidebar).toBeVisible();
});

test("the bare URL is the front door — Home, no hash needed", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("body")).toHaveCSS(
    "background-color",
    "rgb(8, 9, 11)", // tokens.css — the one global stylesheet
  );
  await expect(
    page.getByRole("navigation", { name: "Workspaces" }),
  ).toBeVisible();
  // an address nobody serves still says so, and offers the way back
  await page.evaluate(() => {
    location.hash = "#/nowhere";
  });
  await expect(page.getByText(/Nothing at this address/)).toBeVisible();
  await page.getByRole("link", { name: "Back to workspaces" }).click();
  await expect(page).toHaveURL(/#\/w$/);
  await expect(
    page.getByRole("navigation", { name: "Workspaces" }),
  ).toBeVisible();
});
