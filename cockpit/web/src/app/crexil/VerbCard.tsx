// The verb card: the floating card beside the take or set being acted on.
// It floats inside the work area, below a room's head and working line and left of the
// voice director's column, so it never covers a room's chrome (law 2). As the board draws
// it, it sits beside the section of the take last acted on, right of it when there is room,
// else left of it, so that section's faces stay in reach for a set; a section too wide for
// either side has the card beside the take itself. It follows the take as the work scrolls
// and never reflows a row. Its verbs come from verbCard.ts; this draws them and places it.
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { Material, Operation } from './api';
import { cardVerbs, type CardVerb } from './verbCard';
import s from './Filmstrip.module.css';

export type Box = {left: number; top: number; right: number; bottom: number};
type Size = {width: number; height: number};

/** Where the card goes inside the work area (stage coordinates): beside the first box it fits
 *  beside (the take's section, then the take), level with that box's top, right of it, else
 *  left of it. Failing both, at the area's edge that covers less of the take, else the nearer
 *  one, level with the take. Always inside the area, `gap` from its edges, even for a take
 *  scrolled out of view. With no box, the top right. */
export function cardPlace(boxes: readonly Box[], stage: Size, card: Size, gap = 12): {left: number; top: number} {
  const rightmost = Math.max(gap, stage.width - card.width - gap);
  const lowest = Math.max(gap, stage.height - card.height - gap);
  const top = (box: Box) => Math.min(Math.max(gap, box.top), lowest);
  for (const box of boxes) {
    if (box.right + gap + card.width <= stage.width - gap && box.right >= 0) return {left: box.right + gap, top: top(box)};
    if (box.left - gap - card.width >= gap && box.left <= stage.width) return {left: box.left - gap - card.width, top: top(box)};
  }
  const take = boxes.at(-1);
  if (!take) return {left: rightmost, top: gap};
  const covers = (left: number) => Math.max(0, Math.min(left + card.width, take.right) - Math.max(left, take.left));
  const away = (left: number) => Math.abs(left + card.width / 2 - (take.left + take.right) / 2);
  const left = covers(gap) < covers(rightmost) || (covers(gap) === covers(rightmost) && away(gap) < away(rightmost)) ? gap : rightmost;
  return {left, top: top(take)};
}

export function VerbCard({nodes, operations, onInspect, onStart, children, onClose}: {
  nodes: Material[]; operations: readonly Operation[] | undefined;
  onInspect: () => void; onStart: (verb: CardVerb) => void; onClose: () => void;
  /** The card's own moves after its verbs: deliver, return. */
  children: ReactNode;
}) {
  const card = useRef<HTMLElement>(null);
  const [place, setPlace] = useState<{left: number; top: number} | null>(null);
  const anchor = nodes.at(-1)?.id;
  useLayoutEffect(() => {
    const element = card.current;
    const stage = element?.offsetParent;
    if (!element || !(stage instanceof HTMLElement)) return;
    let frame = 0;
    const measure = () => {
      const area = stage.getBoundingClientRect();
      const face = anchor ? document.querySelector(`[data-take="${CSS.escape(anchor)}"]`) : null;
      const boxes = [face?.closest('section'), face].flatMap(item => {
        const box = item?.getBoundingClientRect();
        return box && box.width > 0 ? [{left: box.left - area.left, right: box.right - area.left, top: box.top - area.top, bottom: box.bottom - area.top}] : [];
      });
      const next = cardPlace(boxes, {width: area.width, height: area.height}, {width: element.offsetWidth, height: element.offsetHeight});
      setPlace(old => old && old.left === next.left && old.top === next.top ? old : next);
    };
    const schedule = () => {cancelAnimationFrame(frame); frame = requestAnimationFrame(measure);};
    measure();
    stage.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    return () => {cancelAnimationFrame(frame); stage.removeEventListener('scroll', schedule, true); window.removeEventListener('resize', schedule);};
  }, [anchor, nodes.length]);
  const verbs = cardVerbs(nodes, operations);
  const draw = (verb: CardVerb) => verb.live ? <button key={verb.label} onClick={() => onStart(verb)}>{verb.label}</button>
    : <button key={verb.label} className={s.verbInert} aria-disabled="true" aria-label={verb.label} aria-describedby={`why-${verb.label.replace(/\W+/g, '-')}`}>{verb.label}
      <small id={`why-${verb.label.replace(/\W+/g, '-')}`}>{verb.reason}</small></button>;
  const other = verbs.filter(verb => verb.tier === 'notForThis');
  return <section ref={card} className={s.verbCard} aria-label="Actions on material"
    style={place ? {left: place.left, top: place.top} : {right: 12, top: 12}}>
    <div className={s.drawerHead}><span className={s.mono}>Acting on · {nodes.length}</span><button onClick={onClose}>Esc</button></div>
    <button onClick={onInspect}>Inspect · V</button>
    {verbs.filter(verb => verb.tier === 'main').map(draw)}
    {other.length > 0 && <details className={s.notForThis}><summary>Not for this material · {other.length}</summary>{other.map(draw)}</details>}
    {children}
  </section>;
}
