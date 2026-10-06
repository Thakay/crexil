// What a material face, the picker and the record say about who made a take, what it cost
// and where its attempts stand, decided without the DOM. A blind take is named "Blind" or
// "Engine hidden" and makes no cost claim until its reveal; its run's totals stay visible
// (law 22). A take its local engine made where no money moved reads LOCAL (law 5).
import { engineLane } from './catalogue';
import { engineIdentity, engineLabel, money, type Direction, type Material, type Snapshot } from './api';

type Turn = Snapshot['turns'][number];
type Slot = Turn['slots'][number];

/** A face's money: LOCAL for a local take where no money moved, the confirmed charge, or
 *  why none is shown ("Unverified" for a hosted take whose charge is not reported yet). */
export function faceCost(node: Material): string {
  const identity = engineIdentity(node);
  if (identity.kind === 'hidden') return 'After reveal';
  if (identity.kind === 'named' && engineLane(identity.name) === 'local' && !node.charged_usd && !node.committed_usd) return 'Local';
  if (node.charged_usd != null) return money(node.charged_usd);
  return identity.kind === 'named' ? 'Unverified' : identity.kind === 'prepared' ? 'Derived' : 'Source';
}

/** The picker's rows: takes grouped by who made them, in the order given (grid order,
 *  byGridPosition); a blind run's takes share one row. */
export function pickerGroups(material: readonly Material[]): {label: string; nodes: Material[]}[] {
  const groups = new Map<string, Material[]>();
  for (const node of material) {
    const label = engineLabel(node, 'source');
    groups.set(label, [...(groups.get(label) ?? []), node]);
  }
  return [...groups].map(([label, nodes]) => ({label, nodes}));
}

const STATUS: Readonly<Record<Slot['status'], string>> = {queued: 'Queued', running: 'Running', landed: 'Landed',
  failed: 'Failed', held: 'Held', skipped: 'Skipped', set_aside: 'Set aside', late: 'Late'};

/** An attempt's state in words: "Landed", "Set aside". */
export const statusWord = (status: Slot['status']): string => STATUS[status] ?? status;

/** A run's attempts in one line for the record: who made each and where it stands. */
export function runLine(slots: readonly Pick<Slot, 'engine' | 'identity_hidden' | 'status'>[]): string {
  return slots.map(slot => `${engineLabel(slot, 'source')} · ${statusWord(slot.status)}`).join(' / ');
}

const counted = (n: number, word: string) => n ? [`${n} ${word}`] : [];

/** A section's own facts, the left slot of its foot (BIBLE: a section's states): nothing run
 *  yet; while its runs land, how far they have got; once landed, how many of its takes are
 *  chosen, and how many attempts failed, are held or were skipped. Counts only, so a blind
 *  run's foot tells nothing of its engines; while one is in flight it says only how far. */
export function sectionFoot(direction: Pick<Direction, 'state' | 'turn_ids' | 'selection' | 'node_ids'>,
  turns: readonly Turn[], hidden: ReadonlySet<string>): string {
  const runs = turns.filter(turn => direction.turn_ids.includes(turn.id));
  const slots = runs.flatMap(turn => turn.slots);
  if (!slots.length) return direction.state === 'draft' ? 'Preparation · nothing run yet' : `${direction.selection.length} of ${direction.node_ids.length} selected`;
  const count = (status: Slot['status']) => slots.filter(slot => slot.status === status).length;
  const done = slots.filter(slot => slot.status !== 'queued' && slot.status !== 'running').length;
  if (done < slots.length)
    return [`Landing · ${done} of ${slots.length}`, ...(runs.some(turn => hidden.has(turn.id)) ? [] : counted(count('failed'), 'failed'))].join(' · ');
  return [`${direction.selection.length} of ${direction.node_ids.length} selected`, ...counted(count('failed'), 'failed'),
    ...counted(count('held'), 'held'), ...counted(count('skipped'), 'skipped')].join(' · ');
}
