// The drawer against a stubbed server: an audio run's words, its blind choice, the offer to
// budget a tariff-priced operation, what each operation sends where, whether the reviewed
// run retries on its own, a submit refused because spending moved since review, which
// reviews once more before asking again, and empty words started from the words of a
// section in the preparation's family.
// Fetch is stubbed; the client resolves globalThis.fetch lazily (api/client.ts).
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Direction, Operation } from './api';
import { DraftDrawer } from './DraftDrawer';

const design = {id: 'audio:voice-design:elevenlabs-voice-design', label: 'Design voice · ElevenLabs Voice Design', revision: 'r1',
  actions: ['voice-design'], inputs: [], reasons: [], source_limit: 0, availability: 'unverified', evidence: 'declared',
  execution_location: 'Hosted provider', data_destination: 'https://api.elevenlabs.io', provider: 'elevenlabs',
  maximum_usd_per_attempt: null, price_usd: 0.08, price_unit: '1k_characters', tariff: {unit: '1k_characters', micro_usd: 80000},
  cost_basis: 'Published tariff, 0.08 US dollars per 1,000 characters', limitations: [], takes_per_attempt: 3} as unknown as Operation;
const local = {...design, id: 'audio:voice-design:qwen3-tts', label: 'Design voice · Qwen3-TTS 1.7B', revision: 'r2', provider: 'voice-runner',
  execution_location: 'Local voice runner', data_destination: 'http://127.0.0.1:8190', maximum_usd_per_attempt: 0, price_usd: 0,
  price_unit: 'attempt', tariff: {unit: 'attempt', micro_usd: 0}, cost_basis: 'Unmetered local operation'} as unknown as Operation;
const direction = (policy: 'hard_cap' | 'published_tariff') => ({id: 'd1', workspace_id: 'p1', title: 'Blacksmith voice', state: 'draft',
  revision: 3, row_idx: 0, selection: [], node_ids: [], turn_ids: [], set_aside: false, sequence_template: false,
  setup: {action: 'voice-design', prompt: 'A gruff dwarf blacksmith.', references: [], controls: {}, instructions: '',
    attempt_limit: 4, spending_limit_usd: 1, spending_policy: policy, purpose: 'creative_request', blind: true,
    operations: [{operation_id: design.id, revision: 'r1', count: 1, controls: {}}]}}) as unknown as Direction;
const review = (reserved: number, token: string, automatic_retry?: Record<string, boolean>) => ({direction_id: 'd1', ready: true,
  ready_attempts: 1, blocked_attempts: 0, attempts: 1, errors: [], maximum_usd: null, reserved_usd: reserved,
  reserved_micro_usd: Math.round(reserved * 1e6), revision: 3, token, snapshot: {}, ...(automatic_retry ? {automatic_retry} : {})});

type Sent = {method: string; path: string; body: unknown};
let sent: Sent[] = [];
let reviews: ReturnType<typeof review>[] = [];

