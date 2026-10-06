// Operation-first entry creates ordinary project preparation with the selected
// frozen description. Discovery and lost-response confirmation never execute it.
import {useEffect,useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {$api,fetchClient} from '../../api/client';
import {blankPreparation,command,isDefiniteRefusal,message,money,projectHref,result,type Operation,type Preparation} from './api';
import s from './Filmstrip.module.css';

type Pending={project:string;body:components['schemas']['CreateDirection']};
const pendingKey='crexil-operation-preparation';
const names:Record<string,string>={generate:'Generate from words',continue:'Continue an image',combine:'Combine sources',region:'Edit regions',mesh:'Generate 3D',decimate:'Reduce mesh',texture:'Texture mesh',custom:'Custom operation'};
function retained():Pending|null {try{return JSON.parse(sessionStorage.getItem(pendingKey)??'null');}catch{return null;}}

export function PreparedOperations({project,openRegistration}:{project:string;openRegistration:(id:string)=>void}) {
  const query=$api.useQuery('get','/api/crexil/operations');
  const [search,setSearch]=useState('');
  const [pending,setPending]=useState<Pending|null>(retained);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const loadError:unknown=query.error;
  const prepare=async(operation?:Operation,action?:string)=>{
    if(busy||(!pending&&(!operation||!project||!action)))return;
    const request=pending??{project,body:{...command(),title:operation!.label,setup:{...blankPreparation(),action:action as Preparation['action'],attempt_limit:1,
      operations:[{operation_id:operation!.id,revision:operation!.revision,count:1,controls:{}}]}}};
    setBusy(true);setError('');
    try {
      sessionStorage.setItem(pendingKey,JSON.stringify(request));setPending(request);
      const saved=await result(fetchClient.POST('/api/crexil/projects/{wid}/directions',{params:{path:{wid:request.project}},body:request.body}));
      if(retained()?.body.request_id===request.body.request_id)sessionStorage.removeItem(pendingKey);
      if(mounted.current){setPending(null);location.hash=`${projectHref(request.project)}?draft=${saved.id}`;}
    }catch(error){
      if(isDefiniteRefusal(error)&&retained()?.body.request_id===request.body.request_id){sessionStorage.removeItem(pendingKey);if(mounted.current)setPending(null);}
      if(mounted.current)setError(message(error));
    }finally{if(mounted.current)setBusy(false);}
  };
  const operations=(query.data??[]).filter(operation=>`${operation.label} ${operation.actions.map(action=>names[action]??action).join(' ')} ${operation.execution_location}`.toLowerCase().includes(search.toLowerCase()));
  return <section className={s.preparedOperations} aria-label="Prepared operations">
    <span className={s.eyebrow}>Start from an intention or an operation</span><h1>Choose what to prepare.</h1>
    <p>Unavailable and unverified operations stay visible. Preparing saves a direction; review checks its chosen inputs and limits before any execution.</p>
    <div className={s.preparedToolbar}><label>Find an operation<input type="search" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Name, action or runtime"/></label>
      <a href={`#/connections${project?`?project=${project}`:''}`}>Connections and setup ›</a><button disabled={query.isFetching} onClick={()=>void query.refetch()}>Refresh descriptions</button></div>
    {!project&&<p>Choose a project in the sidebar to save a preparation, or <a href={projectHref()}>create a project</a>.</p>}
    {query.isLoading&&<p role="status">Reading operation descriptions…</p>}
    {Boolean(loadError)&&<p role="alert">{message(loadError)}</p>}
    {error&&<p className={s.error} role="alert">{error}</p>}
    {pending&&<p className={s.connectionNotice}>A preparation save is not confirmed. Its project and inputs are retained. <button disabled={busy} onClick={()=>void prepare()}>Confirm same preparation</button></p>}
    <div className={s.preparedList}>{operations.map(operation=><PreparedOperation key={`${operation.id}:${operation.revision}`} operation={operation} project={project} disabled={busy||Boolean(pending)||!project}
      openRegistration={openRegistration} prepare={action=>void prepare(operation,action)}/>)}</div>
    {!query.isLoading&&!query.error&&!operations.length&&<p>No description matches that search. Try its name or action.</p>}
  </section>;
}

function PreparedOperation({operation,project,disabled,prepare,openRegistration}:{operation:Operation;project:string;disabled:boolean;prepare:(action:string)=>void;openRegistration:(id:string)=>void}) {
  const actions=operation.actions.filter(action=>action in names);
  const [action,setAction]=useState(actions.includes('generate')?'generate':actions[0]??'');
  const [report,setReport]=useState<components['schemas']['OperationRequirements']|null>(null);
  const [checking,setChecking]=useState(false);
  const [error,setError]=useState('');
  const check=async()=>{
    setChecking(true);setError('');
    try{setReport(await result(fetchClient.GET('/api/crexil/operation-requirements',{params:{query:{operation_id:operation.id,revision:operation.revision}}})));}
    catch(error){setError(message(error));}finally{setChecking(false);}
  };
  return <article className={s.preparedOperation} aria-label={operation.label}>
    <div className={s.preparedHeading}><h2>{operation.label}</h2><span className={s.micro}>{operation.evidence} · {operation.availability}</span></div>
    <p>{operation.execution_location} · Up to {operation.source_limit} sources · {operation.output_payload} outputs</p>
    <p>{operation.maximum_usd_per_attempt!==null?`Maximum ${money(operation.maximum_usd_per_attempt)} per attempt`:operation.price_usd===null?'Price unresolved':`${money(operation.price_usd)} estimated per attempt; no guaranteed ceiling`}. {operation.cost_basis}.</p>
    {operation.reasons.map(reason=><p className={s.hint} key={reason}>{reason}</p>)}
    <div className={s.actions}><label>Prepare an action<select value={action} onChange={event=>setAction(event.target.value)} disabled={disabled}>{actions.map(value=><option key={value} value={value}>{names[value]}</option>)}</select></label>
      <button disabled={disabled||!action} onClick={()=>prepare(action)}>Prepare in project</button><button disabled={checking} onClick={()=>void check()}>{checking?'Checking…':'Check requirements'}</button></div>
    <details><summary>Controls, route and limits</summary><p className={s.hash}>{operation.data_destination}</p>
      <p>{operation.inputs.map(input=>String(input.label??input.key)).join(' · ')||'This operation uses its described source inputs.'}</p>
      {operation.limitations?.map(note=><p key={note}>{note}</p>)}{(operation.source_roles??[]).length>0&&<p>Source roles: {operation.source_roles?.join(', ')}</p>}
    </details>
    {error&&<p role="alert">{error}</p>}
    {report&&<section className={s.requirements} aria-label={`Requirements for ${operation.label}`}><h3>Requirements · {report.state}</h3>
      <p>Checked {new Date(report.checked_at).toLocaleString()}</p>
      {report.findings.length>0&&<p>{report.findings.length} node interfaces advertised by this runtime.</p>}
      {report.gaps.length>0?<ul>{report.gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul>:<p>{report.state==='inspected'?'No gaps found in this inspection. Execution remains unverified for this request.':'Requirements remain unresolved until request review.'}</p>}
      {report.limitations.map((note,index)=><p className={s.hint} key={index}>{note}</p>)}
      {operation.id.startsWith('custom-')&&<a href={`#/operations?id=${operation.id.slice(7)}${project?`&project=${project}`:''}`} onClick={event=>{event.preventDefault();openRegistration(operation.id.slice(7));}}>Open registration ›</a>}
    </section>}
  </article>;
}
