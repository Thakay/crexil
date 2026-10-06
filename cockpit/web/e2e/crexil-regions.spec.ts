import {test,expect,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Region browser verification'}});
async function maskPixels(page:Page,id:string){
 return page.evaluate(async id=>{
  const image=new Image();image.src=`/api/crexil/masks/${id}/image`;await image.decode();
  const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
  const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);
  return {center:ctx.getImageData(Math.floor(canvas.width*.5),Math.floor(canvas.height*.5),1,1).data[0],edge:ctx.getImageData(Math.floor(canvas.width*.3),Math.floor(canvas.height*.3),1,1).data[0]};
 },id);
}
test('artist draws, reloads, erases and reviews a source-bound region',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Region drawing ${Date.now()}`}})).json();
 const material=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Region source.png',data_base64:readFileSync(source).toString('base64')}})).json();
 await request.put(`/api/crexil/directions/${material.direction.id}/selection`,{data:{...command(),revision:0,node_ids:[material.node.id]}});
 await page.goto(`/#/projects/${project.id}`);
 await page.getByRole('button',{name:'Act on Region source.png',exact:true}).click();
 await page.getByRole('button',{name:'Edit regions',exact:true}).click();
 const drawer=page.getByRole('region',{name:'Preparation',exact:true});
 await drawer.getByRole('button',{name:'Draw, import or reuse masks',exact:true}).click();
 const editor=page.getByRole('region',{name:'Region editor',exact:true});
 await editor.getByRole('button',{name:'+ Add region',exact:true}).click();
 await editor.getByLabel('Region name',{exact:true}).fill('Surface detail');
 await editor.getByLabel('Region instruction',{exact:true}).fill('Blue cloth with fine stitched detail');
 await editor.getByRole('button',{name:'Rectangle',exact:true}).click();
 const stage=editor.locator('.konvajs-content');await expect(stage).toBeVisible();
 let box=(await stage.boundingBox())!;
 await page.mouse.move(box.x+box.width*.25,box.y+box.height*.25);await page.mouse.down();
 await page.mouse.move(box.x+box.width*.75,box.y+box.height*.75,{steps:5});await page.mouse.up();
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await expect(editor.getByText(/^Saved mask /)).toBeVisible();
 await editor.getByRole('button',{name:'Back to preparation · Esc'}).click();
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 let data=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 let direction=data.directions.find((d:{title:string})=>d.title==='Region study');
 const first=direction.setup.regions[0].mask_id;
 expect(await maskPixels(page,first)).toEqual({center:255,edge:255});
 await page.keyboard.press('Escape');await page.reload();
 await page.getByRole('button',{name:'Open preparation: Region study',exact:true}).click();
 await drawer.getByRole('button',{name:'Draw, import or reuse masks',exact:true}).click();
 await expect(editor.getByLabel('Region instruction',{exact:true})).toHaveValue('Blue cloth with fine stitched detail');
 await expect(stage.locator('canvas')).toHaveCount(3);
 await editor.getByRole('button',{name:'Erase',exact:true}).click();
 await editor.getByLabel('Brush pixels',{exact:true}).fill('128');
 box=(await stage.boundingBox())!;
 await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.down();
 await page.mouse.move(box.x+box.width*.52,box.y+box.height*.5,{steps:3});await page.mouse.up();
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await editor.getByRole('button',{name:'Back to preparation · Esc'}).click();
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 data=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 direction=data.directions.find((d:{title:string})=>d.title==='Region study');
 const second=direction.setup.regions[0].mask_id;
 expect(second).not.toBe(first);
 expect(await maskPixels(page,second)).toEqual({center:0,edge:255});
 const masks=await(await request.get(`/api/crexil/projects/${project.id}/masks`)).json();
 expect(masks.find((m:{id:string})=>m.id===second).previous_mask_id).toBe(first);
 await drawer.getByRole('spinbutton',{name:/Qwen Edit.*attempts/}).fill('1');
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 await drawer.getByRole('button',{name:'Review run',exact:true}).click();
 await expect(drawer.getByRole('img',{name:'Effective edit area for pass 1'})).toBeVisible();
 expect(data.turns).toHaveLength(0);expect(errors).toEqual([]);
});

