// The verb row: the dock's targeting strip.
// Selection is the entire targeting state: click retargets this row,
// shift-click extends the selection, Esc drops it back to root verbs. There
// is no per-node hover toolbar (law 11) — at turn 30 a node is ~90px wide.
//
// The row never filters (law 12). A verb the cockpit can't fire yet
// stays in the main row, dashed, with the reason where its effect line was;
// a verb for a different payload drops to NOT FOR THIS NODE. Which verbs are
// live is decided in verbs.ts, not here — this file only draws and dispatches.
//
// The verbs that cannot be un-happened sit BELOW
// a hairline, red-ruled — PROMOTE, which opens its plinth here, and PURGE,
// which points at the tray where it is held. Everything above the line is
// something a second click undoes.
//
// ⌫ is SET ASIDE's keystroke on the canvas (every drag destination also
// has one: the tray's is this). It fires exactly what the chip fires,
// under the chip's gate: a live selection of any size, and no overlay
// holding the keys (the grid's hovered-cell ⌫, the lightbox, the palette).
import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import { invalidateWorkspace } from "../invalidate";
import { DecimatePlinth } from "../mesh/DecimatePlinth";
import { MeshPlinth } from "../mesh/MeshPlinth";
import { TexturePlinth } from "../mesh/TexturePlinth";
import { PromotePlinth } from "../promote/PromotePlinth";
import { Record } from "../record/Record";
import { recallPrompt } from "../record/record";
import { useUi } from "../state/ui";
import { resolveRow, type Chip, type VerbId } from "./verbs";
import s from "./VerbRow.module.css";

// The server's own refusals are already plain words with the cost stated
// ("the old take stays and nothing was spent") — pass them through verbatim
// rather than paraphrasing. The fallback carries the same two facts (law 3:
// no status codes; law 5: say what it cost). Shared with every surface
// that fires a verb (the tray, the grid).
export function verbErr(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  return "That verb didn't go through — nothing changed and nothing was spent.";
}

function VerbChip({
  chip,
  onFire,
  danger = false,
  open = false,
}: {
  chip: Chip;
  onFire: (id: VerbId) => void;
  danger?: boolean;
  open?: boolean;
}) {
  return (
    <button
      type="button"
      className={[
        s.chip,
        chip.live ? "" : s.chipBlocked,
        danger ? s.chipDanger : "",
        open ? s.chipOpen : "",
      ]
        .filter(Boolean)
        .join(" ")}
      // Not `disabled`: a blocked verb stays selectable and focusable so the
      // keyboard reaches its reason too (law 12 says visible AND selectable).
      aria-disabled={!chip.live}
      data-verb={chip.id}
      onClick={() => chip.live && onFire(chip.id)}
    >
      <span className={`mono-label ${s.chipLabel}`}>
        {chip.label}
        {chip.key !== undefined && (
          <span className={s.chipKey}>{chip.key}</span>
        )}
      </span>
      <span className={s.chipLine}>{chip.line}</span>
    </button>
  );
}

