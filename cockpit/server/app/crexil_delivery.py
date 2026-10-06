"""Deliver immutable material through attributed, recoverable server commands.

The existing event ledger holds frozen destinations, manifests and file hashes
before any transfer. Retries reconcile the same asset identities; only a new
delivery intent creates another copy. Each item succeeds or fails independently.
Completed receipts are historical facts, not claims that an external file still
exists. Inspection never transfers, and recovery never regenerates material.
An item refused during review needs a new review after repair; healthy items
still transfer. Purge prevents later copies, but a copy already started may
finish and record its verified destination bytes after the source is purged.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import shutil
from pathlib import Path
from weakref import WeakKeyDictionary

from . import blind, manifests, promote
from .api_v4_promote import _folder_for, _root
from .crexil_store import Conflict, CreativeStore
from .db import new_id, now_iso

_locks = WeakKeyDictionary()


def _hash(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _file(path: Path, relative: str) -> dict:
    return {'source': str(path), 'relative': relative, 'sha256': _hash(path), 'size_bytes': path.stat().st_size}


def _view_item(item: dict, destination: str) -> dict:
    return {key: item[key] for key in ('node_id', 'filename', 'asset_id', 'state', 'error', 'missing', 'completed_at')} | {
        'requires_review': item.get('requires_review', False),
        'files': [{'path': str(Path(destination) / item['folder'] / f['relative']),
                   'sha256': f['sha256'], 'size_bytes': f['size_bytes']} for f in item['files']]}


async def _plans(db, wid: str, body) -> tuple[str, list[dict]]:
    workspace = await CreativeStore(db).project(wid)
    if len(set(body.node_ids)) != len(body.node_ids):
        raise ValueError('Choose each material once in a delivery.')
    root = promote.from_windows(body.destination) if body.destination.strip() else str((await _root(db))[0])
    if not Path(root).is_absolute():
        raise ValueError('Choose the full destination path.')
    root = str(Path(root).resolve())
    items = []
    for nid in body.node_ids:
        node = await db.get_node(nid)
        if not node or node['workspace_id'] != wid:
            raise ValueError('Every delivered item must belong to this project.')
        info = await db._one('SELECT filename FROM material_info WHERE node_id=?', (nid,))
        asset_id = 'review-' + nid
        item = {'node_id': nid, 'filename': info['filename'] if info else Path(node['output_path']).name,
                'asset_id': asset_id, 'folder': _folder_for(node, workspace), 'state': 'prepared',
                'error': None, 'missing': [], 'completed_at': None, 'files': [], 'source_nodes': [nid],
                'requires_review': False}
        items.append(item)
        try:
            if node['purged_at'] or not Path(node['output_path']).is_file():
                raise ValueError('The original file is unavailable. Its record remains in the project.')
            if await blind.is_hidden(db, node['turn_id']):
                raise ValueError('This take belongs to a blind run. Reveal the run before delivering it, '
                                 'so its manifest can name its engine.')
            turn = await db.get_turn(node['turn_id']) if node['turn_id'] else None
            slot = await db.get_slot(node['slot_id']) if node['slot_id'] else None
            parents = [await db.get_node(pid) for pid in node['parents']]
            orbit = Path(node['output_path']).with_suffix('.turntable.webp')
            manifest, extras = await manifests.build_v4_manifest(db, asset_id=asset_id,
                workspace=workspace, node=node, turn=turn, slot=slot,
                parents=[p for p in parents if p], turntable=orbit if orbit.is_file() else None)
            outputs = (manifest.get('execution') or {}).get('outputs', [])
            required = ((node.get('dials') or {}).get('crexil') or {}).get('output_ports', [])
            absent = [member['filename'] for member in outputs if (not member['available'] or member.get('error'))
                      and member['descriptor'].get('declared_output', {}).get('required', member['role'] == 'companion')]
            related = (manifest.get('material') or {}).get('related_files', [])
            absent += [member['filename'] for member in related
                       if member['required'] and (not member['available'] or member.get('error'))]
            for port in required:
                if port.get('required') and not any(
                    member['available'] and not member.get('error')
                    and member['descriptor'].get('declared_output') == port for member in outputs
                ):
                    absent.append(f"{port['role']} from node {port['node_id']} ({port['field']})")
            if absent:
                raise ValueError('Required related material is unavailable: ' + ', '.join(absent)
                                 + '. Collect the missing files, then review this material again.')
            manifest['created_at'] = None  # Filled once when the intent is accepted.
            primary = f"{asset_id}{Path(node['output_path']).suffix or '.png'}"
            paths = [(Path(node['output_path']), primary), *extras]
            for index, (path, relative) in enumerate(paths):
                await asyncio.to_thread(promote._verify_collected_copy, path, relative, manifest, primary=index == 0)
                item['files'].append(await asyncio.to_thread(_file, path, relative))
            item.update(manifest=manifest, primary=primary)
            content = json.dumps(manifest, indent=2).encode()
            item['files'].append({'source': None, 'relative': asset_id + '.json',
                                  'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content)})
            for parent in manifest['input_images']:
                if parent['available']:
                    item['source_nodes'].append(parent['node_id'])
                else:
                    item['missing'].append(f"Source {parent['node_id']} is unavailable; its record will be included.")
            for member in outputs + related:
                if member['available'] and member.get('node_id'):
                    item['source_nodes'].append(member['node_id'])
                if not member['available']:
                    item['missing'].append(f"Related file {member['filename']} is unavailable; its record will be included.")
        except (OSError, ValueError, promote.PromoteMaterialError) as exc:
            item.update(state='incomplete', error=str(exc), requires_review=True, files=[])
    return root, items


async def review(db, wid: str, body) -> dict:
    async with db._locked():
        root, items = await _plans(db, wid, body)
        target = await asyncio.to_thread(promote.target_state, Path(root))
        errors = [f"{item['filename']}: {item['error']}" for item in items if item['error']]
        if not target['ok']:
            errors.append(f"{target['message']}. {target['remedy']}")
        return {'token': _digest([root, items]), 'destination': root, 'target': target,
                'ready': target['ok'] and any(not item['requires_review'] for item in items),
                'errors': errors, 'items': [_view_item(item, root) for item in items]}


async def prepare(db, wid: str, body) -> dict:
    current = await review(db, wid, body)
    if current['token'] != body.review_token:
        raise Conflict('The material or delivery context changed. Review this delivery again.')
    if not current['ready']:
        raise ValueError(' '.join(current['errors']))
    root, items = await _plans(db, wid, body)
    if _digest([root, items]) != body.review_token:
        raise Conflict('The material changed while preparing delivery. Review it again.')
    did, created = new_id(), now_iso()
    for item in items:
        old, asset = item['asset_id'], new_id()
        # Only server-generated destination names change. User text, source
        # paths and frozen operation data retain their exact original values.
        item['asset_id'] = asset
        if item['requires_review']:
            continue
        item['primary'] = item['primary'].replace(old, asset, 1)
        manifest = item['manifest']
        members = [(member, 'file') for member in manifest['input_images'] + manifest.get('runtime_inputs', [])
                   + (manifest.get('execution') or {}).get('outputs', [])
                   + (manifest.get('material') or {}).get('related_files', [])]
        members += [(region, 'mask_file') for region in manifest.get('regions', [])]
        if manifest.get('mesh'):
            members.append((manifest['mesh'], 'turntable'))
        if manifest.get('keep_composite'):
            members.append((manifest['keep_composite'], 'mask_file'))
        for member, key in members:
            if member.get(key):
                member[key] = member[key].replace(old, asset, 1)
        manifest.update(asset_id=asset, created_at=created)
        for f in item['files']:
            f['relative'] = f['relative'].replace(old, asset, 1)
        data = json.dumps(item['manifest'], indent=2).encode()
        item['files'][-1].update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    intent = {'id': did, 'workspace_id': wid, 'destination': root, 'actor': body.actor.model_dump(),
              'created_at': created, 'items': items}
    await db.append_event('delivery.prepared', workspace_id=wid, payload=intent)
    return {'id': did}


async def _intent(db, did: str) -> dict:
    row = await db._one("SELECT payload_json FROM events WHERE type='delivery.prepared' "
                        "AND json_extract(payload_json,'$.id')=?", (did,))
    if not row:
        raise LookupError('This delivery could not be found.')
    intent = json.loads(row['payload_json'])
    events = await db._all("SELECT payload_json FROM events WHERE workspace_id=? AND type='delivery.item' "
                          "AND json_extract(payload_json,'$.delivery_id')=? ORDER BY seq", (intent['workspace_id'], did))
    by_node = {item['node_id']: item for item in intent['items']}
    for event in events:
        item = json.loads(event['payload_json'])
        by_node[item['node_id']].update({k: item[k] for k in ('state', 'error', 'completed_at')})
    return intent


async def state(db, did: str) -> dict:
    async with db._locked():
        intent = await _intent(db, did)
        return {**intent, 'items': [_view_item(item, intent['destination']) for item in intent['items']]}


async def inventory(db, wid: str) -> list[dict]:
    await CreativeStore(db).project(wid)
    rows = await db._all("SELECT json_extract(payload_json,'$.id') AS id FROM events "
                         "WHERE workspace_id=? AND type='delivery.prepared' ORDER BY seq DESC", (wid,))
    return [await state(db, row['id']) for row in rows]


def _transfer(item: dict, root: str, *, allow_copy: bool) -> None:
    destination = promote.validate_target(Path(root))
    folder = destination / item['folder']
    complete = True
    for f in item['files']:
        path = folder / f['relative']
        if not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError('A destination folder points outside the chosen delivery directory. Choose another destination.')
        if path.exists():
            if not path.is_file() or _hash(path) != f['sha256']:
                raise ValueError('A delivered path contains different material. It was left untouched. Create a new delivery to another destination.')
        else:
            complete = False
    if complete:
        return
    if not allow_copy:
        raise ValueError('Required material was purged after preparation. Existing delivered files remain untouched.')
    for f in item['files']:
        if f['source'] and (not Path(f['source']).is_file() or _hash(Path(f['source'])) != f['sha256']):
            raise ValueError('A prepared source file is missing or changed. Restore its exact bytes before resuming this delivery.')
    # A process interruption may have left this intent's private staging folder.
    # All final paths are checked above; only disposable copier scratch is removed.
    for scratch in folder.glob(f".promote-{item['asset_id']}.tmp-*"):
        if scratch.is_dir() and not scratch.is_symlink():
            shutil.rmtree(scratch)
    primary = next(f for f in item['files'] if f['relative'] == item['primary'])
    extras = [(Path(f['source']), f['relative']) for f in item['files'] if f['source'] and f is not primary]
    promote.promote_candidate(Path(primary['source']), item['manifest'], item['folder'], item['asset_id'], extras, root=destination)
    for f in item['files']:
        if _hash(folder / f['relative']) != f['sha256']:
            raise ValueError('Delivery bytes could not be confirmed. Resume to inspect the same paths.')


async def transfer(db, did: str) -> dict:
    locks = _locks.setdefault(db, {})
    async with locks.setdefault(did, asyncio.Lock()):
        intent = await _intent(db, did)
        if intent.get('archive_origin'):
            raise ValueError('Restored delivery history cannot transfer. Review a new delivery from the restored material.')
        for item in intent['items']:
            if item['state'] == 'complete' or item.get('requires_review'):
                continue
            async with db._locked():
                nodes = [await db.get_node(nid) for nid in item['source_nodes']]
                allow_copy = all(n and not n['purged_at'] for n in nodes)
            error = None
            try:
                job = asyncio.create_task(asyncio.to_thread(_transfer, item, intent['destination'], allow_copy=allow_copy))
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    await job  # Never release the transfer lock while its copy still runs.
                    raise
            except (OSError, ValueError, promote.PromoteTargetError, promote.PromoteMaterialError) as exc:
                error = str(exc)
            async with db._tx():
                result = {'delivery_id': did, 'node_id': item['node_id'], 'state': 'incomplete' if error else 'complete',
                          'error': error, 'completed_at': None if error else now_iso()}
                await db.append_event('delivery.item', workspace_id=intent['workspace_id'], node_id=item['node_id'], payload=result)
                if not error:
                    folder = Path(intent['destination']) / item['folder']
                    await db.append_event('node.promoted', workspace_id=intent['workspace_id'], node_id=item['node_id'],
                        payload={'delivery_id': did, 'asset_id': item['asset_id'], 'actor': intent['actor'],
                                 'manifest_path': str(folder / (item['asset_id'] + '.json')),
                                 'promoted_path': str(folder / item['primary'])})
                    await db.touch_workspace(intent['workspace_id'])
        return await state(db, did)


async def csv_records(db, wid: str) -> str:
    await CreativeStore(db).project(wid)
    rows = await db._all("SELECT ts,node_id,payload_json FROM events WHERE workspace_id=? AND type='node.promoted' ORDER BY seq", (wid,))
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['asset_id', 'node_id', 'engine', 'prompt', 'parent_node_ids', 'delivered_at', 'destination', 'delivery_id'])
    for row in rows:
        node = await db.get_node(row['node_id'])
        record = json.loads(row['payload_json'])
        values = [record.get('asset_id') or Path(record['manifest_path']).stem, node['id'], node['engine'] or '',
                  node['prompt'], json.dumps(node['parents']), row['ts'], record['promoted_path'], record.get('delivery_id', '')]
        # Spreadsheet consumers should read participant text as literal text.
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in values])
    return stream.getvalue()
