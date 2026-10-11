"""The ElevenLabs adapter against a synthetic ElevenLabs.

Every answer here is synthetic: built from the request and response shapes in
ElevenLabs' public API reference, never recorded from a live account. Nothing in
this file reaches the network, except `test_live_sound_effect_round_trip`, which
runs only when LIVE_ELEVENLABS=1 and a real key are set.
"""

import asyncio
import base64
import hashlib
import json
import math
import os
import shutil
import struct
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import audio, audio_executors, config, crexil_audio, crexil_budget, elevenlabs
from app.elevenlabs import ElevenLabsExecutor
from app.executors import DispatchRequest, EngineFailure, SourceImage
from fakes import tone_wav
from hermetic import EXPORTED

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
BASE = 'https://api.elevenlabs.test'
KEY = 'test-elevenlabs-key-0123456789'
DESCRIPTION = 'A gruff dwarf blacksmith, low and warm, with a smile under the soot'
PREVIEW = 'Steel remembers every strike, and so do I. Bring me iron and patience, and I will bring you a blade.'
LINE = 'Steel remembers every strike. So do I.'


def pcm(seconds=0.5, rate=24000, pitch=220.0) -> bytes:
    """Raw 16-bit little-endian mono PCM, as ElevenLabs sends speech and Voice
    Design for `pcm_*`."""
    return b''.join(struct.pack('<h', int(0.3 * 32767 * math.sin(2 * math.pi * pitch * i / rate)))
                    for i in range(int(seconds * rate)))


# A channel of a stereo answer: (amplitude, pitch in Hz), or None for silence.
LEFT, RIGHT = (0.3, 440.0), (0.15, 660.0)


def stereo(seconds, rate=48000, left=LEFT, right=RIGHT) -> bytes:
    """Raw 16-bit little-endian stereo PCM, its channels interleaved L, R, L, R,
    as ElevenLabs sends sound effects for `pcm_48000`. Each sounding channel is
    the tone `fakes.tone_wav` makes with the same amplitude and pitch."""
    frames = round(seconds * rate)

    def channel(tone):
        if tone is None:
            return [0] * frames
        amplitude, pitch = tone
        return [int(amplitude * 32767 * math.sin(2 * math.pi * pitch * i / rate)) for i in range(frames)]

    interleaved = [sample for pair in zip(channel(left), channel(right), strict=True) for sample in pair]
    return struct.pack(f'<{len(interleaved)}h', *interleaved)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FakeElevenLabs:
    """A scripted ElevenLabs behind httpx.MockTransport. Each route answers from
    its queue (the last answer repeats); an exception in the queue is raised as
    the transport's own failure."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method, path, *answers):
        self.routes.setdefault((method, path), []).extend(answers)
        return self

    def calls(self, method, path):
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={'detail': {'type': 'not_found', 'code': 'not_found',
                                                        'message': f'no route {request.url.path}'}})
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(answer) and not isinstance(answer, httpx.Response):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        return answer


def designed(count=3, *, rid='req-design'):
    return httpx.Response(200, json={
        'previews': [{'audio_base_64': b64(pcm(pitch=180 + 40 * i)), 'generated_voice_id': f'gen-{i}',
                      'media_type': 'audio/pcm', 'duration_secs': 0.5, 'language': 'en'} for i in range(count)],
        'text': PREVIEW}, headers={'request-id': rid, 'character-cost': str(len(PREVIEW)),
                                   'current-concurrent-requests': '1', 'maximum-concurrent-requests': '5'})


ALIGNMENT = {'characters': list(LINE[:3]), 'character_start_times_seconds': [0.0, 0.05, 0.1],
             'character_end_times_seconds': [0.05, 0.1, 0.15]}


def spoken(*, rid='req-tts'):
    return httpx.Response(200, json={'audio_base64': b64(pcm()), 'alignment': ALIGNMENT,
                                     'normalized_alignment': ALIGNMENT},
                          headers={'request-id': rid, 'history-item-id': 'hist-1', 'character-cost': str(len(LINE))})


def sounded(left=LEFT, right=RIGHT):
    """Sound Effects as the live account answered `pcm_48000`: interleaved stereo
    PCM lasting the duration the request asked for, its credits in
    `character-cost`, and no request or history id."""
    def answer(request):
        seconds = json.loads(request.content)['duration_seconds']
        return httpx.Response(200, content=stereo(seconds, left=left, right=right), headers={
            'content-type': 'audio/pcm', 'character-cost': str(math.ceil(11 * seconds)), 'x-trace-id': 'trace-sfx'})
    return answer


class Prices:
    """A stand-in for the budget's arithmetic: exact published-tariff prices,
    with every call recorded, so the adapter's use of it can be checked."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, tariff, usage):
        self.calls.append((tariff, dict(usage)))
        if self.fail:
            raise crexil_budget.UnknownPrice(self.fail)
        per = {'1k_characters': ('characters', 1000), 'second': ('seconds', 1)}[tariff['unit']]
        exact = Decimal(str(usage[per[0]])) * tariff['micro_usd'] / per[1]
        return int(exact.to_integral_value(rounding=ROUND_CEILING))


