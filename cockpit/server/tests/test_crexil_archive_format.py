"""Project archive validation preserves trusted records and rejects unsafe ZIPs."""

import hashlib
import json
import sqlite3
import stat
import struct
import warnings
import zipfile
from contextlib import closing

import pytest

from app import config, crexil_archives as archives
from app.db import Database, SCHEMA
from app.mesh import fields_sidecar


@pytest.fixture
def saved_archive(tmp_path, monkeypatch):
    staging = tmp_path / 'material'
    staging.mkdir()
    monkeypatch.setattr(config, 'STAGING_DIR', staging)
    image = staging / 'original.png'
    image.write_bytes(bytes(range(256)) * 16)
    receipt = staging / 'response.json'
    receipt.write_bytes(b'{ "request": {"prompt": "exact bytes"}, "response": [] }\n')
    graph = '{ "1": { "class_type": "Example", "inputs": {"text": "original-id /old/path"} } }'
    setup = '{ "prompt": "Do not rewrite original-id", "references": [{"node_id": "parent"}] }'
    database = tmp_path / 'source.sqlite'
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO workspaces(id,name,slug,created_at,updated_at) VALUES('project','Study','study','then','then')")
        conn.execute("INSERT INTO project_details(workspace_id,brief) VALUES('project','Preserve every original byte')")
        conn.execute("INSERT INTO turns(id,workspace_id,idx,prompt,dials_json,created_at) VALUES('turn','project',0,'Saved request',?,'then')", (setup,))
        conn.execute("INSERT INTO slots(id,turn_id,pos,engine,submitted_graph_json,created_at) VALUES('slot','turn',0,'local',?,'then')", (graph,))
        for nid in ('parent', 'child'):
            conn.execute("INSERT INTO nodes(id,workspace_id,payload,verb,prompt,output_path,created_at) VALUES(?,'project','image','import',?,?, 'then')",
                         (nid, 'Exact prompt ' + nid, str(image)))
            conn.execute("INSERT INTO material_info VALUES(?,?,?,'image/png',NULL,NULL,?)",
                         (nid, hashlib.sha256(image.read_bytes()).hexdigest(), 'Original.png', '{ "kind": "imported" }'))
        conn.execute("INSERT INTO node_parents VALUES('child','parent',0)")
        conn.execute("INSERT INTO directions(id,workspace_id,row_idx,title,setup_json,created_at,updated_at) VALUES('direction','project',0,'Saved direction',?,'then','then')", (setup,))
        conn.execute("INSERT INTO direction_material VALUES('direction','child')")
        conn.execute("INSERT INTO execution_groups(id,slot_id,take,state,created_at,updated_at) VALUES('group','slot',0,'incomplete','then','then')")
        conn.execute("INSERT INTO execution_outputs(id,group_id,identity,ordinal,role,filename,media_type,output_path,sha256,size_bytes,created_at) VALUES('receipt','group','response',0,'provider receipt','response.json','application/json',?,?,?,'then')",
                     (str(receipt), hashlib.sha256(receipt.read_bytes()).hexdigest(), receipt.stat().st_size))
        conn.execute("INSERT INTO events(ts,type,workspace_id,turn_id,slot_id,payload_json) VALUES('then','slot.prepared','project','turn','slot',?)", (setup,))
        conn.row_factory = sqlite3.Row
        records = {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
                   for table in archives.PROJECT_TABLES}
    destination = tmp_path / 'project.crexil.zip'
    identity = {'id': 'backup', 'project_id': 'project', 'project_name': 'Study',
                'created_at': 'then', 'actor': {'kind': 'human', 'name': 'Artist'}}
    archives._write_archive(destination, records, identity)
    return destination, records, identity


def rewrite(saved, destination, *, manifest_change=None, database_change=None, member_change=None, extra=()):
    with zipfile.ZipFile(saved) as source:
        members = {info.filename: (info, source.read(info)) for info in source.infolist()}
    manifest = json.loads(members['manifest.json'][1])
    if database_change:
        path = destination.with_suffix('.sqlite')
        path.write_bytes(members['project.sqlite'][1])
        with closing(sqlite3.connect(path)) as conn, conn:
            database_change(conn)
        data = path.read_bytes()
        members['project.sqlite'] = (members['project.sqlite'][0], data)
        manifest['database_sha256'] = hashlib.sha256(data).hexdigest()
    if manifest_change:
        manifest_change(manifest)
    members['manifest.json'] = (members['manifest.json'][0], json.dumps(manifest).encode())
    if member_change:
        member_change(members)
    with warnings.catch_warnings(), zipfile.ZipFile(destination, 'w') as result:
        warnings.simplefilter('ignore', UserWarning)
        for info, data in [*members.values(), *extra]:
            result.writestr(info, data)
    return destination


