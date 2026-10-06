"""Delivery preserves grouped files and historical execution identity."""
from pathlib import Path
from dataclasses import replace
import hashlib
import io
import json

import pytest
from PIL import Image

from app import config, execution_outputs, manifests, mask_assets, promote, regions
from app.executors import CollectedOutput, DispatchRequest, EngineFailure
from app.region_exec import RegionExecutor
from test_db import opened
from test_region_exec import FakeComfy, SIZE, box_size, make_envelope

pytestmark = pytest.mark.anyio


async def test_manifest_keeps_related_files_and_original_attempt_job(tmp_path):
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Delivery')
        parent_path = tmp_path / 'source.jpg'
        parent_path.write_bytes(b'original-jpeg-bytes')
        parent = await db.import_node(workspace['id'], output_path=str(parent_path))
        turn = await db.create_turn_with_slots(workspace['id'], 'coat', {}, [{'engine': 'local'}])
        sid = turn['slots'][0]['id']
        slot = await db.dispatch_slot(sid, job_ref='first-job', submitted_graph={})
        req = DispatchRequest(slot_id=sid, engine='local', submitted_graph={}, parents=(parent['id'],))
        one = await execution_outputs.collect(db, tmp_path, slot, req,
            CollectedOutput('body', 'body.png', b'body-bytes', role='final'))
        two = await execution_outputs.collect(db, tmp_path, slot, req,
            CollectedOutput('mask', 'mask.png', b'mask-bytes', role='mask'))
        await execution_outputs.collect(db, tmp_path, slot, req,
            CollectedOutput('notes', 'notes.json', b'{"notes":true}', payload=None))
        await execution_outputs.finish(db, slot, 'complete')
        await db.land_node(sid, payload='image', verb='run', output_path=one['output_path'])
        await db.reroll_slot(sid, 42)
        current = await db.dispatch_slot(sid, job_ref='later-job', submitted_graph={})
        node = await db.get_node(one['node_id'])
        manifest, files = await manifests.build_v4_manifest(db, asset_id='delivery', workspace=workspace,
            node=node, turn=turn, slot=current, parents=[parent])
        assert manifest['comfyui']['job_ref'] == 'first-job'
        assert manifest['execution']['take'] == 1
        assert manifest['execution']['state'] == 'complete'
        assert [o['role'] for o in manifest['execution']['outputs']] == ['final', 'mask', 'unclassified']
        assert manifest['input_images'][0]['file'] == 'delivery.inputs/source.jpg'
        assert manifest['input_images'][0]['available']
        assert len(files) == 3
        assert {path.read_bytes() for path, _ in files} == {b'original-jpeg-bytes', b'mask-bytes', b'{"notes":true}'}
        assert all('output_path' not in output for output in manifest['execution']['outputs'])
        Path(two['output_path']).unlink()
        missing, files = await manifests.build_v4_manifest(db, asset_id='delivery', workspace=workspace,
            node=node, turn=turn, slot=current, parents=[parent])
        absent = next(o for o in missing['execution']['outputs'] if o['id'] == two['id'])
        assert absent['file'] is None and absent['available'] is False
        assert len(files) == 2


async def test_retained_input_binding_is_once_only_and_replayable(tmp_path):
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Input receipts')
        turn = await db.create_turn_with_slots(workspace['id'], '', {}, [{'engine': 'local'}])
        slot = await db.dispatch_slot(turn['slots'][0]['id'])
        request = DispatchRequest(slot_id=slot['id'], engine='local')
        blob = b'input bytes'
        output = CollectedOutput('region-input:source', 'source.png', blob, payload=None,
            role='runtime input', descriptor={'sha256': hashlib.sha256(blob).hexdigest(), 'purpose': 'runtime source crop'})
        first = await execution_outputs.collect(db, tmp_path, slot, request, output)
        graph = {'1': {'inputs': {'image': 'uploaded.png'}}}
        bound = replace(output, submitted_graph=graph, descriptor={**output.descriptor, 'upload': 'uploaded.png'})
        saved = await execution_outputs.collect(db, tmp_path, slot, request, bound)
        replay = await execution_outputs.collect(db, tmp_path, slot, request, bound)
        assert first['id'] == saved['id'] == replay['id']
        assert saved == replay and saved['node_id'] is None
        assert json.loads(saved['descriptor_json'])['receipt']['submitted_graph'] == graph
        assert len(await db._all('SELECT * FROM execution_outputs')) == 1
        assert sum(event['type'] == 'execution.input_bound' for event in await db.events_since(0)) == 1
        with pytest.raises(ValueError, match='immutable upload and graph binding'):
            await execution_outputs.collect(db, tmp_path, slot, request,
                replace(bound, submitted_graph={'2': {'inputs': {'image': 'different.png'}}}))
        assert dict(await db._one('SELECT * FROM execution_outputs WHERE id=?', (first['id'],))) == saved


