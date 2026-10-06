"""Audio prices: until the tariff arithmetic lands, a paid audio attempt refuses
rather than guessing (law 22). The prices builder replaces these with the real
reservations: per 1,000 characters, per second and per generation, in exact
micro-dollars."""

import pytest

from app import crexil_audio
from test_audio_contract import booted_audio, draft
from test_crexil import project
from test_crexil_execution import reviewed

pytestmark = pytest.mark.anyio


async def test_paid_audio_refuses_until_its_price_is_computed(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        check = await reviewed(client, await draft(client, p['id'], 'audio:sfx:elevenlabs-sfx', 'Hammer on anvil',
                                                   controls={'duration_seconds': 1.5}, policy='published_tariff'))
        assert not check['ready'] and check['reserved_micro_usd'] == 0
        assert 'An unknown price counts as over the spending limit.' in ' '.join(check['errors'])
        short = await reviewed(client, await draft(client, p['id'], 'audio:voice-design:elevenlabs-voice-design',
                                                   'A voice', controls={'preview_text': 'too short'}, policy='published_tariff'))
        assert 'needs 100 to 1000 characters; it has 9' in ' '.join(short['errors'])
        assert fakes['elevenlabs'].requests == []


def test_the_reservation_refuses_rather_than_guesses():
    from app import crexil_budget
    assert crexil_budget.audio_reservation({'unit': 'attempt', 'micro_usd': 0}, {'attempts': 1}) == 0
    for price in (None, {'unit': 'second', 'micro_usd': 2000}, {'unit': 'second', 'micro_usd': 0.5}):
        with pytest.raises(crexil_budget.UnknownPrice, match='counts as over the spending limit'):
            crexil_budget.audio_reservation(price, {'seconds': 2})
    with pytest.raises(crexil_budget.UnknownPrice, match='missing'):
        crexil_audio.measures({'unit': 'second', 'micro_usd': 2000, 'measure': 'duration_seconds'}, 'Boom', {})
