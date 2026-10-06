"""Portable project records and material, separate from delivery manifests.

A backup contains one sealed SQLite record, independently copied material and
an inventory of unavailable files. It preserves original history and never
copies installation settings or model weights. Supplied project text is retained. The sealed database cannot become a
live store through ordinary startup; restoring it is an explicit server action.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from functools import lru_cache
from pathlib import Path, PurePosixPath

from . import config
from .crexil_store import SCHEMA as CREATIVE_SCHEMA, CreativeStore
from .db import SCHEMA, SCHEMA_VERSION, new_id, now_iso
from .mesh import fields_sidecar

FORMAT_VERSION = 1
# Writer and reader share these limits: a successful backup is always readable.
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_RECORDS = 250_000
MAX_DIRECTORY_BYTES = 64 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
PROJECT_TABLES = (
    'workspaces', 'project_details', 'turns', 'slots', 'nodes', 'node_parents',
    'regions', 'directions', 'direction_turns', 'direction_material',
    'material_info', 'mask_assets', 'execution_groups', 'execution_outputs',
    'sequences', 'sequence_steps', 'sequence_runs', 'events',
    'operation_registrations', 'operation_revisions', 'operation_inspections',
    'command_receipts',
)


class _ArchiveLimit(ValueError):
    pass


def _sha(path: Path) -> str:
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def _stream(source, *, limit: int, label: str, target=None) -> tuple[int, str]:
    if limit < 0:
        raise _ArchiveLimit(f'{label} exceeds the archive size limit.')
    size, digest = 0, hashlib.sha256()
    while chunk := source.read(min(COPY_CHUNK_BYTES, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise _ArchiveLimit(f'{label} exceeds the archive size limit.')
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return size, digest.hexdigest()


def _member_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(name and '\\' not in name and '\x00' not in name and ':' not in name
                and not name.startswith('/') and all(part not in ('', '.', '..') for part in parts)
                and '/'.join(parts) == name)


def _source_name(source: str) -> bool:
    return isinstance(source, str) and '\x00' not in source and _member_name(PurePosixPath(source).name)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'The archive manifest repeats the field {key!r}.')
        result[key] = value
    return result


def _digest_value(value) -> bool:
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


@lru_cache(maxsize=1)
def _trusted_layout():
    """Only application-owned DDL defines the accepted archive schema."""
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.executescript(SCHEMA)
        objects = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        columns = {table: [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                   for table in PROJECT_TABLES}
    return objects, columns


def _read_database(path: Path, manifest: dict) -> dict[str, list[dict]]:
    # Never open an archive with Database.open(), execute its SQL, or use names
    # taken from its schema to construct queries. The temporary file is private.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA query_only=ON')
        objects, columns = _trusted_layout()
        actual = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        if actual != objects:
            raise ValueError('The archive database schema does not match the supported project format.')
        if conn.execute('PRAGMA quick_check(1)').fetchall() != [('ok',)]:
            raise ValueError('The archive database is damaged.')
        if dict(conn.execute('SELECT key,value FROM meta')) != {'schema_version': SCHEMA_VERSION, 'archive_sealed': '1'}:
            raise ValueError('The archive database must be sealed and carry the supported schema version.')
        records = {}
        for table, names in columns.items():
            if [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')] != names:
                raise ValueError(f'The archive has unsupported columns in {table}.')
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count != manifest['records'][table]:
                raise ValueError(f'The archive record count does not match {table}.')
            projection = ','.join(f'"{name}"' for name in names)
            records[table] = [dict(zip(names, row, strict=True))
                              for row in conn.execute(f'SELECT {projection} FROM "{table}"')]
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('The archive has a missing or cross-project record relationship.')
        if conn.execute('SELECT COUNT(*) FROM model_hashes').fetchone()[0]:
            raise ValueError('The archive contains installation model-cache records.')
        search = conn.execute('SELECT node_id,prompt FROM node_fts ORDER BY node_id').fetchall()
        if search != sorted((row['id'], row['prompt']) for row in records['nodes']):
            raise ValueError('The archive search index contains unrelated material.')
    projects = records['workspaces']
    if len(projects) != 1 or projects[0]['id'] != manifest['project_id'] or projects[0]['name'] != manifest['project_name']:
        raise ValueError('The archive must contain exactly the project named in its manifest.')
    for table, rows in records.items():
        for row in rows:
            if 'workspace_id' in row and row['workspace_id'] != manifest['project_id']:
                raise ValueError(f'The archive contains unrelated project records in {table}.')
            for key, value in row.items():
                if key.endswith('_json') and value is not None:
                    try:
                        json.loads(value)
                    except (TypeError, ValueError, RecursionError) as exc:
                        raise ValueError(f'The archive has invalid JSON in {table}.{key}.') from exc
    return records


def read_archive(path: Path) -> tuple[dict, dict[str, list[dict]]]:
    """Validate a sealed project ZIP and return exact manifest and record values.

    Material is streamed for verification, never extracted. Only the bounded
    SQLite member is copied to a private temporary directory and opened read-only.
    Archive SQL, paths and request text are never executed or rewritten.
    """
    try:
        return _read_archive(Path(path))
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, sqlite3.Error,
            UnicodeError, RecursionError, NotImplementedError, RuntimeError,
            TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f'This project archive could not be read: {exc}') from exc


def _validate_directory(path: Path) -> None:
    # The stdlib footer parser handles ZIP64 without constructing one Python
    # object per directory member. Bound that allocation before ZipFile opens.
    with path.open('rb') as source:
        footer = zipfile._EndRecData(source)
        if footer is None:
            raise ValueError('This file is not a complete project ZIP archive.')
        remaining = footer[zipfile._ECD_SIZE]
        if footer[zipfile._ECD_ENTRIES_TOTAL] > MAX_MEMBERS or remaining > MAX_DIRECTORY_BYTES:
            raise ValueError('The archive exceeds the supported member or directory size limit.')
        start = footer[zipfile._ECD_OFFSET]
        if start < 0:
            raise ValueError('The archive ZIP directory is invalid.')
        source.seek(start)
        count = 0
        while remaining:
            header = source.read(46)
            if len(header) != 46 or header[:4] != b'PK\x01\x02':
                raise ValueError('The archive ZIP directory is damaged.')
            length = 46 + sum(int.from_bytes(header[index:index + 2], 'little') for index in (28, 30, 32))
            if length > remaining:
                raise ValueError('The archive ZIP directory is truncated.')
            remaining -= length
            source.seek(length - 46, 1)
            count += 1
            if count > MAX_MEMBERS:
                raise ValueError('The archive exceeds the supported member limit.')
        if count != footer[zipfile._ECD_ENTRIES_TOTAL]:
            raise ValueError('The archive ZIP directory does not match its declared member count.')


def _read_archive(path: Path) -> tuple[dict, dict[str, list[dict]]]:
    _validate_directory(path)
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS or sum(member.file_size for member in members) > MAX_UNPACKED_BYTES:
            raise ValueError('The archive exceeds the supported member or unpacked size limit.')
        by_name = {}
        names = set()
        for member in members:
            name = member.filename
            if not _member_name(name) or member.orig_filename != name or name.casefold() in names:
                raise ValueError('The archive has an unsafe or duplicate member path.')
            mode = stat.S_IFMT(member.external_attr >> 16)
            if member.is_dir() or mode not in (0, stat.S_IFREG) or member.flag_bits & 0x41:
                raise ValueError('Archive members must be ordinary, unencrypted files; links are not allowed.')
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError('The archive uses an unsupported compression method.')
            by_name[name] = member
            names.add(name.casefold())
        if not {'manifest.json', 'project.sqlite'} <= by_name.keys():
            raise ValueError('The archive is missing its manifest or project database.')
        if by_name['manifest.json'].file_size > MAX_MANIFEST_BYTES or by_name['project.sqlite'].file_size > MAX_DATABASE_BYTES:
            raise ValueError('The archive manifest or database exceeds the supported size limit.')
        try:
            manifest = json.loads(archive.read('manifest.json'), object_pairs_hook=_json_object)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError(f'The archive manifest is not valid JSON: {exc}') from exc
        required = {'id', 'project_id', 'project_name', 'created_at', 'actor', 'format', 'version',
                    'schema_version', 'database_sha256', 'records', 'files'}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError('The archive manifest has missing or unsupported fields.')
        if (manifest['format'] != 'crexil-project' or type(manifest['version']) is not int
                or manifest['version'] != FORMAT_VERSION or type(manifest['schema_version']) is not int
                or manifest['schema_version'] != int(SCHEMA_VERSION)):
            raise ValueError('This project archive format or schema version is not supported.')
        if (any(not isinstance(manifest[key], str) or not manifest[key] for key in ('id', 'project_id', 'project_name', 'created_at'))
                or not isinstance(manifest['actor'], dict) or not _digest_value(manifest['database_sha256'])):
            raise ValueError('The archive manifest has invalid project identity or database hash.')
        counts = manifest['records']
        if (not isinstance(counts, dict) or set(counts) != set(PROJECT_TABLES)
                or any(type(count) is not int or count < 0 for count in counts.values())
                or sum(counts.values()) > MAX_RECORDS or counts['workspaces'] != 1):
            raise ValueError('The archive must inventory the supported tables for one project within the record limit.')
        with tempfile.TemporaryDirectory(prefix='crexil-archive-read-') as temporary:
            database = Path(temporary) / 'project.sqlite'
            with archive.open('project.sqlite') as source, database.open('xb') as target:
                size, digest = _stream(source, limit=MAX_DATABASE_BYTES, label='The archive database', target=target)
            if size != by_name['project.sqlite'].file_size or digest != manifest['database_sha256']:
                raise ValueError('The archive database does not match its recorded content hash or size.')
            records = _read_database(database, manifest)
        _validate_files(archive, by_name, manifest, records)
        return manifest, records


def _validate_files(archive, by_name: dict, manifest: dict, records: dict) -> None:
    files = manifest['files']
    if not isinstance(files, list) or len(files) > MAX_MEMBERS - 2:
        raise ValueError('The archive file inventory is invalid or exceeds the supported limit.')
    required = {item['source']: item for item in _files(records, optional_files=False)}
    optional = {str(Path(node['output_path']).with_suffix(suffix)): node['id']
                for node in records['nodes'] if not node['purged_at']
                for suffix in ('.thumb.webp', '.turntable.webp')}
    optional.update({str(fields_sidecar(node['output_path'])): node['id'] for node in records['nodes'] if not node['purged_at']})
    owners = {row['id'] for table in ('nodes', 'regions', 'mask_assets', 'execution_outputs', 'slots')
              for row in records[table]}
    disclosed, sources = {'manifest.json', 'project.sqlite'}, set()
    keys = {'source', 'sha256', 'purged', 'role', 'owners', 'file', 'available', 'size_bytes', 'error'}
    for item in files:
        if not isinstance(item, dict) or not keys <= item.keys() or item.keys() - keys - {'conflict'}:
            raise ValueError('The archive file inventory has missing or unsupported fields.')
        source = item['source']
        if not _source_name(source) or source in sources:
            raise ValueError('The archive file inventory repeats or omits a source path.')
        sources.add(source)
        expected = required.get(source)
        if expected is None and source not in optional:
            raise ValueError('The archive inventories a file outside its project records.')
        if (type(item['available']) is not bool or type(item['purged']) is not bool
                or not isinstance(item['role'], str) or not isinstance(item['owners'], list)
                or any(not isinstance(owner, str) for owner in item['owners'])
                or len(set(item['owners'])) != len(item['owners'])
                or not set(item['owners']) <= owners
                or item['sha256'] is not None and not _digest_value(item['sha256'])):
            raise ValueError('The archive file inventory has invalid ownership, availability or hash values.')
        if expected is None and (optional[source] not in item['owners'] or item['purged']):
            raise ValueError('The archive companion inventory disagrees with its project ownership.')
        if expected and (item['purged'] != expected['purged']
                         or expected['sha256'] and item['sha256'] != expected['sha256']
                         or not set(expected['owners']) <= set(item['owners'])
                         or bool(item.get('conflict')) != bool(expected.get('conflict'))):
            raise ValueError('The archive file inventory disagrees with its project records.')
        if not item['available']:
            if item['file'] is not None or item['size_bytes'] is not None or not isinstance(item['error'], str) or not item['error']:
                raise ValueError('An unavailable archive file must retain its missing-file explanation without packaged bytes.')
            continue
        name = item['file']
        if (item['purged'] or item.get('conflict') or item['error'] is not None or not _digest_value(item['sha256'])
                or expected and (expected.get('_receipt_error') or expected.get('_receipt_size') is not None
                                 and item['size_bytes'] != expected['_receipt_size'])
                or type(item['size_bytes']) is not int or item['size_bytes'] < 0
                or not isinstance(name, str) or not _member_name(name)
                or not re.fullmatch(r'material/[0-9]+/[^/]+', name)
                or name not in by_name or name in disclosed):
            raise ValueError('An available archive file has invalid content, ownership or member details.')
        disclosed.add(name)
        if by_name[name].file_size != item['size_bytes']:
            raise ValueError(f'Archive material {name} does not match its recorded size.')
        with archive.open(name) as stream:
            size, digest = _stream(stream, limit=item['size_bytes'], label=f'Archive material {name}')
        if size != item['size_bytes'] or digest != item['sha256']:
            raise ValueError(f'Archive material {name} does not match its recorded content hash.')
    if not required.keys() <= sources:
        raise ValueError('The archive omits required project files from its inventory.')
    if set(by_name) != disclosed:
        raise ValueError('The archive contains undisclosed members.')


def _values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)
    elif isinstance(value, str):
        yield value


async def snapshot(db, wid: str) -> dict[str, list[dict]]:
    """Caller owns the transaction through material copying and receipt commit."""
    await CreativeStore(db).project(wid)
    records: dict[str, list[dict]] = {}
    async def select(table: str, where: str, args):
        records[table] = [dict(row) for row in await db._all(f'SELECT * FROM {table} WHERE {where}', args)]
    async def related(table: str, column: str, values):
        values = list(values)
        if not values:
            records[table] = []
        else:
            await select(table, column + ' IN (' + ','.join('?' for _ in values) + ')', values)
    await select('workspaces', 'id=?', (wid,))
    for table in ('project_details', 'turns', 'nodes', 'directions', 'mask_assets', 'sequences'):
        await select(table, 'workspace_id=?', (wid,))
    def ids(table):
        return [row['id'] for row in records[table]]
    await related('slots', 'turn_id', ids('turns'))
    await related('node_parents', 'node_id', ids('nodes'))
    await related('regions', 'node_id', ids('nodes'))
    await related('material_info', 'node_id', ids('nodes'))
    await related('direction_turns', 'direction_id', ids('directions'))
    await related('direction_material', 'direction_id', ids('directions'))
    await related('execution_groups', 'slot_id', ids('slots'))
    await related('execution_outputs', 'group_id', ids('execution_groups'))
    await related('sequence_steps', 'sequence_id', ids('sequences'))
    await related('sequence_runs', 'sequence_id', ids('sequences'))
    await select('events', 'workspace_id=?', (wid,))
    records['events'].sort(key=lambda row: row['seq'])
    operations = {row['engine'] for row in records['slots']}
    for direction in records['directions']:
        operations.update(choice.get('operation_id', '') for choice in json.loads(direction['setup_json']).get('operations', []))
    registrations = {op.removeprefix('custom-') for op in operations if op.startswith('custom-')}
    await related('operation_registrations', 'id', registrations)
    await related('operation_revisions', 'registration_id', registrations)
    await related('operation_inspections', 'registration_id', registrations)
    global_tables = {'operation_registrations', 'operation_revisions', 'operation_inspections'}
    known = {str(row['id']) for table, rows in records.items() if table not in global_tables for row in rows if 'id' in row}
    global_ids = {row['id'] for table in global_tables for row in records[table] if 'id' in row}
    records['command_receipts'] = []
    for receipt in await db._all('SELECT * FROM command_receipts ORDER BY created_at'):
        response = json.loads(receipt['response_json'])
        # Registration responses include evidence from every project using the
        # operation. Its source/revisions are captured above without that view.
        if isinstance(response, dict) and response.get('id') in global_ids:
            continue
        if known.intersection(_values(response)):
            records['command_receipts'].append(dict(receipt))
    return records


def _files(records: dict, *, optional_files=True) -> list[dict]:
    """Enumerate owned files and typed frozen inputs, never nested source history."""
    from .material_files import files_from_origin
    found: dict[str, dict] = {}
    info = {row['node_id']: row for row in records['material_info']}
    nodes = {row['id']: row for row in records['nodes']}
    groups = {row['id']: json.loads(row['metadata_json']) for row in records['execution_groups']}
    restored_paths = {}
    for event in sorted(records['events'], key=lambda row: row['seq'], reverse=True):
        if event['type'] != 'project.restored':
            continue
        payload = json.loads(event['payload_json'])
        if 'archive_origin' not in payload:
            restored_paths = payload.get('paths', {})
            break
    def frozen_path(path):
        seen = set()
        while path in restored_paths:
            target = restored_paths[path]
            if target == path:
                return path
            if path in seen:
                raise ValueError('The restored project has a loop in its material path history.')
            seen.add(path)
            path = target
        return path
    def add(path, digest=None, *, purged=False, role='material', owner=None, size=None, error=None):
        if not path:
            return
        item = found.setdefault(str(path), {'source': str(path), 'sha256': digest, 'purged': purged,
                                           'role': role, 'owners': []})
        if digest:
            if item['sha256'] and item['sha256'] != digest:
                item['conflict'] = True
            item['sha256'] = digest
        item['purged'] |= purged
        if size is not None:
            if item.get('_receipt_size') is not None and item['_receipt_size'] != size:
                item['conflict'] = True
            item['_receipt_size'] = size
        if error:
            item['_receipt_error'] = error
        if owner and owner not in item['owners']:
            item['owners'].append(owner)
    for node in records['nodes']:
        add(node['output_path'], info.get(node['id'], {}).get('sha256'), purged=bool(node['purged_at']), owner=node['id'])
        origin = json.loads(info.get(node['id'], {}).get('origin_json', '{}'))
        for retained in files_from_origin(node, origin):
            # A null path has no physical archive member. Its identity and
            # explicit error remain in the unchanged material origin record.
            add(retained['path'], retained['sha256'], purged=bool(node['purged_at']),
                role=retained['role'], owner=node['id'], size=retained['size_bytes'], error=retained['error'])
        if not node['purged_at']:
            original = Path(node['output_path'])
            for path in (original.with_suffix('.thumb.webp'), original.with_suffix('.turntable.webp'), fields_sidecar(original)):
                if optional_files and path.is_file():
                    add(path, role='companion' if path == fields_sidecar(original) else 'preview', owner=node['id'])
    for region in records['regions']:
        add(region['mask_path'], region['mask_sha256'], purged=bool(nodes[region['node_id']]['purged_at']), role='region mask', owner=region['id'])
    for mask in records['mask_assets']:
        add(mask['mask_path'], mask['sha256'], role='saved mask', owner=mask['id'])
        add(mask['original_path'], mask['original_sha256'], role='supplied mask', owner=mask['id'])
    for output in records['execution_outputs']:
        owner = output['node_id'] or (groups[output['group_id']].get('companion_owner_id') if output['role'] == 'companion' else None)
        add(output['output_path'], output['sha256'], purged=bool(nodes.get(owner, {}).get('purged_at')), role=output['role'], owner=output['id'])
    for row in records['slots'] + records['nodes']:
        graph = json.loads(row['submitted_graph_json'] or '{}')
        plan = graph.get('region_plan', {}) if isinstance(graph, dict) else {}
        refs = [plan.get('source'), plan.get('keep')]
        refs += [part.get('mask') for part in plan.get('passes', [])]
        for ref in refs:
            if isinstance(ref, dict):
                add(frozen_path(ref.get('path')), ref.get('sha256'), role='frozen region input', owner=row['id'])
        job = graph.get('mesh_job', {}) if isinstance(graph, dict) else {}
        for ref in job.get('source_hashes', []):
            add(frozen_path(ref.get('path')), ref.get('sha256'), role='frozen mesh input', owner=row['id'])
    return list(found.values())


def _write_database(path: Path, records: dict) -> None:
    if sum(len(rows) for rows in records.values()) > MAX_RECORDS:
        raise ValueError('This project exceeds the archive record limit.')
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA + CREATIVE_SCHEMA)
        conn.executemany('INSERT INTO meta VALUES(?,?)', [('schema_version', SCHEMA_VERSION), ('archive_sealed', '1')])
        for table in PROJECT_TABLES:
            for row in records[table]:
                columns = ','.join(row)
                conn.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in row)})', tuple(row.values()))
        violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise ValueError('The project has relationships outside its record. Repair those links before backing it up.')
    if path.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError('This project exceeds the archive database size limit.')


def _write_archive(destination: Path, records: dict, identity: dict) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = []
    with tempfile.TemporaryDirectory(prefix='archive-', dir=destination.parent) as temporary:
        directory = Path(temporary)
        database = directory / 'project.sqlite'
        _write_database(database, records)
        total = database.stat().st_size
        for index, item in enumerate(_files(records)):
            if not _source_name(item['source']):
                raise ValueError('A project source has an unsafe archive filename. Repair its record before backing it up.')
            path = Path(item['source'])
            entry = {**{key: value for key, value in item.items() if not key.startswith('_receipt_')},
                     'file': None, 'available': False, 'size_bytes': None, 'error': None}
            files.append(entry)
            if item['purged']:
                entry['error'] = 'Purged material; its record is retained.'
                continue
            if item.get('conflict'):
                entry['error'] = 'Recorded content hashes disagree.'
                continue
            if item.get('_receipt_error'):
                entry['error'] = item['_receipt_error']
                continue
            if not path.resolve().is_relative_to(config.STAGING_DIR.resolve()):
                entry['error'] = 'The recorded file is outside this installation\'s material folder.'
                continue
            local = directory / 'material' / str(index) / path.name
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                if not _member_name(local.relative_to(directory).as_posix()):
                    raise ValueError('The original filename cannot be represented as a safe archive member.')
                with path.open('rb') as source, local.open('xb') as target:
                    size, digest = _stream(source, limit=MAX_UNPACKED_BYTES - total - MAX_MANIFEST_BYTES,
                                           label='Project material', target=target)
                if item['sha256'] and digest != item['sha256']:
                    raise ValueError('The file does not match its recorded content hash.')
                if item.get('_receipt_size') is not None and size != item['_receipt_size']:
                    raise ValueError('The file does not match its recorded byte count.')
                entry.update(file=local.relative_to(directory).as_posix(), available=True,
                             sha256=digest, size_bytes=size)
                total += size
            except _ArchiveLimit:
                raise
            except (OSError, ValueError) as exc:
                local.unlink(missing_ok=True)
                entry['error'] = str(exc)
        manifest = {**identity, 'format': 'crexil-project', 'version': FORMAT_VERSION,
                    'schema_version': int(SCHEMA_VERSION), 'database_sha256': _sha(database),
                    'records': {table: len(rows) for table, rows in records.items()}, 'files': files}
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        if len(manifest_bytes) > MAX_MANIFEST_BYTES or 2 + len(files) > MAX_MEMBERS:
            raise ValueError('This project exceeds the archive inventory limit.')
        if total + len(manifest_bytes) > MAX_UNPACKED_BYTES:
            raise ValueError('This project exceeds the archive unpacked size limit.')
        with zipfile.ZipFile(directory / 'project.crexil.zip', 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.write(database, 'project.sqlite')
            archive.writestr('manifest.json', manifest_bytes)
            for entry in files:
                if entry['available']:
                    archive.write(directory / entry['file'], entry['file'])
        _validate_directory(directory / 'project.crexil.zip')
        (directory / 'project.crexil.zip').replace(destination)
    return {**manifest, 'sha256': _sha(destination), 'size_bytes': destination.stat().st_size}


async def create(db, wid: str, actor: dict) -> dict:
    """Create under the shared command transaction; no engine work is involved."""
    records = await snapshot(db, wid)
    bid = new_id()
    path = config.STAGING_DIR / 'backups' / bid / 'project.crexil.zip'
    identity = {'id': bid, 'project_id': wid, 'project_name': records['workspaces'][0]['name'],
                'created_at': now_iso(), 'actor': actor}
    try:
        job = asyncio.create_task(asyncio.to_thread(_write_archive, path, records, identity))
        try:
            result = await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise
        await db.append_event('project.backed_up', workspace_id=wid, payload={**result, 'path': str(path)})
        return result
    except BaseException:
        path.unlink(missing_ok=True)
        raise
