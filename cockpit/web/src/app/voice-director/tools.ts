// The voice director's six client tools. Each answers the agent with facts, ids, labels and
// reasons as JSON text, never media or an address (guard.ts). The agent prepares runs and
// proposes picks as `agent:voice-director`; it never submits paid work, picks, reveals,
// delivers or changes limits (laws 15, 22, 23). A paid run becomes a card the artist confirms
// with a click; a run on free local engines starts on the agent's request.
import { blankPreparation, label as nodeLabel, message, type Material, type Operation, type Preparation } from '../crexil/api';
import { DIRECTOR, type Door, type RunView } from './door';
import { answer, plain } from './guard';

/** The tools, by the names `tools/voice-director/agent.json` gives them. */
export const TOOL_NAMES = ['list_engines', 'get_project_summary', 'prepare_run', 'get_run_status', 'play_take', 'propose_pick'] as const;
export type ToolName = (typeof TOOL_NAMES)[number];
export type Tool = (parameters: Record<string, unknown>) => Promise<string>;

const VERBS = {'voice-design': 'voice design', speak: 'speech', sfx: 'sound effect'} as const;
type Verb = keyof typeof VERBS;
const MAX_ENGINES = 4;
const MAX_COUNT = 4;

/** A prepared paid run the artist confirms or discards in the panel. */
export type RunCard = {
  id: string; runId: string; title: string; action: Verb; prompt: string;
  engines: {label: string; count: number}[];
  capUsd: number; reservedUsd: number; maximumUsd: number | null;
  /** Reserved by the published price: the final provider charge is not capped. */
  published: boolean;
  ready: boolean; errors: string[]; revision: number; token: string; attempts: number;
};

/** Takes the director proposes, which the artist may pick with a click. */
export type ProposalCard = {
  id: string; runId: string; takes: {label: string; nodeId: string}[]; reason: string; decisionId: string;
};

/** What the panel does with the tools' results. */
export type Handoff = {
  runCard(card: RunCard): void;
  ranFree(run: {runId: string; title: string; attempts: number}): void;
  proposal(card: ProposalCard): void;
  /** Plays a take for the artist; resolves to a plain reason when it cannot. */
  play(nodeId: string, seconds?: number): Promise<string | null>;
};

const refuse = (words: string) => answer({error: words});
const words = (value: unknown, limit: number) => typeof value === 'string' ? value.trim().slice(0, limit) : '';
const excerpt = (value: string, limit: number) => value.length > limit ? `${value.slice(0, limit - 1).trimEnd()}…` : value;
const seconds = (value: number | null | undefined) => value === null || value === undefined ? undefined : Math.round(value * 10) / 10;

/** A take of a run by its label ("2B", "2B.2"); a bare letter means the latest submission. */
export function findTake(view: RunView, wanted: string) {
  const name = wanted.trim().toUpperCase();
  const takes = view.cells.flatMap(cell => cell.takes.map(take => ({take, cell})));
  const exact = takes.find(({take}) => take.label.toUpperCase() === name);
  if (exact || !/^[A-Z](\.\d+)?$/.test(name) || !view.submissions.length) return exact ?? null;
  const latest = Math.max(...view.submissions.map(submission => submission.idx));
  return takes.find(({take}) => take.label.toUpperCase() === `${latest}${name}`) ?? null;
}

const audioByLabel = (nodes: Material[], wanted: string) =>
  nodes.find(node => node.payload === 'audio' && nodeLabel(node).toUpperCase() === wanted.trim().toUpperCase()) ?? null;

function controlsFor(op: Operation, parameters: Record<string, unknown>): Record<string, unknown> {
  const keys = new Set(op.inputs.map(input => input.key));
  const controls: Record<string, unknown> = {};
  const preview = words(parameters.preview_line, 1000);
  if (keys.has('preview_text') && preview) controls.preview_text = preview;
  if (keys.has('duration_seconds') && typeof parameters.duration_seconds === 'number') controls.duration_seconds = parameters.duration_seconds;
  return controls;
}

