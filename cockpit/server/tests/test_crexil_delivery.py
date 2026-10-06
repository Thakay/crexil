"""Delivery identity survives lost responses, partial copies and server restarts."""

import asyncio
import csv
import io
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from app import config, crexil_delivery, execution_outputs
from app.db import Database
from app.executors import CollectedOutput, DispatchRequest
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_delivery_target(tmp_path, monkeypatch):
    target = tmp_path / 'promote'
    monkeypatch.setattr(config, 'PROMOTE_DIR', target)
    monkeypatch.setattr(config, 'PROMOTE_ROOTS', [target])


async def prepared(client, wid, ids, destination):
    selection = {'node_ids': ids, 'destination': str(destination)}
    review = await client.post(f'/api/crexil/projects/{wid}/deliveries/review', json=selection)
    assert review.status_code == 200, review.text
    assert review.json()['ready'], review.text
    body = command(**selection, review_token=review.json()['token'])
    path = f'/api/crexil/projects/{wid}/deliveries'
    result = await client.post(path, json=body)
    assert result.status_code == 201, result.text
    return result.json(), body


async def test_delivery_replay_bytes_attribution_and_csv(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = await imported(client, p['id'])
        returned = await imported(client, p['id'], parents=[source['node']['id']], tool='Krita', explanation='=reported edit')
        target = config.PROMOTE_DIR
        target.mkdir(parents=True, exist_ok=True)
        delivery, body = await prepared(client, p['id'], [returned['node']['id']], target)
        assert not list(target.rglob('*.png'))
        replay = await client.post(f"/api/crexil/projects/{p['id']}/deliveries", json=body)
        assert replay.json() == delivery
        transfer = command()
        first, second = await asyncio.gather(*[client.post(f"/api/crexil/deliveries/{delivery['id']}/transfer", json=transfer) for _ in range(2)])
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        item = first.json()['items'][0]
        assert item['state'] == 'complete'
        assert len(item['files']) == 3
        manifest = json.loads(next(Path(f['path']) for f in item['files'] if f['path'].endswith('.json')).read_text())
        assert manifest['node_id'] == returned['node']['id']
        assert manifest['material']['origin']['tool'] == 'Krita'
        assert manifest['parent_node_ids'] == [source['node']['id']]
        assert [f['sha256'] for f in item['files'][:2]] == [manifest['material']['sha256'], manifest['input_images'][0]['sha256']]
        events = await store().node_events(returned['node']['id'], 'node.promoted')
        assert len(events) == 1 and events[0]['payload']['actor'] == body['actor']
        rows = list(csv.DictReader(io.StringIO((await client.get(f"/api/crexil/projects/{p['id']}/deliveries.csv")).text)))
        assert rows[0]['prompt'] == "'=reported edit"
        assert rows[0]['asset_id'] == item['asset_id']
        assert rows[0]['delivery_id'] == delivery['id']


async def test_delivery_independent_failure_and_resume_same_paths(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        nodes = [(await imported(client, p['id']))['node'] for _ in range(2)]
        target = config.PROMOTE_DIR
        target.mkdir(parents=True, exist_ok=True)
        delivery, _ = await prepared(client, p['id'], [n['id'] for n in nodes], target)
        original = crexil_delivery._chunks
        def interrupted(source):
            if str(source) == nodes[0]['output_path']:
                raise OSError('Drive disconnected')
            return original(source)
        monkeypatch.setattr(crexil_delivery, '_chunks', interrupted)
        path = f"/api/crexil/deliveries/{delivery['id']}/transfer"
        result = (await client.post(path, json=command())).json()
        assert [i['state'] for i in result['items']] == ['incomplete', 'complete']
        assert 'Drive disconnected' in result['items'][0]['error']
        # The failed item leaves none of its files behind; the healthy one is whole.
        assert not any(Path(f['path']).exists() for f in result['items'][0]['files'])
        assert all(Path(f['path']).is_file() for f in result['items'][1]['files'])
        monkeypatch.setattr(crexil_delivery, '_chunks', original)
        resumed = (await client.post(path, json=command())).json()
        assert all(i['state'] == 'complete' for i in resumed['items'])
        assert [i['files'] for i in resumed['items']] == [i['files'] for i in delivery['items']]
        assert len(await store().node_events(nodes[1]['id'], 'node.promoted')) == 1


async def test_delivery_crash_after_copy_reconciles_without_recopied_files(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        config.PROMOTE_DIR.mkdir(parents=True, exist_ok=True)
        delivery, _ = await prepared(client, p['id'], [node['id']], config.PROMOTE_DIR)
        intent = await crexil_delivery._intent(store(), delivery['id'])
        await asyncio.to_thread(crexil_delivery._transfer, intent['items'][0], intent['destination'], allow_copy=True)
        before = {f['path']: Path(f['path']).stat().st_mtime_ns for f in delivery['items'][0]['files']}
        assert not await store().node_events(node['id'], 'node.promoted')
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        def never_copy(*args, **kwargs):
            raise AssertionError('Already delivered bytes must not be copied again')
        monkeypatch.setattr(crexil_delivery, '_chunks', never_copy)
        monkeypatch.setattr(crexil_delivery, '_conversion', never_copy)
        result = await crexil_delivery.transfer(db, delivery['id'])
        assert result['items'][0]['state'] == 'complete'
        assert before == {path: Path(path).stat().st_mtime_ns for path in before}
        assert len(await db.node_events(node['id'], 'node.promoted')) == 1
    finally:
        await db.close()


async def test_delivery_stale_sources_collision_and_purge_do_not_overwrite(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        target = config.PROMOTE_DIR
        target.mkdir(parents=True, exist_ok=True)
        selection = {'node_ids': [node['id']], 'destination': str(target)}
        review = (await client.post(f"/api/crexil/projects/{p['id']}/deliveries/review", json=selection)).json()
        original = Path(node['output_path']).read_bytes()
        Path(node['output_path']).write_bytes(b'changed')
        refused = await client.post(f"/api/crexil/projects/{p['id']}/deliveries", json=command(**selection, review_token=review['token']))
        assert refused.status_code == 409
        Path(node['output_path']).write_bytes(original)
        delivery, _ = await prepared(client, p['id'], [node['id']], target)
        dest = Path(delivery['items'][0]['files'][0]['path'])
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b'External work')
        path = f"/api/crexil/deliveries/{delivery['id']}/transfer"
        failed = (await client.post(path, json=command())).json()
        assert failed['items'][0]['state'] == 'incomplete'
        assert 'different material' in failed['items'][0]['error']
        assert dest.read_bytes() == b'External work'
        dest.unlink()
        await store().purge_node(node['id'])
        failed = (await client.post(path, json=command())).json()
        assert 'purged' in failed['items'][0]['error']
        assert not dest.exists()


@pytest.mark.parametrize('kind', ['unreported_port', 'reported_port', 'mesh_fields'])
async def test_required_companion_refusal_keeps_healthy_delivery_and_needs_new_review(tmp_path, monkeypatch, kind):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        healthy = (await imported(client, p['id']))['node']
        port = {'node_id': '2', 'field': 'files', 'payload': 'file', 'role': 'companion',
                'primary': False, 'required': True, 'ordinal': 0}
        turn = await store().create_turn_with_slots(p['id'], '', {}, [
            {'engine': 'local', 'dials': {'crexil': {'output_ports': [] if kind == 'mesh_fields' else [port]}}}])
        slot = await store().dispatch_slot(turn['slots'][0]['id'], job_ref='one-attempt')
        request = DispatchRequest(slot_id=slot['id'], engine='local')
        primary = await execution_outputs.collect(store(), tmp_path, slot, request,
            CollectedOutput('primary', 'body.png', image_bytes(), role='final'))
        companion = CollectedOutput('companion', 'fields.bin', payload=None, role='companion',
            descriptor={'route': 'fields', 'advertised_path': '/runner/fields.npz'} if kind == 'mesh_fields'
            else {'declared_output': port}, error='Could not collect')
        if kind != 'unreported_port':
            await execution_outputs.collect(store(), tmp_path, slot, request, companion)
        config.PROMOTE_DIR.mkdir(parents=True, exist_ok=True)
        delivery, _ = await prepared(client, p['id'], [primary['node_id'], healthy['id']], config.PROMOTE_DIR)
        path = f"/api/crexil/deliveries/{delivery['id']}/transfer"
        result = (await client.post(path, json=command())).json()
        refused, delivered = result['items']
        assert refused['requires_review'] and refused['state'] == 'incomplete' and refused['files'] == []
        assert 'Required related material' in refused['error']
        assert delivered['state'] == 'complete'
        assert not await store().node_events(primary['node_id'], 'node.promoted')
        await execution_outputs.collect(store(), tmp_path, slot, request,
            replace(companion, data=b'voxel fields', error=None))
        # Repair cannot change the frozen refusal or silently widen a transfer.
        resumed = (await client.post(path, json=command())).json()
        assert resumed['items'] == result['items']
        fresh, _ = await prepared(client, p['id'], [primary['node_id']], config.PROMOTE_DIR)
        completed = (await client.post(f"/api/crexil/deliveries/{fresh['id']}/transfer", json=command())).json()
        assert completed['items'][0]['state'] == 'complete'
        assert any(Path(f['path']).read_bytes() == b'voxel fields' for f in completed['items'][0]['files'])
        assert (await store().get_slot(slot['id']))['attempt'] == 1


async def test_missing_primary_is_recorded_without_blocking_other_items(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        nodes = [(await imported(client, p['id']))['node'] for _ in range(2)]
        Path(nodes[0]['output_path']).unlink()
        config.PROMOTE_DIR.mkdir(parents=True, exist_ok=True)
        delivery, _ = await prepared(client, p['id'], [n['id'] for n in nodes], config.PROMOTE_DIR)
        result = (await client.post(f"/api/crexil/deliveries/{delivery['id']}/transfer", json=command())).json()
        assert [i['state'] for i in result['items']] == ['incomplete', 'complete']
        assert result['items'][0]['requires_review']
        assert 'original file is unavailable' in result['items'][0]['error']


async def test_started_copy_can_record_verified_receipt_after_source_purge(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        config.PROMOTE_DIR.mkdir(parents=True, exist_ok=True)
        delivery, _ = await prepared(client, p['id'], [node['id']], config.PROMOTE_DIR)
        copied, release = threading.Event(), threading.Event()
        original = crexil_delivery._transfer
        def waiting(item, root, *, allow_copy):
            original(item, root, allow_copy=allow_copy)
            copied.set()
            assert release.wait(timeout=5)
        monkeypatch.setattr(crexil_delivery, '_transfer', waiting)
        task = asyncio.create_task(crexil_delivery.transfer(store(), delivery['id']))
        try:
            assert await asyncio.to_thread(copied.wait, 5)
            purged = await store().purge_node(node['id'])
            for path in purged['purge_paths']:
                Path(path).unlink(missing_ok=True)
        finally:
            release.set()
        result = await task
        assert result['items'][0]['state'] == 'complete'
        assert len(await store().node_events(node['id'], 'node.promoted')) == 1
        assert all(Path(f['path']).is_file() for f in result['items'][0]['files'])
