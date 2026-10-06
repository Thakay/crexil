// One keyboard for the Crexil surface. Escape is bound once: the newest layer that
// claims it handles it, and otherwise the project room closes its top layer (law 2:
// nothing traps the canvas; Escape always leads back). A plain-key command never
// fires with a modifier held or while typing in a field, and checks it is ready at
// the moment of the key, so a busy room ignores a repeat.
import { useEffect, useRef } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';

/** Returns true when it handled Escape, so no layer beneath sees it. */
export type EscapeHandler = (event: KeyboardEvent) => boolean;

const layers: {handle: EscapeHandler}[] = [];
let base: (() => void) | null = null;

/** Escape for the whole surface: the newest claiming layer, else the base close. */
export function escape(event: KeyboardEvent): boolean {
  for (let i = layers.length - 1; i >= 0; i--) if (layers[i].handle(event)) return true;
  if (!base) return false;
  base();
  return true;
}

/** A mounted layer claims Escape while it lives. Layers stack in mount order. */
export function useEscapeLayer(handle: EscapeHandler) {
  const current = useRef(handle);
  current.current = handle;
  useEffect(() => {
    const layer = {handle: (event: KeyboardEvent) => current.current(event)};
    layers.push(layer);
    return () => {layers.splice(layers.indexOf(layer), 1);};
  }, []);
}

/** The close that runs when no layer claims Escape: the project room's top layer. */
export function useEscapeBase(close: () => void) {
  const current = useRef(close);
  current.current = close;
  useEffect(() => {
    const own = () => current.current();
    base = own;
    return () => {if (base === own) base = null;};
  }, []);
}

/** Binds Escape once for the surface. Escape still works while typing in a field. */
export function useEscapeKey() {
  useHotkeys('esc', event => {
    if (event.defaultPrevented) return;
    if (escape(event)) event.preventDefault();
  }, {enableOnFormTags: true, enableOnContentEditable: true});
}

/** A plain-key command, live only while `ready` says so at the moment of the key. A key
 *  that `skip` claims for the focused element is left to it, unprevented. */
export function useCommandKey(key: string, run: () => void, ready: () => boolean,
  skip?: (event: KeyboardEvent) => boolean) {
  const current = useRef({run, ready, skip});
  current.current = {run, ready, skip};
  useHotkeys(key, event => {
    if (current.current.skip?.(event) || !current.current.ready()) return;
    event.preventDefault();
    current.current.run();
  });
}

/** Marks an audio take's face: Space plays it instead of pressing it. */
export const AUDIO_FACE = 'data-audio-face';
const ACTIVATES = 'button, a[href], summary, [role=button], [role=checkbox], [role=switch], [role=tab], [role=menuitem]';

/** Whether Space belongs to the focused control: a button, link or other control that
 *  Space activates (WCAG 2.1.1), unless it is an audio take's face, where Space plays. */
export function spaceActivates(event: KeyboardEvent): boolean {
  const target = event.target instanceof Element ? event.target : null;
  const control = target?.closest(ACTIVATES);
  return Boolean(control) && !control!.closest(`[${AUDIO_FACE}]`);
}
