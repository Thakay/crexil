"""Blind runs and the decision record: the behavior behind the blind contract.
Until the blind-runs builder lands it, reveal and the export answer 501 in words."""

import pytest

from test_audio_contract import booted_audio
from test_crexil import command, project

pytestmark = pytest.mark.anyio


async def test_unbuilt_blind_and_decision_features_answer_501_in_words(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        revealed = await client.post('/api/crexil/turns/any/reveal', json=command(reason='Done listening'))
        assert revealed.status_code == 501 and revealed.json()['detail'].startswith('Revealing a blind run is not built yet.')
        for route in (f"/api/crexil/projects/{p['id']}/preferences", f"/api/crexil/projects/{p['id']}/preferences.jsonl"):
            answer = await client.get(route)
            assert answer.status_code == 501 and 'not built yet' in answer.json()['detail']
