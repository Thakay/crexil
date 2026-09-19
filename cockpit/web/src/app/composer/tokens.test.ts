// E5: the token-window warning, an estimate that says so, per
// stepped engine, only where the matrix pins a window, never a refusal.
import { expect, test } from "vitest";
import { estimateTokens, tokenWarnings } from "./tokens";
import { SDXL, ZIMAGE } from "./dials.test";

const v = (value: unknown, state = "verified") => ({ value, state, note: "n" });
const ENGINES = {
  updated: "2026-08-11",
  engines: [
    {
      id: "sdxl-juggernaut-xi",
      name: "SDXL Juggernaut XI",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: { seed: v(true), tokenLimit: v(77, "unverified") },
    },
    {
      id: "z-image-base",
      name: "Z-Image Base",
      lane: "local",
      payload: "image",
      price: "free",
      capabilities: { seed: v(true), tokenLimit: v(null, "unverified") },
    },
  ],
  external: {
    lane: "external",
    payload: "image",
    price: "",
    catalogue: "",
    why_no_per_model_rows: "",
    defaults: { tokenLimit: v(null, "unverified") },
    overrides: {},
  },
} as unknown as Parameters<typeof tokenWarnings>[0]["engines"];

const long = Array.from({ length: 60 }, (_, i) => `word${i}`).join(", ");

test("the estimate is CLIP-shaped: a word a token, punctuation its own, long words in pieces", () => {
  expect(estimateTokens("")).toBe(0);
  expect(estimateTokens("a rusty sword")).toBe(3);
  expect(estimateTokens("a rusty sword, kept")).toBe(5); // the comma counts
  expect(estimateTokens("photorealistic")).toBe(3); // 14 chars → pieces
  expect(estimateTokens("don't")).toBe(1); // apostrophe joins
  expect(estimateTokens(long)).toBeGreaterThan(77);
});

test("a warning names the engine, the count as ABOUT, the window with its state, and that nothing is cut", () => {
  const out = tokenWarnings({
    prompt: long,
    engines: ENGINES,
    presets: [SDXL, ZIMAGE],
    counts: new Map([
      [SDXL.id, 2],
      [ZIMAGE.id, 3],
    ]),
    total: 5,
  });
  expect(out).toHaveLength(1); // Z-Image has no pinned window: no claim
  expect(out[0].engine).toBe(SDXL.display_name);
  expect(out[0].slots).toBe(2);
  expect(out[0].limit).toBe(77);
  expect(out[0].unverified).toBe(true);
  expect(out[0].text).toContain("2 OF YOUR 5 SLOTS");
  expect(out[0].text).toContain(`READ ABOUT ${out[0].tokens} TOKENS`);
  expect(out[0].text).toContain("77-TOKEN WINDOW (UNVERIFIED)");
  expect(out[0].text).toContain("NOTHING IS CUT HERE");
});

test("no warning at or under the window, for an unstepped engine, or before the matrix arrives", () => {
  const short = { prompt: "a rusty sword", presets: [SDXL], total: 1 };
  expect(
    tokenWarnings({ ...short, engines: ENGINES, counts: new Map([[SDXL.id, 1]]) }),
  ).toEqual([]);
  expect(
    tokenWarnings({ prompt: long, engines: ENGINES, presets: [SDXL], counts: new Map(), total: 0 }),
  ).toEqual([]);
  expect(
    tokenWarnings({ prompt: long, engines: undefined, presets: [SDXL], counts: new Map([[SDXL.id, 1]]), total: 1 }),
  ).toEqual([]);
});
