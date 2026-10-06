// The grid room's keyboard and its working line, the second line of its head. Digits play
// the room's take positions, Space the focused take and X the two marked takes, at matched
// levels; playing is inspection, never a verb (law 11). S selects the focused take: it
// toggles the take in its direction's displayed selection through the shared selection
// action ("the row is the choice"), and the reason asked for next becomes that choice's
// own reason, a revision of the choice it was given with, since a pick carries a reason
// (law 23). A blind run's reveal is asked for in line, never in a dialog (law 2), is an
// authored event and follows the artist's choice, once its reason is settled. The line
// says out loud that local work takes turns on the one GPU (law 13). It is one line high,
// so nothing it says moves a cell (law 8), and it holds one status that every message
// shares, the reveal included.
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchClient } from '../../api/client';
import { isAudible, useAudition, usePlayer } from '../audio';
import { command, isDefiniteRefusal, label, message, result, type Material, type Snapshot } from './api';
import { keyHint, pairOf, playingLine } from './audition';
import { hiddenRuns, inFlight } from './blind';
import { engineLane } from './catalogue';
import { useCommandKey, useEscapeLayer } from './keys';
import { covered, useViewStore } from './view';
import s from './Filmstrip.module.css';

type Turn = Snapshot['turns'][number];

/** The take the keyboard is on: the face that holds focus. */
export function focusedTake(): string | null {
  const face = document.activeElement instanceof Element ? document.activeElement.closest('[data-take]') : null;
  return face?.getAttribute('data-take') ?? null;
}

/** Moves focus to a take's face, so the next key acts on the take just heard. */
export function focusTake(id: string) {
  document.querySelector<HTMLElement>(`[data-take=${JSON.stringify(id)}]`)?.focus();
}

/** Audition over the room's take positions. Every key rests while anything covers the room;
 *  the digits also rest while the verb card is open, since 3 is its to-3D key. */
export function useRoomAudition(takes: readonly (Material | null)[]) {
  const store = useViewStore();
  useAudition({
    takes,
    ready: key => !covered(store.getState()) && (key !== 'digit' || store.getState().acting.length === 0),
    focused: focusedTake,
    pair: () => pairOf(store.getState().acting, takes),
    onPlay: take => focusTake(take.id),
  });
}

/** A take just selected or removed, waiting for its optional reason; `decision` is the
 *  choice the reason revises. */
export type FastPick = {node: Material; direction: string; chosen: boolean; decision: string | null};

/** The choice a fast pick just made, as the record names it: the selection answer's latest
 *  decision, else the newest display choice of the direction that holds the take where S
 *  put it (in the selection, or out of it). Null when neither names it. */
async function choiceMade(wid: string, direction: string, node: string, chosen: boolean,
  answer: {latest_decision?: {event_id: string} | null}): Promise<string | null> {
  if (answer.latest_decision?.event_id) return answer.latest_decision.event_id;
  try {
    const record = await result(fetchClient.GET('/api/crexil/projects/{wid}/record',
      {params: {path: {wid}, query: {direction_id: direction, limit: 10}}}));
    return record.decisions.find(item => item.kind === 'selection' && !item.revised_by && item.node_ids.includes(node) === chosen)?.event_id ?? null;
  } catch {
    return null;
  }
}

/** The S key: toggles the focused take, else the one take acted on, in its direction's
 *  displayed selection. Only a take shown in the open grid room can be selected. While the
 *  choice is on its way, Escape waits for it, so it never closes the room under the reason
 *  that follows. */
