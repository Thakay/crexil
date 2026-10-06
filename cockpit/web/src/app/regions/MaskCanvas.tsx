// Controlled Konva drawing in the browser image's natural coordinate space.
// Pointer moves update the live layer imperatively; pointer-up emits one Ink.
// The caller owns drafts, rasterization, storage, keyboard shortcuts and submission.
// Reuse model.rasterize for committed masks; never accumulate translucent strokes.
import Konva from "konva";
import { useEffect, useMemo, useRef, useState, type ComponentRef } from "react";
import { Image as KonvaImage, Layer, Stage } from "react-konva";
import {
  ACTION_COLORS, fitScale, layerOrder, tintCanvas,
  type DrawTool, type Ink, type RegionAction, type RegionDraft,
} from "./model";
import s from "./Regions.module.css";

export type NaturalSize = { w: number; h: number };

export type MaskCanvasProps = {
  sourceUrl: string;
  drafts: readonly RegionDraft[];
  activeKey: string | null;
  tool: DrawTool;
  brushSize: number;
  disabled?: boolean;
  onInk: (key: string, ink: Ink, naturalSize: NaturalSize) => void;
  onSourceSize?: (naturalSize: NaturalSize | null) => void;
  /** The caller may create a region when drawing starts without an active one. */
  onCreateRegion?: () => string | null;
  className?: string;
  loadingMessage?: string;
  errorMessage?: string;
};

/** Browser decoding supplies the orientation and natural coordinates used to draw. */
export function useLoadedImage(url: string | null): HTMLImageElement | "error" | null {
  const [loaded, setLoaded] = useState<{
    url: string;
    image: HTMLImageElement | "error";
  } | null>(null);
  useEffect(() => {
    if (url === null) return;
    const el = new window.Image();
    el.onload = () => setLoaded({ url, image: el });
    el.onerror = () => setLoaded({ url, image: "error" });
    el.src = url;
    return () => { el.onload = null; el.onerror = null; };
  }, [url]);
  return loaded?.url === url ? loaded.image : null;
}

/** The stage area's box, re-measured on window resize — a state write per
 *  resize event, never per pointer frame. */
function useAreaSize(ref: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    const measure = () => {
      const el = ref.current;
      // The 80px floor keeps the fit scale positive however short the
      // window gets — the area itself has a min-height and scrolls.
      if (el !== null)
        setSize({
          w: Math.max(el.clientWidth - 24, 80),
          h: Math.max(el.clientHeight - 24, 80),
        });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [ref]);
  return size;
}

/** One region's whole ink as one tinted image:
 *  the bitmap recoloured per commit — `inkRev` keys the memo because the
 *  canvas mutates in place. */
