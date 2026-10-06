"""Reuse keeps independent bytes and historical origin without replaying work."""

import asyncio
import hashlib
import json
import threading
from pathlib import Path

import pytest

from app import config, crexil_reuse
from app.crexil_store import CreativeStore
from app.db import Database
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project

pytestmark = pytest.mark.anyio


async def reviewed(client, target, source, node):
    selection = {'source_project_id': source, 'node_id': node}
    response = await client.post(f'/api/crexil/projects/{target}/reuse/review', json=selection)
    assert response.status_code == 200, response.text
    return response.json(), command(**selection, review_token=response.json()['token'], explanation='A useful starting point')


async def purge(client, node):
    await client.post(f"/api/nodes/{node['id']}/set-aside")
    response = await client.post(f"/api/workspaces/{node['workspace_id']}/purge", json={'ids': [node['id']]})
    assert response.status_code == 200, response.text


async def test_reuse_preserves_source_record_and_independent_copy_after_restart(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        source, target = await project(client), await project(client)
        parent = (await imported(client, source['id']))['node']
        node = (await imported(client, source['id'], parents=[parent['id']], tool='Krita', explanation='Refined the silhouette'))['node']
        before = (await client.get(f"/api/crexil/projects/{source['id']}")).json()
        events = await store().node_events(node['id'])
        review, body = await reviewed(client, target['id'], source['id'], node['id'])
        assert review['ready'] and len(review['files']) == 2
        body['actor'] = {'kind': 'agent', 'name': 'Material assistant'}
        path = f"/api/crexil/projects/{target['id']}/reuse"
        first, second = await asyncio.gather(*[client.post(path, json=body) for _ in range(2)])
        assert first.status_code == second.status_code == 201 and first.json() == second.json()
        copied = first.json()['node']
        after = (await client.get(f"/api/crexil/projects/{source['id']}")).json()
        # The shared global event cursor advances, but the source project does not.
        assert {k: v for k, v in before.items() if k != 'event_seq'} == {k: v for k, v in after.items() if k != 'event_seq'}
        assert await store().node_events(node['id']) == events
        assert copied['parents'] == [] and copied['turn_id'] is None and copied['slot_id'] is None
        assert copied['engine'] is None and copied['charged_usd'] is None
        snapshot = (await client.get(f"/api/crexil/projects/{target['id']}")).json()
        origin = snapshot['nodes'][0]['material']['origin']
        assert origin['kind'] == 'project_reuse' and origin['actor'] == body['actor']
        assert origin['source']['project_id'] == source['id'] and origin['source']['node_id'] == node['id']
        frozen = origin['source_record']['manifest']
        assert frozen['parent_node_ids'] == [parent['id']]
        assert frozen['material']['origin']['tool'] == 'Krita'
        assert snapshot['turns'] == snapshot['execution_groups'] == []
        assert Path(copied['output_path']).read_bytes() == image_bytes()
        assert copied['output_path'] != node['output_path']
        assert len(snapshot['nodes'][0]['retained_files']) == 1
        retained = snapshot['nodes'][0]['retained_files'][0]
        assert (await client.get(f"/api/crexil/material/{copied['id']}/files/{retained['id']}")).content == image_bytes()
        await purge(client, node)
        await purge(client, parent)
        assert not Path(node['output_path']).exists()
        assert Path(copied['output_path']).read_bytes() == image_bytes()
        assert (await client.post(path, json=body)).json() == first.json()
        assert (await client.post(path, json={**body, 'explanation': 'Changed command'})).status_code == 409
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        snapshot = await CreativeStore(db).snapshot(target['id'])
        assert len(snapshot['nodes']) == 1 and snapshot['nodes'][0]['available']
        assert snapshot['nodes'][0]['material']['origin']['source_record']['manifest'] == frozen
        assert snapshot['nodes'][0]['retained_files'][0]['available']
    finally:
        await db.close()


@pytest.mark.parametrize('change', ['bytes', 'purge', 'project_name'])
async def test_reuse_refuses_changed_review_without_creating_material(tmp_path, monkeypatch, change):
    async with booted(tmp_path, monkeypatch) as (client, _):
        source, target = await project(client), await project(client)
        node = (await imported(client, source['id']))['node']
        _, body = await reviewed(client, target['id'], source['id'], node['id'])
        if change == 'bytes':
            Path(node['output_path']).write_bytes(b'Changed externally')
        elif change == 'purge':
            await purge(client, node)
        else:
            await store().conn.execute("UPDATE workspaces SET name='Renamed source' WHERE id=?", (source['id'],))
        response = await client.post(f"/api/crexil/projects/{target['id']}/reuse", json=body)
        assert response.status_code == 409, response.text
        assert not await store().list_nodes(target['id'])
        assert not await store().list_turns(target['id'])


async def test_reuse_required_files_and_missing_receipts_survive_repeated_reuse(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        source, target, third = await project(client), await project(client), await project(client)
        node = (await imported(client, source['id']))['node']
        original = Path(node['output_path'])
        companion = original.parent / 'related' / 'source-data.bin'
        companion.parent.mkdir()
        companion.write_bytes(b'Original companion')
        origin = json.loads((await store()._one('SELECT origin_json FROM material_info WHERE node_id=?', (node['id'],)))['origin_json'])
        origin['retained_files'] = [
            {'id': 'fields', 'path': str(companion), 'filename': 'Useful data.bin', 'role': 'companion', 'required': True,
             'media_type': 'application/x-voxel-fields', 'sha256': hashlib.sha256(companion.read_bytes()).hexdigest(),
             'size_bytes': companion.stat().st_size, 'error': None},
            {'id': 'preview', 'path': None, 'filename': 'Uncollected.webp', 'role': 'preview', 'required': False,
             'media_type': 'image/webp', 'sha256': None, 'size_bytes': None, 'error': 'The source runtime did not retain this preview.'}]
        # An optional alias appears first and shares bytes with a required file.
        origin['retained_files'].insert(0, {**origin['retained_files'][0], 'id': 'optional-alias',
            'filename': 'Preview data.bin', 'role': 'preview', 'required': False})
        await store().conn.execute('UPDATE material_info SET origin_json=? WHERE node_id=?', (json.dumps(origin), node['id']))
        previous, previous_project = node, source
        for destination in (target, third):
            review, body = await reviewed(client, destination['id'], previous_project['id'], previous['id'])
            assert review['ready'] and len(review['missing']) == 1
            response = await client.post(f"/api/crexil/projects/{destination['id']}/reuse", json=body)
            assert response.status_code == 201, response.text
            previous, previous_project = response.json()['node'], destination
            snap = (await client.get(f"/api/crexil/projects/{destination['id']}")).json()
            receipts = snap['nodes'][0]['retained_files']
            live = next(file for file in receipts if file['required'])
            alias = next(file for file in receipts if file['available'] and not file['required'])
            missing = next(file for file in receipts if not file['available'])
            assert live['required'] and live['filename'] == 'Useful data.bin'
            assert live['media_type'] == 'application/x-voxel-fields'
            assert alias['role'] == 'preview' and alias['filename'] == 'Preview data.bin'
            assert missing['error'] == origin['retained_files'][-1]['error']
            owned = json.loads((await store()._one('SELECT origin_json FROM material_info WHERE node_id=?', (previous['id'],)))['origin_json'])
            paths = [file['path'] for file in owned['retained_files'] if file['path']]
            assert len(paths) == 2 and len(set(paths)) == 1
            assert (await client.get(f"/api/crexil/material/{previous['id']}/files/{live['id']}")).content == b'Original companion'
        await purge(client, previous)
        assert companion.read_bytes() == b'Original companion'
        companion.write_bytes(b'Corrupt companion')
        refused, _ = await reviewed(client, target['id'], source['id'], node['id'])
        assert not refused['ready'] and 'Required related material' in str(refused['errors'])
        assert (await client.post(f"/api/nodes/{node['id']}/promote")).status_code == 409


async def test_reuse_wrong_project_and_self_import_are_refused(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        source, target = await project(client), await project(client)
        node = (await imported(client, source['id']))['node']
        path = f"/api/crexil/projects/{source['id']}/reuse/review"
        for sid in (source['id'], target['id']):
            response = await client.post(path, json={'source_project_id': sid, 'node_id': node['id']})
            assert response.status_code == 422


@pytest.mark.parametrize('failure', ['receipt', 'copy', 'cancellation'])
async def test_failed_reuse_cleans_only_its_owned_copy(tmp_path, monkeypatch, failure):
    async with booted(tmp_path, monkeypatch) as (client, _):
        source, target = await project(client), await project(client)
        node = (await imported(client, source['id']))['node']
        _, body = await reviewed(client, target['id'], source['id'], node['id'])
        directories = set((config.STAGING_DIR / 'imports').iterdir())
        if failure == 'receipt':
            original = store().conn.execute
            async def fail_receipt(sql, *args, **kwargs):
                if sql.startswith('INSERT INTO command_receipts'):
                    raise OSError('Receipt unavailable')
                return await original(sql, *args, **kwargs)
            monkeypatch.setattr(store().conn, 'execute', fail_receipt)
        elif failure == 'copy':
            def fail_copy(*args):
                raise OSError('Disk disconnected')
            monkeypatch.setattr(crexil_reuse, '_copy', fail_copy)
        else:
            started, release = threading.Event(), threading.Event()
            original_copy = crexil_reuse._copy
            def slow_copy(*args):
                started.set()
                assert release.wait(5)
                return original_copy(*args)
            monkeypatch.setattr(crexil_reuse, '_copy', slow_copy)
        request = asyncio.create_task(client.post(f"/api/crexil/projects/{target['id']}/reuse", json=body))
        if failure == 'cancellation':
            assert await asyncio.to_thread(started.wait, 5)
            request.cancel()
            await asyncio.sleep(0.01)
            assert not request.done()
            release.set()
        with pytest.raises(asyncio.CancelledError if failure == 'cancellation' else OSError):
            await request
        assert set((config.STAGING_DIR / 'imports').iterdir()) == directories
        assert Path(node['output_path']).read_bytes() == image_bytes()
        assert not await store().list_nodes(target['id'])
        assert not await store()._one('SELECT 1 FROM command_receipts WHERE request_id=?', (body['request_id'],))
