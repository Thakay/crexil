// The voice director's browser code, read as text: it never names the ElevenLabs key, its
// header or the provider's address, never keeps anything in browser storage, and loads the
// ElevenLabs SDK only from the lazily loaded panel, so the project surface never carries it.
import { expect, test } from 'vitest';

const sources = import.meta.glob(['./*.ts', './*.tsx', '!./*.test.ts', '!./*.test.tsx', '!./fakes.ts'],
  {query: '?raw', import: 'default', eager: true}) as Record<string, string>;

test('the browser code never names the key, its header or the provider address, and stores nothing', () => {
  const needles = ['xi-' + 'api-key', 'ELEVENLABS_' + 'API_KEY', 'api.' + 'elevenlabs.io', 'local' + 'Storage', 'session' + 'Storage'];
  expect(Object.keys(sources).length).toBeGreaterThanOrEqual(7);
  for (const [file, text] of Object.entries(sources)) {
    for (const needle of needles) expect(text.includes(needle), `${file} names ${needle}`).toBe(false);
  }
});

test('only the lazily loaded panel imports the ElevenLabs SDK', () => {
  const importers = Object.entries(sources).filter(([, text]) => text.includes("from '@elevenlabs/react'")).map(([file]) => file);
  expect(importers).toEqual(['./Director.tsx']);
  expect(sources['./VoiceDirectorPanel.tsx']).toContain("lazy(() => import('./Director'))");
});
