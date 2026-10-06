"""Sequence source binding and failure isolation through shared artist actions."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from app import api_v4, wiring
from app.executors import CollectedOutput, EngineFailure
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import attest, draft, settled
from test_crexil_sequences import sequence, start

pytestmark = pytest.mark.anyio


async def snapshot(client, sid):
    response = await client.get(f'/api/crexil/sequences/{sid}')
    assert response.status_code == 200, response.text
    return response.json()


async def advance_until(client, sid, states):
    # Tests stop only the background polling task and drive its public
    # reconciliation boundary explicitly, keeping scheduler execution real.
    for _ in range(100):
        await wiring.current.sequences.reconcile()
        result = await snapshot(client, sid)
        if [step['state'] for step in result['steps']] == states:
            return result
        await asyncio.sleep(.01)
    raise AssertionError(result)


async def single_step(client, wid, direction, *, limit=16):
    response = await client.post(f'/api/crexil/projects/{wid}/sequences', json=command(
        title=direction['title'], limits={'attempt_limit': limit, 'spending_limit_usd': 0},
        steps=[{'key': 'work', 'direction_id': direction['id'], 'revision': direction['revision']}]))
    assert response.status_code == 201, response.text
    return response.json()


async def select_material(client, current, template, nid):
    step = current['steps'][1]
    response = await client.post(f'/api/crexil/sequences/{current["id"]}/steps/refine/decision', json=command(
        revision=current['revision'], direction_revision=template['revision'], candidate_token=step['candidate_token'],
        selections={'explore': [nid]}, reason='Continue from this exact retained image.'))
    assert response.status_code == 200, response.text
    return response.json()


async def test_reauthorization_redirects_fixed_source_of_bound_unaccepted_work(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        a, b = await imported(client, p['id']), await imported(client, p['id'])
        template = await draft(client, p['id'], action='continue', references=[{'node_id': a['node']['id']}])
        planned = await single_step(client, p['id'], template, limit=1)
        sid = planned['id']
        await start(client, sid)
        blocked = await advance_until(client, sid, ['blocked'])
        assert 'Sequence attempt limit' in blocked['steps'][0]['reason']
        assert blocked['steps'][0]['binding']['inputs'][0]['references'][0]['node_id'] == a['node']['id']
        assert not blocked['steps'][0]['runs'] and not fakes['comfy'].starts

        response = await client.patch(f'/api/crexil/directions/{template["id"]}', json=command(
            revision=template['revision'], title=template['title'],
            setup={**template['setup'], 'references': [{'node_id': b['node']['id'], 'contribution': 'Use this replacement.'}]}))
        assert response.status_code == 200, response.text
        changed = response.json()
        await start(client, sid)
        current = await snapshot(client, sid)
        response = await client.patch(f'/api/crexil/sequences/{sid}', json=command(
            revision=current['revision'], title=current['title'], instructions=current['instructions'],
            limits={'attempt_limit': 2, 'spending_limit_usd': 0}))
        assert response.status_code == 200, response.text
        done = await advance_until(client, sid, ['complete'])
        assert len(done['steps'][0]['runs']) == 1 and len(fakes['comfy'].starts) == 2
        run = await store().get_turn(done['steps'][0]['runs'][0]['turn_id'])
        frozen = run['dials']['crexil']
        assert frozen['setup']['references'] == changed['setup']['references']
        assert frozen['sequence']['template_revision'] == changed['revision']
        outputs = [node for node in await store().list_nodes(p['id']) if node['turn_id'] == run['id']]
        assert outputs and all(node['parents'] == [b['node']['id']] for node in outputs)
        assert not any(a['node']['id'] in node['parents'] for node in outputs)


async def test_failed_and_imported_material_require_choice_instead_of_automatic_fanout(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        executor = fakes['comfy']
        original = executor.start
        async def partial_failure(req):
            handle = await original(req)
            if not req.sources:
                await req.on_output(CollectedOutput(identity='partial', filename='partial.png', data=image_bytes()))
                handle.step = Step(outcome=EngineFailure('error', 'failed after returning partial material'))
            else:
                handle.step = Step(data=image_bytes())
            return handle
        monkeypatch.setattr(executor, 'start', partial_failure)
        p = await project(client)
        planned, _, second = await sequence(client, p['id'])
        sid = planned['id']
        await start(client, sid)
        blocked = await advance_until(client, sid, ['complete', 'blocked'])
        upstream = blocked['steps'][0]['runs'][0]
        imported_result = await imported(client, p['id'], direction_id=upstream['direction_id'])
        await wiring.current.sequences.reconcile()
        blocked = await snapshot(client, sid)
        candidates = blocked['steps'][1]['candidates']['explore']
        assert len(candidates) == 3 and all(not item['successful'] for item in candidates)
        assert imported_result['node']['id'] in {item['node_id'] for item in candidates}
        assert blocked['steps'][1]['binding'] is None and not blocked['steps'][1]['runs']
        assert len(executor.starts) == 2
        partial = next(item for item in candidates if item['slot_id'])

        response = await client.patch(f'/api/crexil/sequences/{sid}/steps/refine', json=command(
            revision=blocked['revision'], input_mode='together',
            dependencies=[{'step_key': 'explore', 'selection': 'chosen', 'contribution': 'Use the retained partial deliberately.'}]))
        assert response.status_code == 200, response.text
        await start(client, sid)
        waiting = await advance_until(client, sid, ['complete', 'decision'])
        await select_material(client, waiting, second, partial['node_id'])
        done = await advance_until(client, sid, ['complete', 'complete'])
        assert len(done['steps'][1]['runs']) == 1 and len(executor.starts) == 4
        child = await store().get_turn(done['steps'][1]['runs'][0]['turn_id'])
        assert child['dials']['crexil']['setup']['references'][0]['node_id'] == partial['node_id']
        assert child['dials']['crexil']['sequence']['decision']['selected']['explore'][0]['node_id'] == partial['node_id']


async def test_http_preflight_refusal_of_first_fanout_input_keeps_later_input_eligible(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        sid = planned['id']
        await start(client, sid)
        await wiring.current.sequences.reconcile()
        await settled(p['id'])
        current = await snapshot(client, sid)
        candidates = current['steps'][1]['candidates']['explore']
        refused, allowed = [item['node_id'] for item in candidates]
        original = api_v4._refuse_if_disk_cannot_hold
        async def refuse_one_input(db, areas):
            row = await db._one('SELECT setup_json FROM directions ORDER BY rowid DESC LIMIT 1')
            references = json.loads(row['setup_json'])['references']
            if references and references[0]['node_id'] == refused:
                raise HTTPException(422, 'This input needs more staging space.')
            await original(db, areas)
        monkeypatch.setattr(api_v4, '_refuse_if_disk_cannot_hold', refuse_one_input)
        blocked = await advance_until(client, sid, ['complete', 'blocked'])
        await settled(p['id'])
        assert 'Input 1: This input needs more staging space.' in blocked['steps'][1]['reason']
        assert len(blocked['steps'][1]['runs']) == 1
        child = await store().get_turn(blocked['steps'][1]['runs'][0]['turn_id'])
        assert child['dials']['crexil']['setup']['references'][0]['node_id'] == allowed
        assert len(fakes['comfy'].starts) == 4
        await wiring.current.sequences.reconcile()
        again = await snapshot(client, sid)
        assert again['steps'][1]['runs'] == blocked['steps'][1]['runs']
        assert len(fakes['comfy'].starts) == 4


async def test_http_preflight_refusal_of_first_sequence_keeps_other_sequence_eligible(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        large = await single_step(client, p['id'], await draft(client, p['id'], prompt='large request'))
        small = await single_step(client, p['id'], await draft(client, p['id'], prompt='small request'))
        await start(client, large['id'])
        await start(client, small['id'])
        original = api_v4._refuse_if_disk_cannot_hold
        async def refuse_large(db, areas):
            row = await db._one('SELECT setup_json FROM directions ORDER BY rowid DESC LIMIT 1')
            if json.loads(row['setup_json'])['prompt'] == 'large request':
                raise HTTPException(422, 'The large request cannot fit on this drive.')
            await original(db, areas)
        monkeypatch.setattr(api_v4, '_refuse_if_disk_cannot_hold', refuse_large)
        done = await advance_until(client, small['id'], ['complete'])
        blocked = await snapshot(client, large['id'])
        assert blocked['steps'][0]['state'] == 'blocked'
        assert 'The large request cannot fit on this drive.' in blocked['steps'][0]['reason']
        assert not blocked['steps'][0]['runs']
        assert len(done['steps'][0]['runs']) == 1 and len(fakes['comfy'].starts) == 2


async def test_chosen_collecting_material_remains_bound_when_attempt_completes(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        entered, release = asyncio.Event(), asyncio.Event()
        executor = fakes['comfy']
        executor.default = Step(data=image_bytes())
        original = executor.start
        async def collect_before_completion(req):
            handle = await original(req)
            if len(executor.starts) == 1:
                await req.on_output(CollectedOutput(identity='early', filename='early.png', data=image_bytes()))
                handle.step = Step(data=image_bytes(), gate=release)
                entered.set()
            return handle
        monkeypatch.setattr(executor, 'start', collect_before_completion)
        p = await project(client)
        planned, _, second = await sequence(client, p['id'], selection='chosen', input_mode='together')
        sid = planned['id']
        await start(client, sid)
        await wiring.current.sequences.reconcile()
        await asyncio.wait_for(entered.wait(), 1)
        collecting = await snapshot(client, sid)
        candidate = collecting['steps'][1]['candidates']['explore'][0]
        assert not candidate['successful']
        await select_material(client, collecting, second, candidate['node_id'])
        release.set()
        await settled(p['id'])
        completed = await snapshot(client, sid)
        now = next(item for item in completed['steps'][1]['candidates']['explore'] if item['node_id'] == candidate['node_id'])
        assert now['successful'] and now['sha256'] == candidate['sha256']
        done = await advance_until(client, sid, ['complete', 'complete'])
        child = await store().get_turn(done['steps'][1]['runs'][0]['turn_id'])
        assert child['dials']['crexil']['setup']['references'][0]['node_id'] == candidate['node_id']
        assert done['steps'][1]['decision']['selected']['explore'][0] == candidate
        assert len(executor.starts) == 4
