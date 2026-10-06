// Pure derivation for the turn grid, with no React, so vitest exercises
// the law-bearing math (frozen geometry, cell states, money) without DOM.
// The cell's law functions live here: `costLine` (law 5), `remedies`
// (laws 4 + 6), `causeGroups` (law 1) and `retryLine` (law 9) — SlotCell
// and TurnGrid only draw what these decide.
// So do the trust-critical faces: a blank frame's same-seed rerun
// (A5a), the charged-but-blank cell's receipt and bill-check line (B4),
// and the stop-the-queue breath with real numbers (C6, `stopLines`).
// And the high-probability faces on a one-GPU workstation: the OOM cell
// names its frame and leads with the record's answer — RETRY AS-IS when
// this engine held that size before (A11), RETRY AT a smaller frame
// otherwise (A1); a rate-limited take counts down on the provider's clock
// with GIVE UP always in reach (B2); a take held for the card (C2) offers
// CHECK AGAIN and HOLD THE QUEUE, one held for its turn (A9) TRY ONCE
// MORE; the strip and the footer word each bench by its kind.
import type {
  Bench,
  BillCheck,
  Engines,
  Node,
  Passes,
  RetryWait,
  Slot,
  SlotStatus,
  Turn,
} from "../../types";
import { usd } from "../costs";

// Column count is a function of SLOT COUNT alone — frozen at Run because the
// count is (law 8): results, failures and rerolls can never reflow the grid.
export function gridCols(slotCount: number): number {
  return Math.max(1, Math.min(4, Math.ceil(Math.sqrt(slotCount))));
}

// Server rule mirrored (run_turn 422s a '/' preset id): external model ids
// are vendor/model, local preset ids never contain '/'.
export function isExternalEngine(engine: string): boolean {
  return engine.includes("/");
}

export function slotLetter(pos: number): string {
  return String.fromCharCode(97 + pos); // a… (turns cap at 16 slots)
}

export type CellVM = {
  slotId: string;
  pos: number;
  engine: string;
  external: boolean;
  status: SlotStatus;
  seed: number | null;
  attempt: number;
  cause: string | null;
  // law 3: the raw engine text behind the collapsed DETAILS — never the face
  detail: string | null;
  // law 9: the failure kind this attempt is the scheduler's own retry of
  retryOf: string | null;
  // the failure kind on the row: the face picks its remedy by it
  kind: string | null;
  // the engine's job id; a paid take's generation id is the receipt's line
  jobRef: string | null;
  // B4: the free bill re-ask in flight (from the queue view), law 9
  billCheck: { ask: number; of: number } | null;
  // B4: the pasteable receipt, built once the cell is charged-but-blank
  receipt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  // held: the bench scope parking it (from the queue view), so the cell
  // itself can offer the resume (laws 2 + 4) — and its kind, so a take
  // held for the card (C2) or for its turn (A9) says which
  bench: string | null;
  benchKind: string | null;
  // the requested frame ("1536x1536") and the failure kind's facts
  // — an OOM cell's `smaller` offer and `held_before` (A11)
  frame: string | null;
  facts: Record<string, unknown> | null;
  // B2: the wait before the next automatic try (from the queue view)
  retryWait: RetryWaitVM | null;
  // a region take's pipeline position (from the queue view): the
  // running face says `PASS i OF n`
  passes: Passes | null;
  // This cell is a 3D take, so its unit is MINUTES. Known from the
  // matrix before anything lands, and from the node once it has (a landed
  // mesh reads as one even if the roster is still loading).
  mesh: boolean;
  // C2: HOLD THE QUEUE was pressed — the lane waits for the artist
  gpuManualHold: boolean;
  // committed and charged stay separate values all the way down
  committedUsd: number | null;
  chargedUsd: number | null;
  // the slot's CURRENT take (take === attempt) — after a reroll the old
  // take keeps its node but stops being the cell's face
  nodeId: string | null;
  thumb: string | null;
  setAside: boolean;
  /** This cell's take has left for the asset tree. One mark, one
      meaning, everywhere a node is drawn. */
  promoted: boolean;
  durationMs: number | null;
  label: string; // "13b" — turn idx + slot letter, the canvas card spelling
};

