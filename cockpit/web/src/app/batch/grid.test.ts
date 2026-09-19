// The law-bearing math of the turn grid, DOM-free.
import { expect, test } from "vitest";
import type { Node, Slot, Turn } from "../../types";
import {
  benchLabel,
  benchNotices,
  benchTurn,
  cancelBreath,
  causeGroups,
  checkLine,
  costLine,
  countsLine,
  deriveCells,
  elapsedLabel,
  frameOf,
  frameWords,
  gridCols,
  isExternalEngine,
  MESH_RUNNING_LINE,
  meshEngineIds,
  passSuffix,
  receipt,
  remedies,
  retryLine,
  stopLines,
  turnActive,
  waitingLine,
  type CellVM,
} from "./grid";

function slot(over: Partial<Slot> & { id: string; pos: number }): Slot {
  return {
    turn_id: "t1",
    engine: "z-image-turbo",
    status: "queued",
    attempt: 0,
    seed: 7,
    committed_usd: null,
    charged_usd: null,
    cause: null,
    detail: null,
    retry_of: null,
    kind: null,
    job_ref: null,
    frame: null,
    facts: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-08-11T00:00:00+00:00",
    ...over,
  };
}

function turn(slots: Slot[]): Turn {
  return {
    id: "t1",
    workspace_id: "w1",
    idx: 3,
    prompt: "a rusty sword",
    dials: {},
    anchor_x: null,
    anchor_y: null,
    created_at: "2026-08-11T00:00:00+00:00",
    slots,
  };
}

function node(over: Partial<Node> & { id: string }): Node {
  return {
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: null,
    take: 1,
    payload: "image",
    verb: "run",
    prompt: null,
    engine: "z-image-turbo",
    seed: 7,
    duration_ms: 4200,
    committed_usd: null,
    charged_usd: null,
    set_aside: false,
    x: null,
    y: null,
    created_at: "2026-08-11T00:00:01+00:00",
    parents: [],
    turn_idx: 3,
    slot_pos: 0,
    ...over,
  };
}

test("gridCols is a pure function of slot count — frozen because N is", () => {
  expect(gridCols(1)).toBe(1);
  expect(gridCols(2)).toBe(2);
  expect(gridCols(4)).toBe(2);
  expect(gridCols(9)).toBe(3);
  expect(gridCols(16)).toBe(4);
});

test("external detection mirrors the server's '/' rule", () => {
  expect(isExternalEngine("z-image-turbo")).toBe(false);
  expect(isExternalEngine("openai/gpt-image-1")).toBe(true);
});

test("deriveCells keeps every slot, in pos order, whatever the status", () => {
  const t = turn([
    slot({ id: "s0", pos: 0, status: "landed", attempt: 1 }),
    slot({ id: "s1", pos: 1, status: "failed", attempt: 1, cause: "engine gave nothing back" }),
    slot({ id: "s2", pos: 2, status: "queued" }),
  ]);
  const cells = deriveCells(t, [node({ id: "n0", slot_id: "s0", take: 1 })]);
  expect(cells.map((c) => c.slotId)).toEqual(["s0", "s1", "s2"]);
  expect(cells[0].nodeId).toBe("n0");
  expect(cells[0].thumb).toBe("/api/nodes/n0/thumb");
  expect(cells[0].label).toBe("3a");
  expect(cells[1].nodeId).toBeNull();
  expect(cells[1].cause).toBe("engine gave nothing back");
});

test("a rerolled slot stops wearing its old take's face", () => {
  // old node was take 1; the slot is on attempt 2 and running again
  const t = turn([
    slot({ id: "s0", pos: 0, status: "running", attempt: 2 }),
  ]);
  const cells = deriveCells(t, [node({ id: "old", slot_id: "s0", take: 1 })]);
  expect(cells[0].nodeId).toBeNull(); // the cell is mid-reroll, not the past
});

test("countsLine states what exists and always says the one-GPU truth", () => {
  const line = countsLine(
    turn([
      slot({ id: "a", pos: 0, status: "landed" }),
      slot({ id: "b", pos: 1, status: "landed" }),
      slot({ id: "c", pos: 2, status: "running" }),
      slot({ id: "d", pos: 3, status: "queued" }),
    ]).slots,
  );
  expect(line).toBe(
    "2 LANDED · 1 RUNNING · 1 QUEUED · ONE GPU, LOCALS TAKE TURNS",
  );
});

