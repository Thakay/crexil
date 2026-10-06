"""Scripted executors for browser journeys, gated by the harness environment.

The real scheduler, store, events and collectors own every state transition.
Only engine transports are scripted. Per-slot FIFO directives permit tests to
land or fail work before or after dispatch without reaching real runtimes.

Output directives report valid fixture bytes or missing descriptors through the
ordinary collector. Separately scripted retrieval can recover the same files;
start and retrieval counters expose accidental resubmission. Unscripted restart
reconciliation reports no known runtime work. These fixtures never qualify real
engine support or artistic output.

Cancellation follows each lane's behavior. Failures use the real retry and bench
policies, and the real blank detector inspects local PNG fixtures. GPU and charge
responses are also explicit test directives. Control routes are absent from the
normal application and its published API schema.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter
from PIL import Image
from pydantic import BaseModel

from . import audio
from .executors import (CollectedOutput, DispatchRequest, EngineFailure, ExecutionResult,
                        GpuReading, Handle, Lane, Reconciliation, blank_frame)


class HarnessOutput(BaseModel):
    identity: str
    filename: str
    role: str = 'unclassified'
    missing: bool = False
    transparent: bool = False
    color: tuple[int, int, int] = (82, 130, 168)


class CollectionDirective(BaseModel):
    kind: Literal['landed', 'failed', 'gone', 'unverifiable', 'running'] = 'landed'
    outputs: list[HarnessOutput] = []
    cause: str | None = None


class Directive(BaseModel):
    outcome: Literal["land", "fail"] = "land"
    kind: str = "error"                 # fail only; an EngineFailure kind
    message: str = "scripted failure"   # fail only; what the engine "said"
    detail: str | None = None           # fail only; raw text behind DETAILS
    charged_usd: float | None = None    # land: external money; charged_blank: reported
    color: tuple[int, int, int] | None = None   # land only; plate color
    job_id: str | None = None           # charged_blank: the generation id
    bill: list[float | None] = []       # charged_blank: answers per re-ask
    retry_after_s: float | None = None  # rate_limit: the provider's window
    outputs: list[HarnessOutput] = []


class GpuScript(BaseModel):
    """The card, as the harness's probe will report it. `null` body fields
    = ComfyUI couldn't say (the probe returns None)."""
    total_bytes: int | None = 24 * (1 << 30)
    free_bytes: int | None = 20 * (1 << 30)
    comfy_bytes: int | None = 2 * (1 << 30)
    foreign_bytes: int | None = 0
    unreachable: bool = False


@dataclass
class _Channel:
    directives: deque = field(default_factory=deque)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)


class HarnessRegistry:
    """Shared by both executors and the control routes — slot ids are global."""

    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}
        self.waiting: set[str] = set()
        self.bills: dict[str, deque] = {}       # job_id -> scripted answers
        self.gpu: GpuScript = GpuScript()       # the card, as scripted
        self.collections: dict[str, CollectionDirective] = {}
        self.starts: dict[str, int] = {}
        self.retrievals: dict[str, int] = {}

    def channel(self, slot_id: str) -> _Channel:
        return self._channels.setdefault(slot_id, _Channel())

    def post(self, slot_id: str, directive: Directive) -> int:
        ch = self.channel(slot_id)
        ch.directives.append(directive)
        ch.arrived.set()
        return len(ch.directives)

    async def next_directive(self, slot_id: str,
                             cancelled: asyncio.Event) -> Directive | None:
        """The slot's next directive, or None once cancel wins the wait."""
        ch = self.channel(slot_id)
        while True:
            if cancelled.is_set():
                return None
            if ch.directives:
                d = ch.directives.popleft()
                if not ch.directives:
                    ch.arrived.clear()
                return d
            waiters = [asyncio.create_task(ch.arrived.wait()),
                       asyncio.create_task(cancelled.wait())]
            try:
                await asyncio.wait(waiters,
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                for w in waiters:
                    w.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)


REGISTRY = HarnessRegistry()


BLACK = (0, 0, 0)


