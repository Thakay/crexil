"""Finite, explicitly requested sequences over shared creative preparation.

Templates remain revisioned directions. Each accepted input creates a separate
direction and uses the ordinary review, freeze and adoption path. Dependencies
bind exact retained material; displayed choices never supply creative judgment.
All-successful membership freezes once upstream attempts settle. Later recovered
material can unblock an unbound step, but never changes an accepted source set.
Dependencies explicitly precede or follow fixed sources without payload sorting.
Mesh source facts include retained voxel fields, including their absence.
The coordinator reconciles durable state after restart and treats events only as
wakeups. Cancellation also covers steps that have not created execution slots.
Participant revisions exclude execution progress; candidate tokens still bind
the exact available material and preparation when a decision is supplied.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import suppress
from pathlib import Path

from fastapi import HTTPException

from .crexil_execution import digest
from .crexil_schemas import CreateSequence, Preparation, SubmitDirection
from .crexil_store import Conflict, CreativeStore
from .db import new_id, now_iso


def _decode(row, fields):
    value = dict(row)
    for field in fields:
        raw = value.pop(field + '_json')
        value[field] = json.loads(raw) if raw is not None else None
    return value


async def _sequence(db, sid):
    row = await db._one('SELECT * FROM sequences WHERE id=?', (sid,))
    if not row:
        raise LookupError('This sequence could not be found. Return to the project.')
    return _decode(row, ('limits', 'authorization', 'actor'))


async def _steps(db, sid):
    return [_decode(row, ('dependencies', 'binding', 'decision')) for row in await db._all(
        'SELECT * FROM sequence_steps WHERE sequence_id=? ORDER BY ordinal', (sid,))]


async def _runs(db, sid, key=None):
    return [dict(row) for row in await db._all('SELECT * FROM sequence_runs WHERE sequence_id=?' +
        (' AND step_key=?' if key is not None else '') + ' ORDER BY created_at,input_key',
        (sid, key) if key is not None else (sid,))]


async def _changed(db, sequence, kind, payload, *, revise=True):
    await db.conn.execute('UPDATE sequences SET revision=revision+?,updated_at=? WHERE id=?',
                          (int(revise), now_iso(), sequence['id']))
    await db.append_event(kind, workspace_id=sequence['workspace_id'],
        payload={'sequence_id': sequence['id'], **payload})
    await db.touch_workspace(sequence['workspace_id'])


async def _step_state(db, sequence, step, state, reason=''):
    if step['state'] == state and step['reason'] == reason:
        return
    await db.conn.execute('UPDATE sequence_steps SET state=?,reason=? WHERE sequence_id=? AND key=?',
                          (state, reason, sequence['id'], step['key']))
    await _changed(db, sequence, 'sequence.step_state', {'step_key': step['key'], 'state': state, 'reason': reason}, revise=False)
    step.update(state=state, reason=reason)


async def material_fact(db, nid):
    """The identity a sequence binds: the node, its output and its bytes'
    SHA-256. Hashes come through the store's size-and-mtime cache, hashed off
    the event loop, so a sequence polling its sources never rereads them."""
    node = await db.get_node(nid)
    if not node or node['purged_at'] or not Path(node['output_path']).is_file():
        return None
    output = await db._one('SELECT id,role,group_id FROM execution_outputs WHERE node_id=?', (nid,))
    info = await db._one('SELECT origin_json FROM material_info WHERE node_id=?', (nid,))
    fact = {'node_id': nid, 'slot_id': node['slot_id'], 'attempt': node['take'],
            'output_id': output['id'] if output else None, 'role': output['role'] if output else None,
            'origin_kind': json.loads(info['origin_json']).get('kind') if info else 'execution' if node['slot_id'] else 'external',
            'payload': node['payload'], 'sha256': await db.file_sha256(node['output_path'])}
    if node['payload'] == '3d':
        from .mesh import FIELDS_SUFFIX, fields_sidecar
        path = fields_sidecar(node['output_path'])
        identity = 'companion:' + FIELDS_SUFFIX
        companion = await db._one('SELECT id FROM execution_outputs WHERE group_id=? AND identity=?',
                                  (output['group_id'], identity)) if output else None
        field_hash = await db.file_sha256(path)
        fact['mesh_fields'] = {'identity': identity, 'output_id': companion['id'] if companion else None,
                               'path': str(path), 'sha256': field_hash}
    return fact


async def candidates(db, sid, step):
    result = {}
    creative = CreativeStore(db)
    for dependency in step['dependencies']:
        found = []
        for run in await _runs(db, sid, dependency['step_key']):
            direction = await creative.direction(run['direction_id'])
            for nid in direction['node_ids']:
                fact = await material_fact(db, nid)
                if fact and fact['node_id'] not in {item['node_id'] for item in found}:
                    slot = await db.get_slot(fact['slot_id']) if fact['slot_id'] else None
                    group = await db._one('SELECT state FROM execution_groups WHERE slot_id=? AND take=?',
                                         (fact['slot_id'], fact['attempt'])) if slot else None
                    fact['successful'] = bool(run['turn_id'] is None and fact['origin_kind'] == 'prepared_image' or
                        slot and slot['turn_id'] == run['turn_id']
                        and slot['attempt'] == fact['attempt'] and slot['status'] in ('landed', 'set_aside')
                        and group and group['state'] == 'complete')
                    found.append(fact)
        result[dependency['step_key']] = found
    return result


async def state(db, sid):
    from .crexil_budget import accounting
    async with db._locked():
        sequence = await _sequence(db, sid)
        steps = await _steps(db, sid)
        runs = await _runs(db, sid)
        for step in steps:
            step['candidates'] = await candidates(db, sid, step)
            direction = await CreativeStore(db).direction(step['direction_id'])
            step['candidate_token'] = digest([step['candidates'], direction['revision'], step['dependencies']])
            step['runs'] = [run for run in runs if run['step_key'] == step['key']]
        return {**sequence, 'steps': steps,
                'accounting': await accounting(db, sequence['workspace_id'],
                    turn_ids=[run['turn_id'] for run in runs if run['turn_id']])}


async def list_sequences(db, wid):
    return [await state(db, row['id']) for row in await db._all(
        'SELECT id FROM sequences WHERE workspace_id=? ORDER BY created_at,id', (wid,))]


async def create(db, wid, body: CreateSequence):
    creative = CreativeStore(db)
    await creative.project(wid)
    keys = [step.key for step in body.steps]
    if len(set(keys)) != len(keys) or len({step.direction_id for step in body.steps}) != len(keys):
        raise ValueError('Each sequence step needs its own key and preparation.')
    graph = {step.key: [dep.step_key for dep in step.dependencies] for step in body.steps}
    seen, active = set(), set()
    def visit(key):
        if key not in graph:
            raise ValueError('A dependency must name a step in this sequence.')
        if key in active:
            raise ValueError('A sequence cannot depend on itself through a loop.')
        if key in seen:
            return
        active.add(key)
        for parent in graph[key]:
            visit(parent)
        active.remove(key)
        seen.add(key)
    for key in keys:
        visit(key)
    for step in body.steps:
        if len(set(graph[step.key])) != len(graph[step.key]):
            raise ValueError('List each dependency once and describe its contribution.')
        if step.input_mode == 'each' and len(step.dependencies) != 1:
            raise ValueError('One run per source needs exactly one upstream step.')
        direction = await creative.direction(step.direction_id)
        if direction['workspace_id'] != wid or direction['state'] != 'draft' or direction['turn_ids']:
            raise ValueError('Use an unsubmitted preparation from this project for each sequence step.')
        if direction['revision'] != step.revision:
            raise Conflict('A step preparation changed. Refresh before creating the sequence.')
        if await db._one('SELECT 1 FROM sequence_steps WHERE direction_id=?', (step.direction_id,)):
            raise ValueError('This preparation already belongs to a sequence. Reuse it to make another template.')
    sid, ts = new_id(), now_iso()
    await db.conn.execute('INSERT INTO sequences(id,workspace_id,title,instructions,limits_json,actor_json,created_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?)', (sid, wid, body.title, body.instructions, json.dumps(body.limits.model_dump()),
                                  json.dumps(body.actor.model_dump()), ts, ts))
    for ordinal, step in enumerate(body.steps):
        await db.conn.execute('INSERT INTO sequence_steps(sequence_id,key,ordinal,direction_id,input_mode,dependencies_json) '
            'VALUES(?,?,?,?,?,?)', (sid, step.key, ordinal, step.direction_id, step.input_mode,
                                   json.dumps([dep.model_dump() for dep in step.dependencies])))
    await db.append_event('sequence.created', workspace_id=wid,
        payload={'sequence_id': sid, 'definition': body.model_dump(), 'actor': body.actor.model_dump()})
    return await state(db, sid)


async def cancel_pending(db, sid, actor, reason):
    sequence = await _sequence(db, sid)
    if sequence['state'] == 'cancelled' or all(step['state'] == 'complete' for step in await _steps(db, sid)):
        return
    await db.conn.execute("UPDATE sequences SET state='cancelled' WHERE id=?", (sid,))
    await db.conn.execute("UPDATE sequence_steps SET state='cancelled',reason=? WHERE sequence_id=? AND state!='complete'",
                          (reason or 'Sequence cancelled', sid))
    await _changed(db, sequence, 'sequence.cancelled', {'actor': actor, 'reason': reason})


async def _finished(db, runs):
    if not runs:
        return False
    for run in runs:
        if run['turn_id']:
            turn = await db.get_turn(run['turn_id'])
            if any(slot['status'] in ('queued', 'held', 'running', 'late') for slot in turn['slots']):
                return False
    return True


async def _resolve(db, sequence, step, template):
    """Resolve once; judgment records concrete results rather than a mutable pick."""
    if step['binding']:
        return step['binding'], '', ''
    available = await candidates(db, sequence['id'], step)
    dependency_sources = {'before_fixed': [], 'after_fixed': []}
    steps = {item['key']: item for item in await _steps(db, sequence['id'])}
    for dependency in step['dependencies']:
        key = dependency['step_key']
        if dependency['selection'] == 'all_successful':
            upstream = steps[key]
            if upstream['binding'] is None or len(await _runs(db, sequence['id'], key)) < len(upstream['binding']['inputs']):
                return None, 'waiting', f'Waiting for step {key} to accept its required inputs.'
            if not await _finished(db, await _runs(db, sequence['id'], key)):
                return None, 'waiting', f'Waiting for step {key} to finish before binding all successful outputs.'
            selected = [item for item in available[key] if item['successful']]
            if not selected:
                return None, 'blocked', f'Step {key} has no completed successful result. Recover its material or change this dependency to an explicit choice.'
        else:
            if not step['decision'] or step['decision']['direction_revision'] != template['revision']:
                return None, 'decision', f'Choose the required material from step {key} and give a reason.'
            selected = step['decision']['selected'][key]
            if any(not any({field: value for field, value in current.items() if field != 'successful'} ==
                           {field: value for field, value in item.items() if field != 'successful'}
                           for current in available[key]) for item in selected):
                return None, 'blocked', f'A chosen result from step {key} is unavailable or changed. Supply a new decision.'
        dependency_sources[dependency.get('placement', 'after_fixed')].extend(
            {'node_id': item['node_id'], 'contribution': dependency['contribution'],
             'role': None, 'strength': None} for item in selected)
    fixed = Preparation.model_validate(template['setup']).model_dump()['references']
    before, after = dependency_sources['before_fixed'], dependency_sources['after_fixed']
    reference_sets = ([(1, [source] + fixed) for source in before] +
                      [(0, fixed + [source]) for source in after]) if step['input_mode'] == 'each' else [
                          (len(before), before + fixed + after)]
    inputs = []
    for fixed_start, references in reference_sets:
        facts = [await material_fact(db, ref['node_id']) for ref in references]
        if any(fact is None for fact in facts):
            return None, 'blocked', 'Required source material is unavailable. Restore it before continuing.'
        inputs.append({'key': digest([ref['node_id'] for ref in references]), 'references': references,
                       'sources': facts, 'fixed_start': fixed_start})
    return {'inputs': inputs, 'fixed_references': fixed, 'decision': step['decision'], 'rule': 'all_successful_after_attempts_settle',
            'bound_at': now_iso()}, '', ''


async def _freeze_input(stack, sequence, step, template, item, index, staged):
    from .crexil_execution import freeze, review as review_direction
    creative = CreativeStore(stack.db)
    actor = sequence['authorization']['actor']
    for expected in item['sources']:
        if await material_fact(stack.db, expected['node_id']) != expected:
            raise ValueError('A bound source is unavailable or changed. Accepted sequence sources are never substituted.')
    setup = {**template['setup'], 'references': item['references']}
    if setup['action'] == 'prepare' and setup.get('image_preparation'):
        recipe = dict(setup['image_preparation'])
        placements = recipe.get('items', [])
        if not placements and recipe['mode'] != 'arrange' and len(item['references']) == 1:
            placements = [{}]
        if len(placements) != len(item['references']):
            raise ValueError('The image preparation needs one explicit placement for each bound source.')
        recipe['items'] = [{**placement, 'node_id': ref['node_id']} for placement, ref in zip(placements, item['references'], strict=True)]
        setup['image_preparation'] = recipe
    direction = await creative.new_direction(sequence['workspace_id'],
        f'{template["title"]} · {index + 1}', setup, actor, row_idx=template['row_idx'])
    context = {'id': sequence['id'], 'step_key': step['key'], 'input_key': item['key'],
               'template_direction_id': template['id'], 'template_revision': template['revision'],
               'instructions': sequence['instructions'], 'binding': item, 'decision': step['decision']}
    preparing = setup['action'] == 'prepare'
    if preparing:
        from .image_preparation import review as review_image
        checked = await review_image(stack.db, direction['id'])
    else:
        checked = await review_direction(stack, direction['id'], sequence_id=sequence['id'])
    if not checked['ready'] or checked['blocked_attempts']:
        raise ValueError(' '.join(checked['errors']) or 'This step has no ready operation.')
    body = SubmitDirection(request_id='sequence-' + digest([sequence['id'], step['key'], item['key']]),
        actor=actor, revision=direction['revision'], review_token=checked['token'])
    if preparing:
        from .image_preparation import save
        result = {**await save(stack.db, direction['id'], body, sequence_context=context, staged_directories=staged), 'turn_id': None}
    else:
        result = await freeze(stack, direction['id'], body, staged_directories=staged, sequence_context=context)
    await stack.db.conn.execute('INSERT INTO sequence_runs VALUES(?,?,?,?,?,?)',
        (sequence['id'], step['key'], item['key'], direction['id'], result['turn_id'], now_iso()))
    await _changed(stack.db, sequence, 'sequence.step_submitted',
        {'step_key': step['key'], 'input_key': item['key'], 'direction_id': direction['id'],
         'turn_id': result['turn_id'], 'actor': actor, 'binding': item}, revise=False)
    return result


class Coordinator:
    def __init__(self, stack):
        self.stack = stack
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._loop(), name='crexil-sequences')
        self.wake.set()

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _loop(self):
        while True:
            self.wake.clear()
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.stack.capture.record('sequences.reconcile', exc,
                    note='Sequence preparation remains saved. Reconciliation will retry without repeating accepted runs.')
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=1)

    async def reconcile(self):
        async with self.lock:
            rows = await self.stack.db._all("SELECT id FROM sequences WHERE state='active' AND EXISTS "
                "(SELECT 1 FROM sequence_steps WHERE sequence_id=sequences.id AND state!='complete') ORDER BY created_at,id")
            for row in rows:
                try:
                    await self._advance(row['id'])
                except Exception as exc:
                    await self.stack.capture.record('sequences.advance', exc,
                        note=f'Sequence {row["id"]} could not advance; independent sequences remain eligible.')

    async def _advance(self, sid):
        from .execution_controls import state as control_state
        from .operation_registry import refresh_for_direction
        db = self.stack.db
        # Acceptance may have committed before the process lost its response or
        # scheduler adoption. Re-adopting frozen slots never submits a new run.
        for run in await _runs(db, sid):
            if run['turn_id']:
                await self.stack.scheduler.adopt_prepared_turn(run['turn_id'])
        for original in await _steps(db, sid):
            await refresh_for_direction(self.stack, original['direction_id'])
            staged, accepted = [], []
            try:
                async with db._tx():
                    sequence = await _sequence(db, sid)
                    if sequence['state'] != 'active':
                        return
                    if any([(await control_state(db, scope, target))['paused'] for scope, target in
                            (('project', sequence['workspace_id']), ('sequence', sid))]):
                        return
                    step = next(item for item in await _steps(db, sid) if item['key'] == original['key'])
                    runs = await _runs(db, sid, step['key'])
                    if step['binding'] and len(runs) == len(step['binding']['inputs']) and await _finished(db, runs):
                        await _step_state(db, sequence, step, 'complete')
                        continue
                    creative = CreativeStore(db)
                    template = await creative.direction(step['direction_id'])
                    project = await creative.project(sequence['workspace_id'])
                    authorization = sequence['authorization']
                    expected = authorization['templates'][step['key']]
                    if (template['revision'] != expected['revision'] or template['set_aside']
                            or sequence['instructions'] != authorization['instructions']
                            or [step['input_mode'], step['dependencies']] != authorization['dependencies'][step['key']]
                            or project['instructions'] != authorization['project_instructions']):
                        await _step_state(db, sequence, step, 'blocked', 'Preparation or instructions changed. Review the sequence to authorize the remaining work.')
                        continue
                    if [await material_fact(db, ref['node_id']) for ref in expected['setup']['references']] != expected['sources']:
                        await _step_state(db, sequence, step, 'blocked', 'A saved source changed or is missing. Restore it and review the sequence.')
                        continue
                    binding, status, reason = await _resolve(db, sequence, step, template)
                    if binding is None:
                        await _step_state(db, sequence, step, status, reason)
                        continue
                    if step['binding'] is None:
                        await db.conn.execute('UPDATE sequence_steps SET binding_json=? WHERE sequence_id=? AND key=?',
                                              (json.dumps(binding), sid, step['key']))
                        step['binding'] = binding
                        await _changed(db, sequence, 'sequence.sources_bound', {'step_key': step['key'], 'binding': binding}, revise=False)
                    existing = {run['input_key'] for run in runs}
                    blocked = []
                    for index, item in enumerate(binding['inputs']):
                        if item['key'] in existing:
                            continue
                        try:
                            # A failed input rolls back its draft and freeze,
                            # while prior independently accepted inputs remain.
                            async with db._tx():
                                result = await _freeze_input(self.stack, sequence, step, template, item, index, staged)
                                if result['turn_id']:
                                    accepted.append(result['turn_id'])
                        except (ValueError, LookupError, OSError, HTTPException) as exc:
                            reason = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                            blocked.append(f'Input {index + 1}: {reason}')
                    await _step_state(db, sequence, step, 'blocked' if blocked else 'running', '\n'.join(blocked))
            finally:
                # Preserve files after uncertain commit; remove only staged
                # directories that no committed runtime plan references.
                for directory in staged:
                    try:
                        retained = await db._one('SELECT 1 FROM slots WHERE instr(submitted_graph_json,?)>0 '
                            'UNION ALL SELECT 1 FROM nodes WHERE instr(output_path,?)=1', (str(directory), str(directory)))
                        if retained is None:
                            await asyncio.to_thread(shutil.rmtree, directory)
                    except Exception:
                        # Uncertain commit or cleanup retains bytes for recovery.
                        pass
            for turn_id in accepted:
                await self.stack.scheduler.adopt_prepared_turn(turn_id)


async def update(db, sid, body):
    sequence = await _sequence(db, sid)
    if sequence['revision'] != body.revision:
        raise Conflict('This sequence changed in another view. Refresh before saving.')
    await db.conn.execute('UPDATE sequences SET title=?,instructions=?,limits_json=? WHERE id=?',
        (body.title, body.instructions, json.dumps(body.limits.model_dump()), sid))
    await _changed(db, sequence, 'sequence.changed', {'actor': body.actor.model_dump(),
        'title': body.title, 'instructions': body.instructions, 'limits': body.limits.model_dump()})
    return await state(db, sid)


async def update_step(db, sid, key, body):
    sequence = await _sequence(db, sid)
    if sequence['revision'] != body.revision:
        raise Conflict('The sequence changed in another view. Refresh before changing a dependency.')
    steps = await _steps(db, sid)
    step = next((item for item in steps if item['key'] == key), None)
    if not step:
        raise LookupError('This sequence step could not be found.')
    if sequence['state'] == 'cancelled' or await _runs(db, sid, key):
        raise ValueError('This step has already accepted work or was cancelled. Reuse the sequence to change its dependencies.')
    dependencies = [dep.model_dump() for dep in body.dependencies]
    if body.input_mode == 'each' and len(dependencies) != 1:
        raise ValueError('One run per source needs exactly one upstream step.')
    graph = {item['key']: [dep['step_key'] for dep in item['dependencies']] for item in steps}
    graph[key] = [dep['step_key'] for dep in dependencies]
    if len(set(graph[key])) != len(graph[key]):
        raise ValueError('List each dependency once.')
    visited = set()
    def visit(current, ancestors):
        if current not in graph or current in ancestors:
            raise ValueError('Dependencies must name this sequence\'s steps without forming a loop.')
        if current in visited:
            return
        for parent in graph[current]:
            visit(parent, ancestors | {current})
        visited.add(current)
    for current in graph:
        visit(current, set())
    await db.conn.execute("UPDATE sequence_steps SET input_mode=?,dependencies_json=?,binding_json=NULL,decision_json=NULL,"
        "state='waiting',reason='' WHERE sequence_id=? AND key=?", (body.input_mode, json.dumps(dependencies), sid, key))
    await _changed(db, sequence, 'sequence.step_changed', {'step_key': key, 'input_mode': body.input_mode,
        'dependencies': dependencies, 'actor': body.actor.model_dump()})
    return await state(db, sid)


async def review(stack, sid):
    from .crexil_execution import catalogue, review as review_direction
    from .operation_registry import catalogue as custom_catalogue
    sequence = await state(stack.db, sid)
    creative = CreativeStore(stack.db)
    project = await creative.project(sequence['workspace_id'])
    operations = {op['id']: op for op in catalogue(stack) + await custom_catalogue(stack)}
    errors, templates = [], {}
    if sequence['state'] == 'cancelled':
        errors.append('This sequence was cancelled. Reuse its preparation to request new work.')
    for step in sequence['steps']:
        direction = await creative.direction(step['direction_id'])
        setup = Preparation.model_validate(direction['setup'])
        sources = [await material_fact(stack.db, ref.node_id) for ref in setup.references]
        templates[step['key']] = {'direction_id': direction['id'], 'revision': direction['revision'],
                                  'setup': setup.model_dump(), 'sources': sources}
        if step['state'] == 'complete':
            continue
        if direction['set_aside'] or direction['state'] != 'draft':
            errors.append(f'{direction["title"]}: restore its saved draft before continuing.')
        if not step['dependencies']:
            try:
                if setup.action == 'prepare':
                    from .image_preparation import review as review_image
                    checked = await review_image(stack.db, direction['id'])
                else:
                    checked = await review_direction(stack, direction['id'])
                errors.extend(f'{direction["title"]}: {error}' for error in checked['errors'])
            except ValueError as exc:
                errors.append(f'{direction["title"]}: {exc}')
        else:
            if not setup.operations and setup.action != 'prepare':
                errors.append(f'{direction["title"]}: choose an operation for this step.')
            for choice in setup.operations:
                operation = operations.get(choice.operation_id)
                if not operation or operation['revision'] != choice.revision:
                    errors.append(f'{direction["title"]}: its operation changed or is unavailable. Update its preparation.')
    snapshot = {'sequence_id': sid, 'revision': sequence['revision'], 'title': sequence['title'],
        'instructions': sequence['instructions'], 'limits': sequence['limits'],
        'project_revision': project['revision'], 'project_instructions': project['instructions'],
        'templates': templates, 'dependencies': {step['key']: [step['input_mode'], step['dependencies']] for step in sequence['steps']}}
    return {'sequence_id': sid, 'revision': sequence['revision'], 'token': digest(snapshot),
            'ready': not errors, 'errors': list(dict.fromkeys(errors)), 'snapshot': snapshot}


async def start(stack, sid, body):
    checked = await review(stack, sid)
    if body.revision != checked['revision'] or body.review_token != checked['token']:
        raise Conflict('The sequence or its preparation changed. Review it again before starting.')
    if not checked['ready']:
        raise ValueError(' '.join(checked['errors']))
    sequence = await _sequence(stack.db, sid)
    for step in await _steps(stack.db, sid):
        binding = step['binding']
        if not binding:
            continue
        template = checked['snapshot']['templates'][step['key']]
        fresh_fixed, fresh_sources = template['setup']['references'], template['sources']
        existing = {run['input_key'] for run in await _runs(stack.db, sid, step['key'])}
        previous_count = len(binding['fixed_references'])
        changed = False
        for item in binding['inputs']:
            if item['key'] in existing:
                continue
            offset = item.get('fixed_start', 0)
            end = offset + previous_count
            if item['references'][offset:end] == fresh_fixed and item['sources'][offset:end] == fresh_sources:
                continue
            if any(source is None for source in fresh_sources):
                raise ValueError('A redirected source is unavailable. Restore it before authorizing remaining work.')
            item['references'] = item['references'][:offset] + fresh_fixed + item['references'][end:]
            item['sources'] = item['sources'][:offset] + fresh_sources + item['sources'][end:]
            changed = True
        if not changed:
            continue
        binding['fixed_references'] = fresh_fixed
        await stack.db.conn.execute('UPDATE sequence_steps SET binding_json=? WHERE sequence_id=? AND key=?',
                                     (json.dumps(binding), sid, step['key']))
        await _changed(stack.db, sequence, 'sequence.sources_redirected',
                       {'step_key': step['key'], 'actor': body.actor.model_dump(), 'binding': binding})
    authorization = {**checked['snapshot'], 'actor': body.actor.model_dump(), 'review_token': body.review_token}
    await stack.db.conn.execute("UPDATE sequences SET state='active',authorization_json=? WHERE id=?",
                               (json.dumps(authorization), sid))
    await _changed(stack.db, sequence, 'sequence.authorized', {'actor': body.actor.model_dump(), 'review': checked})
    return await state(stack.db, sid)


async def decide(db, sid, key, body):
    sequence = await state(db, sid)
    step = next((step for step in sequence['steps'] if step['key'] == key), None)
    if not step:
        raise LookupError('This sequence step could not be found.')
    direction = await CreativeStore(db).direction(step['direction_id'])
    if (sequence['revision'] != body.revision or direction['revision'] != body.direction_revision
            or step['candidate_token'] != body.candidate_token):
        raise Conflict('The sequence, preparation or available material changed. Refresh before choosing.')
    if sequence['state'] == 'cancelled' or step['binding'] is not None:
        raise ValueError('This step is cancelled or already bound. Reuse the sequence to choose different sources.')
    required = {dep['step_key'] for dep in step['dependencies'] if dep['selection'] == 'chosen'}
    if not required or set(body.selections) != required:
        raise ValueError('Supply a choice for each dependency that requires judgment.')
    selected = {}
    for parent, ids in body.selections.items():
        available = {item['node_id']: item for item in step['candidates'][parent]}
        if not ids or len(set(ids)) != len(ids) or any(nid not in available for nid in ids):
            raise ValueError('Choose distinct available results from the named upstream step.')
        selected[parent] = [available[nid] for nid in ids]
    decision = {'actor': body.actor.model_dump(), 'reason': body.reason, 'selected': selected,
                'candidate_token': body.candidate_token, 'direction_revision': body.direction_revision,
                'instructions': sequence['instructions'], 'limits': sequence['limits']}
    await db.conn.execute('UPDATE sequence_steps SET decision_json=? WHERE sequence_id=? AND key=?',
                          (json.dumps(decision), sid, key))
    await _changed(db, sequence, 'sequence.decision', {'step_key': key, **decision})
    return await state(db, sid)


async def reuse(db, sid, body):
    sequence = await state(db, sid)
    creative = CreativeStore(db)
    definitions = []
    for step in sequence['steps']:
        template = await creative.direction(step['direction_id'])
        direction = await creative.new_direction(sequence['workspace_id'], template['title'],
            template['setup'], body.actor.model_dump())
        definitions.append({'key': step['key'], 'direction_id': direction['id'], 'revision': direction['revision'],
                            'input_mode': step['input_mode'], 'dependencies': step['dependencies']})
    return await create(db, sequence['workspace_id'], CreateSequence(**body.model_dump(),
        title=sequence['title'] + ' · reuse', instructions=sequence['instructions'], limits=sequence['limits'], steps=definitions))


async def refresh(stack, sid):
    from .operation_registry import refresh_for_direction
    await _sequence(stack.db, sid)
    for step in await _steps(stack.db, sid):
        await refresh_for_direction(stack, step['direction_id'])


async def guard_purge(db, nid):
    """Keep required bytes until dependent accepted work ends or is cancelled."""
    node = await db.get_node(nid)
    active = await db._one("SELECT 1 FROM slots s,json_each(s.dials_json,'$.parents') parent "
        "WHERE s.status IN ('queued','held','running','late') AND parent.value=?", (nid,))
    if active:
        raise ValueError('This material is required by active work. Wait for it to finish or confirm its cancellation before purging the source.')
    for sequence in await list_sequences(db, node['workspace_id']):
        if sequence['state'] != 'active':
            continue
        for step in sequence['steps']:
            if step['state'] == 'complete':
                continue
            direction = await CreativeStore(db).direction(step['direction_id'])
            fixed = {ref['node_id'] for ref in direction['setup'].get('references', [])}
            bound = {ref['node_id'] for item in (step['binding'] or {}).get('inputs', []) for ref in item['references']}
            required = fixed | bound | {item['node_id'] for values in step['candidates'].values() for item in values}
            if nid in required:
                raise ValueError('This material is required by an active sequence. Finish or cancel its dependent work before purging it.')
