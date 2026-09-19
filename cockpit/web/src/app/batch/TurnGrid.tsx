// The turn surface: header · frozen grid ·
// nothing else. Overlays the canvas region while open — the canvas stays
// mounted underneath (its RF store and events poll are the workspace's one
// live feed). Geometry law (8): template columns derive from slot COUNT,
// cells sit at their pos — statuses flip in place, nothing ever reflows.
// The two verbs that re-spend — reroll and backfill — carry the top bar's
// LOCAL ONLY switch in their bodies: the server refuses a paid
// cell beneath it in words, and the cell's own face already says so.
// The header folds failures sharing one cause into one line (law
// 1), a held cell resumes from its own face (laws 2 + 4), and a failed
// paid cell's free remedy arms the composer with the turn's brief (law 6).
// CANCEL TURN is two presses: the first arms STOP THE QUEUE beside
// KEEP GOING and says C6's breath with real numbers in the footer (never
// a modal, law 2; never above the grid, law 8), the second stops. A blank
// frame's RUN AGAIN · SAME SEED rides the reroll route with `keep_seed`;
// the queue view's bill checks reach the charged-but-blank cell.
// An OOM cell's RETRY AT a smaller frame rides the reroll route
// with `frame` (same seed); the queue view's retry waits reach the
// rate-limited cell; a bench scoped to this turn (A9) or holding this
// turn's cells for the card (C2) is one footer line with its move —
// TRY ONCE MORE, or CHECK AGAIN · HOLD THE QUEUE.
// The header carries the turn's provenance record: MADE FROM
// and THE RECIPE, once above up to sixteen cells (record/Record.tsx); P
// recalls the words into a fresh root arm, the same door as law 6's
// free remedy.
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type {
  BackfillRefusal,
  BillCheck,
  Passes,
  RetryWait,
  WorkspaceDetail,
} from "../../types";
import { invalidateWorkspace } from "../invalidate";
import { Record } from "../record/Record";
import { recallPrompt } from "../record/record";
import { useUi } from "../state/ui";
import { verbErr } from "../verbs/VerbRow";
import {
  benchNotices,
  cancelBreath,
  causeGroups,
  countsLine,
  deriveCells,
  frameOf,
  gridCols,
  meshEngineIds,
  stopLines,
  turnActive,
  type BenchRef,
} from "./grid";
import { SlotCell, type CellActions } from "./SlotCell";
import s from "./Batch.module.css";

// Ticking clock, skew-corrected by the queue payload's server `now`:
// terminal cells use server durations; only live elapsed ticks locally.
function useNowMs(serverNow: string | undefined): number {
  const [tick, setTick] = useState(() => Date.now());
  const skew = useRef(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (serverNow !== undefined) skew.current = Date.parse(serverNow) - Date.now();
  }, [serverNow]);
  return tick + skew.current;
}

