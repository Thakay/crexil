"""Compile audio preparations into frozen provider requests.

The capability matrix's audio rows (`audio.engines()`) publish the engines; each
verb a row serves becomes one operation. Per verb, a row declares its public
controls (`params`), the model it asks for (`models`), provider fields fixed by
configuration (`fixed`), its published price (`tariffs`), how many takes one
attempt returns (`takes_per_attempt`), the longest text one request takes
(`text_limits`) and how many interleaved channels a raw PCM answer carries
(`pcm_channels`, mono unless stated; never in `fixed`, whose fields are sent to
the provider). Rows may declare the voices their speech accepts
(`voice_providers`), the provider's terms on their outputs (`terms`) and the
seeds the engine takes (`seed_range`, lowest and highest). The catalogue
publishes a row's accepted voices as its speech role's `voices`, so the roster
badges another provider's voice before the engine is chosen (law 12).

Review binds the voice take's bytes and checks its recorded terms (laws 12, 21),
and refuses a fixed or locked seed whose attempts would leave the engine's seed
range. Each attempt freezes one envelope in the slot's `external` field,
transport `audio`: provider, engine, verb, model, the text, validated controls,
fixed fields, seed, takes, PCM channels, voice, tariff, measures and the exact
reservation in micro-dollars. Every frozen seed lies inside the engine's range,
so the seed recorded is the seed sent. Audio slots retry visibly on their own
(law 9): the kinds that retry are refused before generation, so a retry never
pays twice. Executors turn the envelope into their provider's request; nothing
here calls a provider.
"""

from __future__ import annotations

import secrets
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


def seed_range(row: dict) -> tuple[int, int]:
    """The lowest and highest seed this engine takes: its row's `seed_range`,
    never beyond the cockpit's own seeds."""
    low, high = row.get('seed_range') or (0, presets.MAX_SEED - 1)
    return int(low), min(int(high), presets.MAX_SEED - 1)


def inputs(row: dict, verb: str) -> list[dict]:
    """The verb's published controls, plus the seed and its range when the
    engine's seed capability says it takes one (the matrix publishes the seed
    there)."""
    specs = [{'key': key, **spec, 'reason': spec.get('why', '')}
             for key, spec in (row.get('params') or {}).get(verb, {}).items()]
    if ((row.get('capabilities') or {}).get('seed') or {}).get('value'):
        low, high = seed_range(row)
        specs.append({'key': 'seed', 'label': 'Seed', 'type': 'seed', 'default': {'mode': 'random'},
                      'min': low, 'max': high, 'reason': row['capabilities']['seed'].get('note', '')})
    return specs


def check_seeds(row: dict, raw, count: int) -> None:
    """Refuse a fixed or locked seed whose attempts would leave the engine's
    seed range, in plain words; a random seed is drawn inside it at freeze."""
    policy = presets.resolve_seed_policy(raw)
    if policy['mode'] == 'random':
        return
    low, high = seed_range(row)
    first = int(policy['value'])
    last = first + (count - 1 if policy['mode'] == 'fixed' else 0)
    if first < low or last > high:
        reach = (f'seed {first} is outside it. Choose a seed in that range.' if last == first else
                 f'{count} attempts from seed {first} reach {last}. Choose a lower seed or fewer attempts.')
        raise ValueError(f"{row['name']} takes seeds from {low} to {high}; {reach}")


def take_seed(policy: dict, index: int, low: int, high: int) -> int:
    """The seed attempt `index` sends, inside the engine's range: drawn for a
    random policy, the fixed seed plus the index, or the locked seed. Review
    keeps every fixed or locked sequence inside the range (`check_seeds`)."""
    if policy['mode'] == 'random':
        return low + secrets.randbelow(high - low + 1)
    return int(policy['value']) + (index if policy['mode'] == 'fixed' else 0)


def takes(row: dict, verb: str) -> int:
    return int((row.get('takes_per_attempt') or {}).get(verb, 1))


def pcm_channels(row: dict, verb: str) -> int:
    """How many interleaved channels the verb's raw PCM answer carries: 1 unless
    the row states otherwise."""
    return int((row.get('pcm_channels') or {}).get(verb, 1))


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


def voice_rule(row: dict) -> dict | None:
    """The voices this engine speaks with, when its row limits them (`voice_providers`):
    the providers whose designed or saved voices it takes, and the sentence that refuses
    any other voice. None when any voice its provider's terms allow will do."""
    providers = row.get('voice_providers')
    if not providers:
        return None
    return {'providers': list(providers),
            'reason': f"{row['name']} speaks only with voices designed or saved at "
                      f"{', '.join(audio_executors.LABELS.get(p, p) for p in providers)}."}


