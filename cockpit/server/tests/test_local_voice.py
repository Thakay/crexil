"""The local voice engine: the runner's door and the executor that reaches it.

The runner (`tools/tts/runner.py`) is served for real on a free port with a
scripted backend in place of the model, so every test speaks real HTTP and
lands real WAV bytes without a GPU. One test drives the runner's own model
backend against a stand-in for torch to prove it frees the card after every
job.
"""

import array
import asyncio
import base64
import gc
import hashlib
import importlib.util
import io
import json
import math
import shutil
import sys
import threading
import types
import wave
import weakref
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import audio, config, local_voice
from app.executors import DispatchRequest, EngineFailure, SourceImage
from app.local_voice import VoiceRunnerExecutor
from fakes import tone_wav
from test_api_v4 import settle_turn
from test_audio_contract import PREVIEW, booted_audio, designed, draft, submit
from test_crexil import command, project
from test_crexil_execution import reviewed

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
DESCRIPTION = 'A gruff dwarf blacksmith in his sixties, deep and gravelly, with a faint Scottish burr.'
LINE = 'Steel remembers every strike. So do I.'


def load_runner():
    spec = importlib.util.spec_from_file_location('voice_runner', REPO / 'tools/tts/runner.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['voice_runner'] = module           # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def pcm_tone(seconds=3.5, rate=24000, pitch=110.0) -> bytes:
    samples = array.array('h', (int(9000 * math.sin(2 * math.pi * pitch * i / rate))
                                for i in range(int(seconds * rate))))
    return samples.tobytes()


class StubBackend:
    """The model's place in the runner: real-shaped takes, scripted outcomes."""

    def __init__(self):
        self.calls = []
        self.gate: threading.Event | None = None
        self.failure: Exception | None = None

    def models(self):
        return {'voice-design': True, 'speak': True}

    def _takes(self, spec, seed):
        if self.gate is not None:
            self.gate.wait(10)
        if self.failure is not None:
            raise self.failure
        return [pcm_tone(pitch=100 + 15 * i + seed % 11) for i in range(spec.n)], 24000

    def design(self, spec, seed):
        self.calls.append(('voice-design', spec, seed))
        takes, rate = self._takes(spec, seed)
        return takes, rate, {'load_s': 0.01, 'generate_s': 0.02, 'peak_vram_mb': 4100}

    def speak(self, spec, seed):
        self.calls.append(('speak', spec, seed))
        takes, rate = self._takes(spec, seed)
        return takes, rate, {'load_s': 0.01, 'generate_s': 0.02, 'peak_vram_mb': 4300,
                             'clone_mode': 'in-context' if spec.reference.text else 'voice-only'}


@contextmanager
def served(tmp_path, backend=None):
    backend = backend or StubBackend()
    door = runner.Runner(backend, tmp_path / 'runner-state')
    server = runner.serve(door, '127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield backend, door, f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        if backend.gate is not None:
            backend.gate.set()
        server.shutdown()
        server.server_close()
        door.stop()


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(local_voice, 'POLL_S', 0.01)


class Collector:
    """The scheduler's collector, recorded: answers stored bytes for an identity
    it already holds, as the real one does."""

    def __init__(self, stored=None):
        self.calls = []
        self.stored = stored or {}

    async def __call__(self, output):
        self.calls.append(output)
        return self.stored.get(output.identity) if output.data is None else None

    def landed(self):
        return [c for c in self.calls if c.data is not None]


def envelope(verb, **fields):
    found = {'transport': 'audio', 'provider': 'voice-runner', 'engine': 'qwen3-tts', 'verb': verb,
             'model_id': 'local', 'fixed': {}, 'takes': 1, 'voice': None, 'seed': 7}
    if verb == 'voice-design':
        found.update(text=DESCRIPTION, controls={'preview_text': LINE, 'language': 'English'}, takes=3)
    else:
        found.update(text='Bring me the ore before the fire dies.', controls={'language': 'English'})
    found.update(fields)
    return found


def request(verb='voice-design', *, slot='slot-1', sources=(), collector=None, **fields):
    external = envelope(verb, **fields)
    return DispatchRequest(slot_id=slot, engine='qwen3-tts', external=external, payload='audio', verb=verb,
                           prompt=external['text'], seed=external.get('seed'), sources=tuple(sources),
                           on_output=collector)


def reference(tmp_path, seconds=3.5, name='voice.wav'):
    path = tmp_path / name
    path.write_bytes(tone_wav(seconds=seconds))
    return path, SourceImage(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), name=name)


def voice(**recorded):
    return {'source_node_id': 'node-voice', 'sha256': 'x', 'text': LINE,
            'recorded': {'provider': 'voice-runner', 'kind': 'designed', **recorded}}


async def opened(base, transport=None):
    ex = VoiceRunnerExecutor(base, transport=transport)
    await ex.open()
    return ex


class Silence(httpx.AsyncBaseTransport):
    """The runner's door with its job polls going unanswered: the first
    `misses` of them, or every one while `silent` holds. The runner itself
    keeps working, as when it is busy or briefly away."""

    def __init__(self, misses=None):
        self.inner = httpx.AsyncHTTPTransport()
        self.misses, self.silent, self.unanswered = misses, True, 0

    async def handle_async_request(self, request):
        poll = request.method == 'GET' and request.url.path.startswith('/jobs/') and '/audio/' not in request.url.path
        if poll and self.silent and (self.misses is None or self.unanswered < self.misses):
            self.unanswered += 1
            raise httpx.ConnectError('the runner did not answer', request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def finished(door, job_ref):
    for _ in range(500):
        if door.get(job_ref)['state'] in runner.TERMINAL:
            return
        await asyncio.sleep(0.01)
    raise AssertionError('the runner never finished the job')


# --- the executor over the real door ---

async def test_a_design_lands_every_take_as_measured_wav_with_its_voice_facts(tmp_path):
    with served(tmp_path) as (backend, _, base):
        ex, collected = await opened(base), Collector()
        try:
            handle = await ex.start(request(collector=collected))
            result = await ex.wait(handle)
        finally:
            await ex.close()
    kind, spec, seed = backend.calls[0]
    assert (kind, spec.text, spec.instruct, spec.n, seed, spec.language) == (
        'voice-design', LINE, DESCRIPTION, 3, 7, 'English')
    inventory = [c for c in collected.calls if c.data is None]
    assert [c.identity for c in inventory] == ['take:0', 'take:1', 'take:2']   # inventory before bytes
    takes = collected.landed()
    assert [t.filename for t in takes] == ['take-1.wav', 'take-2.wav', 'take-3.wav']
    for take in takes:
        facts = audio.inspect(take.data)
        assert facts['sample_rate'] == 24000 and facts['channels'] == 1 and facts['duration_s'] == pytest.approx(3.5)
        assert take.descriptor['audio'] == {'text': LINE, 'alignment': None, 'voice': {
            'provider': 'voice-runner', 'kind': 'designed', 'generated_voice_id': None}}
        assert take.descriptor['seed'] == 7 and take.descriptor['charged_micro_usd'] == 0
    assert result.primary_identity == 'take:0' and result.payload == 'audio' and result.data == takes[0].data
    assert result.charged_usd == 0.0 and result.detail['seed'] == 7 and result.detail['peak_vram_mb'] == 4100


async def test_speech_sends_the_checked_reference_take_and_its_words(tmp_path):
    path, source = reference(tmp_path)
    with served(tmp_path) as (backend, _, base):
        ex, collected = await opened(base), Collector()
        try:
            req = request('speak', sources=[source], collector=collected, voice=voice())
            result = await ex.wait(await ex.start(req))
        finally:
            await ex.close()
    kind, spec, _ = backend.calls[0]
    sent = spec.reference
    assert kind == 'speak' and spec.text == 'Bring me the ore before the fire dies.' and spec.n == 1
    with wave.open(io.BytesIO(path.read_bytes())) as source_wav:
        assert sent.pcm == source_wav.readframes(source_wav.getnframes())
    assert sent.text == LINE and sent.seconds == pytest.approx(3.5)
    assert result.descriptor['audio']['voice'] == {'provider': 'voice-runner', 'kind': 'cloned',
                                                   'reference_node_id': 'node-voice', 'clone_mode': 'in-context'}


async def test_speech_refuses_a_voice_its_provider_forbids_before_anything_is_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIGS_DIR', REPO / 'configs')
    _, source = reference(tmp_path)
    with served(tmp_path) as (backend, door, base):
        ex = await opened(base)
        try:
            with pytest.raises(EngineFailure) as refused:
                await ex.start(request('speak', sources=[source],
                                       voice=voice(provider='elevenlabs', generated_voice_id='gen-1')))
        finally:
            await ex.close()
        assert door.health()['queued'] == 0
    assert refused.value.kind == 'preflight' and refused.value.charged_usd == 0
    assert 'Nothing was sent.' in str(refused.value) and not backend.calls
    reason = audio.engines()['elevenlabs-v4']['terms']['reason']
    assert reason in str(refused.value)


async def test_speech_checks_the_reference_bytes_before_sending(tmp_path):
    path, source = reference(tmp_path)
    with served(tmp_path) as (backend, _, base):
        ex = await opened(base)
        try:
            path.write_bytes(tone_wav(seconds=3.6))
            with pytest.raises(EngineFailure) as changed:
                await ex.start(request('speak', sources=[source], voice=voice()))
            path.unlink()
            with pytest.raises(EngineFailure) as missing:
                await ex.start(request('speak', sources=[source], voice=voice()))
        finally:
            await ex.close()
    assert changed.value.kind == 'source_changed' and missing.value.kind == 'source_missing'
    assert not backend.calls


async def test_the_runners_refusal_reaches_the_take_in_its_own_words(tmp_path):
    _, short = reference(tmp_path, seconds=1.2)
    with served(tmp_path) as (backend, _, base):
        ex = await opened(base)
        try:
            with pytest.raises(EngineFailure) as refused:
                await ex.start(request('speak', sources=[short], voice=voice()))
        finally:
            await ex.close()
    assert refused.value.kind == 'error' and refused.value.status == 422
    assert 'at least 3 seconds' in str(refused.value) and not backend.calls


async def test_engine_failures_keep_their_kind_and_the_engines_words(tmp_path):
    backend = StubBackend()
    backend.failure = runner.OutOfMemory('CUDA out of memory. Tried to allocate 2.00 GiB')
    with served(tmp_path, backend) as (_, _, base):
        ex = await opened(base)
        try:
            with pytest.raises(EngineFailure) as oom:
                await ex.wait(await ex.start(request()))
            backend.failure = KeyError('talker')
            with pytest.raises(EngineFailure) as broken:
                await ex.wait(await ex.start(request(slot='slot-2')))
        finally:
            await ex.close()
    assert oom.value.kind == 'oom' and 'ran out of video memory' in str(oom.value)
    assert 'Tried to allocate' in oom.value.detail
    assert broken.value.kind == 'error' and 'KeyError' in str(broken.value) and 'talker' in broken.value.detail


async def test_a_queued_job_cancels_and_a_running_one_finishes_cancelled(tmp_path):
    backend = StubBackend()
    backend.gate = threading.Event()
    with served(tmp_path, backend) as (_, door, base):
        ex = await opened(base)
        try:
            running = await ex.start(request(slot='slot-1'))
            queued = await ex.start(request(slot='slot-2'))
            while door.health()['running'] != running.job_ref:
                await asyncio.sleep(0.01)
            await ex.cancel(queued)
            await ex.cancel(running)
            backend.gate.set()
            with pytest.raises(EngineFailure) as first:
                await ex.wait(running)
            with pytest.raises(EngineFailure) as second:
                await ex.wait(queued)
        finally:
            await ex.close()
    assert first.value.kind == 'interrupted' and 'were not kept' in str(first.value)
    assert second.value.kind == 'interrupted' and 'before it started' in str(second.value)
    assert len(backend.calls) == 1


async def test_recovery_is_a_lookup_that_never_raises(tmp_path):
    backend = StubBackend()
    backend.gate = threading.Event()
    with served(tmp_path, backend) as (_, _, base):
        ex = await opened(base)
        try:
            assert (await ex.reconcile({'job_ref': None})).kind == 'gone'
            assert (await ex.reconcile({'job_ref': 'no-such-job'})).kind == 'gone'
            handle = await ex.start(request())
            found = await ex.reconcile({'job_ref': handle.job_ref}, request=request())
            assert found.kind == 'running' and found.handle.job_ref == handle.job_ref
            backend.gate.set()
            collected = Collector()
            resumed = await ex.wait(local_voice.VoiceHandle(job_ref=handle.job_ref,
                                                            request=request(collector=collected)))
            assert resumed.primary_identity == 'take:0' and len(collected.landed()) == 3
            settled = await ex.reconcile({'job_ref': handle.job_ref}, request=request())
            assert settled.kind == 'landed' and settled.result.payload == 'audio'
        finally:
            await ex.close()
    down = await opened('http://127.0.0.1:9')
    try:
        assert (await down.reconcile({'job_ref': 'any'})).kind == 'unverifiable'
        assert (await down.collect_existing({'job_ref': 'any'})).kind == 'unverifiable'
        with pytest.raises(EngineFailure) as unreachable:
            await down.start(request())
    finally:
        await down.close()
    assert unreachable.value.kind == 'unreachable' and 'tools/tts' in str(unreachable.value)


async def test_free_collection_reuses_stored_takes_and_fetches_only_the_rest(tmp_path):
    with served(tmp_path) as (_, door, base):
        ex = await opened(base)
        try:
            handle = await ex.start(request())
            await ex.wait(handle)
            first = (door.jobs_dir / handle.job_ref / 'take-1.wav').read_bytes()
            (door.jobs_dir / handle.job_ref / 'take-1.wav').unlink()      # only the stored copy remains
            collected = Collector(stored={'take:0': first})
            found = await ex.collect_existing({'job_ref': handle.job_ref}, request=request(collector=collected))
            assert found.kind == 'landed' and found.result.data == first
            assert [c.identity for c in collected.landed()] == ['take:1', 'take:2']
            (door.jobs_dir / handle.job_ref / 'take-2.wav').unlink()
            missing = await ex.collect_existing({'job_ref': handle.job_ref}, request=request(collector=Collector()))
        finally:
            await ex.close()
    assert missing.kind == 'failed' and missing.failure_kind == 'collection'
    assert 'Collected takes are kept' in missing.cause


# --- a runner that stops answering while a take runs (law 14) ---

async def test_a_poll_the_runner_does_not_answer_asks_about_the_same_job_again(tmp_path):
    silence = Silence(misses=5)
    with served(tmp_path) as (backend, door, base):
        ex, collected = await opened(base, silence), Collector()
        try:
            result = await ex.wait(await ex.start(request(collector=collected)))
        finally:
            await ex.close()
        jobs = list(door.jobs_dir.glob('*.json'))
    assert silence.unanswered == 5 and len(jobs) == 1 and len(backend.calls) == 1     # one take, one job
    assert result.primary_identity == 'take:0' and len(collected.landed()) == 3


async def test_a_runner_silent_too_long_leaves_the_take_uncertain_and_collecting_finds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(local_voice, 'REATTACH_S', 0.1)
    backend, silence = StubBackend(), Silence()
    backend.gate = threading.Event()
    with served(tmp_path, backend) as (_, door, base):
        ex = await opened(base, silence)
        try:
            handle = await ex.start(request())
            with pytest.raises(EngineFailure) as silent:
                await ex.wait(handle)
            silence.silent = False
            backend.gate.set()
            await finished(door, handle.job_ref)
            found = await ex.collect_existing({'job_ref': handle.job_ref}, request=request(collector=Collector()))
        finally:
            await ex.close()
    # Never `unreachable`, which the scheduler would retry as a second job.
    assert (silent.value.kind, silent.value.job_ref, silent.value.charged_usd) == ('uncertain', handle.job_ref, 0)
    assert silent.value.plain and str(silent.value) == local_voice.SILENT
    assert found.kind == 'landed' and len(backend.calls) == 1


async def test_a_silent_runner_never_runs_a_take_twice_through_the_shared_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(local_voice, 'REATTACH_S', 0.2)
    backend, silence = StubBackend(), Silence()
    backend.gate = threading.Event()
    with served(tmp_path, backend) as (_, door, base):
        async with booted_audio(tmp_path, monkeypatch, executors={
                'voice-runner': VoiceRunnerExecutor(base, transport=silence)}) as (client, _):
            monkeypatch.setattr(config, 'VOICE_RUNNER_URL', base)
            p = await project(client)
            direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', DESCRIPTION,
                                    controls={'preview_text': PREVIEW}, count=2)
            check = await reviewed(client, direction)
            answer = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                       json=command(revision=check['revision'], review_token=check['token']))
            turn_id = answer.json()['turn_id']
            first, second = (await settle_turn(turn_id, until={'failed', 'held'}))['slots']
            # The runner went silent on an accepted job: no automatic retry, one runner job, the
            # runner's other takes held behind its own bench, in its own words (law 1).
            assert (first['kind'], first['cause'], second['status']) == ('uncertain', local_voice.SILENT, 'held')
            assert first['job_ref'] and len(list(door.jobs_dir.glob('*.json'))) == 1
            (bench,) = (await client.get('/api/queue')).json()['benches']
            assert (bench['scope'], bench['kind']) == ('provider:voice-runner', 'unreachable')
            assert 'tools/tts/README.md' in bench['cause']

            silence.silent = False
            backend.gate.set()
            await finished(door, first['job_ref'])
            collected = await client.post(f"/api/crexil/attempts/{first['id']}/collect",
                                          json=command(take=first['attempt']))
            assert collected.status_code == 200, collected.text
            resumed = await client.post(f"/api/crexil/attempts/{second['id']}/resume",
                                        json=command(attempt=second['attempt']))
            assert resumed.status_code == 200 and resumed.json()['released'] == 1, resumed.text
            landed = await settle_turn(turn_id, until={'landed'})
            nodes = (await client.get(f"/api/crexil/projects/{p['id']}")).json()['nodes']
    assert [slot['status'] for slot in landed['slots']] == ['landed', 'landed']
    assert len([n for n in nodes if n['turn_id'] == turn_id]) == 6                     # three previews per take
    assert len(list(door.jobs_dir.glob('*.json'))) == 2 and len(backend.calls) == 2   # one job per take


