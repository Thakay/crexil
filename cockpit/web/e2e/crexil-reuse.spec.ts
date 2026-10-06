import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const image=readFileSync(fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url))).toString('base64');
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Reuse verification'}});

test('reuse reviews known origin and recovers one independent copy after lost acceptance and reload',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const source=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Source instrument ${Date.now()}`}})).json();
  const target=await(await request.post('/api/crexil/projects',{data:{...command(),name:`New instrument study ${Date.now()}`}})).json();
  const parent=await(await request.post(`/api/crexil/projects/${source.id}/material`,{data:{...command(),filename:'Original.png',data_base64:image}})).json();
  const material=await(await request.post(`/api/crexil/projects/${source.id}/material`,{data:{...command(),filename:'Returned.png',data_base64:image,parents:[parent.node.id],tool:'Krita',explanation:'Reported hand edit'}})).json();
  await page.goto(`/#/projects/${target.id}`);
  await page.getByRole('button',{name:'Import material',exact:true}).first().click();
  const room=page.getByRole('region',{name:'Import material',exact:true});
  await expect(room.getByRole('button',{name:'Esc · Project',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');await expect(room).toBeHidden();
  await expect(page.getByRole('button',{name:'Import material',exact:true}).first()).toBeFocused();
  await page.keyboard.press('Enter');
  await room.getByLabel('Source project',{exact:true}).selectOption(source.id);
  await room.getByRole('radio',{name:'Returned.png Image',exact:true}).check();
  await room.getByLabel('Why bring it here?').fill('Keep the silhouette for a new instrument.');
  await room.getByRole('button',{name:'Review import',exact:true}).click();
  const review=room.getByRole('region',{name:'Import review',exact:true});
  await expect(review.getByText('From '+source.name,{exact:true})).toBeVisible();
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});await review.scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);await page.screenshot({path:artifact(`reuse-${width}.png`)});}
  const bodies:unknown[]=[];
  await page.route(`**/api/crexil/projects/${target.id}/reuse`,async route=>{
    bodies.push(route.request().postDataJSON());
    if(bodies.length===1){await route.fetch();await route.abort('failed');return;}
    await route.continue();
  });
  await review.getByRole('button',{name:'Import independent copy',exact:true}).click();
  await expect(room.getByRole('button',{name:'Retry same import',exact:true})).toBeEnabled();
  // Source purge after acceptance must not prevent receipt replay or affect bytes.
  await request.post(`/api/nodes/${material.node.id}/set-aside`);
  expect((await request.post(`/api/workspaces/${source.id}/purge`,{data:{ids:[material.node.id]}})).ok()).toBe(true);
  await page.reload();await page.getByRole('button',{name:'Import material',exact:true}).first().click();
  await room.getByRole('button',{name:'Retry same import',exact:true}).click();
  await expect(room.getByText('Material imported',{exact:true})).toBeVisible();
  expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
  const copy=await(await request.get(`/api/crexil/projects/${target.id}`)).json();
  expect(copy.nodes).toHaveLength(1);expect(copy.nodes[0].parents).toEqual([]);expect(copy.turns).toEqual([]);
  const node=copy.nodes[0];expect(node.material.origin.source.node_id).toBe(material.node.id);
  expect(node.material.origin.source_record.manifest.parent_node_ids).toEqual([parent.node.id]);
  expect(node.retained_files).toHaveLength(1);
  expect((await request.get(`/api/nodes/${node.id}/image`)).ok()).toBe(true);
  expect((await request.get(`/api/crexil/material/${node.id}/files/${node.retained_files[0].id}`)).ok()).toBe(true);
  await room.getByRole('button',{name:'Inspect imported material',exact:true}).click();
  const inspection=page.getByRole('region',{name:'Inspect material',exact:true});
  await expect(inspection.getByRole('link',{name:source.name+' ›',exact:true})).toBeVisible();
  await expect(inspection.getByText('Retained related files',{exact:true})).toBeVisible();
  await page.screenshot({path:artifact('reuse-inspection-1920.png')});
  expect(await inspection.locator('aside').evaluate(element=>element.scrollWidth>element.clientWidth)).toBe(false);
  await inspection.getByRole('link',{name:source.name+' ›',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(source.id+'\\?material='+material.node.id));
  await expect(page.getByText('This material is unavailable. Its record remains.',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await expect(inspection).toBeHidden();
  expect(errors).toEqual([]);
});
