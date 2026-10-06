"""Restore project history into new identities and independently owned material.

Original requests, graphs and participant text remain historical facts. Only
editable preparations and structural references are remapped. Restored attempts
cannot dispatch, reroll, collect or reconnect to an old runtime; continuation
uses a newly reviewed preparation. Imported charges remain project history and
are not new installation spending. Restored deliveries cannot transfer files.
Unfinished attempts become skipped with their original status retained; sequences
become cancelled with original authorization and step records retained separately.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

from . import config
from .crexil_archives import PROJECT_TABLES, read_archive
from .db import new_id, now_iso
from .operation_registry import revision_id

MESSAGE = 'This is restored execution history. Reuse its preparation and review a new run to continue.'
ENTITIES = ('workspaces', 'turns', 'slots', 'nodes', 'regions', 'directions', 'mask_assets',
            'execution_groups', 'execution_outputs', 'sequences', 'operation_registrations', 'operation_inspections')
RELATIONS = {
    'workspace_id': 'workspaces', 'turn_id': 'turns', 'slot_id': 'slots', 'node_id': 'nodes',
    'parent_id': 'nodes', 'source_node_id': 'nodes', 'direction_id': 'directions',
    'group_id': 'execution_groups', 'sequence_id': 'sequences', 'registration_id': 'operation_registrations',
    'consumed_by_turn_id': 'turns', 'previous_mask_id': 'mask_assets',
}


def guard(slot: dict) -> None:
    if (slot.get('dials') or {}).get('crexil_restore'):
        raise ValueError(MESSAGE)


async def context(db, wid: str) -> dict | None:
    row = await db._one("SELECT payload_json FROM events WHERE workspace_id=? AND type='project.restored' "
                        "AND json_type(payload_json,'$.archive_origin') IS NULL ORDER BY seq DESC LIMIT 1", (wid,))
    return json.loads(row['payload_json']) if row else None


async def summary(db, wid: str) -> dict | None:
    restored = await context(db, wid)
    return ({key: restored[key] for key in ('source', 'identities', 'unavailable_files')} if restored else None)


async def resolve(db, wid: str, table: str, identity: str) -> str:
    origin = await context(db, wid)
    return origin.get('identities', {}).get(table, {}).get(identity, identity) if origin else identity


def _preparation(setup: dict, mapping: dict, operation_revisions: dict) -> dict:
    value = copy.deepcopy(setup)
    for reference in value.get('references', []):
        reference['node_id'] = mapping['nodes'].get(reference['node_id'], reference['node_id'])
    for placement in (value.get('image_preparation') or {}).get('items', []):
        placement['node_id'] = mapping['nodes'].get(placement['node_id'], placement['node_id'])
    for region in value.get('regions', []):
        region['mask_id'] = mapping['mask_assets'].get(region.get('mask_id'), region.get('mask_id'))
    for operation in value.get('operations', []):
        old = operation['operation_id'].removeprefix('custom-')
        if old in mapping['operation_registrations']:
            operation['operation_id'] = 'custom-' + mapping['operation_registrations'][old]
            revision = operation_revisions[old]
            if operation['revision'] == revision['previous']:
                operation['revision'] = revision['current']
    return value


def _prepared_rows(records: dict, manifest: dict, wid: str, name: str, directory: Path) -> tuple[dict, dict, dict]:
    from .material_files import files_from_origin
    rows = copy.deepcopy(records)
    mapping = {table: {row['id']: new_id() for row in rows[table]} for table in ENTITIES}
    original_project = records['workspaces'][0]['id']
    mapping['workspaces'][original_project] = wid
    # Accepted request JSON can still refer to identities preceding an earlier
    # restore. Compose aliases rather than rewriting that historical request.
    for event in records['events']:
        if event['type'] == 'project.restored':
            prior = json.loads(event['payload_json']).get('identities', {})
            for table in ENTITIES:
                for original, previous in prior.get(table, {}).items():
                    if previous in mapping[table]:
                        mapping[table].setdefault(original, mapping[table][previous])
    origin = {'version': 1, 'backup_id': manifest['id'], 'project_id': original_project,
              'created_at': manifest['created_at']}
    paths = {}
    def path_for(value):
        if not value:
            return value
        if value not in paths:
            old = Path(value)
            folder = hashlib.sha256(str(old.parent).encode()).hexdigest()[:24]
            paths[value] = str(directory / 'material' / folder / old.name)
        return paths[value]
    for entry in manifest['files']:
        path_for(entry['source'])
    original_nodes = {node['id']: node for node in records['nodes']}
    for material in records['material_info']:
        node = original_nodes[material['node_id']]
        root = Path(node['output_path']).parent.resolve()
        owned_root = Path(path_for(node['output_path'])).parent
        for retained in files_from_origin(node, json.loads(material['origin_json'])):
            if retained['path'] is not None:
                # Preserve nested owned directories, instead of hashing each
                # companion's parent into a folder beside its owning material.
                relative = Path(retained['path']).resolve().relative_to(root)
                paths[retained['path']] = str(owned_root / relative)
    for event in records['events']:
        if event['type'] == 'project.restored':
            for original, previous in json.loads(event['payload_json']).get('paths', {}).items():
                if previous in paths:
                    paths.setdefault(original, paths[previous])
    revisions = {}
    for registration in records['operation_registrations']:
        revision = next(row for row in records['operation_revisions']
                        if row['registration_id'] == registration['id'] and row['revision'] == registration['revision'])
        recorded = {'id': registration['id'], 'revision': registration['revision'],
                    'definition': json.loads(revision['definition_json']), 'binding': json.loads(revision['binding_json'])}
        revisions[registration['id']] = {
            'previous': revision_id(recorded),
            'current': revision_id({**recorded, 'id': mapping['operation_registrations'][registration['id']]})}
        # An unmatched revision remains stale, including an unknown original
        # runtime endpoint. Inspection must never silently upgrade preparation.
    delivery_ids = {json.loads(row['payload_json'])['id']: new_id() for row in records['events'] if row['type'] == 'delivery.prepared'}
    for table in PROJECT_TABLES:
        for row in rows[table]:
            old_id = row.get('id')
            if table in mapping:
                row['id'] = mapping[table][old_id]
            for column, entity in RELATIONS.items():
                if column in row and row[column] is not None:
                    row[column] = mapping[entity].get(row[column], row[column])
            for column in ('output_path', 'mask_path', 'original_path'):
                if column in row:
                    row[column] = path_for(row[column])
            if table == 'workspaces':
                row.update(name=name, slug='project-' + wid, created_at=now_iso(), updated_at=now_iso())
            elif table in ('nodes', 'slots'):
                dials = json.loads(row['dials_json'])
                previous = dials.get('crexil_restore')
                dials['crexil_restore'] = {**origin, 'original_id': old_id,
                    **({'previous': previous} if previous else {}),
                    **({'status': row['status'], 'kind': row['kind'], 'cause': row['cause']} if table == 'slots' else {})}
                row['dials_json'] = json.dumps(dials)
                if table == 'slots' and row['status'] in {'queued', 'running', 'held', 'late'}:
                    row.update(status='skipped', kind='restored',
                        cause=f"Archived while {dials['crexil_restore']['status']}. Its original outcome and spending record remain; this attempt was not resumed.")
            elif table == 'directions':
                row['setup_json'] = json.dumps(_preparation(json.loads(row['setup_json']), mapping, revisions))
                row['selection_json'] = json.dumps([mapping['nodes'].get(nid, nid) for nid in json.loads(row['selection_json'])])
            elif table == 'material_info':
                material = json.loads(row['origin_json'])
                for retained in material.get('retained_files', []):
                    retained['path'] = path_for(retained['path'])
                material['archive_origin'] = origin
                row['origin_json'] = json.dumps(material)
            elif table == 'execution_groups':
                metadata = json.loads(row['metadata_json'])
                metadata['archive_origin'] = {**origin, 'original_id': old_id}
                for key, entity in (('companion_owner_id', 'nodes'), ('primary_output_id', 'execution_outputs')):
                    if metadata.get(key):
                        metadata[key] = mapping[entity].get(metadata[key], metadata[key])
                if 'unresolved_output_ids' in metadata:
                    metadata['unresolved_output_ids'] = [mapping['execution_outputs'].get(oid, oid) for oid in metadata['unresolved_output_ids']]
                row['metadata_json'] = json.dumps(metadata)
            elif table == 'sequence_steps':
                def references(items):
                    for item in items:
                        for key, entity in (('node_id', 'nodes'), ('slot_id', 'slots'), ('output_id', 'execution_outputs')):
                            if item.get(key):
                                item[key] = mapping[entity].get(item[key], item[key])
                        if item.get('mesh_fields'):
                            field = item['mesh_fields']
                            field['path'] = path_for(field.get('path'))
                            field['output_id'] = mapping['execution_outputs'].get(field.get('output_id'), field.get('output_id'))
                def decision(value):
                    if value:
                        for selected in value.get('selected', {}).values():
                            references(selected)
                    return value
                binding = json.loads(row['binding_json']) if row['binding_json'] else None
                if binding:
                    references(binding.get('fixed_references', []))
                    for item in binding.get('inputs', []):
                        references(item.get('references', []))
                        references(item.get('sources', []))
                    decision(binding.get('decision'))
                    row['binding_json'] = json.dumps(binding)
                if row['decision_json']:
                    row['decision_json'] = json.dumps(decision(json.loads(row['decision_json'])))
                if row['state'] not in {'complete', 'cancelled'}:
                    row.update(state='cancelled', reason='Restored sequence history. Reuse this sequence to review new work.')
            elif table == 'sequences':
                row.update(state='cancelled', authorization_json=None)
            elif table == 'operation_inspections':
                # Preserve inspection history while making its runtime identity
                # ineligible for the new installation's readiness cache.
                row['runtime_url'] = 'archive:' + row['runtime_url']
            elif table == 'events':
                from .crexil_record import event_identity
                payload = json.loads(row['payload_json'])
                record_id = event_identity({'workspace_id': original_project, 'seq': row['seq'], 'payload': payload})
                payload['archive_origin'] = {**origin, 'event_seq': row['seq'], 'record_id': record_id}
                if row['type'] == 'delivery.prepared':
                    payload['id'] = delivery_ids[payload['id']]
                    payload['workspace_id'] = wid
                    for item in payload['items']:
                        item['node_id'] = mapping['nodes'][item['node_id']]
                        if item['state'] != 'complete':
                            item.update(requires_review=True, state='incomplete', error='Restored delivery history cannot transfer. Review a new delivery from the restored material.')
                if payload.get('delivery_id') in delivery_ids:
                    payload['delivery_id'] = delivery_ids[payload['delivery_id']]
                if row['type'] == 'delivery.item':
                    payload['node_id'] = mapping['nodes'][payload['node_id']]
                row['payload_json'] = json.dumps(payload)
                del row['seq']
    # These are historical receipts, never active idempotency permissions.
    rows['command_receipts'] = []
    return rows, {'identities': mapping, 'source': origin, 'paths': paths,
                  'historical_sequences': records['sequences'],
                  'historical_steps': records['sequence_steps']}, paths


def _copy_material(archive_path: Path, manifest: dict, paths: dict, directory: Path):
    with zipfile.ZipFile(archive_path) as archive:
        for entry in manifest['files']:
            if not entry['available']:
                continue
            target = Path(paths.get(entry['source']) or directory / 'retained' / entry['file'])
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError('A restored file would leave its project directory.')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry['file']) as source, target.open('xb') as out:
                digest, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > entry['size_bytes']:
                        raise ValueError('The archive material changed during restore.')
                    digest.update(chunk)
                    out.write(chunk)
                if size != entry['size_bytes'] or digest.hexdigest() != entry['sha256']:
                    raise ValueError('The archive material changed during restore.')


async def restore(db, archive_path: Path, *, name: str | None, actor: dict, expected_sha256: str | None = None) -> dict:
    """Caller commits these rows with its idempotent restore command receipt."""
    if expected_sha256 is not None:
        from .crexil_archives import _sha
        try:
            actual = await asyncio.to_thread(_sha, archive_path)
        except OSError as exc:
            raise ValueError('The uploaded backup file is unavailable. Choose and upload it again.') from exc
        if actual != expected_sha256:
            raise ValueError('The uploaded backup changed after review. Choose and review it again.')
    manifest, records = await asyncio.to_thread(read_archive, archive_path)
    wid = new_id()
    directory = config.STAGING_DIR / 'restored' / wid
    directory.mkdir(parents=True)
    try:
        rows, origin, paths = _prepared_rows(records, manifest, wid, name or manifest['project_name'] + ' (restored)', directory)
        job = asyncio.create_task(asyncio.to_thread(_copy_material, archive_path, manifest, paths, directory))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise
        # Regions and mask revisions can refer to rows later in their own table.
        # The shared command transaction checks all references together at commit.
        await db.conn.execute('PRAGMA defer_foreign_keys=ON')
        for table in PROJECT_TABLES:
            for row in rows[table]:
                columns = ','.join(row)
                await db.conn.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in row)})', tuple(row.values()))
        if await db._all('PRAGMA foreign_key_check'):
            raise ValueError('The restored project has a missing relationship.')
        await db.append_event('project.restored', workspace_id=wid,
            payload={**origin, 'actor': actor, 'original_project': records['workspaces'][0],
                     'historical_receipts': records['command_receipts'],
                     'unavailable_files': [entry for entry in manifest['files'] if not entry['available']]})
        return {'project_id': wid, 'name': name or manifest['project_name'] + ' (restored)',
                'unavailable_files': sum(not entry['available'] for entry in manifest['files'])}
    except BaseException as exc:
        shutil.rmtree(directory)
        if isinstance(exc, (TypeError, KeyError, AttributeError, sqlite3.IntegrityError)):
            raise ValueError('The backup contains records that cannot form a supported project.') from exc
        raise


async def reserve_upload(db, body) -> dict:
    """Reserve an attributed upload before accepting any binary bytes."""
    from .crexil_archives import MAX_UNPACKED_BYTES, MAX_DIRECTORY_BYTES
    if body.size_bytes > MAX_UNPACKED_BYTES + MAX_DIRECTORY_BYTES:
        raise ValueError('This archive exceeds the supported upload size.')
    item = {'id': new_id(), 'filename': body.filename, 'size_bytes': body.size_bytes,
            'actor': body.actor.model_dump(), 'created_at': now_iso()}
    await db.append_event('archive.reserved', payload=item)
    return {**item, 'state': 'waiting', 'review': None, 'sha256': None}


async def upload_state(db, uid: str) -> dict:
    row = await db._one("SELECT payload_json FROM events WHERE type='archive.reserved' "
                        "AND json_extract(payload_json,'$.id')=? AND workspace_id IS NULL", (uid,))
    if not row:
        raise LookupError('This backup upload could not be found. Choose the file again.')
    item = json.loads(row['payload_json'])
    accepted = await db._one("SELECT payload_json FROM events WHERE type='archive.uploaded' "
                             "AND json_extract(payload_json,'$.id')=? AND workspace_id IS NULL", (uid,))
    return {**item, 'state': 'ready' if accepted else 'waiting',
            **(json.loads(accepted['payload_json']) if accepted else {'review': None, 'sha256': None})}


def upload_path(uid: str) -> Path:
    # IDs are looked up before using this path; still refuse separators locally.
    if not uid.isalnum():
        raise ValueError('This upload identity is invalid.')
    return config.STAGING_DIR / 'archive-uploads' / uid / 'project.crexil.zip'


# Upload and restore serialize per reserved identity, independently of DB writes.
_upload_locks: dict[str, asyncio.Lock] = {}


def upload_lock(uid: str):
    return _upload_locks.setdefault(uid, asyncio.Lock())


async def accept_upload(db, uid: str, stream) -> dict:
    """Validate all records and bytes before an immutable upload becomes ready."""
    saved = await upload_state(db, uid)
    path = upload_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.' + new_id() + '.upload')
    digest, size = hashlib.sha256(), 0
    try:
        with temporary.open('xb') as output:
            async for chunk in stream:
                size += len(chunk)
                if size > saved['size_bytes']:
                    raise ValueError('The uploaded file is larger than the reserved backup.')
                digest.update(chunk)
                job = asyncio.create_task(asyncio.to_thread(output.write, chunk))
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    await job
                    raise
        if size != saved['size_bytes']:
            raise ValueError('The backup upload is incomplete. Choose the same file and retry.')
        sha = digest.hexdigest()
        if saved['state'] == 'ready':
            if sha != saved['sha256']:
                raise ValueError('This upload already holds a different backup. Choose a new upload.')
            return saved
        job = asyncio.create_task(asyncio.to_thread(read_archive, temporary))
        try:
            manifest, _ = await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise
        review = {key: manifest[key] for key in ('id', 'project_id', 'project_name', 'created_at', 'records')}
        review['unavailable_files'] = [entry for entry in manifest['files'] if not entry['available']]
        review['available_files'] = sum(entry['available'] for entry in manifest['files'])
        review['available_bytes'] = sum(entry['size_bytes'] or 0 for entry in manifest['files'])
        temporary.replace(path)
        async with db._tx():
            await db.append_event('archive.uploaded', payload={'id': uid, 'sha256': sha, 'review': review})
        return await upload_state(db, uid)
    finally:
        temporary.unlink(missing_ok=True)
