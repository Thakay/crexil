"""The audio adapters' hardening: a lost speech answer binds only its own take,
every refusal before generation reads in the adapter's plain words, a benched
engine says its bench's own cause, and a refused attempt that retries settles
its known zero charge.

ElevenLabs answers come from the synthetic `FakeElevenLabs` (built from the
public API reference, never recorded); the voice runner answers through a
scripted transport. No network, no GPU, no key.
"""

import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from app import elevenlabs, local_voice, wiring
from app.crexil_budget import accounting
from app.executors import DispatchRequest, EngineFailure, ProviderRecord
from app.local_voice import VoiceRunnerExecutor
from test_api_v4 import settle_turn, store
from test_audio_contract import booted_audio
from test_audio_guardrails import (DESCRIPTION, ELEVEN_DESIGN, ELEVEN_SFX, ELEVEN_SPEECH, SOUND, SPOKEN, accepted,
                                   boot, events, landed, prepare, slot_events, until)
from test_crexil import project
from test_crexil_execution import reviewed
from test_elevenlabs import (LINE, FakeElevenLabs, b64, designed, designed_voice, executor, history_item,  # noqa: F401
                             pcm, prices, receipt_of, refusal, rows, sound_envelope, sounded, speech_envelope)

pytestmark = pytest.mark.anyio

SPEECH = '/v1/text-to-speech/voice-1/with-timestamps'
NOT_CHARGED = 'Nothing was charged.'


class Record:
    """A stand-in for the server's record of one provider: what receipts bind,
    which other attempts are unsettled, and the saved voices it keeps."""

    def __init__(self, *, bound=(), unsettled=(), sent_at=None, voices=None):
        self.bound, self.unsettled, self.sent_at = set(bound), list(unsettled), sent_at
        self.voices, self.kept, self.reads = dict(voices or {}), [], 0
        self.on_read = None

    async def attempts(self):
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        return {'bound': set(self.bound), 'unsettled': list(self.unsettled), 'sent_at': self.sent_at}

    async def saved_voice(self, generated):
        return self.voices.get(generated)

    async def keep_saved_voice(self, generated, voice_id, *, found=False):
        self.kept.append((generated, voice_id, found))
        self.voices[generated] = voice_id

    def provider(self) -> ProviderRecord:
        return ProviderRecord(attempts=self.attempts, saved_voice=self.saved_voice,
                              keep_saved_voice=self.keep_saved_voice)


def request(env, record=None, *, slot_id='slot-1', outputs=None):
    async def on_output(output):
        if outputs is not None:
            outputs.append(output)

    return DispatchRequest(slot_id=slot_id, engine=env['engine'], external=env, payload='audio', verb=env['verb'],
                           prompt=env['text'], seed=env.get('seed'), on_output=on_output,
                           record=record.provider() if record else None)


async def attempt(ex, env, record=None, *, slot_id='slot-1'):
    """One attempt through start and wait; returns (result, outputs)."""
    outputs = []
    handle = await ex.start(request(env, record, slot_id=slot_id, outputs=outputs))
    return await ex.wait(handle), outputs


async def collect(ex, env, record=None, *, job_ref=None):
    """Free collection of an attempt sent a moment ago, as after a restart."""
    outputs = []
    slot = {'started_at': datetime.now(timezone.utc).isoformat(), 'job_ref': job_ref}
    return await ex.collect_existing(slot, request=request(env, record, outputs=outputs)), outputs


def saved_voice():
    return {**designed_voice(), 'voice_id': 'voice-1'}


def item(hid, **fields):
    """A history item ElevenLabs dates when it is listed, so after the send."""
    return {**history_item(hid), 'date_unix': int(time.time()), **fields}


def listing(*items):
    """A history answer listing `items` (history ids, or (id, fields) pairs), dated when asked."""
    def answer(_request):
        listed = [item(entry) if isinstance(entry, str) else item(entry[0], **entry[1]) for entry in items]
        return httpx.Response(200, json={'history': listed, 'has_more': False})
    return answer


