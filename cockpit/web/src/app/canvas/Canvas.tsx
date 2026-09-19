// The lineage canvas: UNCONTROLLED React Flow.
// Verified against the installed v12.11.2 dist: in controlled mode a node
// only moves if the parent setStates every drag frame (triggerNodeChanges →
// onNodesChange), which the performance budget forbids; `useNodesState` IS that
// per-frame setState. Uncontrolled (defaultNodes + setNodes) keeps drag
// inside RF's own store. Server truth flows one way — react-query →
// rf.setNodes on invalidation — and positions flow back only at
// onNodeDragStop. Do NOT "simplify" this into useNodesState/controlled
// props; the turn surface and the dock's verbs build on this wiring.
//
// Merge rule: a node RF already holds keeps ITS position (drag authority is
// client-side; the server's copy is write-behind), except when a failed
// position POST marks it for snap-back. New nodes take server x/y or the
// deterministic fallback (all landings arrive x=y NULL today).
//
// The drag layer rides the same RF node
// drag: lift lights the three destinations, each move resolves what the
// pointer is over (drop.ts), and the release applies the verb of the
// destination — canvas moves, another card pairs, the tray sets aside,
// anything else is F5's clean no-op. See `onNodeDragStart` below.
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useReactFlow,
  useStoreApi,
  type Edge,
  type Node,
  type Viewport,
} from "@xyflow/react";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type { WorkspaceDetail } from "../../types";
import { SummonMenu } from "../composer/SummonMenu";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { useEvents } from "../useEvents";
import { verbErr } from "../verbs/VerbRow";
import { destinations, resolveDrop } from "./drop";
import { ImageNode } from "./ImageNode";
import {
  TURN_COL,
  edgeTypesPresent,
  deriveEdges,
  onCanvas,
  toFlowNodes,
  type CanvasNode,
} from "./graph";
import s from "./Canvas.module.css";

const nodeTypes = { image: ImageNode } as const; // module scope: stable identity

const VIEW_SAVE_MS = 500;

function savedViewport(w: WorkspaceDetail): Viewport | null {
  const v = w.viewport as Partial<Viewport>;
  return typeof v.x === "number" &&
    typeof v.y === "number" &&
    typeof v.zoom === "number"
    ? { x: v.x, y: v.y, zoom: v.zoom }
    : null;
}

function ClearSelectionOnEsc() {
  const storeApi = useStoreApi();
  // An armed run owns Esc (it discards the run, the selection stays — the
  // composer's hotkey), and so does an open region surface (it closes,
  // keeping its drafts) and an open To-3D plinth (it closes, having
  // queued nothing); deselecting is the next press.
  const idle = useUi(
    (st) =>
      st.armed === null && st.regionsNodeId === null && st.meshNodeId === null,
  );
  useHotkeys("esc", () => storeApi.getState().unselectNodesAndEdges(), {
    enabled: idle,
  });
  return null;
}

