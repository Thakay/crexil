"""Sequence preparation, durable adoption and scoped controls through shared actions."""

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image

from app import wiring
from app.crexil_sequences import Coordinator
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_controls import control, statuses
from test_crexil_execution import attest, draft
from test_crexil_sequence_edges import advance_until, single_step, snapshot
from test_crexil_sequences import sequence, start
from test_image_preparation import prepared_draft

pytestmark = pytest.mark.anyio


async def test_cancelling_project_work_does_not_relabel_finished_sequences(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        await start(client, planned['id'])
        done = await advance_until(client, planned['id'], ['complete', 'complete'])
        await control(client, 'project', p['id'], 'cancel')
        retained = await snapshot(client, planned['id'])
        assert retained['state'] == done['state'] == 'active'
        assert retained['steps'] == done['steps']
        assert retained['revision'] == done['revision']


async def test_preparation_before_and_after_generation_keeps_pixels_and_lineage(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        first = await prepared_draft(client, p['id'], [source['id']], {
            'mode': 'resize', 'width': 4, 'height': 3, 'resample': 'nearest', 'items': [{'node_id': source['id']}]})
        middle = await draft(client, p['id'], action='continue')
        last = await prepared_draft(client, p['id'], [], {'mode': 'resize', 'width': 2, 'height': 2, 'resample': 'nearest'})
        response = await client.post(f'/api/crexil/projects/{p["id"]}/sequences', json=command(
            title='Resize, generate, resize', limits={'attempt_limit': 2, 'spending_limit_usd': 0}, steps=[
                {'key': 'prepare', 'direction_id': first['id'], 'revision': first['revision']},
                {'key': 'generate', 'direction_id': middle['id'], 'revision': middle['revision'],
                 'dependencies': [{'step_key': 'prepare', 'selection': 'all_successful'}]},
                {'key': 'finish', 'direction_id': last['id'], 'revision': last['revision'], 'input_mode': 'each',
                 'dependencies': [{'step_key': 'generate', 'selection': 'all_successful'}]}]))
        assert response.status_code == 201, response.text
        sid = response.json()['id']
        await start(client, sid)
        done = await advance_until(client, sid, ['complete', 'complete', 'complete'])
        assert [len(step['runs']) for step in done['steps']] == [1, 1, 2]
        assert done['steps'][0]['runs'][0]['turn_id'] is None
        assert all(run['turn_id'] is None for run in done['steps'][2]['runs'])
        assert done['accounting']['attempts'] == 2 and done['accounting']['reserved_usd'] == 0
        assert len(await store().list_turns(p['id'])) == 1 and len(fakes['comfy'].starts) == 2
        assert not fakes['openrouter'].starts

        view = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        nodes = {node['id']: node for node in view['nodes']}
        prepared = next(node for node in nodes.values() if node['parents'] == [source['id']])
        expected = Image.open(io.BytesIO(image_bytes())).resize((4, 3), Image.Resampling.NEAREST)
        prepared_image = await client.get(f'/api/nodes/{prepared["id"]}/image')
        assert prepared_image.status_code == 200
        assert Image.open(io.BytesIO(prepared_image.content)).tobytes() == expected.tobytes()
        assert prepared['material']['origin']['sequence']['step_key'] == 'prepare'
        generated = [node for node in nodes.values() if node['parents'] == [prepared['id']]]
        assert len(generated) == 2
        finished = [node for node in nodes.values() if node['material'] and node['material']['origin'].get('kind') == 'prepared_image'
                    and node['id'] != prepared['id']]
        assert len(finished) == 2 and {node['parents'][0] for node in finished} == {node['id'] for node in generated}
        for node in finished:
            origin = node['material']['origin']
            result_image = await client.get(f'/api/nodes/{node["id"]}/image')
            assert result_image.status_code == 200 and Image.open(io.BytesIO(result_image.content)).size == (2, 2)
            assert origin['sequence']['id'] == sid and origin['sequence']['step_key'] == 'finish'
            assert origin['transform']['setup']['references'][0]['node_id'] == node['parents'][0]
        assert Path(source['output_path']).read_bytes() == image_bytes()
        await wiring.current.sequences.reconcile()
        assert len((await client.get(f'/api/crexil/projects/{p["id"]}')).json()['nodes']) == len(nodes)
        assert len(fakes['comfy'].starts) == 2


async def test_coordinator_restart_adopts_committed_run_without_duplicate_submission(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        sid = planned['id']
        scheduler = wiring.current.scheduler
        original = scheduler.adopt_prepared_turn
        attempted_adoptions = []
        async def interrupted_adoption(turn_id):
            # The acceptance transaction committed, but process interruption
            # prevented handing that durable run to the scheduler.
            attempted_adoptions.append(turn_id)
        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', interrupted_adoption)
        await start(client, sid)
        await wiring.current.sequences.reconcile()
        accepted = await snapshot(client, sid)
        root = accepted['steps'][0]['runs'][0]
        frozen = await statuses(root['turn_id'], ['queued', 'queued'])
        assert attempted_adoptions == [root['turn_id']] and not fakes['comfy'].starts
        assert not accepted['steps'][1]['runs']

        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', original)
        wiring.current.sequences = Coordinator(wiring.current)
        done = await advance_until(client, sid, ['complete', 'complete'])
        assert done['steps'][0]['runs'] == [root]
        assert (await store().get_turn(root['turn_id']))['dials'] == frozen['dials']
        assert [len(step['runs']) for step in done['steps']] == [1, 2]
        assert len(await store().list_turns(p['id'])) == 3 and len(fakes['comfy'].starts) == 6
        assert done['accounting']['attempts'] == 6
        for _ in range(2):
            await wiring.current.sequences.reconcile()
        replay = await snapshot(client, sid)
        assert [step['runs'] for step in replay['steps']] == [step['runs'] for step in done['steps']]
        assert len(fakes['comfy'].starts) == 6


async def test_sequence_pause_collects_running_work_and_resume_releases_future_steps(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        release = asyncio.Event()
        fakes['comfy'].default = Step(data=image_bytes(), gate=release)
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        sid = planned['id']
        await start(client, sid)
        await wiring.current.sequences.reconcile()
        before = await snapshot(client, sid)
        turn_id = before['steps'][0]['runs'][0]['turn_id']
        frozen = await statuses(turn_id, ['running', 'queued'])
        paused = await control(client, 'sequence', sid, 'pause', reason='Review the first result before continuing.')
        await statuses(turn_id, ['running', 'held'])
        release.set()
        await statuses(turn_id, ['landed', 'held'])
        wiring.current.sequences = Coordinator(wiring.current)
        await wiring.current.sequences.reconcile()
        waiting = await snapshot(client, sid)
        saved_control = (await client.get(f'/api/crexil/execution/sequence/{sid}')).json()
        assert saved_control['paused'] and saved_control['revision'] == paused['revision']
        assert not waiting['steps'][1]['runs'] and len(fakes['comfy'].starts) == 1
        assert len(await store().list_nodes(p['id'])) == 1

        await control(client, 'sequence', sid, 'resume')
        done = await advance_until(client, sid, ['complete', 'complete'])
        assert done['steps'][0]['runs'] == before['steps'][0]['runs']
        assert (await store().get_turn(turn_id))['dials'] == frozen['dials']
        assert done['accounting']['attempts'] == 6 and len(fakes['comfy'].starts) == 6
        assert all(slot['attempt'] == 1 for turn in await store().list_turns(p['id']) for slot in turn['slots'])


@pytest.mark.parametrize('mixed_batch', [False, True])
async def test_purge_required_source_refuses_whole_request_until_sequence_cancelled(tmp_path, monkeypatch, mixed_batch):
    async with booted(tmp_path, monkeypatch, raise_app_exceptions=False) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        p = await project(client)
        safe, required = [(await imported(client, p['id']))['node'] for _ in range(2)]
        template = await draft(client, p['id'], action='continue', references=[{'node_id': required['id']}])
        planned = await single_step(client, p['id'], template)
        sid = planned['id']
        await control(client, 'sequence', sid, 'pause')
        await start(client, sid)
        await wiring.current.sequences.reconcile()
        ids = [safe['id'], required['id']] if mixed_batch else [required['id']]
        for nid in ids:
            response = await client.post(f'/api/nodes/{nid}/set-aside')
            assert response.status_code == 200, response.text
        response = await client.post(f'/api/workspaces/{p["id"]}/purge', json={'ids': ids})
        retained = [await store().get_node(nid) for nid in ids]
        assert all(node['purged_at'] is None and Path(node['output_path']).is_file() for node in retained), response.text
        assert response.status_code == 422, response.text
        assert 'required by an active sequence' in response.json()['detail']
        assert not await store().list_turns(p['id']) and not fakes['comfy'].starts
        await control(client, 'sequence', sid, 'cancel')
        released = await client.post(f'/api/workspaces/{p["id"]}/purge', json={'ids': ids})
        assert released.status_code == 200 and released.json()['purged'] == len(ids)
        assert all(not Path(node['output_path']).exists() for node in retained)


@pytest.mark.parametrize('invalid', ['self_cycle', 'mutual_cycle', 'cross_project'])
async def test_invalid_sequence_definition_leaves_no_partial_membership(tmp_path, monkeypatch, invalid):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        attest()
        p = await project(client)
        first, second = await draft(client, p['id']), await draft(client, p['id'])
        steps = [{'key': 'one', 'direction_id': first['id'], 'revision': first['revision']},
                 {'key': 'two', 'direction_id': second['id'], 'revision': second['revision']}]
        if invalid == 'self_cycle':
            steps[0]['dependencies'] = [{'step_key': 'one', 'selection': 'all_successful'}]
        elif invalid == 'mutual_cycle':
            steps[0]['dependencies'] = [{'step_key': 'two', 'selection': 'all_successful'}]
            steps[1]['dependencies'] = [{'step_key': 'one', 'selection': 'all_successful'}]
        else:
            other = await project(client)
            foreign = await draft(client, other['id'])
            steps[1]['direction_id'] = foreign['id']
        url = f'/api/crexil/projects/{p["id"]}/sequences'
        response = await client.post(url, json=command(title='Invalid sequence', steps=steps))
        assert response.status_code == 422, response.text
        assert ('this project' if invalid == 'cross_project' else 'loop') in response.json()['detail']
        view = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        assert not view['sequences'] and not view['turns']
        assert all(direction['sequence_id'] is None and direction['state'] == 'draft' for direction in view['directions'])
        assert not any(event['type'] == 'sequence.created' for event in await store().events_since(0))
        valid = await client.post(url, json=command(title='Both preparations still usable', steps=[
            {'key': 'one', 'direction_id': first['id'], 'revision': first['revision']},
            {'key': 'two', 'direction_id': second['id'], 'revision': second['revision']}]))
        assert valid.status_code == 201, valid.text
        assert not fakes['comfy'].starts
