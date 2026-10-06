"""Cancellation wins retry and finalization races without losing failure evidence."""

import asyncio
import json

import pytest

from app import execution_outputs
from app.crexil_budget import accounting
from app.execution_controls import ExecutionCancelled, record, state
from app.executors import EngineFailure, GpuReading
from app.scheduler import RetryRule
from fakes import FakeExecutor, Step
from test_scheduler import make_batch, opened, running, settle

pytestmark = pytest.mark.anyio


async def request_cancel(db, workspace_id):
    before = await state(db, 'project', workspace_id)
    return await record(db, 'project', workspace_id, {
        'revision': before['revision'], 'action': 'cancel',
        'actor': {'kind': 'human', 'name': 'Ada'}, 'reason': 'stop this work'})


async def completed_event(db, slot_id, event_type):
    for _ in range(100):
        events = await db.slot_events(slot_id)
        if any(event['type'] == event_type for event in events):
            return events
        await asyncio.sleep(.01)
    raise AssertionError(f'{event_type} was not recorded')


@pytest.mark.parametrize('gpu_retry', [False, True])
async def test_committed_cancel_prevents_normal_and_gpu_retries_without_losing_failure(
        tmp_path, monkeypatch, gpu_retry):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        lane, engine = ('local', 'local') if gpu_retry else ('external', 'provider/model')
        failure = EngineFailure('oom' if gpu_retry else 'rate_limit', 'original runtime failure',
                                charged_usd=0 if gpu_retry else .02)
        executor = FakeExecutor('fake', lane, default=Step(outcome=failure))
        async def gpu_probe():
            return GpuReading(total=24 << 30, free=1 << 30, comfy=1 << 30, foreign=22 << 30)
        async with running(db, tmp_path, [executor], {engine: 'fake'}, gpu_probe=gpu_probe,
                           retry_policy={'rate_limit': RetryRule(1, 0)}) as scheduler:
            entered, release = asyncio.Event(), asyncio.Event()
            original = scheduler._gpu_held if gpu_retry else scheduler._retry_requeue
            async def hold_retry(*args):
                entered.set()
                await release.wait()
                return await original(*args)
            monkeypatch.setattr(scheduler, '_gpu_held' if gpu_retry else '_retry_requeue', hold_retry)
            turn, requests = await make_batch(db, ws, [(engine, lane)], committed=.04)
            sid = turn['slots'][0]['id']
            await scheduler.submit_turn(turn['id'], requests)
            await asyncio.wait_for(entered.wait(), 1)
            # Simulate command commit before its postcommit scheduler callback.
            await request_cancel(db, ws['id'])
            assert not scheduler._takes[sid].cancel_requested
            release.set()
            await settle(db, turn['id'], until={'failed'})
            await scheduler.apply_execution_controls()
            slot = await db.get_slot(sid)
            assert slot['attempt'] == 1 and executor.starts == [sid]
            assert slot['kind'] == failure.kind
            assert 'original runtime failure' in slot['detail']
            assert await db.workspace_charged(ws['id']) == failure.charged_usd
            assert not any(e['type'] == 'slot.retrying' for e in await db.slot_events(sid))
            group = await db._one('SELECT * FROM execution_groups WHERE slot_id=?', (sid,))
            assert group['state'] == 'incomplete'
            assert json.loads(group['metadata_json'])['failure_kind'] == failure.kind
            assert (await state(db, 'project', ws['id']))['cancel_requested'] == 0
            assert sid not in scheduler._takes
            assert not scheduler._benches and scheduler._gpu_task is None


@pytest.mark.parametrize('lane', ['local', 'external'])
async def test_dispatch_and_restart_block_cancelled_automatic_retry(tmp_path, lane):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        engine = 'local' if lane == 'local' else 'provider/model'
        turn, _ = await make_batch(db, ws, [(engine, lane)], committed=.04)
        slot = await db.dispatch_slot(turn['slots'][0]['id'])
        await request_cancel(db, ws['id'])
        # A queued retry persisted by an older scheduler must not escape the
        # cancellation when restart would increment its attempt number.
        await db.transition_slot(slot['id'], 'queued', retry_of='rate_limit')
        with pytest.raises(ExecutionCancelled):
            await db.dispatch_slot(slot['id'])
        assert (await db.get_slot(slot['id']))['attempt'] == 1
        executor = FakeExecutor('fake', lane, default=Step(outcome='hang'))
        async with running(db, tmp_path, [executor], {engine: 'fake'}) as scheduler:
            await settle(db, turn['id'], until={'skipped'})
            assert not executor.starts
            assert slot['id'] not in scheduler._takes
            assert (await db.get_slot(slot['id']))['attempt'] == 1


