"""Reach the local voice runner: designed and spoken takes on the shared local lane.

The runner (`tools/tts/runner.py`) serves the local voice engine over HTTP from
the engine's own environment. This executor turns one frozen audio envelope into
one runner job, follows the job's record and lands its takes as WAV through the
collector, inventory first, so a take that cannot be fetched is recorded as
missing instead of erasing the others. It never touches the store: the scheduler
owns every transition and serializes these jobs with the other local engines
(law 14). Nothing here bills, so every take reports a charge of zero.

A voice take whose provider forbids its output as input to another AI model
never reaches the runner (law 21). Review refuses it, and `start` refuses it
again before anything is sent, using the terms of that provider's engine rows.
A request the runner refuses (400, 413, 422) fails with the runner's own plain
words as its cause, and nothing is generated.

Recovery is a lookup of the runner's job records, and it never raises: a raised
failure during recovery would bench the whole local lane and stop the image
engines beside this one (law 1). Each attempt's job carries the client
reference `<slot>:<attempt>`, which the runner keeps one job for. Only a
connection the runner refused, which reached nothing, fails as `unreachable`
and retries. Once the request is out, the take is never sent again (law 14): an
answer lost on its way back looks the job up by its client reference and
re-attaches; a poll the runner does not answer asks about the same job again;
and a runner silent for REATTACH_S, or one that holds no job for the
reference, leaves the take's outcome uncertain, so collecting looks the job up
later and nothing is generated twice.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from contextlib import suppress
from dataclasses import dataclass, replace

import httpx

from . import audio, config
from .executors import (CollectedOutput, DispatchRequest, EngineFailure, ExecutionResult, Handle, Lane,
                        Reconciliation)

PROVIDER = 'voice-runner'
VERBS = ('voice-design', 'speak')
TERMINAL = frozenset({'done', 'failed', 'cancelled'})
POLL_S = 0.5                       # a local voice job lasts seconds; the poll is cheap
# How long a running job is asked about again while the runner does not answer,
# before its outcome counts as uncertain. The lane waits that long, because the
# card may still be busy with the job (law 14), and no longer (law 1).
REATTACH_S = 60.0
START_HINT = 'Start it with tools/tts/runner.py (see tools/tts/README.md).'
SILENT = ('The local voice runner stopped answering while this take was running, so it was not sent '
          'again. When the runner answers, collect files to look for its takes; nothing is generated again.')
LOST = ("The local voice runner's answer to this take's request was lost, so it was not sent again. When the "
        'runner answers, collect files to look for its takes; nothing is generated again.')
# The failures that mean the request never reached the runner, so sending it
# again cannot start a second job.
REFUSED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass
class VoiceHandle(Handle):
    """`job_ref` is the runner's job id, recorded on the slot as soon as it
    exists, which makes recovery a lookup."""
    request: DispatchRequest | None = None


class VoiceRunnerExecutor:
    """Local lane executor over the local voice runner."""

    name: str = PROVIDER
    lane: Lane = 'local'

    def __init__(self, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = (base_url or config.VOICE_RUNNER_URL).rstrip('/')
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle ---

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base, transport=self._transport,
                                             timeout=httpx.Timeout(60.0, connect=5.0))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise EngineFailure('unreachable', 'The local voice runner client was never opened.')
        return self._client

    def _unreachable(self, exc: Exception, when: str) -> EngineFailure:
        return EngineFailure('unreachable', f'The local voice runner is not answering at {self._base} {when}. '
                                            f'{START_HINT} The other engines are unaffected.',
                             detail=str(exc)[:600])

    # --- the executor's verbs ---

    async def start(self, req: DispatchRequest) -> VoiceHandle:
        envelope = req.external or {}
        verb = envelope.get('verb')
        if envelope.get('transport') != 'audio' or envelope.get('provider') != self.name or verb not in VERBS:
            raise EngineFailure('error', f"The local voice runner does not serve {verb or 'this request'}. "
                                         'Nothing was sent.', charged_usd=0)
        controls = envelope.get('controls') or {}
        client_ref = reference(req.slot_id, req.attempt)
        body = {'kind': verb, 'language': controls.get('language') or 'English',
                'n': int(envelope.get('takes') or 1), 'seed': envelope.get('seed'), 'client_ref': client_ref}
        if verb == 'voice-design':
            body.update(text=controls.get('preview_text'), instruct=envelope.get('text'))
        else:
            body.update(text=envelope.get('text'), voice=await self._voice(req, envelope.get('voice') or {}))
        if req.before_send is not None:
            try:
                await req.before_send()
            except ValueError as exc:
                raise EngineFailure('preflight', str(exc), charged_usd=0) from exc
        try:
            answer = await self._http.post('/jobs', json=body)
        except REFUSED as exc:
            raise self._unreachable(exc, 'at the door') from exc
        except httpx.HTTPError as exc:
            return await self._reattach(req, client_ref, exc)
        if answer.status_code in (400, 413, 422):
            # The runner refused the request itself, in plain words that name
            # the field; they become the take's cause with the next move (laws 3, 4).
            raise EngineFailure('error', f'The local voice runner refused this take: {_detail(answer)} Nothing '
                                         'was generated. Change what it names, then prepare the take again.',
                                status=answer.status_code, detail=_detail(answer), charged_usd=0, plain=True)
        if answer.status_code >= 400:
            raise EngineFailure('error', f'The local voice runner refused this take ({answer.status_code}). '
                                         f'{_detail(answer)}', status=answer.status_code, charged_usd=0)
        job_id = (answer.json() or {}).get('job_id')
        if not job_id:
            raise EngineFailure('error', 'The local voice runner accepted the take but named no job.', charged_usd=0)
        return VoiceHandle(job_ref=job_id, request=req)

    async def _reattach(self, req: DispatchRequest, client_ref: str, lost: httpx.HTTPError) -> VoiceHandle:
        """The take's job after the runner's answer was lost: looked up by its
        client reference, never sent again (law 14). While the runner does
        not answer, it is asked again for REATTACH_S; a runner that stays
        silent, or holds no job for the reference, leaves the outcome
        uncertain, and collecting looks the job up later."""
        loop = asyncio.get_running_loop()
        started, record = loop.time(), None
        while True:
            try:
                record = await self._find(client_ref)
            except httpx.HTTPError:
                if loop.time() - started < REATTACH_S:
                    await asyncio.sleep(POLL_S)
                    continue
            break
        if not record or not record.get('id'):
            raise EngineFailure('uncertain', LOST, detail=str(lost)[:600], charged_usd=0, plain=True) from lost
        return VoiceHandle(job_ref=record['id'], request=req)

    async def _voice(self, req: DispatchRequest, voice: dict) -> dict:
        """The reference take's checked bytes and words for a `speak` job."""
        why = forbidden(voice)
        if why:
            raise EngineFailure('preflight', f'{why} Nothing was sent.', charged_usd=0)
        if not req.sources or not req.sources[0].path:
            raise EngineFailure('source_missing', 'The voice take is unavailable. Restore it before trying again.')
        source = req.sources[0]
        try:
            blob = await asyncio.to_thread(_read, source.path)
        except OSError as exc:
            raise EngineFailure('source_missing',
                                'The voice take is unavailable. Restore it before trying again.') from exc
        if hashlib.sha256(blob).hexdigest() != source.sha256:
            raise EngineFailure('source_changed', 'The voice take changed. Review its current bytes before trying again.')
        return {'wav_b64': base64.b64encode(blob).decode(), 'text': voice.get('text')}

    async def wait(self, handle: VoiceHandle) -> ExecutionResult:
        """Follow the accepted job to its end. A poll the runner does not
        answer asks about the same job again, never starting another (law
        14): an `unreachable` failure here would be retried as a second job.
        After REATTACH_S of silence the outcome is uncertain: the job id
        stays on the take, so collecting looks it up later."""
        if not handle.job_ref:
            raise EngineFailure('error', 'The local voice runner gave no job id.')
        loop = asyncio.get_running_loop()
        answered = loop.time()                 # the runner just named this job
        while True:
            try:
                record = await self._fetch(handle.job_ref)
            except httpx.HTTPError as exc:
                if loop.time() - answered >= REATTACH_S:
                    raise EngineFailure('uncertain', SILENT, detail=str(exc)[:600], job_ref=handle.job_ref,
                                        charged_usd=0, plain=True) from exc
                await asyncio.sleep(POLL_S)
                continue
            answered = loop.time()
            if record is None:
                raise EngineFailure('error', 'The local voice runner has no record of this job. '
                                             'It may have restarted during the take.')
            if record.get('state') in TERMINAL:
                return await self._land(handle, record)
            await asyncio.sleep(POLL_S)

    async def cancel(self, handle: VoiceHandle) -> None:
        """Best effort and safe on finished work: a queued job is dropped, and a
        running one finishes on the card and is recorded as cancelled."""
        if handle.job_ref:
            with suppress(httpx.HTTPError):   # wait() reports what actually happened
                await self._http.delete(f'/jobs/{handle.job_ref}')

    async def reconcile(self, slot: dict, *, request: DispatchRequest | None = None) -> Reconciliation:
        try:
            record = await self._record_of(slot)
        except httpx.HTTPError:
            return Reconciliation('unverifiable')
        if record is None:
            return Reconciliation('gone')     # the runner never queued this attempt's job
        if record.get('state') not in TERMINAL:
            return Reconciliation('running', handle=VoiceHandle(job_ref=record['id'], request=request))
        return await self._settled(record['id'], record, request)

    async def collect_existing(self, slot: dict, *, request: DispatchRequest | None = None) -> Reconciliation:
        """Retrieve a finished job's takes again, without generating anything."""
        try:
            record = await self._record_of(slot)
        except httpx.HTTPError:
            return Reconciliation('unverifiable')
        if record is None:
            return Reconciliation('gone')
        if record.get('state') not in TERMINAL:
            return Reconciliation('unverifiable')
        return await self._settled(record['id'], record, request)

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None                           # nothing bills on the card

    # --- internals ---

    async def _settled(self, job_ref: str, record: dict, request: DispatchRequest | None) -> Reconciliation:
        try:
            result = await self._land(VoiceHandle(job_ref=job_ref, request=request), record)
        except EngineFailure as exc:
            return Reconciliation('failed', cause=str(exc), failure_kind=exc.kind)
        except httpx.HTTPError as exc:
            return Reconciliation('failed', failure_kind='collection',
                                  cause=f'The takes finished but could not be fetched: {exc}')
        return Reconciliation('landed', result=result)

    async def _fetch(self, job_ref: str) -> dict | None:
        answer = await self._http.get(f'/jobs/{job_ref}')
        if answer.status_code == 404:
            return None
        answer.raise_for_status()
        return answer.json()

    async def _find(self, client_ref: str) -> dict | None:
        """The job the runner holds for this client reference, if any."""
        answer = await self._http.get('/jobs', params={'client_ref': client_ref})
        if answer.status_code == 404:
            return None
        answer.raise_for_status()
        return answer.json()

    async def _record_of(self, slot: dict) -> dict | None:
        """An attempt's job record: by the job id the slot keeps, else by the
        attempt's client reference, for a request whose answer never named it."""
        if slot.get('job_ref'):
            return await self._fetch(slot['job_ref'])
        if not slot.get('id'):
            return None
        return await self._find(reference(slot['id'], slot['attempt']))

    async def _land(self, handle: VoiceHandle, record: dict) -> ExecutionResult:
        state = record.get('state')
        if state == 'cancelled':
            raise EngineFailure('interrupted', record.get('error') or 'The take was cancelled.', charged_usd=0)
        if state == 'failed':
            kind = 'oom' if record.get('error_kind') == 'oom' else 'error'
            raise EngineFailure(kind, record.get('error') or 'The local voice engine failed without saying why.',
                                detail=str(record.get('tail') or '')[:800], charged_usd=0)
        takes = record.get('takes') or []
        if not takes:
            raise EngineFailure('error', 'The local voice engine finished without a take.', charged_usd=0)
        request = handle.request
        envelope = (request.external if request else None) or {}
        on_output = request.on_output if request else None
        outputs = [CollectedOutput(identity=f'take:{i}', filename=f'take-{i + 1}.wav', payload='audio',
                                   descriptor=_descriptor(record, envelope)) for i in range(len(takes))]
        # Record the whole inventory first: one take that cannot be fetched
        # must not erase the facts of the takes after it.
        cached = [await on_output(output) if on_output else None for output in outputs]
        primary, missing = None, []
        for output, take, blob in zip(outputs, takes, cached, strict=True):
            error = None
            if blob is None:
                try:
                    blob = await self._audio(handle.job_ref or '', take['index'])
                except (httpx.HTTPError, EngineFailure) as exc:
                    error = f'Take {take["index"] + 1} could not be fetched from the runner: {exc}'
                    missing.append(error)
                if on_output:
                    await on_output(replace(output, data=blob, error=error))
            if blob is not None and primary is None:
                primary = replace(output, data=blob)
        if missing:
            raise EngineFailure('collection', 'Some takes could not be collected. Collected takes are kept. '
                                              'Collect again to retrieve this same job without generating.',
                                detail='; '.join(missing)[:800], charged_usd=0)
        return ExecutionResult(data=primary.data, payload='audio', filename=primary.filename,
                               primary_identity=primary.identity, descriptor=primary.descriptor, charged_usd=0.0,
                               detail={'runner_job_id': record.get('id'), 'seed': record.get('seed'),
                                       'elapsed_s': record.get('elapsed_s'), 'load_s': record.get('load_s'),
                                       'generate_s': record.get('generate_s'),
                                       'peak_vram_mb': record.get('peak_vram_mb')})

    async def _audio(self, job_ref: str, index: int) -> bytes:
        answer = await self._http.get(f'/jobs/{job_ref}/audio/{index}')
        if answer.status_code >= 400 or not answer.content:
            raise EngineFailure('collection', f'The runner returned {answer.status_code} with '
                                              f'{len(answer.content)} bytes for take {index + 1}.')
        return answer.content


