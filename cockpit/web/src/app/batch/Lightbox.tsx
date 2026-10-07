// Full-res lightbox: the ONLY surface that loads a node's full artefact —
// cells and cards stay thumbs. Opens from a landed grid cell click, a
// canvas card double-click, a tray thumbnail, and the INSPECT verb / V.
//
// An IMAGE is the full-res take: click anywhere or Esc closes. Its caption
// is the provenance record BESIDE the pixels, never over them — the whole
// record at the moment of the decision, before an irreversible promote.
// Its recall closes the box first; the composer is under it.
//
// A MESH is the viewer: a real GLB view of the artefact itself, loaded
// through the same immutable-cached `/image` the GLB always downloaded
// from — one fetch per node, ever, no preview GLB. Everything that touches
// three lives in `MeshViewer.tsx`, a lazy chunk, so the main bundle is
// untouched; this file is the STAGE shell: the artefact edge to edge, chrome
// floating in the corners, one mode row of four groups (camera · surface ·
// truth · overlay) above the facts line `meshFactsLine` prints. While
// the GLB streams, the engine's own turntable orbit plays as the poster
// and hands over on arrival, the controls dimmed until then — the orbit IS
// what is being waited for, and it is already cached. Esc or the backdrop
// closes; a click in the frame never does, because the frame drags to
// orbit. Under it, what the funnel measured: a turntable at a fixed
// elevation hides exactly what an artist needs before an irreversible
// promote — open surfaces, backside collapse, holes in the underside.
//
// The pure halves are here so vitest reaches them without WebGL:
// `meshFactsLine`, `nextView` (the mode state), `truthLine` (curated
// static text picked by a lookup, never generated), `loadFraction`,
// `viewerHeader`.
import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHotkeys } from "react-hotkeys-hook";
import { $api } from "../../api/client";
import type { Node } from "../../types";
import { invalidateWorkspace } from "../invalidate";
// The facts line lives in mesh/facts.ts: the decimate plinth
// prints the same sentence for its parent, and one home per fact.
import { meshFactsLine } from "../mesh/facts";
import { Record } from "../record/Record";
import { useUi } from "../state/ui";
import { targetLabel } from "../verbs/verbs";
import { verbErr } from "../verbs/VerbRow";
import s from "./Batch.module.css";

// three, R3F and drei enter the bundle only through this seam.
const MeshStage = lazy(() => import("./MeshViewer"));

// ── the view state (pure) ────────────────────────────────────────────────
// The modes: two camera presets, an exclusive surface pair, and four
// toggles. Modes are VIEW state: they never touch
// the artefact. The stage applies them to the loaded scene.

export type ModeId =
  | "reset"
  | "below"
  | "clay"
  | "cull"
  | "normals"
  | "wireframe"
  | "grid";

export type View = {
  surface: "textured" | "clay";
  /** BACKFACE CULL — off: backfaces drawn, a shell looks closed; on: a face
   *  seen from behind is not drawn, so an open surface reads as a hole. */
  cull: boolean;
  /** Facing as colour; replaces the surface while on. */
  normals: boolean;
  wireframe: boolean;
  grid: boolean;
  /** A camera preset the stage applies once — `presetSeq` is what makes the
   *  same preset asked twice apply twice. */
  preset: "reset" | "below" | null;
  presetSeq: number;
  /** The last mode touched: the truth line explains that one while it is on. */
  last: ModeId | null;
};

/** The opening view: textured, backfaces drawn, grid on. */
export const INITIAL_VIEW: View = {
  surface: "textured",
  cull: false,
  normals: false,
  wireframe: false,
  grid: true,
  preset: null,
  presetSeq: 0,
  last: null,
};

/** Every mode wears its keyboard letter. */
export const MODE_KEYS: Record<ModeId, string> = {
  reset: "0",
  below: "U",
  clay: "C",
  cull: "B",
  normals: "N",
  wireframe: "W",
  grid: "G",
};

