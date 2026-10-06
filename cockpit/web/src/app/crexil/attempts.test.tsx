import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { AttemptFace } from './AttemptFace';
import { attemptFace, CANCEL_TITLE, CHECK_TITLE, PAUSE_TITLE, RESUME_TITLE, sharedCauses, type AttemptContext, type AttemptFaceVM } from './attempts';
import { catalogueFrom, setCatalogue } from './catalogue';

const NOW = Date.parse('2026-10-05T12:00:30Z');

const slot = (fields: object) => ({
  id: 's1', turn_id: 't1', pos: 0, engine: 'raw-flux2', status: 'failed', attempt: 1, seed: 7, cause: null, detail: null,
  retry_of: null, kind: 'error', job_ref: null, started_at: '2026-10-05T12:00:00Z', finished_at: '2026-10-05T12:00:12Z',
  frame: null, facts: null, committed_usd: 0, charged_usd: null, created_at: '2026-10-05T12:00:00Z', identity_hidden: null,
  ...fields,
}) as never;

const local = {id: 'raw-flux2', output_payload: 'image', maximum_usd_per_attempt: 0, price_usd: null, tariff: null};
const hosted = {id: 'openai/gpt-image-2', output_payload: 'image', maximum_usd_per_attempt: .04, price_usd: .04, tariff: null};
const voice = {id: 'audio:speak:elevenlabs-tts', output_payload: 'audio', maximum_usd_per_attempt: .0032, price_usd: null,
  tariff: {unit: '1k_characters', micro_usd: 80000}};
const sfx = {id: 'audio:sfx:elevenlabs-sfx', output_payload: 'audio', maximum_usd_per_attempt: null, price_usd: .002,
  tariff: {unit: 'second', micro_usd: 2000}};

function ctx(fields: Partial<AttemptContext> = {}, slots: object[] = []): AttemptContext {
  return {turn: {id: 't1', idx: 0, prompt: 'A brass lamp', slots: slots as never} as never,
    operations: [local, hosted, voice, sfx] as never, controls: [], nowMs: NOW, ...fields};
}
/** Each remedy as [id, label, primary, price]. */
const moves = (face: AttemptFaceVM) => face.remedies.map(r => [r.id, r.label, r.primary, r.price]);

afterEach(() => {cleanup(); setCatalogue(new Map());});

