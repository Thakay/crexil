"""Durable project, run and attempt execution controls on the existing event ledger.

Pause gates future dispatch while leaving running work and unrelated holds intact.
Cancellation freezes current slot attempts and skips unsent work atomically with
its attributed intent; cancelling one attempt records the same intent for that
attempt alone. The scheduler owns interruption and result collection. Resume never
revokes cancellation of an earlier attempt or releases holds itself.

Resuming the engine that holds one attempt records the bench it lifts by the event
that installed it; the scheduler lifts that bench once, so a replayed or restarted
request never resumes twice. A runtime's own bench (a key, a runner, the card)
lifts only once its cause is gone; an engine's own bench (A9's repeat, a model
that was down, a timeout) lifts on request. Every control names its participant
(law 23), and an attempt's answer names no engine of a blind run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Literal

from .crexil_store import Conflict

Scope = Literal['project', 'run', 'sequence']


class ExecutionPaused(ValueError):
    def __init__(self, scope: Scope, target_id: str):
        self.scope = scope
        self.target_id = target_id
        super().__init__(f'This {scope} is paused. Resume it before dispatching more work.')


class ExecutionCancelled(ValueError):
    """An automatic retry still belongs to the attempt whose stop was requested."""


async def _workspace(db, scope: Scope, target_id: str) -> str:
    if scope == 'project':
        target = await db.get_workspace(target_id)
        if target:
            return target_id
    elif scope == 'run':
        target = await db._one('SELECT workspace_id FROM turns WHERE id=?', (target_id,))
        if target:
            return target['workspace_id']
    elif scope == 'sequence':
        target = await db._one('SELECT workspace_id FROM sequences WHERE id=?', (target_id,))
        if target:
            return target['workspace_id']
    else:
        raise ValueError('Execution controls require a project, run or sequence scope.')
    raise LookupError(f'This {scope} could not be found.')


# The workspace predicate uses the existing events index. All cancel events
# remain relevant: a later pause/resume must not erase an earlier stop request.
_CANCEL_MATCH = """
 EXISTS (
   SELECT 1 FROM events e, json_each(e.payload_json, '$.targets') target
   WHERE e.workspace_id=t.workspace_id AND e.type='execution.control'
     AND json_type(e.payload_json, '$.archive_origin') IS NULL
     AND json_extract(e.payload_json, '$.action')='cancel'
     AND json_extract(target.value, '$.slot_id')=s.id
     AND json_extract(target.value, '$.attempt')=s.attempt
 )
