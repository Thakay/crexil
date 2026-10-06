// A Filmstrip connection describes one exact source set, not the current draft
// substituted for old work. Each starter's source set travels on one bundled bus.
import {preparation,type Direction,type Material,type Snapshot} from './api';

export type Binding={id:string;direction:string;parents:string[];outputs:string[];title:string;mesh:boolean;draft:boolean};
export type Anchor={id:string;row:number;x:number;y:number;left?:number;right?:number;visible?:boolean};
export type RowBox={row:number;top:number;bottom:number;left:number};
export type Stroke={points:string;binding:string;parent?:string;arrival?:boolean;bus?:boolean;lit:boolean};
export type Dot={x:number;y:number;binding:string;lit:boolean};

export function sourceBindings(direction:Direction,data:Snapshot):Binding[]{
  const aliases=(data.restoration?.identities as {nodes?:Record<string,string>}|undefined)?.nodes??{};
  const output=data.nodes.filter(n=>direction.node_ids.includes(n.id));
  const sets:Binding[]=[];
  const add=(parents:string[],outputs:string[],title:string,draft:boolean,mesh:boolean)=>{
    const same=sets.find(s=>s.draft===draft&&s.mesh===mesh&&JSON.stringify(s.parents)===JSON.stringify(parents));
    if(same){same.outputs.push(...outputs);return;}
    sets.push({id:`${direction.id}:${sets.length}`,direction:direction.id,parents,outputs,title,mesh,draft});
  };
  const prep=preparation(direction);
  if(direction.state==='draft')add((prep.references??[]).map(ref=>ref.node_id),[], 'Saved preparation',true,['mesh','decimate','texture'].includes(prep.action));
  for(const turn of data.turns.filter(t=>direction.turn_ids.includes(t.id))){
    const nodes=output.filter(n=>n.turn_id===turn.id);
    for(const node of nodes)add(node.parents,[node.id],`Run ${turn.idx} sources`,false,node.payload==='3d');
    if(!nodes.length){
      const frozen=turn.dials as {crexil?:{sources?:{node_id:string}[];setup?:{action?:string}}};
      add(frozen.crexil?.sources?.map(s=>aliases[s.node_id]??s.node_id)??[],[],`Run ${turn.idx} sources`,false,['mesh','decimate','texture'].includes(frozen.crexil?.setup?.action??''));
    }
  }
  for(const node of output.filter(n=>!n.turn_id))add(node.parents,[node.id],'Reported sources',false,node.payload==='3d');
  return sets;
}

export function ancestors(nodes:Material[],selected:string[]):Set<string>{
  const byId=new Map(nodes.map(n=>[n.id,n])),found=new Set<string>(),todo=[...selected];
  while(todo.length){const id=todo.pop()!;if(found.has(id))continue;found.add(id);todo.push(...(byId.get(id)?.parents??[]));}
  return found;
}

