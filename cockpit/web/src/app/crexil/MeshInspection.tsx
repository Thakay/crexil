// A project-scoped shell for the established GLB renderer and inspection modes.
// Camera and surface changes never modify the retained file or its record.
import { lazy, Suspense, useEffect, useState } from 'react';
import { INITIAL_VIEW, MODE_KEYS, StageBoundary, loadFraction, nextView, truthLine, type LoadStatus, type ModeId } from '../batch/Lightbox';
import { meshFactsLine } from '../mesh/facts';
import { imageUrl, type Material } from './api';
import s from './Filmstrip.module.css';

const Stage = lazy(() => import('../batch/MeshViewer'));
const controls: [ModeId, string][] = [
  ['reset', 'Reset view'], ['below', 'From below'], ['clay', 'Clay'],
  ['cull', 'Backface cull'], ['normals', 'Normals'], ['wireframe', 'Wireframe'], ['grid', 'Grid'],
];

export function MeshInspection({node}: {node: Material}) {
  const [view,setView] = useState(INITIAL_VIEW);
  const [load,setLoad] = useState<LoadStatus>({status:'loading',loaded:0,total:0});
  const [below,setBelow] = useState(false);
  const [attempt,setAttempt] = useState(0);
  const live = load.status==='ready';
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(!live||event.altKey||event.metaKey||event.ctrlKey||(event.target as HTMLElement).closest('input,textarea,select'))return;
      const mode=controls.find(([id])=>MODE_KEYS[id].toLowerCase()===event.key.toLowerCase())?.[0];
      if(mode){event.preventDefault();setView(value=>nextView(value,mode));}
    };
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[live]);
  const fraction=load.status==='loading'?loadFraction(load.loaded,load.total,node.mesh?.file_mb):null;
  return <div className={s.meshInspection} aria-label="Mesh viewer">
    <div className={s.meshStage}>
      <StageBoundary key={attempt} onFail={error=>setLoad({status:'failed',error})}>
        <Suspense fallback={null}><Stage url={imageUrl(node.id,true)} view={view} onLoad={setLoad} onBelow={setBelow}/></Suspense>
      </StageBoundary>
      {load.status==='loading'&&<div className={s.meshLoading} role="status">Loading original mesh{fraction===null?'…':` · ${Math.round(fraction*100)}%`}</div>}
      {load.status==='empty'&&<div className={s.meshLoading}>The file opens, but contains no mesh geometry. Its original bytes and record are retained.</div>}
      {load.status==='failed'&&<div className={s.meshLoading} role="alert"><p>This browser could not draw the mesh. The original file remains available for download.</p>
        <button onClick={()=>{setLoad({status:'loading',loaded:0,total:0});setAttempt(value=>value+1);}}>Try viewer again</button>
        <details><summary>Viewer details</summary><p>{load.error}</p></details></div>}
    </div>
    <div className={s.meshTools}>
      <div className={s.meshModes}>
        <button disabled={!live} aria-pressed={view.surface==='textured'} onClick={()=>setView(v=>({...v,surface:'textured',last:null}))}>Textured</button>
        {controls.map(([id,name])=><button key={id} disabled={!live}
          aria-pressed={id==='reset'||id==='below'?undefined:id==='clay'?view.surface==='clay':view[id]}
          onClick={()=>setView(value=>nextView(value,id))}>{name} <span className={s.micro}>{MODE_KEYS[id]}</span></button>)}
      </div>
      <p className={s.micro}>{meshFactsLine(node)}</p>
      <p>{live?truthLine(node,view,below):'Inspection changes the view only. The retained mesh stays unchanged.'}</p>
      <span className={s.micro}>Drag to orbit · Scroll to zoom · Right-drag to pan</span>
    </div>
  </div>;
}
