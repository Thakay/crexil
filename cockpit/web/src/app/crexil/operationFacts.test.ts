import { describe, expect, it } from 'vitest';
import type { Operation } from './api';
import { dataLine, priceLine, tariffOffer, tariffPriced } from './operationFacts';

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
