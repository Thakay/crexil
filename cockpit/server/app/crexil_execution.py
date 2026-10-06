"""Review and freeze shared creative requests before handing them to the lanes.

Reviews have no engine side effects. Their token binds three parts separately:
the request (preparation, brief, project instructions, source bytes, operation
revisions, destination and cost bounds), the spending limits, and the spending
already recorded. Submission names the part that moved since review. Spending
recorded by other work alone never refuses a request that still fits its limits,
because the limits are checked again inside the submitting transaction (law 22).
Submission and its receipt commit with every prepared slot before scheduling.
A delivered command can only adopt that same committed turn, including after
restart. Estimates never count as enforceable spending bounds. Preset zero-cost
attestations apply only to the exact reviewed workflow bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
from decimal import Decimal
from pathlib import Path

from . import config, presets
from . import blind, hosted_images, crexil_audio, crexil_budget, crexil_mesh
from .crexil_schemas import Preparation
from .crexil_store import Conflict, CreativeStore
from .db import new_id


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class SpendingChanged(Conflict):
    """Spending or its limits moved since review while the request stayed the same.
    Answered as a 409 whose `X-Crexil-Conflict` header says `spending`, so a
    client reviews again and shows the current totals."""
    code = 'spending'


# A review token stays 64 hex characters: the request, then the limits, then the
# spending already recorded, each the leading characters of its own digest.
TOKEN_PARTS = (('request', 32), ('limits', 16), ('spending', 16))


def review_token(request: dict, limits: dict, spending: dict) -> str:
    return digest(request)[:32] + digest(limits)[:16] + digest(spending)[:16]


def token_change(reviewed: str, current: str) -> str | None:
    """The first token part that differs, or None when the tokens match."""
    start = 0
    for name, width in TOKEN_PARTS:
        if reviewed[start:start + width] != current[start:start + width]:
            return name
        start += width
    return None


def workflow_hashes(preset: dict) -> dict[str, str]:
    return {cap: hashlib.sha256((config.WORKFLOWS_DIR / f'{ref}.json').read_bytes()).hexdigest()
            for cap, ref in preset.get('workflows', {}).items()}


def catalogue(stack, graphs: dict | None = None) -> list[dict]:
    results = []
    preview = config.CREXIL_PREVIEW
    benches = stack.scheduler.snapshot()['benches']
    for preset in stack.catalog.values():
        raw = {cap: (config.WORKFLOWS_DIR / f'{ref}.json').read_bytes()
               for cap, ref in preset.get('workflows', {}).items()}
        hashes = {cap: hashlib.sha256(blob).hexdigest() for cap, blob in raw.items()}
        if graphs is not None:
            for cap, blob in raw.items():
                graphs[(preset['id'], cap)] = json.loads(blob)
        cost = preset.get('execution_cost', {})
        reviewed = cost.get('workflow_sha256') == hashes
        bound = cost.get('maximum_usd_per_attempt') if reviewed else None
        # These attestations describe unmetered graphs. A paid graph needs an
        # adapter that enforces its provider's ceiling, not just a declaration.
        bound = 0.0 if type(bound) in (int, float) and bound == 0 else None
        actions = ['generate'] if 'txt2img' in preset.get('workflows', {}) else []
        if 'img2img' in preset.get('workflows', {}):
            actions.append('continue')
        if preset.get('source_ports'):
            actions.append('combine')
        if {'inpaint_modify', 'inpaint_replace', 'remove'} & set(preset.get('workflows', {})):
            actions.append('region')
        unavailable = preview or 'lane:local' in benches or f"engine:{preset['id']}" in benches
        results.append({'id': preset['id'], 'label': preset['display_name'],
            'revision': digest([preset, hashes])[:16], 'actions': actions,
            'inputs': preset.get('inputs', []), 'source_limit': max([1, *[len(ports) for ports in preset.get('source_ports', {}).values()]]),
            'evidence': 'declared', 'availability': 'unavailable' if unavailable else 'unverified',
            'reasons': ['Generation is disconnected in this isolated preview.'] if preview else
                       ['The local runtime is paused or unavailable.'] if unavailable else [],
            'execution_location': 'Configured ComfyUI runtime', 'data_destination': config.COMFYUI_URL,
            'price_usd': 0.0 if bound == 0 else None, 'maximum_usd_per_attempt': bound,
            'cost_basis': 'Reviewed unmetered workflow bytes' if bound == 0 else 'No enforceable cost bound',
            'sources': [{'role': 'source', 'accepts': ['image']}], 'price_unit': 'attempt'})
    for model in stack.provider_catalog.selectable().values():
        limit = hosted_images.source_limit(model)
        unavailable = preview or not stack.openrouter.key_present or 'lane:external' in benches or f"engine:{model['id']}" in benches
        results.append({'id': model['id'], 'label': model['label'],
            'revision': hosted_images.revision(model), 'actions': ['generate'] + (['continue'] if limit else []) + (['combine'] if limit > 1 else []),
            'inputs': hosted_images.inputs(model), 'source_limit': limit,
            'evidence': 'declared', 'availability': 'unavailable' if unavailable else 'unverified',
            'reasons': ['Generation is disconnected in this isolated preview.'] if preview else
                       ['The provider is paused, unavailable, or needs a key.'] if unavailable else [],
            'execution_location': 'Hosted provider', 'data_destination': config.OPENROUTER_URL,
            'price_usd': model.get('price_usd_per_image'), 'maximum_usd_per_attempt': None,
            'cost_basis': 'Published tariffs can reserve expected spend; this route has no enforceable per-request ceiling',
            'sources': [{'role': 'source', 'accepts': ['image']}] if limit else [], 'price_unit': 'image',
            'provider': 'openrouter', 'pinned': bool(model.get('pinned'))})
    return results + crexil_mesh.catalogue(stack) + crexil_audio.catalogue(stack)


def strict_value(spec: dict, value):
    key, typ = spec['key'], spec.get('type')
    if typ in ('int', 'float'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} needs a finite number.')
        if typ == 'int' and int(value) != value:
            raise ValueError(f'{key} needs a whole number.')
        if 'min' in spec and value < spec['min'] or 'max' in spec and value > spec['max']:
            raise ValueError(f"{key} must be between {spec.get('min', 'no lower limit')} and {spec.get('max', 'no upper limit')}.")
        if 'step' in spec:
            step, offset = Decimal(str(spec['step'])), Decimal(str(spec.get('min', 0)))
            if step > 0 and (Decimal(str(value)) - offset) % step:
                raise ValueError(f"{key} must use increments of {spec['step']} from {offset}.")
        return int(value) if typ == 'int' else float(value)
    if typ == 'enum' and value not in spec.get('choices', []):
        raise ValueError(f"{key} must be one of {', '.join(map(str, spec.get('choices', [])))}.")
    if typ == 'text' and not isinstance(value, str):
        raise ValueError(f'{key} needs text.')
    if typ == 'seed':
        if isinstance(value, int) and not isinstance(value, bool):
            value = {'mode': 'fixed', 'value': value}
        if not isinstance(value, dict) or value.get('mode') not in ('random', 'fixed', 'locked'):
            raise ValueError('Choose a random, fixed or locked seed.')
        if value['mode'] != 'random' and (type(value.get('value')) is not int or not 0 <= value['value'] < presets.MAX_SEED):
            raise ValueError(f'A fixed or locked seed needs a whole number from 0 to {presets.MAX_SEED - 1}.')
    if spec.get('required') and (value is None or isinstance(value, str) and not value.strip()):
        raise ValueError(f'{key} is required.')
    return value


def strict_inputs(preset: dict, setup: Preparation, choice) -> dict:
    capability = 'region' if setup.action == 'region' else 'img2img' if setup.references else 'txt2img'
    applicable = {s['key']: s for s in preset['inputs'] if s.get('supported', True) and presets.spec_applies(s, capability)}
    if capability == 'region':
        applicable = {key: spec for key, spec in applicable.items() if key not in ('width', 'height')
                      and (key != 'denoise' or any(r.action == 'modify' for r in setup.regions))}
    given = {**setup.controls, **choice.controls}
    for key in given:
        if key not in applicable:
            raise ValueError(f'{key} is not supported for this operation and action. Its saved value has been kept.')
    inputs = {}
    for key, spec in applicable.items():
        default = setup.prompt if key == 'prompt' else {'mode': 'random'} if key == 'seed' else spec.get('default')
        inputs[key] = strict_value({**spec, 'required': False} if capability == 'region' and key == 'prompt' else spec, given.get(key, default))
    if capability == 'region' and 'prompt' in given:
        raise ValueError('Region prompts come from the saved regions and shared prompt. Clear the separate operation prompt override.')
    # Contribution text is sent as part of the instruction. Roles and strengths
    # require a real mapped control; storing them alone does not implement them.
    contributions = [f'Source {i + 1}: {ref.contribution}' for i, ref in enumerate(setup.references) if ref.contribution.strip()]
    if contributions:
        if 'prompt' not in inputs:
            raise ValueError('This operation cannot receive source contribution instructions.')
        inputs['prompt'] += '\n\n' + '\n'.join(contributions)
    return inputs


async def review(stack, did: str, *, compiled: dict | None = None, sequence_id: str | None = None) -> dict:
    creative = CreativeStore(stack.db)
    direction = await creative.direction(did)
    project = await creative.project(direction['workspace_id'])
    setup = Preparation.model_validate(direction['setup'])
    errors: list[str] = []
    if direction['state'] != 'draft':
        errors.append('This direction was already submitted. Reuse its setup to prepare new work.')
    if direction['set_aside']:
        errors.append('Restore this direction before submitting it.')
    sources = []
    source_nodes = []
    mesh_action = setup.action in crexil_mesh.ACTIONS
    audio_action = setup.action in crexil_audio.ACTIONS
    for ref in setup.references:
        node = await stack.db.get_node(ref.node_id)
        if not node or node['workspace_id'] != direction['workspace_id'] or node['purged_at'] or not Path(node['output_path']).is_file():
            errors.append(f'Source {ref.node_id} is unavailable. Restore or replace it.')
            continue
        if not mesh_action and not audio_action and node['payload'] != 'image':
            errors.append('This action needs image sources. Use a mesh operation for 3D material.')
        source_nodes.append(node)
        sources.append({'node_id': node['id'], 'sha256': hashlib.sha256(Path(node['output_path']).read_bytes()).hexdigest()})
    if len({r.node_id for r in setup.references}) != len(setup.references):
        errors.append('A source is listed more than once.')
    if setup.action == 'generate' and setup.references:
        errors.append('Choose Continue or Combine for a preparation with sources.')
    if setup.action == 'continue' and len(setup.references) != 1:
        errors.append('Continue needs one source.')
    if setup.action == 'combine' and len(setup.references) < 2:
        errors.append('Combine needs at least two sources.')
    count = sum(c.count for c in setup.operations)
    if not count:
        errors.append('Choose at least one operation and attempt.')
    limit = min(setup.attempt_limit, 8 if mesh_action else 16)
    if count > limit:
        errors.append(f'This run asks for {count} attempts; its current limit is {limit}.')
    graphs = {}
    region_prepared = None
    if setup.action == 'region':
        from . import crexil_regions
        try:
            region_prepared = await crexil_regions.plan(stack.db, direction['workspace_id'], setup,
                                                       sources[0]['sha256'] if sources else None)
            if compiled is not None:
                compiled['_region'] = region_prepared
        except (ValueError, LookupError) as exc:
            errors.append(str(exc))
    from . import operation_registry
    registrations = {}
    operations = {op['id']: op for op in catalogue(stack, graphs) + await operation_registry.catalogue(stack, registrations)}
    global_errors = errors
    chosen, seen = [], set()
    maximum = Decimal('0')
    unknown = False
    uses_tariff = False
    for choice in setup.operations:
        errors = []
        op = operations.get(choice.operation_id)
        if not op:
            chosen.append({'id': choice.operation_id, 'count': choice.count, 'ready': False,
                           'errors': [f'Operation {choice.operation_id} is missing. Select a current operation.']})
            continue
        chosen.append({**op, 'count': choice.count, 'controls': choice.controls})
        if choice.operation_id in seen:
            global_errors.append(f"{op['label']} is listed twice. Put its attempts in one entry.")
        seen.add(choice.operation_id)
        if choice.revision != op['revision']:
            errors.append(f"{op['label']} changed since preparation. Review and accept its current revision.")
        if setup.action not in op['actions'] or len(setup.references) > op['source_limit']:
            errors.append(f"{op['label']} cannot perform {setup.action} with {len(setup.references)} sources. All sources are kept.")
        if op['availability'] == 'unavailable':
            errors.extend(f"{op['label']}: {reason}" for reason in op['reasons'])
        bound = op['maximum_usd_per_attempt']
        hosted_model = stack.provider_catalog.selectable().get(choice.operation_id)
        if bound is None and not hosted_model and not op.get('audio_verb'):
            unknown = True
            errors.append(f"{op['label']} has no enforceable cost bound. Its estimate cannot protect this spending limit.")
        if op.get('mesh_kind'):
            try:
                envelope = crexil_mesh.compile_request(op, setup, choice, source_nodes)
                chosen[-1].update(applied_controls=envelope['controls'], source_hashes=envelope['source_hashes'])
                if compiled is not None:
                    compiled[choice.operation_id] = envelope
            except (ValueError, OSError) as exc:
                errors.append(f"{op['label']}: {exc}")
        elif op.get('audio_verb'):
            try:
                envelope = await crexil_audio.compile_request(stack.db, op, setup, choice, source_nodes)
                if envelope['paid'] and setup.spending_policy != 'published_tariff':
                    raise ValueError('This engine charges by a published tariff, which is not a hard cap. '
                                     'Choose published-tariff budgeting explicitly or use an unmetered local engine.')
                bound = Decimal(envelope['reserved_micro_usd']) / crexil_budget.MICRO_USD
                chosen[-1].update(applied_controls=envelope['controls'], reserved_usd=float(bound),
                                  reserved_micro_usd=envelope['reserved_micro_usd'])
                uses_tariff = uses_tariff or envelope['paid']
                if compiled is not None:
                    compiled[choice.operation_id] = envelope
            except (ValueError, OSError) as exc:
                unknown = unknown or isinstance(exc, crexil_budget.UnknownPrice)
                errors.append(f"{op['label']}: {exc}")
        elif choice.operation_id in stack.catalog:
            try:
                if any(ref.role is not None or ref.strength is not None for ref in setup.references):
                    raise ValueError('This operation has no mapped source role or strength control. Clear those values explicitly.')
                inputs = strict_inputs(stack.catalog[choice.operation_id], setup, choice)
                chosen[-1]['applied_controls'] = inputs
                from .api_v4 import _build_local_graph
                if setup.action == 'region':
                    if region_prepared is None:
                        raise ValueError('Resolve the region preparation before compiling this operation.')
                    envelope = crexil_regions.compile_operation(stack.catalog[choice.operation_id], inputs, region_prepared,
                        {cap: graph for (pid, cap), graph in graphs.items() if pid == choice.operation_id})
                    given = {**setup.controls, **choice.controls}
                    consumed = {key for p in envelope['region_plan']['passes'] for key in p['applied_controls']}
                    for key in given:
                        if key not in consumed:
                            raise ValueError(f'{key} does not apply to these effective region passes. Clear its saved override.')
                    inputs = {key: value for key, value in inputs.items() if key in consumed}
                    chosen[-1]['applied_controls'] = inputs
                    chosen[-1]['region_passes'] = [{key: p[key] for key in ('idx', 'kind', 'prompt', 'template_id', 'box', 'applied_controls')} for p in envelope['region_plan']['passes']]
                    if compiled is not None:
                        compiled[choice.operation_id] = (inputs, [], envelope)
                    # The common result below records this operation's readiness.
                    chosen[-1].update(ready=not errors, errors=errors)
                    if not errors:
                        maximum += Decimal(str(bound)) * choice.count
                    continue
                capability = 'img2img' if setup.references else 'txt2img'
                graph_key = f'combine{len(setup.references)}' if setup.action == 'combine' else capability
                ports = stack.catalog[choice.operation_id].get('source_ports', {}).get(graph_key, [])
                if setup.action == 'combine' and len(ports) != len(setup.references):
                    raise ValueError('This operation has no exact source mapping for the requested count.')
                chosen[-1]['source_ports'] = ports
                built = _build_local_graph(stack.catalog[choice.operation_id], inputs, capability,
                    'source.png' if setup.references else None, workflow=graphs[(choice.operation_id, graph_key)])
                mapped = set()
                for i, port in enumerate(ports):
                    targets = presets.find_nodes_by_title(built[2], port.get('node_title'))
                    if len(targets) != 1:
                        raise ValueError(f'Source {i + 1} needs one unambiguous graph input mapping.')
                    target = (targets[0], port.get('field'))
                    if target in mapped:
                        raise ValueError('Two sources map to the same graph input. Give each source a distinct input.')
                    if not isinstance(built[2][target[0]].get('inputs', {}).get(target[1]), str):
                        raise ValueError(f'Source {i + 1} must map to an existing image filename input.')
                    mapped.add(target)
                    presets.patch_input(built[2], port, f'source-{i + 1}.png')
                if compiled is not None:
                    compiled[choice.operation_id] = built
            except (ValueError, KeyError, presets.PresetError) as exc:
                errors.append(f"{op['label']}: {exc}")
            except Exception as exc:
                from fastapi import HTTPException
                if not isinstance(exc, HTTPException):
                    raise
                errors.append(str(exc.detail))
        elif hosted_model:
            try:
                envelope = hosted_images.compile_request(hosted_model, setup, choice,
                    [Path((await stack.db.get_node(s['node_id']))['output_path']).read_bytes() for s in sources])
                if len(sources) != len(setup.references):
                    raise ValueError('All source files must be available before compiling a hosted request.')
                if setup.spending_policy != 'published_tariff':
                    raise ValueError('This route has no enforceable cost bound. Choose published-tariff budgeting explicitly or use an unmetered local operation.')
                bound = envelope['reserved_usd']
                chosen[-1].update(applied_controls={**envelope['request'], 'input_references': [s['node_id'] for s in sources]},
                    reserved_usd=bound, hosted=True, endpoint=envelope['endpoint'],
                    endpoints_fetched_at=envelope['endpoints_fetched_at'])
                envelope['destination'] = config.OPENROUTER_URL
                if compiled is not None:
                    compiled[choice.operation_id] = envelope
                uses_tariff = uses_tariff or not errors
            except ValueError as exc:
                unknown = True
                errors.append(f"{op['label']}: {exc}")
        elif choice.operation_id in registrations:
            item = registrations[choice.operation_id]
            try:
                inputs, seed_targets, graph, ports, outputs = operation_registry.compile_request(item, setup, choice)
                chosen[-1].update(applied_controls=inputs, source_ports=ports, output_ports=outputs,
                    registration_id=item['id'], registration_revision=item['revision'],
                    definition=item['definition'], binding=item['binding'],
                    runtime_interface_sha256=item['inspection']['interface_sha256'],
                    source_definition_sha256=item['source_sha256'],
                    seed_range=operation_registry.seed_range(item, seed_targets))
                seed_key = next((c['key'] for c in item['definition']['inputs'] if c['type'] == 'seed'), None)
                policy = presets.resolve_seed_policy(inputs.get(seed_key))
                if seed_targets and policy['mode'] == 'fixed' and policy['value'] + choice.count - 1 > chosen[-1]['seed_range'][1]:
                    raise ValueError('The requested seed sequence exceeds the runtime range. Use fewer attempts or a lower seed.')
                if compiled is not None:
                    compiled[choice.operation_id] = (inputs, seed_targets, graph)
            except ValueError as exc:
                errors.append(f"{op['label']}: {exc}")
        chosen[-1].update(ready=not errors, errors=errors)
        if not errors:
            maximum += Decimal(str(bound)) * choice.count
    if maximum > Decimal(str(setup.spending_limit_usd)):
        global_errors.append(f'The reserved ${maximum} exceeds the ${setup.spending_limit_usd} spending limit.')
    # Everything above depends on the request alone; what follows also depends on
    # spending other work recorded, which the token keeps in its own part.
    request_errors = list(global_errors)
    request_operations = [dict(op) for op in chosen]
    accounting = await crexil_budget.accounting(stack.db, direction['workspace_id'])
    spending_errors = crexil_budget.violations(project['limits'], accounting,
        attempts=sum(op['count'] for op in chosen if op['ready']), reserved=float(maximum), tariff=uses_tariff)
    sequence_budget = None
    if sequence_id is not None:
        from .crexil_sequences import state as sequence_state
        sequence = await sequence_state(stack.db, sequence_id)
        if sequence['workspace_id'] != direction['workspace_id']:
            raise ValueError('The sequence and preparation must belong to the same project.')
        sequence_budget = {'id': sequence_id, 'limits': sequence['limits'], 'accounting': sequence['accounting']}
        spending_errors += crexil_budget.violations(sequence['limits'], sequence['accounting'],
            attempts=sum(op['count'] for op in chosen if op['ready']), reserved=float(maximum), tariff=uses_tariff, label='Sequence')
    global_errors += spending_errors
    if global_errors:
        for op in chosen:
            op['ready'] = False
    ready_count = sum(op['count'] for op in chosen if op['ready'])
    errors = list(dict.fromkeys(global_errors + [error for op in chosen for error in op['errors']]))
    snapshot = {'direction_id': did, 'revision': direction['revision'], 'setup': setup.model_dump(),
                'project_revision': project['revision'], 'brief': project['brief'],
                'project_instructions': project['instructions'], 'sources': sources, 'operations': chosen,
                'project_limits': project['limits'], 'accounting': accounting,
                'sequence_budget': sequence_budget,
                'region': region_prepared['details'] if region_prepared else None,
                'blocking_errors': global_errors}
    # A project rename or limit change moves project_revision, so the request part
    # binds the brief and instructions themselves and the limits get their own part.
    request = {'direction_id': did, 'revision': direction['revision'], 'setup': snapshot['setup'],
               'brief': project['brief'], 'project_instructions': project['instructions'],
               'sources': sources, 'operations': request_operations, 'sequence_id': sequence_id,
               'region': snapshot['region'], 'blocking_errors': request_errors}
    limits = {'project': project['limits'], 'sequence': sequence_budget and sequence_budget['limits']}
    spending = {'project': accounting, 'sequence': sequence_budget and sequence_budget['accounting']}
    return {'direction_id': did, 'revision': direction['revision'],
            'token': review_token(request, limits, spending), 'spending_errors': spending_errors,
            'ready': ready_count > 0, 'errors': errors, 'attempts': count,
            'ready_attempts': ready_count, 'blocked_attempts': count - ready_count,
            'maximum_usd': None if uses_tariff or unknown and not ready_count else float(maximum),
            'reserved_usd': float(maximum),
            'reserved_micro_usd': int((maximum * crexil_budget.MICRO_USD).to_integral_value()), 'snapshot': snapshot}


async def freeze(stack, did: str, body, *, staged_directories: list | None = None,
                 sequence_context: dict | None = None) -> dict:
    from .api_v4 import _frame_of, _refuse_if_disk_cannot_hold, ASSUMED_PX
    creative = CreativeStore(stack.db)
    compiled = {}
    if (await creative.direction(did))['sequence_template']:
        raise ValueError('This preparation is a sequence template. Review and start its sequence to run it.')
    reviewed = await review(stack, did, compiled=compiled,
                            sequence_id=sequence_context['id'] if sequence_context else None)
    change = 'request' if body.revision != reviewed['revision'] else token_change(body.review_token, reviewed['token'])
    if change == 'request':
        raise Conflict('Preparation, sources or operations changed. Review the current request before submitting.')
    if change == 'limits':
        raise SpendingChanged('Spending limits changed since review. Review again to see the current limits.')
    if change == 'spending' and reviewed['spending_errors']:
        raise SpendingChanged("Other work changed this project's spending since review, and this run no longer "
                              'fits its limits. ' + ' '.join(reviewed['spending_errors'])
                              + ' Review again to see the current totals.')
    if not reviewed['ready']:
        raise ValueError(' '.join(reviewed['errors']))
    snapshot = reviewed['snapshot']
    setup = Preparation.model_validate(snapshot['setup'])
    direction = await creative.direction(did)
    parents = [r.node_id for r in setup.references]
    source_name = None
    source_uploads = []
    for source in snapshot['sources']:
        node = await stack.db.get_node(source['node_id'])
        name = f"crexil-{source['sha256']}{Path(node['output_path']).suffix or '.png'}"
        source_uploads.append({**source, 'name': name})
    if source_uploads:
        source_name = source_uploads[0]['name']
    specs = []
    token = new_id()
    capability = 'region' if setup.action == 'region' else 'img2img' if parents else 'txt2img'
    region_ids = []
    if capability == 'region':
        from . import crexil_regions
        await _refuse_if_disk_cannot_hold(stack.db, [snapshot['region']['source']['width'] * snapshot['region']['source']['height']] * reviewed['ready_attempts'])
        region_ids = await crexil_regions.freeze(stack.db, compiled['_region'],
            {op['id']: compiled[op['id']][2] for op in snapshot['operations'] if op['ready']},
            staged_directories=staged_directories)
    for op in snapshot['operations']:
        if not op['ready']:
            continue
        inputs = op['applied_controls']
        if op.get('mesh_kind'):
            specs.extend(crexil_mesh.frozen_specs(op, compiled[op['id']], parents, setup))
            continue
        if op.get('audio_verb'):
            specs.extend(crexil_audio.frozen_specs(op, compiled[op['id']], parents, setup))
            continue
        if op.get('hosted'):
            envelope = compiled[op['id']]
            seed_policy = presets.resolve_seed_policy({**setup.controls,
                **next(c.controls for c in setup.operations if c.operation_id == op['id'])}.get('seed'))
            for attempt_index in range(op['count']):
                request = copy.deepcopy(envelope)
                if 'seed' in request['request'] and seed_policy['mode'] == 'fixed':
                    request['request']['seed'] += attempt_index
                specs.append({'engine': op['id'], 'seed': request['request'].get('seed'),
                    'external': {'transport': 'openrouter-images', **request}, 'committed_usd': op['reserved_usd'],
                    'dials': {'prompt': request['request']['prompt'], 'parents': parents, 'capability': capability,
                        'crexil': {'operation_revision': op['revision'], 'automatic_retry': False,
                                  'spending_policy': setup.spending_policy, 'source_uploads': source_uploads,
                                  'kind_of_work': setup.purpose, 'output_ports': []}}})
            continue
        applied, seed_targets, base = compiled[op['id']]
        if op.get('source_ports'):
            for port, source in zip(op['source_ports'], source_uploads, strict=True):
                if op.get('registration_id'):
                    base[port['node_id']]['inputs'][port['field']] = source['name']
                else:
                    presets.patch_input(base, port, source['name'])
        elif source_name is not None and capability != 'region':
            presets.patch_input(base, {'node_title': 'source_image', 'field': 'image'}, source_name)
        seed_key = next((c['key'] for c in op.get('definition', {}).get('inputs', []) if c['type'] == 'seed'), 'seed')
        policy = presets.resolve_seed_policy(inputs.get(seed_key))
        for attempt_index in range(op['count']):
            seed = presets.take_seed(policy, len(specs))
            graph = copy.deepcopy(base)
            if op.get('registration_id'):
                low, high = op['seed_range']
                seed = (secrets.randbelow(high - low + 1) + low if policy['mode'] == 'random' else
                        policy['value'] + (attempt_index if policy['mode'] == 'fixed' else 0)) if seed_targets else None
                for target in seed_targets:
                    graph[target['node_id']]['inputs'][target['field']] = seed
            elif capability == 'region':
                from .regionrun import stamp_take
                if not any('seed' in p['applied_controls'] for p in base['region_plan']['passes']):
                    seed = None
                graph = stamp_take(base, seed, token, len(specs))
            else:
                presets.patch_seed(graph, seed, seed_targets, capability)
            if capability != 'region':
                presets.set_save_prefix(graph, f'crexil/{token}/s{len(specs)}')
            specs.append({'engine': op['id'], 'seed': seed, 'submitted_graph': graph,
                          'committed_usd': op['maximum_usd_per_attempt'],
                          'dials': {**applied, 'parents': parents, 'capability': capability,
                                    'crexil': {'operation_revision': op['revision'], 'automatic_retry': False,
                                               'spending_policy': 'hard_cap',
                                               'source_uploads': source_uploads if capability != 'region' else [],
                                               'registration_id': op.get('registration_id'),
                                               'registration_revision': op.get('registration_revision'),
                                               'kind_of_work': setup.purpose,
                                               'output_ports': op.get('output_ports', [])}}})
    if setup.blind:
        specs = blind.arrange(specs)
    frames = [[snapshot['region']['source']['width'], snapshot['region']['source']['height']] for s in specs] if capability == 'region' else [None if 'mesh_job' in (s.get('submitted_graph') or {}) else _frame_of(s['dials']) for s in specs]
    if setup.action in crexil_mesh.ACTIONS:
        from .api_v4_mesh import _refuse_if_disk_cannot_hold_meshes
        await _refuse_if_disk_cannot_hold_meshes(len(specs))
    elif setup.action not in crexil_audio.ACTIONS:
        # audio takes are seconds of PCM, far below the pixel-based estimate
        await _refuse_if_disk_cannot_hold(stack.db, [f[0] * f[1] if f else ASSUMED_PX for f in frames])
    turn = await stack.db.create_turn_with_slots(direction['workspace_id'], setup.prompt,
        {'crexil': {**snapshot, 'review_token': body.review_token, 'actor': body.actor.model_dump(),
                    'request_id': body.request_id, 'maximum_usd': reviewed['maximum_usd'],
                    **({'sequence': sequence_context} if sequence_context else {})},
         **({'region': {'region_ids': region_ids, 'policy': snapshot['region']['policy']}} if region_ids else {})}, specs)
    await stack.db.conn.execute('INSERT INTO direction_turns(direction_id,turn_id) VALUES(?,?)', (did, turn['id']))
    if setup.blind:
        await blind.mark(stack.db, turn['id'], direction['workspace_id'], body.actor.model_dump())
    if region_ids:
        await stack.db.mark_regions_consumed(region_ids, turn['id'])
    for slot in turn['slots']:
        from .mesh import mesh_verb
        mesh_kind = (slot.get('submitted_graph') or {}).get('mesh_job', {}).get('kind')
        await stack.db.append_event('slot.prepared', workspace_id=direction['workspace_id'],
            turn_id=turn['id'], slot_id=slot['id'], payload={'engine': slot['engine'],
                'payload': slot['dials']['crexil'].get('output_payload') or ('3d' if mesh_kind else next(
                    (p['payload'] for p in slot['dials']['crexil']['output_ports'] if p['primary']), 'image')),
                'verb': mesh_verb(mesh_kind) if mesh_kind else setup.action, 'prompt': slot['dials'].get('prompt', setup.prompt), 'parents': parents,
                'actor': body.actor.model_dump(), 'direction_id': did, 'frame': frames[slot['pos']]})
    submitted_ids = {op['id'] for op in snapshot['operations'] if op['ready']}
    remaining = [op.model_dump() for op in setup.operations if op.operation_id not in submitted_ids]
    changes = {'state': 'draft', 'setup': {**setup.model_dump(), 'operations': remaining}} if remaining else {'state': 'submitted'}
    saved = await creative.revise_direction(did, body.revision, changes, body.actor.model_dump())
    await stack.db.append_event('direction.submitted', workspace_id=direction['workspace_id'],
        turn_id=turn['id'], payload={'actor': body.actor.model_dump(), 'direction_id': did,
                                   'review': reviewed, 'request_id': body.request_id})
    return {'direction': saved, 'turn_id': turn['id'], 'review_token': reviewed['token']}
