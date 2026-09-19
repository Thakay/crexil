// The provenance record: one component, three named rows,
// mounted by scope — the grid's turn header (turn rows: MADE FROM · THE
// RECIPE), the verb row's ACTING ON head and the image lightbox's caption
// (all three, THIS TAKE added). The words come from record.ts; this file
// only draws them and fires the one write path there is — USE THIS PROMPT
// arms the composer with the words through `armed.prompt` (the same door
// the grid's run-locally remedy uses). Nothing here edits a record: an
// edited prompt on a landed take would make the ledger lie, so recall is
// the whole verb.
//
// A row with nothing behind it is drawn dashed and muted, in the app's own
// grammar for an absence (IMPORTED · NO SEED) — never blank, never zero.
import { useState } from "react";
import type { Node, Turn } from "../../types";
import { recallPrompt, recordRows } from "./record";
import s from "./Record.module.css";

// Past this many characters the words clamp to two lines with MORE — a
// turn-30 prompt is a paragraph, and the grid's cells are the point.
export const CLAMP_AT = 160;

export function Record({
  scope,
  turn,
  nodes,
  all,
  onUsePrompt,
  className = "",
}: {
  scope: "turn" | "take";
  /** The turn in scope — the grid's, or the node's by `turn_id`. */
  turn: Turn | undefined;
  /** The selection (take scope); empty for the turn itself. */
  nodes: Node[];
  /** The workspace feed, for naming parents. */
  all: Node[];
  /** Fires the recall; absent = the record is an echo with no door. */
  onUsePrompt?: (prompt: string) => void;
  className?: string;
}) {
  const rows = recordRows({ scope, turn, nodes, all });
  const recall = recallPrompt(turn, nodes);
  const [open, setOpen] = useState(false);
  const long = recall !== null && recall.length > CLAMP_AT;
  return (
    <dl
      className={`${s.record} ${className}`}
      role="group"
      aria-label="Provenance"
      data-scope={scope}
    >
      {rows.map((r) => (
        <div
          key={r.name}
          className={`${s.row} ${r.blank ? s.blank : ""}`}
          data-row={r.name}
        >
          <dt className={`mono-micro ${s.name}`}>{r.name}</dt>
          <dd className={s.body}>
            {r.name === "MADE FROM" && !r.blank ? (
              <span
                className={`${s.words} ${long && !open ? s.clamped : ""}`}
              >
                {r.lines.join("\n")}
              </span>
            ) : (
              r.lines.map((line, i) => (
                <span key={i} className={`mono-label ${s.line}`}>
                  {line}
                </span>
              ))
            )}
            {r.note !== null && (
              <span className={`mono-micro ${s.note}`}>{r.note}</span>
            )}
          </dd>
          {r.name === "MADE FROM" && (long || (recall !== null && onUsePrompt)) && (
            <span className={s.acts}>
              {long && (
                <button
                  type="button"
                  className={`mono-micro ${s.act}`}
                  aria-expanded={open}
                  onClick={() => setOpen((o) => !o)}
                >
                  {open ? "LESS" : "MORE"}
                </button>
              )}
              {recall !== null && onUsePrompt !== undefined && (
                <button
                  type="button"
                  className={`mono-micro ${s.act} ${s.actBlue}`}
                  onClick={() => onUsePrompt(recall)}
                >
                  USE THIS PROMPT · P
                </button>
              )}
            </span>
          )}
        </div>
      ))}
    </dl>
  );
}