export function nextView(v: View, mode: ModeId): View {
  switch (mode) {
    case "clay":
      return {
        ...v,
        surface: v.surface === "clay" ? "textured" : "clay",
        last: mode,
      };
    case "cull":
      return { ...v, cull: !v.cull, last: mode };
    case "normals":
      return { ...v, normals: !v.normals, last: mode };
    case "wireframe":
      return { ...v, wireframe: !v.wireframe, last: mode };
    case "grid":
      return { ...v, grid: !v.grid, last: mode };
    case "below":
    case "reset":
      return { ...v, preset: mode, presetSeq: v.presetSeq + 1, last: mode };
  }
}

/** What the stage reports back: the bytes on the way, or how it ended. */
export type LoadStatus =
  | { status: "loading"; loaded: number; total: number }
  | { status: "ready" }
  | { status: "empty" }
  | { status: "failed"; error: string };

/** The poster bar's fraction: Content-Length when the server sent one, the
 *  record's own size when it didn't, nothing when neither is known. */
export function loadFraction(
  loaded: number,
  total: number,
  fileMb: number | null | undefined,
): number | null {
  const bytes =
    total > 0 ? total : fileMb != null && fileMb > 0 ? fileMb * 1_000_000 : 0;
  if (bytes <= 0) return null;
  return Math.max(0, Math.min(1, loaded / bytes));
}

export function loadingLine(node: Node): string {
  const mb = node.mesh?.file_mb;
  return mb != null ? `LOADING THE MESH · ${mb} MB` : "LOADING THE MESH";
}

/** The top-left corner: which take, and what it is and came from. */
export function viewerHeader(
  node: Node,
  nodes: Node[],
): { take: string; facts: string } {
  const parent = nodes.find((n) => n.id === node.parents[0]);
  const parts = ["3D"];
  if (node.mesh?.engine) parts.push(node.mesh.engine.toUpperCase());
  if (parent !== undefined) parts.push(`FROM ${targetLabel([parent])}`);
  return { take: targetLabel([node]), facts: parts.join(" · ") };
}

const FULL_DETAIL = "This is the original file, drawn at full detail.";

/** The line under the facts: what this view shows, in plain words. The last
 *  mode touched is the one explained while it is on; the camera's actual
 *  side decides the underside lines, so a chip lit by a drag is never
 *  claimed. A record that doesn't say watertight is not judged (law 12).
 *  Shared by the project's inspection (crexil/MeshInspection.tsx). */
export function truthLine(node: Node, v: View, below: boolean): string {
  const open = node.mesh?.watertight;
  switch (v.last) {
    case "normals":
      if (v.normals)
        return "Facing as colour. Flipped and inverted faces read instantly, before they surprise a DCC import.";
      break;
    case "wireframe":
      if (v.wireframe)
        return "Topology over the surface. Density, stretched triangles, and where the generator gave up.";
      break;
    case "grid":
      if (v.grid)
        return "Ground and scale. Whether it is sitting on the floor, and how big it thinks it is.";
      break;
    case "clay":
      if (v.surface === "clay")
        return "Clay drops the baked texture. What is left is form: the lumps and the flat spots the texture was covering.";
      break;
    default:
      break;
  }
  if (below && v.cull)
    return open === false
      ? "From underneath with backfaces culled: the hole is where the shell never closed. A turntable at one elevation could not have told you."
      : "From underneath with backfaces culled: an open surface would show as a hole here. A turntable at one elevation could not tell you either way.";
  if (below)
    return "Underneath with backfaces drawn: a shell looks closed even where it isn't. BACKFACE CULL (B) shows the holes.";
  if (v.cull)
    return "Backfaces culled: a face seen from behind is not drawn, so an open surface reads as a hole. FROM BELOW (U) is where they hide.";
  if (open === false)
    return `Open surfaces: cull backfaces and orbit underneath to see where they are. ${FULL_DETAIL}`;
  if (open === true)
    return `Watertight: the mesh has no open surface. ${FULL_DETAIL}`;
  return FULL_DETAIL;
}

// ── the surface ──────────────────────────────────────────────────────────

const POSTER_LINE =
  "The engine's own orbit, turning while the artefact streams. It hands over to the live view the moment the mesh is ready.";
const TURNTABLE_LINE =
  "The engine's own render: one elevation, thirty frames.";
const NO_WEBGL2_LINE =
  "This browser has no WebGL2, so the viewer can't draw; the engine's own orbit stands in. Nothing about the take changed.";
const NO_WEBGL2_KEY = "cockpit.mesh.noWebgl2Said";

