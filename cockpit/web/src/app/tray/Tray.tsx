// The set-aside tray: a right-edge column
// beside the canvas holding every set-aside take — off the canvas, still in
// the record, each with its seed and prompt. Searchable, filterable by
// engine, counted. Restore is one press; the lightbox opens from a thumb.
// Purge — the one irreversible verb (law 7) — sits below a hairline,
// red-ruled, hold-to-confirm, with the consequence spelled out; it purges
// exactly what the tray shows, so a filtered tray is a scalpel.
import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { $api } from "../../api/client";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { verbErr } from "../verbs/VerbRow";
import { HoldButton } from "./HoldButton";
import {
  EVERY_ENGINE,
  enginesPresent,
  filterTray,
  formatBytes,
  purgeCopy,
  trayLabel,
} from "./tray";
import s from "./Tray.module.css";

export function Tray({ workspaceId }: { workspaceId: string }) {
  const closeTray = useUi((st) => st.closeTray);
  const openLightbox = useUi((st) => st.openLightbox);
  // The open column is the drag layer's tray destination: lit on lift,
  // solid under the pointer; TrayDropZone stands in while the tray is
  // closed.
  const drag = useUi((st) =>
    st.drag === null
      ? undefined
      : st.drag.over?.kind === "tray"
        ? "over"
        : "target",
  );
  const queryClient = useQueryClient();
  const trayQ = $api.useQuery("get", "/api/workspaces/{wid}/tray", {
    params: { path: { wid: workspaceId } },
  });
  const [query, setQuery] = useState("");
  const [engine, setEngine] = useState(EVERY_ENGINE);
  const [lastPurge, setLastPurge] = useState<string | null>(null);

  const invalidate = useCallback(
    () => invalidateWorkspace(queryClient, workspaceId),
    [queryClient, workspaceId],
  );
  const restoreMut = $api.useMutation("post", "/api/nodes/{nid}/restore", {
    onSettled: invalidate,
  });
  const purgeMut = $api.useMutation(
    "post",
    "/api/workspaces/{wid}/purge",
    { onSettled: invalidate },
  );

  const all = trayQ.data?.nodes ?? [];
  const shown = filterTray(all, query, engine);
  const engines = enginesPresent(all);
  const copy = purgeCopy(shown, all.length);

  // An engine filter whose last node left (restored or purged) would show
  // an empty tray for no visible reason — fall back to every engine.
  useEffect(() => {
    if (engine !== EVERY_ENGINE && !engines.some((e) => e.id === engine))
      setEngine(EVERY_ENGINE);
  }, [engine, engines]);

  const failed = [restoreMut, purgeMut].find((m) => m.isError);

  return (
    <aside
      className={s.tray}
      aria-label="Set-aside tray"
      data-drop="tray"
      data-drag={drag}
    >
      <header className={s.head}>
        <span className="mono-label">SET ASIDE · {all.length}</span>
        <span className={s.spacer} />
        <button
          type="button"
          className={s.headBtn}
          onClick={closeTray}
          title="Close the tray (T)"
        >
          ✕ · T
        </button>
      </header>
      <p className={`effect-line ${s.lede}`}>
        Off the canvas, still in the record — every one keeps its seed and
        prompt.
      </p>

      <div className={s.filters}>
        <input
          type="search"
          className={s.input}
          placeholder="search prompts"
          aria-label="Search set-aside prompts"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className={s.select}
          aria-label="Filter by engine"
          value={engine}
          onChange={(e) => setEngine(e.target.value)}
        >
          <option value={EVERY_ENGINE}>ALL ENGINES · {all.length}</option>
          {engines.map((e) => (
            <option key={e.id} value={e.id}>
              {e.id.toUpperCase()} · {e.count}
            </option>
          ))}
        </select>
      </div>

      {trayQ.isError && (
        <p className="effect-line">
          Couldn't read the tray — the cockpit server didn't answer. Nothing
          was lost; it fills in once the server is back.
        </p>
      )}
      {trayQ.data !== undefined && all.length === 0 && (
        <p className={`effect-line ${s.emptyLine}`}>
          Nothing set aside. ⌫ on a grid cell, or SET ASIDE in the verb row,
          parks a take here — a place, not a delete.
        </p>
      )}
      {all.length > 0 && shown.length === 0 && (
        <p className={`effect-line ${s.emptyLine}`}>
          No set-aside take matches — clear the search to see all{" "}
          {all.length}.
        </p>
      )}

      <ul className={s.list} aria-label={`${shown.length} set-aside takes`}>
        {shown.map((n) => (
          <li key={n.id} className={s.entry} data-node-id={n.id}>
            <button
              type="button"
              className={s.plateBtn}
              aria-label={`Open ${trayLabel(n)} full-res`}
              onClick={() => openLightbox(n.id)}
            >
              {n.payload === "image" ? (
                <img
                  className={s.thumb}
                  src={`/api/nodes/${n.id}/thumb`}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                />
              ) : (
                <span className="mono-micro">MESH</span>
              )}
            </button>
            <span className={`mono-micro ${s.entryLabel}`} title={trayLabel(n)}>
              {trayLabel(n)}
            </span>
            <span className={`mono-micro ${s.entryFacts}`}>
              {n.seed === null ? "NO SEED" : `S ${n.seed}`}
              {n.children > 0 &&
                ` · ${n.children} ${n.children === 1 ? "CHILD" : "CHILDREN"}`}
              {/* Purge empties the tray, and a promoted take has a
                  copy in the asset tree that purge never touches — worth
                  saying here, where the held delete lives. */}
              {n.promoted_at !== null && (
                <span className={s.entryPromoted}> · PROMOTED</span>
              )}
            </span>
            {n.prompt !== null && n.prompt !== "" && (
              <span className={s.entryPrompt} title={n.prompt}>
                {n.prompt}
              </span>
            )}
            <button
              type="button"
              className={`mono-micro ${s.restore}`}
              onClick={() =>
                restoreMut.mutate({ params: { path: { nid: n.id } } })
              }
            >
              RESTORE
            </button>
          </li>
        ))}
      </ul>

      <hr className="hairline-rule" />
      <footer className={s.foot}>
        <span className={`mono-micro ${s.irreversible}`}>
          THE ONE IRREVERSIBLE VERB
        </span>
        <HoldButton
          label={copy.label}
          ariaLabel="Hold to purge the set-aside takes shown"
          disabled={shown.length === 0 || purgeMut.isPending}
          onHold={() =>
            purgeMut.mutate(
              {
                params: { path: { wid: workspaceId } },
                body: { ids: shown.map((n) => n.id) },
              },
              {
                onSuccess: (r) =>
                  setLastPurge(
                    `Purged ${r.purged} · freed ${formatBytes(r.freed_bytes)}`,
                  ),
              },
            )
          }
        />
        <span className="effect-line">{copy.consequence}</span>
        {lastPurge !== null && (
          <span className={`mono-micro ${s.lastPurge}`}>{lastPurge}</span>
        )}
        {failed !== undefined && (
          <p className={s.err}>{verbErr(failed.error)}</p>
        )}
      </footer>
    </aside>
  );
}
