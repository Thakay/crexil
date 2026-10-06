"""Seeds stay inside each engine's declared range, so the seed recorded is the
seed sent; and a designed ElevenLabs voice takes at most one voice slot: its
save is kept in the record the moment ElevenLabs answers and is reused after a
restart, a cancel during the save or a failure after it. Crexil never deletes
a voice.

ElevenLabs answers come from the synthetic `FakeElevenLabs` (built from the
public API reference, never recorded). No network, no GPU, no key.
"""

import asyncio
import json

import httpx
import pytest

from app import crexil_audio, presets, wiring
from app.db import Database
from app.elevenlabs import ElevenLabsExecutor
from app.executors import CollectedOutput, DispatchRequest, EngineFailure
from test_api_v4 import settle_turn, store
from test_audio_guardrails import (DESCRIPTION, ELEVEN_DESIGN, ELEVEN_SPEECH, LOCAL_DESIGN, SPOKEN, accepted, boot,
                                   landed, prepare)
from test_audio_hardening import SPEECH, Record, attempt, no_deletes
from test_crexil import project
from test_crexil_controls import control, statuses
from test_crexil_execution import reviewed
from test_elevenlabs import (LINE, FakeElevenLabs, designed, designed_voice, executor, prices,  # noqa: F401
                             refusal, rows, speech_envelope, spoken)

pytestmark = pytest.mark.anyio

ELEVEN_TOP = 2 ** 32 - 1
SAVE = '/v1/text-to-voice'
LABELLED = {'voices': [{'voice_id': 'voice-1', 'name': 'Crexil gen-0',
                        'labels': {'source': 'crexil', 'crexil_gen_voice_id': 'gen-0'}}]}


def seeded(value=None, mode='fixed'):
    return {**SPOKEN, 'seed': {'mode': mode, 'value': value} if mode != 'random' else {'mode': 'random'}}


# --- seeds ---

async def test_seeded_audio_engines_publish_their_seed_range(tmp_path, monkeypatch):
    async with boot(tmp_path, monkeypatch) as (client, _):
        ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
    seed = {op: next((i for i in ops[op]['inputs'] if i['key'] == 'seed'), None)
            for op in (ELEVEN_DESIGN, ELEVEN_SPEECH, LOCAL_DESIGN, 'audio:sfx:elevenlabs-sfx')}
    assert (seed[ELEVEN_DESIGN]['min'], seed[ELEVEN_DESIGN]['max']) == (0, ELEVEN_TOP)
    assert (seed[ELEVEN_SPEECH]['min'], seed[ELEVEN_SPEECH]['max']) == (0, ELEVEN_TOP)
    assert (seed[LOCAL_DESIGN]['min'], seed[LOCAL_DESIGN]['max']) == (0, presets.MAX_SEED - 1)
    assert seed['audio:sfx:elevenlabs-sfx'] is None                                # the sound endpoint takes none


async def test_a_drawn_seed_stays_inside_the_engines_range_and_is_the_seed_elevenlabs_receives(tmp_path, monkeypatch):
    # The highest draw the range allows. A draw above 2**32 - 1 would be sent reduced, and the
    # record would show a seed ElevenLabs never received.
    monkeypatch.setattr(crexil_audio.secrets, 'randbelow', lambda n: n - 1)
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
    async with boot(tmp_path, monkeypatch, fake) as (client, fakes):
        p = await project(client)
        turn, takes = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                         [(ELEVEN_DESIGN, 1, seeded(mode='random'))]))
        local, _ = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                      [(LOCAL_DESIGN, 1, seeded(mode='random'))]))
    (slot,) = turn['slots']
    sent = json.loads(fake.calls('POST', '/v1/text-to-voice/design')[0].content)['seed']
    assert slot['seed'] == slot['external']['seed'] == sent == ELEVEN_TOP
    assert {take['seed'] for take in takes} == {ELEVEN_TOP}                         # every take says the seed sent
    assert local['slots'][0]['seed'] == fakes['voice-runner'].requests[0].external['seed'] == presets.MAX_SEED - 1


