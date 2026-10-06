import {test,expect,type APIRequestContext,type Locator,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Law faces check'}});
const FLUX='Raw · FLUX.2 klein attempts';
const ARTIST={kind:'human',name:'Artist'};
const FAIL={outcome:'fail',kind:'error',message:'Scripted engine fault',detail:'RuntimeError: scripted engine fault'};
// What a resume and a cancel say they do, as their tooltips (attempts.ts).
const RESUME='Sends the attempts this engine holds. Each was reserved when its run was reviewed, and meets the spending limits again before it is sent.';
const SKIPS='Skips this attempt. Nothing more is sent, and skipping costs nothing.';
const SENT='Its request is already sent, so it keeps this cell: the take still lands here if it arrives, and its charge stays unverified until the provider reports it.';
// The hues as the browser resolves them: money gold on a paid move, the primary frame's text.
const GOLD='rgb(210, 162, 76)',TEXT='rgb(228, 232, 235)';

async function preparedRun(page:Page,attempts:number,prompt:string){
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill(prompt);
  await drawer.getByRole('spinbutton',{name:FLUX,exact:true}).fill(String(attempts));
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('Ready to submit');
  await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('This run makes no automatic retries.');   // law 9
  return drawer;
}

async function shots(page:Page,name:string){
  for(const [width,height] of [[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});
    await page.screenshot({path:artifact(`${name}-${width}.png`)});
  }
}

/** Every move on a face is drawn whole: inside the cell's frozen footprint (law 8), its words and
 *  its price never clipped (law 6). */
async function drawnWhole(face:Locator){
  const box=(await face.boundingBox())!;
  for(const button of await face.getByRole('button').all()){
    const own=(await button.boundingBox())!;
    expect(own.x>=box.x&&own.y>=box.y&&own.x+own.width<=box.x+box.width&&own.y+own.height<=box.y+box.height,
      `${await button.textContent()} sits inside its face`).toBe(true);
    expect(await button.evaluate(el=>el.scrollWidth<=el.clientWidth&&el.scrollHeight<=el.clientHeight),
      `${await button.textContent()} shows all its words`).toBe(true);
  }
}

/** The project's attempt moves as the record keeps them: each authored, on its own attempt (law 23). */
async function movesOf(request:APIRequestContext,projectId:string){
  const {events}=await(await request.get(`/api/events?workspace_id=${projectId}&limit=500`)).json();
  return (events as {type:string;slot_id:string;payload:{scope:string;action:string;actor:unknown}}[])
    .filter(event=>event.type==='execution.control'&&event.payload.scope==='slot')
    .map(event=>({slot:event.slot_id,action:event.payload.action,actor:event.payload.actor}));
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
    await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:FAIL});
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
  // laws 4 and 23: preparing again leads; TRY ONCE MORE is live and free for local work; the cancel comes last
  await expect(held.getByRole('button')).toHaveText(['REUSE SETUP','TRY ONCE MORE','CANCEL ATTEMPT']);
  const tryAgain=held.getByRole('button',{name:'TRY ONCE MORE',exact:true});
  await expect(tryAgain).not.toHaveAttribute('aria-disabled','true');
  await expect(tryAgain).toHaveAccessibleDescription(RESUME);
  await drawnWhole(held);
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
  // TRY ONCE MORE sends the held attempt as it is, and the face follows the server
  await tryAgain.click();
  await expect.poll(states).toEqual(['failed','failed','running']);
  // its cancel stops it from its own face, and leaves the one GPU lane free for the specs that follow
  const running=section.getByRole('group',{name:'Attempt 0C · running',exact:true});
  const stop=running.getByRole('button',{name:'CANCEL ATTEMPT',exact:true});
  await expect(stop).toHaveAccessibleDescription('Asks the engine to stop this attempt. Local work costs no money.');
  await stop.click();
  await expect.poll(states).toEqual(['failed','failed','failed']);
  expect(await movesOf(request,project.id)).toEqual([{slot:slots[2].id,action:'resume',actor:ARTIST},
    {slot:slots[2].id,action:'cancel',actor:ARTIST}]);
});

