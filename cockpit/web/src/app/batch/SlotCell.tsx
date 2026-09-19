// One grid cell. The cell IS the slot's honest
// face: eight states, per-slot failure copy (what happened · what it cost ·
// what to do next), money badges only where money moved. KEEP is
// deliberately absent — it has no product-model meaning (no kept flag, no
// Keep in the verb roster).
//
// The laws the face holds, each decided in grid.ts and only drawn here:
// the cause is the server's plain line and the raw engine text sits behind
// a collapsed DETAILS (law 3); line 2 is `costLine` — GPU seconds and takes
// for a local slot, dollars only where money was in play (law 5); the
// buttons are `remedies` — every non-landed state has its next move, free
// first, the costed one last with its price and never framed (laws 4 + 6);
// a running retry says so (law 9, `retryLine`); a held cell carries its
// own resume (laws 2 + 4). Under the top bar's LOCAL ONLY switch a paid
// reroll stays visible and inert with the reason on the face.
//
// The failure faces, by kind: a blank frame absolves the prompt and offers
// RUN AGAIN · SAME SEED first; a charged-but-blank paid take says the bill
// is being re-asked while it is (law 9), offers COPY RECEIPT first, and
// keeps the receipt selectable behind DETAILS so a clipboard that refuses
// still leaves the text in reach; an OOM cell leads with the record's
// answer (`remedies`); a rate-limited take waiting on the provider's clock
// counts down with GIVE UP in reach (`retryLine`); a held cell says whether
// it waits for the card (CHECK AGAIN · HOLD THE QUEUE) or for its turn
// (TRY ONCE MORE).
import { useState } from "react";
import {
  MESH_RUNNING_LINE,
  checkLine,
  costLine,
  elapsedLabel,
  money,
  oomFacts,
  passSuffix,
  remedies,
  retryLine,
  waitingLine,
  type CellVM,
  type Remedy,
} from "./grid";
import type { Slot } from "../../types";
import s from "./Batch.module.css";

export { REROLL_GATED_TITLE } from "./grid";

export type CellActions = {
  onReroll: (slotId: string) => void;
  onRerunSameSeed: (slotId: string) => void; // A5a: same slot, same seed
  onSetAside: (nodeId: string) => void;
  onRestore: (nodeId: string) => void;
  onCancel: (slotId: string) => void;
  onOpen: (nodeId: string) => void; // lightbox — full-res lives there only
  onUnbench: (scope: string) => void; // laws 2 + 4: resume from the cell
  onRunLocally: () => void; // law 6: the free remedy on a paid slot
  onRetryFrame: (slotId: string, frame: string) => void; // A1: "1152x1152"
  onCheckGpu: () => void; // C2: look at the card now
  onHoldQueue: () => void; // C2: keep the lane held until an unbench
};

function CostBadge({ cell }: { cell: CellVM }) {
  // charged is the truth once known; committed carries the badge while the
  // money is still in flight. Divergence stays visible in the title.
  const shown = cell.chargedUsd ?? cell.committedUsd;
  if (shown === null || shown === 0) return null;
  const title =
    cell.chargedUsd !== null && cell.committedUsd !== null
      ? `committed ${money(cell.committedUsd)} · charged ${money(cell.chargedUsd)}`
      : undefined;
  return (
    <span className={`mono-micro ${s.cost}`} title={title}>
      {money(shown)}
    </span>
  );
}

// The clipboard is asked politely; if it refuses (F4: inline, quiet), the
// receipt is already selectable behind DETAILS and the button says so.
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function RemedyBtn({
  remedy,
  cell,
  actions,
  framed,
  onCopied,
}: {
  remedy: Remedy;
  cell: CellVM;
  actions: CellActions;
  framed: boolean;
  onCopied?: (ok: boolean) => void;
}) {
  const [copied, setCopied] = useState<"COPIED" | "SELECT IT BELOW" | null>(null);
  const fire = () => {
    switch (remedy.id) {
      case "reroll":
        return actions.onReroll(cell.slotId);
      case "rerun-same-seed":
        return actions.onRerunSameSeed(cell.slotId);
      case "copy-receipt":
        if (cell.receipt === null) return;
        return void copyText(cell.receipt).then((ok) => {
          setCopied(ok ? "COPIED" : "SELECT IT BELOW");
          onCopied?.(ok);
        });
      case "run-locally":
        return actions.onRunLocally();
      case "unbench":
        return actions.onUnbench(cell.bench ?? `engine:${cell.engine}`);
      case "retry-as-is":
        return actions.onRerunSameSeed(cell.slotId);
      case "retry-smaller": {
        const small = oomFacts(cell).smaller;
        return small !== null && actions.onRetryFrame(cell.slotId, small);
      }
      case "give-up":
        return actions.onCancel(cell.slotId);
      case "check-gpu":
        return actions.onCheckGpu();
      case "hold-queue":
        return actions.onHoldQueue();
      case "restore":
        return cell.nodeId !== null && actions.onRestore(cell.nodeId);
      case "set-aside":
        return cell.nodeId !== null && actions.onSetAside(cell.nodeId);
    }
  };
  const cls = [
    "mono-micro",
    s.act,
    remedy.paid ? s.actPaid : s.actBlue,
    framed && remedy.primary ? s.actFramed : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      className={cls}
      disabled={remedy.gated !== null}
      title={remedy.gated ?? undefined}
      data-remedy={remedy.id}
      data-paid={remedy.paid ? "1" : undefined}
      onClick={fire}
    >
      {copied ?? remedy.label}
    </button>
  );
}

