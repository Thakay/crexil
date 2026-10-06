import {test,expect} from '@playwright/test';

const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'agent',name:'Draft collaborator'}});

test('a stale preparation survives reload and a lost separate-copy response without overwriting the agent',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',dialog=>void dialog.accept());
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Conflicting drafts ${Date.now()}`}})).json();
  const path=`/api/crexil/projects/${project.id}`;
  const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Shared study',setup:{prompt:'Original brass study',instructions:'Keep the original instructions',controls:{retained_control:7}}}})).json();
  const updatePath=`/api/crexil/directions/${direction.id}`;
  let first=true;
  await page.route(`**${updatePath}`,async route=>{if(first){first=false;await route.abort('failed');}else await route.continue();});
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'Open preparation: Shared study',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('My local surface study');
  await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
  expect((await request.patch(updatePath,{data:{...command(),revision:0,title:'Agent study',setup:{...direction.setup,prompt:'The agent chose a different shape'}}})).ok()).toBe(true);
  await page.reload();
  await page.getByRole('region',{name:'Local preparations',exact:true}).getByRole('button',{name:'Recover Agent study',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('My local surface study');
  await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
  const conflict=drawer.getByRole('region',{name:'Preparation conflict',exact:true});
  await expect(conflict).toBeVisible();
  await expect(drawer.getByRole('button',{name:'Review run',exact:true})).toBeDisabled();
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('region',{name:'Your version',exact:true})).toContainText('My local surface study');
  await expect(conflict.getByRole('region',{name:'Saved revision 1',exact:true})).toContainText('The agent chose a different shape');
  for(const width of [1440,1920]){
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:`../../.preview/screenshots/preparation-conflict-${width}.png`});
  }
  await conflict.getByRole('button',{name:'Keep in this tab and close',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  await page.getByRole('button',{name:'Recover Agent study',exact:true}).click();
  const copies:unknown[]=[];let lost=true;
  await page.route(`**${path}/directions`,async route=>{
    copies.push(route.request().postDataJSON());
    if(route.request().postDataJSON().setup.attempt_limit===0){await route.continue();return;}
    if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(201);await route.abort('failed');}
    else await route.continue();
  });
  await drawer.getByRole('spinbutton',{name:'Attempt limit',exact:true}).fill('0');
  await conflict.getByRole('button',{name:'Save my version separately',exact:true}).click();
  await expect(drawer.getByRole('alert')).toContainText('Some values need attention');
  await drawer.getByRole('spinbutton',{name:'Attempt limit',exact:true}).fill('3');
  await conflict.getByRole('button',{name:'Save my version separately',exact:true}).click();
  await expect(conflict.getByRole('button',{name:'Confirm same copy',exact:true})).toBeEnabled();
  await drawer.locator('textarea[aria-label="Prompt"]').focus();
  await expect(drawer.locator('textarea[aria-label="Prompt"]')).not.toBeFocused();
  await page.reload();await page.getByRole('button',{name:'Recover Agent study',exact:true}).click();
  await conflict.getByRole('button',{name:'Confirm same copy',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Direction name',exact:true})).toHaveValue('Shared study copy');
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  const snapshot=await(await request.get(path)).json();
  expect(snapshot.directions).toHaveLength(2);expect(snapshot.turns).toEqual([]);
  expect(snapshot.directions.find((item:{id:string})=>item.id===direction.id).setup.prompt).toBe('The agent chose a different shape');
  const copy=snapshot.directions.find((item:{id:string})=>item.id!==direction.id);
  expect(copy.setup.prompt).toBe('My local surface study');
  expect(copy.setup.attempt_limit).toBe(3);
  expect(copy.setup.instructions).toBe('Keep the original instructions');expect(copy.setup.controls).toEqual({retained_control:7});
  expect(copies).toHaveLength(3);expect(copies[0]).not.toEqual(copies[1]);expect(copies[1]).toEqual(copies[2]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.startsWith('crexil-preparation:')))).toEqual([]);
  expect(errors).toEqual([]);
});

test('a comparison names its revision and refuses an overwrite after another edit',async({page,request})=>{
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Compare revisions ${Date.now()}`}})).json();
  const path=`/api/crexil/projects/${project.id}`;
  const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Shared study',setup:{prompt:'Original'}}})).json();
  const updatePath=`/api/crexil/directions/${direction.id}`;
  let lost=true;await page.route(`**${updatePath}`,async route=>{if(lost){lost=false;await route.abort('failed');}else await route.continue();});
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Open preparation: Shared study',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const conflict=drawer.getByRole('region',{name:'Preparation conflict',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Artist wording');
  await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
  const agentEdit=async(revision:number,prompt:string)=>expect((await request.patch(updatePath,{data:{...command(),revision,title:'Shared study',setup:{...direction.setup,prompt}}})).ok()).toBe(true);
  await agentEdit(0,'Agent revision one');
  await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('region',{name:'Saved revision 1',exact:true})).toBeVisible();
  await agentEdit(1,'Agent revision two');
  await conflict.getByRole('button',{name:'Save my version over this revision',exact:true}).click();
  await expect(drawer.getByRole('alert')).toContainText('changed');
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('region',{name:'Saved revision 2',exact:true})).toContainText('Agent revision two');
  await conflict.getByRole('button',{name:'Save my version over this revision',exact:true}).click();
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  const saved=(await(await request.get(path)).json()).directions[0];
  expect(saved.revision).toBe(3);expect(saved.setup.prompt).toBe('Artist wording');
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Leave this local draft');
  await page.getByRole('link',{name:'Projects',exact:true}).click();
  await expect(page).toHaveURL(/#\/projects$/);
  expect((await(await request.get(path)).json()).directions[0].setup.prompt).toBe('Leave this local draft');
});