test("cancelBreath states landed/queued/paid in one breath", () => {
  const slots = [
    slot({ id: "a", pos: 0, status: "landed" }),
    slot({ id: "b", pos: 1, status: "queued" }),
    slot({ id: "c", pos: 2, status: "queued" }),
    slot({
      id: "d",
      pos: 3,
      status: "running",
      engine: "openai/gpt-image-1",
      committed_usd: 0.06,
    }),
  ];
  expect(cancelBreath(slots)).toBe(
    "1 landed stay · 2 queued drop free · 1 external in flight already paid ($0.06)",
  );
  expect(turnActive(slots)).toBe(true);
  expect(
    turnActive([slot({ id: "a", pos: 0, status: "landed" })]),
  ).toBe(false);
});

test("elapsedLabel reads as seconds, then minutes and seconds", () => {
  expect(elapsedLabel(4000)).toBe("4S");
  expect(elapsedLabel(83_000)).toBe("1M 23S");
});

test("passSuffix rides a region take's running line and stays silent elsewhere", () => {
  expect(passSuffix({ current: 2, total: 4 })).toBe(" · PASS 2 OF 4");
  expect(passSuffix(null)).toBe("");
  expect(passSuffix(undefined)).toBe("");
});

// ── the cell's laws, every state × both lanes ──────────────────────────────

const STATUSES = [
  "queued",
  "running",
  "landed",
  "failed",
  "held",
  "skipped",
  "set_aside",
  "late",
] as const;

const T0 = "2026-08-11T00:00:00+00:00";
const T4 = "2026-08-11T00:00:04+00:00";
const NOW = Date.parse("2026-08-11T00:00:10+00:00");

function cellOf(
  over: Partial<Slot> & { status: Slot["status"] },
  paid = false,
): CellVM {
  const sl = slot({
    id: "s",
    pos: 0,
    engine: paid ? "openai/gpt-image-1" : "z-image-turbo",
    committed_usd: paid ? 0.06 : null,
    ...over,
  });
  return deriveCells(turn([sl]), [])[0];
}

test("law 5: a local failure states GPU seconds and takes ahead — never money", () => {
  const t = turn([
    slot({ id: "a", pos: 0, status: "failed", attempt: 1, started_at: T0, finished_at: T4 }),
    slot({ id: "b", pos: 1, status: "queued" }),
    slot({ id: "c", pos: 2, status: "held" }),
    slot({ id: "d", pos: 3, status: "landed" }),
  ]);
  const cells = deriveCells(t, []);
  expect(costLine(cells[0], t.slots, NOW)).toBe(
    "4s of GPU time spent · 2 takes still queued",
  );
  // alone in its turn: just the seconds; a sub-second failure says so
  const lone = cellOf({ status: "failed", started_at: T0, finished_at: T0 });
  expect(costLine(lone, [], NOW)).toBe("under a second of GPU time spent");
  // still ticking (no finished_at): measured against now
  const open = cellOf({ status: "failed", started_at: T0 });
  expect(costLine(open, [], NOW)).toBe("10s of GPU time spent");
});

test("law 5: a paid failure states the dollars either way — charged, or not", () => {
  expect(
    costLine(cellOf({ status: "failed", charged_usd: 0.04 }, true), [], NOW),
  ).toBe("$0.04 was charged");
  expect(costLine(cellOf({ status: "failed" }, true), [], NOW)).toBe(
    "nothing was charged",
  );
  expect(costLine(cellOf({ status: "skipped" }, true), [], NOW)).toBe(
    "nothing was charged",
  );
  expect(costLine(cellOf({ status: "held" }, true), [], NOW)).toContain(
    "nothing charged while it waits",
  );
  expect(costLine(cellOf({ status: "late" }, true), [], NOW)).toBe(
    "already paid, ~$0.06 — lands here if it still arrives",
  );
});

test("law 5, the invariant: money is named only where money was in play", () => {
  for (const status of STATUSES) {
    const local = costLine(cellOf({ status, started_at: T0 }), [], NOW);
    if (local !== null) {
      expect(local, status).not.toMatch(/\$|charged|paid/);
    }
    const paid = costLine(cellOf({ status, started_at: T0 }, true), [], NOW);
    if (["failed", "skipped", "held", "late"].includes(status))
      expect(paid, status).toMatch(/charged|paid/);
  }
  // no cost line for states that cost nothing yet or are the artist's own
  for (const status of ["queued", "running", "landed", "set_aside"] as const) {
    expect(costLine(cellOf({ status }), [], NOW)).toBeNull();
    expect(costLine(cellOf({ status }, true), [], NOW)).toBeNull();
  }
});

