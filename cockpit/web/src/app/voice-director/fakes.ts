// Test support for the voice director: a fake door with realistic answers, including the
// addresses and file routes a careless answer could leak, so the tests can prove none does.
import { vi } from 'vitest';
import type { Direction, Material, Operation, Snapshot } from '../crexil/api';
import type { Door, Review, RunView, VoiceSession, VoiceUsage } from './door';

export function operation(partial: Partial<Operation> & {id: string; actions: string[]}): Operation {
  return {label: partial.id, attempt_max: 4, availability: 'unverified', cost_basis: 'Unmetered local operation',
    data_destination: 'http://127.0.0.1:8190', evidence: 'declared', execution_location: 'Local voice runner',
    inputs: [], maximum_usd_per_attempt: 0, output_payload: 'audio', pinned: false, price_usd: null, reasons: [],
    revision: `rev-${partial.id}`, source_limit: 0, takes_per_attempt: 1, ...partial} as Operation;
}

export const OPERATIONS: Operation[] = [
  operation({id: 'audio:voice-design:elevenlabs-voice-design', label: 'Voice design · ElevenLabs', actions: ['voice-design'],
    execution_location: 'Hosted provider', data_destination: 'https://api.elevenlabs.io', maximum_usd_per_attempt: null,
    cost_basis: 'Published tariff, 0.08 US dollars per 1,000 characters', takes_per_attempt: 3,
    inputs: [{key: 'preview_text'}, {key: 'loudness'}]}),
  operation({id: 'audio:voice-design:qwen3-tts', label: 'Voice design · Qwen3-TTS', actions: ['voice-design'], takes_per_attempt: 3,
    reasons: ['The local voice runner is not answering at http://127.0.0.1:8190. Start it with tools/tts/runner.py.'],
    inputs: [{key: 'preview_text'}, {key: 'language'}]}),
  operation({id: 'audio:speak:qwen3-tts', label: 'Speech · Qwen3-TTS', actions: ['speak'], inputs: [{key: 'language'}]}),
  operation({id: 'audio:sfx:elevenlabs-sfx', label: 'Sound effect · ElevenLabs', actions: ['sfx'], execution_location: 'Hosted provider',
    data_destination: 'https://api.elevenlabs.io', maximum_usd_per_attempt: null, inputs: [{key: 'duration_seconds'}]}),
  operation({id: 'raw-sdxl', label: 'SDXL', actions: ['generate'], output_payload: 'image'}),
];

export function material(partial: Partial<Material> & {id: string}): Material {
  return {payload: 'audio', turn_idx: 1, slot_pos: 1, output_index: 0, engine: 'qwen3-tts', available: true,
    material: null, ...partial} as Material;
}

export function snapshot(): Snapshot {
  return {
    project: {id: 'p1', name: 'Dwarf blacksmith', brief: 'A gruff dwarf who forges for a living.', limits: {spending_limit_usd: 1},
      created_at: '', updated_at: '', dials: {}, instructions: '', revision: 3, slug: 'dwarf', viewport: {}},
    charged_usd: 0.031, event_seq: 9, execution_groups: [], turns: [],
    nodes: [material({id: 'voice-1b'}), material({id: 'image-1a', payload: 'image', slot_pos: 0})],
    directions: [
      {id: 'run-old', title: 'Voices', state: 'submitted', setup: {action: 'voice-design'}, selection: ['voice-1b'],
        node_ids: ['voice-1b'], set_aside: false, sequence_template: false, updated_at: '2026-10-05T10:00:00Z'},
      {id: 'run-aside', title: 'Set aside', state: 'draft', setup: {}, selection: [], node_ids: [], set_aside: true,
        sequence_template: false, updated_at: '2026-10-05T12:00:00Z'},
    ] as unknown as Direction[],
  } as unknown as Snapshot;
}