export type RetryWaitVM = {
  attempt: number;
  of: number;
  nextTryAt: string;
  window: "provider" | "cockpit";
};

/** A held slot's bench: the scope alone, or the scope with its kind
 *  (the face words C2 and A9 by it). */
export type BenchRef = string | { scope: string; kind: string };

export function deriveCells(
  turn: Turn,
  nodes: Node[],
  benches: ReadonlyMap<string, BenchRef> = new Map(),
  checks: ReadonlyMap<string, BillCheck> = new Map(),
  retries: ReadonlyMap<string, RetryWait> = new Map(),
  gpuManualHold = false,
  passes: ReadonlyMap<string, Passes> = new Map(),
  meshEngines: ReadonlySet<string> = new Set(),
): CellVM[] {
  const takeBySlot = new Map<string, Node>();
  for (const n of nodes) {
    if (n.slot_id !== null) takeBySlot.set(`${n.slot_id}#${n.take}`, n);
  }
  return turn.slots.map((s: Slot) => {
    const node = takeBySlot.get(`${s.id}#${s.attempt}`) ?? null;
    const check = checks.get(s.id);
    const wait = retries.get(s.id);
    const b = benches.get(s.id);
    const label = `${turn.idx}${slotLetter(s.pos)}`;
    return {
      slotId: s.id,
      pos: s.pos,
      engine: s.engine,
      external: isExternalEngine(s.engine),
      status: s.status,
      seed: s.seed,
      attempt: s.attempt,
      cause: s.cause,
      detail: s.detail,
      retryOf: s.retry_of,
      kind: s.kind,
      jobRef: s.job_ref,
      billCheck: check === undefined ? null : { ask: check.ask, of: check.of },
      receipt:
        s.status === "failed" && s.kind === "charged_blank"
          ? receipt(s, turn.prompt, label)
          : null,
      startedAt: s.started_at,
      finishedAt: s.finished_at,
      bench: b === undefined ? null : typeof b === "string" ? b : b.scope,
      benchKind: b === undefined || typeof b === "string" ? null : b.kind,
      frame: s.frame,
      facts: s.facts,
      retryWait:
        wait === undefined
          ? null
          : { attempt: wait.attempt, of: wait.of, nextTryAt: wait.next_try_at, window: wait.window },
      passes: passes.get(s.id) ?? null,
      mesh: meshEngines.has(s.engine) || node?.payload === "3d",
      gpuManualHold,
      committedUsd: s.committed_usd,
      chargedUsd: s.charged_usd,
      nodeId: node ? node.id : null,
      // a mesh's plate is frame 0 of its turntable, off the same
      // route; one whose orbit was never recorded asks for nothing
      thumb:
        node &&
        (node.payload === "image" || node.mesh?.has_turntable === true)
          ? `/api/nodes/${node.id}/thumb`
          : null,
      setAside: node ? node.set_aside : false,
      promoted: node ? node.promoted_at !== null : false,
      durationMs: node ? node.duration_ms : null,
      label,
    };
  });
}

// B4's receipt, built for pasting into a support email: time, provider,
// job ID, amount, prompt, seed. Plain text, one fact per line, the
// amount stated as what it is (the response's own report; the bill check
// writes its verdict to the ledger, not to the face).
export function receipt(
  s: Pick<
    Slot,
    "engine" | "job_ref" | "charged_usd" | "seed" | "finished_at" | "attempt"
  >,
  prompt: string | null,
  label: string,
): string {
  // the ledger's figure: the response's own report, corrected by the
  // provider's record where the bill check got an answer
  const amount =
    s.charged_usd !== null && s.charged_usd > 0
      ? `${money(s.charged_usd)} charged — the response's figure, corrected by the provider's record where it answered`
      : "amount not reported";
  return [
    "CHARGED, NOTHING RETURNED — receipt",
    `time      ${s.finished_at ?? "unknown"}`,
    `provider  ${s.engine} via OpenRouter`,
    `job id    ${s.job_ref ?? "none returned"}`,
    `amount    ${amount}`,
    `prompt    ${prompt ?? ""}`,
    `seed      ${s.seed !== null ? s.seed : "none sent"}`,
    `slot      ${label} · attempt ${s.attempt}`,
  ].join("\n");
}

