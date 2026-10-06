"""Legacy mesh companions remain portable without inventing execution receipts."""

import hashlib
import json
from pathlib import Path

import pytest

from app import config, crexil_reuse, execution_outputs, manifests
from app.crexil_schemas import CreateReuse, ReuseSelection
from app.crexil_store import CreativeStore
from app.db import new_id
from app.executors import CollectedOutput, DispatchRequest
from app.mesh import fields_sidecar
from test_db import opened

pytestmark = pytest.mark.anyio

PRIMARY = b'glTF\x02\x00\x00\x00retained mesh bytes'
FIELDS = b'PK\x03\x04retained voxel field bytes'
ORBIT = b'RIFFretained cached turntable bytesWEBP'
ACTOR = {'kind': 'human', 'name': 'Artist'}


async def mesh_node(db, tmp_path, *, generated=False):
    project = await db.create_workspace('Original mesh')
    primary = tmp_path / 'original' / 'mesh.glb'
    primary.parent.mkdir()
    primary.write_bytes(PRIMARY)
    fields_sidecar(primary).write_bytes(FIELDS)
    primary.with_suffix('.turntable.webp').write_bytes(ORBIT)
    if generated:
        turn = await db.create_turn_with_slots(project['id'], '', {}, [{'engine': 'trellis2'}])
        slot = await db.dispatch_slot(turn['slots'][0]['id'], job_ref='observed-job',
            submitted_graph={'mesh_job': {'engine': 'trellis2', 'runner_job_id': 'observed-job'}})
        node = await db.land_node(slot['id'], payload='3d', verb='to_3d', output_path=str(primary))
    else:
        node = await db.import_node(project['id'], output_path=str(primary), payload='3d')
    return project, node


async def save_origin(db, node, origin):
    await db.conn.execute('INSERT OR REPLACE INTO material_info VALUES(?,?,?,?,?,?,?)',
        (node['id'], hashlib.sha256(PRIMARY).hexdigest(), 'Artist mesh.glb',
         'model/gltf-binary', None, None, json.dumps(origin)))


async def export(db, project, node):
    return await manifests.build_v4_manifest(db, asset_id='portable', workspace=project,
        node=node, turn=None, slot=None, parents=[],
        turntable=Path(node['output_path']).with_suffix('.turntable.webp'))


@pytest.mark.parametrize('generated', [False, True])
async def test_legacy_files_export_exact_bytes_without_persisting_new_history(tmp_path, generated):
    async with opened(tmp_path) as db:
        project, node = await mesh_node(db, tmp_path, generated=generated)
        before = (await db._one('SELECT COUNT(*) AS n FROM events'))['n']
        manifest, extras = await export(db, project, node)
        material = manifest['material']
        assert manifest['manifest_version'] == 4 and material['related_files_version'] == 1
        assert material['sha256'] == hashlib.sha256(PRIMARY).hexdigest()
        assert material['filename'] == 'mesh.glb' and material['media_type'] == 'model/gltf-binary'
        assert material['origin'] == {'kind': 'unattributed'}
        assert manifest['execution'] is None and manifest['shared_request'] is None
        related = material['related_files']
        assert related[0]['required'] and related[0]['role'] == 'companion'
        assert related[0]['sha256'] == hashlib.sha256(FIELDS).hexdigest()
        assert related[0]['size_bytes'] == len(FIELDS)
        assert all(item['available'] and item['error'] is None and 'path' not in item for item in related)
        assert {path.read_bytes() for path, _ in extras} == {FIELDS, ORBIT}
        assert len(extras) == 2
        if generated:
            assert len(related) == 1
            assert manifest['mesh']['turntable'] == 'portable.turntable.webp'
        else:
            assert len(related) == 2 and not related[1]['required']
            assert related[1]['role'] == 'preview' and related[1]['media_type'] == 'image/webp'
            assert related[1]['sha256'] == hashlib.sha256(ORBIT).hexdigest()
            assert manifest['mesh'] is None and manifest['provider'] is None
            assert manifest['transport'] is None and manifest['replayable'] == manifests.REPLAY_IMPORTED
        assert (await db._one('SELECT COUNT(*) AS n FROM material_info'))['n'] == 0
        assert (await db._one('SELECT COUNT(*) AS n FROM execution_groups'))['n'] == 0
        assert (await db._one('SELECT COUNT(*) AS n FROM events'))['n'] == before


