// One attempt's move against a stubbed server: it names the attempt its face shows and its
// participant; an unconfirmed delivery is sent again as the same request, so a retried click
// replays and never repeats; a definite refusal drops it and keeps the server's plain words.
// Each test moves its own slot, since pending deliveries outlive a component by design.
// Fetch is stubbed; the client resolves globalThis.fetch lazily (api/client.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { message } from './api';
import { moveAttempt } from './ExecutionControls';

type Body = {request_id: string; actor: unknown; attempt: number; reason: string};
let sent: {path: string; body: Body}[] = [];
let answers: (() => Response)[] = [];

beforeEach(() => {
  sent = [];
  answers = [];
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    sent.push({path: new URL(input.url).pathname, body: await input.json()});
    return answers.shift()!();
  }));
});
afterEach(() => vi.unstubAllGlobals());

const moved = (action: 'resume' | 'cancel', attempt: number) => () => Response.json({slot_id: 's', attempt, action,
  words: 'Done.', released: 0, actor: {kind: 'human', name: 'Artist'}, reason: ''});
const ids = () => sent.map(item => item.body.request_id);

describe('moveAttempt', () => {
  it("sends the attempt its face shows, with its participant, to the move's own route (laws 4 and 23)", async () => {
    answers = [moved('resume', 2), moved('cancel', 2)];
    await moveAttempt({id: 's-routes', attempt: 2}, 'resume');
    await moveAttempt({id: 's-routes', attempt: 2}, 'cancel');
    expect(sent).toEqual([
      {path: '/api/crexil/attempts/s-routes/resume', body: {request_id: expect.any(String), actor: {kind: 'human', name: 'Artist'}, attempt: 2, reason: ''}},
      {path: '/api/crexil/attempts/s-routes/cancel', body: {request_id: expect.any(String), actor: {kind: 'human', name: 'Artist'}, attempt: 2, reason: ''}},
    ]);
    expect(ids()[0]).not.toBe(ids()[1]);
  });
  it('sends an unconfirmed move again as the same request, so a retried click replays and never repeats', async () => {
    answers = [() => Response.json({detail: 'Bad gateway'}, {status: 502}), () => { throw new TypeError('Failed to fetch'); },
      moved('resume', 1), moved('resume', 1)];
    const slot = {id: 's-replay', attempt: 1};
    await expect(moveAttempt(slot, 'resume')).rejects.toThrow('Bad gateway');
    await expect(moveAttempt(slot, 'resume')).rejects.toThrow('Failed to fetch');
    await moveAttempt(slot, 'resume');
    expect(new Set(ids()).size).toBe(1);
    await moveAttempt(slot, 'resume');                      // confirmed: a new press is a new request
    expect(ids()[3]).not.toBe(ids()[0]);
  });
  it('keeps one pending request per move, slot and attempt', async () => {
    answers = Array.from({length: 4}, () => () => Response.json({detail: 'Unavailable'}, {status: 503}));
    await Promise.allSettled([moveAttempt({id: 's-keys', attempt: 1}, 'resume'), moveAttempt({id: 's-keys', attempt: 2}, 'resume'),
      moveAttempt({id: 's-keys', attempt: 1}, 'cancel'), moveAttempt({id: 's-keys-other', attempt: 1}, 'resume')]);
    expect(new Set(ids()).size).toBe(4);
    const first = sent.find(item => item.path.endsWith('/s-keys/resume') && item.body.attempt === 1)!.body.request_id;
    answers = [moved('resume', 1)];
    await moveAttempt({id: 's-keys', attempt: 1}, 'resume');
    expect(sent.at(-1)!.body.request_id).toBe(first);
  });
  it.each([
    [403, "Resuming paid work is the artist's act: this would send held attempts that reserve money."],
    [409, 'This attempt is not held, so there is nothing to resume. Refresh it.'],
    [422, "Project spending limit is $1; charged and reserved spending totals $1.004. Raise the project's limit in its settings to send it, or cancel it."],
  ])('drops a %i refusal, keeping its plain words for the error line (law 3)', async (status, detail) => {
    answers = [() => Response.json({detail}, {status}), moved('resume', 1)];
    const slot = {id: `s-refused-${status}`, attempt: 1};
    const refused = await moveAttempt(slot, 'resume').catch((error: unknown) => error);
    expect(message(refused)).toBe(detail);
    await moveAttempt(slot, 'resume');
    expect(ids()[1]).not.toBe(ids()[0]);
  });
});
