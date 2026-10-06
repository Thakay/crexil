import {test,expect} from '@playwright/test';
import {artifact} from './paths';

const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Onboarding verification'}});

test('an offline newcomer discovers prepared work and recovers one preparation after lost acceptance',async({page,request})=>{
 const health=await(await request.get('/api/health')).json();
 test.skip(!health.preview,'The offline wording and the locked key field belong to the isolated preview.');
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`First prepared direction ${Date.now()}`}})).json();
 await page.goto(`/#/projects/${project.id}`);
 await page.getByRole('button',{name:project.name,exact:true}).click();
 await page.getByRole('textbox',{name:'Instructions for participants',exact:true}).fill('Keep the source silhouette. Record every material choice.');
 await page.getByRole('button',{name:'Save project',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Instructions for participants',exact:true})).not.toBeVisible();
 await page.getByRole('link',{name:'Connections',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Keep working while setup is incomplete.',exact:true})).toBeVisible();
 await expect(page.getByLabel('OpenRouter API key',{exact:true})).toHaveAttribute('type','password');
 await expect(page.getByLabel('OpenRouter API key',{exact:true})).toBeDisabled();
 await page.getByText('Connect a compatible installation',{exact:true}).click();
 await expect(page.getByRole('region',{name:'Local runtime',exact:true})).toContainText('COMFYUI_URL');
 for(const width of [1440,1920]){
  await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:artifact(`connections-${width}.png`)});
 }
 await page.getByRole('link',{name:'Prepared operations',exact:true}).click();
 const operations=page.getByRole('region',{name:'Prepared operations',exact:true});
 await operations.getByRole('searchbox',{name:'Find an operation',exact:true}).fill('Raw · FLUX.2 klein');
 const operation=operations.getByRole('article',{name:'Raw · FLUX.2 klein',exact:true});
 await expect(operation).toContainText('declared · unavailable');
 await operation.getByRole('button',{name:'Check requirements',exact:true}).click();
 await expect(operation.getByRole('region',{name:'Requirements for Raw · FLUX.2 klein',exact:true})).toContainText('disconnected');
 for(const width of [1440,1920]){
  await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:artifact(`prepared-operations-${width}.png`)});
 }
 let lost=true;const bodies:unknown[]=[];
 await page.route(`**/api/crexil/projects/${project.id}/directions`,async route=>{
  bodies.push(route.request().postDataJSON());
  if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(201);await route.abort('failed');}
  else await route.continue();
 });
 await operation.getByRole('button',{name:'Prepare in project',exact:true}).click();
 await expect(operations.getByRole('button',{name:'Confirm same preparation',exact:true})).toBeEnabled();
 await page.reload();
 await operations.getByRole('button',{name:'Confirm same preparation',exact:true}).click();
 const drawer=page.getByRole('region',{name:'Preparation',exact:true});
 await expect(drawer).toBeVisible();
 await expect(drawer.getByRole('spinbutton',{name:'Raw · FLUX.2 klein attempts',exact:true})).toHaveValue('1');
 await drawer.getByRole('button',{name:'Review run',exact:true}).click();
 await expect(drawer.getByRole('region',{name:'Run review',exact:true})).toContainText('Keep the source silhouette. Record every material choice.');
 const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 expect(snapshot.directions).toHaveLength(1);expect(snapshot.turns).toEqual([]);
 expect(snapshot.project.instructions).toBe('Keep the source silhouette. Record every material choice.');
 expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
 expect(errors).toEqual([]);
});

