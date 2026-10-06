"""Project restore owns its bytes while old execution stays historical."""

import base64
import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import api_v4, config, crexil_delivery, mesh, wiring
from app.crexil_store import CreativeStore
from app.db import Database
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_delivery import prepared

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline_catalogue(monkeypatch):
    from app.providers import ProviderCatalog
    async def disabled(_self):
        return None
    monkeypatch.setattr(ProviderCatalog, '_refresh', disabled)


async def backup_upload(client, wid):
    body = command()
    route = f'/api/crexil/projects/{wid}/backups'
    backup = await client.post(route, json=body)
    assert backup.status_code == 201, backup.text
    assert (await client.post(route, json=body)).json() == backup.json()
    file = await client.get(f"/api/crexil/backups/{backup.json()['id']}/file")
    assert hashlib.sha256(file.content).hexdigest() == backup.json()['sha256']
    reservation = command(filename='project.crexil.zip', size_bytes=len(file.content))
    upload = await client.post('/api/crexil/archive-uploads', json=reservation)
    assert upload.status_code == 201, upload.text
    uid = upload.json()['id']
    uploaded = await client.put(f'/api/crexil/archive-uploads/{uid}/file', content=file.content)
    assert uploaded.status_code == 200, uploaded.text
    assert (await client.get(f'/api/crexil/archive-uploads/{uid}')).json() == uploaded.json()
    return uploaded.json(), file.content


async def restored(client, upload, **fields):
    body = command(sha256=upload['sha256'], **fields)
    path = f"/api/crexil/archive-uploads/{upload['id']}/restore"
    result = await client.post(path, json=body)
    assert result.status_code == 201, result.text
    assert (await client.post(path, json=body)).json() == result.json()
    return (await client.get(f"/api/crexil/projects/{result.json()['project_id']}")).json()


async def test_restore_preserves_pixels_order_text_masks_and_independent_purge(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        second = (await imported(client, p['id']))['node']
        child = (await imported(client, p['id'], parents=[second['id'], source['id']],
                                tool='Krita', explanation='Literal ' + source['id']))['node']
        info = (await client.get(f"/api/crexil/projects/{p['id']}/mask-source/{source['id']}")).json()
        mask = await client.post(f"/api/crexil/projects/{p['id']}/masks", json=command(
            source_node_id=source['id'], source_sha256=info['sha256'], name='Saved area',
            data_base64=base64.b64encode(image_bytes()).decode()))
        assert mask.status_code == 201, mask.text
        setup = {'action': 'region', 'prompt': source['id'], 'references': [{'node_id': source['id']}],
                 'regions': [{'key': 'area', 'name': 'Area', 'action': 'modify', 'mask_id': mask.json()['id']}]}
        draft = (await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(title='Edit', setup=setup))).json()
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload, name='Independent copy')
        mapping = copy['restoration']['identities']
        assert copy['project']['name'] == 'Independent copy'
        assert len(await store().list_workspaces()) == 2
        kid = await store().get_node(mapping['nodes'][child['id']])
        assert kid['parents'] == [mapping['nodes'][second['id']], mapping['nodes'][source['id']]]
        assert kid['prompt'] == 'Literal ' + source['id']
        assert Path(kid['output_path']).read_bytes() == image_bytes()
        preparation = await CreativeStore(store()).direction(mapping['directions'][draft['id']])
        assert preparation['setup']['prompt'] == source['id']
        assert preparation['setup']['references'][0]['node_id'] == mapping['nodes'][source['id']]
        saved_mask = (await client.get(f"/api/crexil/projects/{copy['project']['id']}/masks")).json()[0]
        assert saved_mask['source_node_id'] == mapping['nodes'][source['id']]
        assert preparation['setup']['regions'][0]['mask_id'] == saved_mask['id']
        assert saved_mask['available']
        await store().purge_node(kid['id'])
        assert Path(child['output_path']).read_bytes() == image_bytes()
        second_upload, _ = await backup_upload(client, copy['project']['id'])
        twice = await restored(client, second_upload)
        aliases = twice['restoration']['identities']['nodes']
        assert aliases[source['id']] == aliases[mapping['nodes'][source['id']]]


