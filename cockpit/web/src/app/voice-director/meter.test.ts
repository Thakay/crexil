import { expect, test } from 'vitest';
import { usage } from './fakes';
import { elapsedSince, minutes, sessionPrice } from './meter';

test('minutes read as m:ss, never negative', () => {
  expect(minutes(0)).toBe('0:00');
  expect(minutes(65.9)).toBe('1:05');
  expect(minutes(300)).toBe('5:00');
  expect(minutes(-3)).toBe('0:00');
  expect(minutes(Number.NaN)).toBe('0:00');
});

test("a session's price is its longest length at the list price per minute", () => {
  expect(sessionPrice(usage({session_max_seconds: 300, usd_per_minute: 0.08}))).toBeCloseTo(0.4);
});

test('elapsed time counts from the connection, and is zero before it', () => {
  expect(elapsedSince(null, 5000)).toBe(0);
  expect(elapsedSince(1000, 31000)).toBe(30);
  expect(elapsedSince(5000, 1000)).toBe(0);
});
