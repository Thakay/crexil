// One character brief carried through image, voice, line reads, a sound and 3D on the
// scripted executors. The brief's runs start from its words on one row; the line reads and
// the mesh continue on the next row from the takes chosen above them; the Filmstrip then
// shows one family, each line in the hue of the medium its start makes, and the record
// keeps each take's words as typed and as submitted.
import {test,expect,type Page} from '@playwright/test';
import {artifact} from './paths';

const command=()=>({request_id:crypto.randomUUID(),actor:{kind:'human',name:'Character journey'}});
const BRIEF='A stout dwarf blacksmith, braided copper beard, soot-dark leather apron, forge-lit, full body.';
const VOICE='Gruff, warm and low, a little out of breath from the forge: a stout dwarf blacksmith.';
const PREVIEW='Steel remembers every strike, little one. Mind the anvil, mind the fire, and the iron will mind you back.';
const LINE='Bring me the ore, and be quick about it.';
const SOUND='Heavy iron-shod boots on a stone forge floor, slow and deliberate.';
// The lineage hues as the browser resolves them: mesh violet and audio rose.
const VIOLET='rgb(180, 140, 247)', ROSE='rgb(226, 131, 198)';
type Node={id:string;turn_idx:number|null;slot_pos:number|null;output_index:number;parents:string[];verb:string};
type Direction={id:string;title:string;row_idx:number;selection:string[];setup:{prompt:string}};

async function shots(page:Page,name:string){
  for(const [width,height] of [[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});
    await page.screenshot({path:artifact(`${name}-${width}.png`)});
  }
  await page.setViewportSize({width:1440,height:900});
}

