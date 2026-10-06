// Legacy region controls, session drafts, submission and attempt comparison.
// MaskCanvas owns the shared Konva drawing surface; this parent rasterizes
// committed ink through the existing store and keeps the run/selection behavior.
// Closing preserves drafts. Keyboard actions remain on the legacy surface.
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useQueryClient } from "@tanstack/react-query";
import { $api } from "../../api/client";
import type { Passes, SlotStatus } from "../../types";
import {
  deriveCells,
  elapsedLabel,
  passSuffix,
  type CellVM,
} from "../batch/grid";
import { Stepper } from "../composer/Composer";
import { errText } from "../composer/compose";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { factsLine, targetLabel } from "../verbs/verbs";
import {
  ATTEMPTS_HINT,
  attemptsHeader,
  buildRegionRun,
  continueLabel,
  EXTERNALS_LINE,
  fanBarLine,
  fanGate,
  IRREVERSIBLE_LINE,
  regionRoster,
  STAY_LINE,
  type PaneView,
} from "./fanout";
import {
  ACTION_COLORS,
  ACTIONS,
  actionLine,
  DRAFTS_LINE,
  DRAW_HINT,
  hasInk,
  maskCapabilityLine,
  promptlessLine,
  regionLabel,
  UNCLAIMED_LINE,
  PRECEDENCE_LINE,
  SAME_REGIONS_LINE,
  type DrawTool,
  type RegionAction,
} from "./model";
import { useRegions } from "./store";
import { MaskCanvas, useLoadedImage } from "./MaskCanvas";
import s from "./Regions.module.css";

const TOOLS: readonly { id: DrawTool; glyph: string; word: string }[] = [
  { id: "brush", glyph: "✎", word: "BRUSH" },
  { id: "rect", glyph: "▭", word: "RECT" },
  { id: "lasso", glyph: "◠", word: "LASSO" },
  { id: "erase", glyph: "⌫", word: "ERASE" },
];

// A stable snapshot for a node with no drafts yet — `?? []` in a selector
// would make a fresh array per store read and loop the render.
const NO_DRAFTS: readonly never[] = [];

/** Landed pane pixels under the picked view mode. Plain is the take
 *  itself; DIFF composites the attempt against the source (difference,
 *  amplified); REGION-ONLY clips the attempt to the union of the drawn
 *  regions over the dimmed source. All client-side, facts not judgment
 *  (no pane carries a caption that judges its attempt). */
function PaneMedia({
  nodeId,
  view,
  source,
  union,
}: {
  nodeId: string;
  view: PaneView;
  source: HTMLImageElement | "error" | null;
  union: HTMLCanvasElement | null;
}) {
  const url = `/api/nodes/${nodeId}/image`;
  const attempt = useLoadedImage(view === "plain" ? null : url);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (view === "plain") return;
    const c = canvasRef.current;
    if (
      c === null ||
      attempt === null ||
      attempt === "error" ||
      source === null ||
      source === "error"
    )
      return;
    const w = source.naturalWidth;
    const h = source.naturalHeight;
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    if (ctx === null) return;
    if (view === "diff") {
      ctx.drawImage(source, 0, 0, w, h);
      ctx.globalCompositeOperation = "difference";
      ctx.drawImage(attempt, 0, 0, w, h);
      // amplify: the difference is usually near-black — two self-adds
      ctx.globalCompositeOperation = "lighter";
      ctx.drawImage(c, 0, 0);
      ctx.drawImage(c, 0, 0);
    } else {
      ctx.globalAlpha = 0.25;
      ctx.drawImage(source, 0, 0, w, h);
      ctx.globalAlpha = 1;
      if (union !== null) {
        const clip = document.createElement("canvas");
        clip.width = w;
        clip.height = h;
        const cctx = clip.getContext("2d");
        if (cctx !== null) {
          cctx.drawImage(attempt, 0, 0, w, h);
          cctx.globalCompositeOperation = "destination-in";
          cctx.drawImage(union, 0, 0, w, h);
          ctx.drawImage(clip, 0, 0);
        }
      }
    }
    ctx.globalCompositeOperation = "source-over";
  }, [view, attempt, source, union]);

  if (view === "plain") return <img className={s.paneImg} src={url} alt="" />;
  return <canvas className={s.paneImg} ref={canvasRef} />;
}

