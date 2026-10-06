"""Authored moves on one attempt, through the HTTP API and the real scheduler with
fake executors: resuming the engine that holds an attempt (A9's TRY ONCE MORE, a
runtime's bench once its cause is gone) and cancelling one attempt (failure
cases 2.7, C6 and C7). Each move records its participant (law 23), refuses in
plain words with the next move (laws 3 and 4), keeps a paid attempt's charge
facts (law 5), lets no agent send held paid work (law 22) and names no engine of
a blind run. The review says which operations retry on their own (law 9). No
network, no GPU.
"""

import asyncio

import pytest

from app import config, wiring
from app.execution_controls import PAID_RESUME, remaining_cause
from app.executors import EngineFailure, GpuReading
from app.scheduler import BenchEntry, Scheduler
from fakes import FakeAudioExecutor, Step
from test_api_v4 import booted, settle_turn, store
from test_audio_contract import PREVIEW, booted_audio, free_design_row
from test_audio_contract import draft as audio_draft
from test_audio_contract import submit as audio_submit
from test_audio_guardrails import ELEVEN_DESIGN, SPOKEN, accepted, prepare
from test_blind_everywhere import HOSTED, LOCAL, names_an_engine, prepared, ran, submitted
from test_crexil import command, image_bytes, project
from test_crexil_controls import control
from test_crexil_execution import attest, draft, reviewed
from test_crexil_hosted import discover, hosted
from test_crexil_hosted import submit as submit_hosted
from test_local_voice import served

pytestmark = pytest.mark.anyio

ARTIST = {'kind': 'human', 'name': 'Artist'}
AGENT = {'kind': 'agent', 'name': 'Scout'}
FAULT = EngineFailure('error', 'scripted engine fault')
SKIPPED = 'Skipped: nothing more is sent for this attempt, and skipping it costs nothing.'


async def move(client, verb, slot, *, actor=None, reason='', attempt=None):
    """One participant's move on one attempt, as the face would send it."""
    body = command(attempt=slot['attempt'] if attempt is None else attempt, reason=reason)
    if actor:
        body['actor'] = actor
    return await client.post(f"/api/crexil/attempts/{slot['id']}/{verb}", json=body)


async def slots(turn_id):
    return (await store().get_turn(turn_id))['slots']


async def statuses(turn_id, expected, ticks=600, *, ordered=True):
    """The run's slots once their states read `expected`, by position, or in
    any order when the lane may take them in either."""
    found = None
    for _ in range(ticks):
        found = await slots(turn_id)
        states = [slot['status'] for slot in found]
        if (states if ordered else sorted(states)) == (expected if ordered else sorted(expected)):
            return found
        await asyncio.sleep(0.01)
    raise AssertionError([(slot['status'], slot['cause']) for slot in found])


async def events(client, kind=None):
    rows = (await client.get('/api/events', params={'limit': 500})).json()['events']
    return [row for row in rows if kind is None or row['type'] == kind]


async def local_run(client, wid, attempts):
    """One local image run of `attempts` attempts, submitted: its run id."""
    ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
    direction = await draft(client, wid, operations=[
        {'operation_id': 'testpreset', 'revision': ops['testpreset']['revision'], 'count': attempts}])
    check = await reviewed(client, direction)
    assert check['ready'], check['errors']
    answer = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                               json=command(revision=direction['revision'], review_token=check['token']))
    assert answer.status_code == 201, answer.text
    return answer.json()['turn_id']


# --- resume the engine that holds an attempt ---

