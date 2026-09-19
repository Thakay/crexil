// The sidebar as the workspace-management surface. There is no home
// screen, so this list is where a workspace is found, read at
// a glance and renamed. Order and counts come from the server; the filter is
// a client-side pass over the list already in hand.
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";

type Row = {
  id: string;
  name: string;
  slug: string;
  nodes: number;
  promoted: number;
};

function row(id: string, name: string, nodes = 0, promoted = 0): Row {
  return { id, name, slug: name.toLowerCase().replace(/\s+/g, "-"),
           nodes, promoted };
}

function stub(rows: Row[], onPatch?: (wid: string, name: string) => void) {
  const state = rows.map((r) => ({
    ...r, viewport: {}, dials: {},
    created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url, "http://x");
      const m = /\/api\/workspaces\/([^/]+)$/.exec(url.pathname);
      if (input.method === "PATCH" && m) {
        const name = (await input.json()).name;
        const hit = state.find((w) => w.id === m[1]);
        if (!hit) return new Response(null, { status: 404 });
        hit.name = name;                 // the slug does NOT follow
        onPatch?.(m[1], name);
        return Response.json(hit);
      }
      if (url.pathname === "/api/workspaces") return Response.json(state);
      return Response.json(url.pathname.endsWith("/costs") ? {} : []);
    }),
  );
}

function mount(activeId: string | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Sidebar activeId={activeId} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a row says what is in the workspace, and an empty one says empty", async () => {
  stub([row("w1", "Hero Sword", 12, 3), row("w2", "Rusty Shield")]);
  mount();

  await screen.findByText("Hero Sword");
  expect(screen.getByText("12 nodes")).toBeInTheDocument();
  expect(screen.getByText("3 promoted")).toBeInTheDocument();
  // a workspace that has never promoted says nothing about it, rather than
  // wearing a "0 promoted" on every row that hasn't got there yet
  expect(screen.getByText("empty")).toBeInTheDocument();
  expect(screen.queryByText("0 promoted")).not.toBeInTheDocument();
});

test("the server's order is the list's order — most recently touched first", async () => {
  stub([row("w2", "Rusty Shield"), row("w1", "Hero Sword")]);
  mount();

  await screen.findByText("Hero Sword");
  const names = screen.getAllByRole("link").map((a) => a.textContent);
  expect(names[0]).toContain("Rusty Shield");
  expect(names[1]).toContain("Hero Sword");
});

test("the filter stays out of the way until the list outgrows the panel", async () => {
  stub([row("w1", "Hero Sword"), row("w2", "Rusty Shield")]);
  const { unmount } = mount();
  await screen.findByText("Hero Sword");
  expect(screen.queryByLabelText("Find")).not.toBeInTheDocument();
  unmount();
  cleanup();

  stub(Array.from({ length: 8 }, (_, i) => row(`w${i}`, `Asset ${i}`)));
  mount();
  expect(await screen.findByLabelText("Find")).toBeInTheDocument();
});

test("filtering hides rows without losing them, and says so when nothing matches", async () => {
  const user = userEvent.setup();
  stub([
    ...Array.from({ length: 7 }, (_, i) => row(`w${i}`, `Asset ${i}`)),
    row("sw", "Hero Sword"),
  ]);
  mount();

  const find = await screen.findByLabelText("Find");
  await user.type(find, "hero");
  expect(screen.getByText("Hero Sword")).toBeInTheDocument();
  expect(screen.queryByText("Asset 3")).not.toBeInTheDocument();

  // the slug is a handle the artist knows — it matches too
  await user.clear(find);
  await user.type(find, "asset-3");
  expect(screen.getByText("Asset 3")).toBeInTheDocument();

  await user.clear(find);
  await user.type(find, "zzz");
  expect(screen.getByText(/All 8 are still there/)).toBeInTheDocument();
});

test("rename moves the name and never the slug, and only the open workspace offers it", async () => {
  const user = userEvent.setup();
  const patched: [string, string][] = [];
  stub([row("w1", "Hero Sword", 4), row("w2", "Rusty Shield")],
       (wid, name) => patched.push([wid, name]));
  mount("w1");

  await screen.findByText("Hero Sword");
  // one rename control, on the open row — not one per row
  expect(screen.getAllByRole("button", { name: "Rename" })).toHaveLength(1);

  await user.click(screen.getByRole("button", { name: "Rename" }));
  const field = screen.getByLabelText("Rename Hero Sword");
  await user.clear(field);
  await user.type(field, "Hero Sword mk2");
  await user.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(patched).toEqual([["w1", "Hero Sword mk2"]]));
  await screen.findByText("Hero Sword mk2");
  expect(screen.getByText("hero-sword")).toBeInTheDocument();
});

test("cancelling a rename leaves the name alone and sends nothing", async () => {
  const user = userEvent.setup();
  const patched: [string, string][] = [];
  stub([row("w1", "Hero Sword")], (wid, name) => patched.push([wid, name]));
  mount("w1");

  await user.click(await screen.findByRole("button", { name: "Rename" }));
  const field = screen.getByLabelText("Rename Hero Sword");
  await user.clear(field);
  await user.type(field, "typo{Escape}");

  expect(patched).toEqual([]);
  expect(screen.getByText("Hero Sword")).toBeInTheDocument();
  // Escape closed the editor rather than leaving a half-typed draft behind
  expect(screen.getByRole("button", { name: "Rename" })).toBeInTheDocument();
});
