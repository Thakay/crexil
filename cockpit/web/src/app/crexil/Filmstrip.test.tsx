// A run's grid room against a stubbed server: a held paid attempt's TRY ONCE MORE and an
// attempt's CANCEL ATTEMPT call their authored routes with the attempt the face shows; a
// refusal's plain words show on the error line; the face changes only when the server's
// state does; the GRID › door Run focuses opens the room with the run just sent in view.
// Fetch is stubbed; the client resolves globalThis.fetch lazily (api/client.ts).
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
    if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: [{id: 'elevenlabs-sfx', name: 'ElevenLabs Sound Effects', lane: 'external'}]});
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

describe('the GRID › door Run focuses opens the room with the run just sent in view', () => {
  // Row 0 holds nine concept images, then a voice draft: once it runs, its section lies past the
  // room's right edge, as in the seed script's project.
  const voice = {...sfx, id: 'audio:voice-design:qwen3-tts', label: 'Design voice · Qwen3-TTS 1.7B', revision: 'r2', actions: ['voice-design'],
    execution_location: 'Local voice runner', data_destination: 'http://127.0.0.1:8190', provider: 'voice-runner', maximum_usd_per_attempt: 0,
    price_usd: 0, price_unit: 'attempt', tariff: {unit: 'attempt', micro_usd: 0}, cost_basis: 'Unmetered local operation', takes_per_attempt: 3};
  const CONCEPTS = Array.from({length: 9}, (_, pos) => `n${pos}`);
  const attempt = (id: string, turn: string, pos: number, engine: string, status: 'landed' | 'queued') => ({...slot(pos, 'queued'), id,
    turn_id: turn, engine, status, committed_usd: 0, charged_usd: status === 'landed' ? 0 : null});
  const concept = (pos: number) => ({id: `n${pos}`, workspace_id: 'p1', turn_id: 't1', slot_id: `c${pos}`, take: 1, payload: 'image',
    verb: 'generate', prompt: 'A stout dwarf blacksmith', engine: 'raw-sdxl', seed: pos, committed_usd: 0, charged_usd: 0, set_aside: false,
    available: true, parents: [], turn_idx: 0, slot_pos: pos, output_index: 0, material: null, retained_files: [], promoted_at: null,
    identity_hidden: null, created_at: '2026-10-06T10:00:10Z'});
  let sent = false;
  const project = () => ({
    project: {id: 'p1', name: 'Dwarf blacksmith', brief: '', revision: 1, instructions: '', limits: {}},
    directions: [
      {id: 'd1', workspace_id: 'p1', title: 'Concept images', state: 'submitted', revision: 2, row_idx: 0, selection: CONCEPTS,
        node_ids: CONCEPTS, turn_ids: ['t1'], set_aside: false, sequence_template: false, latest_decision: null,
        setup: {action: 'generate', prompt: 'A stout dwarf blacksmith', references: [], operations: []}},
      {id: 'd2', workspace_id: 'p1', title: 'Blacksmith voice', state: sent ? 'submitted' : 'draft', revision: sent ? 4 : 3, row_idx: 0,
        selection: [], node_ids: [], turn_ids: sent ? ['t2'] : [], set_aside: false, sequence_template: false, latest_decision: null,
        setup: {action: 'voice-design', prompt: 'A gruff dwarf blacksmith, warm and low.', references: [], controls: {}, instructions: '',
          attempt_limit: 1, spending_limit_usd: 0, spending_policy: 'hard_cap', purpose: 'creative_request', blind: false,
          operations: [{operation_id: voice.id, revision: voice.revision, count: 1, controls: {}}]}},
    ],
    nodes: CONCEPTS.map((_, pos) => concept(pos)), execution_groups: [], blind_runs: [], sequences: [], charged_usd: 0, accounting: {},
    restoration: null, execution_controls: [control('project', 'p1'), control('run', 't1'), ...(sent ? [control('run', 't2')] : [])],
    event_seq: sent ? 12 : 11,
    turns: [{id: 't1', idx: 0, prompt: 'A stout dwarf blacksmith', created_at: '2026-10-06T10:00:00Z', dials: {},
      slots: CONCEPTS.map((_, pos) => attempt(`c${pos}`, 't1', pos, 'raw-sdxl', 'landed'))},
    ...(sent ? [{id: 't2', idx: 1, prompt: 'A gruff dwarf blacksmith, warm and low.', created_at: '2026-10-06T10:05:00Z', dials: {},
      slots: [attempt('v0', 't2', 0, 'qwen3-tts', 'queued')]}] : [])],
  });
  beforeEach(() => {
    sent = false;
    sessionStorage.clear();
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === '/api/crexil/projects/p1') return Response.json(project());
      if (path === '/api/crexil/directions/d2/review') return Response.json({direction_id: 'd2', ready: true, ready_attempts: 1,
        blocked_attempts: 0, attempts: 1, errors: [], maximum_usd: 0, reserved_usd: 0, reserved_micro_usd: 0, revision: 3,
        token: 'a'.repeat(64), snapshot: {}, automatic_retry: {[voice.id]: true}});
      if (path === '/api/crexil/directions/d2/submit') {sent = true; return Response.json({turn_id: 't2'});}
      if (path === '/api/crexil/projects/p1/record') return Response.json({decisions: [], material_ids: [], next_before: null,
        direction_ids: ['d1', 'd2'], turn_ids: sent ? ['t1', 't2'] : ['t1']});
      if (path === '/api/crexil/operations') return Response.json([voice]);
      if (path === '/api/queue') return Response.json({...queue(), held: [], benches: []});
      if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: []});
      if (path === '/api/costs') return Response.json({today_usd: 0, month_usd: 0, day: '2026-10-06', month: '2026-10'});
      if (path === '/api/health') return Response.json({build: {id: 'test'}});
      throw new Error(`unstubbed fetch: ${input.method} ${path}`);
    }));
  });

  /** Opens the voice draft from its starter, reviews and runs it; resolves to the row's GRID ›
   *  door once Run has focused it. */
  async function ran() {
    render(<App/>);
    fireEvent.click(await screen.findByRole('button', {name: 'Open preparation: Blacksmith voice'}));
    const drawer = await screen.findByRole('region', {name: 'Preparation'});
    fireEvent.click(within(drawer).getByRole('button', {name: 'Review run'}));
    fireEvent.click(await within(drawer).findByRole('button', {name: 'Run 1 attempt · free'}));
    const door = screen.getByRole('button', {name: 'Grid ›'});
    await waitFor(() => expect(door).toHaveFocus());
    return door;
  }

  /** Lays the row out as a browser would: its strip shows x from 200 to 1200 with the 3 px
   *  padding the row's stylesheet gives it, so a row's first section starts at 203, and the
   *  voice section spans `left` to `right`. Resolves to the scroll offsets written to the strip. */
  function laidOut(left: number, right: number) {
    const strip = document.querySelector<HTMLElement>('#row-0 [data-lineage-strip]')!;
    const section = document.querySelector<HTMLElement>('[data-direction="d2"]')!;
    const written: number[] = [];
    strip.style.paddingLeft = '3px';
    Object.defineProperty(strip, 'clientWidth', {configurable: true, value: 1000});
    Object.defineProperty(strip, 'scrollLeft', {configurable: true, get: () => written.at(-1) ?? 0,
      set: (offset: number) => {written.push(offset);}});
    const box = (from: number, to: number) => ({left: from, right: to, x: from, width: to - from, top: 0, y: 0, bottom: 300, height: 300}) as DOMRect;
    strip.getBoundingClientRect = () => box(200, 1200);
    section.getBoundingClientRect = () => box(left, right);
    return written;
  }

  /** Leaves the open grid room by its head's Escape control. */
  async function leave() {
    fireEvent.click(screen.getByRole('button', {name: 'Esc · Project'}));
    await waitFor(() => expect(screen.queryByText('Grid · Turn 0')).toBeNull());
  }

  it('brings a sent run that lies past the edge to where the first section starts; the door keeps focus', async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([1497]);
    expect(door).toHaveFocus();
  });

  it('gives the row its scroll back when the room closes, once, so the Filmstrip and the next room find it where it was', async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    await leave();
    expect(written).toEqual([1497, 0]);
    // the artist scrolls the Filmstrip's row; the next room opens on it, with nothing given back again
    const strip = document.querySelector<HTMLElement>('#row-0 [data-lineage-strip]')!;
    strip.scrollLeft = 300;
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    await leave();
    expect(written).toEqual([1497, 0, 300]);
  });

  it('marks the door Run armed, though Run was clicked, so its focus shows, until it opens the room or loses its focus', async () => {
    let door = await ran();
    expect(door).toHaveAttribute('data-armed');
    laidOut(1700, 2100);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    await waitFor(() => expect(door).not.toHaveAttribute('data-armed'));
    cleanup();
    sent = false;
    door = await ran();
    screen.getByRole('button', {name: 'Record ›'}).focus();
    await waitFor(() => expect(door).not.toHaveAttribute('data-armed'));
  });

  it('keeps the door armed through a window switch: a blur with no related target while the document lost focus', async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    const focused = vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    fireEvent.blur(door);
    focused.mockRestore();
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([1497]);
  });

  it('disarms the door when focus leaves it for nothing in a page that keeps focus', async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    fireEvent.blur(door);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });

  it("reveals the run for the opening the door made only: the record's way into the room leaves the row where it is", async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    await leave();
    fireEvent.click(screen.getByRole('button', {name: 'Record ›'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Runs'}));
    fireEvent.click((await screen.findAllByRole('button', {name: 'Open execution grid ›'}))[0]);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([1497, 0]);
  });

  it('never arms the door when the drawer closes without a Run', async () => {
    render(<App/>);
    fireEvent.click(await screen.findByRole('button', {name: 'Open preparation: Blacksmith voice'}));
    const drawer = await screen.findByRole('region', {name: 'Preparation'});
    fireEvent.click(within(drawer).getByRole('button', {name: 'Close · Esc'}));
    // focus goes to the preparation's own starter, never the row's door
    await waitFor(() => expect(screen.getByRole('button', {name: 'Open preparation: Blacksmith voice'})).toHaveFocus());
    const written = laidOut(1700, 2100);
    fireEvent.click(screen.getByRole('button', {name: 'Grid ›'}));
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });

  it('never moves a sent run already wholly in view (law 8)', async () => {
    const door = await ran();
    const written = laidOut(400, 900);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });

  it('never moves a sent run that already starts the row, however far it runs past the edge (law 8)', async () => {
    const door = await ran();
    const written = laidOut(203, 1803);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });

  it('leaves the row where it is when the door lost focus since Run', async () => {
    const door = await ran();
    const written = laidOut(1700, 2100);
    screen.getByRole('button', {name: 'Record ›'}).focus();
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });

  it('leaves the row where it is when no Run came first', async () => {
    render(<App/>);
    const door = await screen.findByRole('button', {name: 'Grid ›'});
    const written = laidOut(1700, 2100);
    fireEvent.click(door);
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    expect(written).toEqual([]);
  });
});

