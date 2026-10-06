// The grid room's keyboard and its working line, the second line of its head. Digits play
// the room's take positions, Space the focused take and X the two marked takes, at matched
// levels; playing is inspection, never a verb (law 11). S selects the focused take: it opens
// the take's optional reason, and the choice is recorded once, with that reason, through the
// shared selection action ("the row is the choice"), so the record and its export hold one
// decision with its author and reason (law 23). A blind run's reveal is asked for in line,
// never in a dialog (law 2), is an authored event and follows the artist's choice, once its
// reason is settled. The line says out loud that local work takes turns on the one GPU (law
// 13). It is one line high, so nothing it says moves a cell (law 8), and it holds one status
// that every message shares, the reveal included.
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

/** A take S selects or removes, waiting for its optional reason before the choice is recorded. */
export type FastPick = {node: Material; direction: string; chosen: boolean};

/** The S key: toggles the focused take, else the one take acted on, in its direction's
 *  displayed selection, once its optional reason is settled. Only a take shown in the open
 *  grid room can be selected. */
export function useFastPick(data: Snapshot | undefined, takes: readonly (Material | null)[], refresh: () => void) {
  const store = useViewStore();
  const [pick, setPick] = useState<FastPick | null>(null);
  const [said, setSaid] = useState('');
  const latest = useRef(data);
  latest.current = data;
  const target = (): Material | null => {
    const {acting} = store.getState();
    const id = focusedTake() ?? (acting.length === 1 ? acting[0] : null);
    return takes.find(take => take !== null && take.id === id) ?? null;
  };
  const begin = (node: Material) => {
    const direction = data?.directions.find(item => item.node_ids.includes(node.id));
    if (!direction) return;
    setSaid('');
    setPick({node, direction: direction.id, chosen: !direction.selection.includes(node.id)});
  };
  useCommandKey('s', () => {const node = target(); if (node) begin(node);},
    () => store.getState().room !== null && !covered(store.getState()) && pick === null && target() !== null);
  /** Records the choice with its reason, as one decision, against the direction as it now is. */
  const choose = useCallback(async (chosen: FastPick, reason: string, request: ReturnType<typeof command>) => {
    const direction = latest.current?.directions.find(item => item.id === chosen.direction);
    if (!direction) throw new Error('This direction is no longer in the project. Its record remains.');
    const others = direction.selection.filter(id => id !== chosen.node.id);
    await result(fetchClient.PUT('/api/crexil/directions/{did}/selection', {params: {path: {did: direction.id}},
      body: {...request, revision: direction.revision, reason, node_ids: chosen.chosen ? [...others, chosen.node.id] : others}}));
    refresh();
  }, [refresh]);
  /** The reason form closed: with a line to say, or none. */
  const settle = useCallback((line: string | null) => {setPick(null); setSaid(line ?? '');}, []);
  return {pick, said, settle, choose, announce: setSaid};
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
  // A take started, stopped or refused says itself; what was said before gives way to it.
  const player = usePlayer(state => `${state.nodeId}:${state.playing}:${state.error}`);
  const {announce} = fastPick;
  const heard = useRef(player);
  useEffect(() => {
    if (heard.current === player) return;
    heard.current = player;
    announce('');
  }, [player, announce]);
  // Law 13, said while local work is in flight; a blind run's in-flight work may be local.
  const local = turns.some(turn => inFlight(turn) && (hidden.has(turn.id)
    || turn.slots.some(slot => ['queued', 'running'].includes(slot.status) && engineLane(slot.engine) !== 'hosted')));
  return <div className={s.roomLine}>
    <span className={s.keyHint}>{keyHint(audible)}</span>
    {fastPick.pick && <PickReason key={fastPick.pick.node.id} pick={fastPick.pick} choose={fastPick.choose} settle={fastPick.settle}/>}
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

/** The take in the player, said politely when a key starts or stops it, or why it did not
 *  play: the browser blocked it, or the voice director's session is connecting. */
function Playing({takes}: {takes: readonly (Material | null)[]}) {
  const id = usePlayer(state => state.nodeId);
  const playing = usePlayer(state => state.playing);
  const error = usePlayer(state => state.error);
  const matched = usePlayer(state => Boolean(state.nodeId && state.levels[state.nodeId]?.matched));
  const take = takes.find(item => item !== null && item.id === id) ?? null;
  if (!take) return null;
  return error ? `${label(take)} · ${error}` : playingLine(take, playing, matched);
}

/** The reason S asks for. Enter records the choice with the reason; Escape, an empty Enter or
 *  leaving the field records it with the words written so far, or none: S has chosen, and
 *  the reason stays optional. */
function PickReason({pick, choose, settle}: {pick: FastPick; settle: (line: string | null) => void;
  choose: ReturnType<typeof useFastPick>['choose']}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{request: ReturnType<typeof command>; reason: string} | null>(null);
  const done = useRef(false);
  const name = label(pick.node);
  const keep = async (words: string) => {
    if (busy || done.current) return;
    setBusy(true);
    setError('');
    // An unconfirmed choice is sent again as the same request; other words make a new one.
    if (pending.current?.reason !== words) pending.current = {request: command(), reason: words};
    try {
      await choose(pick, words, pending.current.request);
      done.current = true;
      settle(words ? `Reason kept with ${name}.` : `${pick.chosen ? 'Selected' : 'Removed'} ${name}.`);
      requestAnimationFrame(() => focusTake(pick.node.id));
    } catch (failure) {
      setError(message(failure));
      if (isDefiniteRefusal(failure)) pending.current = null;
    } finally {
      setBusy(false);
    }
  };
  useEscapeLayer(() => {void keep(''); return true;});
  return <form className={s.pickReason} aria-label={`Reason for ${name}`}
    onSubmit={event => {event.preventDefault(); void keep(reason.trim());}}>
    <strong>{pick.chosen ? 'Selected' : 'Removed'} {name}</strong>
    <input autoFocus aria-label={`Why ${name}? Optional`} placeholder="Why? Optional, kept with your choice" maxLength={4000}
      value={reason} disabled={busy} onChange={event => setReason(event.target.value)}
      onBlur={event => {if (!event.currentTarget.form?.contains(event.relatedTarget)) void keep(reason.trim());}}/>
    <button disabled={busy}>{busy ? 'Keeping…' : reason.trim() ? 'Keep reason' : 'Keep'}</button>
    <span className={s.keyHint}>Enter keeps · Esc skips the reason</span>
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
