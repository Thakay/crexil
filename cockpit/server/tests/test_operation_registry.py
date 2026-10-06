"""Saved custom interfaces share revision, review, execution and collection rules."""

import copy
import hashlib
import json

import pytest

from app import operation_registry as registry
from app.crexil_store import CreativeStore
from app.crexil_schemas import OperationDefinition, SourcePort
from app.db import Database
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import reviewed, settled

pytestmark = pytest.mark.anyio

GRAPH = {
    'a': {'class_type': 'LoadImage', 'inputs': {'image': 'absent.png'}},
    'b': {'class_type': 'DistinctiveStudy', 'inputs': {'image': ['a', 0], 'instruction': '', 'grain': 2, 'noise': 4, 'enabled': True}},
    'c': {'class_type': 'PreviewImage', 'inputs': {'images': ['b', 0]}},
}
INFO = {
    'LoadImage': {'input': {'required': {'image': [['installed.png'], {'image_upload': True}]}}, 'output': ['IMAGE', 'MASK']},
    'DistinctiveStudy': {'input': {'required': {'image': ['IMAGE'], 'instruction': ['STRING'], 'grain': ['INT', {'min': 1, 'max': 6}], 'noise': ['INT'], 'enabled': ['BOOLEAN']}}, 'output': ['IMAGE']},
    'PreviewImage': {'input': {'required': {'images': ['IMAGE']}}, 'output_node': True, 'output': []},
}


async def installed(definition):
    return {'state': 'inspected', 'nodes': copy.deepcopy(INFO), 'findings': [], 'gaps': [],
            'interface_sha256': registry.digest(INFO)}


async def prepared(client, monkeypatch):
    monkeypatch.setattr(registry, 'discover', installed)
    received = await client.post('/api/crexil/registrations', json=command(
        name='Distinctive custom study', source_name='study-api.json', source_text=json.dumps(GRAPH)))
    assert received.status_code == 201, received.text
    item = received.json()
    definition = item['definition']
    definition.update(actions=['continue', 'custom'], inputs=[
        {'key': 'prompt', 'type': 'text', 'required': True, 'targets': [{'node_id': 'b', 'field': 'instruction'}]},
        {'key': 'grain', 'type': 'int', 'min': 1, 'max': 6, 'default': 2, 'targets': [{'node_id': 'b', 'field': 'grain'}]},
        {'key': 'seed', 'type': 'seed', 'default': {'mode': 'fixed', 'value': 719}, 'targets': [{'node_id': 'b', 'field': 'noise'}]},
        {'key': 'enabled', 'type': 'bool', 'default': True, 'targets': [{'node_id': 'b', 'field': 'enabled'}]},
    ], sources=[{'node_id': 'a', 'field': 'image', 'role': 'structure', 'label': 'Structure'}],
        outputs=[{'node_id': 'c', 'field': 'images', 'payload': 'image', 'role': 'preview', 'primary': True}])
    response = await client.patch(f"/api/crexil/registrations/{item['id']}", json=command(
        revision=item['revision'], name=item['name'], definition=definition,
        binding={'cost_review': 'unmetered', 'cost_note': 'The installed graph and its exposed controls use only local computation.', 'downloads': 'none'}))
    assert response.status_code == 200, response.text
    return response.json()


async def direction(client, item, **controls):
    p = await project(client)
    source = await imported(client, p['id'])
    created = await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(
        title='Recorded operation test', setup={'action': 'continue', 'purpose': 'operation_test', 'prompt': 'Keep the silhouette',
            'references': [{'node_id': source['node']['id'], 'role': 'structure', 'contribution': 'Keep the outline'}],
            'operations': [{'operation_id': 'custom-' + item['id'], 'revision': item['operation_revision'], 'count': 1}],
            'controls': controls, 'attempt_limit': 1}))
    assert created.status_code == 201, created.text
    return p, source['node'], created.json()


