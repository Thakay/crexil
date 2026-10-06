import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png', import.meta.url));
const body = () => ({request_id: crypto.randomUUID(), actor:{kind:'human',name:'Browser verification'}});

test('artist imports, chooses, continues and reopens saved preparation', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#/projects');
  await page.getByRole('button',{name:'+ New project',exact:true}).click();
  await page.getByLabel('Project name',{exact:true}).fill(`Browser journey ${Date.now()}`);
  await page.getByLabel('Brief', {exact:false}).fill('Explore the silhouette, then carry the strongest source forward.');
  await page.getByRole('button',{name:'Create project',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Start with words or material.'})).toBeVisible();
  await page.locator('input[type=file]').setInputFiles(source);
  await page.getByRole('button',{name:'Strip ›',exact:true}).first().click();
  const picker=page.getByRole('region',{name:'Choose displayed takes'});
  await picker.getByRole('button',{name:'Display source.png',exact:true}).click();
  await picker.getByRole('button',{name:'Apply',exact:true}).click();
  await expect(picker).not.toBeVisible();
  const face=page.getByRole('button',{name:'Act on source.png',exact:true});
  await expect(face).toBeVisible();
  await face.click();
  await page.getByRole('button',{name:'Continue from image',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Retain the silhouette; explore weathered brass.');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Close · Esc'}).click();
  await page.reload();
  await page.getByRole('button',{name:'Open preparation: Continue from an image',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Retain the silhouette; explore weathered brass.');
  await drawer.getByRole('button',{name:'Close · Esc'}).click();
  await page.getByRole('button',{name:'Strip ›',exact:true}).first().click();
  await picker.getByRole('button',{name:'Display source.png',exact:true}).click();
  await page.keyboard.press('Escape');
  await expect(face).toBeVisible();
  await face.dblclick();
  await expect(page.getByRole('region',{name:'Inspect material'})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('region',{name:'Inspect material'})).not.toBeVisible();
  await page.getByRole('button',{name:'Strip ›',exact:true}).first().click();
  await picker.getByRole('button',{name:'Set direction aside'}).click();
  await page.getByRole('button',{name:'Tray · 1 ›'}).click();
  await page.getByRole('button',{name:'Strip ›',exact:true}).click();
  await picker.getByRole('button',{name:'Restore direction'}).click();
  await page.getByRole('button',{name:'Esc · Project'}).click();
  await expect(face).toBeVisible();
  expect(errors).toEqual([]);
});

test('long project keeps every direction reachable at desktop sizes', async ({page,request}) => {
  const created=await request.post('/api/crexil/projects',{data:{...body(),name:`Forty directions ${Date.now()}`,brief:'A long history stays available.'}});
  expect(created.ok()).toBeTruthy();
  const project=await created.json();
  const imported=await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...body(),filename:'reference.png',data_base64:readFileSync(source).toString('base64')}});
  expect(imported.ok()).toBeTruthy();
  const material=await imported.json();
  for(let i=1;i<40;i++) {
    const response=await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...body(),title:`Direction ${i}`,setup:{prompt:`Variation ${i}`,references:[{node_id:material.node.id,contribution:'Silhouette'}]}}});
    expect(response.ok()).toBeTruthy();
  }
  for(const width of [1440,1920]) {
    await page.setViewportSize({width,height:900});
    await page.goto(`/#/projects/${project.id}`);
    await expect(page.getByRole('region',{name:'Direction 39',exact:true})).toBeAttached();
    await page.getByRole('region',{name:'Direction 39',exact:true}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('region',{name:'Direction 39',exact:true})).toBeVisible();
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    expect(overflow).toBe(false);
  }
});

