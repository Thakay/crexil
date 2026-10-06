// The voice director's door: the shared HTTP actions it uses, and nothing else (law 19: the
// server is the only writer). The director writes as `agent:voice-director`; what the artist
// confirms in the panel is written under the artist's name (law 23). The ElevenLabs key never
// reaches the browser: the server starts a session and hands over a conversation token once.
import { fetchClient } from '../../api/client';
import type { components } from '../../generated/openapi';
import { ActionError, command, result, type Actor, type Direction, type Operation, type Preparation, type Snapshot } from '../crexil/api';

export type RunView = components['schemas']['RunView'];
export type Review = components['schemas']['ExecutionReview'];
export type Decision = components['schemas']['DecisionView'];
export type VoiceUsage = components['schemas']['VoiceUsage'];
export type VoiceStarted = components['schemas']['VoiceSessionStarted'];
export type VoiceSession = components['schemas']['VoiceSessionView'];

/** The voice director's own identity on every write it makes. */
export const DIRECTOR: Actor = {kind: 'agent', name: 'voice-director'};

/** A submit's answer: the turn it queued, or why it was refused, with the conflict kind the
 * server names (`spending` means review again, then submit with the new amount). */
export type Submitted =
  | {ok: true; turnId: string}
  | {ok: false; status: number | undefined; conflict: string | null; message: string};

export type Door = {
  operations(): Promise<Operation[]>;
  snapshot(projectId: string): Promise<Snapshot>;
  run(runId: string): Promise<RunView>;
  prepare(projectId: string, title: string, setup: Preparation): Promise<Direction>;
  review(runId: string): Promise<Review>;
  submit(runId: string, revision: number, token: string, actor: Actor): Promise<Submitted>;
  propose(projectId: string, nodeIds: string[], reason: string): Promise<Decision>;
  pick(runId: string, revision: number, nodeIds: string[], reason: string, actor: Actor): Promise<Direction>;
  usage(): Promise<VoiceUsage>;
  start(projectId: string, actor: Actor): Promise<VoiceStarted>;
  end(sessionId: string, elapsedSeconds: number, conversationId: string | null, actor: Actor): Promise<VoiceSession>;
};

export const door: Door = {
  operations: () => result(fetchClient.GET('/api/crexil/operations')),
  snapshot: projectId => result(fetchClient.GET('/api/crexil/projects/{wid}', {params: {path: {wid: projectId}}})),
  run: runId => result(fetchClient.GET('/api/crexil/directions/{did}/run', {params: {path: {did: runId}}})),
  prepare: (projectId, title, setup) => result(fetchClient.POST('/api/crexil/projects/{wid}/directions', {
    params: {path: {wid: projectId}}, body: {...command(DIRECTOR), title, setup}})),
  review: runId => result(fetchClient.GET('/api/crexil/directions/{did}/review', {params: {path: {did: runId}}})),
  async submit(runId, revision, token, actor) {
    const answer = await fetchClient.POST('/api/crexil/directions/{did}/submit', {
      params: {path: {did: runId}}, body: {...command(actor), revision, review_token: token}});
    if (answer.data) return {ok: true, turnId: answer.data.turn_id};
    const status = answer.response?.status;
    return {ok: false, status, conflict: answer.response?.headers.get('X-Crexil-Conflict') ?? null,
      message: new ActionError(answer.error, status).message};
  },
  propose: (projectId, nodeIds, reason) => result(fetchClient.POST('/api/crexil/projects/{wid}/assessments', {
    params: {path: {wid: projectId}}, body: {...command(DIRECTOR), node_ids: nodeIds, reason}})),
  pick: (runId, revision, nodeIds, reason, actor) => result(fetchClient.PUT('/api/crexil/directions/{did}/selection', {
    params: {path: {did: runId}}, body: {...command(actor), revision, node_ids: nodeIds, reason}})),
  usage: () => result(fetchClient.GET('/api/crexil/voice-sessions/usage')),
  start: (projectId, actor) => result(fetchClient.POST('/api/crexil/voice-sessions', {
    body: {...command(actor), project_id: projectId, connection: 'webrtc'}})),
  end: (sessionId, elapsedSeconds, conversationId, actor) => result(fetchClient.POST('/api/crexil/voice-sessions/{sid}/end', {
    params: {path: {sid: sessionId}}, body: {...command(actor), elapsed_s: elapsedSeconds, conversation_id: conversationId}})),
};
