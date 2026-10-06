import {test,expect,type APIRequestContext,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Sequence browser verification'}});

async function preparation(request:APIRequestContext){
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Sequence study ${Date.now()}`}})).json();
  const imported=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Study source.png',data_base64:readFileSync(source).toString('base64')}})).json();
  const directions=[];
  for(const [index,title] of ['Prepare source','Finish chosen study'].entries()){
    const response=await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title,setup:{action:'prepare',references:index?[]:[{node_id:imported.node.id}],image_preparation:{mode:'resize',width:index?128:256,height:index?128:256,resample:'nearest',items:index?[]:[{node_id:imported.node.id}]}}}});
    expect(response.ok()).toBeTruthy();directions.push(await response.json());
  }
  return {project,directions};
}

async function describe(page:Page,projectId:string,directions:{id:string}[]){
  await page.goto(`/#/projects/${projectId}`);
  await page.getByRole('button',{name:/^Sequences/}).click();
  const room=page.getByRole('region',{name:'Sequences',exact:true});
  await room.getByLabel('Sequence name',{exact:true}).fill('Prepare, choose, finish');
  await room.getByLabel('Instructions for the next participant',{exact:true}).fill('Retain the brass silhouette.');
  for(const direction of directions){await room.getByLabel('Saved preparation',{exact:true}).selectOption(direction.id);await room.getByRole('button',{name:'Add step',exact:true}).click();}
  const last=room.getByRole('region',{name:'Planned step 2',exact:true});
  await last.getByRole('checkbox',{name:'Prepare source',exact:true}).check();
  await last.getByLabel('Material rule for Prepare source',{exact:true}).selectOption('chosen');
  await last.getByLabel('Use these sources',{exact:true}).selectOption('each');
  return room;
}

test('artist creates a sequence, supplies an attributed choice and returns after reload',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const {project,directions}=await preparation(request);
  const room=await describe(page,project.id,directions);
  await room.getByRole('button',{name:'Save sequence',exact:true}).click();
  await expect(room.getByRole('heading',{name:'Prepare, choose, finish',exact:true})).toBeVisible();
  await room.getByRole('button',{name:'Edit preparation',exact:true}).last().click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await expect(drawer).toContainText('This is a sequence preparation.');
  await expect(room.getByRole('button',{name:'Edit preparation',exact:true}).first()).toBeDisabled();
  await drawer.getByLabel('Result width',{exact:true}).fill('129');
  await expect(drawer.getByRole('button',{name:'Save derived image',exact:true})).not.toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).not.toBeVisible();await expect(room).toBeVisible();
  const saved=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(saved.directions.find((item:{id:string})=>item.id===directions[1].id).setup.image_preparation.width).toBe(129);
  await room.getByRole('button',{name:'Review remaining work',exact:true}).click();
  await room.getByRole('button',{name:'Start sequence',exact:true}).click();
  const child=room.getByRole('region',{name:'Step 2: Finish chosen study',exact:true});
  await expect(child.getByRole('button',{name:/^Choose /})).toHaveCount(1);
  const refreshed=child.getByRole('button',{name:'Review updated choices',exact:true});
  if(await refreshed.isVisible())await refreshed.click();
  await child.getByRole('button',{name:/^Choose /}).click();
  await child.getByLabel('Reason for this choice',{exact:true}).fill('The silhouette is clear at this scale.');
  for(const width of [1440,1920]){
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:`../../.preview/screenshots/sequence-decision-${width}.png`});
  }
  await child.getByRole('button',{name:'Record decision',exact:true}).click();
  await expect(child).toContainText('Chosen by Artist: The silhouette is clear at this scale.');
  await expect.poll(async()=>{const view=await(await request.get(`/api/crexil/projects/${project.id}`)).json();return view.sequences[0].steps.map((step:{state:string})=>step.state);}).toEqual(['complete','complete']);
  const view=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(view.nodes).toHaveLength(3);expect(view.turns).toHaveLength(0);
  expect(view.sequences[0].accounting.attempts).toBe(0);
  await page.reload();await page.getByRole('button',{name:/^Sequences/}).click();
  await expect(child).toContainText('Chosen by Artist: The silhouette is clear at this scale.');
  await expect(room.getByRole('button',{name:'Review remaining work',exact:true})).not.toBeVisible();
  await room.getByRole('button',{name:/^Inspect /}).last().click();
  await expect(page.getByRole('region',{name:'Inspect material',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await expect(room).toBeVisible();
  await page.keyboard.press('Escape');await expect(room).not.toBeVisible();
  await expect(page.getByRole('button',{name:/^Sequences/})).toBeFocused();
  expect(errors).toEqual([]);
});

test('invalid dependency can be corrected and uncertain creation retries one immutable command',async({page,request})=>{
  const {project,directions}=await preparation(request);
  const room=await describe(page,project.id,directions);
  const first=room.getByRole('region',{name:'Planned step 1',exact:true});
  await first.getByRole('checkbox',{name:'Finish chosen study',exact:true}).check();
  await room.getByRole('button',{name:'Save sequence',exact:true}).click();
  await expect(room.getByRole('alert')).toContainText('loop');
  await expect(room.getByLabel('Sequence name',{exact:true})).toHaveValue('Prepare, choose, finish');
  await first.getByRole('checkbox',{name:'Finish chosen study',exact:true}).uncheck();
  const bodies:unknown[]=[];
  await page.route(`**/api/crexil/projects/${project.id}/sequences`,async route=>{
    if(route.request().method()!=='POST'){await route.continue();return;}
    bodies.push(route.request().postDataJSON());
    if(bodies.length===1){const response=await route.fetch();expect(response.ok()).toBeTruthy();await route.abort('failed');}
    else await route.continue();
  });
  await room.getByRole('button',{name:'Save sequence',exact:true}).click();
  await expect(room.getByRole('button',{name:'Retry same request',exact:true})).toBeVisible();
  await expect(room.getByLabel('Sequence name',{exact:true})).toBeDisabled();
  await page.keyboard.press('Escape');await expect(room).toBeVisible();
  await room.getByRole('button',{name:'Retry same request',exact:true}).click();
  await expect(room.getByRole('heading',{name:'Prepare, choose, finish',exact:true})).toBeVisible();
  expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
  const view=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(view.sequences).toHaveLength(1);expect(view.turns).toHaveLength(0);
  const controls:unknown[]=[];
  await page.route(`**/api/crexil/execution/sequence/${view.sequences[0].id}`,async route=>{
    controls.push(route.request().postDataJSON());
    if(controls.length===1){const response=await route.fetch();expect(response.ok()).toBeTruthy();await route.abort('failed');}
    else await route.continue();
  });
  await room.getByRole('region',{name:'Sequence execution',exact:true}).getByRole('button',{name:'Cancel active work',exact:true}).click();
  await expect(room.getByRole('button',{name:'Retry same request',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await expect(room).not.toBeVisible();
  await page.getByRole('button',{name:/^Sequences/}).click();
  await room.getByRole('button',{name:'Retry same request',exact:true}).click();
  expect(controls).toHaveLength(2);expect(controls[0]).toEqual(controls[1]);
  await expect.poll(async()=> (await(await request.get(`/api/crexil/projects/${project.id}`)).json()).sequences[0].state).toBe('cancelled');
  await page.reload();await page.getByRole('button',{name:/^Sequences/}).click();
  await expect(room.getByRole('button',{name:'Review remaining work',exact:true})).not.toBeVisible();
});

test('successful upstream outputs advance separately while a failed sibling remains recorded',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Engine dispatch is verified against the isolated scripted harness.');
  const {project,directions}=await preparation(request);
  const operations=await(await request.get('/api/crexil/operations')).json();
  const operation=operations.find((item:{id:string})=>item.id==='raw-flux2');
  const root=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:'Explore variations',setup:{action:'generate',prompt:'A brass instrument.',operations:[{operation_id:operation.id,revision:operation.revision,count:2,controls:{}}]}}})).json();
  const planned=await(await request.post(`/api/crexil/projects/${project.id}/sequences`,{data:{...command(),title:'Explore and finish',steps:[{key:'explore',direction_id:root.id,revision:root.revision},{key:'finish',direction_id:directions[1].id,revision:directions[1].revision,input_mode:'each',dependencies:[{step_key:'explore',selection:'all_successful'}]}]}})).json();
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:/^Sequences/}).click();
  const room=page.getByRole('region',{name:'Sequences',exact:true});
  await room.getByRole('button',{name:'Review remaining work',exact:true}).click();await room.getByRole('button',{name:'Start sequence',exact:true}).click();
  const state=async()=>await(await request.get(`/api/crexil/sequences/${planned.id}`)).json();
  await expect.poll(async()=> (await state()).steps[0].runs.length).toBe(1);
  const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();const slots=snapshot.turns[0].slots;
  await request.post(`/api/harness/slots/${slots[0].id}/resolve`,{data:{outcome:'fail',kind:'error',message:'Scripted sequence source failure'}});
  await request.post(`/api/harness/slots/${slots[1].id}/resolve`,{data:{outcome:'land',color:[106,143,173]}});
  await expect.poll(async()=> (await state()).steps.map((step:{state:string})=>step.state)).toEqual(['complete','complete']);
  expect((await state()).steps[1].runs).toHaveLength(1);
  expect((await state()).accounting.attempts).toBe(2);
  await room.getByRole('button',{name:'Open run',exact:true}).click();
  const run=page.getByRole('region',{name:'Explore variations · 1',exact:true});
  await expect(run.getByText('failed',{exact:true})).toBeVisible();
  await expect(run.getByRole('button',{name:'Act on 0B',exact:true})).toBeVisible();
  const finished=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(finished.turns[0].slots.map((slot:{status:string})=>slot.status)).toEqual(['failed','landed']);
  await page.getByRole('button',{name:'Esc · Sequence',exact:true}).click();
  await expect(room.getByRole('heading',{name:'Explore and finish',exact:true})).toBeVisible();
});
