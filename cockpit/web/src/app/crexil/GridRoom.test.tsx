// The grid room's keys and working line against a stubbed server: S selects the focused take
// through the shared selection action and keeps an optional reason as that choice's own
// reason; a blind run's reveal is asked for in line with an optional reason, once the
// choice's reason is settled, and is announced in the line's one status. Fetch is stubbed; the client
// resolves globalThis.fetch lazily (api/client.ts).
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { player } from '../audio/player';
import type { Material, Snapshot } from './api';
import { RoomLine, useFastPick, useRoomAudition } from './GridRoom';
import { useEscapeBase, useEscapeKey } from './keys';
import { createViewStore, ViewContext } from './view';

const take = (id: string, slot_pos: number, output_index = 0) => ({id, payload: 'audio', available: true, turn_id: 't1',
  turn_idx: 0, slot_pos, output_index, identity_hidden: true, audio: {duration_s: 4.4, peaks: [0.4]}}) as unknown as Material;
const takes = [take('a1', 0), take('a2', 0, 1), take('b1', 1)];
const snapshot = (selection: string[] = [], revealed = false) => ({
  project: {id: 'p1'}, nodes: takes, execution_groups: [],
  directions: [{id: 'd1', revision: 4, selection, node_ids: takes.map(item => item.id), turn_ids: ['t1']}],
  turns: [{id: 't1', idx: 0, slots: [{id: 's1', status: 'landed'}, {id: 's2', status: 'landed'}]}],
  blind_runs: [{turn_id: 't1', revealed, revealed_by: revealed ? {kind: 'human', name: 'Kay'} : null}],
}) as unknown as Snapshot;

type Sent = {method: string; path: string; body: Record<string, unknown>};
let sent: Sent[] = [];
let answers: Record<string, () => Response> = {};

