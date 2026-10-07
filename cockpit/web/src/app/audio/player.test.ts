import { afterEach, describe, expect, it, vi } from 'vitest';
import { audioUrl, createPlayer, type MediaLike } from './player';

class FakeMedia implements MediaLike {
  src = '';
  currentTime = 0;
  volume = 1;
  preload = '';
  failNext: Error | null = null;
  /** What happened, in order, across this element and the test's listeners. */
  log: string[] = [];
  private listeners = new Map<string, (() => void)[]>();
  play = vi.fn(async () => {
    this.log.push('element plays');
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

afterEach(() => {vi.useRealTimers();});

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

  it('knows a take that played to its end from one paused before it, until it plays or moves again', async () => {
    const {player, made} = setup();
    await player.play('a', {duration: 4});
    await player.toggle('a');
    expect(player.store.getState()).toMatchObject({playing: false, ended: false});
    await player.toggle('a');
    made[0].emit('pause');
    made[0].emit('ended');
    expect(player.store.getState()).toMatchObject({playing: false, ended: true, position: 4});
    await player.toggle('a');
    expect(player.store.getState()).toMatchObject({playing: true, ended: false});
    made[0].emit('ended');
    player.seek('a', 1);
    expect(player.store.getState().ended).toBe(false);
    made[0].emit('ended');
    await player.play('b');
    expect(player.store.getState()).toMatchObject({nodeId: 'b', ended: false});
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
    made[0].emit('ended');
    expect(made[0].play).toHaveBeenCalledTimes(2);
  });

  it('wraps a loop on a timer due at its end, never on a late timeupdate', async () => {
    vi.useFakeTimers();
    const {player, made} = setup();
    await player.play('a', {duration: 4});
    player.setLoop({start: 1, end: 2});
    made[0].currentTime = 1.99;
    vi.advanceTimersByTime(999);
    expect(made[0].currentTime).toBe(1.99);
    made[0].currentTime = 2;
    vi.advanceTimersByTime(1);
    expect(made[0].currentTime).toBe(1);
    expect(player.store.getState().position).toBe(1);
    // The element runs a little behind the timer: the wrap waits for the end, then the next turn.
    made[0].currentTime = 1.996;
    vi.advanceTimersByTime(1000);
    expect(made[0].currentTime).toBe(1.996);
    made[0].currentTime = 2.001;
    vi.advanceTimersByTime(4);
    expect(made[0].currentTime).toBe(1);
    // timeupdate keeps the clock only.
    made[0].currentTime = 1.5;
    made[0].emit('timeupdate');
    expect(player.store.getState().position).toBe(1.5);
    expect(made[0].currentTime).toBe(1.5);
  });

  it('stops watching a loop once playback pauses or the loop is cleared', async () => {
    vi.useFakeTimers();
    const {player, made} = setup();
    await player.play('a', {duration: 4});
    player.setLoop({start: 1, end: 2});
    player.pause();
    made[0].currentTime = 2.5;
    vi.advanceTimersByTime(5000);
    expect(made[0].currentTime).toBe(2.5);
    await player.play('a');
    player.setLoop(null);
    made[0].currentTime = 3;
    vi.advanceTimersByTime(5000);
    expect(made[0].currentTime).toBe(3);
  });

  it('asks its listeners before the element plays: a start, a resume and a loop’s restart', async () => {
    const {player, made} = setup();
    player.beforePlay(() => {made[0].log.push('listener');});
    await player.play('a');
    await player.toggle('a');
    await player.toggle('a');
    player.setLoop({start: 0, end: 1});
    made[0].emit('ended');
    expect(made[0].log).toEqual(['listener', 'element plays', 'listener', 'element plays', 'listener', 'element plays']);
  });

  it('plays nothing when a listener refuses, and says the listener’s line', async () => {
    const {player, made} = setup();
    const remove = player.beforePlay(() => 'Not now.');
    await player.play('a');
    expect(made[0].play).not.toHaveBeenCalled();
    expect(player.store.getState()).toMatchObject({nodeId: 'a', playing: false, error: 'Not now.'});
    remove();
    await player.play('a');
    expect(made[0].play).toHaveBeenCalledTimes(1);
    expect(player.store.getState()).toMatchObject({playing: true, error: null});
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
