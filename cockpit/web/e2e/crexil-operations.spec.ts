import { test, expect } from '@playwright/test';

const command = () => ({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Operation browser verification'}});
const graph = {
  load:{class_type:'LoadImage',inputs:{image:'unbound.png'}},
  resize:{class_type:'ImageScale',inputs:{image:['load',0],width:512,height:512,upscale_method:'nearest-exact',crop:'disabled'}},
  show:{class_type:'PreviewImage',inputs:{images:['resize',0]}},
};

test('artist saves an operation interface and returns through shared test preparation', async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const created=await request.post('/api/crexil/projects',{data:{...command(),name:`Operation journey ${Date.now()}`}});
  const project=await created.json();
  const name=`Resize study ${Date.now()}`;
  await page.goto(`/#/operations?project=${project.id}`);
  await page.getByRole('button',{name:'+ Import operation',exact:true}).click();
  await page.getByLabel('Name',{exact:true}).fill(name);
  await page.getByLabel('ComfyUI API export or prepared description').setInputFiles({name:'resize-api.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(graph))});
  await page.getByRole('button',{name:'Save imported preparation'}).click();
  await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'+ Expose control',exact:true}).click();
  const control=page.locator('article').filter({has:page.getByLabel('Key',{exact:true})});
  await control.getByLabel('Key',{exact:true}).fill('width');
  await control.getByLabel('Label',{exact:true}).fill('Output width');
  await control.getByRole('combobox',{name:'Type',exact:true}).selectOption('int');
  await control.getByRole('combobox',{name:'Graph input',exact:true}).selectOption(JSON.stringify(['resize','width']));
  await control.getByLabel('Default',{exact:true}).fill('512');
  await control.getByLabel('min',{exact:true}).fill('64');
  await control.getByLabel('max',{exact:true}).fill('1024');
  await control.getByLabel('step',{exact:true}).fill('64');
  await page.getByRole('button',{name:'+ Source input',exact:true}).click();
  const source=page.locator('article').filter({has:page.getByRole('combobox',{name:'Kind',exact:true})});
  await source.getByLabel('Label',{exact:true}).fill('Source image');
  await source.getByLabel('Role',{exact:true}).fill('structure');
  await source.getByRole('combobox',{name:'Graph input',exact:true}).selectOption(JSON.stringify(['load','image']));
  await page.getByRole('button',{name:'+ Result mapping',exact:true}).click();
  await page.getByRole('combobox',{name:'Result node',exact:true}).selectOption('show');
  await page.getByRole('button',{name:'Save preparation',exact:true}).click();
  await expect(page.getByRole('status')).toHaveText('Saved');
  await page.getByRole('button',{name:'Save and inspect requirements',exact:true}).click();
  await expect(page.getByText('Runtime discovery is disconnected in this isolated preview.',{exact:true}).first()).toBeVisible();
  await page.getByLabel('Description',{exact:true}).fill('An unfinished local resize interface.');
  // Leaving saves preparation through the same revision contract.
  await page.getByRole('link',{name:'Return to project',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}$`));
  await page.getByRole('link',{name:'Operations',exact:true}).click();
  const prepared=page.getByRole('region',{name:'Prepared operations',exact:true});
  await prepared.getByRole('searchbox',{name:'Find an operation',exact:true}).fill(name);
  const savedOperation=prepared.getByRole('article',{name,exact:true});
  await savedOperation.getByRole('button',{name:'Check requirements',exact:true}).click();
  await savedOperation.getByRole('link',{name:'Open registration ›',exact:true}).click();
  await expect(page.getByLabel('Description',{exact:true})).toHaveValue('An unfinished local resize interface.');
  await page.getByLabel('Description',{exact:true}).fill('Saved while returning through browser history.');
  await page.goBack();await expect(prepared).toBeVisible();
  await page.goForward();await expect(page.getByLabel('Description',{exact:true})).toHaveValue('Saved while returning through browser history.');
  await page.getByRole('button',{name:'Prepare recorded test',exact:true}).click();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('combobox',{name:'Action',exact:true})).toHaveValue('custom');
  await expect(drawer.getByLabel('Attempt limit',{exact:true})).toHaveValue('1');
  await expect(drawer.getByText(name,{exact:true})).toBeVisible();
  const snapshot=await (await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(snapshot.turns).toHaveLength(0);
  expect(snapshot.directions[0].setup.purpose).toBe('operation_test');
  expect(snapshot.directions[0].setup.operations).toHaveLength(1);
  expect(errors).toEqual([]);
});