@pytest.fixture
def rows(tmp_path, monkeypatch):
    """The real ElevenLabs rows, enabled, in a throwaway configs folder."""
    configs = tmp_path / 'configs'
    shutil.copytree(REPO / 'configs', configs)
    matrix = json.loads((configs / 'engines.json').read_text())
    for row in matrix['engines']:
        if row.get('provider') == 'elevenlabs':
            row['enabled'] = True
    (configs / 'engines.json').write_text(json.dumps(matrix))
    monkeypatch.setattr(config, 'CONFIGS_DIR', configs)
    monkeypatch.setattr(config, 'ELEVENLABS_URL', BASE)
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', KEY)
    monkeypatch.setattr(elevenlabs, 'HISTORY_GAP_S', 0.0)
    return configs


@pytest.fixture
def prices(monkeypatch):
    spy = Prices()
    monkeypatch.setattr(crexil_budget, 'audio_reservation', spy)
    return spy


def envelope(engine, verb, text, controls, *, seed=None, voice=None):
    """A frozen envelope as review compiles it, from the engine's own row."""
    row = audio.engines()[engine]
    tariff = crexil_audio.tariff(row, verb)
    return {'transport': 'audio', 'provider': 'elevenlabs', 'engine': engine, 'verb': verb,
            'model_id': row['models'][verb], 'text': text, 'controls': dict(controls),
            'fixed': dict(row['fixed'][verb]), 'takes': crexil_audio.takes(row, verb),
            'pcm_channels': crexil_audio.pcm_channels(row, verb), 'voice': voice,
            'tariff': tariff, 'measures': crexil_audio.measures(tariff, text, controls), 'reserved_micro_usd': 0,
            'destination': BASE, 'engine_revision': 'rev-1', 'seed': seed}


def design_envelope(**fields):
    return envelope('elevenlabs-voice-design', 'voice-design', DESCRIPTION,
                    {'preview_text': PREVIEW, 'loudness': 0.5}, seed=fields.pop('seed', 123), **fields)


def designed_voice(gid='gen-0'):
    return {'source_node_id': 'node-voice', 'sha256': 'unchecked', 'text': PREVIEW,
            'recorded': {'provider': 'elevenlabs', 'kind': 'designed', 'generated_voice_id': gid,
                         'description': DESCRIPTION, 'model_id': 'eleven_multilingual_ttv_v2'}}


def speech_envelope(voice=None, **fields):
    return envelope('elevenlabs-v4', 'speak', LINE, {'stability': 0.5, 'similarity_boost': 0.75},
                    seed=fields.pop('seed', 42), voice=voice or designed_voice(), **fields)


def sound_envelope(**fields):
    return envelope('elevenlabs-sfx', 'sfx', 'Hammer on an anvil, one ringing strike',
                    {'duration_seconds': 2.0, 'prompt_influence': 0.3, 'loop': False}, **fields)


def executor(fake: FakeElevenLabs) -> ElevenLabsExecutor:
    return ElevenLabsExecutor(transport=httpx.MockTransport(fake))


async def run(ex, env, *, sources=(), before_send=None, slot_id='slot-1'):
    """One attempt through start and wait; returns (result, outputs, sends)."""
    outputs, sends = [], []

    async def on_output(output):
        outputs.append(output)

    async def sending():
        sends.append(env['verb'])
        if before_send is not None:
            await before_send()

    req = DispatchRequest(slot_id=slot_id, engine=env['engine'], external=env, payload='audio', verb=env['verb'],
                          prompt=env['text'], seed=env.get('seed'), sources=tuple(sources), on_output=on_output,
                          before_send=sending)
    handle = await ex.start(req)
    return await ex.wait(handle), outputs, sends


def receipt_of(outputs):
    receipt = next(o for o in outputs if o.role == 'provider receipt')
    return receipt, json.loads(receipt.data)


async def test_the_label_a_save_gives_a_voice_fits_elevenlabs_key_limit():
    """ElevenLabs refuses a label key longer than 20 characters (400 invalid_labels)."""
    assert len(elevenlabs.LABEL_KEY) <= 20


# --- Voice Design ---

