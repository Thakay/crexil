// Region ink is saved as immutable source-bound mask versions. The parent owns
// instructions and direction revision; mask writes serialize and replay by ID.
// A bitmap replacement holds drawing until accepted pixels decode. A validation
// refusal keeps the pixels but rebuilds the command; uncertain delivery replays it.
import {useEffect,useRef,useState,type KeyboardEvent} from 'react';
import {$api,fetchClient} from '../../api/client';
import type {components} from '../../generated/openapi';
import {MaskCanvas,type NaturalSize} from '../regions/MaskCanvas';
import {ACTIONS,ACTION_COLORS,rasterize,exportMaskB64,type DrawTool,type Ink,type RegionDraft} from '../regions/model';
import {ActionError,command,imageUrl,label,message,result,type Material,type Preparation} from './api';
import s from './Filmstrip.module.css';

type Instruction=components['schemas']['RegionInstruction'];
type Mask=components['schemas']['MaskView'];
type Intent={kind:'ink'|'import';data:string}|{kind:'adjust';previousId:string};
type Work={key:string;intent:Intent;body:components['schemas']['SaveMask'];saved?:Mask};

async function canvasFromMask(mask:Mask):Promise<HTMLCanvasElement>{
 const image=new Image();image.src=`/api/crexil/masks/${mask.id}/image`;
 await image.decode();
 const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
 const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);
 const pixels=ctx.getImageData(0,0,canvas.width,canvas.height);
 for(let i=0;i<pixels.data.length;i+=4){const alpha=pixels.data[i]>=128?255:0;pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=255;pixels.data[i+3]=alpha;}
 ctx.putImageData(pixels,0,0);return canvas;
}

