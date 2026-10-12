// The voice director's door and column on the project surface: the column's code loads only
// when asked, the column claims its width as the surfaces' inset while it is open, focus moves
// in and back out, and Escape inside the column is the column's own, never the room's behind.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useEscapeBase, useEscapeKey, useEscapeLayer } from '../crexil/keys';
import { createViewStore, ViewContext } from '../crexil/view';
import { fakeDoor } from './fakes';
import { COLUMN_PX, INSET, VoiceDirectorPanel } from './VoiceDirectorPanel';

const sdk = vi.hoisted(() => ({start: vi.fn(), end: vi.fn()}));

vi.mock('@elevenlabs/react', () => ({
  ConversationProvider: ({children}: {children: unknown}) => children,
  useConversation: () => ({status: 'connecting', isSpeaking: false, startSession: sdk.start, endSession: sdk.end,
    sendContextualUpdate: vi.fn(), setMuted: vi.fn(), setVolume: vi.fn()}),
}));

/** The project room's own Escape, beside the column: it closes the room's top layer. */
function Room({base, children}: {base: () => void; children: React.ReactNode}) {
  useEscapeKey();
  useEscapeBase(base);
  return <>{children}</>;
}

/** A layer that opens after the column, such as the draft drawer, and claims every Escape. */
function Drawer({handle}: {handle: () => boolean}) {
  useEscapeLayer(handle);
  return null;
}

const inset = () => document.documentElement.style.getPropertyValue(INSET);
const press = (key = 'Escape') => fireEvent.keyDown(document.activeElement ?? document.body, {key, code: key});
const openDoor = async () => {
  fireEvent.click(screen.getByRole('button', {name: 'Voice director ›'}));
  return screen.findByRole('complementary', {name: 'Voice director'});
};
const startSession = async () => {
  await act(async () => {fireEvent.click(await screen.findByRole('button', {name: /Start · up to/}));});
  await screen.findByRole('button', {name: 'End and close'});
};

beforeEach(() => {
  sdk.start.mockClear();
  sdk.end.mockClear();
  // The log watches its own size; this page lays nothing out.
  vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('the door opens a column beside the work, focus moves in, and closing returns it to the door', async () => {
  render(<VoiceDirectorPanel projectId="p1" door={fakeDoor()}/>);
  const door = screen.getByRole('button', {name: 'Voice director ›'});
  expect(door).toHaveAttribute('aria-expanded', 'false');
  const column = await openDoor();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByRole('heading', {name: 'Voice director'})).toHaveFocus();
  expect(await screen.findByText(/left of/)).toBeInTheDocument();
  expect(column).toHaveStyle({width: `${COLUMN_PX}px`});
  fireEvent.click(screen.getByRole('button', {name: 'Esc · Close'}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Voice director ›'})).toHaveFocus());
});

test('while open, the column sets the inset to its width on the root element, and removes it on close', async () => {
  const view = render(<VoiceDirectorPanel projectId="p1" door={fakeDoor()}/>);
  expect(inset()).toBe('');
  await openDoor();
  expect(inset()).toBe(`${COLUMN_PX}px`);
  fireEvent.click(screen.getByRole('button', {name: 'Esc · Close'}));
  expect(inset()).toBe('');
  await openDoor();
  view.unmount();
  expect(inset()).toBe('');
});

test('Escape inside an idle column closes it and returns focus to the door, and never reaches the room', async () => {
  const room = vi.fn(), drawer = vi.fn(() => true);
  const view = render(<Room base={room}><VoiceDirectorPanel projectId="p1" door={fakeDoor()}/></Room>);
  await openDoor();
  // A layer opened after the column claims every Escape that reaches the room, and still hears none from the column.
  view.rerender(<Room base={room}><VoiceDirectorPanel projectId="p1" door={fakeDoor()}/><Drawer handle={drawer}/></Room>);
  (await screen.findByRole('button', {name: /Start · up to/})).focus();
  press();
  expect(screen.queryByRole('complementary', {name: 'Voice director'})).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', {name: 'Voice director ›'})).toHaveFocus());
  expect(room).not.toHaveBeenCalled();
  expect(drawer).not.toHaveBeenCalled();
  // Outside the column, Escape is the room's again.
  press();
  expect(drawer).toHaveBeenCalledTimes(1);
});

test('Escape inside the column during a session returns focus to the work and keeps the session', async () => {
  const room = vi.fn();
  render(<Room base={room}><button type="button">Act on 0A</button><VoiceDirectorPanel projectId="p1" door={fakeDoor()}/></Room>);
  await openDoor();
  await startSession();
  expect(screen.getByText('Esc · Back to the work')).toBeInTheDocument();
  const take = screen.getByRole('button', {name: 'Act on 0A'});
  take.focus();
  screen.getByRole('button', {name: 'Mute microphone'}).focus();
  press();
  expect(take).toHaveFocus();
  expect(screen.getByRole('complementary', {name: 'Voice director'})).toBeInTheDocument();
  expect(sdk.end).not.toHaveBeenCalled();
  expect(room).not.toHaveBeenCalled();
});

test('closing the column during a session ends the session first, and records it', async () => {
  const door = fakeDoor();
  render(<VoiceDirectorPanel projectId="p1" door={door}/>);
  await openDoor();
  await startSession();
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'End and close'}));});
  expect(sdk.end).toHaveBeenCalled();
  expect(door.end).toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Voice director ›'})).toBeInTheDocument();
});