def no_deletes(fake):
    return not [r for r in fake.requests if r.method == 'DELETE']


# --- speech recovery binds only the attempt's own take ---

@pytest.mark.parametrize('bound', ['req-hist-attempt-1', 'hist-attempt-1'], ids=['by request id', 'by history id'])
@pytest.mark.usefixtures('rows', 'prices')
async def test_a_lost_attempt_never_binds_a_take_another_attempts_receipt_holds(bound):
    # Attempt 1 of the line landed and its receipt holds its take. Attempt 2 is lost before
    # ElevenLabs generated it, so the only matching item in history is attempt 1's.
    fake = (FakeElevenLabs().on('POST', SPEECH, httpx.ReadError('dropped'))
            .on('GET', '/v1/history', listing('hist-attempt-1'))
            .on('GET', '/v1/history/hist-attempt-1/audio', httpx.Response(200, content=pcm())))
    record = Record(bound={bound})
    with pytest.raises(EngineFailure) as lost:
        await attempt(executor(fake), speech_envelope(saved_voice()), record, slot_id='slot-attempt-2')
    assert lost.value.kind == 'timeout' and lost.value.charged_usd is None       # unverified, never charged twice
    assert 'no take for this line' in str(lost.value) and 'Nothing was generated again' in str(lost.value)
    assert fake.calls('GET', '/v1/history/hist-attempt-1/audio') == []           # nothing fetched, nothing bound
    assert record.reads == elevenlabs.HISTORY_ASKS                               # the record is read on every ask
    assert len(fake.calls('POST', SPEECH)) == 1


async def test_a_lost_second_attempt_through_the_server_leaves_the_first_attempts_take_alone(tmp_path, monkeypatch):
    # Two runs of one line in one voice. The first lands; its receipt records its request id
    # (ElevenLabs sends no history id here). The second is lost, and history lists the first
    # attempt's take, dated as if the two had overlapped.
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
            .on('POST', '/v1/text-to-voice', httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', SPEECH, httpx.Response(200, json={'audio_base64': b64(pcm()), 'alignment': None},
                                               headers={'request-id': 'req-A'}), httpx.ReadError('dropped'))
            .on('GET', '/v1/history', listing(('hist-A', {'request_id': 'req-A'})))
            .on('GET', '/v1/history/hist-A/audio', httpx.Response(200, content=pcm())))
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        _, voices = await landed(client, await prepare(client, p['id'], 'voice-design', DESCRIPTION,
                                                       [(ELEVEN_DESIGN, 1, SPOKEN)]))
        line = [(ELEVEN_SPEECH, 1, {})]
        _, (take,) = await landed(client, await prepare(client, p['id'], 'speak', LINE, line, refs=[voices[0]['id']]))
        assert take['external']['request_id'] == 'req-A'
        lost = await settle_turn(await accepted(client, await prepare(client, p['id'], 'speak', LINE, line,
                                                                      refs=[voices[0]['id']])), until={'failed'})
        (slot,) = lost['slots']
        assert (slot['kind'], slot['charged_usd']) == ('timeout', None)
        assert 'no take for this line' in slot['detail']
        assert fake.calls('GET', '/v1/history/hist-A/audio') == []                 # the first take stays the first's
        counts = await accounting(store(), p['id'])
        assert counts['charged_usd'] == pytest.approx((len(SPOKEN['preview_text']) + len(LINE)) * 80 / 1e6)
        assert counts['unresolved_attempts'] == 1                                  # the lost one stays reserved
        assert no_deletes(fake)


