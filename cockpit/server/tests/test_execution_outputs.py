"""Collected runtime files survive failure, restart and explicit purge.

The real executor and scheduler run against a descriptor-aware fake client.
Only temporary files and SQLite stores are touched; HTTP transport is forbidden.
"""

import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app import api_crexil, config, execution_outputs, wiring
from app.executors import ComfyExecutor
from test_executors import FakeComfy
from test_scheduler import events_of, make_batch, opened, running, settle

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline_fast_runtime(monkeypatch):
    async def forbidden_http(_transport, request):
        raise AssertionError(f'Output regression attempted HTTP: {request.url}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_http)
    monkeypatch.setattr(config, 'COMFY_POLL_S', .001)
    monkeypatch.setattr(config, 'HISTORY_CONFIRM_TRIES', 2)
    monkeypatch.setattr(config, 'HISTORY_CONFIRM_INTERVAL_S', .001)


def png(color):
    image = Image.new('RGBA', (3, 2), (*color, 123))
    image.putpixel((2, 1), (200, 80, 11, 255))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def related_files():
    """Same basenames remain distinct across folders and runtime file types."""
    entry = {
        'status': {'status_str': 'success', 'completed': True},
        'outputs': {
            '7': {
                'images': [
                    {'filename': 'same.png', 'subfolder': 'body', 'type': 'output'},
                    {'filename': 'same.png', 'subfolder': 'coat', 'type': 'output'},
                ],
                'metadata': [{'filename': 'notes.json', 'subfolder': 'meta', 'type': 'output'}],
            },
            '12': {'images': [
                {'filename': 'same.png', 'subfolder': 'body', 'type': 'temp'},
            ]},
        },
    }
    files = {
        ('same.png', 'body', 'output'): png((11, 22, 33)),
        ('same.png', 'coat', 'output'): png((44, 55, 66)),
        ('notes.json', 'meta', 'output'): b'{"unclassified":"runtime metadata"}\n',
        ('same.png', 'body', 'temp'): png((77, 88, 99)),
    }
    return entry, files


class FileComfy(FakeComfy):
    def __init__(self, entry, files):
        super().__init__()
        self.history_map['pid-1'] = copy.deepcopy(entry)
        self.files = dict(files)
        self.views = []
        self.entered = {}
        self.gates = {}

    async def view(self, filename, subfolder='', type_='output'):
        key = (filename, subfolder, type_)
        self.views.append(key)
        self.entered.setdefault(key, asyncio.Event()).set()
        if key in self.gates:
            await self.gates[key].wait()
        result = self.files[key]
        if isinstance(result, Exception):
            raise result
        return result


class CountedComfy(ComfyExecutor):
    def __init__(self, client):
        super().__init__(client)
        self.start_calls = 0

    async def start(self, request):
        self.start_calls += 1
        return await super().start(request)


async def members(db, sid):
    group = await db._one('SELECT * FROM execution_groups WHERE slot_id=?', (sid,))
    assert group is not None
    rows = await db._all('SELECT * FROM execution_outputs WHERE group_id=? ORDER BY ordinal',
                         (group['id'],))
    return dict(group), [dict(row) for row in rows]


def descriptor_key(member):
    descriptor = json.loads(member['descriptor_json'])
    return descriptor['filename'], descriptor.get('subfolder', ''), descriptor.get('type', 'output')


async def test_scheduler_keeps_all_runtime_files_and_distinct_same_named_outputs(tmp_path):
    entry, files = related_files()
    fake = FileComfy(entry, files)
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Layered result')
        source = tmp_path / 'source.png'
        source.write_bytes(png((21, 42, 63)))
        parent = await db.import_node(workspace['id'], output_path=str(source))
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')],
                                               parents=[parent['id']])
            await sched.submit_turn(turn['id'], requests)
            done = await settle(db, turn['id'], until={'landed'})
        slot = done['slots'][0]
        group, outputs = await members(db, slot['id'])
        assert group['state'] == 'complete' and group['job_ref'] == 'pid-1'
        assert group['take'] == slot['attempt'] == 1
        assert len(outputs) == len(files) == len(set(fake.views)) == 4
        assert len({item['output_path'] for item in outputs}) == 4
        assert executor.start_calls == len(fake.submitted) == 1
        generated = [node for node in await db.list_nodes(workspace['id']) if node['slot_id']]
        assert len(generated) == 3
        for item in outputs:
            blob = files[descriptor_key(item)]
            assert Path(item['output_path']).read_bytes() == blob
            assert item['sha256'] == hashlib.sha256(blob).hexdigest()
            assert item['size_bytes'] == len(blob)
            assert item['role'] == 'unclassified' and item['error'] is None
            assert json.loads(item['descriptor_json'])['job_ref'] == 'pid-1'
            if descriptor_key(item)[0] == 'notes.json':
                assert item['node_id'] is None
            else:
                node = await db.get_node(item['node_id'])
                assert node['parents'] == [parent['id']]
                assert node['output_index'] == item['ordinal']
                assert node['submitted_graph'] == requests[0].submitted_graph
        assert len(await events_of(db, 'node.landed')) == 1
        assert len(await events_of(db, 'execution.output_collected')) == 4
        assert await db.workspace_charged(workspace['id']) == 0
        public = (await execution_outputs.inventory(db, workspace['id']))[0]
        assert all(item['available'] and 'output_path' not in item for item in public['outputs'])