async def test_restore_never_dispatches_old_work_or_transfers_old_deliveries(tmp_path, monkeypatch):
    target = tmp_path / 'delivery'
    monkeypatch.setattr(config, 'PROMOTE_DIR', target)
    monkeypatch.setattr(config, 'PROMOTE_ROOTS', [target])
    target.mkdir()
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        delivery, _ = await prepared(client, p['id'], [node['id']], target)
        turn = await store().create_turn_with_slots(p['id'], 'Unfinished', {}, [{'engine': 'local', 'committed_usd': .3}])
        sid = turn['slots'][0]['id']
        await store().dispatch_slot(sid, job_ref='old-runtime')
        await store().append_event('slot.charged', workspace_id=p['id'], turn_id=turn['id'], slot_id=sid,
                                   charged_usd=.2, payload={'attempt': 1})
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload)
        mapped = copy['restoration']['identities']['slots'][sid]
        slot = await store().get_slot(mapped)
        assert slot['status'] == 'skipped' and slot['job_ref'] == 'old-runtime'
        assert slot['dials']['crexil_restore']['status'] == 'running'
        for action in (lambda: store().dispatch_slot(mapped), lambda: store().reroll_slot(mapped, 1, dials={}),
                       lambda: wiring.current.scheduler.collect_existing(mapped, 1)):
            with pytest.raises(ValueError, match='restored execution history'):
                await action()
        assert copy['charged_usd'] == .2
        assert await store().charged_between('2000', '9999') == .2
        imported_delivery = (await crexil_delivery.inventory(store(), copy['project']['id']))[0]
        assert imported_delivery['id'] != delivery['id']
        response = await client.post(f"/api/crexil/deliveries/{imported_delivery['id']}/transfer", json=command())
        assert response.status_code == 422
        assert not list(target.iterdir())
        assert all(s['id'] != mapped for s in await store().pending_slots())
        assert all(not executor.starts for executor in fakes.values())
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        assert all(s['id'] != mapped for s in await db.pending_slots())
        assert (await db.get_slot(mapped))['status'] == 'skipped'
        assert await db.charged_between('2000', '9999') == .2
    finally:
        await db.close()


async def test_upload_identity_missing_material_and_rejected_archive_are_explicit(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        Path(node['output_path']).unlink()
        upload, data = await backup_upload(client, p['id'])
        assert len(upload['review']['unavailable_files']) == 1
        route = f"/api/crexil/archive-uploads/{upload['id']}/file"
        assert (await client.put(route, content=data)).json() == upload
        assert (await client.put(route, content=b'x' * len(data))).status_code == 422
        copy = await restored(client, upload)
        assert not copy['nodes'][0]['available']
        assert copy['nodes'][0]['purged_at'] is None
        bad = (await client.post('/api/crexil/archive-uploads', json=command(filename='bad.zip', size_bytes=5))).json()
        rejected = await client.put(f"/api/crexil/archive-uploads/{bad['id']}/file", content=b'wrong')
        assert rejected.status_code == 422, rejected.text
        assert (await client.get(f"/api/crexil/archive-uploads/{bad['id']}")).json()['state'] == 'waiting'
        assert len(await store().list_workspaces()) == 2


async def test_restored_mesh_reads_only_retained_cache_and_never_old_runner(tmp_path, monkeypatch):
    path = tmp_path / 'mesh.glb'
    node = {'payload': '3d', 'output_path': str(path), 'dials': {'crexil_restore': {'original_id': 'old'}},
            'submitted_graph': {'mesh_job': {'runner_job_id': 'original-job', 'turntable': 'orbit.webp'}}}
    async def forbidden(*args):
        raise AssertionError('Restored material contacted the old runner')
    monkeypatch.setattr(api_v4, '_fetch_turntable', forbidden)
    assert mesh.parent_runner_job(node) is None
    with pytest.raises(HTTPException):
        await api_v4._turntable_file(node)
    cached = path.with_suffix('.turntable.webp')
    cached.write_bytes(b'retained orbit')
    assert await api_v4._turntable_file(node) == cached


async def test_restored_sequence_retains_authorization_and_reuses_fresh_preparations(tmp_path, monkeypatch):
    from app.manifests import _shared_request
    from fakes import Step
    from test_crexil_execution import attest
    from test_crexil_sequences import sequence, start, wait_sequence
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        planned, _, _ = await sequence(client, p['id'])
        await start(client, planned['id'])
        done = await wait_sequence(client, planned['id'], ['complete', 'complete'])
        nid = (await store().list_nodes(p['id']))[-1]['id']
        node = await store().get_node(nid)
        original = await _shared_request(store(), node, await store().get_turn(node['turn_id']))
        count = len(fakes['comfy'].starts)
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload)
        mapped = copy['restoration']['identities']
        sid = mapped['sequences'][planned['id']]
        restored_sequence = next(s for s in copy['sequences'] if s['id'] == sid)
        assert restored_sequence['state'] == 'cancelled'
        assert (await store()._one('SELECT authorization_json FROM sequences WHERE id=?', (sid,)))['authorization_json'] is None
        restored_node = await store().get_node(mapped['nodes'][nid])
        retained = await _shared_request(store(), restored_node, await store().get_turn(restored_node['turn_id']))
        assert retained['setup'] == original['setup']
        assert retained['sources'] == original['sources']
        assert retained['sequence_authorization']['actor'] == original['sequence_authorization']['actor']
        assert retained['sequence_authorization']['accepted_at'] == original['sequence_authorization']['accepted_at']
        assert retained['sequence_authorization']['archive_origin']['event_seq'] == original['sequence_authorization']['event_seq']
        await wiring.current.sequences.reconcile()
        assert len(fakes['comfy'].starts) == count
        reused = await client.post(f'/api/crexil/sequences/{sid}/reuse', json=command())
        assert reused.status_code == 201, reused.text
        assert reused.json()['state'] == 'draft'
        assert len(reused.json()['steps']) == len(done['steps'])
        assert len(fakes['comfy'].starts) == count


