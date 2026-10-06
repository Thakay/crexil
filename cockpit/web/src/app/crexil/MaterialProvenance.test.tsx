import {render,screen,within} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import {MaterialProvenance} from './MaterialProvenance';
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
