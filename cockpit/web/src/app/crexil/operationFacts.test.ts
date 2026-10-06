import { describe, expect, it } from 'vitest';
import type { Operation } from './api';
import { dataLine, priceLine, priceTag, retriesLine, tariffOffer, tariffPriced } from './operationFacts';

const op = (fields: Partial<Operation>) => ({label: 'Op', maximum_usd_per_attempt: null, price_usd: null, tariff: null,
  price_unit: null, cost_basis: 'Basis', data_destination: 'https://api.elevenlabs.io', ...fields}) as Operation;

describe('dataLine: what leaves the machine', () => {
  it('says what a hosted operation sends off this machine, and to which host', () => {
    expect(dataLine(op({}), 'voice-design')).toBe('Sends off this machine to api.elevenlabs.io: the voice description and its preview line.');
    expect(dataLine(op({}), 'speak')).toBe('Sends off this machine to api.elevenlabs.io: the line and its voice.');
    expect(dataLine(op({}), 'sfx')).toBe('Sends off this machine to api.elevenlabs.io: the sound description.');
  });
  it('says where a local runtime is, without claiming what that runtime does next', () => {
    expect(dataLine(op({data_destination: 'http://127.0.0.1:8190'}), 'speak')).toBe('Sends to 127.0.0.1:8190 on this machine: the line and its voice.');
    expect(dataLine(op({data_destination: 'http://localhost:8188'}), 'generate')).toBe('Sends to localhost:8188 on this machine: the prompt.');
    expect(dataLine(op({data_destination: 'http://[::1]:8189'}), 'mesh')).toBe('Sends to [::1]:8189 on this machine: the sources.');
  });
  it('states a destination that is not an address as given', () => {
    expect(dataLine(op({data_destination: 'Configured runtime'}), 'continue')).toBe('Sends off this machine to Configured runtime: the prompt and any sources.');
  });
});

describe('priceLine and the tariff offer (laws 5 and 22)', () => {
  it('prices a tariff in its own unit, never per attempt, and keeps sub-cent amounts', () => {
    const tariff = {unit: '1k_characters', micro_usd: 80000} as Operation['tariff'];
    expect(priceLine(op({price_usd: 0.08, tariff, price_unit: '1k_characters', cost_basis: 'Published tariff, 0.08 US dollars per 1,000 characters'})))
      .toBe('Published tariff, 0.08 US dollars per 1,000 characters.');
    expect(priceLine(op({maximum_usd_per_attempt: 0.004}))).toBe('Maximum $0.004 per attempt. Basis.');
    expect(priceLine(op({price_usd: 0.0032}))).toBe('$0.0032 estimated per attempt. Basis.');
    expect(priceLine(op({}))).toBe('Price unknown. Basis.');
  });
  it('offers published-tariff budgeting for an operation a hard cap would refuse', () => {
    const paid = op({label: 'Design voice · ElevenLabs Voice Design', tariff: {unit: '1k_characters', micro_usd: 80000} as Operation['tariff']});
    expect(tariffPriced(paid)).toBe(true);
    expect(tariffPriced(op({maximum_usd_per_attempt: 0, tariff: {unit: 'attempt', micro_usd: 0} as Operation['tariff']}))).toBe(false);
    expect(tariffPriced(op({}))).toBe(false);
    expect(tariffOffer([paid])).toBe('Design voice · ElevenLabs Voice Design is priced by its published tariff, with no hard ceiling. '
      + 'A hard cap refuses it before anything is sent.');
    expect(tariffOffer([paid, op({label: 'Speak · ElevenLabs v4'})])).toMatch(/^Design voice · ElevenLabs Voice Design and Speak · ElevenLabs v4 are priced/);
  });
});

describe('priceTag: the price a roster row carries before it is chosen (laws 6 and 22)', () => {
  it('says a ceiling, a price per image, or a tariff in its own unit', () => {
    expect(priceTag(op({maximum_usd_per_attempt: 0.004}))).toBe('up to $0.004');
    expect(priceTag(op({price_usd: 0.039, price_unit: 'image'}))).toBe('$0.039 per image');
    expect(priceTag(op({price_usd: 0.08, price_unit: '1k_characters', tariff: {unit: '1k_characters', micro_usd: 80000} as Operation['tariff']})))
      .toBe('$0.08 per 1,000 characters');
    expect(priceTag(op({price_usd: 0.002, price_unit: 'second', tariff: {unit: 'second', micro_usd: 2000} as Operation['tariff']})))
      .toBe('$0.002 per second');
  });
  it('reads "price unknown" for a hosted engine that publishes no price it can reserve', () => {
    expect(priceTag(op({price_unit: 'image'}))).toBe('price unknown');
    expect(priceTag(op({}))).toBe('price unknown');
  });
  it('carries no price for unmetered local work', () => {
    expect(priceTag(op({maximum_usd_per_attempt: 0, price_usd: 0, price_unit: 'attempt', tariff: {unit: 'attempt', micro_usd: 0} as Operation['tariff']}))).toBeNull();
    expect(priceTag(op({price_usd: 0}))).toBeNull();
  });
});

describe('retriesLine: whether a run retries on its own (law 9)', () => {
  const ops = [{id: 'a', label: 'Make a sound · ElevenLabs SFX'}, {id: 'b', label: 'Design voice · Qwen3-TTS 1.7B'},
    {id: 'c', label: 'Raw · FLUX.2 klein'}, {id: 'd', label: 'Design voice · ElevenLabs Voice Design'}];
  it('says a run whose operations never retry makes no automatic retries, as when the review names none', () => {
    expect(retriesLine({c: false}, ops)).toBe('This run makes no automatic retries.');
    expect(retriesLine({}, ops)).toBe('This run makes no automatic retries.');
  });
  it('says when every attempt retries on its own, and that the retry shows', () => {
    expect(retriesLine({a: true, b: true}, ops)).toBe('An attempt whose engine asks to wait or cannot be reached is tried again '
      + 'on its own, and the retry shows on that attempt.');
  });
  it('names the operations that retry by their catalogue labels, and says the others do not', () => {
    expect(retriesLine({a: true, c: false}, ops)).toBe('Make a sound · ElevenLabs SFX tries an attempt again on its own when the engine '
      + 'asks to wait or cannot be reached, and the retry shows on that attempt. The other operations make no automatic retries.');
    expect(retriesLine({b: true, c: false, d: true}, ops)).toMatch(
      /^Design voice · Qwen3-TTS 1\.7B and Design voice · ElevenLabs Voice Design try an attempt again on its own when /);
  });
});