// Asked once, kept for the session: a context that can't be made is the
// no-WebGL2 state, said plainly once and then simply the fallback.
let webgl2Known: boolean | null = null;
function webgl2(): boolean {
  if (webgl2Known === null) {
    try {
      webgl2Known = document.createElement("canvas").getContext("webgl2") !== null;
    } catch {
      webgl2Known = false;
    }
  }
  return webgl2Known;
}

// A renderer that dies mid-flight (a context that can't be created after
// all, a shader that won't compile) becomes the failed state rather than
// a blank frame: the turntable is the fallback for every dead case.
export class StageBoundary extends Component<
  { onFail: (error: string) => void; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(err: unknown) {
    this.props.onFail(err instanceof Error ? err.message : String(err));
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function ModeChip({
  id,
  label,
  on,
  live,
  seg = false,
  onPress,
}: {
  id: ModeId | "textured";
  label: string;
  on?: boolean;
  live: boolean;
  seg?: boolean;
  onPress: () => void;
}) {
  const cls = seg
    ? `${s.segItem} ${on ? s.segOn : ""}`
    : `${s.mode} ${on ? s.modeOn : ""}`;
  return (
    <button
      type="button"
      className={cls}
      aria-pressed={on}
      // Not `disabled`: dimmed, still reachable, so the row reads the same
      // before and after the mesh lands.
      aria-disabled={!live}
      data-mode={id}
      onClick={() => live && onPress()}
    >
      {label}
      {id !== "textured" && <span className={s.modeKey}>{MODE_KEYS[id]}</span>}
    </button>
  );
}

function MeshLightbox({
  node,
  nodes,
  wid,
  close,
}: {
  node: Node;
  nodes: Node[];
  wid: string;
  close: () => void;
}) {
  const [view, setView] = useState<View>(INITIAL_VIEW);
  const [load, setLoad] = useState<LoadStatus>({
    status: "loading",
    loaded: 0,
    total: 0,
  });
  const [below, setBelow] = useState(false);
  const [details, setDetails] = useState(false);
  // The turntable as the face: no WebGL2, or the artist asked for it from
  // a failed or empty state.
  const [turntable, setTurntable] = useState(() => !webgl2());
  const [webglSaid] = useState(() => {
    if (webgl2()) return true;
    try {
      const said = sessionStorage.getItem(NO_WEBGL2_KEY) === "1";
      sessionStorage.setItem(NO_WEBGL2_KEY, "1");
      return said;
    } catch {
      return false;
    }
  });

  const live = load.status === "ready" && !turntable;
  const mode = useCallback(
    (m: ModeId) => setView((v) => nextView(v, m)),
    [],
  );
  useHotkeys("c", () => mode("clay"), { enabled: live });
  useHotkeys("b", () => mode("cull"), { enabled: live });
  useHotkeys("n", () => mode("normals"), { enabled: live });
  useHotkeys("w", () => mode("wireframe"), { enabled: live });
  useHotkeys("g", () => mode("grid"), { enabled: live });
  useHotkeys("u", () => mode("below"), { enabled: live });
  useHotkeys("0", () => mode("reset"), { enabled: live });

  // The empty file's next move: the take exists and is
  // nothing — set it aside from here, the verb's own route, closing only
  // once the server has it.
  const queryClient = useQueryClient();
  const asideMut = $api.useMutation("post", "/api/nodes/{nid}/set-aside", {
    onSettled: () => invalidateWorkspace(queryClient, wid),
  });
  const setAside = () =>
    asideMut.mutate(
      { params: { path: { nid: node.id } } },
      { onSuccess: close },
    );
  useHotkeys(["backspace", "delete"], setAside, {
    enabled: load.status === "empty" && !turntable,
    preventDefault: true,
  });

  // The close rule: Esc or the backdrop, never the frame. `click`
  // fires on the common ancestor of the press and the release, so an orbit
  // drag that ends outside the frame would land on the backdrop — it counts
  // only when the press began there too.
  const downOnBackdrop = useRef(false);
  const onPointerDown = (e: React.PointerEvent) => {
    downOnBackdrop.current = e.target === e.currentTarget;
  };
  const onClick = (e: React.MouseEvent) => {
    if (downOnBackdrop.current && e.target === e.currentTarget) close();
  };

  const facts = meshFactsLine(node);
  const head = viewerHeader(node, nodes);
  const orbit = `/api/nodes/${node.id}/turntable`;
  const fraction =
    load.status === "loading"
      ? loadFraction(load.loaded, load.total, node.mesh?.file_mb)
      : null;

  const truth = turntable
    ? webglSaid
      ? TURNTABLE_LINE
      : NO_WEBGL2_LINE
    : load.status === "loading"
      ? POSTER_LINE
      : load.status === "ready"
        ? truthLine(node, view, below)
        : null;
  const hint = turntable
    ? "TURNTABLE · THE ENGINE'S OWN RENDER · ESC OR THE BACKDROP CLOSES"
    : load.status === "loading"
      ? "THE CONTROLS WAKE WHEN THE MESH LANDS"
      : load.status === "ready"
        ? "DRAG ORBITS · SCROLL ZOOMS · RIGHT-DRAG PANS"
        : "ESC OR THE BACKDROP CLOSES";

  return (
    <div
      className={s.viewer}
      role="dialog"
      aria-label="Mesh viewer"
      onPointerDown={onPointerDown}
      onClick={onClick}
    >
      <div className={`${s.viewerCorner} ${s.viewerCornerLeft}`}>
        <span className={`mono-label ${s.viewerTitle}`}>MESH VIEWER</span>
        <span className={s.viewerTake}>on {head.take}</span>
        <span className="mono-micro">{head.facts}</span>
      </div>
      <div className={`${s.viewerCorner} ${s.viewerCornerRight}`}>
        <span className="mono-micro">
          ESC OR THE BACKDROP CLOSES · THE FRAME DOESN&apos;T
        </span>
        <button
          type="button"
          className={s.viewerClose}
          aria-label="Close the viewer"
          onClick={close}
        >
          ✕
        </button>
      </div>

      <div className={s.viewerStage}>
        {turntable ? (
          <img className={s.viewerOrbit} src={orbit} alt="" />
        ) : (
          <>
            <StageBoundary
              onFail={(error) => setLoad({ status: "failed", error })}
            >
              <Suspense fallback={null}>
                <MeshStage
                  url={`/api/nodes/${node.id}/image`}
                  view={view}
                  onLoad={setLoad}
                  onBelow={setBelow}
                />
              </Suspense>
            </StageBoundary>
            {load.status === "loading" && (
              <div className={s.viewerPoster}>
                <img className={s.viewerPosterImg} src={orbit} alt="" />
                <div className={s.viewerLoading}>
                  <span className={`mono-label ${s.viewerLoadingLine}`}>
                    {loadingLine(node)}
                  </span>
                  {fraction !== null && (
                    <div
                      className={s.viewerBar}
                      role="progressbar"
                      aria-label="Mesh bytes"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(fraction * 100)}
                    >
                      <div
                        className={s.viewerBarFill}
                        style={{ width: `${Math.round(fraction * 100)}%` }}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}
            {load.status === "failed" && (
              <div className={s.viewerState}>
                <span className={`mono-label ${s.viewerStateDead}`}>
                  COULDN&apos;T DRAW THE MESH
                </span>
                <p className={s.viewerLine}>
                  The loader refused the file. The artefact is untouched on
                  disk and still promotable — nothing was lost and nothing
                  was spent.
                </p>
                <div className={s.viewerStateRow}>
                  <button
                    type="button"
                    className={s.viewerBtn}
                    onClick={() => setTurntable(true)}
                  >
                    SHOW THE TURNTABLE
                  </button>
                  <button
                    type="button"
                    className={`mono-micro ${s.viewerMicroBtn}`}
                    aria-expanded={details}
                    onClick={() => setDetails((d) => !d)}
                  >
                    DETAILS
                  </button>
                </div>
                {details && <pre className={s.viewerDetails}>{load.error}</pre>}
              </div>
            )}
            {load.status === "empty" && (
              <div className={s.viewerState}>
                <span className={`mono-label ${s.viewerStateInfo}`}>
                  THE FILE PARSED · THERE IS NO GEOMETRY IN IT
                </span>
                <p className={s.viewerLine}>
                  An empty scene — the extraction produced a GLB with no
                  meshes. The engine reported done, so this is the take, not
                  the viewer.
                </p>
                <div className={s.viewerStateRow}>
                  <button
                    type="button"
                    className={s.viewerBtn}
                    onClick={() => setTurntable(true)}
                  >
                    SHOW THE TURNTABLE
                  </button>
                  <button
                    type="button"
                    className={`mono-micro ${s.viewerMicroBtn}`}
                    onClick={setAside}
                  >
                    SET ASIDE ⌫
                  </button>
                </div>
                {asideMut.isError && (
                  <p className={s.viewerLine}>{verbErr(asideMut.error)}</p>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <div className={s.viewerBottom}>
        <div
          className={`${s.viewerModes} ${live ? "" : s.viewerModesOff}`}
          role="toolbar"
          aria-label="View modes"
        >
          <ModeChip id="reset" label="RESET VIEW" live={live} onPress={() => mode("reset")} />
          <ModeChip id="below" label="FROM BELOW" on={below} live={live} onPress={() => mode("below")} />
          <span className={s.modeDot} />
          <div className={s.modeSeg} role="group" aria-label="Surface">
            <ModeChip
              id="textured"
              label="TEXTURED"
              on={view.surface === "textured"}
              live={live}
              seg
              onPress={() => view.surface !== "textured" && mode("clay")}
            />
            <ModeChip
              id="clay"
              label="CLAY"
              on={view.surface === "clay"}
              live={live}
              seg
              onPress={() => view.surface !== "clay" && mode("clay")}
            />
          </div>
          <ModeChip id="cull" label="BACKFACE CULL" on={view.cull} live={live} onPress={() => mode("cull")} />
          <span className={s.modeDot} />
          <ModeChip id="normals" label="NORMALS" on={view.normals} live={live} onPress={() => mode("normals")} />
          <ModeChip id="wireframe" label="WIREFRAME" on={view.wireframe} live={live} onPress={() => mode("wireframe")} />
          <ModeChip id="grid" label="GRID" on={view.grid} live={live} onPress={() => mode("grid")} />
        </div>
        {facts !== "" && (
          <span className={`mono-label ${s.lightboxFacts}`}>{facts}</span>
        )}
        {truth !== null && <p className={s.viewerTruth}>{truth}</p>}
        <span className={`mono-label ${s.lightboxHint}`}>{hint}</span>
      </div>
    </div>
  );
}

export function Lightbox({ wid }: { wid: string }) {
  const nid = useUi((st) => st.lightboxNodeId);
  const close = useUi((st) => st.closeLightbox);
  const wantArm = useUi((st) => st.wantArm);
  useHotkeys("esc", close, { enabled: nid !== null });
  // the workspace feed is already warm on every surface that opens this
  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid } },
  });
  // the turns, for the caption's recipe: the shell's own entry
  const wsQ = $api.useQuery("get", "/api/workspaces/{wid}", {
    params: { path: { wid } },
  });
  if (nid === null) return null;
  const nodes = nodesQ.data ?? [];
  const node = nodes.find((n) => n.id === nid);
  const turn = wsQ.data?.turns.find((t) => t.id === node?.turn_id);
  if (node !== undefined && node.payload === "3d")
    return (
      // keyed so a second mesh opened over the first starts its own load
      <MeshLightbox key={nid} node={node} nodes={nodes} wid={wid} close={close} />
    );
  return (
    <div
      className={s.lightbox}
      role="dialog"
      aria-label="Full-resolution take"
      onClick={close}
    >
      <img className={s.lightboxImg} src={`/api/nodes/${nid}/image`} alt="" />
      {node !== undefined && (
        // the caption is content, not backdrop: its clicks are its own
        <div className={s.lightboxRecord} onClick={(e) => e.stopPropagation()}>
          <span className={`mono-label ${s.lightboxFacts}`}>
            {targetLabel([node]).toUpperCase()} · FULL RES
          </span>
          <Record
            scope="take"
            turn={turn}
            nodes={[node]}
            all={nodes}
            onUsePrompt={(p) => {
              close();
              wantArm([], null, p);
            }}
          />
        </div>
      )}
      <span className={`mono-label ${s.lightboxHint}`}>
        FULL RES · CLICK OR ESC CLOSES
      </span>
    </div>
  );
}
