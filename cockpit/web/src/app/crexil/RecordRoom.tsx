// The record room finds work without changing its display selection. Reasons are
// separate attributed events; revising one retains the earlier author and text.
// Uncertain saves replay the same body, including after returning to this room.
// Material shows its measured facts (law 17); a blind run's takes join the record
// once the run settles, and each run says whether it was blind and who revealed it.
// The decision record exports in one step: the artist's choices, never a dataset,
// and never any audio. The name the record gives this browser's artist is set here.
import {useEffect,useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {$api,fetchClient} from '../../api/client';
import {ActionError,actorLine,artist,command,count,engineLabel,isDefiniteRefusal,label,message,result,setArtistName,type Snapshot} from './api';
import {isWithheld,settled,withheldRuns} from './blind';
import {byGridPosition} from './cells';
import {MaterialThumbnail} from './MaterialThumbnail';
import {runLine} from './faces';
import {mediumOf} from './media';
import s from './Filmstrip.module.css';

export type Decision=components['schemas']['DecisionView'];
type Assessment=components['schemas']['RecordAssessment'];
export type RecordScope={node_id?:string;direction_id?:string;turn_id?:string};
const kindName={selection:'Display choice',sequence_choice:'Sequence choice',assessment:'Assessment'};
const storageKey=(wid:string)=>`crexil-assessment:${wid}`;
type SavedAssessment={body:Assessment|null;reason:string;previous?:Decision;nodeIds:string[];directionId?:string;turnId?:string;independent?:boolean};
function savedAssessment(wid:string):SavedAssessment|null {
  try{
    const value=JSON.parse(sessionStorage.getItem(storageKey(wid))??'null');
    return value?.request_id?{body:value,reason:value.reason,nodeIds:value.node_ids??[],directionId:value.direction_id,turnId:value.turn_id}:value;
  }catch{return null;}
}
function pendingSave(wid:string):Assessment|null {
  return savedAssessment(wid)?.body??null;
}

export function RecordRoom({data,scope={},close,inspect,openDirection,openRun,returnLabel='Project',secondary=false}:{
  data:Snapshot;scope?:RecordScope;close:()=>void;inspect:(id:string)=>void;
  openDirection:(id:string)=>void;openRun:(id:string)=>void;
  returnLabel?:string;
  secondary?:boolean;
}) {
  const [search,setSearch]=useState('');
  const [tab,setTab]=useState<'decisions'|'material'|'directions'|'attempts'>('decisions');
  const [before,setBefore]=useState<number|undefined>();
  const [filtered,setFiltered]=useState(scope);
  const [otherDraft]=useState(()=>secondary&&Boolean(savedAssessment(data.project.id)));
  const [editing,setEditing]=useState<{previous?:Decision}|null>(()=>!secondary&&savedAssessment(data.project.id)?{previous:savedAssessment(data.project.id)?.previous}:null);
  const scopeNodes=filtered.node_id?[filtered.node_id]:[];
  const query=$api.useQuery('get','/api/crexil/projects/{wid}/record',{params:{path:{wid:data.project.id},
    query:{q:search,before,node_id:filtered.node_id,direction_id:filtered.direction_id,turn_id:filtered.turn_id}}});
  useEffect(()=>{void query.refetch();},[data.event_seq]);
  const record=query.data;
  const records=record?.decisions??[];
  const withheld=withheldRuns(data);
  const material=byGridPosition(data.nodes).filter(node=>record?.material_ids.includes(node.id)&&!isWithheld(node,withheld)&&(!filtered.node_id||node.id===filtered.node_id)&&(!filtered.direction_id||data.directions.find(d=>d.id===filtered.direction_id)?.node_ids.includes(node.id))&&(!filtered.turn_id||node.turn_id===filtered.turn_id));
  const [artistName,setName]=useState(()=>artist().name);
  const blindLine=(turnId:string)=>{
    const run=data.blind_runs?.find(item=>item.turn_id===turnId);
    if(!run)return null;
    return run.revealed?`Blind run · revealed${run.revealed_by?` by ${run.revealed_by.name}`:''}${run.revealed_at?` · ${new Date(run.revealed_at).toLocaleString()}`:''}`
      :'Blind run · engines hidden until the reveal';
  };
  const directions=data.directions.filter(direction=>record?.direction_ids.includes(direction.id)&&(!filtered.direction_id||direction.id===filtered.direction_id)&&(!filtered.node_id||direction.node_ids.includes(filtered.node_id))&&(!filtered.turn_id||direction.turn_ids.includes(filtered.turn_id)));
  const turns=data.turns.filter(turn=>record?.turn_ids.includes(turn.id)&&(!filtered.turn_id||turn.id===filtered.turn_id)&&(!filtered.direction_id||data.directions.find(d=>d.id===filtered.direction_id)?.turn_ids.includes(turn.id))&&(!filtered.node_id||data.nodes.find(n=>n.id===filtered.node_id)?.turn_id===turn.id));
  const scoped=Object.values(filtered).some(Boolean);
  return <section className={s.recordRoom} aria-label="Project record" onKeyDown={event=>{
    if(event.key==='Escape'&&editing){event.preventDefault();event.stopPropagation();if(!pendingSave(data.project.id)){sessionStorage.removeItem(storageKey(data.project.id));setEditing(null);}}
  }}>
    <header className={s.roomHead}><strong>Record</strong><span className={s.hint}>Find material, requests and the people behind each choice.</span><button onClick={close}>Esc · {returnLabel}</button></header>
    <div className={s.recordToolbar}>
      <label>Find in this project<input autoFocus type="search" value={search} maxLength={500} placeholder="Instructions, engine, material or reason" onChange={e=>{setSearch(e.target.value);setBefore(undefined);}}/></label>
      <div className={s.recordTabs}>{(['decisions','material','directions','attempts'] as const).map(value=><button key={value} aria-pressed={tab===value} onClick={()=>setTab(value)}>{value==='directions'?'Directions':value==='attempts'?'Runs':value==='material'?'Material':'Decisions'}</button>)}</div>
      {scoped&&<button onClick={()=>{setFiltered({});setBefore(undefined);}}>Show whole project</button>}
      <div className={s.recordExport}>
        <a className={s.download} href={`/api/crexil/projects/${encodeURIComponent(data.project.id)}/decisions.jsonl`} download>Export decision record</a>
        <label>Recorded as<input value={artistName} maxLength={120} placeholder="Artist"
          onChange={e=>{setName(e.target.value);setArtistName(e.target.value);}}/></label>
      </div>
    </div>
    <p className={`${s.hint} ${s.recordExportNote}`}>The export holds one line per choice and assessment: the brief, the candidates, the choice, its reason and who made it. It holds no audio and is not a dataset.</p>
    <div className={s.recordContent}>
      {scoped&&<p className={s.hint}>Showing {filtered.node_id?label(data.nodes.find(n=>n.id===filtered.node_id)!):filtered.direction_id?data.directions.find(d=>d.id===filtered.direction_id)?.title:`run ${data.turns.find(t=>t.id===filtered.turn_id)?.idx}`}.</p>}
      {query.isLoading&&<p role="status">Reading the project record…</p>}
      {query.error&&<p role="alert" className={s.error}>{message(query.error)}<button onClick={()=>void query.refetch()}>Try again</button></p>}
      {otherDraft&&<p className={s.hint}>An unfinished reason is kept in the main record. Return there to finish it before adding another.</p>}
      {editing&&<div hidden={tab!=='decisions'}><AssessmentEditor key={editing.previous?.event_id??'new'} wid={data.project.id} previous={editing.previous}
        nodeIds={scopeNodes} directionId={filtered.direction_id} turnId={filtered.turn_id}
        close={()=>{sessionStorage.removeItem(storageKey(data.project.id));setEditing(null);}} saved={()=>{setEditing(null);void query.refetch();}}/></div>}
      {tab==='decisions'&&<>
        {scoped&&!editing&&!otherDraft&&<button onClick={()=>setEditing({})}>Add assessment</button>}
        {records.map(item=><DecisionCard key={item.event_id} item={item} data={data} inspect={inspect} openDirection={openDirection} openRun={openRun}
          revise={editing||otherDraft?undefined:()=>setEditing({previous:item})}/>)}
        {record&&!records.length&&<div className={s.recordEmpty}><h2>{search?'No decisions match.':'No decisions recorded here yet.'}</h2><p>Display choices keep their supplied reasons. Open material or a direction to add an assessment.</p></div>}
        <div className={s.actions}>{before&&<button onClick={()=>setBefore(undefined)}>Newest decisions</button>}{record?.next_before&&<button onClick={()=>setBefore(record.next_before!)}>Older decisions</button>}</div>
      </>}
      {tab==='material'&&<><div className={s.recordMaterials}>{material.map(node=><button key={node.id} className={s.recordMaterial} onClick={()=>inspect(node.id)}>
        <MaterialThumbnail node={node} decorative/><strong>{label(node)}</strong><span>{engineLabel(node,'source')}</span>
        {mediumOf(node.payload).facts?.(node).map(([name,value])=><span key={name}>{name} · {value}</span>)}
        <span>{!node.available?'Material unavailable':node.set_aside?'Set aside':'Available'} · Inspect ›</span>
      </button>)}</div>{record&&!material.length&&<p>No material matches this search.</p>}</>}
      {tab==='directions'&&<>{directions.map(direction=><article key={direction.id} className={s.decision}>
        <span className={s.micro}>T{direction.row_idx} · {direction.state}{direction.set_aside?' · Set aside':''}</span><h2>{direction.title}</h2><p>{String(direction.setup.prompt??'')}</p>
        <div className={s.actions}><button onClick={()=>openDirection(direction.id)}>{direction.state==='discarded'?'Reuse preparation':'Open direction'} ›</button><button onClick={()=>{setFiltered({direction_id:direction.id});setBefore(undefined);setTab('decisions');}}>Decisions ›</button></div>
      </article>)}{record&&!directions.length&&<p>No directions match this search.</p>}</>}
      {tab==='attempts'&&<>{turns.map(turn=><article key={turn.id} className={s.decision}>
        <span className={s.micro}>Run {turn.idx} · {new Date(turn.created_at).toLocaleString()}</span><p>{turn.prompt}</p>
        <p>{withheld.has(turn.id)?`In progress · ${settled(turn).done} of ${count(settled(turn).total,'attempt')} done`:runLine(turn.slots)}</p>{blindLine(turn.id)&&<p className={s.hint}>{blindLine(turn.id)}</p>}<div className={s.actions}><button onClick={()=>openRun(turn.id)}>Open execution grid ›</button><button onClick={()=>{setFiltered({turn_id:turn.id});setBefore(undefined);setTab('decisions');}}>Decisions ›</button></div>
      </article>)}{record&&!turns.length&&<p>No runs match this search.</p>}</>}
    </div>
  </section>;
}

export function DecisionCard({item,data,inspect,openDirection,openRun,revise}:{item:Decision;data:Snapshot;inspect:(id:string)=>void;openDirection?:(id:string)=>void;openRun?:(id:string)=>void;revise?:()=>void}) {
  const direction=data.directions.find(d=>d.id===item.direction_id);
  return <article className={s.decision} aria-label={`${kindName[item.kind]} by ${item.actor?.name??'unrecorded participant'}`}>
    <div className={s.decisionMeta}><strong>{kindName[item.kind]}</strong><span>{actorLine(item.actor)}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></div>
    <p className={s.decisionReason}>{item.reason||'No reason supplied.'}</p>
    <div className={s.decisionLinks}>{item.node_ids.map(id=>{const node=data.nodes.find(n=>n.id===id);return node?<button key={id} onClick={()=>inspect(id)}>{label(node)}{!node.available?' · unavailable':''} ›</button>:<span key={id}>Material {id} · unavailable</span>;})}
      {direction&&openDirection&&<button onClick={()=>openDirection(direction.id)}>T{direction.row_idx} · {direction.title} ›</button>}
      {item.turn_id&&openRun&&<button onClick={()=>openRun(item.turn_id!)}>Execution grid ›</button>}
    </div>
    <div className={s.decisionFoot}>{item.previous_id&&<span>Reason revised; the earlier version is retained.</span>}{item.revised_by&&<span>A later reason is recorded.</span>}{item.historical&&<span>Restored history</span>}{!item.revised_by&&revise&&<button onClick={revise}>Revise reason</button>}</div>
  </article>;
}

function AssessmentEditor({wid,previous:offeredPrevious,nodeIds:offeredNodes,directionId:offeredDirection,turnId:offeredTurn,close,saved}:{wid:string;previous?:Decision;nodeIds:string[];directionId?:string;turnId?:string;close:()=>void;saved:()=>void}) {
  const initial=useRef(savedAssessment(wid));
  const previous=offeredPrevious??initial.current?.previous;
  const nodeIds=initial.current?.nodeIds??offeredNodes,directionId=initial.current?.directionId??offeredDirection,turnId=initial.current?.turnId??offeredTurn;
  const [pending,setPending]=useState<Assessment|null>(initial.current?.body??null);
  const [reason,setReason]=useState(initial.current?.reason??previous?.reason??'');
  const [independent,setIndependent]=useState(initial.current?.independent??false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [conflict,setConflict]=useState(false);
  const input=useRef<HTMLTextAreaElement>(null);
  const retain=(value:string,body:Assessment|null,separate=independent)=>sessionStorage.setItem(storageKey(wid),JSON.stringify({body,reason:value,previous,nodeIds,directionId,turnId,independent:separate} satisfies SavedAssessment));
  const save=async()=>{
    const body=pending??{...command(),reason,...(previous&&!independent?{previous_id:previous.event_id}:{node_ids:previous?.node_ids??nodeIds,direction_id:previous?.direction_id??directionId,turn_id:previous?.turn_id??turnId})};
    setBusy(true);setError('');setPending(body);retain(reason,body);
    try{await result(fetchClient.POST('/api/crexil/projects/{wid}/assessments',{params:{path:{wid}},body}));sessionStorage.removeItem(storageKey(wid));setPending(null);saved();}
    catch(err){setError(message(err));if(isDefiniteRefusal(err)){setPending(null);retain(reason,null);setConflict(err instanceof ActionError&&err.status===409);}}
    finally{setBusy(false);}
  };
  return <form className={s.assessmentEditor} onSubmit={e=>{e.preventDefault();void save();}}>
    <h3>{previous&&!independent?'Revise the reason':'Add an assessment'}</h3>
    <p className={s.hint}>This records your judgment. Earlier authorship, display choices and execution results stay in the record.</p>
    <label>Your reason<textarea ref={input} autoFocus value={reason} disabled={Boolean(pending)||busy} maxLength={4000} onChange={e=>{setReason(e.target.value);retain(e.target.value,null);}}/></label>
    {error&&<p role="alert" className={s.error}>{error}</p>}
    {pending&&<p role="status">Confirming the same saved request. Its text is kept here across reload.</p>}
    <div className={s.actions}><button type="button" disabled={busy||Boolean(pending)} onClick={close}>Cancel</button>
      {conflict&&<button type="button" onClick={()=>{setIndependent(true);setConflict(false);setError('');retain(reason,null,true);input.current?.focus();}}>Keep as a separate assessment</button>}
      <button className={s.primary} disabled={busy||conflict||!reason.trim()}>{busy?'Saving…':pending?'Confirm same save':'Save reason'}</button></div>
  </form>;
}
