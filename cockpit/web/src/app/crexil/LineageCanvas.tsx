// Measure actual card anchors after layout and both scroll axes. The SVG is
// pointer-transparent; clipping never substitutes another card for a source.
// A change of its inputs (bindings, nodes, acting, enabled) observes afresh the rows,
// strips, cards and starters then drawn; its caller changes them only with the record,
// the room or the acting takes, so a render alone never re-measures.
import {useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import {ancestors,routeLineage,type Anchor,type Binding,type RowBox} from './lineage';
import type {Material} from './api';
import {mediumOf} from './media';
import s from './Filmstrip.module.css';

export function LineageCanvas({bindings,nodes,acting,children,enabled}:{bindings:Binding[];nodes:Material[];acting:string[];children:ReactNode;enabled:boolean}){
  const root=useRef<HTMLDivElement>(null);
  const [drawing,setDrawing]=useState<ReturnType<typeof routeLineage>&{width:number;height:number}>({paths:[],dots:[],gaps:{},topGap:16,gutter:16,width:0,height:0});
  const ancestry=ancestors(nodes,acting);
  useLayoutEffect(()=>{
    const host=root.current;if(!host||!enabled)return;
    let frame=0,active=true;
    const measure=()=>{
      if(!active)return;
      const box=host.getBoundingClientRect();
      const rowElements=[...host.querySelectorAll<HTMLElement>('[data-lineage-row]')];
      const rows:RowBox[]=rowElements.map(el=>{const r=el.getBoundingClientRect(),section=el.querySelector('[data-lineage-strip]')!.getBoundingClientRect();return {row:Number(el.dataset.lineageRow),top:r.top-box.top,bottom:r.bottom-box.top,left:section.left-box.left};});
      const anchors=(selector:string,key:'lineageNode'|'lineageStart',bottom:boolean):Anchor[]=>[...host.querySelectorAll<HTMLElement>(selector)].flatMap(el=>{
        const r=el.getBoundingClientRect(),strip=el.closest('[data-lineage-strip]')!.getBoundingClientRect(),x=(r.left+r.right)/2;
        return [{id:el.dataset[key]!,row:Number(el.closest<HTMLElement>('[data-lineage-row]')!.dataset.lineageRow),x:x-box.left,y:(bottom?r.bottom:r.top)-box.top,
          left:r.left-box.left,right:r.right-box.left,visible:x>=strip.left&&x<=strip.right}];
      });
      const result=routeLineage(rows,anchors('[data-lineage-node]','lineageNode',true),anchors('[data-lineage-start]','lineageStart',false),bindings,ancestors(nodes,acting));
      let changed=false;
      const set=(name:string,value:string)=>{if(host.style.getPropertyValue(name)!==value){host.style.setProperty(name,value);changed=true;}};
      set('--lineage-gutter',`${result.gutter}px`);
      set('--lineage-top-gap',`${result.topGap}px`);
      for(const row of rows)set(`--lineage-gap-${row.row}`,`${result.gaps[row.row]??26}px`);
      if(changed){schedule();return;}
      const next={...result,width:host.clientWidth,height:host.getBoundingClientRect().height};
      setDrawing(old=>JSON.stringify(old)===JSON.stringify(next)?old:next);
    };
    const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(measure);};
    const observer=new ResizeObserver(schedule);
    observer.observe(host);host.querySelectorAll('[data-lineage-row],[data-lineage-strip],[data-lineage-node],[data-lineage-start]').forEach(el=>observer.observe(el));
    host.addEventListener('scroll',schedule,true);window.addEventListener('resize',schedule);void document.fonts.ready.then(schedule);schedule();
    return()=>{active=false;cancelAnimationFrame(frame);observer.disconnect();host.removeEventListener('scroll',schedule,true);window.removeEventListener('resize',schedule);};
  },[bindings,nodes,acting,enabled]);
  const byId=new Map(bindings.map(b=>[b.id,b]));
  const color=(id:string,lit:boolean)=>lit?'var(--accent)':mediumOf(byId.get(id)?.payload??'image').hue;
  return <div className={s.lineageContent} ref={root}>
    {enabled&&<svg className={s.lineageLines} width={drawing.width} height={drawing.height} role="img" aria-label="Source connections" data-connection-count={drawing.paths.filter(p=>p.arrival).length}>
      <title>Source cards connect to the preparations and recorded runs they feed.</title>
      {drawing.paths.map((path,i)=><polyline key={i} points={path.points} fill="none" stroke={color(path.binding,path.lit)} strokeWidth={1} opacity={path.lit?1:.8} strokeDasharray={byId.get(path.binding)?.draft?'4 4':undefined}
        data-binding={path.binding} data-arrival={path.arrival||undefined} data-bus={path.bus||undefined} data-source={path.parent} data-highlighted={path.lit||undefined}/>) }
      {drawing.dots.map((dot,i)=><circle key={i} cx={dot.x} cy={dot.y} r={2.5} fill={color(dot.binding,dot.lit)} opacity={dot.lit?1:.8}/>)}
    </svg>}
    <div className={s.lineageRows} data-ancestry={ancestry.size}>{children}</div>
  </div>;
}
