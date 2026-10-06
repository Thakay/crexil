// Backups leave through a download; restore opens a separate project. Pending
// commands survive room navigation and reload, while binary files stay outside
// browser storage. A response lost in transit never creates a new command ID.
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useHotkeys} from 'react-hotkeys-hook';
import type {components} from '../../generated/openapi';
import {fetchClient} from '../../api/client';
import {ActionError,command,isDefiniteRefusal,message,result,type Project} from './api';
import {useEscapeLayer} from './keys';
import s from './Filmstrip.module.css';

type Command=components['schemas']['Command'];
type Backup=components['schemas']['ProjectBackup'];
type Upload=components['schemas']['ArchiveUpload'];
type Reserve=components['schemas']['ReserveArchive'];
type Restore=components['schemas']['RestoreArchive'];
type BackupPending={version:1;mode:'backup';body:Command};
type RestorePending={version:1;mode:'restore';reserve:Reserve;uid?:string;name:string;restore?:Restore};
type FileRecord={source?:unknown;error?:unknown;available?:unknown};
const RESTORE_KEY='crexil.archive.restore';
const rejected=isDefiniteRefusal;

function load<T extends BackupPending|RestorePending>(key:string,mode:T['mode']):T|null{
  try{
    const saved=JSON.parse(sessionStorage.getItem(key)??'null');
    if(saved?.version!==1||saved.mode!==mode)return null;
    const body=mode==='backup'?saved.body:saved.reserve;
    if(typeof body?.request_id!=='string'||!body.actor)return null;
    if(mode==='restore'&&(typeof body.filename!=='string'||typeof body.size_bytes!=='number'||typeof saved.name!=='string'))return null;
    return saved as T;
  }catch{return null;}
}
function remember(key:string,value:BackupPending|RestorePending){
  try{sessionStorage.setItem(key,JSON.stringify(value));}
  catch{throw Error('The browser could not save recovery details. Free some session storage before trying this request.');}
}
function forget(key:string){try{sessionStorage.removeItem(key);}catch{/* A stale receipt still replays the same request safely. */}}
function bytes(value:number){
  if(value<1024)return `${value.toLocaleString()} bytes`;
  const scale=Math.min(4,Math.floor(Math.log(value)/Math.log(1024)));
  return `${(value/1024**scale).toLocaleString(undefined,{maximumFractionDigits:1})} ${['bytes','KiB','MiB','GiB','TiB'][scale]}`;
}
function fileRecords(value:unknown):FileRecord[]{
  return Array.isArray(value)?value.filter((item):item is FileRecord=>item!==null&&typeof item==='object'):[];
}
function recordCounts(value:unknown):Record<string,number>{
  if(!value||typeof value!=='object'||Array.isArray(value))return {};
  return Object.fromEntries(Object.entries(value).filter((entry):entry is [string,number]=>typeof entry[1]==='number'));
}
function MissingFiles({files}:{files:FileRecord[]}){
  const [shown,setShown]=useState(50);
  return files.length?<details><summary>{files.length} unavailable {files.length===1?'file':'files'} · records retained</summary>
    {files.slice(0,shown).map((file,index)=><p className={s.deliveryPath} key={index}>{typeof file.source==='string'?file.source:'Recorded file'}<br/>{typeof file.error==='string'?file.error:'Material was unavailable when this backup was created.'}</p>)}
    {shown<files.length&&<button onClick={()=>setShown(count=>count+50)}>Show more unavailable files ({files.length-shown} remaining)</button>}
  </details>:<p>All material listed in this backup is available.</p>;
}
function Counts({records}:{records:Record<string,number>}){
  return <details><summary>Project record counts</summary>{Object.entries(records).map(([table,count])=><p key={table}>
    {table.replaceAll('_',' ')}: {count.toLocaleString()}
  </p>)}</details>;
}