# --- a request reaches the runner once (law 14) ---

class Door(httpx.AsyncBaseTransport):
    """The runner's door with its answers going astray. The first `refusals`
    job requests are refused before anything is sent. The next `losses` reach
    the runner, which queues the job, and the answer is lost on its way back;
    with `dropped`, those requests are lost before the runner reads them. While
    `silent`, lookups by client reference go unanswered."""

    def __init__(self, *, refusals=0, losses=0, dropped=False, silent=False):
        self.inner = httpx.AsyncHTTPTransport()
        self.refusals, self.losses, self.dropped, self.silent = refusals, losses, dropped, silent
        self.posts = 0

    async def handle_async_request(self, request):
        if request.method == 'POST' and request.url.path == '/jobs':
            self.posts += 1
            if self.refusals:
                self.refusals -= 1
                raise httpx.ConnectError('connection refused', request=request)
            if self.losses:
                self.losses -= 1
                if not self.dropped:
                    await (await self.inner.handle_async_request(request)).aread()
                raise httpx.ReadError('the connection dropped before the answer arrived', request=request)
        if self.silent and request.method == 'GET' and request.url.path == '/jobs':
            raise httpx.ConnectError('the runner did not answer', request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def attempt(number, **fields):
    from dataclasses import replace
    return replace(request(**fields), attempt=number)


async def test_an_answer_lost_after_the_request_went_out_reattaches_to_its_one_job(tmp_path):
    door_ = Door(losses=1)
    with served(tmp_path) as (backend, door, base):
        ex, collected = await opened(base, door_), Collector()
        try:
            handle = await ex.start(attempt(1, collector=collected))
            result = await ex.wait(handle)
        finally:
            await ex.close()
        (job,) = [json.loads(path.read_text()) for path in door.jobs_dir.glob('*.json')]
    assert (handle.job_ref, job['client_ref']) == (job['id'], 'slot-1:1')
    assert door_.posts == 1 and len(backend.calls) == 1                                  # one request, one job
    assert result.primary_identity == 'take:0' and len(collected.landed()) == 3


async def test_a_refused_connection_reached_nothing_and_is_the_failure_that_retries(tmp_path):
    door_ = Door(refusals=1)
    with served(tmp_path) as (backend, door, base):
        ex = await opened(base, door_)
        try:
            with pytest.raises(EngineFailure) as refused:
                await ex.start(attempt(1))
            assert list(door.jobs_dir.glob('*.json')) == []
            await ex.wait(await ex.start(attempt(2)))                # the scheduler's retry, as the next attempt
        finally:
            await ex.close()
        refs = [json.loads(path.read_text())['client_ref'] for path in door.jobs_dir.glob('*.json')]
    assert refused.value.kind == 'unreachable' and refused.value.charged_usd is None
    assert refs == ['slot-1:2'] and len(backend.calls) == 1


async def test_a_lost_answer_the_runner_cannot_place_leaves_the_take_uncertain(tmp_path, monkeypatch):
    monkeypatch.setattr(local_voice, 'REATTACH_S', 0.1)
    with served(tmp_path) as (backend, door, base):
        # The runner queued the job, then stopped answering lookups: collecting finds the job later.
        silent = Door(losses=1, silent=True)
        ex = await opened(base, silent)
        try:
            with pytest.raises(EngineFailure) as unplaced:
                await ex.start(attempt(1))
            silent.silent = False
            (job,) = [json.loads(path.read_text()) for path in door.jobs_dir.glob('*.json')]
            await finished(door, job['id'])
            found = await ex.collect_existing({'id': 'slot-1', 'attempt': 1, 'job_ref': None},
                                              request=attempt(1, collector=Collector()))
        finally:
            await ex.close()
        # The request was lost before the runner read it: the runner holds no job for it.
        dropped = await opened(base, Door(losses=1, dropped=True))
        try:
            with pytest.raises(EngineFailure) as unheld:
                await dropped.start(attempt(1, slot='slot-2'))
            gone = await dropped.collect_existing({'id': 'slot-2', 'attempt': 1, 'job_ref': None})
        finally:
            await dropped.close()
    for failure in (unplaced.value, unheld.value):             # never `unreachable`, which would retry it
        assert (failure.kind, str(failure), failure.plain, failure.charged_usd) == (
            'uncertain', local_voice.LOST, True, 0)
    assert found.kind == 'landed' and gone.kind == 'gone' and len(backend.calls) == 1


async def test_a_lost_answer_and_a_refused_door_each_send_a_take_once_through_the_shared_actions(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RETRY_BACKOFF_S', 0.01)
    door_ = Door(losses=1, refusals=1)
    with served(tmp_path) as (backend, door, base):
        async with booted_audio(tmp_path, monkeypatch, executors={
                'voice-runner': VoiceRunnerExecutor(base, transport=door_)}) as (client, _):
            monkeypatch.setattr(config, 'VOICE_RUNNER_URL', base)
            p = await project(client)
            direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', DESCRIPTION,
                                    controls={'preview_text': PREVIEW}, count=2)
            check = await reviewed(client, direction)
            answer = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                       json=command(revision=check['revision'], review_token=check['token']))
            turn = await settle_turn(answer.json()['turn_id'], until={'landed'})
        refs = sorted(json.loads(path.read_text())['client_ref'] for path in door.jobs_dir.glob('*.json'))
    first, second = turn['slots']
    # The refused try retried visibly as attempt 2; the lost answer was re-attached, never sent again.
    assert (first['attempt'], first['retry_of']) == (2, 'unreachable')
    assert (second['attempt'], second['retry_of']) == (1, None)
    assert refs == sorted([f"{first['id']}:2", f"{second['id']}:1"]) and len(backend.calls) == 2
    assert door_.posts == 3


