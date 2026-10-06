"""Durable scope controls preserve requests, attribution and attempt identity."""

import json
import uuid

import pytest

from app.crexil_schemas import ControlExecution
from app.crexil_store import Conflict, CreativeStore
from app.execution_controls import (ExecutionPaused, cancel_requested, cancellations,
                                    guard_dispatch, record, state)
from tests.test_db import opened

pytestmark = pytest.mark.anyio


async def turn(db, wid, count=1):
    return await db.create_turn_with_slots(wid, 'keep this request', {'cook': 3}, [
        {'engine': 'local', 'seed': i, 'committed_usd': 0,
         'submitted_graph': {'seed': i}} for i in range(count)])


async def control(db, scope, target_id, action, *, revision=None, actor=None, reason='artist choice'):
    if revision is None:
        revision = (await state(db, scope, target_id))['revision']
    body = ControlExecution(request_id=str(uuid.uuid4()), revision=revision, action=action,
                            actor=actor or {'kind': 'human', 'name': 'Ada'}, reason=reason)
    return await CreativeStore(db).command(body.request_id, f'{scope}:{target_id}:control',
        body.model_dump(), lambda: record(db, scope, target_id, body))


async def test_state_validates_scope_target_and_has_unpaused_defaults(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await turn(db, ws['id'])
        for scope, target in [('project', ws['id']), ('run', run['id'])]:
            assert await state(db, scope, target) == {
                'scope': scope, 'target_id': target, 'workspace_id': ws['id'],
                'revision': 0, 'paused': False, 'cancel_requested': 0,
                'last_action': None, 'actor': None, 'reason': '', 'requested_at': None}
            with pytest.raises(LookupError, match='could not be found'):
                await state(db, scope, 'missing')
        with pytest.raises(ValueError, match='scope'):
            await state(db, 'engine', 'local')


async def test_pause_preserves_running_and_other_holds_and_frozen_requests(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await turn(db, ws['id'], 3)
        queued, held, running = run['slots']
        await db.transition_slot(held['id'], 'held', payload={'bench': 'engine:local', 'reason': 'engine unavailable'})
        await db.dispatch_slot(running['id'])
        held_events = await db.slot_events(held['id'])
        paused = await control(db, 'project', ws['id'], 'pause', reason='review results')
        assert paused['paused'] is True
        assert paused['last_action'] == 'pause'
        assert paused['actor'] == {'kind': 'human', 'name': 'Ada'}
        events = await db.events_since(0, workspace_id=ws['id'])
        event = next(e for e in events if e['type'] == 'execution.control')
        assert paused['revision'] == event['seq'] < events[-1]['seq']
        assert paused['requested_at'] == event['ts']
        assert (await db.get_slot(running['id']))['status'] == 'running'
        assert await db.slot_events(held['id']) == held_events
        current = await db.get_slot(queued['id'])
        assert current['status'] == 'held'
        for key in ('seed', 'dials', 'submitted_graph', 'external', 'attempt'):
            assert current[key] == queued[key]
        assert (await db.slot_events(queued['id']))[-1]['payload'] == {
            'from': 'queued', 'pause': True, 'reason': 'review results'}
        resumed = await control(db, 'project', ws['id'], 'resume')
        assert resumed['paused'] is False
        assert (await db.get_slot(queued['id']))['status'] == 'held'
        assert await db.slot_events(held['id']) == held_events


async def test_overlapping_controls_block_new_work_until_each_scope_resumes(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        other = await db.create_workspace('other')
        run = await turn(db, ws['id'])
        await control(db, 'run', run['id'], 'pause')
        await control(db, 'project', ws['id'], 'pause')
        new = (await turn(db, ws['id']))['slots'][0]
        unaffected = (await turn(db, other['id']))['slots'][0]
        with pytest.raises(ExecutionPaused) as error:
            await db.dispatch_slot(new['id'])
        assert (error.value.scope, error.value.target_id) == ('project', ws['id'])
        assert (await db.get_slot(new['id']))['attempt'] == 0
        await db.dispatch_slot(unaffected['id'])
        await control(db, 'project', ws['id'], 'resume')
        await db.dispatch_slot(new['id'])
        with pytest.raises(ExecutionPaused) as error:
            await guard_dispatch(db, run['slots'][0])
        assert (error.value.scope, error.value.target_id) == ('run', run['id'])
        await control(db, 'run', run['id'], 'resume')
        await guard_dispatch(db, run['slots'][0])


async def test_cancel_freezes_active_attempts_skips_unsent_and_preserves_pause(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await turn(db, ws['id'], 5)
        queued, held, running, late, failed = run['slots']
        await db.transition_slot(held['id'], 'held', payload={'limit': True})
        for slot in (running, late, failed):
            await db.dispatch_slot(slot['id'])
        await db.transition_slot(late['id'], 'late')
        await db.fail_slot(failed['id'], cause='failed')
        await control(db, 'run', run['id'], 'pause')
        stopped = await control(db, 'run', run['id'], 'cancel')
        assert stopped['paused'] is True
        assert stopped['cancel_requested'] == 2
        for slot, status in [(queued, 'skipped'), (held, 'skipped'),
                             (running, 'running'), (late, 'late'), (failed, 'failed')]:
            assert (await db.get_slot(slot['id']))['status'] == status
        events = await db.events_since(0, workspace_id=ws['id'])
        event = next(e for e in events if e['type'] == 'execution.control' and e['payload']['action'] == 'cancel')
        assert event['turn_id'] == run['id']
        assert {tuple(t.values()) for t in event['payload']['targets']} == {
            (queued['id'], 0), (held['id'], 0), (running['id'], 1), (late['id'], 1)}
        assert await cancellations(db) == [
            {'slot_id': running['id'], 'attempt': 1}, {'slot_id': late['id'], 'attempt': 1}]
        assert await cancel_requested(db, await db.get_slot(running['id']))
        assert not await cancel_requested(db, await db.get_slot(failed['id']))
        await control(db, 'run', run['id'], 'resume')
        assert await cancel_requested(db, await db.get_slot(running['id']))
        assert (await state(db, 'run', run['id']))['cancel_requested'] == 2


async def test_cancellation_scopes_counts_and_new_attempts_do_not_inherit_intent(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        other = await db.create_workspace('other')
        run_a, run_b = await turn(db, ws['id']), await turn(db, ws['id'])
        run_c = await turn(db, other['id'])
        first = await db.dispatch_slot(run_a['slots'][0]['id'])
        second = await db.dispatch_slot(run_b['slots'][0]['id'])
        third = await db.dispatch_slot(run_c['slots'][0]['id'])
        await control(db, 'run', run_a['id'], 'cancel')
        assert not await cancel_requested(db, second)
        assert not await cancel_requested(db, third)
        assert (await state(db, 'project', ws['id']))['cancel_requested'] == 1
        await control(db, 'project', ws['id'], 'cancel')
        assert (await state(db, 'run', run_b['id']))['cancel_requested'] == 1
        assert (await state(db, 'project', ws['id']))['cancel_requested'] == 2
        assert (await state(db, 'project', other['id']))['cancel_requested'] == 0
        await db.fail_slot(first['id'], cause='stopped')
        await db.reroll_slot(first['id'], new_seed=9, committed_usd=0)
        new = await db.dispatch_slot(first['id'])
        assert new['attempt'] == 2
        assert not await cancel_requested(db, new)
        assert not await cancel_requested(db, first)
        assert await cancellations(db) == [{'slot_id': second['id'], 'attempt': 1}]
        assert (await state(db, 'run', run_a['id']))['cancel_requested'] == 0


async def test_revision_conflict_and_command_retry_do_not_repeat_intent(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        await turn(db, ws['id'])
        body = {'request_id': 'stable-request', 'revision': 0, 'action': 'pause',
                'actor': {'kind': 'agent', 'name': 'assistant'}, 'reason': 'review needed'}
        store = CreativeStore(db)
        async def deliver():
            return await store.command(body['request_id'], 'control', body,
                lambda: record(db, 'project', ws['id'], body))
        original = await deliver()
        events = await db.events_since(0)
        assert await deliver() == original
        with pytest.raises(Conflict, match='changed in another view'):
            await control(db, 'project', ws['id'], 'resume', revision=0)
        assert await db.events_since(0) == events
        assert (await state(db, 'project', ws['id']))['actor'] == body['actor']


async def test_command_failure_rolls_back_intent_and_slot_changes(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await turn(db, ws['id'])
        body = {'revision': 0, 'action': 'cancel', 'actor': {'kind': 'human', 'name': 'Ada'}}
        before = await db.events_since(0)
        async def fail_after_control():
            await record(db, 'project', ws['id'], body)
            raise RuntimeError('receipt failed')
        with pytest.raises(RuntimeError, match='receipt failed'):
            await CreativeStore(db).command('failed-request', 'control', body, fail_after_control)
        assert (await db.get_slot(run['slots'][0]['id']))['status'] == 'queued'
        assert (await state(db, 'project', ws['id']))['revision'] == 0
        assert await db.events_since(0) == before
        assert await db._one('SELECT 1 FROM command_receipts') is None


async def test_restart_preserves_pause_cancel_actor_and_exact_receipt(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await turn(db, ws['id'])
        slot = await db.dispatch_slot(run['slots'][0]['id'])
        await control(db, 'project', ws['id'], 'pause', actor={'kind': 'agent', 'name': 'reviewer'})
        prior = await control(db, 'project', ws['id'], 'cancel', reason='stop requested')
        await control(db, 'run', run['id'], 'resume')
        receipt = await db._one('SELECT response_json FROM command_receipts ORDER BY rowid DESC LIMIT 1')
    async with opened(tmp_path) as db:
        assert await state(db, 'project', ws['id']) == prior
        assert await cancel_requested(db, slot)
        assert await cancellations(db) == [{'slot_id': slot['id'], 'attempt': 1}]
        with pytest.raises(ExecutionPaused):
            await guard_dispatch(db, slot)
        row = await db._one('SELECT response_json FROM command_receipts ORDER BY rowid DESC LIMIT 1')
        assert json.loads(row['response_json']) == json.loads(receipt['response_json'])
