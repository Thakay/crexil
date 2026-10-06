"""Decisions stay attributed and navigable across edits, missing material and restore."""

import asyncio
from pathlib import Path

import pytest

from app.crexil_record import record
from app.db import Database
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import attest, draft, reviewed, settled
from test_crexil_restore import backup_upload, restored

pytestmark = pytest.mark.anyio


async def read(client, wid, **params):
    response = await client.get(f'/api/crexil/projects/{wid}/record', params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def assess(client, wid, **fields):
    response = await client.post(f'/api/crexil/projects/{wid}/assessments', json=command(**fields))
    assert response.status_code == 201, response.text
    return response.json()


async def test_selection_reason_revisions_preserve_choice_and_each_author(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = await imported(client, p['id'])
        node, direction = source['node'], source['direction']
        selected = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=0, node_ids=[node['id']], reason='Strong silhouette', actor={'kind': 'agent', 'name': 'Scout'}))
        assert selected.status_code == 200
        original = (await read(client, p['id']))['decisions'][0]
        revision = await assess(client, p['id'], previous_id=original['event_id'], reason='Keep the shape; revise the surface')
        history = (await read(client, p['id']))['decisions']
        assert [item['actor']['name'] for item in history] == ['Artist', 'Scout']
        assert [item['reason'] for item in history] == ['Keep the shape; revise the surface', 'Strong silhouette']
        assert revision['id'] == original['id'] and revision['previous_id'] == original['event_id']
        assert history[1]['revised_by'] == revision['event_id']
        snap = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        assert snap['directions'][0]['selection'] == [node['id']]
        assert snap['directions'][0]['revision'] == 1
        assert snap['directions'][0]['latest_decision']['actor']['name'] == 'Artist'
        assert not snap['turns']
        assert (await read(client, p['id'], q='scout'))['decisions'] == [history[1]]
        assert (await read(client, p['id'], direction_id=direction['id']))['decisions'] == history


