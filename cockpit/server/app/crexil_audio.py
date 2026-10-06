"""Compile audio preparations into frozen provider requests.

The capability matrix's audio rows (`audio.engines()`) publish the engines; each
verb a row serves becomes one operation. Per verb, a row declares its public
controls (`params`), the model it asks for (`models`), provider fields fixed by
configuration (`fixed`), its published price (`tariffs`) and how many takes one
attempt returns (`takes_per_attempt`). Rows may declare the voices their speech
accepts (`voice_providers`) and the provider's terms on their outputs (`terms`).

Review binds the voice take's bytes and checks its recorded terms (laws 12, 21).
Each attempt freezes one envelope in the slot's `external` field, transport
`audio`: provider, engine, verb, model, the text, validated controls, fixed
fields, seed, takes, voice, tariff, measures and the exact reservation in
micro-dollars. Executors turn the envelope into their provider's request;
nothing here calls a provider.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from . import audio, audio_executors, config, crexil_budget, presets

ACTIONS = audio.VERBS
LABELS = {'voice-design': 'Design voice', 'speak': 'Speak', 'sfx': 'Sound effect'}
TEXT = {'voice-design': 'a voice description', 'speak': 'the line to speak', 'sfx': 'a sound description'}
ROLES = {'speak': ['voice']}
ATTEMPT_MAX = 16


def destination(provider: str) -> str:
    return config.ELEVENLABS_URL if provider == 'elevenlabs' else config.VOICE_RUNNER_URL


def inputs(row: dict, verb: str) -> list[dict]:
    """The verb's published controls, plus the seed when the engine's seed
    capability says it takes one (the matrix publishes the seed there)."""
    specs = [{'key': key, **spec, 'reason': spec.get('why', '')}
             for key, spec in (row.get('params') or {}).get(verb, {}).items()]
    if ((row.get('capabilities') or {}).get('seed') or {}).get('value'):
        specs.append({'key': 'seed', 'label': 'Seed', 'type': 'seed', 'default': {'mode': 'random'},
                      'reason': row['capabilities']['seed'].get('note', '')})
    return specs


def takes(row: dict, verb: str) -> int:
    return int((row.get('takes_per_attempt') or {}).get(verb, 1))


def tariff(row: dict, verb: str) -> dict | None:
    """The verb's published tariff, or None when the row names none or names one this
    version cannot read; either way the price is unknown and counts as over the
    spending limit (law 22)."""
    found = (row.get('tariffs') or {}).get(verb)
    if not isinstance(found, dict):
        return None
    micro = found.get('micro_usd')
    if found.get('unit') not in PER_UNIT or not isinstance(micro, int) or isinstance(micro, bool) or micro < 0:
        return None
    return dict(found)


def restrictions(row: dict) -> list[str]:
    """Why some audio takes may not be this engine's voice (law 12: badge, never filter)."""
    notes = [other['terms']['reason'] for other in audio.engines().values()
             if audio.refusal(audio.terms(other), row.get('provider')) and other['terms'].get('reason')]
    if row.get('voice_providers'):
        notes.append(f"{row['name']} speaks only with voices designed or saved at "
                     f"{', '.join(audio_executors.LABELS.get(p, p) for p in row['voice_providers'])}.")
    return list(dict.fromkeys(notes))


def limitations(row: dict, verb: str) -> list[str]:
    notes = list((row.get('notes') or {}).get(verb, []))
    if row.get('lane') == 'local':
        notes.append('Local operations share one queue with image generation. No provider charge.')
    else:
        notes.append(f"Text and audio go to {audio_executors.LABELS.get(row.get('provider'), row.get('provider'))} "
                     f"at {destination(row.get('provider'))}. Charges follow its published tariff, which is not a hard cap.")
    if (found := audio.terms(row)) and found.get('reason'):
        notes.append(found['reason'])
    if takes(row, verb) > 1:
        notes.append(f'One attempt returns {takes(row, verb)} takes.')
    return notes


def reasons(stack, row: dict) -> list[str]:
    """Why this engine cannot run now, in plain words; empty when it can."""
    provider, lane = row.get('provider'), row.get('lane')
    if config.CREXIL_PREVIEW:
        return ['Generation is disconnected in this isolated preview.']
    if audio.PROVIDERS.get(provider) != lane:
        return [f"{row.get('name', row.get('id'))} names an unknown provider or lane in configs/engines.json."]
    if not row.get('enabled', False):
        return ['This engine is not enabled in configs/engines.json.']
    executor = stack.scheduler.executor(provider)
    if not audio_executors.ready(executor):
        return [getattr(executor, 'message', None) or f'{audio_executors.LABELS[provider]} adapter is not built yet.']
    if provider == 'elevenlabs' and not config.ELEVENLABS_API_KEY:
        return ['Needs an ElevenLabs key. Set ELEVENLABS_API_KEY in .env, then restart.']
    benches = stack.scheduler.snapshot()['benches']
    if f'lane:{lane}' in benches or f"engine:{row['id']}" in benches:
        return ['The local runtime is paused or unavailable.' if lane == 'local'
                else 'The provider is paused or unavailable.']
    return []


