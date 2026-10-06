import { MaterialThumbnail } from './MaterialThumbnail';
// Filmstrip rows hold sections: a starter, its chosen faces and a door onward.
// A picker owns a pending display choice; action targets are separate transient
// state. Rows never widen merely because outputs arrived. Undrawn rooms use the
// same tokens and return to their entry point, one Escape layer at a time.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { $api, fetchClient } from '../../api/client';
import { useHashRoute } from '../routes';
import { DraftDrawer,hasPendingSubmission } from './DraftDrawer';
import {hasLocalPreparation} from './usePreparationDraft';
import {ConnectionsRoom} from './ConnectionsRoom';
import {ProjectDetails,hasProjectEdits} from './ProjectDetails';
import { OperationLibrary } from './OperationLibrary';
import { ExecutionControls } from './ExecutionControls';
import { SequenceRoom } from './SequenceRoom';
import { MeshInspection } from './MeshInspection';
import { DeliveryRoom } from './DeliveryRoom';
import { BackupRoom } from './BackupRoom';
import { ReturnedWork } from './ReturnedWork';
import { ReuseRoom } from './ReuseRoom';
import { RecordRoom, type RecordScope } from './RecordRoom';
import { MaterialProvenance } from './MaterialProvenance';
import { LineageCanvas } from './LineageCanvas';
import { ancestors, sourceBindings } from './lineage';
import { initialImagePreparation } from './ImagePreparationFields';
import { blankPreparation, command, imageUrl, importFile, label, message, money, preparation,
  projectHref, result, type Direction, type Material, type Snapshot } from './api';
import s from './Filmstrip.module.css';

function useEscape(close: () => void) {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => { if (event.key === 'Escape'&&!event.defaultPrevented) {event.preventDefault(); close();} };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [close]);
}

function keepFocus(event: ReactKeyboardEvent<HTMLElement>) {
  if(event.key!=='Tab')return;
  const targets=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],summary,input:not(:disabled),textarea:not(:disabled)')).filter(el=>el.getClientRects().length>0);
  const first=targets[0], last=targets[targets.length-1];
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
}