describe("a row's faces and sources: engines named as their faces name them, and a set-aside section's sources in the tray", () => {
  // A take from a hosted model whose catalogue name leads with its vendor, a take of a blind run
  // not yet revealed and a prepared image, each displayed on its row; and a section set aside
  // whose take continues the hosted one.
  const SEEDREAM = 'bytedance-seed/seedream-4.5';
  const hosted = {...sfx, id: SEEDREAM, label: 'ByteDance Seed: Seedream 4.5', actions: ['generate'], provider: 'openrouter',
    execution_location: 'Hosted provider', data_destination: 'https://openrouter.ai/api/v1', output_payload: 'image',
    maximum_usd_per_attempt: null, price_usd: .04, price_unit: 'image', tariff: null, cost_basis: 'Published tariffs can reserve expected spend'};
  const take = (id: string, turn: string | null, slotId: string | null, turnIdx: number | null, extra: object = {}) => ({id,
    workspace_id: 'p1', turn_id: turn, slot_id: slotId, take: 1, payload: 'image', verb: 'generate', prompt: 'A stout dwarf blacksmith',
    engine: SEEDREAM, seed: null, committed_usd: .04, charged_usd: .04, set_aside: false, available: true, parents: [], turn_idx: turnIdx,
    slot_pos: 0, output_index: 0, material: null, retained_files: [], promoted_at: null, identity_hidden: null,
    created_at: '2026-10-06T10:00:10Z', ...extra});
  const landed = (id: string, turn: string, engine: string, extra: object = {}) => ({...slot(0, 'queued'), id, turn_id: turn, engine,
    status: 'landed', committed_usd: .04, charged_usd: .04, ...extra});
  const direction = (id: string, title: string, row: number, turn: string | null, node: string, extra: object = {}) => ({id,
    workspace_id: 'p1', title, state: 'submitted', revision: 2, row_idx: row, selection: [node], node_ids: [node],
    turn_ids: turn ? [turn] : [], set_aside: false, sequence_template: false, latest_decision: null,
    setup: {action: 'generate', prompt: 'A stout dwarf blacksmith', references: [], operations: []}, ...extra});
  const hidden = {engine: 'hidden', identity_hidden: true, committed_usd: null, charged_usd: null};
  const prepared = {engine: null, committed_usd: null, charged_usd: null, verb: 'prepare', material: {filename: 'derived.png',
    width: 1024, height: 1024, sha256: 'a'.repeat(64), media_type: 'image/png', origin: {kind: 'prepared_image'}}};
  const project = () => ({
    project: {id: 'p1', name: 'Dwarf blacksmith', brief: '', revision: 1, instructions: '', limits: {}},
    directions: [direction('d1', 'Casting', 0, 't1', 'n1'), direction('d2', 'Blind casting', 1, 't2', 'n2'),
      direction('d3', 'Derived', 2, null, 'n3'), direction('d4', 'Variation', 3, 't4', 'n4', {set_aside: true})],
    nodes: [take('n1', 't1', 's1', 0), take('n2', 't2', 's2', 1, hidden), take('n3', null, null, null, prepared),
      take('n4', 't4', 's4', 4, {parents: ['n1']})],
    execution_groups: [], blind_runs: [{turn_id: 't2', revealed: false}], sequences: [], charged_usd: .08, accounting: {},
    restoration: null, execution_controls: [control('project', 'p1')], event_seq: 3,
    turns: [{id: 't1', idx: 0, prompt: '', created_at: '2026-10-06T10:00:00Z', dials: {}, slots: [landed('s1', 't1', SEEDREAM)]},
      {id: 't2', idx: 1, prompt: '', created_at: '2026-10-06T10:01:00Z', dials: {}, slots: [landed('s2', 't2', 'hidden', hidden)]},
      {id: 't4', idx: 4, prompt: '', created_at: '2026-10-06T10:04:00Z', dials: {}, slots: [landed('s4', 't4', SEEDREAM)]}],
  });

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === '/api/crexil/projects/p1') return Response.json(project());
      if (path === '/api/crexil/operations') return Response.json([hosted]);
      if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: []});
      if (path === '/api/costs') return Response.json({today_usd: 0, month_usd: 0, day: '2026-10-06', month: '2026-10'});
      if (path === '/api/health') return Response.json({build: {id: 'test'}});
      throw new Error(`unstubbed fetch: ${input.method} ${path}`);
    }));
  });

  /** A face's footer line, the take and its engine, by the face's name. */
  const footer = async (name: RegExp) => (await screen.findByRole('button', {name})).querySelector('footer > span')!;

  it('titles a named engine in full and any other take as its face reads: a blind take "Blind", a prepared image "Prepared"', async () => {
    render(<App/>);
    const named = await footer(/^Act on 0A/);
    await waitFor(() => expect(named).toHaveTextContent('0A · Seedream 4.5'));
    expect(named).toHaveAttribute('title', '0A · ByteDance Seed: Seedream 4.5');
    const blind = await footer(/^Act on 1A/);
    expect(blind).toHaveTextContent('1A · Blind');
    expect(blind).toHaveAttribute('title', '1A · Blind');
    const derived = await footer(/^Act on derived\.png/);
    expect(derived).toHaveTextContent('derived.png · Prepared');
    expect(derived).toHaveAttribute('title', 'derived.png · Prepared');
  });

  it("names a set-aside section's sources in the tray, which reads the row's sources for the room it shows", async () => {
    render(<App/>);
    await footer(/^Act on 0A/);
    fireEvent.click(screen.getByRole('button', {name: 'Tray · 1 ›'}));
    const variation = await screen.findByRole('region', {name: 'Variation'});
    expect(within(variation).getByText(/^1 source/)).toBeInTheDocument();
  });
});

