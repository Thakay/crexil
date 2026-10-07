"""Scripted fake executors, shared by the scheduler suite and the API tests,
which wire them through the real app via `wiring.EXECUTOR_FACTORY`, and a
stepping clock for the store."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.executors import (CollectedOutput, DispatchRequest, EngineFailure, ExecutionResult,
                           Handle, Reconciliation)


class Probe:
    def __init__(self):
        self.concurrent = 0
        self.max_concurrent = 0


@dataclass
class Step:
    delay_s: float = 0.0
    outcome: object = "land"          # "land" | EngineFailure | "hang"
    cost: float | None = None
    data: bytes = b"artifact"
    gate: asyncio.Event | None = None
    # the as-run record a real executor lands with (a mesh take's engine
    # args + validation + turntable); None keeps the slot's
    # own envelope, which is what an executor that reports nothing does
    submitted_graph: dict | None = None
    # what the engine called the file it wrote — the scheduler stages under
    # its suffix and promote keeps it, so a mesh fake lands `.glb`
    # exactly as MeshExecutor does. None is the image lane's own default.
    filename: str | None = None
    # artefacts written beside the take's own, keyed by suffix — a TRELLIS.2
    # mesh lands its voxel field this way
    sidecars: dict[str, bytes] | None = None


@dataclass
class FakeHandle(Handle):
    step: Step = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


class FakeExecutor:
    """Scripted executor: per-slot Step (or list of Steps, one per attempt).
    `probe` counts concurrent wait()s — the serial-lane invariant probe; pass
    one shared Probe to several executors to measure a whole lane."""

    def __init__(self, name, lane, script=None, default=None, probe=None):
        self.name, self.lane = name, lane
        self.script = dict(script or {})
        self.default = default or Step()
        self.probe = probe or Probe()
        self.starts: list[str] = []
        self.cancels: list[str] = []
        self.reconcile_map: dict[str, Reconciliation] = {}
        # B4: scripted answers to the bill re-asks, per job id:
        # None = "no answer yet"; `asks` records every re-ask made
        self.bills: dict[str, list] = {}
        self.asks: list[str] = []

    async def open(self):
        pass

    async def close(self):
        pass

    def _next_step(self, slot_id):
        s = self.script.get(slot_id, self.default)
        if isinstance(s, list):
            return s.pop(0) if len(s) > 1 else s[0]
        return s

    async def start(self, req: DispatchRequest) -> FakeHandle:
        self.starts.append(req.slot_id)
        step = self._next_step(req.slot_id)
        if isinstance(step.outcome, EngineFailure) and step.outcome.kind == "preflight":
            raise step.outcome          # refused before the paid call, as a real preflight is
        await guard(self, req)
        return FakeHandle(job_ref=f"job-{req.slot_id}-{len(self.starts)}", step=step)

    async def wait(self, h: FakeHandle) -> ExecutionResult:
        self.probe.concurrent += 1
        self.probe.max_concurrent = max(self.probe.max_concurrent,
                                        self.probe.concurrent)
        waiters = []
        try:
            step = h.step
            waiters = [asyncio.create_task(h.cancelled.wait())]
            timeout = step.delay_s
            if step.gate is not None:
                waiters.append(asyncio.create_task(step.gate.wait()))
                timeout = None
            elif step.outcome == "hang":
                timeout = None                 # only cancel releases
            done, pending = await asyncio.wait(
                waiters, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if h.cancelled.is_set():
                if self.lane == "external":     # mimic a cancelled HTTP task
                    raise asyncio.CancelledError()
                raise EngineFailure("interrupted", "interrupted by cancel")
            if isinstance(step.outcome, EngineFailure):
                raise step.outcome
            return ExecutionResult(
                data=step.data, charged_usd=step.cost,
                filename=step.filename,
                submitted_graph=step.submitted_graph,
                sidecars=step.sidecars,
                external=({"transport": "fake"} if self.lane == "external"
                          else None))
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            self.probe.concurrent -= 1

    async def cancel(self, h: FakeHandle):
        self.cancels.append(h.job_ref)
        h.cancelled.set()

    async def reconcile(self, slot, *, request=None):
        return self.reconcile_map.get(slot["id"], Reconciliation("gone"))

    async def verify_charge(self, job_ref):
        self.asks.append(job_ref)
        answers = self.bills.get(job_ref)
        return answers.pop(0) if answers else None


def fail(kind, msg="boom"):
    return EngineFailure(kind, msg)


async def guard(ex, req: DispatchRequest) -> None:
    """An external executor's last word before its paid call, as every real
    one says it: the server's guard, which records the send; a refusal sends
    nothing."""
    if ex.lane != "external" or req.before_send is None:
        return
    try:
        await req.before_send()
    except ValueError as exc:
        raise EngineFailure("preflight", str(exc), charged_usd=0) from exc


def tone_wav(seconds=0.5, rate=24000, pitch=220.0, amplitude=0.3, channels=1, width=2) -> bytes:
    """A real PCM WAV tone, so collection measures it as it measures any take."""
    import math
    import struct

    from app.audio import wrap_pcm
    full = (1 << (8 * width - 1)) - 1
    frames = bytearray()
    for i in range(int(seconds * rate)):
        value = int(amplitude * full * math.sin(2 * math.pi * pitch * i / rate))
        if width == 1:
            sample = struct.pack('<B', value + 128)
        elif width == 3:
            sample = value.to_bytes(3, 'little', signed=True)
        else:
            sample = struct.pack('<h' if width == 2 else '<i', value)
        frames += sample * channels
    return wrap_pcm(bytes(frames), sample_rate=rate, channels=channels, sample_width=width)


@dataclass
class AudioHandle(Handle):
    request: DispatchRequest | None = None


class FakeAudioExecutor:
    """Scripted audio provider. Each attempt returns the envelope's `takes`
    WAV takes through the real collector, with the voice facts a real
    adapter reports. `data` replaces the bytes, `failure` fails the attempt,
    `gate` holds it until set, and `max_concurrency` is the executor's own
    permit. `requests` records every request; `collected` every free
    collection the scheduler asked of it."""

    def __init__(self, name, lane, *, data=None, failure=None, gate=None, delay_s=0.0,
                 max_concurrency=None, cost=None):
        self.name, self.lane = name, lane
        self.data, self.failure, self.gate, self.delay_s, self.cost = data, failure, gate, delay_s, cost
        if max_concurrency:
            self.max_concurrency = max_concurrency
        self.requests: list[DispatchRequest] = []
        self.collected: list[str] = []
        self.probe = Probe()

    async def open(self):
        pass

    async def close(self):
        pass

    async def start(self, req):
        await guard(self, req)
        self.requests.append(req)
        return AudioHandle(job_ref=f'audio-{req.slot_id}-{len(self.requests)}', request=req)

    def _voice(self, envelope, index, slot_id):
        verb = envelope.get('verb')
        if verb == 'voice-design':
            return {'provider': self.name, 'kind': 'designed', 'generated_voice_id': f'gen-{slot_id}-{index}'}
        if verb == 'speak':
            recorded = (envelope.get('voice') or {}).get('recorded') or {}
            return {'provider': self.name, 'kind': 'saved' if self.name == 'elevenlabs' else 'cloned',
                    'voice_id': f'voice-{slot_id}', 'from_generated_voice_id': recorded.get('generated_voice_id'),
                    'reference_node_id': (envelope.get('voice') or {}).get('source_node_id')}
        return None

    async def wait(self, h):
        self.probe.concurrent += 1
        self.probe.max_concurrent = max(self.probe.max_concurrent, self.probe.concurrent)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.failure is not None:
                raise self.failure
            envelope = h.request.external or {}
            spoken = ((envelope.get('controls') or {}).get('preview_text') if envelope.get('verb') == 'voice-design'
                      else envelope.get('text') if envelope.get('verb') == 'speak' else None)
            primary = None
            for index in range(int(envelope.get('takes') or 1)):
                data = self.data if self.data is not None else tone_wav(pitch=180 + 40 * index)
                output = CollectedOutput(identity=f'take:{index}', filename=f'take-{index + 1}.wav', data=data,
                                         payload='audio', descriptor={'audio': {
                                             'text': spoken, 'voice': self._voice(envelope, index, h.request.slot_id),
                                             'alignment': None}})
                if h.request.on_output:
                    await h.request.on_output(output)
                if primary is None:
                    primary = ExecutionResult(data=data, payload='audio', filename=output.filename,
                                              primary_identity=output.identity, descriptor=output.descriptor,
                                              charged_usd=self.cost)
            return primary
        finally:
            self.probe.concurrent -= 1

    async def cancel(self, h):
        pass

    async def reconcile(self, slot, *, request=None):
        return Reconciliation('gone')

    async def verify_charge(self, job_ref):
        return None

    async def collect_existing(self, slot, *, request=None):
        self.collected.append(slot['id'])
        return Reconciliation('failed', cause='The scripted provider has nothing to collect.', failure_kind='error')


class SteppingClock:
    """The store's clock (`app.db.now_iso`): ten milliseconds a tick, set back when
    told, as a time correction sets back a machine's clock."""

    def __init__(self):
        self.now = datetime(2026, 10, 7, 1, 19, 17, tzinfo=timezone.utc)

    def __call__(self) -> str:
        self.now += timedelta(milliseconds=10)
        return self.now.isoformat()

    def step_back(self, seconds: float = 1.0):
        self.now -= timedelta(seconds=seconds)
