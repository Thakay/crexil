// The voice director's conversation, with the ElevenLabs SDK replaced by a fake and the shared
// player driving a fake media element: the token reaches only the SDK, paid runs, free runs that
// leave this machine and picks wait for the artist's click, the meter ends the session at its
// limit and records the minutes, the panel says what leaves the machine before a session, and the
// microphone mutes before any take sounds, whoever starts it, so the agent never hears one.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { audioUrl, player } from '../audio';
import { HUMAN, money } from '../crexil/api';
import Director, { CONNECTING, DISCLOSURE, shownWords } from './Director';
import { DIRECTOR, type VoiceStarted } from './door';
import { fakeDoor, freeHosted, OPERATIONS, review, session, usage } from './fakes';
import { TOOL_NAMES } from './tools';

// One log, in order, of what reaches the conversation and what sounds.
const fake = vi.hoisted(() => {
  const log: string[] = [];
  const sdk = {
    open: false,
    options: {} as Record<string, never>,
    start: vi.fn(), update: vi.fn(),
    end: vi.fn(() => {sdk.open = false;}),
    // Like the SDK: with no conversation open, these throw.
    setMuted: vi.fn((muted: boolean) => {
      if (!sdk.open) throw new Error('No active conversation. Call startSession() first.');
      log.push(muted ? 'microphone muted' : 'microphone open');
    }),
    setVolume: vi.fn(({volume}: {volume: number}) => {
      if (!sdk.open) throw new Error('No active conversation. Call startSession() first.');
      log.push(`agent at ${volume}`);
    }),
  };
  type Element = {src: string; currentTime: number; volume: number; preload: string; play: () => Promise<void>;
    pause: () => void; addEventListener: (type: string, listener: () => void) => void; emit: (type: string) => void};
  const audio = {element: null as Element | null};
  const make = (): Element => {
    const listeners = new Map<string, (() => void)[]>();
    const emit = (type: string) => {for (const listener of listeners.get(type) ?? []) listener();};
    const element: Element = {src: '', currentTime: 0, volume: 1, preload: '', emit,
      play: vi.fn(async () => {log.push('take sounds'); emit('play');}),
      pause: vi.fn(() => emit('pause')),
      addEventListener: (type, listener) => {listeners.set(type, [...(listeners.get(type) ?? []), listener]);}};
    audio.element = element;
    return element;
  };
  return {log, sdk, audio, make};
});

vi.mock('@elevenlabs/react', () => ({
  ConversationProvider: ({children}: {children: unknown}) => children,
  useConversation: (options: Record<string, never>) => {
    fake.sdk.options = options;
    return {status: fake.sdk.open ? 'connected' : 'connecting', isSpeaking: false, startSession: fake.sdk.start,
      endSession: fake.sdk.end, sendContextualUpdate: fake.sdk.update, setMuted: fake.sdk.setMuted, setVolume: fake.sdk.setVolume};
  },
}));

vi.mock('../audio', async () => {
  const actual = await vi.importActual<typeof import('../audio/player')>('../audio/player');
  const {useStore} = await import('zustand');
  const shared = actual.createPlayer(fake.make);
  return {
    audioUrl: actual.audioUrl, player: shared,
    usePlayer: <T,>(select: (state: import('../audio/player').PlayerState) => T) => useStore(shared.store, select),
  };
});

type Tools = Record<string, (parameters: object) => Promise<string>>;

test('a director line keeps its words and loses its audio tags', () => {
  expect(shownWords('[ready] Ready to run two takes.')).toBe('Ready to run two takes.');
  expect(shownWords('Sure. [laughs] Let\'s go.')).toBe("Sure. Let's go.");
  expect(shownWords('[whispers] [strong accent]')).toBe('');
  expect(shownWords('Take [3A] is warmer.')).toBe('Take [3A] is warmer.');
});

const paid = {action: 'voice-design', prompt: 'A gruff dwarf blacksmith', spending_limit_usd: 0.5,
  preview_line: 'Steel remembers every strike, and so do I. Bring me the ore before the fire dies, and mind the anvil.',
  engines: [{id: 'audio:voice-design:elevenlabs-voice-design', count: 1}]};

