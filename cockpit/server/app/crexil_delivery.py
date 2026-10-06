"""Deliver chosen takes as one character package per delivery, through attributed,
recoverable server commands.

A package is one folder under the chosen destination. Each take lands in its
medium's folder (law 20): `Art/` for images, `Models/` for meshes,
`Audio/Voice/` for speech, `Audio/Voice/Previews/` for voice design previews
and `Audio/SFX/` for sound effects. It is named by its grid label and a slug of
its own words, and its manifest v4 sits beside it under the same name. Original
bytes are copied exactly. Where a mainstream game engine cannot import an
image's own format, a PNG of its pixels is written beside it as a separate file,
which `package.json` names as a conversion with the engines that need it. A
source or another take of the same attempt that the package also delivers is
referenced at its package path, never copied twice; another take of the attempt
that the package does not deliver keeps its record without its bytes. Once
every planned take is in place, the package gains `records.csv`, the record
summary; `decisions.json`, the decision record for these takes (authors, reasons
and candidates, never audio and never a path); and last `package.json`, which
lists every other file with its role, path and SHA-256.

Review plans once. Database reads hold the store's lock only while they read,
and each file is hashed once, outside it, through the size-and-time cache. The
plan is held under its review token; preparing records exactly that plan once
no reviewed file or source changed since review, so preparing hashes nothing.
The event ledger holds the frozen destination, names, manifests and file hashes
before any transfer. Retries reconcile the same asset identities; only a new
delivery intent creates another copy. Each item succeeds or fails
independently, and a file already in place is verified, never overwritten.
Completed receipts are historical facts, not claims that an external file still
exists. Inspection never transfers, and recovery never regenerates material.
An item refused during review needs a new review after repair; healthy items
still transfer. A take of a blind run is refused until the run's authored
reveal, so no file names its engine before then. Preparing and sending a
delivery is the artist's act (law 23): the routes refuse an agent before this
module runs. Purge prevents later copies, but a copy already started may finish
and record its verified destination bytes after the source is purged.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import mimetypes
import os
import posixpath
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from weakref import WeakKeyDictionary

from PIL import Image

from . import blind, decisions, manifests, promote
from .agent_door import label as grid_label
from .api_v4_promote import _root
from .crexil_store import Conflict, CreativeStore
from .db import new_id, now_iso

_locks = WeakKeyDictionary()
_reviews = WeakKeyDictionary()   # store -> reviewed plans by token, oldest first
REVIEWS_HELD = 8
PACKAGE_VERSION = 1

# law 20: each medium's place in a package. (payload, verb) -> (folder, role);
# a verb of None stands for every other verb of that payload.
LAYOUT = {
    ('image', None): ('Art', 'art'),
    ('3d', None): ('Models', 'model'),
    ('audio', 'speak'): ('Audio/Voice', 'voice_line'),
    ('audio', 'voice-design'): ('Audio/Voice/Previews', 'voice_preview'),
    ('audio', 'sfx'): ('Audio/SFX', 'sound_effect'),
    ('audio', None): ('Audio', 'audio'),
}

# Image formats a mainstream engine's importer cannot read, with the engines that
# need a PNG beside the original. PNG, JPEG and PCM WAV import in Unity, Unreal
# Engine and Godot as they are, and so does GLB (Unity through its glTFast package).
NEEDS_PNG = {'image/webp': ('WebP', ('Unity', 'Unreal Engine')), 'image/tiff': ('TIFF', ('Godot',))}
_PNG_MODES = frozenset({'1', 'L', 'LA', 'I', 'I;16', 'P', 'RGB', 'RGBA'})

PACKAGE_FILES = ('records.csv', 'decisions.json', 'package.json')
SUMMARY = ['asset_id', 'label', 'role', 'path', 'node_id', 'engine', 'prompt', 'parent_node_ids',
           'sha256', 'delivered_at', 'delivery_id']

CHANGED = ('A file changed since it was collected. Restore its original bytes or collect this attempt '
           'again before delivery. Nothing was delivered.')
SOURCE_CHANGED = ('A prepared source file is missing or changed. Restore its exact bytes before '
                  'resuming this delivery.')
CONVERSION_CHANGED = ('The PNG for game engines no longer comes out byte for byte as reviewed. '
                      'Review this material in a new delivery.')
UNCONFIRMED = 'Delivery bytes could not be confirmed. Resume to inspect the same paths.'
DIFFERENT = ('A delivered path contains different material. It was left untouched. '
             'Create a new delivery to another destination.')


def _hash(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, indent=2).encode()


def _literal(value):
    """Spreadsheet consumers read participant text as literal text."""
    return "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value


def _view_item(item: dict, destination: str) -> dict:
    return {key: item[key] for key in ('node_id', 'filename', 'asset_id', 'state', 'error', 'missing', 'completed_at')} | {
        'requires_review': item.get('requires_review', False),
        'files': [{'path': str(Path(destination) / item['folder'] / f['relative']),
                   'sha256': f['sha256'], 'size_bytes': f['size_bytes']} for f in item['files']]}


def _manifest_name(item: dict) -> str:
    return next(f['relative'] for f in item['files'] if f['source'] is None)


def _package_view(package: dict | None, destination: str) -> dict | None:
    """Where the package's own files land and whether they are in place."""
    if not package:
        return None
    folder = Path(destination) / package['folder']
    return {'path': str(folder), 'state': package.get('state', 'prepared'), 'error': package.get('error'),
            'completed_at': package.get('completed_at'), 'files': [str(folder / name) for name in PACKAGE_FILES]}


