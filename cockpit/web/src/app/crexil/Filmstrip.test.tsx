// A run's grid room against a stubbed server: a held paid attempt's TRY ONCE MORE and an
// attempt's CANCEL ATTEMPT call their authored routes with the attempt the face shows; a
// refusal's plain words show on the error line; the face changes only when the server's
// state does. Fetch is stubbed; the client resolves globalThis.fetch lazily (api/client.ts).
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const sfx = {id: 'audio:sfx:elevenlabs-sfx', label: 'Make a sound · ElevenLabs SFX', revision: 'r1', actions: ['sfx'], inputs: [],
  reasons: [], source_limit: 0, sources: [], source_roles: [], availability: 'unverified', evidence: 'declared',
  execution_location: 'Hosted provider', data_destination: 'https://api.elevenlabs.io', provider: 'elevenlabs', output_payload: 'audio',
  maximum_usd_per_attempt: null, price_usd: .002, price_unit: 'second', tariff: {unit: 'second', micro_usd: 2000},
  cost_basis: 'Published tariff, 0.002 US dollars per second', limitations: [], takes_per_attempt: 1, attempt_max: 16};
const BENCH = 'engine:elevenlabs-sfx@turn:t1';
const CAUSE = 'elevenlabs-sfx failed twice in this run, so its remaining attempts here are held.';

type Status = 'failed' | 'held' | 'queued' | 'skipped';
let statuses: Status[] = [];
const slot = (pos: number, status: Status) => ({id: `s${pos}`, turn_id: 't1', pos, engine: 'elevenlabs-sfx', status, attempt: 1, seed: null,
  cause: status === 'failed' ? 'ElevenLabs could not make this sound.' : null, detail: null, retry_of: null,
  kind: status === 'failed' ? 'error' : null, job_ref: null, started_at: null, finished_at: null, frame: null, facts: null,
  committed_usd: .01, charged_usd: null, created_at: '2026-10-06T10:00:00Z', identity_hidden: null});
const control = (scope: string, target_id: string) => ({scope, target_id, paused: false, revision: 1, cancel_requested: 0,
  actor: null, reason: '', last_action: null});
const snapshot = () => ({
  project: {id: 'p1', name: 'Forge sounds', brief: '', revision: 1, instructions: '', limits: {}},
  directions: [{id: 'd1', workspace_id: 'p1', title: 'Anvil strikes', state: 'submitted', revision: 2, row_idx: 0, selection: [],
    node_ids: [], turn_ids: ['t1'], set_aside: false, sequence_template: false, latest_decision: null,
    setup: {action: 'sfx', prompt: 'An anvil strike', references: [], operations: []}}],
  nodes: [], execution_groups: [], blind_runs: [], sequences: [], charged_usd: 0, accounting: {}, restoration: null,
  execution_controls: [control('project', 'p1'), control('run', 't1')],
  turns: [{id: 't1', idx: 0, prompt: 'An anvil strike', created_at: '2026-10-06T10:00:00Z', dials: {}, slots: statuses.map((s, i) => slot(i, s))}],
});
// The queue lists every attempt the bench holds, here one in another project too: a resume sends both.
const queue = () => ({seq: 1, now: '2026-10-06T10:01:00Z', depth: 0, late: [], checks: [], retries: [],
  lanes: {local: {label: 'Local', depth: 0, slots: []}, external: {label: 'Hosted', concurrency: 4, active: 0, slots: []}},
  held: [{id: 's2', turn_id: 't1', bench: BENCH, committed_usd: .01}, {id: 'elsewhere', turn_id: 't9', bench: BENCH, committed_usd: .01}]
    .filter(row => row.id !== 's2' || statuses[2] === 'held'),
  benches: [{scope: BENCH, kind: 'repeat', cause: CAUSE}],
  gpu: {reading: null, rechecking: false, manual_hold: false}});

type Sent = {path: string; body: {request_id: string; attempt: number; actor: unknown; reason: string}};
let moves: Sent[] = [];
let answers: {status: number; body: unknown; then?: Status}[] = [];

beforeEach(() => {
  moves = [];
  answers = [];
  statuses = ['failed', 'failed', 'held'];
  vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}});
  Object.defineProperty(document, 'fonts', {configurable: true, value: {ready: Promise.resolve()}});
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    if (input.method === 'POST' && path.startsWith('/api/crexil/attempts/')) {
      moves.push({path, body: await input.json()});
      const answer = answers.shift()!;
      if (answer.then) statuses[2] = answer.then;           // the server's state after the move
      return Response.json(answer.body, {status: answer.status});
    }
    if (path === '/api/crexil/projects/p1') return Response.json(snapshot());
    if (path === '/api/queue') return Response.json(queue());
    if (path === '/api/crexil/operations') return Response.json([sfx]);
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

const moved = (action: 'resume' | 'cancel', words: string) => ({slot_id: 's2', attempt: 1, action, words, released: 2,
  actor: {kind: 'human', name: 'Artist'}, reason: ''});

async function heldFace() {
  render(<App/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Grid ›'}));
  const face = await screen.findByRole('group', {name: 'Attempt 0C · held'});
  // the price is what every attempt the bench holds reserves, read from the queue view (law 6)
  await within(face).findByRole('button', {name: 'TRY ONCE MORE · UP TO $0.02'});
  return face;
}

describe("a held attempt's moves from its face (laws 4, 6 and 23)", () => {
  it('sends TRY ONCE MORE for the attempt the face shows; a refusal reads on the error line and changes nothing', async () => {
    const face = await heldFace();
    const detail = 'This attempt is not held, so there is nothing to resume. Refresh it.';
    answers = [{status: 409, body: {detail}}];
    fireEvent.click(within(face).getByRole('button', {name: 'TRY ONCE MORE · UP TO $0.02'}));
    expect(await screen.findByRole('alert')).toHaveTextContent(detail);
    expect(moves).toEqual([{path: '/api/crexil/attempts/s2/resume',
      body: {request_id: expect.any(String), actor: {kind: 'human', name: 'Artist'}, attempt: 1, reason: ''}}]);
    expect(screen.getByRole('group', {name: 'Attempt 0C · held'})).toBeInTheDocument();
  });
  it('changes the face only when the server says the attempt moved, then cancels it from its new face', async () => {
    const face = await heldFace();
    // accepted, but the server still reads it as held: nothing is assumed
    answers = [{status: 200, body: moved('resume', 'Trying once more: 2 held attempts went back to the queue.')}];
    fireEvent.click(within(face).getByRole('button', {name: 'TRY ONCE MORE · UP TO $0.02'}));
    await waitFor(() => expect(moves).toHaveLength(1));
    await waitFor(() => expect(within(face).getByRole('button', {name: 'TRY ONCE MORE · UP TO $0.02'})).not.toHaveAttribute('aria-disabled'));
    expect(screen.getByRole('group', {name: 'Attempt 0C · held'})).toBeInTheDocument();
    // the cancel sits last and stops it; the face reads the server's answer once it is recorded
    expect(within(face).getAllByRole('button').at(-1)).toHaveTextContent('CANCEL ATTEMPT');
    answers = [{status: 200, body: moved('cancel', 'Skipped: nothing more is sent for this attempt.'), then: 'skipped'}];
    fireEvent.click(within(face).getByRole('button', {name: 'CANCEL ATTEMPT'}));
    expect(await screen.findByRole('group', {name: 'Attempt 0C · skipped'})).toBeInTheDocument();
    expect(moves.map(move => [move.path, move.body.attempt])).toEqual([['/api/crexil/attempts/s2/resume', 1], ['/api/crexil/attempts/s2/cancel', 1]]);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
