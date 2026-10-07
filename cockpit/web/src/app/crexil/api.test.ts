import { afterEach, describe, expect, it } from 'vitest';
import { ActionError, HUMAN, NO_ANSWER, artist, command, engineIdentity, engineLabel, isDefiniteRefusal, isSpendingConflict, label, message,
  money, result, setArtistName, takeLabel, type Material } from './api';

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
  it('shows a reservation of a cent or more exactly, never rounded down (law 6)', () => {
    expect(money(0.012)).toBe('$0.012');
    expect(money(0.0336)).toBe('$0.0336');
  });
});

describe('message', () => {
  it("never shows the browser's raw words for a request the server never answered", () => {
    for (const words of ['Failed to fetch', 'NetworkError when attempting to fetch resource.', 'Load failed'])
      expect(message(new TypeError(words))).toBe(NO_ANSWER);
  });
  it("says the server's own sentence, and any other failure in its own words", () => {
    expect(message({detail: 'This run is over its spending limit.'})).toBe('This run is over its spending limit.');
    expect(message(new ActionError({detail: 'Refused.'}, 409))).toBe('Refused.');
    expect(message(new TypeError('x is not a function'))).toBe('x is not a function');
  });
});

describe('takeLabel: one name per cell and take, as the server spells it', () => {
  it('letters cells from A, numbers runs from 0, and numbers a later output from 2', () => {
    expect(takeLabel(0, 0)).toBe('0A');
    expect(takeLabel(12, 3)).toBe('12D');
    expect(takeLabel(3, 1, 0)).toBe('3B');
    expect(takeLabel(3, 1, 1)).toBe('3B.2');
  });
  it('continues past Z as AA, AB and on', () => {
    expect(takeLabel(0, 25)).toBe('0Z');
    expect(takeLabel(0, 26)).toBe('0AA');
    expect(takeLabel(0, 27)).toBe('0AB');
    expect(takeLabel(0, 51)).toBe('0AZ');
    expect(takeLabel(0, 52)).toBe('0BA');
    expect(takeLabel(0, 701)).toBe('0ZZ');
    expect(takeLabel(0, 702)).toBe('0AAA');
    expect(takeLabel(7, 26, 2)).toBe('7AA.3');
  });
  it('gives every position of a run its own name of capital letters', () => {
    const names = Array.from({length: 2000}, (_, pos) => takeLabel(4, pos));
    expect(new Set(names).size).toBe(names.length);
    expect(names.every(name => /^4[A-Z]+$/.test(name))).toBe(true);
  });
  it('names material by its run, cell and output, and imported material by its file', () => {
    const take = {turn_idx: 2, slot_pos: 27, output_index: 1, material: null} as unknown as Material;
    expect(label(take)).toBe('2AB.2');
    expect(label({turn_idx: null, slot_pos: null, output_index: 0, material: {filename: 'source.png'}} as unknown as Material)).toBe('source.png');
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
    setArtistName('  Ada  ');
    expect(artist()).toEqual({kind: 'human', name: 'Ada'});
    expect(command().actor).toEqual({kind: 'human', name: 'Ada'});
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