async def test_try_once_more_sends_an_engine_held_for_one_run_back_to_the_queue(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(outcome=FAULT)
        p = await project(client)
        turn_id = await local_run(client, p['id'], 4)
        _, _, held, waiting = await statuses(turn_id, ['failed', 'failed', 'held', 'held'])
        scope = f'engine:testpreset@turn:{turn_id}'                      # A9: this engine, this run only
        queue = (await client.get('/api/queue')).json()
        assert {row['id']: row['bench'] for row in queue['held']} == {held['id']: scope, waiting['id']: scope}

        fakes['comfy'].default = Step(data=image_bytes())
        answer = await move(client, 'resume', held, reason='The fault is fixed')
        assert answer.status_code == 200, answer.text
        assert answer.json() == {
            'slot_id': held['id'], 'attempt': 0, 'action': 'resume', 'released': 2, 'actor': ARTIST,
            'reason': 'The fault is fixed',
            'words': 'Trying once more: 2 held attempts went back to the queue, and each meets the spending '
                     'limits again before it is sent.'}
        await statuses(turn_id, ['failed', 'failed', 'landed', 'landed'])

        # Law 23: the move and the lift it caused both name their participant.
        (record,) = await events(client, 'execution.control')
        assert record['slot_id'] == held['id'] and record['turn_id'] == turn_id
        assert {key: record['payload'][key] for key in ('scope', 'action', 'actor', 'reason', 'bench', 'targets')} == {
            'scope': 'slot', 'action': 'resume', 'actor': ARTIST, 'reason': 'The fault is fixed', 'bench': scope,
            'targets': [{'slot_id': held['id'], 'attempt': 0}]}
        (lifted,) = await events(client, 'engine.unbenched')
        assert set(lifted['payload'].pop('resumed')) == {held['id'], waiting['id']}
        assert lifted['payload'] == {'scope': scope, 'actor': ARTIST, 'reason': 'The fault is fixed'}
        assert fakes['comfy'].starts.count(held['id']) == 1


async def test_a_replayed_resume_never_lifts_the_bench_installed_after_it(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(outcome=FAULT)
        p = await project(client)
        turn_id = await local_run(client, p['id'], 4)
        _, _, held, waiting = await statuses(turn_id, ['failed', 'failed', 'held', 'held'])
        body = command(attempt=0, reason='Once more')
        first = await client.post(f"/api/crexil/attempts/{held['id']}/resume", json=body)
        assert first.status_code == 200 and first.json()['released'] == 2
        # It fails a third time, so A9 holds the engine for this run again: another bench.
        found = await statuses(turn_id, ['failed', 'failed', 'failed', 'held'], ordered=False)
        (still,) = [slot for slot in found if slot['status'] == 'held']
        assert still['id'] in (held['id'], waiting['id'])
        lifts = len(await events(client, 'engine.unbenched'))

        replay = await client.post(f"/api/crexil/attempts/{held['id']}/resume", json=body)
        assert replay.status_code == 200 and replay.json() == first.json()      # the saved answer, nothing done twice
        await asyncio.sleep(0.05)
        assert [slot['status'] for slot in await slots(turn_id)] == [slot['status'] for slot in found]
        assert len(await events(client, 'engine.unbenched')) == lifts

        again = await move(client, 'resume', still)                            # a new request is a new move
        assert again.status_code == 200 and again.json()['released'] == 1
        await statuses(turn_id, ['failed'] * 4)


async def test_a_recorded_resume_survives_a_restart_and_lifts_its_bench_once(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(outcome=FAULT)
        p = await project(client)
        turn_id = await local_run(client, p['id'], 3)
        _, _, held = await statuses(turn_id, ['failed', 'failed', 'held'])

        async def stopped_before_applying():
            pass
        # The server stops after the resume is recorded and before the bench is lifted.
        monkeypatch.setattr(wiring.current.scheduler, 'apply_execution_controls', stopped_before_applying)
        assert (await move(client, 'resume', held)).status_code == 200
        assert [slot['status'] for slot in await slots(turn_id)] == ['failed', 'failed', 'held']
        await wiring.current.scheduler.stop()

        fakes['comfy'].default = Step(data=image_bytes())
        restarted = Scheduler(store(), list(fakes.values()), route=lambda engine: 'openrouter' if '/' in engine else 'comfy')
        wiring.current.scheduler = restarted
        await restarted.start()
        await statuses(turn_id, ['failed', 'failed', 'landed'])
        (lifted,) = await events(client, 'engine.unbenched')
        assert lifted['payload']['actor'] == ARTIST and lifted['payload']['resumed'] == [held['id']]


async def test_a_runtime_bench_lifts_only_once_its_runner_answers_and_an_agent_may_resume_free_work(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RETRY_BACKOFF_S', 0.0)
    down = FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('unreachable', 'runner down'))
    async with booted_audio(tmp_path, monkeypatch, executors={'voice-runner': down}) as (client, _):
        p = await project(client)
        direction = await audio_draft(client, p['id'], f'audio:voice-design:{LOCAL}', 'A gruff smith',
                                      controls={'preview_text': PREVIEW}, count=3)
        turn_id = await accepted(client, direction)
        _, held, waiting = await statuses(turn_id, ['failed', 'held', 'held'])

        refused = await move(client, 'resume', held, actor=AGENT)
        assert refused.status_code == 422
        assert refused.json()['detail'] == ('The local voice runner is not ready yet, so its attempts stay held. '
                                            'Not answering at http://127.0.0.1:9. Start the voice runner.')
        assert await events(client, 'execution.control') == []                # a refusal records nothing
        assert 'provider:voice-runner' in wiring.current.scheduler.snapshot()['benches']

        down.failure = None
        with served(tmp_path) as (_, _, base):
            monkeypatch.setattr(config, 'VOICE_RUNNER_URL', base)
            answer = await move(client, 'resume', held, actor=AGENT, reason='The runner is back')
            assert answer.status_code == 200, answer.text
            assert answer.json()['words'].startswith('It answers again, so its hold was lifted: 2 held attempts')
            await statuses(turn_id, ['failed', 'landed', 'landed'])
        (lifted,) = await events(client, 'engine.unbenched')
        assert lifted['payload']['actor'] == AGENT and set(lifted['payload']['resumed']) == {held['id'], waiting['id']}


async def test_resuming_held_paid_work_is_the_artists_act(tmp_path, monkeypatch):
    refused = FakeAudioExecutor('elevenlabs', 'external', max_concurrency=1, failure=EngineFailure(
        'dead_key', 'ElevenLabs refused the key.', charged_usd=0))
    async with booted_audio(tmp_path, monkeypatch, executors={'elevenlabs': refused}) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'voice-design', 'A gruff smith',
                                                       [(ELEVEN_DESIGN, 3, SPOKEN)]))
        _, held, _ = await statuses(turn_id, ['failed', 'held', 'held'])
        assert held['committed_usd'] > 0

        agent = await move(client, 'resume', held, actor=AGENT)
        assert agent.status_code == 403 and agent.json()['detail'] == PAID_RESUME
        monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', '')
        keyless = await move(client, 'resume', held)
        assert keyless.status_code == 422 and keyless.json()['detail'] == (
            'ElevenLabs is not ready yet, so its attempts stay held. No key. Set ELEVENLABS_API_KEY in .env, '
            'then restart.')
        assert await events(client, 'execution.control') == []
        assert [slot['status'] for slot in await slots(turn_id)] == ['failed', 'held', 'held']

        monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', 'test-elevenlabs-key-0123456789')
        refused.failure = None
        artist = await move(client, 'resume', held, reason='New key in place')
        assert artist.status_code == 200 and artist.json()['released'] == 2
        await statuses(turn_id, ['failed', 'landed', 'landed'])


async def test_a_card_held_by_another_program_resumes_only_when_a_reading_shows_it_free(tmp_path, monkeypatch):
    gb = 1 << 30
    foreign = {'bytes': 6 * gb}

    async def card():
        return GpuReading(total=24 * gb, free=16 * gb - foreign['bytes'], comfy=2 * gb, foreign=foreign['bytes'],
                          name='test card')
    monkeypatch.setattr(wiring, 'GPU_PROBE', card)
    monkeypatch.setattr(config, 'GPU_RECHECK_S', 3600.0)             # no re-check lifts it during the test
    oom = FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('oom', 'CUDA out of memory'))
    async with booted_audio(tmp_path, monkeypatch, executors={'voice-runner': oom}) as (client, _):
        p = await project(client)
        direction = await audio_draft(client, p['id'], f'audio:voice-design:{LOCAL}', 'A gruff smith',
                                      controls={'preview_text': PREVIEW}, count=2)
        turn_id = await accepted(client, direction)
        held, _ = await statuses(turn_id, ['held', 'held'])             # C2: the card is held, not the take
        assert wiring.current.scheduler.snapshot()['benches']['lane:local']['kind'] == 'gpu_held'

        refused = await move(client, 'resume', held)
        assert refused.status_code == 422 and refused.json()['detail'] == (
            'Something else still holds 6.0 GB of the card. Close what holds it, then resume.')
        foreign['bytes'], oom.failure = 0, None
        answer = await move(client, 'resume', held, reason='The card is free')
        assert answer.status_code == 200 and answer.json()['released'] == 2
        await statuses(turn_id, ['landed', 'landed'])