def _png(slot_id: str, color: tuple[int, int, int] | None) -> bytes:
    """A real decodable artefact so /thumb and the lightbox work. Default
    color derives from the slot id — distinct plates, purely cosmetic.
    Every plate carries a second tone (an inner square) so it has CONTENT
    for the A5a detector; the one flat plate is `color: [0, 0, 0]` — the
    scripted empty frame."""
    if color is None:
        h = hash(slot_id)
        color = (64 + h % 128, 64 + (h >> 7) % 128, 64 + (h >> 14) % 128)
    color = tuple(color)
    im = Image.new("RGB", (64, 64), color)
    if color != BLACK:
        inner = tuple((c + 128) % 256 for c in color)
        im.paste(inner, (16, 16, 48, 48))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _glb(slot_id: str) -> bytes:
    """A real, minimal, valid GLB so a scripted mesh is a mesh — the cockpit
    only stores the bytes, but a stand-in that isn't the format it claims is
    the kind of lie a law-7 test can't see through."""
    doc = json.dumps({"asset": {"version": "2.0",
                                "generator": f"cockpit harness {slot_id}"},
                      "scenes": [{"nodes": []}], "scene": 0,
                      "nodes": [], "meshes": []}).encode()
    doc += b" " * (-len(doc) % 4)                  # chunks are 4-byte aligned
    chunk = struct.pack("<II", len(doc), 0x4E4F534A) + doc   # 'JSON'
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk  # 'glTF'


def turntable_webp(job_ref: str) -> bytes:
    """A real animated WEBP so a scripted mesh has a real BODY, for
    the same reason `_glb` is a real GLB: a stand-in that isn't the format
    it claims is the kind of lie the e2e specs can't see through, and the
    card plate is literally frame 0 of this file.

    Eight frames of a marker orbiting a fixed centre — the cheapest thing
    that is unmistakably a turntable, and different enough per frame that a
    still is visibly one moment of a rotation. `api_v4._fetch_turntable`
    calls this instead of the host runner under scripted executors, exactly
    as `wiring.probe_mesh_runner` stands down."""
    h = hash(job_ref)
    ground = (24 + h % 40, 26 + (h >> 7) % 40, 30 + (h >> 14) % 40)
    frames = []
    n, size, r = 8, 96, 28
    for i in range(n):
        im = Image.new("RGB", (size, size), ground)
        a = 2 * math.pi * i / n
        cx = size / 2 + r * math.cos(a)
        cy = size / 2 + r * 0.45 * math.sin(a)      # a ~20° elevation orbit
        box = (int(cx - 12), int(cy - 12), int(cx + 12), int(cy + 12))
        im.paste((210, 214, 218), box)
        frames.append(im)
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:],
                   duration=120, loop=0)
    return buf.getvalue()


def _wav(key: str, seconds: float = 0.6, rate: int = 24000) -> bytes:
    """A real PCM WAV so a scripted audio take is measured like a real one:
    a short tone whose pitch derives from the key, faded at both ends."""
    pitch = 180 + hash(key) % 240
    count = int(seconds * rate)
    frames = bytearray()
    for i in range(count):
        envelope = min(1.0, i / 600, (count - i) / 600)
        frames += struct.pack('<h', int(9000 * envelope * math.sin(2 * math.pi * pitch * i / rate)))
    return audio.wrap_pcm(bytes(frames), sample_rate=rate)


async def _audio_takes(h: "HarnessHandle", engine_name: str) -> ExecutionResult:
    """Every take one scripted audio attempt returns, through the real collector."""
    request = h.request.external or {}
    verb, count = request.get('verb'), int(request.get('takes') or 1)
    primary = None
    for index in range(count):
        identity = f'take:{index}'
        voice = ({'provider': engine_name, 'kind': 'designed', 'generated_voice_id': f'harness-{h.slot_id}-{index}'}
                 if verb == 'voice-design' else
                 {'provider': engine_name, 'kind': 'saved',
                  'voice_id': f"harness-voice-{(request.get('voice') or {}).get('source_node_id', 'library')}"}
                 if verb == 'speak' else None)
        spoken = (request.get('controls') or {}).get('preview_text') if verb == 'voice-design' else \
            request.get('text') if verb == 'speak' else None
        output = CollectedOutput(identity=identity, filename=f'take-{index + 1}.wav', data=_wav(h.slot_id + identity),
                                 payload=audio.PAYLOAD, descriptor={'harness': True,
                                 'audio': {'text': spoken, 'voice': voice, 'alignment': None}})
        if h.request.on_output:
            await h.request.on_output(output)
        if primary is None:
            primary = ExecutionResult(data=output.data, payload=audio.PAYLOAD, filename=output.filename,
                                      primary_identity=identity, descriptor=output.descriptor)
    return primary


