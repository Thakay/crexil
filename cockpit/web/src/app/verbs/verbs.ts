// The verb roster and its resolution. Pure: vitest
// exercises it with plain node objects, no React, no query layer.
//
// Two rules carry the whole file:
//   The row is filtered by the selected node's PAYLOAD ONLY, never by
//        which verb produced it. A refined node and a masked node offer the
//        same verbs.
//   Law 12 — a verb the app can't fire stays visible, de-emphasised,
//        with the reason in plain words. Nothing is hidden, so the artist
//        never has to wonder whether the app can do it at all.
//
// Hence two tiers, and they mean different things:
//   `blocked`      — right kind of node, the machinery isn't here yet. Stays
//                    in the main row, dashed, reason instead of effect line.
//   `notForThis`   — wrong kind of node. Drops to the NOT FOR THIS NODE group.
//
// And one cut ACROSS those tiers: a verb that cannot be
// un-happened sits BELOW a hairline, red-ruled, and fires only through a
// hold. `irreversible` marks those two — PROMOTE and PURGE — so the row can
// draw them apart from everything a second click can undo. It is not a
// third tier: an irreversible verb still blocks, still drops to NOT FOR
// THIS NODE, still carries its reason.
// Every string here is curated static text — nothing is generated.
//
// Roster = the image verbs plus the 3D lane's own (law 16: every 3D step
// is an engine-shaped verb, and a verb here is how the artist reaches it).
// Animate, To Texture, Extract Style and Moodboard are ABSENT UNTIL
// DESIGNED. They are not stubbed here.
import type { Node } from "../../types";

export type VerbId =
  | "new-root"
  | "refine"
  | "edit-region"
  | "vary-dial"
  | "upscale"
  | "to-3d"
  | "decimate"
  | "texture"
  | "inspect"
  | "promote"
  | "reroll"
  | "set-aside"
  | "restore"
  | "purge";

type Payload = Node["payload"];

type VerbDef = {
  id: VerbId;
  label: string;
  effect: string;              // the 10.5px plain-effect line
  payloads: readonly Payload[]; // which node kinds accept it
  single: boolean;             // acts on exactly one node
  // Replaces the default "acts on one node" line when several are selected,
  // for a verb whose one-ness has a reason worth stating.
  singleWhy?: string;
  // Why it can't fire yet — null means the route exists and the row runs it.
  // Named for what the artist would see, not for the module that's missing.
  notWired: string | null;
  // Its keystroke on the canvas, shown on the chip (every drag
  // destination also has one: set aside is the tray's).
  key?: string;
  // Cannot be un-happened: below the hairline, red-ruled, held.
  irreversible?: true;
};

