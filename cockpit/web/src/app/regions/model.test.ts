// The region surface's pure core. The first block is the one that
// matters most: it pins the `nd3-v1` precedence order against a
// regression that omits DELETE (the contract order
// KEEP > DELETE > REPLACE > MODIFY is binding).
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineCapability, Engines } from "../../types";
import { MATRIX_UNREACHABLE } from "../composer/badges";
import {
  ACTION_COLORS,
  ACTIONS,
  actionLine,
  applyInk,
  exportMaskB64,
  fitScale,
  hasInk,
  layerOrder,
  maskCapabilityLine,
  nextName,
  PRECEDENCE_LINE,
  promptlessLine,
  rasterize,
  regionLabel,
  tintCanvas,
  UNCLAIMED_LINE,
  type RegionDraft,
} from "./model";

function draft(patch: Partial<RegionDraft>): RegionDraft {
  return {
    key: "k",
    name: "region 1",
    action: "modify",
    prompt: "",
    mask: null,
    inkRev: 0,
    inked: false,
    visible: true,
    ...patch,
  };
}

describe("the precedence rule in words (nd3-v1)", () => {
  it("prints KEEP > DELETE > REPLACE > MODIFY — the binding order", () => {
    expect(PRECEDENCE_LINE).toContain(
      "KEEP BEATS DELETE BEATS REPLACE BEATS MODIFY",
    );
    expect(PRECEDENCE_LINE).toContain("ND3-V1");
    // Guard against a regression to KEEP BEATS MODIFY BEATS REPLACE (a
    // wrong order that also omits DELETE): DELETE must sit between KEEP
    // and REPLACE.
    expect(PRECEDENCE_LINE.indexOf("KEEP")).toBeLessThan(
      PRECEDENCE_LINE.indexOf("DELETE"),
    );
    expect(PRECEDENCE_LINE.indexOf("DELETE")).toBeLessThan(
      PRECEDENCE_LINE.indexOf("REPLACE"),
    );
    expect(PRECEDENCE_LINE.indexOf("REPLACE")).toBeLessThan(
      PRECEDENCE_LINE.indexOf("MODIFY"),
    );
  });

  it("states the unclaimed-pixels rule: regenerate only when a KEEP exists", () => {
    expect(UNCLAIMED_LINE).toContain("ONLY WHEN A KEEP EXISTS");
  });
});

describe("actions and their plain words", () => {
  it("offers all four actions, each with an effect line", () => {
    expect(ACTIONS).toEqual(["keep", "modify", "replace", "delete"]);
    for (const a of ACTIONS) {
      expect(actionLine(a)).toContain(a.toUpperCase());
      expect(ACTION_COLORS[a]).toMatch(/^#/);
    }
  });

  it("KEEP and DELETE say why they carry no prompt", () => {
    expect(promptlessLine("keep")).toContain("LOCKED · NO PROMPT");
    expect(promptlessLine("delete")).toContain("NO PROMPT");
  });
});

describe("draft chrome", () => {
  it("labels rows R1, R2… by position and names drafts in sequence", () => {
    expect(regionLabel(0)).toBe("R1");
    expect(regionLabel(2)).toBe("R3");
    expect(nextName([])).toBe("region 1");
    expect(nextName([draft({}), draft({})])).toBe("region 3");
  });

  it("hasInk is any committed draw stroke (the list row's INK mark)", () => {
    expect(hasInk(draft({}))).toBe(false);
    expect(hasInk(draft({ inked: true }))).toBe(true);
  });

  it("fitScale fits within the viewport and never upscales", () => {
    expect(fitScale({ w: 2048, h: 1024 }, { w: 1024, h: 1024 })).toBe(0.5);
    expect(fitScale({ w: 100, h: 100 }, { w: 1000, h: 1000 })).toBe(1);
  });
});

// A recording stand-in for the 2D context — jsdom has none, and the raster
// rules (erase = destination-out, export flattens onto black, tint =
// source-in) are exactly what these tests pin.
function fakeCtx() {
  let gco = "source-over";
  const calls: string[] = [];
  const ctx = {
    calls,
    fillStyle: "",
    strokeStyle: "",
    lineCap: "",
    lineJoin: "",
    lineWidth: 0,
    globalAlpha: 1,
    get globalCompositeOperation() {
      return gco;
    },
    set globalCompositeOperation(v: string) {
      gco = v;
    },
    fillRect: () => calls.push(`fillRect@${gco}`),
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    closePath: () => {},
    fill: () => calls.push(`fill@${gco}`),
    stroke: () => calls.push(`stroke@${gco}`),
    drawImage: () => calls.push(`drawImage@${gco}`),
  };
  return ctx;
}

describe("the one-bitmap rasteriser", () => {
  afterEach(() => vi.restoreAllMocks());

  it("a draw stroke paints source-over and restores the op", () => {
    const ctx = fakeCtx();
    applyInk(ctx as unknown as CanvasRenderingContext2D, {
      tool: "brush",
      points: [1, 1, 4, 4],
      size: 8,
    });
    expect(ctx.calls).toEqual(["stroke@source-over"]);
    expect(ctx.globalCompositeOperation).toBe("source-over");
  });

  it("an erase punches alpha out — destination-out, never an appended layer", () => {
    const ctx = fakeCtx();
    applyInk(ctx as unknown as CanvasRenderingContext2D, {
      tool: "erase",
      points: [1, 1, 4, 4],
      size: 8,
    });
    expect(ctx.calls).toEqual(["stroke@destination-out"]);
    expect(ctx.globalCompositeOperation).toBe("source-over");
  });

  it("a lasso under three points encloses nothing", () => {
    const ctx = fakeCtx();
    applyInk(ctx as unknown as CanvasRenderingContext2D, {
      tool: "lasso",
      points: [1, 1, 2, 2],
    });
    expect(ctx.calls).toEqual([]);
  });

  it("rasterize creates the bitmap at natural size once and then reuses it", () => {
    const ctx = fakeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const rect = { tool: "rect", x: 0, y: 0, w: 4, h: 4 } as const;
    const c = rasterize(null, rect, { w: 320, h: 160 });
    expect(c.width).toBe(320);
    expect(c.height).toBe(160);
    const again = rasterize(c, rect, { w: 999, h: 999 });
    expect(again).toBe(c);
    expect(c.width).toBe(320); // natural size is fixed at first ink
    expect(ctx.calls).toEqual([
      "fillRect@source-over",
      "fillRect@source-over",
    ]);
  });

  it("exportMaskB64 flattens onto black and hands back bare base64", () => {
    const ctx = fakeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/png;base64,QUJD",
    );
    const mask = document.createElement("canvas");
    const b64 = exportMaskB64(draft({ mask, inked: true }), { w: 64, h: 64 });
    expect(b64).toBe("QUJD");
    // black ground first, then the draft's white-on-transparent bitmap
    expect(ctx.calls).toEqual([
      "fillRect@source-over",
      "drawImage@source-over",
    ]);
    expect(ctx.fillStyle).toBe("#000");
  });

  it("tintCanvas recolours in one source-in composite", () => {
    const ctx = fakeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const mask = document.createElement("canvas");
    mask.width = 10;
    mask.height = 10;
    const tinted = tintCanvas(mask, "#7FB2FF");
    expect(tinted.width).toBe(10);
    expect(ctx.calls).toEqual([
      "drawImage@source-over",
      "fillRect@source-in",
    ]);
    expect(ctx.fillStyle).toBe("#7FB2FF");
  });
});

