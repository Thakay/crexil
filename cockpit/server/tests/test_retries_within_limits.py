"""A visible automatic retry is the same requested attempt as the try it
repeats (laws 9 and 22): it never counts as another attempt against a run's,
a project's or a sequence's attempt limit, so a run whose limit equals its
attempt count still sends every attempt after one refused try.

The voice director sets a run's `attempt_limit` to its attempt count, and the
prepared-operations door sets 1 for one attempt; the drawer defaults to 16,
the most a run holds. Each journey runs the real HTTP API, scheduler and
ElevenLabs adapter against the synthetic ElevenLabs, or the scripted voice
runner. No network, no GPU, no key.
"""

import pytest

from app import config
from app.crexil_budget import accounting
from app.executors import EngineFailure
from fakes import FakeAudioExecutor
from test_api_v4 import settle_turn, store
from test_audio_contract import booted_audio
from test_audio_guardrails import DESCRIPTION, ELEVEN_SFX, LOCAL_DESIGN, SOUND, SPOKEN, boot
from test_crexil import command, project
from test_crexil_execution import reviewed
from test_crexil_hosted import limits
from test_elevenlabs import FakeElevenLabs, refusal, sounded

pytestmark = pytest.mark.anyio
SLOW_DOWN = refusal(429, 'rate_limit_exceeded', headers={'retry-after-ms': '10'})


async def prepared(client, wid, *, count, attempt_limit, action='sfx', operation=ELEVEN_SFX, controls=SOUND,
                   prompt='Hammer on an anvil', spending=None):
    """A run's preparation and its review, with the attempt limit set as the
    voice director and the prepared-operations door set it."""
    ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
    setup = {'action': action, 'prompt': prompt, 'references': [],
             'operations': [{'operation_id': operation, 'revision': ops[operation]['revision'],
                             'count': count, 'controls': controls}],
             'attempt_limit': attempt_limit,
             **(spending or {'spending_policy': 'published_tariff', 'spending_limit_usd': 1.0})}
    created = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title='Run', setup=setup))
    assert created.status_code == 201, created.text
    direction = created.json()
    return direction, await reviewed(client, direction)


async def submitted(client, wid, **setup):
    direction, check = await prepared(client, wid, **setup)
    assert check['ready'], check['errors']
    sent = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                             json=command(revision=check['revision'], review_token=check['token']))
    assert sent.status_code == 201, sent.text
    return sent.json()['turn_id']


@pytest.mark.parametrize('count', [1, 2, 16], ids=['one of one', 'two of two', 'sixteen of sixteen'])
async def test_a_retry_never_holds_a_run_whose_attempt_limit_equals_its_attempt_count(tmp_path, monkeypatch, count):
    # ElevenLabs asks the first attempt to slow down once; every later call lands.
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', SLOW_DOWN, sounded())
    async with boot(tmp_path, monkeypatch, fake, in_flight=1) as (client, _):
        p = await project(client)
        turn_id = await submitted(client, p['id'], count=count, attempt_limit=count)
        turn = await settle_turn(turn_id, until={'landed', 'held'})
        assert [s['status'] for s in turn['slots']] == ['landed'] * count
        retried = turn['slots'][0]
        assert (retried['retry_of'], retried['attempt']) == ('rate_limit', 2)
        assert not [e for e in await store().slot_events(retried['id']) if e['type'] == 'slot.held']
        # The refused try and its retry, then every other attempt once (law 9).
        assert len(fake.calls('POST', '/v1/sound-generation')) == count + 1
        for scope in ({}, {'turn_id': turn_id}):
            counts = await accounting(store(), p['id'], **scope)
            assert (counts['attempts'], counts['reserved_usd']) == (count, 0)


async def test_a_run_under_a_roomier_limit_retries_and_lands_as_before(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', SLOW_DOWN, sounded())
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn = await settle_turn(await submitted(client, p['id'], count=1, attempt_limit=16), until={'landed'})
        (slot,) = turn['slots']
        assert (slot['status'], slot['attempt'], slot['retry_of']) == ('landed', 2, 'rate_limit')


async def test_a_project_attempt_limit_counts_a_retried_attempt_once(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', SLOW_DOWN, sounded())
    async with boot(tmp_path, monkeypatch, fake, in_flight=1) as (client, _):
        p = await limits(client, await project(client), attempt_limit=2, spending_limit_usd=1.0,
                         spending_policy='published_tariff')
        turn_id = await submitted(client, p['id'], count=2, attempt_limit=16)
        turn = await settle_turn(turn_id, until={'landed', 'held'})
        assert [s['status'] for s in turn['slots']] == ['landed', 'landed']
        assert (await accounting(store(), p['id']))['attempts'] == 2
        # The limit still refuses a third attempt: the project's two are used.
        _, check = await prepared(client, p['id'], count=1, attempt_limit=16)
        assert not check['ready']
        assert check['errors'] == ['Project attempt limit is 2; used and reserved attempts total 3.']


class DoorBlip(FakeAudioExecutor):
    """The local voice runner refusing its first connection at the door, then
    answering, as after a runner restart between two runs."""

    def __init__(self):
        super().__init__('voice-runner', 'local')
        self.blips = 1

    async def wait(self, h):
        if self.blips:
            self.blips -= 1
            raise EngineFailure('unreachable', 'The local voice runner is not answering at the door.')
        return await super().wait(h)


async def test_a_free_local_run_the_voice_director_starts_survives_one_retry_at_the_runners_door(
        tmp_path, monkeypatch):
    runner = DoorBlip()
    async with booted_audio(tmp_path, monkeypatch, executors={'voice-runner': runner}) as (client, _):
        monkeypatch.setattr(config, 'RETRY_BACKOFF_S', 0.01)
        p = await project(client)
        turn_id = await submitted(client, p['id'], count=2, attempt_limit=2, action='voice-design',
                                  operation=LOCAL_DESIGN, controls=SPOKEN, prompt=DESCRIPTION,
                                  spending={'spending_policy': 'hard_cap', 'spending_limit_usd': 0})
        turn = await settle_turn(turn_id, until={'landed', 'held'})
        assert [s['status'] for s in turn['slots']] == ['landed', 'landed']
        assert len(runner.requests) == 3                 # two attempts and one visible retry
        assert (await accounting(store(), p['id'], turn_id=turn_id))['attempts'] == 2
