// Blind voice casting on the scripted executors: an audio run is prepared blind in the
// drawer, runs, is auditioned from the keyboard, picked with a reason, revealed, and read
// back in the record and its export. Every voice-design engine the harness offers takes
// part with one attempt; when only the local engine is ready it takes both attempts, so the
// grid holds the same six takes either way.
import {test,expect,type APIRequestContext,type Page} from '@playwright/test';
import {artifact} from './paths';

const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Audio journey'}});
// 105 characters: ElevenLabs prices a preview line by its characters, 80 micro-dollars each.
const LINE='Steel remembers every strike, little one. Mind the anvil, mind the fire, and the iron will mind you back.';
type Op={id:string;label:string;actions:string[];availability:string;output_payload:string;provider:string|null;maximum_usd_per_attempt:number|null};

async function shots(page:Page,name:string){
  for(const [width,height] of [[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});
    await page.screenshot({path:artifact(`${name}-${width}.png`)});
  }
  await page.setViewportSize({width:1440,height:900});
}

async function voiceDesigners(request:APIRequestContext):Promise<Op[]>{
  const ops:Op[]=await(await request.get('/api/crexil/operations')).json();
  return ops.filter(op=>op.output_payload==='audio'&&op.actions.includes('voice-design')&&op.availability!=='unavailable');
}