@pytest.mark.usefixtures('rows', 'prices')
async def test_collecting_after_a_restart_never_looks_for_a_take_without_its_voice():
    # After a restart the envelope names only the designed preview; the record holds no save
    # and the account answers with no voice under its label. Another voice read the line.
    fake = (FakeElevenLabs().on('GET', '/v1/voices', httpx.Response(200, json={'voices': []}))
            .on('GET', '/v1/history', listing('hist-other-voice'))
            .on('GET', '/v1/history/hist-other-voice/audio', httpx.Response(200, content=pcm())))
    found, outputs = await collect(executor(fake), speech_envelope(designed_voice()), Record())
    assert found.kind == 'failed' and found.failure_kind == 'collection'
    assert found.cause == ('Crexil cannot find which ElevenLabs voice read this line, so this take cannot be '
                           'collected safely; nothing was generated again.')
    assert fake.calls('GET', '/v1/history') == [] and outputs == []                # never searched without a voice


@pytest.mark.usefixtures('rows', 'prices')
async def test_collecting_after_a_restart_finds_the_voice_by_its_label_and_binds_only_that_voice():
    labelled = {'voices': [{'voice_id': 'voice-9', 'name': 'Someone else'},
                           {'voice_id': 'voice-1', 'name': 'Renamed by the artist',
                            'labels': {'crexil_generated_voice_id': 'gen-0'}}]}
    fake = (FakeElevenLabs().on('GET', '/v1/voices', httpx.Response(200, json=labelled))
            .on('GET', '/v1/history', listing(('hist-other', {'voice_id': 'voice-OTHER'}), 'hist-mine'))
            .on('GET', '/v1/history/hist-mine/audio', httpx.Response(200, content=pcm())))
    record = Record()
    found, outputs = await collect(executor(fake), speech_envelope(designed_voice()), record)
    assert found.kind == 'landed'
    assert fake.calls('GET', '/v1/history')[0].url.params['voice_id'] == 'voice-1'
    assert receipt_of(outputs)[1]['recovered_from_history']['history_item_id'] == 'hist-mine'
    assert record.kept == [('gen-0', 'voice-1', True)]                              # kept the moment it is found
    assert no_deletes(fake)


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_lost_answer_with_a_request_id_binds_that_generation_and_records_its_id():
    # The speech call fails with a server error that names its request. History holds that
    # generation, and another unbound take of the same line. The read of the audio carries its
    # own request id, which is never mistaken for the generation's.
    fake = (FakeElevenLabs().on('POST', SPEECH, refusal(500, 'internal_error'))
            .on('GET', '/v1/history', listing('hist-theirs', ('hist-ours', {'request_id': 'req-refused'})))
            .on('GET', '/v1/history/hist-ours/audio', httpx.Response(200, content=pcm(),
                                                                     headers={'request-id': 'req-the-read'})))
    result, outputs = await attempt(executor(fake), speech_envelope(saved_voice()), Record())
    receipt, body = receipt_of(outputs)
    assert body['recovered_from_history']['history_item_id'] == 'hist-ours'
    assert receipt.external['request_id'] == receipt.external['generation_id'] == 'req-refused'
    assert receipt.external['history_item_id'] == 'hist-ours'
    assert result.external['request_id'] == 'req-refused' and result.charged_usd > 0
    assert fake.calls('GET', '/v1/history/hist-theirs/audio') == []


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_request_id_that_history_does_not_hold_binds_no_other_take():
    fake = (FakeElevenLabs().on('POST', SPEECH, refusal(500, 'internal_error'))
            .on('GET', '/v1/history', listing('hist-theirs')))
    with pytest.raises(EngineFailure) as lost:
        await attempt(executor(fake), speech_envelope(saved_voice()), Record())
    assert lost.value.kind == 'timeout' and lost.value.charged_usd is None and lost.value.job_ref == 'req-refused'
    assert "a take that matches this line, but none carries this request's id" in str(lost.value)
    assert not [r for r in fake.requests if r.url.path.endswith('/audio')]