beforeEach(() => {
  for (const spy of [fake.sdk.start, fake.sdk.end, fake.sdk.update, fake.sdk.setMuted, fake.sdk.setVolume]) spy.mockClear();
  fake.sdk.open = false;
  player.release();
  fake.log.length = 0;
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

async function opened(door = fakeDoor()) {
  const view = render(<Director projectId="p1" door={door}/>);
  await screen.findByText(/left of/);
  return {door, view};
}

/** A session the server started, still connecting. */
async function started(door = fakeDoor()) {
  const {view} = await opened(door);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: /Start · up to/}));});
  await waitFor(() => expect(fake.sdk.start).toHaveBeenCalled());
  return {door, view, tools: fake.sdk.start.mock.calls[0][0].clientTools as Tools};
}

const connect = () => act(() => {
  fake.sdk.open = true;
  (fake.sdk.options.onConnect as unknown as (props: {conversationId: string}) => void)({conversationId: 'conv-1'});
});

/** A session that is connected and listening. */
async function connected(door = fakeDoor()) {
  const found = await started(door);
  connect();
  fake.log.length = 0;
  return found;
}

test('before a session the panel says what leaves the machine and what a session may cost', async () => {
  await opened();
  const notice = screen.getByText(DISCLOSURE);
  for (const words of ['microphone audio goes to ElevenLabs and the director\'s language model', 'the brief, run prompts, take labels and reasons',
    'keeps each conversation for 7 days', 'Take audio never goes']) expect(notice).toHaveTextContent(words);
  expect(screen.getAllByText(/goes to ElevenLabs/)).toHaveLength(1);
  expect(screen.getByText(`28:00 left of 30:00 · about ${money(0.08)} a minute, estimate; model use is billed on top`)).toBeInTheDocument();
  expect(screen.getByRole('button', {name: `Start · up to ${money(0.4)}`})).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('Ready');
});

test('a session cannot start until the budget allows one, and the panel says why', async () => {
  await opened(fakeDoor({usage: async () => usage({ready: false, cap_seconds: 0, left_seconds: 0,
    reasons: ['The voice minutes budget is zero. Set CREXIL_VOICE_MINUTES_CAP in .env, then restart.']})}));
  expect(screen.getByText(/The voice minutes budget is zero/)).toBeInTheDocument();
  // a session that cannot start carries no price and no money gold
  const start = screen.getByRole('button', {name: 'Start'});
  expect(start).toBeDisabled();
  expect(start.className).not.toMatch(/paid/);
});

test('starting asks the server for a token and hands it only to the SDK, with the six tools', async () => {
  const {door} = await started();
  expect(door.start).toHaveBeenCalledWith('p1', HUMAN);
  const options = fake.sdk.start.mock.calls[0][0];
  expect(options).toMatchObject({conversationToken: 'conversation-token-secret', connectionType: 'webrtc'});
  expect(Object.keys(options.clientTools).sort()).toEqual([...TOOL_NAMES].sort());
  expect(document.body.textContent).not.toContain('conversation-token-secret');
  expect(screen.getByRole('button', {name: 'End session'})).toBeInTheDocument();
  expect(screen.queryByText(DISCLOSURE)).toBeNull();
  expect(screen.getByText('Esc · Back to the work')).toBeInTheDocument();
});

