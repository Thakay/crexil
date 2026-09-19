"""Scripted-executor harness mode: the law tests' engine room.

Activated ONLY by COCKPIT_HARNESS_EXECUTORS=1 (set by the playwright
webServer env — never by compose or deploy, so the shipped image carries
this inert). wiring.start then builds these two executors in place of the
real pair (same names, so route_engine is untouched) and the e2e stack can
no longer reach real ComfyUI or OpenRouter by construction.

Each take blocks inside wait() until a directive arrives over the control
routes below; the Playwright specs land/fail slots one at a time and assert
the laws while the batch is genuinely mid-flight — real scheduler, real
store, real events; only the engines are scripted.

Directives are a per-slot FIFO: posting before the lane picks the slot up is
fine (no race), and a reroll's attempt 2 consumes the next one. A fail
directive's `message`/`detail` are what the engine "said" — the face shows
the scheduler's plain line for the kind (law 3) and the scripted text lands
behind the cell's DETAILS, where the specs assert it. Cancel mirrors
tests/fakes.py lane semantics — local raises EngineFailure("interrupted"),
external raises CancelledError. Scripted failure kinds ride the REAL
retry/bench tables: rate_limit/unreachable auto-retry once (law 9) and
consume a second directive, so specs script with error/model_down unless
the retry path is the point.

The trust and high-probability cases ride the same directives: a local
`land` with a black plate (`color: [0,0,0]`) goes through the REAL blank
detector (`executors.blank_frame`) — the spec injects the fault, the product
finds it; a `fail` with `kind: charged_blank` carries `charged_usd` and
`job_id`, and `bill` scripts the answers to the three re-asks in order
(null = no answer yet); `kind: oom` reads the request's frame and the
record — the harness invents nothing; `retry_after_s` on a `rate_limit`
fail is the provider's window; `POST /api/harness/gpu` scripts the card
reading the scheduler's probe returns (`foreign_bytes` at or over the
threshold makes the next OOM a foreign hold).

Every route is include_in_schema=False: the committed OpenAPI contract must
not flap with an env var; specs call these with plain requests, never the
typed client.
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

from .executors import (DispatchRequest, EngineFailure, ExecutionResult,
                        GpuReading, Handle, Lane, Reconciliation, blank_frame)


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


class HarnessExecutor:
    """Executor-protocol shim over the directive registry."""

    def __init__(self, name: str, lane: Lane, payload: str = "image") -> None:
        self.name = name
        self.lane: Lane = lane
        self.payload = payload           # "3d" for the mesh stand-in

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start(self, req: DispatchRequest) -> HarnessHandle:
        job = (req.submitted_graph or {}).get("mesh_job") or {}
        return HarnessHandle(job_ref=f"harness-{req.slot_id}",
                             slot_id=req.slot_id,
                             kind=job.get("kind") or "generate",
                             engine=job.get("engine") or "harness")

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
        if d.outcome == "fail":
            if d.kind == "charged_blank" and d.job_id:
                REGISTRY.bills[d.job_id] = deque(d.bill)
            raise EngineFailure(d.kind, d.message, detail=d.detail,
                                charged_usd=d.charged_usd, job_ref=d.job_id,
                                retry_after_s=d.retry_after_s)
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

    async def reconcile(self, _slot: dict) -> Reconciliation:
        return Reconciliation("gone")    # restart recovery stays pytest-land


def build_executors() -> list[HarnessExecutor]:
    """The stand-in set, under the real executors' names. A region take
    is ONE directive: the whole pipeline lands or fails as one
    scripted outcome; pass mechanics live in pytest over the real
    RegionExecutor, and the e2e specs script landing shapes only. (If a
    spec ever needs pass granularity, extend the directive here.)"""
    return [HarnessExecutor("comfy", "local"),
            HarnessExecutor("openrouter", "external"),
            HarnessExecutor("region", "local"),
            HarnessExecutor("mesh", "local", payload="3d")]


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
    return {"waiting": sorted(REGISTRY.waiting)}


@router.post("/gpu", include_in_schema=False)
async def script_gpu(body: GpuScript) -> dict:
    REGISTRY.gpu = body
    return body.model_dump()
