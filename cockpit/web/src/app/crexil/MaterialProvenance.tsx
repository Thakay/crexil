// Inspection reads the accepted request and the take's actual controls. Current
// operation definitions never substitute for missing historical evidence. A blind
// take withholds its operation, seed, charge and landing time until the reveal. A
// generated take's words show as typed and as submitted (`TakeWords`).
import {useEffect} from 'react';
import {$api} from '../../api/client';
import {DecisionCard} from './RecordRoom';
import {actionView} from './actions';
import {actorLine,engineLabel,isHidden,label,message,money,type Actor,type Material,type Snapshot} from './api';
import {recordedWords} from './family';
import s from './Filmstrip.module.css';

const WITHHELD='Withheld until the reveal';

/** A control's value as the record column says it: a seed policy in words, a switch as
 *  yes or no, anything else as recorded. */
export function controlValue(value:unknown):string{
  const seed=value as {mode?:unknown;value?:unknown}|null;
  if(seed&&typeof seed==='object'&&!Array.isArray(seed)&&typeof seed.mode==='string'){
    if(seed.mode==='random')return 'Random each time';
    if(seed.mode==='fixed')return `A fixed sequence from ${String(seed.value)}`;
    if(seed.mode==='locked')return `${String(seed.value)} for every attempt`;
  }
  if(typeof value==='boolean')return value?'Yes':'No';
  return typeof value==='object'?JSON.stringify(value):String(value);
}

type RequestRecord={
  actor?:Actor;brief?:string;project_instructions?:string;
  setup?:{prompt?:string;instructions?:string;references?:{node_id:string;contribution?:string;role?:string|null;strength?:number|null}[];spending_policy?:string};
  sources?:{node_id:string;sha256?:string}[];
  operation?:{label:string;revision:string;maximum_usd_per_attempt?:number|null;cost_basis?:string;limitations?:string[];applied_controls?:Record<string,unknown>};
};

