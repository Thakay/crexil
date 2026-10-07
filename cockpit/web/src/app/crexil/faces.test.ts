import { afterEach, describe, expect, it } from 'vitest';
import type { Material } from './api';
import { catalogueFrom, setCatalogue } from './catalogue';
import { attemptCharges, chargeLine, faceCost, pickerGroups, runLine, sectionFoot, statusWord } from './faces';

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

describe('an attempt that returns several takes is charged once', () => {
  const material = (fields: Record<string, unknown>) => ({id: 'x', engine: null, identity_hidden: null, charged_usd: null,
    committed_usd: null, material: null, slot_id: null, take: null, output_index: 0, ...fields}) as unknown as Material;
  // Voice Design: one call, three previews; the engine reports its charge on the first take.
  const preview = (output: number, fields: Record<string, unknown> = {}) => material({id: `p${output}`,
    engine: 'elevenlabs-voice-design', slot_id: 's1', take: 1, output_index: output, committed_usd: .0084, ...fields});
  const previews = [preview(0, {charged_usd: .0084}), preview(1), preview(2)];
  it('shows the charge on the take that carries it and INCLUDED on the others, never UNVERIFIED', () => {
    setCatalogue(catalogueFrom(OPERATIONS, [...ENGINES, {id: 'elevenlabs-voice-design', name: 'ElevenLabs Voice Design', lane: 'external'}]));
    const charges = attemptCharges(previews);
    expect(previews.map(item => faceCost(item, charges))).toEqual(['$0.0084', 'Included', 'Included']);
    // inspection says the attempt's one charge on every one of its takes
    expect(previews.map(item => chargeLine(item, charges))).toEqual(Array(3).fill("$0.0084 for this attempt's 3 takes"));
  });
  it("reads UNVERIFIED on every take while the attempt's charge is not reported, and keeps attempts apart", () => {
    const unreported = [preview(0), preview(1)];
    expect(unreported.map(item => faceCost(item, attemptCharges(unreported)))).toEqual(['Unverified', 'Unverified']);
    const retried = preview(1, {take: 2});
    expect(faceCost(retried, attemptCharges([...previews, retried]))).toBe('Unverified');
  });
  it('reads LOCAL on a local take in inspection as on its face, whether its engine reported $0 or nothing', () => {
    setCatalogue(catalogueFrom(OPERATIONS, [...ENGINES, {id: 'trellis2-4b', name: 'TRELLIS.2 4B', lane: 'local'}]));
    const voice = material({engine: 'qwen3-tts', charged_usd: 0, committed_usd: 0, slot_id: 's2', take: 1});
    const mesh = material({engine: 'trellis2-4b', slot_id: 's3', take: 1});
    expect([faceCost(voice), faceCost(mesh)]).toEqual(['Local', 'Local']);
    expect([chargeLine(voice, attemptCharges([voice])), chargeLine(mesh, attemptCharges([mesh]))]).toEqual(['Local', 'Local']);
    // a hosted take whose charge is not reported says so; a reported one shows it exactly
    expect(chargeLine(material({engine: 'elevenlabs-sfx'}), new Map())).toBe('No confirmed charge recorded');
    expect(chargeLine(charged, new Map())).toBe('$0.004');
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

describe("sectionFoot: a section says its run's state", () => {
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
