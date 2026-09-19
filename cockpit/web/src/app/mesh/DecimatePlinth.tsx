// The DECIMATE plinth (law 16): what firing DECIMATE on a
// mesh take opens.
//
// It is the To-3D plinth's shape, deliberately: same dock seam, same lane
// colour, same roster grammar, same run bar. A second verb on the same lane
// should not cost the artist a second surface to learn. What differs is what
// this verb actually has and that one doesn't — a HANDLE.
//
// The roster is headed `ENGINES FOR: <action>`. The verb picked the
// plinth, so the plinth states the action and the roster answers for
// THAT action. Which is where law 12 earns
// its keep on this surface: TRELLIS.2 and Hunyuan stay listed and dashed
// with "doesn't do decimate", because an artist who came here to shrink a
// mesh should learn that those two generate and this one simplifies — not
// find two engines missing and wonder whether the app forgot them.
//
// The handle is one number and it is a REQUEST. Everything about how it is
// drawn follows from that: the parent's own count sits above it (a target
// means nothing without the number it is measured against), the line under
// it says the mesh answers, and the child's facts line prints what was
// reached. `decimate.ts` carries the measurement behind all three.
//
// Seconds, not minutes (measured 2026-08-29: 0.8 s on a 970k-triangle mesh).
// gltfpack is a binary — no weights, no GPU — so this plinth's truth line is
// the To-3D plinth's inverted, and it still says the queue is shared.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import { Stepper } from "../composer/Composer";
import { MATRIX_UNREACHABLE, badgeLine } from "../composer/badges";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { targetLabel } from "../verbs/verbs";
import { meshFactsLine } from "./facts";
import { meshErr, meshRoster, verbLabel } from "./mesh";
import {
  DECIMATE_MAX,
  DECIMATE_MAX_TAKES,
  DECIMATE_MIN,
  DECIMATE_ONE_TAKE_LINE,
  DECIMATE_SECONDS_LINE,
  DECIMATE_TARGET_LINE,
  DECIMATE_TOUCHES_LINE,
  EXTRACT_SECONDS_LINE,
  EXTRACT_TARGET_LINE,
  EXTRACT_TEXTURE_LINE,
  EXTRACT_TOUCHES_LINE,
  FORCE_OFF_LINE,
  FORCE_ON_LINE,
  TEXTURE_DEFAULT,
  TEXTURE_SIZES,
  buildDecimateRun,
  decimateBarLine,
  decimateDefault,
  decimateGate,
  decimateRunLabel,
  noFieldsReason,
  reExtracts,
} from "./decimate";
import s from "./Mesh.module.css";

const VERB = "decimate";