def test_the_runner_keeps_one_job_per_client_reference_and_finds_it(tmp_path):
    backend = StubBackend()
    backend.gate = threading.Event()
    with served(tmp_path, backend) as (_, door, base):
        body = {'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'n': 1, 'client_ref': 'slot-9:1'}
        with httpx.Client(base_url=base) as client:
            first = client.post('/jobs', json=body)
            again = client.post('/jobs', json=body)
            found = client.get('/jobs', params={'client_ref': 'slot-9:1'})
            unknown = client.get('/jobs', params={'client_ref': 'slot-9:2'})
            unnamed = client.get('/jobs')
        assert first.status_code == again.status_code == 202
        assert first.json()['job_id'] == again.json()['job_id'] == found.json()['id']
        assert found.json()['client_ref'] == 'slot-9:1' and len(list(door.jobs_dir.glob('*.json'))) == 1
        assert (unknown.status_code, unnamed.status_code) == (404, 400)


# --- the startup check ---

class Benches:
    def __init__(self):
        self.benched, self.unbenched = [], []

    async def bench(self, scope, *, kind, cause):
        self.benched.append((scope, kind, cause))

    async def unbench(self, scope):
        self.unbenched.append(scope)


def configs_with_runner(tmp_path, monkeypatch, enabled):
    configs = tmp_path / 'configs'
    shutil.copytree(REPO / 'configs', configs)
    matrix = json.loads((configs / 'engines.json').read_text())
    for row in matrix['engines']:
        if row.get('id') == 'qwen3-tts':
            row['enabled'] = enabled
    (configs / 'engines.json').write_text(json.dumps(matrix))
    monkeypatch.setattr(config, 'CONFIGS_DIR', configs)


async def test_a_runner_down_at_boot_benches_its_provider_and_heals_when_it_answers(tmp_path, monkeypatch):
    configs_with_runner(tmp_path, monkeypatch, enabled=True)
    monkeypatch.setattr(config, 'VOICE_RUNNER_URL', 'http://127.0.0.1:9')
    down = Benches()
    await local_voice.startup_check(down, {})
    [(scope, kind, cause)] = down.benched
    assert (scope, kind) == ('provider:voice-runner', 'unreachable')
    assert 'not answering at http://127.0.0.1:9' in cause and 'tools/tts' in cause
    with served(tmp_path) as (_, _, base):
        monkeypatch.setattr(config, 'VOICE_RUNNER_URL', base)
        up = Benches()
        await local_voice.startup_check(up, {scope: {'scope': scope, 'kind': 'unreachable', 'cause': cause}})
        await local_voice.startup_check(up, {})            # nothing to heal, nothing to bench
    assert up.unbenched == [scope] and up.benched == []


async def test_a_disabled_runner_is_never_benched(tmp_path, monkeypatch):
    configs_with_runner(tmp_path, monkeypatch, enabled=False)
    monkeypatch.setattr(config, 'VOICE_RUNNER_URL', 'http://127.0.0.1:9')
    quiet = Benches()
    await local_voice.startup_check(quiet, {})
    assert quiet.benched == [] and quiet.unbenched == []


# --- through the shared actions ---

async def test_a_local_design_then_speech_land_through_the_shared_actions(tmp_path, monkeypatch):
    with served(tmp_path) as (backend, _, base):
        async with booted_audio(tmp_path, monkeypatch, executors={'voice-runner': VoiceRunnerExecutor(base)}) as (
                client, _):
            p = await project(client)
            turn, takes = await designed(client, p['id'])
            assert [s['status'] for s in turn['slots']] == ['landed'] and len(takes) == 3
            voice_take = takes[0]['id']
            direction = await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Bring me the ore.', refs=[voice_take])
            turn, spoken = await submit(client, direction)
            assert [s['status'] for s in turn['slots']] == ['landed'] and len(spoken) == 1
            nodes = {n['id']: n for n in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['nodes']}
            designed_facts, spoken_facts = nodes[voice_take]['audio'], nodes[spoken[0]['id']]['audio']
            played = await client.get(f"/api/crexil/material/{spoken[0]['id']}/audio")
    assert designed_facts['duration_s'] == pytest.approx(3.5) and designed_facts['sample_rate'] == 24000
    assert designed_facts['voice'] == {'provider': 'voice-runner', 'kind': 'designed', 'generated_voice_id': None}
    assert spoken_facts['text'] == 'Bring me the ore.' and spoken_facts['loudness_lufs'] is not None
    assert spoken_facts['voice'] == {'provider': 'voice-runner', 'kind': 'cloned',
                                     'reference_node_id': voice_take, 'clone_mode': 'in-context'}
    assert played.status_code == 200 and audio.inspect(played.content)['duration_s'] == pytest.approx(3.5)
    design, speech = backend.calls
    assert design[0] == 'voice-design' and design[1].n == 3 and design[1].instruct
    assert speech[0] == 'speak' and speech[1].text == 'Bring me the ore.'
    assert speech[1].reference.seconds == pytest.approx(3.5) and speech[1].reference.text


# --- the runner's own door ---

def wav_b64(seconds):
    return base64.b64encode(tone_wav(seconds=seconds)).decode()


@pytest.mark.parametrize('body, words', [
    ([], 'JSON object'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'speed': 2}, 'Unknown field: speed'),
    ({'kind': 'sing', 'text': LINE}, 'kind must be one of'),
    ({'kind': 'voice-design', 'instruct': DESCRIPTION}, 'line to speak is missing'),
    ({'kind': 'voice-design', 'text': LINE}, 'voice description is missing'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'voice': {}}, 'not a voice'),
    ({'kind': 'speak', 'text': LINE, 'instruct': DESCRIPTION}, 'not a description'),
    ({'kind': 'speak', 'text': LINE}, 'needs a voice'),
    ({'kind': 'speak', 'text': LINE, 'voice': {'wav_b64': 'not base64!'}}, 'not valid base64'),
    ({'kind': 'speak', 'text': LINE, 'voice': {'wav_b64': base64.b64encode(b'RIFF-nope').decode()}}, 'not a PCM WAV'),
    ({'kind': 'speak', 'text': LINE, 'voice': {'wav_b64': wav_b64(2.0), 'accent': 'x'}}, 'Unknown voice field'),
    ({'kind': 'speak', 'text': LINE, 'voice': {'wav_b64': wav_b64(2.0)}}, 'at least 3 seconds'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'n': 9}, 'from 1 to 8'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'n': True}, 'from 1 to 8'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'seed': -1}, 'seed must be'),
    ({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'language': 'Klingon'}, 'language must be'),
    ({'kind': 'voice-design', 'text': 'x' * 2001, 'instruct': DESCRIPTION}, 'at most 2000'),
])
def test_the_runner_refuses_a_request_by_name(body, words):
    with pytest.raises(runner.Refused, match=words):
        runner.check(body)


