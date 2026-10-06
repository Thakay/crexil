// The project grid's attempt faces, decided without the DOM. A face holds a slot
// that has no material to show: what happened in the server's plain words (law 3),
// what it cost (law 5) and the next move as a button (law 4). A retry the scheduler
// is making says so (law 9), and failed attempts that share a cause are named once
// (law 1). The cost and retry lines are the legacy grid's tested law functions,
// read through a CellVM; only the remedies differ. On this surface every new
// attempt is prepared, reviewed and priced before it runs, so no remedy on a face
// spends money: a paid path opens a preparation, and its price sits on the Run
// button (law 6). A blind run's face never names its engine or its cost.
import { benchWords, costLine, receipt, retryLine, waitingLine, type CellVM } from '../batch/grid';
import { engineLabel, money, type Operation, type Snapshot } from './api';
import type { Slot } from './cells';

export type Turn = Snapshot['turns'][number];
export type Control = NonNullable<Snapshot['execution_controls']>[number];

/** What the queue view adds about a held slot: the bench that covers it. */
export type QueueFacts = {bench: string | null; benchKind: string | null; benchCause: string | null};

export type RemedyId = 'collect' | 'copy-receipt' | 'resume-run' | 'resume-project' | 'reuse' | 'connections' | 'resume-engine';

export type AttemptRemedy = {
  id: RemedyId;
  label: string;
  /** The one the eye lands on first. Never a costed move; a face holds none (law 6). */
  primary: boolean;
  /** Set when the move is shown but cannot be made here, with the reason (law 12). */
  gated: string | null;
};

export type AttemptTone = 'dead' | 'waiting' | 'info';

export type AttemptFaceVM = {
  slotId: string;
  /** "0A": the run's index and the slot's letter, as material labels spell them. */
  label: string;
  /** The face's first words, in caps: FAILED, HELD, OUTCOME UNCERTAIN… */
  title: string;
  /** Red needs you, gold waits without you, grey-blue informs (the design's message colours). */
  tone: AttemptTone;
  engine: string;
  /** The server's plain line (law 3); raw engine text stays behind DETAILS. */
  cause: string | null;
  detail: string | null;
  /** Line two: what it cost (law 5). */
  cost: string | null;
  /** What a waiting or working attempt is doing. */
  progress: string | null;
  /** A retry the scheduler is making, said while it happens (law 9). */
  retry: {label: string; why: string} | null;
  remedies: AttemptRemedy[];
  /** B4: the pasteable receipt of a charged attempt that returned nothing. */
  receipt: string | null;
};

export type AttemptContext = {
  turn: Turn;
  operations: readonly Operation[];
  controls: readonly Control[];
  queue?: QueueFacts | null;
  nowMs?: number;
};

/** Failure kinds whose files can still be retrieved from the same request, free. */
const COLLECTABLE = new Set(['collection', 'uncertain', 'timeout', 'restart', 'charged_blank']);
/** Failure kinds whose next move is to look at the engine's connection. */
const CONNECTION = new Set(['dead_key', 'unreachable', 'model_down']);

const REUSE_TITLE = 'Opens a new preparation from this direction. Nothing runs or spends until you review and run it.';
export const RESUME_ENGINE_GATED = 'Not available here yet: this surface cannot resume a held engine.';

export const remedyTitle = (remedy: AttemptRemedy): string => remedy.gated ?? (remedy.id === 'reuse' ? REUSE_TITLE
  : remedy.id === 'collect' ? 'Checks this same request again. It never generates again and never charges again.'
  : remedy.id === 'connections' ? 'Opens Connections, where keys and runtimes are checked.'
  : remedy.id === 'copy-receipt' ? 'Copies the receipt for this charge.' : remedy.label);

/** The operation a slot's engine came from: its id, or the engine an audio or mesh operation names. */
export function operationFor(engine: string, operations: readonly Operation[]): Operation | undefined {
  return operations.find(op => op.id === engine) ?? operations.find(op => op.id.endsWith(`:${engine}`));
}

/** Whether money is plausibly in play for this operation: it publishes a price. */
export function priced(op: Operation | undefined): boolean {
  if (!op) return false;
  return (op.maximum_usd_per_attempt ?? 0) > 0 || (op.price_usd ?? 0) > 0 || (op.tariff?.micro_usd ?? 0) > 0;
}

const slotLabel = (turn: Turn, slot: Slot) => `${turn.idx}${String.fromCharCode(65 + slot.pos)}`;

function landing(op: Operation | undefined): string {
  return op?.output_payload === 'image' ? 'an image' : op?.output_payload === '3d' ? 'a mesh' : 'a take';
}

