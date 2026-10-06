import {test,expect,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Law faces check'}});
const FLUX='Raw · FLUX.2 klein attempts';

async function preparedRun(page:Page,attempts:number,prompt:string){
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill(prompt);
  await drawer.getByRole('spinbutton',{name:FLUX,exact:true}).fill(String(attempts));
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('Ready to submit');
  return drawer;
}

async function shots(page:Page,name:string){
  for(const [width,height] of [[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});
    await page.screenshot({path:artifact(`${name}-${width}.png`)});
  }
}

test('a failed attempt says what happened, what it cost and what to do next; shared causes are named once',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Attempt faces need scripted executors to fail a slot.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Law faces ${Date.now()}`}})).json();
  await page.goto(`/#/projects/${project.id}`);
  const drawer=await preparedRun(page,3,'A brass lamp, three takes.');
  // law 6: the reviewed price is on the button that spends it; a local run is free
  const run=drawer.getByRole('button',{name:'Run 3 attempts · free',exact:true});
  await expect(run).toBeVisible();
  await run.click();
  await expect(drawer).not.toBeVisible();
  const slots=(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots;
  for(const slot of slots.slice(0,2))
    await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'fail',kind:'error',message:'Scripted engine fault',detail:'RuntimeError: scripted engine fault'}});
  // two failures bench the engine for the rest of this run (A9), so the third waits, held
  const states=async()=>(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots.map((s:{status:string})=>s.status);
  await expect.poll(states).toEqual(['failed','failed','held']);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const section=page.getByRole('region',{name:'New direction',exact:true});
  const face=section.getByRole('group',{name:'Attempt 0A · failed',exact:true});
  await expect(face).toContainText('FAILED');
  await expect(face).toContainText('of GPU time spent');                          // law 5: local time, never money
  await expect(face.getByRole('button',{name:'REUSE SETUP',exact:true})).toBeVisible();  // law 4: the next move
  await expect(face.getByText('DETAILS',{exact:true})).toBeVisible();             // law 3: raw text folded away
  await expect(face.getByText('RuntimeError: scripted engine fault')).toBeHidden();       // until DETAILS opens
  const cause:string=(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots[0].cause;
  await expect(face).toContainText(cause);                                        // law 3: the server's plain line
  const shared=section.getByText('0A · 0B',{exact:true});                         // law 1: the shared cause, once
  await expect(shared).toBeVisible();
  await expect(shared.locator('..')).toContainText(cause);
  const held=section.getByRole('group',{name:'Attempt 0C · held',exact:true});
  await expect(held).toContainText('waiting costs nothing');
  await expect(held).not.toContainText('engine:');                                // a hold is named in words, never its scope
  await expect(held.getByRole('button',{name:'REUSE SETUP',exact:true})).toBeVisible();
  await expect(held.getByRole('button',{name:'TRY ONCE MORE',exact:true})).toHaveAttribute('aria-disabled','true');  // law 12
  await shots(page,'fx-c3-failure-face');
  await page.setViewportSize({width:1440,height:900});
  await face.getByText('DETAILS',{exact:true}).click();
  await expect(face.getByText('RuntimeError: scripted engine fault')).toBeVisible();
  await face.getByText('DETAILS',{exact:true}).click();
  await face.getByRole('button',{name:'REUSE SETUP',exact:true}).click();
  const reused=page.getByRole('region',{name:'Preparation',exact:true});
  await expect(reused).toBeVisible();
  await reused.getByRole('button',{name:'Close · Esc'}).click();
  await expect(reused).not.toBeVisible();
  // leave the queue tidy: cancel the held attempt through the run's own controls
  await page.getByRole('region',{name:'Run 0 execution',exact:true}).getByRole('button',{name:'Cancel active work',exact:true}).click();
  await expect.poll(states).toEqual(['failed','failed','skipped']);
});

test('a held attempt carries its own resume, recorded with its participant',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Held attempts need scripted executors.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Held face ${Date.now()}`}})).json();
  await page.goto(`/#/projects/${project.id}`);
  const controls=page.getByRole('region',{name:'Project execution',exact:true});
  await controls.getByRole('button',{name:'Pause project',exact:true}).click();
  const drawer=await preparedRun(page,2,'A brass bell, held.');
  await drawer.getByRole('button',{name:'Run 2 attempts · free',exact:true}).click();
  const states=async()=>(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots.map((s:{status:string})=>s.status);
  await expect.poll(states).toEqual(['held','held']);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const face=page.getByRole('group',{name:'Attempt 0A · held',exact:true});
  await expect(face).toContainText('waiting costs nothing');
  await face.getByRole('button',{name:'RESUME PROJECT',exact:true}).click();
  await expect.poll(states).toEqual(['running','queued']);
  const control=await(await request.get(`/api/crexil/execution/project/${project.id}`)).json();
  expect(control).toMatchObject({paused:false,last_action:'resume',actor:{kind:'human',name:'Artist'}});   // law 23
  // free the one GPU lane for the specs that follow
  for(const slot of (await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots)
    await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'land',color:[90,120,150]}});
  await expect.poll(states).toEqual(['landed','landed']);
});

test('verbs and operations that cannot act stay visible, de-emphasised, with their reasons (law 12)',async({page,request})=>{
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Badges ${Date.now()}`}})).json();
  const material=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Badge source.png',data_base64:readFileSync(source).toString('base64')}})).json();
  await request.put(`/api/crexil/directions/${material.direction.id}/selection`,{data:{...command(),revision:0,node_ids:[material.node.id]}});
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Act on Badge source.png',exact:true}).click();
  const card=page.getByRole('region',{name:'Actions on material',exact:true});
  await card.getByText(/^Not for this material/).click();
  const reduce=card.getByRole('button',{name:'Reduce mesh',exact:true});
  await expect(reduce).toHaveAttribute('aria-disabled','true');
  await expect(reduce).toHaveAccessibleDescription('Works on meshes; this is an image.');
  await expect(card.getByRole('button',{name:'Edit regions',exact:true})).not.toHaveAttribute('aria-disabled','true');
  await card.getByRole('button',{name:'Edit regions',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const voice=drawer.locator('[data-standing="notForThis"]').filter({has:page.getByText(/^Design voice · /)}).first();
  await expect(voice).toBeVisible();
  await expect(voice).toContainText('Not for this action');
  await expect(voice).toContainText('This operation cannot edit a region. Its saved inputs remain available.');
  await voice.scrollIntoViewIfNeeded();
  await shots(page,'fx-c3-drawer-badge');
});
