import { describe, expect, it } from 'vitest';
import type { Snapshot } from './api';
import { blindFace } from './attempts';
import { hiddenRuns, inFlight, isWithheld, settled, withheldRuns } from './blind';

const slot = (id: string, status: string, pos = 0) => ({id, status, pos, engine: 'hidden', identity_hidden: true, turn_id: 't1'});
const data = (statuses: string[], revealed = false) => ({
  turns: [{id: 't1', idx: 0, slots: statuses.map((status, i) => slot(`s${i}`, status, i))},
    {id: 't2', idx: 1, slots: [slot('x', 'running')]}],
  blind_runs: [{turn_id: 't1', revealed}],
}) as unknown as Snapshot;

describe('blind runs on the client', () => {
  it('knows which runs are still hidden', () => {
    expect([...hiddenRuns(data(['landed']))]).toEqual(['t1']);
    expect([...hiddenRuns(data(['landed'], true))]).toEqual([]);
    expect([...hiddenRuns({} as Snapshot)]).toEqual([]);
  });
  it('withholds a hidden run while any attempt is queued or running, never a run that is not blind', () => {
    expect([...withheldRuns(data(['landed', 'running']))]).toEqual(['t1']);
    expect([...withheldRuns(data(['landed', 'queued']))]).toEqual(['t1']);
    expect([...withheldRuns(data(['landed', 'failed', 'held', 'skipped']))]).toEqual([]);
    expect([...withheldRuns(data(['running'], true))]).toEqual([]);
    expect(isWithheld({turn_id: 't1'}, new Set(['t1']))).toBe(true);
    expect(isWithheld({turn_id: null}, new Set(['t1']))).toBe(false);
  });
  it('counts how far a run has got', () => {
    const turn = data(['landed', 'running', 'failed']).turns[0];
    expect(inFlight(turn)).toBe(true);
    expect(settled(turn)).toEqual({done: 2, total: 3});
  });
  it('gives every attempt of a run in flight one neutral face that names no engine, cost or next move', () => {
    const turn = data(['landed', 'running']).turns[0];
    const landed = blindFace(turn as never, turn.slots[0] as never), running = blindFace(turn as never, turn.slots[1] as never);
    expect(landed).toMatchObject({title: 'IN PROGRESS', tone: 'waiting', engine: 'Blind', cost: null, remedies: [],
      progress: '1 of 2 attempts done. Every take shows when the run settles.'});
    expect({...running, slotId: '', label: ''}).toEqual({...landed, slotId: '', label: ''});
    expect([landed.label, running.label]).toEqual(['0A', '0B']);
  });
});