test('a paid run waits for the artist, then is submitted under the artist’s name', async () => {
  const {door, tools} = await connected();
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  expect(within(card).getByText(`Cap ${money(0.5)} · reserves up to ${money(0.031)} · by the published price; the final provider charge is not capped`)).toBeInTheDocument();
  expect(within(card).getByText('Sends off this machine to api.elevenlabs.io: the voice description and its preview line.')).toBeInTheDocument();
  expect(door.submit).not.toHaveBeenCalled();
  const run = within(card).getByRole('button', {name: `Run · up to ${money(0.031)}`});
  expect(run.className).toMatch(/paid/);
  expect(run).not.toHaveFocus();
  await act(async () => {fireEvent.click(run);});
  await waitFor(() => expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', HUMAN));
  expect(fake.sdk.update).toHaveBeenCalledWith(expect.stringContaining('The artist ran'));
  expect(await within(card).findByText('Queued 2 attempts.')).toBeInTheDocument();
});

test('a free local run starts on the agent’s request, as the director', async () => {
  const {door, tools} = await connected(fakeDoor({review: async () => review({reserved_usd: 0, maximum_usd: 0})}));
  await act(async () => {
    await tools.prepare_run({...paid, spending_limit_usd: 0, engines: [{id: 'audio:voice-design:qwen3-tts', count: 2}]});
  });
  expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', DIRECTOR);
  expect(await screen.findByText(/Started for you · free · local engines/)).toBeInTheDocument();
});

test('a free run that leaves this machine waits for the artist, says what leaves, and its Run carries no money', async () => {
  const hosted = freeHosted('sfx', 'Sound effect · Free hosted');
  const {door, tools} = await connected(fakeDoor({operations: async () => [...OPERATIONS, hosted],
    review: async () => review({reserved_usd: 0, reserved_micro_usd: 0, maximum_usd: 0})}));
  await act(async () => {
    await tools.prepare_run({action: 'sfx', prompt: 'Hammer on hot iron', spending_limit_usd: 0, engines: [{id: hosted.id, count: 2}]});
  });
  const card = await screen.findByRole('article', {name: /Prepared run/});
  expect(door.submit).not.toHaveBeenCalled();
  expect(screen.queryByText(/Started for you/)).toBeNull();
  expect(within(card).getByText(`Cap ${money(0)} · free`)).toBeInTheDocument();
  expect(within(card).getByText('Sends off this machine to sounds.example.com: the sound description.')).toBeInTheDocument();
  const run = within(card).getByRole('button', {name: 'Run · free'});
  expect(run.className).not.toMatch(/paid/);
  expect(run).not.toHaveFocus();
  await act(async () => {fireEvent.click(run);});
  await waitFor(() => expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', HUMAN));
  expect(fake.sdk.update).toHaveBeenCalledWith(expect.stringContaining('The artist ran'));
});

test('a price that moved since the director prepared the run is shown again and not submitted', async () => {
  let reviews = 0;
  const door = fakeDoor({review: async () => ++reviews === 1 ? review() : review({token: 'token-2', reserved_usd: 0.05})});
  const {tools} = await connected(door);
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: `Run · up to ${money(0.031)}`}));});
  expect(await within(card).findByText(/changed since the director prepared it/)).toBeInTheDocument();
  expect(within(card).getByRole('button', {name: `Run · up to ${money(0.05)}`})).toBeInTheDocument();
  expect(door.submit).not.toHaveBeenCalled();
});

test('spending that moved at submit is reviewed again and waits for another click', async () => {
  let reviews = 0;
  const door = fakeDoor({
    review: async () => ++reviews < 3 ? review() : review({token: 'token-3', reserved_usd: 0.04}),
    submit: async () => ({ok: false, status: 409, conflict: 'spending', message: 'Spending moved since review.'}),
  });
  const {tools} = await connected(door);
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: `Run · up to ${money(0.031)}`}));});
  expect(await within(card).findByText(/Spending moved since review. The amount shown is current/)).toBeInTheDocument();
  expect(within(card).getByRole('button', {name: `Run · up to ${money(0.04)}`})).toBeInTheDocument();
  expect(door.submit).toHaveBeenCalledTimes(1);
});

test('discarding a prepared run keeps its draft and tells the agent', async () => {
  const {tools} = await connected();
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  fireEvent.click(within(card).getByRole('button', {name: 'Discard'}));
  expect(within(card).getByText('Discarded. The draft stays in the project.')).toBeInTheDocument();
  expect(fake.sdk.update).toHaveBeenCalledWith(expect.stringContaining('discarded the prepared run'));
});