# The as-run record a scripted mesh lands with. Zeroed geometry is the
# HONEST reading of `_glb`'s empty scene — the harness never claims a
# triangle it didn't make — and `watertight: []` is what makes the
# projection answer null rather than "yes" for a mesh with no geometry.
HARNESS_MESH_VALIDATION = {"triangles": 0, "vertices": 0, "watertight": [],
                           "texture_present": False, "file_mb": 0.0}


@dataclass
class HarnessHandle(Handle):
    slot_id: str = ""
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    # Which 3D verb the take asked for, carried from the envelope so
    # a scripted child records the kind that made it. The stand-in engine
    # does no work either way — the point is that the RECORD is shaped like
    # the real one, which is what the e2e specs read.
    kind: str = "generate"
    engine: str = "harness"
    request: DispatchRequest | None = None


async def _collect_outputs(request: DispatchRequest, outputs: list[HarnessOutput]) -> ExecutionResult | None:
    """Script runtime descriptors and bytes through the real collector callback."""
    primary = None
    for item in outputs:
        blob = _png(request.slot_id + ':' + item.identity, item.color)
        if item.transparent:
            image = Image.open(io.BytesIO(blob)).convert('RGBA')
            alpha = Image.new('L', image.size, 0)
            alpha.paste(190, (16, 16, image.width - 16, image.height - 16))
            image.putalpha(alpha)
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
            blob = buffer.getvalue()
        output = CollectedOutput(identity=item.identity, filename=item.filename, role=item.role,
            data=None if item.missing else blob, descriptor={'harness': True, 'identity': item.identity},
            error='Scripted download unavailable.' if item.missing else None)
        if request.on_output:
            await request.on_output(output)
        if not item.missing and primary is None:
            primary = ExecutionResult(data=blob, filename=item.filename, primary_identity=item.identity)
    return primary


