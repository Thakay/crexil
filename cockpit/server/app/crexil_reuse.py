"""Explicitly import reviewed material between projects without moving its origin.

Reuse copies the same verified portable material bundle used by delivery. The
new node owns every copied file and records its source project, node and frozen
provenance; it does not acquire the source's execution or foreign parent links.
Missing history remains explicit. Acceptance and its command receipt commit
once, and a source purge or changed byte cannot widen the reviewed copy.

A copy keeps its source's provider terms and audio facts, so every later check
(review's terms refusal, a runner's preflight) sees the same restrictions: a
copy never gains rights its source lacked (laws 12, 21).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

from . import audio, config
from .crexil_delivery import _digest, _plans
from .crexil_store import Conflict, CreativeStore
from .db import new_id, now_iso
from .mesh import fields_sidecar


def _terms(origin: dict, source: dict) -> dict | None:
    """The provider terms a copy keeps: those recorded on its source, or, for a take
    recorded before terms were kept, those of the engine that made it."""
    return origin.get('terms') or audio.terms(audio.engines().get(source.get('engine') or '', {}))


def _portable_facts(facts: dict) -> dict:
    """The source take's audio facts for its copy. The bytes are identical, so the
    measured facts hold; the voice keeps its provider identity, which voice and
    terms checks read, and drops links into the source project."""
    voice = facts.get('voice')
    if isinstance(voice, dict):
        voice = {key: value for key, value in voice.items() if not key.endswith('node_id')}
    return {**facts, 'voice': voice}


async def _plan(db, wid: str, body):
    await CreativeStore(db).project(wid)
    if body.source_project_id == wid:
        raise ValueError('Choose another project. Continue from material already in this project through its existing source.')
    source = await CreativeStore(db).project(body.source_project_id)
    _, items = await _plans(db, source['id'], SimpleNamespace(node_ids=[body.node_id], destination=str(config.STAGING_DIR.resolve())))
    item = items[0]
    node = await db.get_node(body.node_id)
    info = await db._one('SELECT * FROM material_info WHERE node_id=?', (node['id'],))
    info = dict(info) if info else None
    files = [file for file in item['files'] if file['source']]
    public = {'source_project_id': source['id'], 'source_project_name': source['name'],
              'source_node_id': node['id'], 'filename': item['filename'], 'payload': node['payload'],
              'files': [{'filename': Path(file['relative']).name, 'sha256': file['sha256'],
                         'size_bytes': file['size_bytes']} for file in files],
              'total_bytes': sum(file['size_bytes'] for file in files),
              'missing': item['missing'], 'errors': [item['error']] if item['error'] else [],
              'ready': not item['requires_review']}
    public['token'] = _digest([wid, source['id'], source['name'], item])
    return public, item, node, info


async def review(db, wid: str, body):
    async with db._locked():
        public, _, _, _ = await _plan(db, wid, body)
        return public


def _copy(files, targets):
    for file in files:
        source, target = Path(file['source']), targets[file['source']]
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                if size > file['size_bytes']:
                    raise ValueError('Source material changed during copying. Review it again.')
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if size != file['size_bytes'] or digest.hexdigest() != file['sha256']:
            raise ValueError('Source material changed during copying. Review it again.')


async def create(db, wid: str, body, staged: list[Path]):
    """The caller owns the command transaction and cleans staged copies on rollback."""
    public, item, source, info = await _plan(db, wid, body)
    if public['token'] != body.review_token:
        raise Conflict('The source material or its record changed. Review the import again.')
    if not public['ready']:
        raise ValueError(' '.join(public['errors']))
    files = [file for file in item['files'] if file['source']]
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(config.STAGING_DIR).free < public['total_bytes'] + config.DISK_MARGIN_BYTES:
        raise ValueError('The staging drive needs more free space before this material can be reused.')
    directory = config.STAGING_DIR / 'imports' / new_id()
    primary_file = next(file for file in files if file['relative'] == item['primary'])
    primary = directory / ('material' + (Path(source['output_path']).suffix or '.bin'))
    targets = {primary_file['source']: primary}
    retained = []
    manifest = item['manifest']
    outputs = (manifest.get('execution') or {}).get('outputs', [])
    related = (manifest.get('material') or {}).get('related_files', [])
    for file in files:
        if file is primary_file:
            continue
        fid = new_id()
        original = Path(file['source'])
        reported = next((entry for entry in outputs + related if entry.get('file') == file['relative']), None)
        if original == fields_sidecar(source['output_path']):
            target, role, required = fields_sidecar(primary), 'companion', True
        elif original == Path(source['output_path']).with_suffix('.turntable.webp'):
            target, role, required = primary.with_suffix('.turntable.webp'), 'preview', False
        else:
            target = directory / 'related' / (fid + original.suffix)
            role, required = 'source input', False
        if reported:
            role = reported['role']
            required = bool(reported.get('required',
                reported.get('descriptor', {}).get('declared_output', {}).get('required', role == 'companion')))
        # Copy each physical file once, while preserving every reviewed alias.
        target = targets.setdefault(file['source'], target)
        filename = reported['filename'] if reported else Path(file['relative']).name
        media_type = reported['media_type'] if reported else mimetypes.guess_type(original.name)[0] or 'application/octet-stream'
        retained.append({'id': fid, 'path': str(target), 'filename': filename,
            'role': role, 'required': required, 'media_type': media_type,
            'sha256': file['sha256'], 'size_bytes': file['size_bytes'], 'error': None})
    for output in outputs + related:
        if not output['available']:
            retained.append({'id': new_id(), 'path': None, 'filename': output['filename'],
                'role': output['role'], 'required': False, 'media_type': output['media_type'],
                'sha256': output['sha256'], 'size_bytes': output['size_bytes'],
                'error': output.get('error') or 'This related file was unavailable in the source project.'})
    directory.mkdir(parents=True)
    staged.append(directory)
    unique = {file['source']: file for file in files}
    job = asyncio.create_task(asyncio.to_thread(_copy, list(unique.values()), targets))
    try:
        await asyncio.shield(job)
    except asyncio.CancelledError:
        await job
        raise
    old_origin = json.loads(info['origin_json']) if info else {}
    origin = {'kind': 'project_reuse', 'version': 1, 'actor': body.actor.model_dump(),
        'explanation': body.explanation, 'embedded_metadata': old_origin.get('embedded_metadata', {}),
        'source': {'project_id': public['source_project_id'], 'project_name': public['source_project_name'],
                   'node_id': source['id'], 'filename': public['filename'], 'sha256': primary_file['sha256']},
        'source_record': {'kind': 'reuse_review', 'captured_at': now_iso(), 'manifest': manifest},
        'retained_files': retained, 'unavailable_history': public['missing']}
    if terms := _terms(old_origin, source):
        origin['terms'] = terms
    received = await CreativeStore(db).receive_material(wid, path=primary, sha256=primary_file['sha256'],
        filename=public['filename'], media_type=info['media_type'] if info else mimetypes.guess_type(primary.name)[0] or 'application/octet-stream',
        payload=source['payload'], width=info['width'] if info else None, height=info['height'] if info else None,
        origin=origin, parents=[])
    if facts := await db.audio_fact(source['id']):
        await db.record_audio_facts(received['node']['id'], _portable_facts(facts))
    return received