export function Crexil() {
  // Subscribe to hash updates with the established routing mechanism.
  useHashRoute();
  const id = /^#\/projects\/([^/?]+)(?:\?.*)?$/.exec(location.hash)?.[1];
  const health = $api.useQuery('get', '/api/health', {}, {refetchInterval: 15000});
  const bundle = import.meta.env.VITE_BUILD_ID ?? 'dev';
  const healthBody = health.data ?? (health.error as unknown as {preview?: boolean; build?: {id: string}});
  return <div className={s.app}>
    {location.hash.startsWith('#/connections')?<ConnectionsRoom/>:location.hash.startsWith('#/operations') ? <OperationLibrary/> : id ? <ProjectRoom key={id} id={id}/> : <Projects/>}
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

type PickerState = {direction: Direction; left: number; top: number};

function ProjectRoom({id}: {id: string}) {
  const query = $api.useQuery('get', '/api/crexil/projects/{wid}', {params:{path:{wid:id}}}, {refetchInterval:3000});
  const operations = $api.useQuery('get', '/api/crexil/operations');
  const costs = $api.useQuery('get', '/api/costs', {params:{query:{tz:-new Date().getTimezoneOffset()}}}, {refetchInterval:10000});
  const cache = useQueryClient();
  const refresh = useCallback(() => {void cache.invalidateQueries({queryKey:$api.queryOptions('get', '/api/crexil/projects/{wid}', {params:{path:{wid:id}}}).queryKey});}, [cache,id]);
  const [draft, setDraft] = useState<string | null>(new URLSearchParams(location.hash.split('?')[1]).get('draft'));
  const [picker, setPicker] = useState<PickerState | null>(null);
  const [acting, setActing] = useState<string[]>([]);
  const [groupId, setGroupId] = useState<string | null>(null);
  const [inspected, setInspected] = useState<string | null>(new URLSearchParams(location.hash.split('?')[1]).get('material'));
  const [room, setRoom] = useState<number | 'tray' | null>(null);
  const [editingProject, setEditingProject] = useState(()=>hasProjectEdits(id));
  const [sequencesOpen, setSequencesOpen] = useState(false);
  const [delivery, setDelivery] = useState<string[]|null>(null);
  const [backup,setBackup]=useState(false);
  const [reusing,setReusing]=useState(false);
  const [recordScope,setRecordScope]=useState<RecordScope|null>(null);
  const [recordDestination,setRecordDestination]=useState(false);
  const [recordReturn,setRecordReturn]=useState<string|null>(null);
  const recordFocus=useRef<HTMLElement|null>(null);
  const [nestedRecordNode,setNestedRecordNode]=useState<string|null>(null);
  const nestedRecordFocus=useRef<HTMLElement|null>(null);
  const [returned, setReturned] = useState<string[]|null>(null);
  const [sequenceReturn, setSequenceReturn] = useState<string|null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const focusOrigin = useRef<HTMLElement | null>(null);
  const data = query.data;
  const directions = data?.directions ?? [];
  const nodes = data?.nodes ?? [];
  const activeDraft = directions.find(d => d.id === draft);
  const actingNodes = nodes.filter(n => acting.includes(n.id));
  const closeLayer = useCallback(() => {
    if (groupId) setGroupId(null);
    else if (inspected) {setInspected(null);if(recordScope){requestAnimationFrame(()=>(nestedRecordNode?nestedRecordFocus:recordFocus).current?.focus());return;}}
    else if (picker) setPicker(null);
    else if (editingProject) setEditingProject(false);
    else if (reusing) setReusing(false);
    else if (backup) setBackup(false);
    else if (delivery) setDelivery(null);
    else if (returned) setReturned(null);
    else if (sequencesOpen) setSequencesOpen(false);
    else if (recordDestination) {setRecordDestination(false);setRoom(null);requestAnimationFrame(()=>(nestedRecordNode?nestedRecordFocus:recordFocus).current?.focus());return;}
    else if (nestedRecordNode) {setInspected(nestedRecordNode);setNestedRecordNode(null);return;}
    else if (recordScope) {setRecordScope(null);if(recordReturn){setInspected(recordReturn);setRecordReturn(null);}}
    else if (acting.length) setActing([]);
    else if (room !== null) {setRoom(null);if(sequenceReturn)setSequencesOpen(true);}
    requestAnimationFrame(() => focusOrigin.current?.focus());
  }, [groupId,inspected,picker,editingProject,reusing,backup,delivery,returned,sequencesOpen,sequenceReturn,acting.length,room,recordScope,recordReturn,recordDestination,nestedRecordNode]);
  useEscape(closeLayer);
  const openRecord=(scope:RecordScope={},returnNode:string|null=null)=>{
    if(returnNode&&recordScope){setNestedRecordNode(returnNode);setInspected(null);return;}
    setNestedRecordNode(null);
    setRecordScope(scope);setRecordReturn(returnNode);setRecordDestination(false);setInspected(null);
    setRoom(null);setActing([]);setPicker(null);setReusing(false);setBackup(false);setDelivery(null);setReturned(null);setSequencesOpen(false);
  };
  useEffect(()=>{if(reusing||backup||delivery||returned||sequencesOpen){setRecordScope(null);setRecordDestination(false);setNestedRecordNode(null);}},[reusing,backup,delivery,returned,sequencesOpen]);
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try {await work(); refresh();} catch(err) {setError(message(err));} finally {setBusy(false);}
  };
  const newDirection = async (row?: number, action: 'generate' | 'continue' | 'combine' | 'prepare' | 'region' | 'mesh' | 'decimate' | 'texture' = 'generate', parents: Material[] = []) => {
    await act(async () => {
      const made = await result(fetchClient.POST('/api/crexil/projects/{wid}/directions', {params:{path:{wid:id}}, body:{...command(),
        title:action === 'generate' ? 'New direction' : action === 'combine' ? 'Combined sources' : action==='prepare'?'Prepared image':action==='region'?'Region study':action==='mesh'?'Generate 3D':action==='decimate'?'Reduce mesh':action==='texture'?'Texture mesh':'Continue from an image', row_idx:row,
        setup:{...blankPreparation(), action, references:parents.map(n => ({node_id:n.id, contribution:''})), ...(action==='prepare'?{image_preparation:initialImagePreparation(parents)}:{})}}}));
      setDraft(made.id); setActing([]); setPicker(null);
    });
  };
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest('input,textarea,select,[contenteditable]') || draft || picker || inspected || sequencesOpen || delivery || returned || backup || reusing || recordScope) return;
      if (e.key.toLowerCase() === 'n') {e.preventDefault(); void newDirection();}
      if (e.key.toLowerCase() === 'v' && acting.length === 1) {e.preventDefault(); setInspected(acting[0]);}
      if (e.key.toLowerCase() === 'm' && actingNodes.length > 1) {e.preventDefault(); void newDirection(undefined,'combine',actingNodes);}
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown',handler);
  });
  if (!data) return <main className={s.projects}><a href={projectHref()}>‹ Projects</a>
    {query.error ? <p className={s.error} role="alert">{message(query.error)} <button onClick={() => void query.refetch()}>Try again</button></p> : <p role="status">Opening project…</p>}</main>;
  const visible = directions.filter(d => d.state !== 'discarded' && (room === 'tray' ? d.set_aside : room!==null||!d.set_aside));
  const rows = [...new Set(visible.map(d => d.row_idx))].sort((a,b) => a-b);
  const asideCount = directions.filter(d => d.set_aside && d.state !== 'discarded').length;
  const stateAction = (direction: Direction, action: 'set_aside' | 'restore' | 'discard' | 'reuse') => act(async () => {
    const next = await result(fetchClient.POST('/api/crexil/directions/{did}/state', {params:{path:{did:direction.id}}, body:{...command(), revision:direction.revision, action}}));
    if (action === 'reuse') setDraft(next.id);
  });
  const inspect = nodes.find(n => n.id === inspected);
  const recordOpenDirection=(id:string)=>{
    (nestedRecordNode?nestedRecordFocus:recordFocus).current=document.activeElement as HTMLElement;
    const direction=directions.find(d=>d.id===id);if(!direction)return;
    if(direction.state==='draft'||hasLocalPreparation(id)||hasPendingSubmission(id))setDraft(id);
    else if(direction.state==='discarded')void stateAction(direction,'reuse');
    else{setRecordDestination(true);setRoom(direction.set_aside?'tray':direction.row_idx);}
  };
  const recordOpenRun=(id:string)=>{
    (nestedRecordNode?nestedRecordFocus:recordFocus).current=document.activeElement as HTMLElement;
    const direction=directions.find(d=>d.turn_ids.includes(id));if(direction){setRecordDestination(true);setRoom(direction.row_idx);}
  };
  const turns = data.turns;
  const bindings = visible.flatMap(direction=>sourceBindings(direction,data));
  const ancestry = ancestors(nodes,acting);
  const displayed = new Set(visible.flatMap(direction=>direction.selection));
  const activeSlots = turns.flatMap(t => t.slots).filter(slot => ['queued','running','held','late'].includes(slot.status));
  return <>
    <header className={s.topbar}>
      <a className={s.back} href={projectHref()} aria-label="Projects">‹</a>
      <button className={s.projectTitle} onClick={() => setEditingProject(v => !v)} title="Project brief and name">{data.project.name}</button>
      <span className={s.micro}>{rows.length} turns</span>
      <a className={s.quiet} href={projectHref()}>+ New project</a>
      <div className={s.spacer}/><a className={s.quiet} href={`#/connections?project=${id}`}>Connections</a><a className={s.quiet} href={`#/operations?project=${id}`}>Operations</a><button onClick={event=>{focusOrigin.current=event.currentTarget;setActing([]);setRoom(null);setSequencesOpen(false);setReturned(null);setDelivery(null);setBackup(false);setReusing(true);}}>Import material</button>
      <button className={s.door} onClick={event=>{focusOrigin.current=event.currentTarget;openRecord();}}>Record ›</button>
      <button onClick={event=>{focusOrigin.current=event.currentTarget;setReusing(false);setBackup(false);setActing([]);setRoom(null);setDelivery(null);setReturned(null);setSequencesOpen(true);}}>Sequences{data.sequences?.length?` · ${data.sequences.length}`:''}</button>
      <button onClick={event=>{focusOrigin.current=event.currentTarget;setReusing(false);setBackup(false);setActing([]);setSequencesOpen(false);setReturned(null);setDelivery([]);}}>Deliveries</button>
      <button onClick={event=>{focusOrigin.current=event.currentTarget;setActing([]);setRoom(null);setSequencesOpen(false);setReturned(null);setDelivery(null);setReusing(false);setBackup(true);}}>Back up</button>
      <button className={s.door} onClick={() => {setRecordScope(null);setRecordDestination(false);setNestedRecordNode(null);setReusing(false);setBackup(false);setDelivery(null);setReturned(null);setSequencesOpen(false);setSequenceReturn(null);setRoom(room === 'tray' ? null : 'tray');}}>Tray{asideCount ? ` · ${asideCount}` : ''} ›</button>
      <span className={s.money}>Today {money(costs.data?.today_usd ?? 0)} · Total {money(data.charged_usd)}</span>
    </header>
    <input hidden ref={file} type="file" multiple accept="image/png,image/jpeg,image/webp,image/tiff,.glb,model/gltf-binary"
      onChange={e => {const files = Array.from(e.target.files ?? []); e.target.value=''; void act(async () => {for (const item of files) await importFile(id,item);setReusing(false);});}}/>
    {error && <div className={s.error} role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {!editingProject&&hasProjectEdits(id)&&<p className={s.localPreparation}>Project edits remain in this tab. <button onClick={()=>setEditingProject(true)}>Recover project edits</button></p>}
    {editingProject && <ProjectDetails project={data.project} accounting={data.accounting??{}} onSaved={() => {refresh(); setEditingProject(false);}}/>}
    {!activeDraft&&directions.some(direction=>hasLocalPreparation(direction.id)||hasPendingSubmission(direction.id))&&<div className={s.localPreparation} role="region" aria-label="Local preparations"><span>Preparation needs attention in this tab</span>
      {directions.filter(direction=>hasLocalPreparation(direction.id)||hasPendingSubmission(direction.id)).map(direction=><button key={direction.id} onClick={()=>setDraft(direction.id)}>Recover {direction.title}</button>)}
    </div>}
    {data.restoration&&<p className={s.hint} role="status">Restored project · Original execution and delivery records are kept as history. Reuse a preparation to review new work.</p>}
    {recordScope&&<div className={s.workspacePane} hidden={recordDestination||Boolean(nestedRecordNode)}><RecordRoom key={JSON.stringify(recordScope)} data={data} scope={recordScope} close={closeLayer} returnLabel={recordReturn?'Inspect':'Project'}
      inspect={id=>{recordFocus.current=document.activeElement as HTMLElement;setInspected(id);}}
      openDirection={recordOpenDirection} openRun={recordOpenRun}/></div>}
    {nestedRecordNode&&<div className={s.workspacePane} hidden={recordDestination}><RecordRoom key={nestedRecordNode} secondary data={data} scope={{node_id:nestedRecordNode}} close={closeLayer} returnLabel="Inspect"
      inspect={id=>{nestedRecordFocus.current=document.activeElement as HTMLElement;setInspected(id);}} openDirection={recordOpenDirection} openRun={recordOpenRun}/></div>}
    <div className={s.workspacePane} hidden={Boolean(recordScope)&&!recordDestination}>
    {room !== null && <div className={s.roomHead}><strong>{room === 'tray' ? 'Tray' : `Grid · Turn ${room}`}</strong><span className={s.hint}>{room === 'tray' ? 'Directions kept aside. Their sources and decisions remain.' : 'Every attempt keeps its place. Display choices are shared with the Filmstrip.'}</span><button onClick={()=>{setActing([]);if(recordDestination)closeLayer();else{setRoom(null);if(sequenceReturn)setSequencesOpen(true);}}}>Esc · {recordDestination?'Record':sequenceReturn?'Sequence':'Project'}</button></div>}
    {reusing?<ReuseRoom wid={id} close={closeLayer} chooseFiles={()=>file.current?.click()} refresh={refresh} inspect={node=>{setReusing(false);setInspected(node);}}/>:backup?<BackupRoom project={data.project} close={closeLayer} restored={()=>{}}/>:delivery?<DeliveryRoom data={data} initial={delivery} close={closeLayer} refresh={refresh}/>:returned?<ReturnedWork wid={id} nodes={nodes} initial={returned} close={closeLayer} refresh={refresh} inspect={node=>{setReturned(null);setInspected(node);}}/>:sequencesOpen?<SequenceRoom data={data} inactive={Boolean(activeDraft)} initialId={sequenceReturn} refresh={refresh} close={closeLayer} edit={setDraft} inspect={setInspected} openRun={(id,sid)=>{const direction=directions.find(item=>item.turn_ids.includes(id));if(direction){setSequenceReturn(sid);setSequencesOpen(false);setRoom(direction.row_idx);}}}/>:<main className={s.canvas} onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); const files=Array.from(e.dataTransfer.files); void act(async () => {for(const item of files) await importFile(id,item);});}}>
      <LineageCanvas bindings={bindings} nodes={nodes} acting={acting} enabled={room===null}>
      {rows.length === 0 && <div className={s.emptyProject}><span className={s.eyebrow}>{room === 'tray' ? 'Nothing set aside' : 'A place for the next idea'}</span>
        <h1>{room === 'tray' ? 'Your directions are on the project.' : 'Start with words or material.'}</h1>
        <p>{room === 'tray' ? 'Setting a direction aside keeps its record and every result.' : data.project.brief || 'Drop an image or GLB here, import a reference, or prepare your first direction. You can leave and return before running anything.'}</p>
        {room !== 'tray' && <div className={s.actions}><button className={s.primary} onClick={() => void newDirection()}>+ New direction · N</button><button onClick={event=>{focusOrigin.current=event.currentTarget;setActing([]);setRoom(null);setSequencesOpen(false);setReturned(null);setDelivery(null);setBackup(false);setReusing(true);}}>Import material</button></div>}
      </div>}
      {rows.filter(row => room === null || room === 'tray' || room === row).map(row => <div className={s.row} id={`row-${row}`} key={row} data-lineage-row={row} style={{marginBottom:`var(--lineage-gap-${row},26px)`}}>
        <aside className={s.gutter}><div><span>T{row}</span> <button className={s.door} onClick={() => {setSequenceReturn(null);setRoom(row);}}>Grid ›</button></div>
          {visible.filter(d=>d.row_idx===row&&d.latest_decision).map(direction=><button key={direction.id} className={s.gutterDecision} onClick={event=>{focusOrigin.current=event.currentTarget;openRecord({direction_id:direction.id});}} title={`Latest decision for ${direction.title}`}>
            <span>{direction.latest_decision?.reason||'No reason supplied'}</span><small>{direction.latest_decision?.actor?`${direction.latest_decision.actor.kind==='agent'?'Agent':'Artist'} · ${direction.latest_decision.actor.name}`:'Participant not recorded'} ›</small>
          </button>)}
          <span>{visible.filter(d => d.row_idx === row).reduce((sum,d) => sum+d.selection.length,0)} selected</span>
          <span>{visible.filter(d => d.row_idx === row).reduce((sum,d) => sum+d.node_ids.length,0)} outputs</span>
        </aside>
        <div className={s.sections} data-lineage-strip>{visible.filter(d => d.row_idx === row).map(direction => {
          const material = nodes.filter(n => direction.node_ids.includes(n.id));
          const shown = room === row ? [
            ...material.filter(n => n.slot_id === null),
            ...turns.filter(t => direction.turn_ids.includes(t.id)).flatMap(t => t.slots.map(slot => material.find(n => n.slot_id === slot.id && n.take === slot.attempt && data.execution_groups.some(g=>g.slot_id===slot.id&&g.take===slot.attempt&&g.metadata.primary_output_id===g.outputs.find(o=>o.node_id===n.id)?.id)) ?? material.find(n=>n.slot_id===slot.id&&n.take===slot.attempt) ?? slot)),
          ] : material.filter(n => direction.selection.includes(n.id));
          const prep = preparation(direction);
          const sources = bindings.filter(binding=>binding.direction===direction.id);
          const firstSource = nodes.find(n => n.id === (sources[0]?.parents[0]??prep.references?.[0]?.node_id));
          return <section className={s.section} key={direction.id} aria-label={direction.title}>
            {room===row&&turns.filter(turn=>direction.turn_ids.includes(turn.id)).map(turn=>{
              const control=data.execution_controls?.find(control=>control.scope==='run'&&control.target_id===turn.id);
              return control?<ExecutionControls key={turn.id} control={control} slots={turn.slots} label={`Run ${turn.idx}`} refresh={refresh}/>:null;
            })}
            <div className={s.sectionBody}>
              <div className={s.starterColumn} style={{marginLeft:Math.max(0,sources.length-1)*5}}>
              <button data-lineage-start={sources[0]?.id} className={`${s.starter} ${sources[0]?.outputs.some(id=>ancestry.has(id))?s.ancestor:''}`} aria-label={`${direction.state === 'draft' ? 'Open preparation' : 'Reuse setup'}: ${direction.title}`} onClick={() => direction.state === 'draft' ? setDraft(direction.id) : void stateAction(direction,'reuse')} title={direction.state === 'draft' ? 'Open preparation' : 'Reuse this setup'}>
                <div>{firstSource?.available ? <MaterialThumbnail key={firstSource.id} node={firstSource} decorative/> : <span>{prep.prompt || direction.title}</span>}</div>
                <footer>{prep.action === 'generate' ? 'Words' : prep.action} <span aria-hidden="true">▤</span></footer>
              </button>
              {sources.map((binding,index)=><div key={binding.id} className={s.sourceBinding}>
                {index>0&&<div className={`${s.recordedStarter} ${binding.outputs.some(id=>ancestry.has(id))?s.ancestor:''}`} data-lineage-start={binding.id}><span>{binding.title}</span><span>{binding.parents.map(id=>nodes.find(node=>node.id===id)).filter((node):node is Material=>Boolean(node)).map(label).join(' · ')}</span></div>}
                {binding.parents.length>0&&<details><summary>{binding.parents.length} {binding.parents.length===1?'source':'sources'}{binding.parents.some(id=>!displayed.has(id))?' · not all displayed':''}{sources.length>1&&index===0?` · ${binding.title}`:''}</summary>
                  {binding.parents.map(id=>{const node=nodes.find(node=>node.id===id);return node?<button key={id} onClick={()=>setInspected(id)}>Inspect {label(node)}{!node.available?' · unavailable':!displayed.has(id)?' · not displayed':''}</button>:<span key={id}>Source {id} · record unavailable</span>;})}
                </details>}
              </div>)}
              </div>
              {shown.map(node => {
                const slot = 'payload' in node ? turns.flatMap(t=>t.slots).find(slot=>slot.id===node.slot_id) : node;
                const group = slot && data.execution_groups.find(g=>g.slot_id===slot.id&&g.take===slot.attempt);
                return <div key={node.id} className={room===row?s.slotBox:undefined}>
                  {!('payload' in node) ? <div className={s.attempt}><strong>{node.kind === 'uncertain' ? 'Outcome uncertain' : node.status}</strong><span>{node.engine}</span><p title={node.cause??undefined}>{node.cause}</p></div> : <button data-lineage-node={node.id} aria-label={`Act on ${label(node)}`} className={`${s.face} ${acting.includes(node.id) ? s.acting : ancestry.has(node.id)?s.ancestor:''}`} aria-pressed={acting.includes(node.id)} onDoubleClick={() => setInspected(node.id)}
                    onClick={e => {focusOrigin.current=e.currentTarget; setActing(e.shiftKey ? acting.includes(node.id) ? acting.filter(n => n !== node.id) : [...acting,node.id] : [node.id]);}}>
                    <div className={s.plate}><MaterialThumbnail key={node.id} node={node}/>{acting.includes(node.id) && <span className={s.actingMark}>Acting on</span>}{node.promoted_at && <span className={s.promoted}>Promoted</span>}</div>
                    <footer><span>{label(node)} · {node.engine ?? (node.material?.origin.kind==='prepared_image'?'Prepared':'Imported')}</span><span>{node.charged_usd == null ? node.engine ? 'Unverified' : node.material?.origin.kind==='prepared_image'?'Derived':'Source' : money(node.charged_usd)}</span></footer>
                  </button>}
                  {room===row&&slot&&<div className={s.slotFoot}><span>{'payload' in node?slot.status:''}</span>{group&&<button onClick={event=>{focusOrigin.current=event.currentTarget;setGroupId(group.id);}}>{group.outputs.length} {group.outputs.length===1?'file':'files'} ›</button>}</div>}
                </div>;
              })}
            </div>
            <div className={s.sectionFoot}><span>{direction.selection.length} of {material.length} selected{direction.state === 'draft' ? ' · Preparation' : ''}</span>
              <span className={s.actions}>{direction.sequence_id&&<button className={s.door} onClick={event=>{focusOrigin.current=event.currentTarget;setSequenceReturn(direction.sequence_id!);setSequencesOpen(true);setRoom(null);}}>Sequence ›</button>}<button className={s.door} onClick={e => {focusOrigin.current=e.currentTarget; const box=e.currentTarget.closest('section')!.getBoundingClientRect(); setPicker({direction,left:Math.min(box.left,window.innerWidth-530),top:Math.min(box.top,window.innerHeight-440)});}}>Strip ›</button></span></div>
          </section>;
        })}<button className={s.addSection} disabled={busy} onClick={() => void newDirection(row)}>+ Section</button></div>
      </div>)}
      {rows.length > 0 && room === null && <button className={s.newRow} disabled={busy} onClick={() => void newDirection()}>+ New direction · N</button>}
      </LineageCanvas>
    </main>}
    </div>
    {picker && <Picker key={picker.direction.id} state={picker} nodes={nodes} current={directions.find(d => d.id === picker.direction.id) ?? picker.direction}
      close={closeLayer} saved={refresh} onInspect={setInspected} stateAction={stateAction}/>}
    {actingNodes.length > 0 && !draft && !inspected && <section className={s.verbCard} aria-label="Actions on material">
      <div className={s.drawerHead}><span className={s.mono}>Acting on · {actingNodes.length}</span><button onClick={() => setActing([])}>Esc</button></div>
      <button onClick={() => setInspected(actingNodes[0].id)}>Inspect · V</button>
      {actingNodes.every(n=>n.payload==='image')&&<button onClick={() => void newDirection(undefined,actingNodes.length > 1 ? 'combine' : 'continue',actingNodes)}>{actingNodes.length > 1 ? 'Combine sources · M' : 'Continue from image'}</button>}
      {actingNodes.every(n=>n.payload==='image')&&<button onClick={()=>void newDirection(undefined,'prepare',actingNodes)}>Crop, resize or arrange</button>}
      {actingNodes.length===1&&actingNodes[0].payload==='image'&&<button onClick={()=>void newDirection(undefined,'region',actingNodes)}>Edit regions</button>}
      {actingNodes.length===1&&actingNodes[0].payload==='image'&&<button onClick={()=>void newDirection(undefined,'mesh',actingNodes)}>Generate 3D</button>}
      {actingNodes.length===1&&actingNodes[0].payload==='3d'&&<><button onClick={()=>void newDirection(undefined,'decimate',actingNodes)}>Reduce mesh</button><button onClick={()=>void newDirection(undefined,'texture',actingNodes)}>Texture mesh</button></>}
      {actingNodes.length===2&&actingNodes.some(n=>n.payload==='3d')&&actingNodes.some(n=>n.payload==='image')&&<button onClick={()=>void newDirection(undefined,'texture',[...actingNodes].sort((a)=>a.payload==='3d'?-1:1))}>Texture mesh from image</button>}
      {actingNodes.length > 1 && actingNodes.every(n=>n.payload==='image') && <button onClick={() => void act(async () => {for (const node of actingNodes) await newDirection(undefined,'continue',[node]);})}>Continue each separately</button>}
      <button onClick={event=>{focusOrigin.current=event.currentTarget;setDelivery([...acting]);setActing([]);}}>Deliver selected material</button>
      <button onClick={event=>{focusOrigin.current=event.currentTarget;setReturned([...acting]);setActing([]);}}>Return edited material</button>
    </section>}
    {activeDraft && <DraftDrawer key={activeDraft.id} direction={activeDraft} nodes={nodes} operations={operations.data ?? []} onSaved={refresh} onCopied={id=>{refresh();setDraft(id);}} onClose={() => {setDraft(null);if(recordScope)requestAnimationFrame(()=>(nestedRecordNode?nestedRecordFocus:recordFocus).current?.focus());}}/>}
    {inspect && <Inspection key={inspect.id} node={inspect} data={data} close={closeLayer} navigate={setInspected} openGroup={setGroupId} openRecord={()=>openRecord({node_id:inspect.id},inspect.id)} returnLabel={recordScope?'Record':'Project'}/>}
    {groupId&&data.execution_groups.find(g=>g.id===groupId)&&<OutputGroup group={data.execution_groups.find(g=>g.id===groupId)!} close={()=>setGroupId(null)} refresh={refresh} inspect={id=>{setGroupId(null);setInspected(id);}}/>}
    {data.execution_controls?.find(control=>control.scope==='project')&&<ExecutionControls
      control={data.execution_controls.find(control=>control.scope==='project')!} slots={turns.flatMap(turn=>turn.slots)} label="Project" refresh={refresh} pendingWork={data.sequences?.some(sequence=>sequence.state==='active'&&sequence.steps.some(step=>step.state!=='complete'))}/>}
    <div className={s.queue}><span>{activeSlots.length} active or unresolved · Local engines take turns on one GPU</span><span>{busy ? 'Saving work…' : 'Changes stay in this project'}</span></div>
  </>;
}

