import { describe, expect, it } from 'vitest';
import { faceCost, pickerGroups, runLine } from './faces';

const named = {id: 'a', engine: 'Flux 2', identity_hidden: null, charged_usd: null, material: null} as never;
const charged = {id: 'b', engine: 'Flux 2', identity_hidden: null, charged_usd: .004, material: null} as never;
const blind = (id: string) => ({id, engine: 'hidden', identity_hidden: true, charged_usd: null, material: null}) as never;
const imported = {id: 'c', engine: null, identity_hidden: null, charged_usd: null, material: {origin: {kind: 'import'}}} as never;

describe('faceCost', () => {
  it('shows a confirmed charge exactly, or why none is shown', () => {
    expect(faceCost(charged)).toBe('$0.004');
    expect(faceCost(named)).toBe('Unverified');
    expect(faceCost(imported)).toBe('Source');
  });
  it('makes no cost claim for a blind take until its reveal, never "Unverified"', () => {
    expect(faceCost(blind('x'))).toBe('After reveal');
  });
});

describe('pickerGroups', () => {
  it('groups takes by who made them, and a blind run under one neutral row', () => {
    expect(pickerGroups([named, blind('x'), charged, blind('y'), imported]).map(g => [g.label, g.nodes.map(n => (n as {id: string}).id)]))
      .toEqual([['Flux 2', ['a', 'b']], ['Engine hidden', ['x', 'y']], ['Imported material', ['c']]]);
  });
});

describe('runLine', () => {
  it("names each attempt's engine, and a blind run's never", () => {
    const slots = [{engine: 'Flux 2', identity_hidden: null, status: 'landed'}, {engine: 'hidden', identity_hidden: true, status: 'failed'}] as never;
    expect(runLine(slots)).toBe('Flux 2 · landed / Engine hidden · failed');
  });
});
