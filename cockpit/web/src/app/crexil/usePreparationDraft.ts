// Local preparation survives a reload until the shared server confirms it.
// Delivery retries keep their exact command; definite conflicts require an
// explicit saved-version or separate-copy choice before another write.
import {useEffect,useRef,useState} from 'react';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {ActionError,command,message,preparation,result,saveDirection,type Direction,type Preparation} from './api';

type Values={title:string;setup:Preparation};
type Pending=components['schemas']['UpdateDirection'];
type Copy=components['schemas']['CreateDirection'];
type LocalDraft={version:1;revision:number;base:Values;values:Values;pending:Pending|null;copy:Copy|null;conflict:boolean;error:string};
const key=(id:string)=>`crexil-preparation:${id}`;
const values=(direction:Direction):Values=>({title:direction.title,setup:preparation(direction)});
const different=(draft:LocalDraft)=>JSON.stringify(draft.values)!==JSON.stringify(draft.base);
const retained=(draft:LocalDraft)=>different(draft)||Boolean(draft.pending||draft.copy||draft.conflict);
function read(id:string):LocalDraft|null {
  try {
    const saved=JSON.parse(sessionStorage.getItem(key(id))??'null');
    return saved?.version===1&&saved.values&&saved.base&&Number.isInteger(saved.revision)?saved:null;
  }catch{return null;}
}
export const hasLocalPreparation=(id:string)=>Boolean(read(id));

export function usePreparationDraft(direction:Direction,onSaved:()=>void,holdRemote=false) {
  const [draft,setDraft]=useState<LocalDraft>(()=>{
    const local=read(direction.id);
    if(local)return {...local,error:local.pending||local.copy?'Confirm the previous save before continuing.':local.error};
    return {version:1,revision:direction.revision,base:values(direction),values:values(direction),pending:null,copy:null,conflict:false,error:''};
  });
  const current=useRef(draft);
  const mounted=useRef(true);
  const task=useRef<Promise<boolean>|null>(null);
  const refreshed=useRef(onSaved);refreshed.current=onSaved;
  const [saving,setSaving]=useState(false);
  const [storageError,setStorageError]=useState('');
  const [comparison,setComparison]=useState<Direction|null>(null);
  const [reading,setReading]=useState(false);
  const [readError,setReadError]=useState('');
  const [adoption,setAdoption]=useState(0);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const commit=(next:LocalDraft)=>{
    if(!mounted.current)return;
    current.current=next;setDraft(next);
    try {
      if(retained(next))sessionStorage.setItem(key(direction.id),JSON.stringify(next));
      else sessionStorage.removeItem(key(direction.id));
      setStorageError('');
    }catch {setStorageError('This browser could not retain the local preparation. Keep this view open until it is saved.');}
  };
  const save=():Promise<boolean>=>{
    if(task.current)return task.current;
    const local=current.current;
    if(local.conflict||local.copy)return Promise.resolve(false);
    if(!local.pending&&!different(local))return Promise.resolve(true);
    const body=local.pending??{...command(),revision:local.revision,...local.values};
    commit({...local,pending:body,error:''});setSaving(true);
    task.current=(async()=>{
      try {
        const saved=await saveDirection(direction.id,body);
        commit({...current.current,revision:saved.revision,base:{title:body.title,setup:body.setup},pending:null,error:''});
        if(mounted.current)refreshed.current();
        return true;
      }catch(error) {
        const definite=error instanceof ActionError&&Boolean(error.status&&error.status>=400&&error.status<500&&error.status!==408);
        const conflict=error instanceof ActionError&&error.status===409;
        commit({...current.current,pending:definite?null:body,conflict,error:message(error)});
        if(conflict&&mounted.current){setComparison(null);refreshed.current();}
        return false;
      }finally{task.current=null;if(mounted.current)setSaving(false);}
    })();
    return task.current;
  };
  useEffect(()=>{
    if(!different(draft)||draft.error||draft.conflict||draft.copy||saving)return;
    const timer=setTimeout(()=>void save(),450);
    return()=>clearTimeout(timer);
  },[draft,saving]);
  useEffect(()=>{
    const local=current.current;
    if(direction.revision<=local.revision||local.pending||local.copy||saving||local.conflict||holdRemote)return;
    if(different(local)) {
      commit({...local,conflict:true,error:'This preparation changed while you were editing. Your version is kept here.'});
      setComparison(null);
    }else {
      commit({...local,revision:direction.revision,base:values(direction),values:values(direction),error:''});
      setAdoption(value=>value+1);
    }
  },[direction.revision,saving,draft.revision,draft.conflict,holdRemote]);
  const change=(next:Values|((previous:Values)=>Values))=>{
    const local=current.current;
    commit({...local,values:typeof next==='function'?next(local.values):next,error:local.pending||local.conflict?local.error:''});
  };
  const readSaved=async()=>{
    setReading(true);setReadError('');
    try {
      const project=await result(fetchClient.GET('/api/crexil/projects/{wid}',{params:{path:{wid:direction.workspace_id}}}));
      const saved=project.directions.find(item=>item.id===direction.id);
      if(!saved)throw new Error('The saved direction is unavailable. Your local version is still here.');
      if(mounted.current)setComparison(saved);
    }catch(error){if(mounted.current)setReadError(message(error));}
    finally{if(mounted.current)setReading(false);}
  };
  const useSaved=()=>{
    if(!comparison||current.current.pending||current.current.copy||saving)return;
    if(comparison.revision<direction.revision){
      commit({...current.current,conflict:true,error:'The saved version changed again. Compare its latest revision before choosing.'});
      setComparison(null);return;
    }
    commit({...current.current,revision:comparison.revision,base:values(comparison),values:values(comparison),conflict:false,error:''});
    setAdoption(value=>value+1);setComparison(null);refreshed.current();
  };
  const replaceSaved=async()=>{
    if(!comparison||comparison.state!=='draft'||current.current.pending||current.current.copy||saving)return;
    commit({...current.current,revision:comparison.revision,base:values(comparison),conflict:false,error:''});
    setComparison(null);
    await save();
  };
  const copy=async():Promise<Direction|null>=>{
    const local=current.current;
    if(saving||local.pending)return null;
    const body=local.copy??{...command(),title:`${local.values.title.slice(0,195)} copy`,setup:local.values.setup};
    commit({...local,copy:body,error:''});setSaving(true);
    try {
      const saved=await result(fetchClient.POST('/api/crexil/projects/{wid}/directions',{params:{path:{wid:direction.workspace_id}},body}));
      commit({...current.current,base:current.current.values,pending:null,copy:null,conflict:false,error:''});
      if(mounted.current)refreshed.current();
      return saved;
    }catch(error) {
      const definite=error instanceof ActionError&&Boolean(error.status&&error.status>=400&&error.status<500&&error.status!==408);
      commit({...current.current,copy:definite?null:body,conflict:true,error:message(error)});
      return null;
    }finally{if(mounted.current)setSaving(false);}
  };
  const requireComparison=()=>{
    commit({...current.current,conflict:true,error:'The saved preparation changed before review. Compare it before running.'});
    setComparison(null);refreshed.current();
  };
  return {title:draft.values.title,setup:draft.values.setup,revision:draft.revision,adoption,change,save,copy,requireComparison,
    changed:different(draft),unresolved:retained(draft),saving,
    status:saving?'Saving':draft.error?'Not saved':retained(draft)?'Unsaved':'Saved',
    error:draft.error,storageError,conflict:draft.conflict,pending:Boolean(draft.pending),copyPending:Boolean(draft.copy),
    readSaved,comparison,reading,readError,useSaved,replaceSaved};
}
