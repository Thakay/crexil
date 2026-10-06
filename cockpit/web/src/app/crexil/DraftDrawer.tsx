import { MaterialThumbnail } from './MaterialThumbnail';
// The drawer floats above the Filmstrip. Closing saves preparation, never dispatches.
// The local draft owns recoverable saves and explicit conflict choices. Operation
// changes preserve the remaining source/control values; Run needs a fresh review.
import { useEffect, useRef, useState } from 'react';
import type { components } from '../../generated/openapi';
import { fetchClient } from '../../api/client';
import { ACTIONS, actionView } from './actions';
import { ActionError, blankPreparation, command, isDefiniteRefusal, label, message,
  result, money, type Direction, type Material, type Operation, type Preparation } from './api';
import { useEscapeLayer } from './keys';
import { sourceRoles as rolesOf } from './media';
import s from './Filmstrip.module.css';
import { ImagePreparationFields, initialImagePreparation } from './ImagePreparationFields';
import { RegionEditor } from './RegionEditor';
import {usePreparationDraft} from './usePreparationDraft';

type Submission={body:components['schemas']['SubmitDirection'];image:boolean;title:string;setup:Preparation};
const submissionKey=(id:string)=>`crexil-submission:${id}`;
function savedSubmission(id:string):Submission|null {
  try {return JSON.parse(sessionStorage.getItem(submissionKey(id))??'null');}catch{return null;}
}
export const hasPendingSubmission=(id:string)=>Boolean(savedSubmission(id));

