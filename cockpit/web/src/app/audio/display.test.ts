import { describe, expect, it } from 'vitest';
import { bars, clock, length, spokenSplit, timing } from './display';

describe('waveform bars', () => {
  it('keeps the loudest peak each bar covers', () => {
    expect(bars([0.1, 0.9, 0.2, 0.4], 2)).toEqual([0.9, 0.4]);
  });
  it('draws the requested number of bars from fewer peaks', () => {
    expect(bars([0.5, 1], 4)).toEqual([0.5, 0.5, 1, 1]);
  });
  it('draws silence for a take without peaks and clamps strays', () => {
    expect(bars([], 3)).toEqual([0, 0, 0]);
    expect(bars([2, -1, Number.NaN], 3)).toEqual([1, 0, 0]);
    expect(bars([0.3], 0)).toEqual([]);
  });
});

describe('time readouts', () => {
  it('reads seconds as a clock', () => {
    expect(clock(3.24)).toBe('0:03.2');
    expect(clock(61.05)).toBe('1:01.0');
    expect(clock(Number.NaN)).toBe('0:00.0');
  });
  it('states a take length on a face', () => {
    expect(length(4.44)).toBe('4.4 s');
    expect(length(12.6)).toBe('13 s');
    expect(length(null)).toBe('');
  });
});

describe('spoken words', () => {
  const alignment = {characters: ['S', 't', 'e', 'e', 'l'], character_start_times_seconds: [0, 0.1, 0.2, 0.3, 0.4], character_end_times_seconds: [0.1, 0.2, 0.3, 0.4, 0.5]};
  it('splits the words at the playhead when the engine reported timing', () => {
    expect(spokenSplit('Steel', alignment, 0.25)).toEqual({spoken: 'Ste', rest: 'el'});
    expect(spokenSplit('Steel', alignment, -1)).toEqual({spoken: '', rest: 'Steel'});
  });
  it('reads timing nested the way the provider returns it', () => {
    expect(timing({alignment, normalized_alignment: alignment})).not.toBeNull();
  });
  it('shows the words unmarked without timing, or with timing that does not line up', () => {
    expect(spokenSplit('Steel', null, 3)).toEqual({spoken: '', rest: 'Steel'});
    expect(timing({characters: ['a'], character_start_times_seconds: [0, 1]})).toBeNull();
  });
});
