// The voice director's tools: what they send, what they refuse, and what the agent may read.
// Every answer is checked for media and addresses, because no take's audio or file may reach
// the agent's model.
import { describe, expect, test, vi } from 'vitest';
import { ActionError } from '../crexil/api';
import { DIRECTOR } from './door';
import { fakeDoor, review, runView } from './fakes';
import { carriesMediaOrAddress } from './guard';
import { createTools, findTake, TOOL_NAMES, type Handoff } from './tools';

function setup(overrides: Parameters<typeof fakeDoor>[0] = {}) {
  const door = fakeDoor(overrides);
  const handoff = {
    runCard: vi.fn(), ranFree: vi.fn(), proposal: vi.fn(),
    play: vi.fn(async (_nodeId: string, _seconds?: number): Promise<string | null> => null),
  };
  const tools = createTools('p1', door, handoff);
  const said: string[] = [];
  const call = async (name: (typeof TOOL_NAMES)[number], parameters: Record<string, unknown> = {}) => {
    const text = await tools[name](parameters);
    said.push(text);
    return JSON.parse(text);
  };
  return {door, handoff, call, said};
}

const paidDesign = {action: 'voice-design', prompt: 'A gruff dwarf blacksmith, low and warm', spending_limit_usd: 0.5,
  preview_line: 'Steel remembers every strike, and so do I. Bring me the ore before the fire dies, and mind the anvil.',
  engines: [{id: 'audio:voice-design:elevenlabs-voice-design', count: 1}, {id: 'audio:voice-design:qwen3-tts', count: 1}]};

describe('what the agent reads', () => {
  test('the six tools are exactly the ones the agent is configured with', () => {
    expect(Object.keys(createTools('p1', fakeDoor(), {} as Handoff)).sort()).toEqual([...TOOL_NAMES].sort());
  });

  test('no answer carries media, a URL, a file route or a media file name', async () => {
    const {call, said} = setup();
    await call('list_engines', {payload: 'audio'});
    await call('list_engines', {payload: 'image'});
    await call('get_project_summary');
    await call('prepare_run', paidDesign);
    await call('get_run_status', {run_id: 'run-2'});
    await call('play_take', {run_id: 'run-2', take: '2B'});
    await call('propose_pick', {run_id: 'run-2', takes: ['2B'], reason: 'Warmest of the three.'});
    for (const text of said) expect(carriesMediaOrAddress(text), text).toBe(false);
  });

  test('engines list their availability in plain words, with addresses withheld', async () => {
    const {call} = setup();
    const {engines} = await call('list_engines', {payload: 'audio'});
    expect(engines.map((engine: {id: string}) => engine.id)).toEqual([
      'audio:voice-design:elevenlabs-voice-design', 'audio:voice-design:qwen3-tts', 'audio:speak:qwen3-tts', 'audio:sfx:elevenlabs-sfx']);
    const local = engines[1];
    expect(local.available).toBe(false);
    expect(local.why_not[0]).toContain('[address withheld]');
    expect(JSON.stringify(engines)).not.toContain('elevenlabs.io');
  });

  test('the project summary names runs and picked takes by label', async () => {
    const {call} = setup();
    const summary = await call('get_project_summary');
    expect(summary.project).toBe('Dwarf blacksmith');
    expect(summary.spending_limit_usd).toBe(1);
    expect(summary.runs).toEqual([{run_id: 'run-old', title: 'Voices', state: 'submitted', action: 'voice-design', takes: 1, picked: ['1B']}]);
  });

  test("a blind run's status never names an engine, and causes lose their addresses", async () => {
    const blind = runView({blind: true, cells: runView().cells.map(cell => ({...cell, engine: 'hidden', identity_hidden: true}))});
    const {call} = setup({run: async () => blind});
    const status = await call('get_run_status', {run_id: 'run-2'});
    expect(status.cells.every((cell: {engine: string}) => cell.engine === 'hidden')).toBe(true);
    expect(status.summary).toContain('[address withheld]');
    expect(status.cells[2].cause).toContain('[address withheld]');
    expect(status.cells[1].takes[0]).toMatchObject({take: '2B', seconds: 3.5, words: 'Steel remembers every strike.'});
  });

  test('a failure answers with the plain line and nothing else', async () => {
    const {call} = setup({operations: async () => {throw new ActionError({detail: 'The server is starting. Try again in a moment.'}, 503);}});
    expect(await call('list_engines')).toEqual({error: 'The server is starting. Try again in a moment.'});
  });
});

