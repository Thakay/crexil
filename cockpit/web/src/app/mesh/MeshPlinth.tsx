// The To-3D plinth — what firing TO 3D on an image take, or picking
// `3D · from an image` in the summon palette, opens.
//
// It is a plinth in the dock under the verb row, not a third shape of the
// composer, because a mesh run shares almost nothing with an image run: no
// prompt reaches a mesh engine (the image IS the input), no intent dial
// applies (there is no frame, no guidance, no steps to normalise), no
// external model can appear, and the engines are not presets. Everything
// the composer draws would have to be conditionally gutted; what is left —
// a roster with a stepper and a run bar — is this file. The same seam
// PromotePlinth cut: a verb's own surface, in the dock, where the take is
// already named.
//
// The one composer part that DOES transfer is the Advanced fold, holding
// resolution tier, texture size and seed. It stays collapsed and its rows
// are read off the matrix's `params` spec rather than authored here; the
// six intent dials still have no meaning on this lane — a fold is the
// other tier.
//
// Law 12 on this surface: when the runner isn't answering, both engines
// stay listed with a dashed 0 and the SERVER's own reason under them
// (`GET /api/mesh/health`) — the row is dimmed, never removed, and the
// reason is the runner's sentence, not a paraphrase. Capability badges
// come off the matrix through the composer's grammar, so a mesh row reads
// like every other roster row in the app.
//
// Minutes, not seconds: the bar says the unit and the one-GPU fact and
// stops there — a predicted total would be this card's guess (law 17).
// The SLOT has its own minutes-not-seconds face; this is the run face.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import { Stepper } from "../composer/Composer";
import { MATRIX_UNREACHABLE, badgeLine } from "../composer/badges";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { targetLabel } from "../verbs/verbs";
import {
  MESH_FOLD_LEAD,
  MESH_MAX_PER_ENGINE,
  MESH_MINUTES_LINE,
  MESH_SEED_DEFAULT,
  MESH_SEED_LINE,
  buildMeshRun,
  clearMeshKnobs,
  meshBarLine,
  meshErr,
  meshFoldChanged,
  meshFoldChangedLine,
  meshFoldCount,
  meshFoldHead,
  meshFoldRows,
  meshGate,
  meshKnobLine,
  meshKnobValue,
  meshKnobsChanged,
  meshRoster,
  meshRunLabel,
  setMeshKnob,
  sumMeshTakes,
  type MeshKnob,
  type MeshKnobs,
  type MeshSeed,
} from "./mesh";
import s from "./Mesh.module.css";