describe('attemptFace: what happened · what it cost · what to do next (laws 3, 4, 5)', () => {
  it('a local failure states GPU time, the takes still queued, and offers its free next move first', () => {
    const queued = slot({id: 's2', pos: 1, status: 'queued'});
    const face = attemptFace(slot({cause: 'raw-flux2 failed on this take.', detail: 'Traceback…'}), ctx({}, [slot({}), queued]));
    expect(face).toMatchObject({label: '0A', title: 'FAILED', tone: 'dead', engine: 'raw-flux2', cause: 'raw-flux2 failed on this take.',
      detail: 'Traceback…', cost: '12s of GPU time spent · 1 take still queued'});
    expect(moves(face)).toEqual([['reuse', 'REUSE SETUP', true, null]]);
  });
  it('names an attempt past Z by the same letters as its takes', () => {
    expect(attemptFace(slot({pos: 25}), ctx()).label).toBe('0Z');
    expect(attemptFace(slot({pos: 26}), ctx()).label).toBe('0AA');
    expect(attemptFace(slot({pos: 27, status: 'queued'}), ctx()).label).toBe('0AB');
  });
  it('a paid failure says nothing was charged, or what was', () => {
    expect(attemptFace(slot({engine: 'openai/gpt-image-2', committed_usd: .04}), ctx()).cost).toBe('nothing was charged');
    expect(attemptFace(slot({engine: 'openai/gpt-image-2', charged_usd: .04}), ctx()).cost).toBe('$0.04 was charged');
  });
  it('spells a sub-cent audio charge exactly, never $0.00 (law 5)', () => {
    expect(attemptFace(slot({engine: 'elevenlabs-tts', charged_usd: .0032}), ctx()).cost).toBe('$0.0032 was charged');
  });
  it('knows money is in play from the operation price before any charge is recorded', () => {
    expect(attemptFace(slot({engine: 'elevenlabs-tts', committed_usd: null}), ctx()).cost).toBe('nothing was charged');
  });
  it("gives a hosted engine a money line by its lane, even at no price (law 5); a local engine's is GPU time", () => {
    setCatalogue(catalogueFrom([], [{id: 'free-hosted', name: 'Free Hosted', lane: 'external'}, {id: 'raw-flux2', name: 'FLUX.2', lane: 'local'}]));
    expect(attemptFace(slot({engine: 'free-hosted', committed_usd: 0}), ctx()).cost).toBe('nothing was charged');
    expect(attemptFace(slot({status: 'skipped', engine: 'free-hosted'}), ctx()).cost).toBe('nothing was charged');
    expect(attemptFace(slot({}), ctx()).cost).toBe('12s of GPU time spent');
    expect(attemptFace(slot({status: 'skipped'}), ctx()).cost).toBe('no GPU time spent; its turn was given back');
  });
  it('names its engine by the catalogue', () => {
    setCatalogue(catalogueFrom([], [{id: 'elevenlabs-sfx', name: 'ElevenLabs Sound Effects', lane: 'external'}]));
    expect(attemptFace(slot({status: 'running', engine: 'elevenlabs-sfx'}), ctx())).toMatchObject({engine: 'ElevenLabs Sound Effects',
      progress: 'ElevenLabs Sound Effects · running'});
  });
  it('offers to collect the same request, free, for outcomes it may still hold', () => {
    for (const kind of ['collection', 'timeout', 'restart']) {
      expect(attemptFace(slot({kind}), ctx()).remedies.map(r => [r.id, r.primary])).toEqual([['collect', true], ['reuse', false]]);
    }
  });
  it('names an uncertain outcome, its unconfirmed charge, and collects first', () => {
    const face = attemptFace(slot({kind: 'uncertain', engine: 'elevenlabs-tts', committed_usd: .0032}), ctx());
    expect(face).toMatchObject({title: 'OUTCOME UNCERTAIN', tone: 'dead', cost: 'charge not confirmed · $0.0032 reserved'});
    expect(face.remedies[0]).toMatchObject({id: 'collect', primary: true, label: 'COLLECT FILES'});
  });
  it('sends a dead key or an unreachable engine to Connections first', () => {
    for (const kind of ['dead_key', 'unreachable', 'model_down'])
      expect(attemptFace(slot({kind}), ctx()).remedies.map(r => r.id)).toEqual(['connections', 'reuse']);
  });
  it('B4: a charged attempt that returned nothing offers its receipt first, then collection, never a paid retry', () => {
    const face = attemptFace(slot({kind: 'charged_blank', engine: 'openai/gpt-image-2', charged_usd: .04, job_ref: 'gen-1'}), ctx());
    expect(face.cost).toBe('$0.04 was charged');
    expect(face.receipt).toContain('job id    gen-1');
    expect(face.remedies.map(r => [r.id, r.primary])).toEqual([['copy-receipt', true], ['collect', false], ['reuse', false]]);
  });
  it('a failed face spends no money and stops nothing: a paid path opens a preparation (law 6)', () => {
    const kinds = ['error', 'oom', 'blank', 'charged_blank', 'collection', 'uncertain', 'dead_key', 'rate_limit', 'moderation'];
    const remedies = kinds.flatMap(kind => attemptFace(slot({kind, engine: 'openai/gpt-image-2', charged_usd: .04}), ctx()).remedies);
    expect(new Set(remedies.map(r => r.id))).toEqual(new Set(['reuse', 'collect', 'connections', 'copy-receipt']));
    expect(remedies.every(r => r.price === null)).toBe(true);
  });
  it('a late attempt waits, already dispatched, and can be collected if it arrived', () => {
    const face = attemptFace(slot({status: 'late', engine: 'openai/gpt-image-2', committed_usd: .04}), ctx());
    expect(face).toMatchObject({title: 'LATE', tone: 'waiting', cost: 'sent · $0.04 reserved · lands here if it arrives'});
    expect(moves(face)).toEqual([['collect', 'COLLECT FILES', true, null]]);
  });
  it('a skipped attempt informs and offers its setup again', () => {
    const face = attemptFace(slot({status: 'skipped'}), ctx());
    expect(face).toMatchObject({title: 'SKIPPED', tone: 'info'});
    expect(face.remedies.map(r => r.id)).toEqual(['reuse']);
  });
});