class HarnessExecutor:
    """Executor-protocol shim over the directive registry."""

    def __init__(self, name: str, lane: Lane, payload: str = "image") -> None:
        self.name = name
        self.lane: Lane = lane
        self.payload = payload           # "3d" for the mesh stand-in, "audio" for voices

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start(self, req: DispatchRequest) -> HarnessHandle:
        job = (req.submitted_graph or {}).get("mesh_job") or {}
        REGISTRY.starts[req.slot_id] = REGISTRY.starts.get(req.slot_id, 0) + 1
        return HarnessHandle(job_ref=f"harness-{req.slot_id}",
                             slot_id=req.slot_id,
                             kind=job.get("kind") or "generate",
                             engine=job.get("engine") or "harness", request=req)

    async def wait(self, h: HarnessHandle) -> ExecutionResult:
        REGISTRY.waiting.add(h.slot_id)
        try:
            d = await REGISTRY.next_directive(h.slot_id, h.cancelled)
        finally:
            REGISTRY.waiting.discard(h.slot_id)
        if d is None:                    # cancel won
            if self.lane == "external":
                raise asyncio.CancelledError()
            raise EngineFailure("interrupted", "interrupted by cancel")
        primary = await _collect_outputs(h.request, d.outputs) if h.request and d.outputs else None
        if d.outcome == "fail":
            if d.kind == "charged_blank" and d.job_id:
                REGISTRY.bills[d.job_id] = deque(d.bill)
            raise EngineFailure(d.kind, d.message, detail=d.detail,
                                charged_usd=d.charged_usd, job_ref=d.job_id,
                                retry_after_s=d.retry_after_s)
        if primary:
            primary.charged_usd = d.charged_usd
            return primary
        if self.payload == audio.PAYLOAD:
            result = await _audio_takes(h, self.name)
            result.charged_usd = d.charged_usd
            return result
        if self.payload == "3d":
            return ExecutionResult(data=_glb(h.slot_id), payload="3d",
                                   filename=f"{h.slot_id}.glb",
                                   submitted_graph={"mesh_job": {
                                       "engine": "harness",
                                       # the verb, and the engine the
                                       # verb was asked of — a scripted
                                       # DECIMATE child reads as a decimate
                                       "kind": h.kind,
                                       "asked_engine": h.engine,
                                       "runner_job_id": h.job_ref,
                                       # the record the node body is
                                       # drawn from; `turntable_webp` is the
                                       # render it points at
                                       "turntable": {"frames": 8, "size": 96},
                                       "validation": HARNESS_MESH_VALIDATION}})
        png = _png(h.slot_id, d.color)
        if self.lane == "local":
            why = blank_frame(png)           # the real A5a detector
            if why is not None:
                raise EngineFailure("blank",
                                    f"the frame came back empty — {why}",
                                    detail=f"harness plate: {why}",
                                    artefact=png)
        return ExecutionResult(
            data=png,
            filename=f"{h.slot_id}.png",
            charged_usd=d.charged_usd,
            external=({"transport": "harness"} if self.lane == "external"
                      else None))

    async def cancel(self, h: HarnessHandle) -> None:
        h.cancelled.set()

    async def verify_charge(self, job_ref: str) -> float | None:
        """B4's re-ask, scripted: the next answer for this job id, None
        when the script is silent or exhausted."""
        answers = REGISTRY.bills.get(job_ref)
        if not answers:
            return None
        return answers.popleft()

    async def reconcile(self, slot: dict, *, request=None) -> Reconciliation:
        REGISTRY.retrievals[slot['id']] = REGISTRY.retrievals.get(slot['id'], 0) + 1
        directive = REGISTRY.collections.get(slot['id'])
        if directive is None:
            return Reconciliation('gone')
        result = await _collect_outputs(request, directive.outputs) if request else None
        return Reconciliation(directive.kind, result=result, cause=directive.cause)


def build_executors() -> list[HarnessExecutor]:
    """The stand-in set, under the real executors' names. A region take
    is ONE directive: the whole pipeline lands or fails as one
    scripted outcome; pass mechanics live in pytest over the real
    RegionExecutor, and the e2e specs script landing shapes only. (If a
    spec ever needs pass granularity, extend the directive here.)"""
    return [HarnessExecutor("comfy", "local"),
            HarnessExecutor("openrouter", "external"),
            HarnessExecutor("region", "local"),
            HarnessExecutor("mesh", "local", payload="3d"),
            HarnessExecutor("elevenlabs", "external", payload=audio.PAYLOAD),
            HarnessExecutor("voice-runner", "local", payload=audio.PAYLOAD)]


async def gpu_probe() -> GpuReading | None:
    """The scheduler's card reading, scripted (C2/E6)."""
    g = REGISTRY.gpu
    if g.unreachable or g.total_bytes is None or g.free_bytes is None:
        return None
    return GpuReading(total=g.total_bytes, free=g.free_bytes,
                      comfy=g.comfy_bytes or 0, foreign=g.foreign_bytes or 0,
                      name="harness card")


router = APIRouter(prefix="/api/harness", tags=["harness"])


@router.post("/slots/{slot_id}/resolve", include_in_schema=False)
async def resolve_slot(slot_id: str, body: Directive) -> dict:
    queued = REGISTRY.post(slot_id, body)
    return {"slot_id": slot_id, "queued": queued,
            "waiting": slot_id in REGISTRY.waiting}


@router.get("/state", include_in_schema=False)
async def harness_state() -> dict:
    return {"waiting": sorted(REGISTRY.waiting), 'starts': REGISTRY.starts, 'retrievals': REGISTRY.retrievals}


@router.post('/slots/{slot_id}/collection', include_in_schema=False)
async def script_collection(slot_id: str, body: CollectionDirective) -> dict:
    REGISTRY.collections[slot_id] = body
    return {'slot_id': slot_id, 'kind': body.kind}


@router.post("/gpu", include_in_schema=False)
async def script_gpu(body: GpuScript) -> dict:
    REGISTRY.gpu = body
    return body.model_dump()
