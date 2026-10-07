import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {MaterialProvenance,TakeWords,controlValue} from './MaterialProvenance';
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

describe('the seed note says what identifies an attempt',()=>{
  beforeEach(cleanup);
  afterEach(cleanup);
  const hosted={controls:{},submitted:{request:{}},regions:null,request:{operation:{label:'Hosted fixture',revision:'r',maximum_usd_per_attempt:null,applied_controls:{provider:'seed'}}}};
  it('names the seed above when one was recorded',()=>{
    fixtures.record=hosted;
    render(<MaterialProvenance node={node} data={data} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.getByText('The seed above and the submitted operation identify this attempt.')).toBeInTheDocument();
  });
  it('never points at a seed that was not recorded',()=>{
    fixtures.record=hosted;
    const unseeded={...node,seed:null} as Material;
    render(<MaterialProvenance node={unseeded} data={{...data,nodes:[unseeded]}} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.getByText('Seed').nextElementSibling).toHaveTextContent('Not recorded');
    expect(screen.getByText('The submitted operation identifies this attempt.')).toBeInTheDocument();
    expect(screen.queryByText(/seed above/)).toBeNull();
  });
});

describe('controls at review, in words',()=>{
  afterEach(cleanup);
  it('says a seed policy, a switch and a plain value without raw records',()=>{
    expect(controlValue({mode:'random'})).toBe('Random each time');
    expect(controlValue({mode:'fixed',value:41})).toBe('A fixed sequence from 41');
    expect(controlValue({mode:'locked',value:7})).toBe('7 for every attempt');
    expect(controlValue(false)).toBe('No');
    expect(controlValue(2)).toBe('2');
    expect(controlValue('English')).toBe('English');
  });
  it('names who requested a take with the one author line',()=>{
    fixtures.record={controls:{seed:{mode:'random'}},submitted:null,regions:null,request:{actor:{kind:'human',name:'Artist'},operation:{label:'Local fixture',revision:'r',maximum_usd_per_attempt:0,applied_controls:{seed:{mode:'random'}}}}};
    render(<MaterialProvenance node={node} data={data} navigate={()=>{}} openRecord={()=>{}}/>);
    expect(screen.getByText('Requested by').nextElementSibling).toHaveTextContent(/^Artist$/);
    expect(screen.getByText('Random each time')).toBeInTheDocument();
    expect(screen.queryByText('{"mode":"random"}')).toBeNull();
  });
});

describe('a take keeps its words as typed and as submitted',()=>{
  afterEach(cleanup);
  const line={...node,id:'line',payload:'audio',verb:'speak',prompt:'Bring me the ore.  '} as Material;
  it('says the words once when an ElevenLabs take kept them in the request it was sent',()=>{
    fixtures.record={controls:{},regions:null,request:{setup:{prompt:'Bring me the ore.'}},
      submitted:{transport:'audio',provider:'elevenlabs',request:{method:'POST',path:'/v1/text-to-speech/V/with-timestamps',body:{text:'Bring me the ore.'}}}};
    render(<TakeWords node={line}/>);
    expect(screen.getByText('As typed and as submitted.')).toBeInTheDocument();
    expect(screen.queryByRole('heading',{name:/as typed$/})).toBeNull();
  });
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
