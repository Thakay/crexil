"""Hosted preparation, immutable dispatch, retained outputs and enclosing limits."""

import asyncio
import base64
import contextlib
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app import config, wiring
from app.crexil_budget import accounting, BudgetExceeded
from app.executors import EngineFailure, OpenRouterExecutor
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import reviewed, settled

pytestmark = pytest.mark.anyio

ENDPOINT = {'provider_tag': 'testvendor', 'provider_name': 'Test vendor',
    'supported_parameters': {'resolution': {'type': 'enum', 'values': ['1K', '2K']},
        'n': {'type': 'range', 'min': 1, 'max': 3},
        'seed': {'type': 'boolean'}, 'input_references': {'type': 'range', 'min': 0, 'max': 2}},
    'pricing': [{'billable': 'output_image', 'unit': 'image', 'cost_usd': .04},
                {'billable': 'input_image', 'unit': 'image', 'cost_usd': .01}]}


def discover():
    """The test model in the live catalogue, confirmed a minute ago, with nothing
    left that could replace it: the refresh the boot started is stopped if it
    still runs, the snapshot on disk counts as read, and a catalogue this fresh
    starts no refresh of its own. So no test depends on when, or whether, a
    refresh lands or what its transport answers, and a small step of the wall
    clock never reads the facts as confirmed in the future."""
    catalog = wiring.current.provider_catalog
    if catalog._refresh_task is not None:
        catalog._refresh_task.cancel()
    catalog._snapshot_loaded = True
    confirmed = time.time() - 60
    catalog._live['testvendor/testmodel'] = {'id': 'testvendor/testmodel', 'provider': 'testvendor',
        'label': 'Test Model', 'name': 'Test Model', 'source': 'live', 'supports_seed': True,
        'endpoints': [copy.deepcopy(ENDPOINT)],
        'endpoints_fetched_at': datetime.fromtimestamp(confirmed, timezone.utc).isoformat(),
        'endpoint_discovery_error': None}
    catalog._fetched_at = confirmed


async def hosted(client, wid, **setup):
    operations = (await client.get('/api/crexil/operations')).json()
    op = next(o for o in operations if o['id'] == 'testvendor/testmodel')
    result = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title='Hosted brass', setup={
        'prompt': 'brass lamp', 'spending_limit_usd': 1, 'spending_policy': 'published_tariff',
        'operations': [{'operation_id': op['id'], 'revision': op['revision'], 'count': 1}], **setup}))
    assert result.status_code == 201, result.text
    return result.json()


async def submit(client, direction):
    check = await reviewed(client, direction)
    assert check['ready'], check['errors']
    response = await client.post(f'/api/crexil/directions/{direction["id"]}/submit',
        json=command(revision=direction['revision'], review_token=check['token']))
    assert response.status_code == 201, response.text
    return response.json()


async def limits(client, project, **values):
    result = await client.patch(f'/api/crexil/projects/{project["id"]}', json=command(
        revision=project['revision'], name=project['name'], limits=values))
    assert result.status_code == 200, result.text
    return result.json()


