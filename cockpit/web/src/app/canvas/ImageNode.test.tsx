// The card body in every drawn state. CardBody only: the full
// ImageNode wraps RF Handles, which need a Flow store jsdom can't give;
// e2e/canvas.spec.ts proves the mounted card.
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CardBody } from "./ImageNode";
import type { CanvasNodeData } from "./graph";

const data: CanvasNodeData = {
  label: "12b · Z-IMAGE-TURBO",
  right: "IMAGE",
  thumb: "/api/nodes/n1/thumb",
  payload: "image",
  promoted: false,
};

// RTL auto-cleanup needs vitest globals (off here) — clean up explicitly.
afterEach(cleanup);

test("image card: thumbnail (never full-res) + footer label", () => {
  render(<CardBody data={data} selected={false} />);
  const img = screen.getByRole("presentation");
  expect(img).toHaveAttribute("src", "/api/nodes/n1/thumb");
  expect(img).toHaveAttribute("draggable", "false"); // RF owns the drag
  expect(screen.getByText("12b · Z-IMAGE-TURBO")).toBeInTheDocument();
  expect(screen.getByText("IMAGE")).toBeInTheDocument();
  expect(screen.queryByText("ACTING ON")).not.toBeInTheDocument();
});

test("selected card carries the ACTING ON pill", () => {
  render(<CardBody data={data} selected={true} />);
  expect(screen.getByText("ACTING ON")).toBeInTheDocument();
});

test("mesh plate shows no fake imagery: a plain word when there is no thumb", () => {
  render(
    <CardBody
      data={{
        label: "3a · TRELLIS.2",
        right: "MESH",
        thumb: null,
        payload: "3d",
        promoted: false,
      }}
      selected={false}
    />,
  );
  expect(screen.getAllByText("MESH")).toHaveLength(2); // plate word + footer
  expect(screen.queryByRole("presentation")).not.toBeInTheDocument();
});

test("a promoted take wears the mark on the plate, and only then", () => {
  expect(
    render(<CardBody data={data} selected={false} />).container.textContent,
  ).not.toContain("PROMOTED");
  cleanup();
  render(<CardBody data={{ ...data, promoted: true }} selected={true} />);
  // both marks coexist: ACTING ON is where you are, PROMOTED is what it is
  expect(screen.getByText("PROMOTED")).toBeInTheDocument();
  expect(screen.getByText("ACTING ON")).toBeInTheDocument();
});
