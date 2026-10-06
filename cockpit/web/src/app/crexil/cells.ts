// What a direction's section shows, computed without the DOM. On the Filmstrip a
// section shows its displayed selection. In its grid room every attempt keeps its
// place: each slot shows its primary output, any output of the current attempt, or
// the attempt itself when nothing has landed (law 8).
import type { Direction, Material, Snapshot } from './api';

export type Slot = Snapshot['turns'][number]['slots'][number];
export type Group = Snapshot['execution_groups'][number];

/** A cell holds material, or an attempt with no material yet. */
export type Cell = {key: string; material: Material | null; slot: Slot | null; group: Group | null};

const groupOf = (data: Snapshot, slot: Slot | null) =>
  slot ? data.execution_groups.find(group => group.slot_id === slot.id && group.take === slot.attempt) ?? null : null;

function isPrimary(data: Snapshot, slot: Slot, node: Material): boolean {
  return data.execution_groups.some(group => group.slot_id === slot.id && group.take === slot.attempt
    && group.metadata.primary_output_id === group.outputs.find(output => output.node_id === node.id)?.id);
}

export function gridCells(direction: Direction, data: Snapshot, grid: boolean): Cell[] {
  const material = data.nodes.filter(node => direction.node_ids.includes(node.id));
  const slots = data.turns.flatMap(turn => turn.slots);
  const materialCell = (node: Material): Cell => {
    const slot = slots.find(slot => slot.id === node.slot_id) ?? null;
    return {key: node.id, material: node, slot, group: groupOf(data, slot)};
  };
  if (!grid) return material.filter(node => direction.selection.includes(node.id)).map(materialCell);
  const attempts = data.turns.filter(turn => direction.turn_ids.includes(turn.id)).flatMap(turn => turn.slots.map(slot => {
    const current = material.filter(node => node.slot_id === slot.id && node.take === slot.attempt);
    const shown = current.find(node => isPrimary(data, slot, node)) ?? current[0];
    return shown ? materialCell(shown) : {key: slot.id, material: null, slot, group: groupOf(data, slot)};
  }));
  return [...material.filter(node => node.slot_id === null).map(materialCell), ...attempts];
}