export function useFastPick(data: Snapshot | undefined, takes: readonly (Material | null)[], refresh: () => void) {
  const store = useViewStore();
  const [pick, setPick] = useState<FastPick | null>(null);
  const [said, setSaid] = useState('');
  const busy = useRef(false);
  useEscapeLayer(() => busy.current);
  const target = (): Material | null => {
    const {acting} = store.getState();
    const id = focusedTake() ?? (acting.length === 1 ? acting[0] : null);
    return takes.find(take => take !== null && take.id === id) ?? null;
  };
  const select = async (node: Material) => {
    const direction = data?.directions.find(item => item.node_ids.includes(node.id));
    if (!direction) return;
    const chosen = !direction.selection.includes(node.id);
    busy.current = true;
    setSaid('');
    try {
      const answer = await result(fetchClient.PUT('/api/crexil/directions/{did}/selection', {params: {path: {did: direction.id}},
        body: {...command(), revision: direction.revision, reason: '',
          node_ids: chosen ? [...direction.selection, node.id] : direction.selection.filter(id => id !== node.id)}}));
      refresh();
      setPick({node, direction: direction.id, chosen, decision: await choiceMade(data!.project.id, direction.id, node.id, chosen, answer)});
    } catch (error) {
      setPick(null);
      setSaid(message(error));
    } finally {
      busy.current = false;
    }
  };
  useCommandKey('s', () => {const node = target(); if (node) void select(node);},
    () => store.getState().room !== null && !covered(store.getState()) && !busy.current && target() !== null);
  /** The reason form closed: with a line to say, or none when it was skipped. */
  const settle = useCallback((line: string | null) => {setPick(null); setSaid(line ?? '');}, []);
  return {pick, said, settle, announce: setSaid};
}

/** The second line of the grid room's head. */
export function RoomLine({data, turns, takes, fastPick, refresh}: {
  data: Snapshot; turns: Turn[]; takes: readonly (Material | null)[];
  fastPick: ReturnType<typeof useFastPick>; refresh: () => void;
}) {
  const hidden = hiddenRuns(data);
  const blind = turns.filter(turn => hidden.has(turn.id));
  const revealed = turns.flatMap(turn => (data.blind_runs ?? []).filter(run => run.turn_id === turn.id && run.revealed)
    .map(run => ({turn, run})));
  const audible = takes.some(isAudible);
  const status = useRef<HTMLSpanElement>(null);
  const playing = usePlayer(state => state.playing ? state.nodeId : null);
  const {announce} = fastPick;
  // A take started from the keyboard says itself; what was said before gives way to it.
  useEffect(() => {if (playing) announce('');}, [playing, announce]);
  // Law 13, said while local work is in flight; a blind run's in-flight work may be local.
  const local = turns.some(turn => inFlight(turn) && (hidden.has(turn.id)
    || turn.slots.some(slot => ['queued', 'running'].includes(slot.status) && engineLane(slot.engine) !== 'hosted')));
  return <div className={s.roomLine}>
    <span className={s.keyHint}>{keyHint(audible)}</span>
    {fastPick.pick && <PickReason key={fastPick.pick.node.id} wid={data.project.id} pick={fastPick.pick} settle={fastPick.settle} refresh={refresh}/>}
    <span ref={status} tabIndex={-1} className={s.roomSaid} role="status">
      {fastPick.pick ? '' : fastPick.said || <Playing takes={takes}/>}</span>
    <span className={s.spacer}/>
    {local && <span className={s.keyHint}>Local · sequential, one GPU</span>}
    {blind.map(turn => <BlindRun key={turn.id} turn={turn} picked={pickedFrom(data, turn)} waiting={Boolean(fastPick.pick)} refresh={refresh}
      revealed={line => {announce(line); requestAnimationFrame(() => status.current?.focus());}}/>)}
    {revealed.map(({turn, run}) => <span key={turn.id} className={s.keyHint}>
      Run {turn.idx} revealed{run.revealed_by ? ` by ${run.revealed_by.name}` : ''}</span>)}
  </div>;
}

/** Whether the artist has chosen any take of this run for its section yet. */
function pickedFrom(data: Snapshot, turn: Turn): string[] {
  const direction = data.directions.find(item => item.turn_ids.includes(turn.id));
  if (!direction) return [];
  return direction.selection.filter(id => data.nodes.some(node => node.id === id && node.turn_id === turn.id));
}

/** The take in the player, said politely when a key starts or stops it. */
function Playing({takes}: {takes: readonly (Material | null)[]}) {
  const id = usePlayer(state => state.nodeId);
  const playing = usePlayer(state => state.playing);
  const matched = usePlayer(state => Boolean(state.nodeId && state.levels[state.nodeId]?.matched));
  const take = takes.find(item => item !== null && item.id === id) ?? null;
  return take ? playingLine(take, playing, matched) : null;
}