async def test_incomplete_editor_import_is_retained_and_portable_without_installation(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        original = '{"nodes": [{"id": 1, "type": "UnknownCustomNode"}], "links": []}\n'
        body = command(name='Unfinished editor graph', source_name='editor.json', source_text=original)
        a = await client.post('/api/crexil/registrations', json=body)
        b = await client.post('/api/crexil/registrations', json=body)
        assert a.status_code == b.status_code == 201 and a.json() == b.json()
        item = a.json()
        assert item['source_text'] == original and item['source_sha256'] == hashlib.sha256(original.encode()).hexdigest()
        assert item['source_kind'] == 'comfy_editor' and any('editor JSON' in gap for gap in item['gaps'])
        assert item['inspection'] is None and item['evidence'] == []
        catalogue = (await client.get('/api/crexil/operations')).json()
        custom = next(o for o in catalogue if o['id'] == 'custom-' + item['id'])
        assert custom['availability'] == 'incomplete' and custom['maximum_usd_per_attempt'] is None
        assert all(fake.starts == [] for fake in fakes.values())
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        persisted = await registry.OperationRegistry(db).get(item['id'])
        assert persisted['source_text'] == original and persisted['revision'] == 0
        assert not await db.list_workspaces()
    finally:
        await db.close()


async def test_custom_revision_and_all_mapped_controls_freeze_through_shared_test(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        p, source, draft = await direction(client, item, grain=5, enabled=False)
        review = await reviewed(client, draft)
        assert review['ready'], review['errors']
        fakes['comfy'].default = Step(data=image_bytes())
        submitted = await client.post(f"/api/crexil/directions/{draft['id']}/submit", json=command(
            revision=draft['revision'], review_token=review['token']))
        assert submitted.status_code == 201, submitted.text
        slot = (await settled(p['id']))[0]['slots'][0]
        assert slot['submitted_graph']['b']['inputs'] == {'image': ['a', 0], 'instruction': 'Keep the silhouette\n\nSource 1: Keep the outline', 'grain': 5, 'noise': 719, 'enabled': False}
        assert slot['submitted_graph']['a']['inputs']['image'].startswith('crexil-')
        assert len(fakes['comfy'].starts) == 1
        assert next(n for n in await store().list_nodes(p['id']) if n['slot_id'])['parents'] == [source['id']]
        observed = (await client.get(f"/api/crexil/registrations/{item['id']}")).json()
        assert len(observed['evidence']) == 1
        assert observed['evidence'][0]['kind_of_work'] == 'operation_test'
        assert observed['evidence'][0]['operation_revision'] == item['operation_revision']
        assert len(observed['revisions']) == 2
        exported = (await client.get(f"/api/crexil/registrations/{item['id']}/export")).json()
        assert 'binding' not in exported and 'binding_required' in exported
        copied = (await client.post('/api/crexil/registrations', json=command(name='Portable copy', source_text=json.dumps(exported)))).json()
        assert copied['definition'] == item['definition'] and copied['binding']['cost_review'] == 'unknown'


async def test_revision_change_and_missing_runtime_dependencies_keep_preparation(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        p, _, draft = await direction(client, item)
        before = await reviewed(client, draft)
        changed = copy.deepcopy(item['definition'])
        changed['graph']['b']['inputs']['grain'] = 4
        body = command(revision=item['revision'], name=item['name'], definition=changed, binding=item['binding'])
        refused = await client.patch(f"/api/crexil/registrations/{item['id']}", json=body)
        assert refused.status_code == 422 and 'adaptation' in refused.text
        body.update(request_id=command()['request_id'], adaptation_note='Change the stored graph grain default to four.')
        revised = await client.patch(f"/api/crexil/registrations/{item['id']}", json=body)
        assert revised.status_code == 200, revised.text
        stale = await client.post(f"/api/crexil/directions/{draft['id']}/submit", json=command(revision=0, review_token=before['token']))
        assert stale.status_code == 409
        latest = (await client.get(f"/api/crexil/registrations/{item['id']}")).json()
        assert json.loads(latest['source_text']) == GRAPH
        assert len(latest['revisions']) == 3
        review = await reviewed(client, draft)
        assert not review['ready'] and any('changed since preparation' in error for error in review['errors'])
        assert not await store().list_turns(p['id']) and not fakes['comfy'].starts
        assert (await CreativeStore(store()).direction(draft['id']))['state'] == 'draft'


async def test_runtime_discovery_distinguishes_missing_choices_and_unverified_execution(monkeypatch):
    definition = OperationDefinition(graph=GRAPH)
    report = await installed(definition)
    gaps = registry.runtime_gaps(definition, report)
    assert any('absent.png' in gap for gap in gaps)
    definition.sources = [SourcePort(node_id='a', field='image')]
    assert registry.runtime_gaps(definition, report) == []


async def test_credential_controls_remain_preparation_instead_of_portable_inputs():
    definition = OperationDefinition(graph={'1': {'class_type': 'RemoteNode', 'inputs': {'api_key': ''}}},
        inputs=[{'key': 'credential', 'targets': [{'node_id': '1', 'field': 'api_key'}]}])
    assert any('Credentials belong to the runtime installation' in gap for gap in registry.structural_gaps(definition))