test('a held paid attempt carries its reservation on TRY ONCE MORE, never as the default; waiting attempts can be given up',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Held attempts need scripted executors.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Paid hold ${Date.now()}`}})).json();
  type Slot={id:string;pos:number;status:string;retry_of:string|null};
  const slotsNow=async():Promise<Slot[]>=>(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots;
  const count=async()=>{const out:Record<string,number>={};for(const slot of await slotsNow())out[slot.status]=(out[slot.status]??0)+1;return out;};
  const name=(slot:{pos:number},state:string)=>`Attempt 0${String.fromCharCode(65+slot.pos)} · ${state}`;
  const first=async(test:(slot:Slot)=>boolean)=>(await slotsNow()).find(test)!;
  await page.goto(`/#/projects/${project.id}`);
  // A sound run through the drawer: its tariff budget, its retry line (law 9) and its price on Run (law 6).
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('combobox',{name:'Action'}).selectOption('sfx');
  await drawer.getByRole('textbox',{name:'Direction name',exact:true}).fill('Anvil strikes');
  await drawer.getByRole('textbox',{name:'Sound description',exact:true}).fill('A hammer on an anvil in a stone forge.');
  await drawer.locator('[data-standing="ready"]').filter({has:page.getByText('Sound effect · ElevenLabs Sound Effects',{exact:true})})
    .getByRole('spinbutton',{name:'Sound effect · ElevenLabs Sound Effects attempts',exact:true}).fill('7');
  await drawer.getByRole('button',{name:'Budget from published tariffs',exact:true}).click();
  await drawer.getByRole('spinbutton',{name:'Spending limit · USD'}).fill('1');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('An attempt whose engine asks to wait or cannot be reached '
    +'is tried again on its own, and the retry shows on that attempt.');
  // the reservation reads exactly, never rounded down to the cent (law 6)
  await drawer.getByRole('button',{name:'Run 7 attempts · reserves $0.028',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  // four hosted attempts run at once and three wait their turn; a rate limit sends one back to wait on the provider's clock
  await expect.poll(count).toEqual({running:4,queued:3});
  const limited=await first(slot=>slot.status==='running');
  await request.post(`/api/harness/slots/${limited.id}/resolve`,{data:{outcome:'fail',kind:'rate_limit',message:'Too many requests',retry_after_s:60}});
  await expect.poll(async()=>(await first(slot=>slot.id===limited.id)).retry_of).toBe('rate_limit');
  await expect.poll(count).toEqual({running:4,queued:3});
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const section=page.getByRole('region',{name:'Anvil strikes',exact:true});
  const waiting=section.getByRole('group',{name:name(await first(slot=>slot.status==='queued'&&!slot.retry_of),'queued'),exact:true});
  await expect(waiting.getByRole('button')).toHaveText(['CANCEL ATTEMPT']);
  await expect(waiting.getByRole('button',{name:'CANCEL ATTEMPT',exact:true})).toHaveAccessibleDescription(SKIPS);
  const retrying=section.getByRole('group',{name:name(limited,'queued'),exact:true});
  await expect(retrying).toContainText('RETRYING · ATTEMPT');                    // law 9: the retry shows while it waits
  await expect(retrying.getByRole('button')).toHaveText(['GIVE UP ON THIS ATTEMPT']);
  const flying=section.getByRole('group',{name:name(await first(slot=>slot.status==='running'),'running'),exact:true});
  await expect(flying.getByRole('button',{name:'CANCEL ATTEMPT',exact:true})).toHaveAccessibleDescription(SENT);
  for(const face of [waiting,retrying,flying])await drawnWhole(face);
  await page.setViewportSize({width:1440,height:900});
  await retrying.scrollIntoViewIfNeeded();
  await page.screenshot({path:artifact('f15-queued-face-1440.png')});
  // giving up during the wait skips it, and nothing more is sent
  await retrying.getByRole('button',{name:'GIVE UP ON THIS ATTEMPT',exact:true}).click();
  await expect.poll(count).toEqual({running:4,queued:2,skipped:1});
  // one failure frees a place for a waiting attempt; the second benches the engine for this run (A9), so the last waits, held
  await request.post(`/api/harness/slots/${(await first(slot=>slot.status==='running')).id}/resolve`,{data:FAIL});
  await expect.poll(count).toEqual({running:4,queued:1,failed:1,skipped:1});
  await request.post(`/api/harness/slots/${(await first(slot=>slot.status==='running')).id}/resolve`,{data:FAIL});
  await expect.poll(count).toEqual({running:3,held:1,failed:2,skipped:1});
  const kept=await first(slot=>slot.status==='held');
  const held=section.getByRole('group',{name:name(kept,'held'),exact:true});
  await expect(held).toContainText('nothing charged yet');
  // law 6: the costed move shows what it sends, sits after the free one, and is never the default
  await expect(held.getByRole('button')).toHaveText(['REUSE SETUP','TRY ONCE MORE · UP TO $0.004','CANCEL ATTEMPT']);
  const tryAgain=held.getByRole('button',{name:'TRY ONCE MORE · UP TO $0.004',exact:true});
  await expect(tryAgain).not.toHaveAttribute('aria-disabled','true');
  await expect(tryAgain).toHaveAccessibleDescription(RESUME);
  await expect(tryAgain).toHaveCSS('color',GOLD);                                  // money about to move, not the primary frame
  await expect(held.getByRole('button',{name:'REUSE SETUP',exact:true})).toHaveCSS('color',TEXT);
  await drawnWhole(held);
  await held.scrollIntoViewIfNeeded();
  await page.screenshot({path:artifact('f15-held-paid-face-1440.png')});
  await tryAgain.click();
  await expect.poll(count).toEqual({running:4,failed:2,skipped:1});
  expect(await movesOf(request,project.id)).toEqual([{slot:limited.id,action:'cancel',actor:ARTIST},{slot:kept.id,action:'resume',actor:ARTIST}]);
  for(const slot of (await slotsNow()).filter(item=>item.status==='running'))
    await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'land'}});
  await expect.poll(count).toEqual({landed:4,failed:2,skipped:1});
});

