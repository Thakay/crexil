"""Ordered mesh dependencies and companion evidence use shared sequence actions."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app import wiring
from app.mesh import FIELDS_SUFFIX, fields_sidecar
from fakes import Step
from test_api_v4 import store
from test_api_v4_mesh import booted_mesh
from test_crexil import command, imported, project
from test_crexil_mesh import draft_mesh, submit
from test_crexil_sequence_edges import advance_until, single_step, snapshot
from test_crexil_sequences import start
from test_image_preparation import prepared_draft
from test_mesh import GLB, NPZ

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def forbidden(_transport, request):
        raise AssertionError(f'Sequence regression attempted real HTTP: {request.url}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)


async def plan(client, wid, steps, *, limit=16):
    response = await client.post(f'/api/crexil/projects/{wid}/sequences', json=command(
        title='Mesh sequence', limits={'attempt_limit': limit, 'spending_limit_usd': 0}, steps=steps))
    assert response.status_code == 201, response.text
    return response.json()


def step(key, direction, **values):
    return {'key': key, 'direction_id': direction['id'], 'revision': direction['revision'], **values}


async def limits(client, current, attempts):
    response = await client.patch(f"/api/crexil/sequences/{current['id']}", json=command(
        revision=current['revision'], title=current['title'], instructions=current['instructions'],
        limits={'attempt_limit': attempts, 'spending_limit_usd': 0}))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('input_mode,count', [('each', 2), ('together', 1)])
async def test_dependency_mesh_precedes_fixed_appearance_in_texture_run(tmp_path, monkeypatch, input_mode, count):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        fakes['mesh'].default = Step(data=GLB)
        wid = (await project(client))['id']
        image = (await imported(client, wid))['node']
        generate = await draft_mesh(client, wid, [image], count=count)
        paint = await draft_mesh(client, wid, [image], action='texture', engine='hunyuan3d-21')
        planned = await plan(client, wid, [step('mesh', generate), step('paint', paint,
            input_mode=input_mode, dependencies=[{'step_key': 'mesh', 'selection': 'all_successful',
                                                  'placement': 'before_fixed'}])])
        body, receipt = await start(client, planned['id'])
        done = await advance_until(client, planned['id'], ['complete', 'complete'])
        assert len(done['steps'][1]['runs']) == count
        assert len(fakes['mesh'].starts) == count * 2
        for run in done['steps'][1]['runs']:
            turn = await store().get_turn(run['turn_id'])
            refs = turn['dials']['crexil']['setup']['references']
            shape = await store().get_node(refs[0]['node_id'])
            assert shape['payload'] == '3d' and refs[1]['node_id'] == image['id']
            job = turn['slots'][0]['submitted_graph']['mesh_job']
            assert job['source_path'] == shape['output_path'] and job['ref_path'] == image['output_path']
            outputs = [node for node in await store().list_nodes(wid) if node['turn_id'] == turn['id']]
            assert outputs[0]['parents'] == [shape['id'], image['id']]
            assert turn['dials']['crexil']['sequence']['binding']['fixed_start'] == 1
        replay = await client.post(f"/api/crexil/sequences/{planned['id']}/start", json=body)
        assert replay.status_code == 200 and replay.json() == receipt
        await wiring.current.sequences.reconcile()
        assert len(fakes['mesh'].starts) == count * 2


@pytest.mark.parametrize('dependency_changed', [False, True])
async def test_reauthorization_redirects_only_fixed_appearance_after_dependency(tmp_path, monkeypatch, dependency_changed):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        fakes['mesh'].default = Step(data=GLB)
        wid = (await project(client))['id']
        image, replacement = [(await imported(client, wid))['node'] for _ in range(2)]
        generate = await draft_mesh(client, wid, [image])
        paint = await draft_mesh(client, wid, [image], action='texture', engine='hunyuan3d-21')
        planned = await plan(client, wid, [step('mesh', generate), step('paint', paint,
            dependencies=[{'step_key': 'mesh', 'selection': 'all_successful', 'placement': 'before_fixed'}])], limit=1)
        sid = planned['id']
        await start(client, sid)
        blocked = await advance_until(client, sid, ['complete', 'blocked'])
        before = blocked['steps'][1]['binding']['inputs'][0]
        shape_id = before['references'][0]['node_id']
        assert before['fixed_start'] == 1 and not blocked['steps'][1]['runs']
        response = await client.patch(f"/api/crexil/directions/{paint['id']}", json=command(
            revision=paint['revision'], title=paint['title'],
            setup={**paint['setup'], 'references': [{'node_id': replacement['id']}]}))
        assert response.status_code == 200, response.text
        if dependency_changed:
            Path(before['sources'][0]['mesh_fields']['path']).write_bytes(NPZ)
        await start(client, sid)
        await limits(client, await snapshot(client, sid), 2)
        if dependency_changed:
            blocked = await advance_until(client, sid, ['complete', 'blocked'])
            assert 'bound source' in blocked['steps'][1]['reason']
            assert blocked['steps'][1]['binding']['inputs'][0]['sources'][0] == before['sources'][0]
            assert not blocked['steps'][1]['runs'] and len(fakes['mesh'].starts) == 1
            return
        done = await advance_until(client, sid, ['complete', 'complete'])
        turn = await store().get_turn(done['steps'][1]['runs'][0]['turn_id'])
        assert [ref['node_id'] for ref in turn['dials']['crexil']['setup']['references']] == [shape_id, replacement['id']]
        assert turn['dials']['crexil']['sequence']['binding']['sources'][0] == before['sources'][0]
        assert len(fakes['mesh'].starts) == 2


async def test_mixed_dependency_placement_preserves_each_group_order_and_legacy_default(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        wid = (await project(client))['id']
        image = (await imported(client, wid))['node']
        roots = [await prepared_draft(client, wid, [image['id']],
            {'mode': 'resize', 'width': 2, 'height': 2, 'resample': 'nearest',
             'items': [{'node_id': image['id']}]}) for _ in range(4)]
        final = await prepared_draft(client, wid, [image['id']], {'mode': 'arrange',
            'width': 10, 'height': 2, 'items': [{'node_id': image['id'],
                'placement': {'x': i * 2, 'y': 0, 'width': 2, 'height': 2}} for i in range(5)]})
        dependencies = [{'step_key': str(i), 'selection': 'all_successful',
                         'placement': 'before_fixed' if i % 2 else 'after_fixed'} for i in range(4)]
        planned = await plan(client, wid, [*(step(str(i), direction) for i, direction in enumerate(roots)),
                                          step('arrange', final, dependencies=dependencies)])
        sid = planned['id']
        # A pre-extension stored dependency has no placement key. It must
        # remain after the fixed sources when read through current actions.
        for dependency in dependencies:
            dependency['contribution'] = ''
            if dependency['placement'] == 'after_fixed':
                del dependency['placement']
        async with store()._tx():
            await store().conn.execute("UPDATE sequence_steps SET dependencies_json=? WHERE sequence_id=? AND key='arrange'",
                                       (json.dumps(dependencies), sid))
        current = await snapshot(client, sid)
        assert current['steps'][-1]['dependencies'][0]['placement'] == 'after_fixed'
        await start(client, sid)
        done = await advance_until(client, sid, ['complete'] * 5)
        ids = []
        for root in done['steps'][:4]:
            direction = root['runs'][0]['direction_id']
            row = await store()._one('SELECT node_id FROM direction_material WHERE direction_id=?', (direction,))
            ids.append(row['node_id'])
        binding = done['steps'][-1]['binding']['inputs'][0]
        assert [ref['node_id'] for ref in binding['references']] == [ids[1], ids[3], image['id'], ids[0], ids[2]]
        assert binding['fixed_start'] == 2
        assert all('mesh_fields' not in fact for fact in binding['sources'])
        assert not fakes['mesh'].starts and not fakes['comfy'].starts


async def test_fixed_voxel_fields_require_new_review_before_start_and_after_binding(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        fakes['mesh'].default = Step(data=GLB, sidecars={FIELDS_SUFFIX: NPZ})
        wid = (await project(client))['id']
        image = (await imported(client, wid))['node']
        _, meshes = await submit(client, await draft_mesh(client, wid, [image]))
        source = meshes[0]
        field = fields_sidecar(source['output_path'])
        reduce = await draft_mesh(client, wid, [source], action='decimate', controls={'target_triangles': 1000})
        planned = await single_step(client, wid, reduce, limit=0)
        sid = planned['id']
        checked = (await client.get(f'/api/crexil/sequences/{sid}/review')).json()
        fact = checked['snapshot']['templates']['work']['sources'][0]['mesh_fields']
        assert fact['sha256'] == hashlib.sha256(NPZ).hexdigest()
        assert fact['identity'] == 'companion:' + FIELDS_SUFFIX and fact['output_id']
        field.write_bytes(b'changed after review')
        response = await client.post(f'/api/crexil/sequences/{sid}/start', json=command(
            revision=checked['revision'], review_token=checked['token']))
        assert response.status_code == 409 and len(fakes['mesh'].starts) == 1

        await start(client, sid)
        blocked = await advance_until(client, sid, ['blocked'])
        assert 'Sequence attempt limit' in blocked['steps'][0]['reason']
        assert blocked['steps'][0]['binding'] and not blocked['steps'][0]['runs']
        field.write_bytes(b'changed after start and binding')
        await limits(client, blocked, 1)
        blocked = await advance_until(client, sid, ['blocked'])
        assert 'saved source changed' in blocked['steps'][0]['reason']
        assert len(fakes['mesh'].starts) == 1
        await start(client, sid)
        done = await advance_until(client, sid, ['complete'])
        turn = await store().get_turn(done['steps'][0]['runs'][0]['turn_id'])
        expected = hashlib.sha256(field.read_bytes()).hexdigest()
        assert turn['dials']['crexil']['sequence']['binding']['sources'][0]['mesh_fields']['sha256'] == expected
        assert turn['slots'][0]['submitted_graph']['mesh_job']['source_hashes'][-1]['sha256'] == expected
        assert len(fakes['mesh'].starts) == 2


@pytest.mark.parametrize('change', ['changed', 'removed', 'appeared'])
async def test_voxel_field_changes_stale_candidate_decisions_and_bound_inputs(tmp_path, monkeypatch, change):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        await wiring.current.sequences.stop()
        fakes['mesh'].default = Step(data=GLB, sidecars=None if change == 'appeared' else {FIELDS_SUFFIX: NPZ})
        wid = (await project(client))['id']
        image = (await imported(client, wid))['node']
        generate = await draft_mesh(client, wid, [image])
        reduce = await draft_mesh(client, wid, [], action='decimate', engine='gltfpack', controls={'target_triangles': 1000})
        planned = await plan(client, wid, [step('mesh', generate), step('reduce', reduce,
            dependencies=[{'step_key': 'mesh', 'selection': 'chosen'}])], limit=1)
        sid = planned['id']
        await start(client, sid)
        waiting = await advance_until(client, sid, ['complete', 'decision'])
        candidate = waiting['steps'][1]['candidates']['mesh'][0]
        assert (candidate['mesh_fields']['sha256'] is None) == (change == 'appeared')
        field = Path(candidate['mesh_fields']['path'])
        if change == 'removed':
            field.unlink()
        else:
            field.write_bytes(b'new candidate fields')
        body = command(revision=waiting['revision'], direction_revision=reduce['revision'],
            candidate_token=waiting['steps'][1]['candidate_token'], selections={'mesh': [candidate['node_id']]},
            reason='Use this exact retained mesh.')
        response = await client.post(f'/api/crexil/sequences/{sid}/steps/reduce/decision', json=body)
        assert response.status_code == 409
        current = await snapshot(client, sid)
        assert current['steps'][1]['candidate_token'] != waiting['steps'][1]['candidate_token']
        response = await client.post(f'/api/crexil/sequences/{sid}/steps/reduce/decision', json={**body,
            **command(), 'revision': current['revision'], 'candidate_token': current['steps'][1]['candidate_token']})
        assert response.status_code == 200, response.text
        blocked = await advance_until(client, sid, ['complete', 'blocked'])
        assert 'Sequence attempt limit' in blocked['steps'][1]['reason']
        original_binding = blocked['steps'][1]['binding']
        field.write_bytes(b'changed after material was bound')
        await limits(client, blocked, 2)
        blocked = await advance_until(client, sid, ['complete', 'blocked'])
        assert 'bound source' in blocked['steps'][1]['reason']
        assert blocked['steps'][1]['binding'] == original_binding
        assert not blocked['steps'][1]['runs'] and len(fakes['mesh'].starts) == 1