describe("layerOrder — the tinted preview paints weakest to strongest", () => {
  it("stacks modify < replace < delete < keep, later-created on top within a tier", () => {
    const drafts = [
      draft({ key: "keep1", action: "keep" }),
      draft({ key: "mod1", action: "modify" }),
      draft({ key: "del1", action: "delete" }),
      draft({ key: "mod2", action: "modify" }),
      draft({ key: "rep1", action: "replace" }),
    ];
    expect(layerOrder(drafts).map((d) => d.key)).toEqual([
      "mod1",
      "mod2",
      "rep1",
      "del1",
      "keep1",
    ]);
  });
});

describe("maskCapabilityLine — the matrix readout (law 12)", () => {
  const cap = (value: unknown, state: EngineCapability["state"]): EngineCapability => ({
    note: "",
    state,
    value,
  });
  const engines = (rows: Engines["engines"]): Engines => ({
    engines: rows,
    external: {
      catalogue: "openrouter",
      defaults: {},
      lane: "external",
      payload: "image",
      price: "per response",
    },
    updated: "2026-08-26",
  });
  const row = (
    id: string,
    name: string,
    mask: EngineCapability,
    over: Partial<Engines["engines"][number]> = {},
  ): Engines["engines"][number] => ({
    id,
    name,
    lane: "local",
    payload: "image",
    verbs: [],
    params: {},
    price: "free",
    capabilities: { mask },
    ...over,
  });

  it("counts image locals and names the capable, verified plain", () => {
    const line = maskCapabilityLine(
      engines([
        row("sdxl", "SDXL Juggernaut XI", cap(true, "verified")),
        row("klein", "FLUX.2 klein 4B", cap(false, "verified")),
        row("zt", "Z-Image Turbo", cap(false, "verified")),
        row("mesh", "TRELLIS.2", cap(false, "verified"), { payload: "3d" }),
      ]),
      false,
    );
    expect(line).toBe(
      "1 OF YOUR 3 LOCAL IMAGE ENGINES CAN MASK · SDXL JUGGERNAUT XI",
    );
  });

  it("an unverified capable engine wears the word (law 12)", () => {
    const line = maskCapabilityLine(
      engines([
        row("sdxl", "SDXL Juggernaut XI", cap(true, "verified")),
        row("qwen", "Qwen Edit 2511", cap(true, "unverified")),
      ]),
      false,
    );
    expect(line).toBe(
      "2 OF YOUR 2 LOCAL IMAGE ENGINES CAN MASK · SDXL JUGGERNAUT XI · " +
        "QWEN EDIT 2511 UNVERIFIED",
    );
  });

  it("says so when none can, in words — never an empty claim", () => {
    const line = maskCapabilityLine(
      engines([row("klein", "FLUX.2 klein 4B", cap(false, "verified"))]),
      false,
    );
    expect(line).toBe("NONE OF YOUR 1 LOCAL IMAGE ENGINES CAN MASK YET");
  });

  it("unreachable matrix is the one shared line; not-arrived prints nothing", () => {
    expect(maskCapabilityLine(undefined, true)).toBe(MATRIX_UNREACHABLE);
    expect(maskCapabilityLine(undefined, false)).toBeNull();
  });
});