test('one character brief carries through image, voice, line reads, a sound and 3D, and the Filmstrip shows one family',async({page,request})=>{
  test.setTimeout(240_000);
  const health=await(await request.get('/api/health')).json();
  test.skip(Boolean(health.preview),'The isolated preview cannot run engines; this journey uses the scripted executors.');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const project=await(await request.post('/api/crexil/projects',{data:{...command(),name:`Dwarf blacksmith ${Date.now()}`,
    brief:"The forge level's smith, in every medium."}})).json();
  const snapshot=async():Promise<{directions:Direction[];nodes:Node[];turns:{idx:number;slots:{id:string;status:string}[]}[]}>=>
    (await request.get(`/api/crexil/projects/${project.id}`)).json();
  const drawer=page.getByRole('region',{name:'Preparation',exact:true});
  const row=(n:number)=>page.locator(`[data-lineage-row="${n}"]`);
  const choose=async(label:string,count:number)=>{
    const operation=drawer.locator('[data-standing="ready"]').filter({has:page.getByText(label,{exact:true})});
    await operation.getByRole('spinbutton',{name:`${label} attempts`,exact:true}).fill(String(count));
    return operation;
  };
  const run=async(face:string)=>{
    await expect(drawer.getByRole('status')).toHaveText('Saved');
    await drawer.getByRole('button',{name:'Review run',exact:true}).click();
    await expect(drawer.getByRole('region',{name:'Run review'})).toContainText('Ready to submit');
    await drawer.getByRole('button',{name:face,exact:true}).click();
    await expect(drawer).not.toBeVisible();
  };
  const land=async(idx:number,outcome:object={outcome:'land'})=>{
    const turn=(await snapshot()).turns.find(item=>item.idx===idx)!;
    for(const slot of turn.slots)await request.post(`/api/harness/slots/${slot.id}/resolve`,{data:outcome});
    await expect.poll(async()=>(await snapshot()).turns.find(item=>item.idx===idx)!.slots.every(slot=>slot.status==='landed')).toBe(true);
  };
  // A take by its place in the grid: its run, its cell and its output.
  const take=(view:{nodes:Node[]},run:number,pos:number,output=0)=>
    view.nodes.find(node=>node.turn_idx===run&&node.slot_pos===pos&&node.output_index===output)!;
  const chosen=async()=>(await snapshot()).directions.reduce((sum,direction)=>sum+direction.selection.length,0);
  // S selects the take in the open grid room; the reason follows, or Enter skips it.
  const pick=async(face:string,name:string,reason='')=>{
    const before=await chosen();
    await page.getByRole('button',{name:face,exact:true}).focus();
    await page.keyboard.press('s');
    const why=page.getByRole('textbox',{name:`Why ${name}? Optional`,exact:true});
    await expect(why).toBeFocused();
    if(reason){await why.fill(reason);await page.keyboard.press('Enter');await expect(page.getByText(`Reason kept with ${name}.`,{exact:true})).toBeVisible();}
    else await page.keyboard.press('Enter');
    await expect.poll(chosen).toBe(before+1);
  };
  const leave=async()=>{await page.getByRole('button',{name:'Esc · Project',exact:true}).click();};

  // The character brief fans out to the image engines; one take is chosen with a reason.
  await page.goto(`/#/projects/${project.id}`);
  await page.getByRole('button',{name:'+ New direction · N',exact:true}).first().click();
  await drawer.getByRole('textbox',{name:'Direction name',exact:true}).fill('Dwarf blacksmith');
  await drawer.getByRole('textbox',{name:'Prompt',exact:true}).fill(BRIEF);
  await choose('Raw · FLUX.2 klein',2);
  await run('Run 2 attempts · free');
  await land(0,{outcome:'land',color:[166,98,52]});
  await row(0).getByRole('button',{name:'Grid ›',exact:true}).click();
  await pick('Act on 0B','0B','Reads as a smith from across the room.');
  await leave();

  // A voice designed from the same brief: a new section on its row starts from its words, edited.
  await row(0).getByRole('button',{name:'+ Section',exact:true}).click();
  await drawer.getByRole('combobox',{name:'Action'}).selectOption('voice-design');
  const offer=drawer.getByRole('group',{name:'Start from the words of',exact:true});
  await expect(offer.getByRole('button')).toHaveCount(1);
  await expect(offer.getByRole('button',{name:'Start from the words of Dwarf blacksmith',exact:true})).toHaveAccessibleDescription(BRIEF);
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await shots(page,'d11-carry-brief');
  await offer.getByRole('button',{name:'Start from the words of Dwarf blacksmith',exact:true}).click();
  const description=drawer.getByRole('textbox',{name:'Voice description',exact:true});
  await expect(description).toHaveValue(BRIEF);
  await expect(offer).not.toBeVisible();
  await description.fill(VOICE);
  await drawer.getByRole('textbox',{name:'Direction name',exact:true}).fill('Blacksmith voice');
  const designer=await choose('Design voice · Qwen3-TTS 1.7B',1);
  await designer.getByText('Inputs, cost and route').click();
  await designer.getByRole('textbox',{name:'Preview line'}).fill(PREVIEW);
  await run('Run 1 attempt · free');
  await land(1);
  await row(0).getByRole('button',{name:'Grid ›',exact:true}).click();
  await pick('Act on 1A, audio, 4.0 s','1A');
  await pick('Act on 1A.3, audio, 4.0 s','1A.3','The grit sits under the words.');
  await leave();

  // The chosen voices read the same line: one line read per voice, the line carried from the first.
  await page.getByRole('button',{name:'Act on 1A, audio, 4.0 s',exact:true}).click();
  await page.getByRole('button',{name:'Act on 1A.3, audio, 4.0 s',exact:true}).click({modifiers:['Shift']});
  await page.getByRole('region',{name:'Actions on material',exact:true}).getByRole('button',{name:'Speak with each voice',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Line to speak',exact:true})).toHaveValue('');
  await expect(drawer.getByRole('group',{name:'Start from the words of',exact:true}).getByRole('button')).toHaveCount(2);
  await drawer.getByRole('textbox',{name:'Line to speak',exact:true}).fill(LINE);
  await choose('Speak · Qwen3-TTS 1.7B',1);
  await run('Run 1 attempt · free');
  await land(2);
  await page.getByRole('button',{name:'Open preparation: Spoken line',exact:true}).click();
  const lines=drawer.getByRole('group',{name:'Start from the words of',exact:true});
  await expect(lines.getByRole('button')).toHaveText([/^Spoken line/,/^Dwarf blacksmith/,/^Blacksmith voice/]);
  await expect(drawer.getByRole('status')).toHaveText('Saved');
  await shots(page,'d11-carry-line');
  await lines.getByRole('button',{name:'Start from the words of Spoken line',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Line to speak',exact:true})).toHaveValue(LINE);
  await choose('Speak · Qwen3-TTS 1.7B',1);
  await run('Run 1 attempt · free');
  await land(3);

  // A sound from the same brief, priced by its published tariff before it runs.
  await row(0).getByRole('button',{name:'+ Section',exact:true}).click();
  await drawer.getByRole('combobox',{name:'Action'}).selectOption('sfx');
  await drawer.getByRole('group',{name:'Start from the words of',exact:true}).getByRole('button',{name:'Start from the words of Dwarf blacksmith',exact:true}).click();
  await expect(drawer.getByRole('textbox',{name:'Sound description',exact:true})).toHaveValue(BRIEF);
  await drawer.getByRole('textbox',{name:'Sound description',exact:true}).fill(SOUND);
  await drawer.getByRole('textbox',{name:'Direction name',exact:true}).fill('Blacksmith footsteps');
  await choose('Sound effect · ElevenLabs Sound Effects',2);
  await drawer.getByRole('button',{name:'Budget from published tariffs',exact:true}).click();
  await drawer.getByRole('spinbutton',{name:'Spending limit · USD'}).fill('1');
  await run('Run 2 attempts · reserves $0.008');
  await land(4);
  await row(0).getByRole('button',{name:'Grid ›',exact:true}).click();
  await pick('Act on 4A, audio, 4.0 s','4A','Heaviest step; the iron rings.');
  await leave();

  // The chosen image continues into 3D through the mesh lane, beside the line reads.
  await page.getByRole('button',{name:'Act on 0B',exact:true}).click();
  await page.getByRole('region',{name:'Actions on material',exact:true}).getByRole('button',{name:'Generate 3D',exact:true}).click();
  await choose('Generate 3D · TRELLIS.2 4B',1);
  await run('Run 1 attempt · free');
  await land(5);
  await row(1).getByRole('button',{name:'Grid ›',exact:true}).click();
  await pick('Act on 2A, audio, 4.0 s','2A');
  await pick('Act on 3A, audio, 4.0 s','3A');
  await pick('Act on 5A','5A','Holds the silhouette.');
  await leave();

  // One family: the brief's sections on one row, their continuations on the next, one
  // line per start in the hue of what it makes.
  const view=await snapshot();
  const titles=(n:number)=>view.directions.filter(direction=>direction.row_idx===n).map(direction=>direction.title);
  expect(titles(0)).toEqual(['Dwarf blacksmith','Blacksmith voice','Blacksmith footsteps']);
  expect(titles(1)).toEqual(['Spoken line','Spoken line','Generate 3D']);
  const svg=page.getByRole('img',{name:'Source connections',exact:true});
  await expect(svg).toHaveAttribute('data-connection-count','3');
  for(const direction of view.directions.filter(item=>item.row_idx===1)){
    const arrival=svg.locator(`polyline[data-arrival][data-binding^="${direction.id}:"]`);
    await expect(arrival).toHaveCount(1);
    expect(await arrival.evaluate(line=>getComputedStyle(line).stroke)).toBe(direction.title==='Generate 3D'?VIOLET:ROSE);
  }
  const reads=view.nodes.filter(node=>node.verb==='speak');
  const [image,first,third]=[take(view,0,1),take(view,1,0),take(view,1,0,2)];
  expect(reads.map(node=>node.parents[0]).sort()).toEqual([first.id,third.id].sort());
  for(const parent of [image,first,third])await expect(svg.locator(`polyline[data-source="${parent.id}"]`)).not.toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await shots(page,'d11-family');

  // The record keeps the carried words as typed and as submitted; both voices read the same line.
  for(const node of reads){
    const record=await(await request.get(`/api/crexil/material/${node.id}/record`)).json();
    expect(record.request.setup.prompt).toBe(LINE);
    expect(record.submitted.text).toBe(LINE);
  }
  expect(view.directions.find(direction=>direction.title==='Blacksmith voice')!.setup.prompt).toBe(VOICE);
  await page.getByRole('button',{name:'Act on 3A, audio, 4.0 s',exact:true}).dblclick();
  const inspection=page.getByRole('region',{name:'Inspect material',exact:true});
  const words=inspection.getByRole('heading',{name:'Line to speak',exact:true});
  await expect(words.locator('xpath=following-sibling::p[1]')).toHaveText(LINE);
  await expect(words.locator('xpath=following-sibling::p[2]')).toHaveText('As typed and as submitted.');
  await shots(page,'d11-record-line');
  await page.keyboard.press('Escape');
  await expect(inspection).not.toBeVisible();
  expect(errors).toEqual([]);
});