@pytest.mark.parametrize(('kind', 'charge', 'reported', 'expected_status'), [
    ('preflight', 0, False, 'skipped'),
    ('charged_blank', .03, False, 'late'),
    ('charged_blank', .03, True, 'late'),
    ('timeout', None, False, 'late'),
])
async def test_cancel_between_failure_and_finalizer_retains_charge_truth(
        tmp_path, monkeypatch, kind, charge, reported, expected_status):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        failure = EngineFailure(kind, 'original provider failure', charged_usd=charge, job_ref='provider-job')
        executor = FakeExecutor('fake', 'external', default=Step(outcome=failure))
        async with running(db, tmp_path, [executor], {'provider/model': 'fake'}, retry_policy={}) as scheduler:
            entered, release = asyncio.Event(), asyncio.Event()
            original = scheduler._finalize_failure
            async def hold_finalizer(*args):
                entered.set()
                await release.wait()
                return await original(*args)
            monkeypatch.setattr(scheduler, '_finalize_failure', hold_finalizer)
            turn, requests = await make_batch(db, ws, [('provider/model', 'external')], committed=.04)
            sid = turn['slots'][0]['id']
            await scheduler.submit_turn(turn['id'], requests)
            await asyncio.wait_for(entered.wait(), 1)
            if reported:
                await db.report_provider_charge(await db.get_slot(sid), charge, 'provider-job')
            await request_cancel(db, ws['id'])
            await scheduler.apply_execution_controls()
            assert (await db.get_slot(sid))['status'] == 'late'
            release.set()
            events = await completed_event(db, sid, 'slot.late_failed')
            slot = await db.get_slot(sid)
            assert slot['status'] == expected_status
            assert slot['charged_usd'] == charge
            money = await accounting(db, ws['id'])
            assert money['charged_usd'] == (charge or 0)
            assert money['reserved_usd'] == (.04 if charge is None else 0)
            final = next(e for e in events if e['type'] == 'slot.late_failed')
            assert final['payload']['kind'] == kind
            assert final['payload']['attempt'] == slot['attempt'] == 1
            assert final['payload']['job_ref'] == slot['job_ref'] == 'provider-job'
            assert final['payload']['charged_usd'] == charge
            assert 'original provider failure' in final['payload']['detail']
            assert bool([e for e in events if e['type'] == 'slot.stop_confirmed']) == (kind == 'preflight')
            assert executor.starts == [sid]
            assert not scheduler._benches


async def test_cancel_cannot_split_failure_state_from_its_zero_charge(tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        executor = FakeExecutor('fake', 'external', default=Step(
            outcome=EngineFailure('preflight', 'not sent', charged_usd=0)))
        async with running(db, tmp_path, [executor], {'provider/model': 'fake'}, retry_policy={}) as scheduler:
            entered, release = asyncio.Event(), asyncio.Event()
            original = execution_outputs.finish
            async def hold_collection(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)
            monkeypatch.setattr(execution_outputs, 'finish', hold_collection)
            turn, requests = await make_batch(db, ws, [('provider/model', 'external')], committed=.04)
            sid = turn['slots'][0]['id']
            await scheduler.submit_turn(turn['id'], requests)
            await asyncio.wait_for(entered.wait(), 1)
            cancellation = asyncio.create_task(request_cancel(db, ws['id']))
            try:
                await asyncio.sleep(0)
                assert not cancellation.done()
            finally:
                release.set()
            await asyncio.wait_for(cancellation, 1)
            await scheduler.apply_execution_controls()
            slot = await db.get_slot(sid)
            assert slot['status'] == 'failed' and slot['charged_usd'] == 0
            assert (await accounting(db, ws['id']))['reserved_usd'] == 0
            events = await db.events_since(0)
            failed = next(e for e in events if e['type'] == 'slot.failed')
            stopped = next(e for e in events if e['type'] == 'execution.control')
            assert failed['seq'] < stopped['seq']
            assert stopped['payload']['targets'] == []
