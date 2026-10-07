// The project grid's attempt faces, decided without the DOM. A face holds a slot
// that has no material to show: what happened in the server's plain words (law 3),
// what it cost (law 5) and the next move as a button (law 4). A retry the scheduler
// is making says so, with its wait (law 9), and failed attempts that share a cause
// are named once (law 1). Whether money is in play follows the engine's lane, never
// its price: a hosted attempt always gets a money line. On this surface every new
// attempt is prepared, reviewed and priced before it runs, so a paid path opens a
// preparation and its price sits on the Run button. A face move that sends held work
// its run already reserved (a resume of the engine, the run or the project) carries
// that reservation as its price and is never primary (law 6). A queued, held or
// running attempt offers its cancel last; stopping never spends. A blind run's face
// never names its engine, its lane or its cost.
import { benchWords, costLine, receipt, waitingLine, type CellVM } from '../batch/grid';
import type { components } from '../../generated/openapi';
import { count, engineLabel, money, takeLabel, type Operation, type Snapshot } from './api';
import { engineLane } from './catalogue';
import type { Slot } from './cells';

export type Turn = Snapshot['turns'][number];
export type Control = NonNullable<Snapshot['execution_controls']>[number];

type RetryWait = components['schemas']['RetryWait'];

/** What the queue view adds about a slot: the bench that covers it while held, what the
 *  attempts that bench holds reserve (a resume sends them together), and the wait before
 *  a retry the scheduler is making. */
export type QueueFacts = {bench: string | null; benchKind: string | null; benchCause: string | null; reservedUsd?: number;
  retry?: Pick<RetryWait, 'attempt' | 'of' | 'next_try_at' | 'window'> | null};

export type RemedyId = 'collect' | 'copy-receipt' | 'resume-run' | 'resume-project' | 'reuse' | 'connections'
  | 'resume-engine' | 'cancel-attempt';

export type AttemptRemedy = {
  id: RemedyId;
  label: string;
  /** The one the eye lands on first. Never a costed move (law 6). */
  primary: boolean;
  /** What the move may spend, said on its face after its label: it sends work that
   *  reserves money (law 6). Null for a move that spends nothing. */
  price: string | null;
  /** What the move does, in plain words: its tooltip, else its label. */
  title?: string;
};

export type AttemptTone = 'dead' | 'waiting' | 'info';

export type AttemptFaceVM = {
  slotId: string;
  /** "0A": the run's index and the slot's letters, as material labels spell them (`takeLabel`). */
  label: string;
  /** The face's first words, in caps: FAILED, HELD, OUTCOME UNCERTAIN… */
  title: string;
  /** Red needs you, gold waits without you, grey-blue informs (the design's message colours). */
  tone: AttemptTone;
  engine: string;
  /** The server's plain line (law 3); raw engine text stays behind DETAILS. */
  cause: string | null;
  /** The lines the cause may fill (`causeLines`). */
  causeLines?: number;
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
  /** Every attempt of the project, which a resume of the paused project sends together. */
  projectSlots?: readonly Slot[];
  nowMs?: number;
};

/** Failure kinds whose files can still be retrieved from the same request, free. */
const COLLECTABLE = new Set(['collection', 'uncertain', 'timeout', 'restart', 'charged_blank']);
/** Failure kinds whose next move is to look at the engine's connection. */
const CONNECTION = new Set(['dead_key', 'unreachable', 'model_down']);

const REUSE_TITLE = 'Opens a new preparation from this direction. Nothing runs or spends until you review and run it.';
const COLLECT_TITLE = 'Checks this same request again. It never generates again and never charges again.';
const CONNECTIONS_TITLE = 'Opens Connections, where keys and runtimes are checked.';
export const RESUME_TITLE = 'Sends the attempts this engine holds. Each was reserved when its run was reviewed, '
  + 'and meets the spending limits again before it is sent.';
export const PAUSE_TITLE = 'Sends the attempts this pause holds. Each was reserved when its run was reviewed, '
  + 'and meets the spending limits again before it is sent.';