"""


async def _cancellations(db, workspace_id: str | None = None,
                         turn_id: str | None = None, sequence_id: str | None = None) -> list[dict]:
    query = ("SELECT s.id AS slot_id, s.attempt FROM slots s JOIN turns t ON t.id=s.turn_id "
             "WHERE s.status IN ('running','late') AND " + _CANCEL_MATCH)
    args = []
    if workspace_id is not None:
        query += ' AND t.workspace_id=?'
        args.append(workspace_id)
    if turn_id is not None:
        query += ' AND s.turn_id=?'
        args.append(turn_id)
    if sequence_id is not None:
        query += ' AND s.turn_id IN (SELECT turn_id FROM sequence_runs WHERE sequence_id=?)'
        args.append(sequence_id)
    rows = await db._all(query + ' ORDER BY s.created_at,s.pos,s.id', args)
    return [dict(row) for row in rows]


async def state(db, scope: Scope, target_id: str) -> dict:
    async with db._locked():
        wid = await _workspace(db, scope, target_id)
        event = await db._one(
            "SELECT seq,ts,payload_json FROM events WHERE workspace_id=? "
            "AND type='execution.control' AND json_type(payload_json,'$.archive_origin') IS NULL "
            "AND json_extract(payload_json,'$.scope')=? "
            "AND json_extract(payload_json,'$.target_id')=? ORDER BY seq DESC LIMIT 1",
            (wid, scope, target_id))
        payload = json.loads(event['payload_json']) if event else {}
        pending = await _cancellations(db, wid, target_id if scope == 'run' else None,
                                        target_id if scope == 'sequence' else None)
        return {'scope': scope, 'target_id': target_id, 'workspace_id': wid,
                'revision': event['seq'] if event else 0,
                'paused': payload.get('paused', False), 'cancel_requested': len(pending),
                'last_action': payload.get('action'), 'actor': payload.get('actor'),
                'reason': payload.get('reason', ''), 'requested_at': event['ts'] if event else None}


async def record(db, scope: Scope, target_id: str, body) -> dict:
    """Record intent inside the caller's attributed, idempotent command transaction."""
    values = body.model_dump() if hasattr(body, 'model_dump') else dict(body)
    action = values['action']
    if action not in ('pause', 'resume', 'cancel'):
        raise ValueError('Execution controls require pause, resume or cancel.')
    async with db._tx():
        before = await state(db, scope, target_id)
        if before['revision'] != values['revision']:
            raise Conflict('Execution controls changed in another view. Refresh before trying again.')
        paused = {'pause': True, 'resume': False, 'cancel': before['paused']}[action]
        reason = values.get('reason', '')
        query = ("SELECT s.id,s.attempt,s.status FROM slots s JOIN turns t ON t.id=s.turn_id "
                 "WHERE t.workspace_id=? AND s.status IN ('queued','held','running','late')")
        args = [before['workspace_id']]
        if scope == 'run':
            query += ' AND s.turn_id=?'
            args.append(target_id)
        elif scope == 'sequence':
            query += ' AND s.turn_id IN (SELECT turn_id FROM sequence_runs WHERE sequence_id=?)'
            args.append(target_id)
        slots = await db._all(query + ' ORDER BY s.created_at,s.pos,s.id', args)
        targets = [{'slot_id': slot['id'], 'attempt': slot['attempt']} for slot in slots] if action == 'cancel' else []
        await db._append_event('execution.control', workspace_id=before['workspace_id'],
            turn_id=target_id if scope == 'run' else None,
            payload={'scope': scope, 'target_id': target_id, 'action': action,
                     'paused': paused, 'actor': values['actor'], 'reason': reason, 'targets': targets})
        for slot in slots:
            if action == 'pause' and slot['status'] == 'queued':
                await db.transition_slot(slot['id'], 'held', payload={'pause': True, 'reason': reason})
            elif action == 'cancel' and slot['status'] in ('queued', 'held'):
                await db.transition_slot(slot['id'], 'skipped',
                    payload={'cancel': True, 'reason': reason, 'attempt': slot['attempt']})
        if action == 'cancel' and scope in ('project', 'sequence'):
            from .crexil_sequences import cancel_pending
            sequences = await db._all('SELECT id FROM sequences WHERE ' +
                ('id=?' if scope == 'sequence' else "workspace_id=? AND state='active'"), (target_id,))
            for sequence in sequences:
                await cancel_pending(db, sequence['id'], values['actor'], reason)
        return await state(db, scope, target_id)


async def guard_dispatch(db, slot: dict) -> None:
    async with db._locked():
        # Intentional reroll clears retry_of. An automatic retry may not use
        # its next attempt number to escape cancellation of the failed one.
        if slot.get('retry_of') and await cancel_requested(db, slot):
            raise ExecutionCancelled('Cancellation was requested before this automatic retry.')
        wid = await _workspace(db, 'run', slot['turn_id'])
        for scope, target_id in (('project', wid), ('run', slot['turn_id'])):
            if (await state(db, scope, target_id))['paused']:
                raise ExecutionPaused(scope, target_id)
        sequence = await db._one('SELECT s.id,s.state FROM sequences s JOIN sequence_runs r ON r.sequence_id=s.id WHERE r.turn_id=?',
                                 (slot['turn_id'],))
        if sequence:
            if sequence['state'] == 'cancelled':
                raise ExecutionCancelled('This sequence was cancelled before the request could start.')
            if (await state(db, 'sequence', sequence['id']))['paused']:
                raise ExecutionPaused('sequence', sequence['id'])