beforeEach(() => {
  sent = [];
  sessionStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    sent.push({method: input.method, path, body: input.method === 'GET' ? null : await input.json()});
    if (path === '/api/crexil/directions/d1/review') return Response.json(reviews.shift());
    if (path === '/api/crexil/directions/d1/submit') return Response.json(
      {detail: "Other work changed this project's spending since review, and this run no longer fits its limits. Review again to see the current totals."},
      {status: 409, headers: {'X-Crexil-Conflict': 'spending'}});
    return Response.json({});
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const open = (policy: 'hard_cap' | 'published_tariff' = 'published_tariff', directions: Direction[] = []) => render(<DraftDrawer
  direction={direction(policy)} directions={directions} nodes={[]} operations={[design, local]} onSaved={() => {}} onClose={() => {}} onCopied={() => {}}/>);

describe('preparing an audio run', () => {
  it("names the action's words, offers a blind run and says it is on", () => {
    open();
    expect(screen.getByRole('textbox', {name: 'Voice description'})).toHaveValue('A gruff dwarf blacksmith.');
    expect(screen.getByRole('checkbox', {name: 'Blind run'})).toBeChecked();
    expect(screen.getByText(/Engines, prices and level figures stay hidden until you reveal the run/)).toBeInTheDocument();
  });
  it('says, for every operation, what leaves this machine and where', () => {
    open();
    expect(screen.getByText('Sends off this machine to api.elevenlabs.io: the voice description and its preview line.')).toBeInTheDocument();
    expect(screen.getByText('Sends to 127.0.0.1:8190 on this machine: the voice description and its preview line.')).toBeInTheDocument();
  });
  it('offers published-tariff budgeting when a tariff-priced operation is chosen under a hard cap', async () => {
    open('hard_cap');
    const offer = screen.getByText(/is priced by its published tariff, with no hard ceiling/).closest('div')!;
    fireEvent.click(within(offer).getByRole('button', {name: 'Budget from published tariffs'}));
    expect(screen.getByRole('combobox', {name: 'Spending policy'})).toHaveValue('published_tariff');
    expect(screen.queryByText(/is priced by its published tariff/)).toBeNull();
  });
});

describe("the review says whether the run retries on its own (law 9)", () => {
  const reviewed = async (automatic_retry?: Record<string, boolean>) => {
    reviews = [review(0.0084, 'a'.repeat(64), automatic_retry)];
    open();
    fireEvent.click(screen.getByRole('button', {name: 'Review run'}));
    return screen.findByRole('region', {name: 'Run review'});
  };
  it('makes no automatic retries when none of its operations retries, or the review names none', async () => {
    expect(await reviewed({[design.id]: false})).toHaveTextContent('This run makes no automatic retries.');
    cleanup();
    expect(await reviewed()).toHaveTextContent('This run makes no automatic retries.');
  });
  it('tries an attempt again on its own, visibly, when every operation retries', async () => {
    const line = await reviewed({[design.id]: true, [local.id]: true});
    expect(line).toHaveTextContent('An attempt whose engine asks to wait or cannot be reached is tried again on its own, '
      + 'and the retry shows on that attempt.');
    expect(line).not.toHaveTextContent('no automatic retries');
  });
  it('names the operations that retry by their catalogue labels when only some do', async () => {
    expect(await reviewed({[design.id]: true, [local.id]: false})).toHaveTextContent('Design voice · ElevenLabs Voice Design tries an '
      + 'attempt again on its own when the engine asks to wait or cannot be reached, and the retry shows on that attempt. '
      + 'The other operations make no automatic retries.');
  });
});

describe('a submit refused because spending moved', () => {
  it('reviews once more, shows the new totals and asks for Run again, without sending it', async () => {
    reviews = [review(0.0084, 'a'.repeat(64)), review(0.0072, 'b'.repeat(64))];
    open();
    fireEvent.click(screen.getByRole('button', {name: 'Review run'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Run 1 attempt · reserves $0.0084'}));
    expect(await screen.findByRole('button', {name: 'Run 1 attempt · reserves $0.0072'})).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('It was reviewed again: the totals below are current.');
    expect(sent.filter(item => item.path.endsWith('/review'))).toHaveLength(2);
    expect(sent.filter(item => item.path.endsWith('/submit'))).toHaveLength(1);
    expect(screen.queryByRole('region', {name: 'Preparation conflict'})).toBeNull();      // the request itself did not change
  });
  it('treats any other conflict as a changed request, to compare before anything else', async () => {
    reviews = [review(0.0084, 'a'.repeat(64))];
    vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname;
      sent.push({method: input.method, path, body: null});
      if (path.endsWith('/review')) return Response.json(reviews.shift());
      if (path.endsWith('/submit')) return Response.json({detail: 'Preparation, sources or operations changed.'}, {status: 409});
      return Response.json({});
    }));
    open();
    fireEvent.click(screen.getByRole('button', {name: 'Review run'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Run 1 attempt · reserves $0.0084'}));
    await waitFor(() => expect(screen.getByRole('region', {name: 'Preparation conflict'})).toBeInTheDocument());
    expect(sent.filter(item => item.path.endsWith('/review'))).toHaveLength(1);
  });
});

const BRIEF = 'A stout dwarf blacksmith, braided copper beard, soot-dark leather apron.';
const section = (id: string, row: number, action: string, prompt: string, title: string, node_ids: string[] = []) => ({id, workspace_id: 'p1',
  title, state: 'submitted', revision: 1, row_idx: row, selection: [], node_ids, turn_ids: [], set_aside: false, sequence_template: false,
  setup: {action, prompt}}) as unknown as Direction;
const family = [section('image', 0, 'generate', BRIEF, 'Dwarf blacksmith'), section('voice', 0, 'voice-design', 'Gruff, warm and low.', 'Blacksmith voice', ['preview']),
  section('line-a', 1, 'speak', 'Bring me the ore.', 'Spoken line')];
const lineRead = (setup: Record<string, unknown> = {}) => {
  const base = direction('hard_cap');
  return {...base, id: 'line-b', title: 'Spoken line', row_idx: 1, setup: {...base.setup, action: 'speak', prompt: '', blind: false, operations: [],
    references: [{node_id: 'preview', contribution: ''}], ...setup}} as unknown as Direction;
};
const openLine = (draft: Direction) => render(<DraftDrawer direction={draft} directions={[...family, draft]} nodes={[]} operations={[design, local]}
  onSaved={() => {}} onClose={() => {}} onCopied={() => {}}/>);

describe('starting from words already in the family', () => {
  it("offers a line read its row's lines, then the words of its voice's row, and copies the one chosen to edit", async () => {
    openLine(lineRead());
    const offer = screen.getByRole('group', {name: 'Start from the words of'});
    expect(within(offer).getAllByRole('button').map(button => button.getAttribute('aria-label'))).toEqual([
      'Start from the words of Spoken line', 'Start from the words of Dwarf blacksmith', 'Start from the words of Blacksmith voice']);
    expect(within(offer).getByRole('button', {name: 'Start from the words of Dwarf blacksmith'})).toHaveAccessibleDescription(BRIEF);
    fireEvent.click(within(offer).getByRole('button', {name: 'Start from the words of Spoken line'}));
    expect(screen.getByRole('textbox', {name: 'Line to speak'})).toHaveValue('Bring me the ore.');
    expect(screen.queryByRole('group', {name: 'Start from the words of'})).toBeNull();
    await waitFor(() => expect(sent.find(item => item.method === 'PATCH')?.body).toMatchObject({setup: {prompt: 'Bring me the ore.'}}));
  });
  it('offers nothing over words already written, or for a description kept only for the record', () => {
    openLine(lineRead({prompt: 'Mind the anvil.'}));
    expect(screen.queryByRole('group', {name: 'Start from the words of'})).toBeNull();
    cleanup();
    openLine(lineRead({action: 'mesh', references: []}));
    expect(screen.queryByRole('group', {name: 'Start from the words of'})).toBeNull();
  });
  it("never offers the preparation's own saved words once they are cleared", () => {
    open('published_tariff', [direction('published_tariff'), section('image', 0, 'generate', BRIEF, 'Dwarf blacksmith')]);
    expect(screen.queryByRole('group', {name: 'Start from the words of'})).toBeNull();
    fireEvent.change(screen.getByRole('textbox', {name: 'Voice description'}), {target: {value: ''}});
    const offer = screen.getByRole('group', {name: 'Start from the words of'});
    expect(within(offer).getAllByRole('button').map(button => button.getAttribute('aria-label'))).toEqual(['Start from the words of Dwarf blacksmith']);
  });
});
