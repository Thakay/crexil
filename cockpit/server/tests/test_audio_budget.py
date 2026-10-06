"""Audio prices: an audio attempt reserves its exact published-tariff price, in
integer micro-dollars, per 1,000 characters, per second or per generation, at
list price, before anything is sent. An unknown price counts as over every cap,
and project limits enclose run limits (law 22)."""

import asyncio

import pytest

from app import crexil_audio
from app.crexil_budget import accounting, audio_reservation, UnknownPrice
from fakes import FakeAudioExecutor, Step
from test_api_v4 import settle_turn, store
from test_audio_contract import PREVIEW, operation, submit
from test_audio_contract import booted_audio as booted
from test_crexil import command, image_bytes, project
from test_crexil_execution import reviewed, settled
from test_crexil_hosted import discover, hosted, limits
from test_crexil_hosted import submit as submit_hosted

pytestmark = pytest.mark.anyio

SPEECH = {'unit': '1k_characters', 'micro_usd': 80_000, 'measure': 'prompt'}   # $0.08 per 1,000 characters
SOUND = {'unit': 'second', 'micro_usd': 2_000, 'measure': 'duration_seconds'}   # $0.12 per minute


async def priced(client, wid, op_id, prompt, *, controls=None, refs=(), count=1, limit=1.0):
    """A published-tariff preparation with its own run limit."""
    op = await operation(client, op_id)
    setup = {'action': op['actions'][0], 'prompt': prompt, 'references': [{'node_id': n} for n in refs],
             'operations': [{'operation_id': op_id, 'revision': op['revision'], 'count': count,
                             'controls': controls or {}}],
             'spending_policy': 'published_tariff', 'spending_limit_usd': limit}
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title=op['label'], setup=setup))
    assert response.status_code == 201, response.text
    return response.json()


# --- the arithmetic ---

def test_each_tariff_unit_reserves_its_exact_price_in_micro_dollars():
    assert audio_reservation(SPEECH, {'characters': 1}) == 80
    assert audio_reservation(SPEECH, {'characters': 37}) == 2_960
    assert audio_reservation(SPEECH, {'characters': 10_000}) == 800_000
    assert audio_reservation(SOUND, {'seconds': 1.5}) == 3_000
    assert audio_reservation(SOUND, {'seconds': 0.1}) == 200
    assert audio_reservation(SOUND, {'seconds': 30}) == 60_000
    assert audio_reservation({'unit': 'generation', 'micro_usd': 50_000}, {'generations': 2}) == 100_000
    assert audio_reservation({'unit': 'attempt', 'micro_usd': 12_345}, {'attempts': 1}) == 12_345
    assert audio_reservation({'unit': 'attempt', 'micro_usd': 0}, {'attempts': 1}) == 0
    assert audio_reservation({'unit': 'attempt', 'micro_usd': 0}, {}) == 0   # unmetered reserves nothing


def test_a_fraction_of_a_micro_dollar_rounds_up_never_down():
    assert audio_reservation({'unit': '1k_characters', 'micro_usd': 80_001}, {'characters': 3}) == 241   # 240.003
    assert audio_reservation({'unit': '1k_characters', 'micro_usd': 1}, {'characters': 1}) == 1           # 0.001
    assert audio_reservation(SOUND, {'seconds': 2.0000001}) == 4_001                                      # 4000.0002


def test_the_list_price_is_reserved_never_a_temporary_sale_price():
    on_sale = {**SPEECH, 'sale_micro_usd': 22_000, 'sale_until': '2026-10-12'}
    assert audio_reservation(on_sale, {'characters': 1000}) == 80_000
    with pytest.raises(UnknownPrice, match='no published price'):
        audio_reservation({'unit': '1k_characters', 'sale_micro_usd': 22_000}, {'characters': 1000})


