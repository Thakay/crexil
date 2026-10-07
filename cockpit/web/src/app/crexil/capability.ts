// What an operation can do with the work in front of it, in plain words (law 12:
// badge, never filter). Every configured operation stays listed: one that cannot
// perform the action, or cannot take these sources now, is de-emphasised with its
// reasons and stays selectable, so the artist always sees the whole roster. A
// provider's terms ride on each take; a take whose provider forbids its output as
// input to another model is refused for every engine those terms do not allow,
// with the provider's own reason (law 21). A speech engine that speaks only with some
// providers' voices says so for any other voice, before it is chosen. A take from a blind
// run that is not yet revealed is refused with one reason that names no provider, since
// its terms would tell its engine.
import { actionView } from './actions';
import { count, isHidden, type Material, type Operation } from './api';
import { BLIND_SOURCE } from './blind';

export type Standing = {tier: 'ready' | 'blocked' | 'notForThis'; reasons: string[]};

type SourceRole = NonNullable<Operation['sources']>[number];

type Terms = {no_ai_input?: boolean; allows?: unknown; reason?: unknown};

/** Why this take may not feed an engine reached through `provider`, or null when it may. */
export function termsRefusal(node: Material, provider: string | null | undefined): string | null {
  const terms = node.material?.origin?.terms as Terms | null | undefined;
  if (!terms || terms.no_ai_input !== true) return null;
  if (provider && Array.isArray(terms.allows) && terms.allows.includes(provider)) return null;
  return typeof terms.reason === 'string' && terms.reason
    ? terms.reason : "This take's provider does not allow its output as input to another AI model.";
}

/** Why this take may not be the voice a speech role takes: its role takes only voices
 *  designed or saved at some providers (`voices`), and the take's recorded voice comes from
 *  none of them. Null when the role takes any voice its terms allow. */
export function voiceRefusal(node: Material, role: SourceRole | undefined): string | null {
  const rule = role?.voices;
  if (!rule) return null;
  const provider = (node.audio?.voice as {provider?: unknown} | null | undefined)?.provider;
  return typeof provider === 'string' && rule.providers.includes(provider) ? null : rule.reason;
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
    reasons.push(`Accepts ${count(op.source_limit, 'source')}. ${sources.length === 1 ? 'The source remains' : `All ${sources.length} sources remain`} in preparation.`);
  for (const [index, node] of sources.entries()) {
    if (isHidden(node)) reasons.push(BLIND_SOURCE);
    else reasons.push(...[termsRefusal(node, op.provider), voiceRefusal(node, op.sources?.[index])].filter((why): why is string => Boolean(why)));
  }
  if (op.availability === 'unavailable' && reasons.length === 0) reasons.push('Unavailable now.');
  const unique = [...new Set(reasons)];
  return {tier: unique.length ? 'blocked' : 'ready', reasons: unique};
}
