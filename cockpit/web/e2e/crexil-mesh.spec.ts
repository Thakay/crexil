import {test,expect,type APIRequestContext,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {artifact} from './paths';
const source=fileURLToPath(new URL('../../../bench/sources/rc-keep/source.png',import.meta.url));
const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'3D browser verification'}});

function tetrahedron(){
  const positions=[0,1,0,-1,-1,1,1,-1,1,0,-1,-1],indices=[0,1,2,0,2,3,0,3,1,1,3,2];
  const bytes=Buffer.alloc(72);
  positions.forEach((v,i)=>bytes.writeFloatLE(v,i*4));indices.forEach((v,i)=>bytes.writeUInt16LE(v,48+i*2));
  const doc={asset:{version:'2.0'},scene:0,scenes:[{nodes:[0]}],nodes:[{mesh:0}],meshes:[{primitives:[{attributes:{POSITION:0},indices:1,material:0}]}],
    materials:[{pbrMetallicRoughness:{baseColorFactor:[0.65,0.4,0.13,1],metallicFactor:0.5,roughnessFactor:0.5},doubleSided:true}],
    buffers:[{byteLength:72}],bufferViews:[{buffer:0,byteOffset:0,byteLength:48,target:34962},{buffer:0,byteOffset:48,byteLength:24,target:34963}],
    accessors:[{bufferView:0,componentType:5126,count:4,type:'VEC3',min:[-1,-1,-1],max:[1,1,1]},{bufferView:1,componentType:5123,count:12,type:'SCALAR'}]};
  const json=Buffer.from(JSON.stringify(doc)),padded=Buffer.concat([json,Buffer.alloc((4-json.length%4)%4,32)]);
  const header=Buffer.alloc(20);header.write('glTF');header.writeUInt32LE(2,4);header.writeUInt32LE(28+padded.length+bytes.length,8);header.writeUInt32LE(padded.length,12);header.write('JSON',16);
  const bin=Buffer.alloc(8);bin.writeUInt32LE(bytes.length);bin.write('BIN\0',4);
  return Buffer.concat([header,padded,bin,bytes]);
}
async function project(request:APIRequestContext){
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`3D study ${Date.now()}`}})).json();
  const imported=await(await request.post(`/api/crexil/projects/${project.id}/material`,{data:{...command(),filename:'Appearance.png',data_base64:readFileSync(source).toString('base64')}})).json();
  await request.put(`/api/crexil/directions/${imported.direction.id}/selection`,{data:{...command(),revision:0,node_ids:[imported.node.id]}});
  return {project,image:imported.node};
}
const drawer=(page:Page)=>page.getByRole('region',{name:'Preparation',exact:true});
async function chooseOperation(page:Page,name:string,count:number){
  const input=drawer(page).getByRole('spinbutton',{name:`${name} attempts`,exact:true});await input.fill(String(count));return input.locator('../..');
}

test('imported mesh opens the real viewer and a saved reduction preserves invalid controls',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const {project:created}=await project(request);
  await page.goto(`/#/projects/${created.id}`);
  await page.locator('input[type=file]').setInputFiles({name:'Study.glb',mimeType:'model/gltf-binary',buffer:tetrahedron()});
  const section=page.getByRole('region',{name:'Study',exact:true});
  await section.getByRole('button',{name:'Strip ›',exact:true}).click();
  const picker=page.getByRole('region',{name:'Choose displayed takes',exact:true});
  await picker.getByRole('button',{name:'Display Study.glb',exact:true}).click();await picker.getByRole('button',{name:'Apply',exact:true}).click();
  await page.getByRole('button',{name:'Act on Study.glb',exact:true}).dblclick();
  const inspection=page.getByRole('region',{name:'Inspect material',exact:true});
  await expect(inspection.getByRole('button',{name:'Clay C',exact:true})).toBeEnabled({timeout:30000});
  await inspection.getByRole('button',{name:'Wireframe W',exact:true}).click();
  await expect(inspection.getByRole('button',{name:'Wireframe W',exact:true})).toHaveAttribute('aria-pressed','true');
  await inspection.getByRole('button',{name:'Wireframe W',exact:true}).click();
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);await page.screenshot({path:artifact(`mesh-inspection-${width}.png`)});}
  await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Act on Study.glb',exact:true})).toBeFocused();
  await page.getByRole('button',{name:'Reduce mesh',exact:true}).click();
  const op=await chooseOperation(page,'Reduce mesh · gltfpack (meshopt)',1);await op.locator('summary').click();
  await op.getByRole('spinbutton',{name:'Target triangles',exact:true}).fill('99');await expect(drawer(page).getByRole('status')).toHaveText('Saved');
  await drawer(page).getByRole('button',{name:'Review run',exact:true}).click();
  await expect(drawer(page).getByRole('region',{name:'Run review'})).toContainText('between 100 and 4000000');
  await expect(op.getByRole('spinbutton',{name:'Target triangles',exact:true})).toHaveValue('99');
  await op.getByRole('spinbutton',{name:'Target triangles',exact:true}).fill('1000');await op.getByLabel('Force reduction through texture seams',{exact:true}).selectOption('true');
  await expect(drawer(page)).toContainText('damage texture mapping');await expect(drawer(page).getByRole('status')).toHaveText('Saved');
  await page.reload();await page.getByRole('button',{name:'Open preparation: Reduce mesh',exact:true}).click();
  const restored=drawer(page).getByRole('spinbutton',{name:'Reduce mesh · gltfpack (meshopt) attempts',exact:true}).locator('../..');await restored.locator('summary').click();
  await expect(restored.getByRole('spinbutton',{name:'Target triangles',exact:true})).toHaveValue('1000');await expect(restored.getByLabel('Force reduction through texture seams',{exact:true})).toHaveValue('true');
  for(const width of [1440,1920]){await page.setViewportSize({width,height:900});await page.screenshot({path:artifact(`mesh-preparation-${width}.png`)});}
  expect(errors).toEqual([]);
});

