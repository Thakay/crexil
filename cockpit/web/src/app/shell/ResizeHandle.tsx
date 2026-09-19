// The resize handle, one component for the shell's three seams:
// sidebar | canvas, canvas | tray, canvas / dock. Hand-rolled on pointer
// events rather than a panel library (a dependency needs a reason, and
// the proven one commits React state per frame): the drag writes the
// island's custom property straight onto the shell root — layout follows,
// nothing re-renders — and only the release commits to the store (the perf
// bar: no setState mid-drag). A click that never moves commits
// nothing, so the dock's cap can't be set by accident at its content height.
//
// It is a focusable separator (WAI-ARIA window splitter): the arrows move
// the seam the way they point, Home/End go to the bounds, double-click
// resets the island to the default geometry; the pointer is not the only
// way to move an edge.
import {
  useCallback,
  useRef,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from "react";
import { useUi } from "../state/ui";
import { clampSize, ISLANDS, KEY_STEP, liveMax, type Island } from "./layout";
import styles from "./Shell.module.css";

type Props = {
  island: Island;
  /** the shell root: the property is written here, the bounds read off its box */
  shell: RefObject<HTMLDivElement | null>;
  label: string;
};

type Drag = {
  pointerId: number;
  start: number;
  base: number;
  max: number;
  last: number;
  moved: boolean;
};

const GROW_KEY: Record<Island, string> = {
  sidebar: "ArrowRight",
  tray: "ArrowLeft",
  dock: "ArrowUp",
};
const SHRINK_KEY: Record<Island, string> = {
  sidebar: "ArrowLeft",
  tray: "ArrowRight",
  dock: "ArrowDown",
};

export function ResizeHandle({ island, shell, label }: Props) {
  const spec = ISLANDS[island];
  const size = useUi((s) => s.layout[island]);
  const setIsland = useUi((s) => s.setIsland);
  const grip = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  // The island's live size, read off the seam's own position: the sidebar
  // ends where its handle sits, the tray and the dock begin there. Measured
  // rather than taken from state because the dock has no number until it is
  // capped, and the stylesheet's 40% guard may have clamped a stored width.
  const measure = useCallback((): number | null => {
    const el = shell.current;
    const seam = grip.current?.parentElement;
    if (el === null || !seam) return null;
    const s = el.getBoundingClientRect();
    const r = seam.getBoundingClientRect();
    if (island === "sidebar") return r.left - s.left;
    if (island === "tray") return s.right - r.left;
    return s.bottom - r.top;
  }, [island, shell]);

  const write = useCallback(
    (px: number | null) => {
      const el = shell.current;
      if (el === null) return;
      if (px === null) el.style.removeProperty(spec.cssVar);
      else el.style.setProperty(spec.cssVar, `${px}px`);
    },
    [shell, spec.cssVar],
  );

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const el = shell.current;
    const g = grip.current;
    const base = measure();
    if (el === null || g === null || base === null) return;
    drag.current = {
      pointerId: e.pointerId,
      start: spec.axis === "x" ? e.clientX : e.clientY,
      base,
      max: liveMax(island, el.getBoundingClientRect()),
      last: base,
      moved: false,
    };
    g.setPointerCapture(e.pointerId);
    g.setAttribute("data-active", "");
    el.setAttribute("data-resizing", spec.axis);
    e.preventDefault(); // no text selection, no focus jump on a click
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (d === null || e.pointerId !== d.pointerId) return;
    const pos = spec.axis === "x" ? e.clientX : e.clientY;
    const next = clampSize(island, d.base + (pos - d.start) * spec.grows, d.max);
    d.moved = d.moved || pos !== d.start;
    if (next === d.last) return;
    d.last = next;
    write(next);
  };

  const end = (commit: boolean) => {
    const d = drag.current;
    if (d === null) return;
    drag.current = null;
    grip.current?.removeAttribute("data-active");
    shell.current?.removeAttribute("data-resizing");
    if (commit && d.moved) setIsland(island, d.last);
    else if (d.moved) write(size ?? null); // cancelled — back to the committed value
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const el = shell.current;
    if (el === null) return;
    const max = liveMax(island, el.getBoundingClientRect());
    const cur = size ?? measure() ?? spec.min;
    let next: number;
    if (e.key === GROW_KEY[island]) next = cur + KEY_STEP;
    else if (e.key === SHRINK_KEY[island]) next = cur - KEY_STEP;
    else if (e.key === "Home") next = spec.min;
    else if (e.key === "End") next = max;
    else return;
    e.preventDefault();
    e.stopPropagation(); // the canvas listens for single keys; this is a seam
    setIsland(island, clampSize(island, next, max));
  };

  return (
    <div className={spec.axis === "x" ? styles.splitX : styles.splitY}>
      <div
        ref={grip}
        role="separator"
        tabIndex={0}
        className={styles.grip}
        data-island={island}
        aria-label={label}
        aria-orientation={spec.axis === "x" ? "vertical" : "horizontal"}
        aria-valuemin={spec.min}
        aria-valuemax={spec.max}
        aria-valuenow={size ?? spec.fallback ?? spec.max}
        title="Drag to resize · double-click to reset"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => end(true)}
        onPointerCancel={() => end(false)}
        onKeyDown={onKeyDown}
        onDoubleClick={() => setIsland(island, null)}
      />
    </div>
  );
}
