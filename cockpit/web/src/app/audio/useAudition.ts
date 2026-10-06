// Keyboard audition of a run's audio takes. Space plays or pauses the focused take, digits 1 to
// 9 play the take in that grid position from its start, and X switches between the two marked
// takes at the same playhead (A/B). Every take plays at its matched level. A key is live only
// while `ready` says so for that key at the moment it is pressed, never with a modifier held and
// never while typing (the keymap's rule). Space never presses a focused button, link or other
// control in place of its own activation; on an audio take's face it plays. Playback keys are
// inspection, not verbs (law 11). The audio surface mounted last owns these keys, so an open
// inspector takes Space from the grid below.
import { useEffect, useMemo, useRef } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import type { Material } from '../crexil/api';
import { spaceActivates, useCommandKey } from '../crexil/keys';
import { matchLevels } from './level';
import { player } from './player';

const owners: symbol[] = [];

/** Registers an audio surface; the returned check is true while it is the newest one. */
export function useAudioKeys(): () => boolean {
  const me = useRef(Symbol('audio keys'));
  useEffect(() => {
    const own = me.current;
    owners.push(own);
    return () => {
      const at = owners.indexOf(own);
      if (at >= 0) owners.splice(at, 1);
    };
  }, []);
  return () => owners[owners.length - 1] === me.current;
}

/** A take that can be played: audio whose bytes are available. */
export const isAudible = (node: Material | null | undefined): node is Material =>
  Boolean(node) && node!.payload === 'audio' && node!.available;

/** The audition keys, each with its own readiness: Space, the digits and X. */
export type AuditionKey = 'space' | 'digit' | 'x';

export type AuditionOptions = {
  /** The take positions in grid order: digit N plays position N. An empty position (null) or a
   *  take of another medium keeps its number and plays nothing. */
  takes: readonly (Material | null)[];
  /** Whether a key may act now: false while the grid is covered, or for the digits while a verb
   *  card that uses digits is open. */
  ready?: (key: AuditionKey) => boolean;
  /** The take Space acts on, such as the focused face. Defaults to the loaded take, then the first. */
  focused?: () => string | null;
  /** The two marked takes X switches between. */
  pair?: () => readonly [string, string] | null;
  /** Told when a digit or X starts a take, so the surface can say so and show where it is. */
  onPlay?: (take: Material) => void;
};

export function useAudition({takes, ready, focused, pair, onPlay}: AuditionOptions) {
  const owner = useAudioKeys();
  const audible = useMemo(() => takes.filter(isAudible), [takes]);
  const latest = useRef({takes, audible, ready, focused, pair, onPlay});
  latest.current = {takes, audible, ready, focused, pair, onPlay};

  const signature = audible.map(take => `${take.id}:${take.audio?.loudness_lufs ?? ''}`).join('|');
  useEffect(() => {
    player.setLevels(matchLevels(latest.current.audible.map(take => ({id: take.id, loudness: take.audio?.loudness_lufs}))));
  }, [signature]);
  useEffect(() => () => player.pauseIf(latest.current.audible.map(take => take.id)), []);

  const live = (key: AuditionKey) => owner() && (latest.current.ready?.(key) ?? true) && latest.current.audible.length > 0;
  const current = (): Material | null => {
    const {audible: candidates, focused: chosen} = latest.current;
    const wanted = chosen?.() ?? null, loaded = player.store.getState().nodeId;
    return candidates.find(take => take.id === wanted) ?? candidates.find(take => take.id === loaded) ?? candidates[0] ?? null;
  };
  const start = (take: Material, from: number) => {
    void player.play(take.id, {from, duration: take.audio?.duration_s});
    latest.current.onPlay?.(take);
  };

  useCommandKey('space', () => {
    const take = current();
    if (take) void player.toggle(take.id, {duration: take.audio?.duration_s});
  }, () => live('space'), spaceActivates);

  useHotkeys('1,2,3,4,5,6,7,8,9', event => {
    if (!live('digit')) return;
    const take = latest.current.takes[Number(event.code.replace(/^(Digit|Numpad)/, '')) - 1];
    if (!isAudible(take)) return;
    event.preventDefault();
    start(take, 0);
  });

  useCommandKey('x', () => {
    const marked = latest.current.pair?.();
    if (!marked) return;
    const [a, b] = marked;
    const {nodeId, position} = player.store.getState();
    const next = latest.current.audible.find(take => take.id === (nodeId === a ? b : a));
    if (next) start(next, nodeId === a || nodeId === b ? position : 0);
  }, () => live('x') && Boolean(latest.current.pair?.()));
}
