// Keyboard audition in a grid room, decided without the DOM. Digit N plays the room's
// Nth take position: each cell holds its positions in grid order from Run, and a
// position with no audible take keeps its number and does nothing, so numbers never
// shift while attempts land, fail or retry (law 8). Playing is inspection, never a
// verb (law 11).
import { isAudible } from '../audio/useAudition';
import { length } from '../audio/display';
import { label, type Material } from './api';
import type { Cell } from './cells';

/** The room's take positions in grid order, an empty position as null. */
export function auditionTakes(cells: readonly Cell[]): (Material | null)[] {
  return cells.flatMap(cell => Array.from({length: cell.span}, (_, i) => cell.takes[i] ?? null));
}

/** The pair X switches between: the acting set, when it is exactly two audible takes of the room. */
export function pairOf(acting: readonly string[], takes: readonly (Material | null)[]): readonly [string, string] | null {
  if (acting.length !== 2) return null;
  const audible = acting.every(id => takes.some(take => take?.id === id && isAudible(take)));
  return audible ? [acting[0], acting[1]] : null;
}

/** The room's polite line for the take in the player: "Playing 0A.2 · 0.6 s · level matched". */
export function playingLine(take: Material, playing: boolean, matched: boolean): string {
  return [`${playing ? 'Playing' : 'Paused'} ${label(take)}`, length(take.audio?.duration_s),
    matched ? 'level matched' : 'as recorded'].filter(Boolean).join(' · ');
}

/** The room head's key hint, in the Esc · <where> idiom. */
export const keyHint = (audible: boolean) => audible ? 'Space play · 1–9 audition · X A/B · S select' : 'S select';
