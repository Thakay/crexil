// Registration is saved preparation. Tests enter the normal project drawer and
// shared review; importing, mapping and inspecting never dispatch a graph.
import { useEffect, useRef, useState, type RefObject } from 'react';
import { $api, fetchClient } from '../../api/client';
import type { components } from '../../generated/openapi';
import { blankPreparation, command, message, projectHref, result } from './api';
import s from './Filmstrip.module.css';
import {PreparedOperations} from './PreparedOperations';

type Registration = components['schemas']['RegistrationView'];
type Binding = components['schemas']['OperationBinding'];
type Control = Omit<components['schemas']['PublicControl'],'targets'|'choices'|'when'> & {targets:Target[];choices:unknown[];when:Record<string,unknown>};
type Source = components['schemas']['SourcePort'];
type Output = components['schemas']['OutputPort'];
type Target = components['schemas']['GraphTarget'];
type Graph = Record<string, {class_type?:string;inputs?:Record<string,unknown>}>;
type Definition = Omit<components['schemas']['OperationDefinition'],'graph'|'actions'|'inputs'|'sources'|'outputs'> & {graph:Record<string,unknown>;actions:('generate'|'continue'|'combine'|'custom')[];inputs:Control[];sources:Source[];outputs:Output[]};
const normalize = (value:components['schemas']['OperationDefinition']):Definition => ({...value,
  graph:value.graph??{},actions:value.actions??['custom'],sources:value.sources??[],outputs:value.outputs??[],
  inputs:(value.inputs??[]).map(control=>({...control,targets:control.targets??[],choices:control.choices??[],when:control.when??{}}))});

