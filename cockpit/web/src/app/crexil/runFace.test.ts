import { describe, expect, it } from 'vitest';
import { runFace } from './runFace';

describe('runFace (law 6: the price on the face that spends it)', () => {
  it('prices a capped run with its maximum, and spells sub-cent money exactly', () => {
    expect(runFace({ready_attempts: 4, maximum_usd: .32, reserved_usd: 0}, 'hard_cap')).toEqual({label: 'Run 4 attempts · max $0.32', paid: true});
    expect(runFace({ready_attempts: 1, maximum_usd: .0032, reserved_usd: 0}, 'hard_cap')).toEqual({label: 'Run 1 attempt · max $0.0032', paid: true});
  });
  it('says a free run is free, and a free run may stay the default', () => {
    expect(runFace({ready_attempts: 2, maximum_usd: 0, reserved_usd: 0}, 'hard_cap')).toEqual({label: 'Run 2 attempts · free', paid: false});
  });
  it('states a published-tariff reservation, never a ceiling', () => {
    expect(runFace({ready_attempts: 3, maximum_usd: null, reserved_usd: .12}, 'published_tariff')).toEqual({label: 'Run 3 attempts · reserves $0.12', paid: true});
    expect(runFace({ready_attempts: 3, maximum_usd: null, reserved_usd: 0}, 'published_tariff')).toEqual({label: 'Run 3 attempts · free', paid: false});
  });
  it('never presents an unknown price as free', () => {
    expect(runFace({ready_attempts: 1, maximum_usd: null, reserved_usd: 0}, 'hard_cap')).toEqual({label: 'Run 1 attempt · price unknown', paid: true});
  });
});
