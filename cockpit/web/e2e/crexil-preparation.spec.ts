import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Preparation browser verification'}});

test('artist crops a saved source, reviews pixels and saves a derived image',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Image preparation ${Date.now()}`}})).json();
 const imported=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Study source.png',data_base64:readFileSync(source).toString('base64')}})).json();
 await request.put(`/api/crexil/directions/${imported.direction.id}/selection`,{data:{...command(),revision:0,node_ids:[imported.node.id]}});
 await page.goto(`/#/projects/${project.id}`);
 await page.getByRole('button',{name:'Act on Study source.png',exact:true}).click();
 await page.getByRole('button',{name:'Crop, resize or arrange',exact:true}).click();
 const drawer=page.getByRole('region',{name:'Preparation',exact:true});
 await drawer.getByRole('combobox',{name:'Transform',exact:true}).selectOption('crop');
 await drawer.getByRole('button',{name:'Start with whole image',exact:true}).click();
 await drawer.getByLabel('Crop x',{exact:true}).fill('30');
 await drawer.getByLabel('Crop y',{exact:true}).fill('40');
 await drawer.getByLabel('Crop width',{exact:true}).fill('300');
 await drawer.getByLabel('Crop height',{exact:true}).fill('220');
 await drawer.getByRole('button',{name:'Use these sources for generation',exact:true}).click();
 await drawer.getByRole('combobox',{name:'Action',exact:true}).selectOption('prepare');
 await expect(drawer.getByLabel('Crop x',{exact:true})).toHaveValue('30');
 await drawer.getByLabel('Crop width',{exact:true}).fill('0');
 await expect(drawer.getByRole('status')).toHaveText('Not saved');
 await drawer.getByLabel('Crop width',{exact:true}).fill('300');
 await expect(drawer.getByRole('status')).toHaveText('Saved');
 await page.keyboard.press('Escape');
 await page.reload();
 await page.getByRole('button',{name:'Open preparation: Prepared image',exact:true}).click();
 await expect(drawer.getByLabel('Crop width',{exact:true})).toHaveValue('300');
 await drawer.getByRole('button',{name:'Review image',exact:true}).click();
 await expect(drawer.getByRole('img',{name:'Exact prepared result'})).toBeVisible();
 await expect(drawer.getByText('300 × 220 pixels · No generation or charge',{exact:true})).toBeVisible();
 for(const width of [1100,1440,1920]){
  await page.setViewportSize({width,height:900});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 }
 await drawer.getByRole('button',{name:'Save derived image',exact:true}).click();
 await expect(drawer).not.toBeVisible();
 const data=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 expect(data.turns).toHaveLength(0);
 expect(data.nodes).toHaveLength(2);
 const prepared=data.nodes.find((n:{id:string})=>n.id!==imported.node.id);
 expect(prepared.parents).toEqual([imported.node.id]);
 expect(prepared.material.origin.kind).toBe('prepared_image');
 expect(prepared.material.width).toBe(300);
 expect(prepared.material.height).toBe(220);
 await page.getByRole('button',{name:`Act on ${prepared.material.filename}`,exact:true}).dblclick();
 await expect(page.getByText('Prepared from these sources by Crexil. Its pixel transform and source hashes were recorded.',{exact:true})).toBeVisible();
 await page.getByText('Preparation recipe and source hashes',{exact:true}).click();
 await expect(page.getByText(prepared.material.origin.transform.sources[0].sha256,{exact:false})).toBeVisible();
 expect(errors).toEqual([]);
});

test('a lost derived-image response remains confirmable after polling and reload',async({page,request})=>{
 page.on('dialog',dialog=>void dialog.accept());
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Derived receipt ${Date.now()}`}})).json();
 const path=`/api/crexil/projects/${project.id}`;
 const imported=await(await request.post(`${path}/material`,{data:{...command(),filename:'Source.png',data_base64:readFileSync(source).toString('base64')}})).json();
 const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Resized study',setup:{action:'prepare',references:[{node_id:imported.node.id}],image_preparation:{mode:'resize',width:256,height:256,items:[{node_id:imported.node.id}]}}}})).json();
 const bodies:unknown[]=[];let lost=true;
 await page.route(`**/api/crexil/directions/${direction.id}/prepare-image`,async route=>{
  bodies.push(route.request().postDataJSON());
  if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(201);await route.abort('failed');}
  else await route.continue();
 });
 await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Open preparation: Resized study',exact:true}).click();
 const drawer=page.getByRole('region',{name:'Preparation',exact:true});
 await drawer.getByRole('button',{name:'Review image',exact:true}).click();
 await drawer.getByRole('button',{name:'Save derived image',exact:true}).click();
 await expect(drawer.getByRole('button',{name:'Check submission',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Reuse setup: Resized study',exact:true})).toBeVisible();
 await page.reload();await page.getByRole('button',{name:'Recover Resized study',exact:true}).click();
 await drawer.getByRole('button',{name:'Check submission',exact:true}).click();
 await expect(drawer).not.toBeVisible();
 const snapshot=await(await request.get(path)).json();expect(snapshot.nodes).toHaveLength(2);expect(snapshot.turns).toEqual([]);
 expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
});
