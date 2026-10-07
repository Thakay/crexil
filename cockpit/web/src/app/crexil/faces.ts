// What a material face, the picker and the record say about who made a take, what it cost
// and where its attempts stand, decided without the DOM. A blind take is named "Blind" or
// "Engine hidden" and makes no cost claim until its reveal; its run's totals stay visible
// (law 22). A take its local engine made where no money moved reads LOCAL, on its face and in
// inspection alike (law 5). An attempt is one call to its engine, charged once however many
// takes it returns, and its first take carries that charge: its other takes read INCLUDED, so
// no surface counts the charge twice.
import { engineLane } from './catalogue';
import { engineIdentity, engineLabel, money, type Direction, type Material, type Snapshot } from './api';

type Turn = Snapshot['turns'][number];
type Slot = Turn['slots'][number];

/** Each attempt's one charge (null until reported) and how many takes it returned, by `attemptOf`. */
export type AttemptCharges = ReadonlyMap<string, {usd: number | null; takes: number}>;

/** The attempt a take came from: its slot and the slot's attempt number. */
export const attemptOf = (node: Pick<Material, 'slot_id' | 'take'>) => `${node.slot_id}:${node.take}`;

/** Every attempt's one charge and its count of takes, read once from a project's material. */
export function attemptCharges(nodes: readonly Material[]): AttemptCharges {
  const attempts = new Map<string, {usd: number | null; takes: number}>();
  for (const node of nodes) {
    if (!node.slot_id) continue;
    const seen = attempts.get(attemptOf(node));
    attempts.set(attemptOf(node), {usd: seen?.usd ?? node.charged_usd, takes: (seen?.takes ?? 0) + 1});
  }
  return attempts;
}

/** A take its local engine made where no money moved. */
function localAndFree(node: Material): boolean {
  const identity = engineIdentity(node);
  return identity.kind === 'named' && engineLane(identity.name) === 'local' && !node.charged_usd && !node.committed_usd;
}

/** A face's money: LOCAL for a local take where no money moved, the confirmed charge, INCLUDED
 *  for a take whose attempt's one charge another of its takes carries, or why none is shown
 *  ("Unverified" for a hosted take whose charge is not reported yet). */
export function faceCost(node: Material, charges: AttemptCharges = new Map()): string {
  const identity = engineIdentity(node);
  if (identity.kind === 'hidden') return 'After reveal';
  if (localAndFree(node)) return 'Local';
  if (node.charged_usd != null) return money(node.charged_usd);
  if (node.slot_id && charges.get(attemptOf(node))?.usd != null) return 'Included';
  return identity.kind === 'named' ? 'Unverified' : identity.kind === 'prepared' ? 'Derived' : 'Source';
}

/** Inspection's confirmed charge, read as the face reads it: LOCAL where no money moved; the
 *  amount the engine reported, said on every take of an attempt that returned several as that
 *  attempt's one charge; else that none is recorded yet. */
export function chargeLine(node: Material, charges: AttemptCharges): string {
  if (localAndFree(node)) return 'Local';
  const attempt = node.slot_id ? charges.get(attemptOf(node)) : undefined;
  const usd = node.charged_usd ?? attempt?.usd ?? null;
  if (usd === null) return 'No confirmed charge recorded';
  return attempt && attempt.takes > 1 ? `${money(usd)} for this attempt's ${attempt.takes} takes` : money(usd);
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

/** A section's own facts, the left slot of its foot, one of its states: nothing run yet;
 *  while its runs land, how far they have got; once landed, how many of its takes are
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
