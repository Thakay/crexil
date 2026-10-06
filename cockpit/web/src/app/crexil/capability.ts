// What an operation can do with the work in front of it, in plain words (law 12:
// badge, never filter). Every configured operation stays listed: one that cannot
// perform the action, or cannot take these sources now, is de-emphasised with its
// reasons and stays selectable, so the artist always sees the whole roster. A
// provider's terms ride on each take; a take whose provider forbids its output as
// input to another model is refused for every engine those terms do not allow,
// with the provider's own reason (law 21).
import { actionView } from './actions';
import type { Material, Operation } from './api';

export type Standing = {tier: 'ready' | 'blocked' | 'notForThis'; reasons: string[]};

type Terms = {no_ai_input?: boolean; allows?: unknown; reason?: unknown};

/** Why this take may not feed an engine reached through `provider`, or null when it may. */
export function termsRefusal(node: Material, provider: string | null | undefined): string | null {
  const terms = node.material?.origin?.terms as Terms | null | undefined;
  if (!terms || terms.no_ai_input !== true) return null;
  if (provider && Array.isArray(terms.allows) && terms.allows.includes(provider)) return null;
  return typeof terms.reason === 'string' && terms.reason
    ? terms.reason : "This take's provider does not allow its output as input to another AI model.";
}

/** The action as a verb phrase: "edit a region", "generate 3D from image". */
export function actionPhrase(action: string): string {
  const label = actionView(action)?.label;
  return label ? label[0].toLowerCase() + label.slice(1) : `perform ${action}`;
}

export function operationStanding(op: Operation, action: string, sources: readonly Material[]): Standing {
  if (!op.actions.includes(action))
    return {tier: 'notForThis', reasons: [`This operation cannot ${actionPhrase(action)}. Its saved inputs remain available.`]};
  const reasons = [...op.reasons];
  if (sources.length > op.source_limit)
    reasons.push(`Accepts ${op.source_limit} source${op.source_limit === 1 ? '' : 's'}. All ${sources.length} sources remain in preparation.`);
  for (const node of sources) {
    const why = termsRefusal(node, op.provider);
    if (why) reasons.push(why);
  }
  if (op.availability === 'unavailable' && reasons.length === 0) reasons.push('Unavailable now.');
  const unique = [...new Set(reasons)];
  return {tier: unique.length ? 'blocked' : 'ready', reasons: unique};
}
