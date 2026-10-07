import { MaterialThumbnail } from './MaterialThumbnail';
// Filmstrip rows hold sections: a starter, its chosen faces and a door onward.
// A picker owns a pending display choice; action targets are separate transient
// state. Rows never widen merely because outputs arrived. Undrawn rooms use the
// same tokens and return to their entry point, one Escape layer at a time. Which
// rooms and layers are open lives in the project view store (view.ts); media
// differences live in the registry (media.tsx).
import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FocusEvent as ReactFocusEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { $api, fetchClient } from '../../api/client';
import { player } from '../audio/player';
import { useHash, useHashRoute } from '../routes';
import { DraftDrawer,hasPendingSubmission } from './DraftDrawer';
import {hasLocalPreparation} from './usePreparationDraft';
import {ConnectionsRoom} from './ConnectionsRoom';
import {ProjectDetails,hasProjectEdits} from './ProjectDetails';
import { OperationLibrary } from './OperationLibrary';
import { deliverControl, ExecutionControls, moveAttempt } from './ExecutionControls';
import { AttemptFace } from './AttemptFace';
import { attemptFace, blindFace, sharedCauses, type AttemptFaceVM, type QueueFacts, type RemedyId } from './attempts';
import { auditionTakes, roomRuns } from './audition';
import { hiddenRuns, isWithheld, withheldRuns } from './blind';
import { RoomLine, useFastPick, useRoomAudition } from './GridRoom';
import { VoiceDirectorPanel } from '../voice-director';
import { SequenceRoom } from './SequenceRoom';
import { DeliveryRoom } from './DeliveryRoom';
import { BackupRoom } from './BackupRoom';
import { ReturnedWork } from './ReturnedWork';
import { ReuseRoom } from './ReuseRoom';
import { RecordRoom } from './RecordRoom';
import { MaterialProvenance, TakeWords } from './MaterialProvenance';
import { LineageCanvas } from './LineageCanvas';
import { ancestors, sourceBindings } from './lineage';
import { continuationRow } from './family';
import { initialImagePreparation } from './ImagePreparationFields';
import { ACTIONS, startWord, type Action } from './actions';
import { actorLine, blankPreparation, command, count, engineIdentity, engineLabel, imageUrl, importFile, label, message, money, preparation,
  projectHref, result, type Direction, type Material, type Snapshot } from './api';
import { useCatalogue } from './catalogue';
import { byGridPosition, gridCells, type Cell, type Slot } from './cells';
import { attemptCharges, faceCost, pickerGroups, sectionFoot, statusWord } from './faces';
import { AUDIO_FACE, useCommandKey, useEscapeBase, useEscapeKey } from './keys';
import { faceName, IMPORT_ACCEPT, mediumOf } from './media';
import { cardVerbs, SPEAK_VERB } from './verbCard';
import { VerbCard } from './VerbCard';
import { covered, createViewStore, overlay, useView, useViewStore, ViewContext, type FocusReturn, type Overlay, type View } from './view';
import s from './Filmstrip.module.css';

/** The directions the workspace draws, never a discarded one: the tray's set-aside ones,
 *  every one in a grid room, else those not set aside. */
const shownDirections = (directions: Direction[], room: View['room']) => directions.filter(d => d.state !== 'discarded'
  && (room === 'tray' ? d.set_aside : room !== null || !d.set_aside));

/** A grid room's cells, every section of its row in order. */
function roomCells(data: Snapshot | undefined, room: View['room']): Cell[] {
  if (!data || typeof room !== 'number') return [];
  return data.directions.filter(direction => direction.state !== 'discarded' && direction.row_idx === room)
    .flatMap(direction => gridCells(direction, data, true));
}

/** The voice director column's width while it is open (`--director-inset` on the root
 *  element), which the picker keeps clear of, as every surface does (law 2). */
const directorInset = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--director-inset')) || 0;

/** A slot holding several takes keeps room for all of them from Run (law 8). */
const SPAN_GAP = 8;
const spanWidth = (span: number) => span * 158 + (span - 1) * SPAN_GAP;

function keepFocus(event: ReactKeyboardEvent<HTMLElement>) {
  if(event.key!=='Tab')return;
  const targets=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],summary,input:not(:disabled),textarea:not(:disabled)')).filter(el=>el.getClientRects().length>0);
  const first=targets[0], last=targets[targets.length-1];
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
}


export function Crexil() {
  // The page comes from the one route parser; each page reads its own query state.
  const route = useHashRoute();
  useEscapeKey();
  // Nothing plays once the surface is gone, whichever room started it.
  useEffect(() => () => player.pause(), []);
  const health = $api.useQuery('get', '/api/health', {}, {refetchInterval: 15000});
  const bundle = import.meta.env.VITE_BUILD_ID ?? 'dev';
  const healthBody = health.data ?? (health.error as unknown as {preview?: boolean; build?: {id: string}});
  return <div className={s.app}>
    {route.view==='connections'?<ConnectionsRoom/>:route.view==='operations' ? <OperationLibrary/> : route.view==='project' ? <ProjectRoom key={route.id} id={route.id}/> : <Projects/>}
    <footer className={s.footer}><span>Crexil · {bundle}{healthBody?.build?.id && healthBody.build.id !== bundle ? ' · BUILD MISMATCH' : ''}</span>
      {healthBody?.preview && <span>Isolated preview · generation disconnected</span>}</footer>
  </div>;
}