async def test_imported_mesh_extends_existing_origin_and_copies_legacy_companions_on_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'STAGING_DIR', tmp_path / 'staging')
    monkeypatch.setattr(config, 'DISK_MARGIN_BYTES', 0)
    async with opened(tmp_path) as db:
        source, node = await mesh_node(db, tmp_path)
        origin = {'kind': 'import', 'source_record': {'exact': {'retained_files': [{'path': '/history/old.npz'}]}}}
        await save_origin(db, node, origin)
        target = await db.create_workspace('Independent mesh')
        selection = ReuseSelection(source_project_id=source['id'], node_id=node['id'])
        review = await crexil_reuse.review(db, target['id'], selection)
        assert review['ready'] and len(review['files']) == 3
        body = CreateReuse(**selection.model_dump(), request_id=new_id(), actor=ACTOR, review_token=review['token'])
        result = await CreativeStore(db).command(body.request_id, 'test.reuse', body.model_dump(),
            lambda: crexil_reuse.create(db, target['id'], body, []))
        copied = result['node']
        primary = Path(copied['output_path'])
        assert primary.read_bytes() == PRIMARY and primary != Path(node['output_path'])
        assert fields_sidecar(primary).read_bytes() == FIELDS
        assert primary.with_suffix('.turntable.webp').read_bytes() == ORBIT
        assert copied['parents'] == [] and copied['turn_id'] is None and copied['slot_id'] is None
        manifest, extras = await export(db, target, copied)
        frozen = manifest['material']['origin']['source_record']['manifest']
        assert frozen['material']['origin'] == origin
        assert len(manifest['material']['related_files']) == len(extras) == 2
        assert {path.read_bytes() for path, _ in extras} == {FIELDS, ORBIT}
        assert manifest['execution'] is None
        assert (await db._one('SELECT COUNT(*) AS n FROM execution_groups'))['n'] == 0
        assert Path(node['output_path']).read_bytes() == PRIMARY
        assert fields_sidecar(node['output_path']).read_bytes() == FIELDS


@pytest.mark.parametrize('kind', ['fields', 'turntable'])
@pytest.mark.parametrize('state', ['available', 'corrupt', 'error', 'missing'])
async def test_declared_receipt_is_authoritative_over_observed_sidecar(tmp_path, kind, state):
    async with opened(tmp_path) as db:
        project, node = await mesh_node(db, tmp_path, generated=True)
        path = fields_sidecar(node['output_path']) if kind == 'fields' else Path(node['output_path']).with_suffix('.turntable.webp')
        data = FIELDS if kind == 'fields' else ORBIT
        receipt = {'id': 'declared', 'path': str(path) if state != 'missing' else None,
            'filename': path.name, 'role': 'companion' if kind == 'fields' else 'preview',
            'media_type': 'application/octet-stream' if kind == 'fields' else 'image/webp',
            'required': kind == 'fields', 'sha256': hashlib.sha256(data).hexdigest(),
            'size_bytes': len(data), 'error': 'Earlier collection failed.' if state in {'missing', 'error'} else None}
        origin = {'kind': 'import', 'retained_files': [receipt]}
        await save_origin(db, node, origin)
        if state == 'corrupt':
            path.write_bytes(b'x' * len(data))
        manifest, extras = await export(db, project, node)
        records = manifest['material']['related_files']
        declared = next(item for item in records if item['id'] == receipt['id'])
        assert declared['sha256'] == receipt['sha256']
        assert declared['available'] == (state == 'available')
        assert sum(source == path for source, _ in extras) == (state == 'available')
        assert sum(item['filename'] == path.name for item in records) == 1
        if state != 'available':
            assert declared['error'] and declared['file'] is None
        if kind == 'turntable':
            assert manifest['mesh']['turntable'] == declared['file']
        stored = await db._one('SELECT origin_json FROM material_info WHERE node_id=?', (node['id'],))
        assert json.loads(stored['origin_json']) == origin


@pytest.mark.parametrize('state', ['available', 'missing', 'corrupt'])
async def test_execution_companion_receipt_prevents_legacy_hash_replacement(tmp_path, state):
    async with opened(tmp_path) as db:
        project, node = await mesh_node(db, tmp_path, generated=True)
        slot = await db.get_slot(node['slot_id'])
        path = fields_sidecar(node['output_path'])
        output = CollectedOutput(identity='companion:.fields.npz', filename='companion.fields.npz',
            data=FIELDS if state != 'missing' else None, payload=None, role='companion',
            error='Runner fields unavailable.' if state == 'missing' else None)
        await execution_outputs.collect(db, tmp_path, slot,
            DispatchRequest(slot_id=slot['id'], engine='trellis2', payload='3d'), output,
            path=path if state != 'missing' else None)
        if state == 'corrupt':
            path.write_bytes(b'x' * len(FIELDS))
        manifest, extras = await export(db, project, node)
        member = manifest['execution']['outputs'][0]
        assert member['sha256'] == (None if state == 'missing' else hashlib.sha256(FIELDS).hexdigest())
        assert manifest['material'] is None
        assert sum(source == path for source, _ in extras) == (state != 'missing')
        if state == 'missing':
            assert member['error'] == 'Runner fields unavailable.' and not member['available']

