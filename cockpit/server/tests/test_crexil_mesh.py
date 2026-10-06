"""Bounded 3D uses shared acceptance, source identities and normal lane adoption."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from app import wiring
from app.mesh import fields_sidecar
from fakes import Step
from test_api_v4 import settle_turn, store
from test_api_v4_mesh import booted_mesh
from test_crexil import command, imported, project
from test_crexil_execution import reviewed

pytestmark = pytest.mark.anyio


async def draft_mesh(client, wid, sources, *, action='mesh', engine='trellis2-4b', count=1, controls=None):
    kind = 'generate' if action == 'mesh' else action
    ops = (await client.get('/api/crexil/operations')).json()
    op = next(o for o in ops if o['id'] == f'mesh:{kind}:{engine}')
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(
        title=op['label'], setup={'action': action, 'prompt': 'Recorded description only',
        'references': [{'node_id': n['id']} for n in sources],
        'operations': [{'operation_id': op['id'], 'revision': op['revision'], 'count': count, 'controls': controls or {}}]}))
    assert response.status_code == 201, response.text
    return response.json()


async def submit(client, direction):
    check = await reviewed(client, direction)
    assert check['ready'], check['errors']
    body = command(revision=check['revision'], review_token=check['token'])
    body['actor'] = {'kind': 'agent', 'name': '3D assistant'}
    url = f"/api/crexil/directions/{direction['id']}/submit"
    a, b = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
    assert a.status_code == b.status_code == 201, (a.text, b.text)
    assert a.json() == b.json()
    await settle_turn(a.json()['turn_id'], until={'landed'})
    turn = await store().get_turn(a.json()['turn_id'])
    nodes = [n for n in await store().list_nodes(direction['workspace_id']) if n['turn_id'] == turn['id']]
    return turn, nodes


async def test_generate_reduce_texture_share_review_freeze_and_exact_lineage(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        fakes['mesh'].default = Step(data=b'glTF-scripted')
        p = await project(client)
        image = (await imported(client, p['id']))['node']
        direction = await draft_mesh(client, p['id'], [image], count=2, controls={'seed': {'mode': 'fixed', 'value': 200}})
        check = await reviewed(client, direction)
        assert check['maximum_usd'] == 0 and not fakes['mesh'].starts
        turn, meshes = await submit(client, direction)
        assert len(meshes) == len(fakes['mesh'].starts) == 2
        assert [s['seed'] for s in turn['slots']] == [200, 201]
        assert all(n['payload'] == '3d' and n['verb'] == 'to_3d' and n['parents'] == [image['id']] for n in meshes)
        assert all(s['frame'] is None and s['committed_usd'] == 0 for s in turn['slots'])
        assert turn['dials']['crexil']['actor']['name'] == '3D assistant'
        job = turn['slots'][0]['submitted_graph']['mesh_job']
        assert job['source_hashes'] == [{'path': image['output_path'], 'sha256': hashlib.sha256(Path(image['output_path']).read_bytes()).hexdigest()}]
        reduced = await draft_mesh(client, p['id'], [meshes[0]], action='decimate', engine='gltfpack', controls={'target_triangles': 1000})
        smaller, results = await submit(client, reduced)
        assert smaller['slots'][0]['seed'] is None
        assert results[0]['verb'] == 'decimate' and results[0]['parents'] == [meshes[0]['id']]
        texture = await draft_mesh(client, p['id'], [results[0], image], action='texture', engine='hunyuan3d-21')
        painted, outputs = await submit(client, texture)
        assert outputs[0]['parents'] == [results[0]['id'], image['id']]
        assert painted['slots'][0]['seed'] is None
        assert 'new UVs' in ' '.join(painted['dials']['crexil']['operations'][0]['limitations'])
        assert fakes['_probe'].max_concurrent == 1
        assert len(fakes['mesh'].starts) == 4


async def test_mesh_review_accumulates_wrong_roles_controls_and_deterministic_limits(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        image = (await imported(client, p['id']))['node']
        wrong = await draft_mesh(client, p['id'], [image], action='decimate', engine='gltfpack', controls={'target_triangles': 1000})
        assert 'shape' in ' '.join((await reviewed(client, wrong))['errors'])
        generated, meshes = await submit(client, await draft_mesh(client, p['id'], [image]))
        for controls, count, expected in [({}, 1, 'required'), ({'target_triangles': 99}, 1, 'between'),
            ({'target_triangles': 1000, 'force': 1}, 1, 'true or false'),
            ({'target_triangles': 1000, 'seed': 4}, 1, 'Unsupported'),
            ({'target_triangles': 1000}, 2, 'at most 1')]:
            d = await draft_mesh(client, p['id'], meshes, action='decimate', engine='gltfpack', controls=controls, count=count)
            review = await reviewed(client, d)
            assert not review['ready'] and expected in ' '.join(review['errors'])
        backwards = await draft_mesh(client, p['id'], [image, meshes[0]], action='texture', engine='hunyuan3d-21')
        assert not (await reviewed(client, backwards))['ready']
        assert len(await store().list_turns(p['id'])) == 1
        assert generated['dials']['crexil']['setup']['prompt'] == 'Recorded description only'


async def test_fields_are_required_and_their_reviewed_bytes_cannot_change(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        image = (await imported(client, p['id']))['node']
        _, meshes = await submit(client, await draft_mesh(client, p['id'], [image]))
        direction = await draft_mesh(client, p['id'], meshes, action='decimate', controls={'target_triangles': 1000})
        assert 'voxel fields' in ' '.join((await reviewed(client, direction))['errors'])
        fields = fields_sidecar(meshes[0]['output_path'])
        fields.write_bytes(b'fields reviewed')
        checked = await reviewed(client, direction)
        assert checked['ready']
        fields.write_bytes(b'fields changed')
        refusal = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(revision=checked['revision'], review_token=checked['token']))
        assert refusal.status_code == 409
        assert len(await store().list_turns(p['id'])) == 1
        turn, _ = await submit(client, direction)
        assert turn['slots'][0]['submitted_graph']['mesh_job']['source_hashes'][-1]['sha256'] == hashlib.sha256(fields.read_bytes()).hexdigest()


async def test_mesh_sequence_runs_through_the_same_freeze_and_budget(tmp_path, monkeypatch):
    from test_crexil_sequences import start, wait_sequence
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        image = (await imported(client, p['id']))['node']
        generate = await draft_mesh(client, p['id'], [image])
        reduce = await draft_mesh(client, p['id'], [], action='decimate', engine='gltfpack', controls={'target_triangles': 1000})
        r = await client.post(f"/api/crexil/projects/{p['id']}/sequences", json=command(title='Image to lighter mesh',
            limits={'attempt_limit': 2, 'spending_limit_usd': 0}, steps=[
                {'key': 'mesh', 'direction_id': generate['id'], 'revision': 0},
                {'key': 'reduce', 'direction_id': reduce['id'], 'revision': 0, 'input_mode': 'each',
                 'dependencies': [{'step_key': 'mesh', 'selection': 'all_successful'}]}]))
        assert r.status_code == 201, r.text
        await start(client, r.json()['id'])
        done = await wait_sequence(client, r.json()['id'], ['complete', 'complete'])
        assert done['accounting']['attempts'] == 2
        assert len(fakes['mesh'].starts) == 2
        child = await store().get_turn(done['steps'][1]['runs'][0]['turn_id'])
        assert child['slots'][0]['dials']['kind'] == 'decimate'
        assert child['dials']['crexil']['sequence']['id'] == done['id']
        await wiring.current.sequences.reconcile()
        assert len(fakes['mesh'].starts) == 2


async def test_glb_import_preserves_embedded_material_and_reported_parentage(tmp_path, monkeypatch):
    import base64
    import json
    import struct

    def glb(document):
        chunk = json.dumps(document).encode()
        chunk += b' ' * (-len(chunk) % 4)
        return struct.pack('<4sIII4s', b'glTF', 2, 20 + len(chunk), len(chunk), b'JSON') + chunk

    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        data = glb({'asset': {'version': '2.0'}, 'scenes': [{}], 'scene': 0})
        body = command(filename='reworked.glb', data_base64=base64.b64encode(data).decode(),
                       parents=[source['id']], tool='Blender', explanation='Rebuilt silhouette')
        path = f"/api/crexil/projects/{p['id']}/material"
        a = await client.post(path, json=body)
        assert a.status_code == 201, a.text
        b = await client.post(path, json=body)
        assert a.json() == b.json()
        node = a.json()['node']
        assert node['payload'] == '3d' and node['parents'] == [source['id']]
        assert Path(node['output_path']).read_bytes() == data
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        material = next(n['material'] for n in snapshot['nodes'] if n['id'] == node['id'])
        assert material['media_type'] == 'model/gltf-binary' and material['width'] is None
        assert material['origin']['kind'] == 'reported_external'
        assert material['origin']['tool'] == 'Blender'
        remote = glb({'asset': {'version': '2.0'}, 'buffers': [{'uri': 'https://example.invalid/private.bin', 'byteLength': 5}]})
        duplicate = bytearray(data + remote[12:])
        struct.pack_into('<I', duplicate, 8, len(duplicate))
        for invalid in [data[:-1], remote, b'glTF-broken', bytes(duplicate)]:
            response = await client.post(path, json=command(filename='broken.glb', data_base64=base64.b64encode(invalid).decode()))
            assert response.status_code == 422
        assert len(await store().list_nodes(p['id'])) == 2
