// Pure helpers for the set-aside tray, with no React, so
// vitest pins the copy and the filter without a DOM.
import type { TrayNode } from "../../types";
import { nodeLabel } from "../canvas/graph";

export const EVERY_ENGINE = ""; // the engine select's "all" value

// A tray entry is named exactly like its card ("12c · QWEN") — the same
// take reads the same everywhere it appears.
export const trayLabel = nodeLabel;

export function enginesPresent(
  nodes: TrayNode[],
): { id: string; count: number }[] {
  const by = new Map<string, number>();
  for (const n of nodes) {
    const k = n.engine ?? "imported";
    by.set(k, (by.get(k) ?? 0) + 1);
  }
  return [...by]
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

// Plain substring search over prompt + label. The tray is a flat grid by
// design until it passes ~100 nodes (the point to come back to it); the
// store's FTS waits for the surface that needs it.
export function filterTray(
  nodes: TrayNode[],
  query: string,
  engine: string,
): TrayNode[] {
  const q = query.trim().toLowerCase();
  return nodes.filter(
    (n) =>
      (engine === EVERY_ENGINE || (n.engine ?? "imported") === engine) &&
      (q === "" ||
        (n.prompt ?? "").toLowerCase().includes(q) ||
        trayLabel(n).toLowerCase().includes(q)),
  );
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

// The held button's face and its consequence line (for example: "Purge
// set-aside · 12 nodes · frees 1.4 GB — Hold to confirm. Their children
// lose their parent image and keep only the prompt record."). The count is
// exactly what will go — a filtered tray purges only what it shows, and
// says so — the bytes are what is on disk now, and the children clause
// appears only when there are children to orphan: no scare copy for a
// purge that orphans nothing.
export function purgeCopy(
  shown: TrayNode[],
  total: number,
): { label: string; consequence: string } {
  const bytes = shown.reduce((t, n) => t + n.bytes, 0);
  const children = shown.reduce((t, n) => t + n.children, 0);
  const nodes = `${shown.length} ${shown.length === 1 ? "NODE" : "NODES"}`;
  const scope =
    shown.length < total
      ? `PURGE ${nodes} SHOWN OF ${total}`
      : `PURGE SET-ASIDE · ${nodes}`;
  const label = `${scope} · FREES ${formatBytes(bytes)}`;
  const consequence =
    children > 0
      ? `Hold to confirm. ${
          children === 1
            ? "One child loses its parent image and keeps"
            : `${children} children lose their parent image and keep`
        } only the prompt record.`
      : "Hold to confirm. Nothing else points at these; their seeds and prompts stay in the record.";
  return { label, consequence };
}
