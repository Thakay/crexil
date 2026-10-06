// The action card's verbs for the material being acted on, resolved as the legacy
// verb row resolves its chips (law 12: badge, never filter). Every verb stays in
// the card. One that fits the material and can start is live; one that fits the
// material but cannot start here (the wrong number of sources, no configured
// operation, a provider's terms) stays in the main group, de-emphasised, with its
// reason; one made for another medium drops to "Not for this material" with its
// reason. A take from a blind run not yet revealed starts nothing until the reveal,
// and says so without naming a provider. Adding a verb, or a medium's verbs, is
// adding entries here.
import type { Action, Payload } from './actions';
import { isHidden, type Material, type Operation } from './api';
import { BLIND_SOURCE } from './blind';
import { termsRefusal } from './capability';
import { mediumOf } from './media';

export type CardVerb = {
  label: string;
  action: Action;
  /** The sources, in the order the runner reads them. */
  parents: Material[];
  /** Start one preparation per source instead of one for all of them. */
  each?: boolean;
  /** It can start from here now. */
  live: boolean;
  /** Why it cannot, in plain words; null when it can. */
  reason: string | null;
  /** 'main' fits this material; 'notForThis' is made for another medium. */
  tier: 'main' | 'notForThis';
};

type Entry = {
  label: string;
  action: Action;
  /** The media it reads. */
  accepts: Payload[];
  /** Why this number of sources cannot be used, or null when it can. */
  count: (n: number) => string | null;
  /** A further requirement on media it accepts, or null when the material meets it. */
  fits?: (nodes: Material[]) => string | null;
  order?: (nodes: Material[]) => Material[];
  each?: boolean;
  /** Its runs go through a configured operation. Preparing an image is Crexil's own transform. */
  operation: boolean;
  /** Its runs feed the sources to a model, so a provider's terms may forbid it. */
  feedsModel: boolean;
};

/** The verb the L key starts: speak a line in the acting take's voice. */
export const SPEAK_VERB = 'Speak with this voice · L';

/** A voice take a line can be spoken with: its audio is on disk and it records a voice. */
const voiced = (nodes: Material[]) => !nodes.every(node => node.available) ? "This take's audio is unavailable. Its record remains."
  : nodes.every(node => node.audio?.voice) ? null : 'This take records no voice to speak with.';

const one = (n: number) => n === 1 ? null : `Acts on one source; ${n} are selected.`;
const several = (n: number) => n > 1 ? null : 'Needs two or more sources; one is selected.';
const any = () => null;

const ENTRIES: Entry[] = [
  {label: 'Combine sources · M', action: 'combine', accepts: ['image'], count: several, operation: true, feedsModel: true},
  {label: 'Continue from image', action: 'continue', accepts: ['image'], operation: true, feedsModel: true,
    count: n => n === 1 ? null : `Continues from one source; ${n} are selected. Continue each separately instead.`},
  {label: 'Crop, resize or arrange', action: 'prepare', accepts: ['image'], count: any, operation: false, feedsModel: false},
  {label: 'Edit regions', action: 'region', accepts: ['image'], count: one, operation: true, feedsModel: true},
  {label: 'Generate 3D', action: 'mesh', accepts: ['image'], count: one, operation: true, feedsModel: true},
  {label: 'Reduce mesh', action: 'decimate', accepts: ['3d'], count: one, operation: true, feedsModel: false},
  {label: 'Texture mesh', action: 'texture', accepts: ['3d'], count: one, operation: true, feedsModel: true},
  {label: 'Texture mesh from image', action: 'texture', accepts: ['3d', 'image'], operation: true, feedsModel: true,
    count: n => n === 2 ? null : `Needs one mesh and one image; ${n === 1 ? 'one is' : `${n} are`} selected.`,
    fits: nodes => nodes.some(node => node.payload === '3d') && nodes.some(node => node.payload === 'image')
      ? null : 'Needs one mesh and one image together.',
    order: nodes => [...nodes.filter(node => node.payload === '3d'), ...nodes.filter(node => node.payload === 'image')]},
  {label: 'Continue each separately', action: 'continue', accepts: ['image'], count: several, each: true, operation: true, feedsModel: true},
  {label: SPEAK_VERB, action: 'speak', accepts: ['audio'], count: one, operation: true, feedsModel: true, fits: voiced},
  {label: 'Speak with each voice', action: 'speak', accepts: ['audio'], count: several, each: true, operation: true, feedsModel: true,
    fits: voiced},
];

const PLURAL: Record<string, string> = {image: 'images', mesh: 'meshes', audio: 'audio'};
const plural = (noun: string) => PLURAL[noun] ?? `${noun}s`;
const words = (nouns: string[]) => nouns.length < 2 ? nouns.join('') : `${nouns.slice(0, -1).join(', ')} and ${nouns.at(-1)}`;

function wrongKind(entry: Entry, nodes: Material[]): string | null {
  const foreign = [...new Set(nodes.map(node => node.payload).filter(payload => !entry.accepts.includes(payload)))];
  if (!foreign.length) return null;
  const made = words(entry.accepts.map(payload => plural(mediumOf(payload).noun)));
  if (nodes.length > 1) return `Works on ${made}; the selection includes ${words(foreign.map(payload => plural(mediumOf(payload).noun)))}.`;
  const noun = mediumOf(foreign[0]).noun;
  return `Works on ${made}; this is ${noun === 'audio' ? 'audio' : /^[aeiou]/.test(noun) ? `an ${noun}` : `a ${noun}`}.`;
}

/** Why no configured operation can take these sources for this verb, or null when one can. */
function operationReason(entry: Entry, nodes: Material[], operations: readonly Operation[] | undefined): string | null {
  if (!entry.operation || operations === undefined) return null;
  const able = operations.filter(op => op.actions.includes(entry.action));
  if (!able.length) return 'No configured operation does this yet. Add one in Operations.';
  if (!entry.feedsModel) return null;
  const refusals = able.map(op => nodes.map(node => termsRefusal(node, op.provider)).find(Boolean) ?? null);
  return refusals.every(Boolean) ? refusals[0] : null;
}

export function cardVerbs(nodes: Material[], operations?: readonly Operation[]): CardVerb[] {
  if (!nodes.length) return [];
  const main: CardVerb[] = [], notForThis: CardVerb[] = [];
  for (const entry of ENTRIES) {
    const base = {label: entry.label, action: entry.action, parents: entry.order?.(nodes) ?? nodes, ...(entry.each ? {each: true} : {})};
    const foreign = wrongKind(entry, nodes);
    if (foreign) {
      notForThis.push({...base, live: false, reason: foreign, tier: 'notForThis'});
      continue;
    }
    const reason = (nodes.some(isHidden) ? BLIND_SOURCE : null) ?? entry.count(nodes.length) ?? entry.fits?.(nodes)
      ?? operationReason(entry, nodes, operations);
    main.push({...base, live: reason === null, reason, tier: 'main'});
  }
  return [...main, ...notForThis];
}
