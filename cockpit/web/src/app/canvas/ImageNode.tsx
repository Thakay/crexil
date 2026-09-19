// The node card: image plate on top, mono footer strip, selected ring +
// ACTING ON pill. memo'd for the performance budget. CardBody is RF-free so component
// tests render it without a Flow store (RF Handles throw outside a
// provider; jsdom lacks ResizeObserver anyway).
import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useUi } from "../state/ui";
import { cardDragRole } from "./drop";
import type { CanvasNode, CanvasNodeData } from "./graph";
import s from "./Canvas.module.css";

export function CardBody({
  data,
  selected,
}: {
  data: CanvasNodeData;
  selected: boolean;
}) {
  return (
    <>
      <div className={s.plate}>
        {selected && <span className={s.pill}>ACTING ON</span>}
        {/* Promoted is the one permanent state a node reaches, so
            it sits on the plate rather than in the strip, where the label
            already ellipsises. */}
        {data.promoted && (
          <span className={`mono-micro ${s.promoted}`}>PROMOTED</span>
        )}
        {data.thumb !== null ? (
          <img
            className={s.thumb}
            src={data.thumb}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false} // native image drag fights RF's node drag
          />
        ) : (
          // A mesh draws its turntable's frame 0 above. This is the
          // fallback — a mesh whose orbit was never recorded, or can't be
          // reached, keeps a visible, selectable card with the plain word
          // on it (law 12: badge, never filter). No fake imagery.
          <span className={`mono-micro ${s.plateWord}`}>MESH</span>
        )}
      </div>
      <div className={s.strip}>
        <span className={s.stripLeft}>{data.label}</span>
        <span className={s.stripRight}>{data.right}</span>
      </div>
    </>
  );
}

export const ImageNode = memo(function ImageNode({
  id,
  data,
  selected,
}: NodeProps<CanvasNode>) {
  // The drag layer's ring: one primitive per card, so a lift
  // re-renders each card once and a pointer crossing only the card it
  // lands on. Canvas.module.css styles it by data-drag.
  const drag = useUi((st) => cardDragRole(st.drag, id));
  return (
    <div
      className={selected ? `${s.card} ${s.selected}` : s.card}
      data-drag={drag}
    >
      <Handle
        type="target"
        position={Position.Left}
        className={s.handle}
        isConnectable={false}
      />
      <CardBody data={data} selected={selected} />
      <Handle
        type="source"
        position={Position.Right}
        className={s.handle}
        isConnectable={false}
      />
    </div>
  );
});