test('a blind voice casting runs end to end: audition from the keyboard, pick with a reason, reveal, record and export',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run audio; this journey uses the scripted executors.');
  const designers=await voiceDesigners(request);
  expect(designers.length).toBeGreaterThan(0);
  const chosen=designers.length>1?designers.slice(0,2).map(op=>({op,count:1})):[{op:designers[0],count:2}];
  const paid=chosen.some(({op})=>op.maximum_usd_per_attempt===null);
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Voice casting ${Date.now()}`}})).json();
  const snapshot=async()=>(await request.get(`/api/crexil/projects/${project.id}`)).json();

  // Prepare: the action's own words, a blind run, what leaves the machine, the price on Run.
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).first().click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('combobox',{name:'Action'}).selectOption('voice-design');
  await drawer.getByRole('textbox',{name:'Direction name',exact:true}).fill('Blacksmith voice');
  await drawer.getByRole('textbox',{name:'Voice description',exact:true}).fill('A gruff dwarf blacksmith, warm and low, unhurried.');
  await drawer.getByRole('checkbox',{name:'Blind run'}).check();
  await expect(drawer).toContainText('Engines, prices and level figures stay hidden until you reveal the run.');
  for(const {op,count} of chosen){
    const row=drawer.locator('[data-standing="ready"]').filter({has:page.getByText(op.label,{exact:true})});
    await row.getByRole('spinbutton',{name:`${op.label} attempts`}).fill(String(count));
    await expect(row.getByText(/^Sends (off this machine to|to) .+: the voice description and its preview line\.$/)).toBeVisible();
    await row.getByText('Inputs, cost and route').click();
    await row.getByRole('textbox',{name:'Preview line'}).fill(LINE);
  }
  if(paid){
    // a tariff has no hard ceiling: the drawer offers the budget that runs it (law 22)
    const offer=drawer.getByText(/is priced by its published tariff, with no hard ceiling\. A hard cap refuses it before anything is sent\.$/);
    await offer.scrollIntoViewIfNeeded();
    await shots(page,'d7-drawer-tariff-offer');
    await drawer.getByRole('button',{name:'Budget from published tariffs'}).click();
    await drawer.getByRole('spinbutton',{name:'Spending limit · USD'}).fill('1');
  }
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.locator('[data-standing="ready"]').first().evaluate(row=>row.scrollIntoView({block:'start'}));
  await shots(page,'d7-drawer-audio');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  const review=drawer.getByRole('region',{name:'Run review'});
  await expect(review).toContainText('Ready to submit');
  await expect(review).toContainText("Blind run: each take's engine and price stay hidden until you reveal the run.");
  // law 6: the reviewed price sits on the button that spends it; a sub-cent amount shows exactly (law 5)
  await drawer.getByRole('button',{name:paid?'Run 2 attempts · reserves $0.0084':'Run 2 attempts · free',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  const slots=(await snapshot()).turns[0].slots;
  expect(slots).toHaveLength(2);
  expect(slots.every((slot:{engine:string;identity_hidden:boolean})=>slot.engine==='hidden'&&slot.identity_hidden)).toBe(true);

  // In flight, every cell of the blind run wears one neutral face: landing order never tells engines apart.
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const section=page.getByRole('region',{name:'Blacksmith voice',exact:true});
  await expect(section.getByRole('group',{name:'Attempt 0A · in progress',exact:true})).toBeVisible();
  await expect(section.getByRole('group',{name:'Attempt 0B · in progress',exact:true})).toBeVisible();
  const flying=await section.getByRole('group',{name:'Attempt 0A · in progress',exact:true}).boundingBox();
  await request.post(`/api/harness/slots/${slots[0].id}/resolve`,{data:{outcome:'land'}});
  await expect(section.getByRole('group',{name:'Attempt 0A · in progress',exact:true})).toContainText('1 of 2 attempts done.');
  await expect(section.getByRole('button',{name:/^Act on /})).toHaveCount(0);
  await shots(page,'d7-grid-blind-in-flight');
  await request.post(`/api/harness/slots/${slots[1].id}/resolve`,{data:{outcome:'land'}});
  await expect(section.getByRole('button',{name:/^Act on /})).toHaveCount(6);
  // law 8: the slot held room for its three takes from Run, so landing moved nothing
  const landed=await section.getByRole('button',{name:'Act on 0A, audio, 4.0 s',exact:true}).boundingBox();
  expect(Math.round(landed!.x)).toBe(Math.round(flying!.x));
  for(const name of ['0A','0A.2','0A.3','0B','0B.2','0B.3'])
    await expect(section.getByRole('button',{name:`Act on ${name}, audio, 4.0 s`,exact:true})).toContainText(`${name} · Blind`);
  const line=page.getByText('Space play · 1–9 audition · X A/B · S select',{exact:true});
  await expect(line).toBeVisible();

  // Audition from the keyboard: digit 4 is the fourth position, at a matched level.
  const status=page.locator('[role=status]').filter({hasText:/^(Playing|Paused) /});
  await page.keyboard.press('4');
  await expect(status).toHaveText('Playing 0B · 4.0 s · level matched');
  const fourth=section.getByRole('button',{name:'Act on 0B, audio, 4.0 s',exact:true});
  await expect(fourth).toBeFocused();
  await page.keyboard.press('Space');
  await expect(status).toHaveText('Paused 0B · 4.0 s · level matched');
  // Space on a focused control is that control's own: it opens the files and plays nothing (WCAG 2.1.1)
  await section.getByRole('button',{name:'3 files ›',exact:true}).first().focus();
  await page.keyboard.press('Space');
  const files=page.getByRole('region',{name:'Related outputs',exact:true});
  await expect(files).toBeVisible();
  await expect(status).toHaveText('Paused 0B · 4.0 s · level matched');
  await page.keyboard.press('Escape');
  await expect(files).not.toBeVisible();
  await page.keyboard.press('4');
  await expect(status).toHaveText('Playing 0B · 4.0 s · level matched');
  await shots(page,'d7-grid-blind-settled');

  // One key picks the take just heard; the reason that follows is the choice's own (law 23).
  await fourth.focus();
  await page.keyboard.press('s');
  const reason=page.getByRole('textbox',{name:'Why 0B? Optional',exact:true});
  await expect(reason).toBeFocused();
  await expect(page.getByText('Selected 0B',{exact:true})).toBeVisible();
  await reason.fill('Warmest of the six; the grit sits under the words.');
  await page.keyboard.press('Enter');
  await expect(page.getByText('Reason kept with 0B.',{exact:true})).toBeVisible();
  await expect(fourth).toBeFocused();
  const picked=(await snapshot()).directions.find((d:{title:string})=>d.title==='Blacksmith voice');
  const chosenTake=(await snapshot()).nodes.find((n:{slot_pos:number;output_index:number})=>n.slot_pos===1&&n.output_index===0);
  expect(picked.selection).toEqual([chosenTake.id]);

  // The reveal follows the choice: in line, with an optional reason, authored (law 23).
  const blind=page.getByRole('group',{name:'Blind run 0',exact:true});
  await blind.getByRole('textbox',{name:'Reason for revealing run 0, optional'}).fill('Chosen blind; now the engines.');
  await blind.getByRole('button',{name:'Reveal engines',exact:true}).click();
  await expect(page.getByText('Run 0 revealed by Artist',{exact:true})).toBeVisible();
  await expect(fourth).not.toContainText('Blind');
  const revealed=(await snapshot()).blind_runs[0];
  expect(revealed).toMatchObject({revealed:true,revealed_by:{kind:'human',name:'Artist'}});
  await shots(page,'d7-grid-revealed');

  // Inspection shows the take's measured facts (law 17).
  await fourth.dblclick();
  const inspection=page.getByRole('region',{name:'Inspect material',exact:true});
  await expect(inspection.getByRole('region',{name:'Transcript'})).toContainText(LINE);
  await expect(inspection).toContainText('Length');
  await expect(inspection).toContainText('LUFS');
  await shots(page,'d7-inspect-audio');
  await page.keyboard.press('Escape');
  await expect(inspection).not.toBeVisible();

  // The record keeps the pick, its reason and its author; the export is one step and holds no audio.
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Record ›',exact:true}).click();
  const room=page.getByRole('region',{name:'Project record',exact:true});
  await expect(room.getByText('Warmest of the six; the grit sits under the words.',{exact:true})).toBeVisible();
  await room.getByRole('button',{name:'Runs',exact:true}).click();
  await expect(room.getByText(/^Blind run · revealed by Artist · /)).toBeVisible();
  await room.getByRole('button',{name:'Decisions',exact:true}).click();
  await shots(page,'d7-record');
  const href=await room.getByRole('link',{name:'Export decision record',exact:true}).getAttribute('href');
  expect(href).toBe(`/api/crexil/projects/${project.id}/decisions.jsonl`);
  const lines=(await(await request.get(href!)).text()).trim().split('\n').map(text=>JSON.parse(text));
  const selection=lines.find(record=>record.kind==='selection');
  expect(selection).toMatchObject({blind:true,chosen:[chosenTake.id],author:{kind:'human',name:'Artist'}});
  expect(selection.candidates).toHaveLength(6);
  // the reason travels with the choice itself, never as a separate assessment
  expect(lines.find(record=>record.kind==='selection'&&record.reason==='Warmest of the six; the grit sits under the words.')).toBeDefined();
  expect(lines.some(record=>record.kind==='assessment')).toBe(false);
  expect(JSON.stringify(lines)).not.toMatch(/base64|\.wav|\/audio/);
});

test('an ElevenLabs take offered to the local engine stays visible, de-emphasised, with its terms (law 12)',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run audio.');
  const designers=await voiceDesigners(request);
  const eleven=designers.find(op=>op.provider==='elevenlabs');
  test.skip(!eleven,'The harness offers no ElevenLabs voice design without its placeholder key.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Voice terms ${Date.now()}`}})).json();
  const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:'ElevenLabs voice',setup:{
    action:'voice-design',prompt:'A bright young apprentice.',spending_policy:'published_tariff',spending_limit_usd:1,attempt_limit:1,
    operations:[{operation_id:eleven!.id,revision:(await(await request.get('/api/crexil/operations')).json()).find((op:Op)=>op.id===eleven!.id).revision,count:1,controls:{preview_text:LINE}}],
  }}})).json();
  const review=await(await request.get(`/api/crexil/directions/${direction.id}/review`)).json();
  expect((await request.post(`/api/crexil/directions/${direction.id}/submit`,{data:{...command(),revision:review.revision,review_token:review.token}})).status()).toBe(201);
  const slot=(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots[0];
  await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'land'}});
  await expect.poll(async()=>(await(await request.get(`/api/crexil/projects/${project.id}`)).json()).nodes.length).toBe(3);
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  await page.getByRole('button',{name:/^Act on 0A, audio/}).click();
  const card=page.getByRole('region',{name:'Actions on material',exact:true});
  await card.getByRole('button',{name:'Speak with this voice · L',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const local=drawer.locator('[data-standing="blocked"]').filter({has:page.getByText('Speak · Qwen3-TTS 1.7B',{exact:true})});
  await expect(local).toContainText("ElevenLabs' terms do not allow its audio as input to another AI model.");
  await expect(drawer.locator('[data-standing="ready"]').filter({has:page.getByText('Speak · ElevenLabs v4',{exact:true})})).toBeVisible();
});