describe("a grid room's scroll is the room's: leaving gives the row back the scroll it had when the room opened", () => {
  // Row 0 holds nine concept images and a settled local voice casting whose takes a digit plays;
  // row 1 holds one more run, which the record opens.
  const image = (id: string, turn: string, turnIdx: number, pos: number) => ({id, workspace_id: 'p1', turn_id: turn, slot_id: `${turn}-${pos}`,
    take: 1, payload: 'image', verb: 'generate', prompt: 'A stout dwarf blacksmith', engine: 'raw-sdxl', seed: pos, committed_usd: 0,
    charged_usd: 0, set_aside: false, available: true, parents: [], turn_idx: turnIdx, slot_pos: pos, output_index: 0, material: null,
    retained_files: [], promoted_at: null, identity_hidden: null, created_at: '2026-10-06T10:00:10Z'});
  const voice = (id: string, index: number) => ({...image(id, 't5', 1, 0), payload: 'audio', verb: 'voice-design', engine: 'qwen3-tts',
    slot_id: 't5-0', output_index: index, audio: {duration_s: 4, loudness_lufs: -19, peaks: [0.4, 0.6], text: null, voice: null}});
  const landed = (turn: string, pos: number, engine: string) => ({...slot(pos, 'queued'), id: `${turn}-${pos}`, turn_id: turn, engine,
    status: 'landed', committed_usd: 0, charged_usd: 0});
  const direction = (id: string, title: string, row: number, turn: string, nodes: string[], selection: string[]) => ({id, workspace_id: 'p1',
    title, state: 'submitted', revision: 2, row_idx: row, selection, node_ids: nodes, turn_ids: [turn], set_aside: false,
    sequence_template: false, latest_decision: null, setup: {action: 'generate', prompt: 'A stout dwarf blacksmith', references: [], operations: []}});
  const CONCEPTS = Array.from({length: 9}, (_, pos) => image(`n${pos}`, 't1', 0, pos));
  const VOICES = [voice('v1', 0), voice('v2', 1)];
  const project = () => ({
    project: {id: 'p1', name: 'Dwarf blacksmith', brief: '', revision: 1, instructions: '', limits: {}},
    directions: [direction('d1', 'Concept images', 0, 't1', CONCEPTS.map(node => node.id), ['n0']),
      direction('d5', 'Blacksmith voice', 0, 't5', ['v1', 'v2'], []), direction('d6', 'Forge strike', 1, 't6', ['m1'], ['m1'])],
    nodes: [...CONCEPTS, ...VOICES, image('m1', 't6', 2, 0)], execution_groups: [], blind_runs: [], sequences: [], charged_usd: 0,
    accounting: {}, restoration: null, execution_controls: [control('project', 'p1')], event_seq: 20,
    turns: [{id: 't1', idx: 0, prompt: '', created_at: '2026-10-06T10:00:00Z', dials: {}, slots: CONCEPTS.map((_, pos) => landed('t1', pos, 'raw-sdxl'))},
      {id: 't5', idx: 1, prompt: '', created_at: '2026-10-06T10:01:00Z', dials: {}, slots: [landed('t5', 0, 'qwen3-tts')]},
      {id: 't6', idx: 2, prompt: '', created_at: '2026-10-06T10:02:00Z', dials: {}, slots: [landed('t6', 0, 'raw-sdxl')]}],
  });

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === '/api/crexil/projects/p1') return Response.json(project());
      if (path === '/api/crexil/projects/p1/record') return Response.json({decisions: [], material_ids: [], next_before: null,
        direction_ids: ['d1', 'd5', 'd6'], turn_ids: ['t1', 't5', 't6']});
      if (path === '/api/crexil/operations') return Response.json([]);
      if (path === '/api/queue') return Response.json({...queue(), held: [], benches: []});
      if (path === '/api/engines') return Response.json({updated: '', external: {}, engines: []});
      if (path === '/api/costs') return Response.json({today_usd: 0, month_usd: 0, day: '2026-10-06', month: '2026-10'});
      if (path === '/api/health') return Response.json({build: {id: 'test'}});
      throw new Error(`unstubbed fetch: ${input.method} ${path}`);
    }));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      this.dispatchEvent(new Event('play'));
      return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete (Element.prototype as {scrollIntoView?: unknown}).scrollIntoView;
  });

  /** A row's strip as a browser keeps it, scrolled to `start`, with the offsets written to it. */
  function scrolled(row: number, start: number) {
    const strip = document.querySelector<HTMLElement>(`#row-${row} [data-lineage-strip]`)!;
    const written: number[] = [];
    let left = start;
    Object.defineProperty(strip, 'scrollLeft', {configurable: true, get: () => left, set: (offset: number) => {left = offset; written.push(offset);}});
    return {strip, written};
  }
  const door = (row: number) => within(document.querySelector<HTMLElement>(`#row-${row}`)!).getByRole('button', {name: 'Grid ›'});

  it('gives back the scroll a digit took, so Escape leaves the Filmstrip row where it was', async () => {
    render(<App/>);
    await screen.findAllByRole('button', {name: 'Grid ›'});
    const row0 = scrolled(0, 120);
    // the browser scrolls the strip to bring the face a digit plays into view
    Object.defineProperty(Element.prototype, 'scrollIntoView', {configurable: true, writable: true,
      value() {row0.strip.scrollLeft = 640;}});
    fireEvent.click(door(0));
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    fireEvent.keyDown(document.body, {key: '1', code: 'Digit1'});
    await waitFor(() => expect(screen.getByRole('button', {name: /^Act on 1A\b/})).toHaveFocus());
    expect(row0.written).toEqual([640]);
    fireEvent.click(screen.getByRole('button', {name: 'Esc · Project'}));
    await waitFor(() => expect(screen.queryByText('Grid · Turn 0')).toBeNull());
    expect(row0.written).toEqual([640, 120]);
    expect(row0.strip.scrollLeft).toBe(120);
  });

  it("gives each row its own scroll back when the record opens a room and the rooms change rows without the Filmstrip between", async () => {
    render(<App/>);
    await screen.findAllByRole('button', {name: 'Grid ›'});
    const row0 = scrolled(0, 120);
    fireEvent.click(door(0));
    expect(await screen.findByText('Grid · Turn 0')).toBeInTheDocument();
    row0.strip.scrollLeft = 500;
    // the record opens over the room, which closes: row 0 has its scroll back
    fireEvent.click(screen.getByRole('button', {name: 'Record ›'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Runs'}));
    expect(row0.written).toEqual([500, 120]);
    // row 1, drawn again behind the record, opens from it
    const row1 = scrolled(1, 40);
    const run = (await screen.findByText(/^Run 2 ·/)).closest('article')!;
    fireEvent.click(within(run).getByRole('button', {name: 'Open execution grid ›'}));
    expect(await screen.findByText('Grid · Turn 1')).toBeInTheDocument();
    row1.strip.scrollLeft = 700;
    fireEvent.click(screen.getByRole('button', {name: 'Esc · Record'}));
    await waitFor(() => expect(screen.queryByText('Grid · Turn 1')).toBeNull());
    expect(row1.written).toEqual([700, 40]);
    expect(row0.written).toEqual([500, 120]);
  });
});