def test_read_preserves_exact_records_material_and_archive(saved_archive, tmp_path):
    path, original, _ = saved_archive
    before = path.read_bytes()
    paths_before = {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    manifest, records = archives.read_archive(path)
    assert records == original
    assert records['slots'][0]['submitted_graph_json'] == original['slots'][0]['submitted_graph_json']
    assert records['turns'][0]['dials_json'] == original['turns'][0]['dials_json']
    with zipfile.ZipFile(path) as packed:
        for item in manifest['files']:
            assert packed.read(item['file']) == (tmp_path / 'material' / item['source'].split('/')[-1]).read_bytes()
    assert path.read_bytes() == before
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob('*')} == paths_before


@pytest.mark.parametrize(('change', 'message'), [
    (lambda m: m.update(version=2), 'version'),
    (lambda m: m.update(version=True), 'version'),
    (lambda m: m.update(schema_version=999), 'schema version'),
    (lambda m: m.update(project_id='different'), 'exactly the project'),
    (lambda m: m['records'].update(workspaces=2), 'one project'),
    (lambda m: m['records'].update(nodes=999), 'record count'),
    (lambda m: m['records'].update(unknown=0), 'supported tables'),
    (lambda m: m.update(database_sha256='0' * 64), 'database.*hash'),
    (lambda m: m['files'][0].update(size_bytes=1), 'recorded size'),
    (lambda m: m['files'].pop(), 'omits required'),
    (lambda m: m['files'].append(dict(m['files'][0])), 'repeats'),
    (lambda m: m['files'][0].update(available=False), 'unavailable archive file'),
    (lambda m: m['files'][0].update(source='/original/unsafe\\name.png'), 'source path'),
])
def test_rejects_invalid_manifest(saved_archive, tmp_path, change, message):
    path = rewrite(saved_archive[0], tmp_path / 'changed.zip', manifest_change=change)
    with pytest.raises(ValueError, match=message):
        archives.read_archive(path)


@pytest.mark.parametrize(('sql', 'message'), [
    ("CREATE TABLE unrelated(secret TEXT)", 'schema'),
    ("ALTER TABLE directions ADD COLUMN unexpected TEXT", 'schema'),
    ("CREATE TRIGGER hostile AFTER INSERT ON nodes BEGIN SELECT load_extension('/not/executed'); END", 'schema'),
    ("DELETE FROM meta WHERE key='archive_sealed'", 'sealed'),
    ("UPDATE meta SET value='0' WHERE key='archive_sealed'", 'sealed'),
    ("UPDATE meta SET value='99' WHERE key='schema_version'", 'schema version'),
    ("UPDATE node_parents SET parent_id='absent'", 'record relationship'),
    ("INSERT INTO model_hashes VALUES('/unrelated/model',1,0,'digest')", 'model-cache'),
    ("INSERT INTO node_fts VALUES('secret','unrelated')", 'search index'),
    ("UPDATE directions SET setup_json='not json'", 'invalid JSON'),
])
def test_rejects_untrusted_or_inconsistent_database(saved_archive, tmp_path, sql, message):
    path = rewrite(saved_archive[0], tmp_path / 'changed.zip', database_change=lambda conn: conn.execute(sql))
    with pytest.raises(ValueError, match=message):
        archives.read_archive(path)


def test_rejects_unrelated_event_even_without_foreign_key(saved_archive, tmp_path):
    def change(conn):
        conn.execute('DROP TRIGGER trg_events_no_update')
        conn.execute("UPDATE events SET workspace_id='another-project'")
        conn.executescript(SCHEMA)
    path = rewrite(saved_archive[0], tmp_path / 'changed.zip', database_change=change)
    with pytest.raises(ValueError, match='unrelated project records in events'):
        archives.read_archive(path)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'material/../escape', 'material\\escape',
                                 'material//escape', 'C:/escape', 'material/./escape'])
def test_rejects_unsafe_paths_without_extracting(saved_archive, tmp_path, name):
    path = rewrite(saved_archive[0], tmp_path / 'changed.zip', extra=[(name, b'not extracted')])
    with pytest.raises(ValueError, match='unsafe'):
        archives.read_archive(path)
    assert not (tmp_path / 'escape').exists()


def test_rejects_duplicates_symlinks_directories_and_undisclosed_members(saved_archive, tmp_path):
    link = zipfile.ZipInfo('material/900/link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for index, (extra, message) in enumerate([
        (('manifest.json', b'{}'), 'duplicate'),
        (('MANIFEST.JSON', b'{}'), 'duplicate'),
        ((link, b'/outside'), 'links'),
        (('material/', b''), 'unsafe'),
        (('material/900/extra', b'undisclosed'), 'undisclosed'),
    ]):
        path = rewrite(saved_archive[0], tmp_path / f'changed-{index}.zip', extra=[extra])
        with pytest.raises(ValueError, match=message):
            archives.read_archive(path)