function Remedies({
  cell,
  localOnly,
  actions,
  onCopied,
}: {
  cell: CellVM;
  localOnly: boolean;
  actions: CellActions;
  onCopied?: (ok: boolean) => void;
}) {
  return (
    <div className={s.remedies}>
      {remedies(cell, localOnly).map((r) => (
        <RemedyBtn
          key={r.id}
          remedy={r}
          cell={cell}
          actions={actions}
          framed
          onCopied={onCopied}
        />
      ))}
    </div>
  );
}

// Law 3's `details` affordance: the raw engine text, collapsed, for a bug
// report — never in the primary message. A B4 cell's receipt lives here
// too, selectable — the clipboard is a convenience, the text is the fact.
function Details({
  text,
  receipt = null,
  open = false,
}: {
  text: string | null;
  receipt?: string | null;
  open?: boolean;
}) {
  if (text === null && receipt === null) return null;
  return (
    <details className={s.details} open={open || undefined}>
      <summary className={`mono-micro ${s.detailsSummary}`}>DETAILS</summary>
      {receipt !== null && (
        <pre className={s.detailsText} data-receipt="1">
          {receipt}
        </pre>
      )}
      {text !== null && <pre className={s.detailsText}>{text}</pre>}
    </details>
  );
}

function CancelX({
  cell,
  onCancel,
}: {
  cell: CellVM;
  onCancel: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className={s.cellCancel}
      aria-label={`Cancel slot ${cell.pos + 1}`}
      title={
        cell.external && cell.status === "running"
          ? "already dispatched — cancelling does not unspend it"
          : "drops free — nothing was spent"
      }
      onClick={() => onCancel(cell.slotId)}
    >
      ✕
    </button>
  );
}