@pytest.mark.usefixtures('rows', 'prices')
async def test_an_unsettled_attempt_of_the_same_line_makes_a_match_unattributable():
    # Another attempt of this line in this voice was sent before the matching take was made
    # and has not settled: the take may be its own, so none is bound.
    sibling = {'external': speech_envelope(saved_voice()), 'sent_at': time.time() - 1}
    fake = (FakeElevenLabs().on('POST', SPEECH, httpx.ReadError('dropped'))
            .on('GET', '/v1/history', listing('hist-x')))
    with pytest.raises(EngineFailure) as lost:
        await attempt(executor(fake), speech_envelope(saved_voice()), Record(unsettled=[sibling]))
    assert 'another attempt of the same line was out at the same time' in str(lost.value)
    assert lost.value.charged_usd is None and not [r for r in fake.requests if r.url.path.endswith('/audio')]


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_take_binds_once_the_sibling_that_shared_its_line_settles():
    # Ask 1: two unbound takes of the line, one of them a sibling's still in flight. By ask 2 the
    # sibling has landed and its receipt holds its take, so the one left is this attempt's own.
    sibling = {'external': speech_envelope(saved_voice()), 'sent_at': time.time() - 1}
    record = Record(unsettled=[sibling])

    def settle(rec):
        if rec.reads == 2:
            rec.unsettled, rec.bound = [], {'req-hist-sibling'}
    record.on_read = settle
    fake = (FakeElevenLabs().on('POST', SPEECH, httpx.ReadError('dropped'))
            .on('GET', '/v1/history', listing('hist-sibling', 'hist-ours'))
            .on('GET', '/v1/history/hist-ours/audio', httpx.Response(200, content=pcm())))
    _, outputs = await attempt(executor(fake), speech_envelope(saved_voice()), record)
    assert receipt_of(outputs)[1]['recovered_from_history']['history_item_id'] == 'hist-ours'
    assert record.reads == 2 and fake.calls('GET', '/v1/history/hist-sibling/audio') == []


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_sibling_in_another_voice_never_blocks_recovery():
    # A/B casting reads the same line in several voices at once (J2).
    other = speech_envelope({**designed_voice('gen-7'), 'voice_id': 'voice-OTHER'})
    fake = (FakeElevenLabs().on('POST', SPEECH, httpx.ReadError('dropped'))
            .on('GET', '/v1/history', listing('hist-ours'))
            .on('GET', '/v1/history/hist-ours/audio', httpx.Response(200, content=pcm())))
    record = Record(unsettled=[{'external': other, 'sent_at': time.time() - 1}])
    result, _ = await attempt(executor(fake), speech_envelope(saved_voice()), record)
    assert result.external['history_item_id'] == 'hist-ours'


@pytest.mark.usefixtures('rows', 'prices')
async def test_a_take_dated_before_the_attempt_was_sent_is_never_bound():
    old = {**history_item('hist-old'), 'date_unix': int(time.time()) - 60}
    fake = (FakeElevenLabs().on('POST', SPEECH, httpx.ReadError('dropped'))
            .on('GET', '/v1/history', httpx.Response(200, json={'history': [old]})))
    with pytest.raises(EngineFailure) as lost:
        await attempt(executor(fake), speech_envelope(saved_voice()), Record())
    assert 'no take for this line' in str(lost.value)
    assert int(fake.calls('GET', '/v1/history')[0].url.params['date_after_unix']) >= int(time.time()) - 5


# --- every refusal before generation reads in the adapter's plain words ---