const ROSTER: readonly VerbDef[] = [
  {
    id: "refine",
    label: "REFINE",
    effect: "same prompt, new seeds",
    payloads: ["image"],
    // One source per refine: every committed img2img graph has a
    // single `source_image`, and a second parent would have to be guessed
    // into a channel that isn't built.
    single: true,
    singleWhy:
      "one source image per refine — the recipes take a single parent, and channels for a second aren't built yet",
    notWired: null,
  },
  {
    // Opens the region surface on this take: draw regions, declare
    // KEEP/MODIFY/REPLACE/DELETE, then fan the run out from there
    // (regions/RegionSurface.tsx owns the roster, the run and the panes).
    id: "edit-region",
    label: "EDIT REGION",
    effect: "mask a part, keep the rest",
    payloads: ["image"],
    single: true,
    notWired: null,
  },
  {
    // Arms the composer on this take with SWEEP ONE DIAL already on
    // — four slots walking one dial from here, everything else held.
    id: "vary-dial",
    label: "VARY ONE DIAL",
    effect: "four takes from here, one dial walked, all else held",
    payloads: ["image"],
    single: true,
    notWired: null,
  },
  {
    id: "upscale",
    label: "UPSCALE",
    effect: "2× or 4×, no new content",
    payloads: ["image"],
    single: true,
    notWired: "not wired yet — no upscale recipe is committed",
  },
  {
    // The chip opens the To-3D plinth in the dock (mesh/) rather
    // than firing — like a run, a mesh needs an engine and a take count,
    // and unlike a run it needs nothing else. The lane reaches the GPU
    // through the host-side runner; a runner that isn't
    // answering dims the plinth's ROWS with the reason, it does not
    // block this chip — the artist gets to see the lane and be told
    // (law 12), not find the verb missing.
    id: "to-3d",
    label: "TO 3D",
    effect: "turn this take into a mesh",
    payloads: ["image"],
    single: true,
    singleWhy:
      "one image per mesh — each take is conditioned on a single source, and there is no channel for a second",
    notWired: null,
  },
  {
    // The first verb whose PARENT is a mesh (law 16): gltfpack's meshopt
    // simplifier over the landed GLB. Seconds on the CPU, no GPU, no model,
    // so it never queues behind an image take.
    //
    // Like TO 3D the chip OPENS a plinth rather than firing, and for a
    // sharper reason: this verb has a handle. The triangle count is a
    // REQUEST the mesh may refuse — a machine UV atlas locks the seams a
    // simplifier would have to collapse — so there has to be somewhere to
    // type a number and somewhere to say what the number means.
    id: "decimate",
    label: "DECIMATE",
    effect: "fewer triangles, same shape — a smaller mesh beside this one",
    payloads: ["3d"],
    single: true,
    singleWhy:
      "one mesh per decimate — the tool takes a single GLB, and each child records what its own parent allowed",
    notWired: null,
  },
  {
    // The first verb with TWO parents (law 16): this mesh is the
    // shape, a reference image picked in the plinth is the appearance.
    // Hunyuan3D-Paint repaints any mesh from the reference; the plinth
    // carries the honesty (Paint rebuilds at 40k faces before painting),
    // which is exactly why the chip opens a surface rather than firing.
    id: "texture",
    label: "TEXTURE",
    effect: "repaint this mesh from an image — same shape, new look",
    payloads: ["3d"],
    single: true,
    singleWhy:
      "one mesh per repaint — the painter takes a single shape, and the reference is picked in the plinth",
    notWired: null,
  },
  {
    // The lightbox's own verb: a visible door and a
    // keystroke, an ADDITION to the double-click, which stays. Payloads are
    // BOTH, not ["3d"]: law 12 would otherwise print "not for this node"
    // on an image, and the image lightbox works — so the image gains the
    // key as a consequence of honesty. A mesh gets the viewer.
    id: "inspect",
    label: "INSPECT",
    effect: "full size in the lightbox — a mesh orbits, underside too",
    payloads: ["image", "3d"],
    single: true,
    notWired: null,
    key: "V",
  },
  {
    // The one act that leaves the cockpit. The chip opens the
    // plinth (destination, files, cost, all before the hold) rather than
    // firing — the consequence is stated, then held.
    id: "promote",
    label: "PROMOTE",
    effect: "copy it out to the asset tree, with its manifest",
    payloads: ["image", "3d"],
    single: true,
    singleWhy:
      "one take at a time — each promotion writes its own manifest, and the plinth says what this one would write",
    notWired: null,
    irreversible: true,
  },
  {
    id: "reroll",
    label: "REROLL",
    effect: "re-run this cell, new seed — the old take stays",
    payloads: ["image", "3d"],
    single: true,
    notWired: null,
  },
  {
    id: "set-aside",
    label: "SET ASIDE",
    effect: "out of the way, not deleted",
    payloads: ["image", "3d"],
    // Any number at once: the route is per node and idempotent, so a
    // refusal is per node too, never per batch. ⌫ and the tray drop share
    // this width.
    single: false,
    notWired: null,
    key: "⌫",
  },
  {
    id: "restore",
    label: "RESTORE",
    effect: "bring it back to the canvas",
    payloads: ["image", "3d"],
    single: true,
    notWired: null,
  },
  {
    // The one irreversible verb (law 7) fires only from the
    // tray, held. On the canvas it stays in the row (a verb in the
    // roster is never hidden) and says where it lives.
    id: "purge",
    label: "PURGE",
    effect: "gone for good — held, from the tray",
    payloads: ["image", "3d"],
    single: false,
    notWired: null,
    irreversible: true,
  },
];

// Nothing selected: the row falls back to root verbs ("Esc →
// acting on nothing"). A root run needs no parent, so this one is live.
const ROOT: VerbDef = {
  id: "new-root",
  label: "NEW IMAGE · ROOT",
  effect: "start a fresh image from a prompt",
  payloads: ["image"],
  single: false,
  notWired: null,
};

export type Chip = {
  id: VerbId;
  label: string;
  /** The plain-effect line, or — when it can't fire — the reason. */
  line: string;
  live: boolean;
  /** The keystroke that fires it from the canvas, when it has one. */
  key?: string;
};

