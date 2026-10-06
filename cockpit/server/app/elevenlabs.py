"""ElevenLabs: Voice Design, speech and sound effects as audio engines.

This adapter turns a frozen audio envelope (`crexil_audio.compile_request`) into
one ElevenLabs conversation over plain HTTP and reports what came back. It owns
no database and no slot state: takes, the provider receipt and the charge reach
the server through `DispatchRequest.on_output` and the returned result.

Guarantees:
- The request is derived from the frozen envelope alone: model, text, controls,
  fixed fields and seed. The exact request sent (without the key) is kept in the
  provider receipt and on every take.
- Nothing retries here (law 9). A refusal becomes one `EngineFailure` whose kind
  the scheduler shows and acts on; a rate limit carries the provider's window.
- Voice Design always sends its preview text and never asks ElevenLabs to choose
  it, and sound effects always send their duration, so each price is known
  before the call (law 22). Speech keeps `enable_logging` on, so ElevenLabs'
  history can return a take whose response was lost.
- Every take arrives as PCM WAV: raw PCM is wrapped sample for sample.
- Each attempt reports its charge, which settles its reservation: the usage
  ElevenLabs bills (the characters of the text sent, or the seconds of sound
  requested) priced at the published tariff the attempt was frozen with, by the
  budget's own arithmetic. The provider's own counts (`character-cost`, history
  character deltas) are kept beside it, unconverted.
- A failure before generation carries a zero charge ("nothing was charged"). A
  lost response carries none, and speech first asks history for the take.

Never: uses the ElevenLabs SDK (it retries silently), follows a redirect, sends
or records the key, sends another engine's audio to ElevenLabs, deletes a voice,
or turns on `should_enhance` or `played_not_selected_voice_ids` on its own.

ElevenLabs accepts seeds from 0 to 4294967295. A frozen seed outside that range
is sent reduced modulo 2**32; the receipt records the seed actually sent.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

from . import audio, config, crexil_audio, crexil_budget
from .executors import (CollectedOutput, DispatchRequest, EngineFailure,
                        ExecutionResult, ExternalHandle, Lane, Reconciliation)
from .openrouter import retry_after_from

PROVIDER = 'elevenlabs'
LABEL = 'ElevenLabs'
# At or below every plan's published limit per model family (the free plan's
# 2), so the shared permit never asks for more than an account allows. The
# provider's own counts are recorded on each receipt.
MAX_IN_FLIGHT = 2
SEED_RANGE = 2 ** 32
# Query parameters among a row's fixed fields; every other fixed field is body.
QUERY_FIELDS = ('output_format', 'enable_logging')
RESPONSE_HEADERS = ('request-id', 'history-item-id', 'character-cost', 'x-trace-id',
                    'current-concurrent-requests', 'maximum-concurrent-requests', 'content-type',
                    'retry-after', 'retry-after-ms', 'x-ratelimit-reset')
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
# History is written shortly after a generation, so a lost speech response is
# looked for a few times with a gap; never by generating again.
HISTORY_ASKS = 3
HISTORY_GAP_S = 2.0
HISTORY_PAGES = 3
HISTORY_SLACK_S = 5
ACCOUNT_TTL_S = 60.0
NOTHING_SENT = 'Nothing was sent.'
NOT_CHARGED = 'Nothing was charged.'
UNVERIFIED = 'Its charge is unverified.'


class _Uncertain(Exception):
    """The request left this server and its answer was lost."""

    def __init__(self, message: str, *, status: int | None = None, request_id: str | None = None,
                 detail: str | None = None):
        super().__init__(message)
        self.status, self.request_id, self.detail = status, request_id, detail


@dataclass
class _Take:
    """One take as delivered: its bytes, their format, and what it says."""
    raw: bytes
    fmt: str
    spoken: dict
    provider: dict


@dataclass
class _Answer:
    """One provider answer: status, the headers kept for the receipt, the body."""
    status: int
    headers: dict
    content: bytes

    def json(self):
        try:
            return json.loads(self.content)
        except ValueError:
            return None


def _seed(value) -> int | None:
    return None if value is None else int(value) % SEED_RANGE


def _wav(raw: bytes, fmt: str) -> bytes:
    """A take's bytes as PCM WAV: `wav` arrives wrapped, `pcm_*` is wrapped
    here as 16-bit little-endian mono at the format's rate."""
    if fmt.startswith('wav'):
        return raw
    if not _measurable(fmt):
        raise ValueError(f'{fmt} is not a PCM format.')
    return audio.wrap_pcm(raw, sample_rate=int(fmt.split('_', 1)[1]), channels=1, sample_width=2)