def test_the_runner_takes_a_reference_without_words_as_a_voice_alone():
    spec = runner.check({'kind': 'speak', 'text': LINE, 'voice': {'wav_b64': wav_b64(3.0), 'text': '  '}})
    assert spec.reference.text is None and spec.reference.seconds == pytest.approx(3.0)


async def test_the_runner_answers_health_and_refuses_over_http(tmp_path):
    with served(tmp_path) as (_, _, base):
        async with httpx.AsyncClient(base_url=base) as client:
            health = (await client.get('/health')).json()
            refused = await client.post('/jobs', json={'kind': 'sing', 'text': LINE})
            nothing = await client.get('/jobs/unknown/audio/0')
    assert health['ok'] and health['engine'] == 'qwen3-tts' and health['busy'] is False
    assert refused.status_code == 422 and 'kind must be one of' in refused.json()['detail']
    assert nothing.status_code == 404


def test_a_restarted_runner_records_its_unfinished_jobs_as_failed(tmp_path):
    jobs = tmp_path / 'runner-state' / 'jobs'
    jobs.mkdir(parents=True)
    (jobs / 'abc.json').write_text(json.dumps({'id': 'abc', 'kind': 'speak', 'state': 'running'}))
    (jobs / 'def.json').write_text(json.dumps({'id': 'def', 'kind': 'speak', 'state': 'done', 'takes': []}))
    door = runner.Runner(StubBackend(), tmp_path / 'runner-state')
    try:
        assert door.get('abc')['state'] == 'failed' and 'restarted' in door.get('abc')['error']
        assert json.loads((jobs / 'abc.json').read_text())['state'] == 'failed'
        assert door.get('def')['state'] == 'done'
    finally:
        door.stop()


