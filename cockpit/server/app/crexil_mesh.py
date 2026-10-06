"""Compile bounded 3D preparations into the existing mesh runner envelope.

The matrix publishes engines and generation controls; the mesh adapter owns
supported verbs and input kinds. Review binds ordered source bytes, including
voxel fields, before the shared command freezes a run. Descriptions remain
provenance: these operations have no text conditioning channel. Repainting and
re-extraction can rebuild geometry and UVs, so neither promises to preserve them.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from . import config, presets
from .mesh import MESH_KINDS, SEEDED_KINDS, fields_sidecar, mesh_envelope, mesh_kind_input, parent_runner_job


ACTIONS = {'mesh': 'generate', 'decimate': 'decimate', 'texture': 'texture'}
LABELS = {'generate': 'Generate 3D', 'decimate': 'Reduce mesh', 'texture': 'Texture mesh'}
ROLES = {'generate': ['image'], 'decimate': ['shape'], 'texture': ['shape', 'appearance']}


def inspect_glb(data: bytes) -> dict:
    """Read a self-contained GLB header without altering or loading its mesh.

    External buffers and images need a package import contract. Refuse them
    here so opening an imported mesh cannot fetch unrelated remote resources.
    Geometry measurements remain unknown until a tool actually measures them.
    """
    if len(data) < 20 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise ValueError('Import an intact GLB 2.0 file with its textures embedded.')
    offset, document, binary = 12, None, None
    try:
        while offset < len(data):
            size, kind = struct.unpack_from('<I4s', data, offset)
            offset += 8
            if size % 4 or offset + size > len(data):
                raise ValueError('The GLB has an incomplete or invalid chunk.')
            chunk = data[offset:offset + size]
            if document is None:
                if kind != b'JSON':
                    raise ValueError('The first GLB chunk must describe the mesh.')
                document = json.loads(chunk)
                if not isinstance(document, dict):
                    raise ValueError('The GLB JSON document must describe an object.')
            elif kind == b'JSON':
                raise ValueError('The GLB must have exactly one JSON document.')
            elif kind == b'BIN\x00':
                if binary is not None:
                    raise ValueError('The GLB has more than one embedded buffer.')
                binary = size
            offset += size
        if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
            raise ValueError('The mesh must use GLB 2.0.')
        buffers = document.get('buffers', [])
        for item in buffers + document.get('images', []):
            if item.get('uri') is not None:
                raise ValueError('Export a self-contained GLB with buffers and textures embedded. Separate or remote files are not imported by this action.')
        if len(buffers) > 1 or any(type(item.get('byteLength')) is not int or item['byteLength'] < 0 or item['byteLength'] > (binary or 0) for item in buffers):
            raise ValueError('The GLB embedded buffer is missing or incomplete.')
        return {'format': 'GLB 2.0', 'self_contained': True}
    except (struct.error, UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise ValueError('The GLB could not be read. Export the original mesh again with embedded textures.') from exc


def inputs(engine: dict, kind: str) -> list[dict]:
    specs = [{'key': key, **value, 'reason': value.get('why', '')}
             for key, value in engine.get('params', {}).get(kind, {}).items()]
    if kind in SEEDED_KINDS:
        specs.append({'key': 'seed', 'label': 'Seed', 'type': 'seed', 'default': {'mode': 'random'}})
    if kind == 'decimate':
        specs.append({'key': 'target_triangles', 'label': 'Target triangles', 'type': 'int',
                      'min': 100, 'max': 4_000_000, 'required': True})
        if mesh_kind_input(kind, engine['id']) == 'fields':
            specs.append({'key': 'texture_size', 'label': 'Texture size', 'type': 'enum',
                          'choices': [1024, 2048, 4096], 'default': 2048})
        else:
            specs.append({'key': 'force', 'label': 'Force reduction through texture seams',
                          'type': 'bool', 'default': False})
    return specs


def limitations(engine: str, kind: str) -> list[str]:
    notes = ['Descriptions and source notes are recorded for participants; this operation does not read text.',
             'Local operations share one queue with image generation. No provider charge.']
    if kind == 'texture':
        notes.append('Hunyuan Paint may simplify larger meshes toward 40,000 triangles and makes new UVs before painting. Geometry and UVs are not locked.')
    elif kind == 'decimate':
        notes.append('One deterministic result per engine. The record keeps both the requested and actual triangle counts.')
        if mesh_kind_input(kind, engine) == 'fields':
            notes.append('Re-extraction needs retained voxel fields and creates new topology, UVs and baked textures.')
        else:
            notes.append('Texture seams may prevent reaching the target. Force can collapse those seams and damage texture mapping.')
    return notes


def catalogue(stack) -> list[dict]:
    from .api_v4_mesh import _matrix_engines
    from .crexil_execution import digest
    benches = stack.scheduler.snapshot()['benches']
    results = []
    for engine in _matrix_engines().values():
        for action, kind in ACTIONS.items():
            if engine['id'] not in MESH_KINDS[kind] or kind not in engine.get('verbs', []):
                continue
            specs = inputs(engine, kind)
            notes = limitations(engine['id'], kind)
            unavailable = config.CREXIL_PREVIEW or 'lane:local' in benches or f"engine:{engine['id']}" in benches
            results.append({'id': f"mesh:{kind}:{engine['id']}", 'engine_id': engine['id'], 'mesh_kind': kind,
                'label': f"{LABELS[kind]} · {engine['name']}",
                'revision': digest([engine, kind, specs, notes, config.THREED_URL])[:16],
                'actions': [action], 'inputs': specs, 'source_limit': len(ROLES[kind]),
                'source_roles': ROLES[kind], 'output_payload': '3d', 'limitations': notes,
                'sources': [{'role': role, 'accepts': ['image' if role in ('image', 'appearance') else '3d']}
                            for role in ROLES[kind]], 'price_unit': 'attempt',
                'attempt_max': 4 if kind in SEEDED_KINDS else 1,
                'evidence': 'declared', 'availability': 'unavailable' if unavailable else 'unverified',
                'reasons': ['Generation is disconnected in this isolated preview.'] if config.CREXIL_PREVIEW else
                           ['The local runtime is paused or unavailable.'] if unavailable else [],
                'execution_location': 'Configured 3D runner', 'data_destination': config.THREED_URL,
                'price_usd': 0.0, 'maximum_usd_per_attempt': 0.0, 'cost_basis': 'Unmetered local operation'})
    return results


def compile_request(op: dict, setup, choice, nodes: list[dict]) -> dict:
    from .crexil_execution import strict_value
    kind = op['mesh_kind']
    expected = ['image'] if kind == 'generate' else ['3d', 'image'] if kind == 'texture' else ['3d']
    if [node['payload'] for node in nodes] != expected:
        raise ValueError(f"{LABELS[kind]} needs sources in this order: {', '.join(ROLES[kind])}.")
    for ref, role in zip(setup.references, ROLES[kind], strict=True):
        if ref.strength is not None or ref.role not in (None, role):
            raise ValueError(f'This input is the {role} source. It has no strength control; clear unsupported source values.')
    if choice.count > op['attempt_max']:
        raise ValueError(f"This operation allows at most {op['attempt_max']} attempts per run.")
    given = {**setup.controls, **choice.controls}
    applicable = {s['key']: s for s in op['inputs'] if s.get('supported', True)}
    unknown = set(given) - applicable.keys()
    if unknown:
        raise ValueError(f"Unsupported controls: {', '.join(sorted(unknown))}. Clear their saved values explicitly.")
    values = {}
    for key, spec in applicable.items():
        value = given.get(key, spec.get('default'))
        if spec.get('required') and value is None:
            raise ValueError(f"{spec.get('label', key)} is required.")
        if spec['type'] == 'bool' and type(value) is not bool:
            raise ValueError(f'{key} must be true or false.')
        if spec['type'] == 'enum' and not any(type(value) is type(v) and value == v for v in spec['choices']):
            raise ValueError(f"{key} must be one of {', '.join(map(str, spec['choices']))}.")
        values[key] = strict_value(spec, value)
    paths = [Path(node['output_path']) for node in nodes]
    if mesh_kind_input(kind, op['engine_id']) == 'fields':
        field = fields_sidecar(nodes[0]['output_path'])
        if not field.is_file():
            raise ValueError('Re-extraction needs retained voxel fields. Collect the missing fields or choose gltfpack to reduce the GLB.')
        paths.append(field)
    source_hashes = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    return {'controls': values, 'source_hashes': source_hashes,
            'source_path': nodes[0]['output_path'], 'ref_path': nodes[1]['output_path'] if kind == 'texture' else None,
            'mesh_job_ref': parent_runner_job(nodes[0]) if kind != 'generate' else None}


def frozen_specs(op: dict, compiled: dict, parents: list[str], setup) -> list[dict]:
    values = compiled['controls']
    params = {key: value for key, value in values.items() if key != 'seed'}
    policy = presets.resolve_seed_policy(values.get('seed'))
    specs = []
    for index in range(op['count']):
        seed = presets.take_seed(policy, index) if op['mesh_kind'] in SEEDED_KINDS else None
        envelope = mesh_envelope(op['engine_id'], compiled['source_path'], kind=op['mesh_kind'],
            seed=seed, params=params, mesh_job_ref=compiled['mesh_job_ref'], ref_path=compiled['ref_path'])
        envelope['mesh_job']['source_hashes'] = compiled['source_hashes']
        specs.append({'engine': op['engine_id'], 'seed': seed, 'submitted_graph': envelope,
            'external': None, 'committed_usd': 0.0,
            'dials': {'capability': 'to_3d', 'kind': op['mesh_kind'], 'parents': parents,
                'source_node_id': parents[0], 'engine_id': op['engine_id'], 'params': params, 'seed_value': seed,
                'crexil': {'operation_id': op['id'], 'operation_revision': op['revision'],
                    'automatic_retry': False, 'spending_policy': 'hard_cap', 'source_uploads': [],
                    'kind_of_work': setup.purpose, 'output_ports': []}}})
    return specs
