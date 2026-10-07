import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const source=readFileSync(fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url))).toString('base64');
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Lineage verification'}});

test('source buses preserve combined and long ancestry through selection, scrolling and reload',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Connected study ${Date.now()}`}})).json();
  const material=async(name:string,row:number,parents:string[]=[])=>{
    const direction=await(await request.post(`/api/crexil/projects/${project.id}/directions`,{data:{...command(),title:name,row_idx:row,setup:{action:'import'}}})).json();
    const imported=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:name+'.png',data_base64:source,parents,direction_id:direction.id,tool:'Recorded external fixture',explanation:'Reported source relationships for browser verification.'}})).json();
    const selected=await request.put(`/api/crexil/directions/${direction.id}/selection`,{data:{...command(),revision:imported.direction.revision,node_ids:[imported.node.id]}});expect(selected.ok()).toBe(true);
    return {node:imported.node,direction};
  };
  const a=await material('Source A',0),b=await material('Source B',0);
  const combined=await material('Combined',1,[a.node.id,b.node.id]);
  await material('Alternate',1,[b.node.id]);
  const final=await material('Continued',2,[combined.node.id]);
  await material('Long connection',3,[a.node.id,final.node.id]);
  await page.goto(`/#/projects/${project.id}`);
  const svg=page.getByRole('img',{name:'Source connections',exact:true});
  await expect(svg).toHaveAttribute('data-connection-count','4');
  await page.getByRole('button',{name:'Act on Continued.png',exact:true}).click();
  await expect(svg.locator('polyline[data-arrival][data-highlighted]')).toHaveCount(2);
  await page.keyboard.press('Escape');
  for(const width of [1440,1920]){
    await page.setViewportSize({width,height:900});
    await expect(svg).toHaveAttribute('data-connection-count','4');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:artifact(`lineage-${width}.png`)});
  }
  const row=page.locator('[data-lineage-row="0"] [data-lineage-strip]');
  await row.evaluate(element=>{element.scrollLeft=element.scrollWidth;});
  await page.reload();await expect(svg).toHaveAttribute('data-connection-count','4');
  const section=page.getByRole('region',{name:'Source A',exact:true});
  await section.getByRole('button',{name:'Strip ›',exact:true}).click();
  const picker=page.getByRole('region',{name:'Choose displayed takes',exact:true});
  await picker.getByRole('button',{name:'Display Source A.png',exact:true}).click();await picker.getByRole('button',{name:'Apply',exact:true}).click();
  await expect(page.getByRole('region',{name:'Combined',exact:true})).toContainText('not all displayed');
  const snapshot=await(await request.get(`/api/crexil/projects/${project.id}`)).json();
  expect(snapshot.nodes.find((node:{id:string})=>node.id===combined.node.id).parents).toEqual([a.node.id,b.node.id]);
  for(let i=0;i<5;i++)await material(`Extra source ${i+1}`,0);
  await page.setViewportSize({width:1440,height:900});await page.reload();
  await expect.poll(()=>row.evaluate(element=>element.scrollWidth>element.clientWidth)).toBe(true);
  // The canvas has measured the loaded rows: every connection is drawn again.
  await expect(svg).toHaveAttribute('data-connection-count','4');
  await expect(svg.locator(`polyline[data-source="${b.node.id}"]`)).not.toHaveCount(0);
  /** Scrolls the row's strip to its start or its end, and waits for the frame after its scroll
   *  event, in which the lineage canvas measures its anchors again. */
  const scrollRow=(to:'start'|'end')=>row.evaluate((element,to)=>new Promise<void>(resolve=>{
    const left=to==='end'?element.scrollWidth-element.clientWidth:0;
    const measured=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()));
    if(Math.abs(element.scrollLeft-left)<1){measured();return;}
    element.addEventListener('scroll',measured,{once:true});
    element.scrollLeft=left;
  }),to);
  await scrollRow('end');
  await expect(svg.locator(`polyline[data-source="${b.node.id}"]`)).toHaveCount(0);
  await expect(svg.locator(`polyline[data-binding^="${combined.direction.id}:"][data-arrival]`)).toHaveCount(0);
  await scrollRow('start');
  await expect(svg.locator(`polyline[data-binding^="${combined.direction.id}:"][data-arrival]`)).toHaveCount(1);
  await page.screenshot({path:artifact('lineage-overflow-1440.png')});
  expect(errors).toEqual([]);
});