test('account setup keeps a rejected candidate editable and clears it after a confirmed save',async({page})=>{
 // UI-only fixture: the real preview remains offline. Backend tests exercise
 // the guarded setup API against a hermetic account transport separately.
 let configured=false;const key='test-credential-never-sent-to-a-provider';
 const status=()=>({configured,source:configured?'stored':null,masked:configured?'test-…ider':null,env_overrides:false,probe:{configured,ok:configured}});
 await page.route('**/api/crexil/setup',route=>route.fulfill({json:{preview:false,comfyui_url:'http://127.0.0.1:8188',comfyui:{ok:false,error:'Runtime is not running.'},openrouter:status()}}));
 const writes:unknown[]=[];let reject=true;
 await page.route('**/api/setup/openrouter',async route=>{
  expect(route.request().headers()['x-cockpit-setup']).toBe('1');
  if(route.request().method()==='POST'){
   writes.push(route.request().postDataJSON());
   if(reject){reject=false;await route.fulfill({status:400,json:{detail:'Key rejected; the current account is unchanged.'}});return;}
   configured=true;
  }else configured=false;
  await route.fulfill({json:status()});
 });
 await page.goto('/#/connections');
 const account=page.getByRole('region',{name:'Hosted account',exact:true});
 await account.getByLabel('OpenRouter API key',{exact:true}).fill(key);
 await account.getByRole('button',{name:'Check and save account access',exact:true}).click();
 await expect(account.getByRole('alert')).toContainText('current account is unchanged');
 await expect(account.getByLabel('OpenRouter API key',{exact:true})).toHaveValue(key);
 await account.getByRole('button',{name:'Check and save account access',exact:true}).click();
 await expect(account.getByText('Account is reachable.',{exact:true})).toBeVisible();
 await expect(account.getByLabel('OpenRouter API key',{exact:true})).toHaveValue('');
 expect(writes).toEqual([{api_key:key},{api_key:key}]);
 expect(await page.evaluate(value=>JSON.stringify({...localStorage,...sessionStorage}).includes(value),key)).toBe(false);
 await account.getByRole('button',{name:'Remove saved key',exact:true}).click();
 await expect(account.getByText('No account access is configured.',{exact:true})).toBeVisible();
 await page.keyboard.press('Escape');await expect(page).toHaveURL(/#\/projects$/);
});

test('project edits compare concurrent changes and confirm an accepted save after reload',async({page,request})=>{
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Shared project settings ${Date.now()}`}})).json();
 await page.goto(`/#/projects/${project.id}`);
 await page.getByRole('button',{name:project.name,exact:true}).click();
 const form=page.getByRole('form',{name:'Project settings',exact:true});
 await form.getByRole('textbox',{name:'Instructions for participants',exact:true}).fill('Artist instructions retained in this tab.');
 await form.getByRole('spinbutton',{name:'Project spending limit · USD',exact:true}).fill('0.12');
 const agent=async(revision:number,instructions:string,amount:number)=>(await request.patch(`/api/crexil/projects/${project.id}`,{data:{
  ...command(),actor:{kind:'agent',name:'Concurrent participant'},revision,name:project.name,brief:'Agent brief',instructions,
  limits:{attempt_limit:2,spending_limit_usd:amount,spending_policy:'hard_cap'},
 }})).json();
 let current=await agent(project.revision,'Agent instructions to compare.',0.02);
 await expect(form.getByRole('button',{name:'Save project',exact:true})).toBeDisabled();
 await form.getByRole('button',{name:'Compare current project',exact:true}).click();
 const comparison=form.getByRole('region',{name:'Current project comparison',exact:true});
 await expect(comparison).toContainText('Agent instructions to compare.');await expect(comparison).toContainText('$0.02');
 current=await agent(current.revision,'Newer agent instructions.',0.03);
 await expect(comparison.getByRole('button',{name:/Replace revision/})).toBeDisabled();
 await expect(comparison).toContainText('already out of date');
 await form.getByRole('button',{name:'Compare current project',exact:true}).click();
 await expect(comparison).toContainText('Newer agent instructions.');
 let lost=true;const bodies:unknown[]=[];
 await page.route(`**/api/crexil/projects/${project.id}`,async route=>{
  if(route.request().method()!=='PATCH'){await route.continue();return;}
  bodies.push(route.request().postDataJSON());
  if(lost){lost=false;const response=await route.fetch();expect(response.status()).toBe(200);await route.abort('failed');}
  else await route.continue();
 });
 await comparison.getByRole('button',{name:`Replace revision ${current.revision} with my values`,exact:true}).click();
 await expect(form.getByRole('button',{name:'Confirm same project save',exact:true})).toBeEnabled();
 await page.reload();
 await expect(form.getByRole('textbox',{name:'Instructions for participants',exact:true})).toHaveValue('Artist instructions retained in this tab.');
 await expect(form.getByRole('textbox',{name:'Instructions for participants',exact:true})).toBeDisabled();
 await form.getByRole('button',{name:'Confirm same project save',exact:true}).click();
 await expect(form).not.toBeVisible();
 expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
 const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
 expect(snapshot.project.instructions).toBe('Artist instructions retained in this tab.');
 expect(snapshot.project.limits.spending_limit_usd).toBe(0.12);expect(snapshot.project.revision).toBe(current.revision+1);
 expect(snapshot.turns).toEqual([]);
});

test('a late response from closed project settings cannot erase newer local edits',async({page,request})=>{
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Late project save ${Date.now()}`}})).json();
 await page.goto(`/#/projects/${project.id}`);
 const title=page.getByRole('button',{name:project.name,exact:true});
 const form=page.getByRole('form',{name:'Project settings',exact:true});
 await title.click();await form.getByRole('textbox',{name:'Instructions for participants',exact:true}).fill('First accepted instructions.');
 let release!:()=>void,accepted!:()=>void,completed!:()=>void;
 const held=new Promise<void>(resolve=>{release=resolve;}),serverAccepted=new Promise<void>(resolve=>{accepted=resolve;}),oldCompleted=new Promise<void>(resolve=>{completed=resolve;});
 let first=true;
 await page.route(`**/api/crexil/projects/${project.id}`,async route=>{
  if(route.request().method()!=='PATCH'){await route.continue();return;}
  const isFirst=first;first=false;const response=await route.fetch();
  if(isFirst){accepted();await held;}
  await route.fulfill({response});if(isFirst)completed();
 });
 try{
  await form.getByRole('button',{name:'Save project',exact:true}).click();await serverAccepted;
  await title.click();await title.click();
  await form.getByRole('button',{name:'Confirm same project save',exact:true}).click();await expect(form).not.toBeVisible();
  await title.click();await form.getByRole('textbox',{name:'Instructions for participants',exact:true}).fill('New local instructions must survive.');
  release();await oldCompleted;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await expect(form.getByRole('textbox',{name:'Instructions for participants',exact:true})).toHaveValue('New local instructions must survive.');
  await page.reload();await expect(form.getByRole('textbox',{name:'Instructions for participants',exact:true})).toHaveValue('New local instructions must survive.');
  expect((await(await request.get(`/api/crexil/projects/${project.id}`)).json()).project.instructions).toBe('First accepted instructions.');
 }finally{release();}
});

test('a late preparation response cannot clear a newer pending preparation or change its room',async({page,request})=>{
 const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Late operation preparation ${Date.now()}`}})).json();
 await page.goto(`/#/operations?project=${project.id}`);
 const operations=page.getByRole('region',{name:'Prepared operations',exact:true});
 const operation=operations.getByRole('article',{name:'Raw · FLUX.2 klein',exact:true});
 await operations.getByRole('searchbox',{name:'Find an operation',exact:true}).fill('Raw · FLUX.2 klein');
 let release!:()=>void,accepted!:()=>void,completed!:()=>void;
 const held=new Promise<void>(resolve=>{release=resolve;}),serverAccepted=new Promise<void>(resolve=>{accepted=resolve;}),oldCompleted=new Promise<void>(resolve=>{completed=resolve;});
 const bodies:unknown[]=[];
 await page.route(`**/api/crexil/projects/${project.id}/directions`,async route=>{
  const number=bodies.length;bodies.push(route.request().postDataJSON());const response=await route.fetch();expect(response.status()).toBe(201);
  if(number===0){accepted();await held;await route.fulfill({response});completed();}
  else if(number===2)await route.abort('failed');
  else await route.fulfill({response});
 });
 try{
  await operation.getByRole('button',{name:'Prepare in project',exact:true}).click();await serverAccepted;
  await page.getByRole('link',{name:'Connections',exact:true}).click();
  await page.getByRole('link',{name:'Prepared operations',exact:true}).click();
  await operations.getByRole('button',{name:'Confirm same preparation',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});await expect(drawer).toBeVisible();
  await drawer.getByRole('button',{name:'Close · Esc',exact:true}).click();
  await page.getByRole('link',{name:'Operations',exact:true}).click();
  await operations.getByRole('searchbox',{name:'Find an operation',exact:true}).fill('Raw · FLUX.2 klein');
  await operation.getByRole('button',{name:'Prepare in project',exact:true}).click();
  await expect(operations.getByRole('button',{name:'Confirm same preparation',exact:true})).toBeEnabled();
  release();await oldCompleted;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await expect(operations).toBeVisible();await page.reload();
  await operations.getByRole('button',{name:'Confirm same preparation',exact:true}).click();await expect(drawer).toBeVisible();
  expect(bodies).toHaveLength(4);expect(bodies[0]).toEqual(bodies[1]);expect(bodies[2]).toEqual(bodies[3]);
  const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();expect(snapshot.directions).toHaveLength(2);expect(snapshot.turns).toEqual([]);
 }finally{release();}
});
