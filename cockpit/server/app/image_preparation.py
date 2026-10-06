"""Deterministic image preparation creates new material with an observed recipe.

Review binds saved coordinates, source bytes and a rendered result. Saving uses
that exact review and a command receipt; originals are never overwritten. These
bounded pixel transforms do not invoke a generation engine. List order is the
explicit arrangement order, independent of the Filmstrip's display geometry.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError, __version__ as pillow_version
from starlette.concurrency import run_in_threadpool

from . import config
from .crexil_execution import digest
from .crexil_schemas import Preparation
from .crexil_store import Conflict, CreativeStore
from .db import new_id


def render(recipe, blobs):
    """Return a lossless PNG and dimensions; reject implicit clipping or resizing."""
    if recipe.mode != 'crop' and recipe.width * recipe.height > 16_000_000:
        raise ValueError('Prepare an image of at most 16 million pixels.')
    if recipe.mode != 'arrange' and len(recipe.items) != 1:
        raise ValueError('Crop and resize each need one source. Choose Arrange for several sources.')
    if not recipe.items:
        raise ValueError('Add an image source before reviewing this preparation.')
    out = Image.new('RGBA', (recipe.width, recipe.height),
                    {'transparent': (0, 0, 0, 0), 'white': (255, 255, 255, 255), 'black': (0, 0, 0, 255)}[recipe.background]) if recipe.mode == 'arrange' else None
    for item, blob in zip(recipe.items, blobs, strict=True):
        try:
            with Image.open(io.BytesIO(blob)) as raw:
                if raw.width * raw.height > 64_000_000:
                    raise ValueError('This source exceeds the 64 million pixel preparation limit.')
                source = ImageOps.exif_transpose(raw).convert('RGBA')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('A source cannot be decoded as an image.') from exc
        if item.crop:
            box = item.crop
            if box.x + box.width > source.width or box.y + box.height > source.height:
                raise ValueError('The crop must stay inside its source image. Adjust the saved coordinates.')
            source = source.crop((box.x, box.y, box.x + box.width, box.y + box.height))
        if recipe.mode == 'crop':
            if not item.crop:
                raise ValueError('Choose explicit crop coordinates before saving.')
            out = source
        elif recipe.mode == 'resize':
            out = source.resize((recipe.width, recipe.height),
                                Image.Resampling.NEAREST if recipe.resample == 'nearest' else Image.Resampling.LANCZOS)
        else:
            box = item.placement
            if not box:
                raise ValueError('Give every source an explicit placement in the arrangement.')
            if box.x + box.width > out.width or box.y + box.height > out.height:
                raise ValueError('Every placement must fit inside the arrangement. Enlarge the canvas or adjust its coordinates.')
            layer = source.resize((box.width, box.height),
                                  Image.Resampling.NEAREST if recipe.resample == 'nearest' else Image.Resampling.LANCZOS)
            out.alpha_composite(layer, (box.x, box.y))
    if out.width * out.height > 16_000_000:
        raise ValueError('Prepare an image of at most 16 million pixels.')
    buffer = io.BytesIO()
    out.save(buffer, format='PNG')
    return buffer.getvalue(), out.size


async def review(db, did: str, *, rendered=None):
    creative = CreativeStore(db)
    direction = await creative.direction(did)
    setup = Preparation.model_validate(direction['setup'])
    if direction['state'] != 'draft' or direction['set_aside']:
        raise ValueError('Open an active unfinished preparation before making a derived image.')
    recipe = setup.image_preparation
    if setup.action != 'prepare' or recipe is None:
        raise ValueError('Choose image preparation and save its crop, resize or arrangement settings.')
    if setup.operations or setup.controls or setup.prompt.strip() or any(r.contribution or r.role or r.strength is not None for r in setup.references):
        raise ValueError('Image preparation uses explicit pixel transforms. Clear generation operations, prompts and source instructions before preparing an image.')
    ids = [item.node_id for item in recipe.items]
    if ids != [ref.node_id for ref in setup.references] or len(ids) != len(set(ids)):
        raise ValueError('The preparation sources and their saved placement order must match.')
    sources, blobs = [], []
    for nid in ids:
        node = await db.get_node(nid)
        if not node or node['workspace_id'] != direction['workspace_id'] or node['purged_at'] or node['payload'] != 'image':
            raise ValueError('Use available image sources from this project.')
        try:
            blob = await run_in_threadpool(Path(node['output_path']).read_bytes)
        except OSError as exc:
            raise ValueError('A source file is unavailable. Restore it or choose another source.') from exc
        sha = hashlib.sha256(blob).hexdigest()
        info = await db._one('SELECT sha256 FROM material_info WHERE node_id=?', (nid,))
        if info and info['sha256'] != sha:
            raise ValueError('A source file no longer matches its recorded hash. Restore its original bytes before preparing it.')
        sources.append({'node_id': nid, 'sha256': sha})
        blobs.append(blob)
    data, size = await run_in_threadpool(render, recipe, blobs)
    snapshot = {'direction_id': did, 'revision': direction['revision'], 'setup': setup.model_dump(),
                'sources': sources, 'output_sha256': hashlib.sha256(data).hexdigest(), 'size': list(size),
                'renderer': {'policy': 'image-preparation-v1', 'pillow': pillow_version,
                             'coordinates': 'Source pixels after applying EXIF display orientation. Original source bytes are retained.'},
                'order': 'Sources are composited in list order. Later sources cover earlier sources where opaque.',
                'route': 'Local deterministic image preparation; no engine, provider or charge.'}
    if rendered is not None:
        rendered.append(data)
    preview = Image.open(io.BytesIO(data))
    preview.thumbnail((900, 650))
    buffer = io.BytesIO()
    preview.save(buffer, format='PNG')
    return {'direction_id': did, 'revision': direction['revision'], 'token': digest(snapshot),
            'ready': True, 'errors': [], 'attempts': 0, 'ready_attempts': 0, 'blocked_attempts': 0,
            'maximum_usd': 0, 'snapshot': {**snapshot, 'preview_data_url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}}


async def save(db, did, body, *, sequence_context=None, staged_directories=None):
    rendered = []
    checked = await review(db, did, rendered=rendered)
    if body.revision != checked['revision'] or body.review_token != checked['token']:
        raise Conflict('The preparation or source bytes changed. Review the current image before saving.')
    creative = CreativeStore(db)
    direction = await creative.direction(did)
    if direction['sequence_template']:
        raise ValueError('This preparation is a sequence template. Review and start its sequence to run it.')
    data, snapshot = rendered[0], checked['snapshot']
    snapshot.pop('preview_data_url')
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(config.STAGING_DIR).free < len(data) + config.DISK_MARGIN_BYTES:
        raise ValueError('The staging drive needs more free space before this image can be prepared.')
    sha = snapshot['output_sha256']
    path = config.STAGING_DIR / 'preparations' / new_id() / f'{sha}.png'
    path.parent.mkdir(parents=True)
    if staged_directories is not None:
        staged_directories.append(path.parent)
    try:
        with path.open('xb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        parents = [s['node_id'] for s in snapshot['sources']]
        mode = snapshot['setup']['image_preparation']['mode']
        node = await db.import_node(direction['workspace_id'], output_path=str(path),
                                    prompt=direction['title'], parents=parents)
        origin = {'kind': 'prepared_image', 'transform': snapshot, 'actor': body.actor.model_dump()}
        if sequence_context:
            origin['sequence'] = sequence_context
        await db.conn.execute('INSERT INTO material_info VALUES(?,?,?,?,?,?,?)',
            (node['id'], sha, f'{mode}-{node["id"]}.png', 'image/png', *snapshot['size'], json.dumps(origin)))
        await db.conn.execute('INSERT INTO direction_material VALUES(?,?)', (did, node['id']))
        saved = await creative.revise_direction(did, body.revision,
            {'state': 'imported', 'selection': [node['id']]}, body.actor.model_dump())
        await db.append_event('material.prepared', workspace_id=direction['workspace_id'], node_id=node['id'],
            payload={**origin, 'direction_id': did, 'parents': parents, 'sha256': sha, 'review_token': checked['token']})
        return {'node': node, 'direction': saved}
    except BaseException:
        path.unlink(missing_ok=True)
        raise
