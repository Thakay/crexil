// The voice minutes meter. Every figure is observed or read from the server's budget (law 17):
// elapsed time is counted from the moment the conversation connected, and the price is the
// published list price per minute, shown as an estimate because model use is billed on top.
import type { VoiceUsage } from './door';

/** Whole seconds as m:ss. */
export function minutes(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

/** The most one session can cost at the list price, before model use. */
export const sessionPrice = (usage: VoiceUsage) => (usage.session_max_seconds / 60) * usage.usd_per_minute;

/** The seconds a session has run, from when it connected. */
export const elapsedSince = (connectedAt: number | null, now: number) =>
  connectedAt === null ? 0 : Math.max(0, (now - connectedAt) / 1000);
