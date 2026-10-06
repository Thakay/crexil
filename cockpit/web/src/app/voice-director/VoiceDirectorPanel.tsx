// The voice director's door on the project surface. Closed, it is one small control; opened,
// it loads the conversation panel and the ElevenLabs SDK in their own chunk, so the project
// surface never pays for them until the artist asks. It stays outside the room's overlay
// stack: it floats beside the work and never covers or reflows it (law 2). The closed door
// shows only while the Filmstrip itself is in view: a grid room, the tray, an overlay, a room
// over the canvas or the record puts its own head, with its Esc control, where the door floats.
import { lazy, Suspense, useContext, useState } from 'react';
import { createStore, useStore, type StoreApi } from 'zustand';
import type { Actor } from '../crexil/api';
import { ViewContext, type View } from '../crexil/view';
import type { Door } from './door';
import css from './VoiceDirector.module.css';

const Director = lazy(() => import('./Director'));

type Heads = Pick<View, 'room' | 'overlays' | 'panel' | 'record' | 'nestedRecord'>;
// Outside a project room no room head takes the door's place.
const NO_ROOM = createStore<Heads>(() => ({room: null, overlays: [], panel: null, record: null, nestedRecord: null}));

/** Whether a room head fills the band under the top bar, where the closed door floats. */
function useRoomHead(): boolean {
  const room = useContext(ViewContext) as StoreApi<Heads> | null;
  return useStore(room ?? NO_ROOM, view => view.room !== null || view.overlays.length > 0 || view.panel !== null
    || view.record !== null || view.nestedRecord !== null);
}

export type VoiceDirectorPanelProps = {
  projectId: string;
  /** Who confirms runs and picks. Defaults to the surface's human artist. */
  artist?: Actor;
  /** The HTTP door; tests pass a fake. */
  door?: Door;
};

export function VoiceDirectorPanel({projectId, artist, door}: VoiceDirectorPanelProps) {
  const [open, setOpen] = useState(false);
  const roomHead = useRoomHead();
  if (!open) {
    if (roomHead) return null;
    return <div className={css.dock}>
      <button type="button" className={css.door} aria-expanded={false} onClick={() => setOpen(true)}>Voice director ›</button>
    </div>;
  }
  return <Suspense fallback={<aside className={css.panel} aria-label="Voice director"><p className={css.loading} role="status">Opening the voice director…</p></aside>}>
    <Director key={projectId} projectId={projectId} artist={artist} door={door} onClose={() => setOpen(false)}/>
  </Suspense>;
}
