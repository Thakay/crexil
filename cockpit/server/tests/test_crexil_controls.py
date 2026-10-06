"""Artist and agent controls survive delivery races and coordinate both lanes."""

import asyncio

import pytest

from app import wiring
from app.scheduler import Scheduler
from app.executors import OpenRouterExecutor, Reconciliation
from fakes import FakeHandle, Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, project
from test_crexil_execution import attest, draft, reviewed, settled

pytestmark = pytest.mark.anyio


async def control(client, scope, target, action, **extra):
    url = f'/api/crexil/execution/{scope}/{target}'
    before = (await client.get(url)).json()
    response = await client.post(url, json=command(revision=before['revision'], action=action, **extra))
    assert response.status_code == 200, response.text
    return response.json()


async def submit(client, direction):
    review = await reviewed(client, direction)
    assert review['ready'], review['errors']
    response = await client.post(f'/api/crexil/directions/{direction["id"]}/submit',
                                json=command(revision=direction['revision'], review_token=review['token']))
    assert response.status_code == 201, response.text
    return response.json()


async def statuses(turn_id, expected):
    for _ in range(100):
        turn = await store().get_turn(turn_id)
        if [slot['status'] for slot in turn['slots']] == expected:
            return turn
        await asyncio.sleep(.01)
    raise AssertionError([(slot['status'], slot['cause']) for slot in turn['slots']])


