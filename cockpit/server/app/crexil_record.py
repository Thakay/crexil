"""Read provenance and attributed decisions from the existing project ledger.

Assessments and reason revisions append events, without changing display choices,
execution success or older authorship. A revision names the exact version it saw.
Search includes unavailable and set-aside work. Restored identities are resolved
only for navigation; participant text and frozen requests remain historical.
"""

from __future__ import annotations

import json

from .crexil_store import Conflict, CreativeStore


def event_identity(event: dict) -> str:
    origin = event['payload'].get('archive_origin') or {}
    return origin.get('record_id') or f"{origin.get('project_id', event['workspace_id'])}:{origin.get('event_seq', event['seq'])}"


def concerns_direction(item: dict, direction: dict) -> bool:
    return (item['direction_id'] == direction['id'] or bool(set(direction['node_ids']).intersection(item['node_ids']))
            or item['turn_id'] in direction['turn_ids'])


async def decisions(db, wid: str) -> list[dict]:
    from .crexil_restore import context
    restored = await context(db, wid)
    aliases = (restored or {}).get('identities', {})
    def resolve(table, value):
        return aliases.get(table, {}).get(value, value)
    rows = await db._all("SELECT * FROM events WHERE workspace_id=? AND type IN "
                         "('direction.changed','sequence.decision','decision.recorded') ORDER BY seq", (wid,))
    results = []
    for row in rows:
        event = db._event_row(row)
        payload = event['payload']
        eid = event_identity(event)
        if event['type'] == 'direction.changed':
            if 'selection' not in payload.get('changes', {}):
                continue
            kind, nodes = 'selection', payload['changes']['selection']
        elif event['type'] == 'sequence.decision':
            kind = 'sequence_choice'
            nodes = [item['node_id'] for group in payload.get('selected', {}).values() for item in group]
        else:
            kind, nodes = payload['kind'], payload['node_ids']
        results.append({'id': payload.get('root_id', eid), 'event_id': eid, 'seq': event['seq'],
                        'created_at': event['ts'], 'kind': kind, 'actor': payload.get('actor'),
                        'reason': payload.get('reason'), 'node_ids': list(dict.fromkeys(resolve('nodes', n) for n in nodes)),
                        'direction_id': resolve('directions', payload.get('direction_id')),
                        'turn_id': event['turn_id'] or resolve('turns', payload.get('turn_id')),
                        'sequence_id': resolve('sequences', payload.get('sequence_id')),
                        'previous_id': payload.get('previous_id'), 'revised_by': None,
                        'historical': bool(payload.get('archive_origin'))})
    successors = {item['previous_id']: item['event_id'] for item in results if item['previous_id']}
    for item in results:
        item['revised_by'] = successors.get(item['event_id'])
    return results


async def record(db, wid: str, *, q='', node_id=None, direction_id=None, turn_id=None, before=None, limit=50):
    async with db._locked():
        await CreativeStore(db).project(wid)
        history = await decisions(db, wid)
        nodes = await db.list_nodes(wid, include_purged=True)
        directions = [await CreativeStore(db).direction(row['id']) for row in await db._all(
            'SELECT id FROM directions WHERE workspace_id=? ORDER BY row_idx,created_at,id', (wid,))]
        turns = await db.list_turns(wid)
        files = {row['node_id']: dict(row) for row in await db._all(
            'SELECT m.* FROM material_info m JOIN nodes n ON n.id=m.node_id WHERE n.workspace_id=?', (wid,))}
        needle = q.casefold().strip()
        def matches(value):
            return not needle or needle in json.dumps(value, ensure_ascii=False).casefold()
        node_text = {n['id']: [n['prompt'], n['engine'], files.get(n['id'], {}).get('filename')] for n in nodes}
        direction_text = {d['id']: [d['title'], d['setup']] for d in directions}
        direction = next((d for d in directions if d['id'] == direction_id), None)
        run_nodes = {n['id'] for n in nodes if n['turn_id'] == turn_id} if turn_id else set()
        filtered = [item for item in reversed(history)
                    if (before is None or item['seq'] < before)
                    and (node_id is None or node_id in item['node_ids'])
                    and (direction_id is None or direction is not None and concerns_direction(item, direction))
                    and (turn_id is None or turn_id == item['turn_id'] or run_nodes.intersection(item['node_ids']))
                    and matches([item, direction_text.get(item['direction_id']),
                                 *[node_text.get(n) for n in item['node_ids']]])]
        return {'decisions': filtered[:limit],
                'next_before': filtered[limit - 1]['seq'] if len(filtered) > limit else None,
                'material_ids': [n['id'] for n in nodes if matches(node_text[n['id']])],
                'direction_ids': [d['id'] for d in directions if matches(direction_text[d['id']])],
                'turn_ids': [t['id'] for t in turns if matches([t['prompt'], t['dials'],
                    [{'engine': s['engine'], 'status': s['status'], 'cause': s['cause']} for s in t['slots']]])]}


async def assess(db, wid: str, body):
    await CreativeStore(db).project(wid)
    if not body.reason.strip():
        raise ValueError('Write an assessment or reason before saving it.')
    if body.previous_id:
        history = await decisions(db, wid)
        previous = next((item for item in history if item['event_id'] == body.previous_id), None)
        if previous is None:
            raise LookupError('This decision could not be found in this project.')
        if previous['revised_by']:
            raise Conflict('This reason has a newer revision. Read it before adding your revision.')
        if body.node_ids or body.direction_id or body.turn_id:
            raise ValueError('A reason revision keeps its original subjects. Make a new assessment to change them.')
        payload = {key: previous[key] for key in ('kind', 'node_ids', 'direction_id', 'turn_id', 'sequence_id')}
        payload.update(root_id=previous['id'], previous_id=previous['event_id'])
    else:
        if not body.node_ids and not body.direction_id and not body.turn_id:
            raise ValueError('Choose material, a direction or a run for this assessment.')
        if len(set(body.node_ids)) != len(body.node_ids):
            raise ValueError('List each assessed item once.')
        for table, ids in (('nodes', body.node_ids), ('directions', [body.direction_id] if body.direction_id else []),
                           ('turns', [body.turn_id] if body.turn_id else [])):
            for identity in ids:
                if not await db._one(f'SELECT 1 FROM {table} WHERE id=? AND workspace_id=?', (identity, wid)):
                    raise ValueError('Every assessed item must belong to this project.')
        payload = {'kind': 'assessment', 'node_ids': body.node_ids, 'direction_id': body.direction_id,
                   'turn_id': body.turn_id, 'sequence_id': None}
    payload.update(actor=body.actor.model_dump(), reason=body.reason)
    event = await db.append_event('decision.recorded', workspace_id=wid, turn_id=payload['turn_id'], payload=payload)
    await db.touch_workspace(wid)
    return next(item for item in await decisions(db, wid) if item['seq'] == event['seq'])


async def material_record(db, nid: str):
    from .manifests import _shared_request
    node = await db.get_node(nid)
    if not node:
        raise LookupError('This material record could not be found.')
    turn = await db.get_turn(node['turn_id']) if node['turn_id'] else None
    frozen = ((turn or {}).get('dials') or {}).get('crexil') or {}
    return {'node_id': nid, 'request': await _shared_request(db, node, turn),
            'controls': node.get('dials') or {}, 'submitted': node.get('submitted_graph') or node.get('external'),
            'regions': frozen.get('region')}