beforeEach(() => {
  sent = [];
  answers = {};
  vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    sent.push({method: input.method, path, body: input.method === 'GET' ? {} : await input.json()});
    return answers[`${input.method} ${path}`]?.() ?? Response.json({});
  }));
});
afterEach(() => {
  cleanup();
  player.release();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function Room({data, refresh = () => {}}: {data: Snapshot; refresh?: () => void}) {
  useEscapeKey();
  const fastPick = useFastPick(data, takes, refresh);
  return <><RoomLine data={data} turns={data.turns} takes={takes} fastPick={fastPick} refresh={refresh}/>
    {takes.map(item => <button key={item.id} data-take={item.id} data-audio-face="">Act on {item.id}</button>)}</>;
}
const mount = (data: Snapshot, refresh?: () => void) =>
  render(<ViewContext.Provider value={createViewStore({room: 0})}><Room data={data} refresh={refresh}/></ViewContext.Provider>);
const press = (key: string) => fireEvent.keyDown(document.activeElement ?? document.body, {key, code: `Key${key.toUpperCase()}`});

describe('S selects the focused take (the row is the choice)', () => {
  it('adds it to its direction through the selection action, then keeps the reason as an assessment', async () => {
    const refresh = vi.fn();
    mount(snapshot(['b1']), refresh);
    screen.getByRole('button', {name: 'Act on a2'}).focus();
    press('s');
    const reason = await screen.findByRole('textbox', {name: 'Why 0A.2? Optional'});
    expect(sent[0]).toMatchObject({method: 'PUT', path: '/api/crexil/directions/d1/selection',
      body: {revision: 4, node_ids: ['b1', 'a2'], reason: '', actor: {kind: 'human', name: 'Artist'}}});
    expect(refresh).toHaveBeenCalled();
    expect(reason).toHaveFocus();
    expect(screen.getByText('Selected 0A.2')).toBeInTheDocument();
    fireEvent.change(reason, {target: {value: 'Warmest of the six.'}});
    fireEvent.submit(reason.closest('form')!);
    await screen.findByText('Reason kept with 0A.2.');
    // with no choice named by the answer or the record, the reason is kept as an assessment of the take
    expect(sent.find(item => item.method === 'POST')).toMatchObject({path: '/api/crexil/projects/p1/assessments',
      body: {reason: 'Warmest of the six.', node_ids: ['a2'], direction_id: 'd1', turn_id: 't1'}});
    await waitFor(() => expect(screen.getByRole('button', {name: 'Act on a2'})).toHaveFocus());
  });
  it('takes a selected take off the section, and records nothing more when the reason is skipped', async () => {
    mount(snapshot(['a1', 'b1']));
    screen.getByRole('button', {name: 'Act on a1'}).focus();
    press('s');
    const reason = await screen.findByRole('textbox', {name: 'Why 0A? Optional'});
    expect(sent[0].body.node_ids).toEqual(['b1']);
    expect(screen.getByText('Removed 0A')).toBeInTheDocument();
    fireEvent.keyDown(reason, {key: 'Escape', code: 'Escape'});
    await waitFor(() => expect(screen.queryByRole('textbox', {name: 'Why 0A? Optional'})).toBeNull());
    expect(sent.filter(item => item.method !== 'GET')).toHaveLength(1);
  });
  it("keeps the reason with the choice itself, as a revision of the choice the answer names, and refreshes (law 23)", async () => {
    answers['PUT /api/crexil/directions/d1/selection'] = () => Response.json({id: 'd1', latest_decision: {event_id: 'event-9'}});
    const refresh = vi.fn();
    mount(snapshot(), refresh);
    screen.getByRole('button', {name: 'Act on b1'}).focus();
    press('s');
    const reason = await screen.findByRole('textbox', {name: 'Why 0B? Optional'});
    fireEvent.change(reason, {target: {value: 'Brightest of the three.'}});
    fireEvent.submit(reason.closest('form')!);
    await screen.findByText('Reason kept with 0B.');
    expect(sent[1]).toMatchObject({method: 'POST', path: '/api/crexil/projects/p1/assessments',
      body: {reason: 'Brightest of the three.', previous_id: 'event-9', actor: {kind: 'human', name: 'Artist'}}});
    expect(sent[1].body).not.toHaveProperty('node_ids');
    expect(refresh).toHaveBeenCalledTimes(2);
  });
  it('finds the choice in the record when the selection answer does not name it', async () => {
    // the record lists the direction's decisions newest first
    answers['GET /api/crexil/projects/p1/record'] = () => Response.json({decisions: [
      {event_id: 'assessed', kind: 'assessment', node_ids: ['a1'], revised_by: null},
      {event_id: 'mine', kind: 'selection', node_ids: ['b1', 'a1'], revised_by: null},
      {event_id: 'older', kind: 'selection', node_ids: ['b1'], revised_by: null}]});
    mount(snapshot(['b1']));
    screen.getByRole('button', {name: 'Act on a1'}).focus();
    press('s');
    const reason = await screen.findByRole('textbox', {name: 'Why 0A? Optional'});
    expect(sent[1]).toMatchObject({method: 'GET', path: '/api/crexil/projects/p1/record'});
    fireEvent.change(reason, {target: {value: 'Brightest.'}});
    fireEvent.submit(reason.closest('form')!);
    await screen.findByText('Reason kept with 0A.');
    expect(sent.find(item => item.method === 'POST')?.body).toMatchObject({reason: 'Brightest.', previous_id: 'mine'});
  });
  it('holds Escape while the choice is on its way, so the room stays open for its reason', async () => {
    let answer!: () => void;
    answers['PUT /api/crexil/directions/d1/selection'] = () => Response.json({});
    const room = createViewStore({room: 0});
    vi.stubGlobal('fetch', vi.fn(async (input: Request) => {
      sent.push({method: input.method, path: new URL(input.url).pathname, body: await input.json()});
      await new Promise<void>(done => {answer = done;});
      return Response.json({});
    }));
    function Base() {useEscapeBase(() => {room.getState().closeTop();}); return null;}
    render(<ViewContext.Provider value={room}><Base/><Room data={snapshot()}/></ViewContext.Provider>);
    screen.getByRole('button', {name: 'Act on a1'}).focus();
    press('s');
    await waitFor(() => expect(sent).toHaveLength(1));
    fireEvent.keyDown(document.body, {key: 'Escape', code: 'Escape'});
    expect(room.getState().room).toBe(0);
    act(() => answer());
    expect(await screen.findByRole('textbox', {name: 'Why 0A? Optional'})).toBeInTheDocument();
  });
  it('acts on nothing when no take of the room is focused or acted on', () => {
    mount(snapshot());
    press('s');
    expect(sent).toEqual([]);
  });
  it("says the server's words when the selection is refused", async () => {
    answers['PUT /api/crexil/directions/d1/selection'] = () =>
      Response.json({detail: 'This direction changed in another view. Reload it before applying your changes.'}, {status: 409});
    mount(snapshot());
    screen.getByRole('button', {name: 'Act on b1'}).focus();
    press('s');
    expect(await screen.findByText('This direction changed in another view. Reload it before applying your changes.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', {name: /Why/})).toBeNull();
  });
});

describe('the reveal follows the choice (law 23, never a dialog)', () => {
  it('offers to reveal without choosing before anything of the run is chosen', async () => {
    mount(snapshot());
    const run = screen.getByRole('group', {name: 'Blind run 0'});
    expect(run).toHaveTextContent('Engines hidden. Select with S; the reveal follows your choice.');
    fireEvent.click(screen.getByRole('button', {name: 'Reveal without choosing'}));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({method: 'POST', path: '/api/crexil/turns/t1/reveal', body: {reason: ''}});
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('asks for an optional reason once a take is chosen, and sends it with the reveal', async () => {
    const refresh = vi.fn();
    mount(snapshot(['b1']), refresh);
    fireEvent.change(screen.getByRole('textbox', {name: 'Reason for revealing run 0, optional'}), {target: {value: 'Chosen blind.'}});
    fireEvent.click(screen.getByRole('button', {name: 'Reveal engines'}));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(sent[0]).toMatchObject({path: '/api/crexil/turns/t1/reveal', body: {reason: 'Chosen blind.', actor: {kind: 'human'}}});
  });
  it("announces the reveal in the line's one status and moves focus there", async () => {
    answers['POST /api/crexil/turns/t1/reveal'] = () => Response.json({turn_id: 't1', revealed: true, revealed_by: {kind: 'human', name: 'Kay'}});
    mount(snapshot(['b1']));
    fireEvent.click(screen.getByRole('button', {name: 'Reveal engines'}));
    const status = screen.getByRole('status');
    await waitFor(() => expect(status).toHaveTextContent('Run 0 revealed by Kay. Every take now names its engine and price.'));
    await waitFor(() => expect(status).toHaveFocus());
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });
  it("offers the reveal only after the choice's own reason is settled: one Why at a time", async () => {
    mount(snapshot(['b1']));
    screen.getByRole('button', {name: 'Act on a1'}).focus();
    press('s');
    await screen.findByRole('textbox', {name: 'Why 0A? Optional'});
    expect(screen.queryByRole('textbox', {name: /revealing/})).toBeNull();
    expect(screen.getByRole('group', {name: 'Blind run 0'})).toHaveTextContent('Blind · Run 0');
    fireEvent.keyDown(screen.getByRole('textbox', {name: 'Why 0A? Optional'}), {key: 'Escape', code: 'Escape'});
    expect(await screen.findByRole('textbox', {name: 'Reason for revealing run 0, optional'})).toBeInTheDocument();
  });
  it('keeps the same request for a retry after an unconfirmed reveal', async () => {
    let fail = true;
    answers['POST /api/crexil/turns/t1/reveal'] = () => fail ? Response.json({detail: 'The server could not be reached.'}, {status: 503}) : Response.json({});
    mount(snapshot(['b1']));
    fireEvent.click(screen.getByRole('button', {name: 'Reveal engines'}));
    await screen.findByText('The server could not be reached.');
    fail = false;
    fireEvent.click(screen.getByRole('button', {name: 'Reveal engines'}));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1].body.request_id).toBe(sent[0].body.request_id);
  });
  it('names who revealed a run once it is revealed', () => {
    mount(snapshot(['b1'], true));
    expect(screen.queryByRole('group', {name: 'Blind run 0'})).toBeNull();
    expect(screen.getByText('Run 0 revealed by Kay')).toBeInTheDocument();
  });
});

describe('the working line', () => {
  it('hints the audition keys and says politely which take plays', async () => {
    mount(snapshot());
    expect(screen.getByText('Space play · 1–9 audition · X A/B · S select')).toBeInTheDocument();
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      this.dispatchEvent(new Event('play'));
      return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    await act(() => player.play('b1'));
    expect(screen.getByRole('status')).toHaveTextContent('Playing 0B · 4.4 s · as recorded');
  });
});

describe('audition in the grid room', () => {
  function Audition({room}: {room: ReturnType<typeof createViewStore>}) {
    return <ViewContext.Provider value={room}><Keys/></ViewContext.Provider>;
  }
  function Keys() {
    useRoomAudition(takes);
    return <>{takes.map(item => <button key={item.id} data-take={item.id} data-audio-face="">Act on {item.id}</button>)}</>;
  }
  const digit = (n: number) => fireEvent.keyDown(document.activeElement ?? document.body, {key: String(n), code: `Digit${n}`});
  let played: string[] = [];
  beforeEach(() => {
    played = [];
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      played.push(decodeURIComponent(this.src.split('/material/')[1].split('/')[0]));
      this.dispatchEvent(new Event('play'));
      return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  });
  it('plays position N with digit N and moves focus to its face, so S and Space act on the take just heard', async () => {
    render(<Audition room={createViewStore({room: 0})}/>);
    digit(2);
    await act(() => Promise.resolve());
    expect(played).toEqual(['a2']);
    expect(screen.getByRole('button', {name: 'Act on a2'})).toHaveFocus();
  });
  it('rests every key while anything covers the room, the related outputs and project edit included', async () => {
    for (const overlays of [[{kind: 'group', id: 'g'}], [{kind: 'projectEdit'}]] as const) {
      const view = render(<Audition room={createViewStore({room: 0, overlays: [...overlays]})}/>);
      digit(1);
      fireEvent.keyDown(document.body, {key: ' ', code: 'Space'});
      await act(() => Promise.resolve());
      view.unmount();
    }
    expect(played).toEqual([]);
  });
  it('rests the digits, never Space or X, while two takes are marked for A/B and the verb card is open', async () => {
    const room = createViewStore({room: 0, acting: ['a1', 'b1']});
    render(<Audition room={room}/>);
    digit(3);
    await act(() => Promise.resolve());
    expect(played).toEqual([]);
    fireEvent.keyDown(document.body, {key: 'x', code: 'KeyX'});
    await act(() => Promise.resolve());
    expect(played).toEqual(['a1']);
    fireEvent.keyDown(document.body, {key: 'x', code: 'KeyX'});
    await act(() => Promise.resolve());
    expect(played).toEqual(['a1', 'b1']);
  });
});