def _errors(items: list[dict], target: dict) -> list[str]:
    errors = [f"{item['filename']}: {item['error']}" for item in items if item['error']]
    if not target['ok']:
        errors.append(f"{target['message']}. {target['remedy']}")
    return errors


# --- one take's portable bundle ---

async def _hashed(db, path: Path) -> tuple[str, int, list[int]]:
    """A file's SHA-256, size and stat fingerprint. The bytes are read only when the
    size-and-time cache holds nothing for exactly this file, on a worker thread,
    outside every lock this call takes."""
    try:
        stat = await asyncio.to_thread(path.stat)
        digest = await db.cached_hash(str(path), stat.st_size, stat.st_mtime)
        if digest is None:
            digest = await asyncio.to_thread(_hash, path)
            await db.store_hash(str(path), stat.st_size, stat.st_mtime, digest)
    except OSError as exc:
        raise ValueError('A file this material needs is unavailable. Restore or collect it, '
                         'then review this material again.') from exc
    return digest, stat.st_size, [stat.st_size, stat.st_mtime_ns]


def _recorded(manifest: dict, relative: str, primary: bool) -> str | None:
    """The SHA-256 the record holds for one file of a bundle, when it holds one."""
    expected = (manifest.get('material') or {}).get('sha256') if primary else None
    for item in [*((manifest.get('execution') or {}).get('outputs') or []), *(manifest.get('input_images') or []),
                 *((manifest.get('material') or {}).get('related_files') or [])]:
        if item.get('file') == relative and item.get('sha256'):
            expected = item['sha256']
    for region in manifest.get('regions') or []:
        if region.get('mask_file') == relative:
            expected = region.get('mask_sha256')
    return expected


def _members(manifest: dict):
    """Every manifest entry that names a file of the bundle, with the key that names it."""
    for member in (manifest['input_images'] + manifest.get('runtime_inputs', [])
                   + (manifest.get('execution') or {}).get('outputs', [])
                   + (manifest.get('material') or {}).get('related_files', [])):
        yield member, 'file'
    for region in manifest.get('regions', []):
        yield region, 'mask_file'
    if manifest.get('mesh'):
        yield manifest['mesh'], 'turntable'
    if manifest.get('keep_composite'):
        yield manifest['keep_composite'], 'mask_file'


