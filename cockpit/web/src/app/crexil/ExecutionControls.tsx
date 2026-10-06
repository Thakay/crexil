// Control delivery is separate from its effect. Retrying uncertain delivery uses
// the same command, while observed slot states determine what has actually stopped.
// The same holds for one attempt's resume or cancel from its face.
import {useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {command,isDefiniteRefusal,message,result,type Snapshot} from './api';
import s from './Filmstrip.module.css';

type Control=components['schemas']['ExecutionControlView'];
type Request=components['schemas']['ControlExecution'];
type Move=components['schemas']['AttemptMove'];
// Room navigation can unmount a control while its response is missing. Keep
// delivery identity at the control carrier, keyed by its exact scope and target,
// shared by this panel and the attempt faces that resume from a cell; an attempt's
// own move is keyed by the move, the slot and the attempt its face showed.
const pendingDeliveries=new Map<string,Request>();
const pendingMoves=new Map<string,Move>();
const keyOf=(control:Control)=>`${control.scope}:${control.target_id}`;

/** Sends one request per key. An unconfirmed earlier delivery for the same key is
 *  sent again as the same request, never as a new one; a confirmed answer or a
 *  definite refusal drops it. */
async function deliver<B,T>(pending:Map<string,B>,key:string,make:()=>B,send:(body:B)=>Promise<T>):Promise<T>{
  const body=pending.get(key)??make();
  pending.set(key,body);
  try{
    const saved=await send(body);
    pending.delete(key);
    return saved;
  }catch(error){
    if(isDefiniteRefusal(error))pending.delete(key);
    throw error;
  }
}

/** Sends one control action for its scope and target. */
export function deliverControl(control:Control,action:Request['action']):Promise<Control>{
  return deliver(pendingDeliveries,keyOf(control),()=>({...command(),revision:control.revision,action,reason:''}),
    body=>result(fetchClient.POST('/api/crexil/execution/{scope}/{target_id}',{
      params:{path:{scope:control.scope,target_id:control.target_id}},body,
    })));
}

/** Sends one participant's resume or cancel of one attempt, with the attempt number its
 *  face shows, so a later attempt of the slot refuses as stale (laws 4 and 23). */
export function moveAttempt(slot:{id:string;attempt:number},move:'resume'|'cancel'):Promise<components['schemas']['AttemptMoveView']>{
  return deliver(pendingMoves,`${move}:${slot.id}:${slot.attempt}`,()=>({...command(),attempt:slot.attempt,reason:''}),body=>{
    const init={params:{path:{sid:slot.id}},body};
    return result(move==='resume'?fetchClient.POST('/api/crexil/attempts/{sid}/resume',init)
      :fetchClient.POST('/api/crexil/attempts/{sid}/cancel',init));
  });
}

export function ExecutionControls({control,slots,label,refresh,pendingWork=false}:{
  control:Control;slots:Snapshot['turns'][number]['slots'];label:string;refresh:()=>void;pendingWork?:boolean;
}){
  const [accepted,setAccepted]=useState<Control|null>(null);
  const [busy,setBusy]=useState(false);
  const key=keyOf(control);
  const pending=useRef<Request|null>(pendingDeliveries.get(key)??null);
  pending.current=pendingDeliveries.get(key)??null;
  const [error,setError]=useState('');
  // An unconfirmed delivery, made here or from an attempt face, keeps its retry in reach.
  const shown=error||(pending.current?'Delivery is unconfirmed. Check this same request to confirm its outcome.':'');
  const current=accepted&&accepted.revision>control.revision?accepted:control;
  const waiting=slots.filter(slot=>['queued','held'].includes(slot.status)).length;
  const running=slots.filter(slot=>slot.status==='running').length;
  const late=slots.filter(slot=>slot.status==='late').length;
  const send=async(action:Request['action'])=>{
    setBusy(true);setError('');
    try{
      const saved=await deliverControl(current,action);
      pending.current=null;setAccepted(saved);refresh();
    }catch(error){
      pending.current=pendingDeliveries.get(key)??null;
      setError(message(error));refresh();
    }finally{setBusy(false);}
  };
  return <section className={s.executionControls} aria-label={`${label} execution`}>
    <div><strong>{label}{current.paused?' · Paused':''}</strong>
      <p>{waiting} waiting · {running} running · {late} with unresolved stop
        {Boolean(current.cancel_requested)&&` · ${current.cancel_requested} cancellation requested`}</p>
      {current.paused&&<p>Future requests wait here. Running work can finish.</p>}
      {current.actor&&<details><summary>{current.last_action==='cancel'?'Cancellation requested':current.last_action==='pause'?'Paused':'Resumed'} by {current.actor.name}</summary>
        <p>{current.reason||'No reason supplied.'}</p></details>}
    </div>
    <button disabled={busy||Boolean(pending.current)} onClick={()=>void send(current.paused?'resume':'pause')}>{current.paused?'Resume':'Pause'} {label.toLowerCase()}</button>
    <button disabled={busy||Boolean(pending.current)||(!pendingWork&&waiting+running+late===0)} onClick={()=>void send('cancel')}>Cancel active work</button>
    {shown&&<p className={s.error} role="alert">{shown} {pending.current&&<button disabled={busy} onClick={()=>void send(pending.current!.action)}>Retry same request</button>}</p>}
  </section>;
}
