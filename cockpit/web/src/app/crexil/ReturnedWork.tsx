// Returned edits preserve reported relationships without claiming observation.
// Only small immutable request metadata persists locally; after a reload, the
// server receipt resolves acceptance or the same original file can be reselected.
import {useEffect,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {ActionError,command,isDefiniteRefusal,label,message,result,type Material} from './api';
import {MaterialThumbnail} from './MaterialThumbnail';
import s from './Filmstrip.module.css';

type Import=components['schemas']['ImportMaterial'];
type Result=components['schemas']['ImportResult'];
type Pending=Omit<Import,'data_base64'>&{sha256:string};
const bodies=new Map<string,Import>();

export function ReturnedWork({wid,nodes,initial,close,refresh,inspect}:{wid:string;nodes:Material[];initial:string[];close:()=>void;refresh:()=>void;inspect:(id:string)=>void}){
  const key=`crexil.returned.${wid}`;
  const [pending,setPending]=useState<Pending|null>(()=>{try{return JSON.parse(sessionStorage.getItem(key)??'null');}catch{return null;}});
  const [parents,setParents]=useState<string[]>(pending?.parents??initial);
  const [tool,setTool]=useState(pending?.tool??'');
  const [explanation,setExplanation]=useState(pending?.explanation??'');
  const [file,setFile]=useState<File|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [saved,setSaved]=useState<Result|null>(null);
  const clear=()=>{setPending(null);bodies.delete(key);try{sessionStorage.removeItem(key);}catch{/* Current state still clears. */}};
  useEffect(()=>{
    if(!pending)return;
    let current=true;
    void result(fetchClient.GET('/api/crexil/projects/{wid}/imports/{request_id}',{params:{path:{wid,request_id:pending.request_id}}})).then(receipt=>{
      if(current){setSaved(receipt);clear();refresh();}
    }).catch(err=>{if(current)setError(err instanceof ActionError&&err.status===404?'No accepted import was found yet. Re-select the original file to retry the saved request.':message(err));});
    return()=>{current=false;};
    // The lookup runs once on entry; retries are explicit and preserve the body.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);
  const send=async()=>{
    setBusy(true);setError('');
    try{
      let body=bodies.get(key);
      if(!body){
        if(!file)throw Error('Choose the returned file. An unconfirmed import needs the same original file.');
        const buffer=await file.arrayBuffer();
        const sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',buffer))].map(v=>v.toString(16).padStart(2,'0')).join('');
        if(pending&&(pending.sha256!==sha256||pending.filename!==file.name))throw Error('This is a different file. Re-select the original file to resolve the saved import first.');
        const metadata=pending??{...command(),filename:file.name,parents,tool,explanation,sha256};
        const {sha256:_,...request}=metadata;void _;
        const bytes=new Uint8Array(buffer);let binary='';
        for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        body={...request,data_base64:btoa(binary)};bodies.set(key,body);setPending(metadata);
        try{sessionStorage.setItem(key,JSON.stringify(metadata));}catch{/* The immutable command remains in memory. */}
      }
      const receipt=await result(fetchClient.POST('/api/crexil/projects/{wid}/material',{params:{path:{wid}},body}));
      setSaved(receipt);clear();refresh();
    }catch(err){if(isDefiniteRefusal(err))clear();setError(message(err));}
    finally{setBusy(false);}
  };
  return <section className={s.sequenceRoom} aria-label="Returned work" onKeyDownCapture={event=>{if(busy&&event.key==='Escape'){event.preventDefault();event.stopPropagation();}}}>
    <div className={s.roomHead}><strong>Return edited material</strong><span className={s.hint}>Keep the original and the returned version</span><button disabled={busy} onClick={close}>Esc · Project</button></div>
    <div className={s.sequenceMain}><div className={s.sequenceFields}>
      <span className={s.eyebrow}>Work from another tool</span><h1>Bring the result back to its sources.</h1>
      <p className={s.sequenceInstructions}>Crexil keeps the exact file and the relationships you report. The external edit itself was not observed. You can continue from the returned version.</p>
      {saved?<div className={s.sequenceReview} role="status"><h2>Returned material saved</h2><p>{saved.direction.title}</p><button onClick={()=>inspect(String(saved.node.id))}>Inspect returned material</button></div>:<>
        <fieldset className={s.deliveryForm} disabled={busy||Boolean(pending)}><legend>Reported sources</legend>
          <div className={s.deliveryChoices}>{nodes.map(node=><label key={node.id} className={s.deliveryChoice}><input type="checkbox" checked={parents.includes(node.id)} onChange={event=>setParents(ids=>event.target.checked?[...ids,node.id]:ids.filter(id=>id!==node.id))}/><MaterialThumbnail node={node} decorative/><span>{label(node)}</span></label>)}</div>
          <label>Tool used<input maxLength={200} value={tool} onChange={event=>setTool(event.target.value)} placeholder="For example, Krita or Blender"/></label>
          <label>What changed?<textarea maxLength={4000} value={explanation} onChange={event=>setExplanation(event.target.value)} placeholder="Describe the edit or leave it unknown."/></label>
        </fieldset>
        <label className={s.returnFile}>Returned file<input type="file" disabled={busy} accept="image/png,image/jpeg,image/webp,image/tiff,.glb,model/gltf-binary" onChange={event=>setFile(event.target.files?.[0]??null)}/></label>
        {pending&&<p className={s.sequenceNotice}>Acceptance is unconfirmed for {pending.filename}. The sources and description stay fixed while this request is resolved.</p>}
        {error&&<p role="alert" className={s.error}>{error}</p>}
        <button className={s.primary} disabled={busy||(!file&&!bodies.has(key))} onClick={()=>void send()}>{busy?'Saving returned material…':pending?'Retry same import':'Save returned material'}</button>
      </>}
    </div></div>
  </section>;
}