async def cancel_requested(db, slot: dict) -> bool:
    row = await db._one(
        'SELECT 1 FROM slots s JOIN turns t ON t.id=s.turn_id '
        'WHERE s.id=? AND s.attempt=? AND ' + _CANCEL_MATCH,
        (slot['id'], slot['attempt']))
    return row is not None


async def cancellations(db) -> list[dict]:
    """Current running or late attempts with durable cancellation intent."""
    return await _cancellations(db)


# --- one attempt: cancel it, or resume the engine that holds it ---

# A runtime's own benches: each stands until that runtime answers again.
RUNTIME_KINDS = frozenset({'unreachable', 'dead_key', 'no_key', 'gpu_held'})
# A blind run's hidden attempt hears no provider, runner or card named.
HIDDEN_NOT_READY = "Its engine is not ready yet, so its attempts stay held. Check Connections, then resume."
PAID_RESUME = ("Resuming paid work is the artist's act: this would send held attempts that reserve money. "
               'An agent may resume free local work; the artist resumes the rest.')


def _values(body) -> dict:
    return body.model_dump() if hasattr(body, 'model_dump') else dict(body)


def _money(value) -> str:
    """An amount as the record holds it: cents when it has no finer part,
    else every digit it has, never rounded into another amount."""
    amount = Decimal(str(value))
    return f'${amount:.2f}' if amount == amount.quantize(Decimal('0.01')) else f'${amount.normalize():f}'


def _attempts(count: int) -> str:
    return f"{count} held attempt{'' if count == 1 else 's'}"


async def _attempt(db, slot_id: str, attempt: int) -> dict:
    """The slot, at the attempt its participant saw."""
    slot = await db.get_slot(slot_id)
    if not slot:
        raise LookupError('This attempt was not found. Refresh the project.')
    if slot['attempt'] != attempt:
        raise Conflict('This attempt changed since it was read. Refresh it, then decide again.')
    return slot


async def _record_attempt(db, slot: dict, action: str, values: dict, **facts) -> None:
    """One attempt's control, on its own slot's record, with the targets a
    run's cancel lists, so durable stops and recovery read it the same way."""
    await db._append_event('execution.control', **await db._slot_ids(slot), payload={
        'scope': 'slot', 'target_id': slot['id'], 'action': action, 'paused': False,
        'actor': values['actor'], 'reason': values.get('reason', ''),
        'targets': [{'slot_id': slot['id'], 'attempt': slot['attempt']}], **facts})


def _view(slot: dict, action: str, values: dict, words: str, released: int = 0) -> dict:
    return {'slot_id': slot['id'], 'attempt': slot['attempt'], 'action': action, 'words': words,
            'released': released, 'actor': values['actor'], 'reason': values.get('reason', '')}


def _cancel_words(slot: dict, *, hidden: bool) -> str:
    """What a stop costs, in the words of failure case 2.7: an unsent attempt
    costs nothing; a sent hosted request is never cut, so it keeps its cell
    and its charge facts, committed and unverified until the provider reports
    it. A blind run's attempt names neither its lane nor its cost."""
    if slot['status'] in ('queued', 'held'):
        return 'Skipped: nothing more is sent for this attempt, and skipping it costs nothing.'
    if hidden:
        return 'Asked to stop. Whatever it still delivers lands in its own cell; its cost shows after the reveal.'
    from .wiring import lane_of
    if lane_of(slot['engine']) == 'local':
        return 'Asked to stop: the local engine stops this attempt. Local work costs no money.'
    sent = 'Its request was already sent, so it keeps its cell: if it still delivers, the take lands here.'
    if slot['charged_usd']:
        return f"{sent} {_money(slot['charged_usd'])} was charged."
    if slot['committed_usd'] is None:
        return f'{sent} Its price is unknown, and its charge stays unverified until the provider reports it.'
    if slot['committed_usd'] == 0:
        return f'{sent} It costs nothing.'
    return (f"{sent} Up to {_money(slot['committed_usd'])} was committed; its charge stays unverified "
            'until the provider reports it.')


