import { describe, expect, it } from 'vitest';
import { dbToVolume, MAX_ATTENUATION_DB, matchLevels } from './level';

describe('level matching', () => {
  it('turns the louder of two takes down to the quieter one', () => {
    const levels = matchLevels([{id: 'loud', loudness: -14}, {id: 'quiet', loudness: -20}]);
    expect(levels.loud.gainDb).toBeCloseTo(-6);
    expect(levels.loud.volume).toBeCloseTo(0.501, 3);
    expect(levels.quiet).toEqual({gainDb: 0, volume: 1, matched: true});
    expect(levels.loud.matched).toBe(true);
  });

  it('never boosts, so no take can clip', () => {
    const levels = matchLevels([{id: 'a', loudness: -9}, {id: 'b', loudness: -31}, {id: 'c', loudness: -18}]);
    for (const level of Object.values(levels)) {
      expect(level.gainDb).toBeLessThanOrEqual(0);
      expect(level.volume).toBeLessThanOrEqual(1);
    }
  });

  it('caps the attenuation, and plays a far quieter outlier as recorded and unmatched', () => {
    const levels = matchLevels([{id: 'a', loudness: -10}, {id: 'b', loudness: -12}, {id: 'outlier', loudness: -40}]);
    expect(levels.a.gainDb).toBeCloseTo(-MAX_ATTENUATION_DB);
    expect(levels.b.gainDb).toBeCloseTo(-16);
    expect(levels.outlier).toEqual({gainDb: 0, volume: 1, matched: false});
  });

  it('plays a take without a loudness measurement as recorded and says it is unmatched', () => {
    const levels = matchLevels([{id: 'short', loudness: null}, {id: 'measured', loudness: -16}]);
    expect(levels.short).toEqual({gainDb: 0, volume: 1, matched: false});
    expect(levels.measured).toEqual({gainDb: 0, volume: 1, matched: true});
    expect(matchLevels([{id: 'x', loudness: undefined}]).x.matched).toBe(false);
  });

  it('converts decibels to a volume between 0 and 1', () => {
    expect(dbToVolume(0)).toBe(1);
    expect(dbToVolume(-20)).toBeCloseTo(0.1);
    expect(dbToVolume(6)).toBe(1);
  });
});