@pytest.mark.parametrize('payload,field,filename,role', [
    ('image', 'images', 'mask.png', 'mask'),
    ('file', 'documents', 'report.json', 'final'),
])
async def test_declared_primary_can_be_a_temp_mask_or_file_without_fabricated_image(tmp_path, payload, field, filename, role):
    out = io.BytesIO()
    Image.new('L', (4, 4), 255).save(out, format='PNG')
    blob = out.getvalue() if payload == 'image' else b'{"area":16}'
    entry = {'status': {'status_str': 'success'}, 'outputs': {'42': {field: [{'filename': filename, 'type': 'temp'}]}}}
    fake = FileComfy(entry, {(filename, '', 'temp'): blob})
    executor = CountedComfy(fake)
    port = {'node_id': '42', 'field': field, 'payload': payload, 'role': role, 'required': True, 'primary': True}
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Declared result')
        async with running(db, tmp_path, [executor], {'custom-test': 'comfy'}, retry_policy={}) as sched:
            turn = await db.create_turn_with_slots(workspace['id'], '', {}, [{
                'engine': 'custom-test', 'submitted_graph': {'42': {'class_type': 'MaskReport', 'inputs': {}}},
                'dials': {'crexil': {'output_ports': [port]}}}])
            slot = turn['slots'][0]
            await db.append_event('slot.prepared', workspace_id=workspace['id'], turn_id=turn['id'], slot_id=slot['id'], payload={'payload': payload})
            await sched.adopt_prepared_turn(turn['id'])
            done = (await settle(db, turn['id'], until={'landed'}))['slots'][0]
            # Recovery reconstructs the same declared meanings from storage.
            rebuilt = await sched._rebuild_request(done)
            assert rebuilt.output_ports == (port,) and rebuilt.payload == payload
        group, outputs = await members(db, slot['id'])
        assert group['state'] == 'complete' and len(outputs) == 1
        assert outputs[0]['role'] == role and Path(outputs[0]['output_path']).read_bytes() == blob
        assert bool(outputs[0]['node_id']) == (payload == 'image')
        assert done['finished_at'] and executor.start_calls == 1


async def test_missing_required_declared_output_fails_after_preserving_present_files(tmp_path):
    entry, files = related_files()
    executor = CountedComfy(FileComfy(entry, files))
    ports = (
        {'node_id': '7', 'field': 'images', 'role': 'final', 'payload': 'image', 'primary': True, 'required': True},
        {'node_id': 'missing', 'field': 'images', 'role': 'mask', 'payload': 'image', 'primary': False, 'required': True},
    )
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Missing required mask')
        async with running(db, tmp_path, [executor], {'custom-test': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('custom-test', 'local')])
            await sched.submit_turn(turn['id'], [replace(requests[0], output_ports=ports)])
            done = (await settle(db, turn['id'], until={'failed'}))['slots'][0]
        group, outputs = await members(db, done['id'])
        assert done['kind'] == 'collection' and 'Required mask' in done['detail']
        assert len(outputs) == len(files) and group['state'] == 'incomplete'
        assert [item['role'] for item in outputs[:2]] == ['final', 'final']


