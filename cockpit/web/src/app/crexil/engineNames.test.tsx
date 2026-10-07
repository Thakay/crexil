// Rendered call sites of a hosted engine's name, in a grid room against a stubbed server:
// two models of one vendor, named as OpenRouter's catalogue names them, read apart on their
// take faces and on an attempt's face, each by its model; inspection heads the take with
// the full name. Fetch is stubbed; the client resolves globalThis.fetch lazily (api/client.ts).
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const hosted = (id: string, label: string) => ({id, label, revision: 'r1', actions: ['generate'], inputs: [], reasons: [],
  source_limit: 0, sources: [], source_roles: [], availability: 'unverified', evidence: 'declared',
  execution_location: 'Hosted provider', data_destination: 'https://openrouter.ai/api/v1', provider: 'openrouter',
  output_payload: 'image', maximum_usd_per_attempt: null, price_usd: .04, price_unit: 'image', tariff: null,
  cost_basis: 'Published tariffs can reserve expected spend', limitations: [], takes_per_attempt: 1, attempt_max: 16});
const V45 = 'bytedance-seed/seedream-4.5', LITE = 'bytedance-seed/seedream-5-0-lite';
const OPERATIONS = [hosted(V45, 'ByteDance Seed: Seedream 4.5'), hosted(LITE, 'ByteDance Seed: Seedream 5.0 Lite')];

const slot = (pos: number, engine: string, status: 'landed' | 'running') => ({id: `s${pos}`, turn_id: 't1', pos, engine, status,
  attempt: 1, seed: null, cause: null, detail: null, retry_of: null, kind: null, job_ref: null, started_at: '2026-10-06T10:00:00Z',
  finished_at: null, frame: null, facts: null, committed_usd: .04, charged_usd: status === 'landed' ? .04 : null,
  created_at: '2026-10-06T10:00:00Z', identity_hidden: null});
const take = (pos: number, engine: string) => ({id: `n${pos}`, workspace_id: 'p1', turn_id: 't1', slot_id: `s${pos}`, take: 1,
  payload: 'image', verb: 'generate', prompt: 'A stout dwarf blacksmith', engine, seed: null, committed_usd: .04, charged_usd: .04,
  set_aside: false, available: true, parents: [], turn_idx: 0, slot_pos: pos, output_index: 0, material: null, retained_files: [],
  promoted_at: null, identity_hidden: null, created_at: '2026-10-06T10:00:10Z'});
const control = (scope: string, target_id: string) => ({scope, target_id, paused: false, revision: 1, cancel_requested: 0,
  actor: null, reason: '', last_action: null});
const snapshot = () => ({
  project: {id: 'p1', name: 'Dwarf blacksmith', brief: '', revision: 1, instructions: '', limits: {}},
  directions: [{id: 'd1', workspace_id: 'p1', title: 'Image casting', state: 'submitted', revision: 2, row_idx: 0, selection: [],
    node_ids: ['n0', 'n1'], turn_ids: ['t1'], set_aside: false, sequence_template: false, latest_decision: null,
    setup: {action: 'generate', prompt: 'A stout dwarf blacksmith', references: [], operations: []}}],
  nodes: [take(0, V45), take(1, LITE)], execution_groups: [], blind_runs: [], sequences: [], charged_usd: .08, accounting: {},
  restoration: null, execution_controls: [control('project', 'p1'), control('run', 't1')],
  turns: [{id: 't1', idx: 0, prompt: 'A stout dwarf blacksmith', created_at: '2026-10-06T10:00:00Z', dials: {},
    slots: [slot(0, V45, 'landed'), slot(1, LITE, 'landed'), slot(2, LITE, 'running')]}],
});
const queue = () => ({seq: 1, now: '2026-10-06T10:01:00Z', depth: 0, late: [], checks: [], retries: [], held: [], benches: [],
  lanes: {local: {label: 'Local', depth: 0, slots: []}, external: {label: 'Hosted', concurrency: 4, active: 1, slots: []}},
  gpu: {reading: null, rechecking: false, manual_hold: false}});

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}});
  Object.defineProperty(document, 'fonts', {configurable: true, value: {ready: Promise.resolve()}});
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    if (path === '/api/crexil/projects/p1') return Response.json(snapshot());
    if (path === '/api/queue') return Response.json(queue());
    if (path === '/api/crexil/operations') return Response.json(OPERATIONS);
    if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: []});
    if (path === '/api/costs') return Response.json({today_usd: 0, month_usd: 0, day: '2026-10-06', month: '2026-10'});
    if (path === '/api/health') return Response.json({build: {id: 'test'}});
    if (path === '/api/crexil/material/n1/record') return Response.json({request: null, controls: {}, submitted: null, regions: null});
    throw new Error(`unstubbed fetch: ${input.method} ${path}`);
  }));
  location.hash = '#/projects/p1';
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  location.hash = '';
});

/** A take face's line as drawn: its label and engine, then its money. */
const line = (face: HTMLElement) => [...face.querySelector('footer')!.children].map(part => part.textContent);

describe('a hosted engine on a face reads by its model (the catalogue names it in full)', () => {
  it("names each take's model on its face, and the full name where the take is inspected", async () => {
    render(<App/>);
    fireEvent.click(await screen.findByRole('button', {name: 'Grid ›'}));
    const v45 = await screen.findByRole('button', {name: 'Act on 0A'});
    expect(line(v45)).toEqual(['0A · Seedream 4.5', '$0.04']);
    const lite = screen.getByRole('button', {name: 'Act on 0B'});
    expect(line(lite)).toEqual(['0B · Seedream 5.0 Lite', '$0.04']);
    // the attempt still running names its model too, on its head and its running line
    const running = screen.getByRole('group', {name: 'Attempt 0C · running'});
    expect(within(running).getByText('0C · Seedream 5.0 Lite')).toBeInTheDocument();
    expect(within(running).getByText('Seedream 5.0 Lite · running')).toBeInTheDocument();
    expect(screen.queryByText(/ByteDance Seed/)).toBeNull();
    // inspection names the engine in full
    fireEvent.doubleClick(lite);
    const head = (await screen.findByRole('region', {name: 'Inspect material'})).querySelector('header')!;
    expect(head).toHaveTextContent('ByteDance Seed: Seedream 5.0 Lite');
  });
});
