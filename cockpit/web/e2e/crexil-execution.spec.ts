import { test, expect } from '@playwright/test';
import { artifact } from './paths';
const command = () => ({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Browser execution check'}});

test('review preserves invalid controls and navigation saves pending preparation', async ({page,request}) => {
  const name=`Review and return ${Date.now()}`;
  const created=await request.post('/api/crexil/projects',{data:{...command(),name}});
  const project=await created.json();
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('A weathered brass lamp.');
  await drawer.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true}).fill('2');
  const operation=drawer.locator('div').filter({has:page.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true})}).filter({has:page.locator('summary')}).last();
  await operation.locator('summary').click();
  await operation.getByRole('spinbutton',{name:'Steps',exact:true}).fill('40');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  const review=drawer.getByRole('region',{name:'Run review'});
  await expect(review).toContainText('steps must be between 1 and 32');
  await expect(operation.getByRole('spinbutton',{name:'Steps',exact:true})).toHaveValue('40');
  await expect(drawer.getByRole('button',{name:'Run 2 attempts',exact:true})).not.toBeVisible();
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Saved even when I leave immediately.');
  await page.getByRole('link',{name:'Projects',exact:true}).click();
  await expect(page).toHaveURL(/#\/projects$/);
  await page.getByRole('link').filter({has:page.getByRole('heading',{name,exact:true})}).click();
  await page.getByRole('button',{name:'Open preparation: New direction',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Saved even when I leave immediately.');
});

test('shared run freezes slots and shows partial success in their original order', async ({page,request}) => {
  const health=await (await request.get('/api/health')).json();
  test.skip(Boolean(health.preview), 'Isolated preview cannot execute; this journey runs against scripted executors.');
  const name=`Submitted direction ${Date.now()}`;
  const project=await (await request.post('/api/crexil/projects',{data:{...command(),name}})).json();
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('A study in brass and lacquer.');
  await drawer.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true}).fill('2');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('Ready to submit');
  await drawer.getByRole('button',{name:'Run 2 attempts',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  const snapshot=await (await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(snapshot.turns).toHaveLength(1);
  const slots=snapshot.turns[0].slots;
  expect(slots).toHaveLength(2);
  await request.post(`/api/harness/slots/${slots[0].id}/resolve`,{data:{outcome:'fail',kind:'error',message:'Scripted first-slot failure'}});
  await request.post(`/api/harness/slots/${slots[1].id}/resolve`,{data:{outcome:'land',color:[82,130,168]}});
  await expect.poll(async()=> (await (await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots.map((slot:{status:string})=>slot.status)).toEqual(['failed','landed']);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const section=page.getByRole('region',{name:'New direction',exact:true});
  await expect(section.getByText('failed',{exact:true})).toBeVisible();
  await expect(section.getByRole('button',{name:'Act on 0B',exact:true})).toBeVisible();
  const failed=await section.getByText('failed',{exact:true}).boundingBox();
  const landed=await section.getByRole('button',{name:'Act on 0B',exact:true}).boundingBox();
  expect(failed!.x).toBeLessThan(landed!.x);
  await page.getByRole('button',{name:'Strip ›',exact:true}).click();
  const picker=page.getByRole('region',{name:'Choose displayed takes'});
  await picker.getByRole('button',{name:'Display 0B',exact:true}).click();
  await picker.getByRole('button',{name:'Apply',exact:true}).click();
  await page.reload();
  await expect(page.getByRole('button',{name:'Act on 0B',exact:true})).toBeVisible();
});

test('project and run pauses compose before a run cancellation takes effect',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Execution controls use scripted executors for this journey.');
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Scoped controls ${Date.now()}`}})).json();
  await page.goto(`/#/projects/${project.id}`);
  const projectControls=page.getByRole('region',{name:'Project execution',exact:true});
  await projectControls.getByRole('button',{name:'Pause project',exact:true}).click();
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('A small brass instrument.');
  await drawer.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true}).fill('2');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await drawer.getByRole('button',{name:'Run 2 attempts',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  const states=async()=> (await(await request.get(`/api/crexil/projects/${project.id}`)).json()).turns[0].slots.map((slot:{status:string})=>slot.status);
  await expect.poll(states).toEqual(['held','held']);
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  const run=page.getByRole('region',{name:'Run 0 execution',exact:true});
  await run.getByRole('button',{name:'Pause run 0',exact:true}).click();
  await projectControls.getByRole('button',{name:'Resume project',exact:true}).click();
  expect(await states()).toEqual(['held','held']);
  await run.getByRole('button',{name:'Resume run 0',exact:true}).click();
  await expect.poll(states).toEqual(['running','queued']);
  await run.getByRole('button',{name:'Cancel active work',exact:true}).click();
  await expect.poll(states).toEqual(['failed','skipped']);
  await expect(run.getByRole('button',{name:'Cancel active work',exact:true})).toBeDisabled();
  await page.reload();
  await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  await expect(run.getByText('Cancellation requested by Artist',{exact:true})).toBeVisible();
});

test('a lost accepted submission survives polling and reload without another execution',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Execution receipt recovery uses scripted executors.');
  page.on('dialog',dialog=>void dialog.accept());
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Submission receipt ${Date.now()}`}})).json();
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'+ New direction · N',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('A single brass instrument');
  await drawer.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true}).fill('1');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  const bodies:unknown[]=[];let lost=true;
  await page.route('**/api/crexil/directions/*/submit',async route=>{
    bodies.push(route.request().postDataJSON());
    if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(201);await route.abort('failed');}
    else await route.continue();
  });
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await drawer.getByRole('button',{name:'Run 1 attempts',exact:true}).click();
  await expect(drawer.getByRole('button',{name:'Check submission',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Reuse setup: New direction',exact:true})).toBeVisible();
  const before=await(await request.get(`/api/crexil/projects/${project.id}`)).json();expect(before.turns).toHaveLength(1);
  await request.post(`/api/harness/slots/${before.turns[0].slots[0].id}/resolve`,{data:{outcome:'land',color:[82,130,168]}});
  await page.reload();await page.getByRole('button',{name:'Recover New direction',exact:true}).click();
  await drawer.getByRole('button',{name:'Check submission',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  const after=await(await request.get(`/api/crexil/projects/${project.id}`)).json();expect(after.turns).toHaveLength(1);
  expect(after.turns[0].id).toBe(before.turns[0].id);expect(after.turns[0].slots).toHaveLength(1);
  expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
});

test('missing related files recover through the same attempt after a lost collection response',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'Collection recovery uses the isolated scripted runtime.');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Related-file recovery ${Date.now()}`}})).json();
  const operations=await(await request.get('/api/crexil/operations')).json();
  const operation=operations.find((o:{id:string})=>o.id==='raw-flux2');
  const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:'Recover existing layer files',setup:{
    prompt:'Scripted layers for collection verification',attempt_limit:1,operations:[{operation_id:operation.id,revision:operation.revision,count:1,controls:{}}],
  }}})).json();
  const review=await(await request.get(`/api/crexil/directions/${direction.id}/review`)).json();
  const accepted=await request.post(`/api/crexil/directions/${direction.id}/submit`,{data:{...command(),revision:review.revision,review_token:review.token}});
  expect(accepted.status()).toBe(201);
  const snapshot=async()=> (await(await request.get(`/api/crexil/projects/${project.id}`)).json());
  const initial=await snapshot();const slot=initial.turns[0].slots[0];
  const outputs=[{identity:'body',filename:'body.png',role:'body layer'},
    {identity:'coat',filename:'coat.png',role:'coat layer',transparent:true},
    {identity:'mask',filename:'mask.png',role:'mask',missing:true}];
  await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:{outcome:'fail',kind:'collection',message:'The mask download failed; two layers were kept.',outputs}});
  await expect.poll(async()=> (await snapshot()).turns[0].slots[0].status).toBe('failed');
  const before=await snapshot();const group=before.execution_groups[0];
  expect(group.outputs.filter((o:{available:boolean})=>o.available)).toHaveLength(2);
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Grid ›',exact:true}).click();
  await page.getByRole('button',{name:'3 files ›',exact:true}).click();
  const room=page.getByRole('region',{name:'Related outputs',exact:true});
  await expect(room).toContainText('Scripted download unavailable.');await expect(room.getByRole('link',{name:'Download original',exact:true})).toHaveCount(2);
  const coat=group.outputs.find((o:{identity:string})=>o.identity==='coat');
  expect(await page.evaluate(async id=>{const blob=await(await fetch(`/api/crexil/outputs/${id}/file`)).blob();const bitmap=await createImageBitmap(blob);
    const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;const ctx=canvas.getContext('2d')!;ctx.drawImage(bitmap,0,0);
    return [ctx.getImageData(0,0,1,1).data[3],ctx.getImageData(32,32,1,1).data[3]];},coat.id)).toEqual([0,190]);
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:artifact(`collection-incomplete-${width}-scripted.png`)});}
  await request.post(`/api/harness/slots/${slot.id}/collection`,{data:{kind:'landed',outputs:outputs.map(output=>({...output,missing:false}))}});
  let lost=true;
  await page.route(`**/api/crexil/attempts/${slot.id}/collect`,async route=>{
    if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(200);await route.abort('failed');}else await route.continue();
  });
  await room.getByRole('button',{name:'Collect existing files again',exact:true}).click();await expect(room.getByRole('alert')).toBeVisible();
  await page.reload();await page.getByRole('button',{name:'Grid ›',exact:true}).click();await page.getByRole('button',{name:'3 files ›',exact:true}).click();
  await expect(room).toContainText('reported files were collected');await expect(room.getByRole('link',{name:'Download original',exact:true})).toHaveCount(3);
  await room.getByRole('button',{name:'Collect existing files again',exact:true}).click();
  await expect(room.getByRole('button',{name:'Collect existing files again',exact:true})).toBeEnabled();
  const after=await snapshot(),recovered=after.execution_groups[0];
  expect(after.turns).toHaveLength(1);expect(after.turns[0].slots[0].attempt).toBe(1);expect(after.turns[0].slots[0].status).toBe('landed');
  expect(after.charged_usd).toBe(before.charged_usd);expect(recovered.id).toBe(group.id);
  expect(recovered.outputs.map((o:{id:string})=>o.id)).toEqual(group.outputs.map((o:{id:string})=>o.id));
  for(const output of group.outputs.filter((o:{available:boolean})=>o.available))expect(recovered.outputs.find((o:{id:string})=>o.id===output.id).sha256).toBe(output.sha256);
  const runtime=await(await request.get('/api/harness/state')).json();expect(runtime.starts[slot.id]).toBe(1);expect(runtime.retrievals[slot.id]).toBe(2);
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});await page.screenshot({path:artifact(`collection-recovered-${width}-scripted.png`)});}
  await room.getByRole('button',{name:'Esc · Back',exact:true}).click();await expect(page.getByRole('button',{name:'Act on 0A',exact:true})).toBeVisible();
  expect(errors).toEqual([]);
  await (await import('node:fs/promises')).writeFile(artifact('collection-browser-verification.json'),JSON.stringify({
    project:project.id,before:group,after:recovered,slot:after.turns[0].slots[0],runtime,errors,
    alpha_samples:[0,190],charged_before:before.charged_usd,charged_after:after.charged_usd,
  },null,2));
});
