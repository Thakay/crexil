"""Mesh companion recovery preserves material without repeating generation."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app import wiring
from app.executors import EngineFailure
from app.mesh import FIELDS_SUFFIX, fields_sidecar
from fakes import Step
from test_api_v4 import make_ws, settle_turn, store, wait_running
from test_api_v4_mesh import booted_mesh, make_source, mesh_fakes, run_mesh
from test_crexil import command
from test_execution_outputs import members
from test_mesh import GLB, NPZ, Runner, done, executor, extract_request, request, texture_request

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def forbidden_http(_transport, request):
        raise AssertionError(f'Mesh regression attempted real HTTP: {request.url}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_http)


def fields_done():
    return done(result={**done()['result'], 'fields': '/host/runs/job-1/fields.npz'})


async def test_missing_fields_have_a_receipt_and_free_retry_repairs_same_companion(tmp_path, monkeypatch):
    runner = Runner([fields_done()])
    fakes = mesh_fakes()
    fakes['mesh'] = executor(runner)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        wid = (await make_ws(client))['id']
        source = await make_source(client, wid)
        response = await run_mesh(client, wid, source['id'])
        assert response.status_code == 201, response.text
        turn = await settle_turn(response.json()['turn']['id'], until={'failed'})
        slot = turn['slots'][0]
        assert slot['kind'] == 'collection' and slot['attempt'] == 1
        group, outputs = await members(store(), slot['id'])
        primary, companion = outputs
        assert group['state'] == 'incomplete'
        assert primary['node_id'] and Path(primary['output_path']).read_bytes() == GLB
        assert companion['identity'] == 'companion:' + FIELDS_SUFFIX
        assert companion['sha256'] is None and companion['error']
        assert json.loads(companion['descriptor_json'])['runner_job_id'] == 'job-1'
        assert json.loads(group['metadata_json'])['companion_owner_id'] == primary['node_id']
        node = next(n for n in (await client.get(f'/api/workspaces/{wid}/nodes')).json()
                    if n['id'] == primary['node_id'])
        assert node['mesh']['fields_kept'] is False

        # The collected GLB stays useful even after the runner loses its copy.
        runner.glb = None
        runner.fields = NPZ
        body = command(take=1)
        endpoint = f"/api/crexil/attempts/{slot['id']}/collect"
        response = await client.post(endpoint, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'complete'
        assert {out['id'] for out in response.json()['outputs']} == {out['id'] for out in outputs}
        assert all(out['available'] and out['error'] is None for out in response.json()['outputs'])
        _, repaired = await members(store(), slot['id'])
        assert repaired[0]['node_id'] == primary['node_id']
        assert Path(repaired[1]['output_path']) == fields_sidecar(primary['output_path'])
        assert Path(repaired[1]['output_path']).read_bytes() == NPZ
        assert (await store().get_slot(slot['id']))['status'] == 'landed'
        node = next(n for n in (await client.get(f'/api/workspaces/{wid}/nodes')).json()
                    if n['id'] == primary['node_id'])
        assert node['mesh']['fields_kept'] is True
        replay = await client.post(endpoint, json=body)
        assert replay.status_code == 200 and replay.json()['state'] == 'complete'
        assert len(runner.posted) == 1 and runner.deleted == []
        assert (await store().get_slot(slot['id']))['attempt'] == 1


async def test_purged_partial_mesh_cannot_gain_a_companion_on_retry(tmp_path, monkeypatch):
    runner = Runner([fields_done()])
    fakes = mesh_fakes()
    fakes['mesh'] = executor(runner)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        wid = (await make_ws(client))['id']
        source = await make_source(client, wid)
        response = await run_mesh(client, wid, source['id'])
        slot = (await settle_turn(response.json()['turn']['id'], until={'failed'}))['slots'][0]
        _, outputs = await members(store(), slot['id'])
        primary = outputs[0]
        assert (await client.post(f"/api/nodes/{primary['node_id']}/set-aside")).status_code == 200
        response = await client.post(f'/api/workspaces/{wid}/purge', json={'ids': [primary['node_id']]})
        assert response.status_code == 200, response.text
        runner.fields = NPZ
        response = await client.post(f"/api/crexil/attempts/{slot['id']}/collect", json=command(take=1))
        assert response.status_code == 422 and 'purged' in response.text
        assert not Path(primary['output_path']).exists()
        assert not fields_sidecar(primary['output_path']).exists()
        assert len(runner.posted) == 1 and runner.deleted == []


async def test_reconcile_completed_job_includes_fields_and_missing_receipts(tmp_path):
    runner = Runner([fields_done()], fields=NPZ)
    ex = executor(runner)
    outputs = []

    async def retain(output):
        outputs.append(output)

    req = replace(request(tmp_path), on_output=retain)
    result = await ex.reconcile({'job_ref': 'job-1'}, request=req)
    assert result.kind == 'landed'
    assert result.result.sidecars == {FIELDS_SUFFIX: NPZ}
    assert any(output.payload == '3d' and output.data == GLB for output in outputs)
    runner.fields = None
    outputs.clear()
    result = await ex.reconcile({'job_ref': 'job-1'}, request=req)
    assert result.kind == 'landed' and result.result.data == GLB
    assert outputs[-1].role == 'companion' and outputs[-1].error
    assert outputs[-1].identity == 'companion:' + FIELDS_SUFFIX
    assert runner.posted == [] and runner.deleted == []
    await ex.close()


@pytest.mark.parametrize('fields', [NPZ, None])
async def test_restart_collects_completed_mesh_and_advertised_companion(tmp_path, monkeypatch, fields):
    fakes = mesh_fakes()
    fakes['mesh'].default = Step(outcome='hang')
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        wid = (await make_ws(client))['id']
        source = await make_source(client, wid)
        response = await run_mesh(client, wid, source['id'])
        assert response.status_code == 201, response.text
        turn_id = response.json()['turn']['id']
        sid = response.json()['turn']['slots'][0]['id']
        await wait_running(sid)
        await wiring.current.scheduler.stop()
        await store().set_job_ref(sid, 'job-1')

    runner = Runner([fields_done()], fields=fields)
    fakes = mesh_fakes()
    fakes['mesh'] = executor(runner)
    async with booted_mesh(tmp_path, monkeypatch, fakes):
        slot = (await settle_turn(turn_id, until={'landed' if fields else 'failed'}))['slots'][0]
        assert slot['attempt'] == 1
        group, outputs = await members(store(), sid)
        assert len(outputs) == 2 and Path(outputs[0]['output_path']).read_bytes() == GLB
        assert group['state'] == ('complete' if fields else 'incomplete')
        if fields:
            assert fields_sidecar(outputs[0]['output_path']).read_bytes() == fields
        else:
            assert outputs[1]['error'] and outputs[1]['sha256'] is None
        assert runner.posted == [] and runner.deleted == []


async def test_collection_does_not_cancel_or_resubmit_an_unfinished_job():
    runner = Runner([{'id': 'job-1', 'state': 'running'}])
    ex = executor(runner)
    result = await ex.collect_existing({'job_ref': 'job-1'})
    assert result.kind == 'unverifiable'
    assert runner.posted == [] and runner.deleted == []
    await ex.close()


@pytest.mark.parametrize('factory', [request, extract_request, texture_request])
@pytest.mark.parametrize('change', ['changed', 'missing'])
async def test_frozen_mesh_sources_are_checked_before_any_http(tmp_path, factory, change):
    req = factory(tmp_path)
    job = req.submitted_graph['mesh_job']
    source = Path(job.get('ref_path') or (fields_sidecar(job['source_path'])
                  if job.get('kind') == 'decimate' else job['source_path']))
    job['source_hashes'] = [{'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]
    job['mesh_job_ref'] = 'cached-parent'
    if change == 'missing':
        source.unlink()
    else:
        source.write_bytes(b'different bytes')
    ex = executor(Runner([done()]))
    calls = []

    def forbidden(req):
        calls.append(str(req.url))
        raise AssertionError('Frozen source validation must precede every HTTP call')

    await ex.close()
    ex._client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden), base_url='http://runner.test')
    with pytest.raises(EngineFailure, match='frozen mesh source'):
        await ex.start(req)
    assert calls == []
    await ex.close()


async def test_frozen_valid_sources_keep_upload_and_reference_bytes(tmp_path):
    req = texture_request(tmp_path)
    job = req.submitted_graph['mesh_job']
    paths = [Path(job['source_path']), Path(job['ref_path'])]
    blobs = [path.read_bytes() for path in paths]
    job['source_hashes'] = [{'path': str(path), 'sha256': hashlib.sha256(blob).hexdigest()}
                            for path, blob in zip(paths, blobs, strict=True)]
    runner = Runner([done()])
    ex = executor(runner)
    await ex.start(req)
    assert runner.uploaded == [blobs[0]]
    import base64
    assert base64.b64decode(runner.posted[0]['image_b64']) == blobs[1]
    await ex.close()
