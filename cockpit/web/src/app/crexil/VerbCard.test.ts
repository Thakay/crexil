// Where the verb card floats: beside the section of the take acted on, else beside the take,
// inside the work area under a room's head and working line and left of the voice director's
// column, so the section's faces stay in reach and the take stays in view.
import { describe, expect, it } from 'vitest';
import { cardPlace } from './VerbCard';

const stage = {width: 1072, height: 700};
const card = {width: 285, height: 320};
const box = (left: number, top: number, width = 158, height = 132) => ({left, top, right: left + width, bottom: top + height});

describe('cardPlace', () => {
  it("sits right of the take's section, level with its top, when the card fits there", () => {
    expect(cardPlace([box(100, 80, 400, 175), box(300, 120)], stage, card)).toEqual({left: 512, top: 80});
  });
  it('sits left of a section near the right edge, so its faces stay in reach', () => {
    expect(cardPlace([box(560, 80, 400, 175), box(760, 120)], stage, card)).toEqual({left: 263, top: 80});
  });
  it('sits beside the take when its section is too wide for either side', () => {
    expect(cardPlace([box(20, 80, 1040, 175), box(300, 120)], stage, card)).toEqual({left: 470, top: 120});
    expect(cardPlace([box(20, 80, 1040, 175), box(860, 120)], stage, card)).toEqual({left: 563, top: 120});
  });
  it('keeps to the right edge of the work area when nothing has room beside it', () => {
    expect(cardPlace([box(100, 120)], {width: 560, height: 700}, card)).toEqual({left: 263, top: 120});
  });
  it('stays inside the work area for a take scrolled out of view', () => {
    expect(cardPlace([box(2200, 120)], stage, card).left).toBe(775);
    expect(cardPlace([box(-400, 120)], stage, card).left).toBe(12);
  });
  it('never leaves the work area: below its top edge, above its foot', () => {
    expect(cardPlace([box(300, -60)], stage, card).top).toBe(12);
    expect(cardPlace([box(300, 600)], stage, card).top).toBe(368);
    expect(cardPlace([box(300, 600)], {width: 1072, height: 200}, card).top).toBe(12);
  });
  it('without a take to sit beside, takes the top right of the work area', () => {
    expect(cardPlace([], stage, card)).toEqual({left: 775, top: 12});
  });
});