test("a blind run's held attempt resumes from its face, naming no engine and no price",async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Held attempts need scripted executors.');
  const ops:{id:string;revision:string;provider:string|null;actions:string[];availability:string}[]=await(await request.get('/api/crexil/operations')).json();
  const designer=(provider:string)=>ops.find(op=>op.provider===provider&&op.actions.includes('voice-design')&&op.availability!=='unavailable');
  const local=designer('voice-runner'),hosted=designer('elevenlabs');
  test.skip(!local||!hosted,'A blind run needs two voice designers.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Blind hold ${Date.now()}`}})).json();
  const line='Steel remembers every strike, little one. Mind the anvil, mind the fire, and the iron will mind you back.';
  const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:'Blind voices',setup:{
    action:'voice-design',prompt:'A gruff dwarf blacksmith.',blind:true,spending_policy:'published_tariff',spending_limit_usd:1,attempt_limit:4,
    operations:[{operation_id:local!.id,revision:local!.revision,count:3,controls:{preview_text:line}},
      {operation_id:hosted!.id,revision:hosted!.revision,count:1,controls:{preview_text:line}}]}}})).json();
  const review=await(await request.get(`/api/crexil/directions/${direction.id}/review`)).json();
  expect(review.errors).toEqual([]);
  expect((await request.post(`/api/crexil/directions/${direction.id}/submit`,{data:{...command(),revision:review.revision,review_token:review.token}})).status()).toBe(201);
  const slotsNow=async():Promise<{id:string;pos:number;status:string}[]>=>(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots;
  const states=async()=>(await slotsNow()).map(slot=>slot.status).sort();
  // Every attempt the engines take fails; the local engine's second failure holds its third attempt (A9).
  const mine=new Set((await slotsNow()).map(slot=>slot.id)),failed=new Set<string>();
  await expect.poll(async()=>{
    const {waiting}=await(await request.get('/api/harness/state')).json() as {waiting:string[]};
    for(const id of waiting.filter(item=>mine.has(item)&&!failed.has(item))){failed.add(id);await request.post(`/api/harness/slots/${id}/resolve`,{data:FAIL});}
    return states();
  },{timeout:20_000}).toEqual(['failed','failed','failed','held']);
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const kept=(await slotsNow()).find(slot=>slot.status==='held')!;
  const held=page.getByRole('group',{name:`Attempt 0${String.fromCharCode(65+kept.pos)} · held`,exact:true});
  // one neutral face: its setup first, its resume live, no engine, lane, cause or price
  await expect(held).toContainText('waiting costs nothing');
  await expect(held).not.toContainText('Its engine is held.');
  await expect(held.getByRole('button')).toHaveText(['REUSE SETUP','RESUME ENGINE','CANCEL ATTEMPT']);
  await expect(held).not.toContainText(/qwen|elevenlabs|\$/i);
  const resume=held.getByRole('button',{name:'RESUME ENGINE',exact:true});
  await expect(resume).not.toHaveAttribute('aria-disabled','true');
  await drawnWhole(held);
  await held.scrollIntoViewIfNeeded();
  await page.setViewportSize({width:1440,height:900});
  await page.screenshot({path:artifact('f15-hidden-held-face-1440.png')});
  await resume.click();
  await expect.poll(states).toEqual(['failed','failed','failed','running']);
  const moves=await movesOf(request,project.id);
  expect(moves).toEqual([{slot:kept.id,action:'resume',actor:ARTIST}]);
  const {events}=await(await request.get(`/api/events?workspace_id=${project.id}&limit=500`)).json();
  expect(JSON.stringify(events.filter((event:{type:string})=>event.type==='execution.control'))).not.toMatch(/qwen|elevenlabs/i);
  await request.post(`/api/harness/slots/${kept.id}/resolve`,{data:{outcome:'land'}});
  await expect.poll(states).toEqual(['failed','failed','failed','landed']);
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
