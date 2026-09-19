// Client-side view state (zustand). Server-owned state (workspaces,
// turns, queue) never lives here — react-query is its cache. Viewport/dial
// persistence is server-side per workspace; this store is session-local
// chrome only.
import { create } from "zustand";
import { sameDrop, type DragState, type Drop } from "../canvas/drop";
import type { SliderId } from "../composer/dials";
import {
  clampSize,
  readLayout,
  writeLayout,
  type Island,
  type Layout,
} from "../shell/layout";

type UiState = {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  // The three shell splits: sidebar width · tray width · dock cap,
  // px per island, absent = the default geometry. App-level chrome kept in
  // the browser like `localOnly` — a size belongs to this window, not to
  // the asset (shell/layout.ts says why). Only the RELEASE of a drag lands
  // here; the drag itself writes the CSS property straight on the shell
  // root (the performance budget: no setState per pointer move).
  layout: Layout;
  setIsland: (island: Island, px: number | null) => void;
  // The LOCAL ONLY switch: app-level chrome (it shows on every screen,
  // Home included), so it lives in the browser,
  // not in a workspace's view blob. It is a CLAIM every money verb's
  // request carries (run · reroll · backfill); the server is the proof
  // and refuses paid work beneath it.
  localOnly: boolean;
  setLocalOnly: (on: boolean) => void;
  // The acting-on target. Selection IS the whole targeting
  // state — there is no "current node" the graph imposes. React-Flow's
  // native `selected` drives the card ring; this ordered mirror feeds the
  // verb row, and the ORDER is parent order (node_parents is ordinal).
  // React Flow reports its selection as a set, so setSelection preserves the
  // order ids were first seen in — a shift-click appends, never reshuffles.
  // Consume via selector — useUi((s) => s.selection) — so canvas updates
  // don't re-render unrelated chrome.
  selection: string[];
  setSelection: (ids: string[]) => void;
  // The turn surface: open ⇒ the grid overlays the canvas region.
  // viewedTurnId null = the newest turn; openGrid resets to newest so a
  // fresh Run always surfaces its own grid.
  gridOpen: boolean;
  viewedTurnId: string | null;
  openGrid: () => void;
  closeGrid: () => void;
  toggleGrid: () => void;
  viewTurn: (id: string | null) => void;
  // The region surface: EDIT REGION on one image take overlays the
  // canvas region, same place the grid lives — the two are exclusive, and
  // a new turn's auto-grid closes it (the artist ran something; the grid's
  // moment). Drafts survive the close in regions/store.ts.
  regionsNodeId: string | null;
  openRegions: (nodeId: string) => void;
  closeRegions: () => void;
  // The promote plinth: PROMOTE opens it under the verb row's
  // hairline on one take — the destination, every file, the cost, then the
  // hold (irreversible). It lives in the dock, not over the canvas: promote
  // is a verb on the selection, and law 2 has no modal to put it in. Esc
  // closes it and nothing leaves. Retargeting the selection closes it too —
  // a plinth naming a take you are no longer acting on would be a lie.
  promoteNodeId: string | null;
  openPromote: (nodeId: string) => void;
  closePromote: () => void;
  // The To-3D plinth: TO 3D on one image take (or the summon
  // palette's `3D · from an image`) opens the mesh roster in the dock,
  // beside the promote plinth and for the same reason (a verb's own
  // surface belongs where the take is already named; law 2 has no modal).
  // It carries no prompt and no dials, so unlike the composer it is not
  // "armed" — nothing is spent by opening it and Esc closes it.
  meshNodeId: string | null;
  openMesh: (nodeId: string) => void;
  closeMesh: () => void;
  // The DECIMATE plinth: the same dock seam on a MESH take. It is
  // its own id rather than a mode of `meshNodeId` because the two name
  // different parents — one an image, one a mesh — and a single id would
  // make "which plinth is open" a second, derived question. Exclusive with
  // the others for the dock's one-run-surface reason.
  decimateNodeId: string | null;
  openDecimate: (nodeId: string) => void;
  closeDecimate: () => void;
  // The TEXTURE plinth: the same dock seam, and the first verb with
  // TWO parents — the acted-on mesh is this id, the reference image is
  // picked inside the plinth. Exclusive with the others for the dock's
  // one-run-surface reason.
  textureNodeId: string | null;
  openTexture: (nodeId: string) => void;
  closeTexture: () => void;
  // The set-aside tray: a right-edge column beside the canvas,
  // session chrome like the sidebar. Set-aside nodes live THERE, not on
  // the canvas ("off the canvas, still in the record").
  trayOpen: boolean;
  toggleTray: () => void;
  openTray: () => void;
  closeTray: () => void;
  // Lightbox: the one surface that shows full-res (cells and
  // cards stay thumbs).
  lightboxNodeId: string | null;
  openLightbox: (id: string) => void;
  closeLightbox: () => void;
  // Summon → armed composer. Screen coords place the palette;
  // flow coords are the turn anchor — the node is born where the palette
  // was (fallbackPosition reads turn.anchor_x/y).
  summonAt: { sx: number; sy: number; fx: number; fy: number } | null;
  openSummon: (at: NonNullable<UiState["summonAt"]>) => void;
  closeSummon: () => void;
  // `parents` are plain parents: empty arms a root run, one id
  // arms Refine — the same composer either way, because a refine still
  // needs a prompt, an engine and a take count. `sweep` arms it
  // with SWEEP ONE DIAL already on that dial — VARY ONE DIAL's shortcut;
  // the composer's own sweep control can change or drop it.
  // `prompt` seeds the brief on a root arm: the failed paid cell's
  // free remedy ("run locally") re-arms the turn's own brief; a refine
  // carries its source's prompt instead and ignores this.
  armed: {
    fx: number;
    fy: number;
    parents: string[];
    sweep?: SliderId | null;
    prompt?: string | null;
  } | null;
  arm: (at: NonNullable<UiState["armed"]>) => void;
  disarm: () => void;
  // Arming from the dock: the verb row lives outside the
  // React Flow provider, so it can't turn a canvas point into flow coords.
  // It raises this request; the canvas resolves the anchor (beside the
  // parent, or the centre-as-cursor stand-in the keyboard-only `N` path
  // uses) and clears it.
  armWanted: {
    parents: string[];
    sweep: SliderId | null;
    prompt: string | null;
  } | null;
  wantArm: (
    parents: string[],
    sweep?: SliderId | null,
    prompt?: string | null,
  ) => void;
  clearArm: () => void;
  // Selecting from outside the React Flow provider: the region
  // surface's CONTINUE HERE hands the picked winners over as the acting-on
  // selection — same seam as armWanted, the canvas resolves and clears
  // (it owns RF's `selected` flags; the store mirror alone would be
  // overwritten by RF's next onSelectionChange).
  selectWanted: string[] | null;
  wantSelect: (ids: string[]) => void;
  clearSelectWanted: () => void;
  // The drag layer: which cards are lifted
  // and what the pointer is over. The canvas writes it (it owns the React
  // Flow drag); the legend, every card's ring and the tray's zone read it
  // through selectors, so a drag re-renders only the chrome that lights.
  drag: DragState;
  lift: (ids: string[]) => void;
  dragOver: (over: Drop) => void;
  drop: () => void;
};