def test_rejects_encryption_before_opening_members(saved_archive, tmp_path):
    data = bytearray(saved_archive[0].read_bytes())
    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        index = data.index(signature)
        flags = struct.unpack_from('<H', data, index + offset)[0]
        struct.pack_into('<H', data, index + offset, flags | 1)
    path = tmp_path / 'encrypted.zip'
    path.write_bytes(data)
    with pytest.raises(ValueError, match='unencrypted'):
        archives.read_archive(path)


@pytest.mark.parametrize('database', [False, True])
def test_rejects_changed_bytes_with_valid_zip_crc(saved_archive, tmp_path, database):
    def change(members):
        name = 'project.sqlite' if database else next(name for name in members if name.startswith('material/'))
        info, data = members[name]
        members[name] = (info, data[:-1] + bytes([data[-1] ^ 1]))
    path = rewrite(saved_archive[0], tmp_path / 'changed.zip', member_change=change)
    with pytest.raises(ValueError, match='content hash'):
        archives.read_archive(path)


def test_bounds_metadata_database_total_members_and_records(saved_archive, monkeypatch):
    for name, limit in [('MAX_MANIFEST_BYTES', 10), ('MAX_DATABASE_BYTES', 10),
                        ('MAX_UNPACKED_BYTES', 10), ('MAX_MEMBERS', 1), ('MAX_RECORDS', 1),
                        ('MAX_DIRECTORY_BYTES', 1)]:
        with monkeypatch.context() as patch:
            patch.setattr(archives, name, limit)
            with pytest.raises(ValueError, match='limit'):
                archives.read_archive(saved_archive[0])


def test_material_hashing_streams_in_bounded_chunks(saved_archive, monkeypatch):
    original = zipfile.ZipExtFile.read
    reads = []
    def read(stream, n=-1):
        if stream.name.startswith('material/'):
            reads.append(n)
            assert 0 < n <= 97
        return original(stream, n)
    monkeypatch.setattr(archives, 'COPY_CHUNK_BYTES', 97)
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', read)
    archives.read_archive(saved_archive[0])
    assert len(reads) > 10


def test_frozen_missing_mesh_dependency_and_missing_bytes_remain_explicit(saved_archive, tmp_path):
    _, records, identity = saved_archive
    fields = fields_sidecar(records['nodes'][0]['output_path'])
    records['slots'][0]['submitted_graph_json'] = json.dumps({'mesh_job': {
        'source_hashes': [{'path': str(fields), 'sha256': 'a' * 64}]}})
    path = tmp_path / 'missing.zip'
    archives._write_archive(path, records, identity)
    manifest, recovered = archives.read_archive(path)
    missing = next(item for item in manifest['files'] if item['source'] == str(fields))
    assert missing['available'] is False and missing['file'] is None and missing['error']
    assert missing['sha256'] == 'a' * 64
    assert recovered == records


@pytest.mark.parametrize('kind', ['region_plan', 'mesh_job'])
def test_rebackup_resolves_owned_frozen_inputs_without_rewriting_history(saved_archive, tmp_path, kind):
    _, records, identity = saved_archive
    original = tmp_path / 'material' / 'original.png'
    owned = tmp_path / 'material' / 'restored.png'
    owned.write_bytes(original.read_bytes())
    original.write_bytes(b'The original installation changed after restoration.')
    for node in records['nodes']:
        node['output_path'] = str(owned)
    source = {'path': str(original), 'sha256': hashlib.sha256(owned.read_bytes()).hexdigest()}
    graph = {kind: {'source': source}} if kind == 'region_plan' else {kind: {'source_hashes': [source]}}
    frozen = json.dumps(graph, indent=3)
    records['slots'][0]['submitted_graph_json'] = frozen
    intermediate = str(tmp_path / 'earlier-installation' / 'original.png')
    for seq, payload in [(2, {'paths': {str(original): str(original)}, 'archive_origin': {}}),
                         (3, {'paths': {str(original): intermediate, intermediate: str(owned)}}),
                         (4, {'paths': {str(original): str(original)}, 'archive_origin': {}})]:
        records['events'].append({'seq': seq, 'ts': 'then', 'type': 'project.restored',
                                  'workspace_id': 'project', 'turn_id': None, 'slot_id': None,
                                  'node_id': None, 'charged_usd': None, 'payload_json': json.dumps(payload)})
    path = tmp_path / 'rebackup.zip'
    archives._write_archive(path, records, identity)
    manifest, recovered = archives.read_archive(path)
    assert all(item['available'] for item in manifest['files'])
    assert str(original) not in {item['source'] for item in manifest['files']}
    assert str(owned) in {item['source'] for item in manifest['files']}
    assert recovered['slots'][0]['submitted_graph_json'] == frozen


