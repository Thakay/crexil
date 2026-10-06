// The Run button's face. The reviewed price sits on the button that spends it, and
// a costed Run is never the default button (law 6): it wears the hue of money about
// to move instead of the primary frame. A free Run says so.
import { money, type Preparation } from './api';
import type { components } from '../../generated/openapi';

type Review = Pick<components['schemas']['ExecutionReview'], 'ready_attempts' | 'maximum_usd' | 'reserved_usd'>;

export type RunFace = {label: string; paid: boolean};

export function runFace(review: Review, policy: Preparation['spending_policy']): RunFace {
  const n = review.ready_attempts;
  const run = `Run ${n} ${n === 1 ? 'attempt' : 'attempts'}`;
  if (review.maximum_usd !== null && review.maximum_usd !== undefined)
    return review.maximum_usd === 0 ? {label: `${run} · free`, paid: false}
      : {label: `${run} · max ${money(review.maximum_usd)}`, paid: true};
  if (policy === 'published_tariff')
    return review.reserved_usd > 0 ? {label: `${run} · reserves ${money(review.reserved_usd)}`, paid: true}
      : {label: `${run} · free`, paid: false};
  return {label: `${run} · price unknown`, paid: true};
}