@pytest.mark.parametrize('seed, count, refused', [
    ({'mode': 'fixed', 'value': ELEVEN_TOP + 1}, 1, f'seed {ELEVEN_TOP + 1} is outside it. Choose a seed in that range.'),
    ({'mode': 'locked', 'value': ELEVEN_TOP + 1}, 2, f'seed {ELEVEN_TOP + 1} is outside it. Choose a seed in that range.'),
    ({'mode': 'fixed', 'value': ELEVEN_TOP}, 2,
     f'2 attempts from seed {ELEVEN_TOP} reach {ELEVEN_TOP + 1}. Choose a lower seed or fewer attempts.'),
    ({'mode': 'locked', 'value': ELEVEN_TOP}, 3, None),
    ({'mode': 'fixed', 'value': ELEVEN_TOP - 2}, 3, None),
], ids=['fixed beyond', 'locked beyond', 'sequence passes the top', 'locked at the top', 'sequence ends at the top'])
async def test_review_refuses_a_seed_whose_attempts_would_leave_the_range_in_plain_words(
        tmp_path, monkeypatch, seed, count, refused):
    async with boot(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        check = await reviewed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                     [(ELEVEN_DESIGN, count, {**SPOKEN, 'seed': seed})]))
    if refused is None:
        assert check['ready'], check['errors']
        return
    words = f'ElevenLabs Voice Design takes seeds from 0 to {ELEVEN_TOP}; {refused}'
    assert not check['ready'] and any(words in error for error in check['errors']), check['errors']
    assert fakes['elevenlabs'].requests == []


async def test_fixed_seeds_up_to_the_top_of_the_range_are_recorded_as_sent(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn, _ = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                     [(ELEVEN_DESIGN, 2, seeded(ELEVEN_TOP - 1))]))
    recorded = sorted(slot['seed'] for slot in turn['slots'])
    sent = sorted(json.loads(call.content)['seed'] for call in fake.calls('POST', '/v1/text-to-voice/design'))
    assert recorded == sent == [ELEVEN_TOP - 1, ELEVEN_TOP]


# --- saved voices ---

async def designed_take(client, wid):
    """A designed ElevenLabs preview: the take whose voice is `gen-0`."""
    _, voices = await landed(client, await prepare(client, wid, 'voice-design', DESCRIPTION,
                                                   [(ELEVEN_DESIGN, 1, SPOKEN)]))
    facts = await store().audio_facts(wid)
    return next(v for v in voices if facts[v['id']]['voice']['generated_voice_id'] == 'gen-0')


async def speak(client, wid, voice, until=('landed',)):
    direction = await prepare(client, wid, 'speak', LINE, [(ELEVEN_SPEECH, 1, {})], refs=[voice['id']])
    return direction, await settle_turn(await accepted(client, direction), until=set(until))


async def voice_events():
    return [e for e in await store().events_since(0) if e['type'] == 'voice.saved']


