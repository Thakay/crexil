// The tray as a surface: what it lists, what the filters do to the
// held button's promise, restore's route, and that purge fires only after
// a real hold (a tap is nothing). Fetch is stubbed (no msw); the
// Playwright spec proves it against the real server.
import { afterEach, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TrayNode } from "../../types";
import { useUi } from "../state/ui";
import { Tray } from "./Tray";

function entry(over: Partial<TrayNode> & { id: string }): TrayNode {
  return {
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "image",
    verb: "run",
    prompt: "a rusty sword",
    engine: "z-image-turbo",
    seed: 42,
    duration_ms: 4000,
    committed_usd: null,
    charged_usd: null,
    set_aside: true,
    x: null,
    y: null,
    created_at: "2026-08-22T00:00:00Z",
    parents: [],
    turn_idx: 0,
    slot_pos: 2,
    bytes: 2048,
    children: 0,
    ...over,
  };
}

const A = entry({ id: "a", children: 1 });
const B = entry({
  id: "b",
  prompt: "a knight in armour",
  engine: "flux2-klein-4b",
  slot_pos: 0,
  bytes: 1024 * 1024,
});

type Post = { path: string; body: unknown };

function stub(tray: TrayNode[]) {
  const posts: Post[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (input.method === "POST") {
        const body = path.endsWith("/purge") ? await input.json() : null;
        posts.push({ path, body });
        if (path.endsWith("/purge"))
          return Response.json({
            purged: (body as { ids: string[] }).ids.length,
            freed_bytes: 4096,
            children: 1,
            seq: 9,
          });
        return Response.json({});
      }
      if (path === "/api/workspaces/w1/tray")
        return Response.json({
          nodes: tray,
          bytes: tray.reduce((t, n) => t + n.bytes, 0),
        });
      return Response.json({}, { status: 404 });
    }),
  );
  return posts;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Tray workspaceId="w1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useUi.setState({ trayOpen: false, lightboxNodeId: null });
});

const hold = () =>
  screen.getByRole("button", {
    name: "Hold to purge the set-aside takes shown",
  });

test("lists the set-aside takes with their facts; filters narrow the purge to what's shown", async () => {
  stub([A, B]);
  const user = userEvent.setup();
  mount();
  expect(await screen.findByText("SET ASIDE · 2")).toBeInTheDocument();
  expect(screen.getByText("0c · Z-IMAGE-TURBO")).toBeInTheDocument();
  expect(screen.getByText("0a · FLUX2-KLEIN-4B")).toBeInTheDocument();
  expect(screen.getByText("S 42 · 1 CHILD")).toBeInTheDocument();
  expect(hold()).toHaveTextContent("PURGE SET-ASIDE · 2 NODES · FREES 1.0 MB");
  expect(
    screen.getByText(/One child loses its parent image/),
  ).toBeInTheDocument();

  await user.type(screen.getByLabelText("Search set-aside prompts"), "knight");
  expect(screen.queryByText("0c · Z-IMAGE-TURBO")).toBeNull();
  expect(hold()).toHaveTextContent("PURGE 1 NODE SHOWN OF 2 · FREES 1.0 MB");
  expect(screen.getByText(/Nothing else points at these/)).toBeInTheDocument();

  await user.clear(screen.getByLabelText("Search set-aside prompts"));
  await user.selectOptions(
    screen.getByLabelText("Filter by engine"),
    "z-image-turbo",
  );
  expect(screen.queryByText("0a · FLUX2-KLEIN-4B")).toBeNull();
  expect(hold()).toHaveTextContent("PURGE 1 NODE SHOWN OF 2 · FREES 2 KB");

  await user.type(screen.getByLabelText("Search set-aside prompts"), "zzz");
  expect(
    screen.getByText(/No set-aside take matches — clear the search to see all 2/),
  ).toBeInTheDocument();
  expect(hold()).toBeDisabled();
});

test("restore posts its route; a thumb opens the lightbox", async () => {
  const posts = stub([A]);
  const user = userEvent.setup();
  mount();
  await screen.findByText("0c · Z-IMAGE-TURBO");
  await user.click(screen.getByRole("button", { name: "RESTORE" }));
  await waitFor(() =>
    expect(posts.map((p) => p.path)).toEqual(["/api/nodes/a/restore"]),
  );
  await user.click(
    screen.getByRole("button", { name: "Open 0c · Z-IMAGE-TURBO full-res" }),
  );
  expect(useUi.getState().lightboxNodeId).toBe("a");
});

test("purge fires only after a hold — a tap or an early release is nothing (law 7)", async () => {
  const posts = stub([A, B]);
  mount();
  await screen.findByText("SET ASIDE · 2");
  vi.useFakeTimers();
  const btn = hold();
  // a tap: down, up — nothing
  fireEvent.pointerDown(btn, { button: 0 });
  fireEvent.pointerUp(btn);
  act(() => void vi.advanceTimersByTime(2000));
  expect(posts).toEqual([]);
  // released early — nothing
  fireEvent.pointerDown(btn, { button: 0 });
  act(() => void vi.advanceTimersByTime(500));
  fireEvent.pointerLeave(btn);
  act(() => void vi.advanceTimersByTime(2000));
  expect(posts).toEqual([]);
  // the hold — fires once, with exactly the ids shown
  fireEvent.pointerDown(btn, { button: 0 });
  expect(btn).toHaveAttribute("data-holding", "true");
  act(() => void vi.advanceTimersByTime(950));
  vi.useRealTimers();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({
    path: "/api/workspaces/w1/purge",
    body: { ids: ["a", "b"] },
  });
  expect(await screen.findByText("Purged 2 · freed 4 KB")).toBeInTheDocument();
});

test("the keyboard holds too: Space down, wait, up", async () => {
  const posts = stub([A]);
  mount();
  await screen.findByText("SET ASIDE · 1");
  vi.useFakeTimers();
  const btn = hold();
  fireEvent.keyDown(btn, { key: " " });
  fireEvent.keyUp(btn, { key: " " }); // a press, not a hold
  act(() => void vi.advanceTimersByTime(2000));
  expect(posts).toEqual([]);
  fireEvent.keyDown(btn, { key: "Enter" });
  fireEvent.keyDown(btn, { key: "Enter", repeat: true }); // auto-repeat is ignored
  act(() => void vi.advanceTimersByTime(950));
  vi.useRealTimers();
  await waitFor(() => expect(posts).toHaveLength(1));
});

test("an empty tray says what fills it; a server refusal is shown in its words", async () => {
  stub([]);
  mount();
  expect(
    await screen.findByText(/Nothing set aside\. ⌫ on a grid cell/),
  ).toBeInTheDocument();
  expect(hold()).toBeDisabled();
});
