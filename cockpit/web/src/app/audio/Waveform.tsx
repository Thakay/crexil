// A take's waveform, drawn from its measured peaks. The played part wears the accent; a loop
// region is shaded. A normalized waveform draws its tallest bar at full height, so its picture
// shows the take's shape and never its level; a blind take is drawn so. It is a picture only:
// it never takes focus or input.
import { useMemo } from 'react';
import s from './audio.module.css';
import { bars } from './display';

type Props = {
  peaks: readonly number[];
  /** How many bars to draw. */
  count: number;
  /** The played share of the take, 0 to 1. */
  progress?: number;
  /** A loop region as shares of the take, 0 to 1. */
  loop?: {start: number; end: number} | null;
  /** Scale the bars to the take's own loudest peak. */
  normalized?: boolean;
  className?: string;
};

const BAR = 2, STEP = 3;

export function Waveform({peaks, count, progress = 0, loop = null, normalized = false, className}: Props) {
  const levels = useMemo(() => {
    const drawn = bars(peaks, count);
    const top = Math.max(0, ...drawn);
    return normalized && top > 0 ? drawn.map(level => level / top) : drawn;
  }, [peaks, count, normalized]);
  const width = levels.length * STEP;
  const cut = Math.round(Math.min(1, Math.max(0, progress)) * levels.length);
  return (
    <svg className={`${s.wave} ${className ?? ''}`} viewBox={`0 0 ${width} 100`} preserveAspectRatio="none"
      aria-hidden="true" focusable="false" data-bars={levels.length} data-played={cut}>
      {loop && loop.end > loop.start && (
        <rect className={s.loop} x={loop.start * width} y={0} width={(loop.end - loop.start) * width} height={100}/>
      )}
      {levels.map((level, i) => {
        const height = Math.max(2, level * 96);
        return <rect key={i} className={i < cut ? s.played : s.unplayed} x={i * STEP} y={50 - height / 2} width={BAR} height={height}/>;
      })}
    </svg>
  );
}
