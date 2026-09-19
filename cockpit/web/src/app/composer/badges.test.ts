// The badge grammar against the capability matrix
// (law 12): verified positives print plain, anything unverified carries
// the word, verified absences stay off the row (the dashed 0's reason
// line owns the one that gates a run), ceilings print only with a value,
// a channel no verb can use yet prints nowhere, and neither an unknown
// engine nor a missing matrix is ever guessed at.
import { expect, test } from "vitest";
import type { EngineCapability, Engines } from "../../types";
import {
  NO_RECORD,
  badgeLine,
  capabilityBadges,
  externalBadges,
  localBadges,
} from "./badges";
import { KLEIN, SDXL, TURBO } from "./dials.test";

const cap = (
  value: unknown,
  state: "verified" | "unverified" = "verified",
): EngineCapability => ({ value, state, note: "n" });

// configs/engines.json as authored 2026-08-11, the rows the roster shows
const SDXL_CAPS: Record<string, EngineCapability> = {
  mask: cap(true),
  seed: cap(true),
  imageRef: cap(true),
  styleRef: cap(false),
  maxResolution: cap(2048, "unverified"),
  tokenLimit: cap(77, "unverified"),
};
const KLEIN_CAPS: Record<string, EngineCapability> = {
  mask: cap(false),
  seed: cap(true),
  imageRef: cap(false),
  styleRef: cap(false),
  maxResolution: cap(2048, "unverified"),
  tokenLimit: cap(null, "unverified"),
};

const MATRIX: Engines = {
  updated: "2026-08-11",
  engines: [
    {
      id: "sdxl-juggernaut-xi",
      name: "SDXL Juggernaut XI",
      lane: "local",
      payload: "image",
      verbs: [],
      params: {},
      price: "free",
      capabilities: SDXL_CAPS,
    },
    {
      id: "flux2-klein-4b",
      name: "FLUX.2 klein 4B",
      lane: "local",
      payload: "image",
      verbs: [],
      params: {},
      price: "free",
      capabilities: KLEIN_CAPS,
    },
  ],
  external: {
    lane: "external",
    payload: "image",
    price: "",
    catalogue: "",
    defaults: {
      mask: cap(false),
      imageRef: cap(false),
      styleRef: cap(false),
      seed: cap(false, "unverified"),
      maxResolution: cap(null, "unverified"),
      tokenLimit: cap(null, "unverified"),
    },
  },
};

const MODEL = {
  id: "openai/gpt-image-2",
  provider: "openrouter",
  label: "GPT Image 2",
  name: "GPT Image 2",
  supports_seed: false,
  pinned: true,
  source: "config" as const,
  price_usd_per_image: 0.04,
};

test("verified positives print plain, in roster order; unverified ceilings carry the word", () => {
  expect(badgeLine(capabilityBadges(SDXL_CAPS))).toBe(
    "SEED · REF · MASK · UP TO 2048 UNVERIFIED · 77 TOKENS UNVERIFIED",
  );
  expect(capabilityBadges(SDXL_CAPS).map((b) => b.unverified)).toEqual([
    false, false, false, true, true,
  ]);
});

test("a verified absence stays off the row; a null ceiling prints nothing", () => {
  // klein: no img2img graph, no region recipe (structural, verified) —
  // the refine roster's reason line says so where it gates; the token
  // ceiling isn't pinned, so nothing is claimed for it
  expect(badgeLine(capabilityBadges(KLEIN_CAPS))).toBe(
    "SEED · UP TO 2048 UNVERIFIED",
  );
});

test("an unverified flag prints its value AND the word, whichever the value", () => {
  expect(badgeLine(capabilityBadges({ seed: cap(false, "unverified") }))).toBe(
    "NO SEED UNVERIFIED",
  );
  expect(badgeLine(capabilityBadges({ seed: cap(true, "unverified") }))).toBe(
    "SEED UNVERIFIED",
  );
  expect(badgeLine(capabilityBadges({ maxResolution: cap(1024) }))).toBe(
    "UP TO 1024",
  );
});

test("a channel no verb can use yet prints nowhere (styleRef waits for an anchor channel)", () => {
  expect(capabilityBadges({ styleRef: cap(true) })).toEqual([]);
  expect(capabilityBadges({ styleRef: cap(true, "unverified") })).toEqual([]);
});

test("a local row reads its preset's engine; an engine the matrix doesn't know says so", () => {
  expect(badgeLine(localBadges(MATRIX, SDXL)!)).toBe(
    "SEED · REF · MASK · UP TO 2048 UNVERIFIED · 77 TOKENS UNVERIFIED",
  );
  expect(badgeLine(localBadges(MATRIX, KLEIN)!)).toBe("SEED · UP TO 2048 UNVERIFIED");
  // z-image-turbo has no row in this matrix — nothing is borrowed
  expect(localBadges(MATRIX, TURBO)).toEqual([{ text: NO_RECORD, unverified: true }]);
});

test("no matrix yet = no badges — silence, not a guess", () => {
  expect(localBadges(undefined, SDXL)).toBeNull();
  expect(externalBadges(undefined, MODEL)).toBeNull();
});

test("an external row: the lane defaults with the seed from the model's pin, at the lane's state", () => {
  expect(badgeLine(externalBadges(MATRIX, MODEL)!)).toBe("NO SEED UNVERIFIED");
  expect(badgeLine(externalBadges(MATRIX, { ...MODEL, supports_seed: true })!)).toBe(
    "SEED UNVERIFIED",
  );
  // a lane without a seed default: the pin still prints, and still unverified
  const bare = { ...MATRIX, external: { ...MATRIX.external, defaults: {} } };
  expect(badgeLine(externalBadges(bare, { ...MODEL, supports_seed: true })!)).toBe(
    "SEED UNVERIFIED",
  );
});
