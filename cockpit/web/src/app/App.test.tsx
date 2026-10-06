// Foundation + shell proof: the typed client renders server
// data end to end — generated paths → openapi-fetch → react-query → screen.
// Fetch is stubbed (no msw, by dependency discipline); the client
// resolves globalThis.fetch lazily for exactly this reason (api/client.ts).
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { frontDoor, parseHash, type Route } from "./routes";
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
  vi.unstubAllEnvs();
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

// Routes: one parser serves every build; only the bare URL depends on the
// build's front door.
const surfaces: [string, Route][] = [
  ["#/w", { view: "workspaces" }],
  ["#/w/w1", { view: "workspace", id: "w1" }],
  ["#/projects", { view: "projects" }],
  ["#/projects/", { view: "projects" }],
  ["#/projects?material=m1", { view: "projects" }],
  ["#/projects/p1", { view: "project", id: "p1" }],
  ["#/projects/p1?draft=d1", { view: "project", id: "p1" }],
  ["#/operations", { view: "operations" }],
  ["#/operations?project=p1&import=1", { view: "operations" }],
  ["#/connections", { view: "connections" }],
  ["#/connections?project=p1", { view: "connections" }],
  ["#/nowhere", { view: "unknown" }],
  ["#/projectsx", { view: "unknown" }],
  ["#/projects/p1/extra", { view: "unknown" }],
  ["#/connectionsx", { view: "unknown" }],
];

test.each(["workspaces", "projects"] as const)(
  "routes: every surface parses the same way when the front door is %s",
  (door) => {
    for (const [hash, route] of surfaces) expect(parseHash(hash, door)).toEqual(route);
    for (const bare of ["", "#", "#/"]) expect(parseHash(bare, door)).toEqual({ view: door });
  },
);

test("routes: a build chooses its front door; a preview opens on projects", () => {
  expect(frontDoor({})).toBe("workspaces");
  expect(frontDoor({ VITE_CREXIL_PREVIEW: "1" })).toBe("projects");
  expect(frontDoor({ VITE_CREXIL_FRONT_DOOR: "projects" })).toBe("projects");
  expect(
    frontDoor({ VITE_CREXIL_PREVIEW: "1", VITE_CREXIL_FRONT_DOOR: "workspaces" }),
  ).toBe("workspaces");
  expect(frontDoor({ VITE_CREXIL_FRONT_DOOR: "elsewhere" })).toBe("workspaces");
});

// The project surface's own reads answer quietly; the legacy shell keeps
// stubApi's answers.
function stubSurfaces() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = input.url;
      if (url.includes("/api/workspaces")) return Response.json([workspace]);
      if (url.includes("/api/costs"))
        return Response.json({ today_usd: 0, month_usd: 0, day: "2026-10-05", month: "2026-10", tz_minutes: "0" });
      if (url.includes("/api/health")) return Response.json({ build: { id: "test", time: "t" } });
      if (url.includes("/api/crexil/setup"))
        return Response.json({
          preview: false,
          comfyui_url: "http://127.0.0.1:8188",
          comfyui: { ok: false, error: "Runtime is not running." },
          openrouter: { configured: false, source: null, masked: null, env_overrides: false, probe: { configured: false, ok: false } },
        });
      if (url.includes("/api/crexil/projects")) return Response.json([]);
      return Response.json({ detail: "not stubbed" }, { status: 404 });
    }),
  );
}

const projectSurface = () => screen.findByText(/^Crexil · /);
// The legacy page's own footer, present whatever the sidebar state.
const legacyShell = () => screen.findByText(/^bundle dev/);

test("routes: connections, operations and projects open the project surface in a normal build", async () => {
  stubSurfaces();
  location.hash = "#/connections?project=p1";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Keep working while setup is incomplete." }),
  ).toBeInTheDocument();
  cleanup();

  for (const hash of ["#/operations", "#/projects"]) {
    location.hash = hash;
    render(<App />);
    await projectSurface();
    expect(screen.queryByText(/Nothing at this address/)).not.toBeInTheDocument();
    cleanup();
  }
});

test("routes: a normal build keeps the legacy Home at the bare URL and sends strays back to it", async () => {
  stubSurfaces();
  location.hash = "";
  render(<App />);
  await legacyShell();
  expect(screen.queryByText(/^Crexil · /)).not.toBeInTheDocument();
  cleanup();

  location.hash = "#/nowhere";
  render(<App />);
  expect(screen.getByText(/Nothing at this address/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to workspaces" })).toHaveAttribute("href", "#/w");
});

test("routes: a preview build opens on projects and still reaches the legacy canvas", async () => {
  vi.stubEnv("VITE_CREXIL_PREVIEW", "1");
  stubSurfaces();
  location.hash = "";
  render(<App />);
  await projectSurface();
  cleanup();

  location.hash = "#/w";
  render(<App />);
  await legacyShell();
  expect(screen.queryByText(/^Crexil · /)).not.toBeInTheDocument();
  cleanup();

  location.hash = "#/nowhere";
  render(<App />);
  expect(screen.getByRole("link", { name: "Back to projects" })).toHaveAttribute("href", "#/projects");
});

test("routes: a normal build can choose the project surface as its front door", async () => {
  vi.stubEnv("VITE_CREXIL_FRONT_DOOR", "projects");
  stubSurfaces();
  location.hash = "";
  render(<App />);
  await projectSurface();
});