export function VerbRow({ workspaceId }: { workspaceId: string }) {
  const selection = useUi((st) => st.selection);
  const wantArm = useUi((st) => st.wantArm);
  const openTray = useUi((st) => st.openTray);
  const openRegions = useUi((st) => st.openRegions);
  const openLightbox = useUi((st) => st.openLightbox);
  const promoteNodeId = useUi((st) => st.promoteNodeId);
  const openPromote = useUi((st) => st.openPromote);
  const closePromote = useUi((st) => st.closePromote);
  const meshNodeId = useUi((st) => st.meshNodeId);
  const openMesh = useUi((st) => st.openMesh);
  const closeMesh = useUi((st) => st.closeMesh);
  const decimateNodeId = useUi((st) => st.decimateNodeId);
  const openDecimate = useUi((st) => st.openDecimate);
  const closeDecimate = useUi((st) => st.closeDecimate);
  const textureNodeId = useUi((st) => st.textureNodeId);
  const openTexture = useUi((st) => st.openTexture);
  const closeTexture = useUi((st) => st.closeTexture);
  const overlayUp = useUi(
    (st) =>
      st.gridOpen ||
      st.lightboxNodeId !== null ||
      st.summonAt !== null ||
      st.regionsNodeId !== null,
  );
  const queryClient = useQueryClient();

  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  // The turns, for the record's recipe: the detail the shell
  // already holds; this shares the cache entry, no second fetch.
  const wsQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid: workspaceId } },
  });

  const invalidateAll = useCallback(
    () => invalidateWorkspace(queryClient, workspaceId),
    [queryClient, workspaceId],
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

  const nodes = nodesQ.data ?? [];
  const row = resolveRow(selection, nodes);
  const one = row.target?.nodes[0];
  // The selection's turn when it has ONE — the record says so in words
  // when several are picked across turns, rather than showing the first's.
  const turnIds = new Set((row.target?.nodes ?? []).map((n) => n.turn_id));
  const turn =
    turnIds.size === 1 && one?.turn_id != null
      ? wsQ.data?.turns.find((t) => t.id === one.turn_id)
      : undefined;

  const fire = (id: VerbId) => {
    // Generative verbs arm the composer rather than firing a route: a run
    // still needs a prompt, an engine and a take count, whether it starts
    // from text (root) or from this take (refine).
    if (id === "new-root") return wantArm([]);
    if (id === "purge") return openTray(); // held there, never here
    if (one === undefined) return;
    if (id === "refine") return wantArm([one.id]);
    // EDIT REGION: the mask surface on this take, over the canvas
    // region — a place, not a modal (law 2).
    if (id === "edit-region") return openRegions(one.id);
    // INSPECT: the lightbox, the surface the double-click and
    // the grid cell already reach, given a visible door and a key. A mesh
    // gets the viewer, an image its full-res take.
    if (id === "inspect") return openLightbox(one.id);
    // PROMOTE opens the plinth rather than firing: the
    // consequence is stated first, then held. Pressing it again closes.
    if (id === "promote")
      return promoteNodeId === one.id ? closePromote() : openPromote(one.id);
    // VARY ONE DIAL: the same refine-shaped run on this take, with
    // the sweep on. ANCHOR HOLD is the dial a source image gives meaning
    // to, so it is the one lit first; the composer lets it be changed.
    if (id === "vary-dial") return wantArm([one.id], "anchor-hold");
    // TO 3D: the mesh roster on this take, in the dock. It opens
    // rather than fires for the composer's reason — a run needs an engine
    // and a take count — and pressing it again closes it, like PROMOTE.
    if (id === "to-3d")
      return meshNodeId === one.id ? closeMesh() : openMesh(one.id);
    // DECIMATE (law 16): the same seam on a mesh take. It opens for a
    // sharper reason than TO 3D — this verb has a handle, and the triangle
    // count is a request the mesh may refuse, which takes words to say.
    if (id === "decimate")
      return decimateNodeId === one.id
        ? closeDecimate()
        : openDecimate(one.id);
    // TEXTURE (law 16): the two-parent verb. The plinth carries the
    // reference picker and the 40k honesty, so it opens rather than fires.
    if (id === "texture")
      return textureNodeId === one.id
        ? closeTexture()
        : openTexture(one.id);
    if (id === "reroll" && one.slot_id !== null)
      rerollMut.mutate({ params: { path: { slot_id: one.slot_id } } });
    if (id === "set-aside")
      // The whole selection: one request per node. The route is
      // idempotent and a refusal is per node, never per batch.
      for (const n of row.target?.nodes ?? [])
        asideMut.mutate({ params: { path: { nid: n.id } } });
    if (id === "restore")
      restoreMut.mutate({ params: { path: { nid: one.id } } });
  };

  const asideLive = row.chips.some((c) => c.id === "set-aside" && c.live);
  useHotkeys(
    ["backspace", "delete"],
    () => fire("set-aside"),
    { enabled: asideLive && !overlayUp, preventDefault: true },
    [asideLive, overlayUp, selection],
  );

  // V opens the lightbox on the one selected take: the canvas node
  // is a React Flow node with a double-click handler and nothing else, so
  // this key is the keyboard path for a mesh on the canvas (where a mesh
  // actually lives) to the inspection that precedes an irreversible
  // promote. Same gate as the chip, and never while an overlay is up.
  const inspectLive = row.chips.some((c) => c.id === "inspect" && c.live);
  useHotkeys(
    "v",
    () => fire("inspect"),
    { enabled: inspectLive && !overlayUp },
    [inspectLive, overlayUp, selection],
  );

  // P recalls the words: a NEW root arm carrying them, the same door as
  // the failed paid cell's free remedy, under the chip's gate and never
  // while an overlay owns the keys. Several nodes with different words
  // recall none.
  const recall = recallPrompt(turn, row.target?.nodes ?? []);
  useHotkeys(
    "p",
    () => {
      if (recall !== null) wantArm([], null, recall);
    },
    // preventDefault: the arm focuses the prompt in the same tick, and the
    // key would otherwise land in it as a letter
    { enabled: recall !== null && !overlayUp, preventDefault: true },
    [recall, overlayUp],
  );

  const failed = [rerollMut, asideMut, restoreMut].find((m) => m.isError);
  // The reason the whole dashed group shares, if it is one reason.
  const shared =
    row.notForThis.length > 1 &&
    row.notForThis.every((c) => c.line === row.notForThis[0].line)
      ? row.notForThis[0].line
      : null;

  return (
    <section className={s.row} aria-label="Verbs">
      <div className={s.head}>
        {row.target === null ? (
          <span className={`mono-label ${s.nothing}`}>ACTING ON NOTHING</span>
        ) : (
          <span className={s.acting}>
            <span className={`mono-micro ${s.actingHead}`}>ACTING ON</span>
            <span className={s.actingName}>{row.target.label}</span>
          </span>
        )}
        {row.target !== null && (
          <span className={`mono-micro ${s.facts}`}>{row.target.facts}</span>
        )}
        <span className={s.spacer} />
        <span className={`mono-micro ${s.hint}`}>
          {row.target === null
            ? "CLICK A NODE TO ACT ON IT · ⇧DRAG SWEEPS MANY · DRAG ONE TO SEE WHERE IT CAN GO"
            : "⌫ SETS ASIDE · ⇧CLICK OR ⇧DRAG SELECTS MORE · ESC ACTS ON NOTHING"}
        </span>
      </div>

      {/* The record under the head: what the selection was
          made from, the turn's recipe said as the turn's, this take's own
          facts. Three nodes selected get the plural answer, an imported
          node the honest blank, in the facts line's own register. */}
      {row.target !== null && (
        <Record
          scope="take"
          turn={turn}
          nodes={row.target.nodes}
          all={nodes}
          onUsePrompt={(p) => wantArm([], null, p)}
        />
      )}

      <div className={s.chips}>
        {row.chips.map((c) => (
          <VerbChip
            key={c.id}
            chip={c}
            onFire={fire}
            open={
              (c.id === "to-3d" && meshNodeId === one?.id) ||
              (c.id === "decimate" && decimateNodeId === one?.id) ||
              (c.id === "texture" && textureNodeId === one?.id)
            }
          />
        ))}
      </div>

      {/* Below the hairline: the verbs a second click cannot undo.
          The line is drawn even when both are blocked — its job is to
          teach where the boundary is, not to appear only when crossed. */}
      {row.irreversible.length > 0 && (
        <div className={s.irreversible}>
          <div className={s.irreversibleHead}>
            <span className={s.hairline} />
            <span className={`mono-micro ${s.irreversibleLabel}`}>
              CAN&apos;T BE UNDONE
            </span>
            <span className={s.hairline} />
          </div>
          <div className={s.chips}>
            {row.irreversible.map((c) => (
              <VerbChip
                key={c.id}
                chip={c}
                onFire={fire}
                danger
                open={c.id === "promote" && promoteNodeId === one?.id}
              />
            ))}
          </div>
        </div>
      )}

      {/* The To-3D plinth mounts ABOVE the hairline: a mesh run
          spends GPU minutes and makes nodes a second click sets aside, so
          it is not one of the irreversible two. */}
      {meshNodeId !== null && (
        <MeshPlinth nodeId={meshNodeId} workspaceId={workspaceId} />
      )}

      {/* The mesh verb's plinth, on the same shelf and for the same
          reason: a decimate makes a node a second click sets aside, so it
          is not one of the irreversible two either. The store keeps the
          two plinths exclusive; this only draws whichever is open. */}
      {decimateNodeId !== null && (
        <DecimatePlinth nodeId={decimateNodeId} workspaceId={workspaceId} />
      )}

      {/* The texture verb's plinth: same shelf, same reason, and
          the store keeps all of them exclusive. */}
      {textureNodeId !== null && (
        <TexturePlinth nodeId={textureNodeId} workspaceId={workspaceId} />
      )}

      {promoteNodeId !== null && (
        <PromotePlinth nodeId={promoteNodeId} workspaceId={workspaceId} />
      )}

      {row.notForThis.length > 0 && (
        <div className={s.notFor}>
          <span className={`mono-micro ${s.notForHead}`}>
            NOT FOR THIS NODE
          </span>
          {/* One cause, one message (law 1): five chips refused for the same
              reason state it once here, not five times over. A chip with its
              own reason still carries it inline. */}
          {shared !== null && <span className={s.notForWhy}>{shared}</span>}
          <div className={s.notForList}>
            {row.notForThis.map((c) => (
              <span key={c.id} className={s.notForChip} data-verb={c.id}>
                <span className={`mono-micro ${s.notForLabel}`}>{c.label}</span>
                {shared === null && (
                  <span className={s.notForWhy}>{c.line}</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {failed !== undefined && <p className={s.err}>{verbErr(failed.error)}</p>}
    </section>
  );
}