// B4's one automatic action, said while it runs (law 9): the bill is being
// re-asked — never a re-generate — and the count is the provider's clock.
export function checkLine(cell: CellVM): string | null {
  if (cell.billCheck === null) return null;
  return `ASKING ${cell.engine.toUpperCase()} ABOUT THE BILL · ${cell.billCheck.ask} OF ${cell.billCheck.of}`;
}

// Header readout: only states that exist, always ending in the one-GPU
// sentence (law 13: said out loud wherever a queue exists).
const COUNT_ORDER: [SlotStatus, string][] = [
  ["landed", "LANDED"],
  ["running", "RUNNING"],
  ["queued", "QUEUED"],
  ["held", "HELD"],
  ["late", "LATE"],
  ["failed", "FAILED"],
  ["set_aside", "SET ASIDE"],
  ["skipped", "SKIPPED"],
];

export function countsLine(slots: Slot[]): string {
  const by = new Map<SlotStatus, number>();
  for (const s of slots) by.set(s.status, (by.get(s.status) ?? 0) + 1);
  const parts = COUNT_ORDER.filter(([k]) => by.has(k)).map(
    ([k, word]) => `${by.get(k)} ${word}`,
  );
  parts.push("ONE GPU, LOCALS TAKE TURNS");
  return parts.join(" · ");
}

// A turn with anything still moving offers cancel; resting turns don't.
const ACTIVE: ReadonlySet<SlotStatus> = new Set([
  "queued",
  "running",
  "held",
]);

export function turnActive(slots: Slot[]): boolean {
  return slots.some((s) => ACTIVE.has(s.status));
}

// Cancel's one breath, with the amounts real: landed stays, queued locals
// drop free, in-flight externals are already paid, and say how much.
export function cancelBreath(slots: Slot[]): string {
  const landed = slots.filter((s) => s.status === "landed").length;
  const queued = slots.filter((s) => s.status === "queued").length;
  const inFlightExt = slots.filter(
    (s) => s.status === "running" && isExternalEngine(s.engine),
  );
  const paid = inFlightExt.reduce((t, s) => t + (s.committed_usd ?? 0), 0);
  const parts: string[] = [];
  if (landed > 0) parts.push(`${landed} landed stay`);
  if (queued > 0) parts.push(`${queued} queued drop free`);
  if (inFlightExt.length > 0)
    parts.push(
      `${inFlightExt.length} external in flight already paid` +
        (paid > 0 ? ` (${money(paid)})` : ""),
    );
  return parts.join(" · ") || "nothing is moving";
}

