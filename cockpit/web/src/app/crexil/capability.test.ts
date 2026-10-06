import { describe, expect, it } from 'vitest';
import { actionPhrase, operationStanding, termsRefusal } from './capability';

const op = (fields: object) => ({id: 'op', actions: ['generate'], reasons: [], source_limit: 1, availability: 'available',
  provider: 'openrouter', ...fields}) as never;
const take = (terms?: object) => ({id: 'n', payload: 'audio', material: terms === undefined ? null : {origin: {terms}}}) as never;
const ELEVEN = {no_ai_input: true, provider: 'elevenlabs', allows: ['elevenlabs'],
  reason: "ElevenLabs' terms do not allow its audio as input to another AI model."};

describe('termsRefusal', () => {
  it('lets a take without restricting terms feed any engine', () => {
    expect(termsRefusal(take(), 'voice-runner')).toBeNull();
    expect(termsRefusal(take({no_ai_input: false}), 'voice-runner')).toBeNull();
  });
  it("refuses a restricted take for every provider its terms do not allow, in the provider's words", () => {
    expect(termsRefusal(take(ELEVEN), 'voice-runner')).toBe(ELEVEN.reason);
    expect(termsRefusal(take(ELEVEN), null)).toBe(ELEVEN.reason);
    expect(termsRefusal(take(ELEVEN), 'elevenlabs')).toBeNull();
    expect(termsRefusal(take({no_ai_input: true}), 'x')).toBe("This take's provider does not allow its output as input to another AI model.");
  });
});

describe('operationStanding (law 12: badge, never filter)', () => {
  it('names an operation made for another action without hiding it', () => {
    expect(operationStanding(op({actions: ['voice-design']}), 'region', [])).toEqual({tier: 'notForThis',
      reasons: ['This operation cannot edit a region. Its saved inputs remain available.']});
    expect(actionPhrase('mesh')).toBe('generate 3D from image');
    expect(actionPhrase('voice-design')).toBe('perform voice-design');
  });
  it('blocks with every reason: the server reasons, too many sources, and provider terms', () => {
    const standing = operationStanding(op({actions: ['speak'], provider: 'voice-runner', reasons: ['Runner offline.']}), 'speak',
      [take(ELEVEN), take()]);
    expect(standing).toEqual({tier: 'blocked', reasons: ['Runner offline.', 'Accepts 1 source. All 2 sources remain in preparation.', ELEVEN.reason]});
  });
  it('is ready when nothing stands in the way, and says unavailable when the server says so without a reason', () => {
    expect(operationStanding(op({}), 'generate', [])).toEqual({tier: 'ready', reasons: []});
    expect(operationStanding(op({availability: 'unavailable'}), 'generate', [])).toEqual({tier: 'blocked', reasons: ['Unavailable now.']});
  });
});
