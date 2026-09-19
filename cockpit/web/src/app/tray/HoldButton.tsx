// Hold-to-confirm: the irreversible verbs fire
// only after the pointer — or Space/Enter — is held for HOLD_MS. A tap, a
// click, a key press is a clean no-op; releasing early cancels; the fill
// shows how far along the hold is (transform only — the motion bar). The
// button's classes live in Tray.module.css. The promote plinth reuses
// this.
import { useEffect, useRef, useState, type CSSProperties } from "react";
import s from "./Tray.module.css";

export const HOLD_MS = 900;

export function HoldButton({
  label,
  onHold,
  holdMs = HOLD_MS,
  disabled = false,
  ariaLabel,
}: {
  label: string;
  onHold: () => void;
  holdMs?: number;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const [holding, setHolding] = useState(false);
  const timer = useRef<number | null>(null);

  const start = () => {
    if (disabled || timer.current !== null) return;
    setHolding(true);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setHolding(false);
      onHold();
    }, holdMs);
  };
  const cancel = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    setHolding(false);
  };
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  return (
    <button
      type="button"
      className={s.hold}
      disabled={disabled}
      aria-label={ariaLabel ?? `Hold to ${label.toLowerCase()}`}
      data-holding={holding || undefined}
      style={{ "--hold-ms": `${holdMs}ms` } as CSSProperties}
      onPointerDown={(e) => {
        if (e.button === 0) start();
      }}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onBlur={cancel}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && !e.repeat) {
          e.preventDefault(); // a key press is not a hold — no native click
          start();
        }
      }}
      onKeyUp={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          cancel();
        }
      }}
    >
      <span className={s.holdFill} aria-hidden />
      <span className={`mono-label ${s.holdLabel}`}>{label}</span>
    </button>
  );
}