# --- the model backend against a stand-in for torch ---

class FakeCuda:
    def __init__(self):
        self.emptied = 0

    def empty_cache(self):
        self.emptied += 1

    def reset_peak_memory_stats(self):
        pass

    def synchronize(self):
        pass

    def max_memory_allocated(self):
        return 4100 * 2**20

    def manual_seed_all(self, seed):
        pass


class FakeOOM(RuntimeError):
    pass


def stand_in_torch(monkeypatch, *, fail=None):
    torch = types.ModuleType('torch')
    torch.cuda = FakeCuda()
    torch.bfloat16 = 'bfloat16'
    torch.OutOfMemoryError = FakeOOM
    torch.seeds = []
    torch.manual_seed = torch.seeds.append
    loaded = []

    class Model:
        def generate_voice_design(self, *, text, instruct, language):
            if fail:
                raise fail
            assert len(text) == len(instruct) == len(language)
            return [[0.5, -0.5, 2.0]] * len(text), 24000

    class Qwen3TTSModel:
        @staticmethod
        def from_pretrained(path, **options):
            model = Model()
            loaded.append((path, options, weakref.ref(model)))
            return model

    qwen = types.ModuleType('qwen_tts')
    qwen.Qwen3TTSModel = Qwen3TTSModel
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setitem(sys.modules, 'qwen_tts', qwen)
    monkeypatch.setattr(runner, 'pcm16', lambda samples: array.array('h', (int(max(-1, min(1, s)) * 32767)
                                                                           for s in samples)).tobytes())
    return torch, loaded