function Projects() {
  const query = $api.useQuery('get', '/api/crexil/projects');
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);
  const [restoring,setRestoring]=useState(false);
  const restoreOrigin=useRef<HTMLButtonElement>(null);
  const closeRestore=()=>{setRestoring(false);requestAnimationFrame(()=>restoreOrigin.current?.focus());};
  const [name, setName] = useState('');
  const [brief, setBrief] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef<ReturnType<typeof command> | null>(null);
  const create = async () => {
    setBusy(true); setError('');
    pending.current ??= command();
    try {
      const project = await result(fetchClient.POST('/api/crexil/projects', {body:{...pending.current, name, brief}}));
      location.hash = projectHref(project.id);
    } catch (err) {setError(message(err));} finally {setBusy(false);}
  };
  const visible = (query.data ?? []).filter(p => `${p.name} ${p.brief}`.toLowerCase().includes(search.toLowerCase()));
  const loadError: unknown = query.error;
  const reload = () => void query.refetch();
  return <>
    <header className={s.topbar}><strong className={s.brand}>Crexil</strong><span className={s.mono}>Projects</span>
      <div className={s.spacer}/><a className={s.quiet} href="#/connections">Connections</a><a className={s.quiet} href="#/operations">Operations</a><button ref={restoreOrigin} onClick={()=>setRestoring(true)}>Restore project</button><button onClick={() => setCreating(v => !v)}>+ New project</button></header>
    {restoring?<BackupRoom close={closeRestore} restored={id=>{location.hash=projectHref(id);}}/>:<main className={s.projects}>
      <div className={s.pageHeading}><div><span className={s.eyebrow}>Your work, kept together</span><h1>Return to a direction.<br/>Or start another.</h1></div>
        <label className={s.search}>Find a project<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name or brief"/></label></div>
      {creating && <form className={s.newProject} onSubmit={e => {e.preventDefault(); void create();}}>
        <label>Project name<input autoFocus required maxLength={200} value={name} onChange={e => {setName(e.target.value); pending.current=null;}} placeholder="Brass handle"/></label>
        <label>Brief <span className={s.hint}>optional</span><textarea value={brief} onChange={e => {setBrief(e.target.value); pending.current=null;}} placeholder="What are you making?"/></label>
        {error && <p role="alert" className={s.error}>{error}</p>}
        <div className={s.actions}><button type="button" onClick={() => setCreating(false)}>Cancel</button><button className={s.primary} disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Create project'}</button></div>
      </form>}
      {query.isLoading && <p role="status">Opening your projects…</p>}
      {Boolean(loadError) && <p role="alert" className={s.error}>{message(loadError)} <button onClick={reload}>Try again</button></p>}
      <div className={s.projectList}>{visible.map(project => <a key={project.id} className={s.projectCard} href={projectHref(project.id)}>
        <div className={s.projectGlyph} aria-hidden="true"><span/><span/><span/></div>
        <h2>{project.name}</h2><p>{project.brief || 'An open direction. Add words or material when you are ready.'}</p>
        <div className={s.projectMeta}><span>Opened {new Date(project.updated_at).toLocaleDateString()}</span><span className={s.door}>Open ›</span></div>
      </a>)}</div>
      {!query.isLoading && !query.error && visible.length === 0 && <div className={s.empty}>
        <h2>{search ? 'No project matches that search.' : 'A project can begin with an image.'}</h2>
        <p>{search ? 'Try another name or a word from the brief.' : 'Keep sources, unfinished ideas and the choices that come next. Importing and preparing work needs no engine or account.'}</p>
        {!search && <button className={s.primary} onClick={() => setCreating(true)}>Start a project</button>}
      </div>}
    </main>}
  </>;
}

type PickerState = Extract<Overlay, {kind: 'picker'}>;

function ProjectRoom({id}: {id: string}) {
  const hash = useHash();
  const [store] = useState(() => {
    const query = new URLSearchParams(hash.split('?')[1]);
    const material = query.get('material');
    return createViewStore({drawer: query.get('draft'), overlays: [
      ...(hasProjectEdits(id) ? [{kind: 'projectEdit'} as const] : []),
      ...(material ? [{kind: 'inspect', id: material} as const] : []),
    ]});
  });
  return <ViewContext.Provider value={store}><ProjectWorkspace id={id}/></ViewContext.Provider>;
}

