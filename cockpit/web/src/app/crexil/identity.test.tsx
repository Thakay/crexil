// Rendered call sites of a blind take's identity: the inspection head, the provenance
// panel and the record's material and run rows say "Engine hidden" or withhold the fact,
// and never print the server's masked placeholder as if it were an engine.
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Material, Snapshot } from './api';

const fixtures = vi.hoisted(() => ({record: {request: null, controls: {}, submitted: null, regions: null} as Record<string, unknown>}));
vi.mock('../../api/client', () => ({
  $api: {useQuery: (_method: string, path: string) => ({
    data: path === '/api/crexil/material/{nid}/record' ? fixtures.record
      : path === '/api/crexil/projects/{wid}/record' ? {decisions: [], material_ids: ['blind-take'], direction_ids: [], turn_ids: ['t1'], next_before: null}
      : undefined,
    refetch: vi.fn(), isLoading: false, error: null,
  })},
  fetchClient: {},
}));

const { Inspection } = await import('./Filmstrip');
const { MaterialProvenance } = await import('./MaterialProvenance');
const { RecordRoom } = await import('./RecordRoom');

const node = {id: 'blind-take', engine: 'hidden', identity_hidden: true, payload: 'image', take: 1, seed: null, created_at: '2026-10-05T00:00:00Z',
  committed_usd: null, charged_usd: null, available: true, parents: [], turn_idx: 0, slot_pos: 1, output_index: 0, slot_id: 's2',
  material: null, retained_files: [], prompt: 'A brass lamp', set_aside: false, promoted_at: null, verb: 'generate'} as unknown as Material;
const data = {project: {id: 'p'}, nodes: [node], directions: [], execution_groups: [], event_seq: 1,
  turns: [{id: 't1', idx: 0, created_at: '2026-10-05T00:00:00Z', prompt: 'A brass lamp',
    slots: [{id: 's1', engine: 'hidden', identity_hidden: true, status: 'failed'}, {id: 's2', engine: 'hidden', identity_hidden: true, status: 'landed'}]}],
} as unknown as Snapshot;

describe('a blind take wherever it is named', () => {
  afterEach(cleanup);
  it('the inspection head says "Engine hidden"', () => {
    render(<Inspection node={node} data={data} close={() => {}} navigate={() => {}} openGroup={() => {}} openRecord={() => {}} returnLabel="Project"/>);
    const head = screen.getByRole('region', {name: 'Inspect material'}).querySelector('header')!;
    expect(head).toHaveTextContent('Engine hidden');
    expect(head).not.toHaveTextContent(/·?\s*hidden$/);
  });
  it('the provenance panel withholds the operation, seed and charge until the reveal', () => {
    render(<MaterialProvenance node={node} data={data} navigate={() => {}} openRecord={() => {}}/>);
    expect(screen.getAllByText('Withheld until the reveal')).toHaveLength(2);
    expect(screen.getByText("Shown after the reveal. The run's totals stay visible.")).toBeInTheDocument();
    expect(screen.queryByText('hidden')).not.toBeInTheDocument();
    expect(screen.queryByText('No confirmed charge recorded')).not.toBeInTheDocument();
    const seed = screen.getByText('Seed').nextElementSibling;
    expect(seed).toHaveTextContent('Withheld until the reveal');               // never "Not recorded" for a masked seed
  });
  it('the record names a blind take and its run without the placeholder', () => {
    render(<RecordRoom data={data} close={() => {}} inspect={() => {}} openDirection={() => {}} openRun={() => {}}/>);
    const room = screen.getByRole('region', {name: 'Project record'});
    fireEvent.click(within(room).getByRole('button', {name: /^Material/}));
    expect(within(room).getByText('Engine hidden')).toBeInTheDocument();
    fireEvent.click(within(room).getByRole('button', {name: /^Runs|^Attempts/}));
    expect(within(room).getByText('Engine hidden · Failed / Engine hidden · Landed')).toBeInTheDocument();
  });
});