test('artist generates a mesh then reduces and textures through shared runs',async({page,request})=>{
  const health=await(await request.get('/api/health')).json();test.skip(Boolean(health.preview),'Scripted engines are used for dispatch verification.');
  const {project:created,image}=await project(request);await page.goto(`/#/projects/${created.id}`);
  await page.getByRole('button',{name:'Act on Appearance.png',exact:true}).click();await page.getByRole('button',{name:'Generate 3D',exact:true}).click();
  await chooseOperation(page,'Generate 3D · TRELLIS.2 4B',1);
  const submit=async()=>{await expect(drawer(page).getByRole('status')).toHaveText('Saved');await drawer(page).getByRole('button',{name:'Review run',exact:true}).click();await drawer(page).getByRole('button',{name:'Run 1 attempts',exact:true}).click();await expect(drawer(page)).not.toBeVisible();};
  const snapshot=async()=>await(await request.get(`/api/crexil/projects/${created.id}`)).json();
  const land=async()=>{const view=await snapshot(),turn=view.turns.at(-1);await request.post(`/api/harness/slots/${turn.slots[0].id}/resolve`,{data:{outcome:'land'}});await expect.poll(async()=>(await snapshot()).turns.at(-1).slots[0].status).toBe('landed');return turn;};
  await submit();await land();
  const showLatest=async()=>{const view=await snapshot(),direction=view.directions.find((d:{turn_ids:string[]})=>d.turn_ids.includes(view.turns.at(-1).id));const section=page.getByRole('region',{name:direction.title,exact:true});await section.getByRole('button',{name:'Strip ›',exact:true}).click();const picker=page.getByRole('region',{name:'Choose displayed takes',exact:true});await picker.getByRole('button',{name:/^Display /}).click();await picker.getByRole('button',{name:'Apply',exact:true}).click();await section.getByRole('button',{name:/^Act on /}).click();};
  await showLatest();await page.getByRole('button',{name:'Reduce mesh',exact:true}).click();
  const op=await chooseOperation(page,'Reduce mesh · gltfpack (meshopt)',1);await op.locator('summary').click();await op.getByRole('spinbutton',{name:'Target triangles',exact:true}).fill('1000');
  await submit();await land();await showLatest();await page.getByRole('button',{name:'Texture mesh',exact:true}).click();
  await drawer(page).getByLabel('Add a source',{exact:true}).selectOption(image.id);await chooseOperation(page,'Texture mesh · Hunyuan3D 2.1',1);
  await expect(drawer(page)).toContainText('Geometry and UVs are not locked');await submit();await land();
  const view=await snapshot();expect(view.turns).toHaveLength(3);
  const child=view.nodes.find((n:{turn_id:string})=>n.turn_id===view.turns[2].id),parent=view.nodes.find((n:{turn_id:string})=>n.turn_id===view.turns[1].id);
  expect(child.parents).toEqual([parent.id,image.id]);expect(child.payload).toBe('3d');
  expect(view.turns.map((t:{slots:{committed_usd:number}[]})=>t.slots[0].committed_usd)).toEqual([0,0,0]);expect(view.turns[1].slots[0].seed).toBeNull();expect(view.turns[2].slots[0].seed).toBeNull();
});
