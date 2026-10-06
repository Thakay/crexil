"""Derived preparation keeps coordinates, source bytes and idempotent receipts."""

import asyncio
import base64
import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from app.crexil_store import CreativeStore
from app.db import Database
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project

pytestmark = pytest.mark.anyio


async def prepared_draft(client, wid, ids, recipe):
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(
        title='Prepared study', setup={'action': 'prepare', 'references': [{'node_id': nid} for nid in ids],
            'image_preparation': recipe}))
    assert response.status_code == 201, response.text
    return response.json()


async def test_crop_exact_pixels_preserved_original_and_restart_receipt(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        direction = await prepared_draft(client, p['id'], [source['id']], {'mode': 'crop', 'items': [
            {'node_id': source['id'], 'crop': {'x': 2, 'y': 1, 'width': 3, 'height': 4}}]})
        did = direction['id']
        review = await client.get(f'/api/crexil/directions/{did}/image-review')
        assert review.status_code == 200, review.text
        checked = review.json()
        assert checked['snapshot']['size'] == [3, 4]
        assert checked['snapshot']['sources'][0]['sha256'] == hashlib.sha256(image_bytes()).hexdigest()
        assert len(await store().list_nodes(p['id'])) == 1
        body = command(revision=0, review_token=checked['token'])
        url = f'/api/crexil/directions/{did}/prepare-image'
        first, replay = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
        assert first.status_code == replay.status_code == 201, first.text
        assert first.json() == replay.json()
        result = first.json()['node']
        assert Image.open(result['output_path']).tobytes() == Image.open(io.BytesIO(image_bytes())).crop((2, 1, 5, 5)).tobytes()
        assert Path(source['output_path']).read_bytes() == image_bytes()
        assert result['parents'] == [source['id']]
        assert first.json()['direction']['selection'] == [result['id']]
        assert await store().list_turns(p['id']) == []
        assert fakes['comfy'].starts == []
        from app.manifests import build_v4_manifest
        manifest, files = await build_v4_manifest(store(), asset_id='prepared', workspace=p,
            node=result, turn=None, slot=None, parents=[source])
        assert manifest['replayable'].startswith('recipe:')
        assert manifest['provider'] is None
        assert manifest['material']['origin']['transform']['renderer']['pillow']
        assert files[0][0] == Path(source['output_path'])
    db = Database(tmp_path / 'v4.sqlite')
    await db.open()
    try:
        snapshot = await CreativeStore(db).snapshot(p['id'])
        assert len(snapshot['directions']) == 2
        node = next(n for n in snapshot['nodes'] if n['id'] == result['id'])
        assert node['material']['origin']['kind'] == 'prepared_image'
        assert node['material']['origin']['transform']['setup']['image_preparation']['items'][0]['crop']['x'] == 2
        response = await CreativeStore(db).delivered(body['request_id'], 'material.prepare:' + did, body)
        assert response['node']['id'] == result['id']
    finally:
        await db.close()


async def test_arrangement_order_alpha_and_explicit_geometry(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        ids = [(await imported(client, p['id']))['node']['id'] for _ in range(2)]
        recipe = {'mode': 'arrange', 'width': 10, 'height': 6, 'resample': 'nearest', 'items': [
            {'node_id': ids[0], 'placement': {'x': 0, 'y': 0, 'width': 8, 'height': 6}},
            {'node_id': ids[1], 'placement': {'x': 4, 'y': 0, 'width': 4, 'height': 6}}]}
        d = await prepared_draft(client, p['id'], ids, recipe)
        reviewed = (await client.get(f"/api/crexil/directions/{d['id']}/image-review")).json()
        made = await client.post(f"/api/crexil/directions/{d['id']}/prepare-image", json=command(revision=0, review_token=reviewed['token']))
        assert made.status_code == 201, made.text
        pixels = Image.open(made.json()['node']['output_path'])
        assert pixels.size == (10, 6)
        assert pixels.getpixel((1, 1)) == (55, 90, 130, 120)
        assert pixels.getpixel((5, 1)) == (55, 90, 130, 184)
        assert pixels.getpixel((9, 1)) == (0, 0, 0, 0)
        assert made.json()['node']['parents'] == ids
        recipe['items'][1]['placement']['width'] = 10
        bad = await prepared_draft(client, p['id'], ids, recipe)
        rejected = await client.get(f"/api/crexil/directions/{bad['id']}/image-review")
        assert rejected.status_code == 422
        assert 'fit inside' in rejected.text


async def test_resize_stale_review_and_changed_source_are_refused(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        recipe = {'mode': 'resize', 'width': 4, 'height': 3, 'resample': 'nearest', 'items': [{'node_id': source['id']}]}
        d = await prepared_draft(client, p['id'], [source['id']], recipe)
        checked = (await client.get(f"/api/crexil/directions/{d['id']}/image-review")).json()
        assert checked['snapshot']['size'] == [4, 3]
        changed = {**d['setup'], 'image_preparation': {**recipe, 'width': 6}}
        await client.patch(f"/api/crexil/directions/{d['id']}", json=command(revision=0, title=d['title'], setup=changed))
        stale = await client.post(f"/api/crexil/directions/{d['id']}/prepare-image", json=command(revision=0, review_token=checked['token']))
        assert stale.status_code == 409
        assert len(await store().list_nodes(p['id'])) == 1
        Path(source['output_path']).write_bytes(image_bytes() + b'changed')
        rejected = await client.get(f"/api/crexil/directions/{d['id']}/image-review")
        assert rejected.status_code == 422
        assert 'recorded hash' in rejected.text


async def test_crop_refuses_hidden_resize_and_ignored_generation_inputs(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        source = (await imported(client, p['id']))['node']
        recipe = {'mode': 'crop', 'items': [{'node_id': source['id'], 'crop': {'x': 2, 'y': 0, 'width': 8, 'height': 6}}]}
        d = await prepared_draft(client, p['id'], [source['id']], recipe)
        rejected = await client.get(f"/api/crexil/directions/{d['id']}/image-review")
        assert rejected.status_code == 422 and 'inside its source' in rejected.text
        await client.patch(f"/api/crexil/directions/{d['id']}", json=command(revision=0, title=d['title'], setup={**d['setup'], 'prompt': 'Make a new pattern'}))
        rejected = await client.get(f"/api/crexil/directions/{d['id']}/image-review")
        assert rejected.status_code == 422 and 'Clear generation' in rejected.text


async def test_oriented_source_crop_uses_display_coordinates_and_ignores_unused_canvas(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        pixels = Image.new('RGB', (8, 6))
        for y in range(6):
            for x in range(8):
                pixels.putpixel((x, y), (x * 25, y * 35, 80))
        exif = Image.Exif()
        exif[274] = 6
        buffer = io.BytesIO()
        pixels.save(buffer, format='JPEG', quality=100, exif=exif)
        original = buffer.getvalue()
        imported_image = await client.post(f"/api/crexil/projects/{p['id']}/material", json=command(
            filename='oriented.jpg', data_base64=base64.b64encode(original).decode()))
        source = imported_image.json()['node']
        recipe = {'mode': 'crop', 'width': 6000, 'height': 4000,
                  'items': [{'node_id': source['id'], 'crop': {'x': 1, 'y': 4, 'width': 4, 'height': 3}}]}
        d = await prepared_draft(client, p['id'], [source['id']], recipe)
        checked = await client.get(f"/api/crexil/directions/{d['id']}/image-review")
        assert checked.status_code == 200, checked.text
        made = await client.post(f"/api/crexil/directions/{d['id']}/prepare-image", json=command(revision=0, review_token=checked.json()['token']))
        assert made.status_code == 201, made.text
        expected = ImageOps.exif_transpose(Image.open(io.BytesIO(original))).convert('RGBA').crop((1, 4, 5, 7))
        assert Image.open(made.json()['node']['output_path']).tobytes() == expected.tobytes()
        assert Path(source['output_path']).read_bytes() == original
