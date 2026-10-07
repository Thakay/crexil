// The lineage canvas in the project's Filmstrip, against a stubbed server: it observes the
// layout once per record, so a render that brings no new record never subscribes it again,
// while a new record, whose cards it must observe, and a change of the acting takes, which
// lights another lineage, do. Fetch is stubbed; the client resolves globalThis.fetch lazily
// (api/client.ts).
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Crexil } from './Filmstrip';

const slot = (pos: number) => ({id: `s${pos}`, turn_id: 't1', pos, engine: 'raw-sdxl', status: 'landed', attempt: 1, seed: 7,
  cause: null, detail: null, retry_of: null, kind: null, job_ref: null, started_at: '2026-10-06T10:00:00Z',
  finished_at: '2026-10-06T10:00:10Z', frame: null, facts: null, committed_usd: 0, charged_usd: 0,
  created_at: '2026-10-06T10:00:00Z', identity_hidden: null});
const take = (pos: number) => ({id: `n${pos}`, workspace_id: 'p1', turn_id: 't1', slot_id: `s${pos}`, take: 1, payload: 'image',
  verb: 'generate', prompt: 'A stout dwarf blacksmith', engine: 'raw-sdxl', seed: 7, committed_usd: 0, charged_usd: 0,
  set_aside: false, available: true, parents: [], turn_idx: 0, slot_pos: pos, output_index: 0, material: null, retained_files: [],
  promoted_at: null, identity_hidden: null, created_at: '2026-10-06T10:00:10Z'});
const control = (scope: string, target_id: string) => ({scope, target_id, paused: false, revision: 1, cancel_requested: 0,
  actor: null, reason: '', last_action: null});

/** What the server's record holds: its event sequence, the takes row 0's run made, and what the project has spent. */
let record = {seq: 5, takes: 1, charged: 0};
/** Row 0's run made the takes; row 1 is a draft that continues take 0A, so one lineage line joins them. */
const snapshot = () => {
  const ids = Array.from({length: record.takes}, (_, pos) => `n${pos}`);
  return {
    project: {id: 'p1', name: 'Dwarf blacksmith', brief: '', revision: 1, instructions: '', limits: {}},
    directions: [
      {id: 'd1', workspace_id: 'p1', title: 'Image casting', state: 'submitted', revision: 2, row_idx: 0, selection: ids,
        node_ids: ids, turn_ids: ['t1'], set_aside: false, sequence_template: false, latest_decision: null,
        setup: {action: 'generate', prompt: 'A stout dwarf blacksmith', references: [], operations: []}},
      {id: 'd2', workspace_id: 'p1', title: 'Continue the blacksmith', state: 'draft', revision: 1, row_idx: 1, selection: [],
        node_ids: [], turn_ids: [], set_aside: false, sequence_template: false, latest_decision: null,
        setup: {action: 'continue', prompt: '', references: [{node_id: 'n0', contribution: ''}], operations: []}},
    ],
    nodes: ids.map((_, pos) => take(pos)), execution_groups: [], blind_runs: [], sequences: [], charged_usd: record.charged,
    accounting: {}, restoration: null, execution_controls: [control('project', 'p1'), control('run', 't1')], event_seq: record.seq,
    turns: [{id: 't1', idx: 0, prompt: 'A stout dwarf blacksmith', created_at: '2026-10-06T10:00:00Z', dials: {},
      slots: ids.map((_, pos) => slot(pos))}],
  };
};

/** Every ResizeObserver made, with what it observes and whether it was let go. */
let observers: {targets: Element[]; disconnected: boolean}[] = [];

beforeEach(() => {
  record = {seq: 5, takes: 1, charged: 0};
  observers = [];
  vi.stubGlobal('ResizeObserver', class {
    seen = {targets: [] as Element[], disconnected: false};
    constructor() {observers.push(this.seen);}
    observe(target: Element) {this.seen.targets.push(target);}
    unobserve() {}
    disconnect() {this.seen.disconnected = true;}
  });
  Object.defineProperty(document, 'fonts', {configurable: true, value: {ready: Promise.resolve()}});
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    if (path === '/api/crexil/projects/p1') return Response.json(snapshot());
    if (path === '/api/crexil/operations') return Response.json([]);
    if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: []});
    if (path === '/api/costs') return Response.json({today_usd: 0, month_usd: 0, day: '2026-10-06', month: '2026-10'});
    if (path === '/api/health') return Response.json({build: {id: 'test'}});
    throw new Error(`unstubbed fetch: ${input.method} ${path}`);
  }));
  location.hash = '#/projects/p1';
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  location.hash = '';
});

/** The project surface as App mounts it, with a query client the test can ask to read again. */
async function project() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  render(<QueryClientProvider client={client}><HotkeysProvider><Crexil/></HotkeysProvider></QueryClientProvider>);
  const lines = await screen.findByRole('img', {name: 'Source connections'});
  await waitFor(() => expect(client.isFetching()).toBe(0));
  // The canvas's own subscriptions: the observers that watch its host.
  const canvas = () => observers.filter(observer => observer.targets.includes(lines.parentElement!));
  const readAgain = () => act(async () => {await client.refetchQueries({type: 'active'});});
  return {lines, canvas, readAgain};
}

describe('the lineage canvas measures again only when the record, the acting takes or the room change', () => {
  it('keeps its one subscription through renders that bring no new record, and draws the line', async () => {
    const {lines, canvas, readAgain} = await project();
    // the snapshot, then the operations, the engines and the costs arrived: one subscription
    expect(canvas()).toHaveLength(1);
    await waitFor(() => expect(lines).toHaveAttribute('data-connection-count', '1'));
    // the project reads again with a figure changed but the same record: the workspace renders anew
    record = {...record, charged: .25};
    await readAgain();
    expect(await screen.findByText(/This project \$0\.25/)).toBeInTheDocument();
    expect(canvas()).toHaveLength(1);
    expect(canvas()[0].disconnected).toBe(false);
  });

  it('subscribes again for a new record, observing the card it brought', async () => {
    const {canvas, readAgain} = await project();
    record = {seq: 6, takes: 2, charged: 0};
    await readAgain();
    const added = await screen.findByRole('button', {name: 'Act on 0B'});
    expect(canvas()).toHaveLength(2);
    expect(canvas()[0].disconnected).toBe(true);
    expect(canvas()[1].targets).toContain(added);
  });

  it('subscribes again when the acting takes change, and not for the same takes again', async () => {
    const {canvas} = await project();
    const face = screen.getByRole('button', {name: 'Act on 0A'});
    fireEvent.click(face);
    expect(face).toHaveAttribute('aria-pressed', 'true');
    expect(canvas()).toHaveLength(2);
    // acting on the same take again names the same set: nothing to measure anew
    fireEvent.click(face);
    expect(canvas()).toHaveLength(2);
    expect(canvas()[1].disconnected).toBe(false);
  });
});