type Snap={turns:{id:string;idx:number;slots:{id:string;committed_usd:number|null}[]}[];nodes:{id:string;turn_id:string|null}[]};

/** Prepares, reviews and sends one run on a row through the API, as an agent would. */
async function sent(request:APIRequestContext,project:string,row:number,title:string,setup:Record<string,unknown>,
  choices:[Op,number,Record<string,unknown>?][]){
  const ops:{id:string;revision:string}[]=await(await request.get('/api/crexil/operations')).json();
  const direction=await(await request.post(`/api/crexil/projects/${project}/directions`,{data:{...command(),title,row_idx:row,setup:{
    spending_policy:'published_tariff',spending_limit_usd:1,attempt_limit:16,...setup,
    operations:choices.map(([op,count,controls={}])=>({operation_id:op.id,revision:ops.find(item=>item.id===op.id)!.revision,count,controls}))}}})).json();
  const review=await(await request.get(`/api/crexil/directions/${direction.id}/review`)).json();
  expect(review.ready,JSON.stringify(review.errors)).toBe(true);
  expect((await request.post(`/api/crexil/directions/${direction.id}/submit`,{data:{...command(),revision:review.revision,review_token:review.token}})).status()).toBe(201);
  const snap:Snap=await(await request.get(`/api/crexil/projects/${project}`)).json();
  return snap.turns[snap.turns.length-1];
}

