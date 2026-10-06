"""Saved operation descriptions, installation bindings and runtime observations.

Original supplied text and every edited revision are retained. Incomplete imports
are preparation; only a fully mapped interface can enter shared execution review.
Discovery reads the configured runtime and never installs, uploads or dispatches.
Declared meaning, observed execution and product qualification remain distinct.
Arbitrary node internals stay with ComfyUI; the public interface is the boundary.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from . import config
from .crexil_schemas import OperationBinding, OperationDefinition
from .crexil_store import Conflict
from .db import new_id, now_iso


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def api_graph(value) -> bool:
    return isinstance(value, dict) and bool(value) and all(
        isinstance(node, dict) and isinstance(node.get('class_type'), str)
        and isinstance(node.get('inputs'), dict) for node in value.values())


def recognize(text: str) -> tuple[str, OperationDefinition]:
    definition = OperationDefinition()
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return 'unrecognized', definition
    if isinstance(value, dict) and value.get('format') == 'crexil-operation-v1':
        try:
            return 'prepared_description', OperationDefinition.model_validate(value['definition'])
        except (ValueError, KeyError):
            return 'incomplete_description', definition
    graph = value.get('prompt') if isinstance(value, dict) and api_graph(value.get('prompt')) else value
    if api_graph(graph):
        definition.graph = graph
        return 'comfy_api', definition
    if isinstance(value, dict) and isinstance(value.get('nodes'), list):
        return 'comfy_editor', definition
    return 'unrecognized', definition


def linked(value, graph) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in graph and type(value[1]) is int


def active(item: dict, controls: dict) -> bool:
    return all(controls.get(key) == value for key, value in item.get('when', {}).items())


def secret_fields(graph: dict) -> list[str]:
    result = []
    for nid, node in graph.items():
        for field, value in node.get('inputs', {}).items() if isinstance(node, dict) else []:
            name = field.lower()
            if any(word in name for word in ('api_key', 'apikey', 'password', 'access_token', 'secret')) and value:
                result.append(f'{nid}.{field}')
    return result


def structural_gaps(definition: OperationDefinition, source_kind: str = '') -> list[str]:
    graph = definition.graph
    if not api_graph(graph):
        return ['This is editor JSON. Export the intended active graph in ComfyUI API format, then supply it as an explicit adaptation.'] if source_kind == 'comfy_editor' else ['Supply a ComfyUI API graph. The original definition has been kept.']
    gaps = []
    if len(graph) > 512:
        gaps.append('This interface supports up to 512 graph nodes. Prepare a bounded graph.')
    if not definition.actions:
        gaps.append('Describe at least one action this operation can perform.')
    keys, targets = set(), set()
    for control in definition.inputs:
        if not control.key or control.key in keys:
            gaps.append('Give every public control a distinct nonempty key.')
        keys.add(control.key)
        if not control.targets:
            gaps.append(f'Control {control.key or "unnamed"} needs a graph input mapping.')
        if control.type == 'enum' and not control.choices:
            gaps.append(f'Control {control.key} needs choices.')
        for value in (control.min, control.max, control.step):
            if value is not None and not math.isfinite(value):
                gaps.append(f'Control {control.key} needs finite bounds.')
        if control.step is not None and control.step <= 0:
            gaps.append(f'Control {control.key} needs a positive step.')
        if control.min is not None and control.max is not None and control.min > control.max:
            gaps.append(f'Control {control.key} has reversed bounds.')
    if len([c for c in definition.inputs if c.type == 'seed']) > 1:
        gaps.append('Expose one seed policy with all of its targets.')
    for item in [*definition.inputs, *definition.sources, *definition.outputs]:
        if any(key not in keys for key in item.when):
            gaps.append('A conditional requirement names a missing public control.')
    for label, mapped in [(f'Control {c.key}', c.targets) for c in definition.inputs] + [
            (f'Source {i + 1}', [port]) for i, port in enumerate(definition.sources)]:
        for target in mapped:
            node = graph.get(target.node_id, {})
            if any(word in target.field.lower() for word in ('api_key', 'apikey', 'password', 'access_token', 'secret')):
                gaps.append(f'{label} maps a credential field. Credentials belong to the runtime installation, not public creative controls.')
            if node.get('class_type') == 'SaveImage' and target.field == 'filename_prefix':
                gaps.append('SaveImage filename_prefix belongs to execution storage. It cannot be exposed as a control or source.')
            value = node.get('inputs', {}).get(target.field)
            if target.field not in node.get('inputs', {}) or linked(value, graph):
                gaps.append(f'{label} must map to an existing literal graph input.')
            pair = (target.node_id, target.field)
            if pair in targets:
                gaps.append(f'{label} overlaps another public input mapping.')
            targets.add(pair)
    for port in definition.sources:
        if not isinstance(graph.get(port.node_id, {}).get('inputs', {}).get(port.field), str):
            gaps.append(f'Source {port.label or port.node_id} needs an image filename input.')
    if not definition.outputs:
        gaps.append('Describe the files this operation returns and choose its primary result.')
    if not any(port.primary for port in definition.outputs):
        gaps.append('Choose a primary result for completion.')
    output_keys = set()
    for port in definition.outputs:
        if port.node_id not in graph or not port.field:
            gaps.append('Each result needs a graph node and runtime file field.')
        pair = (port.node_id, port.field)
        for old_node, old_field, old_ordinal in output_keys:
            if pair == (old_node, old_field) and (old_ordinal is None or port.ordinal is None or old_ordinal == port.ordinal):
                gaps.append('Result selectors overlap. Give every returned file one meaning.')
        output_keys.add((*pair, port.ordinal))
        if port.primary and not port.required:
            gaps.append('The primary result must be required.')
    if secret_fields(graph):
        gaps.append('Move embedded credentials to the runtime installation. This interface does not bind credentials inside a graph.')
    return list(dict.fromkeys(gaps))


def revision_id(item: dict) -> str:
    return digest([item['id'], item['revision'], item['definition'], item['binding'], config.COMFYUI_URL])[:16]


class OperationRegistry:
    def __init__(self, db):
        self.db = db

    async def get(self, rid: str, *, details=True) -> dict:
        row = await self.db._one('SELECT r.*,v.name,v.definition_json,v.binding_json,v.adaptation_note,v.actor_json,v.created_at AS updated_at '
            'FROM operation_registrations r JOIN operation_revisions v ON v.registration_id=r.id AND v.revision=r.revision WHERE r.id=?', (rid,))
        if row is None:
            raise LookupError('This operation registration is missing.')
        item = dict(row)
        for key in ('source', 'definition', 'binding', 'actor'):
            item[key] = json.loads(item.pop(key + '_json'))
        item['operation_revision'] = revision_id(item)
        item['gaps'] = structural_gaps(OperationDefinition.model_validate(item['definition']), item['source_kind'])
        found = await self.db._one('SELECT * FROM operation_inspections WHERE registration_id=? AND revision=? AND runtime_url=? ORDER BY created_at DESC LIMIT 1',
            (rid, item['revision'], config.COMFYUI_URL))
        item['inspection'] = {**json.loads(found['report_json']), 'id': found['id'], 'checked_at': found['created_at'], 'runtime_url': found['runtime_url']} if found else None
        if item['inspection']:
            item['gaps'] = list(dict.fromkeys(item['gaps'] + runtime_gaps(OperationDefinition.model_validate(item['definition']), item['inspection'])))
        item['revisions'] = [dict(r) for r in await self.db._all('SELECT revision,name,adaptation_note,created_at FROM operation_revisions WHERE registration_id=? ORDER BY revision', (rid,))] if details else []
        item['evidence'] = []
        if details:
            slots = await self.db._all("SELECT s.id,s.status,s.attempt,s.kind,s.cause,s.job_ref,s.started_at,s.finished_at,s.dials_json,t.workspace_id FROM slots s JOIN turns t ON t.id=s.turn_id WHERE s.engine=? ORDER BY s.created_at", ('custom-' + rid,))
            for slot in slots:
                value = dict(slot)
                frozen = json.loads(value.pop('dials_json')).get('crexil', {})
                item['evidence'].append({**value, 'operation_revision': frozen.get('operation_revision'),
                    'registration_revision': frozen.get('registration_revision'), 'kind_of_work': frozen.get('kind_of_work', 'creative_request')})
        return item

    async def list(self, *, details=True) -> list[dict]:
        return [await self.get(row['id'], details=details) for row in await self.db._all('SELECT id FROM operation_registrations ORDER BY created_at,id')]

    async def create(self, body) -> dict:
        text, source = body.source_text, {'kind': 'supplied', 'actor': body.actor.model_dump()}
        if body.source_node_id:
            info = await self.db._one('SELECT * FROM material_info WHERE node_id=?', (body.source_node_id,))
            if not info:
                raise ValueError('That material has no retained import metadata.')
            metadata = json.loads(info['origin_json']).get('embedded_metadata', {})
            text = metadata.get('prompt') or metadata.get('workflow') or ''
            if not text:
                raise ValueError('This image has no reusable workflow metadata. Its original image remains unchanged.')
            source.update(node_id=body.source_node_id, material_sha256=info['sha256'], kind='supplied_image_metadata')
        kind, definition = recognize(text)
        rid, now = new_id(), now_iso()
        await self.db.conn.execute('INSERT INTO operation_registrations VALUES(?,?,?,?,?,?,0,?)',
            (rid, body.source_name, text, hashlib.sha256(text.encode()).hexdigest(), kind, json.dumps(source), now))
        await self.db.conn.execute('INSERT INTO operation_revisions VALUES(?,0,?,?,?,?,?,?)',
            (rid, body.name.strip(), definition.model_dump_json(), OperationBinding().model_dump_json(), '', body.actor.model_dump_json(), now))
        await self.db.append_event('operation.imported', payload={'registration_id': rid, 'actor': body.actor.model_dump(), 'source_kind': kind})
        return await self.get(rid)

    async def update(self, rid: str, body) -> dict:
        previous = await self.get(rid)
        if previous['revision'] != body.revision:
            raise Conflict('This operation changed in another view. Reload before saving.')
        if previous['definition']['graph'] != body.definition.graph and not body.adaptation_note.strip():
            raise ValueError('Describe the graph adaptation before saving it. The original definition will be retained.')
        binding = body.binding.model_dump()
        if binding['cost_review'] == 'unmetered' and not binding['cost_note'].strip():
            raise ValueError('Record why this graph and its exposed controls use no paid processing.')
        revision = body.revision + 1
        await self.db.conn.execute('INSERT INTO operation_revisions VALUES(?,?,?,?,?,?,?,?)',
            (rid, revision, body.name.strip(), body.definition.model_dump_json(), json.dumps(binding), body.adaptation_note, body.actor.model_dump_json(), now_iso()))
        await self.db.conn.execute('UPDATE operation_registrations SET revision=? WHERE id=?', (revision, rid))
        await self.db.append_event('operation.revised', payload={'registration_id': rid, 'revision': revision,
            'actor': body.actor.model_dump(), 'adaptation_note': body.adaptation_note})
        return await self.get(rid)

    async def save_inspection(self, rid: str, revision: int, report: dict) -> dict:
        current = await self.get(rid)
        if current['revision'] != revision:
            raise Conflict('The operation changed during discovery. Inspect its current revision.')
        iid, now = new_id(), now_iso()
        await self.db.conn.execute('INSERT INTO operation_inspections VALUES(?,?,?,?,?,?)',
            (iid, rid, revision, config.COMFYUI_URL, json.dumps(report), now))
        return {**report, 'id': iid, 'checked_at': now, 'runtime_url': config.COMFYUI_URL}


async def discover(definition: OperationDefinition) -> dict:
    """Read registered node interfaces, including advertised model/asset choices."""
    gaps = structural_gaps(definition)
    report = {'state': 'unknown', 'nodes': {}, 'findings': [], 'gaps': []}
    if config.CREXIL_PREVIEW:
        report['gaps'] = ['Runtime discovery is disconnected in this isolated preview.']
        return report
    if not api_graph(definition.graph) or len(definition.graph) > 512:
        report['gaps'] = gaps
        return report
    semaphore = asyncio.Semaphore(6)
    async with httpx.AsyncClient(base_url=config.COMFYUI_URL, timeout=8) as client:
        async def read(name):
            async with semaphore:
                try:
                    response = await client.get('/api/object_info/' + quote(name, safe=''))
                    response.raise_for_status()
                    result = response.json()
                    return name, result.get(name) if isinstance(result, dict) else None, None
                except (httpx.HTTPError, ValueError) as exc:
                    return name, None, type(exc).__name__
        found = await asyncio.gather(*(read(name) for name in sorted({n['class_type'] for n in definition.graph.values()})))
    for name, info, error in found:
        if isinstance(info, dict):
            report['nodes'][name] = info
            report['findings'].append({'kind': 'node', 'name': name, 'state': 'advertised', 'package': info.get('python_module')})
        else:
            report['gaps'].append(f'{name}: runtime could not be inspected ({error}).' if error else f'{name}: node is missing from the configured runtime. Install its package with the runtime tools.')
    report['state'] = 'inspected' if not any(error for _, _, error in found) else 'unknown'
    report['interface_sha256'] = digest(report['nodes'])
    report['limitations'] = ['Advertised nodes and filename choices do not prove model integrity, memory fit, hidden downloads or successful execution.']
    return report


def runtime_gaps(definition: OperationDefinition, report: dict | None, graph: dict | None = None) -> list[str]:
    if not report:
        return ['Inspect the configured runtime to check required nodes, models and assets.']
    gaps = list(report['gaps'])
    graph = graph if graph is not None else definition.graph
    if not api_graph(graph):
        return gaps
    source_targets = {(p.node_id, p.field) for p in definition.sources}
    for nid, node in graph.items():
        info = report['nodes'].get(node['class_type'])
        if not info:
            continue
        ports = info.get('input', {})
        specs = {**ports.get('required', {}), **ports.get('optional', {})}
        for field in ports.get('required', {}):
            if field not in node['inputs']:
                gaps.append(f'Node {nid} ({node["class_type"]}) needs {field}. Map or adapt this required input.')
        for field, value in node['inputs'].items():
            spec = specs.get(field)
            if not spec:
                gaps.append(f'Node {nid}: input {field} is not advertised by this runtime. Review the graph adaptation.')
                continue
            if linked(value, graph):
                upstream = report['nodes'].get(graph[value[0]]['class_type'])
                if upstream:
                    outputs = upstream.get('output', [])
                    if not 0 <= value[1] < len(outputs):
                        gaps.append(f'Node {nid}.{field} links to a missing output port.')
                    elif isinstance(spec[0], str) and spec[0] != '*' and outputs[value[1]] not in (spec[0], '*'):
                        gaps.append(f'Node {nid}.{field} has incompatible connected types.')
                continue
            typ, options = spec[0], spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if (nid, field) in source_targets:
                if typ != 'STRING' and not options.get('image_upload'):
                    gaps.append(f'Node {nid}.{field} does not advertise an uploaded image filename input.')
                continue
            if isinstance(typ, list) and value not in typ:
                gaps.append(f'Node {nid}.{field}: {str(value)[:180]} is not an available choice. Supply the required model or asset, or record an adaptation.')
            elif isinstance(typ, str):
                valid = {'STRING': isinstance(value, str), 'INT': type(value) is int,
                         'FLOAT': type(value) in (int, float), 'BOOLEAN': type(value) is bool}
                if typ in valid and not valid[typ]:
                    gaps.append(f'Node {nid}.{field} needs {typ.lower()}.')
                elif typ not in valid:
                    gaps.append(f'Node {nid}.{field} needs a connected {typ} value. This public interface supplies scalar controls and uploaded images.')
                elif type(value) in (int, float) and (not math.isfinite(value) or
                        options.get('min', value) > value or options.get('max', value) < value):
                    gaps.append(f'Node {nid}.{field} is outside the runtime range.')
    for port in definition.outputs:
        node = graph.get(port.node_id)
        info = report['nodes'].get(node['class_type']) if node else None
        if info and not info.get('output_node'):
            gaps.append(f'Result node {port.node_id} is not advertised as a runtime output node. Add collection explicitly.')
    return list(dict.fromkeys(gaps))


def binding_gaps(binding: dict) -> list[str]:
    gaps = []
    if binding['cost_review'] != 'unmetered':
        gaps.append('Processing cost is unresolved. This connector currently executes only reviewed unmetered custom graphs.')
    if binding['downloads'] in ('unknown', 'required'):
        gaps.append('First-use dependencies are unresolved. Record required downloads and prepare them with the runtime tools.')
    return gaps


def inspection_fresh(report: dict | None) -> bool:
    if not report or report.get('runtime_url') != config.COMFYUI_URL:
        return False
    return (datetime.now(timezone.utc) - datetime.fromisoformat(report['checked_at'])).total_seconds() < 60


async def refresh_for_direction(stack, did: str) -> None:
    from .crexil_store import CreativeStore
    direction = await CreativeStore(stack.db).direction(did)
    if direction['state'] != 'draft':
        return
    registry = OperationRegistry(stack.db)
    for choice in direction['setup'].get('operations', []):
        oid = choice['operation_id']
        if not oid.startswith('custom-'):
            continue
        try:
            item = await registry.get(oid[7:])
        except LookupError:
            continue
        if not inspection_fresh(item['inspection']):
            report = await discover(OperationDefinition.model_validate(item['definition']))
            async with stack.db._tx():
                await registry.save_inspection(item['id'], item['revision'], report)


def compile_request(item: dict, setup, choice):
    from .crexil_execution import strict_value
    definition = OperationDefinition.model_validate(item['definition'])
    gaps = structural_gaps(definition, item['source_kind']) + binding_gaps(item['binding'])
    if gaps:
        raise ValueError(' '.join(gaps))
    graph = copy.deepcopy(definition.graph)
    given = {**setup.controls, **choice.controls}
    controls = {c.key: given.get(c.key, setup.prompt if c.key == 'prompt' else c.default) for c in definition.inputs}
    if set(given) - set(controls):
        raise ValueError('Unsupported control values were retained. Resolve them before submission: ' + ', '.join(sorted(set(given) - set(controls))))
    seed_targets = []
    prompt_control = next((c for c in definition.inputs if c.key == 'prompt' and c.type == 'text' and active(c.model_dump(), controls)), None)
    if setup.prompt.strip() and prompt_control is None:
        raise ValueError('This operation has no active prompt control. Clear the saved prompt explicitly or map it to the intended instruction input.')
    for control in definition.inputs:
        spec = control.model_dump(exclude_none=True)
        if not active(spec, controls):
            if control.key in given:
                raise ValueError(f'{control.key} is inactive for these controls. Its value was kept; clear it explicitly.')
            continue
        value = controls[control.key]
        if control.type == 'bool' and type(value) is not bool:
            raise ValueError(f'{control.key} needs true or false.')
        value = strict_value(spec, value)
        controls[control.key] = value
        if control.type == 'seed':
            seed_targets = [t.model_dump() for t in control.targets]
            if value['mode'] != 'random':
                for target in control.targets:
                    graph[target.node_id]['inputs'][target.field] = value['value']
            continue
        for target in control.targets:
            graph[target.node_id]['inputs'][target.field] = value
    sources = [p.model_dump() for p in definition.sources if active(p.model_dump(), controls)]
    if len(sources) != len(setup.references):
        raise ValueError(f'This operation needs exactly {len(sources)} ordered sources for these controls. All supplied sources were kept.')
    for index, (port, ref) in enumerate(zip(sources, setup.references, strict=True)):
        if ref.role and ref.role != port['role']:
            raise ValueError(f'Source {index + 1} must use its declared role: {port["role"] or "unspecified"}.')
        if ref.strength is not None:
            raise ValueError('Source strength needs an exposed public control. Its saved value was kept.')
        graph[port['node_id']]['inputs'][port['field']] = f'crexil-source-{index + 1}.png'
    contributions = [f'Source {i + 1}: {ref.contribution}' for i, ref in enumerate(setup.references) if ref.contribution.strip()]
    if contributions:
        prompt = next((c for c in definition.inputs if c.key == 'prompt' and c.type == 'text'), None)
        if not prompt or not active(prompt.model_dump(), controls):
            raise ValueError('This operation has no active prompt control for contribution instructions.')
        controls['prompt'] += '\n\n' + '\n'.join(contributions)
        for target in prompt.targets:
            graph[target.node_id]['inputs'][target.field] = controls['prompt']
    outputs = [p.model_dump() for p in definition.outputs if active(p.model_dump(), controls)]
    primary = [p for p in outputs if p['primary']]
    if len(primary) != 1:
        raise ValueError('These controls must select exactly one required primary result.')
    gaps = runtime_gaps(definition, item['inspection'], graph)
    if gaps:
        raise ValueError(' '.join(gaps))
    return controls, seed_targets, graph, sources, outputs


def seed_range(item: dict, targets: list[dict]) -> tuple[int, int]:
    from .presets import MAX_SEED
    low, high = 0, MAX_SEED - 1
    for target in targets:
        node = item['definition']['graph'][target['node_id']]
        info = item['inspection']['nodes'][node['class_type']]['input']
        spec = {**info.get('required', {}), **info.get('optional', {})}[target['field']]
        options = spec[1] if len(spec) > 1 else {}
        low, high = max(low, int(options.get('min', low))), min(high, int(options.get('max', high)))
    if low > high:
        raise ValueError('The mapped seed inputs have no common supported range.')
    return low, high


async def catalogue(stack, definitions: dict | None = None) -> list[dict]:
    results = []
    benches = stack.scheduler.snapshot()['benches']
    for item in await OperationRegistry(stack.db).list(details=False):
        if definitions is not None:
            definitions['custom-' + item['id']] = item
        definition = OperationDefinition.model_validate(item['definition'])
        reasons = item['gaps'] + binding_gaps(item['binding'])
        unavailable = config.CREXIL_PREVIEW or 'lane:local' in benches or 'engine:custom-' + item['id'] in benches
        if config.CREXIL_PREVIEW:
            reasons.append('Generation is disconnected in this isolated preview.')
        elif unavailable:
            reasons.append('The local runtime is paused or unavailable.')
        results.append({'id': 'custom-' + item['id'], 'label': item['name'], 'revision': item['operation_revision'],
            'actions': definition.actions, 'inputs': [c.model_dump(exclude_none=True) for c in definition.inputs],
            'source_limit': len(definition.sources), 'evidence': 'declared',
            'availability': 'unavailable' if unavailable else 'incomplete' if reasons else 'unverified',
            'reasons': reasons, 'execution_location': 'Configured ComfyUI runtime', 'data_destination': config.COMFYUI_URL,
            'price_usd': 0 if not binding_gaps(item['binding']) else None,
            'maximum_usd_per_attempt': 0 if not binding_gaps(item['binding']) else None,
            'cost_basis': 'Installation owner declaration for this graph and exposed controls' if not binding_gaps(item['binding']) else 'Unresolved processing cost'})
    return results
