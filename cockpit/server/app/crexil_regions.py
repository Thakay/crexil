"""Shared Region Edit plans bind saved masks to source bytes and reviewed passes.

Planning reads and verifies material without staging files or contacting engines.
Protected pixels always win; other overlap requires the participant's explicit
priority choice. Unclaimed-area regeneration is opt-in. Native region envelopes
reuse the proven crop and compositing engine with frozen workflow bytes.
"""

from __future__ import annotations

import base64
import copy
import io
import os

from PIL import Image, ImageChops
from starlette.concurrency import run_in_threadpool

from . import config, mask_assets, presets, regionrun, regions
from .db import new_id


def preview_url(mask):
    shown = mask.copy()
    shown.thumbnail((640, 480), Image.Resampling.NEAREST)
    return 'data:image/png;base64,' + base64.b64encode(regions.mask_png(shown)).decode()


async def plan(db, wid, setup, source_hash):
    if len(setup.references) != 1:
        raise ValueError('Region Edit needs one source image.')
    if not setup.regions:
        raise ValueError('Draw, import or reuse a saved region before reviewing.')
    if len({r.key for r in setup.regions}) != len(setup.regions):
        raise ValueError('Each region needs its own key.')
    if any(r.contribution or r.role or r.strength is not None for r in setup.references):
        raise ValueError('Put Region Edit instructions on the regions or in the shared prompt. Clear source contribution, role and strength values explicitly.')
    info, pixels = await mask_assets.source(db, wid, setup.references[0].node_id)
    if info['sha256'] != source_hash:
        raise ValueError('The source changed during review. Review its current bytes again.')
    rows, masks, assets = [], {}, []
    claimed_edits = Image.new('L', pixels.size, 0)
    keep = Image.new('L', pixels.size, 0)
    for spec in setup.regions:
        if spec.mask_id is None:
            raise ValueError(f'{spec.name} has no saved mask yet. Finish saving its drawing.')
        asset = await mask_assets.get(db, spec.mask_id)
        if asset['workspace_id'] != wid or asset['source_node_id'] != info['node_id'] or asset['source_sha256'] != info['sha256']:
            raise ValueError(f'{spec.name} belongs to different source bytes. Save an explicit adjustment before using it here.')
        blob = await mask_assets.verified_bytes(asset)
        mask = await run_in_threadpool(mask_assets.binary_mask, blob, pixels.size)
        if regions.is_empty(mask):
            raise ValueError(f'{spec.name} is empty. Draw an area or remove the region.')
        if spec.action in ('keep', 'delete') and spec.prompt.strip():
            raise ValueError(f'{spec.name} does not accept an instruction for {spec.action}. Clear its saved prompt explicitly.')
        if spec.action == 'keep':
            keep = ImageChops.lighter(keep, mask)
        rows.append({'id': spec.key, 'name': spec.name, 'action': spec.action,
                     'prompt': spec.prompt, 'z_order': len(rows)})
        masks[spec.key] = mask
        assets.append(asset)
    overlaps = []
    for row in rows:
        if row['action'] == 'keep':
            continue
        edited = ImageChops.subtract(masks[row['id']], keep)
        if not regions.is_empty(ImageChops.darker(edited, claimed_edits)):
            overlaps.append(row['name'])
        claimed_edits = ImageChops.lighter(claimed_edits, edited)
    if overlaps and setup.region_overlap_policy != regions.POLICY_ID:
        raise ValueError('Editable regions overlap. Choose the explicit action priority before dispatch: KEEP, DELETE, REPLACE, MODIFY; later regions win within an action.')
    if setup.region_regenerate_unclaimed and (not setup.prompt.strip() or regions.is_empty(keep)):
        raise ValueError('Regenerating unclaimed pixels needs a protected region and a shared prompt.')
    try:
        resolved = regionrun.build_region_plan(rows, masks, pixels.size, setup.prompt,
                                               regenerate_unclaimed=setup.region_regenerate_unclaimed)
    except regionrun.RegionRunError as exc:
        raise ValueError(str(exc)) from exc
    effective, _ = regions.effective_masks(rows, masks, pixels.size)
    details = {'policy': regions.POLICY_ID, 'overlap_confirmed': setup.region_overlap_policy,
               'regenerate_unclaimed': setup.region_regenerate_unclaimed,
               'source': info, 'passes': [{key: value for key, value in p.items() if key in
                   ('idx', 'kind', 'capability', 'region_ids', 'prompt', 'box')} | {
                       'mask_sha256': regions.sha256_bytes(p['mask']),
                       'effective_mask_preview': preview_url(Image.open(io.BytesIO(p['mask'])))} for p in resolved['passes']],
               'regions': [{**row, 'mask_id': asset['id'], 'mask_sha256': asset['sha256'],
                            'effective_preview': preview_url(effective[row['id']]),
                            'occluded': row['id'] in resolved['occluded_region_ids']} for row, asset in zip(rows, assets, strict=True)],
               'keep_preview': preview_url(keep),
               'protection': 'Protected pixels and source alpha are restored exactly. Feathering stays inside each effective edit mask.',
               'recovery': 'Completed passes are retained if a later pass fails. Interrupted execution is uncertain and is never restarted automatically.'}
    return {'plan': resolved, 'rows': rows, 'assets': assets, 'source_pixels': pixels, 'details': details}


