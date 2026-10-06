"""Read provenance and attributed decisions from the existing project ledger.

Assessments and reason revisions append events, without changing display choices,
execution success or older authorship. A revision names the exact version it saw.
An assessment made while its run is blind records it, like a selection.
Search includes unavailable and set-aside work. Restored identities are resolved
only for navigation; participant text and frozen requests remain historical.

Search matches words, never JSON key names or nulls: every query word must
appear in a result's own text. Prompts are read through the full-text index
(word prefixes); a purged take's prompt, names and reasons match as substrings.
While a run is blind, its takes are never matched by engine or file name, so a
search cannot tell its takes apart by engine (law 23 keeps authorship readable).
"""

from __future__ import annotations

import sqlite3

from . import blind
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


def words(*values) -> str:
    """The searchable text of values: their strings and numbers only, never the
    key names, booleans or nulls of a structured record."""
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            found.append(str(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for value in values:
        walk(value)
    return ' '.join(found).casefold()


async def prompt_hits(db, wid: str, tokens: list[str]) -> list[set[str]]:
    """Per query word, the takes whose indexed prompt holds a word starting with it."""
    hits = []
    for token in tokens:
        try:
            rows = await db._all('SELECT f.node_id FROM node_fts f JOIN nodes n ON n.id=f.node_id '
                                 'WHERE node_fts MATCH ? AND n.workspace_id=?',
                                 ('"' + token.replace('"', '""') + '"*', wid))
        except sqlite3.OperationalError:     # a word the index cannot tokenize matches by substring only
            rows = []
        hits.append({row['node_id'] for row in rows})
    return hits


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
        hidden = await blind.hidden_turns(db, wid)
        tokens = q.casefold().split()
        hits = await prompt_hits(db, wid, tokens)

        def node_text(n):
            named = [] if n['turn_id'] in hidden else [n['engine'], files.get(n['id'], {}).get('filename')]
            return words(*named, n['prompt'] if n['purged_at'] else None)

        texts = {n['id']: node_text(n) for n in nodes}

        def node_has(nid, index, token):
            return nid in hits[index] or token in texts.get(nid, '')

        direction_text = {d['id']: words(d['title'], d['setup']) for d in directions}

        def turn_text(t):
            slots = [s['status'] for s in t['slots']] if t['id'] in hidden else [
                {'engine': s['engine'], 'status': s['status'], 'cause': s['cause']} for s in t['slots']]
            return words(t['prompt'], t['dials'], slots)

        def decision_matches(item):
            own = words(item['reason'], (item['actor'] or {}).get('name'), direction_text.get(item['direction_id']))
            return all(token in own or any(node_has(n, i, token) for n in item['node_ids'])
                       for i, token in enumerate(tokens))

        direction = next((d for d in directions if d['id'] == direction_id), None)
        run_nodes = {n['id'] for n in nodes if n['turn_id'] == turn_id} if turn_id else set()
        filtered = [item for item in reversed(history)
                    if (before is None or item['seq'] < before)
                    and (node_id is None or node_id in item['node_ids'])
                    and (direction_id is None or direction is not None and concerns_direction(item, direction))
                    and (turn_id is None or turn_id == item['turn_id'] or run_nodes.intersection(item['node_ids']))
                    and decision_matches(item)]
        return {'decisions': filtered[:limit],
                'next_before': filtered[limit - 1]['seq'] if len(filtered) > limit else None,
                'material_ids': [n['id'] for n in nodes
                                 if all(node_has(n['id'], i, token) for i, token in enumerate(tokens))],
                'direction_ids': [d['id'] for d in directions
                                  if all(token in direction_text[d['id']] for token in tokens)],
                'turn_ids': [t['id'] for t in turns if all(token in turn_text(t) for token in tokens)]}


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
    payload.update(await blind.facts_for(db, payload['node_ids'], [payload['turn_id']]),
                   actor=body.actor.model_dump(), reason=body.reason)
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
