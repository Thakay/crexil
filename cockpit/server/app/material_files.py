"""Read related-file receipts owned by imported material, without execution.

Only material_info.origin_json.retained_files declares ownership. Paths stay
inside the material's own directory; nested source provenance grants no access.
Snapshots check availability cheaply, while downloads and exports verify bytes.
Missing receipts retain their identity and error. This module never copies,
deletes, repairs or retrieves files from a runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from .db import Database

_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_SUFFIX = re.compile(r'\.[A-Za-z0-9]{1,16}\Z')
_FIELDS = {'id', 'path', 'filename', 'role', 'media_type', 'required', 'sha256', 'size_bytes', 'error'}


def _path(node: dict, value: str) -> Path:
    try:
        root = Path(node['output_path']).parent.resolve()
        path = Path(value).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError('A retained file has an invalid owned path.') from exc
    if not path.is_relative_to(root) or path == root:
        raise ValueError('A retained file would leave its material directory.')
    return path


def files_from_origin(node: dict, origin: dict) -> list[dict]:
    """Validate explicit receipts without reading file contents or changing them."""
    if not isinstance(origin, dict):
        raise ValueError('The material origin must be an object.')
    receipts = origin.get('retained_files', [])
    if not isinstance(receipts, list):
        raise ValueError('Retained files must be a list of receipts.')
    found, identities = [], set()
    for item in receipts:
        if not isinstance(item, dict) or set(item) != _FIELDS:
            raise ValueError('A retained file receipt has missing or unsupported fields.')
        identity = item['id']
        if not isinstance(identity, str) or not _TOKEN.fullmatch(identity) or identity in identities:
            raise ValueError('Retained file identities must be safe, distinct names within their material.')
        identities.add(identity)
        for key in ('filename', 'role', 'media_type'):
            if not isinstance(item[key], str) or not item[key].strip() or '\x00' in item[key]:
                raise ValueError(f'A retained file needs a valid {key}.')
        filename = item['filename']
        if filename in {'.', '..'} or '/' in filename or '\\' in filename:
            raise ValueError('A retained filename must be a name, not a path.')
        if type(item['required']) is not bool:
            raise ValueError('A retained file must state whether it is required.')
        digest, size, error = item['sha256'], item['size_bytes'], item['error']
        if digest is not None and (not isinstance(digest, str) or not _HASH.fullmatch(digest)):
            raise ValueError('A retained file has an invalid SHA-256 receipt.')
        if size is not None and (type(size) is not int or size < 0):
            raise ValueError('A retained file has an invalid byte count.')
        if error is not None and (not isinstance(error, str) or not error.strip()):
            raise ValueError('An unavailable retained file needs an explicit error.')
        value = item['path']
        if value is None:
            if error is None:
                raise ValueError('An unavailable retained file needs an explicit error.')
        else:
            if not isinstance(value, str) or not value or '\x00' in value:
                raise ValueError('A retained file has an invalid owned path.')
            _path(node, value)
            if error is None and (digest is None or size is None):
                raise ValueError('An available retained file needs a SHA-256 and byte count.')
        found.append(dict(item))
    return found


async def _receipts(db: Database, node: dict) -> list[dict]:
    row = await db._one('SELECT origin_json FROM material_info WHERE node_id=?', (node['id'],))
    if not row:
        return []
    try:
        origin = json.loads(row['origin_json'])
    except (TypeError, ValueError) as exc:
        raise ValueError('The material origin could not be read.') from exc
    return files_from_origin(node, origin)


def _problem(node: dict, receipt: dict) -> str | None:
    if node.get('purged_at'):
        return 'This material was purged. Its retained files are unavailable.'
    if receipt['error']:
        return receipt['error']
    if receipt['path'] is None or not _path(node, receipt['path']).is_file():
        return 'The retained file is missing from its material directory.'
    return None


def _public(receipt: dict, error: str | None, *, file: str | None = None) -> dict:
    return {key: value for key, value in receipt.items() if key != 'path'} | {
        'error': error, 'available': error is None, 'file': file if error is None else None}


async def view(db: Database, node: dict) -> list[dict]:
    """Expose receipt metadata and current existence without hashing large files."""
    return [_public(receipt, _problem(node, receipt)) for receipt in await _receipts(db, node)]


async def owned(db: Database, nid: str) -> list[Path]:
    """Return fenced paths for purge, including missing or already purged files."""
    node = await db.get_node(nid)
    if not node:
        return []
    return list(dict.fromkeys(_path(node, item['path']) for item in await _receipts(db, node)
                              if item['path'] is not None))


def _verify(node: dict, receipt: dict) -> tuple[Path | None, str | None]:
    error = _problem(node, receipt)
    if error:
        return None, error
    path = _path(node, receipt['path'])
    digest, size = hashlib.sha256(), 0
    try:
        with path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > receipt['size_bytes']:
                    return None, 'The retained file no longer matches its recorded byte count.'
                digest.update(chunk)
    except OSError:
        return None, 'The retained file could not be read from its material directory.'
    if size != receipt['size_bytes']:
        return None, 'The retained file no longer matches its recorded byte count.'
    if digest.hexdigest() != receipt['sha256']:
        return None, 'The retained file no longer matches its recorded SHA-256.'
    return path, None


async def export_files(db: Database, node: dict, asset_id: str) -> tuple[list[dict], list[tuple[Path, str]]]:
    """Return verified export paths and explicit receipts for unavailable members."""
    if not isinstance(asset_id, str) or not _TOKEN.fullmatch(asset_id):
        raise ValueError('The export asset identity must be a safe filename.')
    records, extras = [], []
    for receipt in await _receipts(db, node):
        path, error = await asyncio.to_thread(_verify, node, receipt)
        suffix = Path(receipt['filename']).suffix
        suffix = suffix if _SUFFIX.fullmatch(suffix) else '.bin'
        relative = f"{asset_id}.outputs/{receipt['id']}{suffix}"
        records.append(_public(receipt, error, file=relative))
        if path is not None:
            extras.append((path, relative))
    return records, extras


async def get_file(db: Database, nid: str, fid: str) -> tuple[Path, dict]:
    """Resolve one owned download only while its recorded bytes remain intact."""
    node = await db.get_node(nid)
    if not node:
        raise LookupError('This material could not be found.')
    receipt = next((item for item in await _receipts(db, node) if item['id'] == fid), None)
    if receipt is None:
        raise LookupError('This retained file does not belong to the material.')
    path, error = await asyncio.to_thread(_verify, node, receipt)
    if error:
        raise ValueError(error)
    return path, _public(receipt, None)
