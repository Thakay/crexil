// One character's family on the Filmstrip, decided without the DOM. Sections that start
// from words sit side by side on a row; a continuation lands on the row after its
// sources, so the family reads downward along its lines, one per start, each in the hue
// of the medium the start makes (BIBLE invariant 8). A preparation can start from the
// words of a section on its own row or on its sources' rows: the words are copied, stay
// editable in the preparation, and the section they come from keeps its own. Crexil
// never fills words on its own (law 15); copying them is the participant's act. The
// record keeps a take's words as typed in its accepted preparation and as submitted in
// its frozen request.
import { actionView } from './actions';
import { preparation, type Direction, type Material } from './api';

/** Words a preparation can start from: the section they come from and its words. */
export type CarriedWords = {direction: string; title: string; words: string};

/** The per-take record the server keeps: the accepted request and the frozen request. */
export type TakeRecord = {request?: Record<string, unknown> | null; submitted?: Record<string, unknown> | null};

const rowOf = (node: string, directions: readonly Direction[]): number | undefined =>
  directions.find(direction => direction.node_ids.includes(node))?.row_idx;

/** The row a continuation of these sources lands on: the row after the last of their
 *  rows, so every line runs down from a parent to its start. Undefined when no source
 *  sits on a row, so the continuation opens a row of its own. */
export function continuationRow(sources: readonly Pick<Material, 'id'>[], directions: readonly Direction[]): number | undefined {
  const rows = sources.map(node => rowOf(node.id, directions)).filter((row): row is number => row !== undefined);
  return rows.length ? Math.max(...rows) + 1 : undefined;
}

/** Whether a direction's words are the words its runs send, so another run can start
 *  from them: never a description kept only for the record, an image preparation or an import. */
function sendsWords(direction: Direction): boolean {
  const action = preparation(direction).action;
  const view = actionView(action);
  return Boolean(view?.listed) && !view?.recordedDescription && action !== 'prepare';
}

/** The words a preparation can start from: the sections on its own row, then on the rows
 *  its sources come from, in the order the Filmstrip shows them, one offer per distinct
 *  text. Its own words, discarded preparations and sections set aside are never offered. */
export function wordsToCarry(preparing: {id: string; row: number; sources: readonly string[]},
  directions: readonly Direction[]): CarriedWords[] {
  const sourceRows = preparing.sources.map(id => rowOf(id, directions)).filter((row): row is number => row !== undefined);
  const rows = [...new Set([preparing.row, ...sourceRows.sort((a, b) => a - b)])];
  const seen = new Set<string>();
  const offers: CarriedWords[] = [];
  for (const row of rows) for (const direction of directions.filter(item => item.row_idx === row)) {
    if (direction.id === preparing.id || direction.state === 'discarded' || direction.set_aside || !sendsWords(direction)) continue;
    const words = preparation(direction).prompt.trim();
    if (!words || seen.has(words)) continue;
    seen.add(words);
    offers.push({direction: direction.id, title: direction.title, words});
  }
  return offers;
}

const text = (value: unknown): string | null => typeof value === 'string' && value.trim() ? value : null;

/** A generated take's words as the record keeps them: as typed in the preparation the run
 *  accepted, and as submitted in the frozen request. An audio request sends its own text;
 *  an image's frozen prompt is the take's prompt, contributions included. Either is null
 *  while the record is unread or withheld, as it is for a blind run before its reveal. */
export function recordedWords(node: Pick<Material, 'payload' | 'prompt'>, record: TakeRecord | null | undefined):
  {typed: string | null; submitted: string | null} {
  if (!record?.request) return {typed: null, submitted: null};
  const setup = record.request.setup as {prompt?: unknown} | undefined;
  const submitted = node.payload === 'audio' ? text(record.submitted?.text) : text(node.prompt);
  return {typed: text(setup?.prompt), submitted};
}