REFUSED = {   # the answer, the verb it answers, the kind, and the primary line in full
    'redirect': (httpx.Response(307, headers={'location': 'https://elsewhere.test', 'request-id': 'req-refused'}),
                 'sfx', 'error', 'ElevenLabs answered with a redirect, which Crexil never follows. Nothing was charged. '
                                 'Check ELEVENLABS_URL in .env, then restart.'),
    'bad key': (refusal(401, 'invalid_api_key'), 'sfx', 'dead_key',
                'ElevenLabs turned the key away. Nothing was charged. Replace ELEVENLABS_API_KEY in .env, then restart.'),
    'quota used up': (refusal(401, 'quota_exceeded'), 'sfx', 'dead_key',
                      "ElevenLabs says this key's quota is used up. Nothing was charged. Raise the key's quota in the "
                      'ElevenLabs account, then resume ElevenLabs.'),
    'balance empty': (refusal(402, 'insufficient_credits'), 'sfx', 'dead_key',
                      'The ElevenLabs balance is empty. Nothing was charged. Add credit in the ElevenLabs account, '
                      'then resume ElevenLabs.'),
    'plan': (refusal(403, 'feature_not_available', message='Sound effects need a paid plan'), 'sfx', 'error',
             "This ElevenLabs plan or key cannot use this feature. Nothing was charged. Check the plan and the key's "
             'permissions in the ElevenLabs account, or choose another engine.'),
    'missing voice': (refusal(404, 'voice_not_found'), 'speak', 'error',
                      'ElevenLabs no longer has this voice; it may have been deleted from the account. Nothing was '
                      'charged. Choose another voice take, or design a new voice.'),
    'missing model': (refusal(404, 'model_not_found'), 'sfx', 'error',
                      "ElevenLabs does not know what this request names. Nothing was charged. Check this engine's "
                      'model in configs/engines.json.'),
    'invalid 400': (refusal(400, 'invalid_parameters', message='duration_seconds out of range'), 'sfx', 'error',
                    'ElevenLabs refused the request. Nothing was charged. Change what its details name, then prepare '
                    'the request again.'),
    'invalid 422': (refusal(422, detail=[{'loc': ['body', 'text'], 'msg': 'field required', 'type': 'missing'}]),
                    'sfx', 'error', 'ElevenLabs refused the request. Nothing was charged. Change what its details '
                                    'name, then prepare the request again.'),
    'content rules': (refusal(400, 'content_moderation', message='The text was flagged by moderation'), 'sfx',
                      'moderation', 'ElevenLabs refused this text under its content rules. Nothing was charged. Change '
                                    'the text, or choose another engine.'),
    'maintenance': (refusal(503, 'maintenance'), 'sfx', 'model_down',
                    'ElevenLabs is unavailable or under maintenance. Nothing was charged. Resume this engine once '
                    'ElevenLabs answers again.'),
}


@pytest.mark.parametrize('case', list(REFUSED))
@pytest.mark.usefixtures('rows', 'prices')
async def test_each_elevenlabs_refusal_is_worded_for_the_face(case):
    answer, verb, kind, words = REFUSED[case]
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer).on('POST', SPEECH, answer)
    with pytest.raises(EngineFailure) as refused:
        await attempt(executor(fake), sound_envelope() if verb == 'sfx' else speech_envelope(saved_voice()))
    failure = refused.value
    assert (failure.kind, str(failure), failure.plain) == (kind, words, True)
    assert failure.charged_usd == 0 and failure.job_ref == 'req-refused'
    assert str(answer.status_code) not in str(failure) and 'ElevenLabs said' not in str(failure)   # law 3
    assert answer.status_code == failure.status                                    # the code stays in the facts


@pytest.mark.parametrize('answer', [refusal(429, 'rate_limit_exceeded'), refusal(409, 'conflict')],
                         ids=['rate limit', 'preview in progress'])
@pytest.mark.usefixtures('rows', 'prices')
async def test_a_rate_limit_keeps_its_curated_line_that_counts_the_tries(answer):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer)
    with pytest.raises(EngineFailure) as refused:
        await attempt(executor(fake), sound_envelope())
    assert refused.value.kind == 'rate_limit' and not refused.value.plain and NOT_CHARGED in str(refused.value)


SERVER_REFUSED = ['redirect', 'invalid 400', 'content rules', 'maintenance']