export function attemptFace(slot: Slot, ctx: AttemptContext): AttemptFaceVM {
  const {turn, operations, controls, queue = null, nowMs = Date.now()} = ctx;
  const hidden = slot.identity_hidden === true;
  const op = hidden ? undefined : operationFor(slot.engine, operations);
  const paid = !hidden && ((slot.committed_usd ?? 0) > 0 || (slot.charged_usd ?? 0) > 0 || priced(op));
  const engine = engineLabel(slot, 'face');
  const label = slotLabel(turn, slot);
  const cell: CellVM = {
    slotId: slot.id, pos: slot.pos, engine, external: paid, status: slot.status, seed: slot.seed,
    attempt: slot.attempt, cause: slot.cause, detail: slot.detail, retryOf: slot.retry_of, kind: slot.kind,
    jobRef: slot.job_ref, billCheck: null, receipt: null, startedAt: slot.started_at, finishedAt: slot.finished_at,
    bench: queue?.bench ?? null, benchKind: queue?.benchKind ?? null, frame: slot.frame, facts: slot.facts,
    retryWait: null, passes: null, mesh: op?.output_payload === '3d', gpuManualHold: false,
    committedUsd: slot.committed_usd, chargedUsd: slot.charged_usd, nodeId: null, thumb: null, setAside: false,
    promoted: false, durationMs: null, label,
  };
  const uncertain = slot.status === 'failed' && slot.kind === 'uncertain';
  const face: AttemptFaceVM = {
    slotId: slot.id, label, engine, title: slot.status.replace('_', ' ').toUpperCase(), tone: 'info',
    cause: slot.cause, detail: slot.detail, cost: null, progress: null, remedies: [],
    retry: (slot.status === 'queued' || slot.status === 'running') ? retryLine(cell, nowMs, landing(op)) : null,
    receipt: null,
  };
  const reuse = (primary: boolean): AttemptRemedy => ({id: 'reuse', label: 'REUSE SETUP', primary, gated: null});
  const collect = (primary: boolean): AttemptRemedy => ({id: 'collect', label: 'COLLECT FILES', primary, gated: null});
  const connections = (primary: boolean): AttemptRemedy => ({id: 'connections', label: 'CONNECTIONS', primary, gated: null});

  switch (slot.status) {
    case 'failed': {
      face.tone = 'dead';
      face.title = uncertain ? 'OUTCOME UNCERTAIN' : 'FAILED';
      if (hidden) face.cost = 'Its cost shows after the reveal';
      else if (uncertain && paid) face.cost = (slot.charged_usd ?? 0) > 0 ? `${money(slot.charged_usd!)} was charged`
        : (slot.committed_usd ?? 0) > 0 ? `charge not confirmed · ${money(slot.committed_usd!)} reserved` : 'charge not confirmed';
      else face.cost = costLine(cell, turn.slots, nowMs);
      if (slot.kind === 'charged_blank') {
        if (!hidden) face.receipt = receipt(slot, turn.prompt, label);
        face.remedies = [
          ...(face.receipt ? [{id: 'copy-receipt', label: 'COPY RECEIPT', primary: true, gated: null} as AttemptRemedy] : []),
          collect(!face.receipt), reuse(false),
        ];
      } else if (slot.kind && COLLECTABLE.has(slot.kind)) face.remedies = [collect(true), reuse(false)];
      else if (slot.kind && CONNECTION.has(slot.kind)) face.remedies = [connections(true), reuse(false)];
      else face.remedies = [reuse(true)];
      break;
    }
    case 'skipped':
      face.cost = hidden ? 'Its cost shows after the reveal' : costLine(cell, turn.slots, nowMs);
      face.remedies = [reuse(true)];
      break;
    case 'late':
      face.tone = 'waiting';
      face.cost = hidden ? 'Its cost shows after the reveal' : costLine(cell, turn.slots, nowMs);
      face.remedies = [collect(true)];
      break;
    case 'held': {
      face.tone = 'waiting';
      face.cost = paid ? 'nothing charged while it waits' : 'waiting costs nothing';
      const run = controls.find(c => c.scope === 'run' && c.target_id === turn.id);
      const project = controls.find(c => c.scope === 'project');
      if (run?.paused) {
        face.progress = 'This run is paused. Future requests wait here.';
        face.remedies = [{id: 'resume-run', label: `RESUME RUN ${turn.idx}`, primary: true, gated: null}];
      } else if (project?.paused) {
        face.progress = 'The project is paused. Future requests wait here.';
        face.remedies = [{id: 'resume-project', label: 'RESUME PROJECT', primary: true, gated: null}];
      } else {
        // A9 benches an engine for this run after repeated failures: preparing again is the
        // live move. A connection bench points to Connections. The card being held waits.
        // The hold in plain words (law 3); a blind run's never names its engine or provider.
        const kind = queue?.benchKind ?? null;
        face.progress = hidden || !queue?.bench ? 'Its engine is held.'
          : benchWords({scope: queue.bench, kind: kind ?? '', cause: queue.benchCause}, id => engineLabel({engine: id}, 'face'));
        const resume: AttemptRemedy = {id: 'resume-engine', label: kind === 'repeat' ? 'TRY ONCE MORE' : 'RESUME ENGINE',
          primary: false, gated: RESUME_ENGINE_GATED};
        face.remedies = kind === 'gpu_held' ? [resume] : kind === 'repeat' ? [reuse(true), resume] : [connections(true), resume];
      }
      break;
    }
    case 'queued':
      face.tone = 'waiting';
      face.progress = hidden ? `${engine} · waiting its turn` : waitingLine(cell);
      break;
    case 'running':
      face.tone = 'waiting';
      face.progress = `${engine} · running`;
      break;
    default:
      break;
  }
  return face;
}

/** Law 1: failed attempts that share one cause keep their own faces, and the run
 *  names the shared cause once. Only causes shared by two or more are returned. */
export function sharedCauses(faces: readonly AttemptFaceVM[]): {cause: string; labels: string[]}[] {
  const by = new Map<string, string[]>();
  for (const face of faces) {
    if (face.tone !== 'dead' || face.cause === null) continue;
    by.set(face.cause, [...(by.get(face.cause) ?? []), face.label]);
  }
  return [...by].filter(([, labels]) => labels.length > 1).map(([cause, labels]) => ({cause, labels}));
}
