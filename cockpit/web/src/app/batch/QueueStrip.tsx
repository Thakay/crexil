// The live 124px queue strip.
// Global truth, not per-workspace: one GPU means SOMETHING running anywhere
// occupies the local lane, and the strip says so in the server's own words
// (labels are server-sent — law 13 copy lives in api_v4, not here).
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type { SlotView } from "../../types";
import { useUi } from "../state/ui";
import { benchLabel, elapsedLabel, passSuffix } from "./grid";
import s from "./Batch.module.css";

function slotLine(v: SlotView): string {
  const eng = v.engine.toUpperCase();
  const turn = `T${v.turn_idx}`;
  // law 9: a running retry says so in the strip too; a region
  // take's pipeline position rides the same line
  if (v.status === "running")
    return `${eng} · ${turn} · ${
      v.elapsed_ms !== null ? elapsedLabel(v.elapsed_ms) : "RUNNING"
    }${passSuffix(v.passes)}${v.retry_of !== null ? " · RETRY" : ""}${v.cancel_requested ? " · CANCELLING" : ""}`;
  return `${eng} · ${turn}${v.position !== null ? ` · #${v.position}` : ""}`;
}

export function QueueStrip() {
  const queryClient = useQueryClient();
  const toggleGrid = useUi((st) => st.toggleGrid);
  const q = $api.useQuery(
    "get",
    "/api/queue",
    {},
    { refetchInterval: 2000 },
  );
  const invalidateQueue = () =>
    void queryClient.invalidateQueries({
      queryKey: $api.queryOptions("get", "/api/queue").queryKey,
    });
  const cancelMut = $api.useMutation("post", "/api/slots/{slot_id}/cancel", {
    onSettled: invalidateQueue,
  });
  const unbenchMut = $api.useMutation("post", "/api/unbench", {
    onSettled: invalidateQueue,
  });
  // C2: a card held by something else. The strip carries the
  // artist's look-again beside the lane's usual UNBENCH
  const gpuCheckMut = $api.useMutation("post", "/api/gpu/check", {
    onSettled: invalidateQueue,
  });
  // G is also the mesh viewer's grid key: the lightbox holds it
  // while it is up, the way it holds N and B.
  const lightboxUp = useUi((st) => st.lightboxNodeId !== null);
  useHotkeys("g", toggleGrid, { enabled: !lightboxUp });

  const d = q.data;
  const local = d?.lanes.local;
  const external = d?.lanes.external;
  const running = local?.slots.find((v) => v.status === "running");
  const queuedBehind = local ? local.slots.filter((v) => v.status === "queued").length : 0;
  const busy =
    d !== undefined &&
    (d.depth > 0 ||
      (external?.slots.length ?? 0) > 0 ||
      d.held.length > 0 ||
      d.late.length > 0 ||
      d.benches.length > 0);

  return (
    <div className={s.strip}>
      <div className={s.stripHead}>
        <p className="mono-label">Queue</p>
        <span className={s.spacer} />
        <button
          type="button"
          className={s.headBtn}
          onClick={toggleGrid}
          title="Toggle the turn grid (G)"
        >
          TURN GRID · G
        </button>
      </div>
      {d === undefined ? (
        <p className="effect-line">
          {q.isError
            ? "The cockpit server isn't answering — the queue is unknown, not empty."
            : "Loading the queue."}
        </p>
      ) : !busy ? (
        <p className="effect-line">
          Nothing running. One GPU — local takes run one at a time; external
          takes run alongside.
        </p>
      ) : (
        <div className={s.stripLanes}>
          <div className={s.lane}>
            <span className={`mono-micro ${s.laneLabel}`}>
              {local?.label}
            </span>
            {running !== undefined ? (
              <span className={s.laneRow}>
                <span className={`mono-label ${s.gold}`}>
                  ▸ {slotLine(running)}
                </span>
                <button
                  type="button"
                  className={s.cellCancelInline}
                  aria-label="Cancel the running local take"
                  title="frees the GPU — nothing was spent"
                  onClick={() =>
                    cancelMut.mutate({
                      params: { path: { slot_id: running.id } },
                    })
                  }
                >
                  ✕
                </button>
              </span>
            ) : (
              <span className="effect-line">GPU idle</span>
            )}
            {queuedBehind > 0 && (
              <span className="effect-line">
                {queuedBehind} queued behind — locals take turns
              </span>
            )}
          </div>
          <div className={s.lane}>
            <span className={`mono-micro ${s.laneLabel}`}>
              {external?.label}
            </span>
            <span className="effect-line">
              {external !== undefined && external.slots.length > 0
                ? `${external.active} in flight of ${external.slots.length}`
                : "none in flight"}
            </span>
          </div>
          <div className={s.lane}>
            {d.held.length > 0 && (
              <span className={`mono-label ${s.gold}`}>
                {d.held.length} HELD
              </span>
            )}
            {d.late.length > 0 && (
              <span className={`mono-label ${s.gold}`}>
                {d.late.length} LATE · ALREADY PAID
              </span>
            )}
            {d.benches.map((b) => (
              <span key={b.scope} className={s.laneRow} data-bench={b.kind}>
                <span
                  className={`mono-label ${b.kind === "gpu_held" ? s.gold : s.red}`}
                  title={b.cause}
                >
                  {benchLabel(b)}
                </span>
                {b.kind === "gpu_held" && (
                  <button
                    type="button"
                    className={s.headBtn}
                    disabled={gpuCheckMut.isPending}
                    onClick={() => gpuCheckMut.mutate({})}
                  >
                    CHECK AGAIN
                  </button>
                )}
                <button
                  type="button"
                  className={s.headBtn}
                  onClick={() =>
                    unbenchMut.mutate({ body: { scope: b.scope } })
                  }
                >
                  {b.kind === "repeat" ? "TRY ONCE MORE" : "UNBENCH"}
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