@pytest.mark.parametrize('failure', ['later_fetch', 'engine'])
async def test_scheduler_keeps_collected_files_when_fetch_or_engine_fails(tmp_path, failure):
    entry, files = related_files()
    missing_key = ('same.png', 'coat', 'output')
    if failure == 'later_fetch':
        files[missing_key] = httpx.ReadError('second file disconnected')
    else:
        entry['status'] = {
            'status_str': 'error',
            'messages': [['execution_error', {'exception_message': 'A later node failed'}]],
        }
    fake = FileComfy(entry, files)
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Interrupted collection')
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')])
            await sched.submit_turn(turn['id'], requests)
            done = await settle(db, turn['id'], until={'failed'})
        slot = done['slots'][0]
        group, outputs = await members(db, slot['id'])
        assert group['state'] == 'incomplete'
        assert slot['kind'] == ('collection' if failure == 'later_fetch' else 'error')
        assert slot['attempt'] == executor.start_calls == len(fake.submitted) == 1
        assert len(outputs) == 4 and set(fake.views) == set(files)
        for item in outputs:
            blob = files[descriptor_key(item)]
            if isinstance(blob, Exception):
                assert item['error'] and item['node_id'] is None
                assert item['sha256'] is None
                assert item['output_path'] and not Path(item['output_path']).exists()
            else:
                assert Path(item['output_path']).read_bytes() == blob
                assert item['sha256'] == hashlib.sha256(blob).hexdigest()
        assert len(await db.list_nodes(workspace['id'])) == (2 if failure == 'later_fetch' else 3)
        assert await events_of(db, 'node.landed') == []
        failed_event = (await events_of(db, 'slot.failed'))[0]
        collected = await events_of(db, 'execution.output_collected')
        assert len(collected) == (3 if failure == 'later_fetch' else 4)
        assert all(event['seq'] < failed_event['seq'] for event in collected)
        assert await db.workspace_charged(workspace['id']) == 0


@pytest.mark.parametrize('purge_first', [False, True], ids=['resume', 'purged_member'])
async def test_same_job_recovery_reuses_committed_members_without_start_or_resurrection(tmp_path,
                                                                                     purge_first):
    entry, files = related_files()
    second = ('same.png', 'coat', 'output')
    fake = FileComfy(entry, files)
    fake.entered[second] = asyncio.Event()
    fake.gates[second] = asyncio.Event()
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Durable collection')
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')])
            sid = turn['slots'][0]['id']
            await sched.submit_turn(turn['id'], requests)
            await asyncio.wait_for(fake.entered[second].wait(), 2)
            group_before, before = await members(db, sid)
            assert len(before) == 4 and group_before['state'] == 'collecting'
            assert {descriptor_key(item) for item in before} == set(files)
            collected = [item for item in before if item['sha256']]
            assert len(collected) == 1
            pending = [item for item in before if not item['sha256']]
            assert all(item['output_path'] and not Path(item['output_path']).exists()
                       and item['node_id'] is None and item['error'] is None for item in pending)
            original = collected[0]
            identities_before = {item['identity']: (item['id'], item['ordinal'], item['created_at'])
                                 for item in before}
            original_path = Path(original['output_path'])
            assert original_path.read_bytes() == files[('same.png', 'body', 'output')]
            assert (await db.get_slot(sid))['status'] == 'running'
            if purge_first:
                await db.set_aside_node(original['node_id'])
                purged = await db.purge_node(original['node_id'])
                for path in purged['purge_paths']:
                    Path(path).unlink(missing_ok=True)
                assert not original_path.exists()
        assert (await db.get_slot(sid))['status'] == 'running'
        assert executor.start_calls == 1

    # Reordering runtime output-node traversal must not change an existing identity.
    entry['outputs'] = dict(reversed(list(entry['outputs'].items())))
    recovered_client = FileComfy(entry, files)
    recovered_executor = CountedComfy(recovered_client)
    async with opened(tmp_path) as db:
        async with running(db, tmp_path, [recovered_executor], {'klein': 'comfy'}, retry_policy={}):
            await settle(db, turn['id'], until={'landed'})
        group_after, after = await members(db, sid)
        assert group_after['id'] == group_before['id']
        assert group_after['state'] == 'complete' and group_after['job_ref'] == 'pid-1'
        assert len(after) == 4
        assert {item['identity']: (item['id'], item['ordinal'], item['created_at'])
                for item in after} == identities_before
        retained = next(item for item in after if item['id'] == original['id'])
        assert retained == original
        assert recovered_executor.start_calls == 0 and recovered_client.submitted == []
        expected_fetches = set(files)
        if not purge_first:
            expected_fetches.remove(descriptor_key(original))
        assert set(recovered_client.views) == expected_fetches
        assert len(recovered_client.views) == len(expected_fetches)
        assert len(await events_of(db, 'node.collected')) == 3
        assert len(await events_of(db, 'node.landed')) == 1
        assert len(await events_of(db, 'execution.output_collected')) == 4
        node = await db.get_node(original['node_id'])
        assert bool(node['purged_at']) is purge_first
        assert original_path.exists() is not purge_first
        public = (await execution_outputs.inventory(db, workspace['id']))[0]
        visible = next(item for item in public['outputs'] if item['id'] == original['id'])
        assert visible['available'] is not purge_first
        for item in after:
            if not (purge_first and item['id'] == original['id']):
                assert Path(item['output_path']).read_bytes() == files[descriptor_key(item)]

        # A second restart neither fetches completed work nor creates receipts again.
        quiet_client = FileComfy(entry, files)
        quiet_executor = CountedComfy(quiet_client)
        async with running(db, tmp_path, [quiet_executor], {'klein': 'comfy'}, retry_policy={}):
            pass
        _, reopened = await members(db, sid)
        assert reopened == after
        assert quiet_executor.start_calls == 0 and quiet_client.views == []
        assert len(await events_of(db, 'node.landed')) == 1
        assert len(await events_of(db, 'execution.output_collected')) == 4