export function createTools(projectId: string, door: Door, handoff: Handoff): Record<ToolName, Tool> {
  const guarded = (run: (parameters: Record<string, unknown>) => Promise<string>): Tool => async parameters => {
    try {
      return await run(parameters ?? {});
    } catch (error) {
      return answer({error: plain(message(error))});
    }
  };

  return {
    list_engines: guarded(async parameters => {
      const payload = parameters.payload === 'image' ? 'image' : 'audio';
      const engines = (await door.operations()).filter(op => op.output_payload === payload).slice(0, 24);
      return answer({payload, engines: engines.map(op => ({
        id: op.id, label: op.label, actions: op.actions,
        available: op.availability !== 'unavailable' && op.reasons.length === 0,
        why_not: op.reasons.slice(0, 2).map(plain), runs: op.execution_location,
        price: plain(op.cost_basis), takes_per_attempt: op.takes_per_attempt,
      }))});
    }),

    get_project_summary: guarded(async () => {
      const snapshot = await door.snapshot(projectId);
      const labels = new Map(snapshot.nodes.map(node => [node.id, nodeLabel(node)]));
      const runs = snapshot.directions.filter(run => !run.set_aside && !run.sequence_template)
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 3);
      const limit = snapshot.project.limits.spending_limit_usd;
      return answer({
        project: plain(snapshot.project.name), brief: plain(excerpt(snapshot.project.brief, 400)),
        spending_limit_usd: typeof limit === 'number' ? limit : null, spent_usd: snapshot.charged_usd,
        runs: runs.map(run => ({run_id: run.id, title: plain(run.title), state: run.state,
          action: typeof run.setup.action === 'string' ? run.setup.action : 'generate',
          takes: run.node_ids.length, picked: run.selection.map(id => labels.get(id) ?? 'a take')})),
      });
    }),

    prepare_run: guarded(async parameters => {
      const action = parameters.action;
      if (typeof action !== 'string' || !Object.hasOwn(VERBS, action)) return refuse('The action must be voice-design, speak or sfx.');
      const verb = action as Verb;
      const prompt = words(parameters.prompt, 2000);
      if (!prompt) return refuse(verb === 'speak' ? 'Speech needs the line to say.' : 'Describe what to make.');
      const wanted = Array.isArray(parameters.engines) ? parameters.engines as Record<string, unknown>[] : [];
      if (!wanted.length || wanted.length > MAX_ENGINES) return refuse(`Choose 1 to ${MAX_ENGINES} engines from list_engines.`);
      const cap = parameters.spending_limit_usd;
      if (typeof cap !== 'number' || !Number.isFinite(cap) || cap < 0) return refuse('Give the run a cap in US dollars, and say it to the artist.');
      if (verb === 'voice-design' && !words(parameters.preview_line, 1000)) {
        return refuse('Voice design needs a preview line of 100 to 1000 characters for the takes to speak.');
      }
      const operations = await door.operations();
      const choices: {op: Operation; count: number}[] = [];
      for (const item of wanted) {
        const op = operations.find(candidate => candidate.id === item?.id);
        if (!op) return refuse(`${String(item?.id ?? 'That')} is not an engine id. Use list_engines.`);
        if (!op.actions.includes(verb)) return refuse(`${op.label} cannot do ${VERBS[verb]}.`);
        const count = item.count;
        if (typeof count !== 'number' || !Number.isInteger(count) || count < 1 || count > MAX_COUNT) {
          return refuse(`Each engine takes 1 to ${MAX_COUNT} attempts.`);
        }
        choices.push({op, count});
      }
      const references: Preparation['references'] = [];
      if (verb === 'speak') {
        const voice = words(parameters.voice_take, 24);
        if (!voice) return refuse('Speech needs a voice take; name it by its label, like 2B.');
        const node = audioByLabel((await door.snapshot(projectId)).nodes, voice);
        if (!node) return refuse(`There is no audio take ${voice} in this project.`);
        references.push({node_id: node.id, contribution: ''});
      }
      // A free local engine reserves nothing; anything else is reserved by its published price.
      const paid = choices.some(({op}) => op.maximum_usd_per_attempt !== 0);
      const attempts = choices.reduce((sum, {count}) => sum + count, 0);
      const setup: Preparation = {...blankPreparation(), action: verb, prompt, references,
        operations: choices.map(({op, count}) => ({operation_id: op.id, revision: op.revision, count, controls: controlsFor(op, parameters)})),
        attempt_limit: attempts, spending_limit_usd: cap, spending_policy: paid ? 'published_tariff' : 'hard_cap',
        blind: parameters.blind === true};
      const title = `Voice director · ${VERBS[verb]} · ${excerpt(prompt, 36)}`;
      const run = await door.prepare(projectId, title, setup);
      const review = await door.review(run.id);
      const errors = review.errors.map(plain);
      if (!paid && review.ready && review.reserved_usd === 0) {
        const sent = await door.submit(run.id, review.revision, review.token, DIRECTOR);
        if (!sent.ok) return answer({status: 'not_started', run_id: run.id, error: plain(sent.message)});
        handoff.ranFree({runId: run.id, title, attempts: review.ready_attempts});
        return answer({status: 'running', run_id: run.id, attempts: review.ready_attempts, cost: 'free: local engines only'});
      }
      handoff.runCard({id: crypto.randomUUID(), runId: run.id, title, action: verb, prompt,
        engines: choices.map(({op, count}) => ({label: op.label, count})),
        capUsd: cap, reservedUsd: review.reserved_usd, maximumUsd: review.maximum_usd, published: paid,
        ready: review.ready, errors, revision: review.revision, token: review.token, attempts: review.ready_attempts});
      return answer({
        status: review.ready ? 'awaiting_confirmation' : 'not_ready', run_id: run.id,
        attempts: review.ready_attempts, reserved_usd: review.reserved_usd, cap_usd: cap, errors,
        next: review.ready ? 'The artist runs it with a click in the panel. Tell them the price.'
          : 'Nothing was sent. Fix what the errors say, then prepare again.',
      });
    }),

    get_run_status: guarded(async parameters => {
      const runId = words(parameters.run_id, 64);
      if (!runId) return refuse('Name the run by its run_id.');
      const view = await door.run(runId);
      return answer({
        run_id: view.run_id, title: plain(view.title), state: view.state, blind: view.blind,
        revealed: view.submissions.some(submission => submission.revealed), summary: plain(view.summary),
        cap_usd: view.cap_usd, reserved_usd: view.reserved_usd, charged_usd: view.charged_usd,
        cells: view.cells.slice(0, 32).map(cell => ({
          cell: cell.label, state: cell.state, engine: cell.identity_hidden ? 'hidden' : cell.engine,
          retrying: cell.retrying, cause: cell.cause ? plain(cell.cause) : undefined,
          takes: cell.takes.map(take => ({take: take.label, payload: take.payload, chosen: take.chosen,
            available: take.available, seconds: seconds(take.audio?.duration_s),
            words: take.audio?.text ? plain(excerpt(take.audio.text, 160)) : undefined})),
        })),
      });
    }),

    play_take: guarded(async parameters => {
      const runId = words(parameters.run_id, 64);
      const wanted = words(parameters.take, 24);
      if (!runId || !wanted) return refuse('Name the run and the take, like 2B.');
      const view = await door.run(runId);
      const found = findTake(view, wanted);
      if (!found) {
        const known = view.cells.flatMap(cell => cell.takes.map(take => take.label)).slice(0, 16);
        return refuse(`There is no take ${wanted} in this run.${known.length ? ` Its takes are ${known.join(', ')}.` : ''}`);
      }
      const {take} = found;
      if (take.payload !== 'audio') return refuse(`${take.label} is not audio. The artist can open it in the grid.`);
      if (!take.available) return refuse(`${take.label}'s file is not available, so it cannot play.`);
      const length = seconds(take.audio?.duration_s);
      const failed = await handoff.play(take.node_id, take.audio?.duration_s);
      if (failed) return refuse(plain(failed));
      return answer({played: true, take: take.label, seconds: length,
        next: 'You do not hear takes. Ask the artist what they heard.'});
    }),

    propose_pick: guarded(async parameters => {
      const runId = words(parameters.run_id, 64);
      const reason = words(parameters.reason, 300);
      const wanted = Array.isArray(parameters.takes) ? parameters.takes.filter((item): item is string => typeof item === 'string') : [];
      if (!runId || !wanted.length) return refuse('Name the run and the takes you propose.');
      if (!reason) return refuse('Give the artist a short reason for the proposal.');
      const view = await door.run(runId);
      const takes: {label: string; nodeId: string}[] = [];
      for (const label of wanted.slice(0, 8)) {
        const found = findTake(view, label);
        if (!found) return refuse(`There is no take ${label} in this run.`);
        takes.push({label: found.take.label, nodeId: found.take.node_id});
      }
      const decision = await door.propose(view.project_id, takes.map(take => take.nodeId), reason);
      handoff.proposal({id: crypto.randomUUID(), runId: view.run_id, takes, reason, decisionId: decision.id});
      return answer({proposed: true, decision_id: decision.id, takes: takes.map(take => take.label),
        next: 'Your proposal is recorded. The artist picks with a click in the panel.'});
    }),
  };
}