async function openEmptyRegion(page:Page,request:import('@playwright/test').APIRequestContext){
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Region recovery ${Date.now()}`}})).json();
 const material=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Region source.png',data_base64:readFileSync(source).toString('base64')}})).json();
 const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:'Region study',setup:{action:'region',references:[{node_id:material.node.id,contribution:''}],regions:[]}}})).json();
 await page.goto(`/#/projects/${project.id}`);
 await page.getByRole('button',{name:'Open preparation: Region study',exact:true}).click();
 const drawer=page.getByRole('region',{name:'Preparation',exact:true});
 await drawer.getByRole('button',{name:'Draw, import or reuse masks'}).click();
 const editor=page.getByRole('region',{name:'Region editor',exact:true});
 await editor.getByRole('button',{name:'+ Add region'}).click();
 await expect(editor.locator('.konvajs-content')).toBeVisible();
 return {project,material,direction,drawer,editor};
}
async function maskPng(page:Page,size=1024){
 const b64=await page.evaluate(size=>{
  const canvas=document.createElement('canvas');canvas.width=canvas.height=size;
  const ctx=canvas.getContext('2d')!;ctx.fillStyle='black';ctx.fillRect(0,0,size,size);
  ctx.fillStyle='white';ctx.fillRect(0,0,size*.4,size);
  return canvas.toDataURL('image/png').split(',')[1];
 },size);
 return Buffer.from(b64,'base64');
}
async function centerStroke(page:Page){
 const stage=page.getByRole('region',{name:'Region editor'}).locator('.konvajs-content');
 const box=(await stage.boundingBox())!;
 await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.down();
 await page.mouse.move(box.x+box.width*.52,box.y+box.height*.5,{steps:3});await page.mouse.up();
}
function gate(){let release!:()=>void;const promise=new Promise<void>(resolve=>{release=resolve;});return {promise,release};}

test('import replaces pixels only after reading, saving and decoding, and contains keyboard focus',async({page,request})=>{
 const {project,editor,drawer}=await openEmptyRegion(page,request);
 const png=await maskPng(page);
 // Delay the browser file read separately from server acceptance and image loading.
 await page.evaluate(()=>{
  const original=File.prototype.arrayBuffer;
  File.prototype.arrayBuffer=async function(){await new Promise<void>(resolve=>{Object.assign(window,{releaseMaskRead:resolve});});return original.call(this);};
 });
 const saved=gate(),decoded=gate();let posts=0;
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  posts++;await saved.promise;await route.continue();
 });
 await page.route('**/api/crexil/masks/*/image',async route=>{await decoded.promise;await route.continue();});
 await editor.getByLabel('Import PNG mask',{exact:true}).setInputFiles({name:'left.png',mimeType:'image/png',buffer:png});
 await expect(editor.getByRole('status')).toHaveText('Reading imported mask');
 await centerStroke(page);await page.keyboard.press('Escape');await expect(editor).toBeVisible();
 await expect(editor.getByRole('button',{name:'Back to preparation · Esc'})).toBeDisabled();
 await page.evaluate(()=>{(window as unknown as {releaseMaskRead:()=>void}).releaseMaskRead();});
 await expect.poll(()=>posts).toBe(1);await expect(editor.getByRole('status')).toHaveText('Saving masks');
 await centerStroke(page);saved.release();
 await expect(editor.getByRole('status')).toHaveText('Loading accepted mask');
 await centerStroke(page);decoded.release();
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 let masks=await(await request.get(`/api/crexil/projects/${project.id}/masks`)).json();
 expect(masks).toHaveLength(1);expect(await maskPixels(page,masks[0].id)).toEqual({center:0,edge:255});
 const first=editor.getByRole('button',{name:'Back to preparation · Esc'});
 await first.focus();await page.keyboard.press('Shift+Tab');
 await expect(editor.getByLabel('Reuse a saved mask',{exact:true})).toBeFocused();
 await page.keyboard.press('Tab');await expect(first).toBeFocused();
 await centerStroke(page);await expect(editor.getByRole('status')).toHaveText('Saved masks');
 masks=await(await request.get(`/api/crexil/projects/${project.id}/masks`)).json();
 expect(masks).toHaveLength(2);expect(await maskPixels(page,masks[1].id)).toEqual({center:255,edge:255});
 expect(masks[1].previous_mask_id).toBe(masks[0].id);
 await page.keyboard.press('Escape');await expect(editor).not.toBeVisible();
 await expect(drawer.getByRole('button',{name:'Draw, import or reuse masks'})).toBeFocused();
});