describe('a held attempt carries its own resume, live and authored (laws 2, 4 and 23)', () => {
  const control = (scope: string, target_id: string, paused: boolean) => ({scope, target_id, paused, revision: 3}) as never;
  const repeat = (fields: object = {}) => ({bench: 'engine:elevenlabs-sfx@turn:t1', benchKind: 'repeat',
    benchCause: 'elevenlabs-sfx failed twice in this run.', ...fields});
  it('resumes its paused run, or the paused project, free first, and keeps its cancel last', () => {
    const run = attemptFace(slot({status: 'held', cause: 'This run is paused.'}), ctx({controls: [control('run', 't1', true), control('project', 'p', true)]}));
    expect(run).toMatchObject({title: 'HELD', tone: 'waiting', cost: 'waiting costs nothing', cause: null,
      progress: 'This run is paused. Its waiting attempts stay here until it resumes.'});
    expect(moves(run)).toEqual([['resume-run', 'RESUME RUN 0', true, null], ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(run.remedies[0].title).toBe(PAUSE_TITLE);
    const project = attemptFace(slot({status: 'held', engine: 'openai/gpt-image-2'}), ctx({controls: [control('project', 'p', true)]}));
    expect(project.cost).toBe('nothing charged yet');
    expect(project.remedies.map(r => r.id)).toEqual(['resume-project', 'cancel-attempt']);
  });
  it('a resume of held paid work carries what the pause holds and is never primary (law 6)', () => {
    const held = (id: string, turn_id: string, committed_usd: number) => slot({id, turn_id, status: 'held', engine: 'elevenlabs-sfx', committed_usd});
    const mine = [held('s1', 't1', .004), held('s2', 't1', .004)];
    const run = attemptFace(mine[0], ctx({controls: [control('run', 't1', true)]}, mine));
    expect(moves(run)).toEqual([['resume-run', 'RESUME RUN 0', false, 'UP TO $0.008'], ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    // a resume of the project sends every attempt the project holds, in every run
    const project = attemptFace(mine[0], ctx({controls: [control('project', 'p', true)], projectSlots: [...mine, held('s9', 't9', .0336)]}, mine));
    expect(moves(project)[0]).toEqual(['resume-project', 'RESUME PROJECT', false, 'UP TO $0.0416']);
  });
  it('A9 on local work: preparing again leads, and TRY ONCE MORE sends it as it is, free', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'engine:raw-flux2@turn:t1', benchKind: 'repeat',
      benchCause: 'raw-flux2 failed twice in this run.', reservedUsd: 0}}));
    expect(face.progress).toBe('raw-flux2 failed twice in this run.');
    expect(moves(face)).toEqual([['reuse', 'REUSE SETUP', true, null], ['resume-engine', 'TRY ONCE MORE', false, null],
      ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(face.remedies[1].title).toBe(RESUME_TITLE);
  });
  it('A9 on paid work: TRY ONCE MORE carries what the held attempts it sends reserve, never primary, free moves first (law 6)', () => {
    const face = attemptFace(slot({status: 'held', engine: 'elevenlabs-sfx', committed_usd: .01}), ctx({queue: repeat({reservedUsd: .02})}));
    expect(face.cost).toBe('nothing charged yet');
    expect(moves(face)).toEqual([['reuse', 'REUSE SETUP', true, null], ['resume-engine', 'TRY ONCE MORE', false, 'UP TO $0.02'],
      ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
  });
  it('without the queue view, a paid resume carries its own attempt’s reservation, exact below a cent (law 5)', () => {
    expect(attemptFace(slot({status: 'held', engine: 'elevenlabs-sfx', committed_usd: .01}), ctx({queue: repeat()})).remedies[1])
      .toMatchObject({label: 'TRY ONCE MORE', price: 'UP TO $0.01'});
    expect(attemptFace(slot({status: 'held', engine: 'elevenlabs-tts', committed_usd: .0032}), ctx()).remedies[1])
      .toMatchObject({label: 'RESUME ENGINE', price: 'UP TO $0.0032'});
  });
  it('C2: a card held by another program reads CHECK AGAIN, free, then its cancel', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'lane:local', benchKind: 'gpu_held', benchCause: null, reservedUsd: 0}}));
    expect(face.progress).toBe('Shared GPU held: the card is taken.');
    expect(moves(face)).toEqual([['resume-engine', 'CHECK AGAIN', true, null], ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(face.remedies[0].title).toBe(CHECK_TITLE);
  });
  it('a benched connection says why and points to Connections first, its resume live after it', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'provider:voice-runner', benchKind: 'unreachable',
      benchCause: 'The voice runner is not answering.'}}));
    expect(face.progress).toBe('The voice runner is not answering.');
    expect(moves(face)).toEqual([['connections', 'CONNECTIONS', true, null], ['resume-engine', 'RESUME ENGINE', false, null],
      ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    const paid = attemptFace(slot({status: 'held', engine: 'elevenlabs-sfx', committed_usd: .01}),
      ctx({queue: {bench: 'provider:elevenlabs', benchKind: 'dead_key', benchCause: null, reservedUsd: .03}}));
    expect(moves(paid)[1]).toEqual(['resume-engine', 'RESUME ENGINE', false, 'UP TO $0.03']);
  });
  it('names a hold in plain words when the queue view carries no cause, and never by its raw scope', () => {
    const face = attemptFace(slot({status: 'held', engine: 'elevenlabs-tts'}), ctx({queue: {bench: 'provider:elevenlabs', benchKind: 'dead_key', benchCause: null}}));
    expect(face.progress).toBe('ElevenLabs key missing or refused.');
  });
  it("a blind run's hold is one neutral face: no engine, lane, cause or price, its setup first and its resume live", () => {
    const face = attemptFace(slot({status: 'held', engine: 'hidden', identity_hidden: true, committed_usd: .01, cause: 'Held.'}),
      ctx({queue: {bench: 'provider:elevenlabs', benchKind: 'dead_key', benchCause: 'ElevenLabs refused the key.', reservedUsd: .01}}));
    expect(face).toMatchObject({title: 'HELD', engine: 'Blind', cause: null, progress: null, cost: 'waiting costs nothing'});
    expect(moves(face)).toEqual([['reuse', 'REUSE SETUP', true, null], ['resume-engine', 'RESUME ENGINE', false, null],
      ['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(face.remedies.map(r => r.title)).toContain(CANCEL_TITLE.unsent);
    expect(JSON.stringify(face)).not.toMatch(/elevenlabs|\$/i);
  });
});

describe('cancel sits last on queued, held and running faces and says what stopping means (failure case 2.7)', () => {
  const cancel = (face: AttemptFaceVM) => face.remedies.at(-1)!;
  it('a queued attempt is skipped, free', () => {
    const face = attemptFace(slot({status: 'queued'}), ctx());
    expect(moves(face)).toEqual([['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(cancel(face).title).toBe(CANCEL_TITLE.unsent);
  });
  it('a running local attempt asks its engine to stop, at no money', () => {
    const face = attemptFace(slot({status: 'running'}), ctx());
    expect(moves(face)).toEqual([['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(cancel(face).title).toBe(CANCEL_TITLE.local);
  });
  it('a running paid hosted attempt keeps its cell, and its charge stays unverified until the provider reports it', () => {
    const face = attemptFace(slot({status: 'running', engine: 'elevenlabs-sfx', committed_usd: .01}), ctx());
    expect(moves(face)).toEqual([['cancel-attempt', 'CANCEL ATTEMPT', false, null]]);
    expect(cancel(face).title).toBe(CANCEL_TITLE.sent);
  });
  it('gives up on an attempt while its retry shows (law 9)', () => {
    const waiting = attemptFace(slot({status: 'queued', engine: 'elevenlabs-tts', retry_of: 'rate_limit'}), ctx());
    expect(waiting.retry?.label).toBe('RETRYING · ATTEMPT 2');
    expect(cancel(waiting)).toMatchObject({id: 'cancel-attempt', label: 'GIVE UP ON THIS ATTEMPT', primary: false, title: CANCEL_TITLE.unsent});
    const sent = attemptFace(slot({status: 'running', engine: 'elevenlabs-tts', retry_of: 'unreachable', committed_usd: .0032}), ctx());
    expect(cancel(sent)).toMatchObject({label: 'GIVE UP ON THIS ATTEMPT', title: CANCEL_TITLE.sent});
  });
  it("a hidden running attempt's cancel names no lane", () => {
    const face = attemptFace(slot({status: 'running', engine: 'hidden', identity_hidden: true, committed_usd: .01}), ctx());
    expect(cancel(face).title).toBe(CANCEL_TITLE.hidden);
  });
  it('a settled attempt offers no cancel', () => {
    for (const status of ['failed', 'skipped', 'late'])
      expect(attemptFace(slot({status}), ctx()).remedies.map(r => r.id)).not.toContain('cancel-attempt');
  });
});

describe('a blind run never leaks its engine or its cost', () => {
  it('names the engine Blind and withholds the cost until the reveal', () => {
    const face = attemptFace(slot({engine: 'hidden', identity_hidden: true, charged_usd: null, committed_usd: null,
      cause: 'The engine failed on this take.'}), ctx());
    expect(face).toMatchObject({engine: 'Blind', cost: 'Its cost shows after the reveal', receipt: null});
    expect(JSON.stringify(face)).not.toMatch(/flux|openai|elevenlabs/i);
  });
  it('a hidden queued attempt waits its turn without naming a lane', () => {
    expect(attemptFace(slot({status: 'queued', engine: 'hidden', identity_hidden: true}), ctx()).progress).toBe('Blind · waiting its turn');
  });
});

describe('waiting and retrying attempts', () => {
  it('a queued local attempt waits for the GPU; a paid one launches', () => {
    expect(attemptFace(slot({status: 'queued'}), ctx()).progress).toBe('raw-flux2 · waiting for the GPU');
    expect(attemptFace(slot({status: 'queued', engine: 'openai/gpt-image-2'}), ctx()).progress).toBe('openai/gpt-image-2 · launching');
  });
  it('a retry the scheduler is making says so while it happens (law 9), in the medium’s words', () => {
    const face = attemptFace(slot({status: 'queued', engine: 'elevenlabs-tts', retry_of: 'rate_limit'}), ctx());
    expect(face.retry).toEqual({label: 'RETRYING · ATTEMPT 2',
      why: 'The engine asked to wait. It is tried again on its own; nothing is charged until a take lands.'});
  });
  it('says how long a retry waits and which attempt of how many it is, from the queue view (law 9)', () => {
    const wait = {attempt: 2, of: 5, next_try_at: '2026-10-05T12:00:48Z', window: 'provider' as const};
    const face = attemptFace(slot({status: 'queued', engine: 'elevenlabs-tts', retry_of: 'rate_limit'}),
      ctx({queue: {bench: null, benchKind: null, benchCause: null, retry: wait}}));
    expect(face.retry).toEqual({label: 'RETRYING · ATTEMPT 2 OF 5',
      why: 'The engine asked to wait. It is tried again on its own; nothing is charged until a take lands. Next try in 18 s.'});
    expect(face.progress).toBe('Next try in 18 s, as the engine asked.');
    // a blind run's retry never reads the queue view, which would name its lane
    const hidden = attemptFace(slot({status: 'queued', engine: 'hidden', identity_hidden: true, retry_of: 'rate_limit'}),
      ctx({queue: {bench: null, benchKind: null, benchCause: null, retry: wait}}));
    expect(hidden.retry?.label).toBe('RETRYING · ATTEMPT 2');
    expect(hidden.progress).toBe('Blind · waiting its turn');
  });
});

describe('sharedCauses (law 1: one cause, one message)', () => {
  it('names a cause shared by two or more failed attempts once, with their labels', () => {
    const cause = 'ComfyUI is not answering.';
    const faces = [slot({id: 'a', pos: 0, cause}), slot({id: 'b', pos: 1, cause}), slot({id: 'c', pos: 2, cause: 'Other.'}),
      slot({id: 'd', pos: 3, status: 'held', cause})].map(s => attemptFace(s, ctx()));
    expect(sharedCauses(faces)).toEqual([{cause, labels: ['0A', '0B']}]);
  });
});

describe('AttemptFace', () => {
  it('draws one group per attempt with its words, and every move live, the resume included', () => {
    const onRemedy = vi.fn();
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'engine:x', benchKind: 'unreachable', benchCause: 'Not answering.'}}));
    render(<AttemptFace face={face} onRemedy={onRemedy}/>);
    const group = screen.getByRole('group', {name: 'Attempt 0A · held'});
    expect(group).toHaveTextContent('HELD');
    expect(group).toHaveTextContent('waiting costs nothing');
    const resume = screen.getByRole('button', {name: 'RESUME ENGINE'});
    expect(resume).not.toHaveAttribute('aria-disabled');
    expect(resume).toHaveAccessibleDescription(RESUME_TITLE);
    for (const name of ['CONNECTIONS', 'RESUME ENGINE', 'CANCEL ATTEMPT']) fireEvent.click(screen.getByRole('button', {name}));
    expect(onRemedy.mock.calls).toEqual([['connections'], ['resume-engine'], ['cancel-attempt']]);
  });
  it('puts the price on the paid resume, after the free moves, and the cancel last (law 6)', () => {
    const face = attemptFace(slot({status: 'held', engine: 'elevenlabs-sfx', committed_usd: .01}), ctx({queue: {bench: 'engine:elevenlabs-sfx@turn:t1',
      benchKind: 'repeat', benchCause: 'Failed twice in this run.', reservedUsd: .01}}));
    render(<AttemptFace face={face} onRemedy={() => {}}/>);
    const buttons = within(screen.getByRole('group', {name: 'Attempt 0A · held'})).getAllByRole('button');
    expect(buttons.map(button => button.textContent)).toEqual(['REUSE SETUP', 'TRY ONCE MORE · UP TO $0.01', 'CANCEL ATTEMPT']);
    expect(buttons[2]).toHaveAccessibleDescription(CANCEL_TITLE.unsent);
  });
  it('keeps every move inert while one is in flight', () => {
    const onRemedy = vi.fn();
    render(<AttemptFace face={attemptFace(slot({status: 'queued'}), ctx())} busy onRemedy={onRemedy}/>);
    const cancel = screen.getByRole('button', {name: 'CANCEL ATTEMPT'});
    expect(cancel).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(cancel);
    expect(onRemedy).not.toHaveBeenCalled();
  });
  it('keeps the raw engine text behind DETAILS (law 3)', () => {
    render(<AttemptFace face={attemptFace(slot({cause: 'It failed.', detail: 'HTTP 500: boom'}), ctx())} onRemedy={() => {}}/>);
    expect(screen.getByText('It failed.')).toBeVisible();
    expect(screen.getByText('DETAILS')).toBeInTheDocument();
    expect(screen.getByText('HTTP 500: boom').closest('details')).not.toHaveAttribute('open');
  });
});
