// The one audio player of the client. A single media element plays at most one take at a
// time: starting a take replaces the one before it, so two takes never sound together. Every
// take plays at its matched level (level.ts). Playback is inspection, not a verb: nothing here
// changes server state (law 11 keeps the three input triggers for verbs).
import { createStore, useStore, type StoreApi } from 'zustand';
import { UNMATCHED, type Level } from './level';

export type Loop = {start: number; end: number};

export type PlayerState = {
  /** The loaded take, playing or paused. */
  nodeId: string | null;
  playing: boolean;
  /** Seconds into the loaded take. */
  position: number;
  /** The loaded take's length in seconds, from its facts until the element reports it. */
  duration: number;
  loop: Loop | null;
  /** The level each take plays at, for the takes being auditioned together. */
  levels: Record<string, Level>;
  /** A plain line when playback failed, for the take that failed. */
  error: string | null;
};

/** The parts of an audio element the player uses; tests supply a fake. */
export type MediaLike = {
  src: string;
  currentTime: number;
  volume: number;
  preload: string;
  play(): Promise<void> | void;
  pause(): void;
  addEventListener(type: string, listener: () => void): void;
};

export const audioUrl = (id: string) => `/api/crexil/material/${encodeURIComponent(id)}/audio`;

const initial = (): PlayerState => ({nodeId: null, playing: false, position: 0, duration: 0, loop: null, levels: {}, error: null});

/** Why playback failed, in words. An interrupted start (a newer take replaced it) is no failure. */
function failure(error: unknown): string | null {
  const name = error instanceof DOMException || error instanceof Error ? error.name : '';
  if (name === 'AbortError') return null;
  if (name === 'NotAllowedError') return 'The browser blocked playback. Press play again.';
  return 'This take could not be played. Its file and record remain.';
}

export type Player = ReturnType<typeof createPlayer>;

export function createPlayer(make: () => MediaLike = () => new Audio()) {
  const store: StoreApi<PlayerState> = createStore<PlayerState>(initial);
  let element: MediaLike | null = null;

  const media = (): MediaLike => {
    if (element) return element;
    const made = make();
    made.preload = 'auto';
    made.addEventListener('play', () => store.setState({playing: true, error: null}));
    made.addEventListener('pause', () => store.setState({playing: false}));
    made.addEventListener('timeupdate', () => {
      const {loop} = store.getState();
      if (loop && made.currentTime >= loop.end) made.currentTime = loop.start;
      store.setState({position: made.currentTime});
    });
    made.addEventListener('ended', () => {
      const {loop} = store.getState();
      if (loop) {
        made.currentTime = loop.start;
        void start(made);
        return;
      }
      store.setState(state => ({playing: false, position: state.duration || state.position}));
    });
    made.addEventListener('loadedmetadata', () => {
      const reported = (made as Partial<HTMLMediaElement>).duration;
      if (typeof reported === 'number' && Number.isFinite(reported) && reported > 0) store.setState({duration: reported});
    });
    made.addEventListener('error', () => store.setState({playing: false, error: failure(new Error('MediaError'))}));
    element = made;
    return made;
  };

  const start = async (made: MediaLike) => {
    try {
      await made.play();
    } catch (error) {
      const line = failure(error);
      if (line) store.setState({playing: false, error: line});
    }
  };

  const volumeFor = (id: string) => (store.getState().levels[id] ?? UNMATCHED).volume;

  /** Loads a take without playing it. A take already loaded keeps its place. */
  const load = (id: string, duration = 0) => {
    const made = media();
    if (store.getState().nodeId === id) return made;
    made.pause();
    made.src = audioUrl(id);
    made.volume = volumeFor(id);
    store.setState({nodeId: id, playing: false, position: 0, duration, loop: null, error: null});
    return made;
  };

  /** Plays a take, from `from` seconds when given. Any other take stops. */
  const play = (id: string, options: {from?: number; duration?: number} = {}) => {
    const made = load(id, options.duration);
    if (options.from !== undefined) {
      made.currentTime = Math.max(0, options.from);
      store.setState({position: made.currentTime});
    }
    made.volume = volumeFor(id);
    return start(made);
  };

  return {
    store,
    play,
    pause() {
      element?.pause();
    },
    /** Plays the take when it is paused or not loaded, and pauses it when it plays. */
    toggle(id: string, options: {duration?: number} = {}) {
      const state = store.getState();
      if (state.nodeId === id && state.playing) {
        element?.pause();
        return Promise.resolve();
      }
      return play(id, options);
    },
    /** Moves the playhead of a take, loading it paused when another take is loaded. */
    seek(id: string, seconds: number, duration = 0) {
      const made = load(id, duration);
      const {duration: known} = store.getState();
      const limit = known > 0 ? known : Infinity;
      made.currentTime = Math.min(limit, Math.max(0, seconds));
      store.setState({position: made.currentTime});
    },
    /** Repeats a span of the loaded take; null plays straight through. */
    setLoop(loop: Loop | null) {
      if (loop && !(loop.end > loop.start)) return false;
      store.setState({loop});
      const made = element;
      if (loop && made && (made.currentTime < loop.start || made.currentTime >= loop.end)) {
        made.currentTime = loop.start;
        store.setState({position: loop.start});
      }
      return true;
    },
    /** The levels for the takes being auditioned together; the loaded take follows at once. */
    setLevels(levels: Record<string, Level>) {
      store.setState({levels});
      const {nodeId} = store.getState();
      if (element && nodeId) element.volume = volumeFor(nodeId);
    },
    /** Pauses when the loaded take is one of `ids`, so nothing plays once its surface is gone. */
    pauseIf(ids: readonly string[]) {
      const {nodeId, playing} = store.getState();
      if (playing && nodeId && ids.includes(nodeId)) element?.pause();
    },
    /** Stops playback and forgets the element and every take. */
    release() {
      element?.pause();
      element = null;
      store.setState(initial(), true);
    },
  };
}

/** The client's player. */
export const player = createPlayer();

export function usePlayer<T>(select: (state: PlayerState) => T): T {
  return useStore(player.store, select);
}
