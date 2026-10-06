import { describe, expect, it } from 'vitest';
import type { Direction, Snapshot } from './api';
import { gridCells, takesPerAttempt } from './cells';
import { cardVerbs } from './verbCard';

const slot = (id: string, attempt = 1, status = 'landed') => ({id, attempt, status, engine: 'flux', turn_id: 't1'});
const node = (id: string, extra: Record<string, unknown> = {}) => ({id, slot_id: null, take: null, payload: 'image', ...extra});
const group = (id: string, slot_id: string, take: number, outputs: {id: string; node_id: string}[], primary?: string) =>
  ({id, slot_id, take, outputs, metadata: primary ? {primary_output_id: primary} : {}});

function snapshot(): Snapshot {
  return {
    nodes: [
      node('import'),
      node('a1', {slot_id: 's1', take: 1}), node('a1-mask', {slot_id: 's1', take: 1}),
      node('b-old', {slot_id: 's2', take: 1}), node('b-new', {slot_id: 's2', take: 2}),
    ],
    turns: [{id: 't1', slots: [slot('s1'), slot('s2', 2), slot('s3', 1, 'failed')]}],
    execution_groups: [
      group('g1', 's1', 1, [{id: 'o-mask', node_id: 'a1-mask'}, {id: 'o-main', node_id: 'a1'}], 'o-main'),
    ],
  } as unknown as Snapshot;
}
const direction = {node_ids: ['import', 'a1', 'a1-mask', 'b-old', 'b-new'], turn_ids: ['t1'], selection: ['a1', 'import']} as unknown as Direction;

describe('gridCells', () => {
  it('shows only the displayed selection on the Filmstrip, in material order', () => {
    const cells = gridCells(direction, snapshot(), false);
    expect(cells.map(cell => cell.key)).toEqual(['import', 'a1']);
    expect(cells[1].slot?.id).toBe('s1');
    expect(cells[1].group?.id).toBe('g1');
    expect(cells[0].slot).toBeNull();
  });
  it('keeps every attempt in place in the grid room', () => {
    const cells = gridCells(direction, snapshot(), true);
    expect(cells.map(cell => cell.key)).toEqual(['import', 'a1', 'b-new', 's3']);
  });
  it('shows the primary output of a multi-file attempt and the current take of a retried slot', () => {
    const cells = gridCells(direction, snapshot(), true);
    expect(cells[1].material?.id).toBe('a1');
    expect(cells[2].material?.id).toBe('b-new');
  });
  it('shows an attempt with no material as the attempt itself', () => {
    const failed = gridCells(direction, snapshot(), true)[3];
    expect(failed.material).toBeNull();
    expect(failed.slot?.status).toBe('failed');
    expect(failed.group).toBeNull();
  });
});

