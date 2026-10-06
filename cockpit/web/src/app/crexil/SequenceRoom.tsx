import { MaterialThumbnail } from './MaterialThumbnail';
// Sequences use the same saved preparations and commands as individual work.
// A decision selects exact retained material. Uncertain command delivery keeps
// its immutable request until the server confirms or definitively refuses it.
import {useEffect,useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {command,isDefiniteRefusal,label,message,money,result,type Direction,type Snapshot} from './api';
import {ExecutionControls} from './ExecutionControls';
import {useEscapeLayer} from './keys';
import s from './Filmstrip.module.css';

type Sequence=components['schemas']['SequenceView'];
type Step=components['schemas']['SequenceStepView'];
type Definition=components['schemas']['SequenceStep'];
type Dependency=components['schemas']['SequenceDependency'];
type Limits=components['schemas']['ProjectLimits'];
type Review=components['schemas']['SequenceReview'];
type Send=(name:string,work:()=>Promise<Sequence>)=>void;
const emptyLimits:Limits={attempt_limit:16,spending_limit_usd:0,spending_policy:'hard_cap'};
const status=(sequence:Sequence)=>sequence.state==='active'&&sequence.steps.every(step=>step.state==='complete')?'complete':sequence.state;

export function SequenceRoom({data,refresh,close,edit,inspect,openRun,initialId,inactive=false}:{
  data:Snapshot;refresh:()=>void;close:()=>void;edit:(id:string)=>void;inspect:(id:string)=>void;openRun:(id:string,sid:string)=>void;initialId?:string|null;inactive?:boolean;
}){
  const [selected,setSelected]=useState<string|null>(initialId??data.sequences?.[0]?.id??null);
  const [creating,setCreating]=useState(!data.sequences?.length);
  const [accepted,setAccepted]=useState<Sequence|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const pending=useRef<{name:string;work:()=>Promise<Sequence>}|null>(null);
  const sequences=[...(data.sequences??[])];
  if(accepted){const index=sequences.findIndex(item=>item.id===accepted.id);if(index<0)sequences.push(accepted);else if(sequences[index].revision<accepted.revision)sequences[index]=accepted;}
  const current=sequences.find(item=>item.id===selected);
  const send:Send=(name,work)=>{
    if(busy)return;
    pending.current??={name,work};setBusy(true);setError('');
    void pending.current.work().then(saved=>{
      pending.current=null;setAccepted(saved);setSelected(saved.id);setCreating(false);refresh();
    }).catch(error=>{
      if(isDefiniteRefusal(error))pending.current=null;
      setError(message(error));refresh();
    }).finally(()=>setBusy(false));
  };
  const root=useRef<HTMLElement>(null);
  const locked=busy||Boolean(pending.current)||inactive;
  useEffect(()=>{document.getElementById('sequence-room-title')?.focus();},[]);
  // An unconfirmed command keeps the room open: Escape waits for its retry.
  useEscapeLayer(()=>Boolean(pending.current));
  useEffect(()=>{
    const origin=location.hash;
    const leave=(event:BeforeUnloadEvent)=>{if(pending.current){event.preventDefault();event.returnValue='';}};
    const click=(event:MouseEvent)=>{if(pending.current&&(!root.current?.contains(event.target as Node)||(event.target as HTMLElement).closest('a[href]'))){event.preventDefault();event.stopImmediatePropagation();}};
    const hash=(event:HashChangeEvent)=>{if(pending.current&&location.hash!==origin){event.stopImmediatePropagation();history.replaceState(null,'',origin);}};
    window.addEventListener('beforeunload',leave);window.addEventListener('click',click,true);window.addEventListener('hashchange',hash,true);
    return()=>{window.removeEventListener('beforeunload',leave);window.removeEventListener('click',click,true);window.removeEventListener('hashchange',hash,true);};
  },[]);
  return <section ref={root} inert={inactive} className={s.sequenceRoom} aria-label="Sequences">
    <header className={s.roomHead}><strong id="sequence-room-title" tabIndex={-1}>Sequences</strong><span className={s.hint}>Saved steps, explicit dependencies, retained decisions.</span><button disabled={locked} onClick={close}>Esc · Project</button></header>
    {error&&<p role="alert" className={s.error}>{error}{pending.current&&<button disabled={busy} onClick={()=>send(pending.current!.name,pending.current!.work)}>Retry same request</button>}</p>}
    {pending.current&&!busy&&<p className={s.sequenceNotice}>Delivery is unconfirmed. Retry this same {pending.current.name.toLowerCase()} before changing the sequence.</p>}
    <div className={s.sequenceLayout}>
      <nav className={s.sequenceNav} aria-label="Project sequences">
        <button disabled={locked} className={creating?s.primary:''} onClick={()=>setCreating(true)}>+ New sequence</button>
        {sequences.map(item=><button disabled={locked} key={item.id} aria-current={!creating&&item.id===selected?'page':undefined} onClick={()=>{setSelected(item.id);setCreating(false);setError('');}}>
          <strong>{item.title}</strong><span>{status(item)} · {item.steps.length} steps</span>
        </button>)}
      </nav>
      <main className={s.sequenceMain}>
        <fieldset className={s.sequenceFields} disabled={locked}>
          {creating?<CreateSequence data={data} send={send} edit={edit}/>:current?<SequenceDetails key={current.id} sequence={current} data={data} refresh={refresh} send={send} edit={edit} inspect={inspect} openRun={id=>openRun(id,current.id)}/>:<p>Select a sequence or create one from saved preparations.</p>}
        </fieldset>
      </main>
    </div>
  </section>;
}

function LimitFields({value,change}:{value:Limits;change:(limits:Limits)=>void}){
  return <div className={s.sequenceLimits}>
    <label>Sequence attempt limit<input aria-label="Sequence attempt limit" type="number" min={0} step={1} value={value.attempt_limit??''} placeholder="No sequence limit" onChange={e=>change({...value,attempt_limit:e.target.value===''?null:Number(e.target.value)})}/></label>
    <label>Sequence spending limit · USD<input aria-label="Sequence spending limit · USD" type="number" min={0} step="0.01" value={value.spending_limit_usd??''} placeholder="No sequence limit" onChange={e=>change({...value,spending_limit_usd:e.target.value===''?null:Number(e.target.value)})}/></label>
    <label>Sequence spending policy<select aria-label="Sequence spending policy" value={value.spending_policy} onChange={e=>change({...value,spending_policy:e.target.value as Limits['spending_policy']})}><option value="hard_cap">Require a hard cap</option><option value="published_tariff">Budget from published tariffs</option></select></label>
    <p>Engine attempts include retries and unfinished requests. Image preparation is free. Project and individual run limits also apply.{value.spending_policy==='published_tariff'?' Published tariffs reserve a budget; they cannot guarantee the provider’s final charge.':''}</p>
  </div>;
}

function Dependencies({steps,currentKey,dependencies,inputMode,change}:{
  steps:{key:string;title:string}[];currentKey:string;dependencies:Dependency[];inputMode:string;change:(deps:Dependency[],mode:'together'|'each')=>void;
}){
  const update=(next:Dependency[])=>change(next,inputMode==='each'&&next.length===1?'each':'together');
  return <div className={s.sequenceDependencies}>
    <h3>Wait for material from</h3>
    {steps.filter(item=>item.key!==currentKey).map(item=>{
      const dep=dependencies.find(value=>value.step_key===item.key);
      return <div key={item.key} className={s.sequenceDependency}>
        <label className={s.sequenceCheck}><input type="checkbox" checked={Boolean(dep)} onChange={e=>update(e.target.checked?[...dependencies,{step_key:item.key,selection:'all_successful',contribution:'',placement:'after_fixed'}]:dependencies.filter(value=>value.step_key!==item.key))}/>{item.title}</label>
        {dep&&<><label>Material rule for {item.title}<select aria-label={`Material rule for ${item.title}`} value={dep.selection} onChange={e=>update(dependencies.map(value=>value===dep?{...value,selection:e.target.value as Dependency['selection']}:value))}><option value="all_successful">All successful outputs</option><option value="chosen">Wait for a participant’s choice</option></select></label>
          <label>Input position for {item.title}<select aria-label={`Input position for ${item.title}`} value={dep.placement??'after_fixed'} onChange={e=>update(dependencies.map(value=>value===dep?{...value,placement:e.target.value as Dependency['placement']}:value))}><option value="after_fixed">After saved sources</option><option value="before_fixed">Before saved sources</option></select></label>
          <label>Contribution from {item.title}<input aria-label={`Contribution from ${item.title}`} value={dep.contribution} placeholder="What should carry forward?" onChange={e=>update(dependencies.map(value=>value===dep?{...value,contribution:e.target.value}:value))}/></label></>}
      </div>;
    })}
    {!dependencies.length?<p className={s.hint}>This step starts from its saved sources as soon as the sequence is authorized.</p>:<>
      <label>Use these sources<select aria-label="Use these sources" value={inputMode} onChange={e=>change(dependencies,e.target.value as 'together'|'each')}><option value="together">Together in one run</option><option value="each" disabled={dependencies.length!==1}>One run per source</option></select></label>
      <p className={s.hint}>All successful outputs waits for the upstream step to settle. A participant’s choice can use retained partial results. Each step also keeps its saved sources. Input position is explicit: for texturing with a saved appearance image, place the generated mesh before saved sources.</p>
    </>}
  </div>;
}

function CreateSequence({data,send,edit}:{data:Snapshot;send:Send;edit:(id:string)=>void}){
  const [title,setTitle]=useState('');
  const [instructions,setInstructions]=useState('');
  const [limits,setLimits]=useState<Limits>(emptyLimits);
  const [steps,setSteps]=useState<Definition[]>([]);
  const [next,setNext]=useState('');
  const counter=useRef(1);
  const available=data.directions.filter(item=>item.state==='draft'&&!item.set_aside&&!item.turn_ids.length&&!item.sequence_id&&!steps.some(step=>step.direction_id===item.id));
  const names=steps.map(step=>({key:step.key,title:data.directions.find(item=>item.id===step.direction_id)?.title??step.key}));
  return <form onSubmit={e=>{
    e.preventDefault();const body={...command(),title,instructions,limits,steps:steps.map(step=>({...step,revision:data.directions.find(item=>item.id===step.direction_id)!.revision}))};
    send('Create sequence',()=>result(fetchClient.POST('/api/crexil/projects/{wid}/sequences',{params:{path:{wid:data.project.id}},body})));
  }}>
    <span className={s.eyebrow}>A preparation for several steps</span><h1>Describe what follows.</h1>
    <p className={s.hint}>Add saved, unsubmitted preparations from this project. Each step keeps its own prompt, operations and controls. Saving a sequence does not start it.</p>
    <div className={s.sequenceIntro}><label>Sequence name<input aria-label="Sequence name" required maxLength={200} value={title} onChange={e=>setTitle(e.target.value)} placeholder="Explore, choose, refine"/></label><label>Instructions for the next participant<textarea aria-label="Instructions for the next participant" value={instructions} onChange={e=>setInstructions(e.target.value)} placeholder="What matters as this work continues?"/></label></div>
    <LimitFields value={limits} change={setLimits}/>
    {steps.map((step,index)=><section key={step.key} className={s.sequenceStep} aria-label={`Planned step ${index+1}`}>
      <header><span className={s.sequenceNumber}>{index+1}</span><h2>{names[index].title}</h2><button type="button" onClick={()=>edit(step.direction_id)}>Edit preparation</button><button type="button" onClick={()=>setSteps(previous=>previous.filter(item=>item.key!==step.key).map(item=>({...item,dependencies:item.dependencies?.filter(dep=>dep.step_key!==step.key),input_mode:'together'})))}>Remove step</button></header>
      <Dependencies steps={names} currentKey={step.key} dependencies={step.dependencies??[]} inputMode={step.input_mode} change={(deps,mode)=>setSteps(previous=>previous.map(item=>item.key===step.key?{...item,dependencies:deps,input_mode:mode}:item))}/>
    </section>)}
    <div className={s.sequenceAdd}><label>Saved preparation<select aria-label="Saved preparation" value={next} onChange={e=>setNext(e.target.value)}><option value="">Choose a preparation</option>{available.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label><button type="button" disabled={!next||steps.length>=32} onClick={()=>{const direction=data.directions.find(item=>item.id===next)!;setSteps(previous=>[...previous,{key:`step-${counter.current++}`,direction_id:next,revision:direction.revision,input_mode:'together',dependencies:[]}]);setNext('');}}>Add step</button></div>
    {!available.length&&<p className={s.hint}>Create or reuse an unsubmitted preparation in the project to add another step.</p>}
    <div className={s.sequenceActions}><span>{steps.length} steps · Review comes next</span><button className={s.primary} disabled={!title.trim()||!steps.length}>Save sequence</button></div>
  </form>;
}

function SequenceDetails({sequence,data,refresh,send,edit,inspect,openRun}:{sequence:Sequence;data:Snapshot;refresh:()=>void;send:Send;edit:(id:string)=>void;inspect:(id:string)=>void;openRun:(id:string)=>void}){
  const [review,setReview]=useState<Review|null>(null);
  const [checking,setChecking]=useState(false);
  const [error,setError]=useState('');
  const [editing,setEditing]=useState(false);
  const [dependencyEdit,setDependencyEdit]=useState<string|null>(null);
  const fingerprint=JSON.stringify([sequence.revision,sequence.steps.map(step=>data.directions.find(item=>item.id===step.direction_id)?.revision),data.project.revision]);
  useEffect(()=>{setReview(null);},[fingerprint]);
  const control=data.execution_controls?.find(item=>item.scope==='sequence'&&item.target_id===sequence.id);
  const turns=new Set(sequence.steps.flatMap(step=>step.runs.map(run=>run.turn_id)));
  const accounting=sequence.accounting;
  return <>
    <span className={s.eyebrow}>{status(sequence)} · Created by {sequence.actor.name}</span><h1>{sequence.title}</h1>
    {sequence.instructions&&<p className={s.sequenceInstructions}>{sequence.instructions}</p>}
    <p className={s.hint}>{String(accounting.attempts??0)} of {sequence.limits.attempt_limit??'unlimited'} engine attempts used or reserved · {money(Number(accounting.charged_usd??0))} charged · {money(Number(accounting.reserved_usd??0))} reserved · Spending limit {sequence.limits.spending_limit_usd===null?'unset':money(sequence.limits.spending_limit_usd??0)}</p>
    {Number(accounting.unbounded_attempts??0)>0&&<p className={s.sequenceNotice}>{String(accounting.unbounded_attempts)} attempts have unresolved commitments. Their charges are not counted as zero.</p>}
    <div className={s.actions}><button onClick={()=>setEditing(value=>!value)}>Edit instructions and limits</button><button onClick={()=>{const body=command();send('Reuse sequence',()=>result(fetchClient.POST('/api/crexil/sequences/{sid}/reuse',{params:{path:{sid:sequence.id}},body})));}}>Reuse sequence</button>
      {status(sequence)!=='complete'&&sequence.state!=='cancelled'&&<button disabled={checking} onClick={()=>{setChecking(true);setError('');void result(fetchClient.GET('/api/crexil/sequences/{sid}/review',{params:{path:{sid:sequence.id}}})).then(setReview).catch(error=>setError(message(error))).finally(()=>setChecking(false));}}>{checking?'Reviewing…':'Review remaining work'}</button>}
    </div>
    {editing&&<EditSequence key={sequence.id} sequence={sequence} send={(name,work)=>send(name,async()=>{const saved=await work();setEditing(false);return saved;})}/>}
    {error&&<p className={s.error} role="alert">{error}</p>}
    {review&&<section className={s.sequenceReview} aria-label="Sequence review"><h2>{review.ready?'Ready for authorization':'Preparation needs attention'}</h2>
      <p>Eligible steps start automatically. Dependencies wait for their required material or decision. Each input is checked again against current operations and all enclosing limits before acceptance.</p>
      {review.errors.length>0&&<ul>{review.errors.map((error,index)=><li key={index}>{error}</li>)}</ul>}
      {review.ready&&<button className={s.primary} onClick={()=>{const body={...command(),revision:review.revision,review_token:review.token};send('Authorize sequence',()=>result(fetchClient.POST('/api/crexil/sequences/{sid}/start',{params:{path:{sid:sequence.id}},body})));}}>{sequence.state==='draft'?'Start sequence':'Authorize remaining work'}</button>}
    </section>}
    {control&&<ExecutionControls control={control} slots={data.turns.filter(turn=>turns.has(turn.id)).flatMap(turn=>turn.slots)} label="Sequence" refresh={refresh} pendingWork={status(sequence)!=='complete'&&sequence.state!=='cancelled'}/>}
    <div className={s.sequenceSteps}>{sequence.steps.map((step,index)=>{
      const direction=data.directions.find(item=>item.id===step.direction_id);
      const decisionActor=step.decision?.actor as {name?:string}|undefined;
      return <section key={step.key} className={s.sequenceStep} aria-label={`Step ${index+1}: ${direction?.title??step.key}`}>
        <header><span className={s.sequenceNumber}>{index+1}</span><div><h2>{direction?.title??step.key}</h2><span className={s.micro}>{step.state} · {step.input_mode==='each'?'one run per source':'sources together'}</span></div><button onClick={()=>edit(step.direction_id)}>Edit preparation</button></header>
        <p className={s.hint}>{step.dependencies.length?step.dependencies.map(dep=>`${data.directions.find(item=>item.id===sequence.steps.find(parent=>parent.key===dep.step_key)?.direction_id)?.title??dep.step_key}: ${dep.selection==='chosen'?'participant’s choice':'all successful outputs'}${dep.placement==='before_fixed'?' · before saved sources':' · after saved sources'}${dep.contribution?` (${dep.contribution})`:''}`).join(' · '):'Starts from saved sources.'}</p>
        {step.reason&&<p className={s.sequenceNotice}>{step.reason}</p>}
        {!step.runs.length&&sequence.state!=='cancelled'&&<button onClick={()=>setDependencyEdit(dependencyEdit===step.key?null:step.key)}>Change dependencies</button>}
        {dependencyEdit===step.key&&<EditDependencies key={step.key} sequence={sequence} step={step} data={data} send={(name,work)=>send(name,async()=>{const saved=await work();setDependencyEdit(null);return saved;})}/>}
        {step.decision&&<p className={s.sequenceInstructions}>Chosen by {decisionActor?.name??'a participant'}: {String(step.decision.reason??'')}</p>}
        {!step.binding&&sequence.state!=='cancelled'&&step.dependencies.some(dep=>dep.selection==='chosen')&&direction&&<ChooseMaterial key={step.key} sequence={sequence} step={step} direction={direction} data={data} send={send} inspect={inspect}/>}
        {step.runs.length>0&&<div className={s.sequenceRuns}>{step.runs.map((run,index)=>{
          const child=data.directions.find(item=>item.id===run.direction_id);
          const nodes=data.nodes.filter(item=>child?.node_ids.includes(item.id));
          const turn=data.turns.find(item=>item.id===run.turn_id);
          const counts=new Map<string,number>();
          for(const slot of turn?.slots??[])counts.set(slot.status,(counts.get(slot.status)??0)+1);
          const input=(step.binding?.inputs as {key:string;references:{node_id:string}[]}[]|undefined)?.find(item=>item.key===run.input_key);
          return <div key={String(run.direction_id)}><div className={s.actions}><strong>Run {index+1}</strong>{Boolean(run.turn_id)&&<button onClick={()=>openRun(String(run.turn_id))}>Open run</button>}</div>
            <p className={s.hint}>{turn?[...counts].map(([state,count])=>`${count} ${state}`).join(' · '):'Image prepared · No generation or charge'}</p>
            {Boolean(input?.references.length)&&<p className={s.hint}>From {input?.references.map(ref=>{const node=data.nodes.find(item=>item.id===ref.node_id);return <button key={ref.node_id} onClick={()=>inspect(ref.node_id)}>{node?label(node):ref.node_id}</button>;})}</p>}
            <div className={s.thumbs}>{nodes.map(node=><button className={s.thumb} key={node.id} onClick={()=>inspect(node.id)} aria-label={`Inspect ${label(node)}`}><MaterialThumbnail key={node.id} node={node} decorative/><span>{label(node)}</span></button>)}</div>
            {!nodes.length&&<p className={s.hint}>No retained material. Open the run for its attempt details.</p>}
          </div>;
        })}</div>}
      </section>;
    })}</div>
  </>;
}

function EditSequence({sequence,send}:{sequence:Sequence;send:Send}){
  const [title,setTitle]=useState(sequence.title);
  const [instructions,setInstructions]=useState(sequence.instructions);
  const [limits,setLimits]=useState(sequence.limits);
  const revision=useRef(sequence.revision);
  return <form className={s.sequenceReview} onSubmit={e=>{e.preventDefault();const body={...command(),revision:revision.current,title,instructions,limits};send('Update sequence',()=>result(fetchClient.PATCH('/api/crexil/sequences/{sid}',{params:{path:{sid:sequence.id}},body})));}}>
    <label>Sequence name<input aria-label="Sequence name" required value={title} onChange={e=>setTitle(e.target.value)}/></label><label>Instructions for the next participant<textarea aria-label="Instructions for the next participant" value={instructions} onChange={e=>setInstructions(e.target.value)}/></label>
    <LimitFields value={limits} change={setLimits}/><p className={s.hint}>Increasing a limit can release waiting work. Changed instructions require a fresh review before remaining steps are accepted.</p>
    {revision.current!==sequence.revision&&<p className={s.sequenceNotice}>The sequence changed while you were editing. <button type="button" onClick={()=>{revision.current=sequence.revision;setTitle(sequence.title);setInstructions(sequence.instructions);setLimits({...sequence.limits});}}>Reload saved values</button></p>}
    <button disabled={revision.current!==sequence.revision}>Save instructions and limits</button>
  </form>;
}

function EditDependencies({sequence,step,data,send}:{sequence:Sequence;step:Step;data:Snapshot;send:Send}){
  const [dependencies,setDependencies]=useState(step.dependencies);
  const [mode,setMode]=useState<'together'|'each'>(step.input_mode==='each'?'each':'together');
  const revision=useRef(sequence.revision);
  return <form className={s.sequenceReview} onSubmit={e=>{e.preventDefault();const body={...command(),revision:revision.current,input_mode:mode,dependencies};send('Update dependencies',()=>result(fetchClient.PATCH('/api/crexil/sequences/{sid}/steps/{key}',{params:{path:{sid:sequence.id,key:step.key}},body})));}}>
    <Dependencies steps={sequence.steps.map(item=>({key:item.key,title:data.directions.find(direction=>direction.id===item.direction_id)?.title??item.key}))} currentKey={step.key} dependencies={dependencies} inputMode={mode} change={(deps,value)=>{setDependencies(deps);setMode(value);}}/>
    <p className={s.hint}>Saving clears this step’s unaccepted bindings and decision. Review the sequence again to authorize this change.</p>
    {revision.current!==sequence.revision&&<p className={s.sequenceNotice}>The sequence changed while you were editing. <button type="button" onClick={()=>{revision.current=sequence.revision;setDependencies([...step.dependencies]);setMode(step.input_mode==='each'?'each':'together');}}>Reload saved dependencies</button></p>}
    <button disabled={revision.current!==sequence.revision}>Save dependencies</button>
  </form>;
}

function ChooseMaterial({sequence,step,direction,data,send,inspect}:{sequence:Sequence;step:Step;direction:Direction;data:Snapshot;send:Send;inspect:(id:string)=>void}){
  const [selections,setSelections]=useState<Record<string,string[]>>({});
  const [reason,setReason]=useState('');
  const [candidateToken,setCandidateToken]=useState(step.candidate_token);
  const dependencies=step.dependencies.filter(dep=>dep.selection==='chosen');
  return <form className={s.sequenceChoice} onSubmit={e=>{e.preventDefault();const body={...command(),revision:sequence.revision,direction_revision:direction.revision,candidate_token:candidateToken,selections,reason};send('Record decision',()=>result(fetchClient.POST('/api/crexil/sequences/{sid}/steps/{key}/decision',{params:{path:{sid:sequence.id,key:step.key}},body})));}}>
    <h3>Your choice carries the work forward</h3>
    {dependencies.map(dep=><div key={dep.step_key}><p>Choose material from {data.directions.find(item=>item.id===sequence.steps.find(parent=>parent.key===dep.step_key)?.direction_id)?.title??dep.step_key}</p><div className={s.sequenceCandidates}>
      {(step.candidates[dep.step_key]??[]).map(fact=>{const node=data.nodes.find(item=>item.id===fact.node_id);if(!node)return null;const chosen=(selections[dep.step_key]??[]).includes(node.id);return <div key={node.id}>
        <button type="button" className={`${s.thumb} ${chosen?s.selected:''}`} aria-pressed={chosen} aria-label={`Choose ${label(node)}`} onClick={()=>setSelections(previous=>({...previous,[dep.step_key]:chosen?(previous[dep.step_key]??[]).filter(id=>id!==node.id):[...(previous[dep.step_key]??[]),node.id]}))}><MaterialThumbnail key={node.id} node={node} decorative/><span>{label(node)}</span></button>
        <span className={s.hint}>{fact.successful?'Complete output':'Retained partial or external material'}</span><button type="button" onClick={()=>inspect(node.id)}>Inspect</button>
      </div>;})}</div>{!step.candidates[dep.step_key]?.length&&<p className={s.hint}>Waiting for retained material from this step.</p>}</div>)}
    <label>Reason for this choice<textarea aria-label="Reason for this choice" required value={reason} onChange={e=>setReason(e.target.value)} placeholder="Why should these results continue?"/></label>
    {candidateToken!==step.candidate_token&&<p className={s.sequenceNotice}>The material or preparation changed. Your reason is kept. <button type="button" onClick={()=>{setCandidateToken(step.candidate_token);setSelections(previous=>Object.fromEntries(dependencies.map(dep=>[dep.step_key,(previous[dep.step_key]??[]).filter(id=>step.candidates[dep.step_key]?.some(fact=>fact.node_id===id))])));}}>Review updated choices</button></p>}
    <p className={s.hint}>Your choice, reason, current instructions and limits stay with the next run. It can proceed automatically after you record this decision.</p><button className={s.primary} disabled={candidateToken!==step.candidate_token||!reason.trim()||dependencies.some(dep=>!selections[dep.step_key]?.length)}>Record decision</button>
  </form>;
}
