"""Durable project and run execution controls on the existing event ledger.

Pause gates future dispatch while leaving running work and unrelated holds intact.
Cancellation freezes current slot attempts and skips unsent work atomically with
its attributed intent. The scheduler owns interruption and result collection.
Resume never revokes cancellation of an earlier attempt or releases holds itself.
"""

from __future__ import annotations

import json
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
