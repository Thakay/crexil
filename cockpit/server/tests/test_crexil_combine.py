"""Ordered source routing uses the shipped graph interfaces and shared actions."""
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app import config, presets, wiring
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, project
from test_crexil_execution import reviewed, settled

pytestmark = pytest.mark.anyio
REPO = Path(__file__).resolve().parents[3]


async def source(client, wid, ordinal):
    out = io.BytesIO()
    Image.new('RGB', (8, 8), (ordinal * 40, 70, 90)).save(out, format='PNG')
    response = await client.post(f'/api/crexil/projects/{wid}/material', json=command(
        filename=f'source-{ordinal}.png', data_base64=base64.b64encode(out.getvalue()).decode()))
    assert response.status_code == 201, response.text
    return response.json()['node']


@pytest.mark.parametrize('count', [2, 3])
async def test_shipped_qwen_combine_preserves_every_ordered_source(tmp_path, monkeypatch, count):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        preset = json.loads((REPO / 'presets/raw-qwen-edit.json').read_text())
        (config.PRESETS_DIR / 'raw-qwen-edit.json').write_text(json.dumps(preset))
        for ref in set(preset['workflows'].values()):
            (config.WORKFLOWS_DIR / f'{ref}.json').write_bytes((REPO / f'workflows/{ref}.json').read_bytes())
        wiring.current.catalog.update(presets.load_presets())
        fakes['comfy'].default = Step(data=image_bytes())
        requests = []
        original_start = fakes['comfy'].start

        async def recorded_start(req):
            requests.append(req)
            return await original_start(req)

        monkeypatch.setattr(fakes['comfy'], 'start', recorded_start)
        p = await project(client)
        sources = [await source(client, p['id'], i + 1) for i in range(count)]
        sources.reverse()  # the requested order must survive filename and node ordering
        op = next(op for op in (await client.get('/api/crexil/operations')).json() if op['id'] == 'raw-qwen-edit')
        assert 'combine' in op['actions'] and op['source_limit'] == 3
        created = await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(
            title='Ordered combination', setup={'action': 'combine', 'prompt': 'Combine these studies.',
                'references': [{'node_id': node['id'], 'contribution': f'Keep contribution {i + 1}'} for i, node in enumerate(sources)],
                'operations': [{'operation_id': op['id'], 'revision': op['revision'], 'count': 1}]}))
        direction = created.json()
        review = await reviewed(client, direction)
        assert review['ready'], review['errors']
        submitted = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=direction['revision'], review_token=review['token']))
        assert submitted.status_code == 201, submitted.text
        turn = (await settled(p['id']))[0]
        slot = turn['slots'][0]
        graph = slot['submitted_graph']
        uploads = slot['dials']['crexil']['source_uploads']
        assert [item['node_id'] for item in uploads] == [node['id'] for node in sources]
        assert len({item['name'] for item in uploads}) == count
        for i, upload in enumerate(uploads, 1):
            for receiver in ('68', '69'):
                loader = graph[receiver]['inputs'][f'image{i}'][0]
                assert graph[loader]['inputs']['image'] == upload['name']
            assert f'Source {i}: Keep contribution {i}' in graph['68']['inputs']['prompt']
        assert f'image{count + 1}' not in graph['68']['inputs']
        node = next(n for n in await store().list_nodes(p['id']) if n['slot_id'])
        assert node['parents'] == [source['id'] for source in sources]
        assert len(fakes['comfy'].starts) == 1
        dispatched = requests[0]
        assert len(dispatched.sources) == count
        assert [item.sha256 for item in dispatched.sources] == [item['sha256'] for item in uploads]


@pytest.mark.parametrize('bad_port,message', [
    ({'node_title': 'source_image', 'field': 'image'}, 'same graph input'),
    ({'node_title': 'missing_source', 'field': 'image'}, 'unambiguous graph input'),
    ({'node_title': 'source_image_2', 'field': 'missing'}, 'existing image filename'),
])
async def test_malformed_mapping_is_blocked_during_review(tmp_path, monkeypatch, bad_port, message):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        preset = json.loads((REPO / 'presets/raw-qwen-edit.json').read_text())
        preset['source_ports']['combine2'][1] = bad_port
        (config.PRESETS_DIR / 'raw-qwen-edit.json').write_text(json.dumps(preset))
        for ref in set(preset['workflows'].values()):
            (config.WORKFLOWS_DIR / f'{ref}.json').write_bytes((REPO / f'workflows/{ref}.json').read_bytes())
        wiring.current.catalog.update(presets.load_presets())
        p = await project(client)
        sources = [await source(client, p['id'], i) for i in (1, 2)]
        op = next(op for op in (await client.get('/api/crexil/operations')).json() if op['id'] == 'raw-qwen-edit')
        direction = (await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(
            setup={'action': 'combine', 'prompt': 'Combine',
                'references': [{'node_id': node['id']} for node in sources],
                'operations': [{'operation_id': op['id'], 'revision': op['revision']}]}))).json()
        review = await reviewed(client, direction)
        assert not review['ready'] and any(message in error for error in review['errors'])
        assert fakes['comfy'].starts == []