describe('cardVerbs', () => {
  const image = (id: string, terms?: object) => ({id, payload: 'image', material: terms ? {origin: {terms}} : null}) as never;
  const mesh = (id: string) => ({id, payload: '3d'}) as never;
  const audio = (id: string, terms?: object) => ({id, payload: 'audio', material: terms ? {origin: {terms}} : null}) as never;
  const live = (nodes: never[], ops?: never[]) => cardVerbs(nodes, ops).filter(verb => verb.live).map(verb => verb.label);
  const find = (nodes: never[], label: string, ops?: never[]) => cardVerbs(nodes, ops).find(verb => verb.label === label)!;
  it('offers one image its continuation, preparation, regions and 3D', () => {
    expect(live([image('a')])).toEqual(['Continue from image', 'Crop, resize or arrange', 'Edit regions', 'Generate 3D']);
  });
  it('offers several images combination and separate continuations', () => {
    expect(live([image('a'), image('b')])).toEqual(['Combine sources · M', 'Crop, resize or arrange', 'Continue each separately']);
    expect(find([image('a'), image('b')], 'Continue each separately').each).toBe(true);
  });
  it('offers a mesh its reduction and texturing', () => {
    expect(live([mesh('m')])).toEqual(['Reduce mesh', 'Texture mesh']);
  });
  it('textures a mesh from an image with the mesh as the first source', () => {
    const verb = find([image('a'), mesh('m')], 'Texture mesh from image');
    expect(live([image('a'), mesh('m')])).toEqual(['Texture mesh from image']);
    expect(verb.parents.map(parent => (parent as {id: string}).id)).toEqual(['m', 'a']);
  });
  it('offers nothing when nothing is acted on', () => {
    expect(cardVerbs([])).toEqual([]);
  });
  it('keeps every verb in the card: a wrong medium drops to its own group with the reason (law 12)', () => {
    const verbs = cardVerbs([image('a')]);
    expect(verbs).toHaveLength(10);
    expect(find([image('a')], 'Speak with this voice · L')).toMatchObject({live: false, tier: 'notForThis', reason: 'Works on audio; this is an image.'});
    expect(find([image('a')], 'Reduce mesh')).toMatchObject({live: false, tier: 'notForThis', reason: 'Works on meshes; this is an image.'});
    expect(find([mesh('m')], 'Edit regions')).toMatchObject({live: false, tier: 'notForThis', reason: 'Works on images; this is a mesh.'});
    expect(find([audio('v')], 'Generate 3D').reason).toBe('Works on images; this is audio.');
    expect(find([image('a'), mesh('m')], 'Combine sources · M').reason).toBe('Works on images; the selection includes meshes.');
    expect(verbs.map(verb => verb.tier)).toEqual([...verbs.filter(v => v.tier === 'main'), ...verbs.filter(v => v.tier === 'notForThis')].map(v => v.tier));
  });
  it('keeps a verb that fits the medium but not the count in the main group, inert, with the count in words', () => {
    expect(find([image('a'), image('b')], 'Edit regions')).toMatchObject({live: false, tier: 'main', reason: 'Acts on one source; 2 are selected.'});
    expect(find([image('a')], 'Combine sources · M').reason).toBe('Needs two or more sources; one is selected.');
    expect(find([image('a'), image('b')], 'Texture mesh from image').reason).toBe('Needs one mesh and one image together.');
  });
  it('says when no configured operation performs a verb, and never for its own image preparation', () => {
    const ops = [{id: 'raw', actions: ['generate', 'continue'], provider: null}] as never[];
    expect(find([image('a')], 'Generate 3D', ops)).toMatchObject({live: false, reason: 'No configured operation does this yet. Add one in Operations.'});
    expect(find([image('a')], 'Continue from image', ops).live).toBe(true);
    expect(find([image('a')], 'Crop, resize or arrange', []).live).toBe(true);
  });
  it("refuses a verb whose every operation a source's provider terms forbid, in the provider's words", () => {
    const terms = {no_ai_input: true, allows: ['elevenlabs'], reason: "ElevenLabs' terms do not allow its audio as input to another AI model."};
    const ops = [{id: 'qwen', actions: ['continue'], provider: 'comfyui'}] as never[];
    expect(find([image('a', terms)], 'Continue from image', ops)).toMatchObject({live: false, reason: terms.reason});
    const allowed = [...ops, {id: 'eleven', actions: ['continue'], provider: 'elevenlabs'}] as never[];
    expect(find([image('a', terms)], 'Continue from image', allowed).live).toBe(true);
    expect(find([image('a', terms)], 'Crop, resize or arrange', ops).live).toBe(true);
  });
});