def reference(slot_id: str, attempt: int) -> str:
    """The client reference that names one attempt's job at the runner."""
    return f'{slot_id}:{attempt}'


def _read(path: str) -> bytes:
    with open(path, 'rb') as handle:
        return handle.read()


def _detail(answer: httpx.Response) -> str:
    try:
        return str((answer.json() or {}).get('detail') or answer.text)[:600]
    except ValueError:
        return answer.text[:600]


def _descriptor(record: dict, envelope: dict) -> dict:
    """What the executor reports about each take: the words spoken, the voice
    and the run facts. The server measures the bytes itself."""
    if record.get('kind') == 'voice-design':
        voice = {'provider': PROVIDER, 'kind': 'designed', 'generated_voice_id': None}
    else:
        voice = {'provider': PROVIDER, 'kind': 'cloned',
                 'reference_node_id': (envelope.get('voice') or {}).get('source_node_id'),
                 'clone_mode': record.get('clone_mode')}
    return {'audio': {'text': record.get('text'), 'voice': voice, 'alignment': None},
            'runner_job_id': record.get('id'), 'seed': record.get('seed'), 'model': record.get('model'),
            'charged_micro_usd': 0}


def forbidden(voice: dict) -> str | None:
    """Why this voice may not feed the local engine: the terms its provider's
    engine rows carry on their outputs, in plain words; None when it may."""
    provider = (voice.get('recorded') or {}).get('provider')
    if not provider or provider == PROVIDER:
        return None
    for row in audio.engines().values():
        if row.get('provider') == provider and (terms := audio.terms(row)):
            return audio.refusal(terms, PROVIDER)
    return None


def build_executor() -> VoiceRunnerExecutor:
    return VoiceRunnerExecutor()


async def startup_check(sched, benches: dict) -> None:
    """A runner that is not answering at boot benches this provider with the
    command that fixes it, and the cockpit boots anyway. The bench never covers
    the image engines (law 1). A bench this check placed heals when the runner
    answers again."""
    if not any(row.get('provider') == PROVIDER and row.get('enabled') for row in audio.engines().values()):
        return
    scope = f'provider:{PROVIDER}'
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            up = (await client.get(f'{config.VOICE_RUNNER_URL}/health')).status_code == 200
    except httpx.HTTPError:
        up = False
    found = benches.get(scope)
    if not up and found is None:
        await sched.bench(scope, kind='unreachable',
                          cause=f'Startup check: the local voice runner is not answering at {config.VOICE_RUNNER_URL}. '
                                f'{START_HINT} Then resume it.')
    elif up and found and found.get('kind') == 'unreachable':
        await sched.unbench(scope)
