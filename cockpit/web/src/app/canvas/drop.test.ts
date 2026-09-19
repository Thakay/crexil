// The drag layer's resolver and its words under test: which place
// the pointer is over (the tray beats a card beats the canvas; a lifted
// card is the canvas beneath it; outside is nothing — F5), and that every
// destination states its effect, or its reason when this lift can't take
// it. Plus the store slice: a move that changes nothing changes nothing.
import { expect, test } from "vitest";
import { useUi } from "../state/ui";
import { cardDragRole, destinations, resolveDrop, sameDrop } from "./drop";

function dom() {
  document.body.innerHTML = `
    <div class="shell">
      <div class="react-flow" id="wrapper">
        <div class="react-flow__pane" id="pane"></div>
        <div class="react-flow__node" data-id="a"><div id="inA">A</div></div>
        <div class="react-flow__node" data-id="b"><div id="inB">B</div></div>
        <div class="react-flow__panel" id="legend"></div>
      </div>
      <aside data-drop="tray" id="tray"><button id="inTray">RESTORE</button></aside>
      <section id="verbs"></section>
    </div>`;
  const $ = (id: string) => document.getElementById(id)!;
  return { wrapper: $("wrapper"), $ };
}

test("the tray beats a card beats the canvas; outside the canvas is nothing", () => {
  const { wrapper, $ } = dom();
  expect(resolveDrop($("inTray"), ["a"], wrapper)).toEqual({ kind: "tray" });
  expect(resolveDrop($("inB"), ["a"], wrapper)).toEqual({ kind: "node", id: "b" });
  expect(resolveDrop($("pane"), ["a"], wrapper)).toEqual({ kind: "canvas" });
  // a panel inside the canvas is the canvas (the legend is see-through anyway)
  expect(resolveDrop($("legend"), ["a"], wrapper)).toEqual({ kind: "canvas" });
  expect(resolveDrop($("verbs"), ["a"], wrapper)).toEqual({ kind: "nothing" });
  expect(resolveDrop(null, ["a"], wrapper)).toEqual({ kind: "nothing" });
});

test("a lifted card under the pointer is the canvas beneath it — never its own target", () => {
  const { wrapper, $ } = dom();
  expect(resolveDrop($("inA"), ["a"], wrapper)).toEqual({ kind: "canvas" });
  expect(resolveDrop($("inB"), ["a", "b"], wrapper)).toEqual({ kind: "canvas" });
});

test("sameDrop: only a change of target is a change", () => {
  expect(sameDrop({ kind: "node", id: "b" }, { kind: "node", id: "b" })).toBe(true);
  expect(sameDrop({ kind: "node", id: "b" }, { kind: "node", id: "c" })).toBe(false);
  expect(sameDrop({ kind: "tray" }, { kind: "tray" })).toBe(true);
  expect(sameDrop({ kind: "tray" }, { kind: "canvas" })).toBe(false);
  expect(sameDrop(null, { kind: "tray" })).toBe(false);
  expect(sameDrop(null, null)).toBe(true);
});

test("every destination states its effect; the tray takes a many-card lift, a pairing doesn't", () => {
  const one = destinations(1);
  expect(one.map((d) => d.kind)).toEqual(["canvas", "node", "tray"]);
  expect(one.every((d) => d.live)).toBe(true);
  expect(one[2].line).toBe(
    "off the canvas, still in the record — no confirmation needed",
  );
  // onto another node is honest about today: it pairs, it doesn't anchor
  expect(one[1].line).toMatch(/anchor channels aren't built yet/);
  expect(one[0].line).toMatch(/the dock follows/);

  const two = destinations(2);
  expect(two[0].live).toBe(true);
  // a pairing hands over one card: the gate that remains
  expect(two[1]).toMatchObject({
    live: false,
    line: "hands over one at a time — 2 are lifted",
  });
  // the tray takes the whole lift, and says so with the count
  expect(two[2]).toMatchObject({
    live: true,
    line: "all 2 off the canvas, still in the record — no confirmation needed",
  });
});

test("a card's part in the drag: lifted, a target, or the target under the pointer", () => {
  expect(cardDragRole(null, "a")).toBeUndefined();
  const drag = { lifted: ["a"], over: { kind: "node", id: "b" } as const };
  expect(cardDragRole(drag, "a")).toBe("lifted");
  expect(cardDragRole(drag, "b")).toBe("over");
  expect(cardDragRole(drag, "c")).toBe("target");
  expect(cardDragRole({ lifted: ["a"], over: { kind: "tray" } }, "b")).toBe("target");
});

test("the store: dragOver keeps identity unless the target changed; drop clears", () => {
  const ui = useUi.getState();
  ui.lift(["a"]);
  const lifted = useUi.getState().drag;
  expect(lifted).toEqual({ lifted: ["a"], over: null });
  ui.dragOver({ kind: "canvas" });
  const over = useUi.getState().drag;
  expect(over).not.toBe(lifted);
  expect(over?.over).toEqual({ kind: "canvas" });
  ui.dragOver({ kind: "canvas" }); // the same target — no new object, no re-render
  expect(useUi.getState().drag).toBe(over);
  ui.drop();
  expect(useUi.getState().drag).toBeNull();
  ui.dragOver({ kind: "tray" }); // a stray move after the drop is nothing
  expect(useUi.getState().drag).toBeNull();
});
