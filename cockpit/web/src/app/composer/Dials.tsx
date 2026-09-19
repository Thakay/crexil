// The six dials: a 3×2 grid in the armed
// composer. Every dial is named for its EFFECT, not its parameter, and
// carries its plain-effect line permanently — never a tooltip. Raws
// print beneath each control for every stepped engine — one engine
// prints its numbers plain, several print one line each (the
// `SENDS FLUX 3.5 · SDXL 7.0` print); an engine that doesn't transmit a
// dial stays in the batch and says why (law 12). Which slots ignore a lock
// and which engines a big frame strains are worded from the tables at run
// time — nothing here predicts a number (law 17). The word "sampler"
// appears nowhere above the Advanced fold (Advanced.tsx), which
// sits under the grid — one fold per stepped engine; a key the fold
// overrides prints ADVANCED SETS on its dial instead of SENDS.
//
// Draws and reports only: resolution lives in dials.ts (pure, under test).
import type { Preset, ProviderModel, Dials as DialsTable, Engines } from "../../types";
import type { Recipe } from "./compose";
import { Advanced } from "./Advanced";
import {
  ASPECTS,
  SLIDERS,
  dialById,
  foldRows,
  frameBounds,
  frameHead,
  frameLines,
  frameWarning,
  frameWithHold,
  rawLines,
  resolveFrame,
  resolveSlider,
  rollSeed,
  seedBadges,
  seedLine,
  sweepLines,
  withHold,
  type DialState,
  type PrintLine,
  type SliderId,
} from "./dials";
import s from "./Composer.module.css";

export const DIALS_LEAD =
  "Normalised across engines — one setting, each engine's own numbers underneath.";

function Lines({ lines }: { lines: PrintLine[] }) {
  return (
    <>
      {lines.map((l) => (
        <span key={l.text} className={s.print}>
          <span className={`mono-micro ${l.muted ? s.badge : s.raw}`}>
            {l.text}
          </span>
          {l.why !== null && <span className={s.why}>{l.why}</span>}
        </span>
      ))}
    </>
  );
}

