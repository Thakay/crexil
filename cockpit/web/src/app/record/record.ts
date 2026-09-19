// The provenance record's words: pure, vitest reaches it with
// plain objects. One record, three named rows, mounted by scope:
//   MADE FROM   the words — Turn.prompt / Node.prompt
//   THE RECIPE  the TURN's dials, said as the turn's (never the take's)
//   THIS TAKE   seed · engine · duration · verb · parent · frame · spend
// The component (Record.tsx) draws whatever these return; the grid mounts
// the turn-shaped rows, the ACTING ON head and the lightbox all three.
//
// What is on the wire, and so what can be said (law 12 — nothing here guesses):
//   Turn.dials (schemas.py Turn, filled by api_v4.run_turn / api_v4_regions
//   / api_v4_mesh) is NOT the composer's 0–100 positions — it is the seed
//   POLICY, the lineage (capability · parents · source sha) and, under
//   `engines`, every preset's APPLIED RAWS (steps, cfg, denoise, sampler,
//   width/height…; a mesh engine's `params`). So THE RECIPE prints raws —
//   the composer's own `SENDS STEPS 36` register — not dial names, because
//   the positions were never recorded. A region turn's words live per pass
//   under `dials.region.passes`; a mesh turn's prompt is its source's brief.
//   Slot carries no dials (schemas.py:457): a VARY ONE DIAL take or a
//   retry ran its own raws and the turn's cannot tell — hence the row is
//   always labelled the turn's. The one per-take raw that IS served is
//   `Slot.frame` (WxH at dispatch), so a retried frame is named on THIS TAKE.
// Every string is curated static text. Read-only: recall is the only
// write path (`armed.prompt`) and the component fires it.
import type { Node, Slot, Turn } from "../../types";
import { targetLabel } from "../verbs/verbs";

export type RecordRow = {
  /** The row's name, as shown. */
  name: "MADE FROM" | "THE RECIPE" | "THIS TAKE";
  /** One line per entry; a row with nothing behind it has exactly one,
   *  worded the way the app already words an absence (IMPORTED · NO SEED). */
  lines: string[];
  /** True when the row has nothing behind it — drawn dashed and muted. */
  blank: boolean;
  /** A qualifying micro-line under the row (whose dials these are, or why
   *  several nodes give one answer), or null. */
  note: string | null;
};

// ── the words ────────────────────────────────────────────────────────────

/** The prompt a single node recalls: its own, else its turn's. */
export function promptOf(node: Node | undefined, turn: Turn | undefined): string | null {
  const p = node?.prompt ?? turn?.prompt ?? null;
  return p !== null && p.trim() !== "" ? p : null;
}

/** MADE FROM — `nodes` empty means the turn itself (the grid's mount). */
export function madeFrom(turn: Turn | undefined, nodes: Node[]): RecordRow {
  const name = "MADE FROM" as const;
  if (nodes.length > 1) {
    const prompts = new Set(nodes.map((n) => n.prompt ?? ""));
    if (prompts.size === 1) {
      const p = [...prompts][0];
      return p.trim() === ""
        ? { name, lines: [`${nodes.length} SELECTED · NONE OF THEM CARRIES WORDS`], blank: true, note: null }
        : { name, lines: [p], blank: false, note: `SAID ONCE — ALL ${nodes.length} SELECTED SHARE IT` };
    }
    return {
      name,
      lines: [`${nodes.length} SELECTED · ${prompts.size} DIFFERENT PROMPTS — pick one to read its words`],
      blank: true,
      note: null,
    };
  }
  const node = nodes[0];
  const p = promptOf(node, turn);
  if (p !== null) return { name, lines: [p], blank: false, note: null };
  if (node !== undefined && node.turn_id === null)
    return {
      name,
      lines: ["IMPORTED · NO PROMPT — this node came in from a file, so there are no words to recall"],
      blank: true,
      note: null,
    };
  // a turn without a brief: a region edit carries its words per pass
  const passes = regionPasses(turn);
  const worded = passes.filter((p) => p.prompt !== null);
  if (worded.length > 0)
    return {
      name,
      lines: worded.map((p) => `${p.kind} · ${p.prompt}`),
      blank: false,
      note: "A REGION EDIT — ITS WORDS ARE PER PASS, THE TURN CARRIES NONE",
    };
  if (turn?.dials.capability === "region")
    return { name, lines: ["NO WORDS — every pass of this region edit was a KEEP or a DELETE"], blank: true, note: null };
  if (turn?.dials.capability === "to_3d")
    return { name, lines: ["NO WORDS — a mesh is made from its source image, and that source carried no prompt"], blank: true, note: null };
  return { name, lines: ["NO PROMPT — this turn ran without one"], blank: true, note: null };
}

