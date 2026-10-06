import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {MaterialProvenance,TakeWords} from './MaterialProvenance';
import type {Material,Snapshot} from './api';

const fixtures=vi.hoisted(()=>({record:{request:null,controls:{},submitted:null,regions:null} as Record<string,unknown>,refetch:vi.fn()}));
vi.mock('../../api/client',()=>({$api:{useQuery:(_method:string,path:string)=>({data:path==='/api/crexil/material/{nid}/record'?fixtures.record:{decisions:[]},refetch:fixtures.refetch})}}));
const node={id:'take-two',engine:'fixture',payload:'image',take:1,seed:42,created_at:'2026-10-02T00:00:00Z',committed_usd:.04,charged_usd:.04} as Material;
const data={project:{id:'project'},nodes:[node],event_seq:3} as Snapshot;

describe('frozen provenance facts',()=>{
  it('identifies a tariff reservation without calling it a spending bound',()=>{
    fixtures.record={controls:{},submitted:{request:{seed:42}},regions:null,request:{setup:{spending_policy:'published_tariff'},
      operation:{label:'Hosted fixture',revision:'old',maximum_usd_per_attempt:null,cost_basis:'Published tariff; no guaranteed ceiling',applied_controls:{seed:41,n:1}}}};
    const view=render(<MaterialProvenance node={node} data={data} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.getByText('Reserved at submission')).toBeInTheDocument();
    expect(screen.queryByText('Spending bound')).not.toBeInTheDocument();
    expect(screen.getByText('Controls at review')).toBeInTheDocument();
    const seed=screen.getAllByText(/^seed$/i)[0];
    expect(seed.nextElementSibling).toHaveTextContent('42');
    expect(within(view.container).getByText('41')).toBeInTheDocument();
    expect(screen.queryByText('Actual controls')).not.toBeInTheDocument();
  });

  it('keeps a known bound and unavailable operation history explicit',()=>{
    fixtures.record={controls:{steps:4},submitted:null,regions:null,request:{operation:{label:'Local fixture',revision:'saved-revision',maximum_usd_per_attempt:0}}};
    const view=render(<MaterialProvenance node={node} data={data} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.getByText('Spending bound')).toBeInTheDocument();
    expect(screen.getByText('saved-revision')).toBeInTheDocument();
    fixtures.record={controls:{},submitted:null,regions:null,request:{operation:null}};
    view.rerender(<MaterialProvenance node={node} data={data} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.queryByText('Spending bound')).not.toBeInTheDocument();
    expect(screen.queryByText('saved-revision')).not.toBeInTheDocument();
    expect(screen.getAllByText('Not recorded').length).toBeGreaterThan(0);
  });
});

describe('a take keeps its words as typed and as submitted',()=>{
  afterEach(cleanup);
  const line={...node,id:'line',payload:'audio',verb:'speak',prompt:'Bring me the ore.  '} as Material;
  it('says the words once when they were submitted as typed',()=>{
    fixtures.record={controls:{},regions:null,request:{setup:{prompt:'Bring me the ore.  '}},submitted:{text:'Bring me the ore.'}};
    render(<TakeWords node={line}/>);
    expect(screen.getByRole('heading',{name:'Line to speak'})).toBeInTheDocument();
    expect(screen.getByText('As typed and as submitted.')).toBeInTheDocument();
    expect(screen.queryByRole('heading',{name:/as typed$/})).toBeNull();
  });
  it('shows both versions when the request sent more than was typed',()=>{
    const image={...node,verb:'continue',prompt:'A stout dwarf blacksmith.\n\nKeep the silhouette.'} as Material;
    fixtures.record={controls:{},regions:null,request:{setup:{prompt:'A stout dwarf blacksmith.'}},submitted:{}};
    render(<TakeWords node={image}/>);
    expect(screen.getByRole('heading',{name:'Prompt as typed'}).nextElementSibling).toHaveTextContent(/^A stout dwarf blacksmith\.$/);
    expect(screen.getByRole('heading',{name:'Prompt as submitted'}).nextElementSibling).toHaveTextContent('Keep the silhouette.');
  });
  it("shows a blind take's own words and claims neither version while the record is withheld",()=>{
    fixtures.record={controls:{},regions:null,request:null,submitted:null};
    render(<TakeWords node={{...line,identity_hidden:true} as Material}/>);
    expect(screen.getByRole('heading',{name:'Line to speak'}).nextElementSibling).toHaveTextContent('Bring me the ore.');
    expect(screen.queryByText('As typed and as submitted.')).toBeNull();
  });
});
