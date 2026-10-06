// The Filmstrip is a client of the same typed actions exposed to agents.
import { fetchClient } from '../../api/client';
import type { components } from '../../generated/openapi';
import { engineFaceName, engineName } from './catalogue';

export type Project = components['schemas']['ProjectView'];
export type Direction = components['schemas']['DirectionView'];
export type Material = components['schemas']['MaterialNode'];
export type Preparation = components['schemas']['Preparation'];
export type Operation = components['schemas']['OperationView'];
export type Snapshot = components['schemas']['ProjectSnapshot'];
export type Actor = components['schemas']['Actor'];
export const HUMAN: Actor = { kind: 'human', name: 'Artist' };
const ARTIST_NAME = 'crexil-artist-name';
/** The person at this browser, named as the record names them: the name kept in this
 *  browser's settings, else "Artist". */
export function artist(): Actor {
  try {
    const name = localStorage.getItem(ARTIST_NAME)?.trim();
    if (name) return {kind: 'human', name: name.slice(0, 120)};
  } catch { /* storage unavailable: the default name stands */ }
  return HUMAN;
}
/** Keeps the name the record gives this browser's artist; an empty name restores "Artist". */
export function setArtistName(name: string) {
  try {
    if (name.trim()) localStorage.setItem(ARTIST_NAME, name.trim().slice(0, 120));
    else localStorage.removeItem(ARTIST_NAME);
  } catch { /* storage unavailable: nothing is kept */ }
}
/** Who made a decision or a request, as every surface names them (law 23): "Artist" for
 *  the default artist, "Artist · Kay" for one with a name of their own, "Agent · <name>" for
 *  an agent, and plainly when the record holds no participant. */
export function actorLine(actor: Actor | null | undefined): string {
  if (!actor) return 'Participant not recorded';
  if (actor.kind === 'agent') return `Agent · ${actor.name}`;
  return actor.name === HUMAN.name ? HUMAN.name : `${HUMAN.name} · ${actor.name}`;
}
// Every write names its participant (law 23). Keys, buttons and agent tools build
// their commands here, so an agent passes its own actor and nothing else differs.
export const command = (actor: Actor = artist()) => ({ request_id: crypto.randomUUID(), actor });

/** What a browser's fetch says when a request never reached the server or its answer
 *  never came back, in each browser's own words. */
const UNANSWERED = /failed to fetch|networkerror|load failed|network request failed/i;
export const NO_ANSWER = 'The Crexil server did not answer. Check that it is running, then try again.';

/** A failure in plain words (law 3): the server's own sentence, or what happened when the
 *  server never answered; never the browser's raw "Failed to fetch". */
export function message(error: unknown): string {
  if (error instanceof TypeError && UNANSWERED.test(error.message)) return NO_ANSWER;
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object' && 'detail' in error) {
    if (typeof error.detail === 'string') return error.detail;
    return 'Some values need attention. Check the inputs and try again.';
  }
  return 'The server could not confirm this change. Your preparation is still here. Try again.';
}

/** A refused or failed request, with its status and the kind of conflict a 409 names
 *  in its X-Crexil-Conflict header, such as `spending`. */
export class ActionError extends Error {
  constructor(error:unknown, public readonly status?:number, public readonly conflict:string|null=null) {super(message(error));}
}

/** Spending moved since the run was reviewed: review again and show the new totals before
 *  asking again. Any other conflict means the request itself changed. */
export const isSpendingConflict = (error: unknown): boolean =>
  error instanceof ActionError && error.status === 409 && error.conflict === 'spending';

// A saved command may be dropped only when the server definitely refused it: a client
// error other than a timeout (408) or a rate limit (429). Network failures, server errors
// and those two keep the same request for a confirmed retry (laws 7 and 9).
export function isDefiniteRefusal(error: unknown): boolean {
  if (!(error instanceof ActionError) || error.status === undefined) return false;
  return error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429;
}

