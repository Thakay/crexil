// The armed composer: what summoning "Image · new root", firing REFINE or
// firing VARY ONE DIAL arms. Prompt, the roster with a stepper on every
// row — grouped LOCAL · SEQUENTIAL, ONE GPU over EXTERNAL · CONCURRENT,
// PRICED (the take count is per engine, never one global N) — the six
// dials with one Advanced fold per stepped engine beneath them (Dials.tsx
// + Advanced.tsx), the run bar with the slot count, the spend and the
// one-GPU truth in counts and observed time (laws 13 + 17 — no predicted
// totals), and Run POSTing the turn WITH the summon anchor (the node is
// born where the palette was).
//
// Capability honesty on every row: under each engine's name its badges
// from the capability matrix — verified channels plain (SEED · REF ·
// MASK), anything unverified wearing the word — worded in badges.ts. A
// matrix that hasn't arrived prints nothing; one that can't be reached
// says so once on the roster; an unknown engine says NO CAPABILITY RECORD.
//
// One surface, two shapes, decided by the armed parents: none is a root
// txt2img run, one is a refine on that take's image. The difference is
// which recipe the roster needs, that the prompt arrives carried in, and
// that the external lane can't take part. The top bar's LOCAL ONLY switch
// makes the external lane inert the same way — rows listed, steppers off,
// the cause on the lane — and rides the run request, where the server
// refuses any external batch beneath it.
//
// Dials: positions resolve to each stepped engine's own raws client-side
// (dials.ts) and ride the run as one batch per engine —
// `local: [{preset_id, takes, inputs, take_overrides}]` — with the seed
// policy sent once for the turn; a sweep walks the dial on every stepped
// engine, each spending its own row's count with the seed held. The
// fold's raw overrides lie over those raws, per preset. Positions and
// overrides persist per workspace through PATCH …/view — debounced,
// flushed on disarm. Take counts don't persist: every arm starts from one
// take on one engine.
//
// Roster honesty (law 12): a preset without the needed recipe stays
// visible with a dashed 0 and the reason in words; an empty or unreachable
// catalogue says so instead of vanishing — and so does the dial table. A
// prompt past a stepped engine's token window is warned per engine in the
// run bar — the engine, the overflow, and that nothing is cut — and Run
// stays armed: validation disarms, never fails; this one doesn't even
// disarm, because the artist may mean it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type { Preset, WorkspaceDetail } from "../../types";
import { useUi } from "../state/ui";
import { targetLabel } from "../verbs/verbs";
import {
  EXTERNAL_LOCAL_ONLY,
  EXTERNAL_NO_SOURCE,
  MAX_TAKES,
  busyLine,
  diskWhy,
  slotAreas,
  vramWarning,
  errText,
  extEstimate,
  gpuSentence,
  hasRecipe,
  noRecipeReason,
  recipeFor,
  runLabel,
  slotsLine,
  sumTakes,
  whyNot,
  type Takes,
} from "./compose";
import {
  MATRIX_UNREACHABLE,
  badgeLine,
  externalBadges,
  localBadges,
} from "./badges";
import { Dials } from "./Dials";
import { tokenWarnings } from "./tokens";
import {
  SWEEP_MIN,
  dialById,
  readState,
  requestInputs,
  seedPolicy,
  sweepSlots,
  type DialState,
  type SliderId,
} from "./dials";
import s from "./Composer.module.css";

const NO_TAKES: never[] = [];
const EMPTY: Takes = new Map();
export const DIAL_SAVE_MS = 400;

function clampTakes(n: number): number {
  return Math.max(0, Math.min(MAX_TAKES, Math.floor(n) || 0));
}

/** One roster row's count: − n + with the number typeable. A row that
 *  can't take part shows a dashed 0 (law 12) — its reason sits beside it.
 *  Exported for the region surface's fan-out roster and the To-3D
 *  plinth: same control, same a11y labels, same clamp. `max`
 *  defaults to the image run's cap; the 3D lane's is lower (a mesh is
 *  minutes taken one at a time), and the ceiling belongs to the caller
 *  because the server's cap does. */
