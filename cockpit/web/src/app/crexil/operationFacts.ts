// What an operation tells the artist before anything runs: where its request goes and what
// goes with it, how it is priced, in the unit its tariff counts, and whether its attempts
// retry on their own. Words only, decided without the DOM; nothing here calls an engine.
import { sentBy } from './actions';
import { money, type Operation } from './api';

const LOOPBACK = /^(127\.|localhost(:|$)|\[::1\])/;

/** Where Crexil sends the request, and what goes with it: off this machine to a hosted
 *  provider, or to a runtime on this machine. A runtime's own onward calls are its own;
 *  this line says only where Crexil sends. */
export function dataLine(op: Pick<Operation, 'data_destination'>, action: string | null | undefined): string {
  let host = op.data_destination;
  try {host = new URL(op.data_destination).host;} catch { /* not an address: said as given */ }
  const what = sentBy(action);
  return LOOPBACK.test(host) ? `Sends to ${host} on this machine: ${what}.` : `Sends off this machine to ${host}: ${what}.`;
}

/** How the operation is priced. A tariff counted in characters or seconds is said in its
 *  own unit, never as a price per attempt; a sub-cent amount shows exactly (law 5). */
export function priceLine(op: Operation): string {
  if (op.maximum_usd_per_attempt !== null) return `Maximum ${money(op.maximum_usd_per_attempt)} per attempt. ${op.cost_basis}.`;
  if (op.tariff && op.price_unit && op.price_unit !== 'attempt') return `${op.cost_basis}.`;
  return `${op.price_usd === null ? 'Price unknown' : `${money(op.price_usd)} estimated per attempt`}. ${op.cost_basis}.`;
}

const UNITS: Readonly<Record<string, string>> = {attempt: 'attempt', image: 'image', '1k_characters': '1,000 characters',
  second: 'second', generation: 'generation'};

/** The price a roster row carries beside its name, before it is chosen (law 6): its ceiling,
 *  its published price per image or attempt, or its tariff in its own unit; "price unknown"
 *  when it publishes none, which counts as over every cap (law 22). Unmetered work carries
 *  none. */
export function priceTag(op: Pick<Operation, 'maximum_usd_per_attempt' | 'price_usd' | 'price_unit' | 'tariff'>): string | null {
  if (op.maximum_usd_per_attempt !== null) return op.maximum_usd_per_attempt > 0 ? `up to ${money(op.maximum_usd_per_attempt)}` : null;
  const unit = UNITS[op.price_unit ?? 'attempt'] ?? 'attempt';
  if (op.tariff && op.price_unit && op.price_unit !== 'attempt')
    return op.tariff.micro_usd > 0 ? `${money(op.tariff.micro_usd / 1e6)} per ${unit}` : null;
  if (op.price_usd === null) return 'price unknown';
  return op.price_usd > 0 ? `${money(op.price_usd)} per ${unit}` : null;
}

/** Priced by a published tariff with no hard ceiling, so a hard cap refuses it (law 22). */
export const tariffPriced = (op: Operation): boolean =>
  op.maximum_usd_per_attempt === null && (op.tariff?.micro_usd ?? op.price_usd ?? 0) > 0;

/** Names as a sentence lists them: "A", "A and B", "A, B and C". */
const spoken = (names: readonly string[]) =>
  names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;

/** The drawer's offer when tariff-priced operations are chosen under a hard cap. */
export function tariffOffer(ops: readonly Operation[]): string {
  const list = spoken(ops.map(op => op.label));
  return ops.length === 1
    ? `${list} is priced by its published tariff, with no hard ceiling. A hard cap refuses it before anything is sent.`
    : `${list} are priced by their published tariffs, with no hard ceiling. A hard cap refuses them before anything is sent.`;
}

/** Whether a reviewed run's attempts retry on their own, from the review's answer per
 *  operation (law 9): none, all, or the ones that do, named by their catalogue labels. */
export function retriesLine(retries: Readonly<Record<string, boolean>>, ops: readonly Pick<Operation, 'id' | 'label'>[]): string {
  const ids = Object.keys(retries);
  const retrying = ids.filter(id => retries[id]);
  if (retrying.length === 0) return 'This run makes no automatic retries.';
  if (retrying.length === ids.length)
    return 'An attempt whose engine asks to wait or cannot be reached is tried again on its own, and the retry shows on that attempt.';
  const names = retrying.map(id => ops.find(op => op.id === id)?.label ?? id);
  return `${spoken(names)} ${names.length === 1 ? 'tries' : 'try'} an attempt again on its own when the engine asks to wait `
    + 'or cannot be reached, and the retry shows on that attempt. The other operations make no automatic retries.';
}
