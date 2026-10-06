"""First-use connection facts and prepared-operation requirement discovery.

Reads use the configured adapters and the registration discovery mechanism.
They never install assets, upload sources, dispatch work or promote advertised
interfaces into execution evidence. Credentials stay in the existing setup API.
"""

from __future__ import annotations

import json

from . import config, operation_registry
from .crexil_schemas import OperationDefinition, SourcePort
from .crexil_store import Conflict
from .db import now_iso


async def overview(stack) -> dict:
    from .api import _openrouter_check, _setup_status
    if config.CREXIL_PREVIEW:
        local = {'ok': False, 'error': 'Local runtime access is disconnected in this isolated preview.'}
    elif stack.comfy is None:
        local = {'ok': False, 'error': 'No local runtime client is connected in this server.'}
    else:
        try:
            stats = await stack.comfy.system_stats()
            local = {'ok': True, 'version': stats.get('system', {}).get('comfyui_version')}
        except Exception as exc:
            local = {'ok': False, 'error': str(exc)[:200]}
    return {'preview': config.CREXIL_PREVIEW, 'comfyui_url': config.COMFYUI_URL,
            'comfyui': local, 'openrouter': {**_setup_status(), 'probe': await _openrouter_check()}}


async def requirements(stack, operation_id: str, revision: str) -> dict:
    from .crexil_execution import catalogue, workflow_hashes
    operations = catalogue(stack) + await operation_registry.catalogue(stack)
    operation = next((item for item in operations if item['id'] == operation_id), None)
    if not operation:
        raise LookupError('That operation is no longer configured. Refresh the operation list.')
    if operation['revision'] != revision:
        raise Conflict('This operation changed. Refresh its description before checking requirements.')
    report = {'operation_id': operation_id, 'revision': revision, 'checked_at': now_iso(),
              'runtime_url': operation['data_destination'], 'state': 'unknown', 'findings': [],
              'gaps': list(operation['reasons']), 'limitations': list(operation.get('limitations', []))}
    preset = stack.catalog.get(operation_id)
    if not preset:
        report['limitations'].append('Use this operation\'s saved description and request review for its specific requirements. This check does not run it.')
        if operation_id.startswith('custom-'):
            report['gaps'].append('Open the registration to inspect and save its node, model and asset requirements.')
        return report
    before = workflow_hashes(preset)
    graphs = {name: json.loads((config.WORKFLOWS_DIR / f'{ref}.json').read_text())
              for name, ref in preset.get('workflows', {}).items()}
    # Discovery reads class interfaces only. Each actual graph is then checked
    # with its own links; source filenames belong to later request uploads.
    union = {f'{name}:{nid}': node for name, graph in graphs.items() for nid, node in graph.items()}
    found = await operation_registry.discover(OperationDefinition(graph=union))
    report.update(state=found['state'], findings=found['findings'])
    report['limitations'] += found.get('limitations', [])
    report['gaps'] += found['gaps']
    if found['state'] == 'inspected':
        for name, graph in graphs.items():
            sources = [SourcePort(node_id=nid, field='image', label='Source or mask')
                       for nid, node in graph.items()
                       if node['class_type'] in ('LoadImage', 'LoadImageMask') and 'image' in node['inputs']]
            definition = OperationDefinition(graph=graph, sources=sources)
            report['gaps'] += [f'{name}: {gap}' for gap in operation_registry.runtime_gaps(definition, found)]
    if workflow_hashes(preset) != before or next(item for item in catalogue(stack) if item['id'] == operation_id)['revision'] != revision:
        raise Conflict('The workflow changed during inspection. Refresh and inspect again.')
    report['gaps'] = list(dict.fromkeys(report['gaps']))
    report['limitations'].append('Source and mask uploads, chosen controls and spending are checked with the prepared request. No installation or generation was started.')
    return report