PER_UNIT = {'1k_characters': 'per 1,000 characters', 'second': 'per second',
            'generation': 'per generation', 'attempt': 'per attempt'}


def cost_basis(price: dict | None) -> str:
    """One plain line on how an operation is priced. A price this version cannot read
    is described, never raised, so one misconfigured row cannot hide the others; it
    counts as over the spending limit like any unknown price (law 22)."""
    unknown = 'An unknown price counts as over the spending limit.'
    if price is None:
        return f'No published price. {unknown}'
    micro, per = price.get('micro_usd'), PER_UNIT.get(price.get('unit'))
    if micro == 0:
        return 'Unmetered local operation'
    if per is None or not isinstance(micro, int) or isinstance(micro, bool):
        return f'This engine row names a price this version cannot read. {unknown}'
    return f"Published tariff, {Decimal(micro) / crexil_budget.MICRO_USD} US dollars {per}"


def catalogue(stack) -> list[dict]:
    from .crexil_execution import digest
    results = []
    for row in audio.engines().values():
        for verb in row.get('verbs', []):
            if verb not in ACTIONS:
                continue
            specs, notes, price = inputs(row, verb), limitations(row, verb), tariff(row, verb)
            why = reasons(stack, row)
            free = price is not None and price.get('micro_usd') == 0
            results.append({'id': f"audio:{verb}:{row['id']}", 'engine_id': row['id'], 'audio_verb': verb,
                'label': f"{LABELS[verb]} · {row['name']}",
                'revision': digest([row, verb, specs, notes, destination(row.get('provider'))])[:16],
                'actions': [verb], 'inputs': specs, 'source_limit': len(ROLES.get(verb, [])),
                'source_roles': ROLES.get(verb, []),
                'sources': [{'role': role, 'accepts': ['audio'], 'restrictions': restrictions(row)}
                            for role in ROLES.get(verb, [])],
                'output_payload': 'audio', 'limitations': notes, 'attempt_max': ATTEMPT_MAX,
                'takes_per_attempt': takes(row, verb), 'provider': row.get('provider'),
                'evidence': 'declared', 'availability': 'unavailable' if why else 'unverified', 'reasons': why,
                'execution_location': 'Local voice runner' if row.get('lane') == 'local' else 'Hosted provider',
                'data_destination': destination(row.get('provider')),
                'price_usd': float(Decimal(price['micro_usd']) / crexil_budget.MICRO_USD) if price else None,
                'maximum_usd_per_attempt': 0.0 if free else None, 'cost_basis': cost_basis(price),
                'price_unit': price['unit'] if price else None, 'tariff': price})
    return results


def measures(price: dict | None, text: str, values: dict) -> dict:
    """What one attempt's price counts, from the verb's text or a named control."""
    if price is None:
        return {}
    measure = price.get('measure')
    source = text if measure in (None, 'prompt') else values.get(measure)
    if price['unit'] == '1k_characters':
        if not isinstance(source, str):
            raise crexil_budget.UnknownPrice(f'The text this price counts ({measure}) is missing, so the price is unknown.')
        return {'characters': len(source)}
    if price['unit'] == 'second':
        if isinstance(source, bool) or not isinstance(source, (int, float)):
            raise crexil_budget.UnknownPrice(f'The duration this price counts ({measure}) is missing, so the price is unknown.')
        return {'seconds': source}
    return {'generations': 1} if price['unit'] == 'generation' else {'attempts': 1}


def _text_value(spec: dict, value) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{spec.get('label') or spec['key']} needs text.")
    low, high = spec.get('min_length'), spec.get('max_length')
    if low is not None and len(value) < low or high is not None and len(value) > high:
        raise ValueError(f"{spec.get('label') or spec['key']} needs {low or 0} to {high or 'any number of'} "
                         f'characters; it has {len(value)}.')
    return value


