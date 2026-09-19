// E5, class E's trust-critical case: a
// prompt over an engine's token window. Never truncate silently, never
// refuse: the server passes every prompt through whole (pinned in
// tests/test_trust_cases.py), so the cockpit's whole duty is the warning:
// per slot, before Run, naming the engine and the overflow, as a warning
// and never an error. Run stays armed.
//
// The count is an ESTIMATE and says so. The real tokenizers live inside
// the engines (CLIP's BPE for SDXL, Qwen3's for the Z-Image/FLUX.2 set),
// and none is on this side of the wire; a client-side count is a guess
// unless it wears the word — so it wears it: ABOUT. The estimate
// leans on CLIP's shape (one token per common word, long or rare words
// splitting into pieces, every punctuation mark its own token), which is
// the one engine with a pinned window today (engines.json: 77, unverified).
// An engine whose window is null in the matrix says nothing here — no
// window, no claim.
import type { Preset, Engines } from "../../types";
import type { Takes } from "./compose";
import { engineOf } from "./dials";

// A word, an apostrophe-joined word, or one punctuation mark.
const PIECE = /[\p{L}\p{N}]+(?:'[\p{L}]+)?|[^\s\p{L}\p{N}]/gu;

/** A CLIP-shaped estimate: 1 per word plus a piece per ~4 characters past
 *  six, 1 per punctuation mark. Never claims to be exact. */
export function estimateTokens(text: string): number {
  let n = 0;
  for (const m of text.matchAll(PIECE)) {
    const piece = m[0];
    if (/[\p{L}\p{N}]/u.test(piece)) n += 1 + Math.floor(Math.max(0, piece.length - 6) / 4);
    else n += 1;
  }
  return n;
}

export type TokenWarning = {
  engine: string; // the roster's display name
  slots: number; // takes this run sends to it
  tokens: number; // the estimate
  limit: number; // its window, from the matrix
  unverified: boolean;
  text: string; // the line, in the E2 register
};

/** One warning per stepped local engine whose window the prompt overruns —
 *  nothing for engines without a pinned window, nothing at or under it. */
export function tokenWarnings(args: {
  prompt: string;
  engines: Engines | undefined;
  presets: Preset[];
  counts: Takes;
  total: number;
}): TokenWarning[] {
  if (args.engines === undefined) return [];
  const tokens = estimateTokens(args.prompt);
  const out: TokenWarning[] = [];
  for (const p of args.presets) {
    const n = args.counts.get(p.id) ?? 0;
    if (n <= 0) continue;
    const e = args.engines.engines.find((x) => x.id === engineOf(p));
    const cap = e?.capabilities.tokenLimit;
    if (cap === undefined || typeof cap.value !== "number") continue;
    if (tokens <= cap.value) continue;
    const unverified = cap.state === "unverified";
    out.push({
      engine: p.display_name,
      slots: n,
      tokens,
      limit: cap.value,
      unverified,
      text:
        `${n} OF YOUR ${args.total} SLOT${args.total === 1 ? "" : "S"} — ` +
        `${p.display_name.toUpperCase()} — READ ABOUT ${tokens} TOKENS ` +
        `AGAINST A ${cap.value}-TOKEN WINDOW${unverified ? " (UNVERIFIED)" : ""}: ` +
        "THE TAIL MAY BE READ LIGHTLY OR NOT AT ALL. NOTHING IS CUT HERE — " +
        "SHORTEN THE PROMPT OR RUN AS IS.",
    });
  }
  return out;
}