function Room({title,busy,close,children}:{title:string;busy:boolean;close:()=>void;children:ReactNode}){
  const room=useRef<HTMLElement>(null);
  const origin=useRef(document.activeElement as HTMLElement|null);
  const current=useRef({busy,close});current.current={busy,close};
  // The room holds Escape and keeps Tab inside itself until it closes.
  useEscapeLayer(()=>{if(!current.current.busy)current.current.close();return true;});
  useHotkeys('tab, shift+tab',event=>{
    if(!room.current)return;
    const targets=Array.from(room.current.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]'))
      .filter(element=>element.getClientRects().length>0&&!element.closest('[inert]'));
    const first=targets[0],last=targets[targets.length-1];
    if(!first){event.preventDefault();room.current.focus();}
    else if(!targets.includes(document.activeElement as HTMLElement)){event.preventDefault();(event.shiftKey?last:first).focus();}
    else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  },{enableOnFormTags:true,enableOnContentEditable:true});
  useEffect(()=>{
    if(!room.current?.contains(document.activeElement))room.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    const entry=origin.current;
    return()=>{if(entry?.isConnected)entry.focus();};
  },[]);
  return <section ref={room} tabIndex={-1} className={s.sequenceRoom} aria-label={title} aria-busy={busy}>
    <div className={s.roomHead}><strong>{title}</strong><span className={s.hint}>Project records and available material</span><button disabled={busy} onClick={close}>Esc · Close</button></div>
    <div className={s.sequenceMain}><div className={s.sequenceFields}>{children}</div></div>
  </section>;
}

export function BackupRoom({project,close,restored}:{project?:Project;close:()=>void;restored:(projectId:string)=>void}){
  return project?<CreateBackup key={project.id} project={project} close={close}/>:<RestoreBackup close={close} restored={restored}/>;
}

function CreateBackup({project,close}:{project:Project;close:()=>void}){
  const key=`crexil.backup.${project.id}`;
  const [pending,setPending]=useState(()=>load<BackupPending>(key,'backup'));
  const savedRequest=useRef(pending);
  const active=useRef(true),working=useRef(false);
  const [busy,setBusy]=useState(false);
  const [backup,setBackup]=useState<Backup|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  const create=async()=>{
    if(working.current)return;
    working.current=true;setBusy(true);setError('');
    try{
      const request=savedRequest.current??{version:1 as const,mode:'backup' as const,body:command()};
      remember(key,request);savedRequest.current=request;setPending(request);
      const receipt=await result(fetchClient.POST('/api/crexil/projects/{wid}/backups',{params:{path:{wid:project.id}},body:request.body}));
      if(active.current){forget(key);savedRequest.current=null;setPending(null);setBackup(receipt);}
    }catch(err){
      if(active.current){
        if(rejected(err)){forget(key);savedRequest.current=null;setPending(null);}
        setError(message(err));
      }
    }finally{working.current=false;if(active.current)setBusy(false);}
  };
  return <Room title="Project backup" busy={busy} close={close}>
    <span className={s.eyebrow}>Keep a portable copy</span><h1>Back up {project.name}.</h1>
    <p className={s.sequenceInstructions}>Save the project record and its available material in one file. Missing material stays identified in the backup. Download the completed copy to keep it outside this installation.</p>
    {pending&&<p className={s.sequenceNotice}>Backup acceptance is unconfirmed. Retry the same request to recover its result.</p>}
    {error&&<p className={s.error} role="alert">{error}</p>}
    <button data-autofocus autoFocus className={backup&&!pending?undefined:s.primary} disabled={busy} onClick={()=>void create()}>{pending?'Retry same backup request':backup?'Create another backup':'Create backup'}</button>
    {busy&&<p role="status">Capturing the project and verifying its available files…</p>}
    {backup&&<section className={s.sequenceReview} aria-label="Completed backup">
      <h2>Backup ready to download</h2><p>{backup.project_name} · {bytes(backup.size_bytes)} · {new Date(backup.created_at).toLocaleString()}</p>
      <p>{backup.records.nodes??0} material records · {backup.records.directions??0} directions · {backup.files.filter(file=>file.available===true).length} available files</p>
      <MissingFiles files={fileRecords(backup.files).filter(file=>file.available!==true)}/><Counts records={backup.records}/>
      <details><summary>Backup content hash</summary><p className={s.deliveryPath}>{backup.sha256}</p></details>
      <a className={`${s.download} ${s.primary}`} href={`/api/crexil/backups/${encodeURIComponent(backup.id)}/file`} download>Download project backup</a>
    </section>}
  </Room>;
}