export type Row = {
  /** null when acting on nothing: the row is showing root verbs. */
  target: { label: string; facts: string; nodes: Node[] } | null;
  chips: Chip[];       // main row — live and blocked, in roster order
  /** Below the hairline, red-ruled, held. */
  irreversible: Chip[];
  notForThis: Chip[];  // dashed group, never empty-hidden, never filtered away
};

const KIND: Record<Payload, string> = { image: "IMAGE", "3d": "MESH" };

// What we actually know about the node, and nothing more: payload, engine,
// whether a seed was recorded, and whether it's set aside. Frame size and
// maskability are NOT stored per node — guessing them here would be the
// "unverified shown as verified" failure law 12 forbids; the engine's flags,
// with their states, live on the roster row (composer/badges.ts).
export function factsLine(nodes: Node[]): string {
  if (nodes.length === 0) return "";
  const kinds = new Set(nodes.map((n) => n.payload));
  if (nodes.length > 1) {
    const kind = kinds.size === 1 ? KIND[nodes[0].payload] : "MIXED KINDS";
    return `${kind} · ${nodes.length} SELECTED · PARENT ORDER AS SELECTED`;
  }
  const n = nodes[0];
  const parts = [KIND[n.payload], (n.engine ?? "IMPORTED").toUpperCase()];
  parts.push(n.seed === null ? "NO SEED" : "SEEDED");
  if (n.set_aside) parts.push("SET ASIDE");
  return parts.join(" · ");
}

export function targetLabel(nodes: Node[]): string {
  if (nodes.length === 1) {
    const n = nodes[0];
    if (n.turn_idx === null) return "Imported node";
    const letter =
      n.slot_pos === null ? "" : String.fromCharCode(97 + n.slot_pos);
    return `Take ${n.turn_idx}${letter}`;
  }
  return `${nodes.length} nodes`;
}

// The one gate that isn't about payload: a verb that acts on a single node,
// with two selected. Stated as what the artist did, not as a rule id.
function whyNotHere(v: VerbDef, nodes: Node[]): string | null {
  if (v.single && nodes.length > 1)
    return v.singleWhy ?? `acts on one node — ${nodes.length} are selected`;
  if (v.id === "reroll" && nodes[0]?.slot_id === null)
    return "this node didn't come from a slot, so there's no cell to re-run";
  if (v.id === "purge" && !nodes.every((n) => n.set_aside))
    return "set it aside first — purge only empties the tray (T), held there";
  if (v.notWired !== null) return v.notWired;
  return null;
}

/** Which of set-aside / restore this selection can be offered. */
function suppressed(v: VerbDef, nodes: Node[]): boolean {
  // Every node aside (a tray selection) offers restore; anything else —
  // canvas nodes only, since aside takes leave the canvas — offers set
  // aside, which is idempotent per node if a mix ever appears.
  const aside = nodes.every((n) => n.set_aside);
  if (v.id === "restore") return !aside;
  if (v.id === "set-aside") return aside;
  return false;
}

export function resolveRow(selection: string[], nodes: Node[]): Row {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const picked = selection
    .map((id) => byId.get(id))
    .filter((n): n is Node => n !== undefined);

  if (picked.length === 0) {
    return {
      target: null,
      chips: [{ id: ROOT.id, label: ROOT.label, line: ROOT.effect, live: true }],
      irreversible: [],
      notForThis: [],
    };
  }

  const kinds = new Set(picked.map((n) => n.payload));
  const chips: Chip[] = [];
  const irreversible: Chip[] = [];
  const notForThis: Chip[] = [];

  for (const v of ROSTER) {
    if (suppressed(v, picked)) continue;
    const wrongKind = [...kinds].filter((k) => !v.payloads.includes(k));
    if (wrongKind.length > 0) {
      notForThis.push({
        id: v.id,
        label: v.label,
        line:
          kinds.size > 1
            ? "the two selected nodes aren't the same kind"
            : wrongKind[0] === "3d"
              ? "this node is a mesh — that verb works on images"
              : "this node is an image — that verb works on meshes",
        live: false,
      });
      continue;
    }
    const why = whyNotHere(v, picked);
    (v.irreversible ? irreversible : chips).push({
      id: v.id,
      label: v.label,
      line: why ?? v.effect,
      live: why === null,
      key: v.key,
    });
  }

  return {
    target: {
      label: targetLabel(picked),
      facts: factsLine(picked),
      nodes: picked,
    },
    chips,
    irreversible,
    notForThis,
  };
}