export function Stepper({
  label,
  value,
  min,
  max = MAX_TAKES,
  disabled,
  onChange,
  onDown,
}: {
  label: string;
  value: number;
  min: number;
  max?: number;
  disabled: boolean;
  onChange: (n: number) => void;
  onDown?: () => void;
}) {
  return (
    <span className={s.stepper} data-dashed={disabled} data-on={value > 0}>
      <button
        type="button"
        aria-label={`${label} −`}
        disabled={disabled || value <= 0}
        onClick={onDown ?? (() => onChange(value - 1))}
      >
        −
      </button>
      <input
        type="number"
        aria-label={label}
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        onChange={(e) =>
          onChange(Math.min(max, clampTakes(Number(e.target.value))))
        }
      />
      <button
        type="button"
        aria-label={`${label} +`}
        disabled={disabled || value >= max}
        onClick={() => onChange(value + 1)}
      >
        +
      </button>
    </span>
  );
}

export function Composer({ workspaceId }: { workspaceId: string }) {
  const armed = useUi((st) => st.armed);
  const disarm = useUi((st) => st.disarm);
  const localOnly = useUi((st) => st.localOnly);
  // Esc discards the run from anywhere on the page — not only while focus
  // sits inside the dock. Arming twice without disarming (the chip while
  // the palette's run is up, or N over the chip's) leaves focus on the
  // chip or the body, and a focus-scoped handler never heard the key.
  // An overlay that is up owns Esc first: the palette dismisses through
  // radix, the lightbox and the region surface through their own
  // hotkeys; the grid and the canvas's deselect stand down while a run is
  // armed (one thing per press).
  const overlayUp = useUi(
    (st) =>
      st.summonAt !== null ||
      st.lightboxNodeId !== null ||
      st.regionsNodeId !== null,
  );
  useHotkeys("esc", disarm, {
    enabled: armed !== null && !overlayUp,
    enableOnFormTags: true,
  });
  // Every arm puts the caret in the prompt — a re-arm too, which a mount-
  // time autoFocus would miss since the component stays up between arms.
  const promptEl = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (armed !== null) promptEl.current?.focus();
  }, [armed]);
  const queryClient = useQueryClient();

  const presetsQ = $api.useQuery("get", "/api/presets");
  const providersQ = $api.useQuery("get", "/api/providers");
  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  const dialsQ = $api.useQuery("get", "/api/dials");
  const enginesQ = $api.useQuery("get", "/api/engines");
  const wsQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  });
  // The live queue (the strip polls the same key): the lane labels are
  // server-sent (law 13 copy lives in api_v4), and the "queues behind" line
  // reads the depth and the running take's observed elapsed from it.
  const queueQ = $api.useQuery(
    "get",
    "/api/queue",
    {},
    { refetchInterval: 2000 },
  );
  // E6: the card and the drive as they are now, re-read while the
  // composer is armed — disk disarms with the numbers, a held card warns
  const headroomQ = $api.useQuery(
    "get",
    "/api/headroom",
    {},
    { refetchInterval: 5000 },
  );
  const runMut = $api.useMutation("post", "/api/workspaces/{wid}/turns");
  const detailKey = $api.queryOptions("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  }).queryKey;
  const viewMut = $api.useMutation("patch", "/api/workspaces/{wid}/view", {
    // The detail cache learns the saved dials at once, so a re-arm inside
    // the next refetch window reads what was just set, not what preceded it.
    onSuccess: (ws) =>
      queryClient.setQueryData<WorkspaceDetail>(detailKey, (old) =>
        old === undefined ? old : { ...old, dials: ws.dials },
      ),
  });

  const [prompt, setPrompt] = useState("");
  // Takes per local preset (0 = not in the batch) and per external model.
  // While a sweep is on, a stepped preset's row shows its walk length
  // instead — kept apart so the sweep switched off hands the count back.
  const [takes, setTakes] = useState<Record<string, number>>({});
  const [sweepTakes, setSweepTakes] = useState<Record<string, number>>({});
  const [ext, setExt] = useState<Record<string, number>>({});
  const [dial, setDial] = useState<DialState | null>(null);
  const [sweep, setSweep] = useState<SliderId | null>(armed?.sweep ?? null);

  const presets = useMemo(() => presetsQ.data ?? [], [presetsQ.data]);
  const models = useMemo(
    () => providersQ.data?.models ?? [],
    [providersQ.data],
  );
  const table = dialsQ.data;

  const parents = armed?.parents ?? NO_TAKES;
  const recipe = recipeFor(parents);
  const source = (nodesQ.data ?? []).find((n) => n.id === parents[0]);

  // The first stepped engine, once per arm and recipe: the parent's own
  // engine when it can run this recipe (refining a take usually means
  // refining it on the same engine), else the first preset that can — one
  // take. Membership is otherwise the artist's: stepping every local row
  // to 0 for an external-only run must not be undone underneath them.
  const defaulted = useRef<string | null>(null);
  useEffect(() => {
    if (presets.length === 0) return;
    const key = `${recipe}:${source?.engine ?? ""}`;
    if (defaulted.current === key) return;
    defaulted.current = key;
    const carried = presets.find(
      (p) => p.id === source?.engine && hasRecipe(p, recipe),
    );
    const pick = carried ?? presets.find((p) => hasRecipe(p, recipe));
    setTakes(pick === undefined ? {} : { [pick.id]: 1 });
  }, [presets, recipe, source?.engine]);

  // Refine means "same prompt, new seeds": the source's
  // prompt arrives carried in and editable. Keyed on the source id so a
  // node refetch never re-clobbers what the artist has since typed.
  const carriedFrom = useRef<string | null>(null);
  useEffect(() => {
    const pid = parents[0] ?? null;
    if (pid === null) {
      carriedFrom.current = null;
      return;
    }
    if (source === undefined || carriedFrom.current === pid) return;
    carriedFrom.current = pid;
    setPrompt(source.prompt ?? "");
  }, [parents, source]);

  // A root arm may bring its own brief (the failed paid cell's free
  // remedy re-arms the turn's prompt). Seeded once per arm — never
  // re-clobbered by a re-render, and a refine's carried prompt wins since
  // it arrives with a source and this one only arms without.
  const seededBy = useRef<object | null>(null);
  useEffect(() => {
    if (armed === null || armed.prompt === undefined || armed.prompt === null)
      return;
    if (seededBy.current === armed || armed.parents.length > 0) return;
    seededBy.current = armed;
    setPrompt(armed.prompt);
  }, [armed]);

  // Dials start from what the workspace remembers, once the table
  // that gives them meaning has arrived. A re-arm re-seeds the sweep
  // choice (the chip arms with one; the palette arms with none).
  useEffect(() => {
    if (dial !== null || table === undefined || wsQ.data === undefined) return;
    setDial(readState(wsQ.data.dials, table));
  }, [dial, table, wsQ.data]);
  useEffect(() => {
    setSweep(armed?.sweep ?? null);
  }, [armed]);

  // Positions persist per workspace: debounced while the artist drags,
  // flushed the moment the composer unmounts (Esc, Run, workspace change).
  const pending = useRef<DialState | null>(null);
  const timer = useRef<number | null>(null);
  const saveView = viewMut.mutate;
  const flush = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    const body = pending.current;
    if (body === null) return;
    pending.current = null;
    saveView({ params: { path: { wid: workspaceId } }, body: { dials: body } });
  }, [saveView, workspaceId]);
  useEffect(() => flush, [flush]);
  const changeDial = (next: DialState) => {
    setDial(next);
    pending.current = next;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, DIAL_SAVE_MS);
  };

  if (armed === null) return null;

  // What each local row spends: its takes, or — sweeping — its walk length
  // (2 up to the run cap, the table's default to begin with). A row
  // without this recipe spends nothing whatever it remembers.
  const walkDefault = sweepSlots(table);
  const countFor = (p: Preset): number => {
    if (!hasRecipe(p, recipe)) return 0;
    const n = takes[p.id] ?? 0;
    if (n <= 0) return 0;
    if (sweep === null) return n;
    return Math.max(SWEEP_MIN, Math.min(MAX_TAKES, sweepTakes[p.id] ?? walkDefault));
  };
  const setLocal = (p: Preset, n: number) => {
    const v = clampTakes(n);
    if (sweep !== null && v > 0) {
      setSweepTakes((prev) => ({ ...prev, [p.id]: Math.max(SWEEP_MIN, v) }));
      setTakes((prev) => ((prev[p.id] ?? 0) > 0 ? prev : { ...prev, [p.id]: 1 }));
    } else {
      setTakes((prev) => ({ ...prev, [p.id]: v }));
    }
  };
  const picked = presets.filter((p) => countFor(p) > 0);
  const counts: Takes = new Map(picked.map((p) => [p.id, countFor(p)]));
  const localTotal = sumTakes(counts);
  // A source image is local-only, so the external rows are inert while
  // one is attached — and nothing stepped there can ride along. The LOCAL
  // ONLY switch does the same from the top bar; the counts stay
  // remembered, so flipping it back hands them straight back.
  const extInert = parents.length > 0 || localOnly;
  const external: Takes = extInert
    ? EMPTY
    : new Map(Object.entries(ext).filter(([, n]) => n > 0));
  const extTotal = sumTakes(external);
  const total = localTotal + extTotal;
  // null while the catalogue is still on its way (or unreachable): "no
  // engine can do this" is a claim, and it isn't one we can make yet.
  const localReady = presetsQ.isSuccess
    ? presets.filter((p) => hasRecipe(p, recipe)).length
    : null;
  const est = extEstimate(models, external);
  // No table (still loading, or unreachable) = nothing sent for the
  // dials: every engine runs its recipe, which is exactly what untouched
  // dials mean. Built here, not in run(), because E6's disk sum
  // reads the same frames the request will carry.
  const batches = picked.map((p) => {
    const n = countFor(p);
    const half =
      table !== undefined && dial !== null
        ? requestInputs({ table, preset: p, recipe, state: dial, sweep, count: n })
        : { inputs: {}, take_overrides: [] };
    return { preset_id: p.id, takes: n, ...half };
  });
  const disk = diskWhy(headroomQ.data, slotAreas(batches, extTotal));
  const vram = vramWarning(headroomQ.data, localTotal);
  const why = whyNot({
    prompt,
    localTakes: localTotal,
    external: extTotal,
    recipe,
    localReady,
    disk,
  });
  const overruns = tokenWarnings({
    prompt,
    engines: enginesQ.data,
    presets: picked,
    counts,
    total,
  });
  const queue = queueQ.data;
  const behind = localTotal > 0 ? (queue?.depth ?? 0) : 0;
  const busy = busyLine(queue, localTotal);
  const laneLabel = (lane: "local" | "external") =>
    queue?.lanes[lane].label ?? lane.toUpperCase();
  const sweptLabel =
    sweep === null || table === undefined
      ? null
      : (dialById(table, sweep)?.label ?? sweep.toUpperCase());

  const run = () => {
    flush();
    runMut.mutate(
      {
        params: { path: { wid: workspaceId } },
        body: {
          prompt,
          local: batches,
          external: [...external].map(([model_id, n]) => ({ model_id, takes: n })),
          seed: dial === null ? { mode: "random" } : seedPolicy(dial.seed, sweep !== null),
          parents: [...parents],
          anchor: { x: armed.fx, y: armed.fy },
          local_only: localOnly,
        },
      },
      {
        onSuccess: () => {
          disarm();
          // Same caches the canvas invalidates on events — but now, not at
          // the next poll tick, so the grid auto-opens with the skeletons.
          void queryClient.invalidateQueries({ queryKey: detailKey });
          void queryClient.invalidateQueries({
            queryKey: $api.queryOptions("get", "/api/queue").queryKey,
          });
        },
      },
    );
  };

  const what =
    parents.length > 0
      ? sweep !== null
        ? "Sweep · from a take"
        : "Refine · from a take"
      : sweep !== null
        ? "Sweep · new root"
        : "New image · root";

  return (
    <div className={s.armed}>
      <div className={s.armedChip}>
        <span className={`mono-label ${s.what}`}>{what}</span>
        {parents.length > 0 && (
          <span className={s.source}>
            {/* thumbs, never full-res: the same card derivative */}
            <img
              className={s.sourceThumb}
              src={`/api/nodes/${parents[0]}/thumb`}
              alt=""
            />
            <span className={`mono-micro ${s.sourceName}`}>
              {source === undefined ? "SOURCE" : targetLabel([source])}
            </span>
          </span>
        )}
        <span className={s.where}>
          ✛ {Math.round(armed.fx)}, {Math.round(armed.fy)}
        </span>
        <span className="mono-micro">
          {parents.length > 0
            ? "Born beside its parent"
            : "Born where the palette was"}
        </span>
        <button type="button" className={s.discard} onClick={disarm}>
          Esc discards — nothing is spent yet
        </button>
      </div>

      <div className={s.promptCol}>
        <span className={`mono-label ${s.laneHead}`}>Prompt</span>
        <textarea
          ref={promptEl}
          className={s.prompt}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the image…"
          aria-label="Prompt"
        />
      </div>

      {table !== undefined && dial !== null ? (
        <Dials
          table={table}
          engines={enginesQ.data}
          presets={picked}
          recipe={recipe}
          state={dial}
          onChange={changeDial}
          sweep={sweep}
          onSweep={setSweep}
          counts={counts}
          models={models}
          external={external}
        />
      ) : (
        <section className={s.dials} aria-label="Dials">
          <span className="mono-label">Dials</span>
          {dialsQ.isError ? (
            <span className={s.runErr}>
              Dial table unreachable — every take runs at its engine&apos;s
              recipe; nothing is sent for the dials.
            </span>
          ) : (
            <span className={s.runWhy}>Dials loading…</span>
          )}
        </section>
      )}

      <div className={s.roster}>
        {/* one cause, one message (law 1): without the matrix no row can
            say what it can do, and none guesses */}
        {enginesQ.isError && (
          <span className={s.reason}>{MATRIX_UNREACHABLE}</span>
        )}
        <div className={s.lane} data-lane="local">
          <span className={`mono-label ${s.laneHead}`}>{laneLabel("local")}</span>
          <div className={s.laneScroll} role="group" aria-label="Local engines">
            {presetsQ.isError && (
              <span className={s.runErr}>
                Preset config unreachable — locals can&apos;t compose.
              </span>
            )}
            {presets.map((p) => {
              const ready = hasRecipe(p, recipe);
              const n = countFor(p);
              const badges = localBadges(enginesQ.data, p);
              return (
                <div
                  key={p.id}
                  className={s.engineRow}
                  data-dim={!ready}
                  data-on={n > 0}
                >
                  <span className={s.engineName}>{p.display_name}</span>
                  <Stepper
                    label={`${p.display_name} takes`}
                    value={n}
                    min={sweep !== null && n > 0 ? SWEEP_MIN : 0}
                    disabled={!ready}
                    onChange={(v) => setLocal(p, v)}
                    // sweeping, a row below its walk's floor leaves the batch
                    onDown={() =>
                      setLocal(p, sweep !== null && n - 1 < SWEEP_MIN ? 0 : n - 1)
                    }
                  />
                  {!ready && (
                    <span className={s.reason}>{noRecipeReason(recipe)}</span>
                  )}
                  {badges !== null && badges.length > 0 && (
                    <span className={s.badges}>{badgeLine(badges)}</span>
                  )}
                </div>
              );
            })}
          </div>
          {sweep !== null && (
            <span className={s.reason}>
              each stepped engine walks the sweep — {SWEEP_MIN} to {MAX_TAKES}{" "}
              slots each
            </span>
          )}
        </div>

        <div className={s.lane} data-lane="external">
          <span className={`mono-label ${s.laneHead}`}>{laneLabel("external")}</span>
          <div className={s.laneScroll} role="group" aria-label="External models">
            {providersQ.isPending && (
              <span className={s.runWhy}>Catalogue loading…</span>
            )}
            {providersQ.isError && (
              <span className={s.runErr}>
                External catalogue unreachable — local still runs.
              </span>
            )}
            {providersQ.isSuccess && models.length === 0 && (
              <span className={s.runWhy}>
                No external models — no key and no pins.
              </span>
            )}
            {/* Law 1: one cause, one message — stated on the lane, not
                repeated on every row. The rows stay listed with their
                steppers inert, never filtered away (law 12). The switch is
                the artist's own cause and outranks the source's. */}
            {models.length > 0 && localOnly && (
              <span className={s.reason}>{EXTERNAL_LOCAL_ONLY}</span>
            )}
            {models.length > 0 && !localOnly && parents.length > 0 && (
              <span className={s.reason}>{EXTERNAL_NO_SOURCE}</span>
            )}
            {models.map((m) => {
              const n = external.get(m.id) ?? 0;
              const badges = externalBadges(enginesQ.data, m);
              return (
                <div
                  key={m.id}
                  className={s.engineRow}
                  data-dim={extInert}
                  data-on={n > 0}
                >
                  <span className={s.engineName}>
                    {m.label}
                    <span className={s.price}>
                      {typeof m.price_usd_per_image === "number"
                        ? `$${m.price_usd_per_image.toFixed(2)}`
                        : "unlisted"}
                    </span>
                  </span>
                  <Stepper
                    label={`${m.label} takes`}
                    value={n}
                    min={0}
                    disabled={extInert}
                    onChange={(v) =>
                      setExt((prev) => ({ ...prev, [m.id]: clampTakes(v) }))
                    }
                  />
                  {badges !== null && badges.length > 0 && (
                    <span className={s.badges}>{badgeLine(badges)}</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className={s.runBar}>
        <span className={s.runCounts}>
          {slotsLine(total, est, localOnly)}
          {sweptLabel !== null && ` · SWEEPS ${sweptLabel}`}
        </span>
        <span className={s.runTruth}>{gpuSentence(localTotal, extTotal)}</span>
        {busy !== null && <span className={s.runBusy}>{busy}</span>}
        {overruns.map((w) => (
          <span key={w.engine} className={s.runWarn} data-token-warning={w.engine}>
            {w.text}
          </span>
        ))}
        {vram !== null && (
          <span className={s.runWarn} data-headroom-warning="vram">
            {vram}
          </span>
        )}
        {why !== null && (
          <span
            className={s.runWhy}
            data-headroom-warning={why === disk ? "disk" : undefined}
          >
            {why}
          </span>
        )}
        {runMut.isError && (
          <span className={s.runErr}>{errText(runMut.error)}</span>
        )}
        <button
          type="button"
          className={s.runBtn}
          disabled={why !== null || runMut.isPending}
          onClick={run}
        >
          {runMut.isPending ? "Starting…" : runLabel(total, est, behind)}
        </button>
      </div>
    </div>
  );
}