@pytest.mark.parametrize('case', SERVER_REFUSED)
async def test_each_refusal_is_the_takes_primary_line_through_the_scheduler(tmp_path, monkeypatch, case):
    answer, _, kind, words = REFUSED[case]
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer)
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn = await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'sfx', 'Hammer on an anvil', [(ELEVEN_SFX, 1, SOUND)])), until={'failed'})
        (slot,) = turn['slots']
        assert (slot['cause'], slot['kind'], slot['charged_usd']) == (words, kind, 0)
        assert 'the engine failed' not in slot['cause']
        failed = slot_events(await events(client, workspace_id=p['id']), slot['id'], 'slot.failed')
        assert failed[-1]['payload']['cause'] == words                          # the record keeps the same line
        assert len(fake.calls('POST', '/v1/sound-generation')) == 1             # a refusal never retries
        if kind == 'model_down':                                                # its bench says the same words
            (bench,) = (await client.get('/api/queue')).json()['benches']
            assert (bench['scope'], bench['cause']) == ('engine:elevenlabs-sfx', words)


class ScriptedRunner(VoiceRunnerExecutor):
    """The real voice runner executor over a scripted runner."""

    def __init__(self, handler):
        super().__init__('http://runner.test')
        self._handler = handler

    async def open(self):
        self._client = httpx.AsyncClient(base_url=self._base, transport=httpx.MockTransport(self._handler))


def runner_refusing(status, detail):
    def handler(request):
        if request.method == 'POST' and request.url.path == '/jobs':
            return httpx.Response(status, json={'detail': detail})
        return httpx.Response(404, json={'detail': 'No such route.'})
    return handler


LONG = 'The voice description has 2500 characters; the runner takes at most 2000.'


@pytest.mark.parametrize('status, detail', [(400, 'The body is not JSON.'),
                                            (413, 'The body must be JSON of at most 32 MB.'),
                                            (422, LONG)])
async def test_the_voice_runners_refusal_is_worded_for_the_face(status, detail):
    ex = ScriptedRunner(runner_refusing(status, detail))
    await ex.open()
    env = {'transport': 'audio', 'provider': 'voice-runner', 'engine': 'qwen3-tts', 'verb': 'voice-design',
           'text': 'A' * 2500, 'controls': {'preview_text': 'Steel remembers.'}, 'takes': 3, 'seed': 7}
    try:
        with pytest.raises(EngineFailure) as refused:
            await ex.start(DispatchRequest(slot_id='slot-1', engine='qwen3-tts', external=env, payload='audio',
                                           verb='voice-design'))
    finally:
        await ex.close()
    assert str(refused.value) == (f'The local voice runner refused this take: {detail} Nothing was generated. '
                                  'Change what it names, then prepare the take again.')
    assert refused.value.plain and refused.value.status == status and refused.value.charged_usd == 0
    assert str(status) not in str(refused.value)


async def test_the_voice_runners_refusal_is_the_takes_primary_line_through_the_scheduler(tmp_path, monkeypatch):
    # A test-only copy of the local row without its text limits, so review lets the long
    # description through and the runner itself refuses it.
    from test_audio_contract import REPO
    matrix = json.loads((REPO / 'configs/engines.json').read_text())
    loose = dict(next(r for r in matrix['engines'] if r['id'] == 'qwen3-tts'), id='test-loose-voice',
                 name='Test loose voice')
    del loose['text_limits']
    runner = ScriptedRunner(runner_refusing(422, LONG))
    async with booted_audio(tmp_path, monkeypatch, rows=[loose], executors={'voice-runner': runner}) as (client, _):
        p = await project(client)
        turn = await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'voice-design', 'A' * 2500, [('audio:voice-design:test-loose-voice', 1, SPOKEN)])),
            until={'failed'})
        (slot,) = turn['slots']
        assert slot['cause'].startswith(f'The local voice runner refused this take: {LONG} Nothing was generated.')
        assert slot['kind'] == 'error' and 'the engine failed' not in slot['cause']
        assert (await client.get('/api/queue')).json()['benches'] == []           # one refused take benches nothing