async def test_a_save_is_kept_when_elevenlabs_answers_and_reused_after_a_restart(tmp_path, monkeypatch):
    # The first speech saves the voice, then ElevenLabs refuses the line, so no speech take
    # records the voice. After a restart, the next speech still finds it in the record.
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
            .on('POST', SAVE, httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', SPEECH, refusal(400, 'invalid_parameters'), spoken()))
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        voice = await designed_take(client, p['id'])
        _, refused = await speak(client, p['id'], voice, until={'failed'})
        (event,) = await voice_events()
        assert event['slot_id'] == refused['slots'][0]['id'] and event['workspace_id'] == p['id']
        assert event['payload'] == {'provider': 'elevenlabs', 'generated_voice_id': 'gen-0', 'voice_id': 'voice-1',
                                    'found_in_account': False, 'attempt': 1}
        # A restart: a fresh adapter that remembers nothing, over the same record.
        sched = wiring.current.scheduler
        earlier = sched._executors['elevenlabs']
        sched._executors['elevenlabs'] = ElevenLabsExecutor(transport=httpx.MockTransport(fake))
        await earlier.close()
        _, spoke = await speak(client, p['id'], voice)
        assert spoke['slots'][0]['external']['voice']['voice_id'] == 'voice-1'      # review found it in the record
        assert len(await voice_events()) == 1
    assert len(fake.calls('POST', SAVE)) == 1                                       # one voice slot, ever
    assert [r.url.path for r in fake.calls('POST', SPEECH)] == [SPEECH, SPEECH] and no_deletes(fake)


async def test_a_cancel_during_the_save_keeps_the_voice_and_sends_nothing_paid(tmp_path, monkeypatch):
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
            .on('POST', SAVE, httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', SPEECH, spoken()))
    entered, release = asyncio.Event(), asyncio.Event()

    async def saving(request):
        if request.method == 'POST' and request.url.path == SAVE:
            entered.set()
            await release.wait()
        return fake(request)

    async with boot(tmp_path, monkeypatch, saving) as (client, _):
        p = await project(client)
        voice = await designed_take(client, p['id'])
        cancelled = await accepted(client, await prepare(client, p['id'], 'speak', LINE, [(ELEVEN_SPEECH, 1, {})],
                                                         refs=[voice['id']]))
        await asyncio.wait_for(entered.wait(), 2)
        await control(client, 'project', p['id'], 'cancel')
        await statuses(cancelled, ['late'])
        release.set()
        turn = await statuses(cancelled, ['skipped'])
        assert fake.calls('POST', SPEECH) == []                                    # nothing paid was sent
        assert [e['payload']['voice_id'] for e in await voice_events()] == ['voice-1']   # the save is kept
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        assert snapshot['accounting']['reserved_usd'] == 0
        assert snapshot['charged_usd'] == pytest.approx(len(SPOKEN['preview_text']) * 80 / 1e6)   # the design alone
        assert turn['slots'][0]['charged_usd'] in (None, 0)
        _, spoke = await speak(client, p['id'], voice)                             # the next line reuses the voice
        assert spoke['slots'][0]['status'] == 'landed'
    assert len(fake.calls('POST', SAVE)) == 1 and no_deletes(fake)


async def test_a_restart_during_the_save_settles_the_unsent_line_at_a_known_zero(tmp_path, monkeypatch):
    # The cockpit stops while the speech attempt saves its voice, before its paid call.
    from app.crexil_budget import accounting
    from app.scheduler import UNSENT_COPY
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
            .on('POST', SAVE, httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', SPEECH, spoken()))
    entered, release = asyncio.Event(), asyncio.Event()

    async def saving(request):
        if request.method == 'POST' and request.url.path == SAVE:
            entered.set()
            await release.wait()
        return fake(request)

    async with boot(tmp_path, monkeypatch, saving) as (client, _):
        p = await project(client)
        voice = await designed_take(client, p['id'])
        turn_id = await accepted(client, await prepare(client, p['id'], 'speak', LINE, [(ELEVEN_SPEECH, 1, {})],
                                                       refs=[voice['id']]))
        await asyncio.wait_for(entered.wait(), 2)
        sched = wiring.current.scheduler
        await sched.stop()                    # a crash-equivalent stop keeps the running row running
        release.set()
        sched._started = sched._stopping = False
        await sched.start()
        (slot,) = (await store().get_turn(turn_id))['slots']
        kinds = [e['type'] for e in await store().slot_events(slot['id'])]
        counts = await accounting(store(), p['id'])
    assert 'slot.provider_sending' not in kinds and fake.calls('POST', SPEECH) == []    # never sent
    assert (slot['status'], slot['kind'], slot['cause'], slot['charged_usd']) == ('failed', 'preflight',
                                                                                  UNSENT_COPY, 0)
    assert 'slot.charge_unverified' not in kinds                                         # its charge is known (law 5)
    assert (counts['reserved_usd'], counts['unresolved_attempts']) == (0, 0)


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_voice_the_record_holds_is_used_at_dispatch_without_a_save_or_a_lookup():
    fake = FakeElevenLabs().on('POST', SPEECH, spoken())
    await attempt(executor(fake), speech_envelope(designed_voice()), Record(voices={'gen-0': 'voice-1'}))
    assert [(r.method, r.url.path) for r in fake.requests] == [('POST', SPEECH)]


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_voice_the_account_holds_under_its_label_is_reused_without_a_save():
    fake = FakeElevenLabs().on('GET', '/v1/voices', httpx.Response(200, json=LABELLED)).on('POST', SPEECH, spoken())
    record = Record()
    result, _ = await attempt(executor(fake), speech_envelope(designed_voice()), record)
    assert fake.calls('POST', SAVE) == [] and len(fake.calls('POST', SPEECH)) == 1
    assert record.kept == [('gen-0', 'voice-1', True)]                              # found, and kept at once
    assert result.descriptor['audio']['voice']['voice_id'] == 'voice-1'


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_save_whose_answer_is_lost_finds_its_voice_by_its_label():
    fake = (FakeElevenLabs().on('GET', '/v1/voices', httpx.Response(200, json={'voices': []}),
                                httpx.Response(200, json=LABELLED))
            .on('POST', SAVE, httpx.ReadError('dropped')).on('POST', SPEECH, spoken()))
    record = Record()
    await attempt(executor(fake), speech_envelope(designed_voice()), record)
    assert len(fake.calls('POST', SAVE)) == 1 and len(fake.calls('POST', SPEECH)) == 1
    assert record.kept == [('gen-0', 'voice-1', True)]


@pytest.mark.usefixtures('rows', 'prices')
async def test_an_unconfirmed_save_that_the_account_does_not_hold_fails_in_plain_words():
    fake = (FakeElevenLabs().on('GET', '/v1/voices', httpx.Response(200, json={'voices': []}))
            .on('POST', SAVE, httpx.ReadError('dropped')))
    record = Record()
    with pytest.raises(EngineFailure) as unsaved:
        await attempt(executor(fake), speech_envelope(designed_voice()), record)
    assert (unsaved.value.kind, unsaved.value.plain, unsaved.value.charged_usd) == ('error', True, 0)
    assert str(unsaved.value) == ('ElevenLabs did not confirm saving this designed voice. Nothing was charged. '
                                  'Prepare the line again; Crexil looks for the voice by its label before it '
                                  'saves again.')
    assert fake.calls('POST', SPEECH) == [] and record.kept == []


@pytest.mark.parametrize('used, words', [
    (30, 'Every ElevenLabs voice slot on this account is in use (30 of 30), so this designed voice could not be '
         'saved. Nothing was charged. Free a voice slot in the ElevenLabs account (Crexil never deletes a voice), '
         'or speak with a voice that is already saved.'),
    (3, 'ElevenLabs refused the request. Nothing was charged. Change what its details name, then prepare the '
        'request again.'),
], ids=['slots full', 'slots free'])
@pytest.mark.usefixtures('rows', 'prices')
async def test_a_refused_save_says_when_every_voice_slot_is_in_use(used, words):
    fake = (FakeElevenLabs().on('POST', SAVE, refusal(400, 'invalid_parameters', message='Cannot add voice'))
            .on('GET', '/v1/user/subscription', httpx.Response(200, json={'voice_slots_used': used,
                                                                          'voice_limit': 30})))
    with pytest.raises(EngineFailure) as refused:
        await attempt(executor(fake), speech_envelope(designed_voice()), Record())
    assert (str(refused.value), refused.value.kind, refused.value.plain) == (words, 'error', True)
    assert refused.value.charged_usd == 0 and fake.calls('POST', SPEECH) == [] and no_deletes(fake)


# --- the record an adapter reads ---

async def test_the_record_names_bound_ids_unsettled_sends_and_this_attempts_send(tmp_path):
    from app import execution_outputs
    db = Database(tmp_path / 'store.sqlite')
    await db.open()
    try:
        ws = await db.create_workspace('Forge')
        envelope = {'transport': 'audio', 'provider': 'elevenlabs', 'verb': 'speak', 'text': LINE}
        turn = await db.create_turn_with_slots(ws['id'], LINE, {}, [
            {'engine': 'elevenlabs-v4', 'external': envelope} for _ in range(5)])
        landed_, lost, refused, running, mine = turn['slots']
        for slot in turn['slots']:
            await db.dispatch_slot(slot['id'])
            await db.append_event('slot.provider_sending', **await db._slot_ids(slot), payload={'attempt': 1})
        for slot, name in ((landed_, 'landed'), (mine, 'mine')):
            receipt = CollectedOutput(identity='provider:receipt', filename='provider-response.json', data=b'{}',
                                      payload=None, role='provider receipt',
                                      external={'provider': 'elevenlabs', 'request_id': f'req-{name}',
                                                'history_item_id': f'hist-{name}'})
            await execution_outputs.collect(db, tmp_path / 'staging', await db.get_slot(slot['id']),
                                            DispatchRequest(slot_id=slot['id'], engine='elevenlabs-v4'), receipt)
        await db.fail_slot(lost['id'], cause='lost', kind='timeout')                    # charge unknown
        await db.fail_slot(refused['id'], cause='refused', kind='error', charged_usd=0)  # a known zero
        found = await db.provider_attempts('elevenlabs', slot_id=mine['id'], attempt=1)
        assert found['bound'] == {'req-landed', 'hist-landed'}                         # never this attempt's own
        assert sorted(u['slot_id'] for u in found['unsettled']) == sorted([lost['id'], running['id']])
        assert all(u['external'] == envelope and u['sent_at'] > 0 for u in found['unsettled'])
        # A landed attempt is listed with the ids its receipt holds, never this attempt's own.
        (other,) = found['receipted']
        assert (other['slot_id'], other['ids'], other['external']) == (landed_['id'], ['hist-landed', 'req-landed'],
                                                                     envelope)
        assert found['sent_at'] is not None
        assert (await db.provider_attempts('voice-runner', slot_id=mine['id'], attempt=1)) == {
            'bound': set(), 'unsettled': [], 'receipted': [], 'sent_at': None}
        slot = await db.get_slot(mine['id'])
        await db.record_saved_voice(slot, 'elevenlabs', 'gen-0', 'voice-1', found=False)
        await db.record_saved_voice(slot, 'elevenlabs', 'gen-0', 'voice-1', found=True)   # kept once
        assert await db.saved_voice('elevenlabs', 'gen-0') == 'voice-1'
        assert await db.saved_voice('voice-runner', 'gen-0') is None
        assert len([e for e in await db.events_since(0) if e['type'] == 'voice.saved']) == 1
    finally:
        await db.close()