export function routeLineage(rows:RowBox[],faces:Anchor[],starts:Anchor[],bindings:Binding[],lit:Set<string>){
  const ordered=[...rows].sort((a,b)=>a.top-b.top),rowIndex=new Map(ordered.map((r,i)=>[r.row,i]));
  const byFace=new Map(faces.map(f=>[f.id,f])),byStart=new Map(starts.map(s=>[s.id,s]));
  const paths:Stroke[]=[],dots:Dot[]=[],gaps:Record<number,number>={};
  let topGap=16;
  type Bus={binding:Binding;parents:Anchor[];gap:number;end:number;arrival:boolean;lane:number;y:number;lo:number;hi:number};
  type Group={binding:Binding;start:Anchor;parents:Anchor[];direct:boolean;lane:number;buses:Bus[];low:number;high:number;arrivalX:number;dropX:number;stacked:boolean};
  const groups:Group[]=[];
  for(const binding of bindings){
    const start=byStart.get(binding.id),parents=binding.parents.map(id=>byFace.get(id)).filter((p):p is Anchor=>Boolean(p));
    if(!start||!parents.length)continue;
    const target=rowIndex.get(start.row)!;
    const parentRows=[...new Set(parents.map(p=>rowIndex.get(p.row)!))];
    const direct=parentRows.length===1&&parentRows[0]===target-1;
    groups.push({binding,start,parents,direct,lane:0,buses:[],low:Math.min(target-1,...parentRows),high:Math.max(target-1,...parentRows),arrivalX:start.x,dropX:start.x,stacked:false});
  }
  // Empty-source starters still occupy space. Later recorded starters in the
  // same column need their own corridor around them, never through their faces.
  for(const g of [...groups].sort((a,b)=>rowIndex.get(a.start.row)!-rowIndex.get(b.start.row)!||a.start.y-b.start.y)){
    const previous=groups.filter(other=>rowIndex.get(other.start.row)===rowIndex.get(g.start.row)!-1);
    const positions=[g.start.x,...Array.from({length:previous.length+1},(_,i)=>[g.start.x+6*(i+1),g.start.x-6*(i+1)]).flat()];
    g.arrivalX=positions.find(x=>previous.every(other=>Math.abs(other.arrivalX-x)>=3)
      &&(g.start.left===undefined||x>g.start.left+2)&&(g.start.right===undefined||x<g.start.right-2))??g.start.x;
    const above=starts.filter(other=>other.row===g.start.row&&other.y<g.start.y
      &&(other.left??other.x)<=(g.start.right??g.start.x)&&(other.right??other.x)>=(g.start.left??g.start.x));
    g.stacked=above.length>0;
    g.dropX=g.stacked?(g.start.left??g.start.x)-6-5*(above.length-1):g.arrivalX;
  }
  const trunks:{low:number;high:number}[][]=[];
  for(const g of groups.filter(g=>!g.direct).sort((a,b)=>(b.high-b.low)-(a.high-a.low))){
    let lane=trunks.findIndex(items=>items.every(i=>g.high<i.low||g.low>i.high));
    if(lane<0){lane=trunks.length;trunks.push([]);}trunks[lane].push(g);g.lane=lane;
  }
  const left=Math.min(...ordered.map(r=>r.left));
  const buses:Bus[]=[];
  for(const g of groups){
    const gx=left-6-g.lane*5;
    for(const row of [...new Set(g.parents.map(p=>p.row))]){
      const parents=g.parents.filter(p=>p.row===row),end=g.direct?g.dropX:gx,xs=[end,...parents.map(p=>p.x)];
      const bus={binding:g.binding,parents,gap:rowIndex.get(row)!,end,arrival:g.direct,lane:0,y:0,lo:Math.min(...xs),hi:Math.max(...xs)};
      buses.push(bus);g.buses.push(bus);
    }
    if(!g.direct){
      const bus={binding:g.binding,parents:[],gap:rowIndex.get(g.start.row)!-1,end:g.dropX,arrival:true,lane:0,y:0,lo:Math.min(gx,g.dropX),hi:Math.max(gx,g.dropX)};
      buses.push(bus);g.buses.push(bus);
    }
  }
  for(const gap of [...new Set(buses.map(b=>b.gap))]){
    const lanes:{lo:number;hi:number}[][]=[];
    for(const b of buses.filter(b=>b.gap===gap).sort((a,b)=>(b.hi-b.lo)-(a.hi-a.lo))){
      let lane=lanes.findIndex(spans=>spans.every(s=>b.hi<s.lo-8||b.lo>s.hi+8));
      if(lane<0){lane=lanes.length;lanes.push([]);}lanes[lane].push(b);b.lane=lane;
    }
    const pitch=lanes.length<=2?10:8,required=Math.max(gap<0?16:26,16+(lanes.length-1)*pitch);
    if(gap>=0)gaps[ordered[gap].row]=required;else topGap=required;
    const bottom=gap<0?0:ordered[gap].bottom;
    const top=ordered[gap+1]?.top??bottom+required;
    const middle=(bottom+top)/2;
    for(const b of buses.filter(b=>b.gap===gap))b.y=middle-(lanes.length-1)*pitch/2+b.lane*pitch;
  }
  const isLit=(binding:Binding)=>binding.outputs.some(id=>lit.has(id));
  const drawnBuses:Bus[]=[];
  for(const g of groups){
    if(g.start.visible===false||!g.parents.some(parent=>parent.visible!==false))continue;
    const gx=left-6-g.lane*5,highlight=isLit(g.binding);
    const visibleBuses=g.buses.filter(b=>b.arrival||b.parents.some(parent=>parent.visible!==false));
    for(const b of visibleBuses){
      drawnBuses.push(b);
      const xs=[b.end,...b.parents.filter(parent=>parent.visible!==false).map(parent=>parent.x),...(!g.direct?[gx]:[])];
      const lo=Math.min(...xs),hi=Math.max(...xs);
      paths.push({points:`${lo},${b.y} ${hi},${b.y}`,binding:g.binding.id,bus:true,lit:highlight});
      if(b.arrival){
        const points=g.stacked?`${b.end},${b.y} ${b.end},${g.start.y-6} ${g.arrivalX},${g.start.y-6} ${g.arrivalX},${g.start.y}`
          :`${b.end},${b.y} ${b.end},${g.start.y}`;
        paths.push({points,binding:g.binding.id,arrival:true,lit:highlight});
        if(lo<b.end&&b.end<hi)dots.push({x:b.end,y:b.y,binding:g.binding.id,lit:highlight});
      }
      if(!g.direct)dots.push({x:gx,y:b.y,binding:g.binding.id,lit:highlight});
    }
    if(!g.direct)paths.push({points:`${gx},${Math.min(...visibleBuses.map(b=>b.y))} ${gx},${Math.max(...visibleBuses.map(b=>b.y))}`,binding:g.binding.id,lit:highlight});
  }
  // One shared stem per source, with a dot at every actual fork. A crossing
  // without a dot remains a crossing, including unrelated gutter paths.
  for(const parent of faces){
    if(parent.visible===false)continue;
    const joins=drawnBuses.filter(b=>b.parents.some(p=>p.id===parent.id)).sort((a,b)=>a.y-b.y);
    let y=parent.y;
    for(let i=0;i<joins.length;i++){
      const b=joins[i],highlight=joins.slice(i).some(next=>isLit(next.binding));
      paths.push({points:`${parent.x},${y} ${parent.x},${b.y}`,binding:b.binding.id,parent:parent.id,lit:highlight});
      dots.push({x:parent.x,y:b.y,binding:b.binding.id,lit:isLit(b.binding)});y=b.y;
    }
  }
  return {paths,dots,gaps,topGap,gutter:Math.max(16,8+trunks.length*5)};
}
