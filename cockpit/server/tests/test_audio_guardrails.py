"""Guardrails with audio engines, proven end to end: the HTTP API, the real
scheduler, and the ElevenLabs adapter against a synthetic ElevenLabs
(test_elevenlabs.FakeElevenLabs, built from the public API reference, never
recorded). Each test is one journey the laws promise an artist who casts a voice:
money never moves past a cap (law 22), one dead provider never stops the others
and speaks once (law 1), every message is plain (law 3) and keeps the facts the
next move needs (law 4), "nothing was charged" is said only when it is known
(law 5), and nothing retries silently (law 9). The unit tests beside this file
prove each piece; these prove the pieces together. No network, no GPU.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from app import config, elevenlabs
from app.crexil_budget import accounting
from app.elevenlabs import ElevenLabsExecutor
from fakes import FakeAudioExecutor, Step
from test_api_v4 import settle_turn, store
from test_audio_contract import PREVIEW, booted_audio
from test_crexil import command, image_bytes, project
from test_crexil_execution import reviewed
from test_crexil_hosted import discover, hosted, limits
from test_crexil_hosted import submit as submit_hosted
from test_elevenlabs import BASE, LINE, FakeElevenLabs, refusal, sounded
from test_elevenlabs import designed as designed_answer

pytestmark = pytest.mark.anyio

ELEVEN_DESIGN = 'audio:voice-design:elevenlabs-voice-design'
ELEVEN_SPEECH = 'audio:speak:elevenlabs-v4'
ELEVEN_SFX = 'audio:sfx:elevenlabs-sfx'
LOCAL_DESIGN = 'audio:voice-design:qwen3-tts'
DESCRIPTION = 'A gruff dwarf blacksmith, low and warm'
SPOKEN = {'preview_text': PREVIEW}           # 129 characters: $0.01032 at ElevenLabs' list price
DESIGN_USD = len(PREVIEW) * 80 / 1e6         # $0.08 per 1,000 characters
SOUND = {'duration_seconds': 1.5}            # $0.003 at $0.12 per minute
KEY_TURNED_AWAY = 'ElevenLabs turned the key away'   # a bad key's primary line begins so
# What a blind run's per-take reads must never name, lower-cased.
IDENTITY = ('elevenlabs', 'eleven_', 'qwen', 'voice-runner', 'voice runner')


@asynccontextmanager
async def boot(tmp_path, monkeypatch, fake=None, *, in_flight=None, **settings):
    """The audio stack: the real ElevenLabs adapter against `fake` when given,
    the scripted provider otherwise. `in_flight` narrows the adapter's own
    permit; `settings` are config values the scheduler reads when it starts
    (retry limits, the bill re-ask gap)."""
    monkeypatch.setattr(config, 'ELEVENLABS_URL', BASE)
    monkeypatch.setattr(elevenlabs, 'HISTORY_GAP_S', 0.0)
    for name, value in settings.items():
        monkeypatch.setattr(config, name, value)
    executors = None
    if fake is not None:
        adapter = ElevenLabsExecutor(transport=httpx.MockTransport(fake))
        if in_flight is not None:
            adapter.max_concurrency = in_flight
        executors = {'elevenlabs': adapter}
    async with booted_audio(tmp_path, monkeypatch, executors=executors) as (client, fakes):
        yield client, fakes


async def prepare(client, wid, action, prompt, cast, *, refs=(), limit=1.0, blind=False):
    """One preparation: `cast` is (operation, attempts, controls) per engine."""
    ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
    setup = {'action': action, 'prompt': prompt, 'references': [{'node_id': n} for n in refs],
             'operations': [{'operation_id': op, 'revision': ops[op]['revision'], 'count': count,
                             'controls': controls} for op, count, controls in cast],
             'spending_policy': 'published_tariff', 'spending_limit_usd': limit, 'blind': blind}
    response = await client.post(f'/api/crexil/projects/{wid}/directions',
                                 json=command(title='Casting', setup=setup))
    assert response.status_code == 201, response.text
    return response.json()


async def submit(client, direction):
    """Submit with the token the current review issued; the response as it is."""
    check = await reviewed(client, direction)
    response = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                 json=command(revision=check['revision'], review_token=check['token']))
    return check, response


async def accepted(client, direction):
    check, response = await submit(client, direction)
    assert check['ready'], check['errors']
    assert response.status_code == 201, response.text
    return response.json()['turn_id']


async def landed(client, direction):
    turn = await settle_turn(await accepted(client, direction), until={'landed'})
    return turn, [n for n in await store().list_nodes(turn['workspace_id']) if n['turn_id'] == turn['id']]


async def until(probe, ticks=600):
    for _ in range(ticks):
        found = await probe()
        if found:
            return found
        await asyncio.sleep(0.01)
    raise AssertionError('the condition never held')


async def events(client, **params):
    return (await client.get('/api/events', params={'limit': 500, **params})).json()['events']


def slot_events(rows, slot_id, kind):
    return [e for e in rows if e['slot_id'] == slot_id and e['type'] == kind]


# --- 1. money never moves past a cap (law 22) ---

async def test_a_run_over_its_cap_is_refused_at_submit_before_any_engine_is_called(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed_answer())
    cast = [(ELEVEN_DESIGN, 2, SPOKEN), (LOCAL_DESIGN, 1, SPOKEN)]
    async with boot(tmp_path, monkeypatch, fake) as (client, fakes):
        own = await project(client)
        check, refused = await submit(client, await prepare(client, own['id'], 'voice-design', DESCRIPTION,
                                                            cast, limit=.01))
        assert not check['ready'] and check['reserved_micro_usd'] == 2 * len(PREVIEW) * 80
        assert refused.status_code == 422
        assert refused.json()['detail'] == 'The reserved $0.02064 exceeds the $0.01 spending limit.'

        enclosing = await limits(client, await project(client), spending_limit_usd=.015,
                                 spending_policy='published_tariff')
        check, refused = await submit(client, await prepare(client, enclosing['id'], 'voice-design', DESCRIPTION,
                                                            cast, limit=1))
        assert not check['ready'] and refused.status_code == 422
        assert refused.json()['detail'] == ('Project spending limit is $0.015; charged and reserved '
                                            'spending totals $0.02064.')
        # Refused before any engine was called: not even the free local one (law 22).
        assert fake.requests == [] and fakes['voice-runner'].requests == []
        assert await store().list_turns(own['id']) == await store().list_turns(enclosing['id']) == []


async def test_spending_that_moved_since_review_refuses_an_audio_run_that_no_longer_fits(tmp_path, monkeypatch):
    gate = asyncio.Event()
    provider = FakeAudioExecutor('elevenlabs', 'external', gate=gate)
    async with booted_audio(tmp_path, monkeypatch, executors={'elevenlabs': provider}) as (client, _):
        p = await limits(client, await project(client), spending_limit_usd=.015, spending_policy='published_tariff')
        one = [(ELEVEN_DESIGN, 1, SPOKEN)]
        first = await prepare(client, p['id'], 'voice-design', DESCRIPTION, one)
        first_review = await reviewed(client, first)
        assert first_review['ready'], first_review['errors']          # alone, it fits
        other = await accepted(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION, one))
        await until(lambda: asyncio.sleep(0, provider.requests))     # other work holds its reservation in flight
        late = await client.post(f"/api/crexil/directions/{first['id']}/submit",
                                 json=command(revision=first_review['revision'], review_token=first_review['token']))
        assert late.status_code == 409 and late.headers['x-crexil-conflict'] == 'spending'
        assert late.json()['detail'] == (
            "Other work changed this project's spending since review, and this run no longer fits its limits. "
            'Project spending limit is $0.015; charged and reserved spending totals $0.02064. '
            'Review again to see the current totals.')
        assert len(provider.requests) == 1 and provider.requests[0].slot_id in {
            s['id'] for s in (await store().get_turn(other))['slots']}
        gate.set()
        await settle_turn(other, until={'landed'})


async def test_an_unknown_audio_price_is_never_sent_and_never_counted_as_zero(tmp_path, monkeypatch):
    # One engine with no published price, beside a free local engine. The unknown
    # price blocks its own operation before dispatch; it is never priced at zero.
    from test_audio_contract import REPO
    matrix = json.loads((REPO / 'configs/engines.json').read_text())
    unpriced = dict(next(r for r in matrix['engines'] if r['id'] == 'elevenlabs-voice-design'),
                    id='test-unpriced-design', name='Unpriced design')
    del unpriced['tariffs']
    blocked = ('Unpriced design: This engine has no published price. '
               'An unknown price counts as over the spending limit.')
    async with booted_audio(tmp_path, monkeypatch, rows=[unpriced]) as (client, fakes):
        p = await project(client)
        alone = [('audio:voice-design:test-unpriced-design', 1, SPOKEN)]
        check, refused = await submit(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION, alone))
        assert not check['ready'] and any(blocked in e for e in check['errors'])
        assert refused.status_code == 422 and blocked in refused.json()['detail']

        beside = alone + [(LOCAL_DESIGN, 1, SPOKEN)]
        check = await reviewed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION, beside))
        assert (check['ready_attempts'], check['blocked_attempts']) == (1, 1)
        assert check['reserved_micro_usd'] == 0 and any(blocked in e for e in check['errors'])
        turn, takes = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION, beside))
        # Only the priced work ran: the unknown price reached no engine.
        assert {s['engine'] for s in turn['slots']} == {'qwen3-tts'} and len(takes) == 3
        assert fakes['elevenlabs'].requests == [] and len(fakes['voice-runner'].requests) == 1


# --- 2. one dead provider stops only itself, with one cause (laws 1, 3, 4, 5) ---

async def test_a_dead_elevenlabs_key_stops_only_elevenlabs_once_while_every_other_engine_lands(
        tmp_path, monkeypatch):
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed_answer())
            .on('POST', '/v1/text-to-voice', refusal(401, 'invalid_api_key'))
            .on('POST', '/v1/text-to-speech/voice-1/with-timestamps', refusal(401, 'invalid_api_key')))
    async with boot(tmp_path, monkeypatch, fake) as (client, fakes):
        discover()
        fakes['openrouter'].default = Step(data=image_bytes(), cost=.04)
        p = await project(client)
        _, voices = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                       [(ELEVEN_DESIGN, 1, SPOKEN)]))
        # The key is turned away mid-session; speech, a local voice and an image go at once.
        speech = await accepted(client, await prepare(client, p['id'], 'speak', LINE, [(ELEVEN_SPEECH, 3, {})],
                                                      refs=[voices[0]['id']]))
        local = await accepted(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                     [(LOCAL_DESIGN, 1, SPOKEN)]))
        image = (await submit_hosted(client, await hosted(client, p['id'])))['turn_id']
        spoken = await settle_turn(speech, until={'failed', 'held'})
        await settle_turn(local, until={'landed'})
        await settle_turn(image, until={'landed'})

        failed = [s for s in spoken['slots'] if s['status'] == 'failed']
        held = [s for s in spoken['slots'] if s['status'] == 'held']
        assert failed and len(failed) + len(held) == 3
        (cause,) = {slot['cause'] for slot in failed}                          # one cause, word for word
        assert cause.startswith(KEY_TURNED_AWAY) and '401' not in cause         # plain, the right provider
        for slot in failed:
            assert (slot['kind'], slot['charged_usd']) == ('dead_key', 0)       # the facts the face needs
            assert 'Nothing was charged.' in slot['detail']
        # One cause, one record: the provider is benched once, and no lane at all.
        benched = [e['payload'] for e in await events(client) if e['type'] == 'engine.benched']
        assert [(b['scope'], b['kind'], b['cause']) for b in benched] == [('provider:elevenlabs', 'dead_key', cause)]
        queue = (await client.get('/api/queue')).json()
        assert queue['benches'] == [{'scope': 'provider:elevenlabs', 'kind': 'dead_key', 'cause': cause}]
        assert {r['id']: r['bench'] for r in queue['held']} == {s['id']: 'provider:elevenlabs' for s in held}
        # Every other engine landed: the same external lane's image and the local voice.
        counts = await accounting(store(), p['id'])
        assert counts['charged_usd'] == pytest.approx(DESIGN_USD + .04)


# --- 3. nothing retries silently, and retries are bounded (law 9) ---

async def test_every_call_elevenlabs_receives_is_a_recorded_attempt(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation',
                               refusal(429, 'rate_limit_exceeded', headers={'retry-after-ms': '10'}), sounded())
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'sfx', 'Hammer on an anvil',
                                                       [(ELEVEN_SFX, 1, SOUND)]))
        (slot,) = (await settle_turn(turn_id, until={'landed', 'failed'}))['slots']
        rows = await events(client, workspace_id=p['id'])
        sends = slot_events(rows, slot['id'], 'slot.provider_sending')
        # Every request the provider saw was recorded before it was sent: none is silent (law 9).
        assert len(fake.calls('POST', '/v1/sound-generation')) == len(sends) >= 1
        assert [s['payload']['attempt'] for s in sends] == list(range(1, len(sends) + 1))
        retries = slot_events(rows, slot['id'], 'slot.retrying')
        assert len(retries) == len(sends) - 1                                  # each repeat says so first
        if slot['status'] == 'failed':
            assert (slot['kind'], slot['charged_usd']) == ('rate_limit', 0)    # visible, and nothing charged
            assert 'Nothing was charged.' in slot['detail']


async def test_a_rate_limited_take_waits_visibly_on_the_providers_clock_and_lands(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation',
                               refusal(429, 'rate_limit_exceeded', headers={'retry-after-ms': '400'}), sounded())
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'sfx', 'Hammer on an anvil',
                                                       [(ELEVEN_SFX, 1, SOUND)]))

        async def waiting():
            return (await client.get('/api/queue')).json()['retries']

        (wait,) = await until(waiting)
        assert (wait['kind'], wait['attempt'], wait['of'], wait['window']) == ('rate_limit', 2, 5, 'provider')
        assert wait['wait_s'] == pytest.approx(.4)
        turn = await settle_turn(turn_id, until={'landed'})
        (slot,) = turn['slots']
        assert (slot['attempt'], slot['retry_of']) == (2, 'rate_limit')
        (retry,) = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.retrying')
        assert {k: retry['payload'][k] for k in ('kind', 'attempt', 'next_attempt', 'of', 'window')} == {
            'kind': 'rate_limit', 'attempt': 1, 'next_attempt': 2, 'of': 5, 'window': 'provider'}
        assert len(fake.calls('POST', '/v1/sound-generation')) == 2
        assert (await client.get('/api/queue')).json()['benches'] == []      # a rate limit never benches
        assert (await accounting(store(), p['id']))['charged_usd'] == pytest.approx(.003)   # the take, once


async def test_a_take_that_stays_rate_limited_fails_after_its_bounded_retries(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation',
                               refusal(429, 'rate_limit_exceeded', headers={'retry-after-ms': '10'}))
    async with boot(tmp_path, monkeypatch, fake, RATE_LIMIT_RETRIES=2) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'sfx', 'Hammer on an anvil',
                                                       [(ELEVEN_SFX, 1, SOUND)]))
        (slot,) = (await settle_turn(turn_id, until={'failed'}))['slots']
        assert (slot['kind'], slot['attempt'], slot['charged_usd']) == ('rate_limit', 3, 0)
        assert 'still rate limited after 3 tries' in slot['cause']
        retries = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.retrying')
        assert [(r['payload']['attempt'], r['payload']['of']) for r in retries] == [(1, 3), (2, 3)]
        assert len(fake.calls('POST', '/v1/sound-generation')) == 3              # bounded: three tries, no more
        assert (await client.get('/api/queue')).json()['benches'] == []
        counts = await accounting(store(), p['id'])
        # Every refused try settled at a known zero, so no cap counts money never at risk (law 22).
        assert (counts['charged_usd'], counts['reserved_usd']) == (0, 0)


# --- 4. "nothing was charged" only when it is known (law 5, B4) ---

@pytest.mark.parametrize('answer', [refusal(401, 'invalid_api_key'), refusal(402, 'insufficient_credits'),
                                    refusal(401, 'quota_exceeded')],
                         ids=['bad key', 'balance empty', 'quota used up'])
async def test_a_refusal_before_generation_records_a_known_zero_and_releases_its_reservation(
        tmp_path, monkeypatch, answer):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer)
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        (slot,) = (await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'sfx', 'Hammer on an anvil', [(ELEVEN_SFX, 1, SOUND)])), until={'failed'}))['slots']
        assert (slot['kind'], slot['charged_usd']) == ('dead_key', 0)            # a known zero, never unknown
        assert 'Nothing was charged.' in slot['detail'] and 'ElevenLabs' in slot['cause']
        assert not any(code in slot['cause'] for code in ('401', '402'))
        counts = await accounting(store(), p['id'])
        assert (counts['charged_usd'], counts['reserved_usd'], counts['unresolved_attempts']) == (0, 0, 0)
        assert [b['scope'] for b in (await client.get('/api/queue')).json()['benches']] == ['provider:elevenlabs']


async def test_a_charged_but_empty_design_keeps_its_charge_and_marks_it_unverified(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed_answer(0))
    async with boot(tmp_path, monkeypatch, fake, BILL_CHECK_GAP_S=0) as (client, _):
        p = await project(client)
        (slot,) = (await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'voice-design', DESCRIPTION, [(ELEVEN_DESIGN, 1, SPOKEN)])), until={'failed'}))['slots']
        assert slot['kind'] == 'charged_blank' and slot['charged_usd'] == pytest.approx(DESIGN_USD)
        assert 'took the job and billed it, but no audio came back' in slot['cause']

        async def unverified():
            found = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.charge_unverified')
            return found or None

        (note,) = await until(unverified)
        assert note['payload']['reason'] == 'bill_check' and note['payload']['charged_usd'] == pytest.approx(DESIGN_USD)
        asks = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.bill_check')
        assert [a['payload']['ask'] for a in asks] == [1, 2, 3]                  # free re-asks, never a new job
        assert len(fake.calls('POST', '/v1/text-to-voice/design')) == 1
        # The charge stays as reported: never zero.
        assert (await accounting(store(), p['id']))['charged_usd'] == pytest.approx(DESIGN_USD)


async def test_a_lost_sound_keeps_its_reservation_and_records_the_charge_as_unverified(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', httpx.ReadTimeout('the answer was lost'))
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        (slot,) = (await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'sfx', 'Hammer on an anvil', [(ELEVEN_SFX, 1, SOUND)])), until={'failed'}))['slots']
        assert slot['kind'] == 'timeout' and slot['charged_usd'] is None        # unknown, never a guessed zero
        assert 'cannot be collected' in slot['detail'] and 'unverified' in slot['detail']
        (note,) = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.charge_unverified')
        assert note['payload']['reason'] == 'timeout' and note['payload']['committed_usd'] == pytest.approx(.003)
        # The reservation keeps counting against every cap until the charge is known (law 22).
        counts = await accounting(store(), p['id'])
        assert (counts['charged_usd'], counts['reserved_usd'], counts['unresolved_attempts']) == (0, .003, 1)
        assert len(fake.calls('POST', '/v1/sound-generation')) == 1              # never generated again


# --- 5. a blind run's refusals and failures name no engine (laws 3, 4) ---

def names_an_engine(item) -> bool:
    text = json.dumps(item).lower()
    return any(word in text for word in IDENTITY)


async def blind_reads(client, wid, turn_id):
    """Every per-take read of one blind run: its slots and takes in the snapshot,
    the events about them (by id, or naming one of them), the queue's rows, and
    the workspace read."""
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    (turn,) = [t for t in snapshot['turns'] if t['id'] == turn_id]
    slot_ids = {s['id'] for s in turn['slots']}
    nodes = [n for n in snapshot['nodes'] if n.get('turn_id') == turn_id]
    node_ids = {n['id'] for n in nodes}
    queue = (await client.get('/api/queue')).json()
    legacy = (await client.get(f'/api/workspaces/{wid}')).json()
    return {
        'snapshot slots': turn['slots'],
        'snapshot nodes': nodes,
        'events': [e for e in await events(client) if e['slot_id'] in slot_ids or e['node_id'] in node_ids
                   or any(sid in json.dumps(e['payload']) for sid in slot_ids)],
        'queue': [r for key in ('held', 'late') for r in queue[key] if r['turn_id'] == turn_id],
        'workspace slots': [s for t in legacy['turns'] if t['id'] == turn_id for s in t['slots']],
    }


def leaks(reads):
    return {name: [i for i in items if names_an_engine(i)] for name, items in reads.items()}


async def test_a_blind_runs_failure_names_no_engine_on_any_read_and_keeps_its_next_move(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', refusal(401, 'invalid_api_key'))
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                       [(ELEVEN_DESIGN, 1, SPOKEN), (LOCAL_DESIGN, 1, SPOKEN)],
                                                       blind=True))
        await settle_turn(turn_id, until={'landed', 'failed'})
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        (turn,) = [t for t in snapshot['turns'] if t['id'] == turn_id]
        (failed,) = [s for s in turn['slots'] if s['status'] == 'failed']
        assert failed['kind'] == 'dead_key'                                     # the face still knows its next move
        assert failed['cause'] == ("The provider refused its key or has no credit. "
                                   "Reveal the run to read the engine's own words.")
        reads = await blind_reads(client, p['id'], turn_id)
        assert len(reads['snapshot nodes']) == 3 and reads['events'], 'the run landed takes and wrote their events'
        assert leaks(reads) == {name: [] for name in reads}


@pytest.mark.xfail(strict=True, reason=(
    "F10 (V1b S-2): a hidden take held by a provider bench keeps that bench's scope, 'provider:elevenlabs', in its "
    "slot.held event, and the engine.benched event lists the hidden slot beside the provider's own cause."))
async def test_a_blind_take_held_by_a_provider_bench_names_no_provider_on_any_read(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', refusal(401, 'invalid_api_key'))
    async with boot(tmp_path, monkeypatch, fake, in_flight=1) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                       [(ELEVEN_DESIGN, 2, SPOKEN), (LOCAL_DESIGN, 1, SPOKEN)],
                                                       blind=True))
        turn = await settle_turn(turn_id, until={'landed', 'failed', 'held'})
        assert sorted(s['status'] for s in turn['slots']) == ['failed', 'held', 'landed']
        reads = await blind_reads(client, p['id'], turn_id)
    assert leaks(reads) == {name: [] for name in reads}


async def test_a_blind_runs_refusal_names_no_engine(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        check, refused = await submit(client, await prepare(
            client, p['id'], 'voice-design', DESCRIPTION, [(ELEVEN_DESIGN, 2, SPOKEN), (LOCAL_DESIGN, 1, SPOKEN)],
            limit=.01, blind=True))
        assert refused.status_code == 422 and not names_an_engine(refused.json()['detail'])
        assert fakes['elevenlabs'].requests == [] and fakes['voice-runner'].requests == []


async def blind_take_held_by_a_lowered_limit(tmp_path, monkeypatch):
    """A blind run whose second attempt a lowered project limit holds before it is
    sent; returns the held slot as the snapshot reads it."""
    gate = asyncio.Event()
    provider = FakeAudioExecutor('elevenlabs', 'external', gate=gate, max_concurrency=1)
    async with booted_audio(tmp_path, monkeypatch, executors={'elevenlabs': provider}) as (client, _):
        p = await limits(client, await project(client), spending_limit_usd=.006, spending_policy='published_tariff')
        turn_id = await accepted(client, await prepare(client, p['id'], 'sfx', 'Anvil', [(ELEVEN_SFX, 2, SOUND)],
                                                       blind=True))
        await until(lambda: asyncio.sleep(0, provider.requests))
        await limits(client, p, spending_limit_usd=.003, spending_policy='published_tariff')
        gate.set()
        await settle_turn(turn_id, until={'landed', 'held'})
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        (turn,) = [t for t in snapshot['turns'] if t['id'] == turn_id]
        (held,) = [s for s in turn['slots'] if s['status'] == 'held']
        assert len(provider.requests) == 1                              # the held attempt was never sent
        return held


async def test_a_blind_take_held_by_a_limit_names_no_engine(tmp_path, monkeypatch):
    held = await blind_take_held_by_a_lowered_limit(tmp_path, monkeypatch)
    assert held['identity_hidden'] is True and not names_an_engine(held)


@pytest.mark.xfail(strict=True, reason=(
    "blind.mask replaces every cause of a hidden take with plain_cause(kind); a take held by a spending "
    "limit has no failure kind, so it reads 'This take did not land. Reveal the run to read the engine's "
    "own words.' That loses the limit reason, which names no engine, calls a held take one that did not "
    "land, and asks for a reveal that blindness does not need (laws 3 and 4)."))
async def test_a_blind_take_held_by_a_limit_still_says_which_limit_holds_it(tmp_path, monkeypatch):
    held = await blind_take_held_by_a_lowered_limit(tmp_path, monkeypatch)
    assert held['cause'] == 'Project spending limit is $0.003; charged and reserved spending totals $0.006.'


# --- 6. a refusal reads the adapter's own plain words (laws 3, 4) ---
#
# A refusal before generation shows the adapter's words as its primary line: they name the
# real cause, never "the engine failed, not your text", and the next move (change the plan,
# choose another voice, add credit, free a voice slot). ElevenLabs' own message stays under
# DETAILS.

INVALID = [{'loc': ['body', 'text'], 'msg': 'field required', 'type': 'missing'}]
REFUSALS = {   # where the refusal comes, the answer, and the primary line it should give
    'plan cannot use this feature': ('sound', refusal(403, 'feature_not_available', message='Sound effects need a paid plan'),
                                     'This ElevenLabs plan or key cannot use this feature. Nothing was charged.'),
    'request refused as invalid': ('sound', refusal(422, detail=INVALID),
                                   'ElevenLabs refused the request. Nothing was charged.'),
    'balance empty': ('sound', refusal(402, 'insufficient_credits'),
                      'The ElevenLabs balance is empty. Nothing was charged.'),
    'quota used up': ('sound', refusal(401, 'quota_exceeded'),
                      "ElevenLabs says this key's quota is used up. Nothing was charged."),
    'voice no longer in the account': ('speech', refusal(404, 'voice_not_found'),
                                       'ElevenLabs no longer has this voice; it may have been deleted from the account.'),
    'voice slots full': ('save', refusal(400, 'voice_limit_reached', message='You have reached your voice limit'),
                         'Every ElevenLabs voice slot on this account is in use, so this designed voice could not be '
                         'saved. Nothing was charged. Free a voice slot in the ElevenLabs account'),
}


@pytest.mark.parametrize('primary_line', list(REFUSALS))
async def test_a_provider_refusal_reads_the_providers_own_plain_words(tmp_path, monkeypatch, primary_line):
    at, answer, words = REFUSALS[primary_line]
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer)
    fake.on('POST', '/v1/text-to-voice/design', designed_answer())
    fake.on('POST', '/v1/text-to-voice', answer if at == 'save' else httpx.Response(200, json={'voice_id': 'voice-1'}))
    fake.on('POST', '/v1/text-to-speech/voice-1/with-timestamps', answer)
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        if at == 'sound':
            direction = await prepare(client, p['id'], 'sfx', 'Hammer on an anvil', [(ELEVEN_SFX, 1, SOUND)])
        else:
            _, voices = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                           [(ELEVEN_DESIGN, 1, SPOKEN)]))
            direction = await prepare(client, p['id'], 'speak', LINE, [(ELEVEN_SPEECH, 1, {})],
                                      refs=[voices[0]['id']])
        (slot,) = (await settle_turn(await accepted(client, direction), until={'failed'}))['slots']
    assert slot['cause'].startswith(words), slot['cause']
    assert 'the engine failed' not in slot['cause'] and slot['charged_usd'] == 0


async def test_a_benched_providers_operations_give_the_benchs_own_cause_as_their_reason(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', refusal(401, 'invalid_api_key'))
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        await settle_turn(await accepted(client, await prepare(client, p['id'], 'sfx', 'Hammer on an anvil',
                                                               [(ELEVEN_SFX, 1, SOUND)])), until={'failed'})
        (bench,) = (await client.get('/api/queue')).json()['benches']
        operations = [o for o in (await client.get('/api/crexil/operations')).json() if o.get('provider') == 'elevenlabs']
    assert operations and all(o['availability'] == 'unavailable' for o in operations)
    assert all(bench['cause'] in o['reasons'] for o in operations)


async def test_a_description_longer_than_the_local_engine_takes_is_refused_at_review(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        check = await reviewed(client, await prepare(client, p['id'], 'voice-design', 'A' * 2500,
                                                     [(LOCAL_DESIGN, 1, SPOKEN)]))
    assert not check['ready'] and fakes['voice-runner'].requests == []
    assert any('at most 2000 characters' in error for error in check['errors'])
