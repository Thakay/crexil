// Workstation shell: top bar 44px · collapsible sidebar (mod+\) · canvas
// (React Flow, src/app/canvas/) · verb row (src/app/verbs/) · docked
// composer (armed by summon, src/app/composer/) · 124px queue strip
// (src/app/batch/) · set-aside tray (src/app/tray/) as a right-edge column,
// `T`, with its drop zone over the canvas edge while a card is lifted. The
// turn grid overlays the canvas region while open (the canvas stays mounted
// beneath it as the workspace's live feed), and the region surface
// (src/app/regions/) overlays the same place, the two mutually exclusive.
// The six dials, the steppers and the run bar ride the armed composer, and
// the top bar's right corner carries the money (Money.tsx): the LOCAL ONLY
// switch and the charged totals, on every screen. The three seams (sidebar |
// canvas, canvas | tray, canvas / dock) carry a resize handle
// (ResizeHandle.tsx): the sizes are custom properties on this root,
// persisted app-level in the browser (shell/layout.ts says why).
import { useEffect, useRef, type CSSProperties } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type { WorkspaceDetail } from "../../types";
import { Lightbox } from "../batch/Lightbox";
import { QueueStrip } from "../batch/QueueStrip";
import { TurnGrid } from "../batch/TurnGrid";
import { turnActive } from "../batch/grid";
import { Canvas } from "../canvas/Canvas";
import { Composer } from "../composer/Composer";
import { RegionSurface } from "../regions/RegionSurface";
import { useRegions } from "../regions/store";
import { useUi } from "../state/ui";
import { Tray } from "../tray/Tray";
import { TrayDropZone } from "../tray/TrayDropZone";
import { VerbRow } from "../verbs/VerbRow";
import { layoutVars } from "./layout";
import { ChargedTotals, LocalOnlySwitch } from "./Money";
import { ResizeHandle } from "./ResizeHandle";
import { Sidebar } from "./Sidebar";
import styles from "./Shell.module.css";

// The grid surfaces itself the moment a new turn exists (the composer's
// Run included), and on open if a batch is mid-air.
function useAutoGrid(
  workspaceId: string | null,
  w: WorkspaceDetail | undefined,
) {
  const openGrid = useUi((s) => s.openGrid);
  const closeGrid = useUi((s) => s.closeGrid);
  const viewTurn = useUi((s) => s.viewTurn);
  const closeSummon = useUi((s) => s.closeSummon);
  const closeRegions = useUi((s) => s.closeRegions);
  const disarm = useUi((s) => s.disarm);
  const prevTurns = useRef<number | null>(null);

  useEffect(() => {
    prevTurns.current = null; // a different workspace is a different story
    closeGrid();
    viewTurn(null);
    closeSummon(); // a palette or armed composer aims at the OLD canvas
    closeRegions(); // so does an open region surface
    disarm();
  }, [workspaceId, closeGrid, viewTurn, closeSummon, closeRegions, disarm]);

  useEffect(() => {
    if (w === undefined) return;
    const prev = prevTurns.current;
    prevTurns.current = w.turns.length;
    const last = w.turns[w.turns.length - 1];
    // A fan out launched FROM the open region surface keeps the
    // surface: the attempt panes are that turn's grid. Every other new
    // turn still hands the canvas region to the grid.
    const ownFan = () => {
      const nid = useUi.getState().regionsNodeId;
      if (nid === null || last === undefined) return false;
      const rs = useRegions.getState();
      return rs.pendingFan === nid || rs.fanTurn[nid] === last.id;
    };
    if (prev === null) {
      if (last !== undefined && turnActive(last.slots) && !ownFan()) openGrid();
    } else if (w.turns.length > prev && !ownFan()) {
      openGrid();
    }
  }, [w, openGrid]);
}