export function MaterialProvenance({node,data,navigate,openRecord}:{node:Material;data:Snapshot;navigate:(id:string)=>void;openRecord:()=>void}) {
  const query=$api.useQuery('get','/api/crexil/material/{nid}/record',{params:{path:{nid:node.id}}});
  const decisions=$api.useQuery('get','/api/crexil/projects/{wid}/record',{params:{path:{wid:data.project.id},query:{node_id:node.id,limit:10}}});
  useEffect(()=>{void decisions.refetch();},[data.event_seq,node.id]);
  const recorded=query.data;
  const request=recorded?.request as RequestRecord|null|undefined;
  const operation=request?.operation;
  const aliases=(data.restoration?.identities as {nodes?:Record<string,string>}|undefined)?.nodes??{};
  // A blind run's take withholds its engine, operation, seed and charge until the reveal.
  const hidden=isHidden(node);
  const controls=operation?.applied_controls??Object.fromEntries(Object.entries(recorded?.controls??{}).filter(([key])=>!['crexil','crexil_restore'].includes(key)));
  return <>
    {query.isLoading&&<p role="status">Reading the accepted request…</p>}
    {query.error&&<p role="alert">{message(query.error)}<button onClick={()=>void query.refetch()}>Read request again</button></p>}
    {hidden?<dl><dt>Operation</dt><dd>{WITHHELD}</dd>
      <dt>Requested by</dt><dd>{actorLine(request?.actor)}</dd>
      <dt>Attempt</dt><dd>{node.take} · landing time {WITHHELD.toLowerCase()}</dd>
      <dt>Seed</dt><dd>{WITHHELD}</dd>
      <dt>Charge</dt><dd>Shown after the reveal. The run's totals stay visible.</dd>
    </dl>:node.engine&&<dl><dt>Operation</dt><dd>{operation?.label??engineLabel(node,'source')}</dd>
      <dt>Operation revision</dt><dd className={s.hash}>{operation?.revision??'Not recorded'}</dd>
      <dt>Requested by</dt><dd>{actorLine(request?.actor)}</dd>
      <dt>Attempt</dt><dd>{node.take} · {new Date(node.created_at).toLocaleString()}</dd>
      <dt>Seed</dt><dd>{node.seed??(node.payload==='3d'&&node.verb!=='to_3d'?'Not used by this operation':'Not recorded')}</dd>
      <dt>Confirmed charge</dt><dd>{node.charged_usd===null?'No confirmed charge recorded':money(node.charged_usd)}</dd>
      {node.committed_usd!==null&&<><dt>{typeof operation?.maximum_usd_per_attempt==='number'?'Spending bound':'Reserved at submission'}</dt><dd>{money(node.committed_usd)}</dd></>}
    </dl>}
    {operation?.cost_basis&&<p>{operation.cost_basis}</p>}
    {operation?.limitations?.length? <><h3>Operation limits</h3>{operation.limitations.map(note=><p key={note}>{note}</p>)}</>:null}
    {request?.setup?.instructions&&<><h3>Additional instructions</h3><p>{request.setup.instructions}</p></>}
    {request?.setup?.references?.length? <><h3>Requested source contributions</h3>{request.setup.references.map((ref,index)=>{
      const id=aliases[ref.node_id]??ref.node_id,node=data.nodes.find(n=>n.id===id);
      return <div key={`${ref.node_id}:${index}`} className={s.recordSource}><strong>{index+1}. {node?label(node):ref.node_id}</strong><p>{ref.contribution||'No contribution instruction supplied.'}</p>
        {ref.role&&<p>Role · {ref.role}</p>}{ref.strength!=null&&<p>Strength · {ref.strength}</p>}{node&&<button onClick={()=>navigate(id)}>Inspect source ›</button>}</div>;
    })}</>:null}
    {Boolean(Object.keys(controls).length)&&<><h3>{operation?.applied_controls?'Controls at review':'Saved settings'}</h3><dl>{Object.entries(controls).map(([name,value])=><div key={name}><dt>{name.replaceAll('_',' ')}</dt><dd>{controlValue(value)}</dd></div>)}</dl><p>The seed above and the submitted operation identify this attempt.</p></>}
    {(request?.brief||request?.project_instructions)&&<details><summary>Project instructions at acceptance</summary>{request.brief&&<p>{request.brief}</p>}{request.project_instructions&&<p>{request.project_instructions}</p>}</details>}
    {recorded?.regions&&<details><summary>Recorded edit regions and protected area</summary><pre>{JSON.stringify(recorded.regions,null,2)}</pre></details>}
    {recorded?.submitted&&<details><summary>Submitted workflow or operation</summary><pre>{JSON.stringify(recorded.submitted,null,2)}</pre></details>}
    {request&&<details><summary>Full accepted request and source hashes</summary><pre>{JSON.stringify(request,null,2)}</pre></details>}
    <h3>Decisions about this material</h3>
    {decisions.error&&<p role="alert">{message(decisions.error)}<button onClick={()=>void decisions.refetch()}>Read decisions again</button></p>}
    {decisions.data?.decisions.slice(0,3).map(item=><DecisionCard key={item.event_id} item={item} data={data} inspect={navigate}/>)}
    {decisions.data&&!decisions.data.decisions.length&&<p>No assessment or display choice is recorded for this material.</p>}
    <button onClick={openRecord}>Decisions and assessments ›</button>
  </>;
}

/** A generated take's words: as typed in the preparation its run accepted and as submitted
 *  in its frozen request, said once when they match. Until the record is read, or while a
 *  blind run withholds it, the take's own words show without either claim. */
export function TakeWords({node}:{node:Material}) {
  const query=$api.useQuery('get','/api/crexil/material/{nid}/record',{params:{path:{nid:node.id}}});
  const name=actionView(node.verb)?.words??'Prompt';
  const {typed,submitted}=recordedWords(node,query.data);
  if(typed!==null&&submitted!==null&&typed.trim()===submitted.trim())
    return <><h3>{name}</h3><p className={s.recordPrompt}>{submitted}</p><p className={s.hint}>As typed and as submitted.</p></>;
  if(typed!==null||submitted!==null)return <>
    {typed!==null&&<><h3>{name} as typed</h3><p className={s.recordPrompt}>{typed}</p></>}
    {submitted!==null&&<><h3>{name} as submitted</h3><p className={s.recordPrompt}>{submitted}</p></>}
  </>;
  return node.prompt?<><h3>{name}</h3><p className={s.recordPrompt}>{node.prompt}</p></>:null;
}
