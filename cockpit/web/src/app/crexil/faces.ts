// What a material face, the picker and the record say about who made a take and what
// it cost, decided without the DOM. A blind take is named "Blind" or "Engine hidden"
// and makes no cost claim until its reveal; its run's totals stay visible (law 22).
import { engineIdentity, engineLabel, money, type Material, type Snapshot } from './api';

/** A face's money: the confirmed charge, or why none is shown. */
export function faceCost(node: Material): string {
  const identity = engineIdentity(node);
  if (identity.kind === 'hidden') return 'After reveal';
  if (node.charged_usd != null) return money(node.charged_usd);
  return identity.kind === 'named' ? 'Unverified' : identity.kind === 'prepared' ? 'Derived' : 'Source';
}

/** The picker's rows: takes grouped by who made them; a blind run's takes share one row. */
export function pickerGroups(material: readonly Material[]): {label: string; nodes: Material[]}[] {
  const groups = new Map<string, Material[]>();
  for (const node of material) {
    const label = engineLabel(node, 'source');
    groups.set(label, [...(groups.get(label) ?? []), node]);
  }
  return [...groups].map(([label, nodes]) => ({label, nodes}));
}

/** A run's attempts in one line for the record: who made each and where it stands. */
export function runLine(slots: Snapshot['turns'][number]['slots']): string {
  return slots.map(slot => `${engineLabel(slot, 'source')} · ${slot.status}`).join(' / ');
}