export function DraftDrawer({ direction, nodes, operations, onSaved, onClose, onCopied }: {
  direction: Direction; nodes: Material[]; operations: Operation[];
  onSaved: () => void; onClose: () => void; onCopied:(id:string)=>void;
}) {
  const [maskPending,setMaskPending] = useState(false);
  const draft=usePreparationDraft(direction,onSaved,maskPending);
  const {changed,error,saving}=draft;
  const [submission,setSubmission]=useState<Submission|null>(()=>savedSubmission(direction.id));
  const pendingSubmission=useRef(submission);pendingSubmission.current=submission;
  const title=submission?.title??draft.title,setup=submission?.setup??draft.setup;
  const [review, setReview] = useState<components['schemas']['ExecutionReview'] | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const [regionEditing,setRegionEditing] = useState(false);
  const pendingMasks = useRef(false);
  const closeRequested = useRef(false);
  const pendingNavigation = useRef<string | null>(null);
  const currentDraft=useRef(draft);currentDraft.current=draft;
  const finishClose = () => {
    onClose();
    if (pendingNavigation.current) location.hash = pendingNavigation.current;
  };
  const close = () => {
    closeRequested.current=true;
    if(!maskPending&&!draft.unresolved&&!saving&&!submission)finishClose();
    else if(!maskPending&&!error&&!submission)void draft.save();
  };
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (pendingMasks.current || currentDraft.current.unresolved || pendingSubmission.current) {event.preventDefault(); event.returnValue='';}
    };
    window.addEventListener('beforeunload',guard);
    return () => window.removeEventListener('beforeunload',guard);
  }, []);
  useEffect(() => {
    const origin = location.hash;
    const dirty = () => pendingMasks.current || currentDraft.current.saving || currentDraft.current.unresolved || Boolean(pendingSubmission.current);
    const defer = (target:string) => {
      pendingNavigation.current=target;closeRequested.current=true;
      if(!pendingMasks.current&&!currentDraft.current.error&&!pendingSubmission.current)void currentDraft.current.save();
    };
    const click = (event:MouseEvent) => {
      const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');
      if (link && link.hash !== origin && dirty()) {event.preventDefault(); event.stopImmediatePropagation(); defer(link.hash);}
    };
    const hash = (event:HashChangeEvent) => {
      if (location.hash !== origin && dirty()) {
        const target=location.hash; event.stopImmediatePropagation(); history.replaceState(null,'',origin); defer(target);
      }
    };
    window.addEventListener('click',click,true); window.addEventListener('hashchange',hash,true);
    return () => {window.removeEventListener('click',click,true); window.removeEventListener('hashchange',hash,true);};
  }, []);
  useEffect(() => {
    if (closeRequested.current && !maskPending && !draft.unresolved && !saving && !error && !submission) finishClose();
  }, [maskPending, draft.unresolved, saving, error, submission]);
  const invalidateReview=()=>{setReview(null);if(!pendingSubmission.current)setRunError('');};
  const update = (patch: Partial<Preparation>) => {
    draft.change(previous=>({...previous,setup:{...previous.setup,...patch}}));invalidateReview();
  };
  const updateTitle = (value:string) => {
    draft.change(previous=>({...previous,title:value}));invalidateReview();
  };
  useEffect(()=>{invalidateReview();},[direction.revision,draft.conflict]);
  const copy=async()=>{
    invalidateReview();const saved=await draft.copy();
    if(saved){if(closeRequested.current)finishClose();else onCopied(saved.id);}
  };
  const refs = setup.references ?? [];
  // The drawer owns Escape while open: it leaves the region editor first, then closes.
  useEscapeLayer(() => {
    if(regionEditing){if(!maskPending)setRegionEditing(false);}else close();
    return true;
  });
  const choices = setup.operations ?? [];
  const preparingImage = setup.action === 'prepare';
  const meshAction = actionView(setup.action)?.recordedDescription ?? false;
  const sourceRoles = rolesOf(setup.action);
  const count = choices.reduce((sum, choice) => sum + choice.count, 0);
  const reviewRun = async () => {
    if(submission)return;
    setRunning(true); setRunError('');
    const revision=draft.revision;
    try {
      const next=await result(fetchClient.GET(preparingImage ? '/api/crexil/directions/{did}/image-review' : '/api/crexil/directions/{did}/review', {params:{path:{did:direction.id}}}));
      if(next.revision!==revision){draft.requireComparison();return;}
      if(currentDraft.current.revision!==revision||currentDraft.current.unresolved||pendingMasks.current)return;
      setReview(next);
    }
    catch (err) {setRunError(message(err));} finally {setRunning(false);}
  };
  const submit = async () => {
    if(running)return;
    if (!submission && (!review?.ready || review.revision!==draft.revision || maskPending || changed || draft.unresolved || saving || error || direction.sequence_template || direction.state!=='draft')) return;
    setRunning(true); setRunError('');
    const accepted=submission??{body:{...command(),revision:review!.revision,review_token:review!.token},image:preparingImage,title,setup};
    try {
      sessionStorage.setItem(submissionKey(direction.id),JSON.stringify(accepted));
      pendingSubmission.current=accepted;setSubmission(accepted);
      if(accepted.image) await result(fetchClient.POST('/api/crexil/directions/{did}/prepare-image', {params:{path:{did:direction.id}}, body:accepted.body}));
      else await result(fetchClient.POST('/api/crexil/directions/{did}/submit', {params:{path:{did:direction.id}}, body:accepted.body}));
      sessionStorage.removeItem(submissionKey(direction.id));pendingSubmission.current=null;setSubmission(null);
      onSaved(); finishClose();
    } catch (err) {
      if(isDefiniteRefusal(err)){
        sessionStorage.removeItem(submissionKey(direction.id));pendingSubmission.current=null;setSubmission(null);setReview(null);
        if(err instanceof ActionError&&err.status===409)draft.requireComparison();
      }
      setRunError(message(err));
    } finally {setRunning(false);}
  };
  return <section className={s.drawer} aria-label="Preparation">
    <div className={s.drawerHead} inert={regionEditing}>
      <span className={s.mono}>{setup.action} · preparation</span>
      <span role="status" className={s.saved}>{draft.status}</span>
      <button onClick={close}>Close · Esc</button>
    </div>
    {draft.storageError&&<p className={s.error} role="alert">{draft.storageError}</p>}
    {error&&<div className={s.error} role="alert">{error}{!draft.conflict&&<button disabled={saving} onClick={()=>draft.copyPending?void copy():void draft.save()}>{draft.copyPending?'Confirm same copy':draft.pending?'Confirm same save':'Retry save'}</button>}</div>}
    {submission&&<section className={s.preparationConflict} aria-label="Unconfirmed submission"><strong>Confirm the previous submission</strong>
      <p>Its response was not confirmed. Check the same request before changing this preparation. This keeps the original submission identity, including after a reload.</p>
      <button disabled={running} onClick={()=>void submit()}>{running?'Checking submission…':'Check submission'}</button>
    </section>}
    {!submission&&(draft.conflict||direction.state!=='draft')&&<section className={s.preparationConflict} aria-label="Preparation conflict">
      <strong>{direction.state==='draft'?'A newer preparation is saved.':'This direction is no longer editable.'}</strong>
      <p>Your local version is kept in this tab. Compare the saved preparation, or keep your version as a separate direction. Neither choice runs anything.</p>
      {draft.readError&&<p role="alert">{draft.readError}</p>}
      <div className={s.actions}>
        <button disabled={draft.reading||saving} onClick={()=>void draft.readSaved()}>{draft.reading?'Reading…':'Compare saved version'}</button>
        <button disabled={maskPending||saving||draft.pending} onClick={()=>void copy()}>{draft.copyPending?'Confirm same copy':'Save my version separately'}</button>
        <button disabled={maskPending||saving||draft.pending||draft.copyPending||Boolean(draft.storageError)} onClick={finishClose}>Keep in this tab and close</button>
      </div>
      {draft.comparison&&<>
        <div className={s.preparationComparison}>
          <PreparationVersion name="Your version" title={title} setup={setup}/>
          <PreparationVersion name={`Saved revision ${draft.comparison.revision}`} title={draft.comparison.title} setup={draft.comparison.setup}/>
        </div>
        <div className={s.actions}>
          <button disabled={maskPending||saving||draft.pending||draft.copyPending||draft.comparison.state!=='draft'} onClick={()=>{invalidateReview();void draft.replaceSaved();}}>Save my version over this revision</button>
          <button disabled={maskPending||saving||draft.pending||draft.copyPending} onClick={()=>{invalidateReview();draft.useSaved();}}>Discard my edits and use saved version</button>
        </div>
        {draft.comparison.state!=='draft'&&<p>The saved direction is {draft.comparison.state}. To make changes, save a separate preparation.</p>}
      </>}
    </section>}
    {setup.action==='region'&&refs.length===1&&nodes.find(n=>n.id===refs[0].node_id)&&<RegionEditor key={`${refs[0].node_id}:${draft.adoption}`} wid={direction.workspace_id} source={nodes.find(n=>n.id===refs[0].node_id)!}
      setup={setup} update={update} open={regionEditing} close={()=>setRegionEditing(false)} onPending={value=>{pendingMasks.current=value;setMaskPending(value);if(value)setReview(null);}}/>}
    {preparingImage ? <div className={s.imagePreparationBody} inert={running||regionEditing||draft.copyPending||Boolean(submission)}>
      <label>Direction name<input autoFocus value={title} onChange={e=>updateTitle(e.target.value)}/></label>
      <ImagePreparationFields setup={setup} nodes={nodes} update={update}/>
    </div> : <div className={s.drawerBody} inert={running||regionEditing||draft.copyPending||Boolean(submission)}>
      <div className={s.sources}>
        <h3>Sources · {refs.length}</h3>
        {refs.map((ref, i) => {
          const node = nodes.find(n => n.id === ref.node_id);
          return <div className={s.source} key={ref.node_id}>
            {node ? <MaterialThumbnail key={node.id} node={node}/> : <span>Source unavailable</span>}
            <span className={s.micro}>{sourceRoles[i]?`${sourceRoles[i]} · `:''}{node ? label(node) : ref.node_id}</span>
            <input aria-label={`Contribution from source ${i + 1}`} placeholder={meshAction?'Recorded source note (not sent)':'What this source contributes'} value={ref.contribution ?? ''}
              onChange={e => update({references: refs.map((r, j) => j === i ? {...r, contribution: e.target.value} : r)})}/>
            {(ref.role!=null||ref.strength!=null)&&<p>Saved role: {ref.role??'none'} · Strength: {ref.strength??'none'} <button onClick={()=>update({references:refs.map((r,j)=>j===i?{...r,role:null,strength:null}:r)})}>Clear role and strength</button></p>}
            <div className={s.actions}>
              <button disabled={i === 0} onClick={() => { const moved = [...refs]; [moved[i-1], moved[i]] = [moved[i], moved[i-1]]; update({references:moved}); }}>Move up</button>
              <button onClick={() => update({references: refs.filter((_, j) => i !== j)})}>Remove</button>
            </div>
          </div>;
        })}
        <select aria-label="Add a source" value="" onChange={e => e.target.value && update({references:[...refs, {node_id:e.target.value, contribution:''}]})}>
          <option value="">Add source…</option>
          {nodes.filter(n => n.available && !refs.some(r => r.node_id === n.id)).map(n => <option value={n.id} key={n.id}>{label(n)}</option>)}
        </select>
        <p className={s.hint}>{meshAction?`Inputs in order: ${sourceRoles.join(', ')}. Source notes are kept for participants; the runner uses their files.`:'Contributions are instructions. An operation must support every supplied source.'}</p>
      </div>
      <div className={s.words}>
        <label>Direction name<input autoFocus value={title} onChange={e => updateTitle(e.target.value)}/></label>
        <label>Action<select aria-label="Action" value={setup.action} onChange={e => {
          const initial=initialImagePreparation(refs.map(r=>nodes.find(n=>n.id===r.node_id)).filter((n):n is Material=>Boolean(n)));
          const recipe=setup.image_preparation??initial;
          update({action:e.target.value as Preparation['action'], ...(e.target.value==='prepare'?{image_preparation:{...recipe,items:refs.map(ref=>recipe.items?.find(item=>item.node_id===ref.node_id)??initial.items?.find(item=>item.node_id===ref.node_id)??{node_id:ref.node_id})}}:{})});
        }}>
          {Object.entries(ACTIONS).filter(([,action])=>action?.listed).map(([id,action])=><option key={id} value={id}>{action!.label}</option>)}
        </select></label>
        <label>{meshAction?'Recorded description':'Prompt'}<textarea aria-label={meshAction?'Recorded description':'Prompt'} placeholder={meshAction?'Describe this direction for the record…':'Describe the next result…'} value={setup.prompt} onChange={e => update({prompt:e.target.value})}/></label>
        <label>Instructions for the next participant<input value={setup.instructions} onChange={e => update({instructions:e.target.value})}/></label>
        {setup.action==='region'&&<div className={s.regionSummary}>
          <h3>Regions · {(setup.regions??[]).length}</h3>
          <button disabled={refs.length!==1} onClick={()=>setRegionEditing(true)}>Draw, import or reuse masks</button>
          {refs.length!==1&&<p>Choose one source image to define its regions.</p>}
          {(setup.regions??[]).map((region,i)=><p key={region.key}>{i+1}. {region.name} · {region.action} · {region.mask_id?'Mask saved':'Drawing needed'}</p>)}
          <label>Overlap priority<select value={setup.region_overlap_policy??''} onChange={e=>update({region_overlap_policy:e.target.value==='nd3-v1'?'nd3-v1':null})}>
            <option value="">Require disjoint edit regions</option><option value="nd3-v1">KEEP, DELETE, REPLACE, MODIFY; later wins within an action</option>
          </select></label>
          <label>Pixels outside the drawn regions<select value={String(setup.region_regenerate_unclaimed??false)} onChange={e=>update({region_regenerate_unclaimed:e.target.value==='true'})}><option value="false">Preserve original pixels</option><option value="true">Regenerate under the shared prompt when a KEEP exists</option></select></label>
        </div>}
        <div className={`${s.actions} ${s.budgetFields}`}>
          <label>Spending policy<select aria-label="Spending policy" value={setup.spending_policy??'hard_cap'} onChange={e=>update({spending_policy:e.target.value as Preparation['spending_policy']})}>
            <option value="hard_cap">Require a hard cap</option><option value="published_tariff">Budget from published tariffs</option>
          </select></label>
          <label>Attempt limit<input type="number" min={1} max={1000} value={setup.attempt_limit}
            onChange={e => update({attempt_limit:Number(e.target.value)})}/></label>
          <label>Spending limit · USD<input type="number" min={0} step="0.01" value={setup.spending_limit_usd}
            onChange={e => update({spending_limit_usd:Number(e.target.value)})}/></label>
        </div>
        {setup.spending_policy==='published_tariff'&&<p>Hosted prices are checked before dispatch. The budget reserves the published amount, but the provider does not guarantee a final charge ceiling. Unknown charges keep their reservation.</p>}
      </div>
      <div className={s.roster}>
        <h3>Configured operations</h3>
        {Object.keys(setup.controls??{}).length>0&&<details><summary>Shared control values</summary>{Object.entries(setup.controls??{}).map(([key,value])=><p key={key}>{key}: {JSON.stringify(value)} <button onClick={()=>{const controls={...setup.controls};delete controls[key];update({controls});}}>Clear shared value</button></p>)}</details>}
        {[...operations].sort((a,b)=>Number(b.actions.includes(setup.action??'generate'))-Number(a.actions.includes(setup.action??'generate'))||Number(choices.some(c=>c.operation_id===b.id))-Number(choices.some(c=>c.operation_id===a.id))).map(operation => {
          const choice = choices.find(c => c.operation_id === operation.id);
          const setChoice = (patch: Partial<NonNullable<Preparation['operations']>[number]>) => update({
            operations:choices.map(c => c.operation_id === operation.id ? {...c,...patch} : c),
          });
          return <div className={`${s.operation} ${!operation.actions.includes(setup.action??'generate')||operation.availability==='unavailable'?s.operationUnavailable:''}`} key={operation.id}>
            <div><span>{operation.label}</span><span className={s.micro}>{operation.evidence} · {operation.availability}</span></div>
            <label className={s.count}>Attempts<input type="number" min={0} max={operation.attempt_max??16} value={choice?.count ?? 0} aria-label={`${operation.label} attempts`}
              onChange={e => update({operations:[...choices.filter(c => c.operation_id !== operation.id), ...(Number(e.target.value) > 0 ? [{operation_id:operation.id, revision:operation.revision, count:Number(e.target.value), controls: choice?.controls ?? {}}] : [])]})}/></label>
            {!operation.actions.includes(setup.action??'generate')&&<p className={s.hint}>This operation does not perform {setup.action}. Its saved inputs remain available.</p>}
            {choice&&(operation.limitations??[]).map(note=><p key={note} className={s.hint}>{note}</p>)}
            {(operation.reasons.length > 0 || refs.length > operation.source_limit) && <p className={s.hint}>
              {refs.length > operation.source_limit ? `Accepts ${operation.source_limit} source. All ${refs.length} sources remain in preparation. ` : ''}{operation.reasons.join(' ')}</p>}
            {choice && choice.revision !== operation.revision && <p className={s.hint}>The operation changed. Your inputs are kept. <button onClick={() => setChoice({revision:operation.revision})}>Accept current revision</button></p>}
            <details><summary>Inputs, cost and route</summary><p>{operation.execution_location} · {operation.data_destination}</p>
              <p>{operation.maximum_usd_per_attempt === null ? `${operation.price_usd === null ? 'Price unknown' : `${money(operation.price_usd)} estimated per attempt`}. ${operation.cost_basis}.` : `Maximum ${money(operation.maximum_usd_per_attempt)} per attempt. ${operation.cost_basis}.`}</p>
              {operation.inputs.map((input, i) => {
                const key=String(input.key);
                const values=Object.fromEntries(operation.inputs.map(spec=>[String(spec.key),choice?.controls?.[String(spec.key)]??setup.controls?.[String(spec.key)]??spec.default]));
                const when=(input.when??{}) as Record<string,unknown>;
                const active=Object.entries(when).every(([name,value])=>JSON.stringify(values[name])===JSON.stringify(value));
                return <div key={i}>{typeof input.effect==='string'&&<p>{input.effect}</p>}<OperationInput spec={input} selected={Boolean(choice)&&active}
                  value={choice?.controls?.[key]??setup.controls?.[key]} onChange={value => setChoice({controls:{...choice?.controls,[key]:value}})}/>
                  {!active&&<p>This control is inactive with the current choices. Its saved value is retained.</p>}
                  {choice?.controls&&key in choice.controls&&<button onClick={()=>{const controls={...choice.controls};delete controls[key];setChoice({controls});}}>Clear {String(input.label||key)} override</button>}</div>;
              })}
              {choice && Object.keys(choice.controls ?? {}).filter(key => !operation.inputs.some(input => input.key === key)).map(key => <p key={key}>
                Unsupported saved control: {key}. <button onClick={() => {const controls={...choice.controls}; delete controls[key]; setChoice({controls});}}>Remove this value</button>
              </p>)}
            </details>
          </div>;
        })}
        {operations.length === 0 && <p>No configured operations. Your preparation can still be saved.</p>}
      </div>
    </div>}
    {review && preparingImage && <section className={`${s.runReview} ${s.imageReview}`} aria-label="Image preparation review">
      <strong>Review the derived image</strong><p>{(review.snapshot.size as number[]).join(' × ')} pixels · No generation or charge</p>
      <img className={s.preparedPreview} src={String(review.snapshot.preview_data_url)} alt="Exact prepared result"/>
      <p>The new image keeps its preparation settings and source hashes. The original material remains available.</p>
    </section>}
    {review && !preparingImage && <section className={`${s.runReview} ${review.snapshot.region?s.regionReview:''}`} aria-label="Run review" tabIndex={0}>
      <strong>{review.ready ? 'Ready to submit' : 'Resolve these points before running'}</strong>
      <p>{review.ready_attempts} ready · {review.blocked_attempts} blocked · {review.maximum_usd === null ? setup.spending_policy==='published_tariff'?`Published-tariff reservation ${money(review.reserved_usd??0)}; no hard ceiling`:'Maximum cannot be established' : `Maximum for ready requests ${money(review.maximum_usd)}`} · Limit {money(setup.spending_limit_usd ?? 0)}</p>
      {review.ready && review.blocked_attempts > 0 && <p>Run submits the ready requests. Blocked requests stay in this preparation for you to revise.</p>}
      {review.errors.length > 0 && <ul>{review.errors.map((error,i) => <li key={i}>{error}</li>)}</ul>}
      {setup.instructions && <p>For the next participant: {setup.instructions}</p>}
      {Boolean(review.snapshot.project_instructions) && <p>Project instructions: {String(review.snapshot.project_instructions)}</p>}
      {Boolean(review.snapshot.region)&&<RegionReview value={review.snapshot.region as Record<string,unknown>}/>}
      {review.ready && <p>The saved sources, controls and operation revisions will be frozen in the record. This run makes no automatic retries.</p>}
    </section>}
    {runError && <p className={s.error} role="alert">{runError}</p>}
    <div className={s.drawerFoot} inert={regionEditing}><span>{preparingImage ? `${refs.length} sources · New derived image` : `${count} planned attempts · ${refs.length > 1 ? 'sources together' : 'one direction'}`}</span>
      {direction.sequence_template ? <span>This is a sequence preparation. Close to review and authorize its remaining work in Sequences.</span> : <span className={s.actions}><button disabled={maskPending || changed || draft.unresolved || saving || Boolean(error) || running || Boolean(submission) || direction.state!=='draft'} onClick={() => void reviewRun()}>{running ? 'Working…' : preparingImage ? 'Review image' : 'Review run'}</button>
        {!submission&&review?.ready && <button className={s.primary} disabled={maskPending || changed || draft.unresolved || saving || Boolean(error) || running || direction.state!=='draft'} onClick={() => void submit()}>{preparingImage ? 'Save derived image' : `Run ${review.ready_attempts} attempts`}</button>}</span>}
    </div>
  </section>;
}