async def test_review_refuses_a_line_longer_than_the_local_engine_takes(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        check = await reviewed(client, await prepare(client, p['id'], 'voice-design', 'A' * 2001,
                                                     [('audio:voice-design:qwen3-tts', 1, SPOKEN)]))
    assert not check['ready'] and fakes['voice-runner'].requests == []
    assert any('Qwen3-TTS 1.7B takes at most 2000 characters; this text has 2001.' in e for e in check['errors'])


# --- a benched engine gives its bench's own cause ---

async def test_a_benched_voice_runner_gives_its_start_command_as_the_reason(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        sched = wiring.current.scheduler
        await local_voice.startup_check(sched, sched.snapshot()['benches'])          # nothing answers at the URL
        (bench,) = (await client.get('/api/queue')).json()['benches']
        assert bench['scope'] == 'provider:voice-runner' and 'tools/tts/runner.py' in bench['cause']
        local = [o for o in (await client.get('/api/crexil/operations')).json() if o.get('provider') == 'voice-runner']
        others = [o for o in (await client.get('/api/crexil/operations')).json() if o.get('provider') == 'elevenlabs']
    assert local and all(o['availability'] == 'unavailable' and o['reasons'] == [bench['cause']] for o in local)
    assert others and all(o['reasons'] == [] for o in others)                      # a down runner holds only itself


# --- a refused attempt that retries settles its known zero ---

async def test_a_retry_waits_visibly_and_its_refused_attempt_stops_counting_against_caps(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation',
                               refusal(429, 'rate_limit_exceeded', headers={'retry-after-ms': '400'}), sounded())
    async with boot(tmp_path, monkeypatch, fake) as (client, _):
        p = await project(client)
        turn_id = await accepted(client, await prepare(client, p['id'], 'sfx', 'Hammer on an anvil',
                                                       [(ELEVEN_SFX, 1, SOUND)]))

        async def waiting():
            return (await client.get('/api/queue')).json()['retries']

        (wait,) = await until(waiting)
        assert (wait['kind'], wait['attempt'], wait['window']) == ('rate_limit', 2, 'provider')    # visible (law 9)
        # The refused try settled at zero: only the waiting retry is reserved, once.
        assert (await accounting(store(), p['id']))['reserved_usd'] == pytest.approx(.003)
        (slot,) = (await settle_turn(turn_id, until={'landed'}))['slots']
        (retry,) = [e for e in await store().slot_events(slot['id']) if e['type'] == 'slot.retrying']
        assert retry['charged_usd'] == 0 and retry['payload']['attempt'] == 1
        counts = await accounting(store(), p['id'])
        assert (counts['charged_usd'], counts['reserved_usd']) == (pytest.approx(.003), 0)


async def test_an_unreachable_elevenlabs_retries_once_on_the_cockpits_clock_and_lands(tmp_path, monkeypatch):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', httpx.ConnectError('refused'), sounded())
    async with boot(tmp_path, monkeypatch, fake, RETRY_BACKOFF_S=0.05) as (client, _):
        p = await project(client)
        (slot,) = (await settle_turn(await accepted(client, await prepare(
            client, p['id'], 'sfx', 'Hammer on an anvil', [(ELEVEN_SFX, 1, SOUND)])), until={'landed'}))['slots']
        (retry,) = [e for e in await store().slot_events(slot['id']) if e['type'] == 'slot.retrying']
        assert (retry['payload']['kind'], retry['payload']['window'], retry['charged_usd']) == ('unreachable', 'cockpit', 0)
        assert (slot['attempt'], slot['retry_of']) == (2, 'unreachable')
        assert (await client.get('/api/queue')).json()['benches'] == []          # a retry that lands benches nothing
