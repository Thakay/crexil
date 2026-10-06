// Inspection reads the accepted request and the take's actual controls. Current
// operation definitions never substitute for missing historical evidence.
import {useEffect} from 'react';
import {$api} from '../../api/client';
import {DecisionCard} from './RecordRoom';
import {isHidden,label,message,money,type Material,type Snapshot} from './api';
import s from './Filmstrip.module.css';

const WITHHELD='Withheld until the reveal';

type RequestRecord={
  actor?:{kind:string;name:string};brief?:string;project_instructions?:string;
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
      <dt>Requested by</dt><dd>{request?.actor?`${request.actor.kind==='agent'?'Agent':'Artist'} · ${request.actor.name}`:'Not recorded'}</dd>
      <dt>Attempt</dt><dd>{node.take} · {new Date(node.created_at).toLocaleString()}</dd>
      <dt>Seed</dt><dd>{WITHHELD}</dd>
      <dt>Charge</dt><dd>Shown after the reveal. The run's totals stay visible.</dd>
    </dl>:node.engine&&<dl><dt>Operation</dt><dd>{operation?.label??node.engine}</dd>
      <dt>Operation revision</dt><dd className={s.hash}>{operation?.revision??'Not recorded'}</dd>
      <dt>Requested by</dt><dd>{request?.actor?`${request.actor.kind==='agent'?'Agent':'Artist'} · ${request.actor.name}`:'Not recorded'}</dd>
      <dt>Attempt</dt><dd>{node.take} · {new Date(node.created_at).toLocaleString()}</dd>
      <dt>Seed</dt><dd>{node.seed??(node.payload==='3d'&&node.verb!=='to_3d'?'Not used by this operation':'Not recorded')}</dd>
      <dt>Confirmed charge</dt><dd>{node.charged_usd===null?'No confirmed charge recorded':money(node.charged_usd)}</dd>
      {node.committed_usd!==null&&<><dt>{typeof operation?.maximum_usd_per_attempt==='number'?'Spending bound':'Reserved at submission'}</dt><dd>{money(node.committed_usd)}</dd></>}
    </dl>}
    {operation?.cost_basis&&<p>{operation.cost_basis}</p>}
    {operation?.limitations?.length? <><h3>Operation limits</h3>{operation.limitations.map(note=><p key={note}>{note}</p>)}</>:null}
    {request?.setup?.prompt&&<><h3>Instructions as prepared</h3><p className={s.recordPrompt}>{request.setup.prompt}</p></>}
    {request?.setup?.instructions&&<><h3>Additional instructions</h3><p>{request.setup.instructions}</p></>}
    {request?.setup?.references?.length? <><h3>Requested source contributions</h3>{request.setup.references.map((ref,index)=>{
      const id=aliases[ref.node_id]??ref.node_id,node=data.nodes.find(n=>n.id===id);
      return <div key={`${ref.node_id}:${index}`} className={s.recordSource}><strong>{index+1}. {node?label(node):ref.node_id}</strong><p>{ref.contribution||'No contribution instruction supplied.'}</p>
        {ref.role&&<p>Role · {ref.role}</p>}{ref.strength!=null&&<p>Strength · {ref.strength}</p>}{node&&<button onClick={()=>navigate(id)}>Inspect source ›</button>}</div>;
    })}</>:null}
    {Boolean(Object.keys(controls).length)&&<><h3>{operation?.applied_controls?'Controls at review':'Saved settings'}</h3><dl>{Object.entries(controls).map(([name,value])=><div key={name}><dt>{name.replaceAll('_',' ')}</dt><dd>{typeof value==='object'?JSON.stringify(value):String(value)}</dd></div>)}</dl><p>The seed above and the submitted operation identify this attempt.</p></>}
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