def restrictions(row: dict) -> list[str]:
    """Why some audio takes may not be this engine's voice (law 12: badge, never filter)."""
    notes = [other['terms']['reason'] for other in audio.engines().values()
             if audio.refusal(audio.terms(other), row.get('provider')) and other['terms'].get('reason')]
    if rule := voice_rule(row):
        notes.append(rule['reason'])
    return list(dict.fromkeys(notes))


def limitations(row: dict, verb: str) -> list[str]:
    notes = list((row.get('notes') or {}).get(verb, []))
    if row.get('lane') == 'local':
        notes.append('Local operations share one queue with image generation. No provider charge.')
    else:
        notes.append("Charges follow the provider's published tariff, which is not a hard cap.")
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
    # The bench a new take would wait behind, by the rule that holds takes, in
    # its own words: they name the fault and the next move (law 4), such as the
    # voice runner's start command or a key to replace.
    bench = stack.scheduler.bench_holding(row['id'])
    return [bench.cause] if bench is not None else []


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
    from .crexil_execution import digest, revision_row
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
                'revision': digest([revision_row(row), verb, specs, notes, destination(row.get('provider'))])[:16],
                'actions': [verb], 'inputs': specs, 'source_limit': len(ROLES.get(verb, [])),
                'source_roles': ROLES.get(verb, []),
                'sources': [{'role': role, 'accepts': ['audio'], 'restrictions': restrictions(row),
                             'voices': voice_rule(row)} for role in ROLES.get(verb, [])],
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
        shortest = row.get('voice_reference_min_seconds')
        if shortest and (facts.get('duration_s') or 0) < shortest:
            raise ValueError(f"{row['name']} needs a voice take of at least {shortest:g} seconds; this one is "
                             f"{facts.get('duration_s') or 0:.1f} seconds. Choose a longer take. All sources are kept.")
        recorded = dict(facts.get('voice') or {})
        if (rule := voice_rule(row)) and recorded.get('provider') not in rule['providers']:
            raise ValueError(f"{rule['reason']} Choose such a take, or a local voice engine.")
        digest = await db.file_sha256(node['output_path'])
        if digest is None:
            raise ValueError('The voice take is no longer on disk. Restore or replace it. All sources are kept.')
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
    if 'seed' in values:
        check_seeds(row, values['seed'], choice.count)
    price = tariff(row, verb)
    counted = measures(price, text, values)
    reserved = crexil_budget.audio_reservation(price, counted)
    request = {'transport': 'audio', 'provider': row['provider'], 'engine': row['id'], 'verb': verb,
               'model_id': (row.get('models') or {}).get(verb), 'text': text,
               'controls': {k: v for k, v in values.items() if k != 'seed'},
               'fixed': dict((row.get('fixed') or {}).get(verb, {})), 'takes': takes(row, verb),
               'pcm_channels': pcm_channels(row, verb), 'voice': voice,
               'tariff': price, 'measures': counted, 'reserved_micro_usd': reserved,
               'destination': destination(row['provider']), 'engine_revision': op['revision']}
    return {'request': request, 'controls': values, 'reserved_micro_usd': reserved,
            'paid': price is None or price['micro_usd'] > 0, 'source_uploads': uploads,
            'seed_range': seed_range(row)}


def frozen_specs(op: dict, compiled: dict, parents: list[str], setup) -> list[dict]:
    """One slot spec per attempt; each attempt's seed follows the seed policy
    inside the engine's seed range, and is the seed the slot records and the
    executor sends."""
    values = compiled['controls']
    policy = presets.resolve_seed_policy(values.get('seed')) if 'seed' in values else None
    low, high = compiled['seed_range']
    committed = float(Decimal(compiled['reserved_micro_usd']) / crexil_budget.MICRO_USD)
    specs = []
    for index in range(op['count']):
        seed = take_seed(policy, index, low, high) if policy else None
        specs.append({'engine': op['engine_id'], 'seed': seed, 'submitted_graph': None,
            'external': {**compiled['request'], 'seed': seed}, 'committed_usd': committed,
            'dials': {'capability': 'audio', 'verb': op['audio_verb'], 'prompt': setup.prompt, 'parents': parents,
                'controls': {k: v for k, v in values.items() if k != 'seed'},
                'crexil': {'operation_id': op['id'], 'operation_revision': op['revision'],
                    # A rate limit or an unreachable provider is refused before
                    # generation, so these visible, bounded retries never pay twice.
                    'automatic_retry': True,
                    'spending_policy': setup.spending_policy if compiled['paid'] else 'hard_cap',
                    'source_uploads': compiled['source_uploads'], 'kind_of_work': setup.purpose,
                    'output_ports': [], 'output_payload': 'audio'}}})
    return specs