async def _bundle(db, workspace: dict, node: dict, asset_id: str, *, siblings: bool = True,
                  seen: dict | None = None) -> dict:
    """One take's portable bundle: its manifest v4 and every file copied beside it,
    named after `asset_id`, each hashed once and checked against the hash the record
    holds. With `siblings` false, the attempt's other takes keep their records but
    not their bytes. `seen` gathers the stat fingerprint of every file hashed. A take
    that cannot travel comes back refused, with its reason in plain words."""
    nid = node['id']
    # A hidden take is refused before anything of it is read, and named as every
    # read names it: the file name its engine gave it would tell the engine apart.
    hidden = await blind.is_hidden(db, node['turn_id'])
    info = None if hidden else await db._one('SELECT filename FROM material_info WHERE node_id=?', (nid,))
    filename = info['filename'] if info else Path(node['output_path']).name
    item = {'node_id': nid, 'filename': blind.take_name(filename) if hidden else filename,
            'asset_id': asset_id, 'state': 'prepared', 'error': None, 'missing': [], 'completed_at': None,
            'files': [], 'source_nodes': [nid], 'requires_review': False}
    try:
        if hidden:
            raise ValueError('This take belongs to a blind run. Reveal the run before delivering it, '
                             'so its manifest can name its engine.')
        if node['purged_at'] or not Path(node['output_path']).is_file():
            raise ValueError('The original file is unavailable. Its record remains in the project.')
        turn = await db.get_turn(node['turn_id']) if node['turn_id'] else None
        slot = await db.get_slot(node['slot_id']) if node['slot_id'] else None
        parents = [await db.get_node(pid) for pid in node['parents']]
        orbit = Path(node['output_path']).with_suffix(manifests.TURNTABLE_SUFFIX)
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
        if not siblings:
            for output in outputs:
                if output.get('node_id') not in (None, nid) and output.get('file'):
                    extras = [(path, relative) for path, relative in extras if relative != output['file']]
                    output['file'] = None
        manifest['created_at'] = None  # Filled once when the intent is accepted.
        primary = f"{asset_id}{Path(node['output_path']).suffix or '.png'}"
        for index, (path, relative) in enumerate([(Path(node['output_path']), primary), *extras]):
            digest, size, fingerprint = await _hashed(db, Path(path))
            expected = _recorded(manifest, relative, index == 0)
            if expected and digest != expected:
                raise promote.PromoteMaterialError(CHANGED)
            if seen is not None:
                seen[str(path)] = fingerprint
            item['files'].append({'source': str(path), 'relative': relative, 'sha256': digest, 'size_bytes': size})
        item.update(manifest=manifest, primary=primary)
        content = _manifest_bytes(manifest)
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
            if not member['available'] and (siblings or member.get('node_id') in (None, nid)):
                item['missing'].append(f"Related file {member['filename']} is unavailable; its record will be included.")
    except (OSError, ValueError, promote.PromoteMaterialError) as exc:
        item.update(state='incomplete', error=str(exc), requires_review=True, files=[])
    return item


async def _destination(db, body) -> str:
    """The destination a request names, resolved: the hand-off folder when it names none."""
    root = promote.from_windows(body.destination) if body.destination.strip() else str((await _root(db))[0])
    if not Path(root).is_absolute():
        raise ValueError('Choose the full destination path.')
    return str(Path(root).resolve())


async def _chosen(db, wid: str, body) -> tuple[dict, str, list[dict]]:
    """The project, the resolved destination and the chosen takes, refused in words
    when a take is listed twice or belongs to another project."""
    workspace = await CreativeStore(db).project(wid)
    if len(set(body.node_ids)) != len(body.node_ids):
        raise ValueError('Choose each material once in a delivery.')
    root = await _destination(db, body)
    nodes = []
    for nid in body.node_ids:
        node = await db.get_node(nid)
        if not node or node['workspace_id'] != wid:
            raise ValueError('Every delivered item must belong to this project.')
        nodes.append(node)
    return workspace, root, nodes


async def _plans(db, wid: str, body) -> tuple[str, list[dict]]:
    """Each chosen take's portable bundle under its review name: what reuse copies."""
    workspace, root, nodes = await _chosen(db, wid, body)
    return root, [await _bundle(db, workspace, node, 'review-' + node['id']) for node in nodes]


# --- the character package ---