type Pass = { kind: string; prompt: string | null };

function regionPasses(turn: Turn | undefined): Pass[] {
  const region = turn?.dials.region as { passes?: unknown } | undefined;
  if (!Array.isArray(region?.passes)) return [];
  return region.passes.flatMap((p: unknown) => {
    if (typeof p !== "object" || p === null) return [];
    const r = p as { kind?: unknown; prompt?: unknown };
    const prompt = typeof r.prompt === "string" && r.prompt.trim() !== "" ? r.prompt : null;
    return [{ kind: String(r.kind ?? "pass").toUpperCase(), prompt }];
  });
}

// ── the recipe ───────────────────────────────────────────────────────────

// The keys the routes add for lineage and identity — not knobs. Everything
// else under an engine's applied raws is a knob the run sent.
const NOT_A_KNOB = new Set([
  "seed", "seed_value", "prompt",
  "capability", "kind", "parents", "source_node_id", "source_image_sha256",
  "engine_id", "region_ids", "mask_sha256s", "policy",
]);
const STRING_CAP = 48;

function knobName(key: string): string {
  return key.replace(/_name$/, "").replace(/_/g, " ").toUpperCase();
}

function knobValue(v: unknown): string | null {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(+v.toFixed(3));
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (typeof v === "string") {
    const t = v.trim();
    if (t === "") return null;
    return t.length > STRING_CAP ? `"${t.slice(0, STRING_CAP - 1)}…"` : t.toUpperCase();
  }
  return null;
}

/** One engine's applied raws as the composer prints them: `STEPS 36 · CFG 4
 *  · 1024×1024 · SAMPLER EULER`. Width and height fold into the frame. */
export function knobLine(raws: Record<string, unknown>): string {
  const flat: Record<string, unknown> = { ...raws };
  // a mesh engine's knobs ride under `params`
  if (typeof flat.params === "object" && flat.params !== null)
    Object.assign(flat, flat.params as Record<string, unknown>);
  delete flat.params;
  const parts: string[] = [];
  const w = flat.width, h = flat.height;
  if (typeof w === "number" && typeof h === "number") parts.push(`${w}×${h}`);
  for (const [k, v] of Object.entries(flat)) {
    if (NOT_A_KNOB.has(k) || k === "width" || k === "height") continue;
    if (typeof v === "object" && v !== null) continue;
    const s = knobValue(v);
    if (s !== null) parts.push(`${knobName(k)} ${s}`);
  }
  return parts.join(" · ");
}

/** The turn's one seed policy, in words. */
export function seedPolicyLine(policy: unknown): string {
  const p = (typeof policy === "object" && policy !== null ? policy : {}) as {
    mode?: unknown; value?: unknown;
  };
  if (p.mode === "locked" && typeof p.value === "number") return `SEED LOCKED AT ${p.value} · EVERY TAKE`;
  if (p.mode === "fixed" && typeof p.value === "number") return `SEED ${p.value} · SPREAD BY SLOT`;
  return "SEEDS ROLLED PER TAKE";
}

/** Who a parent is, by the feed: the feed serves landed and set-aside nodes
 *  and never purged ones, so an id it can't find is a purged parent. */
