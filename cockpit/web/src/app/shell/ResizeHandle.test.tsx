// The resize handle's contract: a drag writes the island's custom
// property on the shell root and commits nothing until release (the perf
// bar); the release lands in the store and the browser; a click that never
// moves commits nothing; a cancel restores; the keys move the seam the way
// they point; double-click resets. jsdom has no layout, so the shell's box
// and the seam's position are stubbed — the maths is the thing under test.
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { useUi } from "../state/ui";
import { ISLANDS, LAYOUT_KEY, clampSize, readLayout, type Island } from "./layout";
import { ResizeHandle } from "./ResizeHandle";

const SHELL = { left: 0, top: 0, width: 1440, height: 900 };

function rect(r: { left: number; top: number; width: number; height: number }): DOMRect {
  return {
    x: r.left,
    y: r.top,
    left: r.left,
    top: r.top,
    width: r.width,
    height: r.height,
    right: r.left + r.width,
    bottom: r.top + r.height,
    toJSON: () => r,
  } as DOMRect;
}

function mount(island: Island, seam: { left: number; top: number }) {
  function Host() {
    const ref = useRef<HTMLDivElement>(null);
    return (
      <div ref={ref} data-shell="">
        <ResizeHandle island={island} shell={ref} label={`${island} size`} />
      </div>
    );
  }
  const r = render(<Host />);
  const shell = r.container.querySelector("[data-shell]") as HTMLDivElement;
  shell.getBoundingClientRect = () => rect(SHELL);
  const grip = screen.getByRole("separator", { name: `${island} size` });
  grip.parentElement!.getBoundingClientRect = () =>
    rect({ ...seam, width: 0, height: 0 });
  return { shell, grip };
}

const stored = () => localStorage.getItem(LAYOUT_KEY);
const prop = (el: HTMLElement, island: Island) =>
  el.style.getPropertyValue(ISLANDS[island].cssVar);

beforeAll(() => {
  // jsdom has no pointer capture; the handle calls it on every drag
  const proto = Element.prototype as unknown as Record<string, unknown>;
  proto.setPointerCapture ??= vi.fn();
  proto.releasePointerCapture ??= vi.fn();
  proto.hasPointerCapture ??= vi.fn(() => false);
});

afterEach(() => {
  cleanup();
  localStorage.removeItem(LAYOUT_KEY);
  useUi.setState({ layout: {} });
});

test("sidebar: the drag writes the property, only the release commits", () => {
  const { shell, grip } = mount("sidebar", { left: 232, top: 44 });
  expect(grip).toHaveAttribute("aria-valuenow", "232");

  fireEvent.pointerDown(grip, { pointerId: 1, button: 0, clientX: 232, clientY: 400 });
  expect(shell.dataset.resizing).toBe("x");
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 300, clientY: 400 });
  expect(prop(shell, "sidebar")).toBe("300px");
  expect(useUi.getState().layout.sidebar).toBeUndefined(); // nothing mid-drag
  expect(stored()).toBeNull();

  // clamps: the ceiling, then the floor
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 2000, clientY: 400 });
  expect(prop(shell, "sidebar")).toBe("480px");
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 10, clientY: 400 });
  expect(prop(shell, "sidebar")).toBe("168px");

  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 320, clientY: 400 });
  fireEvent.pointerUp(grip, { pointerId: 1, clientX: 320, clientY: 400 });
  expect(shell.dataset.resizing).toBeUndefined();
  expect(useUi.getState().layout.sidebar).toBe(320);
  expect(JSON.parse(stored()!)).toEqual({ sidebar: 320 });
  expect(grip).toHaveAttribute("aria-valuenow", "320");
});

test("a click that never moves commits nothing — the dock's cap can't land by accident", () => {
  const { shell, grip } = mount("dock", { left: 0, top: 600 });
  fireEvent.pointerDown(grip, { pointerId: 1, button: 0, clientX: 500, clientY: 600 });
  fireEvent.pointerUp(grip, { pointerId: 1, clientX: 500, clientY: 600 });
  expect(prop(shell, "dock")).toBe("");
  expect(useUi.getState().layout.dock).toBeUndefined();
  expect(stored()).toBeNull();
});