def _measurable(output_format) -> bool:
    if not isinstance(output_format, str) or '_' not in output_format:
        return False
    kind, _, rate = output_format.partition('_')
    return kind in ('pcm', 'wav') and rate.isdigit()


def _reason(answer: _Answer) -> tuple[str, str, str]:
    """ElevenLabs' (type, code, message) from either of its error shapes."""
    body = answer.json()
    detail = body.get('detail') if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return (str(detail.get('type') or ''), str(detail.get('code') or detail.get('status') or ''),
                str(detail.get('message') or '').strip())
    if isinstance(detail, list):
        words = '; '.join(str(item.get('msg', item)) for item in detail if isinstance(item, (dict, str)))
        return 'validation_error', 'invalid_parameters', words
    if isinstance(detail, str):
        return '', '', detail.strip()
    return '', '', answer.content[:300].decode('utf-8', 'replace').strip()


def _kept(headers: httpx.Headers) -> dict:
    """The answer's headers worth a receipt, by their lower-case names."""
    return {name: headers[name] for name in RESPONSE_HEADERS if name in headers}


def _retry_after(headers: dict) -> float | None:
    """The provider's window on a rate limit, in seconds, or None."""
    millis = headers.get('retry-after-ms')
    if millis:
        with suppress(ValueError):
            return max(0.0, float(millis) / 1000.0)
    return retry_after_from(httpx.Headers(headers))


def _refusal(answer: _Answer, verb: str) -> EngineFailure | _Uncertain:
    """What a non-success answer means (failure-cases §2.1). A refusal before
    generation carries a zero charge; a server error leaves the charge open."""
    kind_, code, message = _reason(answer)
    said = f' ElevenLabs said: {message}' if message else ''
    status, rid = answer.status, answer.headers.get('request-id')
    detail = json.dumps({'status': status, 'type': kind_, 'code': code, 'message': message,
                         'request_id': rid})[:1600]

    def failed(kind: str, words: str, charged: float | None = 0.0, **extra) -> EngineFailure:
        return EngineFailure(kind, words, status=status, detail=detail, charged_usd=charged, job_ref=rid, **extra)

    if 300 <= status < 400:
        return failed('error', f'ElevenLabs answered with a redirect, which is never followed. {NOT_CHARGED}')
    if status == 401 and code == 'quota_exceeded':
        return failed('dead_key', f"ElevenLabs says this key's quota is used up. {NOT_CHARGED}{said}")
    if status == 401:
        return failed('dead_key', f'ElevenLabs turned the key away. {NOT_CHARGED}{said}')
    if status == 402:
        return failed('dead_key', f'The ElevenLabs balance is empty. {NOT_CHARGED}{said}')
    if 'moderation' in f'{kind_} {code} {message}'.lower():
        return failed('moderation', f'ElevenLabs refused this text under its content rules. {NOT_CHARGED}{said}')
    if status == 403:
        # A plan or key that cannot use this feature: a capability, never moderation.
        return failed('error', f'This ElevenLabs plan or key cannot use this feature. {NOT_CHARGED}{said}')
    if status == 404:
        missing = 'voice' if 'voice' in code or verb == 'speak' else 'resource'
        return failed('error', f'ElevenLabs no longer has this {missing}; it may have been deleted from the '
                               f'account. {NOT_CHARGED}{said}')
    if status == 409:
        return failed('rate_limit', f'ElevenLabs is still generating this preview. {NOT_CHARGED}{said}',
                      retry_after_s=_retry_after(answer.headers))
    if status == 429:
        busy = code in ('concurrent_limit_exceeded', 'too_many_concurrent_requests')
        words = ('ElevenLabs is at its limit of requests in flight for this plan.' if busy else
                 'ElevenLabs asked us to slow down.')
        return failed('rate_limit', f'{words} {NOT_CHARGED}{said}', retry_after_s=_retry_after(answer.headers))
    if status == 503:
        return failed('model_down', f'ElevenLabs is unavailable or under maintenance. {NOT_CHARGED}{said}')
    if status in (500, 502, 504):
        return _Uncertain(f'ElevenLabs failed while answering (HTTP {status}). {UNVERIFIED}{said}',
                          status=status, request_id=rid, detail=detail)
    if 400 <= status < 500:
        return failed('error', f'ElevenLabs refused the request. {NOT_CHARGED}{said}')
    return _Uncertain(f'ElevenLabs answered with HTTP {status}. {UNVERIFIED}{said}', status=status,
                      request_id=rid, detail=detail)


def _charge(envelope: dict, usage: dict) -> tuple[int | None, float | None, str | None]:
    """The attempt's charge: the billed usage priced at the published tariff it
    was frozen with, by the same arithmetic as its reservation. An unpriceable
    usage reports no charge and says why."""
    try:
        micro = crexil_budget.audio_reservation(envelope.get('tariff'), usage)
    except crexil_budget.UnknownPrice as exc:
        return None, None, str(exc)
    return micro, float(Decimal(micro) / crexil_budget.MICRO_USD), None


