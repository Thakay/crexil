// The voice director's conversation: an ElevenLabs Agents session in the column its door opens
// (VoiceDirectorPanel.tsx), never a modal (law 2). The server starts each session and hands
// over a conversation token once, so the key never reaches the browser (law 18). Minutes are
// counted from the moment the conversation connects and the session ends at its maximum (laws
// 17 and 22). The agent's tools prepare and propose; a paid run and a pick wait for the
// artist's click, with the price on the paid button's face (laws 6, 15 and 23). Before a
// session starts the panel says what leaves the machine. Take audio never does: the microphone
// mutes before any take plays (microphone.ts), and no take starts while a session connects.
import { ConversationProvider, useConversation } from '@elevenlabs/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { player, usePlayer } from '../audio';
import { artist as browserArtist, message, money, type Actor } from '../crexil/api';
import { door as httpDoor, type Door, type VoiceSession, type VoiceUsage } from './door';
import { plain } from './guard';
import { elapsedSince, minutes, sessionPrice } from './meter';
import { DUCKED, guardTakes, type Guard } from './microphone';
import { createTools, TOOL_NAMES, type ProposalCard, type RunCard, type Tool } from './tools';
import css from './VoiceDirector.module.css';

export type DirectorProps = {
  projectId: string;
  /** Who confirms runs and picks, and starts and ends sessions: by default, the artist this browser names. */
  artist?: Actor;
  door?: Door;
  /** Told whether a session is live, so the column's close and Escape follow it. */
  onLive?(live: boolean): void;
};

/** What leaves the machine during a session, said once before it starts. */
export const DISCLOSURE = 'While a session runs, your microphone audio goes to ElevenLabs and the director\'s language '
  + 'model, and so does what the director reads about this project: the brief, run prompts, take labels and reasons. '
  + 'ElevenLabs keeps each conversation for 7 days. Take audio never goes: the microphone mutes before any take plays.';

/** Why a take waits while a session connects. */
export const CONNECTING = 'The voice director is connecting. Play the take once it is listening.';

type Line = {id: number; role: 'artist' | 'director' | 'note'; text: string};
type Waiting = {state: 'waiting' | 'busy' | 'done'; note?: string};
type Card =
  | ({kind: 'run'} & RunCard & Waiting)
  | ({kind: 'proposal'; draft: string} & ProposalCard & Waiting)
  | {kind: 'free'; id: string; runId: string; title: string; attempts: number; state: 'done'};
type Unrecorded = {sessionId: string; elapsed: number; conversationId: string | null};

const POLL_MS = 4000;    // how often a queued run's progress is read for the agent
const VERB_WORDS = {'voice-design': 'Voice design', speak: 'Speech', sfx: 'Sound effect'} as const;
const short = (text: string, limit = 90) => text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;

export default function Director(props: DirectorProps) {
  return <ConversationProvider><Session {...props}/></ConversationProvider>;
}

