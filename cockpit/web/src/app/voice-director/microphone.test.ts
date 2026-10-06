// The take guard against the shared player: the microphone mutes before a take sounds, however
// the take starts, stays muted 400 ms past its end, and a held session plays nothing.
import { afterEach, expect, test, vi } from 'vitest';
import { createPlayer, type MediaLike } from '../audio/player';
import { guardTakes, TAIL_MS, type Guard } from './microphone';

function setup() {
  const log: string[] = [];
  const listeners = new Map<string, (() => void)[]>();
  const emit = (type: string) => {for (const listener of listeners.get(type) ?? []) listener();};
  const element: MediaLike = {src: '', currentTime: 0, volume: 1, preload: '',
    play: async () => {log.push('take sounds'); emit('play');},
    pause: () => emit('pause'),
    addEventListener: (type, listener) => {listeners.set(type, [...(listeners.get(type) ?? []), listener]);}};
  const player = createPlayer(() => element);
  const apply = (guard: Guard) => log.push(`${guard.muted ? 'muted' : 'open'}${guard.ducked ? ', ducked' : ''}`);
  return {log, emit, player, apply};
}

afterEach(() => {vi.useRealTimers();});

test('mutes and ducks before the take sounds, and opens the microphone 400 ms after it stops', async () => {
  vi.useFakeTimers();
  const {log, player, apply} = setup();
  guardTakes(player, apply, () => null);
  await player.play('a');
  player.pause();
  vi.advanceTimersByTime(TAIL_MS - 1);
  expect(log).toEqual(['open', 'muted, ducked', 'take sounds', 'muted']);
  vi.advanceTimersByTime(1);
  expect(log.at(-1)).toBe('open');
});

test('starts muted when a take already plays, and mutes for a start the player was not asked about', async () => {
  const {log, emit, player, apply} = setup();
  await player.play('a');
  guardTakes(player, apply, () => null);
  expect(log).toEqual(['take sounds', 'muted, ducked']);
  player.pause();
  emit('play');
  expect(log.slice(-2)).toEqual(['muted', 'muted, ducked']);
});

test('while held, a take plays nothing and the guard keeps its state', async () => {
  const {log, player, apply} = setup();
  guardTakes(player, apply, () => 'Not while connecting.');
  await player.play('a');
  expect(log).toEqual(['open']);
  expect(player.store.getState()).toMatchObject({playing: false, error: 'Not while connecting.'});
});

test('once removed, the guard hears nothing more, a pending tail included', async () => {
  vi.useFakeTimers();
  const {log, player, apply} = setup();
  const remove = guardTakes(player, apply, () => null);
  await player.play('a');
  player.pause();
  remove();
  vi.advanceTimersByTime(TAIL_MS);
  await player.play('a');
  expect(log).toEqual(['open', 'muted, ducked', 'take sounds', 'muted', 'take sounds']);
});
