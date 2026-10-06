// The Filmstrip is a client of the same typed actions exposed to agents.
import { fetchClient } from '../../api/client';
import type { components } from '../../generated/openapi';

export type Project = components['schemas']['ProjectView'];
export type Direction = components['schemas']['DirectionView'];
export type Material = components['schemas']['MaterialNode'];
export type Preparation = components['schemas']['Preparation'];
export type Operation = components['schemas']['OperationView'];
export type Snapshot = components['schemas']['ProjectSnapshot'];
export const actor = { kind: 'human' as const, name: 'Artist' };
export const command = () => ({ request_id: crypto.randomUUID(), actor });

export function message(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object' && 'detail' in error) {
    if (typeof error.detail === 'string') return error.detail;
    return 'Some values need attention. Check the inputs and try again.';
  }
  return 'The server could not confirm this change. Your preparation is still here. Try again.';
}

export class ActionError extends Error {
  constructor(error:unknown, public readonly status?:number) {super(message(error));}
}

export async function result<T>(request: Promise<{data?: T; error?: unknown; response?:Response}>): Promise<T> {
  const response = await request;
  if (response.error || response.data === undefined) throw new ActionError(response.error,response.response?.status);
  return response.data;
}

export const imageUrl = (id: string, full = false) => `/api/nodes/${id}/${full ? 'image' : 'thumb'}`;
export const label = (node: Material) => node.turn_idx === null ? (node.material?.filename ?? 'Imported image')
  : `${node.turn_idx}${String.fromCharCode(65 + (node.slot_pos ?? 0))}${node.output_index>0?`.${node.output_index+1}`:''}`;
export const money = (value: number) => `$${value.toFixed(2)}`;
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
