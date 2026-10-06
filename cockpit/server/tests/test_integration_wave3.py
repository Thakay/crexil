"""Seams between wave 3 changes: review asks the hosted catalogue for fresh facts
about exactly the operations it compiles, so a long-idle catalogue never refuses
a hosted engine only for its age (law 17)."""

import pytest

from app import wiring
from test_api_v4 import booted
from test_crexil import project
from test_crexil_execution import reviewed
from test_crexil_hosted import discover, hosted

pytestmark = pytest.mark.anyio


async def test_review_refreshes_the_catalogue_for_the_operations_it_compiles(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _fakes):
        discover()
        p = await project(client)
        direction = await hosted(client, p['id'])
        asked = []

        async def ensure_fresh(operation_ids=None):
            asked.append(list(operation_ids or []))

        monkeypatch.setattr(wiring.current.provider_catalog, 'ensure_fresh', ensure_fresh)
        await reviewed(client, direction)
        assert asked == [['testvendor/testmodel']]


def test_an_unreadable_price_is_described_never_raised():
    from app.crexil_audio import cost_basis
    unknown = 'An unknown price counts as over the spending limit.'
    for price in ({'unit': 'minute', 'micro_usd': 100}, {'unit': 'second'},
                  {'unit': 'second', 'micro_usd': 1.5}, {'unit': 'second', 'micro_usd': True}):
        assert cost_basis(price).endswith(unknown), price
    assert cost_basis({'unit': 'second', 'micro_usd': 2000}) == 'Published tariff, 0.002 US dollars per second'
    assert cost_basis({'unit': 'minute', 'micro_usd': 0}) == 'Unmetered local operation'


async def test_one_misconfigured_audio_row_never_hides_the_other_operations(tmp_path, monkeypatch):
    from app import audio
    async with booted(tmp_path, monkeypatch) as (client, _fakes):
        rows = audio.engines()
        assert rows, 'the shipped matrix has audio rows'
        broken = {**next(iter(rows.values()))}
        broken['tariffs'] = {verb: {'unit': 'fortnight', 'micro_usd': 5} for verb in broken.get('verbs', [])}
        monkeypatch.setattr(audio, 'engines', lambda: {**rows, broken['id']: broken})
        response = await client.get('/api/crexil/operations')
        assert response.status_code == 200, response.text
        listed = [o for o in response.json() if o['id'].endswith(f":{broken['id']}")]
        assert listed and all(o['cost_basis'].startswith('No published price') for o in listed)
