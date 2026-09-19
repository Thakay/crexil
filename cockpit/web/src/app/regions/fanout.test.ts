// The fan-out core: the roster's badge-never-filter derivation
// (law 12), the disarm reasons, the run bar's counts, the RegionRun
// serialization (z-order IS list order, prompts only where the action
// takes one, masks flat), and the commit bar's words.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Preset, Engines } from "../../types";
import {
  attemptsHeader,
  buildRegionRun,
  continueLabel,
  EXTERNALS_LINE,
  fanBarLine,
  fanGate,
  NO_RECIPES_REASON,
  regionRoster,
} from "./fanout";
import { NO_RECORD, UNVERIFIED } from "../composer/badges";
import type { RegionDraft } from "./model";

const REGION_WORKFLOWS = {
  txt2img: "t@v1",
  inpaint_modify: "m@v1",
  inpaint_replace: "r@v1",
  remove: "d@v1",
};

function preset(patch: Partial<Preset>): Preset {
  return {
    id: "raw-sdxl",
    display_name: "Raw · SDXL",
    description: "",
    modality: "image",
    workflows: REGION_WORKFLOWS,
    default_candidates: 1,
    inputs: [],
    model_family: "sdxl-juggernaut-xi",
    ...patch,
  } as Preset;
}

function engines(rows: Engines["engines"]): Engines {
  return {
    engines: rows,
    external: {
      catalogue: "openrouter",
      defaults: {},
      lane: "external",
      payload: "image",
      price: "per response",
    },
    updated: "2026-08-26",
  };
}

function engineRow(
  id: string,
  maskState: "verified" | "unverified",
): Engines["engines"][number] {
  return {
    id,
    name: id,
    lane: "local",
    payload: "image",
    verbs: [],
    params: {},
    price: "free",
    capabilities: { mask: { note: "", state: maskState, value: true } },
  };
}

function draft(patch: Partial<RegionDraft>): RegionDraft {
  return {
    key: "k",
    name: "region 1",
    action: "modify",
    prompt: "",
    mask: null,
    inkRev: 0,
    inked: true,
    visible: true,
    ...patch,
  };
}

describe("regionRoster — badge, never filter (law 12)", () => {
  it("capable first; the incapable stay listed with the reason; externals are one line", () => {
    const rows = regionRoster(
      [
        preset({ id: "raw-zimage", workflows: { txt2img: "t@v1" }, model_family: "zimage-turbo" }),
        preset({}),
        preset({ id: "mesh", modality: "3d" }),
      ],
      engines([engineRow("sdxl-juggernaut-xi", "verified")]),
    );
    expect(rows.map((r) => r.preset.id)).toEqual(["raw-sdxl", "raw-zimage"]);
    expect(rows[0]).toMatchObject({ capable: true, reason: null, stateWord: null });
    expect(rows[1]).toMatchObject({ capable: false, reason: NO_RECIPES_REASON });
    expect(EXTERNALS_LINE).toContain("LOCAL RAILS");
  });

  it("an unverified mask wears the word; a preset the matrix doesn't know says so", () => {
    const rows = regionRoster(
      [
        preset({ id: "raw-qwen-edit", model_family: "qwen-edit-2511" }),
        preset({}),
      ],
      engines([engineRow("qwen-edit-2511", "unverified")]),
    );
    expect(rows[0].stateWord).toBe(UNVERIFIED);
    expect(rows[1].stateWord).toBe(NO_RECORD);
  });

  it("no matrix yet = no state claim (silence is not a claim)", () => {
    const rows = regionRoster([preset({})], undefined);
    expect(rows[0].stateWord).toBeNull();
  });
});

describe("fanGate — the disarm reasons, in words", () => {
  const ready = {
    drafts: [draft({})],
    counts: new Map([["raw-sdxl", 1]]),
    rosterReady: true,
    imageReady: true,
  };

  it("armed when a drawn region, a stepped engine and the pixels are all in hand", () => {
    expect(fanGate(ready)).toBeNull();
  });

  it("each missing piece names itself", () => {
    expect(fanGate({ ...ready, rosterReady: false })).toContain("roster");
    expect(fanGate({ ...ready, imageReady: false })).toContain("pixels");
    expect(fanGate({ ...ready, drafts: [] })).toBe(
      "Draw at least one region first.",
    );
    expect(
      fanGate({ ...ready, drafts: [draft({}), draft({ inked: false })] }),
    ).toBe("R2 has no ink yet — draw it or remove the row.");
    expect(fanGate({ ...ready, counts: new Map() })).toBe(
      "Step at least one engine above zero takes.",
    );
  });
});