function parentName(id: string | undefined, all: Node[]): string {
  if (id === undefined) return "A PARENT";
  const p = all.find((n) => n.id === id);
  return p === undefined ? "A PURGED PARENT" : targetLabel([p]).toUpperCase();
}

/** The turn's lineage: what it was made from, by capability. */
export function lineageLine(turn: Turn, all: Node[]): string {
  const d = turn.dials;
  const parents = Array.isArray(d.parents) ? (d.parents as string[]) : [];
  const first = parentName(parents[0], all);
  switch (d.capability) {
    case "img2img": return `REFINE OF ${first}`;
    case "region": return `REGION EDIT OF ${first} · ${String((d.region as { policy?: unknown } | undefined)?.policy ?? "nd3-v1").toUpperCase()}`;
    case "to_3d": {
      const kind = String(d.kind ?? "generate").toUpperCase();
      return parents.length > 1
        ? `${kind} · ${first} FROM ${parentName(parents[1], all)}`
        : `${kind} · FROM ${first}`;
    }
    default: return parents.length > 0 ? `FROM ${first}` : "FROM TEXT · A ROOT";
  }
}

/** THE RECIPE — one line per engine when several ran, the policy and the
 *  lineage on a last line. `scope` decides the note: the grid IS the turn,
 *  a take may have run its own raws the wire doesn't carry. */
export function recipe(
  turn: Turn | undefined,
  all: Node[],
  scope: "turn" | "take",
  nodes: Node[] = [],
): RecordRow {
  const name = "THE RECIPE" as const;
  const turnsAmong = new Set(nodes.map((n) => n.turn_id));
  if (nodes.length > 1 && turnsAmong.size > 1)
    return {
      name,
      lines: [`${turnsAmong.size} TURNS AMONG THE ${nodes.length} SELECTED — pick one to read its recipe`],
      blank: true,
      note: null,
    };
  if (turn === undefined)
    return nodes[0]?.turn_id != null
      // the feed answered before the detail did — say so, never NO TURN
      ? { name, lines: ["THE TURN'S RECORD IS ON ITS WAY"], blank: true, note: null }
      : { name, lines: ["NO TURN — an imported node ran nothing, so there is no recipe"], blank: true, note: null };
  const engines = (typeof turn.dials.engines === "object" && turn.dials.engines !== null
    ? turn.dials.engines : {}) as Record<string, Record<string, unknown>>;
  const entries = Object.entries(engines)
    .map(([id, raws]) => ({ id, line: knobLine(raws ?? {}) }))
    .filter((e) => e.line !== "");
  const lines =
    entries.length === 0
      ? [turn.slots.some((s) => s.engine.includes("/"))
          ? "EXTERNAL · THE MODEL'S OWN DEFAULTS — no raws to recall"
          : "NO RAWS RECORDED ON THIS TURN"]
      : entries.length === 1
        ? [entries[0].line]
        : entries.map((e) => `${e.id.toUpperCase()} · ${e.line}`);
  const tail = [seedPolicyLine(turn.dials.seed), lineageLine(turn, all)];
  if (turn.dials.local_only === true) tail.push("LOCAL ONLY");
  lines.push(tail.join(" · "));
  return {
    name,
    lines,
    blank: entries.length === 0,
    note:
      scope === "turn"
        ? "THIS TURN'S DIALS, AS SENT"
        : "THE TURN'S DIALS — a swept or retried cell ran its own, and the wire doesn't carry those",
  };
}

// ── this take ────────────────────────────────────────────────────────────

export function durationWord(ms: number | null): string | null {
  if (ms === null) return null;
  const s = ms / 1000;
  if (s < 60) return `${(Math.round(s * 10) / 10).toString()} S`;
  return `${Math.floor(s / 60)} MIN ${Math.round(s % 60)} S`;
}

function verbWord(node: Node, all: Node[]): string {
  const verb = node.verb.replace(/_/g, " ").toUpperCase();
  if (node.verb === "import") return "IMPORTED";
  const [p0, p1] = node.parents;
  if (p0 === undefined) return verb;
  const of = `${verb} OF ${parentName(p0, all)}`;
  return node.verb === "texture" && p1 !== undefined ? `${of} FROM ${parentName(p1, all)}` : of;
}

