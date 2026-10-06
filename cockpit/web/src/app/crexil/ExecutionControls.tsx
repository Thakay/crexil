// Control delivery is separate from its effect. Retrying uncertain delivery uses
// the same command, while observed slot states determine what has actually stopped.
import {useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {ActionError,command,message,result,type Snapshot} from './api';
import s from './Filmstrip.module.css';

type Control=components['schemas']['ExecutionControlView'];
type Request=components['schemas']['ControlExecution'];
// Room navigation can unmount a control while its response is missing. Keep
// delivery identity at the control carrier, keyed by its exact scope and target.
const pendingDeliveries=new Map<string,Request>();

export function ExecutionControls({control,slots,label,refresh,pendingWork=false}:{
  control:Control;slots:Snapshot['turns'][number]['slots'];label:string;refresh:()=>void;pendingWork?:boolean;
}){
  const [accepted,setAccepted]=useState<Control|null>(null);
  const [busy,setBusy]=useState(false);
  const key=`${control.scope}:${control.target_id}`;
  const pending=useRef<Request|null>(pendingDeliveries.get(key)??null);
  const [error,setError]=useState(pending.current?'Delivery is unconfirmed. Check this same request to confirm its outcome.':'');
  const current=accepted&&accepted.revision>control.revision?accepted:control;
  const waiting=slots.filter(slot=>['queued','held'].includes(slot.status)).length;
  const running=slots.filter(slot=>slot.status==='running').length;
  const late=slots.filter(slot=>slot.status==='late').length;
  const send=async(action:Request['action'])=>{
    setBusy(true);setError('');
    pending.current??={...command(),revision:current.revision,action,reason:''};
    pendingDeliveries.set(key,pending.current);
    try{
      const saved=await result(fetchClient.POST('/api/crexil/execution/{scope}/{target_id}',{
        params:{path:{scope:current.scope,target_id:current.target_id}},body:pending.current,
      }));
      pendingDeliveries.delete(key);pending.current=null;setAccepted(saved);refresh();
    }catch(error){
      if(error instanceof ActionError&&[404,409,422].includes(error.status??0)){pendingDeliveries.delete(key);pending.current=null;}
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
    {error&&<p className={s.error} role="alert">{error} {pending.current&&<button disabled={busy} onClick={()=>void send(pending.current!.action)}>Retry same request</button>}</p>}
  </section>;
}
