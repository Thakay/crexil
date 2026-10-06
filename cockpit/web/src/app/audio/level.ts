// Level matching for auditioning takes side by side. Takes are compared at the level of the
// quietest measured take: louder takes are turned down, never up, so playback can never clip
// and needs nothing beyond the media element's volume. One far quieter outlier cannot drag the
// rest down by more than MAX_ATTENUATION_DB; it plays as recorded and says it is unmatched.
// The loudness is the server's BS.1770 measurement; nothing here is estimated (law 17).

export const MAX_ATTENUATION_DB = 18;

export type Level = {
  /** Applied gain in dB, never above 0. */
  gainDb: number;
  /** The media element volume for that gain, 0 to 1. */
  volume: number;
  /** True when the take plays at the shared target level. */
  matched: boolean;
};

export type Measured = {id: string; loudness: number | null | undefined};

export const UNMATCHED: Level = {gainDb: 0, volume: 1, matched: false};

export const dbToVolume = (db: number) => Math.min(1, Math.max(0, 10 ** (db / 20)));

/** The level each take plays at so every measured take sounds equally loud. */
export function matchLevels(takes: readonly Measured[]): Record<string, Level> {
  const measured = takes.filter((take): take is {id: string; loudness: number} =>
    typeof take.loudness === 'number' && Number.isFinite(take.loudness));
  const levels: Record<string, Level> = {};
  for (const take of takes) levels[take.id] = UNMATCHED;
  if (measured.length === 0) return levels;
  const loudest = Math.max(...measured.map(take => take.loudness));
  const quietest = Math.min(...measured.map(take => take.loudness));
  const target = Math.max(quietest, loudest - MAX_ATTENUATION_DB);
  for (const take of measured) {
    if (take.loudness < target) continue; // too quiet to match without boosting: as recorded
    const gainDb = Math.min(0, target - take.loudness);
    levels[take.id] = {gainDb, volume: dbToVolume(gainDb), matched: true};
  }
  return levels;
}