test('an accepted save can be confirmed after reload before saving subsequent local edits',async({page,request})=>{
  page.on('dialog',dialog=>void dialog.accept());
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Lost draft receipt ${Date.now()}`}})).json();
  const path=`/api/crexil/projects/${project.id}`;
  const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Receipt study'}})).json();
  const bodies:unknown[]=[];let lost=true;
  await page.route(`**/api/crexil/directions/${direction.id}`,async route=>{
    bodies.push(route.request().postDataJSON());
    if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(200);await route.abort('failed');}
    else await route.continue();
  });
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Open preparation: Receipt study',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('First accepted text');
  await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Later local text');
  await page.reload();await page.getByRole('button',{name:'Recover Receipt study',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Later local text');
  await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  const snapshot=await(await request.get(path)).json();
  expect(snapshot.directions).toHaveLength(1);expect(snapshot.directions[0].revision).toBe(2);
  expect(snapshot.directions[0].setup.prompt).toBe('Later local text');expect(snapshot.turns).toEqual([]);
  expect(bodies).toHaveLength(3);expect(bodies[0]).toEqual(bodies[1]);expect(bodies[2]).not.toEqual(bodies[1]);
});

test('an agent-discarded preparation keeps local edits reachable and can use the saved version explicitly',async({page,request})=>{
  page.on('dialog',dialog=>void dialog.accept());
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Discarded draft ${Date.now()}`}})).json();
  const path=`/api/crexil/projects/${project.id}`;
  const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Retained study',setup:{prompt:'Original words'}}})).json();
  let lost=true;await page.route(`**/api/crexil/directions/${direction.id}`,async route=>{if(lost){lost=false;await route.abort('failed');}else await route.continue();});
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Open preparation: Retained study',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('Still present locally');
  await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
  expect((await request.post(`/api/crexil/directions/${direction.id}/state`,{data:{...command(),revision:0,action:'discard'}})).ok()).toBe(true);
  await page.reload();await page.getByRole('button',{name:'Recover Retained study',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Still present locally');
  await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
  const conflict=drawer.getByRole('region',{name:'Preparation conflict',exact:true});
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('button',{name:'Save my version over this revision',exact:true})).toBeDisabled();
  await conflict.getByRole('button',{name:'Discard my edits and use saved version',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Original words');
  await drawer.getByRole('button',{name:'Close · Esc',exact:true}).click();
  await expect(drawer).not.toBeVisible();
  await expect(page.getByRole('region',{name:'Local preparations',exact:true})).not.toBeVisible();
  expect((await(await request.get(path)).json()).directions[0].state).toBe('discarded');
});

test('adopting a stale comparison and reviewing an unseen revision both require a new choice',async({page,request})=>{
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Review visible settings ${Date.now()}`}})).json();
  const path=`/api/crexil/projects/${project.id}`;
  const direction=await(await request.post(`${path}/directions`,{data:{...command(),title:'Shared study',setup:{prompt:'Original'}}})).json();
  const updatePath=`/api/crexil/directions/${direction.id}`;
  let lost=true;await page.route(`**${updatePath}`,async route=>{if(lost){lost=false;await route.abort('failed');}else await route.continue();});
  const agentEdit=async(revision:number,prompt:string)=>expect((await request.patch(updatePath,{data:{...command(),revision,title:'Shared study',setup:{...direction.setup,prompt}}})).ok()).toBe(true);
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Open preparation: Shared study',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const conflict=drawer.getByRole('region',{name:'Preparation conflict',exact:true});
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill('My version is still here');
  await expect(drawer.getByRole('button',{name:'Confirm same save',exact:true})).toBeVisible();
  await agentEdit(0,'Agent revision one');
  await drawer.getByRole('button',{name:'Confirm same save',exact:true}).click();
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('region',{name:'Saved revision 1',exact:true})).toBeVisible();
  await agentEdit(1,'Agent revision two');
  await expect(page.getByRole('button',{name:'Open preparation: Shared study',exact:true})).toContainText('Agent revision two');
  await conflict.getByRole('button',{name:'Discard my edits and use saved version',exact:true}).click();
  await expect(drawer.getByRole('alert')).toContainText('changed again');
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('My version is still here');
  await conflict.getByRole('button',{name:'Compare saved version',exact:true}).click();
  await expect(conflict.getByRole('region',{name:'Saved revision 2',exact:true})).toBeVisible();
  await conflict.getByRole('button',{name:'Discard my edits and use saved version',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Prompt',exact:true})).toHaveValue('Agent revision two');
  await page.route(`**${updatePath}/review`,async route=>{await agentEdit(2,'An unseen revision during review');await route.continue();});
  await drawer.getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer.getByRole('alert')).toContainText('changed before review');
  await expect(drawer.getByRole('button',{name:'Review run',exact:true})).toBeDisabled();
  expect((await(await request.get(path)).json()).turns).toEqual([]);
});
