"""Material receipts grant access only to independently owned, recorded bytes."""

import hashlib
import json
from pathlib import Path

import pytest

from app import material_files
from app.db import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
async def db(tmp_path):
    store = Database(tmp_path / 'material.sqlite')
    await store.open()
    try:
        yield store
    finally:
        await store.close()


def receipt(path, data=b'owned companion', **changes):
    return {'id': 'related-1', 'path': str(path) if path is not None else None,
            'filename': 'fields.npz', 'role': 'companion', 'media_type': 'application/octet-stream',
            'required': True, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
            'error': None, **changes}


async def material(db, tmp_path, name='first', data=b'owned companion'):
    project = await db.create_workspace(name)
    directory = tmp_path / name
    directory.mkdir()
    primary = directory / 'mesh.glb'
    primary.write_bytes(b'primary material')
    companion = directory / 'mesh.fields.npz'
    companion.write_bytes(data)
    node = await db.import_node(project['id'], output_path=str(primary), payload='3d')
    item = receipt(companion, data)
    await db.conn.execute('INSERT INTO material_info VALUES(?,?,?,?,?,?,?)',
        (node['id'], hashlib.sha256(b'primary material').hexdigest(), 'mesh.glb', 'model/gltf-binary',
         None, None, json.dumps({'kind': 'project_reuse', 'retained_files': [item]})))
    return node, companion, item


async def origin(db, node, value):
    await db.conn.execute('UPDATE material_info SET origin_json=? WHERE node_id=?',
                          (json.dumps(value), node['id']))


async def test_receipts_view_download_and_export_keep_identity_without_execution(db, tmp_path):
    node, path, item = await material(db, tmp_path)
    before = await db.latest_seq()
    metadata = await material_files.view(db, node)
    assert metadata == [{key: value for key, value in item.items() if key != 'path'} | {
        'available': True, 'file': None}]
    assert await material_files.owned(db, node['id']) == [path]
    assert await material_files.get_file(db, node['id'], item['id']) == (path, metadata[0])
    records, extras = await material_files.export_files(db, node, 'asset-1')
    assert records == [{**metadata[0], 'file': 'asset-1.outputs/related-1.npz'}]
    assert extras == [(path, records[0]['file'])]
    assert await db.latest_seq() == before
    assert await db._all('SELECT * FROM execution_groups') == []
    assert path.read_bytes() == b'owned companion'


async def test_missing_and_purged_members_remain_explicit_and_owned(db, tmp_path):
    node, path, item = await material(db, tmp_path)
    absent = receipt(None, id='uncollected', sha256=None, size_bytes=None,
                     error='The source did not retain its fields.')
    await origin(db, node, {'retained_files': [item, absent]})
    path.unlink()
    records, extras = await material_files.export_files(db, node, 'asset')
    assert extras == []
    assert [record['id'] for record in records] == ['related-1', 'uncollected']
    assert all(not record['available'] and record['file'] is None for record in records)
    assert 'missing' in records[0]['error']
    assert records[1]['error'] == absent['error']
    assert records[0]['sha256'] == item['sha256'] and records[0]['required']
    with pytest.raises(ValueError, match='missing'):
        await material_files.get_file(db, node['id'], item['id'])
    path.write_bytes(b'owned companion')
    await db.purge_node(node['id'])
    purged = await db.get_node(node['id'])
    assert not (await material_files.view(db, purged))[0]['available']
    assert await material_files.owned(db, node['id']) == [path]
    with pytest.raises(ValueError, match='purged'):
        await material_files.get_file(db, node['id'], item['id'])
    assert (await material_files.export_files(db, purged, 'asset'))[1] == []
    assert path.is_file()  # Only the caller performs deletion.


@pytest.mark.parametrize(('changed', 'message'), [
    (b'other companion', 'SHA-256'), (b'short', 'byte count'), (b'owned companion plus', 'byte count')])
async def test_snapshot_avoids_hashing_but_download_and_export_refuse_changed_bytes(db, tmp_path, changed, message):
    node, path, item = await material(db, tmp_path)
    path.write_bytes(changed)
    assert (await material_files.view(db, node))[0]['available']
    with pytest.raises(ValueError, match=message):
        await material_files.get_file(db, node['id'], item['id'])
    records, extras = await material_files.export_files(db, node, 'asset')
    assert extras == [] and not records[0]['available']
    assert message in records[0]['error'] and records[0]['file'] is None
    assert records[0]['sha256'] == item['sha256']


async def test_stored_error_is_not_healed_by_existing_bytes(db, tmp_path):
    node, path, item = await material(db, tmp_path)
    item['error'] = 'Source receipt was incomplete.'
    await origin(db, node, {'retained_files': [item]})
    assert not (await material_files.view(db, node))[0]['available']
    assert (await material_files.export_files(db, node, 'asset'))[1] == []
    with pytest.raises(ValueError, match='Source receipt'):
        await material_files.get_file(db, node['id'], item['id'])
    assert await material_files.owned(db, node['id']) == [path]


