// The voice director on the project surface: a door, and the column it opens. The door sits in
// the top bar beside the project's other doors, where no room's Esc control ever is, whatever
// is open; the column is a column of its own at the right of the work, from the top bar to the
// footer, drawn at the end of the page, and it loads the conversation and the ElevenLabs SDK in
// their own chunk, so the project surface never pays for them until the artist asks. While the
// column is open it sets `--director-inset` (its width, in px) on the root element and nowhere
// else, and removes it on close; every Filmstrip surface keeps its right edge clear of that
// inset, so the column never covers a room's chrome or work (law 2).
//
// Focus moves to the column's heading when it opens, stays in the column when a control in it
// goes away, and returns to the door when it closes. The door closes an idle column and brings
// focus into a column in session, since closing ends the session. Escape inside the column is
// the column's own and never reaches the room behind: it closes an idle column, and during a
// session it returns focus to the work while the conversation carries on.
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Actor } from '../crexil/api';
import type { Door } from './door';
import css from './VoiceDirector.module.css';

const Director = lazy(() => import('./Director'));

/** The open column's width in px: the inset every Filmstrip surface yields while it is open. */
export const COLUMN_PX = 300;
/** The root element's property that carries the inset. */
export const INSET = '--director-inset';

const connected = (element: HTMLElement | null) => element?.isConnected ? element : null;

export type VoiceDirectorPanelProps = {
  projectId: string;
  /** Who confirms runs and picks. Defaults to the surface's human artist. */
  artist?: Actor;
  /** The HTTP door; tests pass a fake. */
  door?: Door;
  /** The door's look, from the bar that seats it; its own otherwise. */
  doorClassName?: string;
  /** Opens a prepared run in the project's drawer, where a card sends a run that changed. */
  openRun?(runId: string): void;
};

export function VoiceDirectorPanel({projectId, artist, door, doorClassName, openRun}: VoiceDirectorPanelProps) {
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState(false);
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

  // Focus returns to the door.
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

  const doorway = <button ref={doorButton} type="button" className={doorClassName ?? css.door} aria-expanded={open}
    onClick={() => {
      if (!open) {origin.current = null; setOpen(true);}
      else if (live) heading.current?.focus();
      else close();
    }}>Voice director ›</button>;
  if (!open) return doorway;
  return <>{doorway}{createPortal(<aside ref={column} className={css.panel} style={{width: COLUMN_PX}} tabIndex={-1} aria-label="Voice director"
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
      <Director key={projectId} projectId={projectId} artist={artist} door={door} onLive={setLive} openRun={openRun}/>
    </Suspense>
  </aside>, document.body)}</>;
}
