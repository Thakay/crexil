// The tray as a drop destination while it is closed:
// a right-edge zone over the canvas, up only while a card is lifted, so
// the gesture has somewhere to go without the tray opening under the
// pointer mid-drag (a layout shift moves the card being dragged). With
// the tray open, the open column is the destination — Tray.tsx carries
// the same data-drop marker drop.ts resolves. Self-subscribing, so the
// shell never re-renders for a lift.
import { destinations } from "../canvas/drop";
import { useUi } from "../state/ui";
import s from "./Tray.module.css";

export function TrayDropZone() {
  const drag = useUi((st) => st.drag);
  const trayOpen = useUi((st) => st.trayOpen);
  if (drag === null || trayOpen) return null;
  const d = destinations(drag.lifted.length).find((x) => x.kind === "tray");
  if (d === undefined) return null;
  return (
    <div
      className={s.zone}
      data-drop="tray"
      data-live={d.live}
      data-over={drag.over?.kind === "tray" || undefined}
      aria-label="Set-aside drop zone"
    >
      <span className="mono-label">{d.label}</span>
      <span className="effect-line">{d.line}</span>
    </div>
  );
}