export function Dials({
  table,
  engines,
  presets,
  recipe,
  state,
  onChange,
  sweep,
  onSweep,
  counts,
  models,
  external,
}: {
  table: DialsTable;
  engines: Engines | undefined;
  /** The stepped local engines, in roster order — the dials' audience. */
  presets: readonly Preset[];
  recipe: Recipe;
  state: DialState;
  onChange: (next: DialState) => void;
  sweep: SliderId | null;
  onSweep: (next: SliderId | null) => void;
  /** Slots each stepped engine spends — while sweeping, the walk length. */
  counts: ReadonlyMap<string, number>;
  models: ProviderModel[];
  /** Stepped external models → takes. */
  external: ReadonlyMap<string, number>;
}) {
  const frameDial = dialById(table, "frame");
  const seedDial = dialById(table, "seed");
  const sweepDial = dialById(table, "sweep-one-dial");
  // Each engine's fold rows once, for the dials that print what it overrides.
  const rowsBy = new Map(
    presets.map((p) => [p.id, foldRows({ table, preset: p, recipe, state, sweep })]),
  );
  const frames =
    frameDial === undefined
      ? []
      : presets.map((p) => ({
          preset: p,
          frame: frameWithHold(resolveFrame(frameDial, p, state.frame), rowsBy.get(p.id) ?? []),
        }));
  const bounds = frameBounds(presets);
  const sweeping = sweep !== null;
  const seedLocked = state.seed.locked || sweeping;
  let extSlots = 0;
  for (const n of external.values()) extSlots += n;
  let sweepSlotsTotal = 0;
  for (const p of presets) sweepSlotsTotal += counts.get(p.id) ?? 0;

  return (
    <section className={s.dials} aria-label="Dials">
      <div className={s.dialsHead}>
        <span className="mono-label">Dials</span>
        <span className={s.lead}>{DIALS_LEAD}</span>
        {extSlots > 0 && (
          // One cause, one line (law 1): the external lane takes the words
          // only, so no dial reaches those slots — said once, not six times.
          <span className={`mono-micro ${s.badge}`}>
            {extSlots} EXTERNAL SLOT{extSlots > 1 ? "S" : ""} TAKE THE WORDS
            ONLY — DIALS DON&apos;T REACH THEM
          </span>
        )}
      </div>

      <div className={s.dialGrid}>
        {SLIDERS.map((id) => {
          const dial = dialById(table, id);
          if (dial === undefined) return null;
          const lines = rawLines(
            presets.map((p) => ({
              preset: p,
              raw: withHold(resolveSlider(dial, p, recipe, state[id]), rowsBy.get(p.id) ?? []),
            })),
          );
          return (
            <div key={id} className={s.dial} data-dial={id}>
              <div className={s.dialHead}>
                <label className="mono-label" htmlFor={`dial-${id}`}>
                  {dial.label}
                </label>
                <span className={s.dialValue}>{state[id]}</span>
              </div>
              <input
                id={`dial-${id}`}
                type="range"
                min={table.position.min}
                max={table.position.max}
                step={1}
                value={state[id]}
                aria-label={dial.label}
                onChange={(e) =>
                  onChange({ ...state, [id]: Number(e.target.value) })
                }
              />
              <span className={s.effect}>{dial.effect}</span>
              <Lines lines={lines} />
            </div>
          );
        })}

        {frameDial !== undefined && (
          <div className={s.dial} data-dial="frame">
            <div className={s.dialHead}>
              <label className="mono-label" htmlFor="dial-frame">
                {frameDial.label}
              </label>
              <span className={s.dialValue}>{frameHead(frames, state.frame)}</span>
            </div>
            <div className={s.seg} role="radiogroup" aria-label="Aspect">
              {(frameDial.aspects ?? ASPECTS).map((a) => (
                <button
                  key={a}
                  type="button"
                  role="radio"
                  aria-checked={state.frame.aspect === a}
                  onClick={() =>
                    onChange({
                      ...state,
                      frame: { ...state.frame, aspect: a as DialState["frame"]["aspect"] },
                    })
                  }
                >
                  {a}
                </button>
              ))}
            </div>
            <input
              id="dial-frame"
              type="range"
              min={bounds.min}
              max={bounds.max}
              step={64}
              value={state.frame.size}
              aria-label="Frame size"
              onChange={(e) =>
                onChange({
                  ...state,
                  frame: { ...state.frame, size: Number(e.target.value) },
                })
              }
            />
            <span className={s.effect}>{frameDial.effect}</span>
            <Lines lines={frameLines(frames)} />
            {frames.map(({ preset, frame }) => {
              const warn = frameWarning(engines, preset, frame);
              return warn === null ? null : (
                <span key={preset.id} className={s.warn}>
                  {warn}
                </span>
              );
            })}
          </div>
        )}

        {seedDial !== undefined && (
          <div className={s.dial} data-dial="seed">
            <div className={s.dialHead}>
              <label className="mono-label" htmlFor="dial-seed">
                {seedDial.label}
              </label>
              <span className={s.dialValue}>
                {seedLocked ? "LOCKED" : "FRESH"}
              </span>
            </div>
            <div className={s.seedRow}>
              <input
                id="dial-seed"
                type="number"
                min={0}
                value={state.seed.value}
                aria-label="Seed"
                onChange={(e) =>
                  onChange({
                    ...state,
                    seed: {
                      ...state.seed,
                      value: Math.max(0, Math.floor(Number(e.target.value) || 0)),
                    },
                  })
                }
              />
              <button
                type="button"
                aria-pressed={state.seed.locked}
                aria-label="Lock seed"
                // A sweep holds the lock itself — the toggle rests while it does.
                disabled={sweeping}
                onClick={() =>
                  onChange({
                    ...state,
                    seed: { ...state.seed, locked: !state.seed.locked },
                  })
                }
              >
                LOCK
              </button>
              <button
                type="button"
                aria-label="New seed"
                title="New seed"
                onClick={() =>
                  onChange({ ...state, seed: { ...state.seed, value: rollSeed() } })
                }
              >
                ↻
              </button>
            </div>
            <span className={s.effect}>{seedDial.effect}</span>
            <span className={`mono-micro ${s.raw}`}>
              {seedLine(state.seed, sweeping)}
            </span>
            {seedBadges({
              dial: seedDial,
              presets,
              models,
              external,
              locked: seedLocked,
            }).map((b) => (
              <span key={b} className={s.why}>
                {b}
              </span>
            ))}
          </div>
        )}

        {sweepDial !== undefined && (
          <div className={s.dial} data-dial="sweep-one-dial">
            <div className={s.dialHead}>
              <span className="mono-label">{sweepDial.label}</span>
              <span className={s.dialValue}>
                {sweep === null ? "OFF" : `${sweepSlotsTotal} SLOTS`}
              </span>
            </div>
            <div
              className={`${s.seg} ${s.segWrap}`}
              role="radiogroup"
              aria-label="Sweep target"
            >
              <button
                type="button"
                role="radio"
                aria-checked={sweep === null}
                onClick={() => onSweep(null)}
              >
                OFF
              </button>
              {(sweepDial.targets ?? []).map((t) => {
                const label = dialById(table, t)?.label ?? t.toUpperCase();
                return (
                  <button
                    key={t}
                    type="button"
                    role="radio"
                    aria-checked={sweep === t}
                    onClick={() => onSweep(t as SliderId)}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <span className={s.effect}>{sweepDial.effect}</span>
            <Lines
              lines={sweepLines({ table, presets, recipe, state, sweep, counts })}
            />
            {sweep !== null && (
              <span className={s.why}>
                {presets.length > 1
                  ? "each stepped engine walks its own row's count"
                  : `${sweepSlotsTotal} takes — the row's stepper sets how many`}{" "}
                — the seed held, everything else as set.
              </span>
            )}
          </div>
        )}
      </div>

      {presets.length === 0 ? (
        <Advanced
          table={table}
          preset={null}
          recipe={recipe}
          state={state}
          onChange={onChange}
          sweep={sweep}
        />
      ) : (
        presets.map((p) => (
          <Advanced
            key={p.id}
            table={table}
            preset={p}
            recipe={recipe}
            state={state}
            onChange={onChange}
            sweep={sweep}
            named={presets.length > 1}
          />
        ))
      )}
    </section>
  );
}