async def compile_request(db, op: dict, setup, choice, nodes: list[dict]) -> dict:
    """Validate one audio operation's preparation and build its envelope."""
    from .crexil_execution import strict_value
    verb, row = op['audio_verb'], audio.engines().get(op['engine_id'])
    if row is None:
        raise ValueError(f"{op['label']} is no longer configured. Refresh the operation list.")
    text = setup.prompt.strip()
    if not text:
        raise ValueError(f'{LABELS[verb]} needs {TEXT[verb]}.')
    limit = (row.get('text_limits') or {}).get(verb)
    if limit is not None and len(text) > limit:
        raise ValueError(f"{row['name']} takes at most {limit} characters; this text has {len(text)}. Its text is kept.")
    roles = ROLES.get(verb, [])
    if len(nodes) != len(roles):
        raise ValueError(f"{LABELS[verb]} takes {'one voice take' if roles else 'no sources'}. All sources are kept.")
    voice, uploads = None, []
    for ref, node, role in zip(setup.references, nodes, roles, strict=True):
        if node['payload'] != 'audio':
            raise ValueError('Speech needs an audio take as its voice. All sources are kept.')
        if ref.role not in (None, role) or ref.strength is not None:
            raise ValueError('The voice source has no role or strength control. Clear those values explicitly.')
        if ref.contribution.strip():
            raise ValueError('Speech uses the voice take as it is. Clear its contribution note; it is kept until you do.')
        why = audio.refusal((await db.material_origin(node['id'])).get('terms'), row['provider'])
        if why:
            raise ValueError(f'{why} Choose a take this engine may use. All sources are kept.')
        facts = await db.audio_fact(node['id']) or {}
        recorded = dict(facts.get('voice') or {})
        if row.get('voice_providers') and recorded.get('provider') not in row['voice_providers']:
            raise ValueError(f"{row['name']} speaks only with voices designed or saved at "
                             f"{', '.join(audio_executors.LABELS.get(p, p) for p in row['voice_providers'])}. "
                             'Choose such a take, or a local voice engine.')
        digest = hashlib.sha256(Path(node['output_path']).read_bytes()).hexdigest()
        voice = {'source_node_id': node['id'], 'sha256': digest, 'text': facts.get('text'), 'recorded': recorded}
        if recorded.get('provider') == row['provider'] and recorded.get('generated_voice_id') and not recorded.get('voice_id'):
            saved = await db.saved_voice(row['provider'], recorded['generated_voice_id'])
            if saved:
                voice['voice_id'] = saved
        uploads.append({'node_id': node['id'], 'sha256': digest,
                        'name': f"crexil-{digest}{Path(node['output_path']).suffix or '.wav'}"})
    given = {**setup.controls, **choice.controls}
    applicable = {s['key']: s for s in op['inputs'] if s.get('supported', True)}
    unknown = set(given) - applicable.keys()
    if unknown:
        raise ValueError(f"Unsupported controls: {', '.join(sorted(unknown))}. Clear their saved values explicitly.")
    if choice.count > op['attempt_max']:
        raise ValueError(f"This operation allows at most {op['attempt_max']} attempts per run.")
    values = {}
    for key, spec in applicable.items():
        value = given.get(key, spec.get('default'))
        if value is None:
            if spec.get('required'):
                raise ValueError(f"{spec.get('label') or key} is required.")
            continue
        if spec.get('type') == 'bool' and type(value) is not bool:
            raise ValueError(f'{key} must be true or false.')
        values[key] = _text_value(spec, value) if spec.get('type') == 'text' else strict_value(spec, value)
    price = tariff(row, verb)
    counted = measures(price, text, values)
    reserved = crexil_budget.audio_reservation(price, counted)
    request = {'transport': 'audio', 'provider': row['provider'], 'engine': row['id'], 'verb': verb,
               'model_id': (row.get('models') or {}).get(verb), 'text': text,
               'controls': {k: v for k, v in values.items() if k != 'seed'},
               'fixed': dict((row.get('fixed') or {}).get(verb, {})), 'takes': takes(row, verb), 'voice': voice,
               'tariff': price, 'measures': counted, 'reserved_micro_usd': reserved,
               'destination': destination(row['provider']), 'engine_revision': op['revision']}
    return {'request': request, 'controls': values, 'reserved_micro_usd': reserved,
            'paid': price is None or price['micro_usd'] > 0, 'source_uploads': uploads}


def frozen_specs(op: dict, compiled: dict, parents: list[str], setup) -> list[dict]:
    """One slot spec per attempt; each attempt's seed follows the seed policy."""
    values = compiled['controls']
    policy = presets.resolve_seed_policy(values.get('seed')) if 'seed' in values else None
    committed = float(Decimal(compiled['reserved_micro_usd']) / crexil_budget.MICRO_USD)
    specs = []
    for index in range(op['count']):
        seed = presets.take_seed(policy, index) if policy else None
        specs.append({'engine': op['engine_id'], 'seed': seed, 'submitted_graph': None,
            'external': {**compiled['request'], 'seed': seed}, 'committed_usd': committed,
            'dials': {'capability': 'audio', 'verb': op['audio_verb'], 'prompt': setup.prompt, 'parents': parents,
                'controls': {k: v for k, v in values.items() if k != 'seed'},
                'crexil': {'operation_id': op['id'], 'operation_revision': op['revision'],
                    'automatic_retry': False,
                    'spending_policy': setup.spending_policy if compiled['paid'] else 'hard_cap',
                    'source_uploads': compiled['source_uploads'], 'kind_of_work': setup.purpose,
                    'output_ports': [], 'output_payload': 'audio'}}})
    return specs
