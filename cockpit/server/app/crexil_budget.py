"""Project, sequence and run limits over the existing slot and charge ledger.

Queued work reserves its recorded amount. Each dispatched attempt stays reserved
until its charge is known, including timeout, cancellation and restart ambiguity.
The ledger preserves earlier attempts when a slot is reused. Tariffs never become
hard bounds, and reducing a limit only gates future dispatches.
"""

from __future__ import annotations

import json
import math
from decimal import ROUND_CEILING, Decimal


class BudgetExceeded(ValueError):
    pass


class UnknownPrice(ValueError):
    """A price that cannot be stated exactly. It counts as over every cap (law 22)."""


MICRO_USD = Decimal(1_000_000)
UNKNOWN = 'An unknown price counts as over the spending limit.'

# A tariff unit, the measure it counts, the plain name of that measure, and how
# many measured units the published price covers.
TARIFF_UNITS = {'1k_characters': ('characters', 'billed characters', 1000),
                'second': ('seconds', 'duration in seconds', 1),
                'generation': ('generations', 'number of generations', 1),
                'attempt': ('attempts', 'number of attempts', 1)}


def money(value) -> str:
    """An amount in dollars as every line Crexil writes says it: cents when it has
    no finer part ("$0.60", "$1.00"), else every digit it has ("$0.018",
    "$0.86508"), never rounded into another amount."""
    amount = Decimal(str(value))
    return f'${amount:.2f}' if amount == amount.quantize(Decimal('0.01')) else f'${amount.normalize():f}'


def audio_reservation(tariff: dict | None, measures: dict) -> int:
    """The published-tariff price of one audio attempt, in integer micro-dollars,
    from the engine's tariff and the attempt's measures: `characters` (for
    `1k_characters`), `seconds` (for `second`), `generations` or `attempts`.

    `micro_usd` is the list price. No other price a tariff may carry, such as a
    temporary sale price, is read, so a reservation never comes in under the
    charge. A fractional micro-dollar rounds up. An unmetered engine (0
    micro-dollars) reserves nothing. A paid price never guesses: an unknown
    unit, or a measure that is missing, not a positive number, or not a whole
    count where one is billed, refuses (law 22).

    The same arithmetic prices a reported usage, so an adapter turns the
    characters or seconds a provider says it billed into the take's charge."""
    if not isinstance(tariff, dict) or type(tariff.get('micro_usd')) is not int or tariff['micro_usd'] < 0:
        raise UnknownPrice(f'This engine has no published price. {UNKNOWN}')
    if tariff['micro_usd'] == 0:
        return 0
    if tariff.get('unit') not in TARIFF_UNITS:
        raise UnknownPrice(f"This price is quoted per {tariff.get('unit')!r}, which Crexil cannot count. {UNKNOWN}")
    key, noun, per = TARIFF_UNITS[tariff['unit']]
    value = (measures or {}).get(key)
    whole = key != 'seconds'
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or (whole and not isinstance(value, int))
            or (isinstance(value, float) and not math.isfinite(value)) or value <= 0):
        raise UnknownPrice(f'The request does not state its {noun}, so its price is unknown. {UNKNOWN}')
    exact = Decimal(str(value)) * tariff['micro_usd'] / per
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