function Picker({state,nodes,current,close,saved,onInspect,stateAction}: {
  state:PickerState; nodes:Material[]; current:Direction; close:()=>void; saved:()=>void;
  onInspect:(id:string)=>void; stateAction:(d:Direction, action:'set_aside'|'restore'|'reuse'|'discard')=>Promise<void>;
}) {
  const [selected,setSelected]=useState(state.direction.selection);
  const [reason,setReason]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const pending=useRef<ReturnType<typeof command> | null>(null);
  const material=nodes.filter(n=>current.node_ids.includes(n.id));
  const groups=[...new Set(material.map(n=>n.engine??'Imported material'))];
  const apply=async()=>{
    setBusy(true);setError('');pending.current??=command();
    try {await result(fetchClient.PUT('/api/crexil/directions/{did}/selection',{params:{path:{did:state.direction.id}},
      body:{...pending.current,revision:state.direction.revision,node_ids:selected,reason}}));saved();close();}
    catch(err){setError(message(err));}finally{setBusy(false);}
  };
  return <section className={s.picker} style={{left:Math.max(16,state.left),top:Math.max(56,state.top)}} aria-label="Choose displayed takes">
    <div className={s.pickerBody}>{groups.map(engine=><div className={s.pickerRow} key={engine}><span className={s.micro}>{engine}</span><div className={s.thumbs}>{material.filter(n=>(n.engine??'Imported material')===engine).map(node=><button key={node.id} className={`${s.thumb} ${selected.includes(node.id)?s.selected:''}`} disabled={!node.available} aria-pressed={selected.includes(node.id)} aria-label={`Display ${label(node)}`} onDoubleClick={()=>onInspect(node.id)} onClick={()=>{pending.current=null;setSelected(previous=>previous.includes(node.id)?previous.filter(id=>id!==node.id):[...previous,node.id]);}}>
      <MaterialThumbnail key={node.id} node={node} decorative/><span>{label(node)}</span></button>)}</div></div>)}
      {material.length===0&&<p>This direction has no outputs yet. Open its starter to continue preparation.</p>}
    </div>
    <label className={s.reason}>Reason <input placeholder="Optional, kept with your choice" value={reason} onChange={e=>{setReason(e.target.value);pending.current=null;}}/></label>
    {error&&<p className={s.error} role="alert">{error}</p>}
    <div className={s.pickerFoot}><span>{selected.length} of {material.length} selected</span><div className={s.spacer}/><button onClick={close}>Esc</button><button autoFocus className={s.primary} disabled={busy} onClick={()=>void apply()}>Apply</button></div>
    <div className={s.pickerActions}><button onClick={()=>void stateAction(current,'reuse').then(close)}>Reuse setup</button>
      <button onClick={()=>void stateAction(current,current.set_aside?'restore':'set_aside').then(close)}>{current.set_aside?'Restore direction':'Set direction aside'}</button>
      {current.state==='draft'&&<button onClick={()=>void stateAction(current,'discard').then(close)}>Discard preparation</button>}</div>
  </section>;
}

