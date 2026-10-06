// The operation library follows the address bar even when a hash is set and
// reverted before any hashchange event is handled. Fetch is stubbed; the client
// resolves globalThis.fetch lazily for exactly this (api/client.ts).
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { useHashRoute } from '../routes';
import { OperationLibrary } from './OperationLibrary';

const registration = (revision: number, description: string) => ({
  id: 'r1', name: 'Resize study', revision, actor: {kind: 'human', name: 'Artist'}, adaptation_note: '',
  created_at: '2026-10-05T00:00:00Z', updated_at: '2026-10-05T00:00:00Z',
  binding: {cost_note: '', cost_review: 'unknown', dependency_note: '', downloads: 'unknown', runtime: 'comfy'},
  definition: {description, requirements: '', graph: {}, actions: ['custom'], inputs: [], sources: [], outputs: []},
  evidence: [], gaps: [], inspection: null, operation_revision: 'op-1', revisions: [], source: {},
  source_kind: 'api', source_name: 'resize.json', source_sha256: 'abc', source_text: '{}',
});

// The page subscribes to the route the way the app does; the library must not rely on it.
function Page() {
  useHashRoute();
  return <OperationLibrary/>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  location.hash = '';
});

test('a registration opened and left within one tick still saves and closes', async () => {
  let current = registration(1, 'First description');
  const patches: {definition: {description: string}}[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    if (path === '/api/crexil/projects' || path === '/api/crexil/operations') return Response.json([]);
    if (path === '/api/crexil/registrations' && input.method === 'GET') return Response.json([current]);
    if (path === '/api/crexil/registrations/r1' && input.method === 'PATCH') {
      const body = await input.json();
      patches.push(body);
      current = registration(current.revision + 1, body.definition.description);
      return Response.json(current);
    }
    throw new Error(`unstubbed fetch: ${input.method} ${path}`);
  }));
  location.hash = '#/operations';
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><Page/></QueryClientProvider>);
  const open = await screen.findByRole('button', {name: /Resize study/});

  // Open the registration and edit it; then go back before any hashchange is handled.
  act(() => {fireEvent.click(open);});
  act(() => {fireEvent.change(screen.getByLabelText('Description'), {target: {value: 'Saved while going back'}});});
  location.hash = '#/operations';

  await waitFor(() => expect(screen.queryByLabelText('Description')).toBeNull());
  expect(patches.map(body => body.definition.description)).toEqual(['Saved while going back']);
});
