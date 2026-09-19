// The drag layer's resolver and its copy.
// Pure — vitest exercises it on jsdom elements, no React Flow store.
//
// One gesture, three verbs by destination. The drag itself is React Flow's
// own node drag (Canvas.tsx — its listeners sit on the window and it hands
// every callback the raw pointer event); this file decides WHAT the pointer
// is over and WHAT each place would do, in words, so the canvas can light
// every target on lift (each states its own effect) and resolve the
// release. Lineage is never rewritten by any of them: a drop moves
// where the artist is working, never what happened.
//
// F5 — class F's trust-critical case: a release over nothing is a clean
// no-op. The card goes back where it was lifted from and no request leaves
// the browser. Never a silent set-aside.

export type Drop =
  | { kind: "canvas" }
  | { kind: "node"; id: string }
  | { kind: "tray" }
  | { kind: "nothing" };

/** What the pointer is over, in drop terms. Checked outermost-intent first:
 *  the tray's marker beats a card beats the canvas. A LIFTED card under the
 *  pointer is the canvas beneath it — the pointer rides the card it drags. */
export function resolveDrop(
  el: Element | null,
  lifted: readonly string[],
  wrapper: Element | null,
): Drop {
  if (el === null) return { kind: "nothing" };
  if (el.closest('[data-drop="tray"]') !== null) return { kind: "tray" };
  const id = el.closest<HTMLElement>(".react-flow__node[data-id]")?.dataset.id;
  if (id !== undefined && !lifted.includes(id)) return { kind: "node", id };
  if (wrapper !== null && wrapper.contains(el)) return { kind: "canvas" };
  return { kind: "nothing" };
}

export function sameDrop(a: Drop | null, b: Drop | null): boolean {
  if (a === null || b === null) return a === b;
  return a.kind === b.kind && (a.kind !== "node" || a.id === (b as typeof a).id);
}

export type Destination = {
  kind: Drop["kind"];
  label: string;
  line: string; // the effect in words — or, when this lift can't take it, why
  live: boolean;
};

// The legend on lift: every destination, its effect in words, and — when
// it can't take THIS lift — the reason in its place (law 12: the same
// treatment a verb chip gets). The tray takes the whole lift (with
// its chip and its ⌫ — one idempotent request per card, refusals per
// card); a pairing still hands over one card at a time.
//
// "Onto another node" is honest about today: anchor channels aren't built,
// so the drop pairs the two as parents in the dock — the two-parent
// state — and REFINE says what a second parent needs.
export function destinations(lifted: number): Destination[] {
  const many = lifted > 1;
  return [
    {
      kind: "canvas",
      label: "EMPTY CANVAS",
      line: "becomes a working node here — the branch continues from it, and the dock follows",
      live: true,
    },
    {
      kind: "node",
      label: "ONTO ANOTHER NODE",
      line: many
        ? `hands over one at a time — ${lifted} are lifted`
        : "pairs them in the dock as parents — anchor channels aren't built yet, so REFINE says what a second parent needs",
      live: !many,
    },
    {
      kind: "tray",
      label: "SET-ASIDE TRAY",
      line: many
        ? `all ${lifted} off the canvas, still in the record — no confirmation needed`
        : "off the canvas, still in the record — no confirmation needed",
      live: true,
    },
  ];
}

export type DragState = { lifted: string[]; over: Drop | null } | null;

/** A card's part in the current drag, for its ring: lifted (carries the
 *  shadow), a target (dashed), the target under the pointer (solid). */
export function cardDragRole(
  drag: DragState,
  id: string,
): "lifted" | "target" | "over" | undefined {
  if (drag === null) return undefined;
  if (drag.lifted.includes(id)) return "lifted";
  return drag.over?.kind === "node" && drag.over.id === id ? "over" : "target";
}