// C6 — class C's trust-critical case: cancel is two different acts in a
// mixed batch and must say so in one breath, with the amounts real, BEFORE
// the stop lands: landed slots stay, in-flight externals are
// already paid and may still land, local takes ahead cost nothing, and a
// running local take is abandoned with its seconds. One line each; empty
// facts are left out rather than said as zero.
export function stopLines(slots: Slot[], nowMs: number): string[] {
  const lines: string[] = [];
  const landed = slots.filter((s) => s.status === "landed");
  const runningLocal = slots.find(
    (s) => s.status === "running" && !isExternalEngine(s.engine),
  );
  lines.push(
    landed.length > 0
      ? `Stopping now. Slot${landed.length === 1 ? "" : "s"} ${landed
          .map((s) => s.pos + 1)
          .join(" · ")} ${landed.length === 1 ? "is" : "are"} yours and stay${
          landed.length === 1 ? "s" : ""
        } on the canvas.`
      : "Stopping now. Nothing has landed yet — nothing is lost by stopping.",
  );
  const inFlight = new Map<string, { n: number; usd: number; priced: boolean }>();
  for (const s of slots) {
    if (s.status !== "running" || !isExternalEngine(s.engine)) continue;
    const e = inFlight.get(s.engine) ?? { n: 0, usd: 0, priced: false };
    e.n += 1;
    if (s.committed_usd !== null && s.committed_usd > 0) {
      e.usd += s.committed_usd;
      e.priced = true;
    }
    inFlight.set(s.engine, e);
  }
  for (const [engine, e] of inFlight)
    lines.push(
      `${engine}'s ${e.n} take${e.n === 1 ? " is" : "s are"} already ${
        e.priced ? `paid at ~${money(e.usd)}` : "dispatched"
      } and will land anyway.`,
    );
  const ahead = slots.filter(
    (s) => s.status === "queued" || s.status === "held",
  ).length;
  const parts: string[] = [];
  if (ahead > 0)
    parts.push(
      `The ${ahead} take${ahead === 1 ? "" : "s"} ahead cost${ahead === 1 ? "s" : ""} nothing to drop.`,
    );
  if (runningLocal !== undefined) {
    const started =
      runningLocal.started_at !== null ? Date.parse(runningLocal.started_at) : nowMs;
    parts.push(
      `The running ${runningLocal.engine} take is abandoned — ${spent(
        Math.max(0, nowMs - started),
      )} of GPU time lost.`,
    );
  }
  if (parts.length > 0) lines.push(parts.join(" "));
  return lines;
}

export function money(v: number | null): string {
  return v === null ? "" : usd(v);
}