function ProjectWorkspace({id}: {id: string}) {
  const query = $api.useQuery('get', '/api/crexil/projects/{wid}', {params:{path:{wid:id}}}, {refetchInterval:3000});
  const operations = $api.useQuery('get', '/api/crexil/operations');
  // Engines read by their catalogue names and lanes on every surface of the project.
  const catalogue = useCatalogue(operations.data);
  const costs = $api.useQuery('get', '/api/costs', {params:{query:{tz:-new Date().getTimezoneOffset()}}}, {refetchInterval:10000});
  const gridOpen = useView(state => state.room !== null && state.room !== 'tray');
  // A held attempt's covering bench comes from the queue view, read only while a grid room is open.
  const queue = $api.useQuery('get', '/api/queue', {}, {enabled: gridOpen, refetchInterval: 3000});
  const cache = useQueryClient();
  const refresh = useCallback(() => {void cache.invalidateQueries({queryKey:$api.queryOptions('get', '/api/crexil/projects/{wid}', {params:{path:{wid:id}}}).queryKey});}, [cache,id]);
  const store = useViewStore();
  const view = useView(state => state);
  const {room, panel, record, nestedRecord, recordDestination, acting, drawer, sequenceReturn} = view;
  const picker = overlay(view, 'picker');
  const inspected = overlay(view, 'inspect')?.id ?? null;
  const groupId = overlay(view, 'group')?.id ?? null;
  const editingProject = Boolean(overlay(view, 'projectEdit'));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const working = useRef(0);
  const file = useRef<HTMLInputElement>(null);
  const focusOrigin = useRef<HTMLElement | null>(null);
  const recordFocus = useRef<HTMLElement | null>(null);
  const nestedRecordFocus = useRef<HTMLElement | null>(null);
  const recordOrigin = () => nestedRecord ? nestedRecordFocus : recordFocus;
  const data = query.data;
  const directions = data?.directions ?? [];
  const nodes = data?.nodes ?? [];
  const activeDraft = directions.find(d => d.id === drawer);
  const actingNodes = nodes.filter(n => acting.includes(n.id));
  const restoreFocus = useCallback((focus: FocusReturn) => {
    const target = focus === 'origin' ? focusOrigin : focus === 'record' ? recordFocus : focus === 'nestedRecord' ? nestedRecordFocus : null;
    if (target) requestAnimationFrame(() => target.current?.focus());
  }, []);
  const closeLayer = useCallback(() => restoreFocus(store.getState().closeTop()), [store, restoreFocus]);
  // A face acts on its take (shift adds or removes it from the set) and inspects it on a
  // double click; both read the view at the moment of the click, so the faces stay memoized.
  const actOn = useCallback((id: string, shift: boolean, element: HTMLElement) => {
    focusOrigin.current = element;
    const {acting: now, act} = store.getState();
    act(shift ? now.includes(id) ? now.filter(n => n !== id) : [...now, id] : [id]);
  }, [store]);
  const inspectTake = useCallback((id: string) => store.getState().inspect(id), [store]);
  useEscapeBase(closeLayer);
  const {inspect: inspectNode, openDrawer} = store.getState();
  // Runs one action; resolves to its failure message, or null when it succeeded.
  const act = async (work: () => Promise<unknown>): Promise<string | null> => {
    setBusy(true); setError(''); working.current += 1;
    try {await work(); refresh(); return null;}
    catch(err) {const failed = message(err); setError(failed); return failed;}
    finally {setBusy(false); working.current -= 1;}
  };
  // Words open a row of their own, or join the row they are added to; a continuation
  // lands on the row after its sources, beside the family's other continuations (family.ts).
  const newDirection = async (row?: number, action: Action = 'generate', parents: Material[] = []) => {
    await act(async () => {
      const made = await result(fetchClient.POST('/api/crexil/projects/{wid}/directions', {params:{path:{wid:id}}, body:{...command(),
        title:ACTIONS[action]?.title ?? 'New direction', row_idx:row ?? continuationRow(parents, directions),
        setup:{...blankPreparation(), action, references:parents.map(n => ({node_id:n.id, contribution:''})), ...(action==='prepare'?{image_preparation:initialImagePreparation(parents)}:{})}}}));
      store.getState().prepared(made.id);
    });
  };
  // Plain keys reach the Filmstrip only when nothing covers it (view.ts `covered`).
  const isCovered = () => covered(store.getState());
  const actingNow = () => nodes.filter(n => store.getState().acting.includes(n.id));
  useCommandKey('n', () => void newDirection(), () => !isCovered() && working.current === 0);
  useCommandKey('v', () => inspectNode(store.getState().acting[0]), () => !isCovered() && store.getState().acting.length === 1);
  useCommandKey('m', () => void newDirection(undefined,'combine',actingNow()), () => !isCovered() && working.current === 0 && actingNow().length > 1);
  // L speaks a line in the acting take's voice, when the card offers that verb live.
  const speakVerb = () => cardVerbs(actingNow(), operations.data).find(verb => verb.label === SPEAK_VERB && verb.live);
  useCommandKey('l', () => {const verb = speakVerb(); if (verb) void newDirection(undefined, verb.action, verb.parents);},
    () => !isCovered() && working.current === 0 && Boolean(speakVerb()));
  // The open grid room's take positions: audition, and S to select the focused take. The
  // digits number one run's positions (GridRoom.tsx).
  const inRoom = roomCells(data, room);
  const takes = auditionTakes(inRoom);
  useRoomAudition(takes, roomRuns(inRoom));
  const fastPick = useFastPick(data, takes, refresh);
  const {settle} = fastPick;
  useEffect(() => {settle(null); return () => player.pause();}, [room, settle]);
  // The lineage canvas observes the layout again whenever its inputs change, so they change
  // only with the project's record (its event sequence), the room or the acting takes, never
  // merely because the workspace rendered again.
  const recordSeq = data?.event_seq;
  const lineage = useMemo(() => ({
    bindings: data ? shownDirections(data.directions, room).flatMap(direction => sourceBindings(direction, data)) : [],
    nodes: data?.nodes ?? [],
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the record changes only with its event sequence
  }), [recordSeq, room]);
  const actingKey = JSON.stringify(acting);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the acting takes, whichever array holds them
  const lineageActing = useMemo(() => acting, [actingKey]);
  // Run focuses its row's GRID › door (focusAfterDrawer). While that door keeps the focus Run
  // gave it, it opens the grid room with the run just sent in view: unless the run's section
  // is already wholly in view, it moves to where a row's first section starts, before the room
  // is first drawn, and the door keeps focus. The scroll that move takes is lent: the next room
  // change gives it back, so the Filmstrip and the next room find the row where it was. Any
  // other way into a room leaves the row's scroll where it is.
  const sentRun = useRef<{row: number; direction: string} | null>(null);
  const revealRun = useRef<string | null>(null);
  const lentScroll = useRef<{strip: HTMLElement; left: number} | null>(null);
  useLayoutEffect(() => {
    const lent = lentScroll.current;
    lentScroll.current = null;
    if (lent) lent.strip.scrollLeft = lent.left;
    const id = revealRun.current;
    revealRun.current = null;
    const section = id !== null && typeof room === 'number' ? document.querySelector<HTMLElement>(`[data-direction="${id}"]`) : null;
    const strip = section?.closest<HTMLElement>('[data-lineage-strip]');
    if (!section || !strip) return;
    const view = strip.getBoundingClientRect().left + strip.clientLeft;
    const start = view + (parseFloat(getComputedStyle(strip).paddingLeft) || 0);
    const {left, right} = section.getBoundingClientRect();
    // A section wholly in view, or already where the first one starts, never moves (law 8).
    if (Math.abs(left - start) < 1 || (left >= view && right <= view + strip.clientWidth)) return;
    lentScroll.current = {strip, left: strip.scrollLeft};
    strip.scrollLeft += left - start;
  }, [room]);
  if (!data) return <main className={s.projects}><a href={projectHref()}>‹ Projects</a>
    {query.error ? <p className={s.error} role="alert">{message(query.error)} <button onClick={() => void query.refetch()}>Try again</button></p> : <p role="status">Opening project…</p>}</main>;
  const visible = shownDirections(directions, room);
  const rows = [...new Set(visible.map(d => d.row_idx))].sort((a,b) => a-b);
  const asideCount = directions.filter(d => d.set_aside && d.state !== 'discarded').length;
  const stateAction = (direction: Direction, action: 'set_aside' | 'restore' | 'discard' | 'reuse'): Promise<string | null> => act(async () => {
    const next = await result(fetchClient.POST('/api/crexil/directions/{did}/state', {params:{path:{did:direction.id}}, body:{...command(), revision:direction.revision, action}}));
    if (action === 'reuse') openDrawer(next.id);
  });
  const inspect = nodes.find(n => n.id === inspected);
  const recordOpenDirection=(id:string)=>{
    recordOrigin().current=document.activeElement as HTMLElement;
    const direction=directions.find(d=>d.id===id);if(!direction)return;
    if(direction.state==='draft'||hasLocalPreparation(id)||hasPendingSubmission(id))openDrawer(id);
    else if(direction.state==='discarded')void stateAction(direction,'reuse');
    else store.getState().showFromRecord(direction.set_aside?'tray':direction.row_idx);
  };
  const recordOpenRun=(id:string)=>{
    recordOrigin().current=document.activeElement as HTMLElement;
    const direction=directions.find(d=>d.turn_ids.includes(id));if(direction)store.getState().showFromRecord(direction.row_idx);
  };
  const fromHere = (open: () => void) => (event: {currentTarget: HTMLElement}) => {focusOrigin.current = event.currentTarget; open();};
  // Focus after the drawer is where the next move is: a sent run's row door, to watch it
  // land, else the section the preparation belongs to.
  const focusAfterDrawer = (direction: Direction, ran: boolean) => requestAnimationFrame(() => {
    const next = document.querySelector<HTMLElement>(ran ? `#row-${direction.row_idx} [data-grid-door]` : `[data-direction="${direction.id}"] [data-starter]`);
    next?.focus();
    sentRun.current = ran && next !== null && document.activeElement === next ? {row: direction.row_idx, direction: direction.id} : null;
  });
  /** Focus that leaves the door for the page, or for nothing in it, disarms it; a blur that
   *  only means the window lost focus (no related target, the document unfocused) keeps it
   *  armed, since focus returns to the door with the window. */
  const disarm = (event: ReactFocusEvent) => {
    if (event.relatedTarget !== null || document.hasFocus()) sentRun.current = null;
  };
  /** A row's GRID › door opens its grid room, with the run Run just sent in view while the
   *  door still holds the focus Run gave it. */
  const openGridFrom = (row: number) => {
    revealRun.current = sentRun.current?.row === row ? sentRun.current.direction : null;
    sentRun.current = null;
    store.getState().openGrid(row);
  };
  const turns = data.turns;
  const {bindings} = lineage;
  const ancestry = ancestors(nodes,acting);
  const displayed = new Set(visible.flatMap(direction=>direction.selection));
  const charges = attemptCharges(nodes);
  const activeSlots = turns.flatMap(t => t.slots).filter(slot => ['queued','running','held','late'].includes(slot.status));
  const group = groupId ? data.execution_groups.find(g=>g.id===groupId) : undefined;
  const controls = data.execution_controls ?? [];
  const queueFacts = (slot: Slot): QueueFacts | null => {
    const rows = queue.data?.held ?? [];
    const held = rows.find(row => row.id === slot.id);
    const retry = queue.data?.retries.find(wait => wait.slot_id === slot.id) ?? null;
    if (!held) return retry ? {bench: null, benchKind: null, benchCause: null, retry} : null;
    const bench = queue.data?.benches.find(b => b.scope === held.bench);
    // A resume sends every attempt its bench holds, in any run, so its price is what they reserve (law 6).
    const sent = held.bench ? rows.filter(row => row.bench === held.bench) : [held];
    return {bench: held.bench ?? null, benchKind: bench?.kind ?? null, benchCause: bench?.cause ?? null,
      reservedUsd: sent.reduce((sum, row) => sum + (row.committed_usd ?? 0), 0), retry};
  };
  // A blind run's held attempt is never linked to its queue row, which would name its lane.
  const faceOf = (slot: Slot): AttemptFaceVM | null => {
    const turn = turns.find(t => t.id === slot.turn_id);
    return turn ? attemptFace(slot, {turn, operations: operations.data ?? [], controls, projectSlots: turns.flatMap(t => t.slots),
      queue: slot.identity_hidden === true ? null : queueFacts(slot)}) : null;
  };
  const withheld = withheldRuns(data);
  const roomTurns = typeof room === 'number' ? turns.filter(turn => visible.some(d => d.row_idx === room && d.turn_ids.includes(turn.id))) : [];
  const remedy = (direction: Direction, slot: Slot, face: AttemptFaceVM) => (which: RemedyId) => {
    const run = controls.find(c => c.scope === 'run' && c.target_id === slot.turn_id);
    const project = controls.find(c => c.scope === 'project');
    if (which === 'reuse') void stateAction(direction, 'reuse');
    else if (which === 'collect') void act(() => result(fetchClient.POST('/api/crexil/attempts/{sid}/collect', {params:{path:{sid:slot.id}}, body:{...command(), take:slot.attempt}})));
    else if (which === 'resume-run' && run) void act(() => deliverControl(run, 'resume'));
    else if (which === 'resume-project' && project) void act(() => deliverControl(project, 'resume'));
    // The move names the attempt this face shows; the face changes when the server's state does.
    else if (which === 'resume-engine') void act(() => moveAttempt(slot, 'resume'));
    else if (which === 'cancel-attempt') void act(() => moveAttempt(slot, 'cancel'));
    else if (which === 'copy-receipt' && face.receipt) void navigator.clipboard?.writeText(face.receipt).catch(() => setError('The clipboard refused. The receipt is under DETAILS on the cell.'));
    else if (which === 'connections') location.hash = `#/connections?project=${id}`;
  };
  return <>
    <header className={s.topbar}>
      <a className={s.back} href={projectHref()} aria-label="Projects">‹</a>
      <button className={s.projectTitle} onClick={() => store.getState().toggleProjectEdit()} title="Project brief and name">{data.project.name}</button>
      <span className={s.micro}>{count(rows.length, 'turn')}</span>
      <a className={s.quiet} href={projectHref()}>+ New project</a>
      <div className={s.spacer}/><a className={s.quiet} href={`#/connections?project=${id}`}>Connections</a><a className={s.quiet} href={`#/operations?project=${id}`}>Operations</a><button onClick={fromHere(store.getState().showImport)}>Import material</button>
      <button className={s.door} onClick={fromHere(() => store.getState().openRecord())}>Record ›</button>
      <VoiceDirectorPanel projectId={id} doorClassName={s.door} openRun={openDrawer}/>
      <button onClick={fromHere(store.getState().showSequences)}>Sequences{data.sequences?.length?` · ${data.sequences.length}`:''}</button>
      <button onClick={fromHere(store.getState().showDeliveries)}>Deliveries</button>
      <button onClick={fromHere(store.getState().showBackup)}>Back up</button>
      <button className={s.door} onClick={() => store.getState().toggleTray()}>Tray{asideCount ? ` · ${asideCount}` : ''} ›</button>
      {/* The money corner says whose each total is: today's spend sums every project; the
          second is this project's, whatever day it was spent. */}
      <span className={s.money}>All projects today {money(costs.data?.today_usd ?? 0)} · This project {money(data.charged_usd)}</span>
    </header>
    <input hidden ref={file} type="file" multiple accept={IMPORT_ACCEPT}
      onChange={e => {const files = Array.from(e.target.files ?? []); e.target.value=''; void act(async () => {for (const item of files) await importFile(id,item);store.getState().closeImport();});}}/>
    <div className={s.workspace}>
    {error && <div className={s.error} role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {!editingProject&&hasProjectEdits(id)&&<p className={s.localPreparation}>Project edits remain in this tab. <button onClick={()=>store.getState().openProjectEdit()}>Recover project edits</button></p>}
    {editingProject && <ProjectDetails project={data.project} accounting={data.accounting??{}} onSaved={() => {refresh(); store.getState().closeProjectEdit();}}/>}
    {!activeDraft&&directions.some(direction=>hasLocalPreparation(direction.id)||hasPendingSubmission(direction.id))&&<div className={s.localPreparation} role="region" aria-label="Local preparations"><span>Preparation needs attention in this tab</span>
      {directions.filter(direction=>hasLocalPreparation(direction.id)||hasPendingSubmission(direction.id)).map(direction=><button key={direction.id} onClick={()=>openDrawer(direction.id)}>Recover {direction.title}</button>)}
    </div>}
    {data.restoration&&<p className={s.hint} role="status">Restored project · Original execution and delivery records are kept as history. Reuse a preparation to review new work.</p>}
    {record&&<div className={s.workspacePane} hidden={recordDestination||Boolean(nestedRecord)}><RecordRoom key={JSON.stringify(record.scope)} data={data} scope={record.scope} close={closeLayer} returnLabel={record.returnNode?'Inspect':'Project'}
      inspect={id=>{recordFocus.current=document.activeElement as HTMLElement;inspectNode(id);}}
      openDirection={recordOpenDirection} openRun={recordOpenRun}/></div>}
    {nestedRecord&&<div className={s.workspacePane} hidden={recordDestination}><RecordRoom key={nestedRecord} secondary data={data} scope={{node_id:nestedRecord}} close={closeLayer} returnLabel="Inspect"
      inspect={id=>{nestedRecordFocus.current=document.activeElement as HTMLElement;inspectNode(id);}} openDirection={recordOpenDirection} openRun={recordOpenRun}/></div>}
    <div className={s.workspacePane} hidden={Boolean(record)&&!recordDestination}>
    {room !== null && <div className={s.roomHead}><strong>{room === 'tray' ? 'Tray' : `Grid · Turn ${room}`}</strong><span className={s.hint}>{room === 'tray' ? 'Directions kept aside. Their sources and decisions remain.' : 'Every attempt keeps its place. Display choices are shared with the Filmstrip.'}</span><button onClick={()=>restoreFocus(store.getState().leaveRoom())}>Esc · {recordDestination?'Record':sequenceReturn?'Sequence':'Project'}</button></div>}
    {typeof room === 'number' && !panel && <RoomLine data={data} turns={roomTurns} takes={takes} fastPick={fastPick} refresh={refresh}/>}
    {panel?.kind==='reuse'?<ReuseRoom wid={id} close={closeLayer} chooseFiles={()=>file.current?.click()} refresh={refresh} inspect={node=>store.getState().inspectFromPanel(node)}/>
      :panel?.kind==='backup'?<BackupRoom project={data.project} close={closeLayer} restored={()=>{}}/>
      :panel?.kind==='delivery'?<DeliveryRoom data={data} initial={panel.ids} close={closeLayer} refresh={refresh}/>
      :panel?.kind==='returned'?<ReturnedWork wid={id} nodes={nodes} initial={panel.ids} close={closeLayer} refresh={refresh} inspect={node=>store.getState().inspectFromPanel(node)}/>
      :panel?.kind==='sequences'?<SequenceRoom data={data} inactive={Boolean(activeDraft)} initialId={sequenceReturn} refresh={refresh} close={closeLayer} edit={openDrawer} inspect={inspectNode} openRun={(id,sid)=>{const direction=directions.find(item=>item.turn_ids.includes(id));if(direction)store.getState().showRunFromSequence(direction.row_idx,sid);}}/>
      :<div className={s.stage}><main className={s.canvas} onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); const files=Array.from(e.dataTransfer.files); void act(async () => {for(const item of files) await importFile(id,item);});}}>
      <LineageCanvas bindings={bindings} nodes={lineage.nodes} acting={lineageActing} enabled={room===null}>
      {rows.length === 0 && <div className={s.emptyProject}><span className={s.eyebrow}>{room === 'tray' ? 'Nothing set aside' : 'A place for the next idea'}</span>
        <h1>{room === 'tray' ? 'Your directions are on the project.' : 'Start with words or material.'}</h1>
        <p>{room === 'tray' ? 'Setting a direction aside keeps its record and every result.' : data.project.brief || 'Drop an image or GLB here, import a reference, or prepare your first direction. You can leave and return before running anything.'}</p>
        {room !== 'tray' && <div className={s.actions}><button className={s.primary} onClick={() => void newDirection()}>+ New direction · N</button><button onClick={fromHere(store.getState().showImport)}>Import material</button></div>}
      </div>}
      {rows.filter(row => room === null || room === 'tray' || room === row).map(row => <div className={s.row} id={`row-${row}`} key={row} data-lineage-row={row} style={{marginBottom:`var(--lineage-gap-${row},26px)`}}>
        <RowStrip sections={visible.filter(d => d.row_idx === row).length} gutter={more => <aside className={s.gutter}><div><span>T{row}</span> <button className={s.door} data-grid-door onClick={() => openGridFrom(row)} onBlur={disarm}>Grid ›</button></div>
          {visible.filter(d=>d.row_idx===row&&d.latest_decision).map(direction=><button key={direction.id} className={s.gutterDecision} onClick={fromHere(()=>store.getState().openRecord({direction_id:direction.id}))} title={`Latest decision for ${direction.title}`}>
            <span>{direction.latest_decision?.reason||'No reason supplied'}</span><small>{actorLine(direction.latest_decision?.actor)} ›</small>
          </button>)}
          <span>{visible.filter(d => d.row_idx === row).reduce((sum,d) => sum+d.selection.length,0)} selected</span>
          <span>{visible.filter(d => d.row_idx === row).reduce((sum,d) => sum+d.node_ids.length,0)} outputs</span>
          {more}
        </aside>}>{visible.filter(d => d.row_idx === row).map(direction => {
          const cells = gridCells(direction, data, room === row);
          const prep = preparation(direction);
          const sources = bindings.filter(binding=>binding.direction===direction.id);
          const firstSource = nodes.find(n => n.id === (sources[0]?.parents[0]??prep.references?.[0]?.node_id));
          return <section className={s.section} key={direction.id} aria-label={direction.title} data-direction={direction.id}>
            {room===row&&turns.filter(turn=>direction.turn_ids.includes(turn.id)).map(turn=>{
              const control=data.execution_controls?.find(control=>control.scope==='run'&&control.target_id===turn.id);
              return control?<ExecutionControls key={turn.id} control={control} slots={turn.slots} label={`Run ${turn.idx}`} refresh={refresh}/>:null;
            })}
            <div className={s.sectionBody}>
              <div className={s.starterColumn} style={{marginLeft:Math.max(0,sources.length-1)*5}}>
              <button data-starter data-lineage-start={sources[0]?.id} className={`${s.starter} ${sources[0]?.outputs.some(id=>ancestry.has(id))?s.ancestor:''}`} aria-label={`${direction.state === 'draft' ? 'Open preparation' : 'Reuse setup'}: ${direction.title}`} onClick={() => direction.state === 'draft' ? openDrawer(direction.id) : void stateAction(direction,'reuse')} title={direction.state === 'draft' ? 'Open preparation' : 'Reuse this setup'}>
                <div>{firstSource?.available ? <MaterialThumbnail key={firstSource.id} node={firstSource} decorative/> : <span>{prep.prompt || direction.title}</span>}</div>
                <footer><span>{startWord(prep.action)}</span> <span aria-hidden="true">▤</span></footer>
              </button>
              {sources.map((binding,index)=><div key={binding.id} className={s.sourceBinding}>
                {index>0&&<div className={`${s.recordedStarter} ${binding.outputs.some(id=>ancestry.has(id))?s.ancestor:''}`} data-lineage-start={binding.id}><span>{binding.title}</span><span>{binding.parents.map(id=>nodes.find(node=>node.id===id)).filter((node):node is Material=>Boolean(node)).map(label).join(' · ')}</span></div>}
                {binding.parents.length>0&&<details><summary>{binding.parents.length} {binding.parents.length===1?'source':'sources'}{binding.parents.some(id=>!displayed.has(id))?' · not all displayed':''}{sources.length>1&&index===0?` · ${binding.title}`:''}</summary>
                  {binding.parents.map(id=>{const node=nodes.find(node=>node.id===id);return node?<button key={id} onClick={()=>inspectNode(id)}>Inspect {label(node)}{!node.available?' · unavailable':!displayed.has(id)?' · not displayed':''}</button>:<span key={id}>Source {id} · record unavailable</span>;})}
                </details>}
              </div>)}
              </div>
              {cells.map(cell => {
                const node = cell.material, slot = cell.slot;
                const turn = slot ? turns.find(t => t.id === slot.turn_id) : undefined;
                const face = cell.withheld && slot && turn ? blindFace(turn, slot) : !node && slot ? faceOf(slot) : null;
                const wide = room===row && cell.span > 1;
                return <div key={cell.key} className={room===row?s.slotBox:undefined} style={wide?{width:spanWidth(cell.span)}:undefined}
                  data-run={room===row?slot?.turn_id:undefined}>
                  {face ? <AttemptFace face={face} busy={busy} onRemedy={remedy(direction, slot!, face)}/> : cell.takes.length > 0 &&
                    <div className={s.takeRow}>{cell.takes.map(take => <TakeFace key={take.id} take={take} catalogue={catalogue} cost={faceCost(take, charges)}
                      mark={acting.includes(take.id) ? 'acting' : room===row&&direction.selection.includes(take.id) ? 'selected' : ancestry.has(take.id) ? 'ancestor' : null}
                      selected={room===row&&direction.selection.includes(take.id)} onAct={actOn} onInspect={inspectTake}/>)}</div>}
                  {room===row&&slot&&<div className={s.slotFoot}><span>{node&&!cell.withheld?statusWord(slot.status):''}</span>{cell.group&&cell.group.outputs.length>0&&<button onClick={fromHere(()=>store.getState().openGroup(cell.group!.id))}>{cell.group.outputs.length} {cell.group.outputs.length===1?'file':'files'} ›</button>}</div>}
                </div>;
              })}
            </div>
            {room===row&&<SharedCauses faces={cells.flatMap(cell=>!cell.material&&cell.slot&&!cell.withheld?[faceOf(cell.slot)]:[]).filter((face):face is AttemptFaceVM=>face!==null)}/>}
            <div className={s.sectionFoot}><span>{sectionFoot(direction, turns, hiddenRuns(data))}</span>
              <span className={s.actions}>{direction.sequence_id&&<button className={s.door} onClick={fromHere(()=>store.getState().showSequence(direction.sequence_id!))}>Sequence ›</button>}<button className={s.door} onClick={e => {focusOrigin.current=e.currentTarget; const box=e.currentTarget.closest('section')!.getBoundingClientRect(); store.getState().openPicker(direction.id,Math.min(box.left,window.innerWidth-directorInset()-530),Math.min(box.top,window.innerHeight-440));}}>Strip ›</button></span></div>
          </section>;
        })}<button className={s.addSection} disabled={busy} onClick={() => void newDirection(row)}>+ Section</button></RowStrip>
      </div>)}
      {rows.length > 0 && room === null && <button className={s.newRow} disabled={busy} onClick={() => void newDirection()}>+ New direction · N</button>}
      </LineageCanvas>
    </main>
    {actingNodes.length > 0 && !drawer && !inspected && <VerbCard nodes={actingNodes} operations={operations.data}
      onInspect={() => inspectNode(actingNodes[0].id)} onClose={() => store.getState().clearActing()}
      onStart={verb => void (verb.each ? act(async () => {for (const node of verb.parents) await newDirection(undefined, verb.action, [node]);})
        : newDirection(undefined, verb.action, verb.parents))}>
      <button onClick={fromHere(store.getState().deliverActing)}>Deliver selected material</button>
      <button onClick={fromHere(store.getState().returnActing)}>Return edited material</button>
    </VerbCard>}
    </div>}
    </div>
    {data.execution_controls?.find(control=>control.scope==='project')&&<ExecutionControls
      control={data.execution_controls.find(control=>control.scope==='project')!} slots={turns.flatMap(turn=>turn.slots)} label="Project" refresh={refresh} pendingWork={data.sequences?.some(sequence=>sequence.state==='active'&&sequence.steps.some(step=>step.state!=='complete'))}/>}
    </div>
    {picker && directions.some(d => d.id === picker.direction) && <Picker key={picker.direction} state={picker} nodes={nodes}
      current={directions.find(d => d.id === picker.direction)!} withheld={withheld}
      close={closeLayer} saved={refresh} onInspect={inspectNode} stateAction={stateAction}/>}
    {activeDraft && <DraftDrawer key={activeDraft.id} direction={activeDraft} directions={directions} nodes={byGridPosition(nodes.filter(n => !isWithheld(n, withheld)))} operations={operations.data ?? []} onSaved={refresh} onCopied={id=>{refresh();openDrawer(id);}}
      onClose={ran => {store.getState().closeDrawer(); if (record) requestAnimationFrame(() => recordOrigin().current?.focus()); else focusAfterDrawer(activeDraft, ran);}}/>}
    {inspect && <Inspection key={inspect.id} node={inspect} data={data} close={closeLayer} navigate={inspectNode} openGroup={id=>store.getState().openGroup(id)} openRecord={()=>store.getState().openRecord({node_id:inspect.id},inspect.id)}
      returnLabel={record&&!recordDestination?'Record':room==='tray'?'Tray':typeof room==='number'?'Grid':'Project'}/>}
    {group&&<OutputGroup group={group} close={()=>store.getState().closeGroup()} refresh={refresh} inspect={id=>{store.getState().closeGroup();inspectNode(id);}}/>}
    <div className={s.queue}><span>{activeSlots.length} active or unresolved · Local engines take turns on one GPU</span><span>{busy ? 'Saving work…' : 'Changes stay in this project'}</span></div>
  </>;
}

