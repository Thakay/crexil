import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AttemptFace } from './AttemptFace';
import { attemptFace, RESUME_ENGINE_GATED, sharedCauses, type AttemptContext } from './attempts';

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

function ctx(fields: Partial<AttemptContext> = {}, slots: object[] = []): AttemptContext {
  return {turn: {id: 't1', idx: 0, prompt: 'A brass lamp', slots: slots as never} as never,
    operations: [local, hosted, voice] as never, controls: [], nowMs: NOW, ...fields};
}

describe('attemptFace: what happened · what it cost · what to do next (laws 3, 4, 5)', () => {
  it('a local failure states GPU time, the takes still queued, and offers its free next move first', () => {
    const queued = slot({id: 's2', pos: 1, status: 'queued'});
    const face = attemptFace(slot({cause: 'raw-flux2 failed on this take.', detail: 'Traceback…'}), ctx({}, [slot({}), queued]));
    expect(face).toMatchObject({label: '0A', title: 'FAILED', tone: 'dead', engine: 'raw-flux2', cause: 'raw-flux2 failed on this take.',
      detail: 'Traceback…', cost: '12s of GPU time spent · 1 take still queued'});
    expect(face.remedies).toEqual([{id: 'reuse', label: 'REUSE SETUP', primary: true, gated: null}]);
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
  it('no remedy on a face spends money (law 6)', () => {
    const kinds = ['error', 'oom', 'blank', 'charged_blank', 'collection', 'uncertain', 'dead_key', 'rate_limit', 'moderation'];
    const ids = kinds.flatMap(kind => attemptFace(slot({kind, engine: 'openai/gpt-image-2', charged_usd: .04}), ctx()).remedies.map(r => r.id));
    expect(new Set(ids)).toEqual(new Set(['reuse', 'collect', 'connections', 'copy-receipt']));
  });
  it('a late attempt waits, already dispatched, and can be collected if it arrived', () => {
    const face = attemptFace(slot({status: 'late', engine: 'openai/gpt-image-2', committed_usd: .04}), ctx());
    expect(face).toMatchObject({title: 'LATE', tone: 'waiting', cost: 'already paid, ~$0.04 — lands here if it still arrives'});
    expect(face.remedies).toEqual([{id: 'collect', label: 'COLLECT FILES', primary: true, gated: null}]);
  });
  it('a skipped attempt informs and offers its setup again', () => {
    const face = attemptFace(slot({status: 'skipped'}), ctx());
    expect(face).toMatchObject({title: 'SKIPPED', tone: 'info'});
    expect(face.remedies.map(r => r.id)).toEqual(['reuse']);
  });
});

describe('a held attempt carries its own resume (laws 2 and 4)', () => {
  const control = (scope: string, target_id: string, paused: boolean) => ({scope, target_id, paused, revision: 3}) as never;
  it('resumes its paused run, or the paused project', () => {
    const run = attemptFace(slot({status: 'held'}), ctx({controls: [control('run', 't1', true), control('project', 'p', true)]}));
    expect(run).toMatchObject({title: 'HELD', tone: 'waiting', cost: 'waiting costs nothing'});
    expect(run.remedies).toEqual([{id: 'resume-run', label: 'RESUME RUN 0', primary: true, gated: null}]);
    const project = attemptFace(slot({status: 'held', engine: 'openai/gpt-image-2'}), ctx({controls: [control('project', 'p', true)]}));
    expect(project.cost).toBe('nothing charged while it waits');
    expect(project.remedies.map(r => r.id)).toEqual(['resume-project']);
  });
  it('an engine benched for this run after repeated failures (A9) offers to prepare again, and keeps its retry inert', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'engine:raw-flux2@turn:t1', benchKind: 'repeat',
      benchCause: 'raw-flux2 failed twice in this run.'}}));
    expect(face.progress).toBe('raw-flux2 failed twice in this run.');
    expect(face.remedies).toEqual([{id: 'reuse', label: 'REUSE SETUP', primary: true, gated: null},
      {id: 'resume-engine', label: 'TRY ONCE MORE', primary: false, gated: RESUME_ENGINE_GATED}]);
  });
  it('a card held by another program waits, with its resume inert', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'lane:local', benchKind: 'gpu_held', benchCause: null}}));
    expect(face.progress).toBe('Shared GPU held: the card is taken.');
    expect(face.remedies.map(r => [r.id, r.gated !== null])).toEqual([['resume-engine', true]]);
  });
  it('names a hold in plain words when the queue view carries no cause, and never by its raw scope', () => {
    const face = attemptFace(slot({status: 'held', engine: 'elevenlabs-tts'}), ctx({queue: {bench: 'provider:elevenlabs', benchKind: 'dead_key', benchCause: null}}));
    expect(face.progress).toBe('ElevenLabs key missing or refused.');
  });
  it("a blind run's hold never names its engine or provider", () => {
    const face = attemptFace(slot({status: 'held', engine: 'hidden', identity_hidden: true}), ctx({queue: {bench: 'provider:elevenlabs',
      benchKind: 'dead_key', benchCause: 'ElevenLabs refused the key.'}}));
    expect(face.progress).toBe('Its engine is held.');
    expect(JSON.stringify(face)).not.toMatch(/elevenlabs/i);
  });
  it('a benched engine says why, points to Connections, and keeps the resume visible but inert with its reason (law 12)', () => {
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'provider:voice-runner', benchKind: 'unreachable',
      benchCause: 'The voice runner is not answering.'}}));
    expect(face.progress).toBe('The voice runner is not answering.');
    expect(face.remedies).toEqual([{id: 'connections', label: 'CONNECTIONS', primary: true, gated: null},
      {id: 'resume-engine', label: 'RESUME ENGINE', primary: false, gated: RESUME_ENGINE_GATED}]);
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
      why: "rate limited — trying once more on the provider's clock; nothing is charged until a take lands"});
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
  it('draws one group per attempt with its words, fires live remedies and keeps gated ones inert', () => {
    const onRemedy = vi.fn();
    const face = attemptFace(slot({status: 'held'}), ctx({queue: {bench: 'engine:x', benchKind: 'unreachable', benchCause: 'Not answering.'}}));
    render(<AttemptFace face={face} onRemedy={onRemedy}/>);
    const group = screen.getByRole('group', {name: 'Attempt 0A · held'});
    expect(group).toHaveTextContent('HELD');
    expect(group).toHaveTextContent('waiting costs nothing');
    fireEvent.click(screen.getByRole('button', {name: 'CONNECTIONS'}));
    const gated = screen.getByRole('button', {name: 'RESUME ENGINE'});
    expect(gated).toHaveAttribute('aria-disabled', 'true');
    expect(gated).toHaveAttribute('title', RESUME_ENGINE_GATED);
    fireEvent.click(gated);
    expect(onRemedy).toHaveBeenCalledTimes(1);
    expect(onRemedy).toHaveBeenCalledWith('connections');
  });
  it('keeps the raw engine text behind DETAILS (law 3)', () => {
    render(<AttemptFace face={attemptFace(slot({cause: 'It failed.', detail: 'HTTP 500: boom'}), ctx())} onRemedy={() => {}}/>);
    expect(screen.getByText('It failed.')).toBeVisible();
    expect(screen.getByText('DETAILS')).toBeInTheDocument();
    expect(screen.getByText('HTTP 500: boom').closest('details')).not.toHaveAttribute('open');
  });
});
