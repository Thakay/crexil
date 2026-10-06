// The door on the project surface opens the panel, whose code loads only when asked.
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { fakeDoor } from './fakes';
import { VoiceDirectorPanel } from './VoiceDirectorPanel';

vi.mock('@elevenlabs/react', () => ({
  ConversationProvider: ({children}: {children: unknown}) => children,
  useConversation: () => ({status: 'disconnected', isSpeaking: false, startSession: vi.fn(), endSession: vi.fn(),
    sendContextualUpdate: vi.fn()}),
}));

afterEach(cleanup);

test('the door opens the panel beside the work, and the panel closes back to the door', async () => {
  render(<VoiceDirectorPanel projectId="p1" door={fakeDoor()}/>);
  const door = screen.getByRole('button', {name: 'Voice director ›'});
  expect(door).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(door);
  expect(await screen.findByRole('heading', {name: 'Voice director'})).toBeInTheDocument();
  expect(screen.getByRole('complementary', {name: 'Voice director'})).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: 'Close'}));
  expect(screen.getByRole('button', {name: 'Voice director ›'})).toBeInTheDocument();
});
