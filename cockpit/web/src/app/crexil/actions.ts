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
  /** Name in the drawer's action list; an unlisted action is never offered there. */
  label: string;
  listed: boolean;
  /** The medium its runs produce, which colours its lineage. */
  produces: Payload;
  /** Ordered inputs, when the runner reads its sources by position. */
  sources?: SourceRole[];
  /** The prompt is kept for the record and never sent as instructions. */
  recordedDescription?: boolean;
};

export const ACTIONS: {readonly [K in Action]?: ActionView} = {
  generate: {title: 'New direction', label: 'Generate from words', listed: true, produces: 'image'},
  continue: {title: 'Continue from an image', label: 'Continue from one source', listed: true, produces: 'image'},
  combine: {title: 'Combined sources', label: 'Combine sources', listed: true, produces: 'image'},
  region: {title: 'Region study', label: 'Edit a region', listed: true, produces: 'image'},
  custom: {title: 'Custom operation', label: 'Custom operation', listed: true, produces: 'image'},
  mesh: {title: 'Generate 3D', label: 'Generate 3D from image', listed: true, produces: '3d',
    sources: [{medium: 'image'}], recordedDescription: true},
  decimate: {title: 'Reduce mesh', label: 'Reduce mesh', listed: true, produces: '3d',
    sources: [{medium: '3d', role: 'Shape'}], recordedDescription: true},
  texture: {title: 'Texture mesh', label: 'Texture mesh from image', listed: true, produces: '3d',
    sources: [{medium: '3d', role: 'Shape'}, {medium: 'image', role: 'Appearance'}], recordedDescription: true},
  prepare: {title: 'Prepared image', label: 'Crop, resize or arrange', listed: true, produces: 'image'},
  import: {title: 'Imported material', label: 'Import material', listed: false, produces: 'image'},
};

/** The action's view, or none for an action this client does not describe yet. */
export const actionView = (action: string | null | undefined): ActionView | undefined =>
  action && Object.hasOwn(ACTIONS, action) ? ACTIONS[action as Action] : undefined;

/** The medium a frozen or saved action produces; an unknown action produces an image. */
export const producedBy = (action: string | null | undefined): Payload => actionView(action)?.produces ?? 'image';
