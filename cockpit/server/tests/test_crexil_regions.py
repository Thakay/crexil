"""Saved masks and shared region requests retain their reviewed source and pixels.

Real HTTP routes and SQLite run against temporary files and fake executors.
External transports and runtime uploads are forbidden throughout these tests.
"""

import asyncio
import base64
import hashlib
import io
import sqlite3
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops

from app import config, wiring
from app.comfy import ComfyClient
from app.providers import ProviderCatalog
from fakes import FakeExecutor, Step
from test_api_v4 import store
from test_api_v4_regions import booted_regions, region_fakes
from test_crexil import command, project
from test_crexil_execution import attest, reviewed, settled

pytestmark = pytest.mark.anyio
SIZE = (64, 48)


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    async def no_catalogue(_self):
        return None

    async def forbidden_http(_transport, request):
        raise AssertionError(f'Region API test attempted external HTTP: {request.url}')

    async def forbidden_upload(*_args, **_kwargs):
        raise AssertionError('Shared region preparation uploaded to a runtime before dispatch')

    monkeypatch.setattr(config, 'CREXIL_PREVIEW', False)
    monkeypatch.setattr(ProviderCatalog, '_refresh', no_catalogue)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_http)
    monkeypatch.setattr(ComfyClient, 'upload_image', forbidden_upload)


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def mask_bytes(box, size=SIZE):
    mask = Image.new('L', size)
    mask.paste(255, box)
    return png(mask)


def preview_pixels(url):
    return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))).convert('L')


async def source(client, wid, *, size=SIZE, blob=None):
    blob = blob or png(Image.new('RGBA', size, (50, 100, 150, 180)))
    response = await client.post(f'/api/crexil/projects/{wid}/material', json=command(
        filename='source.png', data_base64=base64.b64encode(blob).decode()))
    assert response.status_code == 201, response.text
    return response.json()['node']


async def source_info(client, wid, node):
    response = await client.get(f"/api/crexil/projects/{wid}/mask-source/{node['id']}")
    assert response.status_code == 200, response.text
    return response.json()


async def mask_command(client, wid, node, blob, **fields):
    info = await source_info(client, wid, node)
    return command(source_node_id=node['id'], source_sha256=info['sha256'], name='Saved area',
                   data_base64=base64.b64encode(blob).decode() if blob is not None else None, **fields)


async def saved_mask(client, wid, node, box, **fields):
    body = await mask_command(client, wid, node, mask_bytes(box), **fields)
    response = await client.post(f'/api/crexil/projects/{wid}/masks', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def instruction(mask, action, key, prompt=''):
    return {'key': key, 'name': key, 'action': action, 'mask_id': mask['id'], 'prompt': prompt}


async def region_draft(client, wid, node, regions, *, count=1, **fields):
    operation = next(op for op in (await client.get('/api/crexil/operations')).json()
                     if op['id'] == 'regionsdxl')
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(
        title='Region study', setup={'action': 'region', 'references': [{'node_id': node['id']}],
            'regions': regions, 'operations': [{'operation_id': operation['id'],
                'revision': operation['revision'], 'count': count}], **fields}))
    assert response.status_code == 201, response.text
    return response.json()


async def revise(client, direction, **changes):
    response = await client.patch(f"/api/crexil/directions/{direction['id']}", json=command(
        revision=direction['revision'], title=direction['title'], setup={**direction['setup'], **changes}))
    assert response.status_code == 200, response.text
    return response.json()


