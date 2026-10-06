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
  const image = (id: string) => ({id, payload: 'image'}) as never;
  const mesh = (id: string) => ({id, payload: '3d'}) as never;
  const labels = (nodes: never[]) => cardVerbs(nodes).map(verb => verb.label);
  it('offers one image its continuation, preparation, regions and 3D', () => {
    expect(labels([image('a')])).toEqual(['Continue from image', 'Crop, resize or arrange', 'Edit regions', 'Generate 3D']);
  });
  it('offers several images combination and separate continuations', () => {
    expect(labels([image('a'), image('b')])).toEqual(['Combine sources · M', 'Crop, resize or arrange', 'Continue each separately']);
    expect(cardVerbs([image('a'), image('b')]).at(-1)?.each).toBe(true);
  });
  it('offers a mesh its reduction and texturing', () => {
    expect(labels([mesh('m')])).toEqual(['Reduce mesh', 'Texture mesh']);
  });
  it('textures a mesh from an image with the mesh as the first source', () => {
    const verbs = cardVerbs([image('a'), mesh('m')]);
    expect(verbs.map(verb => verb.label)).toEqual(['Texture mesh from image']);
    expect(verbs[0].parents.map(parent => (parent as {id: string}).id)).toEqual(['m', 'a']);
  });
  it('offers nothing when nothing is acted on', () => {
    expect(cardVerbs([])).toEqual([]);
  });
});