export function SlotCell({
  cell,
  slots,
  nowMs,
  actions,
  onHover,
  localOnly = false,
}: {
  cell: CellVM;
  slots: readonly Slot[];
  nowMs: number;
  actions: CellActions;
  onHover: (slotId: string | null) => void;
  localOnly?: boolean;
}) {
  const a = actions;
  const footerLeft =
    cell.seed !== null
      ? `${cell.engine.toUpperCase()} · S ${cell.seed}`
      : `${cell.engine.toUpperCase()} · NO SEED`;
  const cost = costLine(cell, slots, nowMs);
  const retry = retryLine(cell, nowMs);
  const check = checkLine(cell);
  // a refused clipboard opens the fold so the receipt is in reach at once
  const [detailsOpen, setDetailsOpen] = useState(false);

  const shared = {
    onMouseEnter: () => onHover(cell.slotId),
    onMouseLeave: () => onHover(null),
    "data-slot-pos": cell.pos,
    "data-status": cell.status,
  };

  switch (cell.status) {
    case "queued":
      return (
        <div className={`${s.cell} ${s.cellGhost}`} {...shared}>
          <CancelX cell={cell} onCancel={a.onCancel} />
          <div className={s.ghostBody}>
            {retry !== null ? (
              <>
                <span className={`mono-label ${s.gold}`}>{retry.label}</span>
                <span className="effect-line">{retry.why}</span>
                {cell.retryWait !== null && (
                  // B2: nothing needed from you — and the give-up in reach
                  <>
                    <span className="effect-line">Nothing needed from you.</span>
                    <Remedies cell={cell} localOnly={localOnly} actions={a} />
                  </>
                )}
              </>
            ) : (
              <>
                <span className={`mono-label ${s.ghostWord}`}>
                  QUEUED · SLOT {cell.pos + 1}
                </span>
                <span className="effect-line">{waitingLine(cell)}</span>
              </>
            )}
          </div>
        </div>
      );

    case "running": {
      const started =
        cell.startedAt !== null ? Date.parse(cell.startedAt) : nowMs;
      return (
        <div className={`${s.cell} ${s.cellRunning}`} {...shared}>
          <CancelX cell={cell} onCancel={a.onCancel} />
          <div className={s.plate}>
            <span className={`mono-label ${s.gold}`}>
              {retry !== null
                ? `${retry.label} · ${elapsedLabel(nowMs - started)}`
                : `${cell.external ? "IN FLIGHT" : "RUNNING"} · ${elapsedLabel(nowMs - started)}${passSuffix(cell.passes)}`}
            </span>
            {/* one cause at a time (law 1): a scheduler retry is the more
                specific thing to say, so it wins the line; otherwise a mesh
                names its unit */}
            {retry !== null ? (
              <span className={`effect-line ${s.plateLine}`}>{retry.why}</span>
            ) : cell.mesh ? (
              <span className={`effect-line ${s.plateLine}`}>
                {MESH_RUNNING_LINE}
              </span>
            ) : null}
            <CostBadge cell={cell} />
          </div>
          <div className={`mono-micro ${s.footer}`}>
            <span>{footerLeft}</span>
            <span className={s.gold}>{cell.external ? "PAID" : "GPU"}</span>
          </div>
        </div>
      );
    }

    case "landed":
      return (
        <div className={`${s.cell} ${s.cellLanded}`} {...shared}>
          <button
            type="button"
            className={s.plateBtn}
            aria-label={`Open take ${cell.label} full-res`}
            onClick={() => cell.nodeId !== null && a.onOpen(cell.nodeId)}
          >
            {cell.thumb !== null ? (
              <img
                className={s.thumb}
                src={cell.thumb}
                alt=""
                loading="lazy"
                decoding="async"
                draggable={false}
              />
            ) : (
              <span className="mono-micro">TAKE {cell.attempt}</span>
            )}
            <CostBadge cell={cell} />
            {cell.promoted && (
              <span className={`mono-micro ${s.promoted}`}>PROMOTED</span>
            )}
          </button>
          {/* hover actions: free first, the paid reroll last (law 6) */}
          <div className={s.hoverBar}>
            {remedies(cell, localOnly).map((r) => (
              <RemedyBtn
                key={r.id}
                remedy={r}
                cell={cell}
                actions={a}
                framed={false}
              />
            ))}
          </div>
          <div className={`mono-micro ${s.footer}`}>
            <span>{footerLeft}</span>
            <span className={s.footerRight}>
              {cell.durationMs !== null
                ? `${cell.label} · ${elapsedLabel(cell.durationMs)}`
                : cell.label}
            </span>
          </div>
        </div>
      );

    case "failed":
      // One line each: what happened · what it cost you · what next.
      return (
        <div className={`${s.cell} ${s.cellFailed}`} {...shared}>
          <div className={s.ghostBody}>
            <span className={`mono-label ${s.red}`}>SLOT FAILED</span>
            <span className={`effect-line ${s.failCause}`}>
              {cell.cause ?? "the engine gave nothing back"}
            </span>
            <span className="effect-line">{cost}</span>
            {check !== null && (
              // B4's one automatic action, visible while it runs (law 9)
              <span className={`mono-micro ${s.gold}`} data-bill-check="1">
                {check}
              </span>
            )}
            <Remedies
              cell={cell}
              localOnly={localOnly}
              actions={a}
              onCopied={(ok) => !ok && setDetailsOpen(true)}
            />
          </div>
          <Details
            text={cell.detail}
            receipt={cell.receipt}
            open={detailsOpen}
          />
          <div className={`mono-micro ${s.footer}`}>
            <span>{footerLeft}</span>
            <span className={s.red}>ATTEMPT {cell.attempt}</span>
          </div>
        </div>
      );

    case "held":
      return (
        <div className={`${s.cell} ${s.cellHeld}`} {...shared}>
          <CancelX cell={cell} onCancel={a.onCancel} />
          <div className={s.ghostBody}>
            <span className={`mono-label ${s.gold}`}>
              {cell.benchKind === "gpu_held" ? "HELD · THE CARD IS TAKEN" : "HELD"}
            </span>
            <span className="effect-line">
              {cell.benchKind === "gpu_held"
                ? "something else holds the card — this take waits its turn, kept, not dropped"
                : cell.benchKind === "repeat"
                  ? `${cell.engine} is benched for this turn — this take is kept, not dropped`
                  : `${cell.engine} is benched — this take is kept, not dropped`}
            </span>
            <span className="effect-line">{cost}</span>
            <Remedies cell={cell} localOnly={localOnly} actions={a} />
          </div>
        </div>
      );

    case "late":
      // a paid cancelled take rests late, in its own frozen cell.
      return (
        <div className={`${s.cell} ${s.cellHeld}`} {...shared}>
          <div className={s.ghostBody}>
            <span className={`mono-label ${s.gold}`}>
              LATE · ALREADY PAID
            </span>
            <span className="effect-line">
              cancelled after dispatch — nothing else waits on it
            </span>
            <span className="effect-line">{cost}</span>
            <CostBadge cell={cell} />
          </div>
        </div>
      );

    case "skipped":
      return (
        <div className={`${s.cell} ${s.cellGhost}`} {...shared}>
          <div className={s.ghostBody}>
            <span className={`mono-label ${s.dim}`}>SKIPPED</span>
            <span className="effect-line">left unrun — reroll to run it</span>
            <span className="effect-line">{cost}</span>
            <Remedies cell={cell} localOnly={localOnly} actions={a} />
          </div>
        </div>
      );

    case "set_aside":
      return (
        <div className={`${s.cell} ${s.cellGhost}`} {...shared}>
          <div className={s.ghostBody}>
            <span className={`mono-label ${s.dim}`}>SET ASIDE</span>
            <span className="effect-line">Slot stays. Nothing moved.</span>
            <Remedies cell={cell} localOnly={localOnly} actions={a} />
          </div>
        </div>
      );
  }
}