def _words(text: str | None, limit: int = 40) -> str:
    """Lowercase ASCII words joined by hyphens, cut at a word boundary within `limit`."""
    plain = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    slug = ''
    for word in re.findall(r'[a-z0-9]+', plain):
        joined = f'{slug}-{word}' if slug else word
        if len(joined) > limit:
            break
        slug = joined
    return slug or re.sub(r'[^a-z0-9]+', '', plain)[:limit]


async def _name(db, node: dict, filename: str) -> tuple[str, str]:
    """A take's label and package name. A take from a run is named by its grid
    label and a slug of its own words: the line it speaks, else its prompt, else
    its first source's prompt (a mesh's description is optional). Material from no
    run is named by its verb, the start of its id and a slug of its file name.
    Names hold letters, digits, hyphens and one underscore, which every
    mainstream engine's importer takes as they are."""
    turn = await db.get_turn(node['turn_id']) if node['turn_id'] else None
    slot = await db.get_slot(node['slot_id']) if node['slot_id'] else None
    if turn and slot:
        label = re.sub(r'[^A-Za-z0-9]+', '-', grid_label(turn['idx'], slot['pos'], node['output_index'] or 0)).strip('-')
        spoken = ((await db.audio_fact(node['id'])) or {}).get('text') if node['verb'] == 'speak' else None
        text = spoken or node['prompt']
        if not (text or '').strip() and node['parents']:
            text = ((await db.get_node(node['parents'][0])) or {}).get('prompt')
        words = _words(text)
    else:
        label, words = '', _words(Path(filename).stem)
    label = label or f"{_words(node['verb'], 12) or 'take'}-{node['id'][:6]}"
    return label, f'{label}_{words}' if words else label


def _rename(item: dict, stem: str) -> None:
    """Rename a bundle's paths from its asset id to the take's package name. The
    bundle's `.inputs/`, `.outputs/` and `.turntable` become `_inputs/`,
    `_outputs/` and `_turntable`, because Unreal Engine refuses a dot inside a
    folder or asset name."""
    asset = item['asset_id']

    def renamed(relative: str) -> str:
        if not relative.startswith(asset):
            return relative
        rest = relative[len(asset):]
        for old, new in (('.inputs/', '_inputs/'), ('.outputs/', '_outputs/'),
                         (manifests.TURNTABLE_SUFFIX, '_turntable' + Path(manifests.TURNTABLE_SUFFIX).suffix)):
            if rest.startswith(old):
                return stem + new + rest[len(old):]
        return stem + rest

    for member, key in _members(item['manifest']):
        if member.get(key):
            member[key] = renamed(member[key])
    for f in item['files']:
        f['relative'] = renamed(f['relative'])
    item['primary'] = renamed(item['primary'])


def _reference(item: dict, members: dict[str, str], folder: str) -> None:
    """Point the manifest at a source or a take of the same attempt that the package
    also delivers, at its package path relative to this manifest's folder, instead
    of copying its bytes again. Only takes this item still copies from can stop its
    copy when purged."""
    manifest, nid = item['manifest'], item['node_id']
    outputs = (manifest.get('execution') or {}).get('outputs', [])
    related = (manifest.get('material') or {}).get('related_files', [])
    dropped = set()
    for entry in manifest['input_images'] + outputs:
        other = entry.get('node_id')
        if other in members and other != nid:
            if entry.get('file'):
                dropped.add(entry['file'])
            entry['file'] = posixpath.relpath(members[other], folder)
    item['files'] = [f for f in item['files'] if f['relative'] not in dropped]
    copied = {f['relative'] for f in item['files']}
    item['source_nodes'] = list(dict.fromkeys([nid] + [
        entry['node_id'] for entry in manifest['input_images'] + outputs + related
        if entry.get('node_id') not in (None, nid) and entry.get('file') in copied]))


def _png(path: Path) -> bytes:
    """The PNG a game engine imports for an image whose own format it cannot read:
    the first frame's pixels, with the colour profile kept when no mode conversion
    is needed. The same bytes come out every time for the same original."""
    with Image.open(path) as image:
        image.seek(0)
        frame, profile = image, image.info.get('icc_profile')
        if image.mode not in _PNG_MODES:
            alpha = 'A' in image.getbands() or 'transparency' in image.info
            frame, profile = image.convert('RGBA' if alpha else 'RGB'), None
        out = io.BytesIO()
        frame.save(out, format='PNG', icc_profile=profile)
        return out.getvalue()