test('a proposal is picked under the artist’s name, keeping the takes already picked', async () => {
  const {door, tools} = await connected();
  await act(async () => {await tools.propose_pick({run_id: 'run-2', takes: ['2B'], reason: 'Warmest.'});});
  expect(door.propose).toHaveBeenCalledWith('p1', ['take-2b'], 'Warmest.');
  const card = await screen.findByRole('article', {name: 'Proposal: 2B'});
  fireEvent.change(within(card).getByLabelText('Reason'), {target: {value: 'Warmest, with grit.'}});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: 'Pick 2B'}));});
  await waitFor(() => expect(door.pick).toHaveBeenCalledWith('run-2', 4, ['take-2a', 'take-2b'], 'Warmest, with grit.', HUMAN));
  expect(fake.sdk.update).toHaveBeenCalledWith('The artist picked 2B.');
});

test('the microphone mutes and the agent turns down before a take the director plays sounds', async () => {
  const {tools} = await connected();
  await act(async () => {await tools.play_take({run_id: 'run-2', take: '2B'});});
  expect(fake.audio.element?.src).toBe(audioUrl('take-2b'));
  expect(fake.log).toEqual(['microphone muted', 'agent at 0.2', 'take sounds']);
  expect(screen.getByRole('status')).toHaveTextContent('Playing a take · microphone muted');
  // the status takes the whole cap row while a take plays: the Escape hint gives way, so the
  // row never wraps at the column's width
  expect(screen.queryByText('Esc · Back to the work')).toBeNull();
  act(() => {player.pause();});
  expect(screen.getByText('Esc · Back to the work')).toBeInTheDocument();
});

test("the log shows the director's words without the audio tags its voice reads as cues", async () => {
  await connected();
  const said = fake.sdk.options.onMessage as unknown as (message: {message: string; role: string}) => void;
  act(() => {
    said({message: '[ready] Ready to run two takes on ElevenLabs sound effects.', role: 'agent'});
    said({message: '[sighs]', role: 'agent'});
    said({message: 'Run the two takes [3A] and [3B].', role: 'agent'});
  });
  const log = screen.getByRole('log', {name: 'Conversation'});
  expect(log).toHaveTextContent('Ready to run two takes on ElevenLabs sound effects.');
  expect(log).not.toHaveTextContent('[ready]');
  expect(log).not.toHaveTextContent('sighs');
  // a take's label in brackets is a word, never a tag
  expect(log).toHaveTextContent('Run the two takes [3A] and [3B].');
});

test('a take started outside the director, by the grid or inspection, mutes the microphone first too', async () => {
  await connected();
  await act(async () => {await player.play('take-from-the-grid');});
  expect(fake.log).toEqual(['microphone muted', 'agent at 0.2', 'take sounds']);
  await act(async () => {await player.toggle('take-from-the-grid');});
  fake.log.length = 0;
  await act(async () => {await player.toggle('take-from-the-grid');});
  expect(fake.log).toEqual(['microphone muted', 'agent at 0.2', 'take sounds']);
});

test('the agent comes back up when a take stops, and the microphone 400 ms later', async () => {
  await connected();
  vi.useFakeTimers();
  await act(async () => {await player.play('take-1');});
  fake.log.length = 0;
  act(() => {player.pause();});
  expect(fake.log).toEqual(['microphone muted', 'agent at 1']);
  act(() => {vi.advanceTimersByTime(399);});
  expect(fake.log).not.toContain('microphone open');
  act(() => {vi.advanceTimersByTime(1);});
  expect(fake.log).toEqual(['microphone muted', 'agent at 1', 'microphone open', 'agent at 1']);
});

test('a take that starts again within the 400 ms keeps the microphone muted throughout', async () => {
  await connected();
  vi.useFakeTimers();
  await act(async () => {await player.play('take-1');});
  act(() => {player.pause();});
  act(() => {vi.advanceTimersByTime(200);});
  await act(async () => {await player.play('take-2');});
  act(() => {vi.advanceTimersByTime(1000);});
  expect(fake.log).not.toContain('microphone open');
});