async def test_lost_assessment_response_replays_once_and_stale_revision_refuses(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        node = (await imported(client, p['id']))['node']
        path = f"/api/crexil/projects/{p['id']}/assessments"
        body = command(node_ids=[node['id']], reason='Technically complete, unsuitable pose')
        a, b = await asyncio.gather(client.post(path, json=body), client.post(path, json=body))
        assert a.status_code == b.status_code == 201 and a.json() == b.json()
        saved = a.json()
        changed = await client.post(path, json={**body, 'reason': 'Changed intent'})
        assert changed.status_code == 409
        updates = await asyncio.gather(*[client.post(path, json=command(previous_id=saved['event_id'], reason=reason))
                                        for reason in ('Artist revision', 'Agent revision')])
        assert sorted(r.status_code for r in updates) == [201, 409]
        assert len((await read(client, p['id']))['decisions']) == 2
        assert (await client.post(path, json=body)).json() == saved
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        assert len((await record(db, p['id']))['decisions']) == 2
    finally:
        await db.close()


async def test_record_search_pages_and_unavailable_subjects(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = await imported(client, p['id'], explanation='Earlier patina')
        for reason in ('brass alpha', 'steel', 'brass beta', 'brass gamma'):
            await assess(client, p['id'], node_ids=[source['node']['id']], reason=reason)
        d = source['direction']
        assert (await client.post(f"/api/crexil/directions/{d['id']}/state", json=command(revision=0, action='set_aside'))).status_code == 200
        Path(source['node']['output_path']).unlink()
        first = await read(client, p['id'], q='brass', limit=2)
        second = await read(client, p['id'], q='brass', before=first['next_before'], limit=2)
        assert [i['reason'] for i in first['decisions'] + second['decisions']] == ['brass gamma', 'brass beta', 'brass alpha']
        assert second['next_before'] is None
        result = await read(client, p['id'], q='source')
        assert result['material_ids'] == [source['node']['id']]
        assert result['direction_ids'] == [d['id']]
        assert len(result['decisions']) == 4
        assert len((await read(client, p['id'], node_id=source['node']['id']))['decisions']) == 4
        assert (await client.get('/api/crexil/projects/missing/record')).status_code == 404


async def test_assessment_subjects_are_scoped_but_need_no_available_bytes(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p, other = await project(client), await project(client)
        source = await imported(client, p['id'])
        path = f"/api/crexil/projects/{other['id']}/assessments"
        for targets in ({'node_ids': [source['node']['id']]}, {'direction_id': source['direction']['id']},
                        {'turn_id': 'missing'}, {}):
            assert (await client.post(path, json=command(reason='Not mine', **targets))).status_code == 422
        Path(source['node']['output_path']).unlink()
        decision = await assess(client, p['id'], node_ids=[source['node']['id']], reason='Missing original; keep its history')
        assert decision['node_ids'] == [source['node']['id']]
        assert (await read(client, p['id'], direction_id=source['direction']['id']))['decisions'] == [decision]
        assert (await client.post(path, json=command(previous_id=decision['event_id'], reason='Foreign revision'))).status_code == 404
        assert (await client.post(f"/api/crexil/projects/{p['id']}/assessments", json=command(
            previous_id=decision['event_id'], node_ids=[source['node']['id']], reason='Changed scope'))).status_code == 422


async def test_decision_links_and_versions_survive_two_restores_without_rewriting_text(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = await imported(client, p['id'])
        reason = 'Literal source identity ' + source['node']['id']
        original = await assess(client, p['id'], node_ids=[source['node']['id']],
                                direction_id=source['direction']['id'], reason=reason)
        correction = await assess(client, p['id'], previous_id=original['event_id'], reason='A later opinion')
        current = p['id']
        for _ in range(2):
            upload, _bytes = await backup_upload(client, current)
            copy = await restored(client, upload)
            current = copy['project']['id']
            history = (await read(client, current))['decisions']
            assert [item['event_id'] for item in history] == [correction['event_id'], original['event_id']]
            assert history[1]['reason'] == reason
            assert history[1]['revised_by'] == correction['event_id']
            assert history[0]['node_ids'] == [copy['nodes'][0]['id']]
            assert history[0]['direction_id'] == copy['directions'][0]['id']
            assert all(item['historical'] for item in history)
        latest = await assess(client, current, previous_id=correction['event_id'], reason='Restored opinion')
        assert latest['id'] == original['id'] and not latest['historical']
        assert len((await read(client, p['id']))['decisions']) == 2


async def test_material_record_is_frozen_search_finds_original_run_and_reads_do_not_execute(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        source = await imported(client, p['id'])
        direction = await draft(client, p['id'], action='continue', instructions='Avoid shiny metal',
                                references=[{'node_id': source['node']['id'], 'contribution': 'Use the worn silhouette'}])
        review = await reviewed(client, direction)
        accepted = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=direction['revision'], review_token=review['token']))
        assert accepted.status_code == 201, accepted.text
        turns = await settled(p['id'])
        node = next(n for n in await store().list_nodes(p['id']) if n['turn_id'])
        path = f"/api/crexil/material/{node['id']}/record"
        saved = (await client.get(path)).json()
        assert saved['request']['setup']['instructions'] == 'Avoid shiny metal'
        assert saved['request']['setup']['references'][0]['contribution'] == 'Use the worn silhouette'
        assert saved['request']['operation']['revision'] == direction['setup']['operations'][0]['revision']
        assert saved['submitted'] == node['submitted_graph']
        assert saved['controls'] == node['dials']
        assert (await read(client, p['id'], q='avoid shiny'))['turn_ids'] == [turns[0]['id']]
        await client.patch(f"/api/crexil/projects/{p['id']}", json=command(revision=0, name='Changed brief', brief='Different now'))
        assert (await client.get(path)).json() == saved
        assert len(fakes['comfy'].starts) == 2
        imported_record = (await client.get(f"/api/crexil/material/{source['node']['id']}/record")).json()
        assert imported_record['request'] is None and imported_record['submitted'] is None