// Summon triggers (law 11): `N` at the last pointer position or
// right-click on empty pane — the two cursor gestures, nothing else. The
// anchor is converted to flow coords at open time, so pan/zoom during an
// open palette can't move where the node is born.
function useSummon() {
  const rf = useReactFlow<CanvasNode, Edge>();
  const storeApi = useStoreApi();
  const openSummon = useUi((st) => st.openSummon);
  const pointer = useRef<{ x: number; y: number } | null>(null);

  const summonFrom = useCallback(
    (sx: number, sy: number) => {
      const f = rf.screenToFlowPosition({ x: sx, y: sy });
      openSummon({ sx, sy, fx: f.x, fy: f.y });
    },
    [rf, openSummon],
  );

  // Overlays own the keys while they're up (the grid holds the canvas,
  // the lightbox holds everything).
  useHotkeys("n", () => {
    const ui = useUi.getState();
    if (ui.gridOpen || ui.lightboxNodeId !== null) return;
    let p = pointer.current;
    if (p === null) {
      // Keyboard-only path: the canvas centre stands in for a cursor.
      const el = storeApi.getState().domNode;
      if (el === null) return;
      const r = el.getBoundingClientRect();
      p = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    summonFrom(p.x, p.y);
  });

  return {
    onPointerMove: (e: React.PointerEvent) => {
      pointer.current = { x: e.clientX, y: e.clientY };
    },
    onPaneContextMenu: (e: React.MouseEvent | MouseEvent) => {
      e.preventDefault();
      summonFrom(e.clientX, e.clientY);
    },
  };
}

// The dock's verbs (root, refine) reach the canvas here: the
// verb row lives outside this provider and can't turn a canvas point into
// flow coords, so it raises a request and this fulfils it.
//
// Where the takes are born: BESIDE THE PARENT when refining — the child of
// a take belongs next to it, and the card is already on screen — on the
// same column pitch the fallback layout uses, so it lands on that grid
// rather than beside it. Otherwise the centre-as-cursor stand-in the
// keyboard-only `N` path uses.
function ArmBridge() {
  const rf = useReactFlow<CanvasNode, Edge>();
  const storeApi = useStoreApi();
  const wanted = useUi((st) => st.armWanted);

  useEffect(() => {
    if (wanted === null) return;
    const { clearArm, arm } = useUi.getState();
    clearArm();
    const parent =
      wanted.parents.length === 1 ? rf.getNode(wanted.parents[0]) : undefined;
    if (parent !== undefined) {
      arm({
        fx: parent.position.x + TURN_COL,
        fy: parent.position.y,
        parents: wanted.parents,
        sweep: wanted.sweep,
        prompt: wanted.prompt,
      });
      return;
    }
    const el = storeApi.getState().domNode;
    if (el === null) return;
    const r = el.getBoundingClientRect();
    const f = rf.screenToFlowPosition({
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
    });
    arm({
      fx: f.x,
      fy: f.y,
      parents: wanted.parents,
      sweep: wanted.sweep,
      prompt: wanted.prompt,
    });
  }, [wanted, rf, storeApi]);

  return null;
}

// The region surface's CONTINUE HERE: a selection request raised
// outside the RF provider, resolved here — the canvas owns RF's `selected`
// flags, so the store mirror alone would be overwritten by the next
// onSelectionChange (the handOver rule).
function SelectBridge() {
  const rf = useReactFlow<CanvasNode, Edge>();
  const wanted = useUi((st) => st.selectWanted);

  useEffect(() => {
    if (wanted === null) return;
    const ui = useUi.getState();
    ui.clearSelectWanted();
    ui.setSelection([]);
    ui.setSelection(wanted);
    rf.setNodes((prev) =>
      prev.map((n) => ({ ...n, selected: wanted.includes(n.id) })),
    );
  }, [wanted, rf]);

  return null;
}

// On lift every destination lights and states its own effect — this
// is the words half: one line per place, the one under the pointer marked.
// The rings are CSS: the pane (Canvas.module.css), each card (ImageNode by
// data-drag), the tray's zone or open column (tray/). Nothing is learned
// from a tooltip. Pointer-transparent, so a release on it is a release on
// the pane beneath.
function DropLegend() {
  const drag = useUi((st) => st.drag);
  if (drag === null) return null;
  const n = drag.lifted.length;
  return (
    <Panel
      position="top-center"
      className={s.dropLegend}
      aria-label="Drop destinations"
    >
      <span className={`mono-micro ${s.dropHead}`}>
        {n === 1
          ? "LIFTED 1 CARD · WHERE IT CAN GO"
          : `LIFTED ${n} CARDS · WHERE THEY CAN GO`}
      </span>
      {destinations(n).map((d) => (
        <span
          key={d.kind}
          className={s.dropRow}
          data-kind={d.kind}
          data-live={d.live}
          data-over={drag.over?.kind === d.kind || undefined}
        >
          <span className={`mono-label ${s.dropLabel}`}>{d.label}</span>
          <span className={`effect-line ${s.dropLine}`}>{d.line}</span>
        </span>
      ))}
    </Panel>
  );
}

function Flow({ workspace }: { workspace: WorkspaceDetail }) {
  const wid = workspace.id;
  const rf = useReactFlow<CanvasNode, Edge>();
  const storeApi = useStoreApi();
  const queryClient = useQueryClient();
  const setSelection = useUi((st) => st.setSelection);
  const dragging = useUi((st) => st.drag !== null);
  const [dropErr, setDropErr] = useState<string | null>(null);

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid } },
  });
  const posMut = $api.useMutation("post", "/api/nodes/{nid}/position");
  const viewMut = $api.useMutation("patch", "/api/workspaces/{wid}/view");

  // The grid + strip read the same detail/queue caches: every
  // ledger kind (slot.* included) can move a cell, a lane or a
  // tray entry, so the whole workspace set invalidates together.
  const invalidateGraph = useCallback(
    () => invalidateWorkspace(queryClient, wid),
    [queryClient, wid],
  );
  const asideMut = $api.useMutation("post", "/api/nodes/{nid}/set-aside", {
    onSettled: invalidateGraph,
  });

  // Nodes whose position POST failed: the next sync takes server truth back.
  const snapBack = useRef(new Set<string>());
  const fitted = useRef(savedViewport(workspace) !== null);

  useEffect(() => {
    if (nodesQ.data === undefined) return;
    const data = onCanvas(nodesQ.data);
    const next = toFlowNodes(data, workspace.turns);
    rf.setNodes((prev) => {
      const prevById = new Map(prev.map((p) => [p.id, p]));
      return next.map((n) => {
        const old = prevById.get(n.id);
        if (old === undefined) return n;
        if (snapBack.current.delete(n.id)) return { ...n, selected: old.selected };
        // Every field of CanvasNodeData, swept rather than listed: a hand
        // written list silently goes stale the next time the card learns
        // something (a list that misses `promoted` never draws the mark).
        // The data is flat scalars by contract.
        const dataSame = (
          Object.keys(n.data) as (keyof typeof n.data)[]
        ).every((k) => old.data[k] === n.data[k]);
        return dataSame ? old : { ...old, data: n.data };
      });
    });
    rf.setEdges(deriveEdges(data));
    if (!fitted.current && data.length > 0) {
      fitted.current = true;
      requestAnimationFrame(() => void rf.fitView({ padding: 0.2 }));
    }
  }, [nodesQ.data, workspace.turns, rf]);

  // The filter takes every kind: slot./engine./scheduler. events
  // move grid cells and queue lanes even when no node changed.
  useEvents(wid, (events) => {
    if (events.length > 0) invalidateGraph();
  });

  const mutatePos = posMut.mutate;
  const persistPosition = useCallback(
    (node: Node) => {
      mutatePos(
        {
          params: { path: { nid: node.id } },
          body: { x: node.position.x, y: node.position.y },
        },
        {
          onError: () => {
            snapBack.current.add(node.id);
            invalidateGraph();
          },
        },
      );
    },
    [mutatePos, invalidateGraph],
  );

  // ── the drag layer ──────────────────────────────────────────
  // React Flow's node drag IS the gesture: it moves the card and selects it
  // (selectNodesOnDrag — the dock follows the lift), and hands these three
  // callbacks the raw pointer event. Lift records where every lifted card
  // came from and lights the targets; each move resolves what the pointer
  // is over (the lifted card is pointer-transparent — CSS); the release
  // applies the verb of the destination and nothing else. Every destination
  // but the canvas returns the card to its lift position — a drop is a
  // verb, not a move — and a release over nothing is F5's clean no-op:
  // back where it was, no request. Lineage is untouched by all of it.
  const liftedAt = useRef(new Map<string, { x: number; y: number }>());

  const onNodeDragStart = useCallback((_: unknown, __: Node, nodes: Node[]) => {
    liftedAt.current = new Map(nodes.map((n) => [n.id, { ...n.position }]));
    setDropErr(null);
    useUi.getState().lift(nodes.map((n) => n.id));
  }, []);

  // RF hands the native event (mouse or touch); only a pointer with
  // client coordinates can be over anything.
  type Pointer = MouseEvent | TouchEvent;
  const overAt = useCallback(
    (e: Pointer, lifted: string[]) =>
      resolveDrop(
        "clientX" in e ? document.elementFromPoint(e.clientX, e.clientY) : null,
        lifted,
        storeApi.getState().domNode,
      ),
    [storeApi],
  );

  const onNodeDrag = useCallback(
    (e: Pointer) => {
      const st = useUi.getState();
      if (st.drag !== null) st.dragOver(overAt(e, st.drag.lifted));
    },
    [overAt],
  );

  const returnLifted = useCallback(() => {
    const at = liftedAt.current;
    rf.setNodes((prev) =>
      prev.map((n) => {
        const p = at.get(n.id);
        return p === undefined ? n : { ...n, position: p };
      }),
    );
  }, [rf]);

  // Onto another node: the two-parent state, target first — the lifted
  // card is handed over as the second parent — so the row states what a
  // second parent needs (anchor channels aren't built yet). Order is parent
  // order; the mirror is cleared first so its merge can't keep the lifted
  // card, selected since the lift, ahead of its target.
  const handOver = useCallback(
    (targetId: string, lifted: string[]) => {
      const ids = [targetId, ...lifted];
      const ui = useUi.getState();
      ui.setSelection([]);
      ui.setSelection(ids);
      rf.setNodes((prev) =>
        prev.map((n) => ({ ...n, selected: ids.includes(n.id) })),
      );
    },
    [rf],
  );

  const mutateAside = asideMut.mutate;
  const onNodeDragStop = useCallback(
    (e: Pointer, _: Node, nodes: Node[]) => {
      const ui = useUi.getState();
      const drag = ui.drag;
      ui.drop();
      if (drag === null) return; // never lifted — nothing moved
      const over = drag.over ?? overAt(e, drag.lifted);
      if (over.kind === "canvas") {
        for (const n of nodes) persistPosition(n); // every lifted card
        return;
      }
      returnLifted();
      if (over.kind === "node") handOver(over.id, drag.lifted);
      else if (over.kind === "tray")
        // The whole lift: one idempotent request per card, so a
        // refusal is per card, never per batch.
        for (const nid of drag.lifted)
          mutateAside(
            { params: { path: { nid } } },
            { onError: (err) => setDropErr(verbErr(err)) },
          );
      // "nothing" — and a many-card lift onto another node: the legend
      // said why, and the cards are back where they were (F5).
    },
    [overAt, persistPosition, returnLifted, handOver, mutateAside],
  );

  const saveTimer = useRef<number | null>(null);
  const mutateView = viewMut.mutate;
  const onMoveEnd = useCallback(
    (_: unknown, viewport: Viewport) => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        mutateView({
          params: { path: { wid } },
          body: {
            viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom },
          },
        });
      }, VIEW_SAVE_MS);
    },
    [mutateView, wid],
  );
  useEffect(
    () => () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    },
    [],
  );

  const drawn = onCanvas(nodesQ.data ?? []);
  const asideCount = (nodesQ.data?.length ?? 0) - drawn.length;
  const legend = edgeTypesPresent(drawn);
  const summon = useSummon();

  return (
    <ReactFlow
      className={s.flow}
      colorMode="dark"
      // The library's "React Flow" attribution badge sits in the canvas's
      // bottom-right corner, over the dock's top edge, so it is hidden.
      // MIT: xyflow asks for the badge or a Pro subscription as
      // support, a courtesy, not a licence term.
      proOptions={{ hideAttribution: true }}
      defaultNodes={[] as CanvasNode[]}
      defaultEdges={[] as Edge[]}
      nodeTypes={nodeTypes}
      defaultViewport={savedViewport(workspace) ?? undefined}
      minZoom={0.1}
      onlyRenderVisibleElements
      nodesDraggable
      nodesConnectable={false}
      zoomOnDoubleClick={false}
      // The rubber band: ⇧drag on the pane sweeps a box. Shift
      // is ALSO the multi key, so a sweep ADDS to what's selected — same
      // gesture family as ⇧click. Partial: a card the box touches is
      // caught — at turn 30 a card is ~90px, full containment is fiddly.
      selectionKeyCode="Shift"
      selectionMode={SelectionMode.Partial}
      multiSelectionKeyCode="Shift"
      deleteKeyCode={null} // nothing is silently lost — no delete key
      onNodeDragStart={onNodeDragStart}
      onNodeDrag={onNodeDrag}
      onNodeDragStop={onNodeDragStop}
      data-dragging={dragging ? "" : undefined}
      onMoveEnd={onMoveEnd}
      onPointerMove={summon.onPointerMove}
      onPaneContextMenu={summon.onPaneContextMenu}
      // RF reports its selection as a set; the store keeps click order,
      // because order IS parent order for a two-parent verb.
      onSelectionChange={({ nodes }) => setSelection(nodes.map((n) => n.id))}
      // The lightbox: double-click, because single click is the acting-on
      // retarget. zoomOnDoubleClick is off, so the gesture is free.
      // It opens for meshes too: a 3d node has a full-res face of its own,
      // and the lightbox picks the right one by payload.
      onNodeDoubleClick={(_, node) => {
        useUi.getState().openLightbox(node.id);
      }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={27}
        size={1}
        color="#171b20"
        bgColor="var(--bg-canvas)"
      />
      <ClearSelectionOnEsc />
      <ArmBridge />
      <SelectBridge />
      <DropLegend />
      <SummonMenu workspaceId={wid} />
      {dropErr !== null && (
        <Panel position="bottom-center" className={s.dropErr}>
          {dropErr}
        </Panel>
      )}
      {nodesQ.data !== undefined && drawn.length === 0 && (
        <Panel position="top-center" className={s.empty}>
          <p className="mono-label">Empty canvas</p>
          <p className="effect-line">
            Press N — or right-click — to summon a node at the cursor.
            {asideCount > 0 &&
              ` ${asideCount} set aside, still in the record — T opens the tray.`}
          </p>
        </Panel>
      )}
      {legend.length > 0 && (
        <Panel position="bottom-left" className={s.legend}>
          <span className="mono-micro">Edge types</span>
          {legend.map((k) => (
            <span key={k.label} className={`mono-micro ${s.legendRow}`}>
              <i className={s.legendSwatch} style={{ background: k.cssVar }} />
              {k.label}
            </span>
          ))}
        </Panel>
      )}
    </ReactFlow>
  );
}

export function Canvas({ workspace }: { workspace: WorkspaceDetail }) {
  // key: a fresh RF store per workspace — stale nodes never flash across
  return (
    <ReactFlowProvider key={workspace.id}>
      <Flow workspace={workspace} />
    </ReactFlowProvider>
  );
}