describe("fanBarLine — counts only, no predictions (law 17)", () => {
  it("tallies takes and stepped engines with the one-GPU sentence", () => {
    expect(fanBarLine(new Map([["a", 2], ["b", 1], ["c", 0]]))).toBe(
      "3 TAKES · 2 ENGINES · LOCALS IN SERIES, ONE GPU",
    );
    expect(fanBarLine(new Map([["a", 1]]))).toBe(
      "1 TAKE · 1 ENGINE · LOCALS IN SERIES, ONE GPU",
    );
  });
});

describe("buildRegionRun — the RegionRun the surface posts", () => {
  afterEach(() => vi.restoreAllMocks());

  function stubExport() {
    // exportMaskB64 goes through canvas; the request shape is what's
    // under test here — the flatten itself is model.test.ts's.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      fillStyle: "",
      fillRect: () => {},
      drawImage: () => {},
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/png;base64,TUFTSw==",
    );
  }

  it("serializes drafts in creation order with prompts only where the action takes one", () => {
    stubExport();
    const body = buildRegionRun({
      drafts: [
        draft({ key: "a", name: "sky", action: "keep", prompt: "ignored" }),
        draft({ key: "b", name: "barrel", action: "delete" }),
        draft({ key: "c", name: "wrap", action: "modify", prompt: " tighter wrap " }),
        draft({ key: "d", name: "blade", action: "replace", prompt: "" }),
      ],
      natural: { w: 64, h: 64 },
      counts: new Map([
        ["raw-sdxl", 2],
        ["raw-zimage", 0],
        ["raw-qwen-edit", 1],
      ]),
      prompt: "  a knight  ",
      source: { id: "n1", x: 120, y: 80 },
    });
    expect(body.parents).toEqual(["n1"]);
    expect(body.prompt).toBe("a knight");
    expect(body.regions.map((r) => r.name)).toEqual([
      "sky",
      "barrel",
      "wrap",
      "blade",
    ]);
    // KEEP/DELETE never carry a prompt; an empty REPLACE falls back (null).
    expect(body.regions.map((r) => r.prompt)).toEqual([
      null,
      null,
      "tighter wrap",
      null,
    ]);
    expect(body.regions.every((r) => r.mask_b64 === "TUFTSw==")).toBe(true);
    // Zero-stepped engines never reach the wire.
    expect(body.local).toEqual([
      { preset_id: "raw-sdxl", takes: 2, inputs: {} },
      { preset_id: "raw-qwen-edit", takes: 1, inputs: {} },
    ]);
    expect(body.seed).toEqual({ mode: "random" });
    expect(body.anchor).toEqual({ x: 120, y: 80 });
  });

  it("a source that was never dragged sends no anchor; an empty prompt sends null", () => {
    stubExport();
    const body = buildRegionRun({
      drafts: [draft({})],
      natural: { w: 8, h: 8 },
      counts: new Map([["raw-sdxl", 1]]),
      prompt: "   ",
      source: { id: "n1", x: null, y: null },
    });
    expect(body.anchor).toBeUndefined();
    expect(body.prompt).toBeNull();
  });
});

describe("the attempts panel's copy", () => {
  it("headers and the commit label speak in facts", () => {
    expect(attemptsHeader(3)).toBe("3 ATTEMPTS · SAME MASK");
    expect(attemptsHeader(1)).toBe("1 ATTEMPT · SAME MASK");
    expect(continueLabel([])).toBe("PICK AN ATTEMPT TO CONTINUE FROM");
    expect(continueLabel([{ engine: "raw-sdxl", label: "13a" }])).toBe(
      "CONTINUE FROM RAW-SDXL · 13a",
    );
    expect(
      continueLabel([
        { engine: "raw-sdxl", label: "13a" },
        { engine: "raw-qwen-edit", label: "13c" },
      ]),
    ).toBe("CONTINUE FROM 2 TAKES");
  });
});