/** A row's sections, which scroll sideways past the work area's width (the board's "past a
 *  row's span the row scrolls sideways"). While sections lie beyond the right edge, that edge
 *  fades, and a door at the foot of the row's gutter says how many and scrolls to them; it
 *  never covers the work. Focus scrolls them into view too. */
function RowStrip({sections, gutter, children}: {sections: number; gutter: (more: ReactNode) => ReactNode; children: ReactNode}) {
  const strip = useRef<HTMLDivElement>(null);
  const [beyond, setBeyond] = useState(0);
  useLayoutEffect(() => {
    const element = strip.current;
    if (!element) return;
    const measure = () => {
      const edge = element.getBoundingClientRect().right;
      setBeyond([...element.querySelectorAll(':scope > section')].filter(section => section.getBoundingClientRect().right > edge + 1).length);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    element.querySelectorAll(':scope > section').forEach(section => observer.observe(section));
    element.addEventListener('scroll', measure);
    return () => {observer.disconnect(); element.removeEventListener('scroll', measure);};
  }, [sections]);
  const more = beyond > 0 ? <button className={`${s.door} ${s.moreDoor}`} aria-label={`Show ${beyond} more ${beyond === 1 ? 'section' : 'sections'} of this row`}
    onClick={() => strip.current?.scrollBy({left: strip.current.clientWidth * .8, behavior: 'smooth'})}>{beyond} more ›</button> : null;
  return <>{gutter(more)}<div ref={strip} className={`${s.sections} ${beyond ? s.moreBeyond : ''}`} data-lineage-strip>{children}</div></>;
}

/** One take's face: a plain card with its plate, a one-row footer naming the take and its
 *  engine by its face name, and at the right its money (faces.ts); the footer's hover title
 *  names a named engine in full, and any other take as its face does ("Blind", "Prepared",
 *  "Imported"). Its name says the take and its medium; its description is the footer, and says
 *  the take is selected where the grid room draws that border. Memoized, so a change of the
 *  acting set redraws only the faces it touches. */
const TakeFace = memo(function TakeFace({take, cost, mark, selected, onAct, onInspect}: {
  take: Material; cost: string; mark: 'acting' | 'selected' | 'ancestor' | null; selected: boolean;
  /** The catalogue the footer names its engine from; a new one redraws the face. */
  catalogue: ReturnType<typeof useCatalogue>;
  onAct: (id: string, shift: boolean, element: HTMLElement) => void; onInspect: (id: string) => void;
}) {
  const foot = `take-foot-${take.id}`;
  return <button data-lineage-node={take.id} data-take={take.id} {...(take.payload === 'audio' ? {[AUDIO_FACE]: ''} : {})}
    aria-label={faceName(take)} aria-describedby={foot} aria-pressed={mark === 'acting'} className={`${s.face} ${mark ? s[mark] : ''}`}
    onDoubleClick={() => onInspect(take.id)} onClick={event => onAct(take.id, event.shiftKey, event.currentTarget)}>
    <div className={s.plate}><MaterialThumbnail node={take}/>{mark === 'acting' && <span className={s.actingMark}>Acting on</span>}{take.promoted_at && <span className={s.promoted}>Promoted</span>}</div>
    <footer id={foot}><span title={`${label(take)} · ${engineLabel(take, engineIdentity(take).kind === 'named' ? 'source' : 'face')}`}>{label(take)} · {engineLabel(take, 'face')}{selected && <span className={s.srOnly}>, selected</span>}</span><span>{cost}</span></footer>
  </button>;
});

// The picker reads the live direction while takes land; its choice applies to the revision
// it opened on, so a change made meanwhile elsewhere is refused, never overwritten. A blind
// run's takes join it once the run settles.
function Picker({state,nodes,current,withheld,close,saved,onInspect,stateAction}: {
  state:PickerState; nodes:Material[]; current:Direction; withheld:ReadonlySet<string>; close:()=>void; saved:()=>void;
  onInspect:(id:string)=>void; stateAction:(d:Direction, action:'set_aside'|'restore'|'reuse'|'discard')=>Promise<string|null>;
}) {
  const [opened]=useState(()=>({revision:current.revision,selection:current.selection}));
  const [selected,setSelected]=useState(opened.selection);
  const [reason,setReason]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const pending=useRef<ReturnType<typeof command> | null>(null);
  const arrived=nodes.filter(n=>current.node_ids.includes(n.id));
  const material=byGridPosition(arrived.filter(n=>!isWithheld(n,withheld)));
  const groups=pickerGroups(material);
  // A direction action closes the picker only when it succeeded; a failure stays here with its words.
  const settle=async(done:Promise<string|null>)=>{const failed=await done;if(failed)setError(failed);else close();};
  const apply=async()=>{
    setBusy(true);setError('');pending.current??=command();
    try {await result(fetchClient.PUT('/api/crexil/directions/{did}/selection',{params:{path:{did:current.id}},
      body:{...pending.current,revision:opened.revision,node_ids:selected,reason}}));saved();close();}
    catch(err){setError(message(err));}finally{setBusy(false);}
  };
  return <section className={s.picker} style={{left:Math.max(16,state.left),top:Math.max(56,state.top)}} aria-label="Choose displayed takes">
    <div className={s.pickerBody}>{groups.map(group=><div className={s.pickerRow} key={group.label}><span className={s.micro}>{group.label}</span><div className={s.thumbs}>{group.nodes.map(node=><button key={node.id} className={`${s.thumb} ${selected.includes(node.id)?s.selected:''}`} disabled={!node.available} aria-pressed={selected.includes(node.id)} aria-label={`Display ${label(node)}`} onDoubleClick={()=>onInspect(node.id)} onClick={()=>{pending.current=null;setSelected(previous=>previous.includes(node.id)?previous.filter(id=>id!==node.id):[...previous,node.id]);}}>
      <MaterialThumbnail key={node.id} node={node} decorative/><span>{label(node)}</span></button>)}</div></div>)}
      {arrived.length===0&&<p>This direction has no outputs yet. Open its starter to continue preparation.</p>}
      {arrived.length>material.length&&<p>Takes from a blind run still in flight show here when it settles.</p>}
    </div>
    <label className={s.reason}>Reason <input placeholder="Optional, kept with your choice" value={reason} onChange={e=>{setReason(e.target.value);pending.current=null;}}/></label>
    {error&&<p className={s.error} role="alert">{error}</p>}
    <div className={s.pickerFoot}><span>{selected.length} of {material.length} selected</span><div className={s.spacer}/><button onClick={close}>Esc</button><button autoFocus className={s.primary} disabled={busy} onClick={()=>void apply()}>Apply</button></div>
    <div className={s.pickerActions}><button onClick={()=>void settle(stateAction(current,'reuse'))}>Reuse setup</button>
      <button onClick={()=>void settle(stateAction(current,current.set_aside?'restore':'set_aside'))}>{current.set_aside?'Restore direction':'Set direction aside'}</button>
      {current.state==='draft'&&<button onClick={()=>void settle(stateAction(current,'discard'))}>Discard preparation</button>}</div>
  </section>;
}

export function Inspection({node,data,close,navigate,openGroup,openRecord,returnLabel}: {node:Material;data:Snapshot;close:()=>void;navigate:(id:string)=>void;openGroup:(id:string)=>void;openRecord:()=>void;returnLabel:string}) {
  const origin=node.material?.origin;
  const medium=mediumOf(node.payload);
  const generated=!['imported','prepared'].includes(engineIdentity(node).kind);
  const group=data.execution_groups.find(g=>g.outputs.some(o=>o.node_id===node.id));
  return <section className={s.inspection} aria-label="Inspect material" onKeyDown={keepFocus}>
    <header className={s.roomHead}><strong>Inspect · {label(node)}</strong><span className={s.hint}>{engineLabel(node,'source')}</span><button autoFocus onClick={close}>Esc · {returnLabel}</button></header>
    <div className={s.inspectBody}><div className={s[medium.inspectFrame]}>{node.available ? <medium.Inspect node={node}/>:<p>This material is unavailable. Its record remains.</p>}</div>
      <aside className={s.record}><h2>The record</h2>
        {medium.recordedDescription||!generated?node.prompt&&<><h3>Recorded description</h3><p className={s.recordPrompt}>{node.prompt}</p></>:<TakeWords node={node}/>}
        {origin&&<><h3>Source information</h3><p>{origin.kind==='execution'?'Collected from this execution. Its related files retain their own identities.':origin.kind==='prepared_image'?'Prepared from these sources by Crexil. Its pixel transform and source hashes were recorded.':origin.kind==='project_reuse'?'Imported from another project with an independent copy and its recorded origin.':origin.kind==='reported_external'?'Returned external work. Relationships were reported by a participant.':'Imported original. No generation was observed by Crexil.'}</p>{origin.tool&&<p>Tool · {String(origin.tool)}</p>}{origin.explanation&&<p>{String(origin.explanation)}</p>}{origin.kind==='prepared_image'&&<details><summary>Preparation recipe and source hashes</summary><pre>{JSON.stringify(origin.transform,null,2)}</pre></details>}</>}
        {origin?.kind==='project_reuse'&&<><h3>Original project</h3><a href={`${projectHref(String((origin.source as {project_id:string}).project_id))}?material=${encodeURIComponent(String((origin.source as {node_id:string}).node_id))}`}>{String((origin.source as {project_name:string}).project_name)} ›</a><p className={s.hash}>Source {String((origin.source as {node_id:string}).node_id)}</p><details><summary>Recorded source provenance</summary><pre>{JSON.stringify(origin.source_record,null,2)}</pre></details></>}
        {Boolean(node.retained_files?.length)&&<><h3>Retained related files</h3>{node.retained_files!.map(file=><div key={file.id}><strong>{file.filename}</strong><p>{file.role}{file.required?' · Required':''}</p>{file.available?<a href={`/api/crexil/material/${node.id}/files/${file.id}`} download>Download {file.filename}</a>:<p>{file.error??'File unavailable; its record remains.'}</p>}</div>)}</>}
        {node.material&&<dl><dt>Original file</dt><dd>{node.material.filename}</dd>{node.material.width!==null&&node.material.height!==null&&<><dt>Dimensions</dt><dd>{node.material.width} × {node.material.height}</dd></>}
          {medium.facts?.(node).map(([name,value])=><Fragment key={name}><dt>{name}</dt><dd>{value}</dd></Fragment>)}<dt>Content hash</dt><dd className={s.hash}>{node.material.sha256}</dd></dl>}
        {group&&<><h3>Related outputs</h3><button onClick={()=>openGroup(group.id)}>{group.outputs.length} {group.outputs.length===1?'file':'files'} · {group.state} ›</button></>}
        {node.parents.length>0&&<><h3>Sources</h3>{node.parents.map(id=>{const parent=data.nodes.find(n=>n.id===id);return parent?<button key={id} onClick={()=>navigate(id)}>{label(parent)}{!parent.available?' · unavailable':''} ›</button>:<p key={id}>Source {id} · record unavailable</p>;})}</>}
        {data.nodes.some(n=>n.parents.includes(node.id))&&<><h3>Continued in</h3>{data.nodes.filter(n=>n.parents.includes(node.id)).map(n=><button key={n.id} onClick={()=>navigate(n.id)}>{label(n)} ›</button>)}</>}
        {node.slot_id&&data.nodes.some(n=>n.slot_id===node.slot_id&&n.take!==node.take)&&<><h3>Other attempts in this slot</h3>{data.nodes.filter(n=>n.slot_id===node.slot_id&&n.id!==node.id).map(n=><button key={n.id} onClick={()=>navigate(n.id)}>Attempt {n.take} · {label(n)}{!n.available?' · unavailable':''} ›</button>)}</>}
        <MaterialProvenance node={node} data={data} navigate={navigate} openRecord={openRecord}/>
        {node.available&&<a className={s.download} href={imageUrl(node.id,true)} download={node.material?.filename??node.id}>Download original</a>}
        {Boolean(node.material?.origin.embedded_metadata)&&Object.keys(node.material!.origin.embedded_metadata as object).some(key=>['workflow','prompt'].includes(key))&&<a className={s.download} href={`#/operations?project=${data.project.id}&source=${node.id}`}>Prepare operation from metadata</a>}
      </aside></div>
  </section>;
}


function OutputGroup({group,close,refresh,inspect}: {group:Snapshot['execution_groups'][number];close:()=>void;refresh:()=>void;inspect:(id:string)=>void}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const collect=async()=>{
    setBusy(true);setError('');
    try {await result(fetchClient.POST('/api/crexil/attempts/{sid}/collect',{params:{path:{sid:group.slot_id}},body:{...command(),take:group.take}}));refresh();}
    catch(error){setError(message(error));}finally{setBusy(false);}
  };
  return <section className={`${s.inspection} ${s.outputRoom}`} aria-label="Related outputs" onKeyDown={keepFocus}>
    <header className={s.roomHead}><strong>Files from this attempt</strong><span className={s.hint}>{group.state} · attempt {group.take}</span><button autoFocus onClick={close}>Esc · Back</button></header>
    <div className={s.outputContent}>
      <p>{group.state==='complete'?'The runtime finished and its reported files were collected.':'This execution is incomplete or unconfirmed. Available files remain usable; they do not prove the full request succeeded.'}</p>
      {Boolean(group.metadata.archive_origin)&&<p>Restored execution history. Retained files are available here; the original runtime is not contacted.</p>}
      {group.state!=='collecting'&&<button disabled={busy||Boolean(group.metadata.archive_origin)} onClick={()=>void collect()}>{busy?'Checking runtime…':'Collect existing files again'}</button>}
      <p className={s.hint}>Retrieval checks this same runtime request. It never generates again. Unclassified roles have not been declared by the operation.</p>
      {error&&<p className={s.error} role="alert">{error}</p>}
      {group.outputs.length===0&&<p>No output files have been reported.</p>}
      <div className={s.outputList}>{group.outputs.map(output=><article key={output.id}>
        <div className={s.outputPreview}>{output.available&&output.node_id&&output.media_type.startsWith('image/')?<img src={imageUrl(output.node_id)} alt={output.filename}/>:<span>{output.available?'File':output.purged_at?'Purged':'Unavailable'}</span>}</div>
        <div><h3>{output.filename}</h3><p>{output.role} · {output.media_type}{output.size_bytes!==null?` · ${(output.size_bytes/1024).toFixed(1)} KB`:''}</p>
          {output.error&&<p className={s.error}>{output.error}</p>}
          <div className={s.actions}>{output.node_id&&<button onClick={()=>inspect(output.node_id!)}>Inspect</button>}{output.available&&<a href={`/api/crexil/outputs/${output.id}/file`} download>Download original</a>}</div>
          <details><summary>File identity</summary><p className={s.hash}>{output.sha256??'Bytes not collected'}</p><pre>{JSON.stringify(output.descriptor,null,2)}</pre></details>
        </div>
      </article>)}</div>
    </div>
  </section>;
}

/** Law 1: failed attempts that share a cause are named once for the run, in a
 *  footer whose height never changes, so a failure never moves a cell (law 8). */
function SharedCauses({faces}: {faces: AttemptFaceVM[]}) {
  const groups = sharedCauses(faces);
  return <div className={s.sharedCauses} aria-live="polite">{groups.map(group =>
    <p key={group.cause} title={group.cause}><strong>{group.labels.join(' · ')}</strong> {group.cause}</p>)}</div>;
}
