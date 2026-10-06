// Blind runs on the client. A blind run withholds which engine made each take until an
// authored reveal (law 23). While any of its attempts is still queued or running, its
// takes stay withheld from the grid room, the picker and the record, so the order in
// which they land cannot tell a local engine from a hosted one; every cell keeps its
// place meanwhile (law 8), and the run's totals stay visible (law 22).
import type { Material, Snapshot } from './api';

type Turn = Snapshot['turns'][number];
export type BlindRun = NonNullable<Snapshot['blind_runs']>[number];

/** Why a hidden take cannot be continued from, in words that name no provider. */
export const BLIND_SOURCE = "This take's run is blind. Reveal it to continue from its takes.";

const FLIGHT = new Set(['queued', 'running']);

/** Blind runs not yet revealed. */
export const hiddenRuns = (data: Pick<Snapshot, 'blind_runs'>): Set<string> =>
  new Set((data.blind_runs ?? []).filter(run => !run.revealed).map(run => run.turn_id));

/** A run with an attempt still queued or running. */
export const inFlight = (turn: Pick<Turn, 'slots'>): boolean => turn.slots.some(slot => FLIGHT.has(slot.status));

/** Hidden runs still in flight: their takes stay withheld until every attempt settles. */
export function withheldRuns(data: Pick<Snapshot, 'blind_runs' | 'turns'>): Set<string> {
  const hidden = hiddenRuns(data);
  return new Set(data.turns.filter(turn => hidden.has(turn.id) && inFlight(turn)).map(turn => turn.id));
}

/** Whether a take belongs to a run in `withheld`. */
export const isWithheld = (node: Pick<Material, 'turn_id'>, withheld: ReadonlySet<string>): boolean =>
  node.turn_id !== null && withheld.has(node.turn_id);

/** How far a run has got: its settled attempts out of all of them. */
export const settled = (turn: Pick<Turn, 'slots'>) =>
  ({done: turn.slots.filter(slot => !FLIGHT.has(slot.status)).length, total: turn.slots.length});