async def accounting(db, wid: str, *, turn_id: str | None = None,
                     turn_ids: list[str] | None = None) -> dict:
    """Account for a project, one run or an exact set of runs within that project.

    `attempts` counts requested attempts: each dispatch and each queued or held
    slot, except that an automatic retry (`slot.retrying`) is the same requested
    attempt as the try it repeats, so a visible retry never counts as another
    attempt against an attempt limit. A manual reroll is a new request. Money is
    counted per dispatch either way: each send reserves until its charge is known."""
    if turn_id is not None and turn_ids is not None:
        raise ValueError('Choose one run or a set of runs, not both accounting scopes.')
    selected = [turn_id] if turn_id is not None else list(dict.fromkeys(turn_ids)) if turn_ids is not None else None
    clause, args = ('', (wid,)) if selected is None else (
        ' AND turn_id IN (SELECT value FROM json_each(?))', (wid, json.dumps(selected)))
    slots = await db._all('SELECT s.* FROM slots s JOIN turns t ON t.id=s.turn_id '
                         'WHERE t.workspace_id=?' + clause, args)
    events = await db._all('SELECT * FROM events WHERE workspace_id=?' + clause + ' ORDER BY seq', args)
    records, latest = {}, {}
    retrying = set()          # slots whose next dispatch repeats a refused try
    requested = 0
    charged = Decimal(0)
    for event in events:
        payload = json.loads(event['payload_json'])
        sid = event['slot_id']
        if event['charged_usd'] is not None:
            charged += Decimal(str(event['charged_usd']))
        if event['type'] == 'slot.dispatched':
            take = payload['attempt']
            latest[sid] = take
            records[(sid, take)] = {'amount': payload.get('committed_usd'), 'settled': False}
            if sid not in retrying:
                requested += 1
            retrying.discard(sid)
        elif event['type'] == 'slot.retrying':
            retrying.add(sid)
        elif event['type'] == 'slot.rerolled':
            retrying.discard(sid)
        attempt = payload.get('attempt', payload.get('take', latest.get(sid)))
        key = (sid, attempt)
        if event['type'] in ('slot.charged', 'slot.charge_verified') and 'attempt' not in payload and latest.get(sid, 0) > 1:
            # Older ambiguous corrections still contribute to charged totals,
            # but cannot prove which outstanding attempt has settled.
            continue
        if key in records and (event['charged_usd'] is not None or event['type'] == 'slot.charge_verified'):
            records[key]['settled'] = True
    reserved = Decimal(0)
    unresolved = unbounded = tariff_attempts = 0
    attempts = requested
    for slot in slots:
        contract = json.loads(slot['dials_json']).get('crexil', {})
        policy = contract.get('spending_policy', 'published_tariff' if '/' in slot['engine'] else 'hard_cap')
        pending = [{'amount': slot['committed_usd'], 'settled': False}] if slot['status'] in ('queued', 'held') else []
        attempts += len(pending) if slot['id'] not in retrying else 0
        pending += [record for (sid, _), record in records.items() if sid == slot['id'] and not record['settled']]
        for record in pending:
            amount = record['amount']
            tariff_attempts += int(policy == 'published_tariff')
            if amount is None:
                unbounded += 1
            elif amount > 0:
                reserved += Decimal(str(amount))
                unresolved += 1
    return {'attempts': attempts, 'charged_usd': float(charged), 'reserved_usd': float(reserved),
            'unresolved_attempts': unresolved, 'unbounded_attempts': unbounded,
            'tariff_attempts': tariff_attempts}


def violations(limits: dict, current: dict, *, attempts: int = 0, reserved: float = 0,
               tariff: bool = False, label: str = 'Project') -> list[str]:
    errors = []
    count = limits.get('attempt_limit')
    if count is not None and current['attempts'] + attempts > count:
        errors.append(f'{label} attempt limit is {count}; used and reserved attempts total {current["attempts"] + attempts}.')
    ceiling = limits.get('spending_limit_usd')
    if ceiling is not None:
        if current['unbounded_attempts']:
            errors.append(f'{label} has requests with unknown commitments. Resolve their charges before adding paid work.')
        if limits.get('spending_policy', 'hard_cap') == 'hard_cap' and (tariff or current['tariff_attempts']):
            errors.append(f'{label} requires a hard cap. Published tariffs cannot meet that promise.')
        total = Decimal(str(current['charged_usd'])) + Decimal(str(current['reserved_usd'])) + Decimal(str(reserved))
        if total > Decimal(str(ceiling)):
            errors.append(f'{label} spending limit is {money(ceiling)}; charged and reserved spending totals {money(total)}.')
    return errors


async def holding_limits(db, slot: dict) -> list[tuple[str, str]]:
    """The limits that would hold this attempt at dispatch now, each as its
    scope ('project', 'run' or 'sequence') and its plain line."""
    turn = await db.get_turn(slot['turn_id'])
    row = await db._one('SELECT limits_json FROM project_details WHERE workspace_id=?', (turn['workspace_id'],))
    limits = json.loads(row['limits_json']) if row else {}
    found = []
    if limits:
        found += [('project', line) for line in violations(limits, await accounting(db, turn['workspace_id']))]
    setup = turn['dials'].get('crexil', {}).get('setup')
    if setup:
        found += [('run', line) for line in violations(
            setup, await accounting(db, turn['workspace_id'], turn_id=turn['id']), label='Run')]
    sequence = await db._one('SELECT s.id,s.limits_json FROM sequences s JOIN sequence_runs r ON r.sequence_id=s.id WHERE r.turn_id=?',
                             (turn['id'],))
    if sequence:
        turns = await db._all('SELECT turn_id FROM sequence_runs WHERE sequence_id=? AND turn_id IS NOT NULL', (sequence['id'],))
        found += [('sequence', line) for line in violations(json.loads(sequence['limits_json']),
            await accounting(db, turn['workspace_id'], turn_ids=[row['turn_id'] for row in turns]), label='Sequence')]
    return found


async def guard_dispatch(db, slot: dict) -> None:
    found = await holding_limits(db, slot)
    if found:
        raise BudgetExceeded(' '.join(line for _, line in found))