async def test_shared_collect_api_repairs_missing_files_without_start_or_new_charge(tmp_path,
                                                                                monkeypatch):
    entry, files = related_files()
    missing_key = ('same.png', 'coat', 'output')
    fake = FileComfy(entry, {**files, missing_key: httpx.ReadError('file not yet available')})
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Repair existing runtime files')
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')])
            sid = turn['slots'][0]['id']
            await sched.submit_turn(turn['id'], requests)
            failed = (await settle(db, turn['id'], until={'failed'}))['slots'][0]
            assert failed['kind'] == 'collection'
            group_before, before = await members(db, sid)
            missing = next(item for item in before if descriptor_key(item) == missing_key)
            assert missing['error'] and missing['node_id'] is None
            charges_before = [(event['seq'], event['charged_usd'])
                              for event in await db.events_since(0)
                              if event['charged_usd'] is not None]
            total_before = await db.workspace_charged(workspace['id'])

            # Mount the production action router over the same real scheduler/store.
            # This avoids starting an application stack or any external catalogue.
            app = FastAPI()
            app.include_router(api_crexil.router)
            monkeypatch.setattr(wiring, 'current', SimpleNamespace(db=db, scheduler=sched))
            body = {'request_id': 'collect-existing-regression', 'take': failed['attempt'],
                    'actor': {'kind': 'agent', 'name': 'Collection regression'}}
            endpoint = f'/api/crexil/attempts/{sid}/collect'
            fake.views.clear()
            fake.files[missing_key] = files[missing_key]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://test') as client:
                stale = await client.post(endpoint, json={**body,
                    'request_id': 'collect-stale-regression', 'take': failed['attempt'] + 1})
                assert stale.status_code == 422
                assert fake.views == []
                response = await client.post(endpoint, json=body)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['id'] == group_before['id'] and result['state'] == 'complete'
                assert {item['id'] for item in result['outputs']} == {item['id'] for item in before}
                assert all(item['available'] and item['error'] is None for item in result['outputs'])
                assert fake.views == [missing_key]
                downloaded = await client.get(f"/api/crexil/outputs/{missing['id']}/file")
                assert downloaded.status_code == 200 and downloaded.content == files[missing_key]

                # A lost response may replay the command, but cannot generate or bill.
                replay = await client.post(endpoint, json=body)
                assert replay.status_code == 200, replay.text
                assert {item['id'] for item in replay.json()['outputs']} == {item['id'] for item in before}
                assert fake.views == [missing_key]

            repaired = await db.get_slot(sid)
            assert repaired['status'] == 'landed'
            assert repaired['kind'] is repaired['cause'] is repaired['detail'] is None
            assert repaired['attempt'] == failed['attempt'] == 1
            assert repaired['job_ref'] == failed['job_ref'] == 'pid-1'
            assert executor.start_calls == len(fake.submitted) == 1
            assert len(await db.list_nodes(workspace['id'])) == 3
            assert len(await events_of(db, 'execution.collection_requested')) == 1
            assert len(await events_of(db, 'slot.collection_recovered')) == 1
            assert len(await events_of(db, 'execution.output_collected')) == 4
            assert await events_of(db, 'node.landed') == []
            assert [(event['seq'], event['charged_usd']) for event in await db.events_since(0)
                    if event['charged_usd'] is not None] == charges_before
            assert await db.workspace_charged(workspace['id']) == total_before


