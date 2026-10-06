// The voice director panel, with the ElevenLabs SDK and the audio player replaced by fakes:
// the token reaches only the SDK, paid runs and picks wait for the artist's click, the meter
// ends the session at its limit and records the minutes, and a playing take mutes the
// microphone so the agent never hears it.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { player } from '../audio';
import { HUMAN } from '../crexil/api';
import { ViewContext } from '../crexil/view';
import { createStore, type StoreApi } from 'zustand';
import Director from './Director';
import { DIRECTOR } from './door';
import { fakeDoor, review, usage } from './fakes';
import { TOOL_NAMES } from './tools';

const sdk = vi.hoisted(() => ({
  options: {} as Record<string, never>,
  start: vi.fn(), end: vi.fn(), update: vi.fn(),
}));

vi.mock('@elevenlabs/react', () => ({
  ConversationProvider: ({children}: {children: unknown}) => children,
  useConversation: (options: Record<string, never>) => {
    sdk.options = options;
    return {status: 'connected', isSpeaking: false, startSession: sdk.start, endSession: sdk.end, sendContextualUpdate: sdk.update};
  },
}));

vi.mock('../audio', async () => {
  const {createStore, useStore} = await import('zustand');
  const store = createStore(() => ({playing: false, nodeId: null as string | null, error: null as string | null}));
  return {
    player: {store, play: vi.fn(async (id: string) => {store.setState({nodeId: id, playing: true});})},
    usePlayer: (select: (state: {playing: boolean}) => unknown) => useStore(store, select as never),
  };
});

type Tools = Record<string, (parameters: object) => Promise<string>>;

const paid = {action: 'voice-design', prompt: 'A gruff dwarf blacksmith', spending_limit_usd: 0.5,
  preview_line: 'Steel remembers every strike, and so do I. Bring me the ore before the fire dies, and mind the anvil.',
  engines: [{id: 'audio:voice-design:elevenlabs-voice-design', count: 1}]};

beforeEach(() => {
  sdk.start.mockReset();
  sdk.end.mockReset();
  sdk.update.mockReset();
  player.store.setState({playing: false, nodeId: null, error: null});
  vi.mocked(player.play).mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

async function opened(door = fakeDoor(), onClose = vi.fn()) {
  const view = render(<Director projectId="p1" door={door} onClose={onClose}/>);
  await screen.findByText(/left of/);
  return {door, view, onClose};
}

async function started(door = fakeDoor(), onClose = vi.fn()) {
  const {view} = await opened(door, onClose);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: /Start · up to/}));});
  await waitFor(() => expect(sdk.start).toHaveBeenCalled());
  return {door, view, onClose, tools: sdk.start.mock.calls[0][0].clientTools as Tools};
}

const connect = () => act(() => {(sdk.options.onConnect as unknown as (props: {conversationId: string}) => void)({conversationId: 'conv-1'});});

test('before a session the panel says where the audio goes and what a session may cost', async () => {
  await opened();
  expect(screen.getByText(/your microphone audio goes to ElevenLabs/)).toBeInTheDocument();
  expect(screen.getByText(/28:00 left of 30:00 · about \$0\.08 a minute, estimate/)).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Start · up to $0.40'})).toBeEnabled();
});

test('a session cannot start until the budget allows one, and the panel says why', async () => {
  await opened(fakeDoor({usage: async () => usage({ready: false, cap_seconds: 0, left_seconds: 0,
    reasons: ['The voice minutes budget is zero. Set CREXIL_VOICE_MINUTES_CAP in .env, then restart.']})}));
  expect(screen.getByText(/The voice minutes budget is zero/)).toBeInTheDocument();
  expect(screen.getByRole('button', {name: /Start · up to/})).toBeDisabled();
});

test('starting asks the server for a token and hands it only to the SDK, with the six tools', async () => {
  const {door} = await started();
  expect(door.start).toHaveBeenCalledWith('p1', HUMAN);
  const options = sdk.start.mock.calls[0][0];
  expect(options).toMatchObject({conversationToken: 'conversation-token-secret', connectionType: 'webrtc'});
  expect(Object.keys(options.clientTools).sort()).toEqual([...TOOL_NAMES].sort());
  expect(document.body.textContent).not.toContain('conversation-token-secret');
  expect(screen.getByRole('button', {name: 'End session'})).toBeInTheDocument();
});

test('a paid run waits for the artist, then is submitted under the artist’s name', async () => {
  const {door, tools} = await started();
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  expect(within(card).getByText(/Cap \$0\.50 · reserves up to \$0\.03 · by the published price/)).toBeInTheDocument();
  expect(door.submit).not.toHaveBeenCalled();
  const run = within(card).getByRole('button', {name: 'Run · up to $0.03'});
  expect(run).not.toHaveFocus();
  await act(async () => {fireEvent.click(run);});
  await waitFor(() => expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', HUMAN));
  expect(sdk.update).toHaveBeenCalledWith(expect.stringContaining('The artist ran'));
  expect(await within(card).findByText('Queued 2 attempts.')).toBeInTheDocument();
});

test('a free local run starts on the agent’s request, as the director', async () => {
  const {door, tools} = await started(fakeDoor({review: async () => review({reserved_usd: 0, maximum_usd: 0})}));
  await act(async () => {
    await tools.prepare_run({...paid, spending_limit_usd: 0, engines: [{id: 'audio:voice-design:qwen3-tts', count: 2}]});
  });
  expect(door.submit).toHaveBeenCalledWith('run-new', 1, 'token-1', DIRECTOR);
  expect(await screen.findByText(/Started for you · free · local engines/)).toBeInTheDocument();
});

test('a price that moved since the director prepared the run is shown again and not submitted', async () => {
  let reviews = 0;
  const door = fakeDoor({review: async () => ++reviews === 1 ? review() : review({token: 'token-2', reserved_usd: 0.05})});
  const {tools} = await started(door);
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: 'Run · up to $0.03'}));});
  expect(await within(card).findByText(/changed since the director prepared it/)).toBeInTheDocument();
  expect(within(card).getByRole('button', {name: 'Run · up to $0.05'})).toBeInTheDocument();
  expect(door.submit).not.toHaveBeenCalled();
});