export function OperationLibrary() {
  const routeHash=location.hash;
  const params = new URLSearchParams(location.hash.split('?')[1]);
  const projects = $api.useQuery('get', '/api/crexil/projects');
  const query = $api.useQuery('get', '/api/crexil/registrations');
  const [selected, setSelected] = useState<string | null>(params.get('id'));
  const [project, setProject] = useState(params.get('project') ?? '');
  const [importing, setImporting] = useState(Boolean(params.get('source')||params.get('import')));
  const [prepared,setPrepared]=useState(!params.get('id')&&!params.get('source')&&!params.get('import'));
  const [name, setName] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [text, setText] = useState('');
  const [sourceNode, setSourceNode] = useState(params.get('source'));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef<{fingerprint:string;delivery:ReturnType<typeof command>} | null>(null);
  const leave = useRef<(() => Promise<boolean>) | null>(null);
  const navigate = async (action:()=>void) => {if(!leave.current||await leave.current())action();};
  const acceptedHash=useRef(routeHash);
  const changeHash=(hash:string)=>{acceptedHash.current=hash;location.hash=hash;};
  const route=(extra='')=>`#/operations?${project?`project=${project}&`:''}${extra}`;
  useEffect(()=>{
    if(routeHash===acceptedHash.current)return;
    let active=true;
    const previous=acceptedHash.current;
    void (async()=>{
      const allowed=!leave.current||await leave.current();
      if(!active)return;
      if(!allowed){location.hash=previous;return;}
      acceptedHash.current=routeHash;
      const next=new URLSearchParams(routeHash.split('?')[1]);
      setSelected(next.get('id'));setProject(next.get('project')??'');setSourceNode(next.get('source'));
      setImporting(Boolean(next.get('source')||next.get('import')));
      setPrepared(!next.get('id')&&!next.get('source')&&!next.get('import'));
    })();
    return()=>{active=false;};
  },[routeHash]);
  const entry = query.data?.find(r => r.id === selected);
  const loadError:unknown = query.error;
  const saveImport = async () => {
    setBusy(true);setError('');
    const body = {name,source_name:sourceName,source_text:text,source_node_id:sourceNode};
    const fingerprint = JSON.stringify(body);
    if(pending.current?.fingerprint !== fingerprint) pending.current={fingerprint,delivery:command()};
    try {
      const saved = await result(fetchClient.POST('/api/crexil/registrations', {body:{...pending.current.delivery,...body}}));
      await query.refetch();setSelected(saved.id);setImporting(false);pending.current=null;
      changeHash(route(`id=${saved.id}`));
      setName('');setText('');setSourceNode(null);
    } catch(err) {setError(message(err));} finally {setBusy(false);}
  };
  return <>
    <header className={s.topbar}><a className={s.back} href={projectHref(project || undefined)} aria-label="Return to project" onClick={e=>{e.preventDefault();void navigate(()=>{location.hash=projectHref(project||undefined);});}}>‹</a>
      <strong className={s.brand}>Operations</strong><span className={s.micro}>Reusable descriptions and recorded evidence</span>
      <div className={s.spacer}/><a className={s.quiet} href={`#/connections${project?`?project=${project}`:''}`} onClick={event=>{event.preventDefault();void navigate(()=>{location.hash=`#/connections${project?`?project=${project}`:''}`;});}}>Connections</a><button onClick={() => void navigate(()=>{setPrepared(false);setImporting(true);setSelected(null);changeHash(route('import=1'));})}>+ Import operation</button></header>
    <main className={s.operationLibrary}>
      <aside className={s.libraryList}>
        <button className={prepared?s.activeRegistration:undefined} onClick={()=>void navigate(()=>{setPrepared(true);setImporting(false);setSelected(null);changeHash(route());})}><strong>Prepared operations</strong><span>Configured actions, controls and requirements</span></button>
        <label>{prepared?'Project for preparation':'Project for recorded tests'}<select value={project} onChange={e=>{setProject(e.target.value);const next=new URLSearchParams(location.hash.split('?')[1]);next.set('project',e.target.value);changeHash(`#/operations?${next}`);}}><option value="">Choose a project</option>
          {project&&!projects.data?.some(p=>p.id===project)&&<option value={project} disabled>{projects.isLoading?'Reading selected project…':'Selected project is unavailable'}</option>}
          {projects.data?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <p className={s.hint}>Tests use the same sources, review, limits and execution record as other project work.</p>
        <h3>Your registrations</h3>
        {query.isLoading && <p role="status">Opening operation descriptions…</p>}
        {Boolean(loadError) && <p role="alert">{message(loadError)} <button onClick={() => void query.refetch()}>Try again</button></p>}
        {query.data?.map(item => <button key={item.id} className={item.id===selected?s.activeRegistration:undefined}
          onClick={() => void navigate(()=>{setPrepared(false);setSelected(item.id);setImporting(false);changeHash(route(`id=${item.id}`));})}><strong>{item.name}</strong><span>Revision {item.revision} · {item.gaps.length?'Needs preparation':'Interface described'}</span></button>)}
        {query.data?.length===0 && <p>Bring an API export or a prepared description. An incomplete import can be saved.</p>}
      </aside>
      <div className={s.libraryContent}>
        {error && <p role="alert" className={s.error}>{error}</p>}
        {prepared?<PreparedOperations project={project} openRegistration={id=>{setSelected(id);setPrepared(false);setImporting(false);changeHash(route(`id=${id}`));}}/>:importing ? <form className={s.registrationForm} onSubmit={e=>{e.preventDefault();void saveImport();}}>
          <span className={s.eyebrow}>Save first. Complete the interface when ready.</span><h1>Bring an operation.</h1>
          <label>Name<input autoFocus value={name} required onChange={e=>setName(e.target.value)} placeholder="A material study"/></label>
          {sourceNode ? <p>Recover supplied workflow metadata from the selected image. This does not claim Crexil observed its execution.</p> : <>
            <label>ComfyUI API export or prepared description<input type="file" accept=".json,application/json" onChange={e=>{const file=e.target.files?.[0];if(file)void file.text().then(value=>{setText(value);setSourceName(file.name);if(!name)setName(file.name.replace(/\.json$/,''));});}}/></label>
            <label>Or paste the supplied definition<textarea className={s.codeInput} value={text} onChange={e=>setText(e.target.value)} spellCheck={false}/></label>
          </>}
          <p className={s.hint}>The original definition stays intact. Importing starts no installations, downloads or generation. Editor JSON is kept with guidance for API export.</p>
          <button className={s.primary} disabled={busy||!name.trim()}>{busy?'Saving…':'Save imported preparation'}</button>
        </form> : entry ? <RegistrationEditor key={entry.id} item={entry} project={project} refreshed={()=>void query.refetch()} leave={leave}/> :
          <div className={s.registrationIntro}><span className={s.eyebrow}>An operation has a public interface</span><h1>Describe what goes in.<br/>Keep what comes back.</h1>
            <p>Map words, controls and ordered sources to a prepared graph. Identify its results, inspect the configured runtime, then prepare a bounded test in a project.</p>
            <button className={s.primary} onClick={()=>setImporting(true)}>Import an operation</button></div>}
      </div>
    </main>
  </>;
}

function RegistrationEditor({item,project,refreshed,leave}:{item:Registration;project:string;refreshed:()=>void;leave:RefObject<(() => Promise<boolean>) | null>}) {
  const [saved,setSaved] = useState(item);
  const [name,setName] = useState(item.name);
  const [definition,setDefinition] = useState<Definition>(normalize(item.definition));
  const [binding,setBinding] = useState<Binding>(item.binding);
  const [adaptation,setAdaptation] = useState(item.adaptation_note);
  const [graphText,setGraphText] = useState(JSON.stringify(item.definition.graph,null,2));
  const [advanced,setAdvanced] = useState('');
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [status,setStatus] = useState('Saved');
  const pending = useRef<{fingerprint:string;delivery:ReturnType<typeof command>} | null>(null);
  const graph=definition.graph as Graph;
  const dirty=name!==saved.name||adaptation!==saved.adaptation_note||JSON.stringify(definition)!==JSON.stringify(saved.definition)||JSON.stringify(binding)!==JSON.stringify(saved.binding)||graphText!==JSON.stringify(saved.definition.graph,null,2);
  useEffect(()=>{
    // Reopening can first mount from the query cache while a newer revision
    // is fetched. Adopt it only while the participant has no local changes.
    if(item.revision<=saved.revision||dirty||busy)return;
    setSaved(item);setName(item.name);setDefinition(normalize(item.definition));
    setBinding(item.binding);setAdaptation(item.adaptation_note);
    setGraphText(JSON.stringify(item.definition.graph,null,2));
  },[item,saved.revision,dirty,busy]);
  const delivery=(value:unknown)=>{const fingerprint=JSON.stringify(value);if(pending.current?.fingerprint!==fingerprint)pending.current={fingerprint,delivery:command()};return pending.current.delivery;};
  const run=async(work:()=>Promise<void>)=>{setBusy(true);setError('');try{await work();}catch(err){setError(message(err));}finally{setBusy(false);}};
  const patch=(changes:Partial<Definition>)=>setDefinition({...definition,...changes});
  const save=async()=>{
    const parsed=JSON.parse(graphText) as Definition['graph'];
    const body={revision:saved.revision,name,definition:{...definition,graph:parsed},binding,adaptation_note:adaptation};
    const next=await result(fetchClient.PATCH('/api/crexil/registrations/{rid}',{params:{path:{rid:item.id}},body:{...delivery(body),...body}}));
    setSaved(next);setDefinition(normalize(next.definition));setGraphText(JSON.stringify(next.definition.graph,null,2));setStatus('Saved');refreshed();pending.current=null;
    return next;
  };
  useEffect(()=>{
    leave.current=async()=>{if(busy)return false;if(!dirty)return true;setBusy(true);try{await save();return true;}catch(err){setError(message(err));return false;}finally{setBusy(false);}};
    const unload=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',unload);
    return()=>{leave.current=null;window.removeEventListener('beforeunload',unload);};
  });
  const inspect=async()=>{const next=dirty?await save():saved;const body={revision:next.revision};
    await result(fetchClient.POST('/api/crexil/registrations/{rid}/inspect',{params:{path:{rid:item.id}},body:{...delivery(body),...body}}));
    const fresh=await result(fetchClient.GET('/api/crexil/registrations/{rid}',{params:{path:{rid:item.id}}}));setSaved(fresh);refreshed();pending.current=null;setStatus('Requirements inspected');};
  const prepareTest=async()=>{const next=dirty?await save():saved;
    const body={title:`Test · ${next.name}`,setup:{...blankPreparation(),action:'custom' as const,purpose:'operation_test' as const,attempt_limit:1,
      operations:[{operation_id:`custom-${next.id}`,revision:next.operation_revision,count:1,controls:{}}]}};
    const draft=await result(fetchClient.POST('/api/crexil/projects/{wid}/directions',{params:{path:{wid:project}},body:{...delivery(body),...body}}));
    location.hash=`${projectHref(project)}?draft=${draft.id}`;};
  const updateControl=(index:number,changes:Partial<Control>)=>patch({inputs:definition.inputs.map((control,i)=>i===index?{...control,...changes}:control)});
  const updateSource=(index:number,changes:Partial<Source>)=>patch({sources:definition.sources.map((source,i)=>i===index?{...source,...changes}:source)});
  const updateOutput=(index:number,changes:Partial<Output>)=>patch({outputs:definition.outputs.map((output,i)=>i===index?{...output,...changes}:output)});
  const inspection=saved.inspection as {gaps?:string[];findings?:{kind:string;name:string;state:string;package?:string}[];checked_at?:string;runtime_url?:string;limitations?:string[]}|null;
  return <div className={s.registrationForm} onClickCapture={e=>{const link=(e.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');if(link&&dirty){e.preventDefault();void run(async()=>{await save();location.hash=link.hash;});}}}>
    <div className={s.registrationHeading}><div><span className={s.eyebrow}>Saved operation · revision {saved.revision}</span><h1>{saved.name}</h1></div><span role="status">{dirty?'Unsaved changes':status}</span></div>
    {error&&<p role="alert" className={s.error}>{error}</p>}
    <fieldset disabled={busy}>
      <div className={s.inputPair}><label>Name<input value={name} onChange={e=>setName(e.target.value)}/></label><label>Description<input value={definition.description} onChange={e=>patch({description:e.target.value})}/></label></div>
      <section><h2>Public interface</h2><p>Only mapped values can reach the graph. Unmapped required values remain visible as preparation gaps.</p>
        <div className={s.checkRow}>{(['generate','continue','combine','custom'] as const).map(action=><label key={action}><input type="checkbox" checked={definition.actions.includes(action)} onChange={e=>patch({actions:e.target.checked?[...definition.actions,action]:definition.actions.filter(a=>a!==action)})}/>{action}</label>)}</div>
        <h3>Words and controls</h3>
        {definition.inputs.map((control,index)=><article className={s.mappingCard} key={index}>
          <div className={s.mappingRow}><label>Key<input value={control.key} onChange={e=>updateControl(index,{key:e.target.value})}/></label><label>Label<input value={control.label} onChange={e=>updateControl(index,{label:e.target.value})}/></label>
            <label>Type<select value={control.type} onChange={e=>updateControl(index,{type:e.target.value as Control['type'],default:e.target.value==='bool'?false:e.target.value==='seed'?{mode:'random'}:e.target.value==='int'||e.target.value==='float'?0:''})}>
              {['text','int','float','enum','seed','bool'].map(type=><option key={type}>{type}</option>)}</select></label>
            <button onClick={()=>patch({inputs:definition.inputs.filter((_,i)=>i!==index)})}>Remove</button></div>
          <p className={s.hint}>{control.key==='prompt'?'This control receives the direction prompt and source contribution instructions.':'Use prompt as the key for the shared instruction field.'}</p>
          {control.targets.map((target,i)=><div className={s.mappingRow} key={i}><TargetPicker graph={graph} target={target} onChange={target=>updateControl(index,{targets:control.targets.map((old,j)=>j===i?target:old)})}/>
            <button onClick={()=>updateControl(index,{targets:control.targets.filter((_,j)=>j!==i)})}>Remove mapping</button></div>)}
          <button onClick={()=>updateControl(index,{targets:[...control.targets,{node_id:'',field:''}]})}>+ Graph input</button>
          <div className={s.mappingRow}><DefaultValue control={control} update={value=>updateControl(index,{default:value})}/>
            {(['int','float'].includes(control.type))&&(['min','max','step'] as const).map(key=><label key={key}>{key}<input type="number" step="any" value={control[key]??''} onChange={e=>updateControl(index,{[key]:e.target.value===''?null:Number(e.target.value)})}/></label>)}
            <label className={s.checkLabel}><input type="checkbox" checked={control.required} onChange={e=>updateControl(index,{required:e.target.checked})}/>Required</label></div>
          {control.type==='enum'&&<label>Choices, one per line<textarea value={control.choices.map(String).join('\n')} onChange={e=>updateControl(index,{choices:e.target.value.split('\n')})}/></label>}
          {Object.keys(control.when).length>0&&<p className={s.hint}>Active when {JSON.stringify(control.when)}. Edit conditions in the complete description below.</p>}
        </article>)}
        <button onClick={()=>patch({inputs:[...definition.inputs,{key:'',label:'',type:'text',default:'',required:false,targets:[{node_id:'',field:''}],choices:[],when:{}}]})}>+ Expose control</button>
        <h3>Ordered sources</h3>
        {definition.sources.map((source,index)=><article className={s.mappingCard} key={index}><div className={s.mappingRow}>
          <span className={s.mono}>{index+1}</span><label>Label<input value={source.label} onChange={e=>updateSource(index,{label:e.target.value})}/></label><label>Role<input value={source.role} onChange={e=>updateSource(index,{role:e.target.value})}/></label>
          <label>Kind<select value={source.kind} onChange={e=>updateSource(index,{kind:e.target.value as Source['kind']})}><option>image</option><option>mask</option></select></label></div>
          <TargetPicker graph={graph} target={source} onChange={target=>updateSource(index,target)}/><div className={s.actions}>
            <button disabled={index===0} onClick={()=>{const sources=[...definition.sources];[sources[index-1],sources[index]]=[sources[index],sources[index-1]];patch({sources});}}>Move up</button>
            <button onClick={()=>patch({sources:definition.sources.filter((_,i)=>i!==index)})}>Remove source</button></div></article>)}
        <button onClick={()=>patch({sources:[...definition.sources,{node_id:'',field:'',label:'',role:'',kind:'image',when:{}}]})}>+ Source input</button>
      </section>
      <section><h2>Returned files</h2><p>Choose one primary result. All reported files are retained, including files outside these declared roles.</p>
        {definition.outputs.map((output,index)=><article className={s.mappingCard} key={index}>
          <div className={s.mappingRow}><label>Result node<select value={output.node_id} onChange={e=>updateOutput(index,{node_id:e.target.value})}><option value="">Choose node</option>{Object.entries(graph).map(([id,node])=><option value={id} key={id}>{id} · {node.class_type??'Unknown'}</option>)}</select></label>
            <label>Runtime file field<input value={output.field} onChange={e=>updateOutput(index,{field:e.target.value})} placeholder="images"/></label>
            <label>File index <span className={s.hint}>blank means every file</span><input type="number" min={0} value={output.ordinal??''} onChange={e=>updateOutput(index,{ordinal:e.target.value===''?null:Number(e.target.value)})}/></label></div>
          <div className={s.mappingRow}><label>Role<select value={output.role} onChange={e=>updateOutput(index,{role:e.target.value as Output['role']})}>{['final','alternative','layer','mask','preview','companion','intermediate','unclassified'].map(role=><option key={role}>{role}</option>)}</select></label>
            <label>Material<select value={output.payload} onChange={e=>updateOutput(index,{payload:e.target.value as Output['payload']})}><option>image</option><option>3d</option><option>file</option></select></label>
            <label className={s.checkLabel}><input type="checkbox" checked={output.primary} onChange={e=>updateOutput(index,{primary:e.target.checked,required:e.target.checked||output.required})}/>Primary</label>
            <label className={s.checkLabel}><input type="checkbox" checked={output.required} onChange={e=>updateOutput(index,{required:e.target.checked})}/>Required</label>
            <button onClick={()=>patch({outputs:definition.outputs.filter((_,i)=>i!==index)})}>Remove</button></div>
        </article>)}
        <button onClick={()=>patch({outputs:[...definition.outputs,{node_id:'',field:'images',ordinal:null,payload:'image',role:'final',primary:definition.outputs.length===0,required:true,when:{}}]})}>+ Result mapping</button>
      </section>
      <section><h2>This installation</h2><p>Uses the configured ComfyUI runtime. Credentials stay with the runtime installation. These declarations do not establish successful execution.</p>
        <label>Known packages, models, assets and first-use requirements<textarea value={definition.requirements} onChange={e=>patch({requirements:e.target.value})}/></label>
        <div className={s.inputPair}><label>Processing cost<select value={binding.cost_review} onChange={e=>setBinding({...binding,cost_review:e.target.value as Binding['cost_review']})}>
          <option value="unknown">Unresolved</option><option value="unmetered">Reviewed: no paid processing</option><option value="paid">Paid processing needs a supported cost bound</option></select></label>
          <label>First-use downloads<select value={binding.downloads} onChange={e=>setBinding({...binding,downloads:e.target.value as Binding['downloads']})}><option value="unknown">Unknown</option><option value="none">None required</option><option value="ready">Required assets already prepared</option><option value="required">Preparation still required</option></select></label></div>
        <label>Cost review basis<textarea value={binding.cost_note} onChange={e=>setBinding({...binding,cost_note:e.target.value})} placeholder="What establishes that this graph and its exposed controls use no paid services?"/></label>
        <label>Installation notes<textarea value={binding.dependency_note} onChange={e=>setBinding({...binding,dependency_note:e.target.value})}/></label>
        <button onClick={()=>void run(inspect)}>Save and inspect requirements</button>
        {inspection&&<div className={s.runtimeReport}><p>Checked {inspection.checked_at&&new Date(inspection.checked_at).toLocaleString()} · {inspection.runtime_url}</p>
          {inspection.gaps?.map((gap,i)=><p key={i}>{gap}</p>)}{inspection.findings?.map((finding,i)=><p key={i}>{finding.name} · {finding.state}{finding.package?` · ${finding.package}`:''}</p>)}
          {inspection.limitations?.map((limit,i)=><p className={s.hint} key={i}>{limit}</p>)}</div>}
      </section>
      <section><h2>Definition and adaptations</h2>
        <details><summary>Original supplied definition · {saved.source_kind}</summary><p className={s.hash}>{saved.source_name} · SHA-256 {saved.source_sha256}</p><pre>{saved.source_text||'No definition supplied yet.'}</pre></details>
        <details><summary>Adapted API graph</summary><p className={s.hint}>Changing the graph requires an adaptation note. SaveImage file prefixes are assigned by execution storage.</p><textarea className={s.codeInput} aria-label="Adapted API graph" value={graphText} spellCheck={false} onChange={e=>{setGraphText(e.target.value);setAdaptation('');setBinding({...binding,cost_review:'unknown'});}}/></details>
        <details onToggle={e=>{if(e.currentTarget.open)setAdvanced(JSON.stringify(definition,null,2));}}><summary>Complete public description, including conditions</summary>
          <p className={s.hint}>Each when object names public controls and the values that activate an input or result. Saving checks this description and clears the installation cost review.</p>
          <textarea className={s.codeInput} value={advanced} spellCheck={false} onChange={e=>setAdvanced(e.target.value)}/><button onClick={()=>void run(async()=>{const body={revision:saved.revision,name,definition:JSON.parse(advanced) as components['schemas']['OperationDefinition'],binding:{...binding,cost_review:'unknown' as const},adaptation_note:adaptation};const next=await result(fetchClient.PATCH('/api/crexil/registrations/{rid}',{params:{path:{rid:item.id}},body:{...delivery(body),...body}}));setSaved(next);setDefinition(normalize(next.definition));setBinding(next.binding);setGraphText(JSON.stringify(next.definition.graph,null,2));pending.current=null;refreshed();})}>Save complete description</button></details>
        <label>Adaptation note<textarea value={adaptation} onChange={e=>setAdaptation(e.target.value)} placeholder="What changed, and what does it mean for future requests?"/></label>
        <details><summary>Earlier revisions · {saved.revisions.length}</summary>{saved.revisions.map((revision,i)=><p key={i}>Revision {String(revision.revision)} · {String(revision.name)} · {String(revision.adaptation_note||'Original interface preparation')}</p>)}</details>
      </section>
      {saved.gaps.length>0&&<section className={s.runtimeReport}><h2>Preparation gaps</h2>{saved.gaps.map((gap,i)=><p key={i}>{gap}</p>)}</section>}
      <section><h2>Recorded evidence</h2><p>Successful execution supplies evidence for its tested case. It does not by itself qualify a product action.</p>
        {saved.evidence.length===0?<p className={s.hint}>No observed execution is attached to this registration.</p>:saved.evidence.map((evidence,i)=><p key={i}><a href={projectHref(String(evidence.workspace_id))}>{String(evidence.status)} · revision {String(evidence.registration_revision)} · {String(evidence.kind_of_work)}</a>{evidence.cause?` · ${String(evidence.cause)}`:''}</p>)}
      </section>
    </fieldset>
    <div className={s.registrationFoot}><span>{dirty?'Save your changes before leaving this operation.':'Original definition and revisions retained.'}</span><button disabled={busy} onClick={()=>void run(async()=>{await save();})}>Save preparation</button>
      <a href={`/api/crexil/registrations/${item.id}/export`} download={`${saved.name}.json`}>Export saved description</a>
      <button className={s.primary} disabled={busy||!project} title={!project?'Choose a project for the test.':undefined} onClick={()=>void run(prepareTest)}>Prepare recorded test</button></div>
  </div>;
}

function TargetPicker({graph,target,onChange}:{graph:Graph;target:Target;onChange:(target:Target)=>void}) {
  const options=Object.entries(graph).flatMap(([node_id,node])=>Object.entries(node.inputs??{}).filter(([,value])=>!Array.isArray(value)&&typeof value!=='object').map(([field])=>({node_id,field,label:`${node_id} · ${node.class_type} · ${field}`})));
  return <label>Graph input<select value={target.node_id?JSON.stringify([target.node_id,target.field]):''} onChange={e=>{const [node_id,field]=e.target.value?JSON.parse(e.target.value):['',''];onChange({node_id,field});}}><option value="">Choose input</option>
    {target.node_id&&!options.some(o=>o.node_id===target.node_id&&o.field===target.field)&&<option value={JSON.stringify([target.node_id,target.field])}>Unavailable mapping: {target.node_id}.{target.field}</option>}
    {options.map(o=><option key={o.label} value={JSON.stringify([o.node_id,o.field])}>{o.label}</option>)}</select></label>;
}

function DefaultValue({control,update}:{control:Control;update:(value:unknown)=>void}) {
  if(control.type==='enum')return <label>Default<select value={control.choices.findIndex(choice=>JSON.stringify(choice)===JSON.stringify(control.default))} onChange={e=>update(control.choices[Number(e.target.value)])}><option value="-1" disabled>Choose a value</option>{control.choices.map((choice,index)=><option value={index} key={index}>{String(choice)}</option>)}</select></label>;
  if(control.type==='bool')return <label>Default<select value={String(control.default??false)} onChange={e=>update(e.target.value==='true')}><option>false</option><option>true</option></select></label>;
  if(control.type==='seed'){const seed=(control.default??{mode:'random'}) as {mode:string;value?:number};return <div className={s.inputPair}><label>Default seed<select value={seed.mode} onChange={e=>update(e.target.value==='random'?{mode:'random'}:{mode:e.target.value,value:seed.value??0})}><option value="random">Random</option><option value="fixed">Fixed sequence</option><option value="locked">Same for all attempts</option></select></label>{seed.mode!=='random'&&<label>Value<input type="number" min={0} value={seed.value??0} onChange={e=>update({...seed,value:Number(e.target.value)})}/></label>}</div>;}
  const numeric=['int','float'].includes(control.type);
  return <label>Default<input type={numeric?'number':'text'} step="any" value={String(control.default??'')} onChange={e=>update(numeric?Number(e.target.value):e.target.value)}/></label>;
}