def test_an_unknown_price_or_measure_refuses_rather_than_guesses():
    for price in (None, {}, {'unit': 'second'}, {'unit': 'second', 'micro_usd': 0.5},
                  {'unit': 'second', 'micro_usd': -1}, {'unit': 'second', 'micro_usd': True}):
        with pytest.raises(UnknownPrice, match='counts as over the spending limit'):
            audio_reservation(price, {'seconds': 2})
    with pytest.raises(UnknownPrice, match="quoted per 'minute'"):
        audio_reservation({'unit': 'minute', 'micro_usd': 120_000}, {'minutes': 1})
    for unit, measures in (('1k_characters', {}), ('1k_characters', {'seconds': 2}),
                           ('1k_characters', {'characters': 2.5}), ('1k_characters', {'characters': 0}),
                           ('1k_characters', {'characters': True}), ('second', {'seconds': None}),
                           ('second', {'seconds': -1}), ('second', {'seconds': float('nan')}),
                           ('second', {'seconds': float('inf')}), ('second', {'seconds': '2'}),
                           ('generation', {'generations': 0}), ('attempt', {'attempts': 1.0})):
        with pytest.raises(UnknownPrice, match='so its price is unknown'):
            audio_reservation({'unit': unit, 'micro_usd': 1_000}, measures)
    with pytest.raises(UnknownPrice, match='missing'):
        crexil_audio.measures({**SOUND}, 'Boom', {})


# --- review prices before anything is sent ---

async def test_review_prices_speech_by_its_text_design_by_its_preview_line_and_sound_by_its_duration(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        sound = await reviewed(client, await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Hammer on anvil',
                                                    controls={'duration_seconds': 1.5}, count=2))
        assert sound['ready'], sound['errors']
        assert sound['reserved_micro_usd'] == 6_000 and sound['maximum_usd'] is None   # a tariff, not a hard cap

        design = 'audio:voice-design:elevenlabs-voice-design'
        line = {'preview_text': PREVIEW}
        assert len(PREVIEW) == 129
        gruff = await reviewed(client, await priced(client, p['id'], design, 'A gruff dwarf blacksmith', controls=line))
        other = await reviewed(client, await priced(client, p['id'], design,
                                                    'A bright young apprentice with a quick, nervous laugh',
                                                    controls=line))
        longer = await reviewed(client, await priced(client, p['id'], design, 'A gruff dwarf blacksmith',
                                                     controls={'preview_text': PREVIEW + 'x'}))
        assert gruff['ready'], gruff['errors']
        assert gruff['reserved_micro_usd'] == other['reserved_micro_usd'] == 129 * 80   # the description is free
        assert longer['reserved_micro_usd'] == 130 * 80
        short = await reviewed(client, await priced(client, p['id'], design, 'A voice',
                                                    controls={'preview_text': 'too short'}))
        assert 'needs 100 to 1000 characters; it has 9' in ' '.join(short['errors'])

        _, takes = await submit(client, await priced(client, p['id'], design, 'A gruff dwarf blacksmith',
                                                     controls=line))
        speech = await reviewed(client, await priced(client, p['id'], 'audio:speak:elevenlabs-v4',
                                                     'Steel remembers every strike.', refs=[takes[0]['id']]))
        assert speech['ready'], speech['errors']
        assert speech['reserved_micro_usd'] == len('Steel remembers every strike.') * 80 == 2_320
        assert all(r.external['verb'] == 'voice-design' for r in fakes['elevenlabs'].requests)


async def test_an_engine_without_a_published_price_refuses_at_review_and_nothing_is_sent(tmp_path, monkeypatch):
    unpriced = {'id': 'test-sfx-unpriced', 'name': 'Unpriced sound', 'payload': 'audio',
                'provider': 'elevenlabs', 'lane': 'external', 'verbs': ['sfx'],
                'models': {'sfx': 'eleven_text_to_sound_v2'}}
    async with booted(tmp_path, monkeypatch, rows=[unpriced]) as (client, fakes):
        p = await project(client)
        direction = await priced(client, p['id'], 'audio:sfx:test-sfx-unpriced', 'Hammer on anvil')
        check = await reviewed(client, direction)
        assert not check['ready'] and check['reserved_micro_usd'] == 0
        assert ('Unpriced sound: This engine has no published price. '
                'An unknown price counts as over the spending limit.') in ' '.join(check['errors'])
        refused = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                    json=command(revision=check['revision'], review_token=check['token']))
        assert refused.status_code != 201
        assert fakes['elevenlabs'].requests == []


