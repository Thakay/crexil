import { describe, expect, it } from 'vitest';
import type { Material } from './api';
import { auditionTakes, digitPositions, keyHint, pairOf, playingLine, roomRuns } from './audition';
import type { Cell, Slot } from './cells';

const take = (id: string, extra: Record<string, unknown> = {}) => ({id, payload: 'audio', available: true, turn_idx: 0,
  slot_pos: 0, output_index: 0, audio: {duration_s: 4.4}, ...extra}) as unknown as Material;
const cell = (takes: Material[], span = 1, run: string | null = null): Cell => ({key: takes[0]?.id ?? 'slot', material: takes[0] ?? null,
  slot: run ? {id: `${run}-slot`, turn_id: run} as Slot : null, group: null, takes, span, withheld: false});

describe('auditionTakes (law 8: digit N is position N)', () => {
  it('keeps every reserved position in grid order, an empty one as null', () => {
    const takes = auditionTakes([cell([take('a1'), take('a2'), take('a3')], 3), cell([], 3), cell([take('c1')], 3)]);
    expect(takes.map(item => item?.id ?? null)).toEqual(['a1', 'a2', 'a3', null, null, null, 'c1', null, null]);
  });
  it('never renumbers a take when an earlier attempt lands, fails or retries', () => {
    const running = auditionTakes([cell([], 1), cell([take('b')], 1)]);
    const landed = auditionTakes([cell([take('a')], 1), cell([take('b')], 1)]);
    expect(running.indexOf(running.find(item => item?.id === 'b')!)).toBe(1);
    expect(landed.findIndex(item => item?.id === 'b')).toBe(1);
  });
});

describe('the digits number one audio run (the row is not one run)', () => {
  // A row as the live seed laid it out: an import, nine images, the voice casting (two attempts
  // of three takes, the second still in flight), then a sound run.
  const images = Array.from({length: 9}, (_, i) => cell([take(`i${i}`, {payload: 'image'})], 1, 'images'));
  const cells = [cell([take('imported')]), ...images, cell([take('v1'), take('v2'), take('v3')], 3, 'voices'), cell([], 3, 'voices'),
    cell([take('s1')], 1, 'sounds')];
  const ids = (positions: (Material | null)[]) => positions.map(item => item?.id ?? null);
  it('groups the cells by run in grid order; material from no run is in none', () => {
    expect(roomRuns(cells).map(run => [run.run, ids(run.takes)])).toEqual([
      ['images', ['i0', 'i1', 'i2', 'i3', 'i4', 'i5', 'i6', 'i7', 'i8']],
      ['voices', ['v1', 'v2', 'v3', null, null, null]],
      ['sounds', ['s1']]]);
  });
  it("plays the room's first audio run when no run holds focus, or an image's run does", () => {
    expect(ids(digitPositions(roomRuns(cells), null))).toEqual(['v1', 'v2', 'v3', null, null, null]);
    expect(ids(digitPositions(roomRuns(cells), 'images'))).toEqual(['v1', 'v2', 'v3', null, null, null]);
  });
  it('plays the audio run that holds focus', () => {
    expect(ids(digitPositions(roomRuns(cells), 'sounds'))).toEqual(['s1']);
  });
  it('plays nothing in a room without audio', () => {
    expect(digitPositions(roomRuns(images), null)).toEqual([]);
  });
});

describe('pairOf: X works while two takes are marked', () => {
  const takes = [take('a'), null, take('b'), take('img', {payload: 'image'}), take('lost', {available: false})];
  it('is the acting set when it is exactly two audible takes of the room', () => {
    expect(pairOf(['a', 'b'], takes)).toEqual(['a', 'b']);
  });
  it('is nothing for one take, three, an image or a lost file', () => {
    expect(pairOf(['a'], takes)).toBeNull();
    expect(pairOf(['a', 'b', 'img'], takes)).toBeNull();
    expect(pairOf(['a', 'img'], takes)).toBeNull();
    expect(pairOf(['a', 'lost'], takes)).toBeNull();
    expect(pairOf(['a', 'elsewhere'], takes)).toBeNull();
  });
});

describe('the words the room says while a take plays', () => {
  it('names the take, its length and its level', () => {
    expect(playingLine(take('a', {turn_idx: 2, slot_pos: 1, output_index: 1}), true, true)).toBe('Playing 2B.2 · 4.4 s · level matched');
    expect(playingLine(take('a'), false, false)).toBe('Paused 0A · 4.4 s · as recorded');
    // a take that played to its end reads as played, never paused
    expect(playingLine(take('a'), false, true, true)).toBe('Played 0A · 4.4 s · level matched');
  });
  it('hints the audition keys only where there is something to hear', () => {
    expect(keyHint(true)).toBe('Space play · 1–9 audition · X A/B · S select');
    expect(keyHint(false)).toBe('S select');
  });
});