describe('preparing a run', () => {
  test('a paid run is prepared as the director and waits for the artist; nothing is submitted', async () => {
    const {call, door, handoff} = setup();
    const result = await call('prepare_run', paidDesign);
    expect(result).toMatchObject({status: 'awaiting_confirmation', run_id: 'run-new', reserved_usd: 0.031, cap_usd: 0.5});
    expect(door.submit).not.toHaveBeenCalled();
    const [projectId, title, setupSent] = door.prepare.mock.calls[0];
    expect(projectId).toBe('p1');
    expect(title).toBe('Voice director · voice design · A gruff dwarf blacksmith, low and w…');
    expect(setupSent).toMatchObject({action: 'voice-design', spending_policy: 'published_tariff', spending_limit_usd: 0.5,
      attempt_limit: 2, blind: false, references: []});
    expect(setupSent.operations).toEqual([
      {operation_id: 'audio:voice-design:elevenlabs-voice-design', revision: 'rev-audio:voice-design:elevenlabs-voice-design', count: 1,
        controls: {preview_text: paidDesign.preview_line}},
      {operation_id: 'audio:voice-design:qwen3-tts', revision: 'rev-audio:voice-design:qwen3-tts', count: 1,
        controls: {preview_text: paidDesign.preview_line}},
    ]);
    expect(handoff.runCard).toHaveBeenCalledWith(expect.objectContaining({runId: 'run-new', reservedUsd: 0.031, published: true,
      ready: true, token: 'token-1', engines: [{label: 'Voice design · ElevenLabs', count: 1}, {label: 'Voice design · Qwen3-TTS', count: 1}]}));
  });

  test('a run on free local engines starts at once, as the director', async () => {
    const {call, door, handoff} = setup({review: async () => review({reserved_usd: 0, reserved_micro_usd: 0, maximum_usd: 0})});
    const result = await call('prepare_run', {...paidDesign, engines: [{id: 'audio:voice-design:qwen3-tts', count: 2}], spending_limit_usd: 0});
    expect(result).toEqual({status: 'running', run_id: 'run-new', attempts: 2, cost: 'free: local engines only'});
    expect(door.prepare.mock.calls[0][2].spending_policy).toBe('hard_cap');
    expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', DIRECTOR);
    expect(handoff.ranFree).toHaveBeenCalledWith(expect.objectContaining({runId: 'run-new', attempts: 2}));
    expect(handoff.runCard).not.toHaveBeenCalled();
  });

  test('speech uses the voice take named by its label', async () => {
    const {call, door} = setup();
    await call('prepare_run', {action: 'speak', prompt: 'Bring me the ore.', voice_take: '1b', spending_limit_usd: 0,
      engines: [{id: 'audio:speak:qwen3-tts', count: 1}]});
    expect(door.prepare.mock.calls[0][2].references).toEqual([{node_id: 'voice-1b', contribution: ''}]);
  });

  test('a sound effect carries its length', async () => {
    const {call, door} = setup();
    await call('prepare_run', {action: 'sfx', prompt: 'Hammer on hot iron', duration_seconds: 2.5, spending_limit_usd: 0.1,
      engines: [{id: 'audio:sfx:elevenlabs-sfx', count: 2}]});
    expect(door.prepare.mock.calls[0][2].operations[0].controls).toEqual({duration_seconds: 2.5});
  });

  test.each([
    [{...paidDesign, action: 'toString'}, 'The action must be voice-design, speak or sfx.'],
    [{...paidDesign, action: 'generate'}, 'The action must be voice-design, speak or sfx.'],
    [{...paidDesign, prompt: '  '}, 'Describe what to make.'],
    [{...paidDesign, engines: []}, 'Choose 1 to 4 engines from list_engines.'],
    [{...paidDesign, spending_limit_usd: undefined}, 'Give the run a cap in US dollars, and say it to the artist.'],
    [{...paidDesign, spending_limit_usd: -1}, 'Give the run a cap in US dollars, and say it to the artist.'],
    [{...paidDesign, preview_line: undefined}, 'Voice design needs a preview line of 100 to 1000 characters for the takes to speak.'],
    [{...paidDesign, engines: [{id: 'made-up', count: 1}]}, 'made-up is not an engine id. Use list_engines.'],
    [{...paidDesign, engines: [{id: 'audio:sfx:elevenlabs-sfx', count: 1}]}, 'Sound effect · ElevenLabs cannot do voice design.'],
    [{...paidDesign, engines: [{id: 'audio:voice-design:qwen3-tts', count: 5}]}, 'Each engine takes 1 to 4 attempts.'],
    [{action: 'speak', prompt: 'Ore.', spending_limit_usd: 0, engines: [{id: 'audio:speak:qwen3-tts', count: 1}]},
      'Speech needs a voice take; name it by its label, like 2B.'],
    [{action: 'speak', prompt: 'Ore.', voice_take: '9Z', spending_limit_usd: 0, engines: [{id: 'audio:speak:qwen3-tts', count: 1}]},
      'There is no audio take 9Z in this project.'],
  ])('refuses a bad request before anything is prepared (%#)', async (parameters, words) => {
    const {call, door} = setup();
    expect(await call('prepare_run', parameters)).toEqual({error: words});
    expect(door.prepare).not.toHaveBeenCalled();
  });

  test('a run that is not ready returns its errors and sends nothing', async () => {
    const {call, door, handoff} = setup({review: async () => review({ready: false, ready_attempts: 0,
      errors: ['Voice design · ElevenLabs: Needs an ElevenLabs key. See https://elevenlabs.io/app/settings.']})});
    const result = await call('prepare_run', paidDesign);
    expect(result.status).toBe('not_ready');
    expect(result.errors[0]).toContain('[address withheld]');
    expect(door.submit).not.toHaveBeenCalled();
    expect(handoff.runCard).toHaveBeenCalledWith(expect.objectContaining({ready: false}));
  });
});