def test_writer_enforces_shared_limits(saved_archive, tmp_path, monkeypatch):
    _, records, identity = saved_archive
    for name, limit in [('MAX_DATABASE_BYTES', 1), ('MAX_RECORDS', 1), ('MAX_MANIFEST_BYTES', 1),
                        ('MAX_MEMBERS', 1), ('MAX_UNPACKED_BYTES', 1), ('MAX_DIRECTORY_BYTES', 1)]:
        destination = tmp_path / f'{name}.zip'
        with monkeypatch.context() as patch:
            patch.setattr(archives, name, limit)
            with pytest.raises(ValueError, match='limit'):
                archives._write_archive(destination, records, identity)
        assert not destination.exists()


def test_dishonest_footer_cannot_bypass_member_allocation_limit(saved_archive, tmp_path, monkeypatch):
    data = bytearray(saved_archive[0].read_bytes())
    footer = data.rindex(b'PK\x05\x06')
    struct.pack_into('<HH', data, footer + 8, 1, 1)
    path = tmp_path / 'dishonest-directory.zip'
    path.write_bytes(data)
    with pytest.raises(ValueError, match='declared member count'):
        archives.read_archive(path)
    monkeypatch.setattr(archives, 'MAX_MEMBERS', 2)
    with pytest.raises(ValueError, match='member limit'):
        archives.read_archive(path)


def test_zip64_directory_and_deflated_material_remain_readable(saved_archive, tmp_path, monkeypatch):
    # Exercise ZIP64 without allocating huge test files.
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 1024)
    with zipfile.ZipFile(saved_archive[0]) as source, zipfile.ZipFile(tmp_path / 'zip64.zip', 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for member in source.infolist():
            target.writestr(member.filename, source.read(member))
    _, records = archives.read_archive(tmp_path / 'zip64.zip')
    assert records == saved_archive[1]


def test_rejects_duplicate_manifest_keys_and_truncated_archive(saved_archive, tmp_path):
    def change(members):
        info, data = members['manifest.json']
        members['manifest.json'] = (info, b'{"version":1,' + data[1:])
    duplicate = rewrite(saved_archive[0], tmp_path / 'duplicate.zip', member_change=change)
    with pytest.raises(ValueError, match='repeats the field'):
        archives.read_archive(duplicate)
    truncated = tmp_path / 'truncated.zip'
    truncated.write_bytes(saved_archive[0].read_bytes()[:-30])
    with pytest.raises(ValueError, match='complete project ZIP'):
        archives.read_archive(truncated)


@pytest.mark.anyio
async def test_snapshot_does_not_copy_cross_project_operation_receipt_evidence(tmp_path):
    db = Database(tmp_path / 'live.sqlite')
    await db.open()
    try:
        project = await db.create_workspace('Archive this')
        other = await db.create_workspace('Unrelated')
        turn = await db.create_turn_with_slots(project['id'], 'Own work', {}, [{'engine': 'custom-operation'}])
        other_turn = await db.create_turn_with_slots(other['id'], 'Private other work', {}, [{'engine': 'custom-operation'}])
        await db.conn.execute("INSERT INTO operation_registrations VALUES('operation','graph','{}','hash','api','{}',0,'then')")
        await db.conn.execute("INSERT INTO operation_revisions VALUES('operation',0,'Operation','{}','{}','','{}','then')")
        receipt = {'id': 'operation', 'evidence': [{'id': turn['slots'][0]['id']},
                                                {'id': other_turn['slots'][0]['id'], 'cause': 'unrelated evidence'}]}
        await db.conn.execute('INSERT INTO command_receipts VALUES(?,?,?,?)',
                              ('operation-update', 'fingerprint', json.dumps(receipt), 'then'))
        await db.conn.execute('INSERT INTO command_receipts VALUES(?,?,?,?)',
                              ('own-submission', 'fingerprint', json.dumps({'turn_id': turn['id']}), 'then'))
        records = await archives.snapshot(db, project['id'])
        assert [row['request_id'] for row in records['command_receipts']] == ['own-submission']
        assert [row['id'] for row in records['operation_registrations']] == ['operation']
        assert other_turn['id'] not in json.dumps(records)
        assert 'unrelated evidence' not in json.dumps(records)
    finally:
        await db.close()
