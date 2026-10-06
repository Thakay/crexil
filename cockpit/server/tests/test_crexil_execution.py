"""Shared submission boundaries: review, durable acceptance and lane adoption."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from app import config, wiring
from app.executors import EngineFailure
from app.scheduler import Scheduler
from app.crexil_execution import workflow_hashes
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project

pytestmark = pytest.mark.anyio


def attest():
    for preset in wiring.current.catalog.values():
        preset['execution_cost'] = {'maximum_usd_per_attempt': 0, 'workflow_sha256': workflow_hashes(preset)}


async def draft(client, wid, **setup):
    ops = (await client.get('/api/crexil/operations')).json()
    op = next(o for o in ops if o['id'] == 'testpreset')
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(
        title='Brass', setup={'prompt': 'brass lamp', 'operations': [
            {'operation_id': op['id'], 'revision': op['revision'], 'count': 2}], **setup}))
    assert response.status_code == 201, response.text
    return response.json()


async def reviewed(client, direction):
    response = await client.get(f"/api/crexil/directions/{direction['id']}/review")
    assert response.status_code == 200, response.text
    return response.json()


async def settled(wid):
    for _ in range(100):
        turns = await store().list_turns(wid)
        if turns and all(s['status'] in ('landed', 'failed', 'skipped') for t in turns for s in t['slots']):
            return turns
        await asyncio.sleep(.01)
    raise AssertionError('Slots did not settle')


async def test_submit_freezes_preparation_and_concurrent_delivery_dispatches_once(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        source = await imported(client, p['id'])
        direction = await draft(client, p['id'], action='continue', references=[
            {'node_id': source['node']['id'], 'contribution': 'Keep the worn brass'}])
        review = await reviewed(client, direction)
        assert review['ready'], review['errors']
        assert review['maximum_usd'] == 0
        assert fakes['comfy'].starts == []
        assert await store().list_turns(p['id']) == []
        body = command(revision=direction['revision'], review_token=review['token'])
        body['actor'] = {'kind': 'agent', 'name': 'Studio assistant'}
        url = f"/api/crexil/directions/{direction['id']}/submit"
        a, b = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
        assert a.status_code == b.status_code == 201, (a.text, b.text)
        assert a.json() == b.json()
        turns = await settled(p['id'])
        assert len(turns) == 1 and len(turns[0]['slots']) == 2
        assert len(fakes['comfy'].starts) == 2
        assert fakes['comfy'].probe.max_concurrent == 1
        frozen = turns[0]['dials']['crexil']
        assert frozen['actor']['name'] == 'Studio assistant'
        assert frozen['sources'][0]['sha256'] == hashlib.sha256(image_bytes()).hexdigest()
        nodes = [n for n in await store().list_nodes(p['id']) if n['turn_id']]
        assert len(nodes) == 2
        assert all(n['parents'] == [source['node']['id']] for n in nodes)
        assert 'Keep the worn brass' in nodes[0]['submitted_graph']['1']['inputs']['text']
        assert nodes[0]['prompt'] == nodes[0]['submitted_graph']['1']['inputs']['text']
        replay = await client.post(url, json=body)
        assert replay.json() == a.json()
        assert len(fakes['comfy'].starts) == 2
        stale = await client.patch(f"/api/crexil/directions/{direction['id']}", json=command(
            revision=1, title='Changed', setup={'prompt': 'change submitted work'}))
        assert stale.status_code == 409
        events = await store().events_since(0)
        assert sum(e['type'] == 'direction.submitted' for e in events) == 1


async def test_review_rejects_clamping_missing_capability_stale_graph_and_unknown_cost(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        direction = await draft(client, p['id'], controls={'steps': 40, 'made_up': True}, attempt_limit=1)
        review = await reviewed(client, direction)
        assert not review['ready']
        assert any('no enforceable cost bound' in e for e in review['errors'])
        assert any('not supported' in e for e in review['errors'])
        assert any('limit is 1' in e for e in review['errors'])
        attest()
        direction = await draft(client, p['id'], controls={'steps': 40})
        review = await reviewed(client, direction)
        assert any('between 1 and 16' in e for e in review['errors'])
        assert direction['setup']['controls']['steps'] == 40
        source = await imported(client, p['id'])
        other = await imported(client, p['id'])
        combined = await draft(client, p['id'], action='combine', references=[
            {'node_id': source['node']['id']}, {'node_id': other['node']['id']}])
        review = await reviewed(client, combined)
        assert any('cannot perform combine with 2 sources' in e for e in review['errors'])
        valid = await draft(client, p['id'])
        before = await reviewed(client, valid)
        assert before['ready']
        graph_path = config.WORKFLOWS_DIR / 'test-txt2img@v1.json'
        graph_path.write_text(graph_path.read_text() + '\n')
        after = await reviewed(client, valid)
        assert not after['ready']
        assert any('changed since preparation' in e for e in after['errors'])
        assert any('no enforceable cost bound' in e for e in after['errors'])
        result = await client.post(f"/api/crexil/directions/{valid['id']}/submit", json=command(
            revision=0, review_token=before['token']))
        assert result.status_code == 409
        assert await store().list_turns(p['id']) == []
        assert fakes['comfy'].starts == []


async def test_lost_response_after_commit_replays_durable_slots_without_new_work(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, raise_app_exceptions=False) as (client, fakes):
        attest()
        p = await project(client)
        direction = await draft(client, p['id'])
        review = await reviewed(client, direction)
        body = command(revision=0, review_token=review['token'])
        scheduler = wiring.current.scheduler
        async def interrupted(turn_id):
            raise RuntimeError('Connection lost after durable acceptance')
        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', interrupted)
        url = f"/api/crexil/directions/{direction['id']}/submit"
        result = await client.post(url, json=body)
        assert result.status_code == 500
        turns = await store().list_turns(p['id'])
        assert len(turns) == 1 and all(s['status'] == 'queued' for s in turns[0]['slots'])
        assert fakes['comfy'].starts == []
        # Recovery, rather than the original request handler, owns adoption.
        await scheduler.stop()
        scheduler = Scheduler(store(), list(fakes.values()), route=lambda engine: 'openrouter' if '/' in engine else 'comfy')
        wiring.current.scheduler = scheduler
        await scheduler.start()
        await settled(p['id'])
        replay = await client.post(url, json=body)
        assert replay.status_code == 201, replay.text
        assert replay.json()['turn_id'] == turns[0]['id']
        assert len(await store().list_turns(p['id'])) == 1
        assert len(fakes['comfy'].starts) == 2
        changed = await client.post(url, json={**body, 'review_token': 'a' * 64})
        assert changed.status_code == 409


async def test_partial_failure_does_not_shift_slots_or_silently_exceed_attempt_limit(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        p = await project(client)
        direction = await draft(client, p['id'], attempt_limit=2)
        review = await reviewed(client, direction)
        scheduler = wiring.current.scheduler
        adopt = scheduler.adopt_prepared_turn
        async def mixed(turn_id):
            turn = await store().get_turn(turn_id)
            fakes['comfy'].script[turn['slots'][0]['id']] = Step(outcome=EngineFailure('rate_limit', 'Slow down'))
            fakes['comfy'].script[turn['slots'][1]['id']] = Step(data=image_bytes())
            await adopt(turn_id)
        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', mixed)
        response = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=0, review_token=review['token']))
        assert response.status_code == 201, response.text
        turns = await settled(p['id'])
        assert [(s['pos'], s['status']) for s in turns[0]['slots']] == [(0, 'failed'), (1, 'landed')]
        assert len(fakes['comfy'].starts) == 2
        nodes = await store().list_nodes(p['id'])
        assert len(nodes) == 1 and Path(nodes[0]['output_path']).read_bytes() == image_bytes()
        snap = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        assert snap['directions'][0]['node_ids'] == [nodes[0]['id']]
        assert snap['directions'][0]['selection'] == []


async def test_ready_requests_run_while_blocked_requests_remain_editable(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        source = await imported(client, p['id'])
        operations = {op['id']: op for op in (await client.get('/api/crexil/operations')).json()}
        direction = await draft(client, p['id'], action='continue', references=[{'node_id': source['node']['id']}],
            operations=[{'operation_id': key, 'revision': operations[key]['revision'], 'count': count}
                        for key, count in [('testpreset', 2), ('txtonly', 1)]])
        review = await reviewed(client, direction)
        assert review['ready'] and review['ready_attempts'] == 2 and review['blocked_attempts'] == 1
        assert any('cannot perform continue' in error for error in review['errors'])
        url = f"/api/crexil/directions/{direction['id']}/submit"
        body = command(revision=0, review_token=review['token'])
        accepted = await client.post(url, json=body)
        assert accepted.status_code == 201, accepted.text
        remaining = accepted.json()['direction']
        assert remaining['state'] == 'draft' and remaining['revision'] == 1
        assert [op['operation_id'] for op in remaining['setup']['operations']] == ['txtonly']
        assert remaining['setup']['references'] == direction['setup']['references']
        first_turns = await settled(p['id'])
        assert len(first_turns) == 1 and len(first_turns[0]['slots']) == 2
        original_record = first_turns[0]['dials']['crexil']
        assert [op['operation_id'] for op in original_record['setup']['operations']] == ['testpreset', 'txtonly']
        assert (await client.post(url, json=body)).json() == accepted.json()
        assert len(fakes['comfy'].starts) == 2
        revised = await client.patch(f"/api/crexil/directions/{direction['id']}", json=command(
            revision=1, title=direction['title'], setup={**remaining['setup'], 'operations':[
                {'operation_id':'testpreset', 'revision':operations['testpreset']['revision'], 'count':1}]}))
        assert revised.status_code == 200, revised.text
        next_review = await reviewed(client, revised.json())
        assert next_review['ready_attempts'] == 1 and next_review['blocked_attempts'] == 0
        next_run = await client.post(url, json=command(revision=2, review_token=next_review['token']))
        assert next_run.status_code == 201, next_run.text
        final_turns = await settled(p['id'])
        assert len(final_turns) == 2 and len(fakes['comfy'].starts) == 3
        assert final_turns[0]['dials']['crexil'] == original_record
        assert next_run.json()['direction']['state'] == 'submitted'