function Inspection({node,data,close,navigate,openGroup,openRecord,returnLabel}: {node:Material;data:Snapshot;close:()=>void;navigate:(id:string)=>void;openGroup:(id:string)=>void;openRecord:()=>void;returnLabel:string}) {
  const origin=node.material?.origin;
  const group=data.execution_groups.find(g=>g.outputs.some(o=>o.node_id===node.id));
  return <section className={s.inspection} aria-label="Inspect material" onKeyDown={keepFocus}>
    <header className={s.roomHead}><strong>Inspect · {label(node)}</strong><span className={s.hint}>{node.engine??'Imported material'}</span><button autoFocus onClick={close}>Esc · {returnLabel}</button></header>
    <div className={s.inspectBody}><div className={node.payload==='3d'?s.fullMesh:s.fullImage}>{node.available ? node.payload==='image'?<img src={imageUrl(node.id,true)} alt={label(node)}/>:<MeshInspection key={node.id} node={node}/>:<p>This material is unavailable. Its record remains.</p>}</div>
      <aside className={s.record}><h2>The record</h2>
        {node.prompt&&<><h3>{node.payload==='3d'||!node.engine?'Recorded description':'Instructions used'}</h3><p className={s.recordPrompt}>{node.prompt}</p></>}
        {origin&&<><h3>Source information</h3><p>{origin.kind==='execution'?'Collected from this execution. Its related files retain their own identities.':origin.kind==='prepared_image'?'Prepared from these sources by Crexil. Its pixel transform and source hashes were recorded.':origin.kind==='project_reuse'?'Imported from another project with an independent copy and its recorded origin.':origin.kind==='reported_external'?'Returned external work. Relationships were reported by a participant.':'Imported original. No generation was observed by Crexil.'}</p>{origin.tool&&<p>Tool · {String(origin.tool)}</p>}{origin.explanation&&<p>{String(origin.explanation)}</p>}{origin.kind==='prepared_image'&&<details><summary>Preparation recipe and source hashes</summary><pre>{JSON.stringify(origin.transform,null,2)}</pre></details>}</>}
        {origin?.kind==='project_reuse'&&<><h3>Original project</h3><a href={`${projectHref(String((origin.source as {project_id:string}).project_id))}?material=${encodeURIComponent(String((origin.source as {node_id:string}).node_id))}`}>{String((origin.source as {project_name:string}).project_name)} ›</a><p className={s.hash}>Source {String((origin.source as {node_id:string}).node_id)}</p><details><summary>Recorded source provenance</summary><pre>{JSON.stringify(origin.source_record,null,2)}</pre></details></>}
        {Boolean(node.retained_files?.length)&&<><h3>Retained related files</h3>{node.retained_files!.map(file=><div key={file.id}><strong>{file.filename}</strong><p>{file.role}{file.required?' · Required':''}</p>{file.available?<a href={`/api/crexil/material/${node.id}/files/${file.id}`} download>Download {file.filename}</a>:<p>{file.error??'File unavailable; its record remains.'}</p>}</div>)}</>}
        {node.material&&<dl><dt>Original file</dt><dd>{node.material.filename}</dd>{node.material.width!==null&&node.material.height!==null&&<><dt>Dimensions</dt><dd>{node.material.width} × {node.material.height}</dd></>}<dt>Content hash</dt><dd className={s.hash}>{node.material.sha256}</dd></dl>}
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