async def test_project_pause_survives_restart_and_other_projects_keep_running(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        paused = await control(client, 'project', p['id'], 'pause', reason='Artist reviewing the current direction')
        assert paused['paused'] and paused['actor']['kind'] == 'human'
        held = await submit(client, await draft(client, p['id']))
        await statuses(held['turn_id'], ['held', 'held'])
        other = await project(client)
        await submit(client, await draft(client, other['id']))
        await settled(other['id'])
        assert len(fakes['comfy'].starts) == 2
        await wiring.current.scheduler.stop()
        scheduler = Scheduler(store(), list(fakes.values()), route=lambda engine: 'openrouter' if '/' in engine else 'comfy')
        wiring.current.scheduler = scheduler
        await scheduler.start()
        await statuses(held['turn_id'], ['held', 'held'])
        state = (await client.get(f'/api/crexil/execution/project/{p["id"]}')).json()
        assert state['paused'] and state['revision'] == paused['revision']
        await control(client, 'project', p['id'], 'resume')
        await settled(p['id'])
        assert len(fakes['comfy'].starts) == 4


async def test_scope_cancel_targets_existing_attempts_and_preserves_project_pause(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        gate = asyncio.Event()
        fakes['comfy'].default = Step(data=image_bytes(), gate=gate)
        p = await project(client)
        first = await submit(client, await draft(client, p['id']))
        await statuses(first['turn_id'], ['running', 'queued'])
        await control(client, 'project', p['id'], 'pause')
        await statuses(first['turn_id'], ['running', 'held'])
        later = await submit(client, await draft(client, p['id']))
        await statuses(later['turn_id'], ['held', 'held'])
        cancelled = await control(client, 'run', first['turn_id'], 'cancel')
        assert cancelled['cancel_requested'] == 1
        await statuses(first['turn_id'], ['failed', 'skipped'])
        await statuses(later['turn_id'], ['held', 'held'])
        state = (await client.get(f'/api/crexil/execution/project/{p["id"]}')).json()
        assert state['paused'] and state['cancel_requested'] == 0
        gate.set()
        await control(client, 'project', p['id'], 'resume')
        await statuses(later['turn_id'], ['landed', 'landed'])
        assert len(fakes['comfy'].starts) == 3


async def test_same_control_delivery_is_idempotent_and_stale_agent_is_refused(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        url = f'/api/crexil/execution/project/{p["id"]}'
        body = command(revision=0, action='pause', reason='Review')
        body['actor'] = {'kind': 'agent', 'name': 'Studio guide'}
        a, b = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
        assert a.status_code == b.status_code == 200 and a.json() == b.json()
        stale = await client.post(url, json=command(revision=0, action='resume'))
        assert stale.status_code == 409
        events = await store().events_since(0)
        records = [e for e in events if e['type'] == 'execution.control']
        assert len(records) == 1 and records[0]['payload']['actor']['name'] == 'Studio guide'


async def test_local_cancel_during_start_interrupts_the_eventual_handle(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        entered, release = asyncio.Event(), asyncio.Event()
        original = fakes['comfy'].start
        async def upload(req):
            entered.set()
            await release.wait()
            return await original(req)
        monkeypatch.setattr(fakes['comfy'], 'start', upload)
        fakes['comfy'].default = Step(data=image_bytes(), outcome='hang')
        p = await project(client)
        accepted = await submit(client, await draft(client, p['id']))
        await asyncio.wait_for(entered.wait(), 1)
        await control(client, 'project', p['id'], 'cancel')
        release.set()
        await statuses(accepted['turn_id'], ['failed', 'skipped'])
        assert len(fakes['comfy'].starts) == len(fakes['comfy'].cancels) == 1
        assert not await store().list_nodes(p['id'])


async def test_cancel_during_hosted_preflight_confirms_no_paid_dispatch(tmp_path, monkeypatch):
    from test_crexil_hosted import ENDPOINT, discover, hosted
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
        p = await project(client)
        accepted = await submit(client, await hosted(client, p['id']))
        await asyncio.wait_for(entered.wait(), 1)
        await control(client, 'project', p['id'], 'cancel')
        await statuses(accepted['turn_id'], ['late'])
        release.set()
        await statuses(accepted['turn_id'], ['skipped'])
        assert orc.requests_total == 0
        state = (await client.get(f'/api/crexil/execution/project/{p["id"]}')).json()
        assert state['cancel_requested'] == 0
        snapshot = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
        assert snapshot['accounting']['reserved_usd'] == snapshot['charged_usd'] == 0


@pytest.mark.parametrize('external', [False, True])
async def test_restart_applies_committed_cancellation_and_collects_its_effect(tmp_path, monkeypatch, external):
    from app.execution_controls import record
    from test_crexil_hosted import discover, hosted
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover() if external else attest()
        ex = fakes['openrouter' if external else 'comfy']
        ex.default = Step(data=image_bytes(), outcome='hang')
        p = await project(client)
        direction = await (hosted(client, p['id']) if external else draft(client, p['id']))
        accepted = await submit(client, direction)
        turn = await statuses(accepted['turn_id'], ['running'] if external else ['running', 'queued'])
        sid = turn['slots'][0]['id']
        for _ in range(100):
            if (await store().get_slot(sid))['job_ref']:
                break
            await asyncio.sleep(.01)
        await wiring.current.scheduler.stop()
        # Simulate interruption after the command commits but before its effects.
        await record(store(), 'project', p['id'], command(revision=0, action='cancel'))
        ex.reconcile_map[sid] = Reconciliation('running', handle=FakeHandle(
            job_ref=(await store().get_slot(sid))['job_ref'], step=ex.default))
        scheduler = Scheduler(store(), list(fakes.values()), route=lambda engine: 'openrouter' if '/' in engine else 'comfy')
        wiring.current.scheduler = scheduler
        await scheduler.start()
        await statuses(turn['id'], ['late'] if external else ['failed', 'skipped'])
        state = (await client.get(f'/api/crexil/execution/project/{p["id"]}')).json()
        assert state['cancel_requested'] == int(external)
        assert len(ex.starts) == 1
        if external:
            snapshot = (await client.get(f'/api/crexil/projects/{p["id"]}')).json()
            assert snapshot['accounting']['reserved_usd'] == .04
            assert ex.cancels == []
        else:
            assert len(ex.cancels) == 1
