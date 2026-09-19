// The palette body (law 12): entries needing a parent are listed and
// say so — never hidden; picking Image · new root arms the composer with
// the summon point's flow coords. The pure SummonPalette renders here; the
// cursor-anchored popover mount is proven in e2e/summon.spec.ts.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useUi } from "../state/ui";
import { SummonPalette } from "./SummonMenu";

// cmdk sizes its list with ResizeObserver and scrolls the selected item
// into view; jsdom has neither.
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = vi.fn();
  useUi.setState({
    summonAt: { sx: 120, sy: 140, fx: 300, fy: 400 },
    armed: null,
    meshNodeId: null,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUi.setState({ summonAt: null, armed: null, meshNodeId: null });
});

test("all entries listed; parent-needing rows say so, never hidden", () => {
  render(<SummonPalette />);
  expect(screen.getByText("N · Summon at cursor")).toBeInTheDocument();
  expect(screen.getByText("Image · new root")).toBeInTheDocument();
  const child = screen.getByText("Image · from a parent");
  const mesh = screen.getByText("3D · from an image");
  expect(child.closest("[data-disabled]")).toHaveAttribute(
    "data-disabled",
    "true",
  );
  expect(mesh.closest("[data-disabled]")).toHaveAttribute(
    "data-disabled",
    "true",
  );
  expect(screen.getAllByText("NEEDS 1 PARENT")).toHaveLength(2);
});

test("search filters; nonsense gets an honest empty line", async () => {
  const user = userEvent.setup();
  render(<SummonPalette />);
  await user.type(screen.getByPlaceholderText("Summon…"), "3d");
  expect(screen.queryByText("Image · new root")).not.toBeInTheDocument();
  expect(screen.getByText("3D · from an image")).toBeInTheDocument();
  await user.clear(screen.getByPlaceholderText("Summon…"));
  await user.type(screen.getByPlaceholderText("Summon…"), "zzz");
  expect(screen.getByText("Nothing answers to that here.")).toBeInTheDocument();
});

test("Enter on Image · new root arms the composer at the flow point", async () => {
  const user = userEvent.setup();
  render(<SummonPalette />);
  await user.keyboard("{Enter}"); // first selectable item is the live one
  expect(useUi.getState().armed).toEqual({ fx: 300, fy: 400, parents: [] });
  expect(useUi.getState().summonAt).toBeNull();
});

test("with a parent to hand, the img2img row goes live and names the take", async () => {
  const user = userEvent.setup();
  render(<SummonPalette parent={{ id: "n1", label: "Take 3b" }} />);
  // both parent-needing rows read FROM the take they'd use
  expect(screen.getAllByText("FROM Take 3b")).toHaveLength(2);
  await user.click(screen.getByText("Image · from a parent"));
  expect(useUi.getState().armed).toEqual({
    fx: 300,
    fy: 400,
    parents: ["n1"],
  });
});

// The 3D entry doesn't arm the composer: a mesh has no prompt and
// no dials, so it opens its own plinth on the same parent, and the palette
// closes behind it.
test("3D · from an image opens the To-3D plinth rather than arming", async () => {
  const user = userEvent.setup();
  render(<SummonPalette parent={{ id: "n1", label: "Take 3b" }} />);
  await user.click(screen.getByText("3D · from an image"));
  expect(useUi.getState().meshNodeId).toBe("n1");
  expect(useUi.getState().armed).toBeNull();
  expect(useUi.getState().summonAt).toBeNull();
});

test("a NEEDS 1 PARENT row cannot arm or open a plinth", async () => {
  const user = userEvent.setup();
  render(<SummonPalette />);
  expect(screen.getAllByText("NEEDS 1 PARENT")).toHaveLength(2);
  await user.click(screen.getByText("3D · from an image"));
  expect(useUi.getState().armed).toBeNull();
  expect(useUi.getState().meshNodeId).toBeNull();
});
