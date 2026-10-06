"""Immutable masks retain their original upload, binary pixels and source binding.

A mask version names source bytes and displayed coordinates. New ink or an
explicit adjustment creates another version; it never edits a consumed region.
Legacy region rows remain per-turn snapshots. Import cannot silently resize or
rebind a mask, and every adjustment records its earlier version and transform.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import config, regions
from .crexil_store import Conflict, CreativeStore
from .db import new_id, now_iso

COORDINATES = 'Source pixels after applying EXIF display orientation; origin at the top left.'


def source_pixels(blob):
    try:
        with Image.open(io.BytesIO(blob)) as original:
            if original.width * original.height > 16_000_000:
                raise ValueError('Region preparation accepts sources up to 16 million pixels. Prepare a smaller copy first.')
            image = ImageOps.exif_transpose(original)
            mode = 'RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB'
            return image.convert(mode)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('The source cannot be read as an image.') from exc


async def source(db, wid, nid):
    node = await db.get_node(nid)
    if not node or node['workspace_id'] != wid or node['purged_at'] or node['payload'] != 'image':
        raise ValueError('Use an available image source from this project.')
    try:
        blob = await run_in_threadpool(Path(node['output_path']).read_bytes)
    except OSError as exc:
        raise ValueError('The source image is unavailable. Restore its material first.') from exc
    sha = regions.sha256_bytes(blob)
    stored = await db._one('SELECT sha256 FROM material_info WHERE node_id=?', (nid,))
    if stored and stored['sha256'] != sha:
        raise ValueError('The source no longer matches its recorded hash. Restore the original bytes.')
    pixels = await run_in_threadpool(source_pixels, blob)
    return {'node_id': nid, 'sha256': sha, 'width': pixels.width, 'height': pixels.height,
            'coordinates': COORDINATES}, pixels


def binary_mask(blob, size):
    try:
        with Image.open(io.BytesIO(blob)) as image:
            if image.format != 'PNG':
                raise ValueError('Import a PNG mask. White pixels select the region; black and transparent pixels do not.')
            if image.size != size:
                raise ValueError('The mask dimensions must match the source. Save an explicit crop or resize adjustment first.')
            value = image.convert('L')
            if 'A' in image.getbands() or 'transparency' in image.info:
                value = ImageChops.multiply(value, image.convert('RGBA').getchannel('A'))
            return value.point(lambda pixel: 255 if pixel >= 128 else 0)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('The supplied mask cannot be read as a PNG image.') from exc


async def get(db, mid):
    row = await db._one('SELECT * FROM mask_assets WHERE id=?', (mid,))
    if not row:
        raise LookupError('This saved mask was not found.')
    return dict(row)


def view(row):
    return {key: value for key, value in row.items() if key not in ('mask_path', 'original_path', 'transform_json', 'actor_json')} | {
        'transform': json.loads(row['transform_json']), 'actor': json.loads(row['actor_json']),
        'available': Path(row['mask_path']).is_file()}


async def list_masks(db, wid):
    await CreativeStore(db).project(wid)
    return [view(dict(row)) for row in await db._all(
        'SELECT * FROM mask_assets WHERE workspace_id=? ORDER BY created_at,id', (wid,))]


async def verified_bytes(row):
    try:
        blob = await run_in_threadpool(Path(row['mask_path']).read_bytes)
    except OSError as exc:
        raise ValueError('This mask file is unavailable. Its saved record remains.') from exc
    if regions.sha256_bytes(blob) != row['sha256']:
        raise ValueError('This mask no longer matches its recorded hash. Restore the saved file.')
    return blob


async def save(db, wid, body):
    info, _ = await source(db, wid, body.source_node_id)
    if body.source_sha256 != info['sha256']:
        raise Conflict('The source changed after the mask was prepared. Reopen it before saving this mask.')
    size = (info['width'], info['height'])
    previous = await get(db, body.previous_mask_id) if body.previous_mask_id else None
    if previous and previous['workspace_id'] != wid:
        raise ValueError('Reuse a saved mask from this project.')
    transform = {'coordinates': COORDINATES, 'binary_threshold': 128,
                 'alpha': 'Alpha multiplies brightness before thresholding; transparent pixels are outside the mask.'}
    if body.adjustment:
        if not previous or body.data_base64 is not None:
            raise ValueError('An adjustment needs one saved mask and no new bitmap upload.')
        original = await verified_bytes(previous)
        mask = Image.open(io.BytesIO(original)).convert('L')
        box = body.adjustment.crop
        if box:
            if box.x + box.width > mask.width or box.y + box.height > mask.height:
                raise ValueError('The adjustment crop must stay inside the earlier mask.')
            mask = mask.crop((box.x, box.y, box.x + box.width, box.y + box.height))
        if body.adjustment.resize_to_source:
            mask = mask.resize(size, Image.Resampling.NEAREST)
        if mask.size != size:
            raise ValueError('The adjusted mask must match the new source. Choose an explicit resize or change the crop.')
        transform.update(kind='adjusted', adjustment=body.adjustment.model_dump(),
                         earlier_source={'node_id': previous['source_node_id'], 'sha256': previous['source_sha256'],
                                         'width': previous['width'], 'height': previous['height']})
    else:
        if body.data_base64 is None:
            raise ValueError('Draw or import a mask, or choose an explicit adjustment from an earlier mask.')
        if previous and (previous['source_node_id'] != body.source_node_id or previous['source_sha256'] != info['sha256']):
            raise ValueError('The earlier mask belongs to different source bytes. Rebind it with an explicit adjustment first.')
        try:
            original = base64.b64decode(body.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('The mask is not valid base64 image data.') from exc
        mask = await run_in_threadpool(binary_mask, original, size)
        transform['kind'] = 'revised' if previous else 'supplied'
    data = regions.mask_png(mask)
    sha, original_sha = regions.sha256_bytes(data), regions.sha256_bytes(original)
    mid = new_id()
    directory = config.STAGING_DIR / 'masks' / mid
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(config.STAGING_DIR).free < len(data) + len(original) + config.DISK_MARGIN_BYTES:
        raise ValueError('The staging drive needs more space before this mask can be saved.')
    directory.mkdir(parents=True)
    path, original_path = directory / 'mask.png', directory / 'supplied.png'
    try:
        for target, blob in ((path, data), (original_path, original)):
            with target.open('xb') as file:
                file.write(blob)
                file.flush()
                os.fsync(file.fileno())
        await db.conn.execute('INSERT INTO mask_assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (mid, wid, body.source_node_id, info['sha256'], *size, body.name, str(path), sha,
             str(original_path), original_sha, body.previous_mask_id, json.dumps(transform),
             json.dumps(body.actor.model_dump()), now_iso()))
        result = view(await get(db, mid))
        await db.append_event('mask.saved', workspace_id=wid, node_id=body.source_node_id,
                              payload={**result, 'request_id': body.request_id})
        return result
    except BaseException:
        path.unlink(missing_ok=True)
        original_path.unlink(missing_ok=True)
        raise
