// What a direction's section shows, computed without the DOM. On the Filmstrip a
// section shows its displayed selection, in grid order. In its grid room every attempt
// keeps its place (law 8): a slot reserves room at Run for every take one attempt of its
// run returns, then shows the takes its current attempt landed, primary first, or the
// attempt itself when nothing has landed. A blind run still in flight shows no take
// until it settles. Every list of material reads in grid order (byGridPosition), never
// in the order takes landed, which would tell a blind run's engines apart.
import type { Direction, Material, Snapshot } from './api';
import { withheldRuns } from './blind';

export type Slot = Snapshot['turns'][number]['slots'][number];
export type Group = Snapshot['execution_groups'][number];
type Turn = Snapshot['turns'][number];

/** A cell holds material, or an attempt with no material yet. */
export type Cell = {
  key: string;
  material: Material | null;
  slot: Slot | null;
  group: Group | null;
  /** The takes drawn side by side in the cell, primary first; at most `span`. */
  takes: Material[];
  /** The take positions the cell holds from Run, so a landing never moves a cell. */
  span: number;
  /** Its blind run is still in flight, so its takes are withheld. */
  withheld: boolean;
};

/** Material in grid order: by run, cell, attempt and output; material from no run (imported
 *  or prepared) first, in the order it arrived. */
export function byGridPosition<T extends Pick<Material, 'turn_idx' | 'slot_pos' | 'take' | 'output_index'>>(nodes: readonly T[]): T[] {
  const key = (node: T) => [node.turn_idx ?? -1, node.slot_pos ?? 0, node.take ?? 0, node.output_index];
  return [...nodes].sort((a, b) => {
    const x = key(a), y = key(b);
    const at = x.findIndex((value, i) => value !== y[i]);
    return at < 0 ? 0 : x[at] - y[at];
  });
}

/** No run reserves more positions per attempt than this. */
const MOST_TAKES = 8;

const groupOf = (data: Snapshot, slot: Slot | null) =>
  slot ? data.execution_groups.find(group => group.slot_id === slot.id && group.take === slot.attempt) ?? null : null;

function isPrimary(data: Snapshot, slot: Slot, node: Material): boolean {
  return data.execution_groups.some(group => group.slot_id === slot.id && group.take === slot.attempt
    && group.metadata.primary_output_id === group.outputs.find(output => output.node_id === node.id)?.id);
}

/** The takes one attempt of this run returns, read from the operations frozen with it:
 *  the most any of them returns, so every slot of a run, blind or not, holds the same room. */
export function takesPerAttempt(turn: Pick<Turn, 'dials'>): number {
  const frozen = (turn.dials as {crexil?: {operations?: {takes_per_attempt?: unknown}[]}} | null | undefined)?.crexil?.operations;
  const counts = (frozen ?? []).map(op => Number(op.takes_per_attempt)).filter(n => Number.isInteger(n) && n > 0);
  return Math.min(MOST_TAKES, Math.max(1, ...counts));
}

export function gridCells(direction: Direction, data: Snapshot, grid: boolean): Cell[] {
  const material = byGridPosition(data.nodes.filter(node => direction.node_ids.includes(node.id)));
  const slots = data.turns.flatMap(turn => turn.slots);
  const materialCell = (node: Material): Cell => {
    const slot = slots.find(slot => slot.id === node.slot_id) ?? null;
    return {key: node.id, material: node, slot, group: groupOf(data, slot), takes: [node], span: 1, withheld: false};
  };
  if (!grid) return material.filter(node => direction.selection.includes(node.id)).map(materialCell);
  const withheld = withheldRuns(data);
  const attempts = data.turns.filter(turn => direction.turn_ids.includes(turn.id)).flatMap(turn => {
    const span = takesPerAttempt(turn);
    return [...turn.slots].sort((a, b) => a.pos - b.pos).map((slot): Cell => {
      if (withheld.has(turn.id)) return {key: slot.id, material: null, slot, group: null, takes: [], span, withheld: true};
      const current = material.filter(node => node.slot_id === slot.id && node.take === slot.attempt);
      const shown = current.find(node => isPrimary(data, slot, node)) ?? current[0];
      if (!shown) return {key: slot.id, material: null, slot, group: groupOf(data, slot), takes: [], span, withheld: false};
      const rest = current.filter(node => node !== shown).sort((a, b) => a.output_index - b.output_index);
      return {...materialCell(shown), takes: [shown, ...rest].slice(0, span), span};
    });
  });
  return [...material.filter(node => node.slot_id === null).map(materialCell), ...attempts];
}