def _converted(path: Path) -> tuple[str, int]:
    data = _png(path)
    return hashlib.sha256(data).hexdigest(), len(data)


async def _convert(item: dict, node: dict) -> None:
    """Add a PNG beside an image whose format a mainstream engine cannot import. The
    original stays exactly as it is; a PNG that cannot be made is said, never hidden."""
    media = (item['manifest'].get('material') or {}).get('media_type') or mimetypes.guess_type(node['output_path'])[0]
    if media not in NEEDS_PNG:
        return
    kind, engines = NEEDS_PNG[media]
    try:
        digest, size = await asyncio.to_thread(_converted, Path(node['output_path']))
    except (OSError, ValueError, Image.DecompressionBombError):
        item['missing'].append(f"A PNG for {' and '.join(engines)} could not be made from this {kind} image; "
                               'its original is delivered.')
        return
    item['files'].insert(len(item['files']) - 1, {
        'source': node['output_path'], 'relative': PurePosixPath(item['primary']).stem + '.png',
        'sha256': digest, 'size_bytes': size, 'convert': 'png', 'needed_by': list(engines),
        'note': f"{' and '.join(engines)} cannot import {kind}. This PNG holds the original's pixels; "
                'the original beside it is unchanged.'})


async def _plan(db, wid: str, body) -> dict:
    """One delivery's whole package, planned once: every take's place, name,
    manifest, conversion and file hashes, and the decision record for the takes it
    delivers. Database reads hold the store's lock only while they read; files are
    hashed outside it, once."""
    workspace, root, nodes = await _chosen(db, wid, body)
    did = new_id()
    package = f"{_words(workspace['name'], 32) or 'project'}_{did}"
    seen: dict[str, list[int]] = {}
    items = [await _bundle(db, workspace, node, new_id(), siblings=False, seen=seen) for node in nodes]
    taken: set[tuple[str, str]] = set()
    members: dict[str, str] = {}
    for node, item in zip(nodes, items, strict=True):
        place = LAYOUT.get((node['payload'], node['verb'])) or LAYOUT.get((node['payload'], None))
        if place is None and not item['requires_review']:
            item.update(state='incomplete', error='This medium has no place in a character package yet.',
                        requires_review=True, files=[])
        folder, role = place or ('', node['payload'])
        item['folder'] = posixpath.join(package, folder) if folder else package
        if item['requires_review']:
            continue
        label, stem = await _name(db, node, item['filename'])
        unique, count = stem, 1
        while (folder, unique.lower()) in taken:
            count += 1
            unique = f'{stem}-{count}'
        taken.add((folder, unique.lower()))
        _rename(item, unique)
        members[node['id']] = posixpath.join(folder, item['primary'])
        item['take'] = {'label': label, 'role': role, 'payload': node['payload'], 'verb': node['verb'],
                        'media_type': (item['manifest'].get('material') or {}).get('media_type')
                        or mimetypes.guess_type(node['output_path'])[0],
                        'engine': node['engine'], 'prompt': node['prompt'], 'parent_node_ids': node['parents']}
    for node, item in zip(nodes, items, strict=True):
        if not item['requires_review']:
            _reference(item, members, posixpath.dirname(members[node['id']]))
            await _convert(item, node)
    record = await decisions.export(db, wid)
    record['records'] = [line for line in record['records'] if members.keys() & (
        {*line['chosen']} | {candidate['node_id'] for candidate in line['candidates']})]
    copied = {f['source'] for item in items for f in item['files'] if f['source']}
    return {'workspace_id': wid, 'node_ids': list(body.node_ids), 'delivery_id': did, 'destination': root,
            'package': {'version': PACKAGE_VERSION, 'folder': package,
                        'project': {'id': wid, 'name': workspace['name']}, 'decisions': record},
            'items': items, 'fingerprints': {path: seen[path] for path in copied}}