@pytest.mark.parametrize('damaged_key', [
    ('same.png', 'body', 'output'),
    ('notes.json', 'meta', 'output'),
], ids=['image', 'independent_file'])
async def test_collect_existing_repairs_corrupt_bytes_without_changing_receipts(tmp_path,
                                                                               damaged_key):
    entry, files = related_files()
    fake = FileComfy(entry, files)
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Repair a damaged staged file')
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')])
            sid = turn['slots'][0]['id']
            await sched.submit_turn(turn['id'], requests)
            await settle(db, turn['id'], until={'landed'})
            group_before, before = await members(db, sid)
            original = next(item for item in before if descriptor_key(item) == damaged_key)
            path = Path(original['output_path'])
            path.write_bytes(b'damaged bytes still occupy the original path')
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() != original['sha256']
            fake.views.clear()
            charges_before = await db.workspace_charged(workspace['id'])

            repaired = await sched.collect_existing(sid, 1)
            assert repaired['id'] == group_before['id'] and repaired['state'] == 'complete'
            assert fake.views == [damaged_key]
            assert path.read_bytes() == files[damaged_key]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == original['sha256']
            _, after = await members(db, sid)
            assert after == before
            assert executor.start_calls == len(fake.submitted) == 1
            assert (await db.get_slot(sid))['attempt'] == 1
            assert len(await events_of(db, 'execution.output_collected')) == 4
            assert len(await events_of(db, 'node.landed')) == 1
            assert await db.workspace_charged(workspace['id']) == charges_before

            # Once repaired, every file again qualifies for the verified cache.
            await sched.collect_existing(sid, 1)
            assert fake.views == [damaged_key]
            assert executor.start_calls == 1
            assert (await members(db, sid))[1] == before
            assert await db.workspace_charged(workspace['id']) == charges_before