async def test_voice_design_sends_its_frozen_request_and_lands_three_designed_takes(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
    result, outputs, sends = await run(executor(fake), design_envelope())
    (request,) = fake.requests
    assert sends == ['voice-design']                       # the dispatch guard ran once, before sending
    assert request.headers['xi-api-key'] == KEY and dict(request.url.params) == {'output_format': 'pcm_24000'}
    assert json.loads(request.content) == {'voice_description': DESCRIPTION, 'model_id': 'eleven_ttv_v3',
                                           'text': PREVIEW, 'auto_generate_text': False, 'loudness': 0.5, 'seed': 123}
    assert [o.identity for o in outputs] == ['provider:receipt', 'take:0', 'take:1', 'take:2']
    for index, take in enumerate(outputs[1:]):
        facts = audio.inspect(take.data)
        assert take.payload == 'audio' and take.filename == f'take-{index + 1}.wav'
        assert facts['sample_rate'] == 24000 and facts['channels'] == 1 and facts['duration_s'] == 0.5
        assert take.descriptor['audio']['text'] == PREVIEW
        assert take.descriptor['audio']['voice'] == {
            'provider': 'elevenlabs', 'kind': 'designed', 'generated_voice_id': f'gen-{index}',
            'description': DESCRIPTION, 'model_id': 'eleven_ttv_v3'}
    expected = math.ceil(len(PREVIEW) * 80000 / 1000)       # list price of the preview text, once per attempt
    assert prices.calls == [(design_envelope()['tariff'], {'characters': len(PREVIEW)})]
    assert result.primary_identity == 'take:0' and result.payload == 'audio'
    assert result.charged_usd == pytest.approx(expected / 1e6)
    receipt, body = receipt_of(outputs)
    assert receipt.external['reported_cost_usd'] == pytest.approx(expected / 1e6)
    assert receipt.external['generation_id'] == 'req-design'
    assert body['charge']['charged_micro_usd'] == expected and body['charge']['provider_character_cost'] == str(len(PREVIEW))
    assert body['request']['body']['text'] == PREVIEW
    assert body['response']['headers']['maximum-concurrent-requests'] == '5'
    assert [p['generated_voice_id'] for p in body['previews']] == ['gen-0', 'gen-1', 'gen-2']
    assert KEY not in receipt.data.decode() and all(KEY not in json.dumps(o.descriptor) for o in outputs)


async def test_voice_design_never_lets_elevenlabs_choose_or_rewrite_the_text(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
    risky = design_envelope()
    risky['fixed'].update(auto_generate_text=True, should_enhance=True)
    await run(executor(fake), risky)
    sent = json.loads(fake.requests[0].content)
    assert sent['auto_generate_text'] is False and 'should_enhance' not in sent
    missing = design_envelope()
    del missing['controls']['preview_text']
    fake.requests.clear()
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), missing)
    assert failed.value.kind == 'preflight' and failed.value.charged_usd == 0 and fake.requests == []


async def test_a_seed_beyond_elevenlabs_range_is_sent_reduced_and_recorded(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
    big = 2 ** 40 + 7
    _, outputs, _ = await run(executor(fake), design_envelope(seed=big))
    assert json.loads(fake.requests[0].content)['seed'] == big % 2 ** 32
    assert receipt_of(outputs)[1]['request']['body']['seed'] == big % 2 ** 32


def test_voice_design_takes_only_the_seeds_its_v3_model_accepts():
    # eleven_ttv_v3 answers 422 to a seed above 2147483647: the row's range ends there, so freeze draws
    # inside it and review refuses a fixed seed past it.
    matrix = json.loads((REPO / 'configs' / 'engines.json').read_text())
    row = next(r for r in matrix['engines'] if r['id'] == 'elevenlabs-voice-design')
    assert crexil_audio.seed_range(row) == (0, 2 ** 31 - 1)
    crexil_audio.check_seeds(row, 2 ** 31 - 1, 1)
    with pytest.raises(ValueError, match='takes seeds from 0 to 2147483647'):
        crexil_audio.check_seeds(row, 2 ** 31, 1)


async def test_an_empty_design_answer_is_a_charged_blank_with_its_receipt(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed(0))
    outputs = []
    with pytest.raises(EngineFailure) as failed:
        _, outputs, _ = await run(executor(fake), design_envelope())
    assert failed.value.kind == 'charged_blank' and failed.value.job_ref == 'req-design'
    assert failed.value.charged_usd == pytest.approx(math.ceil(len(PREVIEW) * 80) / 1e6)


# --- speech ---

async def test_speech_saves_a_designed_preview_once_and_reads_with_its_seed(rows, prices):
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice', httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', '/v1/text-to-speech/voice-1/with-timestamps', spoken()))
    ex = executor(fake)
    (first, outputs, _), (second, _, _) = await asyncio.gather(
        run(ex, speech_envelope(), slot_id='slot-1'), run(ex, speech_envelope(), slot_id='slot-2'))
    (save,) = fake.calls('POST', '/v1/text-to-voice')
    assert json.loads(save.content) == {
        'voice_name': 'Crexil gen-0', 'voice_description': DESCRIPTION, 'generated_voice_id': 'gen-0',
        'labels': {'source': 'crexil', 'crexil_gen_voice_id': 'gen-0'}}
    reads = fake.calls('POST', '/v1/text-to-speech/voice-1/with-timestamps')
    assert len(reads) == 2
    for read in reads:
        assert dict(read.url.params) == {'output_format': 'pcm_24000', 'enable_logging': 'true'}
        assert json.loads(read.content) == {'text': LINE, 'model_id': 'eleven_v4', 'seed': 42,
                                            'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}}
    take = outputs[1]
    assert take.descriptor['audio'] == {'text': LINE, 'alignment': ALIGNMENT, 'voice': {
        'provider': 'elevenlabs', 'kind': 'saved', 'voice_id': 'voice-1', 'from_generated_voice_id': 'gen-0'}}
    # Speech answers mono PCM, so it stays one channel at its own length.
    facts = audio.inspect(take.data)
    assert (facts['channels'], facts['sample_rate'], facts['duration_s']) == (1, 24000, 0.5)
    assert first.charged_usd == second.charged_usd == pytest.approx(math.ceil(len(LINE) * 80) / 1e6)
    assert receipt_of(outputs)[0].external['history_item_id'] == 'hist-1'


async def test_speech_with_a_known_voice_never_saves_again(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-speech/voice-9/with-timestamps', spoken())
    await run(executor(fake), speech_envelope({**designed_voice(), 'voice_id': 'voice-9'}))
    saved = {**designed_voice(), 'recorded': {'provider': 'elevenlabs', 'kind': 'saved', 'voice_id': 'voice-9'}}
    await run(executor(fake), speech_envelope(saved))
    assert fake.calls('POST', '/v1/text-to-voice') == [] and len(fake.requests) == 2


async def test_speech_checks_its_voice_take_bytes_before_sending(rows, prices, tmp_path):
    fake = FakeElevenLabs()
    take = tmp_path / 'voice.wav'
    take.write_bytes(b'RIFF-the-voice')
    changed = SourceImage(path=str(take), sha256=hashlib.sha256(b'other').hexdigest(), name='voice.wav')
    missing = SourceImage(path=str(tmp_path / 'gone.wav'), sha256='0' * 64, name='gone.wav')
    for source, kind in ((changed, 'source_changed'), (missing, 'source_missing')):
        with pytest.raises(EngineFailure) as failed:
            await run(executor(fake), speech_envelope(), sources=[source])
        assert failed.value.kind == kind and failed.value.charged_usd == 0
    local = {**designed_voice(), 'recorded': {'provider': 'voice-runner', 'kind': 'cloned'}}
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), speech_envelope(local))
    assert failed.value.kind == 'preflight' and 'designed or saved at ElevenLabs' in str(failed.value)
    assert fake.requests == []