def _split(envelope: dict) -> tuple[dict, dict]:
    """The row's fixed fields, divided between query and body."""
    fixed = dict(envelope.get('fixed') or {})
    query = {key: fixed.pop(key) for key in QUERY_FIELDS if key in fixed}
    return query, fixed


def _speech_request(envelope: dict, voice_id: str | None) -> tuple[str, dict, dict]:
    """The speech call a frozen envelope makes: path, query and body."""
    controls = envelope.get('controls') or {}
    query, fixed = _split(envelope)
    # History recovers a lost take only while logging is on.
    query['enable_logging'] = 'true'
    body = {'text': envelope['text'], 'model_id': envelope['model_id'], **fixed}
    settings = {key: controls[key] for key in ('stability', 'similarity_boost') if key in controls}
    if settings:
        body['voice_settings'] = settings
    if (seed := _seed(envelope.get('seed'))) is not None:
        body['seed'] = seed
    return f'/v1/text-to-speech/{voice_id}/with-timestamps', query, body


def _sent(method: str, path: str, query: dict, body: dict | None) -> dict:
    """A request as recorded: everything that was sent except the key."""
    return {'method': method, 'path': path, 'query': dict(query), 'body': body}


class ElevenLabsExecutor:
    """The external executor for every ElevenLabs audio engine."""

    name = PROVIDER
    label = LABEL
    lane: Lane = 'external'
    max_concurrency = MAX_IN_FLIGHT

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url or config.ELEVENLABS_URL).rstrip('/')
        self._api_key = api_key
        # Speech saves a designed preview once; concurrent attempts share it.
        self._saved: dict[str, str] = {}
        self._saving: dict[str, asyncio.Lock] = {}
        self._account: tuple[float, dict] | None = None
        self._client = httpx.AsyncClient(base_url=self.base_url, transport=transport, follow_redirects=False,
                                         timeout=httpx.Timeout(300.0, connect=10.0), trust_env=False)

    @property
    def key(self) -> str:
        return (config.ELEVENLABS_API_KEY if self._api_key is None else self._api_key) or ''

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        await self._client.aclose()

    # --- the executor protocol ---

    async def start(self, req: DispatchRequest) -> ExternalHandle:
        return ExternalHandle(task=asyncio.create_task(self._run(req), name=f'elevenlabs-{req.slot_id}'))

    async def wait(self, handle: ExternalHandle) -> ExecutionResult:
        try:
            return await handle.task
        except asyncio.CancelledError:
            handle.task.cancel()
            raise

    async def cancel(self, handle: ExternalHandle) -> None:
        if handle.task:
            handle.task.cancel()

    async def reconcile(self, _slot: dict, *, request: DispatchRequest | None = None) -> Reconciliation:  # noqa: ARG002
        return Reconciliation('unverifiable')

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None

    async def collect_existing(self, slot: dict, *, request: DispatchRequest) -> Reconciliation:
        """Retrieve an earlier attempt without generating again. Speech is
        looked up in ElevenLabs' history; Voice Design and sound effects keep
        no history there, so their lost results cannot be collected."""
        envelope = request.external or {}
        if envelope.get('verb') != 'speak':
            return Reconciliation('failed', failure_kind='collection', cause=(
                f'ElevenLabs keeps no record of voice designs or sound effects, so this result cannot be '
                f'collected. {UNVERIFIED} Nothing was generated again.'))
        try:
            started = datetime.fromisoformat(slot['started_at']).timestamp() if slot.get('started_at') else None
        except ValueError:
            started = None
        try:
            found = await self._recover_speech(request, envelope, self._known_voice(envelope),
                                               started if started is not None else time.time() - 3600,
                                               asks=1)
        except EngineFailure as exc:
            return Reconciliation('failed', failure_kind=exc.kind, cause=str(exc))
        if isinstance(found, ExecutionResult):
            return Reconciliation('landed', result=found)
        return Reconciliation('failed', failure_kind='collection', cause=found)

    # --- one attempt ---

    async def _run(self, req: DispatchRequest) -> ExecutionResult:
        envelope = req.external or {}
        await self._preflight(req, envelope)
        if req.before_send is not None:
            try:
                await req.before_send()
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                raise EngineFailure('preflight', str(exc), charged_usd=0) from exc
        verb = envelope['verb']
        if verb == 'voice-design':
            return await self._design(req, envelope)
        if verb == 'speak':
            return await self._speak(req, envelope)
        return await self._sound(req, envelope)

    async def _preflight(self, req: DispatchRequest, envelope: dict) -> None:
        """Refuse before anything leaves this server, in plain words."""
        def refuse(words: str) -> EngineFailure:
            return EngineFailure('preflight', f'{words} {NOTHING_SENT}', charged_usd=0)

        verb = envelope.get('verb')
        if envelope.get('transport') != 'audio' or envelope.get('provider') != PROVIDER or verb not in audio.VERBS:
            raise refuse('This request was not prepared for ElevenLabs.')
        if not self.key:
            raise refuse('No ElevenLabs key is set. Set ELEVENLABS_API_KEY in .env, then restart.')
        if str(envelope.get('destination') or '').rstrip('/') != self.base_url:
            raise refuse('The ElevenLabs address changed since review. Prepare a new reviewed request.')
        row = audio.engines().get(envelope.get('engine'))
        if row is None or not row.get('enabled', False) or row.get('provider') != PROVIDER:
            raise refuse(f"{envelope.get('engine')} is no longer an enabled ElevenLabs engine.")
        if crexil_audio.tariff(row, verb) != envelope.get('tariff'):
            raise refuse(f"{row.get('name', row['id'])}'s published tariff changed since review. "
                         'Review a new request.')
        if not _measurable((envelope.get('fixed') or {}).get('output_format')):
            raise refuse(f"{row.get('name', row['id'])} asks for an output format this server cannot measure. "
                         'Configure a pcm_ or wav_ format.')
        if verb == 'voice-design' and not isinstance((envelope.get('controls') or {}).get('preview_text'), str):
            raise refuse('Voice Design needs its preview line, so its price is known before the call.')
        if verb == 'sfx' and (envelope.get('controls') or {}).get('duration_seconds') is None:
            raise refuse('A sound effect needs its duration, so its price is known before the call.')
        if verb == 'speak':
            for source in req.sources:
                try:
                    blob = await asyncio.to_thread(Path(source.path).read_bytes)
                except OSError as exc:
                    raise EngineFailure('source_missing', 'A prepared source is unavailable. Restore it before '
                                        'trying again.', charged_usd=0) from exc
                if hashlib.sha256(blob).hexdigest() != source.sha256:
                    raise EngineFailure('source_changed', 'A prepared source changed. Review its current bytes '
                                        'before trying again.', charged_usd=0)
            recorded = ((envelope.get('voice') or {}).get('recorded') or {})
            if recorded.get('provider') != PROVIDER and not (envelope.get('voice') or {}).get('voice_id'):
                raise refuse('ElevenLabs speaks only with voices designed or saved at ElevenLabs.')
            if not self._known_voice(envelope) and not (recorded.get('kind') == 'designed'
                                                        and recorded.get('generated_voice_id')):
                raise refuse('This voice take names no ElevenLabs voice. Choose a take designed or saved at '
                             'ElevenLabs.')

    async def _design(self, req: DispatchRequest, envelope: dict) -> ExecutionResult:
        controls = envelope.get('controls') or {}
        query, fixed = _split(envelope)
        # Prompt rewriting sends the artist's words to another model; never by configuration.
        fixed.pop('should_enhance', None)
        body = {'voice_description': envelope['text'], 'model_id': envelope['model_id'],
                'text': controls['preview_text'], **fixed,
                **{key: controls[key] for key in ('loudness', 'guidance_scale', 'quality') if key in controls}}
        # The price is the preview text's: ElevenLabs never chooses it (law 22).
        body['auto_generate_text'] = False
        if (seed := _seed(envelope.get('seed'))) is not None:
            body['seed'] = seed
        answer, sent = await self._send(req, 'POST', '/v1/text-to-voice/design', query, body)
        reply = answer.json()
        reply = reply if isinstance(reply, dict) else {}
        spoken = reply.get('text') if isinstance(reply.get('text'), str) else controls['preview_text']
        fmt = query.get('output_format', '')
        takes, problems = [], []
        for index, preview in enumerate(reply.get('previews') or []):
            try:
                raw = base64.b64decode(preview['audio_base_64'], validate=True)
            except (KeyError, TypeError, binascii.Error):
                problems.append(f'Preview {index + 1} carried no readable audio.')
                continue
            voice = {'provider': PROVIDER, 'kind': 'designed', 'generated_voice_id': preview.get('generated_voice_id'),
                     'description': envelope['text'], 'model_id': envelope['model_id']}
            takes.append(_Take(raw, fmt, {'text': spoken, 'voice': voice, 'alignment': None},
                               {key: preview.get(key) for key in ('media_type', 'duration_secs', 'language')}))
        facts = {'previews': [{'generated_voice_id': t.spoken['voice']['generated_voice_id'], **t.provider}
                              for t in takes], 'spoken_text': spoken, 'problems': problems}
        return await self._land(req, envelope, answer, sent, takes, facts,
                                usage={'characters': len(body['text'])})

    async def _speak(self, req: DispatchRequest, envelope: dict) -> ExecutionResult:
        voice_id, generated = await self._voice_for(req, envelope)
        path, query, body = _speech_request(envelope, voice_id)
        started = time.time()
        try:
            answer, sent = await self._send(req, 'POST', path, query, body, uncertain=True)
        except _Uncertain as lost:
            try:
                found = await self._recover_speech(req, envelope, voice_id, started, asks=HISTORY_ASKS)
            except EngineFailure as unread:
                found = f'ElevenLabs history could not be checked ({unread}).'
            if isinstance(found, ExecutionResult):
                return found
            raise EngineFailure('timeout', f'{lost} {found}', status=lost.status, detail=lost.detail,
                                charged_usd=None, job_ref=lost.request_id) from None
        reply = answer.json()
        reply = reply if isinstance(reply, dict) else {}
        voice = {'provider': PROVIDER, 'kind': 'saved', 'voice_id': voice_id, 'from_generated_voice_id': generated}
        takes = []
        try:
            raw = base64.b64decode(reply['audio_base64'], validate=True)
        except (KeyError, TypeError, binascii.Error):
            pass
        else:
            takes.append(_Take(raw, query.get('output_format', ''),
                               {'text': envelope['text'], 'voice': voice, 'alignment': reply.get('alignment')}, {}))
        return await self._land(req, envelope, answer, sent, takes, {'voice_id': voice_id},
                                usage={'characters': len(body['text'])})

    async def _sound(self, req: DispatchRequest, envelope: dict) -> ExecutionResult:
        controls = envelope.get('controls') or {}
        query, fixed = _split(envelope)
        body = {'text': envelope['text'], 'model_id': envelope['model_id'], **fixed,
                'duration_seconds': controls['duration_seconds'],
                **{key: controls[key] for key in ('prompt_influence', 'loop') if key in controls}}
        answer, sent = await self._send(req, 'POST', '/v1/sound-generation', query, body)
        takes = ([_Take(answer.content, query.get('output_format', ''),
                        {'text': None, 'voice': None, 'alignment': None}, {})] if answer.content else [])
        return await self._land(req, envelope, answer, sent, takes, {},
                                usage={'seconds': body['duration_seconds']})

    # --- voices ---

    def _known_voice(self, envelope: dict) -> str | None:
        voice = envelope.get('voice') or {}
        recorded = voice.get('recorded') or {}
        if voice.get('voice_id'):
            return voice['voice_id']
        if recorded.get('kind') == 'saved' and recorded.get('voice_id'):
            return recorded['voice_id']
        return self._saved.get(recorded.get('generated_voice_id') or '')

    async def _voice_for(self, req: DispatchRequest, envelope: dict) -> tuple[str, str | None]:
        """The ElevenLabs voice this speech reads with. A designed preview is
        saved as a voice once, which uses one of the account's voice slots."""
        voice = envelope.get('voice') or {}
        recorded = voice.get('recorded') or {}
        generated = recorded.get('generated_voice_id') or recorded.get('from_generated_voice_id')
        known = self._known_voice(envelope)
        if known:
            return known, generated
        async with self._saving.setdefault(generated, asyncio.Lock()):
            if generated in self._saved:
                return self._saved[generated], generated
            body = {'voice_name': f'Crexil {generated[:12]}',
                    'voice_description': recorded.get('description') or
                    'A voice designed with ElevenLabs Voice Design and chosen in Crexil.',
                    'generated_voice_id': generated,
                    'labels': {'source': 'crexil', 'crexil_generated_voice_id': generated}}
            answer, _ = await self._send(req, 'POST', '/v1/text-to-voice', {}, body)
            reply = answer.json()
            reply = reply if isinstance(reply, dict) else {}
            if not isinstance(reply.get('voice_id'), str):
                raise EngineFailure('error', 'ElevenLabs saved the designed voice but named no voice id. '
                                    f'{NOT_CHARGED}', charged_usd=0, job_ref=answer.headers.get('request-id'))
            self._saved[generated] = reply['voice_id']
            return reply['voice_id'], generated

    # --- transport ---

    def _headers(self) -> dict:
        return {'xi-api-key': self.key, 'Accept': '*/*'}

    async def _send(self, req: DispatchRequest, method: str, path: str, query: dict, body: dict | None,
                    *, uncertain: bool = False) -> tuple[_Answer, dict]:
        """One call. Returns the answer and the request as sent (without the
        key); raises the failure its answer means. With `uncertain`, a lost
        answer raises `_Uncertain` for the caller to recover; otherwise it is a
        timeout whose charge is unverified."""
        sent = _sent(method, path, query, body)
        try:
            response = await self._client.request(method, path, params=query or None, json=body,
                                                  headers=self._headers())
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
                httpx.WriteError, httpx.WriteTimeout, httpx.UnsupportedProtocol) as exc:
            raise EngineFailure('unreachable', f'ElevenLabs could not be reached at {self.base_url}. '
                                f'{NOTHING_SENT}', detail=type(exc).__name__, charged_usd=0) from exc
        except httpx.HTTPError as exc:
            lost = _Uncertain(f'The connection to ElevenLabs dropped before its answer arrived. {UNVERIFIED}',
                              detail=type(exc).__name__)
            if uncertain:
                raise lost from exc
            raise self._lost(req, lost) from exc
        answer = _Answer(response.status_code, _kept(response.headers), response.content)
        if len(answer.content) > MAX_RESPONSE_BYTES:
            raise EngineFailure('error', f'ElevenLabs answered with more data than this server accepts. {UNVERIFIED}',
                                charged_usd=None, job_ref=answer.headers.get('request-id'))
        if response.status_code == 200:
            return answer, sent
        failure = _refusal(answer, (req.external or {}).get('verb', ''))
        if isinstance(failure, _Uncertain):
            if uncertain:
                raise failure
            raise self._lost(req, failure)
        raise failure

    def _lost(self, req: DispatchRequest, lost: _Uncertain) -> EngineFailure:
        verb = (req.external or {}).get('verb')
        why = (' ElevenLabs keeps no record of voice designs or sound effects, so this result cannot be collected.'
               if verb in ('voice-design', 'sfx') else '')
        return EngineFailure('timeout', f'{lost}{why}', status=lost.status, detail=lost.detail,
                             charged_usd=None, job_ref=lost.request_id)

    # --- landing ---

    async def _emit(self, req: DispatchRequest, output: CollectedOutput) -> None:
        if req.on_output is not None:
            await req.on_output(output)

    async def _land(self, req: DispatchRequest, envelope: dict, answer: _Answer, sent: dict,
                    takes: list[_Take], facts: dict, *, usage: dict, recovered: dict | None = None
                    ) -> ExecutionResult:
        """Report the receipt (and with it the charge), then every take, then
        return the first take as the attempt's primary result. `usage` is what
        ElevenLabs bills for the call: the characters of the text sent, or the
        seconds of sound requested."""
        micro, usd, unpriced = _charge(envelope, usage)
        rid = answer.headers.get('request-id') or (recovered or {}).get('request_id')
        output_format = (envelope.get('fixed') or {}).get('output_format')
        external = {'transport': 'audio', 'provider': PROVIDER, 'engine': envelope.get('engine'),
                    'verb': envelope.get('verb'), 'model_id': envelope.get('model_id'), 'request': sent,
                    'request_id': rid, 'history_item_id': answer.headers.get('history-item-id') or
                    (recovered or {}).get('history_item_id'), 'output_format': output_format,
                    'charge_basis': 'published_tariff', 'charged_micro_usd': micro,
                    'reported_cost_usd': usd, 'generation_id': rid}
        receipt = {'provider': PROVIDER, 'engine': envelope.get('engine'), 'verb': envelope.get('verb'),
                   'request': sent, 'response': {'status': answer.status, 'headers': answer.headers},
                   'charge': {'basis': 'published_tariff', 'tariff': envelope.get('tariff'), 'usage': usage,
                              'charged_micro_usd': micro, 'unpriced': unpriced,
                              'provider_character_cost': answer.headers.get('character-cost')},
                   'takes': len(takes), **facts}
        if recovered:
            receipt['recovered_from_history'] = recovered
        await self._emit(req, CollectedOutput(identity='provider:receipt', filename='provider-response.json',
            data=json.dumps(receipt, sort_keys=True).encode(), payload=None, role='provider receipt',
            descriptor={'charge_basis': 'published_tariff', 'charged_micro_usd': micro}, external=external))
        if not takes:
            raise EngineFailure('charged_blank', 'ElevenLabs answered and billed, but returned no audio.',
                                detail=json.dumps(facts)[:1600], charged_usd=usd, job_ref=rid)
        primary = None
        take_external = {k: v for k, v in external.items() if k not in ('reported_cost_usd', 'generation_id')}
        for index, take in enumerate(takes):
            try:
                data, filename = _wav(take.raw, take.fmt), f'take-{index + 1}.wav'
            except ValueError:
                # Kept as delivered; collection reports why it cannot be measured (law 7).
                data, filename = take.raw, f'take-{index + 1}.raw'
            descriptor = {'audio': take.spoken, 'provider': take.provider} if take.provider else {'audio': take.spoken}
            output = CollectedOutput(identity=f'take:{index}', filename=filename, data=data, payload='audio',
                                     descriptor=descriptor, external=take_external)
            await self._emit(req, output)
            if primary is None:
                primary = ExecutionResult(data=data, payload='audio', filename=filename, charged_usd=usd,
                                          primary_identity=output.identity, descriptor=descriptor,
                                          external=take_external)
        return primary

    # --- history ---

    def _same_speech(self, item: dict, envelope: dict, voice_id: str | None, since: float) -> bool:
        if not isinstance(item, dict) or item.get('text') != envelope.get('text'):
            return False
        if item.get('model_id') not in (None, envelope.get('model_id')):
            return False
        if voice_id and item.get('voice_id') not in (None, voice_id):
            return False
        if isinstance(item.get('date_unix'), (int, float)) and item['date_unix'] < since - HISTORY_SLACK_S:
            return False
        wanted = (envelope.get('fixed') or {}).get('output_format')
        if wanted and item.get('output_format') not in (None, wanted):
            return False
        settings = item.get('settings') if isinstance(item.get('settings'), dict) else {}
        controls = envelope.get('controls') or {}
        for key in ('stability', 'similarity_boost'):
            if key in controls and key in settings and isinstance(settings[key], (int, float)) \
                    and not math.isclose(float(settings[key]), float(controls[key]), abs_tol=1e-9):
                return False
        return True

    async def _history(self, query: dict) -> list[dict]:
        found: list[dict] = []
        for _ in range(HISTORY_PAGES):
            answer, _read = await self._send_read('/v1/history', query)
            reply = answer.json()
            reply = reply if isinstance(reply, dict) else {}
            found += [item for item in reply.get('history') or [] if isinstance(item, dict)]
            if not reply.get('has_more') or not reply.get('last_history_item_id'):
                break
            query = {**query, 'start_after_history_item_id': reply['last_history_item_id']}
        return found

    async def _send_read(self, path: str, query: dict | None = None) -> tuple[_Answer, dict]:
        """A free read: history and its audio. A failure here never charges."""
        try:
            response = await self._client.get(path, params=query or None, headers=self._headers())
        except httpx.HTTPError as exc:
            raise EngineFailure('unreachable', f'ElevenLabs history could not be read. {NOT_CHARGED}',
                                detail=type(exc).__name__, charged_usd=0) from exc
        answer = _Answer(response.status_code, _kept(response.headers), response.content)
        if response.status_code != 200:
            failure = _refusal(answer, 'history')
            if isinstance(failure, _Uncertain):
                raise EngineFailure('error', f'ElevenLabs history could not be read (HTTP {answer.status}).',
                                    status=answer.status, charged_usd=0)
            raise failure
        return answer, {'method': 'GET', 'path': path, 'query': dict(query or {})}

    async def _recover_speech(self, req: DispatchRequest, envelope: dict, voice_id: str | None, since: float,
                              *, asks: int) -> ExecutionResult | str:
        """Look for this exact line in ElevenLabs' history and land it into the
        original attempt. Never generates again. Returns the landed result, or
        the plain reason nothing was bound."""
        query = {'page_size': 100, 'source': 'TTS', 'model_id': envelope.get('model_id'),
                 'date_after_unix': int(since) - HISTORY_SLACK_S, 'sort_direction': 'asc'}
        if voice_id:
            query['voice_id'] = voice_id
        matches: list[dict] = []
        for ask in range(asks):
            if ask:
                await asyncio.sleep(HISTORY_GAP_S)
            matches = [item for item in await self._history(query) if self._same_speech(item, envelope, voice_id, since)]
            if matches:
                break
        if not matches:
            return 'ElevenLabs history has no take for this line yet. Nothing was generated again.'
        if len(matches) > 1:
            return (f'ElevenLabs history holds {len(matches)} takes that match this line, so none was bound '
                    'automatically. Nothing was generated again.')
        item = matches[0]
        hid = item.get('history_item_id')
        answer, fetched = await self._send_read(f'/v1/history/{hid}/audio')
        raw = answer.content
        fmt = 'wav' if raw[:4] == b'RIFF' else (item.get('output_format') or
                                                (envelope.get('fixed') or {}).get('output_format') or '')
        delta = None
        if isinstance(item.get('character_count_change_to'), int) and \
                isinstance(item.get('character_count_change_from'), int):
            delta = item['character_count_change_to'] - item['character_count_change_from']
        recovered = {'history_item_id': hid, 'request_id': item.get('request_id'), 'date_unix': item.get('date_unix'),
                     'provider_character_count': delta, 'output_format': item.get('output_format'),
                     'read': fetched, 'generated_again': False}
        voice = {'provider': PROVIDER, 'kind': 'saved', 'voice_id': item.get('voice_id') or voice_id,
                 'from_generated_voice_id': ((envelope.get('voice') or {}).get('recorded') or {}).get(
                     'generated_voice_id')}
        alignment = item.get('alignments') if isinstance(item.get('alignments'), dict) else None
        take = _Take(raw, fmt, {'text': envelope.get('text'), 'voice': voice, 'alignment': alignment}, {})
        path, query, body = _speech_request(envelope, voice['voice_id'])
        return await self._land(req, envelope, answer, _sent('POST', path, query, body), [take] if raw else [], {},
                                usage={'characters': len(body['text'])}, recovered=recovered)

    # --- the account ---

    async def account(self, *, fresh: bool = False) -> dict:
        """The key's account, read for free and kept for a minute; never raises.
        `rejected` means ElevenLabs turned the key away."""
        if not fresh and self._account and time.monotonic() - self._account[0] < ACCOUNT_TTL_S:
            return self._account[1]
        if not self.key:
            return {'ok': False, 'rejected': False, 'error': 'no key'}
        try:
            response = await self._client.get('/v1/user/subscription', headers=self._headers(), timeout=5.0)
        except httpx.HTTPError:
            return {'ok': False, 'rejected': False, 'error': 'unreachable'}
        if response.status_code in (401, 402):
            found = {'ok': False, 'rejected': True, 'error': f'key rejected ({response.status_code})'}
        elif response.status_code != 200:
            found = {'ok': False, 'rejected': False, 'error': f'HTTP {response.status_code}'}
        else:
            try:
                body = response.json()
            except ValueError:
                body = {}
            body = body if isinstance(body, dict) else {}
            found = {'ok': True, 'rejected': False, **{key: body.get(key) for key in (
                'tier', 'voice_slots_used', 'voice_limit', 'character_count', 'character_limit')}}
        self._account = (time.monotonic(), found)
        return found


