// The tray's pure half: the filter and the held button's copy.
import { expect, test } from "vitest";
import type { TrayNode } from "../../types";
import {
  EVERY_ENGINE,
  enginesPresent,
  filterTray,
  formatBytes,
  purgeCopy,
  trayLabel,
} from "./tray";

function entry(over: Partial<TrayNode> & { id: string }): TrayNode {
  return {
    workspace_id: "w1",
    turn_id: "t1",
    slot_id: "s1",
    take: 1,
    payload: "image",
    verb: "run",
    prompt: "a rusty sword",
    engine: "z-image-turbo",
    seed: 42,
    duration_ms: 4000,
    committed_usd: null,
    charged_usd: null,
    set_aside: true,
    x: null,
    y: null,
    created_at: "2026-08-22T00:00:00Z",
    parents: [],
    turn_idx: 0,
    slot_pos: 2,
    bytes: 2048,
    children: 0,
    ...over,
  };
}

const A = entry({ id: "a", children: 1 });
const B = entry({
  id: "b",
  prompt: "a knight in armour",
  engine: "flux2-klein-4b",
  slot_pos: 0,
  bytes: 1024 * 1024,
});
const C = entry({ id: "c", engine: null, prompt: null, turn_idx: null, slot_pos: null });

test("entries are named like their cards, engines counted", () => {
  expect(trayLabel(A)).toBe("0c · Z-IMAGE-TURBO");
  expect(trayLabel(C)).toBe("IMPORT");
  expect(enginesPresent([A, B, C, A])).toEqual([
    { id: "flux2-klein-4b", count: 1 },
    { id: "imported", count: 1 },
    { id: "z-image-turbo", count: 2 },
  ]);
});

test("search narrows by prompt or label; engine narrows by engine; both compose", () => {
  expect(filterTray([A, B, C], "", EVERY_ENGINE)).toEqual([A, B, C]);
  expect(filterTray([A, B, C], "KNIGHT", EVERY_ENGINE)).toEqual([B]);
  expect(filterTray([A, B, C], "0c", EVERY_ENGINE)).toEqual([A]);
  expect(filterTray([A, B, C], "", "z-image-turbo")).toEqual([A]);
  expect(filterTray([A, B, C], "", "imported")).toEqual([C]);
  expect(filterTray([A, B, C], "sword", "flux2-klein-4b")).toEqual([]);
});

test("bytes read like a human wrote them", () => {
  expect(formatBytes(0)).toBe("0 B");
  expect(formatBytes(2048)).toBe("2 KB");
  expect(formatBytes(1.4 * 1024 ** 3)).toBe("1.40 GB");
});

test("the held button promises exactly what goes, and the consequence is only as scary as it is", () => {
  const all = purgeCopy([A, B], 2);
  expect(all.label).toBe("PURGE SET-ASIDE · 2 NODES · FREES 1.0 MB");
  expect(all.consequence).toBe(
    "Hold to confirm. One child loses its parent image and keeps only the prompt record.",
  );
  const shown = purgeCopy([B], 2);
  expect(shown.label).toBe("PURGE 1 NODE SHOWN OF 2 · FREES 1.0 MB");
  expect(shown.consequence).toMatch(/Nothing else points at these/);
  expect(purgeCopy([A, entry({ id: "d", children: 2 })], 2).consequence).toMatch(
    /3 children lose their parent image/,
  );
});