# --- sound effects ---

async def test_a_stereo_sound_answer_lands_at_its_requested_length(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', sounded())
    env = sound_envelope()
    assert env['pcm_channels'] == 2                          # the row's fact, frozen beside `fixed`, never sent
    result, outputs, _ = await run(executor(fake), env)
    (request,) = fake.requests
    assert dict(request.url.params) == {'output_format': 'pcm_48000'}
    assert json.loads(request.content) == {'text': 'Hammer on an anvil, one ringing strike',
                                           'model_id': 'eleven_text_to_sound_v2', 'duration_seconds': 2.0,
                                           'prompt_influence': 0.3, 'loop': False}
    # Two interleaved channels, so the take lasts the 2.0 s asked for, not twice that an octave low.
    facts = audio.inspect(outputs[1].data)
    assert (facts['channels'], facts['sample_rate'], facts['duration_s']) == (2, 48000, 2.0)
    assert result.data == outputs[1].data and result.data[44:] == stereo(2.0)    # the samples as delivered
    assert outputs[1].descriptor['audio'] == {'text': None, 'voice': None, 'alignment': None}
    assert prices.calls[-1][1] == {'seconds': 2.0} and result.charged_usd == pytest.approx(0.004)
    assert receipt_of(outputs)[1]['charge']['provider_character_cost'] == '22'


# A 1 kHz tone at half scale in one channel or both: its mono twin is `tone_wav`'s.
TONE = (0.5, 1000.0)


@pytest.mark.parametrize('left, right, gain', [(TONE, None, 0.0), (None, TONE, 0.0), (TONE, TONE, 3.01)],
                         ids=['left only', 'right only', 'both'])
async def test_a_stereo_takes_loudness_and_peaks_are_measured_over_both_channels(rows, prices, left, right, gain):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', sounded(left=left, right=right))
    result, _, _ = await run(executor(fake), sound_envelope())
    facts = audio.inspect(result.data)
    mono = audio.inspect(tone_wav(seconds=2.0, rate=48000, pitch=1000.0, amplitude=0.5))
    assert (facts['channels'], facts['duration_s']) == (2, 2.0)
    # Each waveform bin spans the same instants in both channels and keeps the louder;
    # loudness sums the channels' power (BS.1770 weighs left and right 1.0).
    assert facts['peaks'] == mono['peaks'] and len(facts['peaks']) == audio.PEAK_BINS
    assert facts['peak_dbfs'] == mono['peak_dbfs']
    assert facts['loudness_lufs'] == pytest.approx(mono['loudness_lufs'] + gain, abs=0.02)


@pytest.mark.parametrize('raw', [b'\x01\x02\x03', b'\x01\x02\x03\x04\x05\x06'], ids=['half a sample', 'half a frame'])
async def test_audio_that_is_not_whole_pcm_is_kept_as_delivered(rows, prices, raw):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', httpx.Response(200, content=raw))
    result, outputs, _ = await run(executor(fake), sound_envelope())
    assert result.filename == 'take-1.raw' and result.data == raw and outputs[1].payload == 'audio'


# --- refusals and failures ---

def refusal(status, code='', *, kind='', message='', headers=None, detail=None):
    body = {'detail': detail if detail is not None else {'type': kind, 'code': code, 'message': message}}
    return httpx.Response(status, json=body, headers={'request-id': 'req-refused', **(headers or {})})


@pytest.mark.parametrize('answer, kind, retry_after', [
    (refusal(401, 'invalid_api_key'), 'dead_key', None),
    (refusal(401, 'quota_exceeded'), 'dead_key', None),
    (refusal(402, 'insufficient_credits'), 'dead_key', None),
    (refusal(403, 'feature_not_available', message='Sound effects need a paid plan'), 'error', None),
    (refusal(404, 'voice_not_found'), 'error', None),
    (refusal(409, 'conflict', headers={'retry-after': '3'}), 'rate_limit', 3.0),
    (refusal(422, detail=[{'loc': ['body', 'text'], 'msg': 'field required', 'type': 'missing'}]), 'error', None),
    (refusal(429, 'rate_limit_exceeded', headers={'Retry-After': '7'}), 'rate_limit', 7.0),
    (refusal(429, 'concurrent_limit_exceeded', headers={'retry-after-ms': '1500'}), 'rate_limit', 1.5),
    (refusal(400, 'content_moderation', message='The text was flagged by moderation'), 'moderation', None),
    (refusal(503, 'maintenance'), 'model_down', None),
])
async def test_refusals_map_to_failure_kinds_and_charge_nothing(rows, prices, answer, kind, retry_after):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', answer)
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), sound_envelope())
    assert failed.value.kind == kind and failed.value.charged_usd == 0
    assert failed.value.retry_after_s == retry_after and failed.value.job_ref == 'req-refused'
    assert failed.value.status == answer.status_code and 'Nothing was charged.' in str(failed.value)
    assert len(fake.requests) == 1                           # nothing retries here (law 9)