export function TurnGrid({ workspace }: { workspace: WorkspaceDetail }) {
  const wid = workspace.id;
  const queryClient = useQueryClient();
  const viewedTurnId = useUi((st) => st.viewedTurnId);
  const viewTurn = useUi((st) => st.viewTurn);
  const closeGrid = useUi((st) => st.closeGrid);
  const openLightbox = useUi((st) => st.openLightbox);
  const wantArm = useUi((st) => st.wantArm);
  const lightboxOpen = useUi((st) => st.lightboxNodeId) !== null;
  const localOnly = useUi((st) => st.localOnly);

  const turns = workspace.turns;
  const turnIx = Math.max(
    0,
    viewedTurnId === null
      ? turns.length - 1
      : turns.findIndex((t) => t.id === viewedTurnId),
  );
  const turn = turns[turnIx];

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid } },
  });
  // Subscribes to the strip's 2s-polled cache entry — no second interval.
  const queueQ = $api.useQuery("get", "/api/queue");
  // The capability matrix, for which engines are 3D (already cached
  // by the composer and the To-3D roster; this shares the entry).
  const enginesQ = $api.useQuery("get", "/api/engines");
  const nowMs = useNowMs(queueQ.data?.now);

  // Mutations invalidate directly for snap; the events poll would catch up
  // within 2s anyway (belt for the browser that misses a tick).
  const invalidateAll = useCallback(
    () => invalidateWorkspace(queryClient, wid),
    [queryClient, wid],
  );

  const rerollMut = $api.useMutation("post", "/api/slots/{slot_id}/reroll", {
    onSettled: invalidateAll,
  });
  const asideMut = $api.useMutation("post", "/api/nodes/{nid}/set-aside", {
    onSettled: invalidateAll,
  });
  const restoreMut = $api.useMutation("post", "/api/nodes/{nid}/restore", {
    onSettled: invalidateAll,
  });
  const cancelSlotMut = $api.useMutation(
    "post",
    "/api/slots/{slot_id}/cancel",
    { onSettled: invalidateAll },
  );
  const cancelTurnMut = $api.useMutation(
    "post",
    "/api/turns/{turn_id}/cancel",
    { onSettled: invalidateAll },
  );
  // Laws 2 + 4: the pause is a queue state and the held cell
  // carries its own resume — the same route the strip's UNBENCH fires.
  const unbenchMut = $api.useMutation("post", "/api/unbench", {
    onSettled: invalidateAll,
  });
  // C2: the artist's two moves on a card held by something else
  const gpuCheckMut = $api.useMutation("post", "/api/gpu/check", {
    onSettled: invalidateAll,
  });
  const gpuHoldMut = $api.useMutation("post", "/api/gpu/hold", {
    onSettled: invalidateAll,
  });
  // Backfill, the batch verb: every set-aside slot re-armed in one action.
  // Refusals come back per slot (law 1) and are shown per slot, in the
  // server's words, in the surface's footer; they clear when the turn
  // changes.
  const [refused, setRefused] = useState<BackfillRefusal[]>([]);
  const backfillMut = $api.useMutation(
    "post",
    "/api/turns/{turn_id}/backfill",
    {
      onSettled: invalidateAll,
      onSuccess: (r) => setRefused(r.refused),
    },
  );

  // which bench parks each held slot (the queue view knows, SlotView.bench)
  // and what kind it is, so the face words C2 and A9
  const kinds = new Map<string, string>();
  for (const b of queueQ.data?.benches ?? []) kinds.set(b.scope, b.kind);
  const benches = new Map<string, BenchRef>();
  for (const h of queueQ.data?.held ?? [])
    if (h.bench !== null) benches.set(h.id, { scope: h.bench, kind: kinds.get(h.bench) ?? "" });
  // B4's bill re-asks in flight — the queue view carries them (law 9)
  const checks = new Map<string, BillCheck>();
  for (const c of queueQ.data?.checks ?? []) checks.set(c.slot_id, c);
  // B2's waits — the countdown's source (law 9)
  const retries = new Map<string, RetryWait>();
  for (const r of queueQ.data?.retries ?? []) retries.set(r.slot_id, r);
  // a region take's pipeline position rides the queue view too
  const passes = new Map<string, Passes>();
  for (const v of queueQ.data?.lanes.local.slots ?? [])
    if (v.passes != null) passes.set(v.id, v.passes);
  const gpuManualHold = queueQ.data?.gpu.manual_hold ?? false;
  // Which engines are 3D, so a cell can say minutes before anything
  // lands. The matrix is the roster (law 12) — never a list typed in here.
  const meshEngines = meshEngineIds(enginesQ.data);
  const cells =
    turn !== undefined
      ? deriveCells(turn, nodesQ.data ?? [], benches, checks, retries,
                    gpuManualHold, passes, meshEngines)
      : [];
  const actions: CellActions = {
    onReroll: (slotId) => {
      // the paid face is inert under the switch (and the server would
      // refuse it) — the keystroke path lands here too
      const c = cells.find((x) => x.slotId === slotId);
      if (c !== undefined && c.external && localOnly) return;
      rerollMut.mutate({
        params: { path: { slot_id: slotId } },
        body: { local_only: localOnly, keep_seed: false },
      });
    },
    // A5a: the settings stand — same slot, same dials, same seed
    onRerunSameSeed: (slotId) =>
      rerollMut.mutate({
        params: { path: { slot_id: slotId } },
        body: { local_only: localOnly, keep_seed: true },
      }),
    onSetAside: (nid) => asideMut.mutate({ params: { path: { nid } } }),
    onRestore: (nid) => restoreMut.mutate({ params: { path: { nid } } }),
    onCancel: (slotId) =>
      cancelSlotMut.mutate({ params: { path: { slot_id: slotId } } }),
    onOpen: openLightbox,
    onUnbench: (scope) => unbenchMut.mutate({ body: { scope } }),
    // A1: the same cell, same seed, at the record's smaller frame — which
    // then IS the cell's size (the server rewrites its dials)
    onRetryFrame: (slotId, frame) => {
      const f = frameOf(frame);
      if (f === null) return;
      rerollMut.mutate({
        params: { path: { slot_id: slotId } },
        body: { local_only: localOnly, keep_seed: true, frame: f },
      });
    },
    onCheckGpu: () => gpuCheckMut.mutate({}),
    onHoldQueue: () => gpuHoldMut.mutate({}),
    // Law 6's free remedy on a paid slot: a NEW run of the same brief,
    // armed with the local roster (the composer's default), nothing spent
    // until Run: the failed cell keeps its identity (B10 is not implemented).
    onRunLocally: () => wantArm([], null, turn?.prompt ?? ""),
  };

  // Keystroke parity for the hover actions (drag/hover is never the
  // only path): R reroll · ⌫ set aside · ⌘Z restore, on the hovered cell.
  const [hovered, setHovered] = useState<string | null>(null);
  const hoveredCell = cells.find((c) => c.slotId === hovered);
  const keysOn = !lightboxOpen && hoveredCell !== undefined;
  useHotkeys(
    "r",
    () => {
      if (
        hoveredCell !== undefined &&
        ["landed", "failed", "skipped", "set_aside"].includes(
          hoveredCell.status,
        )
      )
        actions.onReroll(hoveredCell.slotId);
    },
    { enabled: keysOn },
    [hoveredCell],
  );
  useHotkeys(
    "backspace",
    () => {
      if (hoveredCell?.status === "landed" && hoveredCell.nodeId !== null)
        actions.onSetAside(hoveredCell.nodeId);
    },
    { enabled: keysOn },
    [hoveredCell],
  );
  useHotkeys(
    "mod+z",
    () => {
      if (hoveredCell?.status === "set_aside" && hoveredCell.nodeId !== null)
        actions.onRestore(hoveredCell.nodeId);
    },
    { enabled: keysOn, preventDefault: true },
    [hoveredCell],
  );
  // C6: the stop is armed by the first press and lands on the second;
  // Esc (or KEEP GOING) stands it down. It disarms itself when the turn
  // changes or nothing is moving any more.
  const [stopArmed, setStopArmed] = useState(false);
  useEffect(() => setStopArmed(false), [turn?.id]);
  // An armed run owns Esc first (the composer's hotkey discards it); an
  // armed stop is next; the grid closes on the press after — one thing
  // per press.
  const armed = useUi((st) => st.armed !== null);
  useHotkeys(
    "esc",
    () => (stopArmed ? setStopArmed(false) : closeGrid()),
    { enabled: !lightboxOpen && !armed },
    [stopArmed],
  );

  const asideSlots =
    turn?.slots.filter((sl) => sl.status === "set_aside").length ?? 0;
  const backfill = () => {
    if (turn === undefined || asideSlots === 0 || backfillMut.isPending)
      return;
    backfillMut.mutate({
      params: { path: { turn_id: turn.id } },
      body: { local_only: localOnly },
    });
  };
  useHotkeys("b", backfill, { enabled: !lightboxOpen && asideSlots > 0 }, [
    turn?.id,
    asideSlots,
  ]);
  useEffect(() => setRefused([]), [turn?.id]);
  // P recalls the turn's words: a NEW root arm carrying them, nothing
  // spent until Run (the same door as law 6's free remedy). Not while the
  // lightbox owns the keys or a run is already armed (one thing per press).
  const recall = turn === undefined ? null : recallPrompt(turn, []);
  useHotkeys(
    "p",
    () => {
      if (recall !== null) wantArm([], null, recall);
    },
    // preventDefault: the arm focuses the prompt in the same tick, and the
    // key would otherwise land in it as a letter
    { enabled: !lightboxOpen && !armed && recall !== null, preventDefault: true },
    [recall],
  );

  if (turn === undefined) {
    return (
      <section className={s.surface} aria-label="Turn grid">
        <header className={s.head}>
          <span className="mono-label">NO TURNS YET</span>
          <span className={s.spacer} />
          <button type="button" className={s.headBtn} onClick={closeGrid}>
            ✕ CANVAS · ESC
          </button>
        </header>
        <p className={`effect-line ${s.emptyLine}`}>
          The first Run draws its grid here — every slot appears before any
          work starts.
        </p>
      </section>
    );
  }

  const active = turnActive(turn.slots);
  const folds = causeGroups(cells);
  const stopping = active && stopArmed;
  const breath = stopping ? stopLines(turn.slots, nowMs) : [];
  const benchLines = benchNotices(queueQ.data?.benches ?? [], turn.id, cells);
  return (
    <section className={s.surface} aria-label={`Turn ${turn.idx} grid`}>
      <header className={s.head}>
        <span className="mono-label">TURN {turn.idx}</span>
        {turns.length > 1 && (
          <span className={s.stepper}>
            <button
              type="button"
              className={s.headBtn}
              disabled={turnIx === 0}
              aria-label="Earlier turn"
              onClick={() => viewTurn(turns[turnIx - 1].id)}
            >
              ◂
            </button>
            <span className="mono-micro">
              {turnIx + 1}/{turns.length}
            </span>
            <button
              type="button"
              className={s.headBtn}
              disabled={turnIx === turns.length - 1}
              aria-label="Later turn"
              onClick={() => viewTurn(turns[turnIx + 1].id)}
            >
              ▸
            </button>
          </span>
        )}
        <span className={s.spacer} />
        <span className={`mono-label ${s.gold}`}>
          {countsLine(turn.slots)}
        </span>
        {asideSlots > 0 && (
          <span className={s.cancelWrap}>
            <button
              type="button"
              className={`${s.headBtn} ${s.headBtnBlue}`}
              disabled={backfillMut.isPending}
              onClick={backfill}
            >
              BACKFILL {asideSlots} SET-ASIDE{" "}
              {asideSlots === 1 ? "SLOT" : "SLOTS"} · B
            </button>
            <span className={`mono-micro ${s.breath}`}>
              fresh seeds, same engines — a batch of {turn.slots.length}{" "}
              stays a batch of {turn.slots.length}
            </span>
          </span>
        )}
        {active && !stopArmed && (
          <span className={s.cancelWrap}>
            <button
              type="button"
              className={`${s.headBtn} ${s.headBtnRed}`}
              onClick={() => setStopArmed(true)}
            >
              CANCEL TURN
            </button>
            <span className={`mono-micro ${s.breath}`}>
              {cancelBreath(turn.slots)}
            </span>
          </span>
        )}
        {stopping && (
          <span className={s.cancelWrap} data-stop-armed="1">
            <button
              type="button"
              className={`${s.headBtn} ${s.headBtnRed}`}
              disabled={cancelTurnMut.isPending}
              onClick={() => {
                setStopArmed(false);
                cancelTurnMut.mutate({
                  params: { path: { turn_id: turn.id } },
                });
              }}
            >
              STOP THE QUEUE
            </button>
            <button
              type="button"
              className={s.headBtn}
              onClick={() => setStopArmed(false)}
            >
              KEEP GOING · ESC
            </button>
          </span>
        )}
        <button type="button" className={s.headBtn} onClick={closeGrid}>
          ✕ CANVAS · ESC
        </button>
      </header>
      {/* The turn's record, once above the cells: the words
          and the recipe the whole turn shares. A strip of the header, never
          a cell: a fact of the turn belongs above the sixteen, not inside
          each. */}
      <Record
        scope="turn"
        turn={turn}
        nodes={[]}
        all={nodesQ.data ?? []}
        onUsePrompt={(p) => wantArm([], null, p)}
        className={s.record}
      />
      <div
        className={s.gridScroll}
        role="grid"
        aria-label={`${cells.length} slots`}
      >
        <div
          className={s.grid}
          style={{ gridTemplateColumns: `repeat(${gridCols(cells.length)}, minmax(150px, 230px))` }}
        >
          {cells.map((c) => (
            <SlotCell
              key={c.slotId}
              cell={c}
              slots={turn.slots}
              nowMs={nowMs}
              actions={actions}
              onHover={setHovered}
              localOnly={localOnly}
            />
          ))}
        </div>
      </div>
      {(refused.length > 0 ||
        folds.length > 0 ||
        breath.length > 0 ||
        benchLines.length > 0 ||
        backfillMut.isError ||
        rerollMut.isError ||
        unbenchMut.isError ||
        gpuCheckMut.isError) && (
        <ul className={s.notices} aria-label="Turn notices">
          {/* C6: the two acts of a stop in one breath, amounts real,
              before the stop lands — a footer, never a dialog (law 2) */}
          {breath.map((line) => (
            <li key={line} className="effect-line" data-stop-line="1">
              {line}
            </li>
          ))}
          {/* law 1: several cells dead of one cause — said once down here,
              each cell keeping its own face. A footer, never a header: a
              notice appearing must not move a single cell (law 8) */}
          {folds.map((f) => (
            <li key={f.cause} className="effect-line">
              slots {f.positions.map((p) => p + 1).join(" · ")} failed for
              one reason — {f.cause}
            </li>
          ))}
          {/* One line per bench touching this turn, with its move:
              A9's engine benched for this turn (TRY ONCE MORE), C2's card
              held by something else (CHECK AGAIN · HOLD THE QUEUE) */}
          {benchLines.map((b) => (
            <li key={b.scope} className="effect-line" data-bench-notice={b.kind}>
              {b.cause}{" "}
              {b.kind === "gpu_held" ? (
                <>
                  <button
                    type="button"
                    className={`mono-micro ${s.act} ${s.actBlue}`}
                    disabled={gpuCheckMut.isPending}
                    onClick={() => gpuCheckMut.mutate({})}
                  >
                    CHECK AGAIN
                  </button>
                  {gpuManualHold ? (
                    <button
                      type="button"
                      className={`mono-micro ${s.act} ${s.actBlue}`}
                      onClick={() => unbenchMut.mutate({ body: { scope: b.scope } })}
                    >
                      UNBENCH · RESUME
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={`mono-micro ${s.act} ${s.actBlue}`}
                      disabled={gpuHoldMut.isPending}
                      onClick={() => gpuHoldMut.mutate({})}
                    >
                      HOLD THE QUEUE
                    </button>
                  )}
                </>
              ) : (
                <button
                  type="button"
                  className={`mono-micro ${s.act} ${s.actBlue}`}
                  disabled={unbenchMut.isPending}
                  onClick={() => unbenchMut.mutate({ body: { scope: b.scope } })}
                >
                  TRY ONCE MORE
                </button>
              )}
            </li>
          ))}
          {gpuCheckMut.isError && (
            <li className="effect-line">{verbErr(gpuCheckMut.error)}</li>
          )}
          {backfillMut.isError && (
            <li className="effect-line">{verbErr(backfillMut.error)}</li>
          )}
          {rerollMut.isError && (
            <li className="effect-line">{verbErr(rerollMut.error)}</li>
          )}
          {unbenchMut.isError && (
            <li className="effect-line">{verbErr(unbenchMut.error)}</li>
          )}
          {refused.map((r) => (
            <li key={r.slot_id} className="effect-line">
              slot {r.pos + 1} stays set aside: {r.reason}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
