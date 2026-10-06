import { afterEach, describe, expect, it } from 'vitest';
import { ActionError, HUMAN, artist, command, engineIdentity, engineLabel, isDefiniteRefusal, isSpendingConflict, money, result,
  setArtistName } from './api';

describe('money', () => {
  it('keeps two decimals from a cent up, and for exactly zero', () => {
    expect(money(0)).toBe('$0.00');
    expect(money(0.01)).toBe('$0.01');
    expect(money(0.04)).toBe('$0.04');
    expect(money(12.5)).toBe('$12.50');
  });
  it('never shows money that moved as $0.00', () => {
    expect(money(0.004)).toBe('$0.004');
    expect(money(0.0032)).toBe('$0.0032');
    expect(money(0.00012)).toBe('$0.00012');
    expect(money(0.000000001)).toBe('$0.000000001');
  });
  it('keeps the sign of a negative amount', () => {
    expect(money(-0.5)).toBe('-$0.50');
    expect(money(-0.004)).toBe('-$0.004');
  });
});

describe('isDefiniteRefusal', () => {
  const refusal = (status?: number) => isDefiniteRefusal(new ActionError({detail: 'no'}, status));
  it('drops a command the server refused with a client error', () => {
    for (const status of [400, 401, 403, 404, 409, 413, 422]) expect(refusal(status)).toBe(true);
  });
  it('keeps the command for timeouts, rate limits, server errors and lost responses', () => {
    for (const status of [408, 429, 500, 502, 503]) expect(refusal(status)).toBe(false);
    expect(refusal(undefined)).toBe(false);
    expect(isDefiniteRefusal(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('command', () => {
  afterEach(() => localStorage.clear());
  it('names the artist by default and an agent when one acts', () => {
    expect(command().actor).toEqual(HUMAN);
    const agent = command({kind: 'agent', name: 'voice-director'});
    expect(agent.actor).toEqual({kind: 'agent', name: 'voice-director'});
    expect(agent.request_id).not.toBe(command().request_id);
  });
  it("names the artist as this browser's setting names them, and Artist when it names no one", () => {
    setArtistName('  Kay  ');
    expect(artist()).toEqual({kind: 'human', name: 'Kay'});
    expect(command().actor).toEqual({kind: 'human', name: 'Kay'});
    setArtistName('x'.repeat(200));
    expect(artist().name).toHaveLength(120);
    setArtistName('   ');
    expect(command().actor).toEqual(HUMAN);
  });
});

describe('conflicts', () => {
  const answer = (status: number, conflict?: string) => Promise.resolve({error: {detail: 'Spending moved.'},
    response: new Response(null, {status, headers: conflict ? {'X-Crexil-Conflict': conflict} : {}})});
  it("reads the kind a 409 names, so spending that moved is told from a request that changed", async () => {
    const spending = await result(answer(409, 'spending')).catch(error => error);
    expect(spending).toBeInstanceOf(ActionError);
    expect(spending).toMatchObject({status: 409, conflict: 'spending', message: 'Spending moved.'});
    expect(isSpendingConflict(spending)).toBe(true);
    expect(isSpendingConflict(await result(answer(409)).catch(error => error))).toBe(false);
    expect(isSpendingConflict(await result(answer(422, 'spending')).catch(error => error))).toBe(false);
    expect(isSpendingConflict(new Error('offline'))).toBe(false);
  });
});

describe('engine identity', () => {
  const generated = {engine: 'Flux 2', material: {origin: {kind: 'execution'}}};
  const imported = {engine: null, material: {origin: {kind: 'import'}}};
  const prepared = {engine: null, material: {origin: {kind: 'prepared_image'}}};
  it('tells a named, hidden, prepared and imported maker apart', () => {
    expect(engineIdentity(generated)).toEqual({kind: 'named', name: 'Flux 2'});
    expect(engineIdentity(generated, true)).toEqual({kind: 'hidden'});
    expect(engineIdentity({engine: null, material: null}, true)).toEqual({kind: 'hidden'});
    expect(engineIdentity(prepared)).toEqual({kind: 'prepared'});
    expect(engineIdentity(imported)).toEqual({kind: 'imported'});
  });
  it('words each identity for a face and for a source heading', () => {
    expect(engineLabel(generated, 'face')).toBe('Flux 2');
    expect(engineLabel(imported, 'face')).toBe('Imported');
    expect(engineLabel(prepared, 'face')).toBe('Prepared');
    expect(engineLabel(prepared, 'source')).toBe('Imported material');
    expect(engineLabel(imported, 'source')).toBe('Imported material');
    expect(engineLabel(imported, 'source', true)).toBe('Engine hidden');
    expect(engineLabel(generated, 'face', true)).toBe('Blind');
  });
  it("honors the server's identity_hidden flag without being told: a masked engine is never a name", () => {
    const masked = {engine: 'hidden', identity_hidden: true, material: null};
    expect(engineIdentity(masked)).toEqual({kind: 'hidden'});
    expect(engineLabel(masked, 'face')).toBe('Blind');
    expect(engineLabel(masked, 'source')).toBe('Engine hidden');
    expect(engineLabel({engine: 'hidden', identity_hidden: null, material: null}, 'face')).toBe('hidden');
  });
});
