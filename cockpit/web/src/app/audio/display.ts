// Pure helpers for drawing a take and following its words. A waveform is drawn from the
// peaks the server measured once at collection; the client never decodes audio to draw it.

/** Resamples measured peaks (0 to 1) to `count` bars; each bar is the loudest peak it covers. */
export function bars(peaks: readonly number[], count: number): number[] {
  const n = Math.max(0, Math.floor(count));
  if (n === 0) return [];
  if (peaks.length === 0) return new Array<number>(n).fill(0);
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const from = Math.floor((i * peaks.length) / n);
    const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / n));
    let top = 0;
    for (let j = from; j < to && j < peaks.length; j++) top = Math.max(top, peaks[j]);
    out.push(Math.min(1, Math.max(0, Number.isFinite(top) ? top : 0)));
  }
  return out;
}

/** Seconds as the audio clock reads them: 0:03.2. */
export function clock(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const tenths = Math.floor(safe * 10);
  const minutes = Math.floor(tenths / 600);
  const rest = (tenths - minutes * 600) / 10;
  return `${minutes}:${rest.toFixed(1).padStart(4, '0')}`;
}

/** A take's length on a face: 4.4 s, or 12 s past ten seconds. */
export function length(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
  return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
}

type Timed = {characters: string[]; starts: number[]};

/** The per-character timing an engine reported, in either of its delivered shapes. */
export function timing(alignment: unknown): Timed | null {
  const read = (value: unknown): Timed | null => {
    if (!value || typeof value !== 'object') return null;
    const record = value as Record<string, unknown>;
    const characters = record.characters, starts = record.character_start_times_seconds;
    if (!Array.isArray(characters) || !Array.isArray(starts) || characters.length !== starts.length) return null;
    if (!characters.every(c => typeof c === 'string') || !starts.every(s => typeof s === 'number')) return null;
    return {characters: characters as string[], starts: starts as number[]};
  };
  return read(alignment) ?? read((alignment as {alignment?: unknown} | null)?.alignment);
}

/** Splits the spoken words at the playhead: what has been said, and what is still to come. */
export function spokenSplit(text: string, alignment: unknown, position: number): {spoken: string; rest: string} {
  const timed = timing(alignment);
  const words = timed ? timed.characters.join('') : text;
  if (!timed) return {spoken: '', rest: words};
  let said = 0;
  while (said < timed.starts.length && timed.starts[said] <= position) said++;
  return {spoken: words.slice(0, said), rest: words.slice(said)};
}