async def cancel_attempt(db, slot_id: str, body, *, hidden: bool = False) -> dict:
    """Record one participant's stop of one attempt inside the caller's
    command transaction (laws 4 and 23). An unsent attempt is skipped at once
    and nothing more is sent for it; a running one keeps a durable stop that
    the scheduler applies, as for a run's cancel. Stopping never spends."""
    values = _values(body)
    async with db._tx():
        slot = await _attempt(db, slot_id, values['attempt'])
        if slot['status'] == 'late':
            raise Conflict('This attempt was already stopped. If its request still delivers, '
                           'the take lands in its own cell.')
        if slot['status'] not in ('queued', 'held', 'running'):
            raise Conflict('This attempt has already settled, so there is nothing to cancel. Refresh it.')
        if slot['status'] == 'running' and await cancel_requested(db, slot):
            raise Conflict('This attempt is already stopping. Refresh it to see where it stands.')
        await _record_attempt(db, slot, 'cancel', values)
        if slot['status'] in ('queued', 'held'):
            await db.transition_slot(slot_id, 'skipped', payload={
                'cancel': True, 'reason': values.get('reason', ''), 'attempt': slot['attempt']})
        return _view(slot, 'cancel', values, _cancel_words(slot, hidden=hidden))


async def held_attempt(db, scheduler, slot_id: str, attempt: int):
    """The held attempt and the bench that holds it. A refusal says why it
    waits instead, and the move that sends it."""
    slot = await _attempt(db, slot_id, attempt)
    if slot['status'] != 'held':
        raise Conflict('This attempt is not held, so there is nothing to resume. Refresh it.')
    holds = [event for event in await db.slot_events(slot_id) if event['type'] == 'slot.held']
    hold = holds[-1]['payload'] if holds else {}
    if hold.get('pause'):
        raise ValueError('This attempt waits for its run or project to resume. Resume that instead.')
    if hold.get('limit'):
        raise ValueError('This attempt waits for a spending limit. Raise the limit to send it, or cancel it.')
    bench = scheduler.standing(hold.get('bench'))
    if bench is None:
        raise Conflict("Nothing holds this attempt's engine any more. Refresh it; if it stays held, "
                       'cancel it and reuse its setup.')
    if not bench.seq:
        raise Conflict('This hold is still being recorded. Refresh, then decide again.')
    return slot, bench


def reserves_money(slots: list[dict]) -> bool:
    """Whether sending these attempts may spend: a hosted attempt that
    reserves an amount, or whose price is unknown (law 22). Local work never
    charges."""
    from .wiring import lane_of
    return any(lane_of(slot['engine']) != 'local' and slot['committed_usd'] != 0 for slot in slots)


async def remaining_cause(stack, bench, *, hidden: bool = False) -> str | None:
    """Why a runtime's bench still stands, in plain words with the next move
    (laws 3 and 4); None when its cause is gone, or when the bench is an
    engine's own. A blind run's hidden attempt hears no runtime named. The
    isolated preview runs no engine, so its benches stand; under the browser
    harness the scripted transports are every runtime, as at its startup."""
    from . import config
    if bench.kind == 'preview':
        return 'This isolated preview runs no engines, so its attempts stay held.'
    if bench.kind not in RUNTIME_KINDS or config.HARNESS_EXECUTORS:
        return None
    why = await _runtime_down(stack, bench)
    return HIDDEN_NOT_READY if why and hidden else why


