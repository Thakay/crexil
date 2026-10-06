// The action card's verbs for the material being acted on. Each entry says when it
// applies and which preparation it starts; the card renders the entries that apply,
// in this order. Adding a verb, or a medium's verbs, is adding entries here.
import type { Action } from './actions';
import type { Material } from './api';

export type CardVerb = {
  label: string;
  action: Action;
  /** The sources, in the order the runner reads them. */
  parents: Material[];
  /** Start one preparation per source instead of one for all of them. */
  each?: boolean;
};

type Entry = {
  label: string;
  action: Action;
  applies: (nodes: Material[]) => boolean;
  order?: (nodes: Material[]) => Material[];
  each?: boolean;
};

const every = (payload: Material['payload']) => (nodes: Material[]) => nodes.every(node => node.payload === payload);
const one = (payload: Material['payload']) => (nodes: Material[]) => nodes.length === 1 && nodes[0].payload === payload;
const images = every('image');

const ENTRIES: Entry[] = [
  {label: 'Combine sources · M', action: 'combine', applies: nodes => nodes.length > 1 && images(nodes)},
  {label: 'Continue from image', action: 'continue', applies: nodes => nodes.length === 1 && images(nodes)},
  {label: 'Crop, resize or arrange', action: 'prepare', applies: images},
  {label: 'Edit regions', action: 'region', applies: one('image')},
  {label: 'Generate 3D', action: 'mesh', applies: one('image')},
  {label: 'Reduce mesh', action: 'decimate', applies: one('3d')},
  {label: 'Texture mesh', action: 'texture', applies: one('3d')},
  {label: 'Texture mesh from image', action: 'texture',
    applies: nodes => nodes.length === 2 && nodes.some(node => node.payload === '3d') && nodes.some(node => node.payload === 'image'),
    order: nodes => [...nodes.filter(node => node.payload === '3d'), ...nodes.filter(node => node.payload === 'image')]},
  {label: 'Continue each separately', action: 'continue', applies: nodes => nodes.length > 1 && images(nodes), each: true},
];

export function cardVerbs(nodes: Material[]): CardVerb[] {
  if (!nodes.length) return [];
  return ENTRIES.filter(entry => entry.applies(nodes)).map(entry => ({
    label: entry.label, action: entry.action, parents: entry.order?.(nodes) ?? nodes, ...(entry.each ? {each: true} : {}),
  }));
}
