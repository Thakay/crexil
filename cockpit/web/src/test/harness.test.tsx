import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

// Harness proof: vitest + jsdom + testing-library are wired. Component
// tests proper live beside their components under src/app.
test("renders into jsdom", () => {
  render(<button>ok</button>);
  expect(screen.getByRole("button", { name: "ok" })).toBeInTheDocument();
});