export function DecimatePlinth({
  nodeId,
  workspaceId,
}: {
  nodeId: string;
  workspaceId: string;
}) {
  const closeDecimate = useUi((st) => st.closeDecimate);
  const [engineId, setEngineId] = useState<string | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  // The artist's own edit wins over the parent-derived default — otherwise
  // a late-arriving node query would overwrite a typed number.
  const [touched, setTouched] = useState(false);
  const [force, setForce] = useState(false);
  const [textureSize, setTextureSize] = useState<number>(TEXTURE_DEFAULT);
  const queryClient = useQueryClient();

  // Esc closes it and nothing is queued — the same promise every dock
  // surface makes, in the same words.
  useHotkeys("esc", closeDecimate, { enableOnFormTags: true });

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  const wsQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  });
  const enginesQ = $api.useQuery("get", "/api/engines");
  const healthQ = $api.useQuery(
    "get",
    "/api/mesh/health",
    {},
    { refetchInterval: 5000 },
  );

  const runMut = $api.useMutation(
    "post",
    "/api/workspaces/{wid}/mesh-turns",
  );

  const source = (nodesQ.data ?? []).find((n) => n.id === nodeId);
  const parentTriangles = source?.mesh?.triangles ?? null;
  // A third dashing reason, and the only one that is about THIS mesh
  // rather than the engine or the lane — re-extraction needs the voxel field
  // the parent take kept. It wins the row's one line when it applies, because
  // it is the cause the artist can act on (pick gltfpack) rather than one
  // they can only wait out.
  const roster = meshRoster(enginesQ.data, healthQ.data, VERB).map((r) => {
    const noFields = r.capable
      ? noFieldsReason(r.engine.id, r.engine.name, source)
      : null;
    return noFields === null
      ? r
      : { ...r, capable: false, reason: noFields };
  });
  const extracting = reExtracts(engineId);

  // Armed at one on the first engine that serves this verb (the
  // same opening the To-3D plinth takes).
  useEffect(() => {
    if (roster.length === 0) return;
    // The armed row can STOP being capable: the engines query
    // resolves before the node's, so an engine armed on roster order alone
    // may turn out not to serve this particular mesh. Disarm rather than
    // leave RUN pointed at a dashed row.
    if (engineId !== null) {
      const armed = roster.find((r) => r.engine.id === engineId);
      if (armed !== undefined && !armed.capable) setEngineId(null);
      return;
    }
    const first = roster.find((r) => r.capable);
    if (first !== undefined) setEngineId(first.engine.id);
  }, [roster, engineId]);

  // A tenth of the parent, once the parent's count is known. Never
  // overwrites a typed number, and stays empty when the count was never
  // recorded — there is nothing to take a tenth of (law 12).
  useEffect(() => {
    if (touched || parentTriangles === null) return;
    setTarget((prev) => prev ?? decimateDefault(parentTriangles));
  }, [parentTriangles, touched]);

  const why = decimateGate({
    roster,
    rosterReady: enginesQ.isSuccess,
    engineId,
    target,
    parentTriangles,
    health: healthQ.data,
  });

  const run = () => {
    if (source === undefined || engineId === null || target === null) return;
    runMut.mutate(
      {
        params: { path: { wid: workspaceId } },
        body: buildDecimateRun({
          source,
          engineId,
          target,
          force,
          textureSize,
          turns: wsQ.data?.turns ?? [],
        }),
      },
      {
        onSuccess: () => {
          closeDecimate();
          invalidateWorkspace(queryClient, workspaceId);
          void queryClient.invalidateQueries({
            queryKey: $api.queryOptions("get", "/api/queue").queryKey,
          });
        },
      },
    );
  };

  return (
    <section className={s.plinth} aria-label="Decimate">
      <div className={s.head}>
        <span className={`mono-label ${s.what}`}>MESH · DECIMATE</span>
        {source !== undefined && (
          <span className={s.source}>
            {/* a mesh's thumb is frame 0 of its turntable */}
            <img
              className={s.sourceThumb}
              src={`/api/nodes/${nodeId}/thumb`}
              alt=""
            />
            <span className={`mono-micro ${s.sourceName}`}>
              {targetLabel([source])}
            </span>
          </span>
        )}
        <span className="mono-micro">Born beside its parent</span>
        <span className={s.spacer} />
        <button type="button" className={s.discard} onClick={closeDecimate}>
          Esc closes — nothing is queued yet
        </button>
      </div>

      {/* The mesh being acted on, in its own numbers. The target below is
          measured against these, so they are not decoration — a handle
          asking for a triangle count with no count in sight would be a
          number typed into the dark. */}
      {source !== undefined && meshFactsLine(source) !== "" && (
        <span className={s.parentFacts}>{meshFactsLine(source)}</span>
      )}

      {/* Law 3's register, as the To-3D plinth uses it: say what this verb
          does and does not touch, rather than leaving the artist to infer it
          from which controls are absent. */}
      <p className={s.noInput}>
        {extracting ? EXTRACT_TOUCHES_LINE : DECIMATE_TOUCHES_LINE}
      </p>

      <div className={s.roster} role="group" aria-label="Decimate engines">
        <span className={`mono-label ${s.laneHead}`}>
          ENGINES FOR: {verbLabel(VERB)}
        </span>
        {enginesQ.isError && (
          <span className={s.reason}>{MATRIX_UNREACHABLE}</span>
        )}
        {enginesQ.isSuccess && roster.length === 0 && (
          <span className={s.reason}>
            The capability matrix publishes no 3D engine.
          </span>
        )}
        {roster.map((r) => {
          const on = engineId === r.engine.id ? DECIMATE_MAX_TAKES : 0;
          return (
            <div
              key={r.engine.id}
              className={s.engineRow}
              data-engine={r.engine.id}
              data-dim={!r.capable}
              data-on={on > 0}
            >
              <span className={s.engineName}>{r.engine.name}</span>
              <Stepper
                label={`${r.engine.name} takes`}
                value={on}
                min={0}
                max={DECIMATE_MAX_TAKES}
                disabled={!r.capable}
                onChange={(v) => setEngineId(v > 0 ? r.engine.id : null)}
              />
              {r.reason !== null && <span className={s.reason}>{r.reason}</span>}
              {r.badges.length > 0 && (
                <span className={s.badges}>{badgeLine(r.badges)}</span>
              )}
            </div>
          );
        })}
      </div>

      {/* ── the handle ──────────────────────────────────────────────── */}
      <div className={s.handle}>
        <div className={s.handleRow}>
          <label className={`mono-label ${s.handleLabel}`} htmlFor="dec-target">
            TARGET TRIANGLES
          </label>
          <input
            id="dec-target"
            className={s.handleInput}
            type="number"
            min={DECIMATE_MIN}
            max={DECIMATE_MAX}
            step={100}
            value={target ?? ""}
            onChange={(e) => {
              setTouched(true);
              const v = Number(e.target.value);
              setTarget(
                e.target.value === "" || !Number.isFinite(v)
                  ? null
                  : Math.floor(v),
              );
            }}
          />
          {parentTriangles !== null && (
            <span className={s.handleUnit}>
              OF {parentTriangles.toLocaleString("en-US")}
            </span>
          )}
        </div>
        <p className={s.handleLine}>
          {extracting ? EXTRACT_TARGET_LINE : DECIMATE_TARGET_LINE}
        </p>
      </div>

      {/* ── the engine's own second handle ──────────────────────────────
          Each engine gets exactly the one it has: gltfpack can be told to
          disregard the seams, re-extraction can be told what to bake the
          texture at. Neither is drawn for the other — a control the runner
          would refuse by name has no business being offered. */}
      {!extracting && (
        <div className={s.force} data-on={force}>
          <div className={s.forceRow}>
            <input
              id="dec-force"
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
            />
            <label className={`mono-label ${s.forceLabel}`} htmlFor="dec-force">
              FORCE THE EXACT COUNT
            </label>
          </div>
          <p className={s.forceLine}>
            {force ? FORCE_ON_LINE : FORCE_OFF_LINE}
          </p>
        </div>
      )}
      {extracting && (
        <div className={s.force} data-on={textureSize !== TEXTURE_DEFAULT}>
          <div className={s.forceRow}>
            <label className={`mono-label ${s.forceLabel}`} htmlFor="dec-tex">
              TEXTURE SIZE
            </label>
            <select
              id="dec-tex"
              className={s.handleInput}
              value={textureSize}
              onChange={(e) => setTextureSize(Number(e.target.value))}
            >
              {TEXTURE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <p className={s.forceLine}>{EXTRACT_TEXTURE_LINE}</p>
        </div>
      )}

      <div className={s.runBar}>
        <span className={s.runCounts}>{decimateBarLine(engineId)}</span>
        <span className={s.runTruth}>
          {extracting ? EXTRACT_SECONDS_LINE : DECIMATE_SECONDS_LINE}
        </span>
        <span className={s.runWhy}>{DECIMATE_ONE_TAKE_LINE}</span>
        {why !== null && <span className={s.runWhy}>{why}</span>}
        {runMut.isError && (
          <span className={s.runErr}>{meshErr(runMut.error)}</span>
        )}
        <button
          type="button"
          className={s.runBtn}
          disabled={why !== null || source === undefined || runMut.isPending}
          onClick={run}
        >
          {runMut.isPending ? "Starting…" : decimateRunLabel(engineId)}
        </button>
      </div>
      {/* Where it goes — the fact neither the bar nor the button face
          carries (the To-3D plinth's foot, for this verb's child). */}
      {engineId !== null && (
        <span className={`mono-micro ${s.foot}`}>
          THE MESH LANDS ON THE CANVAS BESIDE ITS PARENT, AS ITS CHILD — THE
          PARENT STAYS
        </span>
      )}
    </section>
  );
}