async def test_a_plan_refusal_is_never_moderation(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', refusal(403, 'subscription_required'))
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), design_envelope())
    assert failed.value.kind == 'error' and 'cannot use this feature' in str(failed.value)


async def test_an_unreachable_elevenlabs_sends_and_charges_nothing(rows, prices):
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', httpx.ConnectError('refused'))
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), sound_envelope())
    assert failed.value.kind == 'unreachable' and failed.value.charged_usd == 0
    assert 'Nothing was sent.' in str(failed.value)


@pytest.mark.parametrize('lost', [httpx.ReadTimeout('slow'), httpx.RemoteProtocolError('dropped'),
                                  refusal(500, 'internal_error')])
async def test_a_lost_design_or_sound_stays_unverified_and_cannot_be_collected(rows, prices, lost):
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', lost)
            .on('POST', '/v1/sound-generation', lost))
    ex = executor(fake)
    for env in (design_envelope(), sound_envelope()):
        with pytest.raises(EngineFailure) as failed:
            await run(ex, env)
        assert failed.value.kind == 'timeout' and failed.value.charged_usd is None
        assert 'cannot be collected' in str(failed.value) and 'unverified' in str(failed.value)
        req = DispatchRequest(slot_id='slot-1', engine=env['engine'], external=env, payload='audio')
        found = await ex.collect_existing({'started_at': None}, request=req)
        assert found.kind == 'failed' and 'keeps no record' in found.cause
    assert len(fake.requests) == 2                           # one call each, nothing generated again


# --- history ---

def history_item(hid, *, text=LINE, voice_id='voice-1', date_unix=None, stability=0.5):
    return {'history_item_id': hid, 'request_id': f'req-{hid}', 'voice_id': voice_id, 'model_id': 'eleven_v4',
            'text': text, 'date_unix': date_unix or int(datetime.now(timezone.utc).timestamp()),
            'character_count_change_from': 1000, 'character_count_change_to': 1000 + len(text),
            'settings': {'stability': stability, 'similarity_boost': 0.75}, 'source': 'TTS',
            'output_format': 'pcm_24000', 'state': 'created', 'alignments': ALIGNMENT}


