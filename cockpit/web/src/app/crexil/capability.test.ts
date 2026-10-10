import { describe, expect, it } from 'vitest';
import { actionPhrase, operationStanding, termsRefusal, voiceRefusal } from './capability';

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

const V4 = 'Eleven v4 speaks only with voices designed or saved at ElevenLabs.';
const voiceRole = (voices: {providers: string[]; reason: string} | null) => ({role: 'voice', accepts: ['audio'], restrictions: [], voices});
const voice = (provider: string | null) => ({id: `v-${provider}`, payload: 'audio', material: null,
  audio: {voice: provider ? {provider, kind: 'designed'} : null}}) as never;

describe('voiceRefusal: a speech engine names the voices it speaks with (law 12)', () => {
  it("refuses a voice from any other provider in the engine's own sentence", () => {
    expect(voiceRefusal(voice('voice-runner'), voiceRole({providers: ['elevenlabs'], reason: V4}) as never)).toBe(V4);
    expect(voiceRefusal(voice(null), voiceRole({providers: ['elevenlabs'], reason: V4}) as never)).toBe(V4);
  });
  it('takes a voice from a provider it names, and any voice when it names none', () => {
    expect(voiceRefusal(voice('elevenlabs'), voiceRole({providers: ['elevenlabs'], reason: V4}) as never)).toBeNull();
    expect(voiceRefusal(voice('voice-runner'), voiceRole(null) as never)).toBeNull();
    expect(voiceRefusal(voice('voice-runner'), undefined)).toBeNull();
  });
});

describe('operationStanding (law 12: badge, never filter)', () => {
  it('names an operation made for another action without hiding it', () => {
    expect(operationStanding(op({actions: ['voice-design']}), 'region', [])).toEqual({tier: 'notForThis',
      reasons: ['This operation cannot edit a region. Its saved inputs remain available.']});
    expect(actionPhrase('mesh')).toBe('generate 3D from image');
    expect(actionPhrase('voice-design')).toBe('design a voice');
    expect(actionPhrase('not-an-action')).toBe('perform not-an-action');
  });
  it('blocks with every reason: the server reasons, too many sources, and provider terms', () => {
    const standing = operationStanding(op({actions: ['speak'], provider: 'voice-runner', reasons: ['Runner offline.']}), 'speak',
      [take(ELEVEN), take()]);
    expect(standing).toEqual({tier: 'blocked', reasons: ['Runner offline.', 'Accepts 1 source. All 2 sources remain in preparation.', ELEVEN.reason]});
  });
  it('marks a speech engine that cannot speak the chosen voice before it is chosen, and the one that can as ready', () => {
    const v4 = op({actions: ['speak'], provider: 'elevenlabs', sources: [voiceRole({providers: ['elevenlabs'], reason: V4})]});
    const local = op({actions: ['speak'], provider: 'voice-runner', sources: [voiceRole(null)]});
    expect(operationStanding(v4, 'speak', [voice('voice-runner')])).toEqual({tier: 'blocked', reasons: [V4]});
    expect(operationStanding(local, 'speak', [voice('voice-runner')])).toEqual({tier: 'ready', reasons: []});
    expect(operationStanding(v4, 'speak', [voice('elevenlabs')])).toEqual({tier: 'ready', reasons: []});
  });
  it("refuses a blind run's take with one reason that names no provider", () => {
    const hidden = {id: 'h', payload: 'audio', identity_hidden: true, material: null} as never;
    const standing = operationStanding(op({actions: ['speak'], provider: 'voice-runner'}), 'speak', [hidden]);
    expect(standing).toEqual({tier: 'blocked', reasons: ["This take's run is blind. Reveal it to continue from its takes."]});
    expect(JSON.stringify(standing)).not.toMatch(/elevenlabs|voice runner/i);
  });
  it('is ready when nothing stands in the way, and says unavailable when the server says so without a reason', () => {
    expect(operationStanding(op({}), 'generate', [])).toEqual({tier: 'ready', reasons: []});
    expect(operationStanding(op({availability: 'unavailable'}), 'generate', [])).toEqual({tier: 'blocked', reasons: ['Unavailable now.']});
  });
});