function PickReason({wid, pick, settle, refresh}: {wid: string; pick: FastPick; settle: (line: string | null) => void; refresh: () => void}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<ReturnType<typeof command> | null>(null);
  const name = label(pick.node);
  const close = (line: string | null) => {settle(line); requestAnimationFrame(() => focusTake(pick.node.id));};
  useEscapeLayer(() => {if (!busy) close(null); return true;});
  const keep = async () => {
    setBusy(true);
    setError('');
    pending.current ??= command();
    // The reason is the choice's own: a revision of the choice it was given with, kept with
    // the choice in the record and its export. A choice the answer did not name keeps the
    // reason as an assessment of the take.
    const body = pick.decision ? {...pending.current, reason: reason.trim(), previous_id: pick.decision}
      : {...pending.current, reason: reason.trim(), node_ids: [pick.node.id], direction_id: pick.direction, turn_id: pick.node.turn_id};
    try {
      await result(fetchClient.POST('/api/crexil/projects/{wid}/assessments', {params: {path: {wid}}, body}));
      refresh();
      close(`Reason kept with ${name}.`);
    } catch (failure) {
      setError(message(failure));
      if (isDefiniteRefusal(failure)) pending.current = null;
    } finally {
      setBusy(false);
    }
  };
  return <form className={s.pickReason} aria-label={`Reason for ${name}`}
    onSubmit={event => {event.preventDefault(); if (reason.trim()) void keep(); else close(null);}}>
    <strong>{pick.chosen ? 'Selected' : 'Removed'} {name}</strong>
    <input autoFocus aria-label={`Why ${name}? Optional`} placeholder="Why? Optional, kept with your choice" maxLength={4000}
      value={reason} disabled={busy} onChange={event => {setReason(event.target.value); pending.current = null;}}/>
    <button disabled={busy || !reason.trim()}>{busy ? 'Keeping…' : pending.current ? 'Keep same reason' : 'Keep reason'}</button>
    <span className={s.keyHint}>Enter keeps · Esc skips</span>
    {error && <span className={s.error} role="alert">{error}</span>}
  </form>;
}

function BlindRun({turn, picked, waiting, refresh, revealed}: {turn: Turn; picked: string[]; waiting: boolean; refresh: () => void;
  /** Told the line to say once the reveal is recorded. */
  revealed: (line: string) => void}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<ReturnType<typeof command> & {reason: string} | null>(null);
  const reveal = async () => {
    setBusy(true);
    setError('');
    pending.current ??= {...command(), reason: reason.trim()};
    try {
      const run = await result(fetchClient.POST('/api/crexil/turns/{tid}/reveal', {params: {path: {tid: turn.id}}, body: pending.current}));
      pending.current = null;
      refresh();
      revealed(`Run ${turn.idx} revealed${run.revealed_by ? ` by ${run.revealed_by.name}` : ''}. Every take now names its engine and price.`);
    } catch (failure) {
      setError(message(failure));
      if (isDefiniteRefusal(failure)) pending.current = null;
    } finally {
      setBusy(false);
    }
  };
  return <div className={s.blindRun} role="group" aria-label={`Blind run ${turn.idx}`}>
    <strong>Blind · Run {turn.idx}</strong>
    {/* The reveal follows the choice once its reason is settled: one "Why?" at a time. */}
    {waiting ? null : picked.length ? <>
      <input aria-label={`Reason for revealing run ${turn.idx}, optional`} placeholder="Why reveal now? Optional" maxLength={4000}
        value={reason} disabled={busy} onChange={event => {setReason(event.target.value); pending.current = null;}}/>
      <button disabled={busy} title="Ends the blind comparison: every reader sees each take's engine." onClick={() => void reveal()}>
        {busy ? 'Revealing…' : 'Reveal engines'}</button>
    </> : <>
      <span className={s.roomSaid}>Engines hidden. Select with S; the reveal follows your choice.</span>
      <button className={s.quietButton} disabled={busy} title="Ends the blind comparison without a choice." onClick={() => void reveal()}>
        Reveal without choosing</button>
    </>}
    {error && <span className={s.error} role="alert">{error}</span>}
  </div>;
}