test("law 6: on a paid cell the free remedy comes first, the costed one last with its price, never primary", () => {
  const paidFailed = remedies(cellOf({ status: "failed" }, true), false);
  expect(paidFailed.map((r) => r.id)).toEqual(["run-locally", "reroll"]);
  expect(paidFailed[0]).toMatchObject({ paid: false, primary: true, label: "RUN LOCALLY · FREE" });
  expect(paidFailed[1]).toMatchObject({ paid: true, primary: false, label: "REROLL R · ~$0.06" });
  // skipped paid: same shape; landed paid: set aside first, the paid reroll last
  expect(remedies(cellOf({ status: "skipped" }, true), false).map((r) => r.id)).toEqual(["run-locally", "reroll"]);
  expect(remedies(cellOf({ status: "landed" }, true), false).map((r) => r.id)).toEqual(["set-aside", "reroll"]);
  // a local cell: the free reroll is the primary, and leads the hover bar
  const localFailed = remedies(cellOf({ status: "failed" }), false);
  expect(localFailed).toEqual([
    { id: "reroll", label: "REROLL R", paid: false, primary: true, gated: null },
  ]);
  expect(remedies(cellOf({ status: "landed" }), false).map((r) => r.id)).toEqual(["reroll", "set-aside"]);
  // an unlisted price is said, not hidden
  const unlisted = remedies(cellOf({ status: "failed", committed_usd: null }, true), false);
  expect(unlisted[1].label).toBe("REROLL R · PRICE UNLISTED");
  // under LOCAL ONLY the paid action stays on the face, inert, with the reason
  const gated = remedies(cellOf({ status: "failed" }, true), true);
  expect(gated[1]).toMatchObject({ label: "REROLL · LOCAL ONLY", paid: true, primary: false });
  expect(gated[1].gated).toMatch(/LOCAL ONLY is on/);
});

test("laws 4 + 6, the invariant: every resting-but-unresolved state offers a move; paid ones trail and are never primary", () => {
  for (const status of STATUSES) {
    for (const paid of [false, true]) {
      const list = remedies(cellOf({ status }, paid), false);
      const firstPaid = list.findIndex((r) => r.paid);
      if (firstPaid !== -1)
        expect(list.slice(firstPaid).every((r) => r.paid), `${status} paid tail`).toBe(true);
      expect(list.some((r) => r.paid && r.primary), `${status} paid primary`).toBe(false);
      if (["failed", "skipped", "held", "set_aside"].includes(status))
        expect(list.length, status).toBeGreaterThan(0);
    }
  }
  // late is its own state: it rests for its own arrival and offers nothing (not a failure)
  expect(remedies(cellOf({ status: "late" }, true), false)).toEqual([]);
});

test("law 1: failed cells sharing a cause fold to one line; a lone cause doesn't", () => {
  const t = turn([
    slot({ id: "a", pos: 0, status: "failed", cause: "OpenRouter can't be reached" }),
    slot({ id: "b", pos: 1, status: "failed", cause: "OpenRouter can't be reached" }),
    slot({ id: "c", pos: 2, status: "failed", cause: "z-image-turbo went silent" }),
    slot({ id: "d", pos: 3, status: "failed", cause: "OpenRouter can't be reached" }),
    slot({ id: "e", pos: 4, status: "landed" }),
  ]);
  expect(causeGroups(deriveCells(t, []))).toEqual([
    { cause: "OpenRouter can't be reached", positions: [0, 1, 3] },
  ]);
  expect(causeGroups(deriveCells(turn([t.slots[2]]), []))).toEqual([]);
});

