import { describe, expect, it } from 'vitest';
import type { Direction, Material } from './api';
import { continuationRow, recordedWords, wordsToCarry } from './family';

const BRIEF = 'A stout dwarf blacksmith, braided copper beard, soot-dark leather apron.';

const direction = (id: string, row: number, action: string, prompt: string, fields: Partial<Direction> = {}) => ({
  id, workspace_id: 'p', title: id, row_idx: row, state: 'submitted', revision: 1, selection: [], node_ids: [], turn_ids: [],
  set_aside: false, sequence_template: false, setup: {action, prompt}, ...fields,
}) as unknown as Direction;

const take = (id: string) => ({id}) as Pick<Material, 'id'>;

describe('continuationRow: a continuation lands on the row after its sources', () => {
  const rows = [direction('image', 0, 'generate', BRIEF, {node_ids: ['0B']}), direction('voice', 0, 'voice-design', 'Gruff', {node_ids: ['1A', '1A.3']}),
    direction('mesh', 1, 'mesh', '', {node_ids: ['2A']}), direction('far', 4, 'generate', 'Later', {node_ids: ['9A']})];
  it('takes the row below the source', () => {
    expect(continuationRow([take('0B')], rows)).toBe(1);
    expect(continuationRow([take('1A.3')], rows)).toBe(1);
    expect(continuationRow([take('2A')], rows)).toBe(2);
  });
  it('takes the row below the last of several sources, so every line runs down to its start', () => {
    expect(continuationRow([take('2A'), take('0B')], rows)).toBe(2);
    expect(continuationRow([take('0B'), take('9A')], rows)).toBe(5);
  });
  it('leaves the row open for words, or for a source no row holds', () => {
    expect(continuationRow([], rows)).toBeUndefined();
    expect(continuationRow([take('gone')], rows)).toBeUndefined();
  });
});

describe('wordsToCarry: the words a preparation can start from', () => {
  const image = direction('image', 0, 'generate', BRIEF, {title: 'Dwarf blacksmith', node_ids: ['0B']});
  const voice = direction('voice', 0, 'voice-design', 'Gruff, warm and low.', {title: 'Blacksmith voice', node_ids: ['1A']});
  const sound = direction('sound', 0, 'sfx', 'Iron-shod boots on stone.', {title: 'Footsteps'});
  const line = direction('line-a', 1, 'speak', 'Bring me the ore.', {title: 'Spoken line', node_ids: ['3A']});
  const mesh = direction('mesh', 1, 'mesh', 'For the record only.', {title: 'Generate 3D'});
  const fresh = direction('new', 0, 'generate', '', {state: 'draft', title: 'New direction'});
  it("offers the words of every section on its row, in the Filmstrip's order", () => {
    expect(wordsToCarry({id: 'new', row: 0, sources: []}, [image, voice, sound, fresh])).toEqual([
      {direction: 'image', title: 'Dwarf blacksmith', words: BRIEF},
      {direction: 'voice', title: 'Blacksmith voice', words: 'Gruff, warm and low.'},
      {direction: 'sound', title: 'Footsteps', words: 'Iron-shod boots on stone.'},
    ]);
  });
  it("offers a line read its row's lines first, then the words of its source's row", () => {
    const next = direction('line-b', 1, 'speak', '', {state: 'draft'});
    expect(wordsToCarry({id: 'line-b', row: 1, sources: ['1A']}, [image, voice, sound, line, mesh, next]).map(item => item.direction))
      .toEqual(['line-a', 'image', 'voice', 'sound']);
  });
  it('never offers its own words, a description kept for the record, an empty, discarded or set-aside section, or a repeat', () => {
    const twin = direction('twin', 0, 'generate', `  ${BRIEF}  `);
    const dropped = direction('dropped', 0, 'sfx', 'Thrown away.', {state: 'discarded'});
    const aside = direction('aside', 0, 'sfx', 'Kept aside.', {set_aside: true});
    const blank = direction('blank', 0, 'sfx', '   ');
    const prepared = direction('prepared', 0, 'prepare', 'A crop.');
    const imported = direction('imported', 0, 'import', 'Recorded external fixture.');
    const own = direction('own', 0, 'generate', 'My own words.');
    expect(wordsToCarry({id: 'own', row: 0, sources: []}, [image, twin, dropped, aside, blank, prepared, imported, own, mesh])
      .map(item => item.direction)).toEqual(['image']);
    expect(wordsToCarry({id: 'own', row: 1, sources: []}, [mesh, own])).toEqual([]);
  });
});

describe('recordedWords: a take keeps its words as typed and as submitted', () => {
  const typed = {request: {setup: {prompt: '  Bring me the ore.  '}}};
  it('reads an audio take as submitted from its frozen request', () => {
    expect(recordedWords({payload: 'audio', prompt: '  Bring me the ore.  '}, {...typed, submitted: {text: 'Bring me the ore.'}}))
      .toEqual({typed: '  Bring me the ore.  ', submitted: 'Bring me the ore.'});
  });
  it("reads an image as submitted from the take's frozen prompt, contributions included", () => {
    expect(recordedWords({payload: 'image', prompt: `${BRIEF}\n\nKeep the silhouette.`}, {request: {setup: {prompt: BRIEF}}, submitted: {}}))
      .toEqual({typed: BRIEF, submitted: `${BRIEF}\n\nKeep the silhouette.`});
  });
  it('claims neither while the record is unread or withheld until a reveal', () => {
    expect(recordedWords({payload: 'audio', prompt: 'Bring me the ore.'}, undefined)).toEqual({typed: null, submitted: null});
    expect(recordedWords({payload: 'image', prompt: BRIEF}, {request: null, submitted: null})).toEqual({typed: null, submitted: null});
  });
  it('treats empty words as none', () => {
    expect(recordedWords({payload: 'audio', prompt: ''}, {request: {setup: {prompt: ' '}}, submitted: {text: ''}}))
      .toEqual({typed: null, submitted: null});
  });
});
