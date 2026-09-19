// The TEXTURE plinth (law 16): what firing TEXTURE on a
// mesh take opens.
//
// It is the DECIMATE plinth's shape, deliberately: same dock seam, same lane
// colour, same roster grammar, same run bar. What differs is what this verb
// has that no other does — a SECOND PARENT. The verb row's selection names
// the mesh (the shape); the reference image (the appearance) is picked here,
// from a strip of the workspace's own image takes, defaulting to the take
// this mesh was generated from. Both land on the child as parents, in that
// order, so the canvas draws an edge from each.
//
// The honesty this surface owes (bench 2026-08-30): Paint rebuilds the mesh
// at 40,000 faces with fresh UVs before painting — the child never carries
// its parent's topology. The touches line says so before anything is
// queued, and the landed child's facts line prints its own count.
import { useEffect, useMemo, useState } from "react";
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
  NO_REFERENCE_LINE,
  TEXTURE_MAX_TAKES,
  TEXTURE_MINUTES_LINE,
  TEXTURE_ONE_TAKE_LINE,
  TEXTURE_REFERENCE_LINE,
  TEXTURE_TOUCHES_LINE,
  buildTextureRun,
  imageAncestor,
  referenceCandidates,
  textureBarLine,
  textureGate,
  textureRunLabel,
} from "./texture";
import s from "./Mesh.module.css";

const VERB = "texture";

export function TexturePlinth({
  nodeId,
  workspaceId,
}: {
  nodeId: string;
  workspaceId: string;
}) {
  const closeTexture = useUi((st) => st.closeTexture);
  const [engineId, setEngineId] = useState<string | null>(null);
  const [referenceId, setReferenceId] = useState<string | null>(null);
  // The artist's own pick wins over the ancestry-derived default —
  // otherwise a late-arriving node query would overwrite a chosen thumb.
  const [picked, setPicked] = useState(false);
  const queryClient = useQueryClient();

  // Esc closes it and nothing is queued — the same promise every dock
  // surface makes, in the same words.
  useHotkeys("esc", closeTexture, { enableOnFormTags: true });

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

  const nodes = useMemo(() => nodesQ.data ?? [], [nodesQ.data]);
  const source = nodes.find((n) => n.id === nodeId);
  const roster = meshRoster(enginesQ.data, healthQ.data, VERB);
  const candidates = referenceCandidates(nodes);

  // Armed at one on the first engine that serves this verb (the
  // same opening the other plinths take).
  useEffect(() => {
    if (roster.length === 0) return;
    if (engineId !== null) {
      const armed = roster.find((r) => r.engine.id === engineId);
      if (armed !== undefined && !armed.capable) setEngineId(null);
      return;
    }
    const first = roster.find((r) => r.capable);
    if (first !== undefined) setEngineId(first.engine.id);
  }, [roster, engineId]);

  // The default reference: the image this mesh was generated from, walked
  // up through mesh-verb parents. Never overwrites a picked thumb, and
  // stays empty when no ancestor is offerable — the artist picks by hand.
  useEffect(() => {
    if (picked || referenceId !== null) return;
    const ancestor = imageAncestor(source, nodes);
    if (ancestor !== null) setReferenceId(ancestor);
  }, [picked, referenceId, source, nodes]);

  const why = textureGate({
    roster,
    rosterReady: enginesQ.isSuccess,
    engineId,
    referenceId,
    candidates: candidates.length,
    health: healthQ.data,
  });

  const run = () => {
    if (source === undefined || engineId === null || referenceId === null)
      return;
    runMut.mutate(
      {
        params: { path: { wid: workspaceId } },
        body: buildTextureRun({
          source,
          engineId,
          referenceId,
          turns: wsQ.data?.turns ?? [],
        }),
      },
      {
        onSuccess: () => {
          closeTexture();
          invalidateWorkspace(queryClient, workspaceId);
          void queryClient.invalidateQueries({
            queryKey: $api.queryOptions("get", "/api/queue").queryKey,
          });
        },
      },
    );
  };

  return (
    <section className={s.plinth} aria-label="Texture">
      <div className={s.head}>
        <span className={`mono-label ${s.what}`}>MESH · TEXTURE</span>
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
        <button type="button" className={s.discard} onClick={closeTexture}>
          Esc closes — nothing is queued yet
        </button>
      </div>

      {/* The mesh being repainted, in its own numbers — the count that will
          NOT survive the repaint, which is exactly why it is shown. */}
      {source !== undefined && meshFactsLine(source) !== "" && (
        <span className={s.parentFacts}>{meshFactsLine(source)}</span>
      )}

      {/* Law 3's register: what this verb does to the mesh, before anything
          is queued — the 40k rebuild is the fact an artist must not learn
          from the landed child. */}
      <p className={s.noInput}>{TEXTURE_TOUCHES_LINE}</p>

      <div className={s.roster} role="group" aria-label="Texture engines">
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
          const on = engineId === r.engine.id ? TEXTURE_MAX_TAKES : 0;
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
                max={TEXTURE_MAX_TAKES}
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

      {/* ── the second parent ───────────────────────────────────────────
          The appearance. A strip of this workspace's image takes, newest
          first, one selected — the child records the mesh AND this pick as
          parents, in that order. */}
      <div className={s.handle} role="group" aria-label="Reference image">
        <span className={`mono-label ${s.handleLabel}`}>PAINT FROM</span>
        {candidates.length === 0 ? (
          <span className={s.reason}>{NO_REFERENCE_LINE}</span>
        ) : (
          <div className={s.refStrip}>
            {candidates.map((n) => (
              <button
                key={n.id}
                type="button"
                className={s.refThumb}
                data-on={referenceId === n.id}
                aria-pressed={referenceId === n.id}
                onClick={() => {
                  setPicked(true);
                  setReferenceId(n.id);
                }}
              >
                <img src={`/api/nodes/${n.id}/thumb`} alt="" />
                <span className={`mono-micro ${s.refThumbName}`}>
                  {targetLabel([n])}
                </span>
              </button>
            ))}
          </div>
        )}
        <p className={s.handleLine}>{TEXTURE_REFERENCE_LINE}</p>
      </div>

      <div className={s.runBar}>
        <span className={s.runCounts}>{textureBarLine(engineId)}</span>
        <span className={s.runTruth}>{TEXTURE_MINUTES_LINE}</span>
        <span className={s.runWhy}>{TEXTURE_ONE_TAKE_LINE}</span>
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
          {runMut.isPending ? "Starting…" : textureRunLabel(engineId)}
        </button>
      </div>
      {engineId !== null && (
        <span className={`mono-micro ${s.foot}`}>
          THE REPAINTED MESH LANDS ON THE CANVAS BESIDE ITS PARENT, AS ITS
          CHILD — THE PARENT STAYS
        </span>
      )}
    </section>
  );
}
