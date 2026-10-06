import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const image=readFileSync(fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url))).toString('base64');
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Backup verification'}});

test('backup downloads and restores a separate project after lost acceptance and reload',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Portable study ${Date.now()}`}})).json();
  const source=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Original.png',data_base64:image}})).json();
  await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Returned.png',data_base64:image,parents:[source.node.id],tool:'Krita',explanation:'Reported hand edit'}});
  await page.goto(`/#/projects/${project.id}`);await page.getByRole('button',{name:'Back up',exact:true}).click();
  const backupRoom=page.getByRole('region',{name:'Project backup',exact:true});
  const receipt=page.waitForResponse(response=>response.url().endsWith(`/projects/${project.id}/backups`)&&response.request().method()==='POST');
  await backupRoom.getByRole('button',{name:'Create backup',exact:true}).click();
  const backup=await(await receipt).json();expect(backup.records.nodes).toBe(2);
  const bytes=await(await request.get(`/api/crexil/backups/${backup.id}/file`)).body();
  await expect(backupRoom.getByRole('link',{name:/Download/})).toBeVisible();
  await page.screenshot({path:artifact('backup-1440.png')});
  await page.goto('/#/projects');await page.getByRole('button',{name:'Restore project',exact:true}).click();
  const room=page.getByRole('region',{name:'Restore project',exact:true});
  await room.getByLabel('Project backup file',{exact:true}).setInputFiles({name:'project.crexil.zip',mimeType:'application/zip',buffer:bytes});
  await room.getByRole('button',{name:'Upload and review backup',exact:true}).click();
  const restoreButton=room.getByRole('button',{name:'Restore as new project',exact:true});
  await expect(restoreButton).toBeEnabled();
  const restoredName=project.name+' restored';
  await room.getByLabel('Restored project name',{exact:true}).fill(restoredName);
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);await page.screenshot({path:artifact(`restore-${width}.png`)});}
  const bodies:unknown[]=[];
  await page.route('**/api/crexil/archive-uploads/*/restore',async route=>{
    bodies.push(route.request().postDataJSON());
    if(bodies.length===1){await route.fetch();await route.abort('failed');return;}
    await route.continue();
  });
  await restoreButton.click();await expect(room.getByRole('button',{name:'Retry same restore',exact:true})).toBeVisible();
  await page.reload();await page.getByRole('button',{name:'Restore project',exact:true}).click();
  await room.getByRole('button',{name:'Retry same restore',exact:true}).click();
  await expect(page).toHaveURL(/#\/projects\/[^/]+$/);
  expect(bodies).toHaveLength(2);expect(bodies[0]).toEqual(bodies[1]);
  const projects=await(await request.get('/api/crexil/projects')).json();
  const copies=projects.filter((p:{name:string})=>p.name===restoredName);expect(copies).toHaveLength(1);
  expect(copies[0].id).not.toBe(project.id);
  const copy=await(await request.get(`/api/crexil/projects/${copies[0].id}`)).json();
  const mapping=copy.restoration.identities.nodes;
  expect(copy.nodes).toHaveLength(2);
  expect(copy.nodes.find((n:{parents:string[]})=>n.parents.length).parents).toEqual([mapping[source.node.id]]);
  expect((await(await request.get(`/api/crexil/projects/${project.id}`)).json()).nodes).toHaveLength(2);
  await page.reload();await expect(page.getByRole('button',{name:restoredName,exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});