export function Shell({ workspaceId }: { workspaceId: string | null }) {
  // Selectors, not destructuring: the store also carries canvas
  // selection, and a node click must not re-render the whole shell tree.
  const sidebarOpen = useUi((s) => s.sidebarOpen);
  const toggleSidebar = useUi((s) => s.toggleSidebar);
  const gridOpen = useUi((s) => s.gridOpen);
  const regionsNodeId = useUi((s) => s.regionsNodeId);
  const trayOpen = useUi((s) => s.trayOpen);
  const toggleTray = useUi((s) => s.toggleTray);
  const armed = useUi((s) => s.armed);
  // The committed island sizes. A drag in flight writes the same
  // properties on this element directly and never touches the store.
  const layout = useUi((s) => s.layout);
  const shellRef = useRef<HTMLDivElement>(null);
  useHotkeys(["mod+backslash", "mod+\\"], toggleSidebar);
  useHotkeys("t", toggleTray, { enabled: workspaceId !== null });

  const detail = $api.useQuery(
    "get",
    "/api/workspaces/{wid}",
    { params: { path: { wid: workspaceId ?? "" } } },
    { enabled: workspaceId !== null },
  );
  const w = workspaceId !== null ? detail.data : undefined;
  useAutoGrid(workspaceId, w);
  // The tray's count rides the node feed the canvas already holds (one
  // query, deduped) — no second fetch for a number in the top bar.
  const nodes = $api.useQuery(
    "get",
    "/api/workspaces/{wid}/nodes",
    { params: { path: { wid: workspaceId ?? "" } } },
    { enabled: workspaceId !== null },
  );
  const asideCount = (nodes.data ?? []).filter((n) => n.set_aside).length;
  // A node exists where a generation landed, or was set aside — a place,
  // not a delete. Read off the same feed rather than re-derived from
  // slot statuses, which would silently undercount imports (they have no
  // slot: they are plain parents) and disagree with the sidebar row's count.
  const nodeCount = (nodes.data ?? []).length;

  return (
    <div
      ref={shellRef}
      className={styles.shell}
      style={layoutVars(layout) as CSSProperties}
    >
      <header className={styles.topbar}>
        <button
          type="button"
          className={styles.toggle}
          onClick={toggleSidebar}
          aria-label="Toggle sidebar"
          aria-pressed={sidebarOpen}
          title="Toggle sidebar (mod+\)"
        >
          {sidebarOpen ? "◂" : "▸"}
        </button>
        <h1 className={styles.assetName}>{w ? w.name : "Crexil"}</h1>
        {w && (
          <span className={`mono-label ${styles.turnReadout}`}>
            turn {w.turns.length} · {nodeCount} nodes
          </span>
        )}
        <span className={styles.spacer} />
        {w && (
          // The right-aligned readout: where the set-aside
          // takes went, and the way to them — a place, not a delete.
          <button
            type="button"
            className={`mono-label ${styles.trayToggle}`}
            onClick={toggleTray}
            aria-pressed={trayOpen}
            title="Set-aside tray (T)"
          >
            SET ASIDE · {asideCount}
          </button>
        )}
        <LocalOnlySwitch />
        <ChargedTotals />
      </header>

      <div className={styles.body}>
        {sidebarOpen && (
          <>
            <Sidebar activeId={workspaceId} />
            <ResizeHandle island="sidebar" shell={shellRef} label="Sidebar width" />
          </>
        )}

        <div
          className={
            w ? styles.canvas : `${styles.canvas} ${styles.idle}`
          }
        >
          {workspaceId === null ? (
            <p className={`effect-line ${styles.canvasHint}`}>
              Pick a workspace in the sidebar — or name a new one.
            </p>
          ) : detail.isPending ? (
            <p className={`mono-label ${styles.canvasHint}`}>Loading</p>
          ) : detail.isError || !w ? (
            <p className={`effect-line ${styles.canvasHint}`}>
              No workspace answers to this address — it may have been purged,
              or the cockpit server isn't running.
            </p>
          ) : (
            <>
              <Canvas workspace={w} />
              {gridOpen && <TurnGrid workspace={w} />}
              {regionsNodeId !== null && (
                <RegionSurface workspaceId={w.id} nodeId={regionsNodeId} />
              )}
              <TrayDropZone />
            </>
          )}
        </div>

        {w && trayOpen && (
          <>
            <ResizeHandle island="tray" shell={shellRef} label="Tray width" />
            <Tray workspaceId={w.id} />
          </>
        )}
      </div>

      {workspaceId !== null && (
        <>
          <ResizeHandle island="dock" shell={shellRef} label="Dock height" />
          {/* The dock: the verb row, the composer and the queue strip
              under one cap. The verb row bands above the composer: selection
              is the whole targeting state, so it stays up whether or
              not a run is being composed. */}
          <div className={styles.dock} role="group" aria-label="Dock">
            <VerbRow workspaceId={workspaceId} />
            <div className={styles.composer}>
              {armed !== null ? (
                <Composer workspaceId={workspaceId} />
              ) : (
                <div className={styles.composerLeft}>
                  <p className="mono-label">Composer</p>
                  <p className="effect-line">
                    Press N — or right-click the canvas — to summon. The node
                    is born where the palette opens; the six dials dock here
                    with the armed run.
                  </p>
                </div>
              )}
            </div>
            <QueueStrip />
          </div>
        </>
      )}
      {workspaceId !== null && <Lightbox wid={workspaceId} />}
    </div>
  );
}
