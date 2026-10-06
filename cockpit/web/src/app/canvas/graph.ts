// Pure graph derivation for the canvas, with no React Flow runtime, so
// vitest exercises it without a Flow store. Edge type = the PARENT node's
// payload; payloads are image|3d only, so only IMAGE and MESH edges can
// occur yet.
import type { Edge, Node as FlowNode } from "@xyflow/react";
import type { Node, Turn } from "../../types";

export type CanvasNodeData = {
  label: string; // "12b · FLUX.1-DEV" — slot letters, not take letters
  right: string; // "IMAGE" | "MESH" — honest, dims aren't stored
  thumb: string | null; // cards render thumbs, never full-res (performance budget)
  payload: Node["payload"];
  // This take has left for the asset tree. The one permanent mark a
  // node wears: a promoted take carries it everywhere it appears (one
  // mark, one meaning, learned once), so it is on the card, the grid
  // cell and the tray entry alike.
  promoted: boolean;
};

// The canvas draws what is NOT set aside (set-aside nodes leave the
// canvas and stay in the record); the tray draws the rest of the same
// feed. Edges to a set-aside parent draw nothing, exactly as they would
// to a purged one: the child's lineage row is untouched either way.
export function onCanvas(nodes: Node[]): Node[] {
  return nodes.filter((n) => !n.set_aside);
}

export type CanvasNode = FlowNode<CanvasNodeData, "image">;

// Card footprint, mirrored from Canvas.module.css: pre-measure dimensions so
// culling and fitView work before first paint (RF falls back to
// initialWidth/Height until measurement lands).
export const CARD_W = 158;
export const CARD_H = 132;

export const EDGE_KIND: Record<
  Node["payload"],
  { label: string; cssVar: string }
> = {
  image: { label: "IMAGE", cssVar: "var(--accent)" },
  "3d": { label: "MESH", cssVar: "var(--good)" },
  // the legacy canvas never draws audio; the project surface owns its hue
  audio: { label: "AUDIO", cssVar: "var(--secondary)" },
};

function slotLetter(pos: number | null): string {
  return pos === null ? "" : String.fromCharCode(97 + pos); // a… (cap 16/turn)
}

// Cell identity, then the TAKE once a cell has more than one. A reroll
// keeps the old take, and the takes sit side by side: three cards
// reading "0a · Z-IMAGE-TURBO" are distinct nodes with indistinguishable
// names. A lone take stays unnumbered — the number APPEARING is the signal
// that this cell has history, so the common card carries no extra chrome.
export function nodeLabel(n: Node): string {
  if (n.turn_idx === null) return "IMPORT";
  const take = (n.take ?? 1) > 1 ? ` · ${n.take}` : "";
  const cell = `${n.turn_idx}${slotLetter(n.slot_pos)}${take}`;
  return n.engine ? `${cell} · ${n.engine.toUpperCase()}` : cell;
}

function rightSpan(n: Node): string {
  return EDGE_KIND[n.payload].label;
}

// One card pitch each way: turns march across, slots stack down.
export const TURN_COL = CARD_W + 62; // 220
export const SLOT_ROW = CARD_H + 48; // 180

// All landed nodes arrive with x=y NULL (run_turn passes no positions:
// grid placement belongs to the turn surface), so this deterministic
// layout IS the normal path: column per turn at its anchor (or a 220px
// march), a row per slot, and ONE MORE COLUMN PER TAKE. That last term is
// there because a reroll keeps the old take (law 7), so a slot holds
// several nodes: keyed on turn+slot alone they land exactly on top of
// each other, invisible until dragged. Takes fan right on the same grid
// as turn columns, so a cell's history reads left-to-right and nothing
// hides anything. Derived only from stable fields (refetches never
// shuffle) and never persisted; only a user drag POSTs a position.
export function fallbackPosition(
  n: Node,
  turnsById: Map<string, Turn>,
  turnlessOrdinal = 0,
): { x: number; y: number } {
  const turn = n.turn_id !== null ? turnsById.get(n.turn_id) : undefined;
  const idx = n.turn_idx ?? -1;
  const take = Math.max(0, (n.take ?? 1) - 1); // attempt 1 = the anchor itself
  const x = (turn?.anchor_x ?? 80 + idx * TURN_COL) + take * TURN_COL;
  const y = (turn?.anchor_y ?? 80) + (n.slot_pos ?? turnlessOrdinal) * SLOT_ROW;
  return { x, y };
}

/** Whether this node has a body to draw: an image always does, a mesh only
 *  once its as-run record carries a turntable. */
export function hasPlate(n: Node): boolean {
  return n.payload === "image" || n.mesh?.has_turntable === true;
}

export function toFlowNodes(nodes: Node[], turns: Turn[]): CanvasNode[] {
  const turnsById = new Map(turns.map((t) => [t.id, t]));
  let turnless = 0;
  return nodes.map((n) => ({
    id: n.id,
    type: "image" as const,
    position:
      n.x !== null && n.y !== null
        ? { x: n.x, y: n.y }
        : fallbackPosition(n, turnsById, n.turn_id === null ? turnless++ : 0),
    initialWidth: CARD_W,
    initialHeight: CARD_H,
    connectable: false,
    deletable: false, // nothing is silently lost — purge is a verb, not a key
    data: {
      label: nodeLabel(n),
      right: rightSpan(n),
      // A mesh has no pixels of its own, so its plate is frame 0 of
      // the engine's own turntable — same route, same <=320 derivative. A
      // mesh whose orbit was never recorded asks for nothing and keeps the
      // plain MESH word instead (law 12: the card stays, the reason is said).
      thumb: hasPlate(n) ? `/api/nodes/${n.id}/thumb` : null,
      payload: n.payload,
      promoted: n.promoted_at !== null,
    },
  }));
}

// Parent → child, only when the parent still has a card in this list (a
// purged parent keeps its lineage row server-side but draws nothing).
export function deriveEdges(nodes: Node[]): Edge[] {
  const payloadById = new Map(nodes.map((n) => [n.id, n.payload]));
  const edges: Edge[] = [];
  for (const n of nodes) {
    for (const pid of n.parents) {
      const parentPayload = payloadById.get(pid);
      if (parentPayload === undefined) continue;
      edges.push({
        id: `${pid}->${n.id}`,
        source: pid,
        target: n.id,
        type: "straight",
        selectable: false,
        focusable: false,
        style: { stroke: EDGE_KIND[parentPayload].cssVar, strokeWidth: 1 },
      });
    }
  }
  return edges;
}

// The legend lists only edge types actually on the canvas — badge-honesty
// over decorating with types that cannot occur yet.
export function edgeTypesPresent(
  nodes: Node[],
): { label: string; cssVar: string }[] {
  const payloadById = new Map(nodes.map((n) => [n.id, n.payload]));
  const seen = new Set<Node["payload"]>();
  for (const n of nodes) {
    for (const pid of n.parents) {
      const p = payloadById.get(pid);
      if (p !== undefined) seen.add(p);
    }
  }
  return (["image", "3d"] as const)
    .filter((p) => seen.has(p))
    .map((p) => EDGE_KIND[p]);
}
