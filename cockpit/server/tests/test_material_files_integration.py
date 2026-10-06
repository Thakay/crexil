"""Owned related material survives export, purge and portable project restore."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app import api_v4, config, crexil_archives, crexil_restore, execution_outputs, manifests, material_files
from app.crexil_store import CreativeStore
from app.db import Database, new_id
from app.schemas import Purge
from test_crexil_archive_format import rewrite

pytestmark = pytest.mark.anyio
ACTOR = {'kind': 'human', 'name': 'Artist'}


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'STAGING_DIR', tmp_path / 'staging')
    store = Database(tmp_path / 'integration.sqlite')
    await store.open()
    monkeypatch.setattr(api_v4, '_stack', lambda: SimpleNamespace(db=store))
    try:
        yield store
    finally:
        await store.close()


def receipt(path, data, **changes):
    return {'id': new_id(), 'path': str(path) if path is not None else None,
            'filename': path.name if path is not None else 'missing.bin', 'role': 'companion',
            'media_type': 'application/octet-stream', 'required': True,
            'sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
            'size_bytes': len(data) if data is not None else None, 'error': None, **changes}


async def imported(db, *, name='Origin', source_record=None):
    project = await db.create_workspace(name)
    directory = config.STAGING_DIR / new_id()
    directory.mkdir(parents=True)
    primary = directory / 'mesh.glb'
    primary.write_bytes(b'retained primary bytes')
    nested = directory / 'related' / 'fields.npz'
    nested.parent.mkdir()
    nested.write_bytes(b'retained fields')
    node = await db.import_node(project['id'], output_path=str(primary), payload='3d')
    files = [receipt(nested, b'retained fields'), receipt(None, None, error='Not supplied by the source.')]
    origin = {'kind': 'project_reuse', 'retained_files': files, 'source_record': source_record or {
        'node_id': 'old-node', 'submitted_graph_json': '{ "path": "/original/input.png" }',
        'material': {'origin': {'retained_files': [{'path': '/historical/never-copy.bin'}]}}}}
    await db.conn.execute('INSERT INTO material_info VALUES(?,?,?,?,?,?,?)',
        (node['id'], hashlib.sha256(primary.read_bytes()).hexdigest(), primary.name,
         'model/gltf-binary', None, None, json.dumps(origin)))
    return project, node, files, origin


async def set_origin(db, node, origin):
    await db.conn.execute('UPDATE material_info SET origin_json=? WHERE node_id=?',
                         (json.dumps(origin), node['id']))


async def export(db, node):
    return await manifests.build_v4_manifest(db, asset_id='asset',
        workspace=await db.get_workspace(node['workspace_id']), node=node, turn=None, slot=None, parents=[])


async def backup(db, project):
    async with db._tx():
        result = await crexil_archives.create(db, project['id'], ACTOR)
    return config.STAGING_DIR / 'backups' / result['id'] / 'project.crexil.zip', result


async def restore(db, path):
    return await CreativeStore(db).command(new_id(), 'test.restore', {},
        lambda: crexil_restore.restore(db, path, name='Restored', actor=ACTOR))


async def test_manifest_related_files_are_portable_and_source_record_is_unchanged(db):
    _, node, files, origin = await imported(db)
    damaged = Path(node['output_path']).parent / 'corrupt.bin'
    damaged.write_bytes(b'changed')
    files.append(receipt(damaged, b'earlier'))
    await set_origin(db, node, origin)
    manifest, extras = await export(db, node)
    material = manifest['material']
    assert manifest['manifest_version'] == 4 and material['related_files_version'] == 1
    assert material['origin']['source_record'] == origin['source_record']
    assert 'retained_files' not in material['origin']
    related = material['related_files']
    assert [item['available'] for item in related] == [True, False, False]
    assert all('path' not in item and item['required'] for item in related)
    assert related[1]['error'] == 'Not supplied by the source.' and related[1]['file'] is None
    assert 'SHA-256' in related[2]['error'] and related[2]['file'] is None
    assert extras == [(Path(files[0]['path']), f"asset.outputs/{files[0]['id']}.npz")]
    assert manifest['execution'] is None and manifest['shared_request'] is None
    stored = json.loads((await db._one('SELECT origin_json FROM material_info WHERE node_id=?', (node['id'],)))['origin_json'])
    assert stored == origin


async def test_purge_deletes_only_material_owned_companions(db):
    _, original, original_files, original_origin = await imported(db)
    project, node, files, _ = await imported(db, name='Reuse', source_record=original_origin)
    original_path = Path(original_files[0]['path'])
    copied_path = Path(files[0]['path'])
    assert original_path != copied_path and original_path.read_bytes() == copied_path.read_bytes()
    assert await execution_outputs.owned_companions(db, node['id']) == [copied_path]
    await db.set_aside_node(node['id'])
    tray = await api_v4.workspace_tray(project['id'])
    expected = Path(node['output_path']).stat().st_size + copied_path.stat().st_size
    assert tray['bytes'] == expected
    result = await api_v4.purge_nodes(project['id'], Purge(ids=[node['id']]))
    assert result['freed_bytes'] == expected
    assert not copied_path.exists() and not Path(node['output_path']).exists()
    assert original_path.read_bytes() == b'retained fields'
    assert Path(original['output_path']).is_file()
    assert (await db.get_node(original['id']))['purged_at'] is None
    assert not (await material_files.view(db, await db.get_node(node['id'])))[0]['available']


async def test_archive_restore_preserves_nested_owned_files_and_exact_source_history(db):
    project, node, files, origin = await imported(db)
    path, created = await backup(db, project)
    assert {entry['source'] for entry in created['files']} == {node['output_path'], files[0]['path']}
    manifest, records = crexil_archives.read_archive(path)
    assert all(entry['available'] for entry in manifest['files'])
    assert json.loads(records['material_info'][0]['origin_json']) == origin
    # Files without a source path remain explicit receipts in the sealed record.
    assert json.loads(records['material_info'][0]['origin_json'])['retained_files'][1] == files[1]
    copied = await restore(db, path)
    context = await crexil_restore.context(db, copied['project_id'])
    restored = await db.get_node(context['identities']['nodes'][node['id']])
    restored_origin = json.loads((await db._one('SELECT origin_json FROM material_info WHERE node_id=?', (restored['id'],)))['origin_json'])
    current = material_files.files_from_origin(restored, restored_origin)
    restored_file = Path(current[0]['path'])
    assert restored_file == Path(restored['output_path']).parent / 'related' / 'fields.npz'
    assert restored_file != Path(files[0]['path'])
    assert restored_file.read_bytes() == b'retained fields'
    assert current[1] == files[1] and restored_origin['source_record'] == origin['source_record']
    assert (await export(db, restored))[0]['material']['related_files'][0]['available']
    # Repeated portability keeps ownership and leaves the historical manifest exact.
    again, _ = await backup(db, await db.get_workspace(copied['project_id']))
    second = await restore(db, again)
    second_context = await crexil_restore.context(db, second['project_id'])
    final = await db.get_node(second_context['identities']['nodes'][node['id']])
    assert (await export(db, final))[0]['material']['origin']['source_record'] == origin['source_record']
    await db.set_aside_node(restored['id'])
    await api_v4.purge_nodes(copied['project_id'], Purge(ids=[restored['id']]))
    assert not restored_file.exists()
    assert Path(files[0]['path']).read_bytes() == b'retained fields'
    assert (await material_files.get_file(db, final['id'], files[0]['id']))[0].read_bytes() == b'retained fields'


@pytest.mark.parametrize('problem', ['size', 'error', 'missing', 'purged'])
async def test_archive_keeps_unavailable_owned_receipts_without_packaged_bytes(db, problem):
    project, node, files, origin = await imported(db)
    if problem == 'size':
        files[0]['size_bytes'] += 1
    elif problem == 'error':
        files[0]['error'] = 'Receipt remains incomplete.'
    elif problem == 'missing':
        Path(files[0]['path']).unlink()
    else:
        await db.purge_node(node['id'])
    await set_origin(db, node, origin)
    path, created = await backup(db, project)
    retained = next(item for item in created['files'] if item['source'] == files[0]['path'])
    assert not retained['available'] and retained['file'] is None and retained['error']
    _, records = crexil_archives.read_archive(path)
    assert json.loads(records['material_info'][0]['origin_json'])['retained_files'] == files


@pytest.mark.parametrize('malformation', ['foreign_path', 'null_without_error', 'wrong_size', 'stored_error'])
async def test_archive_decoder_validates_owned_file_receipts(db, tmp_path, malformation):
    project, _, _, _ = await imported(db)
    path, _ = await backup(db, project)

    def change(conn):
        row = conn.execute('SELECT node_id,origin_json FROM material_info').fetchone()
        origin = json.loads(row[1])
        retained = origin['retained_files'][0]
        if malformation == 'foreign_path':
            retained['path'] = str(tmp_path / 'unrelated.bin')
        elif malformation == 'null_without_error':
            retained['path'] = None
        elif malformation == 'wrong_size':
            retained['size_bytes'] += 1
        else:
            retained['error'] = 'Unavailable receipt cannot authorize packaged bytes.'
        conn.execute('UPDATE material_info SET origin_json=? WHERE node_id=?', (json.dumps(origin), row[0]))

    tampered = tmp_path / 'tampered.zip'
    rewrite(path, tampered, database_change=change)
    with pytest.raises(ValueError):
        crexil_archives.read_archive(tampered)


async def test_archive_requires_declared_retained_paths_and_refuses_unsafe_capture(db, tmp_path):
    project, node, files, origin = await imported(db)
    path, _ = await backup(db, project)
    missing = tmp_path / 'omitted.zip'
    rewrite(path, missing, manifest_change=lambda manifest: manifest['files'].__setitem__(
        slice(None), [item for item in manifest['files'] if item['source'] != files[0]['path']]))
    with pytest.raises(ValueError, match='omits required'):
        crexil_archives.read_archive(missing)
    origin['retained_files'][0]['path'] = str(tmp_path / 'foreign.bin')
    await set_origin(db, node, origin)
    with pytest.raises(ValueError, match='leave its material directory'):
        await backup(db, project)


async def test_imported_mesh_uses_cached_orbit_without_runner_identity(db, monkeypatch):
    _, node, _, _ = await imported(db)

    async def forbidden(*args):
        raise AssertionError('An imported mesh must not contact a runner')

    monkeypatch.setattr(api_v4, '_fetch_turntable', forbidden)
    assert not api_v4._mesh_view(node)['has_turntable']
    with pytest.raises(HTTPException) as absent:
        await api_v4._turntable_file(node)
    assert absent.value.status_code == 404
    orbit = Path(node['output_path']).with_suffix('.turntable.webp')
    Image.new('RGB', (4, 3), 'red').save(orbit, format='WEBP')
    mesh = api_v4._mesh_view(node)
    assert mesh['has_turntable'] and mesh['runner_job_id'] is None
    assert await api_v4._turntable_file(node) == orbit
    response = await api_v4.node_thumb(node['id'])
    assert Path(response.path).is_file()
    historical = copy.deepcopy(node)
    historical['dials']['crexil_restore'] = {'source': 'archive'}
    assert await api_v4._turntable_file(historical) == orbit
    historical['purged_at'] = 'purged'
    assert not api_v4._mesh_view(historical)['has_turntable']
    with pytest.raises(HTTPException):
        await api_v4._turntable_file(historical)
