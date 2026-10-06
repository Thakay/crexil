import { describe, expect, it } from 'vitest';
import type { Direction, Snapshot } from './api';
import { gridCells } from './cells';
import { cardVerbs } from './verbCard';

const slot = (id: string, attempt = 1, status = 'landed') => ({id, attempt, status, engine: 'flux', turn_id: 't1'});
const node = (id: string, extra: Record<string, unknown> = {}) => ({id, slot_id: null, take: null, payload: 'image', ...extra});
const group = (id: string, slot_id: string, take: number, outputs: {id: string; node_id: string}[], primary?: string) =>
  ({id, slot_id, take, outputs, metadata: primary ? {primary_output_id: primary} : {}});

function snapshot(): Snapshot {
  return {
    nodes: [
      node('import'),
      node('a1', {slot_id: 's1', take: 1}), node('a1-mask', {slot_id: 's1', take: 1}),
      node('b-old', {slot_id: 's2', take: 1}), node('b-new', {slot_id: 's2', take: 2}),
    ],
    turns: [{id: 't1', slots: [slot('s1'), slot('s2', 2), slot('s3', 1, 'failed')]}],
    execution_groups: [
      group('g1', 's1', 1, [{id: 'o-mask', node_id: 'a1-mask'}, {id: 'o-main', node_id: 'a1'}], 'o-main'),
    ],
  } as unknown as Snapshot;
}
const direction = {node_ids: ['import', 'a1', 'a1-mask', 'b-old', 'b-new'], turn_ids: ['t1'], selection: ['a1', 'import']} as unknown as Direction;

describe('gridCells', () => {
  it('shows only the displayed selection on the Filmstrip, in material order', () => {
    const cells = gridCells(direction, snapshot(), false);
    expect(cells.map(cell => cell.key)).toEqual(['import', 'a1']);
    expect(cells[1].slot?.id).toBe('s1');
    expect(cells[1].group?.id).toBe('g1');
    expect(cells[0].slot).toBeNull();
  });
  it('keeps every attempt in place in the grid room', () => {
    const cells = gridCells(direction, snapshot(), true);
    expect(cells.map(cell => cell.key)).toEqual(['import', 'a1', 'b-new', 's3']);
  });
  it('shows the primary output of a multi-file attempt and the current take of a retried slot', () => {
    const cells = gridCells(direction, snapshot(), true);
    expect(cells[1].material?.id).toBe('a1');
    expect(cells[2].material?.id).toBe('b-new');
  });
  it('shows an attempt with no material as the attempt itself', () => {
    const failed = gridCells(direction, snapshot(), true)[3];
    expect(failed.material).toBeNull();
    expect(failed.slot?.status).toBe('failed');
    expect(failed.group).toBeNull();
  });
});

describe('cardVerbs', () => {
  const image = (id: string, terms?: object) => ({id, payload: 'image', material: terms ? {origin: {terms}} : null}) as never;
  const mesh = (id: string) => ({id, payload: '3d'}) as never;
  const audio = (id: string, terms?: object) => ({id, payload: 'audio', material: terms ? {origin: {terms}} : null}) as never;
  const live = (nodes: never[], ops?: never[]) => cardVerbs(nodes, ops).filter(verb => verb.live).map(verb => verb.label);
  const find = (nodes: never[], label: string, ops?: never[]) => cardVerbs(nodes, ops).find(verb => verb.label === label)!;
  it('offers one image its continuation, preparation, regions and 3D', () => {
    expect(live([image('a')])).toEqual(['Continue from image', 'Crop, resize or arrange', 'Edit regions', 'Generate 3D']);
  });
  it('offers several images combination and separate continuations', () => {
    expect(live([image('a'), image('b')])).toEqual(['Combine sources · M', 'Crop, resize or arrange', 'Continue each separately']);
    expect(find([image('a'), image('b')], 'Continue each separately').each).toBe(true);
  });
  it('offers a mesh its reduction and texturing', () => {
    expect(live([mesh('m')])).toEqual(['Reduce mesh', 'Texture mesh']);
  });
  it('textures a mesh from an image with the mesh as the first source', () => {
    const verb = find([image('a'), mesh('m')], 'Texture mesh from image');
    expect(live([image('a'), mesh('m')])).toEqual(['Texture mesh from image']);
    expect(verb.parents.map(parent => (parent as {id: string}).id)).toEqual(['m', 'a']);
  });
  it('offers nothing when nothing is acted on', () => {
    expect(cardVerbs([])).toEqual([]);
  });
  it('keeps every verb in the card: a wrong medium drops to its own group with the reason (law 12)', () => {
    const verbs = cardVerbs([image('a')]);
    expect(verbs).toHaveLength(9);
    expect(find([image('a')], 'Reduce mesh')).toMatchObject({live: false, tier: 'notForThis', reason: 'Works on meshes; this is an image.'});
    expect(find([mesh('m')], 'Edit regions')).toMatchObject({live: false, tier: 'notForThis', reason: 'Works on images; this is a mesh.'});
    expect(find([audio('v')], 'Generate 3D').reason).toBe('Works on images; this is audio.');
    expect(find([image('a'), mesh('m')], 'Combine sources · M').reason).toBe('Works on images; the selection includes meshes.');
    expect(verbs.map(verb => verb.tier)).toEqual([...verbs.filter(v => v.tier === 'main'), ...verbs.filter(v => v.tier === 'notForThis')].map(v => v.tier));
  });
  it('keeps a verb that fits the medium but not the count in the main group, inert, with the count in words', () => {
    expect(find([image('a'), image('b')], 'Edit regions')).toMatchObject({live: false, tier: 'main', reason: 'Acts on one source; 2 are selected.'});
    expect(find([image('a')], 'Combine sources · M').reason).toBe('Needs two or more sources; one is selected.');
    expect(find([image('a'), image('b')], 'Texture mesh from image').reason).toBe('Needs one mesh and one image together.');
  });
  it('says when no configured operation performs a verb, and never for its own image preparation', () => {
    const ops = [{id: 'raw', actions: ['generate', 'continue'], provider: null}] as never[];
    expect(find([image('a')], 'Generate 3D', ops)).toMatchObject({live: false, reason: 'No configured operation does this yet. Add one in Operations.'});
    expect(find([image('a')], 'Continue from image', ops).live).toBe(true);
    expect(find([image('a')], 'Crop, resize or arrange', []).live).toBe(true);
  });
  it("refuses a verb whose every operation a source's provider terms forbid, in the provider's words", () => {
    const terms = {no_ai_input: true, allows: ['elevenlabs'], reason: "ElevenLabs' terms do not allow its audio as input to another AI model."};
    const ops = [{id: 'qwen', actions: ['continue'], provider: 'comfyui'}] as never[];
    expect(find([image('a', terms)], 'Continue from image', ops)).toMatchObject({live: false, reason: terms.reason});
    const allowed = [...ops, {id: 'eleven', actions: ['continue'], provider: 'elevenlabs'}] as never[];
    expect(find([image('a', terms)], 'Continue from image', allowed).live).toBe(true);
    expect(find([image('a', terms)], 'Crop, resize or arrange', ops).live).toBe(true);
  });
});