async def test_other_node_receipts_and_nested_source_metadata_grant_no_access(db, tmp_path):
    node, path, item = await material(db, tmp_path)
    other, other_path, other_item = await material(db, tmp_path, 'other', data=b'different bytes')
    assert (await material_files.get_file(db, other['id'], other_item['id']))[0] == other_path
    assert (await material_files.get_file(db, node['id'], item['id']))[0] == path
    await origin(db, node, {'kind': 'imported', 'source': {'retained_files': [other_item]}})
    assert await material_files.view(db, node) == []
    assert await material_files.owned(db, node['id']) == []
    assert await material_files.export_files(db, node, 'asset') == ([], [])
    with pytest.raises(LookupError, match='does not belong'):
        await material_files.get_file(db, node['id'], other_item['id'])
    await origin(db, node, {'retained_files': [other_item]})
    for action in (lambda: material_files.view(db, node), lambda: material_files.owned(db, node['id']),
                   lambda: material_files.export_files(db, node, 'asset'),
                   lambda: material_files.get_file(db, node['id'], other_item['id'])):
        with pytest.raises(ValueError, match='leave its material directory'):
            await action()
    assert other_path.read_bytes() == b'different bytes'


@pytest.mark.parametrize('kind', ['sibling', 'traversal', 'symlink', 'directory'])
async def test_paths_are_fenced_even_when_receipt_has_an_error(db, tmp_path, kind):
    node, path, item = await material(db, tmp_path)
    foreign = tmp_path / 'outside.bin'
    foreign.write_bytes(b'foreign')
    if kind == 'sibling':
        unsafe = foreign
    elif kind == 'traversal':
        unsafe = path.parent / '..' / foreign.name
    elif kind == 'symlink':
        unsafe = path.parent / 'escaped.bin'
        unsafe.symlink_to(foreign)
    else:
        unsafe = path.parent
    item.update(path=str(unsafe), error='Still unavailable')
    with pytest.raises(ValueError, match='leave its material directory'):
        material_files.files_from_origin(node, {'retained_files': [item]})


@pytest.mark.parametrize('change', [
    {'id': '../escape'}, {'id': 'a/b'}, {'id': 'a\\b'}, {'filename': '../source'},
    {'required': 1}, {'sha256': 'not a hash'}, {'size_bytes': True}, {'size_bytes': -1},
    {'path': None}, {'path': None, 'error': ''}, {'path': ''}, {'sha256': None},
    {'role': ''}, {'media_type': None}, {'unexpected': 'metadata'},
])
async def test_malformed_receipts_are_refused(db, tmp_path, change):
    node, _, item = await material(db, tmp_path)
    with pytest.raises(ValueError):
        material_files.files_from_origin(node, {'retained_files': [{**item, **change}]})


async def test_duplicate_ids_and_invalid_origin_shapes_are_refused(db, tmp_path):
    node, _, item = await material(db, tmp_path)
    for value in ([], {'retained_files': None}, {'retained_files': {}},
                  {'retained_files': [item, item]}, {'retained_files': [{'id': 'missing-fields'}]}):
        with pytest.raises(ValueError):
            material_files.files_from_origin(node, value)
    with pytest.raises(LookupError, match='material could not be found'):
        await material_files.get_file(db, 'missing', 'file')
    assert await material_files.owned(db, 'missing') == []


async def test_exports_stream_hashing_and_use_safe_suffixes(db, tmp_path, monkeypatch):
    data = b'abcd' * (1024 * 1024)
    node, path, item = await material(db, tmp_path, data=data)
    item['filename'] = 'archive.unusual-suffix'
    await origin(db, node, {'retained_files': [item]})
    opened, reads = Path.open, []

    class Stream:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            reads.append(size)
            return self.source.read(size)

    def open_stream(self, *args, **kwargs):
        source = opened(self, *args, **kwargs)
        return Stream(source) if self == path else source

    def no_whole_read(self):
        raise AssertionError('File verification must stream its reads')

    monkeypatch.setattr(Path, 'open', open_stream)
    monkeypatch.setattr(Path, 'read_bytes', no_whole_read)
    assert (await material_files.view(db, node))[0]['available'] and reads == []
    records, extras = await material_files.export_files(db, node, 'asset')
    assert len(reads) > 1
    assert extras == [(path, 'asset.outputs/related-1.bin')]
    assert records[0]['available'] and records[0]['sha256'] == hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match='safe filename'):
        await material_files.export_files(db, node, '../escape')
