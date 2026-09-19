// The Advanced fold: one collapsed
// row under the dials — `ADVANCED · N ENGINE-SPECIFIC SETTINGS`, the lead
// "Nothing in here changes what the picture is of", and the changed-from-
// default count — that opens onto the raw knobs. Contents are per engine,
// per verb: the picked preset's input specs that apply to this recipe,
// the dial-owned keys at their FULL raw range (the extremes the dial bands
// stop short of), and every unsupported handle shown with its reason,
// never dropped (law 12). The word "sampler" is spoken only in here, and
// only once the fold is open.
//
// Draws and reports only: rows, baselines and what gets sent resolve in
// dials.ts (foldRows, pure, under test). Collapsed on every arm.
// Several engines in one batch mean one fold per engine: each
// instance is one preset's, and `named` puts that engine's name in its row.
import { useState, type ReactNode } from "react";
import type { Preset, Dials } from "../../types";
import type { Recipe } from "./compose";
import {
  FOLD_LEAD,
  clearAdvanced,
  foldChanged,
  foldChangedLine,
  foldHead,
  foldRowLine,
  foldRows,
  setAdvanced,
  type AdvancedValue,
  type DialState,
  type FoldRow,
  type SliderId,
} from "./dials";
import s from "./Composer.module.css";

export function Advanced({
  table,
  preset,
  recipe,
  state,
  onChange,
  sweep,
  named = false,
}: {
  table: Dials;
  preset: Preset | null;
  recipe: Recipe;
  state: DialState;
  onChange: (next: DialState) => void;
  sweep: SliderId | null;
  /** Name the engine in the row — set when the batch has several. */
  named?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rows =
    preset === null ? null : foldRows({ table, preset, recipe, state, sweep });
  const changed = rows === null ? 0 : foldChanged(rows);
  const stored =
    preset === null ? 0 : Object.keys(state.advanced[preset.id] ?? {}).length;

  return (
    <section
      className={s.fold}
      aria-label={named && preset !== null ? `Advanced · ${preset.display_name}` : "Advanced"}
      data-open={open}
    >
      <button
        type="button"
        className={s.foldHead}
        aria-expanded={open}
        disabled={rows === null}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`mono-label ${s.foldName}`}>
          {open ? "▾" : "▸"}{" "}
          {foldHead(rows, named && preset !== null ? preset.display_name : undefined)}
        </span>
        <span className={s.foldLead}>{FOLD_LEAD}</span>
        {rows !== null && (
          <span
            className={`mono-micro ${changed > 0 ? s.foldChanged : ""}`}
            data-changed={changed}
          >
            {foldChangedLine(changed)}
          </span>
        )}
      </button>

      {open && rows !== null && preset !== null && (
        <div className={s.foldBody}>
          <div className={s.foldGrid}>
            {rows.map((row) => (
              <Row
                key={row.spec.key}
                row={row}
                onSet={(v) =>
                  onChange(setAdvanced(state, preset.id, row.spec.key, v))
                }
              />
            ))}
          </div>
          {stored > 0 && (
            <button
              type="button"
              className={s.foldResetAll}
              onClick={() => onChange(clearAdvanced(state, preset.id))}
            >
              RESET ALL TO DEFAULT
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function Row({
  row,
  onSet,
}: {
  row: FoldRow;
  onSet: (v: AdvancedValue | null) => void;
}) {
  const { spec, label } = row;
  const inert = row.inert !== null;
  const value = row.stored ?? row.baseline;
  const choices = spec.choices ?? [];

  let control: ReactNode = null;
  if (!spec.supported) control = null;
  else if (spec.type === "enum")
    control = (
      <select
        aria-label={label}
        value={typeof value === "string" ? value : ""}
        disabled={inert}
        onChange={(e) => onSet(e.target.value)}
      >
        {/* a remembered value that is no longer a choice stays visible
            (the row says it won't be sent) rather than snapping to
            another — nothing is rewritten under the artist */}
        {typeof value === "string" && !choices.includes(value) && (
          <option value={value}>{value}</option>
        )}
        {choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  else if (spec.type === "int" || spec.type === "float")
    control = (
      <input
        type="number"
        aria-label={label}
        min={spec.min ?? undefined}
        max={spec.max ?? undefined}
        step={spec.step ?? (spec.type === "int" ? 1 : "any")}
        value={value === null ? "" : String(value)}
        disabled={inert}
        onChange={(e) => {
          // An emptied field keeps the last value — RESET is the way back
          // to the dial; a typed number is stored as typed and clamped
          // visibly on the line below.
          const v = e.target.value;
          if (v === "") return;
          const n = Number(v);
          if (Number.isFinite(n)) onSet(n);
        }}
      />
    );
  else
    control = (
      <input
        type="text"
        aria-label={label}
        placeholder={spec.placeholder ?? undefined}
        value={typeof value === "string" ? value : ""}
        disabled={inert}
        onChange={(e) => onSet(e.target.value)}
      />
    );

  return (
    <div
      className={s.foldRow}
      data-key={spec.key}
      data-wide={spec.type === "text"}
      data-inert={inert}
      data-changed={row.changed}
    >
      <div className={s.foldRowHead}>
        <span className={`mono-label ${s.foldLabel}`}>{label}</span>
        {row.stored !== null && (
          <button
            type="button"
            className={s.foldRowReset}
            aria-label={`Reset ${label}`}
            onClick={() => onSet(null)}
          >
            RESET
          </button>
        )}
      </div>
      {control}
      <span
        className={`mono-micro ${row.changed ? s.foldChanged : inert ? s.badge : s.raw}`}
      >
        {foldRowLine(row)}
      </span>
      {inert && <span className={s.why}>{row.inert}</span>}
    </div>
  );
}
