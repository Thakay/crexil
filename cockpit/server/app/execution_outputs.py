"""Persist files from one execution without treating collection as completion.

Runtime identities, bytes and lineage survive failure and recovery. Each file
has its own atomic write and receipt before the next file is fetched. A group
finishes separately; only the scheduler records the attempt's terminal charge.
Missing descriptors remain visible and can be collected again without running
the engine. A runtime that changes bytes under an identity creates a new member.
Retained runtime inputs can gain one immutable upload/graph binding before
dispatch; recording that binding never replaces the previously retained bytes.
An audio take is measured before its transaction and lands with its facts and
its provider's recorded terms; audio that cannot be measured stays a file.
Bytes reach their file before the receipt transaction: no write, fsync or
whole-payload hash runs on the event loop or while the store's lock is held.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import tempfile
from pathlib import Path
from dataclasses import replace

from PIL import Image, UnidentifiedImageError

from . import audio
from .db import Database, new_id, now_iso
from .executors import CollectedOutput, DispatchRequest


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='collect-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def matches_receipt(path: Path, digest: str) -> bool:
    try:
        with path.open('rb') as source:
            return hashlib.file_digest(source, 'sha256').hexdigest() == digest
    except OSError:
        return False


def sha256_bytes(data: bytes) -> str:
    """SHA-256 of a payload in memory. Blocking for large meshes: call it off the loop."""
    return hashlib.sha256(data).hexdigest()


async def _measured(output: CollectedOutput, data: bytes) -> tuple[CollectedOutput, dict | None]:
    """Audio facts measured off the loop; audio that cannot be measured stays a file."""
    try:
        return output, await asyncio.to_thread(audio.inspect, data)
    except ValueError as exc:
        return replace(output, payload=None, error=f'{output.filename} was declared as audio but could not be measured. {exc}'), None


async def _adopted(output: CollectedOutput, path: Path) -> tuple[CollectedOutput, str, dict | None] | None:
    """A server-owned atomic file can outlive its receipt transaction. Its
    bytes are adopted before the runtime is asked for these same bytes again."""
    try:
        cached = await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None
    output = replace(output, data=cached, error=None)
    facts = None
    if output.payload == audio.PAYLOAD:
        output, facts = await _measured(output, cached)
    return output, await asyncio.to_thread(sha256_bytes, cached), facts


def default_path(staging: Path, slot: dict, identity: str, filename: str) -> Path:
    """Where a new output's bytes live: one file per slot, attempt and identity
    under the turn's staging folder, with the runtime's suffix when it is sane."""
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        suffix = '.bin'
    return staging / slot['turn_id'] / f"{slot['id']}-take{slot['attempt']}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}{suffix}"


async def _ensure_bytes(path: Path, data: bytes, placed: set[Path]) -> None:
    """Write `data` to `path` unless placement already put it there."""
    if path not in placed:
        await asyncio.to_thread(atomic_bytes, path, data)
        placed.add(path)


async def _place(db: Database, staging: Path, slot: dict, output: CollectedOutput,
                 digest: str | None, facts: dict | None, path: Path | None
                 ) -> tuple[CollectedOutput, str | None, dict | None, set[Path]]:
    """Put an output's bytes in their file before the receipt transaction.

    Reads the current receipt without the transaction, then writes, verifies
    or adopts the file off the loop. Returns the output (with adopted bytes),
    its digest and facts, and the paths now holding its bytes. The transaction
    repeats every decision against fresh rows; a path not placed here (a
    concurrent collector changed the receipt) is written there instead."""
    placed: set[Path] = set()
    row = await db._one('SELECT id FROM execution_groups WHERE slot_id=? AND take=?', (slot['id'], slot['attempt']))
    identity = output.identity

    async def receipt(name):
        return await db._one('SELECT * FROM execution_outputs WHERE group_id=? AND identity=?',
                             (row['id'], name)) if row else None
    previous = await receipt(identity)
    if previous and not previous['sha256'] and output.data is None and previous['output_path'] and not await purged_at(db, previous):
        adopted = await _adopted(output, Path(previous['output_path']))
        if adopted:
            output, digest, facts = adopted
            placed.add(Path(previous['output_path']))
    if previous and previous['sha256'] and digest and previous['sha256'] != digest:
        identity += '/changed:' + digest
        previous = await receipt(identity)
        path = None
    if output.data is None:
        return output, digest, facts, placed
    if previous and previous['sha256']:
        target = Path(previous['output_path'])
        if not await purged_at(db, previous):
            if not await asyncio.to_thread(matches_receipt, target, previous['sha256']):
                await asyncio.to_thread(atomic_bytes, target, output.data)
            placed.add(target)
        return output, digest, facts, placed
    await _ensure_bytes(path or default_path(staging, slot, identity, output.filename), output.data, placed)
    return output, digest, facts, placed


async def group(db: Database, slot: dict) -> dict:
    async with db._tx():
        current = await db.get_slot(slot['id'])
        if current and current['attempt'] == slot['attempt']:
            slot = {**slot, 'job_ref': current['job_ref']}
        row = await db._one('SELECT * FROM execution_groups WHERE slot_id=? AND take=?',
                            (slot['id'], slot['attempt']))
        if not row:
            ts = now_iso()
            await db.conn.execute('INSERT INTO execution_groups(id,slot_id,take,state,job_ref,created_at,updated_at) '
                                  'VALUES(?,?,?,?,?,?,?)',
                                  (new_id(), slot['id'], slot['attempt'], 'collecting', slot.get('job_ref'), ts, ts))
        else:
            await db.conn.execute('UPDATE execution_groups SET job_ref=COALESCE(job_ref,?),updated_at=? WHERE id=?',
                                  (slot.get('job_ref'), now_iso(), row['id']))
        return dict(await db._one('SELECT * FROM execution_groups WHERE slot_id=? AND take=?',
                                  (slot['id'], slot['attempt'])))


async def finish(db: Database, slot: dict, state: str, metadata: dict | None = None) -> str:
    async with db._tx():
        item = await group(db, slot)
        metadata = {**json.loads(item['metadata_json']), **(metadata or {})}
        if state == 'complete':
            unresolved = []
            for output in await db._all('SELECT * FROM execution_outputs WHERE group_id=?', (item['id'],)):
                if not await purged_at(db, output) and (not output['sha256'] or not output['output_path'] or not Path(output['output_path']).is_file()):
                    unresolved.append(output['id'])
            metadata['unresolved_output_ids'] = unresolved
            if unresolved:
                state = 'incomplete'
        await db.conn.execute('UPDATE execution_groups SET state=?,metadata_json=?,updated_at=? WHERE id=?',
                              (state, json.dumps(metadata), now_iso(), item['id']))
        return state


async def purged_at(db: Database, member: dict) -> str | None:
    owner = member['node_id']
    if owner is None and member['role'] == 'companion':
        row = await db._one("SELECT json_extract(metadata_json,'$.companion_owner_id') AS owner FROM execution_groups WHERE id=?", (member['group_id'],))
        owner = row['owner'] if row else None
    node = await db.get_node(owner) if owner else None
    return node['purged_at'] if node else None


async def owned_companions(db: Database, nid: str) -> list[Path]:
    from .material_files import owned
    rows = await db._all("SELECT o.output_path FROM execution_outputs o JOIN execution_groups g ON g.id=o.group_id "
        "WHERE o.node_id IS NULL AND o.role='companion' AND o.output_path IS NOT NULL AND json_extract(g.metadata_json,'$.companion_owner_id')=?", (nid,))
    return list(dict.fromkeys([Path(row['output_path']) for row in rows] + await owned(db, nid)))


async def collect(db: Database, staging: Path, slot: dict, req: DispatchRequest,
                  output: CollectedOutput, *, path: Path | None = None) -> dict:
    output = replace(output, descriptor={**output.descriptor, 'receipt': {
        'submitted_graph': output.submitted_graph, 'external': output.external, 'payload': output.payload}})
    digest = await asyncio.to_thread(sha256_bytes, output.data) if output.data is not None else None
    facts = None
    if output.data is not None and output.payload == audio.PAYLOAD:
        output, facts = await _measured(output, output.data)
    output, digest, facts, placed = await _place(db, staging, slot, output, digest, facts, path)
    async with db._tx():
        item = await group(db, slot)
        identity = output.identity
        previous = await db._one('SELECT * FROM execution_outputs WHERE group_id=? AND identity=?', (item['id'], identity))
        if previous and not previous['sha256'] and output.data is None and previous['output_path'] and not await purged_at(db, previous):
            adopted = await _adopted(output, Path(previous['output_path']))
            if adopted:
                output, digest, facts = adopted
                placed.add(Path(previous['output_path']))
        if previous and previous['sha256'] and digest and previous['sha256'] != digest:
            identity += '/changed:' + digest
            previous = await db._one('SELECT * FROM execution_outputs WHERE group_id=? AND identity=?', (item['id'], identity))
            path = None
        if previous and previous['sha256']:
            path = Path(previous['output_path'])
            if (output.data is not None and path not in placed and not await purged_at(db, previous)
                    and not await asyncio.to_thread(matches_receipt, path, previous['sha256'])):
                await asyncio.to_thread(atomic_bytes, path, output.data)
            earlier = json.loads(previous['descriptor_json'])
            binding = previous['role'] == output.role == 'runtime input' and output.submitted_graph is not None
            if binding and earlier.get('receipt', {}).get('submitted_graph'):
                if output.descriptor != earlier:
                    raise ValueError('A retained runtime input already has an immutable upload and graph binding.')
            elif binding:
                if (output.descriptor.get('sha256') != previous['sha256'] or any(
                        output.descriptor.get(key) != value for key, value in earlier.items() if key != 'receipt')):
                    raise ValueError('A runtime input binding cannot change its retained identity or description.')
                await db.conn.execute('UPDATE execution_outputs SET descriptor_json=? WHERE id=?',
                                      (json.dumps(output.descriptor), previous['id']))
                await db._append_event('execution.input_bound', **await db._slot_ids(slot),
                    payload={'group_id': item['id'], 'output_id': previous['id'], 'identity': identity,
                             'sha256': previous['sha256'], 'upload': output.descriptor.get('upload')})
                previous = await db._one('SELECT * FROM execution_outputs WHERE id=?', (previous['id'],))
            return dict(previous)
        if previous and output.data is None and previous['error'] == output.error and previous['descriptor_json'] == json.dumps(output.descriptor):
            return dict(previous)
        if previous:
            oid, ordinal = previous['id'], previous['ordinal']
        else:
            oid = new_id()
            maximum = await db._one('SELECT MAX(ordinal) AS last FROM execution_outputs WHERE group_id=?', (item['id'],))
            ordinal = (maximum['last'] if maximum['last'] is not None else -1) + 1
        if path is None:
            path = default_path(staging, slot, identity, output.filename)
        media = mimetypes.guess_type(output.filename)[0] or 'application/octet-stream'
        node = None
        width, height = None, None
        if output.data is not None:
            await _ensure_bytes(path, output.data, placed)
            if output.payload == 'image':
                try:
                    with Image.open(io.BytesIO(output.data)) as image:
                        width, height = image.size
                        media = Image.MIME.get(image.format, media)
                except (UnidentifiedImageError, OSError, ValueError):
                    pass
            if facts is not None:
                media = audio.MEDIA_TYPE
            if output.payload in {'image', '3d', audio.PAYLOAD}:
                node = await db.land_node(slot['id'], payload=output.payload, verb=req.verb,
                    output_path=str(path), prompt=req.prompt, parents=list(req.parents),
                    submitted_graph=output.submitted_graph, external=output.external, x=req.x, y=req.y,
                    complete=False, output_index=ordinal, expected_attempt=slot['attempt'])
                metadata = json.loads(item['metadata_json'])
                if not metadata.get('companion_owner_id'):
                    metadata['companion_owner_id'] = node['id']
                    await db.conn.execute('UPDATE execution_groups SET metadata_json=? WHERE id=?',
                                          (json.dumps(metadata), item['id']))
                origin = {'kind': 'execution', 'group_id': item['id'], 'output_id': oid, 'role': output.role}
                terms = audio.terms(audio.engines().get(slot['engine'], {}))
                if terms:
                    origin['terms'] = terms
                await db.conn.execute('INSERT OR IGNORE INTO material_info(node_id,sha256,filename,media_type,width,height,origin_json) '
                                      'VALUES(?,?,?,?,?,?,?)',
                    (node['id'], digest, output.filename, media, width, height, json.dumps(origin)))
                if facts is not None:
                    reported = output.descriptor.get('audio') or {}
                    await db.record_audio_facts(node['id'], {**facts, **{key: reported.get(key) for key in ('text', 'voice', 'alignment')}})
        values = (node['id'] if node else None, output.role, output.filename, media,
                  str(path), digest,
                  len(output.data) if output.data is not None else None,
                  json.dumps(output.descriptor), output.error)
        if previous:
            await db.conn.execute('UPDATE execution_outputs SET node_id=?,role=?,filename=?,media_type=?,output_path=?,sha256=?,size_bytes=?,descriptor_json=?,error=? WHERE id=?', (*values, oid))
        else:
            await db.conn.execute('INSERT INTO execution_outputs(id,group_id,identity,ordinal,node_id,role,filename,media_type,output_path,sha256,size_bytes,descriptor_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (oid, item['id'], identity, ordinal, *values, now_iso()))
        await db._append_event('execution.output_collected' if output.data is not None else
                               'execution.output_missing' if output.error else 'execution.output_discovered',
                               **await db._slot_ids(slot), node_id=node['id'] if node else None,
                               payload={'group_id': item['id'], 'output_id': oid, 'identity': identity,
                                        'role': output.role, 'sha256': digest, 'error': output.error})
        return dict(await db._one('SELECT * FROM execution_outputs WHERE id=?', (oid,)))


async def inventory(db: Database, wid: str) -> list[dict]:
    """A project's execution groups, oldest first. A blind run's groups keep its
    grid order, by cell and attempt, never the order its takes landed in."""
    groups = await db._all('SELECT g.* FROM execution_groups g JOIN slots s ON s.id=g.slot_id '
        'JOIN turns t ON t.id=s.turn_id LEFT JOIN blind_runs b ON b.turn_id=s.turn_id WHERE t.workspace_id=? '
        'ORDER BY COALESCE(b.created_at, g.created_at), CASE WHEN b.turn_id IS NULL THEN NULL ELSE s.pos END, '
        'g.take, g.id', (wid,))
    result = []
    for row in groups:
        item = dict(row)
        item['metadata'] = json.loads(item.pop('metadata_json'))
        outputs = []
        for member in await db._all('SELECT o.*,n.purged_at FROM execution_outputs o LEFT JOIN nodes n ON n.id=o.node_id WHERE group_id=? ORDER BY ordinal', (item['id'],)):
            output = dict(member)
            path = output.pop('output_path')
            output['purged_at'] = await purged_at(db, output)
            output['available'] = bool(output['sha256'] and path and not output['purged_at'] and Path(path).is_file())
            output['descriptor'] = json.loads(output.pop('descriptor_json'))
            outputs.append(output)
        result.append({**item, 'outputs': outputs})
    return result


async def recover_files(db: Database, staging: Path, slot: dict, req: DispatchRequest) -> None:
    item = await group(db, slot)
    rows = await db._all('SELECT * FROM execution_outputs WHERE group_id=? AND sha256 IS NULL', (item['id'],))
    for row in rows:
        if row['output_path'] and Path(row['output_path']).is_file():
            descriptor = json.loads(row['descriptor_json'])
            payload = ('image' if row['media_type'].startswith('image/') else audio.PAYLOAD if row['media_type'].startswith('audio/')
                       else '3d' if Path(row['filename']).suffix in {'.glb', '.gltf', '.ply', '.obj'} else None)
            await collect(db, staging, slot, req, CollectedOutput(identity=row['identity'], filename=row['filename'],
                payload=descriptor.get('receipt', {}).get('payload', payload), role=row['role'], descriptor=descriptor,
                submitted_graph=descriptor.get('receipt', {}).get('submitted_graph'),
                external=descriptor.get('receipt', {}).get('external')), path=Path(row['output_path']))
