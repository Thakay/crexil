"""Shared preparation journeys, concurrent clients and lossless transition."""

import asyncio
import base64
import io
import sqlite3
import uuid
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from app.crexil_store import CreativeStore
from app.db import Database
from test_api_v4 import booted, store

pytestmark = pytest.mark.anyio


def command(**fields):
    return {'request_id': str(uuid.uuid4()), 'actor': {'kind': 'human', 'name': 'Artist'}, **fields}


def image_bytes():
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('provenance', 'Do not strip this mark')
    metadata.add_text('workflow', '{"supplied":true}')
    Image.new('RGBA', (8, 6), (55, 90, 130, 120)).save(buffer, format='PNG', pnginfo=metadata)
    return buffer.getvalue()


async def project(client):
    response = await client.post('/api/crexil/projects', json=command(name='Brass handle', brief='A worn handle'))
    assert response.status_code == 201, response.text
    return response.json()


async def imported(client, wid, **fields):
    response = await client.post(f'/api/crexil/projects/{wid}/material', json=command(
        filename='source.png', data_base64=base64.b64encode(image_bytes()).decode(), **fields))
    assert response.status_code == 201, response.text
    return response.json()


async def test_shared_preparation_conflict_replay_and_restart(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        body = command(title='Words', setup={'prompt': 'brass'})
        path = f"/api/crexil/projects/{p['id']}/directions"
        first, replay = await asyncio.gather(client.post(path, json=body), client.post(path, json=body))
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        direction = first.json()
        changed = await client.post(path, json={**body, 'title': 'Different request'})
        assert changed.status_code == 409
        update = command(revision=0, title='Weathered brass', setup={'prompt': 'patina'})
        update['actor'] = {'kind': 'agent', 'name': 'Art assistant'}
        result = await client.patch(f"/api/crexil/directions/{direction['id']}", json=update)
        assert result.status_code == 200
        assert result.json()['revision'] == 1
        stale = await client.patch(f"/api/crexil/directions/{direction['id']}", json=command(
            revision=0, title='Stale browser', setup={'prompt': 'overwrite'}))
        assert stale.status_code == 409
        assert not await store().list_turns(p['id'])
        events = await store().events_since(0)
        assert events[-1]['payload']['actor']['name'] == 'Art assistant'
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        snapshot = await CreativeStore(db).snapshot(p['id'])
        assert len(snapshot['directions']) == 1
        assert snapshot['directions'][0]['setup']['prompt'] == 'patina'
        assert snapshot['turns'] == []
    finally:
        await db.close()


async def test_import_preserves_bytes_and_reported_lineage_selection_is_separate(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        first = await imported(client, p['id'])
        node = first['node']
        direction = first['direction']
        assert direction['selection'] == []
        assert Path(node['output_path']).read_bytes() == image_bytes()
        returned = await imported(client, p['id'], parents=[node['id']], tool='Krita', explanation='Repainted edges')
        assert returned['node']['parents'] == [node['id']]
        snap = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        origin = snap['nodes'][1]['material']['origin']
        assert origin['kind'] == 'reported_external'
        assert origin['tool'] == 'Krita'
        assert origin['embedded_metadata']['workflow'] == '{"supplied":true}'
        url = f"/api/crexil/directions/{direction['id']}/selection"
        selected = await client.put(url, json=command(revision=0, node_ids=[node['id']], reason='Good silhouette'))
        assert selected.status_code == 200, selected.text
        assert selected.json()['selection'] == [node['id']]
        assert (await store().get_node(returned['node']['id']))['parents'] == [node['id']]
        invalid = await client.put(url, json=command(revision=1, node_ids=[returned['node']['id']]))
        assert invalid.status_code == 422
        state = await client.post(f"/api/crexil/directions/{direction['id']}/state", json=command(
            revision=1, action='set_aside'))
        assert state.status_code == 200
        assert state.json()['set_aside']
        assert Path(node['output_path']).read_bytes() == image_bytes()


async def test_import_replay_invalid_bytes_and_foreign_source(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        body = command(filename='source.png', data_base64=base64.b64encode(image_bytes()).decode())
        url = f"/api/crexil/projects/{p['id']}/material"
        one = await client.post(url, json=body)
        two = await client.post(url, json=body)
        assert one.json() == two.json()
        assert len(await store().list_nodes(p['id'])) == 1
        assert len(list((tmp_path / 'staging' / 'imports').rglob('*.png'))) == 1
        other = await project(client)
        bad = await client.post(f"/api/crexil/projects/{other['id']}/material", json={
            **command(filename='returned.png', data_base64=body['data_base64']),
            'parents': [one.json()['node']['id']]})
        assert bad.status_code == 422
        invalid = await client.post(url, json=command(filename='fake.png', data_base64='no image'))
        assert invalid.status_code == 422
        assert len(await store().list_nodes(p['id'])) == 1


async def test_version_seven_copy_gains_directions_without_losing_record(tmp_path):
    original = tmp_path / 'original.sqlite'
    db = Database(original)
    await db.open()
    p = await db.create_workspace('Existing asset')
    turn = await db.create_turn_with_slots(p['id'], 'Old brief', {}, [{'engine': 'klein'}])
    sid = turn['slots'][0]['id']
    await db.dispatch_slot(sid, seed=42, submitted_graph={'graph': 'kept'})
    node = await db.land_node(sid, payload='image', verb='run', output_path='/unavailable/original.png')
    old_node = await db.get_node(node['id'])
    old_events = await db.events_since(0)
    # Make a disposable v7 fixture, then migrate a copy, never the source.
    await db.conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    await db.close()
    copied = tmp_path / 'copy.sqlite'
    with sqlite3.connect(original) as source, sqlite3.connect(copied) as target:
        source.backup(target)
    before = original.read_bytes()
    db = Database(copied)
    await db.open()
    try:
        assert await db.get_node(node['id']) == old_node
        assert await db.events_since(0) == old_events
        snapshot = await CreativeStore(db).snapshot(p['id'])
        assert len(snapshot['directions']) == 1
        assert snapshot['directions'][0]['actor'] is None
        assert snapshot['directions'][0]['selection'] == []
        assert snapshot['directions'][0]['node_ids'] == [node['id']]
        assert snapshot['nodes'][0]['available'] is False
        assert original.read_bytes() == before
    finally:
        await db.close()
