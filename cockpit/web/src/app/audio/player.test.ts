import { describe, expect, it, vi } from 'vitest';
import { audioUrl, createPlayer, type MediaLike } from './player';

class FakeMedia implements MediaLike {
  src = '';
  currentTime = 0;
  volume = 1;
  preload = '';
  failNext: Error | null = null;
  private listeners = new Map<string, (() => void)[]>();
  play = vi.fn(async () => {
    if (this.failNext) {
      const error = this.failNext;
      this.failNext = null;
      throw error;
    }
    this.emit('play');
  });
  pause = vi.fn(() => this.emit('pause'));
  addEventListener(type: string, listener: () => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  emit(type: string) {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }
}

function setup() {
  const made: FakeMedia[] = [];
  const player = createPlayer(() => {
    const media = new FakeMedia();
    made.push(media);
    return media;
  });
  return {player, made};
}

const named = (name: string) => Object.assign(new Error(name), {name});

describe('the audio player', () => {
  it('plays one take at a time through one element', async () => {
    const {player, made} = setup();
    await player.play('a');
    await player.play('b');
    expect(made).toHaveLength(1);
    expect(made[0].src).toBe(audioUrl('b'));
    expect(made[0].pause).toHaveBeenCalled();
    expect(player.store.getState()).toMatchObject({nodeId: 'b', playing: true});
  });

  it('pauses a playing take and resumes it where it was', async () => {
    const {player, made} = setup();
    await player.play('a');
    made[0].currentTime = 1.5;
    await player.toggle('a');
    expect(player.store.getState().playing).toBe(false);
    await player.toggle('a');
    expect(made[0].currentTime).toBe(1.5);
    expect(player.store.getState().playing).toBe(true);
  });

  it('plays each take at its matched level, and moves the loaded take with new levels', async () => {
    const {player, made} = setup();
    player.setLevels({a: {gainDb: -6, volume: 0.5, matched: true}});
    await player.play('a');
    expect(made[0].volume).toBe(0.5);
    player.setLevels({a: {gainDb: 0, volume: 1, matched: true}});
    expect(made[0].volume).toBe(1);
    await player.play('unknown');
    expect(made[0].volume).toBe(1);
  });

  it('repeats a loop region and refuses a loop that ends before it starts', async () => {
    const {player, made} = setup();
    await player.play('a', {duration: 4});
    expect(player.setLoop({start: 2, end: 1})).toBe(false);
    expect(player.setLoop({start: 1, end: 2})).toBe(true);
    expect(made[0].currentTime).toBe(1);
    made[0].currentTime = 2.1;
    made[0].emit('timeupdate');
    expect(made[0].currentTime).toBe(1);
    made[0].emit('ended');
    expect(made[0].play).toHaveBeenCalledTimes(2);
  });

  it('seeks within the take, loading it paused when another take is loaded', async () => {
    const {player, made} = setup();
    await player.play('a', {duration: 3});
    player.seek('b', 9, 2);
    expect(player.store.getState()).toMatchObject({nodeId: 'b', playing: false, position: 2});
    expect(made[0].src).toBe(audioUrl('b'));
  });

  it('says plainly when the browser blocks playback, and ignores a start a newer take replaced', async () => {
    const {player, made} = setup();
    await player.play('a');
    made[0].failNext = named('NotAllowedError');
    await player.play('b');
    expect(player.store.getState().error).toBe('The browser blocked playback. Press play again.');
    made[0].failNext = named('AbortError');
    await player.play('c');
    expect(player.store.getState().error).toBeNull();
  });

  it('pauses only when the loaded take belongs to a surface that is going away', async () => {
    const {player, made} = setup();
    await player.play('a');
    player.pauseIf(['b']);
    expect(player.store.getState().playing).toBe(true);
    player.pauseIf(['a']);
    expect(player.store.getState().playing).toBe(false);
    player.release();
    expect(player.store.getState().nodeId).toBeNull();
    expect(made[0].pause).toHaveBeenCalled();
  });
});