export async function result<T>(request: Promise<{data?: T; error?: unknown; response?:Response}>): Promise<T> {
  const response = await request;
  if (response.error || response.data === undefined)
    throw new ActionError(response.error, response.response?.status, response.response?.headers?.get('X-Crexil-Conflict') ?? null);
  return response.data;
}

export const imageUrl = (id: string, full = false) => `/api/nodes/${id}/${full ? 'image' : 'thumb'}`;
/** The grid's name for a cell or a take: the run's index, the cell's letters from A, and
 *  `.n` for a later output of the same cell ("3B", "3B.2"). Cells past Z continue as
 *  spreadsheet columns do ("3AA", "3AB"), as the server's run view and package names spell them. */
export function takeLabel(run: number, pos: number, output = 0): string {
  let letters = '';
  for (let rest = pos + 1; rest > 0; rest = Math.floor((rest - 1) / 26)) letters = String.fromCharCode(65 + (rest - 1) % 26) + letters;
  return `${run}${letters}${output > 0 ? `.${output + 1}` : ''}`;
}
export const label = (node: Material) => node.turn_idx === null ? (node.material?.filename ?? 'Imported image')
  : takeLabel(node.turn_idx, node.slot_pos ?? 0, node.output_index);
// The project surface spells money as every surface does (costs.ts): money that moved
// never reads as $0.00 (law 5).
export { usd as money } from '../costs';

// Who made a material or an attempt. A hidden identity is a blind run's withheld engine,
// read from the server's identity_hidden flag (its masked engine is a placeholder, never
// a name); it is never mistaken for imported material, which has no engine at all.
export type EngineIdentity = {kind: 'named'; name: string} | {kind: 'hidden'} | {kind: 'prepared'} | {kind: 'imported'};
type Identified = {engine: string | null; identity_hidden?: boolean | null; material?: {origin: Record<string, unknown>} | null};
/** A blind run's material or attempt, before its reveal. */
export const isHidden = (item: {identity_hidden?: boolean | null}) => item.identity_hidden === true;
export function engineIdentity(node: Identified, hidden = false): EngineIdentity {
  if (hidden || isHidden(node)) return {kind: 'hidden'};
  if (node.engine) return {kind: 'named', name: node.engine};
  return node.material?.origin.kind === 'prepared_image' ? {kind: 'prepared'} : {kind: 'imported'};
}
// 'face' names the maker on a take's or an attempt's face; 'source' groups and heads material
// by origin. An engine reads by its catalogue name (catalogue.ts), on a face by its face name:
// a hosted model without its vendor. A blind take reads "Blind" on its face and "Engine
// hidden" where material is grouped.
export function engineLabel(node: Identified, variant: 'face' | 'source', hidden = false): string {
  const identity = engineIdentity(node, hidden);
  if (identity.kind === 'named') return variant === 'face' ? engineFaceName(identity.name) : engineName(identity.name);
  if (identity.kind === 'hidden') return variant === 'face' ? 'Blind' : 'Engine hidden';
  if (variant === 'source') return 'Imported material';
  return identity.kind === 'prepared' ? 'Prepared' : 'Imported';
}
export const projectHref = (id?: string) => id ? `#/projects/${id}` : '#/projects';

export function blankPreparation(): Preparation {
  return { action: 'generate', prompt: '', references: [], operations: [], controls: {},
    instructions: '', attempt_limit: 16, spending_limit_usd: 0, spending_policy:'hard_cap', purpose:'creative_request',
    regions:[],region_overlap_policy:null,region_regenerate_unclaimed:false };
}

export function preparation(direction: Direction): Preparation {
  return { ...blankPreparation(), ...direction.setup } as Preparation;
}

export const saveDirection = (id: string, body: components['schemas']['UpdateDirection']) =>
  result(fetchClient.PATCH('/api/crexil/directions/{did}', { params: {path: {did: id}}, body }));

export async function importFile(wid: string, file: File, parents: string[] = []) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return result(fetchClient.POST('/api/crexil/projects/{wid}/material', {params: {path: {wid}},
    body: {...command(), filename: file.name, data_base64: btoa(binary), parents, tool:'', explanation:''} }));
}