export function RegionSurface({
  workspaceId,
  nodeId,
}: {
  workspaceId: string;
  nodeId: string;
}) {
  const queryClient = useQueryClient();
  const closeRegions = useUi((st) => st.closeRegions);
  const wantSelect = useUi((st) => st.wantSelect);
  const localOnly = useUi((st) => st.localOnly);
  const lightboxUp = useUi((st) => st.lightboxNodeId !== null);
  const summonUp = useUi((st) => st.summonAt !== null);

  const drafts = useRegions((st) => st.drafts[nodeId] ?? NO_DRAFTS);
  const activeKey = useRegions((st) => st.activeKey[nodeId] ?? null);
  const fanTurnId = useRegions((st) => st.fanTurn[nodeId] ?? null);
  const tool = useRegions((st) => st.tool);
  const brushSize = useRegions((st) => st.brushSize);
  const setTool = useRegions((st) => st.setTool);
  const setBrushSize = useRegions((st) => st.setBrushSize);
  const addDraft = useRegions((st) => st.addDraft);
  const removeDraft = useRegions((st) => st.removeDraft);
  const patchDraft = useRegions((st) => st.patchDraft);
  const commitInk = useRegions((st) => st.commitInk);
  const setActive = useRegions((st) => st.setActive);

  // The overlay owns Esc while up — the lightbox/palette first, per the
  // document-hotkey stack; closing keeps the drafts (and the panes:
  // fanTurn is session state beside them).
  useHotkeys("esc", closeRegions, {
    enabled: !lightboxUp && !summonUp,
    enableOnFormTags: true,
  });

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  const node = (nodesQ.data ?? []).find((n) => n.id === nodeId);
  const enginesQ = $api.useQuery("get", "/api/engines");
  const capability = maskCapabilityLine(enginesQ.data, enginesQ.isError);
  const presetsQ = $api.useQuery("get", "/api/presets");
  const detailQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  });
  const queueQ = $api.useQuery("get", "/api/queue");

  const image = useLoadedImage(`/api/nodes/${nodeId}/image`);
  const natural = useMemo(
    () =>
      image === null || image === "error"
        ? null
        : { w: image.naturalWidth, h: image.naturalHeight },
    [image],
  );

  const active = drafts.find((d) => d.key === activeKey) ?? null;

  // --- the fan-out roster -------------------------------------------------
  const roster = useMemo(
    () => regionRoster(presetsQ.data ?? [], enginesQ.data),
    [presetsQ.data, enginesQ.data],
  );
  const [counts, setCounts] = useState<ReadonlyMap<string, number>>(new Map());
  // Every open starts at one take on one engine: the source's own engine
  // when it can mask, else the first that can.
  const seeded = useRef(false);
  useEffect(() => {
    seeded.current = false;
  }, [nodeId]);
  useEffect(() => {
    if (seeded.current || roster.length === 0 || node === undefined) return;
    seeded.current = true;
    const capable = roster.filter((r) => r.capable);
    const own = capable.find((r) => r.preset.id === node.engine);
    const first = own ?? capable[0];
    setCounts(new Map(first !== undefined ? [[first.preset.id, 1]] : []));
  }, [roster, node]);

  const [prompt, setPrompt] = useState<string | null>(null);
  const promptValue = prompt ?? node?.prompt ?? "";

  const runMut = $api.useMutation(
    "post",
    "/api/workspaces/{wid}/region-turns",
  );
  const rerollMut = $api.useMutation("post", "/api/slots/{slot_id}/reroll", {
    onSettled: () => invalidateWorkspace(queryClient, workspaceId),
  });

  const gateWhy = fanGate({
    drafts,
    counts,
    rosterReady: presetsQ.isSuccess,
    imageReady: natural !== null,
  });

  const fanOut = () => {
    if (gateWhy !== null || natural === null || node === undefined) return;
    useRegions.getState().setPendingFan(nodeId);
    runMut.mutate(
      {
        params: { path: { wid: workspaceId } },
        body: buildRegionRun({
          drafts,
          natural,
          counts,
          prompt: promptValue,
          source: { id: node.id, x: node.x, y: node.y },
        }),
      },
      {
        onSuccess: (r) => {
          const st = useRegions.getState();
          st.setFanTurn(nodeId, r.turn.id);
          st.setPendingFan(null);
          setPicked([]);
          invalidateWorkspace(queryClient, workspaceId);
        },
        onError: () => useRegions.getState().setPendingFan(null),
      },
    );
  };

  // --- the attempt panes (results side by side) -----------------------
  const fanTurn = (detailQ.data?.turns ?? []).find((t) => t.id === fanTurnId);
  const passes = useMemo(() => {
    const m = new Map<string, Passes>();
    for (const v of queueQ.data?.lanes.local.slots ?? [])
      if (v.passes != null) m.set(v.id, v.passes);
    return m;
  }, [queueQ.data]);
  const cells = useMemo(
    () =>
      fanTurn === undefined
        ? []
        : deriveCells(
            fanTurn,
            nodesQ.data ?? [],
            new Map(),
            new Map(),
            new Map(),
            false,
            passes,
          ),
    [fanTurn, nodesQ.data, passes],
  );
  const elapsedBySlot = useMemo(() => {
    const m = new Map<string, number>();
    for (const v of queueQ.data?.lanes.local.slots ?? [])
      if (v.elapsed_ms !== null) m.set(v.id, v.elapsed_ms);
    return m;
  }, [queueQ.data]);

  const [view, setView] = useState<PaneView>("plain");
  const [picked, setPicked] = useState<string[]>([]);
  const panesUp = fanTurnId !== null;
  useHotkeys("d", () => setView((v) => (v === "diff" ? "plain" : "diff")), {
    enabled: panesUp && !lightboxUp && !summonUp,
  });
  useHotkeys("r", () => setView((v) => (v === "region" ? "plain" : "region")), {
    enabled: panesUp && !lightboxUp && !summonUp,
  });

  // The union of every inked draft: REGION-ONLY's clip, composed from the
  // drafts' own bitmaps.
  const inkRevs = drafts.map((d) => d.inkRev).join(",");
  const union = useMemo(() => {
    if (natural === null) return null;
    const c = document.createElement("canvas");
    c.width = natural.w;
    c.height = natural.h;
    const ctx = c.getContext("2d");
    if (ctx !== null)
      for (const d of drafts) if (d.mask !== null) ctx.drawImage(d.mask, 0, 0);
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- inkRevs versions the masks
  }, [drafts, inkRevs, natural]);

  const pick = (cell: CellVM, additive: boolean) => {
    if (cell.nodeId === null) return;
    const id = cell.nodeId;
    setPicked((prev) =>
      additive
        ? prev.includes(id)
          ? prev.filter((x) => x !== id)
          : [...prev, id]
        : [id],
    );
  };

  const pickedCells = picked
    .map((id) => cells.find((c) => c.nodeId === id))
    .filter((c): c is CellVM => c !== undefined);

  const paneFace = (cell: CellVM): string => {
    const st: SlotStatus = cell.status;
    if (st === "running") {
      const el = elapsedBySlot.get(cell.slotId);
      return `RUNNING${el !== undefined ? ` · ${elapsedLabel(el)}` : ""}${passSuffix(cell.passes)}`;
    }
    if (st === "queued") return "QUEUED";
    if (st === "landed")
      return `✓ ${cell.durationMs !== null ? elapsedLabel(cell.durationMs) : "LANDED"}${
        cell.seed !== null ? ` · S ${cell.seed}` : ""
      }`;
    if (st === "held") return "HELD — the grid has the resume (G)";
    if (st === "set_aside") return "SET ASIDE — in the tray";
    return st.toUpperCase();
  };

  return (
    <section className={s.surface} aria-label="Edit region">
      <div className={s.head}>
        <span className={`mono-label ${s.title}`}>EDIT REGION</span>
        {node !== undefined && (
          <>
            <span className={s.takeName}>on {targetLabel([node])}</span>
            <span className={`mono-micro ${s.takeFacts}`}>
              {factsLine([node])}
            </span>
          </>
        )}
        <span className={s.spacer} />
        <span className={`mono-micro ${s.escHint}`}>
          ESC CLOSES · REGIONS STAY WITH THE TAKE
        </span>
        <button
          type="button"
          className={s.headBtn}
          onClick={closeRegions}
          aria-label="Close the region surface"
        >
          ✕
        </button>
      </div>

      <div className={s.capRow}>
        {capability !== null && (
          <span className={`mono-micro ${s.capLine}`}>{capability}</span>
        )}
        <span className={`mono-micro ${s.gapLine}`}>{DRAFTS_LINE}</span>
      </div>

      <div className={s.body}>
        <div className={s.stageCol}>
          <div className={s.tools} role="toolbar" aria-label="Drawing tools">
            {TOOLS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={tool === t.id ? `${s.tool} ${s.toolOn}` : s.tool}
                aria-pressed={tool === t.id}
                onClick={() => setTool(t.id)}
              >
                <span aria-hidden="true">{t.glyph}</span>
                <span className={`mono-micro ${s.toolWord}`}>{t.word}</span>
              </button>
            ))}
            <label className={`mono-micro ${s.brush}`}>
              BRUSH {brushSize} PX
              <input
                type="range"
                min={6}
                max={160}
                value={brushSize}
                onChange={(e) => setBrushSize(Number(e.target.value))}
              />
            </label>
            {active !== null && (
              <span className={`mono-micro ${s.inkingInto}`}>
                INKING {regionLabel(drafts.indexOf(active))} ·{" "}
                {active.action.toUpperCase()}
              </span>
            )}
          </div>

          <MaskCanvas
            sourceUrl={`/api/nodes/${nodeId}/image`}
            drafts={drafts}
            activeKey={activeKey}
            tool={tool}
            brushSize={brushSize}
            onCreateRegion={() => addDraft(nodeId)}
            onInk={(key, ink, size) => commitInk(nodeId, key, ink, size)}
            loadingMessage={nodesQ.isError || (nodesQ.data !== undefined && node === undefined)
              ? "This take isn't in the record any more." : "LOADING THE TAKE"}
            errorMessage="The take's pixels can't be read — the file is missing or unreadable. The record row still stands."
          />
        </div>

        <aside className={s.side} aria-label="Regions">
          <div className={s.sideHead}>
            <span className="mono-label">REGIONS</span>
            <span className={`mono-micro ${s.sideCount}`}>
              {drafts.length === 0 ? "NONE YET" : `${drafts.length}`}
            </span>
          </div>

          {drafts.length === 0 && (
            <p className={`effect-line ${s.drawHint}`}>{DRAW_HINT}</p>
          )}

          <div className={s.rows}>
            {drafts.map((d, i) => {
              const isActive = d.key === activeKey;
              return (
                <div
                  key={d.key}
                  className={isActive ? `${s.row} ${s.rowActive}` : s.row}
                  onClick={() => setActive(nodeId, d.key)}
                >
                  <div className={s.rowHead}>
                    <span
                      className={s.dot}
                      style={{ background: ACTION_COLORS[d.action] }}
                      aria-hidden="true"
                    />
                    <span className={`mono-label ${s.rowLabel}`}>
                      {regionLabel(i)} · {d.action.toUpperCase()}
                    </span>
                    <span className={`mono-micro ${s.inkMark}`}>
                      {hasInk(d) ? "INK" : "NO INK YET"}
                    </span>
                    <button
                      type="button"
                      className={s.rowBtn}
                      aria-pressed={!d.visible}
                      onClick={(e) => {
                        e.stopPropagation();
                        patchDraft(nodeId, d.key, { visible: !d.visible });
                      }}
                    >
                      {d.visible ? "HIDE" : "SHOW"}
                    </button>
                    <button
                      type="button"
                      className={s.rowBtn}
                      aria-label={`Remove ${regionLabel(i)}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        removeDraft(nodeId, d.key);
                      }}
                    >
                      ✕
                    </button>
                  </div>
                  <input
                    className={s.name}
                    value={d.name}
                    aria-label={`${regionLabel(i)} name`}
                    onChange={(e) =>
                      patchDraft(nodeId, d.key, { name: e.target.value })
                    }
                  />
                  <select
                    className={s.action}
                    value={d.action}
                    aria-label={`${regionLabel(i)} action`}
                    onChange={(e) =>
                      patchDraft(nodeId, d.key, {
                        action: e.target.value as RegionAction,
                      })
                    }
                  >
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>
                        {actionLine(a)}
                      </option>
                    ))}
                  </select>
                  {d.action === "modify" || d.action === "replace" ? (
                    <textarea
                      className={s.prompt}
                      rows={2}
                      placeholder="region prompt — empty falls back to the turn prompt"
                      value={d.prompt}
                      aria-label={`${regionLabel(i)} prompt`}
                      onChange={(e) =>
                        patchDraft(nodeId, d.key, { prompt: e.target.value })
                      }
                    />
                  ) : (
                    <span className={`mono-micro ${s.promptless}`}>
                      {promptlessLine(d.action)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          <button
            type="button"
            className={s.add}
            onClick={() => addDraft(nodeId)}
          >
            + NEW REGION
          </button>

          <div className={s.fan} aria-label="Fan out">
            <div className={s.sideHead}>
              <span className="mono-label">FAN OUT</span>
            </div>
            <div className={s.fanRows}>
              {roster.map((r) => (
                <div
                  key={r.preset.id}
                  className={s.fanRow}
                  data-capable={r.capable}
                >
                  <span className={`mono-micro ${s.fanName}`}>
                    {r.preset.display_name.toUpperCase()}
                    {r.stateWord !== null && (
                      <span className={s.fanState}> {r.stateWord}</span>
                    )}
                  </span>
                  <Stepper
                    label={`${r.preset.display_name} takes`}
                    value={counts.get(r.preset.id) ?? 0}
                    min={0}
                    disabled={!r.capable}
                    onChange={(n) =>
                      setCounts((prev) => {
                        const next = new Map(prev);
                        next.set(r.preset.id, n);
                        return next;
                      })
                    }
                  />
                  {r.reason !== null && (
                    <span className={`mono-micro ${s.fanReason}`}>
                      {r.reason}
                    </span>
                  )}
                </div>
              ))}
              {presetsQ.isError && (
                <span className={`mono-micro ${s.fanReason}`}>
                  The engine roster can't be reached.
                </span>
              )}
            </div>
            <p className={`mono-micro ${s.fanExternals}`}>{EXTERNALS_LINE}</p>
            <textarea
              className={s.prompt}
              rows={2}
              placeholder="turn prompt — drives the unmasked rest under a KEEP, and any region without its own"
              value={promptValue}
              aria-label="Turn prompt"
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className={s.fanBar}>
              <span className={`mono-micro ${s.fanBarLine}`}>
                {fanBarLine(counts)}
              </span>
              <button
                type="button"
                className={s.fanBtn}
                disabled={gateWhy !== null || runMut.isPending}
                title={gateWhy ?? undefined}
                onClick={fanOut}
              >
                {runMut.isPending ? "FANNING OUT…" : "FAN OUT"}
              </button>
            </div>
            {gateWhy !== null && (
              <span className={`effect-line ${s.fanWhy}`}>{gateWhy}</span>
            )}
            {runMut.isError && (
              <span className={`effect-line ${s.fanErr}`}>
                {errText(runMut.error)}
              </span>
            )}
          </div>
        </aside>

        {panesUp && (
          <aside className={s.attempts} aria-label="Attempts">
            <div className={s.sideHead}>
              <span className="mono-label">
                {attemptsHeader(cells.length)}
              </span>
            </div>
            <p className={`mono-micro ${s.attemptsHint}`}>{ATTEMPTS_HINT}</p>
            <div className={s.viewChips}>
              {(
                [
                  ["diff", "D DIFF"],
                  ["region", "R REGION-ONLY"],
                ] as const
              ).map(([v, word]) => (
                <button
                  key={v}
                  type="button"
                  className={s.viewChip}
                  aria-pressed={view === v}
                  onClick={() => setView((cur) => (cur === v ? "plain" : v))}
                >
                  {word}
                </button>
              ))}
            </div>
            <div className={s.panes}>
              {fanTurn === undefined && (
                <p className={`mono-label ${s.loading}`}>
                  STARTING THE FAN OUT
                </p>
              )}
              {cells.map((cell) => {
                const isPicked =
                  cell.nodeId !== null && picked.includes(cell.nodeId);
                return (
                  <div
                    key={cell.slotId}
                    className={isPicked ? `${s.pane} ${s.panePicked}` : s.pane}
                    data-status={cell.status}
                    onClick={(e) =>
                      cell.status === "landed" && pick(cell, e.shiftKey)
                    }
                  >
                    <div className={s.paneHead}>
                      <span className={`mono-label ${s.paneEngine}`}>
                        {cell.engine.toUpperCase()}
                      </span>
                      <span className={`mono-micro ${s.paneFacts}`}>
                        {paneFace(cell)}
                      </span>
                    </div>
                    {cell.status === "landed" && cell.nodeId !== null ? (
                      <PaneMedia
                        nodeId={cell.nodeId}
                        view={view}
                        source={image}
                        union={union}
                      />
                    ) : cell.status === "failed" ? (
                      <div className={s.paneFail}>
                        <span className={`effect-line ${s.paneCause}`}>
                          {cell.cause ??
                            "failed — the record has the details"}
                        </span>
                        <button
                          type="button"
                          className={s.paneBtn}
                          disabled={rerollMut.isPending}
                          onClick={(e) => {
                            e.stopPropagation();
                            rerollMut.mutate({
                              params: { path: { slot_id: cell.slotId } },
                              body: { local_only: localOnly, keep_seed: false },
                            });
                          }}
                        >
                          REROLL · NEW SEED
                        </button>
                      </div>
                    ) : (
                      <div className={s.panePlate} aria-hidden="true" />
                    )}
                  </div>
                );
              })}
            </div>
            <div className={s.commit}>
              <span className={`mono-micro ${s.commitLabel}`}>
                {continueLabel(
                  pickedCells.map((c) => ({
                    engine: c.engine,
                    label: c.label,
                  })),
                )}
              </span>
              <div className={s.commitBtns}>
                <button
                  type="button"
                  className={s.commitKeep}
                  onClick={closeRegions}
                >
                  KEEP ALL
                </button>
                <button
                  type="button"
                  className={s.commitGo}
                  disabled={picked.length === 0}
                  onClick={() => {
                    wantSelect(picked);
                    closeRegions();
                  }}
                >
                  CONTINUE HERE
                </button>
              </div>
              <span className={`mono-micro ${s.commitLine}`}>{STAY_LINE}</span>
              <span className={`mono-micro ${s.commitLine}`}>
                {IRREVERSIBLE_LINE}
              </span>
            </div>
          </aside>
        )}
      </div>

      <div className={s.foot}>
        <span className={`mono-micro ${s.rule}`}>{PRECEDENCE_LINE}</span>
        <span className={`mono-micro ${s.rule}`}>{UNCLAIMED_LINE}</span>
        <span className={`mono-micro ${s.ruleQuiet}`}>{SAME_REGIONS_LINE}</span>
      </div>
    </section>
  );
}