describe('takes', () => {
  test('a take is found by its label, or by its letter in the latest submission', () => {
    const view = runView();
    expect(findTake(view, '2b')?.take.node_id).toBe('take-2b');
    expect(findTake(view, 'B')?.take.node_id).toBe('take-2b');
    expect(findTake(view, '3B')).toBeNull();
  });

  test('playing a take plays it for the artist and returns facts only', async () => {
    const {call, handoff} = setup();
    expect(await call('play_take', {run_id: 'run-2', take: 'B'})).toEqual({played: true, take: '2B', seconds: 3.5,
      next: 'You do not hear takes. Ask the artist what they heard.'});
    expect(handoff.play).toHaveBeenCalledWith('take-2b', 3.46);
  });

  test.each([
    ['2D', '2D is not audio. The artist can open it in the grid.'],
    ['2Z', 'There is no take 2Z in this run. Its takes are 2A, 2B, 2D.'],
  ])('playing %s is refused in plain words', async (take, words) => {
    const {call, handoff} = setup();
    expect(await call('play_take', {run_id: 'run-2', take})).toEqual({error: words});
    expect(handoff.play).not.toHaveBeenCalled();
  });

  test('a take whose file is gone does not play', async () => {
    const gone = runView();
    gone.cells[1].takes[0].available = false;
    const {call} = setup({run: async () => gone});
    expect(await call('play_take', {run_id: 'run-2', take: '2B'})).toEqual({error: "2B's file is not available, so it cannot play."});
  });

  test('a playback failure is told as the player says it', async () => {
    const {call, handoff} = setup();
    handoff.play.mockResolvedValueOnce('The browser blocked playback. Press play again.');
    expect(await call('play_take', {run_id: 'run-2', take: '2A'})).toEqual({error: 'The browser blocked playback. Press play again.'});
  });

  test('a proposal is recorded as the director and handed to the artist; it never picks', async () => {
    const {call, door, handoff} = setup();
    const result = await call('propose_pick', {run_id: 'run-2', takes: ['B'], reason: 'Warmest of the three.'});
    expect(result).toEqual({proposed: true, decision_id: 'decision-1', takes: ['2B'],
      next: 'Your proposal is recorded. The artist picks with a click in the panel.'});
    expect(door.propose).toHaveBeenCalledWith('p1', ['take-2b'], 'Warmest of the three.');
    expect(door.pick).not.toHaveBeenCalled();
    expect(handoff.proposal).toHaveBeenCalledWith(expect.objectContaining({runId: 'run-2', decisionId: 'decision-1',
      takes: [{label: '2B', nodeId: 'take-2b'}], reason: 'Warmest of the three.'}));
  });

  test.each([
    [{run_id: 'run-2', takes: ['2B'], reason: ''}, 'Give the artist a short reason for the proposal.'],
    [{run_id: 'run-2', takes: [], reason: 'Warm.'}, 'Name the run and the takes you propose.'],
    [{run_id: 'run-2', takes: ['2Z'], reason: 'Warm.'}, 'There is no take 2Z in this run.'],
  ])('a proposal without its parts is refused (%#)', async (parameters, words) => {
    const {call, door} = setup();
    expect(await call('propose_pick', parameters)).toEqual({error: words});
    expect(door.propose).not.toHaveBeenCalled();
  });
});
