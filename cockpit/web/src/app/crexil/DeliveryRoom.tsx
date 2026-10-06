// Delivery owns a durable intent before copying. An uncertain response keeps
// its immutable command across room navigation and browser reload.
import {useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {$api,fetchClient} from '../../api/client';
import {Browser as DestinationBrowser} from '../promote/PromotePlinth';
import {command,isDefiniteRefusal,label,message,result,type Snapshot} from './api';
import {MaterialThumbnail} from './MaterialThumbnail';
import s from './Filmstrip.module.css';

type Review=components['schemas']['DeliveryReview'];
type Delivery=components['schemas']['DeliveryView'];
type Create=components['schemas']['CreateDelivery'];

export function DeliveryRoom({data,initial,close,refresh}:{data:Snapshot;initial:string[];close:()=>void;refresh:()=>void}){
  const key=`crexil.delivery.${data.project.id}`;
  const pending=useRef<Create|null>(null);
  const fields=useRef<HTMLFieldSetElement>(null);
  if(pending.current===null){try{pending.current=JSON.parse(sessionStorage.getItem(key)??'null');}catch{/* Storage is optional; the in-memory command remains usable. */}}
  const [selected,setSelected]=useState<string[]>(pending.current?.node_ids??initial);
  const [destination,setDestination]=useState(pending.current?.destination??'');
  const [review,setReview]=useState<Review|null>(null);
  const [browse,setBrowse]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState(pending.current?'Delivery acceptance is unconfirmed. Retry the saved request to recover its outcome.':'');
  const target=$api.useQuery('get','/api/promote-target');
  const history=$api.useQuery('get','/api/crexil/projects/{wid}/deliveries',{params:{path:{wid:data.project.id}}},{refetchInterval:3000});
  const update=()=>{void history.refetch();refresh();};
  const clearPending=()=>{pending.current=null;try{sessionStorage.removeItem(key);}catch{/* Keep the current room usable. */}};
  const transfer=async(delivery:Delivery)=>{
    await result(fetchClient.POST('/api/crexil/deliveries/{did}/transfer',{params:{path:{did:delivery.id}},body:command()}));
  };
  const deliver=async()=>{
    setBusy(true);setError('');
    try{
      if(!pending.current){if(!review?.ready)return;pending.current={...command(),node_ids:selected,destination:review.destination,review_token:review.token};}
      try{sessionStorage.setItem(key,JSON.stringify(pending.current));}catch{/* The command still remains fixed in memory. */}
      const saved=await result(fetchClient.POST('/api/crexil/projects/{wid}/deliveries',{params:{path:{wid:data.project.id}},body:pending.current}));
      clearPending();setReview(null);setSelected([]);update();
      await transfer(saved);
    }catch(err){
      if(isDefiniteRefusal(err)){clearPending();setReview(null);}
      setError(message(err));
    }finally{setBusy(false);update();}
  };
  const inspect=async()=>{
    setBusy(true);setError('');
    try{setReview(await result(fetchClient.POST('/api/crexil/projects/{wid}/deliveries/review',{params:{path:{wid:data.project.id}},body:{node_ids:selected,destination}})));}
    catch(err){setError(message(err));}finally{setBusy(false);}
  };
  const resume=async(delivery:Delivery)=>{setBusy(true);setError('');try{await transfer(delivery);}catch(err){setError(message(err));}finally{setBusy(false);update();}};
  const changeDestination=(value:string)=>{setDestination(value);setReview(null);};
  return <section className={s.sequenceRoom} aria-label="Delivery">
    <div className={s.roomHead}><strong>Delivery</strong><span className={s.hint}>Original material, companions and the recorded provenance</span><button onClick={close}>Esc · Project</button></div>
    <div className={s.sequenceMain}><div className={s.sequenceFields}>
      <span className={s.eyebrow}>Leave a usable copy</span><h1>Choose what leaves the project.</h1>
      <p className={s.sequenceInstructions}>Each output gets its own delivery record. Returning to this room never repeats a transfer. Your project retains the original material.</p>
      <fieldset ref={fields} className={s.deliveryForm} disabled={busy||Boolean(pending.current)}>
        <legend>Material to deliver</legend>
        <div className={s.deliveryChoices}>{data.nodes.map(node=><label key={node.id} className={s.deliveryChoice}>
          <input type="checkbox" checked={selected.includes(node.id)} onChange={event=>{setSelected(ids=>event.target.checked?[...ids,node.id]:ids.filter(id=>id!==node.id));setReview(null);}}/>
          <MaterialThumbnail node={node} decorative/><span>{label(node)}{!node.available&&<small>Original unavailable</small>}</span>
        </label>)}</div>
        <label>Destination folder<input value={destination} placeholder={target.data?.path??'Full destination path'} onChange={event=>changeDestination(event.target.value)}/></label>
        <p className={s.hint}>Allowed destinations: {target.data?.roots_said??'Loading configured folders…'}. Existing folders only.</p>
        <div className={s.actions}><button type="button" onClick={()=>setBrowse(value=>!value)}>{browse?'Close folders':'Browse folders'}</button>
          <button type="button" disabled={!selected.length} onClick={()=>void inspect()}>Review delivery</button></div>
      </fieldset>
      {browse&&target.data&&<DestinationBrowser target={target.data} busy={busy||Boolean(pending.current)} onPick={path=>{changeDestination(path);setBrowse(false);}}/>}
      {review&&<section className={s.sequenceReview} aria-label="Delivery review"><h2>{review.items.length} outputs · {review.items.reduce((count,item)=>count+item.files.length,0)} files</h2>
        <p>{review.destination}</p><p>{String(review.target.where??'')}</p>
        {review.items.map(item=><div key={item.node_id}><strong>{item.filename}</strong><p>{item.requires_review?'This item cannot transfer yet. Its refusal will remain in the delivery record.':`${item.files.length} files including its manifest. Final asset names are assigned when this delivery is saved.`}</p>
          {(item.missing??[]).map(note=><p key={note}>{note}</p>)}<details><summary>Files and hashes</summary>{item.files.map(file=><p key={file.path} className={s.deliveryPath}>{file.path}<br/>{file.sha256}</p>)}</details></div>)}
        {review.errors.map(text=><p key={text} className={s.error}>{text}</p>)}
        <button className={s.primary} disabled={busy||!review.ready||Boolean(pending.current)} onClick={()=>void deliver()}>{review.items.some(item=>item.requires_review)?'Deliver ready originals':'Deliver originals'}</button>
      </section>}
      {error&&<p className={s.error} role="alert">{error}</p>}
      {pending.current&&<button disabled={busy} onClick={()=>void deliver()}>Retry same delivery request</button>}
      {busy&&<p role="status">Checking and recording delivery…</p>}
      <div className={s.sequenceActions}><h2>Delivery history</h2><a href={`/api/crexil/projects/${data.project.id}/deliveries.csv`} download>Download CSV summary</a></div>
      {history.error&&<p role="alert" className={s.error}>{message(history.error)} <button onClick={()=>void history.refetch()}>Reload history</button></p>}
      {!history.data?.length&&<p className={s.hint}>{history.isLoading?'Loading delivery history…':'No deliveries recorded yet.'}</p>}
      {history.data?.map(delivery=><section key={delivery.id} className={s.sequenceStep} aria-label={`Delivery ${delivery.id}`}>
        <header><div><h2>{delivery.items.filter(item=>item.state==='complete').length} of {delivery.items.length} outputs delivered</h2><p className={s.hint}>{delivery.actor.name} · {new Date(delivery.created_at).toLocaleString()}</p></div>
          {delivery.items.some(item=>item.state!=='complete'&&!item.requires_review)&&<button disabled={busy} onClick={()=>void resume(delivery)}>Resume this delivery</button>}</header>
        <p className={s.deliveryPath}>{delivery.destination}</p>
        {delivery.items.map(item=><details key={item.node_id}><summary>{item.filename} · {item.state==='complete'?'Delivered':item.state==='incomplete'?'Needs attention':'Ready to transfer'}</summary>
          {item.error&&<p className={s.error}>{item.error}</p>}{item.completed_at&&<p>Confirmed {new Date(item.completed_at).toLocaleString()}. This records the completed transfer; later changes outside Crexil are not tracked.</p>}
          {item.requires_review&&<button disabled={busy||Boolean(pending.current)} onClick={()=>{setSelected([item.node_id]);setDestination(delivery.destination);setReview(null);fields.current?.scrollIntoView({block:'start'});}}>Choose for a new review after repair</button>}
          {(item.missing??[]).map(note=><p key={note}>{note}</p>)}{item.files.map(file=><p className={s.deliveryPath} key={file.path}>{file.path}</p>)}
        </details>)}
      </section>)}
    </div></div>
  </section>;
}
