// The voice director's door on the project surface. Closed, it is one small control; opened,
// it loads the conversation panel and the ElevenLabs SDK in their own chunk, so the project
// surface never pays for them until the artist asks. It stays outside the room's overlay
// stack: it floats beside the work and never covers or reflows it (law 2).
import { lazy, Suspense, useState } from 'react';
import type { Actor } from '../crexil/api';
import type { Door } from './door';
import css from './VoiceDirector.module.css';

const Director = lazy(() => import('./Director'));

export type VoiceDirectorPanelProps = {
  projectId: string;
  /** Who confirms runs and picks. Defaults to the surface's human artist. */
  artist?: Actor;
  /** The HTTP door; tests pass a fake. */
  door?: Door;
};

export function VoiceDirectorPanel({projectId, artist, door}: VoiceDirectorPanelProps) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return <div className={css.dock}>
      <button type="button" className={css.door} aria-expanded={false} onClick={() => setOpen(true)}>Voice director ›</button>
    </div>;
  }
  return <Suspense fallback={<aside className={css.panel} aria-label="Voice director"><p className={css.loading} role="status">Opening the voice director…</p></aside>}>
    <Director key={projectId} projectId={projectId} artist={artist} door={door} onClose={() => setOpen(false)}/>
  </Suspense>;
}