async def review(db, wid: str, body) -> dict:
    """Plan the package once and hold the plan under its token for preparing. Records
    nothing; files are hashed outside the store's lock."""
    plan = await _plan(db, wid, body)
    root, items = plan['destination'], plan['items']
    target = await asyncio.to_thread(promote.target_state, Path(root))
    token = _digest([plan['node_ids'], root, plan['package'], items])
    held = _reviews.setdefault(db, OrderedDict())
    held[token] = plan
    while len(held) > REVIEWS_HELD:
        held.popitem(last=False)
    return {'token': token, 'destination': root, 'target': target,
            'ready': target['ok'] and any(not item['requires_review'] for item in items),
            'errors': _errors(items, target), 'items': [_view_item(item, root) for item in items],
            'package': _package_view(plan['package'], root)}


async def _unchanged(db, plan: dict) -> bool:
    """Whether every file the plan copies keeps the size and modification time it was
    hashed at, and every take it copies from is still unpurged."""
    for path, fingerprint in plan['fingerprints'].items():
        try:
            stat = await asyncio.to_thread(os.stat, path)
        except OSError:
            return False
        if [stat.st_size, stat.st_mtime_ns] != fingerprint:
            return False
    for item in plan['items']:
        for nid in [] if item['requires_review'] else item['source_nodes']:
            node = await db.get_node(nid)
            if node is None or node['purged_at']:
                return False
    return True