async def _runtime_down(stack, bench) -> str | None:
    from . import audio, audio_executors, config
    from .mesh import MESH_ENGINES
    from .scheduler import COMFY_SCOPE
    scope = bench.scope
    if bench.kind == 'gpu_held':
        return await stack.scheduler.card_still_held()
    if scope == COMFY_SCOPE:
        try:
            if stack.comfy is None:              # no ComfyUI client to ask in this stack
                raise ConnectionError('no ComfyUI client')
            await stack.comfy.system_stats()
            return None
        except Exception:
            return (f'ComfyUI is still not answering at {config.COMFYUI_URL}, so its attempts stay held. '
                    'Start it, then resume.')
    if scope.startswith('provider:') and scope.split(':', 1)[1] in audio.PROVIDERS:
        provider = scope.split(':', 1)[1]
        executor = stack.scheduler.executor(provider)
        if callable(getattr(executor, 'account', None)):
            await executor.account(fresh=True)     # ask the provider now; its connection line reads this answer
        line = await audio_executors.connection(provider, stack)
        if line['ok'] is True:
            return None
        return f"{line['label']} is not ready yet, so its attempts stay held. {line['detail']}"
    if scope == 'lane:external':
        probe = await stack.openrouter.probe()
        if probe.get('ok'):
            return None
        if not stack.openrouter.key_present:
            return ('There is no OpenRouter key yet, so hosted attempts stay held. '
                    'Save one in Connections, then resume.')
        if 'rejected' in str(probe.get('error') or ''):
            return ('OpenRouter still turns the key away, so hosted attempts stay held. '
                    'Replace it in Connections, then resume.')
        return 'OpenRouter still cannot be reached, so hosted attempts stay held. Check the network, then resume.'
    if scope.startswith('engine:') and scope.split(':', 1)[1] in MESH_ENGINES:
        from .wiring import probe_mesh_runner
        if await probe_mesh_runner():
            return None
        return (f'The 3D engine runner is still not answering at {config.THREED_URL}, so its attempts stay held. '
                'Start it, then resume.')
    return None


async def resume_attempt(db, slot: dict, body, bench, released: int) -> dict:
    """Record one participant's resume of the bench that holds an attempt,
    inside the caller's command transaction (laws 4 and 23). The record names
    the bench and the event that installed it; the scheduler lifts that bench,
    once. Each attempt it releases meets every spending limit again before it
    is sent (law 22)."""
    values = _values(body)
    async with db._tx():
        current = await _attempt(db, slot['id'], values['attempt'])
        if current['status'] != 'held':
            raise Conflict('This attempt is not held, so there is nothing to resume. Refresh it.')
        await _record_attempt(db, current, 'resume', values, bench=bench.scope, bench_seq=bench.seq)
        start = ('Trying once more' if bench.kind == 'repeat' else
                 'It answers again, so its hold was lifted' if bench.kind in RUNTIME_KINDS else
                 'Its hold was lifted')
        words = (f'{start}: {_attempts(released)} went back to the queue, and each meets the '
                 'spending limits again before it is sent.')
        return _view(current, 'resume', values, words, released)


async def resumes(db, benches: dict[str, int]) -> list[dict]:
    """Recorded resumes of benches that still stand as they stood when their
    participant asked: each resume names its bench by scope and by the event
    that installed it. `benches` maps each standing scope to that event."""
    installed = [seq for seq in benches.values() if seq]
    if not installed:
        return []
    # A resume is recorded after the event that installed its bench.
    rows = await db._all(
        "SELECT payload_json FROM events WHERE seq > ? AND type='execution.control' "
        "AND json_extract(payload_json,'$.action')='resume' "
        "AND json_type(payload_json,'$.archive_origin') IS NULL ORDER BY seq",
        (min(installed),))
    found = [json.loads(row['payload_json']) for row in rows]
    return [intent for intent in found if intent.get('bench') in benches
            and benches[intent['bench']] == intent.get('bench_seq')]