test("law 9: a retry is visible while queued or running, counted, worded — and only then", () => {
  const queued = cellOf({ status: "queued", attempt: 1, retry_of: "rate_limit" }, true);
  expect(retryLine(queued)).toEqual({
    label: "RETRYING · ATTEMPT 2",
    why: expect.stringMatching(/rate limited/),
  });
  const running = cellOf({ status: "running", attempt: 2, retry_of: "unreachable" });
  expect(retryLine(running)?.label).toBe("RETRYING · ATTEMPT 2");
  expect(retryLine(running)?.why).toMatch(/couldn't be reached/);
  // an unknown kind still says so rather than hiding the retry
  expect(retryLine(cellOf({ status: "running", attempt: 2, retry_of: "new_kind" }))?.why).toContain("new_kind");
  // the record keeps the mark; the landed face shows no scar
  expect(retryLine(cellOf({ status: "landed", attempt: 2, retry_of: "rate_limit" }))).toBeNull();
  expect(retryLine(cellOf({ status: "running", attempt: 1 }))).toBeNull();
});

test("deriveCells carries detail, retry_of, finished_at and the bench scope", () => {
  const t = turn([
    slot({ id: "s0", pos: 0, status: "failed", cause: "plain", detail: "raw (HTTP 500)", finished_at: T4 }),
    slot({ id: "s1", pos: 1, status: "held" }),
  ]);
  const cells = deriveCells(t, [], new Map([["s1", "engine:z-image-turbo"]]));
  expect(cells[0]).toMatchObject({ detail: "raw (HTTP 500)", finishedAt: T4, retryOf: null, bench: null });
  expect(cells[1].bench).toBe("engine:z-image-turbo");
});

// ── the trust-critical faces ────────────────────────────────────────────

test("A5a: a blank frame's remedies are same-seed first, a fresh roll second — both free", () => {
  const cells = deriveCells(
    turn([
      slot({
        id: "s-blank",
        pos: 0,
        status: "failed",
        attempt: 1,
        kind: "blank",
        cause: "z-image-turbo finished, but the frame came back empty — that's the engine, not your prompt",
        detail: "harness plate: every pixel black\nthe empty frame is kept at /x/s-blank-take1-blank.png",
      }),
    ]),
    [],
  );
  const r = remedies(cells[0], false);
  expect(r.map((x) => x.id)).toEqual(["rerun-same-seed", "reroll"]);
  expect(r[0]).toMatchObject({ label: "RUN AGAIN · SAME SEED", paid: false, primary: true });
  expect(r[1]).toMatchObject({ label: "REROLL · NEW SEED", paid: false, primary: false });
  expect(cells[0].receipt).toBeNull();
  // an ordinary local failure keeps its one plain reroll
  const plain = deriveCells(
    turn([slot({ id: "s-f", pos: 0, status: "failed", attempt: 1, kind: "error" })]),
    [],
  );
  expect(remedies(plain[0], false).map((x) => x.id)).toEqual(["reroll"]);
});

test("B4: a charged-but-blank cell carries the receipt, the bill-check line while it asks, and receipt-first remedies with the paid one last", () => {
  const base = slot({
    id: "s-b4",
    pos: 1,
    engine: "openai/gpt-image-2",
    status: "failed",
    attempt: 2,
    kind: "charged_blank",
    job_ref: "gen-abc",
    committed_usd: 0.04,
    charged_usd: 0.04,
    seed: null,
    finished_at: "2026-08-25T10:00:00+00:00",
    cause: "openai/gpt-image-2 took the job and billed it, but no image came back",
  });
  const asking = deriveCells(
    turn([base]),
    [],
    new Map(),
    new Map([["s-b4", { slot_id: "s-b4", ask: 2, of: 3 }]]),
  )[0];
  expect(checkLine(asking)).toBe("ASKING OPENAI/GPT-IMAGE-2 ABOUT THE BILL · 2 OF 3");
  expect(costLine(asking, [base], 0)).toBe("$0.04 was charged");
  const r = remedies(asking, false);
  expect(r.map((x) => x.label)).toEqual(["COPY RECEIPT", "RUN LOCALLY · FREE", "REROLL R · ~$0.04"]);
  expect(r[0].primary).toBe(true);
  expect(r[2]).toMatchObject({ paid: true, primary: false });
  expect(asking.receipt).toBe(
    [
      "CHARGED, NOTHING RETURNED — receipt",
      "time      2026-08-25T10:00:00+00:00",
      "provider  openai/gpt-image-2 via OpenRouter",
      "job id    gen-abc",
      "amount    $0.04 charged — the response's figure, corrected by the provider's record where it answered",
      "prompt    a rusty sword",
      "seed      none sent",
      "slot      3b · attempt 2",
    ].join("\n"),
  );
  // the check gone: no line; the face is otherwise the same
  const done = deriveCells(turn([base]), [])[0];
  expect(checkLine(done)).toBeNull();
  expect(done.receipt).toBe(asking.receipt);
  // no job id: the receipt says so rather than inventing one
  expect(receipt({ ...base, job_ref: null, charged_usd: null }, null, "3b")).toContain(
    "job id    none returned\namount    amount not reported\nprompt    \n",
  );
});

test("C6: the stop breath says the three acts with real numbers, and leaves empty facts unsaid", () => {
  const now = Date.parse("2026-08-25T10:00:30+00:00");
  const mixed = [
    slot({ id: "a", pos: 0, engine: "openai/gpt-image-2", status: "landed", attempt: 1, charged_usd: 0.04 }),
    slot({ id: "b", pos: 1, status: "running", attempt: 1, started_at: "2026-08-25T10:00:18+00:00" }),
    slot({ id: "c", pos: 2, status: "queued" }),
    slot({ id: "d", pos: 3, status: "held" }),
    slot({ id: "e", pos: 4, engine: "openai/gpt-image-2", status: "running", attempt: 1, committed_usd: 0.04 }),
    slot({ id: "f", pos: 5, engine: "openai/gpt-image-2", status: "running", attempt: 1, committed_usd: 0.04 }),
    slot({ id: "g", pos: 6, status: "landed", attempt: 1 }),
  ];
  expect(stopLines(mixed, now)).toEqual([
    "Stopping now. Slots 1 · 7 are yours and stay on the canvas.",
    "openai/gpt-image-2's 2 takes are already paid at ~$0.08 and will land anyway.",
    "The 2 takes ahead cost nothing to drop. The running z-image-turbo take is abandoned — 12s of GPU time lost.",
  ]);
  // nothing landed, nothing paid, one queued: two lines, no zeros
  expect(stopLines([slot({ id: "x", pos: 0, status: "queued" })], now)).toEqual([
    "Stopping now. Nothing has landed yet — nothing is lost by stopping.",
    "The 1 take ahead costs nothing to drop.",
  ]);
  // an unpriced in-flight external is "dispatched", not "paid at $0.00"
  expect(
    stopLines([slot({ id: "y", pos: 0, engine: "v/m", status: "running", attempt: 1 })], now),
  ).toEqual([
    "Stopping now. Nothing has landed yet — nothing is lost by stopping.",
    "v/m's 1 take is already dispatched and will land anyway.",
  ]);
});

// ── the high-probability faces ──────────────────────────────────────────────

function oomCell(over: Partial<Slot> = {}): CellVM {
  const s = slot({
    id: "s-oom",
    pos: 1,
    status: "failed",
    attempt: 1,
    kind: "oom",
    frame: "1536x1536",
    facts: { frame: "1536x1536", held_before: null, smaller: "1152x1152" },
    cause: "z-image-turbo ran out of video memory at 1536 × 1536 — a smaller frame is the sure fix",
    ...over,
  });
  return deriveCells(turn([s]), [])[0];
}

test("A1: an OOM cell leads with the record's smaller frame, retry-as-is second, both free", () => {
  const r = remedies(oomCell(), false);
  expect(r.map((x) => x.label)).toEqual(["RETRY AT 1152 × 1152", "RETRY AS-IS · SAME SEED"]);
  expect(r[0].primary).toBe(true);
  expect(r.every((x) => !x.paid)).toBe(true);
  expect(frameWords("1536x1536")).toBe("1536 × 1536");
  expect(frameOf("1152x1152")).toEqual({ width: 1152, height: 1152 });
  expect(frameOf("nope")).toBeNull();
});

test("A11: a size this engine held before leads with retry-as-is", () => {
  const r = remedies(
    oomCell({ facts: { frame: "1536x1536", held_before: { turn_idx: 12, pos: 0, n: 1 }, smaller: "1152x1152" } }),
    false,
  );
  expect(r.map((x) => x.label)).toEqual(["RETRY AS-IS · SAME SEED", "RETRY AT 1152 × 1152"]);
  expect(r[0].primary).toBe(true);
  expect(r[1].primary).toBe(false);
});

test("an OOM with no smaller frame to offer falls back to as-is then a fresh roll", () => {
  const r = remedies(oomCell({ frame: "512x512", facts: { frame: "512x512", held_before: null, smaller: null } }), false);
  expect(r.map((x) => x.label)).toEqual(["RETRY AS-IS · SAME SEED", "REROLL · NEW SEED"]);
  expect(r[0].primary).toBe(true);
});

test("B2: a waiting rate-limited take counts down on the provider's clock with give-up in reach", () => {
  const s = slot({ id: "s-rl", pos: 0, status: "queued", attempt: 1, retry_of: "rate_limit", engine: "openai/gpt-image-2" });
  const retries = new Map([
    ["s-rl", { slot_id: "s-rl", kind: "rate_limit", attempt: 2, of: 5, wait_s: 12, window: "provider" as const, next_try_at: "2026-08-25T00:00:12+00:00" }],
  ]);
  const c = deriveCells(turn([s]), [], new Map(), new Map(), retries)[0];
  const now = Date.parse("2026-08-25T00:00:03+00:00");
  expect(retryLine(c, now)).toEqual({
    label: "RETRYING · ATTEMPT 2 OF 5",
    why: "openai/gpt-image-2 is holding us at the door — retrying on its clock; nothing is charged until an image lands · next try in 9s",
  });
  expect(retryLine(c, now + 30_000)?.why).toMatch(/next try in 0s$/);
  expect(remedies(c, false).map((x) => x.label)).toEqual(["GIVE UP ON THIS SLOT"]);
  expect(remedies(c, false)[0].paid).toBe(false);
  // the cockpit's own default window says whose clock it is
  const own = { ...c, retryWait: { ...c.retryWait!, window: "cockpit" as const } };
  expect(retryLine(own, now)?.why).toContain("retrying on the cockpit's clock");
  // a plain queued cell offers nothing; a running retry has no countdown
  expect(remedies(deriveCells(turn([slot({ id: "q", pos: 0 })]), [])[0], false)).toEqual([]);
  const running = deriveCells(turn([slot({ id: "r", pos: 0, status: "running", attempt: 2, retry_of: "gpu_held" })]), [])[0];
  expect(retryLine(running)).toEqual({
    label: "RETRYING · ATTEMPT 2",
    why: "ran out of memory while something else held the card — it waits its turn; the size isn't the problem",
  });
});

test("C2 and A9: a held cell offers the move its bench kind calls for", () => {
  const s = slot({ id: "s-h", pos: 0, status: "held", engine: "z-image-turbo" });
  const forCard = deriveCells(turn([s]), [], new Map([["s-h", { scope: "lane:local", kind: "gpu_held" }]]))[0];
  expect(forCard.bench).toBe("lane:local");
  expect(forCard.benchKind).toBe("gpu_held");
  expect(remedies(forCard, false).map((x) => x.label)).toEqual(["CHECK AGAIN", "HOLD THE QUEUE"]);
  const heldByHand = deriveCells(turn([s]), [], new Map([["s-h", { scope: "lane:local", kind: "gpu_held" }]]), new Map(), new Map(), true)[0];
  expect(remedies(heldByHand, false).map((x) => x.label)).toEqual(["CHECK AGAIN", "UNBENCH · RESUME"]);
  const forTurn = deriveCells(turn([s]), [], new Map([["s-h", { scope: "engine:z-image-turbo@turn:t1", kind: "repeat" }]]))[0];
  expect(remedies(forTurn, false).map((x) => x.label)).toEqual(["TRY ONCE MORE"]);
  // a caller may still pass a bare scope
  const plain = deriveCells(turn([s]), [], new Map([["s-h", "engine:z-image-turbo"]]))[0];
  expect(plain.bench).toBe("engine:z-image-turbo");
  expect(plain.benchKind).toBeNull();
  expect(remedies(plain, false).map((x) => x.label)).toEqual(["UNBENCH · RESUME"]);
});

test("benches are worded by kind in the strip and reach the turn footer only where they touch it", () => {
  expect(benchLabel({ scope: "lane:local", kind: "gpu_held" })).toBe("LOCAL LANE HELD · THE CARD IS TAKEN");
  expect(benchLabel({ scope: "engine:raw-zimage@turn:t1", kind: "repeat" })).toBe("RAW-ZIMAGE BENCHED · THIS TURN ONLY");
  expect(benchLabel({ scope: "engine:raw-zimage", kind: "model_down" })).toBe("ENGINE:RAW-ZIMAGE BENCHED");
  expect(benchTurn("engine:raw-zimage@turn:t1")).toBe("t1");
  expect(benchTurn("lane:local")).toBeNull();
  const benches = [
    { scope: "engine:raw-zimage@turn:t1", kind: "repeat", cause: "repeat" },
    { scope: "engine:raw-zimage@turn:t9", kind: "repeat", cause: "elsewhere" },
    { scope: "lane:local", kind: "gpu_held", cause: "card" },
    { scope: "lane:external", kind: "dead_key", cause: "key" },
  ];
  const heldHere = deriveCells(turn([slot({ id: "h", pos: 0, status: "held" })]), [], new Map([["h", { scope: "lane:local", kind: "gpu_held" }]]));
  expect(benchNotices(benches, "t1", heldHere).map((b) => b.cause)).toEqual(["repeat", "card"]);
  const nothingHeld = deriveCells(turn([slot({ id: "q", pos: 0 })]), []);
  expect(benchNotices(benches, "t1", nothingHeld).map((b) => b.cause)).toEqual(["repeat"]);
  expect(benchNotices(benches, "t2", nothingHeld)).toEqual([]);
});

// ── the 3D lane's unit is minutes ────────────────────────────────────────

const TRELLIS = "trellis2-4b";

const MATRIX = {
  updated: "2026-08-27",
  engines: [
    { id: "z-image-turbo", name: "Z", lane: "local", payload: "image",
      price: "free", capabilities: {} },
    { id: TRELLIS, name: "TRELLIS.2", lane: "local", payload: "3d",
      price: "free", capabilities: {} },
    { id: "hunyuan3d-21", name: "Hunyuan3D 2.1", lane: "local", payload: "3d",
      price: "free", capabilities: {} },
  ],
  external: { lane: "external", payload: "image", price: "priced" },
} as unknown as Parameters<typeof meshEngineIds>[0];

test("which engines are 3D comes from the matrix, never a list typed here", () => {
  expect(meshEngineIds(MATRIX)).toEqual(new Set([TRELLIS, "hunyuan3d-21"]));
  // roster not loaded yet: nothing is claimed either way
  expect(meshEngineIds(undefined)).toEqual(new Set());
});

test("a queued mesh names its unit; a queued image is untouched", () => {
  const cells = deriveCells(
    turn([slot({ id: "a", pos: 0, engine: TRELLIS }),
          slot({ id: "b", pos: 1, engine: "z-image-turbo" })]),
    [], new Map(), new Map(), new Map(), false, new Map(),
    meshEngineIds(MATRIX),
  );
  expect(cells[0].mesh).toBe(true);
  expect(waitingLine(cells[0])).toBe(
    `${TRELLIS} · waiting for the GPU · minutes, not seconds`,
  );
  // a 4-second take saying "minutes" would be the same lie the other way
  expect(cells[1].mesh).toBe(false);
  expect(waitingLine(cells[1])).toBe("z-image-turbo · waiting for the GPU");
});

test("the running mesh line states the fact and never a total (law 17)", () => {
  expect(MESH_RUNNING_LINE).toBe(
    "The mesh engine holds the GPU for minutes. Nothing is stuck.",
  );
  // no ETA, no prediction, no number at all — the clock beside it is the
  // only number the cell shows
  expect(MESH_RUNNING_LINE).not.toMatch(/\d/);
});

test("a landed mesh reads as one even before the roster arrives", () => {
  // the matrix is still loading, but the node itself says what it is
  const cells = deriveCells(
    turn([slot({ id: "a", pos: 0, engine: TRELLIS, status: "landed",
                 attempt: 1 })]),
    [node({ id: "m", slot_id: "a", take: 1, payload: "3d",
            mesh: { engine: "trellis2", runner_job_id: "j", triangles: 10,
                    vertices: 8, watertight: false, textured: true,
                    file_mb: 1.2, has_turntable: true,
                    forced: false, fields_kept: false } })],
  );
  expect(cells[0].mesh).toBe(true);
  // and its plate is frame 0 of its turntable, off the same thumb route
  expect(cells[0].thumb).toBe("/api/nodes/m/thumb");
});

test("a landed mesh with no turntable asks for no plate", () => {
  const cells = deriveCells(
    turn([slot({ id: "a", pos: 0, engine: TRELLIS, status: "landed",
                 attempt: 1 })]),
    [node({ id: "m", slot_id: "a", take: 1, payload: "3d" })],
  );
  expect(cells[0].thumb).toBeNull();
});