async def test_saved_masks_bind_display_dimensions_original_hash_and_alpha(tmp_path, monkeypatch):
    raw = Image.new('RGB', (12, 8), (50, 100, 150))
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    raw.save(buffer, format='JPEG', exif=exif)
    original = buffer.getvalue()
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        wid = (await project(client))['id']
        node = await source(client, wid, blob=original)
        info = await source_info(client, wid, node)
        assert (info['width'], info['height']) == (8, 12)
        assert info['sha256'] == hashlib.sha256(original).hexdigest()
        assert 'EXIF' in info['coordinates']
        mask = Image.new('RGBA', (8, 12), (255, 255, 255, 0))
        mask.putpixel((0, 0), (255, 255, 255, 255))
        mask.putpixel((1, 0), (255, 255, 255, 127))
        mask.putpixel((2, 0), (255, 255, 255, 128))
        supplied = png(mask)
        body = await mask_command(client, wid, node, supplied)
        response = await client.post(f'/api/crexil/projects/{wid}/masks', json=body)
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved['source_sha256'] == info['sha256']
        assert (saved['width'], saved['height']) == (8, 12)
        assert saved['original_sha256'] == hashlib.sha256(supplied).hexdigest()
        received = await client.get(f"/api/crexil/masks/{saved['id']}/image")
        assert received.status_code == 200
        pixels = Image.open(io.BytesIO(received.content))
        assert [pixels.getpixel((x, 0)) for x in range(4)] == [255, 0, 255, 0]
        assert hashlib.sha256(received.content).hexdigest() == saved['sha256']
        row = await store()._one('SELECT original_path FROM mask_assets WHERE id=?', (saved['id'],))
        assert Path(row['original_path']).read_bytes() == supplied
        assert Path(node['output_path']).read_bytes() == original
        assert not await store().list_turns(wid)
        assert all(not fake.starts for fake in fakes.values())