test('the artist’s own mute holds through takes, and opens only on the artist’s word', async () => {
  await connected();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', {name: 'Mute microphone'}));
  await act(async () => {await player.play('take-1');});
  act(() => {player.pause();});
  act(() => {vi.advanceTimersByTime(1000);});
  expect(fake.log).not.toContain('microphone open');
  fireEvent.click(screen.getByRole('button', {name: 'Unmute microphone'}));
  expect(fake.log.at(-2)).toBe('microphone open');
});

test('starting a session stops the take that plays, and no take sounds until it listens', async () => {
  await act(async () => {await player.play('take-1');});
  await started();
  // The microphone opens while the session connects, before it can be muted.
  expect(player.store.getState().playing).toBe(false);
  await act(async () => {await player.play('take-2');});
  expect(fake.log).toEqual(['take sounds']);
  expect(player.store.getState()).toMatchObject({nodeId: 'take-2', playing: false, error: CONNECTING});
  connect();
  await act(async () => {await player.play('take-2');});
  expect(fake.log.slice(-3)).toEqual(['microphone muted', 'agent at 0.2', 'take sounds']);
});

test('the session is set from the guard and the artist’s mute the moment it connects', async () => {
  await started();
  fireEvent.click(screen.getByRole('button', {name: 'Mute microphone'}));
  expect(fake.log).toEqual([]);
  connect();
  expect(fake.log).toEqual(['microphone muted', 'agent at 1']);
});

test('a session that fails to connect is ended and recorded, and takes play again', async () => {
  const {door} = await started();
  act(() => {(fake.sdk.options.onError as unknown as (problem: string) => void)('Could not reach the microphone.');});
  expect(fake.sdk.end).toHaveBeenCalled();
  await waitFor(() => expect(door.end).toHaveBeenCalledWith('voice-1', 0, null, HUMAN));
  expect(screen.getByRole('alert')).toHaveTextContent('The voice session reported a problem: Could not reach the microphone.');
  await act(async () => {await player.play('take-1');});
  expect(fake.log).toEqual(['take sounds']);
});

test('a panel closed while the server starts its session never connects it, and records it unused', async () => {
  let release: (value: VoiceStarted) => void = () => undefined;
  const door = fakeDoor({start: () => new Promise<VoiceStarted>(resolve => {release = resolve;})});
  const {view} = await opened(door);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: /Start · up to/}));});
  view.unmount();
  await act(async () => {release({session: session(), conversation_token: 'conversation-token-secret', signed_url: null, usage: usage()});});
  expect(fake.sdk.start).not.toHaveBeenCalled();
  expect(door.end).toHaveBeenCalledWith('voice-1', 0, null, HUMAN);
});

test('the session ends at its limit and records the minutes observed', async () => {
  const {door} = await started();
  vi.useFakeTimers();
  connect();
  await act(async () => {vi.advanceTimersByTime(300_000);});
  expect(fake.sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalledWith('voice-1', 300, 'conv-1', HUMAN);
  expect(screen.getByText('The session reached its limit and ended.')).toBeInTheDocument();
});

test('ending a session records the minutes since it connected', async () => {
  const {door} = await started();
  vi.useFakeTimers();
  connect();
  await act(async () => {vi.advanceTimersByTime(30_000);});
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End session'}));});
  expect(fake.sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalledWith('voice-1', 30, 'conv-1', HUMAN);
});

test('minutes that could not be recorded stay offered for recording', async () => {
  let ends = 0;
  const door = fakeDoor({end: async () => {if (++ends === 1) throw new Error('The server could not be reached.'); return {} as never;}});
  await connected(door);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End session'}));});
  expect(await screen.findByRole('alert')).toHaveTextContent(/minutes were not recorded/);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: "Record the last session's minutes"}));});
  expect(door.end).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('button', {name: /Start · up to/})).toBeInTheDocument();
});

test('a panel that goes away ends its live session and records it', async () => {
  const {door, view} = await connected();
  await act(async () => {view.unmount();});
  expect(fake.sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalled();
});

test('each tool the director uses is shown in the conversation', async () => {
  const {tools} = await connected();
  await act(async () => {await tools.list_engines({payload: 'audio'});});
  expect(screen.getByRole('log', {name: 'Conversation'})).toHaveTextContent('Director used list engines');
});