# --- limits enclose runs, and charges settle reservations ---

async def test_image_and_audio_spending_share_one_project_limit_and_a_run_keeps_its_own(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover()
        fakes['openrouter'].default = Step(data=image_bytes(), cost=.04)
        p = await limits(client, await project(client), spending_limit_usd=.045, spending_policy='published_tariff')
        await submit_hosted(client, await hosted(client, p['id']))
        await settled(p['id'])
        assert (await accounting(store(), p['id']))['charged_usd'] == .04

        fits = await reviewed(client, await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Hammer on anvil',
                                                   controls={'duration_seconds': 2.5}))
        assert fits['ready'], fits['errors']                    # $0.04 charged + $0.005 reserved = the limit
        over = await reviewed(client, await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Hammer on anvil',
                                                   controls={'duration_seconds': 2.6}))
        assert not over['ready']
        assert 'Project spending limit is $0.045; charged and reserved spending totals $0.0452.' in over['errors']
        run = await reviewed(client, await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Hammer on anvil',
                                                  controls={'duration_seconds': 1.5}, limit=.002))
        assert not run['ready']
        assert 'The reserved $0.003 exceeds the $0.002 spending limit.' in run['errors']
        assert fakes['elevenlabs'].requests == []


@pytest.mark.parametrize('cost, charged, reserved', [(.003, .003, 0), (None, 0, .003)])
async def test_a_reported_charge_settles_an_audio_reservation_and_an_unreported_one_keeps_counting(
        tmp_path, monkeypatch, cost, charged, reserved):
    audio = FakeAudioExecutor('elevenlabs', 'external', cost=cost)
    async with booted(tmp_path, monkeypatch, executors={'elevenlabs': audio}) as (client, _):
        p = await limits(client, await project(client), spending_limit_usd=1, spending_policy='published_tariff')
        turn, nodes = await submit(client, await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Anvil',
                                                        controls={'duration_seconds': 1.5}))
        assert turn['slots'][0]['status'] == 'landed' and nodes[0]['payload'] == 'audio'
        counts = await accounting(store(), p['id'])
        # A take with no reported charge stays reserved at list price: never zero (law 22).
        assert (counts['charged_usd'], counts['reserved_usd']) == (charged, reserved)
        assert counts['unresolved_attempts'] == (0 if cost else 1)


async def test_a_lowered_project_limit_holds_a_queued_audio_attempt_before_it_is_sent(tmp_path, monkeypatch):
    gate = asyncio.Event()
    audio = FakeAudioExecutor('elevenlabs', 'external', gate=gate, max_concurrency=1)
    async with booted(tmp_path, monkeypatch, executors={'elevenlabs': audio}) as (client, _):
        p = await limits(client, await project(client), spending_limit_usd=.006, spending_policy='published_tariff')
        direction = await priced(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Anvil',
                                 controls={'duration_seconds': 1.5}, count=2)
        check = await reviewed(client, direction)
        assert check['ready'] and check['reserved_micro_usd'] == 6_000, check['errors']
        accepted = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                     json=command(revision=check['revision'], review_token=check['token']))
        assert accepted.status_code == 201, accepted.text
        for _ in range(200):
            if audio.requests:
                break
            await asyncio.sleep(.01)
        assert len(audio.requests) == 1
        await limits(client, p, spending_limit_usd=.003, spending_policy='published_tariff')
        gate.set()
        turn = await settle_turn(accepted.json()['turn_id'], until={'landed', 'held'})
        assert sorted(s['status'] for s in turn['slots']) == ['held', 'landed']
        held = next(s for s in turn['slots'] if s['status'] == 'held')
        assert held['cause'] == 'Project spending limit is $0.003; charged and reserved spending totals $0.006.'
        assert len(audio.requests) == 1                          # the second attempt was never sent