const LOCAL_ONLY_KEY = "cockpit.localOnly";

function storedLocalOnly(): boolean {
  try {
    return localStorage.getItem(LOCAL_ONLY_KEY) === "1";
  } catch {
    return false; // storage refused (private mode) — off until flipped
  }
}

// Ids already selected keep their position; new ids land in the order given.
function merge(prev: string[], next: string[]): string[] {
  const keep = prev.filter((id) => next.includes(id));
  return [...keep, ...next.filter((id) => !keep.includes(id))];
}

export const useUi = create<UiState>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  layout: readLayout(),
  setIsland: (island, px) =>
    set((s) => {
      const layout: Layout = { ...s.layout };
      if (px === null) delete layout[island];
      else layout[island] = clampSize(island, px);
      writeLayout(layout);
      return { layout };
    }),
  localOnly: storedLocalOnly(),
  setLocalOnly: (on) => {
    try {
      localStorage.setItem(LOCAL_ONLY_KEY, on ? "1" : "0");
    } catch {
      // storage refused — the switch still holds for this tab
    }
    set({ localOnly: on });
  },
  selection: [],
  setSelection: (ids) =>
    set((s) => {
      const merged = merge(s.selection, ids);
      // Identity is the re-render trigger — an unchanged selection (RF
      // fires onSelectionChange on every node data sync) keeps its array.
      const same =
        merged.length === s.selection.length &&
        merged.every((id, i) => id === s.selection[i]);
      // An open plinth names ONE take by its facts and its files.
      // Retargeting makes those words wrong, so the plinth closes with the
      // selection that opened it — nothing was written, nothing is lost.
      // The To-3D plinth names its source the same way and goes
      // with it; nothing was queued.
      return same
        ? {}
        : {
            selection: merged,
            promoteNodeId: null,
            meshNodeId: null,
            decimateNodeId: null,
            textureNodeId: null,
          };
    }),
  gridOpen: false,
  viewedTurnId: null,
  openGrid: () =>
    set({ gridOpen: true, viewedTurnId: null, regionsNodeId: null }),
  closeGrid: () => set({ gridOpen: false }),
  toggleGrid: () =>
    set((s) =>
      s.gridOpen
        ? { gridOpen: false }
        : { gridOpen: true, viewedTurnId: null, regionsNodeId: null },
    ),
  viewTurn: (id) => set({ viewedTurnId: id }),
  regionsNodeId: null,
  openRegions: (nodeId) => set({ regionsNodeId: nodeId, gridOpen: false }),
  closeRegions: () => set({ regionsNodeId: null }),
  promoteNodeId: null,
  // The two dock plinths are exclusive: each names ONE take and the pair
  // would fight for Esc and for the dock's height.
  openPromote: (nodeId) =>
    set({
      promoteNodeId: nodeId,
      meshNodeId: null,
      decimateNodeId: null,
      textureNodeId: null,
    }),
  closePromote: () => set({ promoteNodeId: null }),
  meshNodeId: null,
  // One run surface in the dock at a time: the armed composer and the
  // To-3D plinth both end in a Run button, and two of those side by side
  // is two answers to "what does pressing this start?". Nothing is lost
  // either way — an armed composer has spent nothing, and the plinth has
  // queued nothing.
  openMesh: (nodeId) =>
    set({
      meshNodeId: nodeId,
      promoteNodeId: null,
      decimateNodeId: null,
      textureNodeId: null,
      armed: null,
    }),
  closeMesh: () => set({ meshNodeId: null }),
  decimateNodeId: null,
  openDecimate: (nodeId) =>
    set({
      decimateNodeId: nodeId,
      meshNodeId: null,
      promoteNodeId: null,
      textureNodeId: null,
      armed: null,
    }),
  closeDecimate: () => set({ decimateNodeId: null }),
  textureNodeId: null,
  openTexture: (nodeId) =>
    set({
      textureNodeId: nodeId,
      meshNodeId: null,
      promoteNodeId: null,
      decimateNodeId: null,
      armed: null,
    }),
  closeTexture: () => set({ textureNodeId: null }),
  trayOpen: false,
  toggleTray: () => set((s) => ({ trayOpen: !s.trayOpen })),
  openTray: () => set({ trayOpen: true }),
  closeTray: () => set({ trayOpen: false }),
  lightboxNodeId: null,
  openLightbox: (id) => set({ lightboxNodeId: id }),
  closeLightbox: () => set({ lightboxNodeId: null }),
  summonAt: null,
  openSummon: (at) => set({ summonAt: at }),
  closeSummon: () => set({ summonAt: null }),
  armed: null,
  arm: (at) =>
    set({ armed: at, summonAt: null, meshNodeId: null, decimateNodeId: null }),
  disarm: () => set({ armed: null }),
  armWanted: null,
  wantArm: (parents, sweep = null, prompt = null) =>
    set({ armWanted: { parents, sweep, prompt } }),
  clearArm: () => set({ armWanted: null }),
  selectWanted: null,
  wantSelect: (ids) => set({ selectWanted: ids }),
  clearSelectWanted: () => set({ selectWanted: null }),
  drag: null,
  lift: (ids) => set({ drag: { lifted: ids, over: null } }),
  // Fires on every pointer move of a drag; only a CHANGE of target is a
  // state change, so the rings and the legend never re-render per frame.
  dragOver: (over) =>
    set((s) =>
      s.drag === null || sameDrop(s.drag.over, over)
        ? {}
        : { drag: { ...s.drag, over } },
    ),
  drop: () => set({ drag: null }),
}));
