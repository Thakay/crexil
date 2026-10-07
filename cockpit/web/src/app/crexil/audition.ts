// Keyboard audition in a grid room, decided without the DOM. Digits 1 to 9 play the take
// positions of one audio run: the run that holds focus, else the room's first audio run, so a
// row whose images come first still reaches its voices and sounds. Digit N plays the run's
// Nth position: each cell holds its positions in grid order from Run, and a position with no
// audible take keeps its number and does nothing, so numbers never shift while attempts land,
// fail or retry (law 8). Playing is inspection, never a verb (law 11).
import { isAudible } from '../audio/useAudition';
import { length } from '../audio/display';
import { label, type Material } from './api';
import type { Cell } from './cells';

/** One run's take positions in a grid room, in grid order. */
export type RunPositions = {run: string; takes: (Material | null)[]};

/** Take positions in grid order, an empty position as null. */
export function auditionTakes(cells: readonly Cell[]): (Material | null)[] {
  return cells.flatMap(cell => Array.from({length: cell.span}, (_, i) => cell.takes[i] ?? null));
}

/** The room's runs in grid order, each with its take positions. Material that came from no
 *  run, such as an import, belongs to none. */
export function roomRuns(cells: readonly Cell[]): RunPositions[] {
  const runs = new Map<string, Cell[]>();
  for (const cell of cells) if (cell.slot) runs.set(cell.slot.turn_id, [...(runs.get(cell.slot.turn_id) ?? []), cell]);
  return [...runs].map(([run, held]) => ({run, takes: auditionTakes(held)}));
}

/** The positions digits 1 to 9 play: those of the audio run that holds focus, else of the
 *  room's first audio run (a run with an audio take); none when the room holds no audio. */
export function digitPositions(runs: readonly RunPositions[], focused: string | null): (Material | null)[] {
  const audio = runs.filter(run => run.takes.some(take => take?.payload === 'audio'));
  return (audio.find(run => run.run === focused) ?? audio[0])?.takes ?? [];
}

/** The pair X switches between: the acting set, when it is exactly two audible takes of the room. */
export function pairOf(acting: readonly string[], takes: readonly (Material | null)[]): readonly [string, string] | null {
  if (acting.length !== 2) return null;
  const audible = acting.every(id => takes.some(take => take?.id === id && isAudible(take)));
  return audible ? [acting[0], acting[1]] : null;
}

/** The room's polite line for the take in the player: "Playing 0A.2 · 0.6 s · level matched";
 *  "Played" once it has played to its end, "Paused" when stopped before. */
export function playingLine(take: Material, playing: boolean, matched: boolean, ended = false): string {
  return [`${playing ? 'Playing' : ended ? 'Played' : 'Paused'} ${label(take)}`, length(take.audio?.duration_s),
    matched ? 'level matched' : 'as recorded'].filter(Boolean).join(' · ');
}

/** The room head's key hint, in the Esc · <where> idiom. */
export const keyHint = (audible: boolean) => audible ? 'Space play · 1–9 audition · X A/B · S select' : 'S select';
