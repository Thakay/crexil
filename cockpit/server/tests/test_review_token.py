"""The review token binds the request, the spending limits and recorded spending
in separate parts, so a submit names what moved since review (law 22, law 3)."""

import asyncio

import pytest

from app.crexil_budget import accounting
from app.crexil_execution import TOKEN_PARTS, token_change
from fakes import Step
from test_api_v4 import booted, store
from test_crexil import command, image_bytes, project
from test_crexil_execution import reviewed, settled
from test_crexil_hosted import discover, hosted, limits

pytestmark = pytest.mark.anyio

AGENT = {'kind': 'agent', 'name': 'Guide'}


# Other work stays in flight, holding its reservation without benching the engine;
# a failure would bench it, and that is an operations change, not a spending one.
IN_FLIGHT = Step(outcome='hang')


async def priced_project(client, ceiling):
    discover()
    return await limits(client, await project(client), spending_limit_usd=ceiling,
                        spending_policy='published_tariff')


async def submit(client, direction, review, **fields):
    return await client.post(f"/api/crexil/directions/{direction['id']}/submit", json={
        **command(revision=direction['revision'], review_token=review['token']), **fields})


async def edit_project(client, wid, **changes):
    current = (await client.get(f'/api/crexil/projects/{wid}')).json()['project']
    body = command(revision=current['revision'], name=current['name'], brief=current['brief'],
                   instructions=current['instructions'], limits=current['limits'])
    response = await client.patch(f'/api/crexil/projects/{wid}', json={**body, **changes})
    assert response.status_code == 200, response.text
    return response.json()


async def test_token_change_names_the_first_part_that_moved():
    token = 'a' * 64
    assert sum(width for _, width in TOKEN_PARTS) == len(token)
    assert token_change(token, token) is None
    assert token_change(token, 'b' + token[1:]) == 'request'
    assert token_change(token, token[:32] + 'b' + token[33:]) == 'limits'
    assert token_change(token, token[:48] + 'b' + token[49:]) == 'spending'


async def test_spending_recorded_by_other_work_does_not_refuse_a_run_that_still_fits(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = Step(cost=.04, data=image_bytes())
        p = await priced_project(client, 1)
        voice, sound = await hosted(client, p['id']), await hosted(client, p['id'])
        voice_review, sound_review = await reviewed(client, voice), await reviewed(client, sound)
        assert (await submit(client, sound, sound_review)).status_code == 201
        await settled(p['id'])
        assert (await accounting(store(), p['id']))['charged_usd'] == .04
        moved = await reviewed(client, voice)
        assert token_change(voice_review['token'], moved['token']) == 'spending'
        accepted = await submit(client, voice, voice_review)
        assert accepted.status_code == 201, accepted.text
        counts = await accounting(store(), p['id'], turn_id=accepted.json()['turn_id'])
        assert counts['attempts'] == 1 and counts['reserved_usd'] == voice_review['reserved_usd']


async def test_spending_that_no_longer_fits_refuses_naming_spending(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, .04)
        first, second = await hosted(client, p['id']), await hosted(client, p['id'])
        first_review, second_review = await reviewed(client, first), await reviewed(client, second)
        assert (await submit(client, first, first_review)).status_code == 201
        refused = await submit(client, second, second_review)
        assert refused.status_code == 409
        assert refused.headers['x-crexil-conflict'] == 'spending'
        message = refused.json()['detail']
        assert message.startswith("Other work changed this project's spending since review")
        assert 'Project spending limit is $0.04' in message and message.endswith('Review again to see the current totals.')
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        assert next(d for d in snapshot['directions'] if d['id'] == second['id'])['state'] == 'draft'
        again = await reviewed(client, second)
        assert not again['ready'] and any('spending limit' in error for error in again['errors'])
        assert (await accounting(store(), p['id']))['attempts'] == 1


async def test_request_change_refuses_as_before_without_a_spending_kind(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, 1)
        direction = await hosted(client, p['id'])
        review = await reviewed(client, direction)
        await edit_project(client, p['id'], brief='A brand new brief')
        refused = await submit(client, direction, review)
        assert refused.status_code == 409
        assert refused.json()['detail'].startswith('Preparation, sources or operations changed.')
        assert 'x-crexil-conflict' not in refused.headers


async def test_limit_change_refuses_naming_the_limits(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, 1)
        direction = await hosted(client, p['id'])
        review = await reviewed(client, direction)
        await edit_project(client, p['id'], limits={'spending_limit_usd': 2, 'spending_policy': 'published_tariff'})
        refused = await submit(client, direction, review)
        assert refused.status_code == 409
        assert refused.headers['x-crexil-conflict'] == 'spending'
        assert refused.json()['detail'] == 'Spending limits changed since review. Review again to see the current limits.'


async def test_renaming_the_project_keeps_a_review_valid(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, 1)
        direction = await hosted(client, p['id'])
        review = await reviewed(client, direction)
        await edit_project(client, p['id'], name='Brass handle, second pass')
        accepted = await submit(client, direction, review)
        assert accepted.status_code == 201, accepted.text


async def test_concurrent_acceptance_serializes_and_names_spending(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, .04)
        a, b = await hosted(client, p['id']), await hosted(client, p['id'])
        ra, rb = await reviewed(client, a), await reviewed(client, b)
        results = await asyncio.gather(submit(client, a, ra), submit(client, b, rb))
        assert sorted(r.status_code for r in results) == [201, 409]
        refused = next(r for r in results if r.status_code == 409)
        assert refused.headers['x-crexil-conflict'] == 'spending'
        assert len(fakes['openrouter'].starts) <= 1
        assert (await accounting(store(), p['id']))['attempts'] == 1


async def test_agent_submits_its_reviewed_amount_after_unrelated_spending(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        p = await priced_project(client, .08)
        mine, other = await hosted(client, p['id']), await hosted(client, p['id'])
        mine_review, other_review = await reviewed(client, mine), await reviewed(client, other)
        assert (await submit(client, other, other_review)).status_code == 201
        accepted = await submit(client, mine, mine_review, actor=AGENT)
        assert accepted.status_code == 201, accepted.text
        turn = await store().get_turn(accepted.json()['turn_id'])
        frozen = turn['dials']['crexil']
        assert frozen['actor'] == AGENT and frozen['review_token'] == mine_review['token']
        assert frozen['maximum_usd'] == mine_review['maximum_usd']
        counts = await accounting(store(), p['id'], turn_id=turn['id'])
        assert counts['reserved_usd'] == mine_review['reserved_usd']
        third = await hosted(client, p['id'])
        late = await submit(client, third, await reviewed(client, third), actor=AGENT)
        assert late.status_code == 422 and 'spending limit' in late.json()['detail']
