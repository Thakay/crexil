// Project settings retain their editing base and uncertain command in this tab.
// Polling cannot silently authorize replacing another participant's changes.
import {useEffect,useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {ActionError,command,message,money,result,type Project} from './api';
import s from './Filmstrip.module.css';

type Values={name:string;brief:string;instructions:string;attemptLimit:string;spendingLimit:string;policy:'hard_cap'|'published_tariff'};
type Draft={base:Project;values:Values;pending:components['schemas']['UpdateProject']|null;dirty:boolean};
const key=(id:string)=>`crexil-project-settings:${id}`;
function retained(id:string):Draft|null {try{return JSON.parse(sessionStorage.getItem(key(id))??'null');}catch{return null;}}
export const hasProjectEdits=(id:string)=>Boolean(retained(id));
const values=(project:Project):Values=>({name:project.name,brief:project.brief,instructions:project.instructions,
  attemptLimit:String(project.limits.attempt_limit??''),spendingLimit:String(project.limits.spending_limit_usd??''),
  policy:project.limits.spending_policy==='published_tariff'?'published_tariff':'hard_cap'});

export function ProjectDetails({project,accounting,onSaved}:{project:Project;accounting:Record<string,unknown>;onSaved:()=>void}) {
  const [draft,setDraft]=useState<Draft>(()=>retained(project.id)??{base:project,values:values(project),pending:null,dirty:false});
  const [comparison,setComparison]=useState<Project|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [conflict,setConflict]=useState(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const changed=conflict||project.revision!==draft.base.revision;
  const retain=(next:Draft)=>{if(next.dirty||next.pending)sessionStorage.setItem(key(project.id),JSON.stringify(next));else sessionStorage.removeItem(key(project.id));setDraft(next);};
  const edit=(field:keyof Values,value:string)=>retain({...draft,values:{...draft.values,[field]:value},dirty:true});
  const save=async(revision=draft.base.revision)=>{
    if(busy)return;
    const input=draft.values;
    const body=draft.pending??{...command(),revision,name:input.name,brief:input.brief,instructions:input.instructions,
      limits:{attempt_limit:input.attemptLimit===''?null:Number(input.attemptLimit),spending_limit_usd:input.spendingLimit===''?null:Number(input.spendingLimit),spending_policy:input.policy}};
    setBusy(true);setError('');
    try {
      retain({...draft,pending:body});
      await result(fetchClient.PATCH('/api/crexil/projects/{wid}',{params:{path:{wid:project.id}},body}));
      if(retained(project.id)?.pending?.request_id===body.request_id)sessionStorage.removeItem(key(project.id));
      if(mounted.current)onSaved();
    }catch(error){
      if(error instanceof ActionError&&error.status&&error.status>=400&&error.status<500&&error.status!==408&&retained(project.id)?.pending?.request_id===body.request_id){retain({...draft,pending:null});if(mounted.current)setConflict(error.status===409);}
      if(mounted.current)setError(message(error));
    }finally{if(mounted.current)setBusy(false);}
  };
  const compare=async()=>{
    setBusy(true);setError('');
    try{const current=await result(fetchClient.GET('/api/crexil/projects/{wid}',{params:{path:{wid:project.id}}}));setComparison(current.project);}
    catch(error){setError(message(error));}finally{setBusy(false);}
  };
  const input=draft.values;
  return <form className={s.projectDetails} aria-label="Project settings" onSubmit={event=>{event.preventDefault();if(draft.pending||!changed)void save();}}>
    <label>Project name<input required maxLength={200} disabled={busy||Boolean(draft.pending)} value={input.name} onChange={event=>edit('name',event.target.value)}/></label>
    <label>Brief<textarea maxLength={32000} disabled={busy||Boolean(draft.pending)} value={input.brief} onChange={event=>edit('brief',event.target.value)}/></label>
    <label>Instructions for participants<textarea maxLength={32000} disabled={busy||Boolean(draft.pending)} value={input.instructions} onChange={event=>edit('instructions',event.target.value)} placeholder="Guidance carried into later request reviews"/></label>
    <p>{String(accounting.attempts??0)} attempts used or reserved · {money(Number(accounting.charged_usd??0))} charged · {money(Number(accounting.reserved_usd??0))} reserved</p>
    {Number(accounting.unbounded_attempts??0)>0&&<p>{String(accounting.unbounded_attempts)} attempts have unknown commitments. Their charges need resolution before a spending limit can protect new work.</p>}
    <label>Project attempt limit<input type="number" min={0} step={1} disabled={busy||Boolean(draft.pending)} placeholder="No project limit" value={input.attemptLimit} onChange={event=>edit('attemptLimit',event.target.value)}/></label>
    <label>Project spending limit · USD<input type="number" min={0} step="0.01" disabled={busy||Boolean(draft.pending)} placeholder="No project limit" value={input.spendingLimit} onChange={event=>edit('spendingLimit',event.target.value)}/></label>
    <label>Project spending policy<select aria-label="Project spending policy" disabled={busy||Boolean(draft.pending)} value={input.policy} onChange={event=>edit('policy',event.target.value)}><option value="hard_cap">Require a hard cap</option><option value="published_tariff">Budget from published tariffs</option></select></label>
    <p>Limits include earlier spending and unfinished work. Raising a limit resumes waiting requests that fit. Requests already sent keep running.</p>
    {input.policy==='published_tariff'&&<p>Published tariffs reserve expected spend. The provider does not guarantee a final charge ceiling.</p>}
    {draft.dirty&&<p className={s.hint}>Your project edits are retained in this tab until saved or discarded.</p>}
    {draft.pending?<p role="status">This save is not confirmed. Check the same save before making another change.</p>:changed&&<div className={s.connectionNotice}>
      <p>The project changed since you opened these values. Your edits remain here.</p><button type="button" disabled={busy} onClick={()=>void compare()}>Compare current project</button>
      {comparison&&<section aria-label="Current project comparison"><h3>Current project · revision {comparison.revision}</h3><strong>{comparison.name}</strong>
        <p>{comparison.brief||'No brief'}</p><p>{comparison.instructions||'No participant instructions'}</p>
        <p>Attempt limit: {String(comparison.limits.attempt_limit??'none')} · Spending limit: {comparison.limits.spending_limit_usd==null?'none':money(Number(comparison.limits.spending_limit_usd))} · {comparison.limits.spending_policy==='published_tariff'?'Published tariffs':'Hard cap'}</p>
        {project.revision>comparison.revision&&<p>This comparison is already out of date. Read the current project again.</p>}
        <button type="button" disabled={busy||project.revision>comparison.revision} onClick={()=>{retain({base:comparison,values:values(comparison),pending:null,dirty:false});setConflict(false);setComparison(null);setError('');}}>Use current project values</button>
        <button type="button" disabled={busy||project.revision>comparison.revision||!input.name.trim()} onClick={()=>void save(comparison.revision)}>Replace revision {comparison.revision} with my values</button>
      </section>}
    </div>}
    {error&&<p role="alert" className={s.error}>{error}</p>}
    <div className={s.actions}><button disabled={busy||(!draft.pending&&changed)}>{busy?'Saving…':draft.pending?'Confirm same project save':'Save project'}</button>
      {!draft.pending&&draft.dirty&&<button type="button" disabled={busy} onClick={()=>{sessionStorage.removeItem(key(project.id));onSaved();}}>Discard local project edits</button>}</div>
  </form>;
}