/** Lands every attempt of a run, charged what it reserved, and waits for its takes. */
async function landed(request:APIRequestContext,project:string,turn:Snap['turns'][number],takes:number){
  for(const slot of turn.slots)
    await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'land',charged_usd:slot.committed_usd||null}});
  await expect.poll(async()=>((await(await request.get(`/api/crexil/projects/${project}`)).json()) as Snap).nodes
    .filter(node=>node.turn_id===turn.id).length).toBe(takes);
}

test('digits audition the voices of a row whose images come first, and stay in the run that holds focus',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run audio; this check uses the scripted executors.');
  const ops:Op[]=await(await request.get('/api/crexil/operations')).json();
  const image=ops.find(op=>op.id==='raw-flux2')!;
  const local=ops.find(op=>op.id==='audio:voice-design:qwen3-tts')!;
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Mixed row ${Date.now()}`}})).json();
  // The live seed's layout: the images first on the row, then the voices.
  await landed(request,project.id,await sent(request,project.id,0,'Concept',{action:'generate',prompt:'A dwarf blacksmith',
    spending_policy:'hard_cap',spending_limit_usd:0},[[image,2]]),2);
  for(const title of ['Voice','Second voice'])
    await landed(request,project.id,await sent(request,project.id,0,title,{action:'voice-design',prompt:'A gruff dwarf blacksmith.',
      spending_policy:'hard_cap',spending_limit_usd:0},[[local,1,{preview_text:LINE}]]),3);
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const status=page.locator('[role=status]').filter({hasText:/^(Playing|Paused|Played) /});
  const face=(name:string)=>page.getByRole('button',{name:`Act on ${name}, audio, 4.0 s`,exact:true});
  // The room's audio runs as it draws them, each with its takes' labels in grid order. The server
  // keeps a row's sections in the order they were made, whatever the machine's clock says, and
  // the digits follow the room.
  const voices=await page.locator('[data-run]').evaluateAll(cells=>{
    const runs=new Map<string,string[]>();
    for(const cell of cells)for(const take of cell.querySelectorAll('[data-take][data-audio-face]'))
      runs.set(cell.getAttribute('data-run')!,[...(runs.get(cell.getAttribute('data-run')!)??[]),take.getAttribute('aria-label')!.split(',')[0].replace('Act on ','')]);
    return [...runs.values()];
  });
  expect(voices).toHaveLength(2);
  const [first,second]=voices;

  // With no take focused, 1 plays the room's first audio run, never an image.
  await page.evaluate(()=>(document.activeElement as HTMLElement|null)?.blur());
  await page.keyboard.press('1');
  await expect(status).toHaveText(`Playing ${first[0]} · 4.0 s · level matched`);
  await expect(face(first[0])).toBeFocused();
  await page.keyboard.press('3');
  await expect(status).toHaveText(`Playing ${first[2]} · 4.0 s · level matched`);
  // The digits number the run that holds focus.
  await face(second[1]).focus();
  await page.keyboard.press('1');
  await expect(status).toHaveText(`Playing ${second[0]} · 4.0 s · level matched`);
  await expect(face(second[0])).toBeFocused();
  // An image's run holds no audio, so the digits play the first audio run's takes.
  await page.getByRole('button',{name:'Act on 0B',exact:true}).focus();
  await page.keyboard.press('2');
  await expect(status).toHaveText(`Playing ${first[1]} · 4.0 s · level matched`);
  await shots(page,'f18-grid-digits-mixed-row');
  // A take played to its end reads as played, never paused.
  await expect(status).toHaveText(`Played ${first[1]} · 4.0 s · level matched`,{timeout:10_000});
});

test('an attempt that returns three takes is charged once: its other takes read INCLUDED, and inspection says so',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run audio.');
  const eleven=(await voiceDesigners(request)).find(op=>op.provider==='elevenlabs');
  test.skip(!eleven,'The harness offers no ElevenLabs voice design without its placeholder key.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`One charge ${Date.now()}`}})).json();
  await landed(request,project.id,await sent(request,project.id,0,'Voice',{action:'voice-design',prompt:'A gruff dwarf blacksmith.'},
    [[eleven!,1,{preview_text:LINE}]]),3);
  await page.goto(`/#/projects/${project.id}`);
  // The corner says whose each total is (one Voice Design call: 105 characters at $0.08 per 1,000).
  await expect(page.getByText(/^All projects today \$[\d.]+ · This project \$0\.0084$/)).toBeVisible();
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const money=(name:string)=>page.getByRole('button',{name:`Act on ${name}, audio, 4.0 s`,exact:true}).locator('footer > span').last();
  await expect(money('0A')).toHaveText('$0.0084');
  await expect(money('0A.2')).toHaveText('Included');
  await expect(money('0A.3')).toHaveText('Included');
  await shots(page,'f18-grid-one-charge');
  await page.getByRole('button',{name:'Act on 0A.2, audio, 4.0 s',exact:true}).dblclick();
  const inspection=page.getByRole('region',{name:'Inspect material',exact:true});
  // Inspection opened from the grid room says where Escape returns.
  await expect(inspection.getByRole('button',{name:'Esc · Grid',exact:true})).toBeFocused();
  await expect(inspection.getByText('Confirmed charge',{exact:true}).locator('xpath=following-sibling::dd[1]'))
    .toHaveText("$0.0084 for this attempt's 3 takes");
  await page.keyboard.press('Escape');
  await expect(inspection).not.toBeVisible();
  await expect(page.getByText(/^Grid · Turn 0$/)).toBeVisible();
});

