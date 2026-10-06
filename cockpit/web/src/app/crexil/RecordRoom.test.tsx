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
const { DecisionCard, RecordRoom, latestVersions } = await import('./RecordRoom');

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

describe('a revised decision shows once, as its latest reason (law 23)', () => {
  afterEach(cleanup);
  const decision = (event_id: string, reason: string, extra: Record<string, unknown> = {}) => ({id: 'root', event_id, seq: 1,
    created_at: '2026-10-06T10:00:00Z', kind: 'selection', actor: {kind: 'human', name: 'Artist'}, reason, node_ids: [],
    direction_id: null, turn_id: null, sequence_id: null, previous_id: null, revised_by: null, historical: false, ...extra}) as never;
  const chosen = decision('e1', '', {revised_by: 'e2'});
  const reasoned = decision('e2', 'Warmest of the six.', {previous_id: 'e1'});
  const ids = (items: unknown[]) => items.map(item => (item as {event_id: string}).event_id);
  it("lists a choice and the reason its participant gave it next as one decision", () => {
    expect(ids(latestVersions([reasoned, decision('e3', 'Other.'), chosen]))).toEqual(['e2', 'e3']);
    expect(ids(latestVersions([chosen]))).toEqual(['e1']);
  });
  it("keeps every other version's card, its author and its text (law 23)", () => {
    const scouts = decision('s1', 'Strong silhouette.', {revised_by: 's2', actor: {kind: 'agent', name: 'Scout'}});
    expect(ids(latestVersions([decision('s2', 'Keep the silhouette.', {previous_id: 's1'}), scouts]))).toEqual(['s2', 's1']);
    const others = decision('o1', '', {revised_by: 'o2', actor: {kind: 'agent', name: 'Scout'}});
    expect(ids(latestVersions([decision('o2', 'Chosen for its edge.', {previous_id: 'o1'}), others]))).toEqual(['o2', 'o1']);
  });
  it('says a reason given with the choice, or a reason revised later', () => {
    render(<DecisionCard item={reasoned} previous={chosen} data={data} inspect={() => {}}/>);
    expect(screen.getByText('Display choice')).toBeInTheDocument();
    expect(screen.getByText('Reason given with the choice.')).toBeInTheDocument();
    cleanup();
    render(<DecisionCard item={reasoned} previous={decision('e1', 'An earlier reason.', {revised_by: 'e2'})} data={data} inspect={() => {}}/>);
    expect(screen.getByText('Reason revised; the earlier version is retained.')).toBeInTheDocument();
  });
  it('names the participant with the one author line', () => {
    render(<DecisionCard item={decision('e4', 'Picked.', {actor: {kind: 'agent', name: 'voice-director'}})} data={data} inspect={() => {}}/>);
    expect(screen.getByText('Agent · voice-director')).toBeInTheDocument();
  });
});