async def test_the_preview_keeps_its_benches_and_the_browser_harness_is_every_runtime(monkeypatch):
    preview = BenchEntry('lane:local', 'preview', 'Generation is unavailable in this preview.', 5)
    assert await remaining_cause(None, preview) == 'This isolated preview runs no engines, so its attempts stay held.'
    monkeypatch.setattr(config, 'HARNESS_EXECUTORS', True)
    assert await remaining_cause(None, BenchEntry('provider:voice-runner', 'unreachable', 'down', 5)) is None


# --- cancel one attempt ---

async def test_cancelling_a_queued_attempt_skips_it_and_sends_nothing_more(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        gate = asyncio.Event()
        fakes['comfy'].default = Step(data=image_bytes(), gate=gate)
        p = await project(client)
        turn_id = await local_run(client, p['id'], 2)
        running, queued = await statuses(turn_id, ['running', 'queued'])

        body = {**command(attempt=0, reason='Enough takes'), 'actor': AGENT}       # stopping never spends
        answer = await client.post(f"/api/crexil/attempts/{queued['id']}/cancel", json=body)
        assert answer.status_code == 200, answer.text
        replay = await client.post(f"/api/crexil/attempts/{queued['id']}/cancel", json=body)
        assert replay.status_code == 200 and replay.json() == answer.json()
        reused = await client.post(f"/api/crexil/attempts/{queued['id']}/cancel", json={**body, 'reason': 'Other'})
        assert reused.status_code == 409 and 'already names different work' in reused.json()['detail']
        assert answer.json() == {'slot_id': queued['id'], 'attempt': 0, 'action': 'cancel', 'released': 0,
                                 'words': SKIPPED, 'actor': AGENT, 'reason': 'Enough takes'}
        await statuses(turn_id, ['running', 'skipped'])
        again = await move(client, 'cancel', queued)
        assert again.status_code == 409 and again.json()['detail'] == (
            'This attempt has already settled, so there is nothing to cancel. Refresh it.')

        gate.set()
        await statuses(turn_id, ['landed', 'skipped'])
        assert fakes['comfy'].starts == [running['id']]
        (record,) = await events(client, 'execution.control')
        assert record['payload']['targets'] == [{'slot_id': queued['id'], 'attempt': 0}]
        assert record['payload']['actor'] == AGENT and record['slot_id'] == queued['id']
        run = (await client.get(f'/api/crexil/execution/run/{turn_id}')).json()
        assert run['last_action'] is None and run['revision'] == 0             # the run's own control is untouched


async def test_cancelling_a_running_local_attempt_asks_its_engine_to_stop(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes(), outcome='hang')
        p = await project(client)
        turn_id = await local_run(client, p['id'], 1)
        (running,) = await statuses(turn_id, ['running'])

        answer = await move(client, 'cancel', running)
        assert answer.status_code == 200 and answer.json()['attempt'] == 1
        assert answer.json()['words'] == 'Asked to stop: the local engine stops this attempt. Local work costs no money.'
        (stopped,) = await statuses(turn_id, ['failed'])
        assert (stopped['kind'], stopped['cause']) == ('interrupted', 'cancelled')
        assert fakes['comfy'].cancels == [f"job-{running['id']}-1"]
        assert (await client.get('/api/queue')).json()['benches'] == []         # a stop benches nothing


async def test_cancelling_a_sent_paid_attempt_keeps_its_charge_facts(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        discover()
        gate = asyncio.Event()
        fakes['openrouter'].default = Step(data=image_bytes(), gate=gate, cost=0.04)
        p = await project(client)
        turn_id = (await submit_hosted(client, await hosted(client, p['id'])))['turn_id']
        (running,) = await statuses(turn_id, ['running'])

        answer = await move(client, 'cancel', running, reason='Wrong prompt')
        assert answer.status_code == 200
        assert answer.json()['words'] == (
            'Its request was already sent, so it keeps its cell: if it still delivers, the take lands here. '
            'Up to $0.04 was committed; its charge stays unverified until the provider reports it.')
        (late,) = await statuses(turn_id, ['late'])
        assert (late['committed_usd'], late['charged_usd']) == (0.04, None)    # no amount invented
        (unverified,) = await events(client, 'slot.charge_unverified')
        assert (unverified['payload']['reason'], unverified['payload']['committed_usd']) == ('cancel', 0.04)
        again = await move(client, 'cancel', late)
        assert again.status_code == 409 and again.json()['detail'] == (
            'This attempt was already stopped. If its request still delivers, the take lands in its own cell.')

        gate.set()
        (landed,) = await statuses(turn_id, ['landed'])                       # into its own cell (laws 7, 8)
        assert landed['charged_usd'] == 0.04
        for _ in range(200):
            if await events(client, 'slot.late_landed'):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError('the late take was never recorded as landing late')


# --- refusals ---

async def test_moves_refuse_in_plain_words_with_the_next_move(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        await control(client, 'project', p['id'], 'pause')
        turn_id = await local_run(client, p['id'], 1)
        (paused,) = await statuses(turn_id, ['held'])

        held_by_pause = await move(client, 'resume', paused)
        assert held_by_pause.status_code == 422 and held_by_pause.json()['detail'] == (
            'This attempt waits for its run or project to resume. Resume that instead.')
        stale = await move(client, 'cancel', paused, attempt=3)
        assert stale.status_code == 409 and stale.json()['detail'] == (
            'This attempt changed since it was read. Refresh it, then decide again.')
        missing = await client.post('/api/crexil/attempts/no-such-attempt/resume', json=command(attempt=0))
        assert missing.status_code == 404
        assert not [e for e in await events(client, 'execution.control') if e['payload']['scope'] == 'slot']

        await control(client, 'project', p['id'], 'resume')
        (landed,) = await statuses(turn_id, ['landed'])
        settled = await move(client, 'resume', landed)
        assert settled.status_code == 409 and settled.json()['detail'] == (
            'This attempt is not held, so there is nothing to resume. Refresh it.')


# --- blind runs ---

async def test_trying_once_more_on_a_hidden_attempt_names_no_engine(tmp_path, monkeypatch):
    failing = FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('error', 'Qwen3-TTS crashed'))
    async with booted_audio(tmp_path, monkeypatch, rows=(free_design_row(),),
                            executors={'voice-runner': failing}) as (client, _):
        p = await project(client)
        turn, _ = await ran(client, await prepared(client, p['id'], (LOCAL, 3), (HOSTED, 1)),
                            until=('landed', 'held', 'failed'))
        (held,) = [slot for slot in turn['slots'] if slot['status'] == 'held']

        failing.failure = None
        answer = await move(client, 'resume', held, reason='Once more, blind')
        assert answer.status_code == 200 and not names_an_engine(answer.json())
        assert answer.json()['words'].startswith('Trying once more: 1 held attempt went back')
        await settle_turn(turn['id'], until={'landed', 'failed'})
        moves = [row for row in await events(client) if row['type'] in ('execution.control', 'engine.unbenched')]
        assert len(moves) == 2
        for row in moves:                                   # no engine, no bench scope, no bench to look up
            assert not names_an_engine(row) and not {'bench', 'bench_seq'} & set(row['payload']), row
            assert row['payload'].get('scope') in (None, 'slot'), row


async def test_cancelling_a_hidden_attempt_names_neither_its_lane_nor_its_cost(tmp_path, monkeypatch):
    gate = asyncio.Event()
    slow = FakeAudioExecutor('voice-runner', 'local', gate=gate)
    async with booted_audio(tmp_path, monkeypatch, rows=(free_design_row(),),
                            executors={'voice-runner': slow}) as (client, _):
        p = await project(client)
        check, answer = await submitted(client, await prepared(client, p['id'], (LOCAL, 2), (HOSTED, 1)))
        assert answer.status_code == 201, answer.text
        turn_id = answer.json()['turn_id']

        async def local_states():
            found = {slot['status']: slot for slot in await slots(turn_id) if slot['engine'] == LOCAL}
            return found if set(found) == {'running', 'queued'} else None
        for _ in range(600):
            local = await local_states()
            if local:
                break
            await asyncio.sleep(0.01)
        skipped = await move(client, 'cancel', local['queued'])
        stopping = await move(client, 'cancel', local['running'])
        assert skipped.status_code == stopping.status_code == 200
        assert skipped.json()['words'] == SKIPPED
        assert stopping.json()['words'] == ('Asked to stop. Whatever it still delivers lands in its own cell; '
                                            'its cost shows after the reveal.')
        assert not names_an_engine([skipped.json(), stopping.json()])
        gate.set()
        await settle_turn(turn_id, until={'landed', 'skipped', 'failed'})


# --- the review says which operations retry on their own (law 9) ---

async def test_the_review_says_which_operations_retry_on_their_own_as_their_attempts_freeze(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        voice = await audio_draft(client, p['id'], f'audio:voice-design:{LOCAL}', 'A gruff smith',
                                  controls={'preview_text': PREVIEW})
        assert (await reviewed(client, voice))['automatic_retry'] == {f'audio:voice-design:{LOCAL}': True}
        turn, _ = await audio_submit(client, voice)
        assert [slot['dials']['crexil']['automatic_retry'] for slot in turn['slots']] == [True]

        image = await draft(client, p['id'])
        assert (await reviewed(client, image))['automatic_retry'] == {'testpreset': False}
        turn_id = await local_run(client, p['id'], 1)
        (slot,) = await statuses(turn_id, ['landed'])
        assert slot['dials']['crexil']['automatic_retry'] is False
        assert (await reviewed(client, await draft(client, p['id'], operations=[])))['automatic_retry'] == {}
