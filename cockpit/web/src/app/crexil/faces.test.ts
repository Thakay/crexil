import { afterEach, describe, expect, it } from 'vitest';
import { catalogueFrom, setCatalogue } from './catalogue';
import { faceCost, pickerGroups, runLine, sectionFoot, statusWord } from './faces';

const named = {id: 'a', engine: 'Flux 2', identity_hidden: null, charged_usd: null, material: null} as never;
const charged = {id: 'b', engine: 'Flux 2', identity_hidden: null, charged_usd: .004, material: null} as never;
const blind = (id: string) => ({id, engine: 'hidden', identity_hidden: true, charged_usd: null, material: null}) as never;
const imported = {id: 'c', engine: null, identity_hidden: null, charged_usd: null, material: {origin: {kind: 'import'}}} as never;
const take = (engine: string, money: {charged_usd?: number | null; committed_usd?: number | null} = {}) =>
  ({id: engine, engine, identity_hidden: null, charged_usd: null, committed_usd: null, material: null, ...money}) as never;

const ENGINES = [{id: 'qwen3-tts', name: 'Qwen3-TTS 1.7B', lane: 'local' as const},
  {id: 'elevenlabs-sfx', name: 'ElevenLabs Sound Effects', lane: 'external' as const}];
const OPERATIONS = [{id: 'raw-flux2', label: 'Raw · FLUX.2 klein', provider: null},
  {id: 'google/gemini-2.5-flash-image', label: 'Nano Banana', provider: 'openrouter'}];
afterEach(() => setCatalogue(new Map()));

describe('faceCost', () => {
  it('shows a confirmed charge exactly, or why none is shown', () => {
    expect(faceCost(charged)).toBe('$0.004');
    expect(faceCost(named)).toBe('Unverified');
    expect(faceCost(imported)).toBe('Source');
  });
  it('makes no cost claim for a blind take until its reveal, never "Unverified"', () => {
    expect(faceCost(blind('x'))).toBe('After reveal');
  });
  it('reads LOCAL for a take its local engine made where no money moved, never "Unverified" (law 5)', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(faceCost(take('qwen3-tts'))).toBe('Local');
    expect(faceCost(take('raw-flux2', {charged_usd: 0, committed_usd: 0}))).toBe('Local');
    // a hosted take keeps its charge, or says it is not reported yet
    expect(faceCost(take('elevenlabs-sfx'))).toBe('Unverified');
    expect(faceCost(take('google/gemini-2.5-flash-image', {charged_usd: .039}))).toBe('$0.039');
    // money that moved is never hidden behind LOCAL
    expect(faceCost(take('qwen3-tts', {committed_usd: .01}))).toBe('Unverified');
    expect(faceCost(take('qwen3-tts', {charged_usd: .01}))).toBe('$0.01');
  });
});

describe('pickerGroups', () => {
  it('groups takes by who made them, and a blind run under one neutral row', () => {
    expect(pickerGroups([named, blind('x'), charged, blind('y'), imported]).map(g => [g.label, g.nodes.map(n => (n as {id: string}).id)]))
      .toEqual([['Flux 2', ['a', 'b']], ['Engine hidden', ['x', 'y']], ['Imported material', ['c']]]);
  });
  it('heads a row with the engine catalogue name', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(pickerGroups([take('qwen3-tts'), take('raw-flux2')]).map(g => g.label)).toEqual(['Qwen3-TTS 1.7B', 'Raw · FLUX.2 klein']);
  });
});

describe('runLine', () => {
  it("names each attempt's engine and state in words, and a blind run's engine never", () => {
    const slots = [{engine: 'Flux 2', identity_hidden: null, status: 'landed'}, {engine: 'hidden', identity_hidden: true, status: 'failed'}] as never;
    expect(runLine(slots)).toBe('Flux 2 · Landed / Engine hidden · Failed');
  });
  it('names engines by their catalogue names', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(runLine([{engine: 'elevenlabs-sfx', identity_hidden: null, status: 'set_aside'}])).toBe('ElevenLabs Sound Effects · Set aside');
    expect(statusWord('held')).toBe('Held');
  });
});

describe("sectionFoot: a section says its run's state (BIBLE section states)", () => {
  const turn = (id: string, statuses: string[]) => ({id, slots: statuses.map(status => ({status}))}) as never;
  const section = (fields: Record<string, unknown> = {}) => ({state: 'submitted', turn_ids: ['t1'], selection: [], node_ids: [], ...fields}) as never;
  it('says nothing has run on a preparation', () => {
    expect(sectionFoot(section({state: 'draft', turn_ids: []}), [], new Set())).toBe('Preparation · nothing run yet');
  });
  it('says how far its runs have landed, and what failed meanwhile', () => {
    expect(sectionFoot(section(), [turn('t1', ['running', 'queued', 'landed', 'failed'])], new Set())).toBe('Landing · 2 of 4 · 1 failed');
    expect(sectionFoot(section(), [turn('t1', ['running', 'running'])], new Set())).toBe('Landing · 0 of 2');
  });
  it("says only how far a blind run in flight has got, never what failed, which could tell its engines apart", () => {
    expect(sectionFoot(section(), [turn('t1', ['running', 'failed', 'landed'])], new Set(['t1']))).toBe('Landing · 2 of 3');
  });
  it('once landed, says what is chosen and every attempt that did not land', () => {
    const settled = [turn('t1', ['landed', 'failed', 'held', 'skipped'])];
    expect(sectionFoot(section({selection: ['a'], node_ids: ['a', 'b', 'c']}), settled, new Set())).toBe('1 of 3 selected · 1 failed · 1 held · 1 skipped');
    expect(sectionFoot(section({selection: ['a'], node_ids: ['a']}), [turn('t1', ['landed'])], new Set())).toBe('1 of 1 selected');
  });
});
