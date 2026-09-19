// The live queue strip: server-sent lane labels, the running take
// with cancel, benches with unbench — and an honest empty/unreachable line.
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QueueStrip } from "./QueueStrip";

const RUNNING = {
  id: "s-run",
  turn_id: "t1",
  turn_idx: 4,
  workspace_id: "w1",
  engine: "z-image-turbo",
  status: "running",
  attempt: 1,
  position: 0,
  elapsed_ms: 12_000,
  committed_usd: null,
  charged_usd: null,
  cancel_requested: false,
  awaiting: false,
  bench: null,
  created_at: "2026-08-11T00:00:00+00:00",
};

function queue(over: object = {}) {
  return {
    seq: 9,
    now: "2026-08-11T00:00:12+00:00",
    depth: 3,
    lanes: {
      local: {
        label: "LOCAL · SEQUENTIAL, ONE GPU",
        depth: 3,
        slots: [
          RUNNING,
          { ...RUNNING, id: "s-q1", status: "queued", position: 1, elapsed_ms: null },
          { ...RUNNING, id: "s-q2", status: "queued", position: 2, elapsed_ms: null },
        ],
      },
      external: {
        label: "EXTERNAL · CONCURRENT, PRICED",
        concurrency: 4,
        active: 1,
        slots: [
          {
            ...RUNNING,
            id: "s-ext",
            engine: "openai/gpt-image-1",
            committed_usd: 0.06,
          },
        ],
      },
    },
    held: [],
    late: [],
    benches: [], checks: [], retries: [], gpu: { reading: null, rechecking: false, manual_hold: false },
    ...over,
  };
}

function stub(payload: object) {
  const posts: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (input.method === "POST") {
        posts.push({
          path: new URL(input.url).pathname,
          body: await input.json().catch(() => null),
        });
        return Response.json({ ok: true });
      }
      return Response.json(payload);
    }),
  );
  return posts;
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <QueueStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("busy strip: server lane labels, running take, queued-behind count", async () => {
  stub(queue());
  mount();
  expect(
    await screen.findByText("LOCAL · SEQUENTIAL, ONE GPU"),
  ).toBeInTheDocument();
  expect(screen.getByText("EXTERNAL · CONCURRENT, PRICED")).toBeInTheDocument();
  expect(screen.getByText(/Z-IMAGE-TURBO · T4 · 12S/)).toBeInTheDocument();
  expect(
    screen.getByText(/2 queued behind — locals take turns/),
  ).toBeInTheDocument();
  expect(screen.getByText("1 in flight of 1")).toBeInTheDocument();
});

test("cancel on the running take hits the slot-cancel route", async () => {
  const posts = stub(queue());
  const user = userEvent.setup();
  mount();
  await user.click(
    await screen.findByRole("button", {
      name: "Cancel the running local take",
    }),
  );
  expect(posts.map((p) => p.path)).toEqual(["/api/slots/s-run/cancel"]);
});

test("a bench shows scope + cause honestly and offers unbench", async () => {
  const posts = stub(
    queue({
      benches: [{ scope: "engine:z-image-turbo", cause: "engine_error" }],
    }),
  );
  const user = userEvent.setup();
  mount();
  expect(
    await screen.findByText("ENGINE:Z-IMAGE-TURBO BENCHED"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "UNBENCH" }));
  expect(posts).toEqual([
    {
      path: "/api/unbench",
      body: { scope: "engine:z-image-turbo" },
    },
  ]);
});

test("idle strip states the one-GPU truth instead of pretending", async () => {
  stub({
    seq: 0,
    now: "2026-08-11T00:00:00+00:00",
    depth: 0,
    lanes: {
      local: { label: "LOCAL · SEQUENTIAL, ONE GPU", depth: 0, slots: [] },
      external: {
        label: "EXTERNAL · CONCURRENT, PRICED",
        concurrency: 4,
        active: 0,
        slots: [],
      },
    },
    held: [],
    late: [],
    benches: [], checks: [], retries: [], gpu: { reading: null, rechecking: false, manual_hold: false },
  });
  mount();
  expect(
    await screen.findByText(/Nothing running\. One GPU — local takes run one/),
  ).toBeInTheDocument();
});