test('validation refusal rebuilds a corrected mask command while uncertain delivery replays its ID',async({page,request})=>{
 const {project,editor}=await openEmptyRegion(page,request);
 const bodies:Record<string,unknown>[]=[];
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  bodies.push(route.request().postDataJSON());await route.continue();
 });
 await editor.getByLabel('Region name',{exact:true}).fill('');
 await editor.getByLabel('Import PNG mask',{exact:true}).setInputFiles({name:'left.png',mimeType:'image/png',buffer:await maskPng(page)});
 await expect(editor.getByRole('status')).toHaveText('Mask not saved');
 await editor.getByLabel('Region name',{exact:true}).fill('Corrected name');
 await editor.getByRole('button',{name:'Retry mask save'}).click();
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 expect(bodies).toHaveLength(2);expect(bodies[1].request_id).not.toBe(bodies[0].request_id);
 expect(bodies[1].data_base64).toBe(bodies[0].data_base64);expect(bodies[1].name).toBe('Corrected name');
 await page.unroute(`**/api/crexil/projects/${project.id}/masks`);
 const uncertain:Record<string,unknown>[]=[];
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  uncertain.push(route.request().postDataJSON());
  if(uncertain.length===1){await route.fetch();await route.abort('failed');}else await route.continue();
 });
 await centerStroke(page);await expect(editor.getByRole('status')).toHaveText('Mask not saved');
 await editor.getByRole('button',{name:'Retry mask save'}).click();await expect(editor.getByRole('status')).toHaveText('Saved masks');
 expect(uncertain).toHaveLength(2);expect(uncertain[1]).toEqual(uncertain[0]);
 const masks=await(await request.get(`/api/crexil/projects/${project.id}/masks`)).json();expect(masks).toHaveLength(2);
});

test('adjustment refusal can be corrected and old ink cannot overwrite the resized mask',async({page,request})=>{
 const {project,editor}=await openEmptyRegion(page,request);
 const png=await maskPng(page,512);
 const donor=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Small source.png',data_base64:png.toString('base64')}})).json();
 const donorInfo=await(await request.get(`/api/crexil/projects/${project.id}/mask-source/${donor.node.id}`)).json();
 const donorMask=await(await request.post(`/api/crexil/projects/${project.id}/masks`,{data:{...command(),name:'Small mask',source_node_id:donor.node.id,source_sha256:donorInfo.sha256,data_base64:png.toString('base64')}})).json();
 // Reopening refetches the immutable mask library without changing preparation.
 await page.keyboard.press('Escape');await page.keyboard.press('Escape');await page.reload();
 await page.getByRole('button',{name:'Open preparation: Region study',exact:true}).click();
 await page.getByRole('button',{name:'Draw, import or reuse masks'}).click();
 await editor.getByLabel('Reuse a saved mask',{exact:true}).selectOption(donorMask.id);
 await editor.getByLabel('Reason for adjustment',{exact:true}).fill('Use the same portion of the larger source');
 await editor.getByRole('button',{name:'Save and inspect adjusted mask'}).click();
 await expect(editor.getByRole('status')).toHaveText('Mask not saved');
 await editor.getByLabel('Coordinate adjustment',{exact:true}).selectOption('true');
 const accepted=gate();let requests=0;
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  requests++;await accepted.promise;await route.continue();
 });
 await editor.getByRole('button',{name:'Retry mask save'}).click();await expect.poll(()=>requests).toBe(1);
 await centerStroke(page);await page.keyboard.press('Escape');await expect(editor).toBeVisible();accepted.release();
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 const masks=await(await request.get(`/api/crexil/projects/${project.id}/masks`)).json();expect(masks).toHaveLength(2);
 const adjusted=masks.find((m:{id:string})=>m.id!==donorMask.id);
 expect(adjusted.previous_mask_id).toBe(donorMask.id);expect(adjusted.width).toBe(1024);
 expect(await maskPixels(page,adjusted.id)).toEqual({center:0,edge:255});
});

test('navigation waits for accepted mask bytes and the saved preparation link',async({page,request})=>{
 const {project,direction,editor}=await openEmptyRegion(page,request);
 const accepted=gate();let posts=0;
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  posts++;await accepted.promise;await route.continue();
 });
 await editor.getByLabel('Import PNG mask',{exact:true}).setInputFiles({name:'left.png',mimeType:'image/png',buffer:await maskPng(page)});
 await expect.poll(()=>posts).toBe(1);
 await page.getByRole('link',{name:'Projects',exact:true}).click();
 expect(new URL(page.url()).hash).toBe(`#/projects/${project.id}`);
 await expect(editor).toBeVisible();accepted.release();
 await expect(page).toHaveURL(/\/#\/projects$/);
 const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 const saved=snapshot.directions.find((d:{id:string})=>d.id===direction.id);
 expect(saved.setup.regions[0].mask_id).toBeTruthy();
 expect(snapshot.turns).toHaveLength(0);
});

