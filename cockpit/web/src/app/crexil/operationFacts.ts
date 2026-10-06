// What an operation tells the artist before anything runs: where its request goes and what
// goes with it, and how it is priced, in the unit its tariff counts. Words only, decided
// without the DOM; nothing here calls an engine.
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

/** Priced by a published tariff with no hard ceiling, so a hard cap refuses it (law 22). */
export const tariffPriced = (op: Operation): boolean =>
  op.maximum_usd_per_attempt === null && (op.tariff?.micro_usd ?? op.price_usd ?? 0) > 0;

/** The drawer's offer when tariff-priced operations are chosen under a hard cap. */
export function tariffOffer(ops: readonly Operation[]): string {
  const names = ops.map(op => op.label);
  const list = names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
  return names.length === 1
    ? `${list} is priced by its published tariff, with no hard ceiling. A hard cap refuses it before anything is sent.`
    : `${list} are priced by their published tariffs, with no hard ceiling. A hard cap refuses them before anything is sent.`;
}