export function runView(partial: Partial<RunView> = {}): RunView {
  const take = (label: string, nodeId: string, extra: object = {}) => ({node_id: nodeId, label, payload: 'audio', verb: 'speak',
    available: true, set_aside: false, chosen: false, identity_hidden: false,
    audio: {duration_s: 3.46, loudness_lufs: -19.2, text: 'Steel remembers every strike.', voice: null}, ...extra});
  const cell = (label: string, position: number, extra: object = {}) => ({slot_id: `slot-${label}`, turn_id: 'turn-2', label,
    position, state: 'landed', attempt: 1, retrying: false, kind: null, cause: null, detail: null, engine: 'qwen3-tts',
    seed: 7, committed_usd: 0, charged_usd: 0, elapsed_ms: 5600, collectable: false, identity_hidden: false, takes: [], ...extra});
  return {
    run_id: 'run-2', project_id: 'p1', title: 'Lines', state: 'submitted', revision: 4, action: 'speak',
    prompt: 'Steel remembers every strike.', blind: false, cap_usd: 0.5, reserved_usd: 0, charged_usd: 0,
    submissions: [{turn_id: 'turn-2', idx: 2, created_at: '', blind: false, revealed: false,
      control: {} as RunView['submissions'][number]['control']}],
    cells: [
      cell('2A', 0, {takes: [take('2A', 'take-2a')]}),
      cell('2B', 1, {takes: [take('2B', 'take-2b')]}),
      cell('2C', 2, {state: 'failed', kind: 'unreachable', takes: [],
        cause: 'The local voice runner is not answering at http://127.0.0.1:8190. Start it with tools/tts/runner.py.'}),
      cell('2D', 3, {takes: [take('2D', 'image-2d', {payload: 'image', verb: 'generate', audio: null})]}),
    ],
    selection: ['take-2a'], decisions: [], summary: '3 landed, 1 failed. The voice runner is not answering at http://127.0.0.1:8190.',
    event_seq: 10, ...partial,
  } as unknown as RunView;
}

export const review = (partial: Partial<Review> = {}): Review => ({attempts: 2, blocked_attempts: 0, direction_id: 'run-new',
  errors: [], maximum_usd: null, ready: true, ready_attempts: 2, reserved_micro_usd: 31000, reserved_usd: 0.031,
  revision: 1, snapshot: {}, token: 'token-1', ...partial});

export const usage = (partial: Partial<VoiceUsage> = {}): VoiceUsage => ({ready: true, reasons: [], cap_seconds: 1800,
  session_max_seconds: 300, used_seconds: 120, reserved_seconds: 0, left_seconds: 1680, usd_per_minute: 0.08,
  open_sessions: [], ...partial});

export const session = (partial: Partial<VoiceSession> = {}): VoiceSession => ({session_id: 'voice-1', project_id: 'p1',
  actor: {kind: 'human', name: 'Artist'}, started_at: '', max_seconds: 300, connection: 'webrtc', ended_at: null,
  ended_by: null, elapsed_s: null, conversation_id: null, ...partial});

/** A door whose every action is a spy with a realistic answer. */
export function fakeDoor(overrides: Partial<Door> = {}): {[K in keyof Door]: ReturnType<typeof vi.fn> & Door[K]} {
  const door: Door = {
    operations: async () => OPERATIONS,
    snapshot: async () => snapshot(),
    run: async () => runView(),
    prepare: async (_projectId, title, setup) => ({id: 'run-new', title, setup, revision: 1} as unknown as Direction),
    review: async () => review(),
    submit: async () => ({ok: true, turnId: 'turn-new'}),
    propose: async () => ({id: 'decision-1'} as Awaited<ReturnType<Door['propose']>>),
    pick: async (runId) => ({id: runId} as unknown as Direction),
    usage: async () => usage(),
    start: async () => ({session: session(), conversation_token: 'conversation-token-secret', signed_url: null, usage: usage({reserved_seconds: 300})}),
    end: async (sessionId, elapsed) => session({session_id: sessionId, elapsed_s: elapsed, ended_at: 'now'}),
    ...overrides,
  };
  return Object.fromEntries(Object.entries(door).map(([name, action]) => [name, vi.fn(action)])) as never;
}
