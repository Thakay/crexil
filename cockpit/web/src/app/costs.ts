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

/** One money spelling for every surface: the exact amount, to the micro-dollar the
 *  tariffs count in, with at least two decimals and no trailing zeros past them
 *  ("$0.012", "$0.0336", "$1.50"). A cent or more is never rounded down to the cent,
 *  so the price a button carries is the amount it reserves (law 6); an amount below a
 *  micro-dollar keeps two significant digits, so money that moved never reads as
 *  $0.00 (law 5). Exactly zero is $0.00. */
export function usd(v: number): string {
  const sign = v < 0 ? "-" : "";
  const amount = Math.abs(v);
  if (amount === 0) return "$0.00";
  let text = amount.toFixed(6);
  if (Number(text) === 0)
    text = amount.toFixed(Math.min(20, Math.ceil(-Math.log10(amount)) + 1));
  const [whole, fraction = ""] = text.replace(/0+$/, "").split(".");
  return `${sign}$${whole}.${fraction.padEnd(2, "0")}`;
}
