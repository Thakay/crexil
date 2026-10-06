// The record room's export, its blind runs, its audio facts and the name it gives this
// browser's artist.
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Material, Snapshot } from './api';
import { artist } from './api';

vi.mock('../../api/client', () => ({
  $api: {useQuery: () => ({data: {decisions: [], material_ids: ['voice', 'flying'], direction_ids: [], turn_ids: ['t1', 't2'], next_before: null},
    refetch: vi.fn(), isLoading: false, error: null})},
  fetchClient: {},
}));
const { DecisionCard, RecordRoom } = await import('./RecordRoom');

const node = (id: string, turn_id: string, extra: Record<string, unknown> = {}) => ({id, turn_id, payload: 'audio', engine: 'qwen3-tts',
  identity_hidden: null, available: true, set_aside: false, turn_idx: 0, slot_pos: 0, output_index: 0, material: null,
  audio: {duration_s: 4.4, peaks: [0.3], loudness_lufs: -18}, ...extra}) as unknown as Material;
const data = {
  project: {id: 'p1'}, event_seq: 1, directions: [], execution_groups: [],
  nodes: [node('voice', 't1'), node('flying', 't2', {identity_hidden: true, engine: 'hidden'})],
  turns: [{id: 't1', idx: 0, created_at: '2026-10-05T00:00:00Z', prompt: 'A gruff voice', slots: [{id: 's1', engine: 'qwen3-tts', status: 'landed'}]},
    {id: 't2', idx: 1, created_at: '2026-10-05T00:00:00Z', prompt: 'Another', slots: [{id: 's2', engine: 'hidden', identity_hidden: true, status: 'running'}]}],
  blind_runs: [{turn_id: 't1', revealed: true, revealed_by: {kind: 'human', name: 'Kay'}, revealed_at: '2026-10-05T01:00:00Z'},
    {turn_id: 't2', revealed: false, revealed_by: null, revealed_at: null}],
} as unknown as Snapshot;

const open = () => render(<RecordRoom data={data} close={() => {}} inspect={() => {}} openDirection={() => {}} openRun={() => {}}/>);

describe('the record room', () => {
  afterEach(() => {cleanup(); localStorage.clear();});
  it('exports the decision record in one step, and says it holds no audio and is not a dataset', () => {
    open();
    const link = screen.getByRole('link', {name: 'Export decision record'});
    expect(link).toHaveAttribute('href', '/api/crexil/projects/p1/decisions.jsonl');
    expect(link).toHaveAttribute('download');
    expect(screen.getByText(/It holds no audio and is not a dataset\./)).toBeInTheDocument();
  });
  it("lists an audio take's measured length, and no take of a blind run still in flight (law 17)", () => {
    open();
    const room = screen.getByRole('region', {name: 'Project record'});
    fireEvent.click(within(room).getByRole('button', {name: /^Material$/}));
    expect(within(room).getByText('Length · 0:04.4')).toBeInTheDocument();
    expect(within(room).getAllByRole('button', {name: /Inspect/})).toHaveLength(1);
  });
  it('says which runs were blind and who revealed them', () => {
    open();
    fireEvent.click(screen.getByRole('button', {name: 'Runs'}));
    expect(screen.getByText(/^Blind run · revealed by Kay · /)).toBeInTheDocument();
    expect(screen.getByText('Blind run · engines hidden until the reveal')).toBeInTheDocument();
    // a blind run in flight says how far it has got, never which attempt landed first
    expect(screen.getByText('In progress · 0 of 1 attempts done')).toBeInTheDocument();
    expect(screen.queryByText(/Engine hidden · running/)).toBeNull();
  });
  it("keeps the name the record gives this browser's artist", () => {
    open();
    const name = screen.getByRole('textbox', {name: 'Recorded as'});
    expect(name).toHaveValue('Artist');
    fireEvent.change(name, {target: {value: 'Kay'}});
    expect(artist()).toEqual({kind: 'human', name: 'Kay'});
  });
});

describe('a decision card', () => {
  afterEach(cleanup);
  const decision = (event_id: string, reason: string, extra: Record<string, unknown> = {}) => ({id: 'root', event_id, seq: 1,
    created_at: '2026-10-06T10:00:00Z', kind: 'selection', actor: {kind: 'human', name: 'Artist'}, reason, node_ids: [],
    direction_id: null, turn_id: null, sequence_id: null, previous_id: null, revised_by: null, historical: false, ...extra}) as never;
  it('names the participant with the one author line (law 23)', () => {
    render(<DecisionCard item={decision('e4', 'Picked.', {actor: {kind: 'agent', name: 'voice-director'}})} data={data} inspect={() => {}}/>);
    expect(screen.getByText('Agent · voice-director')).toBeInTheDocument();
    cleanup();
    render(<DecisionCard item={decision('e5', 'Picked.')} data={data} inspect={() => {}}/>);
    expect(screen.getByText('Artist')).toBeInTheDocument();
    expect(screen.queryByText('Artist · Artist')).toBeNull();
  });
});