describe('gridCells for runs whose attempts return several takes (law 8)', () => {
  const take = (id: string, slot_id: string, output_index: number, extra: Record<string, unknown> = {}) =>
    ({id, slot_id, take: 1, output_index, payload: 'audio', turn_id: 't1', available: true, ...extra});
  const designs = (status = 'landed', blind?: {revealed: boolean}) => ({
    nodes: status === 'landed' ? [take('a3', 'sa', 2), take('a1', 'sa', 0), take('a2', 'sa', 1)] : [],
    turns: [{id: 't1', dials: {crexil: {operations: [{takes_per_attempt: 3}, {takes_per_attempt: 1}]}},
      slots: [slot('sa', 1, status), slot('sb', 1, 'failed')]}],
    execution_groups: status === 'landed' ? [group('g', 'sa', 1, [{id: 'o1', node_id: 'a1'}, {id: 'o2', node_id: 'a2'}, {id: 'o3', node_id: 'a3'}], 'o1')] : [],
    blind_runs: blind ? [{turn_id: 't1', ...blind}] : [],
  }) as unknown as Snapshot;
  const run = {node_ids: ['a1', 'a2', 'a3'], turn_ids: ['t1'], selection: []} as unknown as Direction;

  it('holds room in every slot for the most takes one attempt of the run returns', () => {
    expect(takesPerAttempt(designs().turns[0])).toBe(3);
    expect(takesPerAttempt({dials: {}} as never)).toBe(1);
    expect(takesPerAttempt({dials: {crexil: {operations: [{takes_per_attempt: 40}]}}} as never)).toBe(8);
    expect(gridCells(run, designs(), true).map(cell => cell.span)).toEqual([3, 3]);
  });
  it('shows every take the attempt landed, primary first, then in output order', () => {
    const [landed, failed] = gridCells(run, designs(), true);
    expect(landed.takes.map(node => node.id)).toEqual(['a1', 'a2', 'a3']);
    expect(landed.material?.id).toBe('a1');
    expect(failed.takes).toEqual([]);
  });
  it('withholds every take of a blind run still in flight, and shows them all once it settles', () => {
    const flying = {...designs(), turns: [{...designs().turns[0], slots: [slot('sa'), slot('sb', 1, 'running')]}]} as Snapshot;
    const hidden = gridCells(run, {...flying, blind_runs: [{turn_id: 't1', revealed: false}]} as unknown as Snapshot, true);
    expect(hidden.map(cell => [cell.withheld, cell.takes.length, cell.group])).toEqual([[true, 0, null], [true, 0, null]]);
    const revealed = gridCells(run, {...flying, blind_runs: [{turn_id: 't1', revealed: true}]} as unknown as Snapshot, true);
    expect(revealed[0].takes).toHaveLength(3);
    expect(gridCells(run, designs('landed', {revealed: false}), true)[0].takes).toHaveLength(3);
  });
});

describe('the audio verb on the action card', () => {
  const ELEVEN = {no_ai_input: true, allows: ['elevenlabs'], reason: "ElevenLabs' terms do not allow its audio as input to another AI model."};
  const voice = (id: string, extra: Record<string, unknown> = {}) => ({id, payload: 'audio', available: true,
    audio: {voice: {kind: 'designed', provider: 'voice-runner', generated_voice_id: id}}, material: null, ...extra}) as never;
  const speakers = [{id: 'audio:speak:qwen3-tts', actions: ['speak'], provider: 'voice-runner'}] as never[];
  const verb = (nodes: never[], ops = speakers) => cardVerbs(nodes, ops).find(item => item.label === 'Speak with this voice · L')!;

  it('speaks a line in one take whose recorded voice an engine can use, with that take as the voice', () => {
    const found = verb([voice('v1')]);
    expect(found).toMatchObject({live: true, action: 'speak', tier: 'main'});
    expect(found.parents.map(parent => (parent as {id: string}).id)).toEqual(['v1']);
  });
  it('stays in the card, inert with its reason, for a sound, a lost file or two takes', () => {
    expect(verb([voice('s', {audio: {voice: null}})]).reason).toBe('This take records no voice to speak with.');
    expect(verb([voice('gone', {available: false})]).reason).toBe("This take's audio is unavailable. Its record remains.");
    expect(verb([voice('a'), voice('b')]).reason).toBe('Acts on one source; 2 are selected.');
  });
  it("de-emphasises an ElevenLabs take offered only to the local engine, with the provider's terms (law 12)", () => {
    const eleven = voice('e', {material: {origin: {terms: ELEVEN}}});
    expect(verb([eleven])).toMatchObject({live: false, reason: ELEVEN.reason});
    expect(verb([eleven], [...speakers, {id: 'audio:speak:elevenlabs-v4', actions: ['speak'], provider: 'elevenlabs'}] as never[]).live).toBe(true);
  });
  it("starts nothing from a blind run's take before the reveal, and names no provider", () => {
    const hidden = voice('h', {identity_hidden: true, audio: {voice: null}, material: {origin: {terms: ELEVEN}}});
    const verbs = cardVerbs([hidden], speakers);
    const main = verbs.filter(item => item.tier === 'main');
    expect(main.length).toBeGreaterThan(0);
    for (const item of main) expect(item).toMatchObject({live: false, reason: "This take's run is blind. Reveal it to continue from its takes."});
    expect(verbs.map(item => `${item.label} ${item.reason}`).join(' ')).not.toMatch(/ElevenLabs|voice-runner/);
  });
});