export function RegionEditor({wid,source,setup,update,onPending,open,close}:{
 wid:string;source:Material;setup:Preparation;update:(patch:Partial<Preparation>)=>void;
 onPending:(pending:boolean)=>void;open:boolean;close:()=>void;
}){
 const sourceInfo=$api.useQuery('get','/api/crexil/projects/{wid}/mask-source/{nid}',{params:{path:{wid,nid:source.id}}});
 const library=$api.useQuery('get','/api/crexil/projects/{wid}/masks',{params:{path:{wid}}});
 const [active,setActive]=useState<string|null>(setup.regions?.[0]?.key??null);
 const [tool,setTool]=useState<DrawTool>('brush');
 const [brush,setBrush]=useState(32);
 const [error,setError]=useState('');
 const [status,setStatus]=useState('Saved masks');
 const [tick,setTick]=useState(0);
 const [adjusting,setAdjusting]=useState('');
 const [resizeMask,setResizeMask]=useState(false);
 const [adjustNote,setAdjustNote]=useState('');
 const bitmaps=useRef(new Map<string,{mask:HTMLCanvasElement;version:number;maskId:string|null}>());
 const queued=useRef(new Map<string,Intent>());
 const replacing=useRef(false);
 const reusePending=useRef<{key:string;mask:Mask}|null>(null);
 const room=useRef<HTMLElement>(null);
 const inFlight=useRef<Work|null>(null);
 const saving=useRef(false);
 const latest=useRef(setup);latest.current=setup;
 const updateRef=useRef(update);updateRef.current=update;
 const pendingRef=useRef(onPending);pendingRef.current=onPending;
 const adjustment=useRef({resize_to_source:resizeMask,explanation:adjustNote});
 adjustment.current={resize_to_source:resizeMask,explanation:adjustNote};
 const regions=setup.regions??[];
 const masks=library.data??[];
 const selected=regions.find(r=>r.key===active);
 useEffect(()=>{
  if(!open)return;
  const origin=document.activeElement as HTMLElement|null;
  room.current?.focus();
  return()=>{if(origin?.isConnected)origin.focus();};
 },[open]);
 const containFocus=(event:KeyboardEvent<HTMLElement>)=>{
  if(event.key!=='Tab')return;
  const targets=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]'));
  const first=targets[0],last=targets[targets.length-1];
  if(event.shiftKey&&(document.activeElement===first||document.activeElement===room.current)){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===room.current)){event.preventDefault();first?.focus();}
 };
 const replaceRegions=(next:Instruction[])=>{
  latest.current={...latest.current,regions:next};updateRef.current({regions:next});
 };
 const patch=(key:string,values:Partial<Instruction>)=>replaceRegions((latest.current.regions??[]).map(r=>r.key===key?{...r,...values}:r));
 const add=()=>{
  if((latest.current.regions??[]).length>=8)return null;
  const key=crypto.randomUUID();replaceRegions([...(latest.current.regions??[]),{key,name:`Region ${(latest.current.regions??[]).length+1}`,action:'modify',prompt:'',mask_id:null}]);setActive(key);return key;
 };
 useEffect(()=>{
  let cancelled=false;
  for(const region of regions){
   if(!region.mask_id||replacing.current||queued.current.has(region.key)||inFlight.current?.key===region.key)continue;
   const saved=masks.find(mask=>mask.id===region.mask_id);
   if(!saved||saved.source_node_id!==source.id||saved.source_sha256!==sourceInfo.data?.sha256||bitmaps.current.get(region.key)?.maskId===saved.id)continue;
   void canvasFromMask(saved).then(mask=>{if(!cancelled&&!queued.current.has(region.key)&&!replacing.current){bitmaps.current.set(region.key,{mask,version:1,maskId:saved.id});setTick(t=>t+1);}}).catch(err=>{if(!cancelled)setError(message(err));});
  }
  return()=>{cancelled=true;};
 },[setup.regions,library.data,source.id,sourceInfo.data?.sha256,tick]);
 const flush=async()=>{
  if(saving.current||!sourceInfo.data)return;
  saving.current=true;setError('');setStatus('Saving masks');pendingRef.current(true);
  try{
   while(inFlight.current||queued.current.size){
    if(!inFlight.current){
     const [key,intent]=queued.current.entries().next().value!;
     queued.current.delete(key);
     const region=latest.current.regions?.find(r=>r.key===key);if(!region)continue;
     inFlight.current={key,intent,body:{...command(),source_node_id:source.id,source_sha256:sourceInfo.data.sha256,name:region.name,
      ...(intent.kind==='adjust'?{previous_mask_id:intent.previousId,adjustment:adjustment.current}:{data_base64:intent.data,previous_mask_id:region.mask_id})}};
    }
    const work=inFlight.current;
    const saved=work.saved??await result(fetchClient.POST('/api/crexil/projects/{wid}/masks',{params:{path:{wid}},body:work.body}));
    work.saved=saved;
    if(work.intent.kind!=='ink'){
     setStatus('Loading accepted mask');
     const mask=await canvasFromMask(saved);
     bitmaps.current.set(work.key,{mask,version:(bitmaps.current.get(work.key)?.version??0)+1,maskId:saved.id});
     replacing.current=false;setAdjusting('');setTick(t=>t+1);
    }else{
     const bitmap=bitmaps.current.get(work.key);if(bitmap)bitmap.maskId=saved.id;
    }
    patch(work.key,{mask_id:saved.id});inFlight.current=null;
    void library.refetch();
   }
   setStatus('Saved masks');pendingRef.current(false);
  }catch(err){
   if(err instanceof ActionError&&err.status===422&&inFlight.current){
    const work=inFlight.current;
    // Refused commands cannot have committed. Keep the pixels, then rebuild
    // with corrected fields and a new ID. Uncertain delivery retains its ID.
    if(!queued.current.has(work.key))queued.current.set(work.key,work.intent);
    inFlight.current=null;
   }
   setError(message(err));setStatus('Mask not saved');
  }
  finally{saving.current=false;}
 };
 useEffect(()=>{
  if(!queued.current.size||error)return;
  const timer=setTimeout(()=>void flush(),300);return()=>clearTimeout(timer);
 },[tick,error,sourceInfo.data]);
 const ink=(key:string,stroke:Ink,natural:NaturalSize)=>{
  if(replacing.current)return;
  if(!sourceInfo.data){setError('Wait for source coordinates before drawing.');return;}
  const previous=bitmaps.current.get(key);
  const instruction=latest.current.regions?.find(r=>r.key===key);
  const saved=masks.find(mask=>mask.id===instruction?.mask_id);
  if(saved&&(saved.source_node_id!==source.id||saved.source_sha256!==sourceInfo.data.sha256)){
   setError('This mask belongs to different source bytes. Reuse it with an explicit adjustment before drawing.');return;
  }
  if(instruction?.mask_id&&previous?.maskId!==instruction.mask_id){setError('Wait for the saved mask to load before drawing.');return;}
  const mask=rasterize(previous?.mask??null,stroke,natural);
  const version=(previous?.version??0)+1;
  bitmaps.current.set(key,{mask,version,maskId:previous?.maskId??null});
  const draft:RegionDraft={key,name:'',action:'modify',prompt:'',mask,inkRev:version,inked:true,visible:true};
  queued.current.set(key,{kind:'ink',data:exportMaskB64(draft,natural)});pendingRef.current(true);setStatus('Unsaved ink');setTick(t=>t+1);
 };
 const importMask=async(file:File)=>{
  if(!selected||!sourceInfo.data||status!=='Saved masks')return;
  const key=selected.key;
  replacing.current=true;pendingRef.current(true);setStatus('Reading imported mask');setError('');
  try{
   const bytes=new Uint8Array(await file.arrayBuffer());let text='';
   for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
   queued.current.set(key,{kind:'import',data:btoa(text)});setTick(t=>t+1);
   await flush();
  }catch(err){setError(message(err));setStatus('Mask not saved');}
 };
 const reuse=async(mask:Mask,key=selected?.key)=>{
  if(!key||status!=='Saved masks')return;
  if(mask.source_node_id!==source.id||mask.source_sha256!==sourceInfo.data?.sha256){setAdjusting(mask.id);return;}
  reusePending.current={key,mask};
  replacing.current=true;pendingRef.current(true);setStatus('Loading saved mask');setError('');
  try{
   const pixels=await canvasFromMask(mask);
   bitmaps.current.set(key,{mask:pixels,version:(bitmaps.current.get(key)?.version??0)+1,maskId:mask.id});
   patch(key,{mask_id:mask.id});reusePending.current=null;
  }catch(err){setError(message(err));}
  finally{replacing.current=false;pendingRef.current(false);setStatus('Saved masks');setTick(t=>t+1);}
 };
 const adjust=async()=>{
  if(!selected||!sourceInfo.data||!adjusting||status!=='Saved masks')return;
  replacing.current=true;pendingRef.current(true);setStatus('Saving adjustment');setError('');
  queued.current.set(selected.key,{kind:'adjust',previousId:adjusting});
  await flush();
 };
 const bitmap=selected?bitmaps.current.get(selected.key):undefined;
 const selectedMask=masks.find(mask=>mask.id===selected?.mask_id);
 const foreignMask=Boolean(selectedMask&&(selectedMask.source_node_id!==source.id||selectedMask.source_sha256!==sourceInfo.data?.sha256));
 const drawingBlocked=replacing.current||foreignMask||Boolean(selected?.mask_id&&bitmap?.maskId!==selected.mask_id);
 const drafts:RegionDraft[]=regions.map(region=>{
  const bitmap=bitmaps.current.get(region.key);
  const saved=masks.find(mask=>mask.id===region.mask_id);
  const compatible=!saved||(saved.source_node_id===source.id&&saved.source_sha256===sourceInfo.data?.sha256);
  return {key:region.key,name:region.name,action:region.action??'modify',prompt:region.prompt??'',mask:compatible?bitmap?.mask??null:null,inkRev:bitmap?.version??0,inked:Boolean(bitmap),visible:true};
 });
 if(!open)return null;
 return <section ref={room} tabIndex={-1} className={s.regionRoom} aria-label="Region editor" onKeyDown={containFocus}>
  <header className={s.drawerHead}><strong>Regions · {label(source)}</strong><span role="status">{status}</span><button disabled={status!=='Saved masks'} onClick={close}>Back to preparation · Esc</button></header>
  {error&&<div className={s.error} role="alert">{error} <button disabled={saving.current} onClick={()=>{setTick(t=>t+1);if(reusePending.current)void reuse(reusePending.current.mask,reusePending.current.key);else void flush();}}>Retry mask save</button>
   {status!=='Saved masks'&&<button disabled={saving.current} onClick={()=>{inFlight.current=null;queued.current.clear();bitmaps.current.clear();replacing.current=false;setAdjusting('');setStatus('Saved masks');setError('');pendingRef.current(false);setTick(t=>t+1);}}>Keep earlier saved masks</button>}</div>}
  {sourceInfo.error&&<p className={s.error} role="alert">{message(sourceInfo.error)}</p>}
  <div className={s.regionBody}>
   <div className={s.regionDrawing}><div className={s.actions}>
    {(['brush','rect','lasso','erase'] as const).map(value=><button key={value} aria-pressed={tool===value} onClick={()=>setTool(value)}>{value==='rect'?'Rectangle':value[0].toUpperCase()+value.slice(1)}</button>)}
    <label>Brush pixels<input type="number" min={1} max={512} value={brush} onChange={e=>setBrush(Math.max(1,Math.min(512,Number(e.target.value))))}/></label>
   </div>
   {sourceInfo.data?<MaskCanvas sourceUrl={imageUrl(source.id,true)} drafts={drafts} activeKey={active} tool={tool} brushSize={brush} disabled={drawingBlocked} onInk={ink} onCreateRegion={add}/>:<p>Reading source coordinates…</p>}
   {foreignMask&&<p className={s.error}>This mask belongs to different source bytes. Choose it under Reuse a saved mask to make an explicit adjustment.</p>}
   <p className={s.hint}>Draw in source pixels. Protected regions always win. Masks save with this source and can be reused after reopening.</p></div>
   <aside className={s.regionSettings}>
    <button disabled={regions.length>=8||replacing.current} onClick={add}>+ Add region</button>
    {regions.map((region,i)=><div className={`${s.regionItem} ${active===region.key?s.regionActive:''}`} key={region.key}>
     <button disabled={replacing.current&&active!==region.key} onClick={()=>setActive(region.key)} style={{borderLeftColor:ACTION_COLORS[region.action??'modify']}}>{i+1}. {region.name} · {region.action}</button>
     {active===region.key&&<>
      <label>Region name<input value={region.name} onChange={e=>patch(region.key,{name:e.target.value})}/></label>
      <label><span id={`action-${region.key}`}>Region action</span><select aria-labelledby={`action-${region.key}`} value={region.action} onChange={e=>patch(region.key,{action:e.target.value as Instruction['action']})}>{ACTIONS.map(action=><option key={action}>{action}</option>)}</select></label>
      <label><span id={`instruction-${region.key}`}>Region instruction</span><textarea aria-labelledby={`instruction-${region.key}`} value={region.prompt} onChange={e=>patch(region.key,{prompt:e.target.value})} placeholder={region.action==='keep'||region.action==='delete'?'This action does not use an instruction.':'Describe the change in this region.'}/></label>
      <p className={s.micro}>{region.mask_id?`Saved mask ${region.mask_id}`:'No saved ink yet'}</p>
      <div className={s.actions}><button disabled={i===0} onClick={()=>{const next=[...regions];[next[i-1],next[i]]=[next[i],next[i-1]];replaceRegions(next);}}>Earlier</button><button disabled={i===regions.length-1} onClick={()=>{const next=[...regions];[next[i+1],next[i]]=[next[i],next[i+1]];replaceRegions(next);}}>Later</button>
       <button disabled={status!=='Saved masks'} onClick={()=>{replaceRegions(regions.filter(r=>r.key!==region.key));setActive(null);}}>Remove from request</button></div>
     </>}
    </div>)}
    {selected&&<><label>Import PNG mask<input type="file" accept="image/png" disabled={status!=='Saved masks'} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void importMask(file);}}/></label>
     <label><span id={`reuse-${source.id}`}>Reuse a saved mask</span><select aria-labelledby={`reuse-${source.id}`} value="" disabled={status!=='Saved masks'} onChange={e=>{const mask=masks.find(m=>m.id===e.target.value);if(mask)void reuse(mask);}}><option value="">Choose a mask…</option>{masks.map(mask=><option key={mask.id} value={mask.id} disabled={!mask.available}>{mask.name} · {mask.width} × {mask.height} · {mask.id}{mask.source_node_id!==source.id?' · needs adjustment':''}</option>)}</select></label></>}
    {adjusting&&<div className={s.regionItem}><h3>Adjust mask for this source</h3><p>The earlier mask belongs to another source. This saves an explicit new version for the current source.</p>
     <label><span id={`adjust-${source.id}`}>Coordinate adjustment</span><select aria-labelledby={`adjust-${source.id}`} value={String(resizeMask)} onChange={e=>setResizeMask(e.target.value==='true')}><option value="false">Same pixel coordinates, matching dimensions</option><option value="true">Resize with nearest pixels to fit this source</option></select></label>
     <label>Reason for adjustment<input value={adjustNote} onChange={e=>setAdjustNote(e.target.value)}/></label><button disabled={!adjustNote.trim()||status!=='Saved masks'} onClick={()=>void adjust()}>Save and inspect adjusted mask</button><button disabled={status!=='Saved masks'} onClick={()=>setAdjusting('')}>Cancel adjustment</button></div>}
   </aside>
  </div>
 </section>;
}