function spendWord(node: Node): string {
  if (node.charged_usd !== null) return `$${node.charged_usd.toFixed(2)}`;
  if (node.committed_usd !== null) return `$${node.committed_usd.toFixed(2)} COMMITTED`;
  return "NO SPEND";
}

/** The take's own frame, from its slot — the one per-take raw on the wire —
 *  and whether it differs from the turn's recipe for that engine. */
export function frameWords(node: Node, turn: Turn | undefined): { frame: string | null; differs: boolean } {
  const slot: Slot | undefined = turn?.slots.find((s) => s.id === node.slot_id);
  const frame = slot?.frame ?? null;
  if (slot === undefined || frame === null) return { frame: null, differs: false };
  const raws = (turn?.dials.engines as Record<string, Record<string, unknown>> | undefined)?.[slot.engine];
  const w = raws?.width, h = raws?.height;
  const differs = typeof w === "number" && typeof h === "number" && `${w}x${h}` !== frame;
  return { frame: frame.replace("x", "×"), differs };
}

/** THIS TAKE — the node's own facts, all on the wire (NODE_KEYS). */
export function thisTake(nodes: Node[], turn: Turn | undefined, all: Node[]): RecordRow {
  const name = "THIS TAKE" as const;
  if (nodes.length === 0) return { name, lines: [], blank: true, note: null };
  if (nodes.length > 1) {
    const seeds = nodes.map((n) => (n.seed === null ? "NO SEED" : String(n.seed)));
    return {
      name,
      lines: [
        nodes.length <= 4
          ? `${nodes.length} TAKES · SEEDS ${seeds.join(" · ")}`
          : `${nodes.length} TAKES — pick one for its seed, engine and time`,
      ],
      blank: false,
      note: null,
    };
  }
  const n = nodes[0];
  const parts: string[] = [];
  parts.push(n.seed === null ? "NO SEED" : `SEED ${n.seed}`);
  // an imported node has no engine; the verb word already says IMPORTED
  if (n.engine !== null) parts.push(n.engine.toUpperCase());
  const dur = durationWord(n.duration_ms);
  if (dur !== null) parts.push(dur);
  parts.push(verbWord(n, all));
  const { frame, differs } = frameWords(n, turn);
  if (frame !== null) parts.push(frame);
  if (n.take !== null && n.take > 1) parts.push(`TAKE ${n.take} IN THIS CELL`);
  parts.push(spendWord(n));
  if (n.set_aside) parts.push("SET ASIDE");
  if (n.promoted_at != null) parts.push("PROMOTED");
  return {
    name,
    lines: [parts.join(" · ")],
    blank: false,
    note: differs ? `RAN AT ${frame} — NOT THE TURN'S FRAME (a retry at a smaller size)` : null,
  };
}

/** The whole record for a scope: the turn's two rows, or a selection's
 *  three. `turn` is the node's turn (by `turn_id`) or the grid's. */
export function recordRows(args: {
  scope: "turn" | "take";
  turn: Turn | undefined;
  nodes: Node[];
  all: Node[];
}): RecordRow[] {
  const { scope, turn, nodes, all } = args;
  const rows = [madeFrom(turn, nodes), recipe(turn, all, scope, nodes)];
  if (scope === "take") rows.push(thisTake(nodes, turn, all));
  return rows;
}

/** The prompt the P key recalls for a scope, or null when there is none to
 *  recall — several nodes with different words recall nothing. */
export function recallPrompt(turn: Turn | undefined, nodes: Node[]): string | null {
  if (nodes.length > 1) {
    const prompts = new Set(nodes.map((n) => n.prompt ?? ""));
    if (prompts.size !== 1) return null;
    const p = [...prompts][0];
    return p.trim() === "" ? null : p;
  }
  return promptOf(nodes[0], turn);
}