def compile_operation(preset, inputs, prepared, workflows):
    planned = copy.deepcopy(prepared['plan'])
    capabilities = {p['capability'] for p in planned['passes']}
    missing = capabilities - set(preset.get('workflows', {}))
    if missing:
        raise ValueError('This operation has no ' + ', '.join(sorted(missing)) + ' recipe for the reviewed regions.')
    specs = {s['key']: s for s in preset['inputs'] if s.get('supported', True) and presets.spec_applies(s, 'region')}
    for p in planned['passes']:
        p.update(mask_upload='mask.png', mask_path='', mask_sha256=regions.sha256_bytes(p['mask']))
    try:
        return regionrun.build_engine_envelope(preset, inputs, specs, planned,
            source={'path': '', 'sha256': '', 'name': 'source.png'}, keep=None,
            region_ids=[row['id'] for row in prepared['rows']], workflows=workflows)
    except regionrun.RegionRunError as exc:
        raise ValueError(str(exc)) from exc


def write_blob(directory, name, blob):
    path = directory / name
    with path.open('xb') as file:
        file.write(blob)
        file.flush()
        os.fsync(file.fileno())
    return {'path': str(path), 'sha256': regions.sha256_bytes(blob)}


async def freeze(db, prepared, envelopes, *, staged_directories=None):
    """Stage verified local files and committed region rows; no runtime uploads."""
    directory = config.STAGING_DIR / 'region-preparations' / new_id()
    directory.mkdir(parents=True)
    if staged_directories is not None:
        staged_directories.append(directory)
    source_blob = regions.mask_png(prepared['source_pixels'])
    source = write_blob(directory, 'source.png', source_blob)
    source.update(name='source.png', original_sha256=prepared['details']['source']['sha256'],
                  coordinates=mask_assets.COORDINATES)
    keep = write_blob(directory, 'keep.png', prepared['plan']['keep_mask']) if prepared['plan']['keep_mask'] else None
    ids = {}
    for row, asset in zip(prepared['rows'], prepared['assets'], strict=True):
        region = await db.create_region(asset['source_node_id'], row['name'], row['action'], row['prompt'],
                                        asset['mask_path'], asset['sha256'])
        ids[row['id']] = region['id']
    masks = [write_blob(directory, f'pass-{p["idx"]}.png', p['mask']) for p in prepared['plan']['passes']]
    for envelope in envelopes.values():
        planned = envelope['region_plan']
        planned.update(source=source, keep=keep, region_ids=list(ids.values()),
                       occluded_region_ids=[ids[r] for r in planned['occluded_region_ids']])
        for p, mask in zip(planned['passes'], masks, strict=True):
            p.update(region_ids=[ids[r] for r in p['region_ids']], mask={**mask, 'upload_at_dispatch': True})
    return list(ids.values())