export const CHECK_TITLE = 'Reads the card now. The held attempts go when the card is free.';
/** What stopping means in each state (failure case 2.7); a blind run's names no lane. */
export const CANCEL_TITLE = {
  unsent: 'Skips this attempt. Nothing more is sent, and skipping costs nothing.',
  local: 'Asks the engine to stop this attempt. Local work costs no money.',
  sent: 'Its request is already sent, so it keeps this cell: the take still lands here if it arrives, '
    + 'and its charge stays unverified until the provider reports it.',
  hidden: 'Asks this attempt to stop. Whatever it still delivers lands in its own cell.',
} as const;

/** The operation a slot's engine came from: its id, or the engine an audio or mesh operation names. */
export function operationFor(engine: string, operations: readonly Operation[]): Operation | undefined {
  return operations.find(op => op.id === engine) ?? operations.find(op => op.id.endsWith(`:${engine}`));
}

/** Whether money is plausibly in play for this operation: it publishes a price. Used only
 *  for an engine the catalogue does not list (catalogue.ts). */
export function priced(op: Operation | undefined): boolean {
  if (!op) return false;
  return (op.maximum_usd_per_attempt ?? 0) > 0 || (op.price_usd ?? 0) > 0 || (op.tariff?.micro_usd ?? 0) > 0;
}

function landing(op: Operation | undefined): string {
  return op?.output_payload === 'image' ? 'an image' : op?.output_payload === '3d' ? 'a mesh' : 'a take';
}

/** Why an attempt is tried again, in plain words; none names a lane or a provider. */
const RETRY_WHY: Record<string, (lands: string) => string> = {
  rate_limit: lands => `The engine asked to wait. It is tried again on its own; nothing is charged until ${lands} lands.`,
  unreachable: () => 'The engine could not be reached. It is tried once more; if that fails, its attempts are held.',
  gpu_held: () => 'It ran out of memory while another program held the card. It waits for the card; the size is not the problem.',
};

const secondsUntil = (at: string, nowMs: number) => Math.max(0, Math.ceil((Date.parse(at) - nowMs) / 1000));

/** A cause without the sentence that says no money moved, when line two says it (law 5): the
 *  face says it once. */
const NOT_CHARGED = /^nothing was charged\.?$/i;
export function causeWithoutCharge(cause: string): string {
  return cause.split(/(?<=\.)\s+/).filter(sentence => !NOT_CHARGED.test(sentence.trim())).join(' ');
}

/** The lines a face's cause may fill, so a long cause keeps its next move in view where the face
 *  has room: four beside one row of moves and a one-line cost, three beside a cost that wraps,
 *  two on a face that also says a retry or its progress or holds more moves. A one-line cost is
 *  at most 25 characters of the face's mono type across a 158 px cell. */
export function causeLines(face: Pick<AttemptFaceVM, 'remedies' | 'retry' | 'progress' | 'cost'>): number {
  if (face.remedies.length !== 1 || face.retry || face.progress) return 2;
  return (face.cost?.length ?? 0) <= 25 ? 4 : 3;
}

/** A retry the scheduler is making, said while it happens (law 9): which attempt of how
 *  many, and while it waits, when it tries next. */
function retrying(slot: Slot, wait: QueueFacts['retry'], nowMs: number, lands: string): AttemptFaceVM['retry'] {
  if (slot.retry_of === null || (slot.status !== 'queued' && slot.status !== 'running')) return null;
  const why = RETRY_WHY[slot.retry_of]?.(lands) ?? `It is tried again after a ${slot.retry_of.replaceAll('_', ' ')}.`;
  if (slot.status === 'queued' && wait)
    return {label: `RETRYING · ATTEMPT ${wait.attempt} OF ${wait.of}`, why: `${why} Next try in ${secondsUntil(wait.next_try_at, nowMs)} s.`};
  return {label: `RETRYING · ATTEMPT ${slot.status === 'queued' ? slot.attempt + 1 : slot.attempt}`, why};
}

/** What the waiting attempts among `slots` reserve: what a resume of their pause sends (law 6). */
const reservedBy = (slots: readonly Slot[]) =>
  slots.filter(item => item.status === 'held' || item.status === 'queued').reduce((sum, item) => sum + (item.committed_usd ?? 0), 0);

