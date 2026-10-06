"""Setup reads and guarded credential changes never dispatch creative work."""

import copy
import json

import httpx
import pytest

from app import api, config, operation_registry, wiring
from test_api_v4 import booted, store

pytestmark = pytest.mark.anyio


async def test_prepared_requirements_name_missing_assets_without_writing_or_running(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        graph_path = config.WORKFLOWS_DIR / 'test-txt2img@v1.json'
        graph = json.loads(graph_path.read_text())
        graph['model'] = {'class_type': 'ModelLoader', 'inputs': {'model_name': 'missing-model.safetensors'}}
        graph_path.write_text(json.dumps(graph))
        nodes = {
            'CLIPTextEncode': {'input': {'required': {'text': ['STRING']}}},
            'KSampler': {'input': {'required': {'seed': ['INT'], 'denoise': ['FLOAT']}}},
            'SaveImage': {'input': {'required': {'filename_prefix': ['STRING']}}, 'output_node': True},
            'LoadImage': {'input': {'required': {'image': [['present.png'], {'image_upload': True}]}}, 'output': ['IMAGE']},
            'ModelLoader': {'input': {'required': {'model_name': [['installed-model.safetensors']]}}},
        }
        async def discover(definition):
            assert {node['class_type'] for node in definition.graph.values()} == set(nodes)
            return {'state': 'inspected', 'nodes': copy.deepcopy(nodes), 'findings': [
                {'kind': 'node', 'name': name, 'state': 'advertised'} for name in nodes], 'gaps': [],
                'limitations': ['Advertised choices do not prove successful execution.']}
        monkeypatch.setattr(operation_registry, 'discover', discover)
        operation = next(item for item in (await client.get('/api/crexil/operations')).json() if item['id'] == 'testpreset')
        seq = await store().latest_seq()
        params = {'operation_id': operation['id'], 'revision': operation['revision']}
        response = await client.get('/api/crexil/operation-requirements', params=params)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report['state'] == 'inspected'
        assert any('missing-model.safetensors' in gap for gap in report['gaps'])
        assert not any('unset.png' in gap for gap in report['gaps'])
        assert await store().latest_seq() == seq
        assert not fakes['comfy'].starts and not fakes['openrouter'].starts
        graph['model']['inputs']['model_name'] = 'installed-model.safetensors'
        graph_path.write_text(json.dumps(graph))
        assert (await client.get('/api/crexil/operation-requirements', params=params)).status_code == 409


async def test_changed_workflow_during_discovery_is_not_reported_as_current(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        operation = next(item for item in (await client.get('/api/crexil/operations')).json() if item['id'] == 'testpreset')
        async def discover(definition):
            path = config.WORKFLOWS_DIR / 'test-txt2img@v1.json'
            graph = json.loads(path.read_text())
            graph['1']['inputs']['text'] = 'Changed'
            path.write_text(json.dumps(graph))
            return {'state': 'unknown', 'nodes': {}, 'findings': [], 'gaps': ['Runtime is unavailable.']}
        monkeypatch.setattr(operation_registry, 'discover', discover)
        response = await client.get('/api/crexil/operation-requirements', params={
            'operation_id': operation['id'], 'revision': operation['revision']})
        assert response.status_code == 409
        assert 'changed' in response.json()['detail']


async def test_account_setup_checks_only_access_and_retains_work_after_a_bad_key(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        monkeypatch.setattr(config, 'OPENROUTER_API_KEY', '')
        monkeypatch.setitem(api._or_probe, 'result', None)
        orc = wiring.current.openrouter
        orc.set_key('', source=None)
        await orc._client.aclose()
        requests = []
        key = 'test-account-key-only-for-this-hermetic-check'
        def transport(request):
            requests.append(request)
            assert request.method == 'GET' and request.url.path == '/key'
            return httpx.Response(200 if request.headers.get('authorization') == f'Bearer {key}' else 401, json={})
        orc._client = httpx.AsyncClient(base_url='http://provider.invalid', transport=httpx.MockTransport(transport))
        before = await store().latest_seq()
        overview = (await client.get('/api/crexil/setup')).json()
        assert not overview['openrouter']['configured']
        refused = await client.post('/api/setup/openrouter', json={'api_key': key})
        assert refused.status_code == 403 and not requests
        saved = await client.post('/api/setup/openrouter', headers={'X-Cockpit-Setup': '1'}, json={'api_key': key})
        assert saved.status_code == 200
        assert key not in saved.text
        assert saved.json()['source'] == 'stored'
        assert (tmp_path / 'secrets.json').stat().st_mode & 0o777 == 0o600
        bad = await client.post('/api/setup/openrouter', headers={'X-Cockpit-Setup': '1'},
                                json={'api_key': 'rejected-key-that-must-not-overwrite-the-old-one'})
        assert bad.status_code == 400
        assert orc.api_key == key
        assert json.loads((tmp_path / 'secrets.json').read_text())['openrouter_api_key'] == key
        overview = await client.get('/api/crexil/setup')
        assert overview.json()['openrouter']['configured'] and key not in overview.text
        removed = await client.delete('/api/setup/openrouter', headers={'X-Cockpit-Setup': '1'})
        assert removed.status_code == 200 and not removed.json()['configured']
        assert not orc.key_present and orc.requests_total == 0
        assert await store().latest_seq() == before
        assert not fakes['comfy'].starts and not fakes['openrouter'].starts
