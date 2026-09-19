// Capability badges on the roster rows (law 12):
// what each engine can do, read from the capability matrix
// (`configs/engines.json`, served as authored by GET /api/engines and
// interpreted only here and in dials.ts's frameWarning) with its
// verification state ON it. The grammar keeps the FRAME warning's pattern
// (NAME: STATE above N — note) as far as a 9 px line allows:
//
//   verified true  → the channel's word           SEED · REF · MASK
//   unverified     → the word plus the state,     UP TO 2048 UNVERIFIED
//                    whichever the value          NO SEED UNVERIFIED
//   verified false → nothing on the row. The absence is structural (no
//                    committed graph or route — engines.json's own
//                    semantics), and where it gates THIS run the dashed
//                    0's reason line says so in words (law 12). Printing
//                    every verified "NO" on every capable row would say
//                    nothing that tells the engines apart.
//
// A numeric ceiling prints only with a value — a null one is "not on
// record", and the FRAME dial says that at the point of use. A channel
// no verb in the roster can use yet prints nowhere (styleRef: identical
// on every row until an anchor channel is wired). An engine
// the matrix doesn't know says so rather than borrowing a neighbour's
// flags; a matrix that hasn't arrived prints nothing — silence isn't a
// claim, a guess would be. Every string here is curated static
// text.
//
// Pure: Composer.tsx renders the line, vitest exercises the grammar.
import type {
  Preset,
  ProviderModel,
  EngineCapability,
  Engines,
} from "../../types";
import { engineOf } from "./dials";

export type Badge = { text: string; unverified: boolean };

/** Boolean channels in roster order — each prints because a verb in the
 *  row needs it: the SEED dial's lock · REFINE / VARY ONE DIAL (img2img)
 *  · EDIT REGION (the mask surface). */
const CHANNELS: readonly { key: string; word: string }[] = [
  { key: "seed", word: "SEED" },
  { key: "imageRef", word: "REF" },
  { key: "mask", word: "MASK" },
];

/** Numeric ceilings, printed only with a value. */
const CEILINGS: readonly { key: string; print: (v: number) => string }[] = [
  { key: "maxResolution", print: (v) => `UP TO ${v}` },
  { key: "tokenLimit", print: (v) => `${v} TOKENS` },
];

export const UNVERIFIED = "UNVERIFIED";

/** A preset whose engine the matrix has no row for. */
export const NO_RECORD = "NO CAPABILITY RECORD";

/** Said once on the roster (law 1) when GET /api/engines fails. */
export const MATRIX_UNREACHABLE =
  "Capability matrix unreachable — no row can say what it can do.";

function channelBadge(word: string, cap: EngineCapability): Badge | null {
  const unverified = cap.state === "unverified";
  if (cap.value === true)
    return { text: unverified ? `${word} ${UNVERIFIED}` : word, unverified };
  if (cap.value === false && unverified)
    return { text: `NO ${word} ${UNVERIFIED}`, unverified };
  return null;
}

/** One engine's capability set → its badges, in roster order. */
export function capabilityBadges(
  caps: Record<string, EngineCapability>,
): Badge[] {
  const out: Badge[] = [];
  for (const c of CHANNELS) {
    const cap = caps[c.key];
    if (cap === undefined) continue;
    const b = channelBadge(c.word, cap);
    if (b !== null) out.push(b);
  }
  for (const c of CEILINGS) {
    const cap = caps[c.key];
    if (cap === undefined || typeof cap.value !== "number") continue;
    const unverified = cap.state === "unverified";
    const text = c.print(cap.value);
    out.push({ text: unverified ? `${text} ${UNVERIFIED}` : text, unverified });
  }
  return out;
}

/** A local row's badges — null while the matrix hasn't arrived. */
export function localBadges(
  engines: Engines | undefined,
  preset: Preset,
): Badge[] | null {
  if (engines === undefined) return null;
  const e = engines.engines.find((x) => x.id === engineOf(preset));
  if (e === undefined) return [{ text: NO_RECORD, unverified: true }];
  return capabilityBadges(e.capabilities);
}

/** An external row's badges: the lane's authored defaults, the seed read
 *  from the model's providers.json pin (`supports_seed`) at the lane's
 *  own state — a pin is authored belief too. The wire carries no
 *  per-model overrides yet (engines.json's `overrides` is empty and
 *  unserved): the first observed per-model truth extends
 *  ExternalEngineLane, and this is where it would print. */
export function externalBadges(
  engines: Engines | undefined,
  model: ProviderModel,
): Badge[] | null {
  if (engines === undefined) return null;
  const d = engines.external.defaults;
  const seed: EngineCapability = {
    ...(d.seed ?? { note: "", state: "unverified" }),
    value: model.supports_seed,
  };
  return capabilityBadges({ ...d, seed });
}

/** The row's one line — one text node, so a locator on `SEED` still finds
 *  the dial's label alone. */
export function badgeLine(badges: Badge[]): string {
  return badges.map((b) => b.text).join(" · ");
}
