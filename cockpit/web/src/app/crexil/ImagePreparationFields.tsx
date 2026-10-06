// Bounded pixel preparation shares saved directions and the server review.
// Arrangement coordinates are explicit input, never inferred from Filmstrip cards.
import { useState } from 'react';
import type { components } from '../../generated/openapi';
import { imageUrl, label, type Material, type Preparation } from './api';
import s from './Filmstrip.module.css';

type Recipe = components['schemas']['ImagePreparation'];
type Box = components['schemas']['ImageBox'];

export function initialImagePreparation(nodes: Material[]): Recipe {
  const first=nodes[0];
  const sourceWidth=first?.material?.width ?? 1024, sourceHeight=first?.material?.height ?? 1024;
  const scale=Math.min(1,8192/sourceWidth,8192/sourceHeight,Math.sqrt(16_000_000/(sourceWidth*sourceHeight)));
  const width=Math.max(1,Math.floor(sourceWidth*scale)),height=Math.max(1,Math.floor(sourceHeight*scale));
  return {mode:nodes.length>1?'arrange':'resize',width,height,background:'transparent',resample:'lanczos',
    items:nodes.map(node=>({node_id:node.id,crop:null,placement:{x:0,y:0,width,height}}))};
}

export function ImagePreparationFields({setup,nodes,update}: {
  setup:Preparation; nodes:Material[]; update:(patch:Partial<Preparation>)=>void;
}) {
  const [natural,setNatural]=useState<Record<string,{width:number;height:number}>>({});
  const selected=(setup.references??[]).map(ref=>nodes.find(n=>n.id===ref.node_id)).filter((n):n is Material=>Boolean(n));
  const recipe=setup.image_preparation ?? initialImagePreparation(selected);
  const patch=(change:Partial<Recipe>)=>update({image_preparation:{...recipe,...change}});
  const items=recipe.items??[];
  const referencesFor=(items:NonNullable<Recipe['items']>)=>items.map(item=>(setup.references??[]).find(ref=>ref.node_id===item.node_id)??{node_id:item.node_id,contribution:''});
  const changeItem=(i:number,change:Partial<Recipe['items'] extends (infer T)[]|undefined?T:never>)=>
    patch({items:items.map((item,j)=>i===j?{...item,...change}:item)});
  const move=(i:number,offset:number)=>{
    const moved=[...items]; [moved[i],moved[i+offset]]=[moved[i+offset],moved[i]];
    update({image_preparation:{...recipe,items:moved},references:referencesFor(moved)});
  };
  return <div className={s.imagePreparation}>
    <div className={s.words}>
      <h3>Prepare an image</h3>
      <p className={s.hint}>Save a new image with its source record. Preparation stays here until you review and save the result.</p>
      <p className={s.hint}>Results may contain up to 16 million pixels. Resize and arrangement dimensions may be up to 8192 pixels each.</p>
      <label>Transform<select value={recipe.mode} onChange={e=>patch({mode:e.target.value as Recipe['mode']})}>
        <option value="crop">Crop one image</option><option value="resize">Resize one image</option><option value="arrange">Arrange sources together</option>
      </select></label>
      {recipe.mode!=='crop'&&<div className={s.inputPair}><label>Result width<input type="number" min={1} max={8192} value={recipe.width} onChange={e=>patch({width:Number(e.target.value)})}/></label>
        <label>Result height<input type="number" min={1} max={8192} value={recipe.height} onChange={e=>patch({height:Number(e.target.value)})}/></label></div>}
      {recipe.mode!=='crop'&&<label>Resampling<select value={recipe.resample} onChange={e=>patch({resample:e.target.value as Recipe['resample']})}><option value="lanczos">Smooth detail</option><option value="nearest">Nearest pixel</option></select></label>}
      {recipe.mode==='arrange'&&<label>Canvas background<select value={recipe.background} onChange={e=>patch({background:e.target.value as Recipe['background']})}><option value="transparent">Transparent</option><option value="white">White</option><option value="black">Black</option></select></label>}
      <p className={s.hint}>{recipe.mode==='arrange'?'Later sources cover earlier sources where opaque. Placements must stay inside the canvas.':'Crop coordinates use the displayed orientation. Resize uses your exact width and height, including any change in proportions.'}</p>
      {Boolean(setup.prompt||(setup.operations??[]).length||Object.keys(setup.controls??{}).length||(setup.references??[]).some(r=>r.contribution||r.role||r.strength!=null))&&<div className={s.error}>
        <p>Your saved generation inputs are still here. Pixel preparation cannot apply those instructions.</p>
        <details><summary>Retained inputs</summary><pre>{JSON.stringify({prompt:setup.prompt,operations:setup.operations,controls:setup.controls,references:setup.references},null,2)}</pre></details>
        <button onClick={()=>update({prompt:'',operations:[],controls:{},references:(setup.references??[]).map(r=>({node_id:r.node_id,contribution:''}))})}>Clear generation inputs for this preparation</button>
      </div>}
      <button onClick={()=>update({action:selected.length>1?'combine':selected.length?'continue':'generate'})}>Use these sources for generation</button>
    </div>
    <div className={s.preparationSources}>
      {items.map((item,i)=>{
        const node=nodes.find(n=>n.id===item.node_id);
        const size=natural[item.node_id]??{width:node?.material?.width??1024,height:node?.material?.height??1024};
        return <div className={s.preparationSource} key={item.node_id}>
          <img src={imageUrl(item.node_id,true)} alt={node?label(node):item.node_id} onLoad={e=>{const image=e.currentTarget;setNatural(old=>({...old,[item.node_id]:{width:image.naturalWidth,height:image.naturalHeight}}));}}/>
          <div><h3>{i+1}. {node?label(node):'Source unavailable'}</h3><p className={s.micro}>{size.width} × {size.height} source pixels</p>
            <div className={s.actions}><button disabled={i===0} onClick={()=>move(i,-1)}>Earlier layer</button><button disabled={i===items.length-1} onClick={()=>move(i,1)}>Later layer</button>
              <button onClick={()=>{const remaining=items.filter((_,j)=>j!==i);update({references:referencesFor(remaining),image_preparation:{...recipe,items:remaining}});}}>Remove source</button></div>
            <label><input type="checkbox" checked={Boolean(item.crop)} onChange={e=>changeItem(i,{crop:e.target.checked?{x:0,y:0,...size}:null})}/> Use a crop</label>
            {item.crop&&<BoxFields title="Crop" value={item.crop} update={crop=>changeItem(i,{crop})}/>}
            {recipe.mode==='crop'&&!item.crop&&<button onClick={()=>changeItem(i,{crop:{x:0,y:0,...size}})}>Start with whole image</button>}
            {recipe.mode==='arrange'&&<BoxFields title="Placement" value={item.placement??{x:0,y:0,...size}} update={placement=>changeItem(i,{placement})}/>}
            {recipe.mode==='resize'&&<button onClick={()=>patch({width:item.crop?.width??size.width,height:item.crop?.height??size.height})}>Use source dimensions</button>}
          </div>
        </div>;
      })}
      <label>Add a source<select value="" onChange={e=>{
        const node=nodes.find(n=>n.id===e.target.value);if(!node)return;
        const next=[...items,{node_id:node.id,crop:null,placement:{x:0,y:0,width:recipe.width??1024,height:recipe.height??1024}}];
        update({references:referencesFor(next),image_preparation:{...recipe,items:next}});
      }}><option value="">Choose material…</option>{nodes.filter(n=>n.available&&n.payload==='image'&&!items.some(item=>item.node_id===n.id)).map(n=><option key={n.id} value={n.id}>{label(n)}</option>)}</select></label>
    </div>
  </div>;
}

function BoxFields({title,value,update}:{title:string;value:Box;update:(box:Box)=>void}) {
  return <fieldset className={s.boxFields}><legend>{title} · pixels</legend>{(['x','y','width','height'] as const).map(key=><label key={key}>{title} {key}<input type="number" min={key==='x'||key==='y'?0:1} value={value[key]} onChange={e=>update({...value,[key]:Number(e.target.value)})}/></label>)}</fieldset>;
}