test('a local voice offered to ElevenLabs v4 is marked before it is chosen, with its reason (law 12)',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run audio.');
  const ops:Op[]=await(await request.get('/api/crexil/operations')).json();
  const local=ops.find(op=>op.id==='audio:voice-design:qwen3-tts')!;
  test.skip(!ops.some(op=>op.id==='audio:speak:elevenlabs-v4'&&op.availability!=='unavailable'),'ElevenLabs v4 needs its placeholder key.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Voice rule ${Date.now()}`}})).json();
  await landed(request,project.id,await sent(request,project.id,0,'Voice',{action:'voice-design',prompt:'A gruff dwarf blacksmith.',
    spending_policy:'hard_cap',spending_limit_usd:0},[[local,1,{preview_text:LINE}]]),3);
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  await page.getByRole('button',{name:/^Act on 0A, audio/}).click();
  await page.getByRole('region',{name:'Actions on material',exact:true}).getByRole('button',{name:'Speak with this voice · L',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const v4=drawer.locator('[data-standing]').filter({has:page.getByText('Speak · ElevenLabs v4',{exact:true})});
  await expect(v4).toHaveAttribute('data-standing','blocked');
  await expect(v4).toContainText('ElevenLabs v4 speaks only with voices designed or saved at ElevenLabs.');
  const qwen=drawer.locator('[data-standing]').filter({has:page.getByText('Speak · Qwen3-TTS 1.7B',{exact:true})});
  await expect(qwen).toHaveAttribute('data-standing','ready');
  // Counts agree in number.
  await qwen.getByRole('spinbutton',{name:'Speak · Qwen3-TTS 1.7B attempts'}).fill('1');
  await expect(drawer.getByText('1 planned attempt · one direction',{exact:true})).toBeVisible();
  await v4.scrollIntoViewIfNeeded();
  await shots(page,'f18-drawer-voice-rule');
});
