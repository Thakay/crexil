import {describe,expect,it} from 'vitest';
import {ancestors,routeLineage,sourceBindings,type Binding} from './lineage';
import type {Direction,Material,Snapshot} from './api';

const binding=(id:string,parents:string[],outputs=[id]):Binding=>({id,direction:id,parents,outputs,title:id,payload:'image',draft:false});
const rows=[{row:0,top:16,bottom:194,left:160},{row:1,top:220,bottom:398,left:160},{row:2,top:424,bottom:602,left:160}];
const faces=[{id:'a',row:0,x:400,y:162},{id:'b',row:0,x:650,y:162}];

describe('Filmstrip source routing',()=>{
  it('combines two parents into one arrival and shares the stem of a source used twice',()=>{
    const graph=routeLineage(rows,faces,[{id:'combine',row:1,x:220,y:232},{id:'edit',row:1,x:780,y:232}],
      [binding('combine',['a','b']),binding('edit',['b'])],new Set(['combine']));
    expect(graph.paths.filter(p=>p.arrival).map(p=>p.binding)).toEqual(['combine','edit']);
    expect(graph.paths.filter(p=>p.bus)).toHaveLength(2);
    const stem=graph.paths.filter(p=>p.parent==='b');
    expect(stem).toHaveLength(2);
    expect(stem[0].points.split(' ')[1]).toBe(stem[1].points.split(' ')[0]);
    expect(graph.dots.filter(dot=>dot.x===650)).toHaveLength(2);
    expect(graph.paths.find(p=>p.arrival&&p.binding==='combine')?.lit).toBe(true);
    expect(graph.paths.find(p=>p.arrival&&p.binding==='edit')?.lit).toBe(false);
  });

  it('carries multi-row and same-row sources through the gutter without crossing intermediate cards',()=>{
    const graph=routeLineage(rows,[...faces,{id:'c',row:1,x:400,y:366}],
      [{id:'long',row:2,x:220,y:436},{id:'same',row:0,x:780,y:28}],
      [binding('long',['a','c']),binding('same',['b'])],new Set());
    expect(graph.paths.filter(p=>p.arrival)).toHaveLength(2);
    const long=graph.paths.filter(p=>p.binding==='long'&&!p.arrival&&!p.bus&&!p.parent);
    expect(long).toHaveLength(1);
    const xs=long[0].points.split(' ').map(point=>Number(point.split(',')[0]));
    expect(xs[0]).toBe(xs[1]);expect(xs[0]).toBeLessThan(160);
    expect(graph.paths.every(path=>!path.points.includes('NaN'))).toBe(true);
  });

  it('does not invent an endpoint for a source hidden by display choice or horizontal clipping',()=>{
    const graph=routeLineage(rows,[],[{id:'child',row:1,x:220,y:232}],[binding('child',['a'])],new Set());
    expect(graph.paths).toEqual([]);expect(graph.dots).toEqual([]);
  });

  it('reserves enough space above the first row for same-row and upward arrivals',()=>{
    const parents=[...faces,{id:'c',row:1,x:400,y:366},{id:'d',row:2,x:400,y:570}];
    const starts=['same','up-one','up-two','same-again'].map((id,i)=>({id,row:0,x:500+i*50,y:28}));
    const bindings=[binding('same',['a']),binding('up-one',['c']),binding('up-two',['d']),binding('same-again',['b'])];
    const measured=routeLineage(rows,parents,starts,bindings,new Set());
    expect(measured.topGap).toBeGreaterThan(16);
    const shift=measured.topGap-16;
    const laidOut=routeLineage(rows.map(row=>({...row,top:row.top+shift,bottom:row.bottom+shift})),
      parents.map(p=>({...p,y:p.y+shift})),starts.map(s=>({...s,y:s.y+shift})),bindings,new Set());
    expect(laidOut.topGap).toBe(measured.topGap);
    const topBuses=laidOut.paths.filter(path=>path.bus&&Number(path.points.split(' ')[0].split(',')[1])<rows[0].top+shift);
    expect(topBuses).toHaveLength(4);
    expect(topBuses.every(path=>path.points.split(' ').every(point=>Number(point.split(',')[1])>=8))).toBe(true);
    expect(laidOut.paths.filter(path=>path.arrival)).toHaveLength(4);
  });

  it('offsets consecutive aligned arrivals and routes stacked starters outside earlier cards',()=>{
    const parents=[...faces,{id:'c',row:1,x:400,y:366}];
    const starts=[{id:'first',row:1,x:220,y:232,left:158,right:282},
      {id:'recorded',row:1,x:220,y:360,left:158,right:282},
      {id:'next-row',row:2,x:220,y:436,left:158,right:282}];
    const graph=routeLineage(rows,parents,starts,
      [binding('first',['a']),binding('recorded',['b']),binding('next-row',['c'])],new Set());
    const arrival=(id:string)=>graph.paths.find(path=>path.arrival&&path.binding===id)!.points.split(' ').map(point=>point.split(',').map(Number));
    const first=arrival('first'),next=arrival('next-row'),recorded=arrival('recorded');
    expect(next.at(-1)![0]).not.toBe(first.at(-1)![0]);
    expect(recorded).toHaveLength(4);
    expect(recorded[0][0]).toBeLessThan(158);
    expect(recorded[1][0]).toBe(recorded[0][0]);
    expect(recorded[1][1]).toBe(354);
    expect(recorded.at(-1)).toEqual([220,360]);
    expect(graph.paths.filter(path=>path.arrival)).toHaveLength(3);
  });

  it('keeps layout capacity stable across true clipping while drawing only visible source anchors',()=>{
    const parents=[...faces,{id:'c',row:1,x:400,y:366}];
    const starts=[{id:'far-a',row:2,x:220,y:436},{id:'far-b',row:2,x:700,y:436}];
    const bindings=[binding('far-a',['a','c']),binding('far-b',['b'])];
    const visible=routeLineage(rows,parents,starts,bindings,new Set());
    const clipped=routeLineage(rows,parents.map(p=>({...p,visible:p.id!=='b'})),starts,bindings,new Set());
    expect(clipped.gutter).toBe(visible.gutter);
    expect(clipped.gaps).toEqual(visible.gaps);
    expect(clipped.paths.some(path=>path.parent==='b'||path.binding==='far-b')).toBe(false);
    expect(clipped.paths.filter(path=>path.arrival).map(path=>path.binding)).toEqual(['far-a']);
    const hiddenStarter=routeLineage(rows,parents,starts.map(s=>({...s,visible:s.id!=='far-a'})),bindings,new Set());
    expect(hiddenStarter.gutter).toBe(visible.gutter);
    expect(hiddenStarter.paths.some(path=>path.binding==='far-a')).toBe(false);
  });

  it('keeps original run sources separate from a subsequently edited draft',()=>{
    const direction={id:'d',state:'draft',node_ids:['out'],turn_ids:['run'],setup:{action:'continue',references:[{node_id:'new'}]}} as unknown as Direction;
    const snapshot={nodes:[{id:'out',turn_id:'run',parents:['old'],payload:'image'}],turns:[{id:'run',idx:0,dials:{crexil:{sources:[{node_id:'old'}]}}}]} as unknown as Snapshot;
    const sets=sourceBindings(direction,snapshot);
    expect(sets.map(set=>({parents:set.parents,draft:set.draft,outputs:set.outputs}))).toEqual([
      {parents:['new'],draft:true,outputs:[]},{parents:['old'],draft:false,outputs:['out']},
    ]);
  });

  it('keeps an empty-reference draft on its own starter and retains root ancestry identity',()=>{
    const direction={id:'d',state:'draft',node_ids:['old-output','root-output'],turn_ids:['old-run','root-run'],
      setup:{action:'generate',references:[]}} as unknown as Direction;
    const snapshot={nodes:[{id:'old-output',turn_id:'old-run',parents:['old-source'],payload:'image'},
      {id:'root-output',turn_id:'root-run',parents:[],payload:'image'}],
      turns:[{id:'old-run',idx:0,dials:{}},{id:'root-run',idx:1,dials:{}}]} as unknown as Snapshot;
    const sets=sourceBindings(direction,snapshot);
    expect(sets.map(set=>({parents:set.parents,draft:set.draft,outputs:set.outputs}))).toEqual([
      {parents:[],draft:true,outputs:[]},
      {parents:['old-source'],draft:false,outputs:['old-output']},
      {parents:[],draft:false,outputs:['root-output']},
    ]);
    const ancestry=ancestors(snapshot.nodes,['root-output']);
    expect(sets[0].outputs.some(id=>ancestry.has(id))).toBe(false);
    expect(sets[2].outputs.some(id=>ancestry.has(id))).toBe(true);
  });

  it('does not merge image and mesh source sets under the first output medium',()=>{
    const direction={id:'d',state:'submitted',node_ids:['image','mesh'],turn_ids:['run'],setup:{}} as unknown as Direction;
    const snapshot={nodes:[{id:'image',turn_id:'run',parents:['source'],payload:'image'},
      {id:'mesh',turn_id:'run',parents:['source'],payload:'3d'}],turns:[{id:'run',idx:0,dials:{}}]} as unknown as Snapshot;
    expect(sourceBindings(direction,snapshot).map(set=>({payload:set.payload,outputs:set.outputs}))).toEqual([
      {payload:'image',outputs:['image']},{payload:'3d',outputs:['mesh']},
    ]);
  });

  it('lights only the real ancestry and terminates on missing records or cyclic supplied history',()=>{
    const nodes=[{id:'child',parents:['source']},{id:'source',parents:['root']},{id:'root',parents:['child']},{id:'sibling',parents:['source']}] as Material[];
    expect([...ancestors(nodes,['child'])].sort()).toEqual(['child','root','source']);
  });
});


it('resolves restored unfinished sources without rewriting frozen accepted request JSON',()=>{
  const direction={id:'draft',state:'submitted',setup:{},node_ids:[],turn_ids:['turn']} as unknown as Direction;
  const snapshot={nodes:[],turns:[{id:'turn',idx:1,dials:{crexil:{sources:[{node_id:'original'}]}}}],
    restoration:{identities:{nodes:{original:'restored'}}}} as unknown as Snapshot;
  expect(sourceBindings(direction,snapshot)[0].parents).toEqual(['restored']);
  expect((snapshot.turns[0].dials.crexil as {sources:{node_id:string}[]}).sources[0].node_id).toBe('original');
});
