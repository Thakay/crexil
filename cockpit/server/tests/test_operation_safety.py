"""Custom requests retain their meaning through refusal, dispatch and recovery.

The shared API, real executor and SQLite scheduler use only temporary state and
fake runtime replies. No test may contact an external HTTP transport.
"""

import asyncio
import copy
import hashlib
from pathlib import Path

import httpx
import pytest

from app import config, execution_outputs, operation_registry as registry
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes
from test_crexil_execution import reviewed, settled
from test_execution_outputs import CountedComfy, FileComfy, members, png
from test_operation_registry import INFO, direction, installed, prepared
from test_scheduler import events_of, opened, running, settle

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    async def forbidden_http(_transport, request):
        raise AssertionError(f'Operation regression attempted HTTP: {request.url}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_http)
    monkeypatch.setattr(config, 'COMFY_POLL_S', .001)
    monkeypatch.setattr(config, 'HISTORY_CONFIRM_TRIES', 2)
    monkeypatch.setattr(config, 'HISTORY_CONFIRM_INTERVAL_S', .001)


async def revise(client, item, definition):
    response = await client.patch(f"/api/crexil/registrations/{item['id']}", json=command(
        revision=item['revision'], name=item['name'], definition=definition,
        binding=item['binding'], adaptation_note='Explicitly revise the test interface.'))
    assert response.status_code == 200, response.text
    return response.json()


async def attempts(client, draft, count):
    setup = copy.deepcopy(draft['setup'])
    setup['operations'][0]['count'] = count
    setup['attempt_limit'] = count
    response = await client.patch(f"/api/crexil/directions/{draft['id']}", json=command(
        revision=draft['revision'], title=draft['title'], setup=setup))
    assert response.status_code == 200, response.text
    return response.json()


async def refused(client, fakes, project, draft, message):
    review = await reviewed(client, draft)
    assert not review['ready'], review
    assert any(message in error for error in review['errors']), review['errors']
    response = await client.post(f"/api/crexil/directions/{draft['id']}/submit", json=command(
        revision=draft['revision'], review_token=review['token']))
    assert response.status_code == 422, response.text
    saved = (await client.get(f"/api/crexil/projects/{project['id']}")).json()
    retained = next(item for item in saved['directions'] if item['id'] == draft['id'])
    assert retained['state'] == 'draft' and retained['setup'] == draft['setup']
    assert await store().list_turns(project['id']) == []
    assert all(fake.starts == [] for fake in fakes.values())


async def bounded_seed(client, monkeypatch):
    item = await prepared(client, monkeypatch)
    definition = copy.deepcopy(item['definition'])
    next(c for c in definition['inputs'] if c['type'] == 'seed')['key'] = 'noise_seed'
    item = await revise(client, item, definition)
    info = copy.deepcopy(INFO)
    info['DistinctiveStudy']['input']['required']['noise'] = ['INT', {'min': 0, 'max': 720}]

    async def bounded_discovery(_definition):
        return {'state': 'inspected', 'nodes': copy.deepcopy(info), 'findings': [], 'gaps': [],
                'interface_sha256': registry.digest(info)}

    monkeypatch.setattr(registry, 'discover', bounded_discovery)
    return item


async def test_named_fixed_seed_reaches_each_frozen_graph_and_record(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await bounded_seed(client, monkeypatch)
        project, _, draft = await direction(client, item, noise_seed={'mode': 'fixed', 'value': 719})
        draft = await attempts(client, draft, 2)
        review = await reviewed(client, draft)
        assert review['ready'], review['errors']
        fakes['comfy'].default = Step(data=image_bytes())
        response = await client.post(f"/api/crexil/directions/{draft['id']}/submit", json=command(
            revision=draft['revision'], review_token=review['token']))
        assert response.status_code == 201, response.text
        slots = (await settled(project['id']))[0]['slots']
        assert [slot['seed'] for slot in slots] == [719, 720]
        assert [slot['submitted_graph']['b']['inputs']['noise'] for slot in slots] == [719, 720]
        assert all(slot['dials']['noise_seed'] == {'mode': 'fixed', 'value': 719} for slot in slots)
        assert len(fakes['comfy'].starts) == 2


@pytest.mark.parametrize(('seed', 'count', 'message'), [
    (721, 1, 'outside the runtime range'),
    (720, 2, 'seed sequence exceeds the runtime range'),
], ids=['requested_seed', 'sequence_end'])
async def test_invalid_seed_or_sequence_refuses_without_losing_preparation(tmp_path, monkeypatch,
                                                                          seed, count, message):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await bounded_seed(client, monkeypatch)
        project, _, draft = await direction(client, item, noise_seed={'mode': 'fixed', 'value': seed})
        draft = await attempts(client, draft, count)
        await refused(client, fakes, project, draft, message)


@pytest.mark.parametrize('inactive', [False, True], ids=['unmapped', 'inactive'])
async def test_saved_prompt_requires_an_active_instruction_mapping(tmp_path, monkeypatch, inactive):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        definition = copy.deepcopy(item['definition'])
        if inactive:
            next(c for c in definition['inputs'] if c['key'] == 'prompt')['when'] = {'enabled': True}
        else:
            definition['inputs'] = [c for c in definition['inputs'] if c['key'] != 'prompt']
        item = await revise(client, item, definition)
        project, _, draft = await direction(client, item, enabled=False)
        await refused(client, fakes, project, draft, 'no active prompt control')


async def test_model_choice_cannot_be_used_as_uploaded_image_source(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        definition = copy.deepcopy(item['definition'])
        definition['graph']['a']['inputs']['image'] = 'installed.png'
        definition['graph']['model'] = {'class_type': 'ModelLoader', 'inputs': {'model_name': 'known.safetensors'}}
        definition['sources'][0].update(node_id='model', field='model_name')
        item = await revise(client, item, definition)
        info = copy.deepcopy(INFO)
        info['ModelLoader'] = {'input': {'required': {'model_name': [['known.safetensors'], {}]}}, 'output': ['MODEL']}

        async def model_discovery(_definition):
            return {'state': 'inspected', 'nodes': copy.deepcopy(info), 'findings': [], 'gaps': [],
                    'interface_sha256': registry.digest(info)}

        monkeypatch.setattr(registry, 'discover', model_discovery)
        project, _, draft = await direction(client, item)
        await refused(client, fakes, project, draft, 'does not advertise an uploaded image filename')


async def test_storage_prefix_mapping_is_saved_as_a_gap_and_cannot_execute(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        definition = copy.deepcopy(item['definition'])
        definition['graph']['c']['class_type'] = 'SaveImage'
        definition['graph']['c']['inputs']['filename_prefix'] = 'original-prefix'
        definition['inputs'].append({'key': 'filename', 'type': 'text', 'default': 'artist-choice',
            'targets': [{'node_id': 'c', 'field': 'filename_prefix'}]})
        item = await revise(client, item, definition)
        assert any('filename_prefix' in gap for gap in item['gaps'])
        assert item['definition']['graph']['c']['inputs']['filename_prefix'] == 'original-prefix'
        project, _, draft = await direction(client, item, filename='keep-this-setting')
        await refused(client, fakes, project, draft, 'belongs to execution storage')


async def test_inspection_delivery_replays_original_receipt_after_revision(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        item = await prepared(client, monkeypatch)
        calls = []

        async def counted_discovery(definition):
            calls.append(copy.deepcopy(definition.graph))
            return await installed(definition)

        monkeypatch.setattr(registry, 'discover', counted_discovery)
        body = command(revision=item['revision'])
        endpoint = f"/api/crexil/registrations/{item['id']}/inspect"
        first = await client.post(endpoint, json=body)
        assert first.status_code == 200, first.text
        definition = copy.deepcopy(item['definition'])
        definition['description'] = 'Later description must not invalidate a delivered receipt.'
        current = await revise(client, item, definition)
        replay = await client.post(endpoint, json=body)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert len(calls) == 1
        assert len(await events_of(store(), 'operation.inspected')) == 1
        latest = (await client.get(f"/api/crexil/registrations/{item['id']}")).json()
        assert latest['revision'] == current['revision'] and latest['inspection'] is None
        conflicting = await client.post(endpoint, json={**body, 'revision': current['revision']})
        assert conflicting.status_code == 409 and len(calls) == 1
        assert all(fake.starts == [] for fake in fakes.values())


async def prepare_output_turn(db, workspace, ports):
    turn = await db.create_turn_with_slots(workspace['id'], '', {}, [{
        'engine': 'custom-test', 'submitted_graph': {'42': {'class_type': 'Result', 'inputs': {}}},
        'dials': {'crexil': {'automatic_retry': False, 'output_ports': ports}}}])
    slot = turn['slots'][0]
    payload = next(port['payload'] for port in ports if port['primary'])
    await db.append_event('slot.prepared', workspace_id=workspace['id'], turn_id=turn['id'],
        slot_id=slot['id'], payload={'payload': payload, 'verb': 'custom'})
    return turn


@pytest.mark.parametrize('broken_index', [0, 1], ids=['primary_image', 'related_image'])
async def test_malformed_declared_image_keeps_every_file_but_fails_collection(tmp_path, broken_index):
    blobs = [png((11, 22, 33)), png((44, 55, 66))]
    blobs[broken_index] = b'{"this":"is not an image"}'
    names = ['final.png', 'layer.png']
    ports = [{'node_id': '42', 'field': 'images', 'ordinal': i, 'payload': 'image',
              'role': 'final' if i == 0 else 'layer', 'primary': i == 0, 'required': True}
             for i in range(2)]
    entry = {'status': {'status_str': 'success'}, 'outputs': {'42': {'images': [
        {'filename': name, 'type': 'output'} for name in names]}}}
    fake = FileComfy(entry, {(name, '', 'output'): blob for name, blob in zip(names, blobs, strict=True)})
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Malformed declared image')
        async with running(db, tmp_path, [executor], {'custom-test': 'comfy'}, retry_policy={}) as sched:
            turn = await prepare_output_turn(db, workspace, ports)
            await sched.adopt_prepared_turn(turn['id'])
            done = (await settle(db, turn['id'], until={'failed'}))['slots'][0]
            assert done['kind'] == 'collection' and 'could not be decoded' in done['detail']
            group, outputs = await members(db, done['id'])
            assert group['state'] == 'incomplete' and len(outputs) == 2
            for index, output in enumerate(outputs):
                assert Path(output['output_path']).read_bytes() == blobs[index]
                assert output['sha256'] == hashlib.sha256(blobs[index]).hexdigest()
                assert output['role'] == ports[index]['role']
                assert bool(output['node_id']) == (index != broken_index)
            assert outputs[broken_index]['error'] and outputs[1 - broken_index]['error'] is None
            # Cached malformed bytes must never turn a failed attempt into success.
            retained_ids = [output['id'] for output in outputs]
            fake.views.clear()
            repaired = await sched.collect_existing(done['id'], done['attempt'])
            assert repaired['state'] == 'incomplete' and (await db.get_slot(done['id']))['status'] == 'failed'
            assert [output['id'] for output in (await members(db, done['id']))[1]] == retained_ids
            assert fake.views == [] and executor.start_calls == 1
            assert await events_of(db, 'node.landed') == []
            assert await db.workspace_charged(workspace['id']) == 0


@pytest.mark.parametrize(('payload', 'filename'), [('image', 'picture.bin'), ('file', 'report.png')])
@pytest.mark.parametrize('history_available', [True, False], ids=['runtime_present', 'runtime_gone'])
async def test_interrupted_receipt_keeps_declared_payload_independent_of_filename(tmp_path, monkeypatch,
                                                                                payload, filename, history_available):
    blob = png((5, 8, 13)) if payload == 'image' else b'ordinary file bytes, not an image'
    port = {'node_id': '42', 'field': 'files', 'payload': payload, 'role': 'final', 'primary': True, 'required': True}
    entry = {'status': {'status_str': 'success'}, 'outputs': {'42': {'files': [{'filename': filename, 'type': 'temp'}]}}}
    key = (filename, '', 'temp')
    first_client = FileComfy(entry, {key: blob})
    first_executor = CountedComfy(first_client)
    written = asyncio.Event()
    atomic_bytes = execution_outputs.atomic_bytes

    def crash_after_rename(path, data):
        atomic_bytes(path, data)
        written.set()
        raise asyncio.CancelledError

    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Declared payload survives interruption')
        with monkeypatch.context() as patch:
            patch.setattr(execution_outputs, 'atomic_bytes', crash_after_rename)
            async with running(db, tmp_path, [first_executor], {'custom-test': 'comfy'}, retry_policy={}) as sched:
                turn = await prepare_output_turn(db, workspace, [port])
                sid = turn['slots'][0]['id']
                await sched.adopt_prepared_turn(turn['id'])
                await asyncio.wait_for(written.wait(), 2)
                group_before, outputs = await members(db, sid)
                orphan = outputs[0]
                assert len(outputs) == 1 and orphan['sha256'] is None and orphan['node_id'] is None
                assert Path(orphan['output_path']).read_bytes() == blob
                assert await db.list_nodes(workspace['id']) == []
        assert (await db.get_slot(sid))['status'] == 'running'
        assert first_executor.start_calls == 1

    second_client = FileComfy(entry, {key: blob})
    if not history_available:
        second_client.history_map.clear()
    second_executor = CountedComfy(second_client)
    async with opened(tmp_path) as db:
        async with running(db, tmp_path, [second_executor], {'custom-test': 'comfy'}, retry_policy={}):
            done = (await settle(db, turn['id'], until={'landed', 'failed'}))['slots'][0]
        group_after, outputs = await members(db, sid)
        recovered = outputs[0]
        assert len(outputs) == 1 and group_after['id'] == group_before['id']
        assert recovered['id'] == orphan['id'] and recovered['output_path'] == orphan['output_path']
        assert recovered['sha256'] == hashlib.sha256(blob).hexdigest()
        assert Path(recovered['output_path']).read_bytes() == blob and recovered['role'] == 'final'
        assert bool(recovered['node_id']) == (payload == 'image')
        nodes = await db.list_nodes(workspace['id'])
        assert len(nodes) == (1 if payload == 'image' else 0)
        if nodes:
            assert nodes[0]['payload'] == 'image' and nodes[0]['output_path'] == orphan['output_path']
        assert done['status'] == ('landed' if history_available else 'failed')
        assert group_after['state'] == ('complete' if history_available else 'uncertain')
        assert second_executor.start_calls == 0 and second_client.submitted == [] and second_client.views == []
        assert done['attempt'] == 1 and await db.workspace_charged(workspace['id']) == 0