async def test_lost_speech_is_found_in_history_without_generating_again(rows, prices):
    listed = {'history': [history_item('hist-other', text='Another line'), history_item('hist-7'),
                          history_item('hist-unsteady', stability=0.9)], 'has_more': False}
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-speech/voice-1/with-timestamps', httpx.ReadTimeout('lost'))
            .on('GET', '/v1/history', httpx.Response(200, json={'history': [], 'has_more': False}),
                httpx.Response(200, json=listed))
            .on('GET', '/v1/history/hist-7/audio', httpx.Response(200, content=pcm())))
    result, outputs, _ = await run(executor(fake), speech_envelope({**designed_voice(), 'voice_id': 'voice-1'}))
    assert len(fake.calls('POST', '/v1/text-to-speech/voice-1/with-timestamps')) == 1
    lookup = fake.calls('GET', '/v1/history')[0]
    assert lookup.url.params['voice_id'] == 'voice-1' and lookup.url.params['model_id'] == 'eleven_v4'
    assert lookup.url.params['source'] == 'TTS' and lookup.url.params['sort_direction'] == 'asc'
    assert len(fake.calls('GET', '/v1/history')) == 2           # asked again while history caught up
    receipt, body = receipt_of(outputs)
    assert body['recovered_from_history']['history_item_id'] == 'hist-7'
    assert body['recovered_from_history']['generated_again'] is False
    assert body['recovered_from_history']['provider_character_count'] == len(LINE)
    assert body['request']['path'] == '/v1/text-to-speech/voice-1/with-timestamps'
    assert receipt.external['request_id'] == 'req-hist-7'
    assert audio.inspect(result.data)['sample_rate'] == 24000 and result.descriptor['audio']['alignment'] == ALIGNMENT
    assert result.charged_usd == pytest.approx(math.ceil(len(LINE) * 80) / 1e6)


async def test_ambiguous_history_binds_nothing_and_leaves_the_charge_unverified(rows, prices):
    listed = {'history': [history_item('hist-1'), history_item('hist-2')], 'has_more': False}
    fake = (FakeElevenLabs().on('POST', '/v1/text-to-speech/voice-1/with-timestamps', refusal(500, 'internal_error'))
            .on('GET', '/v1/history', httpx.Response(200, json=listed)))
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), speech_envelope({**designed_voice(), 'voice_id': 'voice-1'}))
    assert failed.value.kind == 'timeout' and failed.value.charged_usd is None
    assert '2 takes that match this line' in str(failed.value)
    assert not [r for r in fake.requests if r.url.path.endswith('/audio')]


async def test_collecting_lost_speech_after_a_restart_lands_it_from_history(rows, prices):
    fake = (FakeElevenLabs().on('GET', '/v1/history', httpx.Response(200, json={'history': [history_item('hist-3')]}))
            .on('GET', '/v1/history/hist-3/audio', httpx.Response(200, content=pcm())))
    landed = []

    async def on_output(output):
        landed.append(output)

    env = speech_envelope({**designed_voice(), 'voice_id': 'voice-1'})
    req = DispatchRequest(slot_id='slot-1', engine=env['engine'], external=env, payload='audio', verb='speak',
                          on_output=on_output)
    started = datetime.now(timezone.utc).isoformat()
    found = await executor(fake).collect_existing({'started_at': started}, request=req)
    assert found.kind == 'landed' and [o.identity for o in landed] == ['provider:receipt', 'take:0']
    assert [r.method for r in fake.requests] == ['GET', 'GET']   # reads only; nothing generated again


# --- before anything is sent ---

def edit_row(configs, engine, **changes):
    path = configs / 'engines.json'
    matrix = json.loads(path.read_text())
    for row in matrix['engines']:
        if row['id'] == engine:
            row.update(changes)
    path.write_text(json.dumps(matrix))
    os.utime(path, ns=(1, 1))      # a new mtime, so the rows are read again


@pytest.mark.parametrize('case', ['no key', 'destination', 'tariff', 'disabled', 'format', 'guard'])
async def test_preflight_refuses_in_plain_words_and_sends_nothing(rows, prices, monkeypatch, case):
    fake = FakeElevenLabs()
    env, guard = sound_envelope(), None
    if case == 'no key':
        monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', '')
    elif case == 'destination':
        env['destination'] = 'https://elsewhere.test'
    elif case == 'tariff':
        edit_row(rows, 'elevenlabs-sfx', tariffs={'sfx': {**env['tariff'], 'micro_usd': 3000}})
    elif case == 'disabled':
        edit_row(rows, 'elevenlabs-sfx', enabled=False)
    elif case == 'format':
        env['fixed']['output_format'] = 'mp3_44100_128'
    else:
        async def guard():
            raise ValueError('The run limit is reached. Nothing was sent.')
    with pytest.raises(EngineFailure) as failed:
        await run(executor(fake), env, before_send=guard)
    assert failed.value.kind == 'preflight' and failed.value.charged_usd == 0
    assert 'Nothing was sent.' in str(failed.value) and fake.requests == []


async def test_an_unpriceable_usage_reports_no_charge_and_says_why(rows, monkeypatch):
    monkeypatch.setattr(crexil_budget, 'audio_reservation', Prices(fail='No published price.'))
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', sounded())
    result, outputs, _ = await run(executor(fake), sound_envelope())
    receipt, body = receipt_of(outputs)
    assert result.charged_usd is None and receipt.external['reported_cost_usd'] is None
    assert body['charge']['unpriced'] == 'No published price.'


# --- registration, the setup line and the startup check ---