async def test_restored_custom_operation_requires_new_inspection(tmp_path, monkeypatch):
    from test_operation_registry import direction, prepared as registered_operation
    async with booted(tmp_path, monkeypatch) as (client, _):
        operation = await registered_operation(client, monkeypatch)
        p, source, draft = await direction(client, operation)
        stale_setup = {**draft['setup'], 'operations': [{**draft['setup']['operations'][0], 'revision': 'earlier-recorded-revision'}]}
        stale = (await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(title='Pinned older revision', setup=stale_setup))).json()
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload)
        mapped = copy['restoration']['identities']
        rid = mapped['operation_registrations'][operation['id']]
        cloned = (await client.get(f'/api/crexil/registrations/{rid}')).json()
        assert cloned['source_text'] == operation['source_text']
        assert cloned['operation_revision'] != operation['operation_revision']
        assert cloned['inspection'] is None
        restored_draft = await CreativeStore(store()).direction(mapped['directions'][draft['id']])
        assert restored_draft['setup']['operations'][0]['operation_id'] == 'custom-' + rid
        assert restored_draft['setup']['operations'][0]['revision'] == cloned['operation_revision']
        assert restored_draft['setup']['references'][0]['node_id'] == mapped['nodes'][source['id']]
        stale_copy = await CreativeStore(store()).direction(mapped['directions'][stale['id']])
        assert stale_copy['setup']['operations'][0]['revision'] == 'earlier-recorded-revision'
        assert stale_copy['setup']['operations'][0]['operation_id'] == 'custom-' + rid


async def test_restore_concurrent_delivery_replays_once_and_rolls_back_failed_receipt(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        await imported(client, p['id'])
        upload, _ = await backup_upload(client, p['id'])
        body = command(sha256=upload['sha256'])
        path = f"/api/crexil/archive-uploads/{upload['id']}/restore"
        first, second = await asyncio.gather(client.post(path, json=body), client.post(path, json=body))
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        copies = set((config.STAGING_DIR / 'restored').iterdir())
        original = store().conn.execute
        async def fail_receipt(sql, *args, **kwargs):
            if sql.startswith('INSERT INTO command_receipts'):
                raise OSError('Receipt storage unavailable')
            return await original(sql, *args, **kwargs)
        monkeypatch.setattr(store().conn, 'execute', fail_receipt)
        with pytest.raises(OSError, match='Receipt storage'):
            await client.post(path, json=command(sha256=upload['sha256']))
        assert len(await store().list_workspaces()) == 2
        assert set((config.STAGING_DIR / 'restored').iterdir()) == copies


async def test_restore_refuses_uploaded_bytes_changed_after_review(tmp_path, monkeypatch):
    from app.crexil_restore import upload_path
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        await imported(client, p['id'])
        upload, _ = await backup_upload(client, p['id'])
        upload_path(upload['id']).write_bytes(b'changed')
        result = await client.post(f"/api/crexil/archive-uploads/{upload['id']}/restore", json=command(sha256=upload['sha256']))
        assert result.status_code == 422 and 'changed after review' in result.text
        assert len(await store().list_workspaces()) == 1