export function elapsedLabel(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}S`;
  return `${Math.floor(s / 60)}M ${(s % 60).toString().padStart(2, "0")}S`;
}

// ── the 3D lane's unit is minutes ────────────────────────────────────────
//
// law 17 stands: no ETA, no predicted total, no bar. The cell says WHAT KIND
// of wait this is, once, in words, and lets the observed clock beside it be
// the only number. Measured on the development machine: 167 s warm, 310 s
// after an image run evicted the model. A range, which is exactly why none of it is
// promised here. The image lane's own wording is untouched; a 4-second take
// that said "minutes" would be the same lie in the other direction.

/** The queued cell's second line. A mesh names its unit; everything else
 *  reads without one. */
export function waitingLine(cell: Pick<CellVM, "engine" | "external" | "mesh">): string {
  const how = cell.external ? "launching" : "waiting for the GPU";
  return cell.mesh
    ? `${cell.engine} · ${how} · minutes, not seconds`
    : `${cell.engine} · ${how}`;
}

/** The running mesh cell's one line, under the clock. Law 13's register:
 *  the plain fact, and the reassurance it exists to give. */
export const MESH_RUNNING_LINE =
  "The mesh engine holds the GPU for minutes. Nothing is stuck.";

/** The 3d engine ids the capability matrix publishes — the SAME source the
 *  To-3D roster reads (`mesh/mesh.ts` meshRoster), never a list typed here
 *  again: the matrix is the product's roster (law 12), so a new mesh
 *  engine reaches this surface by being published, not by being hardcoded. */
export function meshEngineIds(engines: Engines | undefined): Set<string> {
  return new Set(
    (engines?.engines ?? []).filter((e) => e.payload === "3d").map((e) => e.id),
  );
}

/** A region take's pipeline position as a ` · PASS i OF n` suffix
 *  on a running line; empty for everything that isn't one. */
export function passSuffix(p: Passes | null | undefined): string {
  return p == null ? "" : ` · PASS ${p.current} OF ${p.total}`;
}

// ── the cell's laws ──────────────────────────────────────────────────────

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function spent(ms: number): string {
  return ms < 1000 ? "under a second" : elapsedLabel(ms).toLowerCase();
}

// Law 5: line 2 states cost — seconds and takes for a local take, dollars
// for a paid one. Money is named ONLY where money was plausibly in play: a
// "nothing charged" that fires on every free failure trains the artist to
// skip the line, which is fatal at B4 (law 5). null = the state
// has no cost line (queued, running, landed, set aside).
export function costLine(
  cell: CellVM,
  slots: readonly Slot[],
  nowMs: number,
): string | null {
  const paid = cell.external;
  switch (cell.status) {
    case "failed": {
      if (paid)
        return cell.chargedUsd !== null && cell.chargedUsd > 0
          ? `${money(cell.chargedUsd)} was charged`
          : "nothing was charged";
      const gpu =
        cell.startedAt === null
          ? "some GPU time spent"
          : `${spent(
              (cell.finishedAt !== null ? Date.parse(cell.finishedAt) : nowMs) -
                Date.parse(cell.startedAt),
            )} of GPU time spent`;
      const ahead = slots.filter(
        (x) => x.id !== cell.slotId && (x.status === "queued" || x.status === "held"),
      ).length;
      return ahead > 0 ? `${gpu} · ${plural(ahead, "take")} still queued` : gpu;
    }
    case "skipped":
      return paid ? "nothing was charged" : "no GPU time spent — its turn was given back";
    case "held":
      return paid
        ? "nothing charged while it waits — resumes on unbench"
        : "waiting costs nothing — resumes on unbench";
    case "late":
      return cell.committedUsd !== null && cell.committedUsd > 0
        ? `already paid, ~${money(cell.committedUsd)} — lands here if it still arrives`
        : "already dispatched — lands here if it still arrives";
    default:
      return null;
  }
}

export type RemedyId =
  | "reroll"
  | "rerun-same-seed"
  | "copy-receipt"
  | "run-locally"
  | "unbench"
  | "restore"
  | "set-aside"
  // the high-probability faces
  | "retry-as-is" // A11: same slot, same dials, same seed, same size
  | "retry-smaller" // A1: the same cell at the record's smaller frame
  | "give-up" // B2: cancel the waiting take — skipped, nothing spent
  | "check-gpu" // C2: look at the card now
  | "hold-queue"; // C2: stop the automatic pickup until an unbench

export type Remedy = {
  id: RemedyId;
  label: string;
  paid: boolean;
  /** The one the eye lands on first (framed). Never a paid one (law 6). */
  primary: boolean;
  /** Set when the action is on the face but inert, with the reason. */
  gated: string | null;
};

export const REROLL_GATED_TITLE =
  "LOCAL ONLY is on — this cell is a paid model; flip the switch in the top bar to reroll it";

function rerollRemedy(cell: CellVM, localOnly: boolean, primary: boolean): Remedy {
  if (!cell.external) return { id: "reroll", label: "REROLL R", paid: false, primary, gated: null };
  if (localOnly)
    return { id: "reroll", label: "REROLL · LOCAL ONLY", paid: true, primary: false, gated: REROLL_GATED_TITLE };
  // A reroll re-quotes today's pin (api_v4._reroll): the last quote is the
  // face's estimate — stated as one, never as the bill (law 6: the price on
  // the button face).
  const price =
    cell.committedUsd !== null && cell.committedUsd > 0
      ? `~${money(cell.committedUsd)}`
      : "PRICE UNLISTED";
  return { id: "reroll", label: `REROLL R · ${price}`, paid: true, primary: false, gated: null };
}

// Laws 4 + 6: every non-landed state offers its next move as a button, and
// on a paid cell the free remedy comes first while the costed one sits last
// with its price on the face, never framed. The free move on a failed or
// skipped paid slot (B1, B5) is to run the same brief locally for free: arm
// the composer with it as a new run, so the cell's identity is untouched
// (law 8; B10, refilling the slot itself, is not implemented).
// "1536x1536" (the wire's key) → "1536 × 1536" (the face's words).
export function frameWords(key: string | null | undefined): string | null {
  if (!key) return null;
  const m = /^(\d+)x(\d+)$/.exec(key);
  return m === null ? null : `${m[1]} × ${m[2]}`;
}

export function frameOf(key: string): { width: number; height: number } | null {
  const m = /^(\d+)x(\d+)$/.exec(key);
  return m === null ? null : { width: Number(m[1]), height: Number(m[2]) };
}

/** An OOM cell's facts, typed: the record's smaller offer and whether this
 *  engine held the size before (A11). */
export function oomFacts(cell: CellVM): {
  smaller: string | null;
  heldBefore: { turn_idx: number } | null;
} {
  const f = cell.facts ?? {};
  const smaller = typeof f.smaller === "string" ? f.smaller : null;
  const hb = f.held_before;
  const heldBefore =
    hb !== null && typeof hb === "object" && typeof (hb as { turn_idx?: unknown }).turn_idx === "number"
      ? { turn_idx: (hb as { turn_idx: number }).turn_idx }
      : null;
  return { smaller, heldBefore };
}

export function remedies(cell: CellVM, localOnly: boolean): Remedy[] {
  switch (cell.status) {
    case "queued":
      // B2: a take waiting on the provider's clock keeps its give-up in
      // reach at all times: cancel, skipped free
      return cell.retryWait === null
        ? []
        : [{ id: "give-up", label: "GIVE UP ON THIS SLOT", paid: false, primary: false, gated: null }];
    case "failed":
    case "skipped":
      if (cell.status === "failed" && cell.kind === "oom" && !cell.external) {
        // A1/A11: both free. The record decides which leads — a size this
        // engine has held before is not the problem (retry as-is first);
        // otherwise the smaller frame the record vouches for leads.
        const { smaller, heldBefore } = oomFacts(cell);
        const asIs: Remedy = { id: "retry-as-is", label: "RETRY AS-IS · SAME SEED", paid: false, primary: false, gated: null };
        const words = frameWords(smaller);
        const small: Remedy | null =
          words === null ? null : { id: "retry-smaller", label: `RETRY AT ${words}`, paid: false, primary: false, gated: null };
        const lead = heldBefore !== null || small === null ? asIs : small;
        lead.primary = true;
        const rest = [asIs, small].filter((r): r is Remedy => r !== null && r !== lead);
        return [lead, ...rest, ...(small === null ? [{ ...rerollRemedy(cell, localOnly, false), label: "REROLL · NEW SEED" }] : [])];
      }
      if (cell.status === "failed" && cell.kind === "blank" && !cell.external)
        // A5a: the settings were fine, the engine wasn't. Same seed first,
        // a fresh roll second; both free.
        return [
          { id: "rerun-same-seed", label: "RUN AGAIN · SAME SEED", paid: false, primary: true, gated: null },
          { ...rerollRemedy(cell, localOnly, false), label: "REROLL · NEW SEED" },
        ];
      if (cell.status === "failed" && cell.kind === "charged_blank" && cell.external)
        // B4: the receipt first (the one thing only the record can give),
        // the free refill next, the paid retry last with its price
        return [
          { id: "copy-receipt", label: "COPY RECEIPT", paid: false, primary: true, gated: null },
          { id: "run-locally", label: "RUN LOCALLY · FREE", paid: false, primary: false, gated: null },
          rerollRemedy(cell, localOnly, false),
        ];
      return cell.external
        ? [
            { id: "run-locally", label: "RUN LOCALLY · FREE", paid: false, primary: true, gated: null },
            rerollRemedy(cell, localOnly, false),
          ]
        : [rerollRemedy(cell, localOnly, true)];
    case "held":
      if (cell.benchKind === "gpu_held")
        // C2: look again now, or keep the queue held until you say so —
        // after HOLD THE QUEUE the resume is the explicit unbench
        return [
          { id: "check-gpu", label: "CHECK AGAIN", paid: false, primary: true, gated: null },
          cell.gpuManualHold
            ? { id: "unbench", label: "UNBENCH · RESUME", paid: false, primary: false, gated: null }
            : { id: "hold-queue", label: "HOLD THE QUEUE", paid: false, primary: false, gated: null },
        ];
      if (cell.benchKind === "repeat")
        // A9: the engine is benched for this batch; one more attempt lifts it
        return [{ id: "unbench", label: "TRY ONCE MORE", paid: false, primary: true, gated: null }];
      return [{ id: "unbench", label: "UNBENCH · RESUME", paid: false, primary: true, gated: null }];
    case "set_aside":
      return [{ id: "restore", label: "RESTORE ⌘Z", paid: false, primary: true, gated: null }];
    case "landed": {
      const aside: Remedy = { id: "set-aside", label: "SET ASIDE ⌫", paid: false, primary: false, gated: null };
      const reroll = rerollRemedy(cell, localOnly, false);
      return cell.external ? [aside, reroll] : [reroll, aside];
    }
    default:
      return [];
  }
}

// Law 1: one cause, one message. Failed cells sharing a cause keep their
// own faces, and the grid says the shared reason once in its header.
export function causeGroups(
  cells: readonly CellVM[],
): { cause: string; positions: number[] }[] {
  const by = new Map<string, number[]>();
  for (const c of cells) {
    if (c.status !== "failed" || c.cause === null) continue;
    const list = by.get(c.cause) ?? [];
    list.push(c.pos);
    by.set(c.cause, list);
  }
  return [...by]
    .filter(([, positions]) => positions.length > 1)
    .map(([cause, positions]) => ({ cause, positions }));
}

// Law 9: an automatic retry is visible while it runs — gold, the attempt
// counted, the reason in words (B2 is the canonical shape). `lands` names what
// a landing is for the medium ("an image" here; "a take" on mixed surfaces).
const RETRY_WHY: Record<string, (lands: string) => string> = {
  rate_limit: (lands) => `rate limited — trying once more on the provider's clock; nothing is charged until ${lands} lands`,
  unreachable: () => "couldn't be reached — trying once more; if that fails the lane benches",
  // C2: the OOM was the hold's, not the frame's. The take went
  // back to the queue and waits for the card, nothing spent
  gpu_held: () => "ran out of memory while something else held the card — it waits its turn; the size isn't the problem",
};