function PreparationVersion({name,title,setup}:{name:string;title:string;setup:Record<string,unknown>}) {
  return <section aria-label={name}><h3>{name}</h3><strong>{title}</strong><p>{String(setup.prompt||'No prompt')}</p>
    <details><summary>All sources and settings</summary><pre>{JSON.stringify(setup,null,2)}</pre></details></section>;
}

function RegionReview({value}:{value:Record<string,unknown>}) {
  const passes=value.passes as {idx:number;kind:string;prompt:string|null;effective_mask_preview:string;box:number[]|null}[];
  return <div><h3>{passes.length} ordered passes per attempt</h3><p>{String(value.protection)}</p><div className={s.regionPasses}>
    {passes.map(pass=><div key={pass.idx}><img src={pass.effective_mask_preview} alt={`Effective edit area for pass ${pass.idx+1}`}/><strong>{pass.idx+1}. {pass.kind}</strong><p>{pass.prompt??'Fill from surrounding pixels'}</p><span>{pass.box?`Crop ${pass.box.join(', ')}`:'Whole source frame'}</span></div>)}
  </div><p>{String(value.recovery)}</p></div>;
}

function OperationInput({spec, value, selected, onChange}: {
  spec: Record<string, unknown>; value: unknown; selected: boolean; onChange:(value:unknown)=>void;
}) {
  const name = String(spec.label ?? spec.key);
  if (spec.key === 'prompt') return <p>Prompt comes from this direction, with its source contribution instructions.</p>;
  if (spec.supported === false) return <p>{name} · Unsupported. {String(spec.reason ?? '')}</p>;
  const current = value ?? spec.default;
  if (spec.type === 'seed') {
    const seed = typeof current === 'number' ? {mode:'fixed',value:current} : (current ?? {mode:'random'}) as {mode:string;value?:number};
    return <div className={s.inputPair}><label>{name}<select aria-label={name} disabled={!selected} value={seed.mode} onChange={e => onChange({mode:e.target.value, ...(e.target.value !== 'random' ? {value:seed.value ?? 0} : {})})}>
      <option value="random">Random each time</option><option value="fixed">Fixed sequence</option><option value="locked">Same for every attempt</option>
    </select></label>{seed.mode !== 'random' && <label>Seed value<input type="number" min={0} disabled={!selected} value={seed.value ?? 0} onChange={e => onChange({...seed,value:Number(e.target.value)})}/></label>}</div>;
  }
  if (spec.type === 'enum') return <label>{name}<select aria-label={name} disabled={!selected} value={String(((spec.choices??[]) as unknown[]).findIndex(choice=>JSON.stringify(choice)===JSON.stringify(current)))} onChange={e => onChange((spec.choices as unknown[])[Number(e.target.value)])}>
    <option value="-1" disabled>Choose a value</option>{((spec.choices ?? []) as unknown[]).map((choice,index) => <option key={index} value={index}>{String(choice)}</option>)}
  </select></label>;
  if (spec.type === 'bool') return <label>{name}<select aria-label={name} disabled={!selected} value={String(current??false)} onChange={e=>onChange(e.target.value==='true')}><option>false</option><option>true</option></select></label>;
  const numeric = spec.type === 'int' || spec.type === 'float';
  return <label>{name}<input disabled={!selected} type={numeric ? 'number' : 'text'} min={spec.min as number | undefined} max={spec.max as number | undefined}
    step={spec.step as number | undefined ?? (spec.type === 'float' ? 'any' : 1)} value={String(current ?? '')}
    onChange={e => onChange(numeric ? Number(e.target.value) : e.target.value)}/></label>;
}

export const defaultSetup = blankPreparation;