def test_the_model_backend_loads_seeds_and_frees_the_card_after_every_job(monkeypatch):
    torch, loaded = stand_in_torch(monkeypatch)
    backend = runner.QwenBackend({'voice-design': '/models/design', 'speak': '/models/base'})
    spec = runner.check({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION, 'n': 2})
    takes, rate, facts = backend.design(spec, seed=5)
    gc.collect()
    path, options, model = loaded[0]
    assert path == '/models/design' and options == {'device_map': 'cuda:0', 'dtype': 'bfloat16',
                                                     'attn_implementation': 'sdpa'}
    assert torch.seeds == [5] and torch.cuda.emptied == 1 and model() is None     # nothing stays on the card
    assert rate == 24000 and len(takes) == 2 and array.array('h', takes[0]).tolist() == [16383, -16383, 32767]
    assert facts['peak_vram_mb'] == 4100


def test_the_model_backend_reports_out_of_memory_and_still_frees_the_card(monkeypatch):
    torch, _ = stand_in_torch(monkeypatch, fail=FakeOOM('CUDA out of memory'))
    backend = runner.QwenBackend({'voice-design': '/models/design', 'speak': '/models/base'})
    spec = runner.check({'kind': 'voice-design', 'text': LINE, 'instruct': DESCRIPTION})
    with pytest.raises(runner.OutOfMemory):
        backend.design(spec, seed=1)
    assert torch.cuda.emptied == 1