export function MeshPlinth({
  nodeId,
  workspaceId,
}: {
  nodeId: string;
  workspaceId: string;
}) {
  const closeMesh = useUi((st) => st.closeMesh);
  const [counts, setCounts] = useState<Map<string, number>>(new Map());
  // The fold's state. Knobs are per engine and hold only what was
  // touched; the seed is the run's, which is why it lives beside them rather
  // than inside either engine's group.
  const [knobs, setKnobs] = useState<MeshKnobs>(new Map());
  const [seed, setSeed] = useState<MeshSeed>(MESH_SEED_DEFAULT);
  const queryClient = useQueryClient();

  // Esc closes it and nothing is queued — the same promise the armed
  // composer makes, in the same words.
  useHotkeys("esc", closeMesh, { enableOnFormTags: true });

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  const wsQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  });
  const enginesQ = $api.useQuery("get", "/api/engines");
  // The door, re-asked while the plinth is up: a runner started from the
  // terminal mid-decision should light the rows without a reload.
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
  const roster = meshRoster(enginesQ.data, healthQ.data);

  // One take on the first engine that can run, the moment the roster
  // arrives: a run starts armed at one, and the
  // artist steps from there rather than at zero.
  useEffect(() => {
    if (counts.size > 0 || roster.length === 0) return;
    const first = roster.find((r) => r.capable);
    if (first !== undefined) setCounts(new Map([[first.engine.id, 1]]));
  }, [roster, counts.size]);

  const total = sumMeshTakes(counts);
  const why = meshGate({
    roster,
    rosterReady: enginesQ.isSuccess,
    counts,
    health: healthQ.data,
  });

  const setTakes = (id: string, n: number) =>
    setCounts((prev) => {
      const next = new Map(prev);
      const v = Math.max(0, Math.min(MESH_MAX_PER_ENGINE, Math.floor(n) || 0));
      if (v === 0) next.delete(id);
      else next.set(id, v);
      return next;
    });

  const run = () => {
    if (source === undefined) return;
    runMut.mutate(
      {
        params: { path: { wid: workspaceId } },
        body: buildMeshRun({
          source,
          counts,
          turns: wsQ.data?.turns ?? [],
          knobs,
          seed,
        }),
      },
      {
        onSuccess: () => {
          closeMesh();
          // Now, not at the next poll tick, so the grid auto-opens with the
          // skeletons — the same caches the composer's Run invalidates.
          invalidateWorkspace(queryClient, workspaceId);
          void queryClient.invalidateQueries({
            queryKey: $api.queryOptions("get", "/api/queue").queryKey,
          });
        },
      },
    );
  };

  return (
    <section className={s.plinth} aria-label="To 3D">
      <div className={s.head}>
        <span className={`mono-label ${s.what}`}>3D · from an image</span>
        {source !== undefined && (
          <span className={s.source}>
            {/* thumbs, never full-res: the same card derivative */}
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
        <span className="mono-micro">Born beside its source</span>
        <span className={s.spacer} />
        <button type="button" className={s.discard} onClick={closeMesh}>
          Esc closes — nothing is queued yet
        </button>
      </div>

      {/* The image is the whole input: there is no prompt to write and no
          dial to set, and saying so beats leaving the artist to notice
          two absent controls (law 3's register). The source's own brief
          rides the turn as lineage: the server carries it. */}
      <p className={s.noInput}>
        The image is the input — no prompt, no dials, no frame. The take&apos;s
        own brief rides along so the mesh reads as what it came from.
      </p>

      <div className={s.roster} role="group" aria-label="Mesh engines">
        <span className={`mono-label ${s.laneHead}`}>
          LOCAL · SEQUENTIAL, ONE GPU
        </span>
        {/* one cause, one message (law 1) */}
        {enginesQ.isError && <span className={s.reason}>{MATRIX_UNREACHABLE}</span>}
        {enginesQ.isSuccess && roster.length === 0 && (
          <span className={s.reason}>
            The capability matrix publishes no 3D engine.
          </span>
        )}
        {roster.map((r) => {
          const n = counts.get(r.engine.id) ?? 0;
          return (
            <div
              key={r.engine.id}
              className={s.engineRow}
              data-engine={r.engine.id}
              data-dim={!r.capable}
              data-on={n > 0}
            >
              <span className={s.engineName}>{r.engine.name}</span>
              <Stepper
                label={`${r.engine.name} takes`}
                value={n}
                min={0}
                max={MESH_MAX_PER_ENGINE}
                disabled={!r.capable}
                onChange={(v) => setTakes(r.engine.id, v)}
              />
              {r.reason !== null && <span className={s.reason}>{r.reason}</span>}
              {r.badges.length > 0 && (
                <span className={s.badges}>{badgeLine(r.badges)}</span>
              )}
            </div>
          );
        })}
      </div>

      <MeshAdvanced
        groups={meshFoldRows(enginesQ.data, counts)}
        knobs={knobs}
        seed={seed}
        onKnob={(engineId, key, v) =>
          setKnobs((prev) => setMeshKnob(prev, engineId, key, v))
        }
        onReset={(engineId) =>
          setKnobs((prev) => clearMeshKnobs(prev, engineId))
        }
        onSeed={setSeed}
      />

      <div className={s.runBar}>
        <span className={s.runCounts}>{meshBarLine(counts)}</span>
        <span className={s.runTruth}>{MESH_MINUTES_LINE}</span>
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
          {runMut.isPending ? "Starting…" : meshRunLabel(counts)}
        </button>
      </div>
      {/* The count the button face carries is the same one the bar states;
          repeating it here would be law 1's failure, so this line carries
          the thing neither says: where the takes go. */}
      {total > 0 && (
        <span className={`mono-micro ${s.foot}`}>
          {total === 1 ? "THE MESH LANDS" : `THE ${total} MESHES LAND`} ON THE
          CANVAS BESIDE THE SOURCE, AS ITS CHILDREN
        </span>
      )}
    </section>
  );
}

/** The Advanced fold (the composer fold's grammar on the 3D lane).
 *
 *  Collapsed on every open, like the composer's: the recipe each engine
 *  ships with is the answer, and this is for the run where it isn't. It
 *  draws only stepped engines — a fold for an engine that isn't in the batch
 *  would be settings with nowhere to go — and it draws EVERY knob those
 *  engines publish, including the ones they don't have, dashed with the
 *  reason (law 12). */
function MeshAdvanced({
  groups,
  knobs,
  seed,
  onKnob,
  onReset,
  onSeed,
}: {
  groups: ReturnType<typeof meshFoldRows>;
  knobs: MeshKnobs;
  seed: MeshSeed;
  onKnob: (engineId: string, key: string, v: string | number | null) => void;
  onReset: (engineId: string) => void;
  onSeed: (s: MeshSeed) => void;
}) {
  const [open, setOpen] = useState(false);
  if (groups.length === 0) return null;
  const changed = meshFoldChanged(groups, knobs, seed);

  return (
    <section className={s.fold} aria-label="Advanced" data-open={open}>
      <button
        type="button"
        className={s.foldHead}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`mono-label ${s.foldName}`}>
          {open ? "▾" : "▸"} {meshFoldHead(meshFoldCount(groups))}
        </span>
        <span className={s.foldLead}>{MESH_FOLD_LEAD}</span>
        <span
          className={`mono-micro ${changed > 0 ? s.foldChanged : ""}`}
          data-changed={changed}
        >
          {meshFoldChangedLine(changed)}
        </span>
      </button>

      {open && (
        <div className={s.foldBody}>
          {/* The seed is the RUN's, not an engine's — said here rather than
              repeated into every engine's group, where changing it in one
              and not the other would be a promise the surface can't keep. */}
          <div className={s.foldGroup}>
            <span className={`mono-label ${s.foldGroupName}`}>
              THE RUN · EVERY ENGINE
            </span>
            <div className={s.foldRow} data-key="seed" data-changed={seed.locked}>
              <div className={s.foldRowHead}>
                <span className={`mono-label ${s.foldLabel}`}>SEED</span>
                {seed.locked && (
                  <button
                    type="button"
                    className={s.foldRowReset}
                    aria-label="Reset seed"
                    onClick={() => onSeed({ ...seed, locked: false })}
                  >
                    RESET
                  </button>
                )}
              </div>
              <div className={s.seedRow}>
                <input
                  id="mesh-seed-lock"
                  type="checkbox"
                  checked={seed.locked}
                  onChange={(e) => onSeed({ ...seed, locked: e.target.checked })}
                />
                <label className="mono-micro" htmlFor="mesh-seed-lock">
                  LOCK
                </label>
                <input
                  className={s.handleInput}
                  type="number"
                  min={0}
                  step={1}
                  aria-label="Seed value"
                  disabled={!seed.locked}
                  value={seed.value}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) onSeed({ ...seed, value: v });
                  }}
                />
              </div>
              <span className={`mono-micro ${s.raw}`}>
                {seed.locked ? `SENDS ${Math.floor(seed.value)}` : "A NEW NUMBER PER TAKE"}
              </span>
              <p className={s.handleLine}>{MESH_SEED_LINE}</p>
            </div>
          </div>

          {groups.map((g) => (
            <div className={s.foldGroup} key={g.engine.id}>
              <span className={`mono-label ${s.foldGroupName}`}>
                {g.engine.name.toUpperCase()}
              </span>
              {g.knobs.map((k) => (
                <KnobRow
                  key={k.key}
                  knob={k}
                  value={meshKnobValue(knobs, g.engine.id, k.key)}
                  onSet={(v) => onKnob(g.engine.id, k.key, v)}
                />
              ))}
              {meshKnobsChanged(knobs, g.engine.id) > 0 && (
                <button
                  type="button"
                  className={s.foldResetAll}
                  onClick={() => onReset(g.engine.id)}
                >
                  RESET {g.engine.name.toUpperCase()} TO DEFAULT
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function KnobRow({
  knob,
  value,
  onSet,
}: {
  knob: MeshKnob;
  value: string | number | null;
  onSet: (v: string | number | null) => void;
}) {
  return (
    <div
      className={s.foldRow}
      data-key={knob.key}
      data-inert={!knob.supported}
      data-changed={value !== null}
    >
      <div className={s.foldRowHead}>
        <span className={`mono-label ${s.foldLabel}`}>{knob.label}</span>
        {value !== null && (
          <button
            type="button"
            className={s.foldRowReset}
            aria-label={`Reset ${knob.label}`}
            onClick={() => onSet(null)}
          >
            RESET
          </button>
        )}
      </div>
      {knob.supported && (
        <select
          className={s.handleInput}
          aria-label={knob.label}
          value={String(value ?? knob.fallback ?? "")}
          onChange={(e) => {
            // the choices carry their own type; a numeric enum has to go back
            // out as a number or the runner refuses it by type, not by name
            const raw = e.target.value;
            const hit = knob.choices.find((c) => String(c) === raw);
            onSet(hit ?? raw);
          }}
        >
          {knob.choices.map((c) => (
            <option key={String(c)} value={String(c)}>
              {String(c)}
            </option>
          ))}
        </select>
      )}
      <span
        className={`mono-micro ${
          value !== null ? s.foldChanged : knob.supported ? s.raw : s.badge
        }`}
      >
        {meshKnobLine(knob, value)}
      </span>
      {/* the plain-effect line is permanently visible, never a tooltip.
          A handle this engine doesn't have prints its reason instead. */}
      <p className={s.handleLine}>{knob.supported ? knob.effect : knob.why}</p>
    </div>
  );
}