def test_the_adapter_registers_as_the_elevenlabs_executor():
    built = {e.name: e for e in audio_executors.build()}
    assert isinstance(built['elevenlabs'], ElevenLabsExecutor) and built['elevenlabs'].lane == 'external'
    assert built['elevenlabs'].max_concurrency == 2 and audio_executors.ready(built['elevenlabs'])
    assert audio_executors.adapter('elevenlabs') is elevenlabs


def stack_with(ex):
    return SimpleNamespace(scheduler=SimpleNamespace(executor=lambda name: ex if name == 'elevenlabs' else None))


async def test_the_setup_line_reads_the_account_and_never_shows_the_key(rows):
    fake = FakeElevenLabs().on('GET', '/v1/user/subscription', httpx.Response(200, json={
        'tier': 'creator', 'voice_slots_used': 3, 'voice_limit': 30, 'character_count': 10, 'character_limit': 100}))
    ex = executor(fake)
    line = await elevenlabs.connection(stack_with(ex))
    again = await elevenlabs.connection(stack_with(ex))
    assert line == again and line['ok'] is True and line['key_source'] == 'env'
    assert 'Plan: creator.' in line['detail'] and 'Voice slots: 3 of 30 used.' in line['detail']
    assert KEY not in json.dumps(line) and len(fake.requests) == 1     # read once, then kept a minute
    turned = executor(FakeElevenLabs().on('GET', '/v1/user/subscription', refusal(401, 'invalid_api_key')))
    assert 'turned the key away' in (await elevenlabs.connection(stack_with(turned)))['detail']
    away = executor(FakeElevenLabs().on('GET', '/v1/user/subscription', httpx.ConnectError('down')))
    assert (await elevenlabs.connection(stack_with(away)))['ok'] is None
    assert (await elevenlabs.connection(stack_with(object())))['detail'].endswith('It has not been checked.')


class Bench:
    def __init__(self, ex):
        self.ex, self.benched, self.unbenched = ex, [], []

    def executor(self, name):
        return self.ex if name == 'elevenlabs' else None

    async def bench(self, scope, *, kind, cause):
        self.benched.append((scope, kind))

    async def unbench(self, scope):
        self.unbenched.append(scope)


async def test_the_startup_check_benches_elevenlabs_alone(rows, monkeypatch):
    ok = executor(FakeElevenLabs().on('GET', '/v1/user/subscription', httpx.Response(200, json={'tier': 'pro'})))
    rejected = executor(FakeElevenLabs().on('GET', '/v1/user/subscription', refusal(401, 'invalid_api_key')))
    healed = Bench(ok)
    await elevenlabs.startup_check(healed, {'provider:elevenlabs': {'kind': 'dead_key', 'cause': 'earlier'}})
    assert healed.unbenched == ['provider:elevenlabs'] and healed.benched == []
    turned = Bench(rejected)
    await elevenlabs.startup_check(turned, {})
    assert turned.benched == [('provider:elevenlabs', 'dead_key')]
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', '')
    keyless = Bench(ok)
    await elevenlabs.startup_check(keyless, {})
    assert keyless.benched == [('provider:elevenlabs', 'no_key')]
    assert all(scope != 'lane:external' for b in (healed, turned, keyless) for scope, *_ in b.benched)


# --- through the server ---

def free_row(engine, new_id):
    """A test-only copy of an ElevenLabs row with no price, so its takes land
    through review before the budget prices paid audio."""
    matrix = json.loads((REPO / 'configs/engines.json').read_text())
    row = json.loads(json.dumps(next(r for r in matrix['engines'] if r['id'] == engine)))
    row.update(id=new_id, name=f'Test {row["name"]}')
    row['tariffs'] = {verb: {**price, 'micro_usd': 0} for verb, price in row['tariffs'].items()}
    return row