async def prepare(db, wid: str, body) -> dict:
    """Record exactly the plan the review token names, once no reviewed file or
    source changed since review. Runs inside the command's transaction and reads
    only file stats and node rows: it hashes nothing."""
    plan = _reviews.get(db, {}).pop(body.review_token, None)
    if (plan is None or plan['workspace_id'] != wid or plan['node_ids'] != list(body.node_ids)
            or plan['destination'] != await _destination(db, body)):
        raise Conflict('The material or delivery context changed. Review this delivery again.')
    if not await _unchanged(db, plan):
        raise Conflict('The material changed while preparing delivery. Review it again.')
    root, items = plan['destination'], plan['items']
    target = await asyncio.to_thread(promote.target_state, Path(root))
    if not target['ok'] or all(item['requires_review'] for item in items):
        raise ValueError(' '.join(_errors(items, target)))
    created = now_iso()
    for item in items:
        if item['requires_review']:
            continue
        item['manifest']['created_at'] = created
        data = _manifest_bytes(item['manifest'])
        next(f for f in item['files'] if f['source'] is None).update(
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    intent = {'id': plan['delivery_id'], 'workspace_id': wid, 'destination': root, 'actor': body.actor.model_dump(),
              'created_at': created, 'items': items, 'package': plan['package']}
    await db.append_event('delivery.prepared', workspace_id=wid, payload=intent)
    return {'id': plan['delivery_id']}


async def _intent(db, did: str) -> dict:
    row = await db._one("SELECT payload_json FROM events WHERE type='delivery.prepared' "
                        "AND json_extract(payload_json,'$.id')=?", (did,))
    if not row:
        raise LookupError('This delivery could not be found.')
    intent = json.loads(row['payload_json'])
    events = await db._all("SELECT type,payload_json FROM events WHERE workspace_id=? "
                           "AND type IN ('delivery.item','delivery.package') "
                           "AND json_extract(payload_json,'$.delivery_id')=? ORDER BY seq", (intent['workspace_id'], did))
    by_node = {item['node_id']: item for item in intent['items']}
    for event in events:
        record = json.loads(event['payload_json'])
        target = intent.get('package') if event['type'] == 'delivery.package' else by_node[record['node_id']]
        if target is not None:
            target.update({k: record[k] for k in ('state', 'error', 'completed_at')})
    return intent


async def state(db, did: str) -> dict:
    async with db._locked():
        intent = await _intent(db, did)
    return {**intent, 'items': [_view_item(item, intent['destination']) for item in intent['items']],
            'package': _package_view(intent.get('package'), intent['destination'])}


async def inventory(db, wid: str) -> list[dict]:
    await CreativeStore(db).project(wid)
    rows = await db._all("SELECT json_extract(payload_json,'$.id') AS id FROM events "
                         "WHERE workspace_id=? AND type='delivery.prepared' ORDER BY seq DESC", (wid,))
    return [await state(db, row['id']) for row in rows]


# --- writing into the destination ---

def _chunks(path: Path):
    if not path.is_file():
        raise ValueError(SOURCE_CHANGED)
    with path.open('rb') as source:
        while chunk := source.read(1 << 20):
            yield chunk


def _conversion(path: Path) -> list[bytes]:
    try:
        return [_png(path)]
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(SOURCE_CHANGED) from exc


def _write(path: Path, digest: str, read, mismatch: str, tag: str) -> None:
    """Write one file under a private name beside its place, verify its SHA-256 as it
    is written, then give it its name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f'.{path.name}.{tag}.part')
    scratch.unlink(missing_ok=True)   # Left by an interrupted copy of this same item.
    try:
        written = hashlib.sha256()
        with scratch.open('xb') as out:
            for chunk in read():
                written.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if written.hexdigest() != digest:
            raise ValueError(mismatch)
        if path.exists() or path.is_symlink():
            raise ValueError(DIFFERENT)
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def _put(destination: Path, folder: Path, entries: list, tag: str, *, allow_copy: bool = True) -> None:
    """Place one item's files. A file already there with the planned bytes is kept; one
    with other bytes is left untouched and refuses the item. Missing files are
    written in order, and a failure removes what this call wrote."""
    pending = []
    for relative, digest, read, mismatch in entries:
        path = folder / relative
        if not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError('A destination folder points outside the chosen delivery directory. Choose another destination.')
        if path.exists() or path.is_symlink():
            if not path.is_file() or _hash(path) != digest:
                raise ValueError(DIFFERENT)
        else:
            pending.append((path, digest, read, mismatch))
    if not pending:
        return
    if not allow_copy:
        raise ValueError('Required material was purged after preparation. Existing delivered files remain untouched.')
    written = []
    try:
        for path, digest, read, mismatch in pending:
            _write(path, digest, read, mismatch, tag)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def _transfer(item: dict, root: str, *, allow_copy: bool) -> None:
    """Place one item: companions and its manifest first, the take's own file last."""
    destination = promote.validate_target(Path(root))
    manifest = _manifest_bytes(item['manifest'])
    entries = []
    for f in item['files']:
        if f.get('convert'):
            read, mismatch = (lambda f=f: _conversion(Path(f['source']))), CONVERSION_CHANGED
        elif f['source']:
            read, mismatch = (lambda f=f: _chunks(Path(f['source']))), SOURCE_CHANGED
        else:
            read, mismatch = (lambda: [manifest]), UNCONFIRMED
        entries.append((f['relative'], f['sha256'], read, mismatch))
    entries.sort(key=lambda entry: entry[0] == item['primary'])
    _put(destination, destination / item['folder'], entries, item['asset_id'], allow_copy=allow_copy)


def _role(item: dict, f: dict) -> str:
    if f['relative'] == item['primary']:
        return item['take']['role']
    if f.get('convert'):
        return 'conversion'
    if f['source'] is None:
        return 'manifest'
    if '_inputs/' in f['relative']:
        return 'input'
    return 'preview' if f['relative'].endswith('_turntable.webp') else 'output'


def _package_files(intent: dict) -> dict[str, bytes]:
    """The package's own files, rendered from the frozen intent and each take's
    recorded completion, so a retry writes the same bytes: the record summary, the
    decision record and last the package manifest, which lists every other file
    with its role, path and SHA-256."""
    package, did = intent['package'], intent['id']
    listed, rows = [], []
    for item in intent['items']:
        if item.get('requires_review'):
            continue
        take = item['take']
        folder = PurePosixPath(item['folder']).relative_to(package['folder'])
        for f in item['files']:
            path = str(folder / f['relative'])
            entry = {'path': path, 'role': _role(item, f), 'sha256': f['sha256'], 'size_bytes': f['size_bytes'],
                     'node_id': item['node_id']}
            if f['relative'] == item['primary']:
                entry.update(asset_id=item['asset_id'], label=take['label'], payload=take['payload'],
                             verb=take['verb'], media_type=take['media_type'],
                             manifest=str(folder / _manifest_name(item)), delivered_at=item['completed_at'])
                rows.append([item['asset_id'], take['label'], take['role'], path, item['node_id'],
                             take['engine'] or '', take['prompt'] or '', json.dumps(take['parent_node_ids']),
                             f['sha256'], item['completed_at'], did])
            elif f.get('convert'):
                entry.update(media_type='image/png', conversion_of=str(folder / item['primary']),
                             needed_by=f['needed_by'], note=f['note'])
            listed.append(entry)
    summary = io.StringIO(newline='')
    writer = csv.writer(summary)
    writer.writerow(SUMMARY)
    writer.writerows([_literal(value) for value in row] for row in rows)
    rendered = {'records.csv': summary.getvalue().encode(),
                'decisions.json': (json.dumps(package['decisions'], indent=2) + '\n').encode()}
    for name, role in (('records.csv', 'record_summary'), ('decisions.json', 'decision_record')):
        listed.append({'path': name, 'role': role, 'sha256': hashlib.sha256(rendered[name]).hexdigest(),
                       'size_bytes': len(rendered[name])})
    manifest = {'package_version': package['version'], 'kind': 'character_package', 'delivery_id': did,
                'project': package['project'], 'created_at': intent['created_at'], 'actor': intent['actor'],
                'files': listed,
                'not_delivered': [{'node_id': item['node_id'], 'filename': item['filename'], 'reason': item['error']}
                                  for item in intent['items'] if item.get('requires_review')]}
    rendered['package.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    return rendered


def _write_package(files: dict[str, bytes], root: str, folder: str, tag: str) -> None:
    destination = promote.validate_target(Path(root))
    entries = [(name, hashlib.sha256(data).hexdigest(), lambda data=data: [data], UNCONFIRMED)
               for name, data in files.items()]
    _put(destination, destination / folder, entries, tag)


async def _off_loop(work, *args, **kwargs) -> str | None:
    """Run one copy on a worker thread to its end, even when the request is cancelled,
    and return its refusal in words, or None."""
    try:
        job = asyncio.create_task(asyncio.to_thread(work, *args, **kwargs))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            await job  # Never release the transfer lock while its copy still runs.
            raise
    except (OSError, ValueError, promote.PromoteTargetError, promote.PromoteMaterialError) as exc:
        return str(exc)
    return None


async def _finish_package(db, did: str) -> None:
    """Write the package's own files once every planned take is in place, and record
    their outcome as a receipt."""
    intent = await _intent(db, did)
    package = intent.get('package')
    planned = [item for item in intent['items'] if not item.get('requires_review')]
    if (not package or package.get('state') == 'complete' or not planned
            or any(item['state'] != 'complete' for item in planned)):
        return
    files = _package_files(intent)
    error = await _off_loop(_write_package, files, intent['destination'], package['folder'], did)
    async with db._tx():
        await db.append_event('delivery.package', workspace_id=intent['workspace_id'], payload={
            'delivery_id': did, 'state': 'incomplete' if error else 'complete', 'error': error,
            'completed_at': None if error else now_iso(),
            'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
                      for name, data in files.items()]})


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
            error = await _off_loop(_transfer, item, intent['destination'], allow_copy=allow_copy)
            async with db._tx():
                result = {'delivery_id': did, 'node_id': item['node_id'], 'state': 'incomplete' if error else 'complete',
                          'error': error, 'completed_at': None if error else now_iso()}
                await db.append_event('delivery.item', workspace_id=intent['workspace_id'], node_id=item['node_id'], payload=result)
                if not error:
                    folder = Path(intent['destination']) / item['folder']
                    await db.append_event('node.promoted', workspace_id=intent['workspace_id'], node_id=item['node_id'],
                        payload={'delivery_id': did, 'asset_id': item['asset_id'], 'actor': intent['actor'],
                                 'manifest_path': str(folder / _manifest_name(item)),
                                 'promoted_path': str(folder / item['primary'])})
                    await db.touch_workspace(intent['workspace_id'])
        await _finish_package(db, did)
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
        writer.writerow([_literal(v) for v in values])
    return stream.getvalue()