function Session({projectId, artist = browserArtist(), door = httpDoor, onLive}: DirectorProps) {
  const [usage, setUsage] = useState<VoiceUsage | null>(null);
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [connectedAt, setConnectedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [lines, setLines] = useState<Line[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [userMuted, setUserMuted] = useState(false);
  const [unrecorded, setUnrecorded] = useState<Unrecorded | null>(null);
  const playing = usePlayer(state => state.playing);
  const sessionRef = useRef<VoiceSession | null>(null);
  const connectedRef = useRef<number | null>(null);
  const conversationRef = useRef<string | null>(null);
  const finished = useRef(false);
  const closed = useRef(false);
  const lineId = useRef(0);
  const tracked = useRef(new Map<string, {title: string; summary: string}>());
  // The microphone's state lives outside React, so a change reaches the conversation before
  // the take it guards against sounds: the take guard, the artist's own mute, and whether a
  // starting session still holds every take back.
  const guard = useRef<Guard>({muted: false, ducked: false});
  const mutedByArtist = useRef(false);
  const holding = useRef(false);

  const note = useCallback((role: Line['role'], text: string) => {
    setLines(current => [...current, {id: ++lineId.current, role, text}].slice(-60));
  }, []);

  const finish = useCallback(async () => {
    const current = sessionRef.current;
    if (!current || finished.current) return;
    finished.current = true;
    const record: Unrecorded = {sessionId: current.session_id, conversationId: conversationRef.current,
      elapsed: Math.round(elapsedSince(connectedRef.current, Date.now()) * 10) / 10};
    sessionRef.current = null;
    connectedRef.current = null;
    conversationRef.current = null;
    holding.current = false;
    setSession(null);
    setConnectedAt(null);
    tracked.current.clear();
    try {
      await door.end(record.sessionId, record.elapsed, record.conversationId, artist);
      setUnrecorded(null);
    } catch (failure) {
      setUnrecorded(record);
      setError(`The session ended, but its minutes were not recorded: ${message(failure)} They stay reserved until they are.`);
    }
    try {
      setUsage(await door.usage());
    } catch {
      // the meter keeps its last reading
    }
  }, [artist, door]);

  const conversation = useConversation({
    onConnect: ({conversationId}) => {
      conversationRef.current = conversationId;
      connectedRef.current = Date.now();
      setConnectedAt(connectedRef.current);
      microphone();
      holding.current = false;
    },
    onDisconnect: () => void finish(),
    onMessage: ({message: text, role}) => note(role === 'agent' ? 'director' : 'artist', text),
    onError: problem => {
      setError(`The voice session reported a problem: ${problem}`);
      // A session that never connected is over: its minutes are recorded, and takes play again.
      if (sessionRef.current && connectedRef.current === null) stop();
    },
  });
  const live = session !== null;
  // The SDK's handle and the latest finish, read through refs so effects keep stable timers
  // and a re-render never looks like the panel closing.
  const sdk = useRef(conversation);
  sdk.current = conversation;
  const finishing = useRef(finish);
  finishing.current = finish;
  const say = useCallback((text: string) => {
    if (sessionRef.current) sdk.current.sendContextualUpdate(text);
  }, []);
  const stop = useCallback(() => {
    sdk.current.endSession();
    void finishing.current();
  }, []);

  /** Sets the open conversation's microphone and the agent's volume from the take guard and the
   *  artist's own mute. With no conversation open it changes nothing; the next one is set as it
   *  connects. */
  const microphone = useCallback(() => {
    try {
      sdk.current.setMuted(mutedByArtist.current || guard.current.muted);
      sdk.current.setVolume({volume: guard.current.ducked ? DUCKED : 1});
    } catch {
      // The SDK throws when no conversation is open.
    }
  }, []);

  // Every take the shared player starts, whoever starts it, is guarded while the panel is open.
  useEffect(() => guardTakes(player, next => {
    guard.current = next;
    microphone();
  }, () => holding.current ? CONNECTING : null), [microphone]);

  useEffect(() => {onLive?.(live);}, [live, onLive]);

  const update = useCallback((id: string, change: Partial<Card>) => {
    setCards(current => current.map(card => card.id === id ? {...card, ...change} as Card : card));
  }, []);

  const tools = useMemo(() => {
    const made = createTools(projectId, door, {
      runCard: card => setCards(current => [...current, {kind: 'run', ...card, state: 'waiting'}]),
      ranFree: run => {
        setCards(current => [...current, {kind: 'free', id: crypto.randomUUID(), ...run, state: 'done'}]);
        tracked.current.set(run.runId, {title: run.title, summary: ''});
      },
      proposal: card => setCards(current => [...current, {kind: 'proposal', ...card, draft: card.reason, state: 'waiting'}]),
      play: async (nodeId, seconds) => {
        await player.play(nodeId, {duration: seconds});
        const state = player.store.getState();
        return state.nodeId === nodeId && state.error ? state.error : null;
      },
    });
    const named: Record<string, Tool> = {};
    for (const name of TOOL_NAMES) {
      named[name] = async parameters => {
        note('note', `Director used ${name.replaceAll('_', ' ')}`);
        return made[name](parameters);
      };
    }
    return named;
  }, [door, note, projectId]);

  // The budget, read once when the panel opens and again after each session.
  useEffect(() => {
    let alive = true;
    door.usage().then(found => alive && setUsage(found), failure => alive && setError(message(failure)));
    return () => {alive = false;};
  }, [door]);

  // The meter counts while connected, and the session ends at its maximum (law 22).
  useEffect(() => {
    if (connectedAt === null || !session) return;
    const timer = setInterval(() => {
      const at = Date.now();
      setNow(at);
      if (elapsedSince(connectedAt, at) >= session.max_seconds) {
        note('note', 'The session reached its limit and ended.');
        stop();
      }
    }, 500);
    return () => clearInterval(timer);
  }, [connectedAt, note, session, stop]);

  // Queued runs report their progress to the agent, as facts it does not answer aloud.
  useEffect(() => {
    if (connectedAt === null) return;
    const timer = setInterval(() => {
      for (const [runId, last] of tracked.current) {
        door.run(runId).then(view => {
          const summary = plain(view.summary);
          if (summary !== last.summary) {
            tracked.current.set(runId, {...last, summary});
            say(`Run "${plain(view.title)}" (${runId}): ${summary}`);
          }
          if (!view.cells.some(cell => ['queued', 'running', 'held'].includes(cell.state))) tracked.current.delete(runId);
        }, () => undefined);
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [connectedAt, door, say]);

  // Closing the panel ends a live session, and records it.
  useEffect(() => () => {
    closed.current = true;
    if (sessionRef.current && !finished.current) {
      sdk.current.endSession();
      void finishing.current();
    }
  }, []);

  async function start() {
    if (busy || sessionRef.current) return;
    setBusy(true);
    setError(null);
    try {
      const started = await door.start(projectId, artist);
      if (closed.current) {
        // The panel closed while the server started the session: it never connects, and its
        // minutes are recorded as unused.
        void door.end(started.session.session_id, 0, null, artist).catch(() => undefined);
        return;
      }
      setUsage(started.usage);
      sessionRef.current = started.session;
      finished.current = false;
      setSession(started.session);
      if (!started.conversation_token) throw new Error('The server started no conversation. Try again.');
      // The microphone opens while the session connects, before it can be muted: no take sounds
      // until it listens.
      holding.current = true;
      player.pause();
      conversation.startSession({conversationToken: started.conversation_token, connectionType: 'webrtc', clientTools: tools});
    } catch (failure) {
      setError(message(failure));
      if (sessionRef.current) void finish();
    } finally {
      setBusy(false);
    }
  }

  async function recordEnd(record: Unrecorded) {
    try {
      await door.end(record.sessionId, record.elapsed, record.conversationId, artist);
      setUnrecorded(null);
      setError(null);
      setUsage(await door.usage());
    } catch (failure) {
      setError(`The minutes were not recorded: ${message(failure)}`);
    }
  }

  async function confirmRun(card: Extract<Card, {kind: 'run'}>) {
    update(card.id, {state: 'busy', note: undefined});
    try {
      const review = await door.review(card.runId);
      if (!review.ready) {
        update(card.id, {state: 'waiting', ready: false, errors: review.errors.map(plain), note: 'This run is no longer ready. Nothing was sent.'});
        return;
      }
      if (review.token !== card.token || review.reserved_usd !== card.reservedUsd) {
        update(card.id, {state: 'waiting', token: review.token, revision: review.revision, reservedUsd: review.reserved_usd,
          maximumUsd: review.maximum_usd, attempts: review.ready_attempts, errors: review.errors.map(plain),
          note: 'The run or its price changed since the director prepared it. Check the amount, then run.'});
        return;
      }
      const sent = await door.submit(card.runId, review.revision, review.token, artist);
      if (sent.ok) {
        update(card.id, {state: 'done', note: `Queued ${card.attempts} attempts.`});
        tracked.current.set(card.runId, {title: card.title, summary: ''});
        say(`The artist ran "${card.title}" (${card.runId}): ${card.attempts} attempts queued.`);
        return;
      }
      if (sent.conflict === 'spending') {
        const again = await door.review(card.runId);
        update(card.id, {state: 'waiting', token: again.token, revision: again.revision, reservedUsd: again.reserved_usd,
          maximumUsd: again.maximum_usd, ready: again.ready, errors: again.errors.map(plain),
          note: 'Spending moved since review. The amount shown is current; run again to confirm it.'});
        return;
      }
      update(card.id, {state: 'waiting', note: plain(sent.message)});
    } catch (failure) {
      update(card.id, {state: 'waiting', note: message(failure)});
    }
  }

  function discard(card: Extract<Card, {kind: 'run'}>) {
    update(card.id, {state: 'done', note: 'Discarded. The draft stays in the project.'});
    say(`The artist discarded the prepared run "${card.title}"; it stays as a draft.`);
  }

  async function pick(card: Extract<Card, {kind: 'proposal'}>) {
    update(card.id, {state: 'busy', note: undefined});
    const names = card.takes.map(take => take.label).join(', ');
    try {
      const view = await door.run(card.runId);
      const chosen = [...new Set([...view.selection, ...card.takes.map(take => take.nodeId)])];
      await door.pick(card.runId, view.revision, chosen, card.draft.trim() || card.reason, artist);
      update(card.id, {state: 'done', note: `Picked ${names}.`});
      say(`The artist picked ${names}.`);
    } catch (failure) {
      update(card.id, {state: 'waiting', note: message(failure)});
    }
  }

  const elapsed = elapsedSince(connectedAt, now);
  const status = !live ? (usage?.ready ? 'Ready' : 'Not ready')
    : conversation.status !== 'connected' ? 'Connecting'
    : playing ? 'Playing a take · microphone muted'
    : conversation.isSpeaking ? 'Speaking' : 'Listening';
  const shown = cards.filter(card => card.kind === 'free' || card.state !== 'done' || card.note).slice(-8);

  return <>
    <div className={css.cap}>
      <span role="status">{status}</span>
      {live && <span>Esc · Back to the work</span>}
    </div>

    <section className={css.meter} aria-label="Voice minutes">
      {live && session ? <>
        <meter min={0} max={session.max_seconds} value={Math.min(elapsed, session.max_seconds)} aria-label="Session minutes used"/>
        <p>{minutes(elapsed)} of {minutes(session.max_seconds)} · ends at the limit</p>
      </> : usage && <>
        <meter min={0} max={Math.max(usage.cap_seconds, 1)} value={usage.used_seconds + usage.reserved_seconds} aria-label="Voice budget used"/>
        <p>{minutes(usage.left_seconds)} left of {minutes(usage.cap_seconds)} · about {money(usage.usd_per_minute)} a minute, estimate; model use is billed on top</p>
      </>}
    </section>

    {!live && <section className={css.start}>
      <p className={css.notice}>{DISCLOSURE}</p>
      {usage && !usage.ready && <ul className={css.reasons}>{usage.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {unrecorded
        ? <button type="button" onClick={() => void recordEnd(unrecorded)}>Record the last session's minutes</button>
        // A session that cannot start carries no price: there is no money about to move (law 6).
        : <button type="button" className={usage?.ready ? css.paid : undefined} disabled={!usage?.ready || busy} onClick={() => void start()}>
            {busy ? 'Starting…' : usage?.ready ? `Start · up to ${money(sessionPrice(usage))}` : 'Start'}
          </button>}
    </section>}

    {live && <section className={css.live}>
      <button type="button" aria-pressed={userMuted} onClick={() => {
        mutedByArtist.current = !mutedByArtist.current;
        setUserMuted(mutedByArtist.current);
        microphone();
      }}>{userMuted ? 'Unmute microphone' : 'Mute microphone'}</button>
      <button type="button" onClick={stop}>End session</button>
    </section>}

    {error && <p className={css.error} role="alert">{error}</p>}

    {shown.length > 0 && <section className={css.cards} aria-label="Runs and proposals">
      {shown.map(card => card.kind === 'run' ? <article key={card.id} className={css.card} aria-label={`Prepared run: ${card.title}`}>
        <p className={css.eyebrow}>Prepared by the voice director</p>
        <p className={css.what}>{VERB_WORDS[card.action]}: “{short(card.prompt)}”</p>
        <p className={css.facts}>{card.engines.map(engine => `${engine.label} ×${engine.count}`).join(' · ')}</p>
        <p className={css.facts}>Cap {money(card.capUsd)} · reserves up to {money(card.reservedUsd)}
          {card.published ? ' · by the published price; the final provider charge is not capped' : ''}</p>
        {card.errors.length > 0 && <ul className={css.reasons}>{card.errors.map(item => <li key={item}>{item}</li>)}</ul>}
        {card.note && <p className={css.cardNote}>{card.note}</p>}
        {card.state !== 'done' && <div className={css.actions}>
          <button type="button" disabled={card.state === 'busy'} onClick={() => discard(card)}>Discard</button>
          {card.ready && <button type="button" className={css.paid} disabled={card.state === 'busy'} onClick={() => void confirmRun(card)}>
            Run · up to {money(card.reservedUsd)}
          </button>}
        </div>}
      </article> : card.kind === 'proposal' ? <article key={card.id} className={css.card} aria-label={`Proposal: ${card.takes.map(take => take.label).join(', ')}`}>
        <p className={css.eyebrow}>The voice director proposes</p>
        <p className={css.what}>{card.takes.map(take => take.label).join(', ')} · {card.reason}</p>
        {card.state !== 'done' && <label className={css.reason}>Reason
          <input value={card.draft} maxLength={300} onChange={event => update(card.id, {draft: event.target.value})}/>
        </label>}
        {card.note && <p className={css.cardNote}>{card.note}</p>}
        {card.state !== 'done' && <div className={css.actions}>
          <button type="button" disabled={card.state === 'busy'} onClick={() => update(card.id, {state: 'done', note: 'Dismissed. The proposal stays in the record.'})}>Dismiss</button>
          <button type="button" disabled={card.state === 'busy'} onClick={() => void pick(card)}>Pick {card.takes.map(take => take.label).join(', ')}</button>
        </div>}
      </article> : <article key={card.id} className={css.card} aria-label={`Started: ${card.title}`}>
        <p className={css.eyebrow}>Started for you · free · local engines</p>
        <p className={css.what}>{card.title}</p>
        <p className={css.facts}>{card.attempts} attempts queued. Local engines take turns on one GPU.</p>
      </article>)}
    </section>}

    <ol className={css.log} role="log" aria-live="polite" aria-label="Conversation">
      {lines.length === 0 && <li className={css.empty}>The conversation appears here.</li>}
      {lines.map(line => <li key={line.id} className={css[line.role]}>
        {line.role !== 'note' && <span className={css.who}>{line.role === 'director' ? 'Director' : 'You'}</span>}{line.text}
      </li>)}
    </ol>
  </>;
}
