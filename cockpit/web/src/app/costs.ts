// The money corner's pure half: the
// totals are CHARGED, summed by the server from the events ledger across
// every workspace (GET /api/costs?tz=); a pre-run estimate or a cell's
// committed quote never counts. Both windows run on the artist's own
// clock — the browser sends its offset, the server turns the day over at
// that midnight. A "session" total is read as TODAY: a sitting
// the app can name without guessing where one ended.

/** Minutes EAST of UTC — the sign the server's `tz` query expects (the
 *  DOM's getTimezoneOffset is west-positive). */
export function tzMinutes(now: Date = new Date()): number {
  return -now.getTimezoneOffset();
}

const MONTHS = [
  "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];

/** "2026-08" → "AUG": the corner's month word (as in `JUL $12.40`). */
export function monthWord(month: string): string {
  const m = Number(month.slice(5, 7));
  return MONTHS[m - 1] ?? month;
}

export function usd(v: number): string {
  return `$${v.toFixed(2)}`;
}