test('focus stays in the column when the control that held it goes away', async () => {
  render(<VoiceDirectorPanel projectId="p1" door={fakeDoor()}/>);
  await openDoor();
  const start = await screen.findByRole('button', {name: /Start · up to/});
  start.focus();
  await act(async () => {fireEvent.click(start);});
  await screen.findByRole('button', {name: 'End and close'});
  expect(start.isConnected).toBe(false);
  await waitFor(() => expect(screen.getByRole('heading', {name: 'Voice director'})).toHaveFocus());
});

test('closing returns focus to the door, whatever opened over the Filmstrip meanwhile', async () => {
  const store = createViewStore();
  render(<ViewContext.Provider value={store}><button type="button">Act on 0A</button>
    <VoiceDirectorPanel projectId="p1" door={fakeDoor()}/></ViewContext.Provider>);
  await openDoor();
  act(() => store.getState().openGrid(0));
  (await screen.findByRole('button', {name: /Start · up to/})).focus();
  press();
  expect(screen.queryByRole('complementary', {name: 'Voice director'})).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', {name: 'Voice director ›'})).toHaveFocus());
});

test('the door stays in its bar whatever is open over the Filmstrip, since no room puts its Esc there', () => {
  const store = createViewStore();
  render(<ViewContext.Provider value={store}><VoiceDirectorPanel projectId="p1" door={fakeDoor()}/></ViewContext.Provider>);
  for (const open of [() => store.getState().inspect('n1'), () => store.getState().openGrid(0), () => store.setState({drawer: 'd1'})]) {
    act(open);
    expect(screen.getByRole('button', {name: 'Voice director ›'})).toBeInTheDocument();
  }
});

test('the door closes an idle column, and brings focus into a column in session, whose close would end it', async () => {
  render(<VoiceDirectorPanel projectId="p1" door={fakeDoor()} doorClassName="bar-door"/>);
  const door = screen.getByRole('button', {name: 'Voice director ›'});
  expect(door).toHaveClass('bar-door');
  await openDoor();
  expect(door).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(door);
  expect(screen.queryByRole('complementary', {name: 'Voice director'})).toBeNull();
  expect(door).toHaveAttribute('aria-expanded', 'false');
  await openDoor();
  await startSession();
  door.focus();
  fireEvent.click(door);
  expect(screen.getByRole('complementary', {name: 'Voice director'})).toBeInTheDocument();
  expect(screen.getByRole('heading', {name: 'Voice director'})).toHaveFocus();
  expect(sdk.end).not.toHaveBeenCalled();
});