function RestoreBackup({close,restored}:{close:()=>void;restored:(projectId:string)=>void}){
  const [pending,setPending]=useState(()=>load<RestorePending>(RESTORE_KEY,'restore'));
  const savedRequest=useRef(pending);
  const active=useRef(true),working=useRef(false);
  const fileInput=useRef<HTMLInputElement>(null);
  const nameInput=useRef<HTMLInputElement>(null),retryRestore=useRef<HTMLButtonElement>(null);
  const [file,setFile]=useState<File|null>(null);
  const [upload,setUpload]=useState<Upload|null>(null);
  const [name,setName]=useState(pending?.restore?.name??pending?.name??'');
  const [busy,setBusy]=useState(false);
  const [stage,setStage]=useState('');
  const [error,setError]=useState('');
  const save=(value:RestorePending)=>{remember(RESTORE_KEY,value);savedRequest.current=value;if(active.current)setPending(value);};
  const acceptState=(state:Upload)=>{
    if(!active.current)return;
    const current=savedRequest.current;
    if(!current)return;
    const proposed=current.name||`${String(state.review?.project_name??'Project')} (restored)`.slice(0,200);
    const next={...current,uid:state.id,name:state.state==='ready'?proposed:current.name};
    save(next);
    if(active.current){setUpload(state);if(!current.restore)setName(next.name);}
  };
  const check=async()=>{
    const uid=savedRequest.current?.uid;
    if(!uid)return null;
    const state=await result(fetchClient.GET('/api/crexil/archive-uploads/{uid}',{params:{path:{uid}}}));
    acceptState(state);return state;
  };
  const recover=async()=>{
    if(working.current)return;
    working.current=true;setBusy(true);setStage('Checking the saved upload…');setError('');
    try{await check();}catch(err){if(active.current)setError(message(err));}
    finally{working.current=false;if(active.current)setBusy(false);}
  };
  useEffect(()=>{
    active.current=true;
    if(savedRequest.current?.uid)void recover();
    return()=>{active.current=false;};
    // Entry only performs a read. Replaying either command remains explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);
  useEffect(()=>{
    if(busy)return;
    if(pending?.restore)retryRestore.current?.focus();
    else if(upload?.state==='ready')nameInput.current?.focus();
  },[busy,upload?.state,Boolean(pending?.restore)]);
  const send=async()=>{
    if(working.current)return;
    working.current=true;setBusy(true);setError('');
    try{
      let current=savedRequest.current;
      if(!current){
        if(!file||file.size===0)throw Error('Choose a nonempty project backup file.');
        current={version:1,mode:'restore',reserve:{...command(),filename:file.name,size_bytes:file.size},name};save(current);
      }
      if(current.restore)throw Error('Resolve the saved restore request before uploading another file.');
      if(!current.uid){
        setStage('Reserving this backup upload…');
        const reserved=await result(fetchClient.POST('/api/crexil/archive-uploads',{body:current.reserve}));
        if(!active.current)return;
        current={...current,uid:reserved.id};save(current);
      }
      setStage('Checking the saved upload…');
      const state=await check();
      if(!active.current)return;
      if(state?.state==='ready')return;
      if(!file)throw Error(`Choose ${current.reserve.filename} again to resume its upload.`);
      if(file.name!==current.reserve.filename||file.size!==current.reserve.size_bytes)throw Error(`Choose ${current.reserve.filename} (${bytes(current.reserve.size_bytes)}) to resume this upload.`);
      setStage('Uploading and validating the project backup…');
      // Passing the File directly lets the browser stream it without a base64
      // copy or a whole-file JavaScript buffer, including large mesh archives.
      const response=await fetch(`/api/crexil/archive-uploads/${encodeURIComponent(current.uid!)}/file`,{method:'PUT',headers:{'Content-Type':'application/zip'},body:file});
      const data:unknown=await response.json();
      if(!response.ok)throw new ActionError(data,response.status);
      acceptState(data as Upload);
    }catch(err){
      if(savedRequest.current?.uid){
        // A lost PUT response may follow successful validation. GET resolves it
        // before the room offers another byte transfer.
        try{const state=await check();if(state?.state==='ready')return;}catch{/* Keep the original upload and its command available for retry. */}
      }
      if(active.current)setError(message(err));
    }finally{working.current=false;if(active.current)setBusy(false);}
  };
  const restore=async()=>{
    if(working.current)return;
    const current=savedRequest.current;
    if(!current?.uid)return;
    working.current=true;setBusy(true);setStage('Restoring a separate project…');setError('');
    try{
      if(!current.restore&&(!upload?.sha256||upload.state!=='ready'||!name.trim()))throw Error('Review the ready backup and choose a project name before restoring.');
      const body=current.restore??{...command(),sha256:upload!.sha256!,name:name.trim()};
      save({...current,name:body.name??'',restore:body});
      const receipt=await result(fetchClient.POST('/api/crexil/archive-uploads/{uid}/restore',{params:{path:{uid:current.uid}},body}));
      if(active.current){forget(RESTORE_KEY);savedRequest.current=null;setPending(null);restored(receipt.project_id);}
    }catch(err){
      if(active.current){
        if(rejected(err)&&savedRequest.current){const next={...savedRequest.current};delete next.restore;save(next);}
        setError(message(err));
      }
    }finally{working.current=false;if(active.current)setBusy(false);}
  };
  const chooseAnother=()=>{
    if(busy||pending?.restore)return;
    forget(RESTORE_KEY);savedRequest.current=null;setPending(null);setUpload(null);setName('');setFile(null);setError('');
    if(fileInput.current){fileInput.current.value='';fileInput.current.focus();}
  };
  const changeName=(value:string)=>{
    setName(value);
    if(savedRequest.current){try{save({...savedRequest.current,name:value});}catch(err){setError(message(err));}}
  };
  const review=upload?.state==='ready'?upload.review:null;
  const missing=fileRecords(review?.unavailable_files);
  return <Room title="Restore project" busy={busy} close={close}>
    <span className={s.eyebrow}>Open a portable project</span><h1>Restore a separate copy of the work.</h1>
    <p className={s.sequenceInstructions}>Choose a project backup, inspect its contents, then restore it as a new project. Existing projects stay intact. Old execution and deliveries remain history. Continue through a fresh preparation and review.</p>
    {!review&&<label className={s.returnFile}>Project backup file<input ref={fileInput} data-autofocus autoFocus type="file" accept=".zip,.crexil,application/zip" disabled={busy||Boolean(pending?.restore)} onChange={event=>{setFile(event.target.files?.[0]??null);setError('');}}/></label>}
    {pending&&!review&&<p className={s.sequenceNotice}>{pending.uid?`Upload reserved for ${pending.reserve.filename} (${bytes(pending.reserve.size_bytes)}). If it is still waiting, select that file again to send its bytes.`:'Upload reservation is unconfirmed. Retry the same request to recover its identity.'}</p>}
    <div className={s.actions}>{!review&&!pending?.restore&&<button className={s.primary} disabled={busy||(!file&&!pending)} onClick={()=>void send()}>{pending?'Retry same upload request':'Upload and review backup'}</button>}
    {pending?.uid&&<button disabled={busy} onClick={()=>void recover()}>Check upload status</button>}</div>
    {review&&<section className={s.sequenceReview} aria-label="Backup restore review">
      <h2>{String(review.project_name??'Project backup')}</h2><p>{upload!.filename} · {bytes(upload!.size_bytes)}</p>
      <p>{Number(review.available_files??0).toLocaleString()} available files · {bytes(Number(review.available_bytes??0))} of retained material</p>
      <MissingFiles files={missing}/><Counts records={recordCounts(review.records)}/>
      <label>Restored project name<input ref={nameInput} maxLength={200} value={name} disabled={busy||Boolean(pending?.restore)} onChange={event=>changeName(event.target.value)}/></label>
      <p>Unfinished attempts remain historical. Continue through a newly reviewed preparation.</p>
      {!pending?.restore&&<button className={s.primary} disabled={busy||!name.trim()} onClick={()=>void restore()}>Restore as new project</button>}
    </section>}
    {pending?.restore&&<><p className={s.sequenceNotice}>Restore acceptance is unconfirmed for “{pending.restore.name}”. Its file and name stay fixed. Retry to recover the same project.</p><button ref={retryRestore} className={s.primary} disabled={busy} onClick={()=>void restore()}>Retry same restore</button></>}
    {error&&<p className={s.error} role="alert">{error}</p>}
    {busy&&<p role="status">{stage}</p>}
    {pending&&!pending.restore&&<p><button disabled={busy} onClick={chooseAnother}>Choose another backup</button></p>}
  </Room>;
}
