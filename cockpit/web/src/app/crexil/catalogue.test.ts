// The name a face gives an engine (engineFaceName, read through engineLabel's 'face'): a
// hosted model without its vendor, so models of one vendor read apart in a face's line;
// inspection and the record keep the full name; a blind take names no engine at all.
import { afterEach, describe, expect, it } from 'vitest';
import { engineLabel } from './api';
import { catalogueFrom, engineFaceName, engineName, setCatalogue } from './catalogue';

// OpenRouter's live names for three hosted models, a pinned hosted model, a local preset, a
// local operation whose owner put a colon in its name, and two engines of the matrix.
const OPERATIONS = [
  {id: 'bytedance-seed/seedream-4.5', label: 'ByteDance Seed: Seedream 4.5', provider: 'openrouter'},
  {id: 'bytedance-seed/seedream-5-0-lite', label: 'ByteDance Seed: Seedream 5.0 Lite', provider: 'openrouter'},
  {id: 'recraft/recraft-v4.1', label: 'Recraft: Recraft V4.1', provider: 'openrouter'},
  {id: 'google/gemini-2.5-flash-image', label: 'Nano Banana', provider: 'openrouter'},
  {id: 'raw-flux2', label: 'Raw · FLUX.2 klein', provider: null},
  {id: 'custom-portrait', label: 'Portrait: soft key light', provider: null},
];
const ENGINES = [{id: 'elevenlabs-sfx', name: 'ElevenLabs Sound Effects', lane: 'external' as const},
  {id: 'qwen3-tts', name: 'Qwen3-TTS 1.7B', lane: 'local' as const}];
const take = (engine: string, identity_hidden: boolean | null = null) => ({engine, identity_hidden, material: null});

afterEach(() => setCatalogue(new Map()));

describe('engineFaceName: the name a face gives an engine', () => {
  it('names a hosted model after its vendor, so two models of one vendor read apart', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(engineFaceName('bytedance-seed/seedream-4.5')).toBe('Seedream 4.5');
    expect(engineFaceName('bytedance-seed/seedream-5-0-lite')).toBe('Seedream 5.0 Lite');
    expect(engineFaceName('recraft/recraft-v4.1')).toBe('Recraft V4.1');
  });
  it('keeps a name without a vendor whole, hosted or local', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(engineFaceName('google/gemini-2.5-flash-image')).toBe('Nano Banana');
    expect(engineFaceName('elevenlabs-sfx')).toBe('ElevenLabs Sound Effects');
    expect(engineFaceName('raw-flux2')).toBe('Raw · FLUX.2 klein');
    expect(engineFaceName('qwen3-tts')).toBe('Qwen3-TTS 1.7B');
  });
  it("keeps a local engine's name as its owner wrote it, colon and all", () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(engineFaceName('custom-portrait')).toBe('Portrait: soft key light');
  });
  it('reads an engine the catalogue does not list as its id, as its full name does', () => {
    expect(engineFaceName('bytedance-seed/seedream-4.5')).toBe('bytedance-seed/seedream-4.5');
    expect(engineName('bytedance-seed/seedream-4.5')).toBe('bytedance-seed/seedream-4.5');
  });
});

describe('engineLabel: a face names the model; inspection and the record name it in full', () => {
  it('words a hosted model by its model on a face and by its full name where material is headed', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    expect(engineLabel(take('bytedance-seed/seedream-5-0-lite'), 'face')).toBe('Seedream 5.0 Lite');
    expect(engineLabel(take('bytedance-seed/seedream-5-0-lite'), 'source')).toBe('ByteDance Seed: Seedream 5.0 Lite');
    expect(engineLabel(take('elevenlabs-sfx'), 'face')).toBe('ElevenLabs Sound Effects');
  });
  it('never names the engine of a blind take before its reveal, whatever its record holds', () => {
    setCatalogue(catalogueFrom(OPERATIONS, ENGINES));
    const hidden = take('bytedance-seed/seedream-4.5', true);
    expect(engineLabel(hidden, 'face')).toBe('Blind');
    expect(engineLabel(hidden, 'source')).toBe('Engine hidden');
    expect(engineLabel(take('bytedance-seed/seedream-4.5'), 'face', true)).toBe('Blind');
  });
});