test('spending that moved at submit is reviewed again and waits for another click', async () => {
  let reviews = 0;
  const door = fakeDoor({
    review: async () => ++reviews < 3 ? review() : review({token: 'token-3', reserved_usd: 0.04}),
    submit: async () => ({ok: false, status: 409, conflict: 'spending', message: 'Spending moved since review.'}),
  });
  const {tools} = await started(door);
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: 'Run · up to $0.03'}));});
  expect(await within(card).findByText(/Spending moved since review. The amount shown is current/)).toBeInTheDocument();
  expect(within(card).getByRole('button', {name: 'Run · up to $0.04'})).toBeInTheDocument();
  expect(door.submit).toHaveBeenCalledTimes(1);
});

test('discarding a prepared run keeps its draft and tells the agent', async () => {
  const {tools} = await started();
  await act(async () => {await tools.prepare_run(paid);});
  const card = await screen.findByRole('article', {name: /Prepared run/});
  fireEvent.click(within(card).getByRole('button', {name: 'Discard'}));
  expect(within(card).getByText('Discarded. The draft stays in the project.')).toBeInTheDocument();
  expect(sdk.update).toHaveBeenCalledWith(expect.stringContaining('discarded the prepared run'));
});

test('a proposal is picked under the artist’s name, keeping the takes already picked', async () => {
  const {door, tools} = await started();
  await act(async () => {await tools.propose_pick({run_id: 'run-2', takes: ['2B'], reason: 'Warmest.'});});
  expect(door.propose).toHaveBeenCalledWith('p1', ['take-2b'], 'Warmest.');
  const card = await screen.findByRole('article', {name: 'Proposal: 2B'});
  fireEvent.change(within(card).getByLabelText('Reason'), {target: {value: 'Warmest, with grit.'}});
  await act(async () => {fireEvent.click(within(card).getByRole('button', {name: 'Pick 2B'}));});
  await waitFor(() => expect(door.pick).toHaveBeenCalledWith('run-2', 4, ['take-2a', 'take-2b'], 'Warmest, with grit.', HUMAN));
  expect(sdk.update).toHaveBeenCalledWith('The artist picked 2B.');
});

test('a playing take mutes the microphone and turns the agent down, a moment past its end', async () => {
  const {tools} = await started();
  expect(sdk.options.micMuted).toBe(false);
  await act(async () => {await tools.play_take({run_id: 'run-2', take: '2B'});});
  expect(player.play).toHaveBeenCalledWith('take-2b', {duration: 3.46});
  expect(sdk.options.micMuted).toBe(true);
  expect(sdk.options.volume).toBe(0.2);
  act(() => {player.store.setState({playing: false});});
  expect(sdk.options.micMuted).toBe(true);
  expect(sdk.options.volume).toBe(1);
  await waitFor(() => expect(sdk.options.micMuted).toBe(false));
});

test('the session ends at its limit and records the minutes observed', async () => {
  const {door} = await started();
  vi.useFakeTimers();
  connect();
  await act(async () => {vi.advanceTimersByTime(300_000);});
  expect(sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalledWith('voice-1', 300, 'conv-1', HUMAN);
  expect(screen.getByText('The session reached its limit and ended.')).toBeInTheDocument();
});

test('ending a session records the minutes since it connected', async () => {
  const {door} = await started();
  vi.useFakeTimers();
  connect();
  await act(async () => {vi.advanceTimersByTime(30_000);});
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End session'}));});
  expect(sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalledWith('voice-1', 30, 'conv-1', HUMAN);
});

test('minutes that could not be recorded stay offered for recording', async () => {
  let ends = 0;
  const door = fakeDoor({end: async () => {if (++ends === 1) throw new Error('The server could not be reached.'); return {} as never;}});
  await started(door);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End session'}));});
  expect(await screen.findByRole('alert')).toHaveTextContent(/minutes were not recorded/);
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: "Record the last session's minutes"}));});
  expect(door.end).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('button', {name: /Start · up to/})).toBeInTheDocument();
});

test('closing the panel ends a live session first', async () => {
  const {door, onClose} = await started();
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End and close'}));});
  expect(sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test('a panel that goes away ends its live session and records it', async () => {
  const {door, view} = await started();
  await act(async () => {view.unmount();});
  expect(sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalled();
});

test('each tool the director uses is shown in the conversation', async () => {
  const {tools} = await started();
  await act(async () => {await tools.list_engines({payload: 'audio'});});
  expect(screen.getByRole('log', {name: 'Conversation'})).toHaveTextContent('Director used list engines');
});

test('while the draft drawer is open the panel keeps above it', async () => {
  const room = createStore(() => ({drawer: 'draft-1' as string | null}));
  render(<ViewContext.Provider value={room as unknown as StoreApi<never>}>
    <Director projectId="p1" door={fakeDoor()} onClose={vi.fn()}/>
  </ViewContext.Provider>);
  const panel = await screen.findByRole('complementary', {name: 'Voice director'});
  expect(panel).toHaveAttribute('data-placement', 'above-drawer');
  act(() => {room.setState({drawer: null});});
  expect(panel).toHaveAttribute('data-placement', 'column');
});