async def test_mask_revision_replay_and_restart_keep_every_version(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        node = await source(client, wid)
        body = await mask_command(client, wid, node, mask_bytes((0, 0, 12, 20)))
        url = f'/api/crexil/projects/{wid}/masks'
        first, replay = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
        assert first.status_code == replay.status_code == 201, (first.text, replay.text)
        assert first.json() == replay.json()
        original = first.json()
        original_bytes = (await client.get(f"/api/crexil/masks/{original['id']}/image")).content
        revised_body = await mask_command(client, wid, node, mask_bytes((20, 5, 40, 30)),
                                          previous_mask_id=original['id'])
        updated = await client.post(url, json=revised_body)
        assert updated.status_code == 201, updated.text
        revised = updated.json()
        assert revised['id'] != original['id'] and revised['sha256'] != original['sha256']
        assert revised['previous_mask_id'] == original['id']
        assert revised['transform']['kind'] == 'revised'
        conflict = await client.post(url, json={**body, 'name': 'Different delivery'})
        assert conflict.status_code == 409
        assert (await client.get(f"/api/crexil/masks/{original['id']}/image")).content == original_bytes
        events = await store().events_since(0)
        assert sum(event['type'] == 'mask.saved' for event in events) == 2
    async with booted_regions(tmp_path, monkeypatch) as (client, _):
        listed = (await client.get(url)).json()
        assert {mask['id']: mask for mask in listed} == {mask['id']: mask for mask in [original, revised]}
        assert (await client.post(url, json=body)).json() == original
        assert (await client.post(url, json=revised_body)).json() == revised
        assert (await client.get(f"/api/crexil/masks/{original['id']}/image")).content == original_bytes
        assert len((await client.get(url)).json()) == 2


async def test_mask_refuses_wrong_hash_size_and_implicit_rebinding(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        first = await source(client, wid)
        other = await source(client, wid)
        body = await mask_command(client, wid, first, mask_bytes((0, 0, 12, 20)))
        url = f'/api/crexil/projects/{wid}/masks'
        wrong_hash = await client.post(url, json={**body, 'source_sha256': '0' * 64})
        assert wrong_hash.status_code == 409 and 'source changed' in wrong_hash.text.lower()
        wrong_size = await client.post(url, json={**body,
            'data_base64': base64.b64encode(mask_bytes((0, 0, 5, 5), (32, 24))).decode()})
        assert wrong_size.status_code == 422 and 'dimensions must match' in wrong_size.text
        assert (await client.get(url)).json() == []
        saved = (await client.post(url, json=body)).json()
        implicit = await mask_command(client, wid, other, mask_bytes((0, 0, 12, 20)),
                                      previous_mask_id=saved['id'])
        refused = await client.post(url, json=implicit)
        assert refused.status_code == 422 and 'explicit adjustment' in refused.text
        assert len((await client.get(url)).json()) == 1


async def test_explicit_crop_and_resize_bind_new_source_without_mutating_old_mask(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        first = await source(client, wid)
        smaller = await source(client, wid, size=(16, 12))
        original = await saved_mask(client, wid, first, (16, 0, 32, 24))
        original_bytes = (await client.get(f"/api/crexil/masks/{original['id']}/image")).content
        adjustment = {'crop': {'x': 16, 'y': 0, 'width': 32, 'height': 24},
                      'resize_to_source': True, 'explanation': 'Use the matching crop at half resolution.'}
        body = await mask_command(client, wid, smaller, None, previous_mask_id=original['id'], adjustment=adjustment)
        response = await client.post(f'/api/crexil/projects/{wid}/masks', json=body)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result['source_node_id'] == smaller['id']
        assert result['source_sha256'] == (await source_info(client, wid, smaller))['sha256']
        assert result['previous_mask_id'] == original['id']
        assert result['transform']['adjustment'] == adjustment
        assert result['transform']['earlier_source']['node_id'] == first['id']
        pixels = Image.open(io.BytesIO((await client.get(f"/api/crexil/masks/{result['id']}/image")).content))
        assert pixels.size == (16, 12)
        assert pixels.tobytes() == Image.open(io.BytesIO(mask_bytes((0, 0, 8, 12), (16, 12)))).tobytes()
        assert (await client.get(f"/api/crexil/masks/{original['id']}/image")).content == original_bytes


async def test_region_review_refuses_foreign_source_and_unconfirmed_overlap(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node, other = await source(client, wid), await source(client, wid)
        first = await saved_mask(client, wid, node, (8, 8, 36, 32))
        second = await saved_mask(client, wid, node, (24, 16, 48, 40))
        requested = [instruction(first, 'modify', 'Change wrap', 'Blue cloth'),
                     instruction(second, 'replace', 'Replace grip', 'Carved wood')]
        mismatch = await region_draft(client, wid, other, requested)
        checked = await reviewed(client, mismatch)
        assert not checked['ready'] and any('different source bytes' in error for error in checked['errors'])
        direction = await region_draft(client, wid, node, requested)
        checked = await reviewed(client, direction)
        assert not checked['ready'] and any('Editable regions overlap' in error for error in checked['errors'])
        accepted = await revise(client, direction, region_overlap_policy='nd3-v1')
        checked = await reviewed(client, accepted)
        assert checked['ready'], checked['errors']
        assert checked['snapshot']['region']['overlap_confirmed'] == 'nd3-v1'
        assert not await store().list_turns(wid)
        assert all(not fake.starts for fake in fakes.values())


async def test_review_keeps_protected_pixels_and_unclaimed_area_outside_edit_passes(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        keep = await saved_mask(client, wid, node, (0, 0, 24, 48))
        edit = await saved_mask(client, wid, node, (16, 8, 40, 32))
        direction = await region_draft(client, wid, node, [instruction(keep, 'keep', 'Keep silhouette'),
            instruction(edit, 'modify', 'Change edge', 'Blue cloth')], prompt='A moonlit workshop')
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        details = checked['snapshot']['region']
        assert not details['regenerate_unclaimed'] and details['overlap_confirmed'] is None
        assert [p['kind'] for p in details['passes']] == ['modify']
        assert [p['prompt'] for p in details['passes']] == ['Blue cloth']
        pixels = preview_pixels(details['passes'][0]['effective_mask_preview'])
        assert pixels.getpixel((20, 16)) == 0  # KEEP wins over overlapping edit ink.
        assert pixels.getpixel((30, 16)) == 255
        assert pixels.getpixel((60, 40)) == 0  # Shared prompt does not select unclaimed pixels.
        assert checked['snapshot']['operations'][0]['region_passes'][0]['kind'] == 'modify'
        explicit = await revise(client, direction, region_regenerate_unclaimed=True)
        checked = await reviewed(client, explicit)
        assert checked['ready'], checked['errors']
        details = checked['snapshot']['region']
        assert details['regenerate_unclaimed']
        assert any(p['kind'] == 'regen_rest' for p in details['passes'])
        union = Image.new('L', SIZE)
        for p in details['passes']:
            union = ImageChops.lighter(union, preview_pixels(p['effective_mask_preview']))
        assert union.getpixel((20, 16)) == 0 and union.getpixel((60, 40)) == 255
        assert all(not fake.starts for fake in fakes.values())


async def test_revised_region_mask_invalidates_prior_review_without_dispatch(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        first = await saved_mask(client, wid, node, (8, 8, 24, 32))
        direction = await region_draft(client, wid, node, [instruction(first, 'modify', 'Wrap', 'Blue cloth')])
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        revised_mask = await saved_mask(client, wid, node, (20, 10, 40, 30), previous_mask_id=first['id'])
        saved = await revise(client, direction, regions=[instruction(revised_mask, 'modify', 'Wrap', 'Blue cloth')])
        rejected = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=direction['revision'], review_token=checked['token']))
        assert rejected.status_code == 409
        assert (await reviewed(client, saved))['token'] != checked['token']
        assert not await store().list_turns(wid)
        assert all(not fake.starts for fake in fakes.values())


@pytest.mark.parametrize('target', ['source', 'mask'])
async def test_changed_recorded_bytes_invalidate_region_review_before_dispatch(tmp_path, monkeypatch, target):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        mask = await saved_mask(client, wid, node, (8, 8, 24, 32))
        direction = await region_draft(client, wid, node, [instruction(mask, 'modify', 'Wrap', 'Blue cloth')])
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        if target == 'source':
            path = Path(node['output_path'])
        else:
            row = await store()._one('SELECT mask_path FROM mask_assets WHERE id=?', (mask['id'],))
            path = Path(row['mask_path'])
        # Valid PNG and unchanged dimensions still cannot replace recorded bytes.
        path.write_bytes(mask_bytes((32, 24, 48, 40)))
        refused = await reviewed(client, direction)
        assert not refused['ready']
        assert any('recorded hash' in error for error in refused['errors'])
        response = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=direction['revision'], review_token=checked['token']))
        assert response.status_code == 409
        assert not await store().list_turns(wid)
        assert all(not fake.starts for fake in fakes.values())


async def test_shared_region_submit_is_durable_once_before_fake_dispatch(tmp_path, monkeypatch):
    requests = []
    delivery = {}

    class CommittedRegion(FakeExecutor):
        async def start(self, req):
            # A separate connection cannot see the writer's uncommitted rows.
            with sqlite3.connect(config.DB_PATH) as reader:
                assert reader.execute('SELECT COUNT(*) FROM command_receipts WHERE request_id=?',
                                      (delivery['request_id'],)).fetchone()[0] == 1
                assert reader.execute('SELECT COUNT(*) FROM slots WHERE id=?', (req.slot_id,)).fetchone()[0] == 1
                assert reader.execute('SELECT COUNT(*) FROM regions').fetchone()[0] == 2
            requests.append(req)
            return await super().start(req)

    fakes = region_fakes()
    fakes['region'] = CommittedRegion('region', 'local', default=Step(data=png(Image.new('RGBA', SIZE, 'blue'))))
    async with booted_regions(tmp_path, monkeypatch, fakes) as (client, _):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        keep = await saved_mask(client, wid, node, (0, 0, 20, 48))
        edit = await saved_mask(client, wid, node, (12, 8, 40, 32))
        direction = await region_draft(client, wid, node, [instruction(keep, 'keep', 'Keep'),
            instruction(edit, 'modify', 'Wrap', 'Blue cloth')], count=2)
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        assert not requests and not await store().list_turns(wid)
        delivery.update(command(revision=direction['revision'], review_token=checked['token']))
        url = f"/api/crexil/directions/{direction['id']}/submit"
        first, replay = await asyncio.gather(client.post(url, json=delivery), client.post(url, json=delivery))
        assert first.status_code == replay.status_code == 201, (first.text, replay.text)
        assert first.json() == replay.json()
        turns = await settled(wid)
        assert len(turns) == 1 and len(turns[0]['slots']) == 2
        assert all(slot['status'] == 'landed' for slot in turns[0]['slots'])
        assert len(requests) == len(fakes['region'].starts) == 2
        assert fakes['comfy'].starts == fakes['openrouter'].starts == []
        frozen = turns[0]['dials']['crexil']
        assert frozen['region'] == checked['snapshot']['region']
        assert frozen['review_token'] == checked['token']
        for req in requests:
            plan = req.submitted_graph['region_plan']
            refs = [plan['source'], plan['keep'], *(p['mask'] for p in plan['passes'])]
            assert all(hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() == ref['sha256'] for ref in refs)
            assert plan['source']['original_sha256'] == checked['snapshot']['sources'][0]['sha256']
            assert len(plan['region_ids']) == 2 and plan['passes'][0]['mask']['upload_at_dispatch']
            assert req.parents == (node['id'],)
        assert (await client.post(url, json=delivery)).json() == first.json()
        assert len(requests) == 2
        events = await store().events_since(0)
        assert sum(event['type'] == 'direction.submitted' for event in events) == 1


async def test_failed_acceptance_never_dispatches_or_leaves_committed_regions(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        mask = await saved_mask(client, wid, node, (8, 8, 24, 32))
        direction = await region_draft(client, wid, node, [instruction(mask, 'modify', 'Wrap', 'Blue cloth')])
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        append = store().append_event

        async def refused_event(kind, **kwargs):
            if kind == 'direction.submitted':
                raise ValueError('Acceptance could not be saved')
            return await append(kind, **kwargs)

        monkeypatch.setattr(store(), 'append_event', refused_event)
        response = await client.post(f"/api/crexil/directions/{direction['id']}/submit", json=command(
            revision=direction['revision'], review_token=checked['token']))
        assert response.status_code == 422 and 'could not be saved' in response.text
        assert not await store().list_turns(wid)
        assert not await store()._all('SELECT * FROM regions')
        assert not list((tmp_path / 'staging' / 'region-preparations').rglob('*.png'))
        assert (await reviewed(client, direction))['ready']
        assert all(not fake.starts for fake in fakes.values())


async def test_occluded_modify_cannot_silently_ignore_denoise_override(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        mask = await saved_mask(client, wid, node, (8, 8, 40, 32))
        direction = await region_draft(client, wid, node, [
            instruction(mask, 'modify', 'Hidden modification', 'Blue cloth'),
            instruction(mask, 'replace', 'Replacement', 'Red cloth')],
            region_overlap_policy='nd3-v1', controls={'denoise': 0.25})
        checked = await reviewed(client, direction)
        assert not checked['ready']
        assert any('denoise does not apply to these effective region passes' in error for error in checked['errors'])
        direction = await revise(client, direction, controls={})
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        operation = checked['snapshot']['operations'][0]
        assert 'denoise' not in operation['applied_controls']
        assert [p['kind'] for p in operation['region_passes']] == ['replace']
        assert 'denoise' not in operation['region_passes'][0]['applied_controls']
        assert all(not fake.starts for fake in fakes.values())


@pytest.mark.parametrize('control', ['denoise', 'seed', 'prompt', 'instruction'])
async def test_region_controls_require_actual_fields_and_prompt_location(tmp_path, monkeypatch, control):
    import json
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        path = config.WORKFLOWS_DIR / 'test-inpaint@v1.json'
        graph = json.loads(path.read_text())
        if control == 'instruction':
            del graph['1']['inputs']['text']
        elif control != 'prompt':
            del graph['2']['inputs'][control]
        path.write_text(json.dumps(graph))
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        mask = await saved_mask(client, wid, node, (8, 8, 40, 32))
        values = {'denoise': 0.25, 'seed': {'mode': 'fixed', 'value': 42}, 'prompt': 'Blue cloth'}
        direction = await region_draft(client, wid, node,
            [instruction(mask, 'modify', 'Wrap', 'Blue cloth')],
            prompt='Blue cloth', controls={} if control == 'instruction' else {control: values[control]})
        checked = await reviewed(client, direction)
        assert not checked['ready']
        reason = ('mapped instruction input' if control == 'instruction' else
                  'prompt override' if control == 'prompt' else f'{control} does not apply')
        assert any(reason in error
                   for error in checked['errors'])
        assert all(not fake.starts for fake in fakes.values())


async def test_region_denoise_uses_declared_existing_target(tmp_path, monkeypatch):
    import json
    async with booted_regions(tmp_path, monkeypatch) as (client, _):
        path = config.WORKFLOWS_DIR / 'test-inpaint@v1.json'
        graph = json.loads(path.read_text())
        graph['2']['_meta']['title'] = 'custom_sampler'
        path.write_text(json.dumps(graph))
        preset = wiring.current.catalog['regionsdxl']
        next(s for s in preset['inputs'] if s['key'] == 'denoise')['target'] = [
            {'node_title': 'custom_sampler', 'field': 'denoise'}]
        attest()
        wid = (await project(client))['id']
        node = await source(client, wid)
        mask = await saved_mask(client, wid, node, (8, 8, 40, 32))
        direction = await region_draft(client, wid, node,
            [instruction(mask, 'modify', 'Wrap', 'Blue cloth')], controls={'denoise': 0.25})
        checked = await reviewed(client, direction)
        assert checked['ready'], checked['errors']
        applied = checked['snapshot']['operations'][0]['region_passes'][0]['applied_controls']
        assert applied['denoise'] == 0.25
