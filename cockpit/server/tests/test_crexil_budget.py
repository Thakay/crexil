"""Exact run membership shares project accounting without changing charge truth,
and a limit's refusal states its money as every line does."""

from decimal import Decimal

import pytest

from app.crexil_budget import accounting, money, violations
from test_db import opened

pytestmark = pytest.mark.anyio


async def make_run(db, wid, *amounts):
    return await db.create_turn_with_slots(wid, 'request', {}, [
        {'engine': 'provider/model', 'external': {'model': 'provider/model'},
         'committed_usd': amount} for amount in amounts])


async def test_multiple_runs_deduplicate_and_exclude_other_runs_and_projects(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('selected project')
        other = await db.create_workspace('other project')
        a = await make_run(db, ws['id'], .04, .06)
        b = await make_run(db, ws['id'], .1)
        await make_run(db, ws['id'], .2)
        foreign = await make_run(db, other['id'], .3)
        await db.dispatch_slot(a['slots'][0]['id'])
        await db.fail_slot(a['slots'][0]['id'], cause='charged failure', charged_usd=.03)
        await db.transition_slot(a['slots'][1]['id'], 'held', payload={'pause': True})
        await db.dispatch_slot(b['slots'][0]['id'])
        await db.transition_slot(b['slots'][0]['id'], 'late')
        await db.dispatch_slot(foreign['slots'][0]['id'])
        await db.fail_slot(foreign['slots'][0]['id'], cause='other project charge', charged_usd=.25)

        selected = await accounting(db, ws['id'], turn_ids=[a['id'], b['id']])
        assert selected == {'attempts': 3, 'charged_usd': .03, 'reserved_usd': .16,
                            'unresolved_attempts': 2, 'unbounded_attempts': 0, 'tariff_attempts': 2}
        assert await accounting(db, ws['id'], turn_ids=[b['id'], a['id'], a['id'], b['id']]) == selected
        assert await accounting(db, ws['id'], turn_ids=[a['id'], foreign['id'], b['id'], 'missing']) == selected
        assert (await accounting(db, ws['id']))['reserved_usd'] == .36
        assert (await accounting(db, other['id']))['charged_usd'] == .25
        for run in (a, b):
            assert await accounting(db, ws['id'], turn_ids=[run['id']]) == await accounting(db, ws['id'], turn_id=run['id'])


async def test_empty_or_foreign_selection_has_no_project_charges_or_reservations(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        other = await db.create_workspace('other')
        await make_run(db, ws['id'], .1)
        foreign = await make_run(db, other['id'], .2)
        await db.append_event('project.charge', workspace_id=ws['id'], charged_usd=.05)
        zero = {'attempts': 0, 'charged_usd': 0, 'reserved_usd': 0,
                'unresolved_attempts': 0, 'unbounded_attempts': 0, 'tariff_attempts': 0}
        for selected in ([], ['missing'], [foreign['id']]):
            assert await accounting(db, ws['id'], turn_ids=selected) == zero
        assert (await accounting(db, ws['id']))['charged_usd'] == .05
        assert (await accounting(db, ws['id']))['reserved_usd'] == .1


async def test_both_accounting_scopes_are_rejected_even_for_empty_selection(tmp_path):
    async with opened(tmp_path) as db:
        for selected in ([], ['another-run']):
            with pytest.raises(ValueError, match='both accounting scopes'):
                await accounting(db, 'project', turn_id='run', turn_ids=selected)


async def test_multiple_runs_preserve_old_attempt_reservations_and_late_charge_corrections(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        a = await make_run(db, ws['id'], .04)
        b = await make_run(db, ws['id'], .1)
        sid = a['slots'][0]['id']
        await db.dispatch_slot(sid)
        await db.fail_slot(sid, cause='first request unresolved', kind='timeout')
        await db.reroll_slot(sid, new_seed=2, committed_usd=.06)
        second = await db.dispatch_slot(sid)
        await db.report_provider_charge(second, .05, 'second-job')
        await db.fail_slot(sid, cause='second response has no image', charged_usd=.05)
        await db.dispatch_slot(b['slots'][0]['id'])
        await db.transition_slot(b['slots'][0]['id'], 'late')
        selected = [a['id'], b['id'], a['id']]
        before = await accounting(db, ws['id'], turn_ids=selected)
        assert before == {'attempts': 3, 'charged_usd': .05, 'reserved_usd': .14,
                          'unresolved_attempts': 2, 'unbounded_attempts': 0, 'tariff_attempts': 2}
        # An older verification lacking attempt identity cannot settle the
        # outstanding first attempt after that slot has already been reused.
        await db.append_event('slot.charge_verified', workspace_id=ws['id'], turn_id=a['id'], slot_id=sid,
                              payload={'charged_usd': 0})
        assert await accounting(db, ws['id'], turn_ids=selected) == before
        await db.record_charge(sid, .02, attempt=1, note='first request later verified')
        corrected = await accounting(db, ws['id'], turn_ids=selected)
        assert corrected['charged_usd'] == .07 and corrected['reserved_usd'] == .1
        assert corrected['attempts'] == 3 and corrected['unresolved_attempts'] == 1
        await db.record_charge(b['slots'][0]['id'], 0, attempt=1, note='provider confirmed no charge')
        settled = await accounting(db, ws['id'], turn_ids=selected)
        assert settled['charged_usd'] == .07 and settled['reserved_usd'] == 0
        assert settled['attempts'] == 3 and settled['unresolved_attempts'] == 0


async def test_unknown_commitments_stay_unbounded_across_selected_runs(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        a = await make_run(db, ws['id'], None)
        b = await make_run(db, ws['id'], None)
        await db.dispatch_slot(a['slots'][0]['id'])
        await db.transition_slot(a['slots'][0]['id'], 'late')
        result = await accounting(db, ws['id'], turn_ids=[a['id'], b['id']])
        assert result == {'attempts': 2, 'charged_usd': 0, 'reserved_usd': 0,
                          'unresolved_attempts': 0, 'unbounded_attempts': 2, 'tariff_attempts': 2}


async def retried(db, sid, kind='rate_limit'):
    """The scheduler's visible retry: the refused try settles at its known zero
    charge and the slot queues again for its next attempt."""
    slot = await db.get_slot(sid)
    await db.append_event('slot.retrying', **await db._slot_ids(slot), charged_usd=0,
                          payload={'kind': kind, 'attempt': slot['attempt'], 'next_attempt': slot['attempt'] + 1})
    await db.transition_slot(sid, 'queued', payload={'retry_of': kind}, retry_of=kind)


async def test_an_automatic_retry_is_the_same_requested_attempt_as_the_try_it_repeats(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('project')
        run = await make_run(db, ws['id'], .04, .04)
        first, second = (slot['id'] for slot in run['slots'])
        await db.dispatch_slot(first)
        await retried(db, first)
        waiting = await accounting(db, ws['id'])
        # Two requested attempts, one of them waiting to be sent again; the refused try reserves nothing.
        assert (waiting['attempts'], waiting['reserved_usd'], waiting['unresolved_attempts']) == (2, .08, 2)
        assert await accounting(db, ws['id'], turn_id=run['id']) == waiting
        assert await accounting(db, ws['id'], turn_ids=[run['id']]) == waiting
        await db.dispatch_slot(first)                     # the retry goes out as attempt 2
        await retried(db, first, 'unreachable')           # and is refused once more
        await db.dispatch_slot(first)
        assert (await db.get_slot(first))['attempt'] == 3
        assert (await accounting(db, ws['id']))['attempts'] == 2

        # A retry cancelled before it is sent leaves its one sent try counted.
        await db.dispatch_slot(second)
        await retried(db, second)
        await db.transition_slot(second, 'skipped', payload={'cancel': True})
        assert (await accounting(db, ws['id']))['attempts'] == 2
        # Running the slot again is the artist's new request.
        await db.reroll_slot(second, new_seed=7, committed_usd=.04)
        assert (await accounting(db, ws['id']))['attempts'] == 3
        await db.dispatch_slot(second)
        assert (await accounting(db, ws['id']))['attempts'] == 3


def test_money_reads_cents_or_every_digit_it_has_never_rounded():
    assert [money(v) for v in (0, 0.6, 1, 1.5, 12.3)] == ['$0.00', '$0.60', '$1.00', '$1.50', '$12.30']
    assert [money(v) for v in (0.018, 0.0336, 0.00408, Decimal('0.86508'))] == \
        ['$0.018', '$0.0336', '$0.00408', '$0.86508']


def test_a_limit_refusal_states_its_amounts_with_two_decimals_or_more():
    # The project cap a live run refused over: $0.60 against $0.36 charged and $0.50508 asked.
    spent = {'attempts': 0, 'charged_usd': 0.36, 'reserved_usd': 0.0, 'unresolved_attempts': 0,
             'unbounded_attempts': 0, 'tariff_attempts': 0}
    limits = {'spending_limit_usd': 0.6, 'spending_policy': 'published_tariff'}
    assert violations(limits, spent, reserved=0.50508, tariff=True) == [
        'Project spending limit is $0.60; charged and reserved spending totals $0.86508.']
    assert violations({'spending_limit_usd': 1, 'spending_policy': 'published_tariff'}, spent,
                      reserved=0.74, tariff=True, label='Run') == [
        'Run spending limit is $1.00; charged and reserved spending totals $1.10.']