export function attemptFace(slot: Slot, ctx: AttemptContext): AttemptFaceVM {
  const {turn, operations, controls, queue = null, projectSlots = turn.slots, nowMs = Date.now()} = ctx;
  const hidden = slot.identity_hidden === true;
  const op = hidden ? undefined : operationFor(slot.engine, operations);
  // Money is in play on a hosted engine whatever its price, and wherever money moved.
  const lane = hidden ? null : engineLane(slot.engine);
  const paid = !hidden && (lane === 'hosted' || (lane === null && priced(op))
    || (slot.committed_usd ?? 0) > 0 || (slot.charged_usd ?? 0) > 0);
  const engine = engineLabel(slot, 'face');
  const label = takeLabel(turn.idx, slot.pos);
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
    retry: retrying(slot, hidden ? null : queue?.retry, nowMs, landing(op)),
    receipt: null,
  };
  const reuse = (primary: boolean): AttemptRemedy => ({id: 'reuse', label: 'REUSE SETUP', primary, price: null, title: REUSE_TITLE});
  const collect = (primary: boolean): AttemptRemedy => ({id: 'collect', label: 'COLLECT FILES', primary, price: null, title: COLLECT_TITLE});
  const connections = (primary: boolean): AttemptRemedy =>
    ({id: 'connections', label: 'CONNECTIONS', primary, price: null, title: CONNECTIONS_TITLE});
  // Stopping comes last and never spends; while a retry shows, it gives up on the attempt.
  const cancel: AttemptRemedy = {id: 'cancel-attempt', label: face.retry ? 'GIVE UP ON THIS ATTEMPT' : 'CANCEL ATTEMPT',
    primary: false, price: null, title: slot.status !== 'running' ? CANCEL_TITLE.unsent
      : hidden ? CANCEL_TITLE.hidden : paid ? CANCEL_TITLE.sent : CANCEL_TITLE.local};
  // A resume sends what its pause or bench holds: free, it may lead; paid, it carries its
  // price and is never primary (law 6). A blind run's carries no price, which would tell its
  // engine apart.
  const resume = (id: RemedyId, words: string, reserved: number, title: string): AttemptRemedy => {
    const price = hidden || reserved <= 0 ? null : `UP TO ${money(reserved)}`;
    return {id, label: words, primary: price === null && id !== 'resume-engine', price, title};
  };

  switch (slot.status) {
    case 'failed': {
      face.tone = 'dead';
      face.title = uncertain ? 'OUTCOME UNCERTAIN' : 'FAILED';
      if (hidden) face.cost = 'Its cost shows after the reveal';
      else if (uncertain && paid) face.cost = (slot.charged_usd ?? 0) > 0 ? `${money(slot.charged_usd!)} was charged`
        : (slot.committed_usd ?? 0) > 0 ? `charge not confirmed · ${money(slot.committed_usd!)} reserved` : 'charge not confirmed';
      else face.cost = costLine(cell, turn.slots, nowMs);
      if (face.cause && face.cost === 'nothing was charged') face.cause = causeWithoutCharge(face.cause);
      if (slot.kind === 'charged_blank') {
        if (!hidden) face.receipt = receipt(slot, turn.prompt, label);
        face.remedies = [
          ...(face.receipt ? [{id: 'copy-receipt', label: 'COPY RECEIPT', primary: true, price: null,
            title: 'Copies the receipt for this charge.'} as AttemptRemedy] : []),
          collect(!face.receipt), reuse(false),
        ];
      } else if (slot.kind && COLLECTABLE.has(slot.kind)) face.remedies = [collect(true), reuse(false)];
      else if (slot.kind && CONNECTION.has(slot.kind)) face.remedies = [connections(true), reuse(false)];
      else face.remedies = [reuse(true)];
      break;
    }
    case 'skipped':
      face.cost = hidden ? 'Its cost shows after the reveal' : paid ? 'nothing was charged' : 'no GPU time spent; its turn was given back';
      face.remedies = [reuse(true)];
      break;
    case 'late':
      face.tone = 'waiting';
      face.cost = hidden ? 'Its cost shows after the reveal' : (slot.committed_usd ?? 0) > 0
        ? `sent · ${money(slot.committed_usd!)} reserved · lands here if it arrives` : 'sent · lands here if it arrives';
      face.remedies = [collect(true)];
      break;
    case 'held': {
      face.tone = 'waiting';
      face.cost = paid ? 'nothing charged yet' : 'waiting costs nothing';
      const run = controls.find(c => c.scope === 'run' && c.target_id === turn.id);
      const project = controls.find(c => c.scope === 'project');
      if (run?.paused || project?.paused) {
        // The pause is said once, in the face's own words.
        face.cause = null;
        face.progress = run?.paused ? 'This run is paused. Its waiting attempts stay here until it resumes.'
          : 'The project is paused. Its waiting attempts stay here until it resumes.';
        face.remedies = [run?.paused ? resume('resume-run', `RESUME RUN ${turn.idx}`, reservedBy(turn.slots), PAUSE_TITLE)
          : resume('resume-project', 'RESUME PROJECT', reservedBy(projectSlots), PAUSE_TITLE), cancel];
      } else if (hidden) {
        // One neutral face for a blind run's hold: it names no engine, lane, cause or price.
        face.cause = null;
        face.remedies = [reuse(true), resume('resume-engine', 'RESUME ENGINE', 0, RESUME_TITLE), cancel];
      } else {
        // A9 benches an engine for this run after repeated failures: preparing again leads
        // and TRY ONCE MORE sends it as it is. The card held by another program (C2) is read
        // again with CHECK AGAIN. A connection bench points to Connections. The hold in plain
        // words (law 3).
        const kind = queue?.benchKind ?? null;
        face.progress = !queue?.bench ? 'Its engine is held.'
          : benchWords({scope: queue.bench, kind: kind ?? '', cause: queue.benchCause}, id => engineLabel({engine: id}, 'face'));
        // A resume sends every attempt its bench holds, so it carries what they reserve (law 6).
        const reserved = queue?.reservedUsd ?? slot.committed_usd ?? 0;
        face.remedies = kind === 'gpu_held' ? [{...resume('resume-engine', 'CHECK AGAIN', 0, CHECK_TITLE), primary: true}, cancel]
          : kind === 'repeat' ? [reuse(true), resume('resume-engine', 'TRY ONCE MORE', reserved, RESUME_TITLE), cancel]
          : [connections(true), resume('resume-engine', 'RESUME ENGINE', reserved, RESUME_TITLE), cancel];
      }
      break;
    }
    case 'queued':
      face.tone = 'waiting';
      face.progress = hidden ? `${engine} · waiting its turn`
        : face.retry && queue?.retry ? `Next try in ${secondsUntil(queue.retry.next_try_at, nowMs)} s${queue.retry.window === 'provider' ? ', as the engine asked' : ''}.`
        : waitingLine(cell);
      face.remedies = [cancel];
      break;
    case 'running':
      face.tone = 'waiting';
      face.progress = `${engine} · running`;
      face.remedies = [cancel];
      break;
    default:
      break;
  }
  if (face.cause) face.causeLines = causeLines(face);
  return face;
}

/** A blind run's attempt while the run is still in flight. Every attempt of the run wears
 *  this one face, landed or not, so which takes come first, or wait for the one GPU, cannot
 *  tell its engines apart; all its takes show together once it settles. It offers no cancel:
 *  one only on the unsettled attempts would say which have landed. The run's own controls
 *  stop it. */
export function blindFace(turn: Turn, slot: Slot): AttemptFaceVM {
  const done = turn.slots.filter(item => !['queued', 'running'].includes(item.status)).length;
  return {
    slotId: slot.id, label: takeLabel(turn.idx, slot.pos), title: 'IN PROGRESS', tone: 'waiting', engine: 'Blind',
    cause: null, detail: null, cost: null, retry: null, remedies: [], receipt: null,
    progress: `${done} of ${count(turn.slots.length, 'attempt')} done. Every take shows when the run settles.`,
  };
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