def build_executor() -> ElevenLabsExecutor:
    return ElevenLabsExecutor()


async def connection(stack) -> dict:
    """The setup overview's ElevenLabs line: whether a key is present and, through
    the live executor, whether ElevenLabs accepts it. The key is never shown."""
    line = {'provider': PROVIDER, 'label': LABEL, 'ok': False, 'key_source': None}
    if config.CREXIL_PREVIEW:
        return {**line, 'detail': 'Disconnected in this isolated preview.'}
    if not config.ELEVENLABS_API_KEY:
        return {**line, 'detail': 'No key. Set ELEVENLABS_API_KEY in .env, then restart.'}
    line = {**line, 'key_source': 'env'}
    executor = stack.scheduler.executor(PROVIDER)
    if not isinstance(executor, ElevenLabsExecutor):
        return {**line, 'ok': True, 'detail': 'A key is set in the environment. It has not been checked.'}
    found = await executor.account()
    if found['ok']:
        slots = (f" Voice slots: {found['voice_slots_used']} of {found['voice_limit']} used."
                 if isinstance(found.get('voice_slots_used'), int) and isinstance(found.get('voice_limit'), int)
                 else '')
        plan = f" Plan: {found['tier']}." if isinstance(found.get('tier'), str) else ''
        return {**line, 'ok': True, 'detail': f'ElevenLabs accepts the key.{plan}{slots}'}
    if found.get('rejected'):
        return {**line, 'detail': 'ElevenLabs turned the key away. Replace ELEVENLABS_API_KEY in .env, then restart.'}
    return {**line, 'ok': None, 'detail': f"ElevenLabs could not be asked about the key ({found['error']})."}


async def startup_check(sched, benches: dict) -> None:
    """Bench ElevenLabs alone when its key is missing or turned away, and heal a
    stale bench from an earlier session when ElevenLabs accepts the key. The
    bench names this provider's own scope, never the external lane, so image
    takes beside it keep running (law 1). An unreachable account check benches
    nothing: the first real take reports it with a live cause."""
    scope = f'provider:{PROVIDER}'
    existing = benches.get(scope)
    if not config.ELEVENLABS_API_KEY:
        if existing is None:
            await sched.bench(scope, kind='no_key', cause='startup check: no ElevenLabs key. Set ELEVENLABS_API_KEY '
                              'in .env, then restart. A bench, not a crash.')
        return
    executor = sched.executor(PROVIDER)
    if not isinstance(executor, ElevenLabsExecutor):
        return
    found = await executor.account(fresh=True)
    if found['ok']:
        if existing and existing.get('kind') in ('no_key', 'unreachable', 'dead_key'):
            await sched.unbench(scope)
    elif found.get('rejected') and existing is None:
        await sched.bench(scope, kind='dead_key', cause='startup check: ElevenLabs turned the key away. Replace '
                          'ELEVENLABS_API_KEY in .env, then restart.')
