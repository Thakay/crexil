// Foundation + shell proof: the typed client renders server
// data end to end — generated paths → openapi-fetch → react-query → screen.
// Fetch is stubbed (no msw, by dependency discipline); the client
// resolves globalThis.fetch lazily for exactly this reason (api/client.ts).
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { useUi } from "./state/ui";

const workspace = {
  id: "w1",
  name: "Sword of Dawn",
  slug: "sword-of-dawn",
  viewport: {},
  dials: {},
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
  nodes: 0,      // the list row's own counts
  promoted: 0,
};

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = input.url;
      if (url.includes("/api/workspaces"))
        return Response.json([workspace]);
      if (url.includes("/api/costs"))
        return Response.json({
          today_usd: 0.18,
          month_usd: 12.4,
          day: "2026-08-25",
          month: "2026-08",
          tz_minutes: new URL(url).searchParams.get("tz"),
        });
      if (url.includes("/api/health"))
        return Response.json({ build: { id: "test", time: "t" } });
      throw new Error(`unstubbed fetch: ${url}`);
    }),
  );
}

// RTL auto-cleanup needs vitest globals (off here) — clean up explicitly.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.removeItem("cockpit.localOnly");
  useUi.setState({ localOnly: false });
});

test("shell: sidebar lists workspaces through the typed client", async () => {
  stubApi();
  location.hash = "#/w";
  render(<App />);

  await screen.findByText("Sword of Dawn");
  expect(screen.getByText("sword-of-dawn")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /Sword of Dawn/ }),
  ).toHaveAttribute("href", "#/w/w1");
  // no workspace selected → the canvas region says what to do next
  expect(screen.getByText(/Pick a workspace/)).toBeInTheDocument();
});

test("shell: sidebar collapses via the top-bar toggle", async () => {
  stubApi();
  location.hash = "#/w";
  const user = userEvent.setup();
  render(<App />);

  await screen.findByRole("navigation", { name: "Workspaces" });
  await user.click(screen.getByRole("button", { name: "Toggle sidebar" }));
  expect(
    screen.queryByRole("navigation", { name: "Workspaces" }),
  ).not.toBeInTheDocument();
});

test("money corner: charged totals on every screen, in the browser's clock; LOCAL ONLY is a real switch that persists", async () => {
  stubApi();
  location.hash = "#/w"; // Home — no workspace, the corner is still there
  const user = userEvent.setup();
  render(<App />);

  const totals = await screen.findByRole("status", { name: "Charged totals" });
  await waitFor(() => expect(totals).toHaveTextContent("TODAY $0.18 · AUG $12.40"));
  // the request carried the browser's offset, east-positive
  const calls = (fetch as unknown as { mock: { calls: [Request][] } }).mock.calls;
  const costsUrl = calls.map(([r]) => r.url).find((u) => u.includes("/api/costs"));
  expect(costsUrl).toContain(`tz=${-new Date().getTimezoneOffset()}`);

  const sw = screen.getByRole("switch", { name: "LOCAL ONLY" });
  expect(sw).toHaveAttribute("aria-checked", "false");
  await user.click(sw);
  expect(sw).toHaveAttribute("aria-checked", "true");
  expect(useUi.getState().localOnly).toBe(true);
  expect(localStorage.getItem("cockpit.localOnly")).toBe("1"); // survives a reload
  await user.click(sw);
  expect(sw).toHaveAttribute("aria-checked", "false");
  expect(localStorage.getItem("cockpit.localOnly")).toBe("0");
});