export function retryLine(
  cell: CellVM,
  nowMs: number = Date.now(),
  lands = "an image",
): { label: string; why: string } | null {
  if (cell.retryOf === null) return null;
  if (cell.status !== "queued" && cell.status !== "running") return null;
  const attempt = cell.status === "queued" ? cell.attempt + 1 : cell.attempt;
  if (cell.status === "queued" && cell.retryWait !== null) {
    // B2's canonical shape: attempt i of n, the countdown on whose clock
    const w = cell.retryWait;
    const secs = Math.max(0, Math.ceil((Date.parse(w.nextTryAt) - nowMs) / 1000));
    const clock = w.window === "provider" ? "its clock" : "the cockpit's clock";
    const why =
      cell.retryOf === "rate_limit"
        ? `${cell.engine} is holding us at the door — retrying on ${clock}; nothing is charged until ${lands} lands · next try in ${secs}s`
        : `${RETRY_WHY[cell.retryOf]?.(lands) ?? `after: ${cell.retryOf} — trying once more`} · next try in ${secs}s`;
    return { label: `RETRYING · ATTEMPT ${w.attempt} OF ${w.of}`, why };
  }
  return {
    label: `RETRYING · ATTEMPT ${attempt}`,
    why: RETRY_WHY[cell.retryOf]?.(lands) ?? `after: ${cell.retryOf} — trying once more`,
  };
}