async def test_hard_cap_unknown_controls_changed_prices_and_source_bounds_refuse(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover()
        p = await project(client)
        cases = [({'spending_policy': 'hard_cap'}, 'no enforceable cost bound'),
                 ({'controls': {'model': 'override'}}, 'not supported'),
                 ({'controls': {'resolution': '4K'}}, 'not accepted'),
                 ({'controls': {'n': 4}}, 'declared range'),
                 ({'controls': {'n': 3}, 'spending_limit_usd': .1},
                  'The reserved $0.12 exceeds the $0.10 spending limit.')]
        for setup, message in cases:
            check = await reviewed(client, await hosted(client, p['id'], **setup))
            assert not check['ready'] and any(message in error for error in check['errors']), check
        d = await hosted(client, p['id'])
        review = await reviewed(client, d)
        assert review['ready'], review['errors']
        wiring.current.provider_catalog._live['testvendor/testmodel']['endpoints'][0]['pricing'][0]['cost_usd'] = .05
        rejected = await client.post(f'/api/crexil/directions/{d["id"]}/submit',
            json=command(revision=0, review_token=review['token']))
        assert rejected.status_code == 409, rejected.text
        assert not fakes['openrouter'].starts
        assert not await store().list_turns(p['id'])


async def test_a_refresh_that_lands_after_the_injected_catalogue_never_replaces_it(tmp_path, monkeypatch):
    # A refresh may still be out when a test injects its model; landing later with
    # a listing that lacks it would leave only the configuration's pin, unconfirmed.
    async with booted(tmp_path, monkeypatch) as (client, _):
        catalog = wiring.current.provider_catalog
        if catalog._refresh_task is not None:                 # the boot's own refresh settles first
            with contextlib.suppress(BaseException):
                await catalog._refresh_task
        release = asyncio.Event()

        async def nothing_listed():
            await release.wait()
            return []
        monkeypatch.setattr(catalog.client, 'list_models', nothing_listed)
        refresh = catalog._kick_refresh()
        await asyncio.sleep(0)                                 # out, waiting for its answer
        discover()
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh
        assert 'testvendor/testmodel' in catalog._live
        check = await reviewed(client, await hosted(client, (await project(client))['id']))
        assert check['ready'], check['errors']


async def test_native_continuation_keeps_exact_source_all_outputs_receipt_and_charge(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        discover()
        calls = []
        def transport(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'endpoints': [ENDPOINT]})
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'native-result', 'created': 42,
                'data': [{'b64_json': base64.b64encode(image_bytes()).decode(), 'label': name}
                         for name in ('first', 'second')], 'usage': {'cost': .09}, 'provider_extra': {'keep': True}})
        orc = wiring.current.openrouter
        await orc._client.aclose()
        orc._client = httpx.AsyncClient(base_url=config.OPENROUTER_URL, transport=httpx.MockTransport(transport))
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        p = await project(client)
        source = await imported(client, p['id'])
        d = await hosted(client, p['id'], action='continue', controls={'n': 2, 'resolution': '1K'},
            references=[{'node_id': source['node']['id'], 'contribution': 'Preserve the patina'}])
        review = await reviewed(client, d)
        assert review['maximum_usd'] is None and review['reserved_usd'] == .09
        response = await submit(client, d)
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['status'] == 'landed', turns
        assert calls[0]['input_references'] == [{'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(image_bytes()).decode()}}]
        assert calls[0]['provider'] == {'only': ['testvendor'], 'allow_fallbacks': False}
        assert calls[0]['n'] == 2 and 'Preserve the patina' in calls[0]['prompt']
        snap = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        nodes = [n for n in snap['nodes'] if n['turn_id'] == response['turn_id']]
        assert len(nodes) == 2 and all(n['parents'] == [source['node']['id']] for n in nodes)
        provenance = (await client.get(f"/api/crexil/material/{nodes[0]['id']}/record")).json()
        assert provenance['request']['operation']['maximum_usd_per_attempt'] is None
        assert provenance['request']['setup']['spending_policy'] == 'published_tariff'
        assert provenance['submitted']['transport'] == 'openrouter-images'
        assert provenance['request']['setup']['references'][0]['contribution'] == 'Preserve the patina'
        group = snap['execution_groups'][0]
        assert group['state'] == 'complete' and len(group['outputs']) == 3
        receipt = next(o for o in group['outputs'] if o['role'] == 'provider receipt')
        # The receipt lands first and is no take: the images are the attempt's first and second takes.
        assert (receipt['ordinal'], receipt['node_id']) == (0, None)
        assert sorted(n['output_index'] for n in nodes) == [0, 1]
        (cell,) = (await client.get(f"/api/crexil/directions/{d['id']}/run")).json()['cells']
        assert [take['label'] for take in cell['takes']] == ['0A', '0A.2']
        row = await store()._one('SELECT output_path FROM execution_outputs WHERE id=?', (receipt['id'],))
        saved = json.loads(Path(row['output_path']).read_bytes())
        assert saved['request'] == calls[0] and saved['response']['provider_extra'] == {'keep': True}
        assert snap['charged_usd'] == .09 and snap['accounting']['reserved_usd'] == 0
        reroll = await client.post(f'/api/slots/{turns[0]["slots"][0]["id"]}/reroll')
        assert reroll.status_code == 409 and 'Reuse this direction' in reroll.text


async def test_dispatch_rechecks_tariff_without_sending_paid_request(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        discover()
        sent = []
        def transport(request):
            sent.append(request.method)
            changed = copy.deepcopy(ENDPOINT)
            changed['pricing'][0]['cost_usd'] = .05
            return httpx.Response(200, json={'endpoints': [changed]})
        orc = wiring.current.openrouter
        await orc._client.aclose()
        orc._client = httpx.AsyncClient(base_url=config.OPENROUTER_URL, transport=httpx.MockTransport(transport))
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        p = await project(client)
        await submit(client, await hosted(client, p['id']))
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['kind'] == 'preflight'
        assert sent == ['GET']
        assert (await accounting(store(), p['id']))['reserved_usd'] == 0


async def test_known_charge_survives_timeout_during_later_image_collection(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        discover()
        received = asyncio.Event()
        def transport(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'endpoints': [ENDPOINT]})
            return httpx.Response(200, json={'id': 'slow-result', 'data': [
                {'b64_json': base64.b64encode(image_bytes()).decode()}, {'url': 'https://example.com/later.png'}],
                'usage': {'cost': .1}})
        orc = wiring.current.openrouter
        await orc._client.aclose()
        orc._client = httpx.AsyncClient(base_url=config.OPENROUTER_URL, transport=httpx.MockTransport(transport))
        original = orc._output_bytes
        async def slow(item):
            if 'url' in item:
                received.set()
                await asyncio.Event().wait()
            return await original(item)
        monkeypatch.setattr(orc, '_output_bytes', slow)
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        wiring.current.scheduler._external_timeout = .1
        p = await limits(client, await project(client), spending_limit_usd=.08, spending_policy='published_tariff')
        await submit(client, await hosted(client, p['id'], controls={'n': 2}))
        await asyncio.wait_for(received.wait(), 1)
        counts = await accounting(store(), p['id'])
        assert counts['charged_usd'] == .1 and counts['reserved_usd'] == 0
        check = await reviewed(client, await hosted(client, p['id']))
        assert not check['ready'] and any('spending limit' in e for e in check['errors'])
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['kind'] == 'timeout'
        snap = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        assert snap['charged_usd'] == .1
        assert len(snap['nodes']) == 1 and snap['nodes'][0]['available']
        assert snap['execution_groups'][0]['state'] == 'incomplete'
        assert any(o['role'] == 'provider receipt' and o['available'] for o in snap['execution_groups'][0]['outputs'])
        async def recovered(_item):
            return image_bytes()
        monkeypatch.setattr(orc, '_output_bytes', recovered)
        sid = turns[0]['slots'][0]['id']
        replay = await client.post(f'/api/crexil/attempts/{sid}/collect', json=command(take=1))
        assert replay.status_code == 200, replay.text
        assert orc.requests_total == 1
        snap = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        assert snap['charged_usd'] == .1 and len(snap['nodes']) == 2
        assert snap['execution_groups'][0]['state'] == 'complete'
        assert snap['turns'][0]['slots'][0]['status'] == 'landed'


async def test_partial_output_failure_keeps_good_material_and_charges_once(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        discover()
        def transport(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'endpoints': [ENDPOINT]})
            return httpx.Response(200, json={'data': [
                {'b64_json': base64.b64encode(image_bytes()).decode()}, 'malformed provider item'],
                'usage': {'cost': .08}})
        orc = wiring.current.openrouter
        await orc._client.aclose()
        orc._client = httpx.AsyncClient(base_url=config.OPENROUTER_URL, transport=httpx.MockTransport(transport))
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        p = await project(client)
        await submit(client, await hosted(client, p['id'], controls={'n': 2}))
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['kind'] == 'collection'
        snap = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        assert snap['charged_usd'] == .08 and snap['accounting']['reserved_usd'] == 0
        assert len(snap['nodes']) == 1 and snap['nodes'][0]['available']
        missing = next(o for o in snap['execution_groups'][0]['outputs'] if o['error'])
        assert missing['descriptor']['provider'] == 'malformed provider item'


async def test_project_budget_serializes_acceptance_and_keeps_unknown_charge(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover()
        fakes['openrouter'].default = Step(outcome=EngineFailure('timeout', 'unknown delivery'))
        p = await limits(client, await project(client), spending_limit_usd=.04, spending_policy='published_tariff')
        a, b = await hosted(client, p['id']), await hosted(client, p['id'])
        ra, rb = await reviewed(client, a), await reviewed(client, b)
        results = await asyncio.gather(*[client.post(f'/api/crexil/directions/{d["id"]}/submit',
            json=command(revision=0, review_token=r['token'])) for d, r in ((a, ra), (b, rb))])
        assert sorted(r.status_code for r in results) == [201, 409]
        await settled(p['id'])
        counts = await accounting(store(), p['id'])
        assert counts['reserved_usd'] == .04 and counts['charged_usd'] == 0
        assert counts['attempts'] == 1 and counts['unresolved_attempts'] == 1
        assert len(fakes['openrouter'].starts) == 1


async def test_lowered_project_limit_gates_queued_dispatch_and_rerolls_count(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        # Exercise the database boundary directly so legacy callers cannot bypass it.
        turn = await store().create_turn_with_slots(p['id'], 'test', {}, [
            {'engine': 'testvendor/testmodel', 'committed_usd': .04},
            {'engine': 'testvendor/testmodel', 'committed_usd': .04}])
        p = await limits(client, p, spending_limit_usd=.08, spending_policy='published_tariff')
        one, two = turn['slots']
        await store().dispatch_slot(one['id'])
        await store().fail_slot(one['id'], cause='charged', charged_usd=.06)
        with pytest.raises(BudgetExceeded, match='spending limit'):
            await store().dispatch_slot(two['id'])
        p = await limits(client, p, attempt_limit=2)
        await store().reroll_slot(one['id'], 2, committed_usd=.04)
        with pytest.raises(BudgetExceeded, match='attempt limit'):
            await store().dispatch_slot(one['id'])
        counts = await accounting(store(), p['id'])
        assert counts['attempts'] == 3 and counts['charged_usd'] == .06 and counts['reserved_usd'] == .08


async def test_prior_attempt_bill_correction_cannot_settle_new_attempt(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        turn = await store().create_turn_with_slots(p['id'], 'test', {}, [
            {'engine': 'testvendor/testmodel', 'committed_usd': .04}])
        sid = turn['slots'][0]['id']
        await store().dispatch_slot(sid)
        await store().fail_slot(sid, cause='charged blank', charged_usd=.04)
        await store().reroll_slot(sid, 2, committed_usd=.04)
        await store().dispatch_slot(sid)
        await store().record_charge(sid, .01, attempt=1, note='Prior attempt bill check')
        await store().append_event('slot.charge_verified', workspace_id=p['id'], slot_id=sid,
                                   payload={'attempt': 1, 'charged_usd': .05})
        counts = await accounting(store(), p['id'])
        assert counts['charged_usd'] == .05 and counts['reserved_usd'] == .04
        assert counts['unresolved_attempts'] == 1


async def test_limit_lowered_during_endpoint_check_prevents_paid_send(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        discover()
        entered, release = asyncio.Event(), asyncio.Event()
        orc = wiring.current.openrouter
        async def endpoints(_model):
            entered.set()
            await release.wait()
            return [ENDPOINT]
        monkeypatch.setattr(orc, 'model_endpoints', endpoints)
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        p = await limits(client, await project(client), spending_limit_usd=.04, spending_policy='published_tariff')
        await submit(client, await hosted(client, p['id']))
        await asyncio.wait_for(entered.wait(), 1)
        await limits(client, p, spending_limit_usd=0, spending_policy='published_tariff')
        release.set()
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['kind'] == 'preflight'
        assert orc.requests_total == 0
        assert (await accounting(store(), p['id']))['reserved_usd'] == 0


async def test_raising_limit_resumes_accepted_budget_hold(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover()
        fakes['openrouter'].default = Step(data=image_bytes(), cost=.04)
        scheduler = wiring.current.scheduler
        adopt = scheduler.adopt_prepared_turn
        async def wait_for_owner(_turn):
            pass
        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', wait_for_owner)
        p = await limits(client, await project(client), spending_limit_usd=.04, spending_policy='published_tariff')
        accepted = await submit(client, await hosted(client, p['id']))
        p = await limits(client, p, spending_limit_usd=0, spending_policy='published_tariff')
        monkeypatch.setattr(scheduler, 'adopt_prepared_turn', adopt)
        await adopt(accepted['turn_id'])
        for _ in range(100):
            turn = await store().get_turn(accepted['turn_id'])
            if turn['slots'][0]['status'] == 'held':
                break
            await asyncio.sleep(.01)
        assert turn['slots'][0]['status'] == 'held' and not fakes['openrouter'].starts
        await limits(client, p, spending_limit_usd=.04, spending_policy='published_tariff')
        turns = await settled(p['id'])
        assert turns[0]['slots'][0]['status'] == 'landed'
        assert len(fakes['openrouter'].starts) == 1
