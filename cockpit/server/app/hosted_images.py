"""Compile the declared native image interface into an immutable hosted request.

Endpoint descriptions own capabilities and tariffs. Only explicitly understood
controls and flat per-image billing can be compiled; all other saved values are
refused in words. A tariff reserves expected spend but never promises a hard cap,
and a tariff that cannot state its amount exactly is an unknown price (law 22).
Endpoint facts older than CATALOG_TTL_S never compile; the refusal states their
observed age and why no fresher facts exist (laws 3 and 17).
Dispatch must recheck the exact endpoint description before sending source bytes.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from PIL import Image

from .crexil_budget import UnknownPrice
from .providers import CATALOG_TTL_S

# Observations that change without the model changing; a revision ignores them.
VOLATILE = ('endpoints_fetched_at', 'endpoint_discovery_error', 'catalogue_state')


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def revision(model: dict) -> str:
    return fingerprint({k: v for k, v in model.items() if k not in VOLATILE})[:16]


def _elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f'{minutes} min' if minutes < 120 else f'{minutes // 60} h'


def stale_reason(model: dict, age: float | None) -> str:
    """One plain line: how old the confirmed endpoint facts are, and why nothing fresher exists."""
    limit = f'{CATALOG_TTL_S // 3600} hours'
    first = (f'Endpoint capabilities were last confirmed {_elapsed(age)} ago; hosted requests need '
             f'them confirmed within {limit}.' if age is not None and age >= 0 else
             f'Endpoint capabilities have not been confirmed; hosted requests need them confirmed within {limit}.')
    state = model.get('catalogue_state') or {}
    if state.get('refreshing'):
        return first + ' A catalogue refresh is running. Review again when it finishes.'
    if state.get('last_failure'):
        return first + f" The latest catalogue refresh failed: {state['last_failure']}. Review again to retry."
    return first + ' Review again to refresh the catalogue.'


def inputs(model: dict) -> list[dict]:
    endpoints = model.get('endpoints', [])
    tags = list(dict.fromkeys(e['provider_tag'] for e in endpoints if e.get('provider_tag')))
    result = [{'key': 'provider', 'label': 'Provider', 'type': 'enum', 'choices': tags,
               'default': tags[0] if tags else None, 'required': True}]
    declarations = [e.get('supported_parameters') or {} for e in endpoints]
    for key, label in (('resolution', 'Resolution'), ('aspect_ratio', 'Aspect ratio'),
                       ('quality', 'Quality'), ('background', 'Background'), ('output_format', 'Format')):
        values = list(dict.fromkeys(v for d in declarations for v in d.get(key, {}).get('values', [])))
        if key == 'output_format':
            values = [v for v in values if v in ('png', 'jpeg', 'webp')]
        if values:
            result.append({'key': key, 'label': label, 'type': 'enum', 'choices': values})
    if any('n' in d for d in declarations):
        result.append({'key': 'n', 'label': 'Images per attempt', 'type': 'int', 'min': 1,
                       'max': min(10, max(d.get('n', {}).get('max', 1) for d in declarations)), 'default': 1})
    if any('seed' in d for d in declarations):
        result.append({'key': 'seed', 'label': 'Seed', 'type': 'seed'})
    return result


def source_limit(model: dict) -> int:
    return min(16, max([0, *[e.get('supported_parameters', {}).get('input_references', {}).get('max', 0)
                             for e in model.get('endpoints', [])]]))


def tariff(endpoint: dict, output_count: int, reference_count: int) -> Decimal:
    """The exact reservation for one attempt from the endpoint's published
    per-image tariff. A tariff that cannot state that amount exactly raises
    `UnknownPrice`: an unknown price counts as over every cap, so the whole run
    is refused before any engine is called (law 22)."""
    prices = endpoint.get('pricing')
    if not isinstance(prices, list) or not prices:
        raise UnknownPrice('This endpoint has no complete published tariff.')
    if not any(p.get('billable') == 'output_image' for p in prices):
        raise UnknownPrice('The published tariff does not describe output-image charges.')
    total = Decimal(0)
    seen = set()
    for price in prices:
        billable = price.get('billable')
        try:
            rate = Decimal(str(price['cost_usd']))
        except (KeyError, InvalidOperation) as exc:
            raise UnknownPrice('The published tariff has an unknown rate.') from exc
        if not rate.is_finite() or rate < 0 or price.get('variants') or price.get('conditions'):
            raise UnknownPrice('This conditional tariff is not supported for budget reservations.')
        if set(price) - {'billable', 'unit', 'cost_usd'}:
            raise UnknownPrice('The tariff has additional pricing rules that need qualification.')
        if rate == 0:
            continue
        if price.get('unit') != 'image' or billable not in ('input_image', 'output_image') or billable in seen:
            raise UnknownPrice('This tariff cannot be reserved from a fixed number of images.')
        seen.add(billable)
        total += rate * (output_count if billable == 'output_image' else reference_count)
    return total


def compile_request(model: dict, setup, choice, source_blobs: list[bytes]) -> dict:
    if model.get('endpoint_discovery_error'):
        raise ValueError('Endpoint discovery failed for this model at the last catalogue refresh. '
                         'Review again in a minute to retry it.')
    try:
        fetched = datetime.fromisoformat(model['endpoints_fetched_at']).astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    if age is None or not 0 <= age <= CATALOG_TTL_S:
        raise ValueError(stale_reason(model, age))
    given = {**setup.controls, **choice.controls}
    declared = inputs(model)
    tag = given.pop('provider', declared[0]['default'])
    endpoints = [e for e in model.get('endpoints', []) if e.get('provider_tag') == tag]
    if len(endpoints) != 1:
        raise ValueError('Choose one unambiguous provider endpoint.')
    endpoint = endpoints[0]
    supported = endpoint.get('supported_parameters') or {}
    if 'output_format' in supported and 'output_format' not in given:
        formats = [v for v in supported['output_format'].get('values', []) if v in ('png', 'jpeg', 'webp')]
        if not formats:
            raise ValueError('This endpoint has no declared supported raster output format.')
        given['output_format'] = formats[0]
    allowed = {s['key'] for s in declared} - {'provider'}
    request = {'model': model['id'], 'prompt': setup.prompt,
               'provider': {'only': [tag], 'allow_fallbacks': False}}
    if not setup.prompt.strip():
        raise ValueError('Hosted image generation needs a prompt.')
    if any(r.role is not None or r.strength is not None for r in setup.references):
        raise ValueError('This endpoint has no mapped source role or strength control. Clear those values explicitly.')
    for key, value in given.items():
        if key == 'output_format' and value not in ('png', 'jpeg', 'webp'):
            raise ValueError('This route can collect PNG, JPEG and WebP outputs. Choose a supported raster format.')
        if key not in allowed or key not in supported:
            raise ValueError(f'{key} is not supported by this provider endpoint. Its saved value is kept.')
        spec = supported[key]
        if key == 'seed':
            from .crexil_execution import strict_value
            seed_policy = strict_value({'key': 'seed', 'type': 'seed'}, value)
            if seed_policy['mode'] == 'random':
                continue
            value = seed_policy['value']
            if seed_policy['mode'] == 'fixed':
                from . import presets
                if value + choice.count - 1 >= presets.MAX_SEED:
                    raise ValueError('The fixed seed sequence exceeds the supported range.')
        elif spec.get('type') == 'enum':
            if value not in spec.get('values', []):
                raise ValueError(f'{key} is not accepted by this provider endpoint.')
        elif key == 'n':
            if type(value) is not int or not max(1, spec.get('min', 1)) <= value <= min(10, spec.get('max', 1)):
                raise ValueError('Images per attempt is outside this endpoint\'s declared range.')
        else:
            raise ValueError(f'The declared {key} interface needs qualification.')
        request[key] = value
    n = request.get('n', 1)
    if 'n' in supported and (supported['n'].get('type') != 'range' or
            type(n) is not int or not max(1, supported['n'].get('min', 1)) <= n <= min(10, supported['n'].get('max', 1))):
        raise ValueError('Images per attempt is outside this endpoint\'s declared range.')
    refs = supported.get('input_references', {})
    if not refs.get('min', 0) <= len(source_blobs) <= min(16, refs.get('max', 0)):
        raise ValueError('The number of source images is outside this endpoint\'s declared range.')
    if source_blobs:
        request['input_references'] = []
        for blob in source_blobs:
            with Image.open(io.BytesIO(blob)) as image:
                mime = Image.MIME.get(image.format)
                if mime not in ('image/png', 'image/jpeg', 'image/webp'):
                    raise ValueError('This hosted route accepts PNG, JPEG or WebP source bytes. Prepare a supported copy first.')
            request['input_references'].append({'type': 'image_url', 'image_url':
                {'url': 'data:' + mime + ';base64,' + base64.b64encode(blob).decode()}})
    contributions = [f'Source {i + 1}: {r.contribution}' for i, r in enumerate(setup.references) if r.contribution.strip()]
    if contributions:
        request['prompt'] += '\n\n' + '\n'.join(contributions)
    cost = tariff(endpoint, n, len(source_blobs))
    return {'request': request, 'endpoint': copy.deepcopy(endpoint),
            'endpoint_sha256': fingerprint(endpoint), 'endpoints_fetched_at': model['endpoints_fetched_at'],
            'reserved_usd': float(cost), 'policy': setup.spending_policy}
