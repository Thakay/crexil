// The engine catalogue on the client: each engine's display name and lane, read from the
// operations catalogue and the engine matrix (configs/engines.json through /api/engines).
// Every surface names an engine through engineLabel (api.ts), which reads this table, so a
// face, an attempt, inspection and the record say "Qwen3-TTS 1.7B" where the store keeps
// `qwen3-tts`; and a take or an attempt is told local from hosted by its engine's lane,
// never by its price. A face names the model, never its maker (engineFaceName): the name its
// matrix row gives faces, else a hosted model's name without its vendor; inspection and the
// record keep the full name. Until the catalogue loads, or for an engine it does not list,
// an engine reads as its id and its lane is unknown. A blind run's engine never reaches this
// table: engineIdentity withholds it before any name is read.
import { useEffect } from 'react';
import { createStore, useStore } from 'zustand';
import { $api } from '../../api/client';
import type { components } from '../../generated/openapi';
import type { Operation } from './api';

export type Lane = 'local' | 'hosted';
/** `faceName` is the name the engine's matrix row gives faces (`face_name`), when it gives one. */
export type CatalogueEntry = {name: string; lane: Lane; faceName?: string | null};
type EngineRow = Pick<components['schemas']['Engine'], 'id' | 'name' | 'lane' | 'face_name'>;

/** Each engine id's name and lane: the matrix's row for an engine it lists (the audio, mesh
 *  and local image engines), else the operation that carries the id: an image preset, which
 *  runs on the local ComfyUI, or a hosted model, which is reached through its provider. */
export function catalogueFrom(operations: readonly Pick<Operation, 'id' | 'label' | 'provider'>[],
  engines: readonly EngineRow[]): ReadonlyMap<string, CatalogueEntry> {
  const entries = new Map<string, CatalogueEntry>(operations.map(op => [op.id, {name: op.label, lane: op.provider ? 'hosted' : 'local'}]));
  for (const row of engines)
    entries.set(row.id, {name: row.name, lane: row.lane === 'external' ? 'hosted' : 'local', faceName: row.face_name});
  return entries;
}

const store = createStore<{entries: ReadonlyMap<string, CatalogueEntry>}>(() => ({entries: new Map()}));

/** Replaces the catalogue the surfaces read. */
export const setCatalogue = (entries: ReadonlyMap<string, CatalogueEntry>) => store.setState({entries});

/** An engine's display name, or its id when the catalogue does not list it. */
export const engineName = (id: string): string => store.getState().entries.get(id)?.name ?? id;

/** The vendor that leads a hosted model's name: OpenRouter names its models "Vendor: Model". */
const VENDOR = /^[^:]+:\s+(?=\S)/;

/** An engine's name on a face, whose line holds about twenty characters: the name its matrix
 *  row gives faces ("ElevenLabs Voice Design" reads "Voice Design"); else a hosted model's
 *  name after its vendor ("ByteDance Seed: Seedream 4.5" reads "Seedream 4.5"), so models of
 *  one vendor read apart; any other engine's display name, as its owner named it; its id
 *  when the catalogue does not list it. */
export function engineFaceName(id: string): string {
  const entry = store.getState().entries.get(id);
  if (!entry) return id;
  if (entry.faceName) return entry.faceName;
  return entry.lane === 'hosted' ? entry.name.replace(VENDOR, '') : entry.name;
}

/** An engine's lane, or null when the catalogue does not list it. */
export const engineLane = (id: string | null | undefined): Lane | null => (id && store.getState().entries.get(id)?.lane) || null;

/** Loads the catalogue for the project surface and re-renders the caller when it changes. */
export function useCatalogue(operations: readonly Operation[] | undefined) {
  const matrix = $api.useQuery('get', '/api/engines', {}, {staleTime: Infinity});
  const rows = matrix.data?.engines;
  useEffect(() => {
    if (operations || rows) setCatalogue(catalogueFrom(operations ?? [], rows ?? []));
  }, [operations, rows]);
  return useStore(store, state => state.entries);
}
