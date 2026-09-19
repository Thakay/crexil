// The laws the web holds by construction, pinned statically. Law 2
// (no modals) can't be proven by rendering one screen, so the source tree
// is the witness: nothing in src/app reaches for a modal primitive. A pause
// is a queue state with a visible resume (the bench + UNBENCH, under test in
// TurnGrid.test.tsx / QueueStrip.test.tsx and live in e2e/batch-laws).
import { expect, test } from "vitest";

// Vite reads the tree for us (no node fs in the app's tsconfig): every
// source under src/app, raw, keyed by path.
const SOURCES = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const PKG = import.meta.glob("../../package.json", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// What a modal looks like in code: a dialog element or component, a native
// blocking prompt, the showModal API. Comments say "dialog" in lowercase;
// the component name is what a build would use.
const MODAL = [
  /\bDialog\b/,
  /\bAlertDialog\b/,
  /<dialog\b/,
  /\.showModal\(/,
  /window\.(confirm|alert|prompt)\(/,
  /(^|[^.\w])(confirm|alert)\(/,
];

test("law 2: nothing in src/app is a modal — no dialog primitive, no blocking prompt", () => {
  const files = Object.keys(SOURCES).filter((f) => !/\.test\.tsx?$/.test(f));
  expect(files.length).toBeGreaterThan(20);
  const offenders: string[] = [];
  for (const f of files)
    for (const re of MODAL)
      if (re.test(SOURCES[f])) offenders.push(`${f}: ${re}`);
  expect(offenders).toEqual([]);
});

test("law 2: no modal dependency is installed either", () => {
  const raw = Object.values(PKG)[0];
  expect(raw).toBeDefined();
  const pkg = JSON.parse(raw) as { dependencies: Record<string, string> };
  const names = Object.keys(pkg.dependencies);
  expect(names.length).toBeGreaterThan(3);
  expect(names.filter((n) => /dialog|modal/i.test(n))).toEqual([]);
});
