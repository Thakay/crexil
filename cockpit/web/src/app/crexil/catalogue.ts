// The engine catalogue on the client: each engine's display name and lane, read from the
// operations catalogue and the engine matrix (configs/engines.json through /api/engines).
// Every surface names an engine through engineLabel (api.ts), which reads this table, so a
// face, an attempt, inspection and the record say "Qwen3-TTS 1.7B" where the store keeps
// `qwen3-tts`; and a take or an attempt is told local from hosted by its engine's lane,
// never by its price. Until the catalogue loads, or for an engine it does not list, an
// engine reads as its id and its lane is unknown. A blind run's engine never reaches this
// table: engineIdentity withholds it before any name is read.
import { useEffect } from 'react';
import { createStore, useStore } from 'zustand';
import { $api } from '../../api/client';
import type { components } from '../../generated/openapi';
import type { Operation } from './api';

export type Lane = 'local' | 'hosted';
export type CatalogueEntry = {name: string; lane: Lane};
type EngineRow = Pick<components['schemas']['Engine'], 'id' | 'name' | 'lane'>;

/** Each engine id's name and lane: the matrix's row for an engine it lists (the audio, mesh
 *  and local image engines), else the operation that carries the id: an image preset, which
 *  runs on the local ComfyUI, or a hosted model, which is reached through its provider. */
export function catalogueFrom(operations: readonly Pick<Operation, 'id' | 'label' | 'provider'>[],
  engines: readonly EngineRow[]): ReadonlyMap<string, CatalogueEntry> {
  const entries = new Map<string, CatalogueEntry>(operations.map(op => [op.id, {name: op.label, lane: op.provider ? 'hosted' : 'local'}]));
  for (const row of engines) entries.set(row.id, {name: row.name, lane: row.lane === 'external' ? 'hosted' : 'local'});
  return entries;
}

const store = createStore<{entries: ReadonlyMap<string, CatalogueEntry>}>(() => ({entries: new Map()}));

/** Replaces the catalogue the surfaces read. */
export const setCatalogue = (entries: ReadonlyMap<string, CatalogueEntry>) => store.setState({entries});

/** An engine's display name, or its id when the catalogue does not list it. */
export const engineName = (id: string): string => store.getState().entries.get(id)?.name ?? id;

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