test('new ink invalidates a ready review before its save completes',async({page,request})=>{
 const {project,drawer,editor}=await openEmptyRegion(page,request);
 await editor.getByLabel('Import PNG mask',{exact:true}).setInputFiles({name:'left.png',mimeType:'image/png',buffer:await maskPng(page)});
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await page.keyboard.press('Escape');await expect(drawer.getByRole('status')).toHaveText('Saved');
 // Isolated preview has no executor. This response only exercises the artist's
 // stale-review guard; no request is submitted and it is not runtime evidence.
 await page.route('**/api/crexil/directions/*/review',async route=>{
  const response=await route.fetch();const review=await response.json();
  await route.fulfill({response,json:{...review,ready:true,ready_attempts:1,errors:[]}});
 });
 let submits=0;page.on('request',request=>{if(request.url().endsWith('/submit'))submits++;});
 await drawer.getByRole('button',{name:'Review run',exact:true}).click();
 await expect(drawer.getByRole('button',{name:/^Run 1 attempt · /})).toBeVisible();
 await drawer.getByRole('button',{name:'Draw, import or reuse masks'}).click();
 const accepted=gate();
 await page.route(`**/api/crexil/projects/${project.id}/masks`,async route=>{
  if(route.request().method()==='POST')await accepted.promise;
  await route.continue();
 });
 await centerStroke(page);
 await expect(drawer.getByRole('button',{name:/^Run 1 attempt · /,includeHidden:true})).toHaveCount(0);
 await expect(editor.getByRole('button',{name:'Back to preparation · Esc'})).toBeDisabled();
 accepted.release();await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await page.keyboard.press('Escape');await expect(drawer.getByRole('status')).toHaveText('Saved');
 await expect(drawer.getByRole('button',{name:/^Run 1 attempt · /})).toHaveCount(0);expect(submits).toBe(0);
});

test('choosing the saved preparation clears discarded ink before the next stroke',async({page,request})=>{
 const {project,direction,drawer,editor}=await openEmptyRegion(page,request);
 const path=`/api/crexil/projects/${project.id}`;
 await editor.getByRole('button',{name:'Back to preparation · Esc'}).click();
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 const base=(await(await request.get(path)).json()).directions.find((item:{id:string})=>item.id===direction.id);
 let lost=true;await page.route(`**/api/crexil/directions/${direction.id}`,async route=>{if(lost){lost=false;await route.abort('failed');}else await route.continue();});
 await drawer.getByRole('button',{name:'Draw, import or reuse masks',exact:true}).click();
 await editor.getByLabel('Import PNG mask',{exact:true}).setInputFiles({name:'left.png',mimeType:'image/png',buffer:await maskPng(page)});
 await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await editor.getByRole('button',{name:'Back to preparation · Esc'}).click();
 await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
 expect((await request.patch(`/api/crexil/directions/${direction.id}`,{data:{...command(),actor:{kind:'agent',name:'Mask collaborator'},revision:base.revision,title:base.title,setup:{...base.setup,prompt:'Keep the blank region'}}})).ok()).toBe(true);
 await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
 const conflict=drawer.getByRole('region',{name:'Preparation conflict',exact:true});
 await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
 await expect(conflict.getByRole('button',{name:'Discard my edits and use saved version',exact:true})).toBeVisible();
 await conflict.getByRole('button',{name:'Discard my edits and use saved version',exact:true}).click();
 await drawer.getByRole('button',{name:'Draw, import or reuse masks',exact:true}).click();
 await expect(editor.getByText('No saved ink yet',{exact:true})).toBeVisible();
 await centerStroke(page);await expect(editor.getByRole('status')).toHaveText('Saved masks');
 await editor.getByRole('button',{name:'Back to preparation · Esc'}).click();
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 const saved=(await(await request.get(path)).json()).directions.find((item:{id:string})=>item.id===direction.id);
 expect(await maskPixels(page,saved.setup.regions[0].mask_id)).toEqual({center:255,edge:0});
 const masks=await(await request.get(`${path}/masks`)).json();expect(masks).toHaveLength(2);
 expect(masks.find((mask:{id:string})=>mask.id===saved.setup.regions[0].mask_id).previous_mask_id).toBeNull();
});