test('project budgets and explicit tariff preparation persist across reload',async({page,request})=>{
  const project=await(await request.post('/api/crexil/projects',{data:{...body(),name:`Budget journey ${Date.now()}`}})).json();
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:project.name,exact:true}).click();
  await page.getByLabel('Project attempt limit',{exact:true}).fill('12');
  await page.getByLabel('Project spending limit · USD',{exact:true}).fill('0.12');
  await expect(page.getByLabel('Project spending policy',{exact:true})).toHaveValue('hard_cap');
  await page.getByLabel('Project spending policy',{exact:true}).selectOption('published_tariff');
  await expect(page.getByText('Published tariffs reserve expected spend. The provider does not guarantee a final charge ceiling.')).toBeVisible();
  await page.getByRole('button',{name:'Save project',exact:true}).click();
  await expect(page.getByRole('form',{name:'Project settings',exact:true})).not.toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:project.name,exact:true}).click();
  await expect(page.getByLabel('Project attempt limit',{exact:true})).toHaveValue('12');
  await expect(page.getByLabel('Project spending limit · USD',{exact:true})).toHaveValue('0.12');
  await page.getByRole('button',{name:project.name,exact:true}).click();
  const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...body(),title:'Hosted preparation',setup:{prompt:'Brass study'}}})).json();
  await page.reload();
  await page.getByRole('button',{name:'Open preparation: Hosted preparation',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await expect(drawer.getByLabel('Spending policy',{exact:true})).toHaveValue('hard_cap');
  await drawer.getByLabel('Spending policy',{exact:true}).selectOption('published_tariff');
  await drawer.getByLabel('Spending limit · USD',{exact:true}).fill('0.08');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await drawer.getByRole('button',{name:'Close · Esc'}).click();
  const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  const saved=snapshot.directions.find((d:{id:string})=>d.id===direction.id);
  expect(saved.setup.spending_policy).toBe('published_tariff');
  expect(saved.setup.spending_limit_usd).toBe(.08);
  expect(snapshot.project.limits).toEqual({attempt_limit:12,spending_limit_usd:.12,spending_policy:'published_tariff'});
  expect(snapshot.turns).toEqual([]);
});

test('project pause is shared, persistent and replays uncertain delivery once',async({page,request})=>{
  const project=await(await request.post('/api/crexil/projects',{data:{...body(),name:`Control journey ${Date.now()}`}})).json();
  const path=`/api/crexil/execution/project/${project.id}`;
  await page.goto(`/#/projects/${project.id}`);
  const controls=page.getByRole('region',{name:'Project execution',exact:true});
  await controls.getByRole('button',{name:'Pause project',exact:true}).click();
  await expect(controls.getByRole('button',{name:'Resume project',exact:true})).toBeVisible();
  await page.reload();
  await expect(controls.getByText('Future requests wait here. Running work can finish.')).toBeVisible();
  const paused=await(await request.get(path)).json();
  const resumed=await request.post(path,{data:{...body(),actor:{kind:'agent',name:'Studio guide'},revision:paused.revision,action:'resume'}});
  expect(resumed.ok()).toBeTruthy();
  await expect(controls.getByRole('button',{name:'Pause project',exact:true})).toBeVisible();
  await expect(controls.getByText('Resumed by Studio guide',{exact:true})).toBeVisible();
  const delivered:unknown[]=[];
  await page.route(`**${path}`,async route=>{
    if(route.request().method()!=='POST'){await route.continue();return;}
    delivered.push(route.request().postDataJSON());
    if(delivered.length===1){await route.fetch();await route.abort('failed');}
    else await route.continue();
  });
  await controls.getByRole('button',{name:'Pause project',exact:true}).click();
  await expect(controls.getByRole('button',{name:'Retry same request',exact:true})).toBeVisible();
  const saved=await(await request.get(path)).json();
  expect(saved.paused).toBe(true);
  await controls.getByRole('button',{name:'Retry same request',exact:true}).click();
  await expect(controls.getByRole('button',{name:'Resume project',exact:true})).toBeEnabled();
  expect(delivered).toHaveLength(2);expect(delivered[1]).toEqual(delivered[0]);
  expect((await(await request.get(path)).json()).revision).toBe(saved.revision);
});
