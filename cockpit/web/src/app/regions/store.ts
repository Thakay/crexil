// Region drafts: session-local compose state, keyed by node id. A draft
// survives closing and reopening the surface, per take, for this
// browser session. Nothing here is a generated artefact (law 7 guards
// takes, not unsent compose state); a fan out consumes the drafts
// into a region turn, where masks ride the request and become
// content-addressed refs server-side (regions.py's contract).
//
// Commits are whole strokes only: the in-progress stroke lives in Konva
// refs (RegionSurface), never here: the performance budget forbids setState per
// pointer-move (the rule that also keeps the canvas's React Flow
// uncontrolled). A committed stroke is rasterised into the draft's ONE
// bitmap (model.rasterize): the canvas mutates in place, `inkRev` is the
// re-render trigger, and an erase removes instead of appending.
//
// `fanTurn` is the region turn this surface launched per node — the
// attempt panes' pointer, session state exactly like the drafts.
import { create } from "zustand";
import {
  nextName,
  rasterize,
  type DrawTool,
  type Ink,
  type RegionAction,
  type RegionDraft,
} from "./model";

type RegionsState = {
  /** Drafts per node id, in creation order (creation order IS z-order —
   *  later wins within an action, model.layerOrder). */
  drafts: Record<string, RegionDraft[]>;
  /** The active (inked) region per node — tools apply to this one. */
  activeKey: Record<string, string | null>;
  /** The fan turn launched from this node's surface, set from
   *  the POST response; the attempt panes render that turn. */
  fanTurn: Record<string, string | null>;
  /** A fan-out POST in flight from this node's surface — set BEFORE the
   *  request so the shell's auto-grid can't steal the canvas region if
   *  the events poll delivers the new turn first. */
  pendingFan: string | null;
  setPendingFan: (nodeId: string | null) => void;
  tool: DrawTool;
  brushSize: number;
  setTool: (tool: DrawTool) => void;
  setBrushSize: (px: number) => void;
  /** Start a fresh draft and make it active; returns its key. */
  addDraft: (nodeId: string, action?: RegionAction) => string;
  removeDraft: (nodeId: string, key: string) => void;
  patchDraft: (
    nodeId: string,
    key: string,
    patch: Partial<Pick<RegionDraft, "name" | "action" | "prompt" | "visible">>,
  ) => void;
  /** One committed stroke/shape, at pointer-up, in natural coords —
   *  rasterised into the draft's bitmap. */
  commitInk: (
    nodeId: string,
    key: string,
    ink: Ink,
    natural: { w: number; h: number },
  ) => void;
  setActive: (nodeId: string, key: string | null) => void;
  setFanTurn: (nodeId: string, turnId: string | null) => void;
};

let keyseq = 0;

export const useRegions = create<RegionsState>((set) => ({
  drafts: {},
  activeKey: {},
  fanTurn: {},
  pendingFan: null,
  setPendingFan: (nodeId) => set({ pendingFan: nodeId }),
  tool: "brush",
  brushSize: 36,
  setTool: (tool) => set({ tool }),
  setBrushSize: (px) => set({ brushSize: px }),
  addDraft: (nodeId, action = "modify") => {
    const key = `r${keyseq++}`;
    set((s) => {
      const list = s.drafts[nodeId] ?? [];
      const draft: RegionDraft = {
        key,
        name: nextName(list),
        action,
        prompt: "",
        mask: null,
        inkRev: 0,
        inked: false,
        visible: true,
      };
      return {
        drafts: { ...s.drafts, [nodeId]: [...list, draft] },
        activeKey: { ...s.activeKey, [nodeId]: key },
      };
    });
    return key;
  },
  removeDraft: (nodeId, key) =>
    set((s) => {
      const list = (s.drafts[nodeId] ?? []).filter((d) => d.key !== key);
      const active =
        s.activeKey[nodeId] === key ? null : (s.activeKey[nodeId] ?? null);
      return {
        drafts: { ...s.drafts, [nodeId]: list },
        activeKey: { ...s.activeKey, [nodeId]: active },
      };
    }),
  patchDraft: (nodeId, key, patch) =>
    set((s) => ({
      drafts: {
        ...s.drafts,
        [nodeId]: (s.drafts[nodeId] ?? []).map((d) =>
          d.key === key ? { ...d, ...patch } : d,
        ),
      },
    })),
  commitInk: (nodeId, key, ink, natural) =>
    set((s) => ({
      drafts: {
        ...s.drafts,
        [nodeId]: (s.drafts[nodeId] ?? []).map((d) =>
          d.key === key
            ? {
                ...d,
                mask: rasterize(d.mask, ink, natural),
                inkRev: d.inkRev + 1,
                inked: d.inked || ink.tool !== "erase",
              }
            : d,
        ),
      },
    })),
  setActive: (nodeId, key) =>
    set((s) => ({ activeKey: { ...s.activeKey, [nodeId]: key } })),
  setFanTurn: (nodeId, turnId) =>
    set((s) => ({ fanTurn: { ...s.fanTurn, [nodeId]: turnId } })),
}));
