"""Explicit dependency execution, participant decisions and enclosing limits."""

import asyncio

import pytest

from app import wiring
from app.executors import EngineFailure
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, project
from test_crexil_controls import control
from test_crexil_execution import attest, draft

pytestmark = pytest.mark.anyio


async def sequence(client, wid, *, selection='all_successful', limit=16, input_mode='each'):
    first = await draft(client, wid)
    second = await draft(client, wid, action='continue')
    response = await client.post(f'/api/crexil/projects/{wid}/sequences', json=command(
        title='Explore, then refine', instructions='Keep the silhouette.', limits={'attempt_limit': limit, 'spending_limit_usd': 0},
        steps=[{'key': 'explore', 'direction_id': first['id'], 'revision': first['revision']},
               {'key': 'refine', 'direction_id': second['id'], 'revision': second['revision'], 'input_mode': input_mode,
                'dependencies': [{'step_key': 'explore', 'selection': selection, 'contribution': 'Retain this silhouette.'}]}]))
    assert response.status_code == 201, response.text
    return response.json(), first, second


async def start(client, sid):
    checked = (await client.get(f'/api/crexil/sequences/{sid}/review')).json()
    assert checked['ready'], checked
    body = command(revision=checked['revision'], review_token=checked['token'])
    response = await client.post(f'/api/crexil/sequences/{sid}/start', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


async def wait_sequence(client, sid, states):
    for _ in range(150):
        wiring.current.sequences.wake.set()
        result = (await client.get(f'/api/crexil/sequences/{sid}')).json()
        if [step['state'] for step in result['steps']] == states:
            return result
        await asyncio.sleep(.02)
    raise AssertionError(result)


async def test_each_successful_result_runs_once_and_replayed_start_preserves_lineage(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, first, _ = await sequence(client, p['id'])
        sid = planned['id']
        body, receipt = await start(client, sid)
        done = await wait_sequence(client, sid, ['complete', 'complete'])
        replay = await client.post(f'/api/crexil/sequences/{sid}/start', json=body)
        assert replay.status_code == 200 and replay.json() == receipt
        assert len(fakes['comfy'].starts) == 6
        assert [len(step['runs']) for step in done['steps']] == [1, 2]
        assert done['accounting']['attempts'] == 6
        roots = (await store().get_turn(done['steps'][0]['runs'][0]['turn_id']))['slots']
        root_ids = {node['id'] for node in await store().list_nodes(p['id']) if node['slot_id'] in {slot['id'] for slot in roots}}
        for run in done['steps'][1]['runs']:
            turn = await store().get_turn(run['turn_id'])
            recorded = turn['dials']['crexil']
            assert recorded['sequence']['id'] == sid
            assert len(recorded['setup']['references']) == 1
            assert recorded['setup']['references'][0]['node_id'] in root_ids
        assert (await store().get_turn(done['steps'][0]['runs'][0]['turn_id']))['dials']['crexil']['sequence']['template_direction_id'] == first['id']
        await wiring.current.sequences.reconcile()
        assert len(fakes['comfy'].starts) == 6


async def test_failed_upstream_slot_does_not_remove_successful_dependency_or_independent_work(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        ex = fakes['comfy']
        original = ex.start
        async def one_failure(req):
            ex.default = Step(outcome=EngineFailure('error', 'one failure')) if not ex.starts else Step(data=image_bytes())
            return await original(req)
        monkeypatch.setattr(ex, 'start', one_failure)
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        await start(client, planned['id'])
        done = await wait_sequence(client, planned['id'], ['complete', 'complete'])
        assert len(done['steps'][1]['runs']) == 1
        assert len(ex.starts) == 4
        assert (await store().get_turn(done['steps'][0]['runs'][0]['turn_id']))['slots'][0]['status'] == 'failed'


async def test_sequence_limit_counts_earlier_runs_and_explicit_increase_unblocks_fanout(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'], limit=3)
        await start(client, planned['id'])
        held = await wait_sequence(client, planned['id'], ['complete', 'blocked'])
        assert held['accounting']['attempts'] == 2
        assert 'Sequence attempt limit' in held['steps'][1]['reason']
        changed = await client.patch(f'/api/crexil/sequences/{planned["id"]}', json=command(
            revision=held['revision'], title=held['title'], instructions=held['instructions'],
            limits={'attempt_limit': 6, 'spending_limit_usd': 0}))
        assert changed.status_code == 200, changed.text
        done = await wait_sequence(client, planned['id'], ['complete', 'complete'])
        assert done['accounting']['attempts'] == 6 and len(fakes['comfy'].starts) == 6


async def test_decision_requires_current_candidates_and_preserves_agent_reason(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, second = await sequence(client, p['id'], selection='chosen', input_mode='together')
        await start(client, planned['id'])
        waiting = await wait_sequence(client, planned['id'], ['complete', 'decision'])
        step = waiting['steps'][1]
        selected = step['candidates']['explore'][1]['node_id']
        body = command(revision=waiting['revision'], direction_revision=second['revision'], candidate_token=step['candidate_token'],
            selections={'explore': [selected]}, reason='This silhouette follows the brief.')
        body['actor'] = {'kind': 'agent', 'name': 'Studio guide'}
        stale = await client.post(f'/api/crexil/sequences/{planned["id"]}/steps/refine/decision', json={**body, 'candidate_token': 'stale'})
        assert stale.status_code == 409
        chosen = await client.post(f'/api/crexil/sequences/{planned["id"]}/steps/refine/decision', json=body)
        assert chosen.status_code == 200, chosen.text
        done = await wait_sequence(client, planned['id'], ['complete', 'complete'])
        decision = done['steps'][1]['decision']
        assert decision['actor']['name'] == 'Studio guide' and decision['reason'] == body['reason']
        turn = await store().get_turn(done['steps'][1]['runs'][0]['turn_id'])
        assert turn['dials']['crexil']['setup']['references'][0]['node_id'] == selected
        assert turn['dials']['crexil']['sequence']['decision'] == decision


async def test_sequence_cancel_prevents_unmaterialized_steps_after_project_resume(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        await control(client, 'project', p['id'], 'pause')
        planned, _, _ = await sequence(client, p['id'])
        await start(client, planned['id'])
        await control(client, 'sequence', planned['id'], 'cancel')
        await control(client, 'project', p['id'], 'resume')
        await wiring.current.sequences.reconcile()
        cancelled = (await client.get(f'/api/crexil/sequences/{planned["id"]}')).json()
        assert cancelled['state'] == 'cancelled'
        assert not await store().list_turns(p['id']) and not fakes['comfy'].starts