async def test_designed_voices_land_speak_and_sound_through_the_server(tmp_path, monkeypatch):
    from test_api_v4 import store
    from test_audio_contract import booted_audio, draft, submit
    from test_crexil import project

    fake = (FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed())
            .on('POST', '/v1/text-to-voice', httpx.Response(200, json={'voice_id': 'voice-1'}))
            .on('POST', '/v1/text-to-speech/voice-1/with-timestamps', spoken())
            .on('POST', '/v1/sound-generation', sounded()))
    monkeypatch.setattr(config, 'ELEVENLABS_URL', BASE)
    ex = executor(fake)
    rows = [free_row('elevenlabs-voice-design', 'test-eleven-design'), free_row('elevenlabs-v4', 'test-eleven-v4'),
            free_row('elevenlabs-sfx', 'test-eleven-sfx')]
    async with booted_audio(tmp_path, monkeypatch, rows=rows, executors={'elevenlabs': ex}) as (client, _):
        p = await project(client)
        design = await draft(client, p['id'], 'audio:voice-design:test-eleven-design', DESCRIPTION,
                             controls={'preview_text': PREVIEW, 'seed': {'mode': 'fixed', 'value': 7}})
        _, takes = await submit(client, design)
        assert len(takes) == 3 and {t['payload'] for t in takes} == {'audio'}
        facts = await store().audio_facts(p['id'])
        for take in takes:
            measured = facts[take['id']]
            assert (measured['sample_rate'], measured['channels']) == (24000, 1)
            assert measured['loudness_lufs'] is not None
            assert 0 < len(measured['peaks']) <= audio.PEAK_BINS and measured['voice']['kind'] == 'designed'
        # The receipt is collected first, but a take is numbered among its attempt's
        # takes only: the previews read 0A, 0A.2 and 0A.3, as a local engine's do.
        number = {facts[t['id']]['voice']['generated_voice_id']: t['output_index'] for t in takes}
        assert number == {'gen-0': 0, 'gen-1': 1, 'gen-2': 2}
        (cell,) = (await client.get(f"/api/crexil/directions/{design['id']}/run")).json()['cells']
        assert [take['label'] for take in cell['takes']] == ['0A', '0A.2', '0A.3']
        (group,) = [g for g in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['execution_groups']
                    if g['slot_id'] == takes[0]['slot_id']]
        receipt, *outputs = group['outputs']
        assert (receipt['identity'], receipt['ordinal'], receipt['node_id']) == ('provider:receipt', 0, None)
        landed = {t['id']: t['output_index'] for t in takes}
        assert [(o['ordinal'], landed[o['node_id']]) for o in outputs] == [(1, 0), (2, 1), (3, 2)]
        voice = next(t for t in takes if facts[t['id']]['voice']['generated_voice_id'] == 'gen-0')
        assert json.loads(fake.calls('POST', '/v1/text-to-voice/design')[0].content)['seed'] == 7

        for _ in range(2):
            line = await draft(client, p['id'], 'audio:speak:test-eleven-v4', LINE, refs=[voice['id']])
            turn, (read,) = await submit(client, line)
            spoken_facts = (await store().audio_facts(p['id']))[read['id']]
            assert spoken_facts['voice'] == {'provider': 'elevenlabs', 'kind': 'saved', 'voice_id': 'voice-1',
                                             'from_generated_voice_id': 'gen-0'}
            assert spoken_facts['text'] == LINE and spoken_facts['alignment'] == ALIGNMENT
            assert (spoken_facts['channels'], spoken_facts['duration_s'], read['output_index']) == (1, 0.5, 0)
        # The second run reused the voice the first saved: review found it in the record.
        assert turn['slots'][0]['external']['voice']['voice_id'] == 'voice-1'
        assert len(fake.calls('POST', '/v1/text-to-voice')) == 1

        sound = await draft(client, p['id'], 'audio:sfx:test-eleven-sfx', 'Hammer on an anvil',
                            controls={'duration_seconds': 2.5})
        turn, (strike,) = await submit(client, sound)
        # Review froze the row's stereo answer beside the fixed fields; the take lasts what was asked.
        assert turn['slots'][0]['external']['pcm_channels'] == 2
        sounded_facts = (await store().audio_facts(p['id']))[strike['id']]
        assert {key: sounded_facts[key] for key in ('sample_rate', 'channels', 'duration_s')} == \
            {'sample_rate': 48000, 'channels': 2, 'duration_s': 2.5}
        (cell,) = (await client.get(f"/api/crexil/directions/{sound['id']}/run")).json()['cells']
        assert [take['label'] for take in cell['takes']] == [f"{turn['idx']}A"]
        origin = await store().material_origin(strike['id'])
        assert origin['terms']['no_ai_input'] is True


# --- live, opt-in ---

# The session is sealed (hermetic.py), so this check names the public address itself and
# takes the key the shell exported, never one from a `.env`.
@pytest.mark.skipif(os.environ.get('LIVE_ELEVENLABS') != '1' or not EXPORTED.get('ELEVENLABS_API_KEY'),
                    reason='a live ElevenLabs call is opt-in: LIVE_ELEVENLABS=1 with ELEVENLABS_API_KEY exported '
                           '(about $0.001)')
async def test_live_sound_effect_round_trip(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    shutil.copytree(REPO / 'configs', configs)
    matrix = json.loads((configs / 'engines.json').read_text())
    for row in matrix['engines']:
        if row['id'] == 'elevenlabs-sfx':
            row['enabled'] = True
    (configs / 'engines.json').write_text(json.dumps(matrix))
    monkeypatch.setattr(config, 'CONFIGS_DIR', configs)
    monkeypatch.setattr(config, 'ELEVENLABS_URL', 'https://api.elevenlabs.io')
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', EXPORTED['ELEVENLABS_API_KEY'])
    live = ElevenLabsExecutor(base_url=config.ELEVENLABS_URL)
    env = envelope('elevenlabs-sfx', 'sfx', 'A single hammer strike on an anvil', {'duration_seconds': 0.5},
                   seed=None)
    env['destination'] = config.ELEVENLABS_URL
    try:
        result, _, _ = await run(live, env)
    finally:
        await live.close()
    assert audio.inspect(result.data)['duration_s'] > 0