test("a cancelled drag goes back to the committed value", () => {
  useUi.getState().setIsland("sidebar", 260);
  const { shell, grip } = mount("sidebar", { left: 260, top: 44 });
  fireEvent.pointerDown(grip, { pointerId: 1, button: 0, clientX: 260, clientY: 400 });
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 400, clientY: 400 });
  expect(prop(shell, "sidebar")).toBe("400px");
  fireEvent.pointerCancel(grip, { pointerId: 1 });
  expect(prop(shell, "sidebar")).toBe("260px");
  expect(useUi.getState().layout.sidebar).toBe(260);
});

test("tray and dock grow against the pointer, and the dock's cap stops at the canvas floor", () => {
  // tray: the seam is its left edge; moving left grows it
  const tray = mount("tray", { left: 1440 - 248, top: 44 });
  fireEvent.pointerDown(tray.grip, { pointerId: 2, button: 0, clientX: 1192, clientY: 400 });
  fireEvent.pointerMove(tray.grip, { pointerId: 2, clientX: 1100, clientY: 400 });
  expect(prop(tray.shell, "tray")).toBe("340px");
  fireEvent.pointerUp(tray.grip, { pointerId: 2, clientX: 1100, clientY: 400 });
  expect(useUi.getState().layout.tray).toBe(340);
  cleanup();

  // dock: the seam is its top edge; moving down shrinks it to its minimum,
  // moving up is capped by the canvas floor (900 − 44 − 260 = 596)
  const dock = mount("dock", { left: 0, top: 600 });
  fireEvent.pointerDown(dock.grip, { pointerId: 3, button: 0, clientX: 500, clientY: 600 });
  fireEvent.pointerMove(dock.grip, { pointerId: 3, clientX: 500, clientY: 700 });
  expect(prop(dock.shell, "dock")).toBe("220px");
  fireEvent.pointerMove(dock.grip, { pointerId: 3, clientX: 500, clientY: 500 });
  expect(prop(dock.shell, "dock")).toBe("400px");
  fireEvent.pointerMove(dock.grip, { pointerId: 3, clientX: 500, clientY: 0 });
  expect(prop(dock.shell, "dock")).toBe("596px");
  fireEvent.pointerUp(dock.grip, { pointerId: 3, clientX: 500, clientY: 0 });
  expect(useUi.getState().layout).toEqual({ tray: 340, dock: 596 });
});

test("keys move the seam the way they point; Home/End are the bounds; double-click resets", () => {
  const { grip } = mount("sidebar", { left: 232, top: 44 });
  fireEvent.keyDown(grip, { key: "ArrowRight" }); // from the measured 232
  expect(useUi.getState().layout.sidebar).toBe(248);
  fireEvent.keyDown(grip, { key: "ArrowLeft" });
  fireEvent.keyDown(grip, { key: "ArrowLeft" });
  expect(useUi.getState().layout.sidebar).toBe(216);
  fireEvent.keyDown(grip, { key: "Home" });
  expect(useUi.getState().layout.sidebar).toBe(168);
  fireEvent.keyDown(grip, { key: "End" }); // 40% of 1440 = 576, then the 480 ceiling
  expect(useUi.getState().layout.sidebar).toBe(480);
  fireEvent.keyDown(grip, { key: "ArrowUp" }); // not this seam's axis
  expect(useUi.getState().layout.sidebar).toBe(480);
  fireEvent.dblClick(grip);
  expect(useUi.getState().layout.sidebar).toBeUndefined();
  expect(stored()).toBeNull();
  cleanup();

  useUi.getState().setIsland("dock", 300);
  const dock = mount("dock", { left: 0, top: 600 });
  fireEvent.keyDown(dock.grip, { key: "ArrowUp" });
  expect(useUi.getState().layout.dock).toBe(316);
  fireEvent.keyDown(dock.grip, { key: "ArrowDown" });
  fireEvent.keyDown(dock.grip, { key: "ArrowDown" });
  expect(useUi.getState().layout.dock).toBe(284);
});

test("storage: garbage and out-of-range values never reach the layout", () => {
  localStorage.setItem(LAYOUT_KEY, "not json");
  expect(readLayout()).toEqual({});
  localStorage.setItem(LAYOUT_KEY, JSON.stringify({ sidebar: 9999, tray: "x", dock: 50, other: 1 }));
  expect(readLayout()).toEqual({ sidebar: 480, dock: 220 });
  expect(clampSize("tray", 100)).toBe(200);
  expect(clampSize("tray", 300, 250)).toBe(250);
  expect(clampSize("tray", 300, 10)).toBe(200); // a live max under the min is the min
});