// ── benches, worded by kind ─────────────────────────────────────────────────

/** Is this a bench for one turn only (A9)? Returns the turn id. */
export function benchTurn(scope: string): string | null {
  const i = scope.indexOf("@turn:");
  return i === -1 ? null : scope.slice(i + 6);
}

// A bench is named in plain words (law 3), never by its raw scope: the shared GPU
// for the local lane, the hosted lane, a provider by its display name, an engine
// by the name the caller gives it (a blind surface gives one that withholds it).
const PROVIDER_NAMES: Record<string, string> = {
  comfy: "ComfyUI",
  comfyui: "ComfyUI",
  openrouter: "OpenRouter",
  elevenlabs: "ElevenLabs",
  "voice-runner": "Voice runner",
  threed: "3D runner",
};
const BENCH_STATE: Record<string, string> = {
  unreachable: "not answering",
  dead_key: "key missing or refused",
  no_key: "key missing or refused",
  model_down: "model unavailable",
  timeout: "timed out",
};

/** A bench's subject and state from its scope and kind, in plain words. */
export function benchName(
  b: Pick<Bench, "scope" | "kind">,
  engineName: (id: string) => string = (id) => id,
): string {
  const cut = b.scope.indexOf(":");
  const prefix = cut === -1 ? b.scope : b.scope.slice(0, cut);
  const rest = cut === -1 ? "" : b.scope.slice(cut + 1);
  const state = BENCH_STATE[b.kind ?? ""];
  if (b.kind === "gpu_held") return "Shared GPU held: the card is taken";
  if (prefix === "lane" && rest === "local")
    return b.kind === "unreachable" ? "Shared GPU paused: ComfyUI not answering" : "Shared GPU paused";
  if (prefix === "lane") return state ? `Hosted lane paused: OpenRouter ${state}` : "Hosted lane paused";
  if (prefix === "provider") return `${PROVIDER_NAMES[rest] ?? `The ${rest} provider`} ${state ?? "paused"}`;
  if (prefix === "engine") {
    const turn = benchTurn(b.scope);
    const id = turn === null ? rest : rest.slice(0, rest.indexOf("@turn:"));
    if (turn !== null)
      return `${engineName(id)} benched for this run${b.kind === "repeat" ? " after repeated failures" : ""}`;
    return `${engineName(id)} ${state ?? "benched"}`;
  }
  return "An engine is held";
}

