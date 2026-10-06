"""Asset exports retain accepted intent without borrowing current project state."""

import base64
import copy
import json
import struct
from pathlib import Path

import pytest

from app import manifests
from fakes import Step
from test_api_v4 import booted, store
from test_api_v4_mesh import booted_mesh
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import attest, reviewed, settled
from test_crexil_mesh import draft_mesh, submit
from test_crexil_sequences import sequence, start, wait_sequence
from test_db import opened
from test_operation_registry import direction, prepared

pytestmark = pytest.mark.anyio


async def export(db, node, *, turn=None, slot=None):
    return await manifests.build_v4_manifest(db, asset_id='delivery',
        workspace=await db.get_workspace(node['workspace_id']), node=node, turn=turn, slot=slot,
        parents=[await db.get_node(nid) for nid in node['parents']])


async def test_shared_export_uses_accepted_custom_operation_and_sources_after_edits(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        p, source, draft = await direction(client, item, grain=5, enabled=False)
        review = await reviewed(client, draft)
        fakes['comfy'].default = Step(data=image_bytes())
        body = command(revision=draft['revision'], review_token=review['token'],
                       actor={'kind': 'agent', 'name': 'Accepted assistant'})
        accepted = await client.post(f"/api/crexil/directions/{draft['id']}/submit", json=body)
        assert accepted.status_code == 201, accepted.text
        turn = (await settled(p['id']))[0]
        node = next(n for n in await store().list_nodes(p['id']) if n['turn_id'])
        expected = copy.deepcopy(turn['dials']['crexil'])

        changed = copy.deepcopy(item['definition'])
        changed['graph']['b']['inputs']['grain'] = 4
        newer = await client.patch(f"/api/crexil/registrations/{item['id']}", json=command(
            revision=item['revision'], name=item['name'], definition=changed, binding=item['binding'],
            adaptation_note='Change the stored default after accepting the earlier request.'))
        assert newer.status_code == 200, newer.text
        changed_project = await client.patch(f"/api/crexil/projects/{p['id']}", json=command(
            revision=p['revision'], name=p['name'], brief='Unrelated newer project intent',
            instructions='These instructions did not make the exported take.'))
        assert changed_project.status_code == 200, changed_project.text
        latest = await store().create_turn_with_slots(p['id'], 'Unrelated later request',
            {'crexil': {'request_id': 'do-not-borrow', 'setup': {'prompt': 'later'}}}, [{'engine': 'other'}])

        manifest, files = await export(store(), node, turn=latest, slot=latest['slots'][0])
        shared = manifest['shared_request']
        assert manifest['manifest_version'] == 4 and shared['version'] == 1
        assert manifest['turn']['id'] == turn['id']
        assert manifest['slot']['id'] == node['slot_id']
        assert shared['request_id'] == body['request_id'] and shared['actor'] == body['actor']
        assert shared['review_token'] == review['token']
        assert shared['setup'] == expected['setup']
        assert shared['sources'] == expected['sources']
        assert shared['setup']['references'] == [
            {'node_id': source['id'], 'role': 'structure', 'contribution': 'Keep the outline', 'strength': None}]
        assert shared['brief'] == expected['brief'] == 'A worn handle'
        assert shared['project_instructions'] == expected['project_instructions']
        assert shared['operation'] == expected['operations'][0]
        assert shared['operation']['registration_revision'] == item['revision']
        assert shared['operation']['definition'] == item['definition']
        assert shared['attempt']['dials'] == node['dials']
        assert shared['attempt']['seed'] == manifest['seed'] == 719
        assert 'Unrelated' not in json.dumps(shared)
        assert 'accounting' not in shared
        assert Path(source['output_path']) in [path for path, _ in files]
        assert manifest['submitted_graph']['b']['inputs']['grain'] == 5


async def test_old_take_keeps_original_controls_and_cannot_borrow_reroll_revision(tmp_path):
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Historical take')
        frozen = {'direction_id': 'original', 'revision': 0, 'request_id': 'accepted',
                  'setup': {'prompt': 'first'}, 'operations': [
                      {'id': 'local', 'revision': 'old', 'definition': {'saved': True}},
                      {'id': 'different', 'revision': 'old', 'definition': {'unrelated': True}}]}
        original_dials = {'prompt': 'first', 'crexil': {'operation_revision': 'old'}}
        turn = await db.create_turn_with_slots(workspace['id'], 'first', {'crexil': frozen},
            [{'engine': 'local', 'seed': 7, 'dials': original_dials}])
        sid = turn['slots'][0]['id']
        await db.dispatch_slot(sid, job_ref='first-job', submitted_graph={})
        path = tmp_path / 'first.png'
        path.write_bytes(image_bytes())
        node = await db.land_node(sid, payload='image', verb='run', output_path=str(path))
        await db.reroll_slot(sid, 9, dials={'prompt': 'later', 'crexil': {'operation_revision': 'new'}})
        current = await db.dispatch_slot(sid, job_ref='second-job', submitted_graph={})
        manifest, _ = await export(db, node, turn=turn, slot=current)
        shared = manifest['shared_request']
        assert shared['operation'] == frozen['operations'][0]
        assert shared['attempt'] == {'slot_id': sid, 'take': 1, 'operation_id': 'local',
                                     'seed': 7, 'dials': original_dials}
        assert manifest['comfyui']['job_ref'] is None  # no first-attempt job receipt exists
        later_path = tmp_path / 'later.png'
        later_path.write_bytes(image_bytes())
        later = await db.land_node(sid, payload='image', verb='run', output_path=str(later_path))
        later_manifest, _ = await export(db, later, turn=turn, slot=current)
        assert later_manifest['shared_request']['operation'] is None
        assert later_manifest['shared_request']['attempt']['dials']['crexil']['operation_revision'] == 'new'


async def test_sequence_export_pins_original_authorization_binding_and_decision(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, second = await sequence(client, p['id'], selection='chosen', input_mode='together')
        initial, _ = await start(client, planned['id'])
        waiting = await wait_sequence(client, planned['id'], ['complete', 'decision'])
        step = waiting['steps'][1]
        selected = step['candidates']['explore'][1]['node_id']
        choice = command(revision=waiting['revision'], direction_revision=second['revision'],
            candidate_token=step['candidate_token'], selections={'explore': [selected]},
            reason='Retain the narrow silhouette.', actor={'kind': 'agent', 'name': 'Sequence guide'})
        result = await client.post(f"/api/crexil/sequences/{planned['id']}/steps/refine/decision", json=choice)
        assert result.status_code == 200, result.text
        done = await wait_sequence(client, planned['id'], ['complete', 'complete'])
        tid = done['steps'][1]['runs'][0]['turn_id']
        turn = await store().get_turn(tid)
        node = next(n for n in await store().list_nodes(p['id']) if n['turn_id'] == tid)
        original_context = copy.deepcopy(turn['dials']['crexil']['sequence'])
        revised = await client.patch(f"/api/crexil/sequences/{planned['id']}", json=command(
            revision=done['revision'], title=done['title'], instructions='New intent after this take.',
            limits=done['limits']))
        assert revised.status_code == 200, revised.text
        newer, _ = await start(client, planned['id'])
        assert newer['review_token'] != initial['review_token']

        manifest, _ = await export(store(), node, turn=turn, slot=turn['slots'][0])
        shared = manifest['shared_request']
        assert shared['sequence'] == original_context
        assert shared['sequence']['decision']['reason'] == choice['reason']
        assert shared['sequence']['decision']['actor'] == choice['actor']
        assert shared['sequence']['binding']['references'][0]['node_id'] == selected
        authorization = shared['sequence_authorization']
        assert authorization['review']['token'] == initial['review_token']
        assert authorization['actor'] == initial['actor']
        assert authorization['review']['snapshot']['instructions'] == planned['instructions']
        assert 'New intent' not in json.dumps(shared)


@pytest.mark.parametrize('payload', ['image', '3d'])
async def test_imports_retain_reported_origin_without_creation_claims(tmp_path, monkeypatch, payload):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        if payload == '3d':
            chunk = json.dumps({'asset': {'version': '2.0'}, 'scenes': [{}], 'scene': 0}).encode()
            chunk += b' ' * (-len(chunk) % 4)
            data = struct.pack('<4sIII4s', b'glTF', 2, 20 + len(chunk), len(chunk), b'JSON') + chunk
            filename = 'returned.glb'
        else:
            data, filename = image_bytes(), 'returned.png'
        response = await client.post(f"/api/crexil/projects/{p['id']}/material", json=command(
            filename=filename, data_base64=base64.b64encode(data).decode(), parents=[source['id']],
            tool='External editor', explanation='Reported edits, not an observed generation prompt.'))
        assert response.status_code == 201, response.text
        node = response.json()['node']
        manifest, files = await export(store(), node)
        assert manifest['replayable'] == manifests.REPLAY_IMPORTED
        assert all(manifest[key] is None for key in
                   ('transport', 'provider', 'local_only', 'run_local_only', 'capability', 'comfyui', 'shared_request'))
        assert manifest['prompts']['positive'] is None
        assert manifest['model']['family'] is None and manifest['model']['files'] == []
        assert manifest['material']['origin']['tool'] == 'External editor'
        assert manifest['material']['origin']['kind'] == 'reported_external'
        assert manifest['parent_node_ids'] == [source['id']]
        assert Path(node['output_path']).read_bytes() == data
        assert len(files) == 1 and files[0][0] == Path(source['output_path'])
        assert not any(fake.starts for fake in fakes.values())


async def test_generated_mesh_matches_shared_operation_without_comfyui_claim(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, fakes):
        fakes['mesh'].default = Step(data=b'glTF-scripted')
        p = await project(client)
        image = (await imported(client, p['id']))['node']
        turn, nodes = await submit(client, await draft_mesh(client, p['id'], [image]))
        manifest, _ = await export(store(), nodes[0], turn=turn, slot=turn['slots'][0])
        assert manifest['replayable'] == manifests.REPLAY_MESH
        assert manifest['provider'] is None and manifest['comfyui'] is None
        assert manifest['transport'] == 'local' and manifest['local_only'] is True
        assert manifest['shared_request']['operation'] == turn['dials']['crexil']['operations'][0]
        assert manifest['shared_request']['attempt']['operation_id'] == 'mesh:generate:trellis2-4b'
