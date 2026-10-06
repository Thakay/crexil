// The preparation actions, described once. The drawer's action list, a new
// direction's title, the source roles and the medium a run produces all read this
// table, so adding an action is one entry.
import type { Material, Preparation } from './api';

export type Action = Preparation['action'];
export type Payload = Material['payload'];
type SourceRole = {medium: Payload; role?: string};

export type ActionView = {
  /** Title of a new direction started with this action. */
  title: string;
  /** The word its section's starter shows for the kind of start: "Words", "Image",
   *  "Voice design". */
  start: string;
  /** Name in the drawer's action list; an unlisted action is never offered there. */
  label: string;
  listed: boolean;
  /** The medium its runs produce, which colours its lineage. */
  produces: Payload;
  /** Ordered inputs, when the runner reads its sources by position. */
  sources?: SourceRole[];
  /** The prompt is kept for the record and never sent as instructions. */
  recordedDescription?: boolean;
  /** The name of its words when they are not a prompt: "Line to speak". */
  words?: string;
  /** What its operations send, inside a sentence: "the line and its voice". */
  sends?: string;
};

export const ACTIONS: {readonly [K in Action]?: ActionView} = {
  generate: {title: 'New direction', start: 'Words', label: 'Generate from words', listed: true, produces: 'image', sends: 'the prompt'},
  continue: {title: 'Continue from an image', start: 'Image', label: 'Continue from one source', listed: true, produces: 'image'},
  combine: {title: 'Combined sources', start: 'Combined', label: 'Combine sources', listed: true, produces: 'image'},
  region: {title: 'Region study', start: 'Region edit', label: 'Edit a region', listed: true, produces: 'image'},
  custom: {title: 'Custom operation', start: 'Custom', label: 'Custom operation', listed: true, produces: 'image'},
  mesh: {title: 'Generate 3D', start: 'Generate 3D', label: 'Generate 3D from image', listed: true, produces: '3d',
    sources: [{medium: 'image'}], recordedDescription: true},
  decimate: {title: 'Reduce mesh', start: 'Reduce mesh', label: 'Reduce mesh', listed: true, produces: '3d',
    sources: [{medium: '3d', role: 'Shape'}], recordedDescription: true},
  texture: {title: 'Texture mesh', start: 'Texture mesh', label: 'Texture mesh from image', listed: true, produces: '3d',
    sources: [{medium: '3d', role: 'Shape'}, {medium: 'image', role: 'Appearance'}], recordedDescription: true},
  prepare: {title: 'Prepared image', start: 'Prepared image', label: 'Crop, resize or arrange', listed: true, produces: 'image'},
  'voice-design': {title: 'Voice design', start: 'Voice design', label: 'Design a voice', listed: true, produces: 'audio',
    words: 'Voice description', sends: 'the voice description and its preview line'},
  speak: {title: 'Spoken line', start: 'Spoken line', label: 'Speak a line', listed: true, produces: 'audio',
    sources: [{medium: 'audio', role: 'Voice'}], words: 'Line to speak', sends: 'the line and its voice'},
  sfx: {title: 'Sound effect', start: 'Sound effect', label: 'Make a sound', listed: true, produces: 'audio',
    words: 'Sound description', sends: 'the sound description'},
  import: {title: 'Imported material', start: 'Import', label: 'Import material', listed: false, produces: 'image'},
};

/** The action's view, or none for an action this client does not describe yet. */
export const actionView = (action: string | null | undefined): ActionView | undefined =>
  action && Object.hasOwn(ACTIONS, action) ? ACTIONS[action as Action] : undefined;

/** A section starter's word for an action's kind of start; an action this client does not
 *  describe yet says its own name. */
export const startWord = (action: string | null | undefined): string => actionView(action)?.start ?? action ?? 'Words';

/** The medium a frozen or saved action produces; an unknown action produces an image. */
export const producedBy = (action: string | null | undefined): Payload => actionView(action)?.produces ?? 'image';

/** What an action's operations send: its own words, the sources alone for an action
 *  whose description is only recorded, else the prompt and any sources. */
export function sentBy(action: string | null | undefined): string {
  const view = actionView(action);
  return view?.sends ?? (view?.recordedDescription ? 'the sources' : 'the prompt and any sources');
}