/** A bench as a sentence: its own plain cause when the view carries one,
 *  else its name. */
export function benchWords(
  b: Pick<Bench, "scope" | "kind"> & { cause?: string | null },
  engineName?: (id: string) => string,
): string {
  const cause = b.cause?.trim();
  return cause ? cause : `${benchName(b, engineName)}.`;
}

/** The strip's one-line name for a bench: the card held (C2), an engine
 *  benched for one turn (A9), else the bench's plain name. */
export function benchLabel(b: Pick<Bench, "scope" | "kind">): string {
  if (b.kind === "gpu_held") return "LOCAL LANE HELD · THE CARD IS TAKEN";
  const turn = benchTurn(b.scope);
  if (turn !== null) {
    const engine = b.scope.slice("engine:".length, b.scope.indexOf("@turn:"));
    return `${engine.toUpperCase()} BENCHED · THIS TURN ONLY`;
  }
  return benchName(b).toUpperCase();
}

/** The turn footer's bench notices (law 1: one cause, one message): every
 *  bench scoped to this turn (A9), and the local lane's card-held bench
 *  when one of this turn's cells is held by it (C2). */
export function benchNotices(
  benches: readonly Bench[],
  turnId: string,
  cells: readonly CellVM[],
): Bench[] {
  const out: Bench[] = [];
  for (const b of benches) {
    if (benchTurn(b.scope) === turnId) out.push(b);
    else if (b.kind === "gpu_held" && cells.some((c) => c.status === "held" && c.bench === b.scope))
      out.push(b);
  }
  return out;
}
