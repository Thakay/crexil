// Take audio never reaches the voice director's model: ElevenLabs' policy forbids its output as
// input to machine learning. The shared player asks this guard before any take starts, whoever
// starts it (the director's play_take, the grid's audition, inspection), and the guard mutes
// the microphone and turns the agent down before the first sound. The microphone stays muted
// TAIL_MS after the take stops, so the room's echo of its last sound is not heard either. A
// session's microphone opens while it connects, before it can be muted, so while `hold` names
// a reason no take starts at all.
import type { Player } from '../audio/player';

/** The agent's voice while a take plays, as a share of full volume. */
export const DUCKED = 0.2;
/** How long the microphone stays muted after a take stops, in ms. */
export const TAIL_MS = 400;

/** What the session's microphone and the agent's voice should be. */
export type Guard = {muted: boolean; ducked: boolean};

const PLAYING: Guard = {muted: true, ducked: true};
const TAIL: Guard = {muted: true, ducked: false};
const CLEAR: Guard = {muted: false, ducked: false};

/** Guards a session against the player's takes. `apply` hears the guard's state at once and on
 *  every change, synchronously, before the change sounds; `hold` returns the plain line that
 *  refuses a take while the microphone cannot be muted, or null. Returns the guard's removal. */
export function guardTakes(player: Player, apply: (guard: Guard) => void, hold: () => string | null): () => void {
  let tail: ReturnType<typeof setTimeout> | undefined;
  let current: Guard | null = null;
  const set = (guard: Guard) => {
    clearTimeout(tail);
    if (guard === current) return;
    current = guard;
    apply(guard);
  };
  set(player.store.getState().playing ? PLAYING : CLEAR);
  const removeListener = player.beforePlay(() => {
    const refusal = hold();
    if (refusal) return refusal;
    set(PLAYING);
  });
  const unsubscribe = player.store.subscribe((now, before) => {
    // A start the player was not asked about, such as the browser's own media keys, mutes the
    // moment it is seen.
    if (now.playing && !before.playing) set(PLAYING);
    if (!now.playing && before.playing) {
      set(TAIL);
      tail = setTimeout(() => set(CLEAR), TAIL_MS);
    }
  });
  return () => {
    clearTimeout(tail);
    removeListener();
    unsubscribe();
  };
}
