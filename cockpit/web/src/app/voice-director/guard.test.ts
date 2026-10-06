import { expect, test } from 'vitest';
import { answer, carriesMediaOrAddress, plain, WITHHELD } from './guard';

test.each([
  ['a URL', 'Listen at https://example.com/take.wav'],
  ['a loopback address', 'Not answering at http://127.0.0.1:8190'],
  ['a data URI', 'data:audio/wav;base64,UklGRiQAAABXQVZF'],
  ['a file route', 'Fetch /api/crexil/material/n1/audio'],
  ['a media file name', 'Saved as take-2b.wav'],
  ['base64 bytes', 'UklGR'.repeat(30)],
  ['a socket URL', 'wss://api.example.com/conversation'],
])('finds %s', (_, text) => {
  expect(carriesMediaOrAddress(text)).toBe(true);
});

test('plain words pass', () => {
  expect(carriesMediaOrAddress('3 landed, 1 failed. The voice runner is not answering.')).toBe(false);
});

test('scrubbing replaces addresses, routes and file names with plain words', () => {
  expect(plain('Not answering at http://127.0.0.1:8190. Start tools/tts/runner.py; see /api/health; file take.wav'))
    .toBe('Not answering at [address withheld] Start tools/tts/runner.py; see [address withheld] file [file name withheld]');
  expect(plain(null)).toBe('');
});

test('an answer that would leak is withheld whole', () => {
  expect(answer({take: '2B'})).toBe('{"take":"2B"}');
  expect(JSON.parse(answer({file: '/api/crexil/material/n1/audio'}))).toEqual({error: WITHHELD});
});
