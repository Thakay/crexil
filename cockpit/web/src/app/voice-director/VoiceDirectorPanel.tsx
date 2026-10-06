// The voice director on the project surface: a door, and the column it opens. Closed, it is one
// small door; open, it is a column of its own at the right of the work, from the top bar to the
// footer, and it loads the conversation and the ElevenLabs SDK in their own chunk, so the
// project surface never pays for them until the artist asks. While the column is open it sets
// `--director-inset` (its width, in px) on the root element and nowhere else, and removes it on
// close; every Filmstrip surface keeps its right edge clear of that inset, so the column never
// covers a room's chrome or work (law 2). The closed door shows only while the Filmstrip itself
// is in view: a grid room, the tray, the drawer, an overlay, a room over the canvas or the
// record puts its own controls where the door floats.
//
// Focus moves to the column's heading when it opens, stays in the column when a control in it
// goes away, and returns to the door when it closes (or, while the door makes way, to where it
// came from). Escape inside the column is the column's own and never reaches the room behind:
// it closes an idle column, and during a session it returns focus to the work while the
// conversation carries on, since closing ends the session.
import { lazy, Suspense, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createStore, useStore, type StoreApi } from 'zustand';
import type { Actor } from '../crexil/api';
import { ViewContext, type View } from '../crexil/view';
import type { Door } from './door';
import css from './VoiceDirector.module.css';

const Director = lazy(() => import('./Director'));

/** The open column's width in px: the inset every Filmstrip surface yields while it is open. */
export const COLUMN_PX = 300;
/** The root element's property that carries the inset. */
export const INSET = '--director-inset';

type Layers = Pick<View, 'drawer' | 'room' | 'overlays' | 'panel' | 'record' | 'nestedRecord'>;
// Outside a project room nothing opens over the work.
const NO_ROOM = createStore<Layers>(() => ({drawer: null, room: null, overlays: [], panel: null, record: null,
  nestedRecord: null}));

/** Whether the Filmstrip itself is in view, with nothing open over it. */
function useFilmstripInView(): boolean {
  const room = useContext(ViewContext) as StoreApi<Layers> | null;
  return useStore(room ?? NO_ROOM, view => view.drawer === null && view.room === null && view.overlays.length === 0
    && view.panel === null && view.record === null && view.nestedRecord === null);
}

const connected = (element: HTMLElement | null) => element?.isConnected ? element : null;

export type VoiceDirectorPanelProps = {
  projectId: string;
  /** Who confirms runs and picks. Defaults to the surface's human artist. */
  artist?: Actor;
  /** The HTTP door; tests pass a fake. */
  door?: Door;
};

export function VoiceDirectorPanel({projectId, artist, door}: VoiceDirectorPanelProps) {
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState(false);
  const inView = useFilmstripInView();
  const column = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const doorButton = useRef<HTMLButtonElement>(null);
  // Where focus was before it entered the column, and the last control focused inside it.
  const origin = useRef<HTMLElement | null>(null);
  const inside = useRef<Element | null>(null);
  const returning = useRef(false);

  // The column claims its width before the first paint and gives it back when it closes.
  useLayoutEffect(() => {
    if (!open) return;
    const root = document.documentElement;
    root.style.setProperty(INSET, `${COLUMN_PX}px`);
    heading.current?.focus();
    return () => {root.style.removeProperty(INSET);};
  }, [open]);

  // A control that goes away with its section or card leaves focus on the column's heading,
  // never on the page.
  useEffect(() => {
    const element = column.current;
    if (!open || !element) return;
    const keep = new MutationObserver(() => {
      const gone = inside.current && !inside.current.isConnected;
      if (gone && (document.activeElement === null || document.activeElement === document.body)) heading.current?.focus();
    });
    keep.observe(element, {childList: true, subtree: true});
    return () => keep.disconnect();
  }, [open]);

  // Focus returns to the door, or, while the door makes way, to where it came from.
  useEffect(() => {
    if (open || !returning.current) return;
    returning.current = false;
    (doorButton.current ?? connected(origin.current))?.focus();
  }, [open]);

  const close = () => {
    returning.current = true;
    inside.current = null;
    setLive(false);
    setOpen(false);
  };
  // During a session Escape leaves the column for the work and the conversation carries on.
  const back = () => {
    inside.current = null;
    const target = connected(origin.current);
    if (target) target.focus();
    else if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  };

  if (!open) {
    if (!inView) return null;
    return <div className={css.dock}>
      <button ref={doorButton} type="button" className={css.door} aria-expanded={false}
        onClick={() => {origin.current = null; setOpen(true);}}>Voice director ›</button>
    </div>;
  }
  return <aside ref={column} className={css.panel} style={{width: COLUMN_PX}} tabIndex={-1} aria-label="Voice director"
    onFocus={event => {
      const from = event.relatedTarget;
      if (from instanceof HTMLElement && !event.currentTarget.contains(from)) origin.current = from;
      inside.current = event.target;
    }}
    onBlur={event => {
      const to = event.relatedTarget;
      if (to instanceof Node && !event.currentTarget.contains(to)) inside.current = null;
    }}
    onKeyDown={event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      if (live) back();
      else close();
    }}>
    <header className={css.head}>
      <h2 ref={heading} className={css.title} tabIndex={-1}>Voice director</h2>
      <button type="button" className={css.close} onClick={close}>{live ? 'End and close' : 'Esc · Close'}</button>
    </header>
    <Suspense fallback={<p className={css.loading} role="status">Opening the voice director…</p>}>
      <Director key={projectId} projectId={projectId} artist={artist} door={door} onLive={setLive}/>
    </Suspense>
  </aside>;
}