function RegionTint({ draft }: { draft: RegionDraft }) {
  const tinted = useMemo(
    () =>
      draft.mask === null
        ? null
        : tintCanvas(draft.mask, ACTION_COLORS[draft.action]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- inkRev IS the mask's version
    [draft.mask, draft.inkRev, draft.action],
  );
  if (tinted === null) return null;
  return <KonvaImage image={tinted} width={tinted.width} height={tinted.height} />;
}

/** The live in-progress stroke, held outside React: created at pointer-
 *  down, grown per move via batchDraw, destroyed at commit. */
type LiveStroke = {
  key: string;
  tool: DrawTool;
  node: Konva.Line | Konva.Rect;
  points: number[]; // natural coords
  origin: { x: number; y: number };
};

export function MaskCanvas({
  sourceUrl, drafts, activeKey, tool, brushSize, onInk, onSourceSize, disabled = false,
  onCreateRegion, className, loadingMessage = "Loading source image",
  errorMessage = "The source image is missing or unreadable.",
}: MaskCanvasProps) {
  const image = useLoadedImage(sourceUrl);
  const areaRef = useRef<HTMLDivElement>(null);
  const area = useAreaSize(areaRef);
  const natural = useMemo(
    () => image === null || image === "error"
      ? null : { w: image.naturalWidth, h: image.naturalHeight },
    [image],
  );
  useEffect(() => { onSourceSize?.(natural); }, [natural, onSourceSize]);
  const scale = natural !== null && area !== null ? fitScale(natural, area) : null;
  const active = drafts.find((draft) => draft.key === activeKey) ?? null;
  const painted = layerOrder(drafts).filter((draft) => draft.visible);

  // --- drawing (imperative between down and up) ---------------------------
  const liveLayerRef = useRef<ComponentRef<typeof Layer>>(null);
  const live = useRef<LiveStroke | null>(null);
  useEffect(() => () => {
    live.current?.node.destroy();
    live.current = null;
  }, [sourceUrl, disabled]);

  const pointerNatural = (stage: Konva.Stage | null) => {
    if (stage === null || scale === null) return null;
    const p = stage.getPointerPosition();
    return p === null ? null : { x: p.x / scale, y: p.y / scale };
  };

  const strokeColor = (t: DrawTool, action: RegionAction) =>
    // A live erase can't punch the region layer it isn't on — it previews
    // as a faint white path and lands as destination-out at commit.
    t === "erase" ? "rgba(228,232,235,0.35)" : ACTION_COLORS[action];

  const down = (e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    if (disabled) return;
    const layer = liveLayerRef.current;
    const pos = pointerNatural(e.target.getStage());
    if (layer === null || pos === null || scale === null) return;
    // DRAW A REGION, THEN DECLARE WHAT IT MEANS: inking with no region
    // starts one (modify, the mildest generative action) and makes it live.
    const key = active?.key ?? onCreateRegion?.();
    if (key == null) return;
    const color = strokeColor(tool, active?.action ?? "modify");
    let node_: Konva.Line | Konva.Rect;
    if (tool === "rect") {
      node_ = new Konva.Rect({
        x: pos.x,
        y: pos.y,
        width: 0,
        height: 0,
        fill: color,
        opacity: 0.55,
      });
    } else {
      node_ = new Konva.Line({
        points: [pos.x, pos.y],
        stroke: color,
        strokeWidth: tool === "lasso" ? 2 / scale : brushSize / scale,
        lineCap: "round",
        lineJoin: "round",
        opacity: tool === "erase" ? 1 : 0.55,
        ...(tool === "lasso" ? { closed: true, fill: color } : {}),
      });
    }
    layer.add(node_);
    layer.batchDraw();
    live.current = { key, tool, node: node_, points: [pos.x, pos.y], origin: pos };
  };

  const move = (e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    const st = live.current;
    const pos = pointerNatural(e.target.getStage());
    if (st === null || pos === null) return;
    if (st.tool === "rect") {
      (st.node as Konva.Rect).setAttrs({
        x: Math.min(st.origin.x, pos.x),
        y: Math.min(st.origin.y, pos.y),
        width: Math.abs(pos.x - st.origin.x),
        height: Math.abs(pos.y - st.origin.y),
      });
    } else {
      st.points.push(pos.x, pos.y);
      (st.node as Konva.Line).points(st.points);
    }
    liveLayerRef.current?.batchDraw();
  };

  const up = () => {
    const st = live.current;
    if (st === null || scale === null || natural === null) return;
    live.current = null;
    st.node.destroy();
    liveLayerRef.current?.batchDraw();
    let ink: Ink | null = null;
    if (st.tool === "rect") {
      const r = st.node as Konva.Rect;
      if (r.width() >= 2 && r.height() >= 2)
        ink = {
          tool: "rect",
          x: r.x(),
          y: r.y(),
          w: r.width(),
          h: r.height(),
        };
    } else if (st.tool === "lasso") {
      if (st.points.length >= 6) ink = { tool: "lasso", points: st.points };
    } else {
      ink = {
        tool: st.tool === "erase" ? "erase" : "brush",
        points: st.points,
        size: brushSize / scale,
      };
    }
    if (!disabled && ink !== null) onInk(st.key, ink, natural);
  };

  return (
    <div className={[s.stageArea, className].filter(Boolean).join(" ")} ref={areaRef} aria-busy={disabled}>
      {image !== null &&
      image !== "error" &&
      natural !== null &&
      scale !== null ? (
        <Stage
          width={Math.round(natural.w * scale)}
          height={Math.round(natural.h * scale)}
          scaleX={scale}
          scaleY={scale}
          className={s.stage}
          style={{ cursor: disabled ? "wait" : "crosshair" }}
          onMouseDown={down}
          onMouseMove={move}
          onMouseUp={up}
          onMouseLeave={up}
        >
          <Layer listening={false}>
            <KonvaImage
              image={image}
              width={natural.w}
              height={natural.h}
            />
          </Layer>
          {painted.map((d) => (
            <Layer
              key={d.key}
              listening={false}
              opacity={d.key === activeKey ? 0.55 : 0.35}
            >
              <RegionTint draft={d} />
            </Layer>
          ))}
          <Layer ref={liveLayerRef} listening={false} />
        </Stage>
      ) : (
        <p className={`mono-label ${s.loading}`}>
          {image === "error" ? errorMessage : loadingMessage}
        </p>
      )}
    </div>
  );
}
