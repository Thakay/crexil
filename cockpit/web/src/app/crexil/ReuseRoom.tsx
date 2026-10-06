// Reuse imports an independent copy through the shared server command. An
// uncertain acceptance keeps its exact reviewed request across room/reload.
import {useState} from 'react';
import type {components} from '../../generated/openapi';
import {$api,fetchClient} from '../../api/client';
import {ActionError,command,label,message,result} from './api';
import {MaterialThumbnail} from './MaterialThumbnail';
import s from './Filmstrip.module.css';

type Review=components['schemas']['ReuseReview'];
type Create=components['schemas']['CreateReuse'];
type Imported=components['schemas']['ImportResult'];

export function ReuseRoom({wid,close,chooseFiles,refresh,inspect}:{wid:string;close:()=>void;chooseFiles:()=>void;refresh:()=>void;inspect:(id:string)=>void}){
  const key=`crexil.reuse.${wid}`;
  const [pending,setPending]=useState<Create|null>(()=>{try{return JSON.parse(sessionStorage.getItem(key)??'null');}catch{return null;}});
  const [source,setSource]=useState(pending?.source_project_id??'');
  const [selected,setSelected]=useState(pending?.node_id??'');
  const [explanation,setExplanation]=useState(pending?.explanation??'');
  const [review,setReview]=useState<Review|null>(null);
  const [saved,setSaved]=useState<Imported|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const projects=$api.useQuery('get','/api/crexil/projects');
  const material=$api.useQuery('get','/api/crexil/projects/{wid}',{params:{path:{wid:source}}},{enabled:Boolean(source)});
  const clear=()=>{setPending(null);try{sessionStorage.removeItem(key);}catch{/* Current state remains usable. */}};
  const check=async()=>{
    setBusy(true);setError('');setReview(null);
    try{setReview(await result(fetchClient.POST('/api/crexil/projects/{wid}/reuse/review',{
      params:{path:{wid}},body:{source_project_id:source,node_id:selected}})));}
    catch(err){setError(message(err));}finally{setBusy(false);}
  };
  const receive=async()=>{
    if(!pending&&!review?.ready)return;
    const body=pending??{...command(),source_project_id:source,node_id:selected,review_token:review!.token,explanation};
    setPending(body);setBusy(true);setError('');
    try{sessionStorage.setItem(key,JSON.stringify(body));}catch{/* Keep the exact command in memory. */}
    try{
      const copied=await result(fetchClient.POST('/api/crexil/projects/{wid}/reuse',{params:{path:{wid}},body}));
      setSaved(copied);clear();refresh();
    }catch(err){
      if(err instanceof ActionError&&[404,409,422].includes(err.status??0)){clear();setReview(null);}
      setError(message(err));
    }finally{setBusy(false);}
  };
  return <section className={s.sequenceRoom} aria-label="Import material" onKeyDownCapture={event=>{if(busy&&event.key==='Escape'){event.preventDefault();event.stopPropagation();}}}>
    <header className={s.roomHead}><strong>Import material</strong><span className={s.hint}>Original bytes and known origin</span><button autoFocus disabled={busy} onClick={close}>Esc · Project</button></header>
    <div className={s.sequenceMain}><div className={s.sequenceFields}>
      <span className={s.eyebrow}>A new starting point</span><h1>Bring material into this project.</h1>
      <p className={s.sequenceInstructions}>Choose a file, or reuse material from another project. Reuse keeps an independent copy with its available related files and recorded origin.</p>
      {saved?<section className={s.sequenceReview} role="status"><h2>Material imported</h2><p>{saved.direction.title}</p><p>The source project and its earlier work are unchanged.</p><button className={s.primary} onClick={()=>inspect(String(saved.node.id))}>Inspect imported material</button></section>:<>
        <button disabled={busy||Boolean(pending)} onClick={chooseFiles}>Choose files</button>
        <fieldset className={s.deliveryForm} disabled={busy||Boolean(pending)}><legend>Or choose from another project</legend>
          <label>Source project<select aria-label="Source project" value={source} onChange={event=>{setSource(event.target.value);setSelected('');setReview(null);setError('');}}><option value="">Choose a project</option>{projects.data?.filter(project=>project.id!==wid).map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
          {projects.isLoading&&<p role="status">Loading projects…</p>}
          {Boolean(projects.error)&&<p role="alert" className={s.error}>{message(projects.error)} <button onClick={()=>void projects.refetch()}>Reload projects</button></p>}
          {source&&material.isLoading&&<p role="status">Loading source material…</p>}
          {source&&material.error&&<p role="alert" className={s.error}>{message(material.error)} <button onClick={()=>void material.refetch()}>Reload material</button></p>}
          {source&&material.data&&<><div className={s.deliveryChoices}>{material.data.nodes.map(node=><label key={node.id} className={s.deliveryChoice}>
            <input type="radio" name="reuse-material" value={node.id} checked={selected===node.id} onChange={()=>{setSelected(node.id);setReview(null);}}/>
            <MaterialThumbnail node={node} decorative/><span>{label(node)}<small>{node.payload==='3d'?'3D material':'Image'}{!node.available?' · Original unavailable':''}</small></span>
          </label>)}</div>{material.data.nodes.length===0&&<p>This project has no material yet.</p>}</>}
          <label>Why bring it here? <span className={s.hint}>optional</span><textarea aria-label="Why bring it here?" maxLength={4000} value={explanation} onChange={event=>setExplanation(event.target.value)} placeholder="Describe what this source contributes."/></label>
          <div className={s.actions}><button disabled={!selected} onClick={()=>void check()}>Review import</button></div>
        </fieldset>
        {review&&<section className={s.sequenceReview} aria-label="Import review"><h2>{review.filename}</h2><p>From {review.source_project_name}</p>
          <p>{review.files.length} files · {(review.total_bytes/1024/1024).toFixed(2)} MB. Original bytes are copied; no generation is requested.</p>
          <p>Recorded source relationships remain in the imported provenance. The new material can become a source for directions in this project.</p>
          {review.missing.map(note=><p key={note}>{note}</p>)}{review.errors.map(note=><p key={note} className={s.error}>{note}</p>)}
          <details><summary>Files and hashes</summary>{review.files.map((file,index)=><p className={s.deliveryPath} key={index}>{file.filename} · {file.size_bytes.toLocaleString()} bytes<br/>{file.sha256}</p>)}</details>
          <button className={s.primary} disabled={busy||!review.ready||Boolean(pending)} onClick={()=>void receive()}>Import independent copy</button>
        </section>}
        {pending&&<div className={s.sequenceNotice}><p>Acceptance is unconfirmed. Retry the saved request to recover its outcome without creating another copy.</p><button disabled={busy} onClick={()=>void receive()}>Retry same import</button></div>}
        {busy&&<p role="status">Checking and copying material…</p>}{error&&<p role="alert" className={s.error}>{error}</p>}
      </>}
    </div></div>
  </section>;
}