@pytest.mark.parametrize('fail_later', [False, True])
async def test_region_export_retains_exact_inputs_through_success_or_later_failure(tmp_path, monkeypatch, fail_later):
    monkeypatch.setattr(config, 'COMFY_POLL_S', .01)
    monkeypatch.setattr(config, 'HISTORY_CONFIRM_INTERVAL_S', .005)
    monkeypatch.setattr(config, 'STAGING_DIR', tmp_path / 'staging')
    monkeypatch.setattr(config, 'MODELS_ROOT', tmp_path / 'models')
    envelope, _, _ = make_envelope(tmp_path, modify_box=(200, 300, 800, 560))
    plan = envelope['region_plan']
    original = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.effect_noise((SIZE[1], SIZE[0]), 60).convert('RGB').save(original, 'JPEG', exif=exif)
    original_path = tmp_path / 'original.jpg'
    original_path.write_bytes(original.getvalue())
    normalized = regions.mask_png(mask_assets.source_pixels(original.getvalue()))
    Path(plan['source']['path']).write_bytes(normalized)
    plan['source'].update(sha256=hashlib.sha256(normalized).hexdigest(),
        original_sha256=hashlib.sha256(original.getvalue()).hexdigest(), coordinates=mask_assets.COORDINATES)
    for p in plan['passes']:
        p['mask']['upload_at_dispatch'] = True
    fake = FakeComfy([('land', box_size(envelope, 0)),
        ('error', 'RuntimeError', 'later failure') if fail_later else ('land', box_size(envelope, 1))])
    executor = RegionExecutor(fake)
    retained = {}
    async with opened(tmp_path) as db:
        workspace = await db.create_workspace('Regions')
        parent = await db.import_node(workspace['id'], output_path=str(original_path))
        turn = await db.create_turn_with_slots(workspace['id'], '', {}, [{'engine': 'raw-sdxl'}])
        slot = await db.dispatch_slot(turn['slots'][0]['id'], submitted_graph=envelope)
        request = DispatchRequest(slot_id=slot['id'], engine='raw-sdxl', submitted_graph=envelope,
                                  parents=(parent['id'],), verb='region')

        async def collect(output):
            member = await execution_outputs.collect(db, tmp_path / 'collected', slot, request, output)
            if output.data is not None:
                retained[output.identity] = (member, output.data)

        submit = fake.submit

        async def observed_submit(graph):
            for key in ('source', 'mask'):
                member, _ = retained[f'region-input:pass:{len(fake.submitted)}:{key}']
                receipt = json.loads(member['descriptor_json'])
                assert receipt['receipt']['submitted_graph'] == graph
                assert receipt['upload'] in fake.uploads
            return await submit(graph)

        fake.submit = observed_submit

        handle = await executor.start(replace(request, on_output=collect))
        if fail_later:
            with pytest.raises(EngineFailure, match='pass 2/2'):
                await executor.wait(handle)
        else:
            await executor.wait(handle)
        await execution_outputs.finish(db, slot, 'incomplete' if fail_later else 'complete')
        identity = 'pass:0/protected' if fail_later else 'pass:1/protected'
        node = await db.get_node(retained[identity][0]['node_id'])
        manifest, files = await manifests.build_v4_manifest(db, asset_id='region', workspace=workspace,
            node=node, turn=turn, slot=slot, parents=[parent])
        inputs = {item['identity']: item for item in manifest['runtime_inputs']}
        assert len(inputs) == 8  # normalized source, KEEP, two effective masks, two source/mask crops
        assert all(item['available'] and item['file'].startswith('region.inputs/') for item in inputs.values())
        assert all(retained[identity][0]['node_id'] is None for identity in inputs)
        assert inputs['region-input:source']['sha256'] == plan['source']['sha256']
        assert inputs['region-input:source']['description']['original_sha256'] == plan['source']['original_sha256']
        assert manifest['keep_composite']['mask_file'] == inputs['region-input:keep']['file']
        assert 'after each completed pass' in manifest['keep_composite']['note']
        assert manifest['execution']['state'] == ('incomplete' if fail_later else 'complete')
        assert len(manifest['pipeline']) == (1 if fail_later else 2)
        for index, (_, graph) in enumerate(fake.submitted):
            for title, key in [('source_image', 'source'), ('region_mask', 'mask')]:
                loader = next(n for n in graph.values() if n.get('_meta', {}).get('title') == title)
                uploaded = fake.uploads[loader['inputs']['image']]
                identity = f'region-input:pass:{index}:{key}'
                member, blob = retained[identity]
                assert blob == uploaded == Path(member['output_path']).read_bytes()
                assert inputs[identity]['sha256'] == hashlib.sha256(uploaded).hexdigest()
                assert inputs[identity]['description']['upload'] == loader['inputs']['image']
                receipt = json.loads(member['descriptor_json'])
                assert receipt['receipt']['submitted_graph'] == graph
        assert retained['region-input:pass:1:source'][1] == executor._crop_png(
            retained['pass:0/protected'][1], plan['passes'][1]['box'])
        root = tmp_path / 'delivery'
        root.mkdir()
        artefact, _, _ = promote.promote_candidate(Path(node['output_path']), manifest, 'project', 'region',
                                                  files, root=root, roots=[root])
        for identity, item in inputs.items():
            assert (artefact.parent / item['file']).read_bytes() == retained[identity][1]
        assert (artefact.parent / manifest['input_images'][0]['file']).read_bytes() == original.getvalue()
        Path(retained['region-input:pass:1:source'][0]['output_path']).write_bytes(b'changed')
        with pytest.raises(promote.PromoteMaterialError, match='changed since it was collected'):
            promote.promote_candidate(Path(node['output_path']), manifest, 'tampered', 'region',
                                     files, root=root, roots=[root])
