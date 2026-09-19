// Resizable islands: the shell's three splits and where
// their sizes live. Sidebar width · tray width · dock height, each a CSS
// custom property on the shell root (`Shell.tsx`); the stylesheets read the
// property with the default pixel value as the fallback, so an unset
// island is exactly the default geometry, laid out for a 1440×900 window.
//
// The sizes persist in the browser, app-level — the LOCAL ONLY switch's
// home (state/ui.ts), not the per-workspace view blob. A split is this
// window's chrome: the same artist wants a different sidebar at 1280×720
// than at 2560×1440, and a workspace opened on either screen must not drag
// the other's layout along. The sidebar also exists with no workspace open,
// where there is no blob to read.
//
// The dock is different in kind: it is content-driven (the verb row and the
// composer grow with what is selected and armed, and scroll when over-full,
// `Shell.module.css`), so its size is a CAP, never a fixed height — the
// artist drags the canvas/dock edge down to reclaim canvas and the dock
// scrolls beneath the cap; an unset cap is no cap at all, where only the
// canvas floor holds the dock back. That floor (`.body { min-height }`)
// stays a hard minimum regardless of what is persisted.

export type Island = "sidebar" | "tray" | "dock";

export type IslandSpec = {
  /** the custom property `Shell.tsx` writes on the shell root */
  cssVar: `--${string}`;
  axis: "x" | "y";
  /** px — the island never gets smaller than this, by drag or by storage */
  min: number;
  /** px — an absolute ceiling; the live drag also clamps against the shell */
  max: number;
  /** the default px, or null for "content-driven, no cap" (the dock) */
  fallback: number | null;
  /** +1 when the pointer moving +x/+y grows the island, −1 when it shrinks */
  grows: 1 | -1;
};

// Sidebar and tray minimums keep their controls whole (the create row's
// input + button, a tray entry's plate + RESTORE). The dock minimum is the
// queue strip (124 px, always shown) plus enough of the row or composer to
// scroll — anything tighter is a handle with nothing behind it.
export const ISLANDS: Record<Island, IslandSpec> = {
  sidebar: { cssVar: "--sidebar-w", axis: "x", min: 168, max: 480, fallback: 232, grows: 1 },
  tray: { cssVar: "--tray-w", axis: "x", min: 200, max: 520, fallback: 248, grows: -1 },
  dock: { cssVar: "--dock-max", axis: "y", min: 220, max: 1200, fallback: null, grows: -1 },
};

// Mirrors of `Shell.module.css` (`.topbar { height }`, `.body { min-height }`)
// and of the `max-width: 40%` guard on `.sidebar` / `.tray`: the live drag
// clamps to the same bounds the stylesheet enforces, so the edge never runs
// ahead of where the layout can follow.
export const TOPBAR_H = 44;
export const CANVAS_FLOOR = 260;
export const SIDE_SHARE = 0.4;

/** Arrow-key nudge (px) on a focused handle. */
export const KEY_STEP = 16;

export type Layout = Partial<Record<Island, number>>;

export function clampSize(island: Island, px: number, liveMax?: number): number {
  const spec = ISLANDS[island];
  const max = liveMax === undefined ? spec.max : Math.min(spec.max, liveMax);
  return Math.round(Math.min(Math.max(px, spec.min), Math.max(max, spec.min)));
}

/** The ceiling the shell's own box allows an island right now. */
export function liveMax(island: Island, shell: { width: number; height: number }): number {
  return ISLANDS[island].axis === "x"
    ? Math.floor(shell.width * SIDE_SHARE)
    : Math.floor(shell.height - TOPBAR_H - CANVAS_FLOOR);
}

export const LAYOUT_KEY = "cockpit.layout";

export function readLayout(): Layout {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (raw === null) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return {};
    const out: Layout = {};
    for (const island of Object.keys(ISLANDS) as Island[]) {
      const v = (parsed as Record<string, unknown>)[island];
      if (typeof v === "number" && Number.isFinite(v)) out[island] = clampSize(island, v);
    }
    return out;
  } catch {
    return {}; // storage refused or garbage: the default geometry
  }
}

export function writeLayout(layout: Layout): void {
  try {
    if (Object.keys(layout).length === 0) localStorage.removeItem(LAYOUT_KEY);
    else localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    // storage refused — the sizes still hold for this tab
  }
}

/** The shell root's inline style: one custom property per sized island. */
export function layoutVars(layout: Layout): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const island of Object.keys(ISLANDS) as Island[]) {
    const px = layout[island];
    if (px !== undefined) vars[ISLANDS[island].cssVar] = `${px}px`;
  }
  return vars;
}