@pytest.mark.parametrize('history_available', [True, False], ids=['runtime_present', 'runtime_gone'])
async def test_atomic_file_survives_crash_before_its_receipt_commits(tmp_path, monkeypatch,
                                                                 history_available):
    entry, files = related_files()
    first_key = ('same.png', 'body', 'output')
    client_before = FileComfy(entry, files)
    executor_before = CountedComfy(client_before)
    wrote_file = asyncio.Event()
    written_paths = []
    original_atomic_bytes = execution_outputs.atomic_bytes

    def stop_after_atomic_write(path, data):
        original_atomic_bytes(path, data)
        written_paths.append(path)
        wrote_file.set()
        # Model process termination after rename, before the SQLite receipt commits.
        raise asyncio.CancelledError

    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Atomic file without its receipt')
        with monkeypatch.context() as patch:
            patch.setattr(execution_outputs, 'atomic_bytes', stop_after_atomic_write)
            async with running(db, tmp_path, [executor_before], {'klein': 'comfy'}, retry_policy={}) as sched:
                turn, requests = await make_batch(db, workspace, [('klein', 'local')])
                sid = turn['slots'][0]['id']
                await sched.submit_turn(turn['id'], requests)
                await asyncio.wait_for(wrote_file.wait(), 2)
                group_before, before = await members(db, sid)
                assert len(before) == 4 and group_before['state'] == 'collecting'
                assert all(item['sha256'] is None and item['node_id'] is None for item in before)
                orphan = next(item for item in before if descriptor_key(item) == first_key)
                assert written_paths == [Path(orphan['output_path'])]
                assert written_paths[0].read_bytes() == files[first_key]
                assert await db.list_nodes(workspace['id']) == []
                assert await events_of(db, 'execution.output_collected') == []
            assert (await db.get_slot(sid))['status'] == 'running'
        assert executor_before.start_calls == len(client_before.submitted) == 1

    client_after = FileComfy(entry, files)
    if not history_available:
        client_after.history_map.clear()
    executor_after = CountedComfy(client_after)
    async with opened(tmp_path) as db:
        async with running(db, tmp_path, [executor_after], {'klein': 'comfy'}, retry_policy={}):
            finished = (await settle(db, turn['id'], until={'landed', 'failed'}))['slots'][0]
        group_after, after = await members(db, sid)
        assert group_after['id'] == group_before['id']
        assert {item['identity']: (item['id'], item['ordinal']) for item in after} == {
            item['identity']: (item['id'], item['ordinal']) for item in before}
        adopted = next(item for item in after if item['id'] == orphan['id'])
        assert adopted['sha256'] == hashlib.sha256(files[first_key]).hexdigest()
        assert adopted['output_path'] == orphan['output_path']
        assert Path(adopted['output_path']).read_bytes() == files[first_key]
        node = await db.get_node(adopted['node_id'])
        assert node['submitted_graph'] == requests[0].submitted_graph
        assert node['output_path'] == orphan['output_path']
        assert executor_after.start_calls == 0 and client_after.submitted == []
        assert finished['attempt'] == 1
        assert first_key not in client_after.views
        if history_available:
            assert finished['status'] == 'landed' and group_after['state'] == 'complete'
            assert set(client_after.views) == set(files) - {first_key}
            assert sum(bool(item['sha256']) for item in after) == 4
        else:
            assert finished['status'] == 'failed' and finished['kind'] == 'uncertain'
            assert group_after['state'] == 'uncertain'
            assert client_after.views == []
            assert sum(bool(item['sha256']) for item in after) == 1
        assert await db.workspace_charged(workspace['id']) == 0


async def test_missing_descriptor_cannot_erase_an_unresolved_expected_output(tmp_path):
    entry, files = related_files()
    missing_key = ('same.png', 'coat', 'output')
    fake = FileComfy(entry, {**files, missing_key: httpx.ReadError('not collected')})
    executor = CountedComfy(fake)
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Runtime history lost one descriptor')
        async with running(db, tmp_path, [executor], {'klein': 'comfy'}, retry_policy={}) as sched:
            turn, requests = await make_batch(db, workspace, [('klein', 'local')])
            sid = turn['slots'][0]['id']
            await sched.submit_turn(turn['id'], requests)
            await settle(db, turn['id'], until={'failed'})
            group_before, before = await members(db, sid)
            missing = next(item for item in before if descriptor_key(item) == missing_key)
            assert missing['sha256'] is None and missing['error']
            fake.history_map['pid-1']['outputs']['7']['images'].pop()
            fake.views.clear()

            incomplete = await sched.collect_existing(sid, 1)
            assert incomplete['id'] == group_before['id'] and incomplete['state'] == 'incomplete'
            assert json.loads(incomplete['metadata_json'])['unresolved_output_ids'] == [missing['id']]
            assert (await members(db, sid))[1] == before
            failed = await db.get_slot(sid)
            assert failed['status'] == 'failed' and failed['kind'] == 'collection'
            assert await events_of(db, 'slot.collection_recovered') == []
            assert fake.views == []

            # Only actual retrieval of the retained missing member can repair this attempt.
            fake.history_map['pid-1'] = copy.deepcopy(entry)
            fake.files[missing_key] = files[missing_key]
            repaired = await sched.collect_existing(sid, 1)
            assert repaired['id'] == group_before['id'] and repaired['state'] == 'complete'
            assert json.loads(repaired['metadata_json'])['unresolved_output_ids'] == []
            assert fake.views == [missing_key]
            after = (await members(db, sid))[1]
            assert {item['id'] for item in after} == {item['id'] for item in before}
            retained = next(item for item in after if item['id'] == missing['id'])
            assert Path(retained['output_path']).read_bytes() == files[missing_key]
            assert (await db.get_slot(sid))['status'] == 'landed'
            assert len(await events_of(db, 'slot.collection_recovered')) == 1
            assert executor.start_calls == len(fake.submitted) == 1
            assert (await db.get_slot(sid))['attempt'] == 1
            assert await db.workspace_charged(workspace['id']) == 0
