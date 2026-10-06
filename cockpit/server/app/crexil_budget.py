"""Project, sequence and run limits over the existing slot and charge ledger.

Queued work reserves its recorded amount. Each dispatched attempt stays reserved
until its charge is known, including timeout, cancellation and restart ambiguity.
The ledger preserves earlier attempts when a slot is reused. Tariffs never become
hard bounds, and reducing a limit only gates future dispatches.
"""

from __future__ import annotations

import json
from decimal import Decimal


class BudgetExceeded(ValueError):
    pass


class UnknownPrice(ValueError):
    """A price that cannot be stated exactly. It counts as over every cap (law 22)."""


MICRO_USD = Decimal(1_000_000)


def audio_reservation(tariff: dict | None, measures: dict) -> int:  # noqa: ARG001
    """The published-tariff reservation for one audio attempt, in integer
    micro-dollars, from the engine's tariff and the attempt's measures:
    `characters` (for `1k_characters`), `seconds` (for `second`), and one
    `generation` or `attempt`. A reservation never guesses: an unpriceable
    tariff refuses. An unmetered engine (0 micro-dollars) reserves nothing."""
    if not isinstance(tariff, dict) or type(tariff.get('micro_usd')) is not int or tariff['micro_usd'] < 0:
        raise UnknownPrice('This engine has no published price. An unknown price counts as over the spending limit.')
    if tariff['micro_usd'] == 0:
        return 0
    raise UnknownPrice('Audio prices are not computed yet. An unknown price counts as over the spending limit.')


async def accounting(db, wid: str, *, turn_id: str | None = None,
                     turn_ids: list[str] | None = None) -> dict:
    """Account for a project, one run or an exact set of runs within that project."""
    if turn_id is not None and turn_ids is not None:
        raise ValueError('Choose one run or a set of runs, not both accounting scopes.')
    selected = [turn_id] if turn_id is not None else list(dict.fromkeys(turn_ids)) if turn_ids is not None else None
    clause, args = ('', (wid,)) if selected is None else (
        ' AND turn_id IN (SELECT value FROM json_each(?))', (wid, json.dumps(selected)))
    slots = await db._all('SELECT s.* FROM slots s JOIN turns t ON t.id=s.turn_id '
                         'WHERE t.workspace_id=?' + clause, args)
    events = await db._all('SELECT * FROM events WHERE workspace_id=?' + clause + ' ORDER BY seq', args)
    records, latest = {}, {}
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
    attempts = len(records)
    for slot in slots:
        contract = json.loads(slot['dials_json']).get('crexil', {})
        policy = contract.get('spending_policy', 'published_tariff' if '/' in slot['engine'] else 'hard_cap')
        pending = [{'amount': slot['committed_usd'], 'settled': False}] if slot['status'] in ('queued', 'held') else []
        attempts += len(pending)
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
            errors.append(f'{label} spending limit is ${ceiling}; charged and reserved spending totals ${total}.')
    return errors


async def guard_dispatch(db, slot: dict) -> None:
    turn = await db.get_turn(slot['turn_id'])
    row = await db._one('SELECT limits_json FROM project_details WHERE workspace_id=?', (turn['workspace_id'],))
    limits = json.loads(row['limits_json']) if row else {}
    errors = []
    if limits:
        errors += violations(limits, await accounting(db, turn['workspace_id']))
    setup = turn['dials'].get('crexil', {}).get('setup')
    if setup:
        errors += violations(setup, await accounting(db, turn['workspace_id'], turn_id=turn['id']), label='Run')
    sequence = await db._one('SELECT s.id,s.limits_json FROM sequences s JOIN sequence_runs r ON r.sequence_id=s.id WHERE r.turn_id=?',
                             (turn['id'],))
    if sequence:
        turns = await db._all('SELECT turn_id FROM sequence_runs WHERE sequence_id=? AND turn_id IS NOT NULL', (sequence['id'],))
        errors += violations(json.loads(sequence['limits_json']),
            await accounting(db, turn['workspace_id'], turn_ids=[row['turn_id'] for row in turns]), label='Sequence')
    if errors:
        raise BudgetExceeded(' '.join(errors))
