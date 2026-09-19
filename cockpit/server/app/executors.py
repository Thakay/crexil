"""Executor abstraction over the engine transports.

An Executor puts prepared work in flight and reports truth; it NEVER touches
the DB or the slot lifecycle — the scheduler owns every transition. That
seam is what lets an executor drop in: implement `start`, `wait`, `cancel`,
`reconcile`, declare a `lane`, and a `local` one automatically shares the
serial local lane (one GPU, across executors).

Two ship here — ComfyUI (HTTP + WS signal) and OpenRouter (one long HTTP
call); the mesh executor is `mesh.py`. Hard-won mechanics, keep them:
- ComfyUI completion: the WS event is only a SIGNAL; `/api/history` is the
  TRUTH — the history entry lags the signal, so it is confirm-polled.
- Output bytes travel over `/api/view`, never a filesystem copy (ComfyUI may
  be remote).
- OpenRouter's error-kind table (dead_key/moderation/rate_limit/model_down/
  timeout/unreachable) flows through unchanged; billing is all-or-nothing,
  so a failed generation carries no confirmed charge — except
  `charged_blank`, a billed response with no image, which carries the
  reported amount and the generation id for the bill check.
- `blank_frame` is the empty-frame detector (every band flat within a few
  levels — the NaN'd-latent signature), run on the fetched bytes: a local
  engine that reports success with an empty frame is a failure, not a
  landing, and the bytes ride the failure as `artefact` so nothing is lost.
- `oom` is its own kind (`classify_execution_error`) because its face names
  the frame and offers a size; the requested frame rides
  `DispatchRequest.frame` so the scheduler need not parse the graph.
- `rate_limit` carries `retry_after_s` when the provider said how long.
- `read_gpu` is the one live VRAM query: ComfyUI's own `/system_stats`, the
  only process on the card that can tell the cockpit anything — WSL2's
  nvidia-smi sees no Windows processes, so a foreign hold is a SIZE, never
  a name.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import httpx
from PIL import Image, UnidentifiedImageError

from . import config
from .comfy import ComfyClient
from .openrouter import ExternalJobError

log = logging.getLogger("cockpit.executors")

Lane = Literal["local", "external"]


@dataclass(frozen=True)
class DispatchRequest:
    """One prepared take: payload composed caller-side (the scheduler is
    payload-agnostic — presets/regions never enter it), plus the land-fields
    known at Run time that become the node row on success.

    Region multi-pass rides this unchanged: the whole plan travels
    in `submitted_graph` as a `{"region_plan": …}` envelope
    (`is_region_payload` routes it to the RegionExecutor, which owns the
    pass chain inside wait()), so slot-row persistence and restart
    recovery's `_rebuild_request` carry a region take like any other."""

    slot_id: str
    engine: str
    # exactly one of these is set
    submitted_graph: dict | None = None   # ComfyUI API graph, seed baked in
    external: dict | None = None          # {"model","prompt","seed","params"}
    committed_usd: float | None = None
    # the requested frame (width, height) where one was asked for — the
    # OOM face names it and the record keeps it
    frame: tuple[int, int] | None = None
    # land-fields (PRD §2 node)
    payload: str = "image"
    verb: str = "run"
    prompt: str | None = None
    parents: tuple[str, ...] = ()
    seed: int | None = None
    x: float | None = None
    y: float | None = None


@dataclass
class ExecutionResult:
    data: bytes
    payload: str = "image"
    filename: str | None = None        # executor's hint; scheduler namespaces
    charged_usd: float | None = None   # CONFIRMED by the engine, else None
    external: dict | None = None       # response-side provenance for the node
    detail: dict | None = None
    # the as-run payload record, when it differs from the request's — a
    # region take lands with its per-pass graphs + prompt_ids here
    # and land_node stores it over the slot's envelope
    submitted_graph: dict | None = None
    # extra artefacts written BESIDE the take's own, keyed by the suffix
    # they take (a TRELLIS.2 mesh lands with its voxel field so it
    # can be re-extracted later). The scheduler writes them; the tray
    # counts them and purge frees them, because `_artefact_paths` derives
    # the same names.
    sidecars: dict[str, bytes] | None = None


def is_region_payload(payload: dict | None) -> bool:
    """A submitted_graph that is a region envelope, not a ComfyUI graph —
    the scheduler's routing peek."""
    return isinstance(payload, dict) and "region_plan" in payload


class EngineFailure(Exception):
    """The one failure currency between executors and the scheduler.

    kinds: dead_key | moderation | rate_limit | model_down | timeout |
           unreachable | interrupted | error | blank | charged_blank | oom
    (`interrupted` = the failure was our own cancel, not the engine's fault;
    `blank` = A5a, the engine said success and returned an empty frame;
    `charged_blank` = B4, the provider billed and returned no image;
    `oom` = A1/A11, the local engine ran out of video memory)

    `charged_usd` is the amount a charged_blank response REPORTED (the
    ledger entry); `job_ref` the provider's generation id for the bill
    check and the receipt; `artefact` the empty frame's bytes (kept, law 7);
    `retry_after_s` the provider's own window on a rate limit (B2), None
    when it named none.
    """

    def __init__(self, kind: str, message: str, *,
                 status: int | None = None, detail: str | None = None,
                 charged_usd: float | None = None, job_ref: str | None = None,
                 artefact: bytes | None = None,
                 retry_after_s: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail
        self.charged_usd = charged_usd
        self.job_ref = job_ref
        self.artefact = artefact
        self.retry_after_s = retry_after_s


# A5a's detector. A diffusion engine's "success with nothing in it" is a
# flat plate — black from NaN'd latents, or one colour end to end — so the
# test is per-band flatness across the whole frame. `FLAT_LEVELS` is the
# tolerance in 8-bit levels: real art, however dark, spans far more than a
# handful of levels in at least one band. Undecodable bytes are NOT blank
# here — that is a different failure and lands as the node's own 404 in
# plain words.
FLAT_LEVELS = 4


def blank_frame(data: bytes) -> str | None:
    """Why these bytes are an empty frame, in plain words — or None when
    the frame has content."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            rgb = im.convert("RGB")
            w, h = rgb.size
            extrema = rgb.getextrema()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    spread = max(hi - lo for lo, hi in extrema)
    if spread > FLAT_LEVELS:
        return None
    lo = min(lo for lo, _ in extrema)
    hi = max(hi for _, hi in extrema)
    shade = ("black" if hi <= FLAT_LEVELS else
             "white" if lo >= 255 - FLAT_LEVELS else "one flat colour")
    return (f"every pixel of the {w}×{h} frame is {shade} (within "
            f"{spread} levels)")


# A1/A11's detector. ComfyUI reports a failed node as an `execution_error`
# message carrying the exception's full type name and text; torch's OOM is
# `torch.OutOfMemoryError` (older builds: `torch.cuda.OutOfMemoryError`)
# and its message reads "CUDA out of memory…" or "Allocation on device".
# The history entry's `status.messages` and the WS hint carry the same
# dict, so one reader serves `_finish` and `reconcile`.
_OOM_TEXT = ("out of memory", "allocation on device", "cuda error: out of")


def classify_execution_error(entry: dict | None, hint: dict | None = None
                             ) -> str:
    """`oom` when the execution error was the card running out of memory,
    else `error`."""
    found: list[dict] = []
    if hint:
        found.append(hint)
    for m in ((entry or {}).get("status") or {}).get("messages") or []:
        if isinstance(m, (list, tuple)) and len(m) > 1 \
                and m[0] == "execution_error" and isinstance(m[1], dict):
            found.append(m[1])
    for d in found:
        typ = str(d.get("exception_type") or "")
        msg = str(d.get("exception_message") or "").lower()
        if typ.endswith("OutOfMemoryError") or any(t in msg for t in _OOM_TEXT):
            return "oom"
    return "error"


@dataclass(frozen=True)
class GpuReading:
    """One live look at the card (C2/E6), in bytes. `foreign` is
    what neither this cockpit's engine nor free space accounts for —
    another program's hold (plus the desktop's own slice, which is why the
    C2 threshold sits well above zero). `name` is the device's, never a
    process's: WSL2 cannot see Windows processes."""
    total: int
    free: int          # free on the device itself (every process counted)
    comfy: int         # reserved by ComfyUI's torch
    foreign: int       # total - free - comfy, floored at 0
    name: str = ""


def gpu_reading_from_stats(stats: dict) -> GpuReading | None:
    """ComfyUI's /system_stats → a reading. Its `vram_free` counts torch's
    reserved-but-idle pool as free (it is, to ComfyUI); the device's own
    free is that less `torch_vram_free`."""
    devices = stats.get("devices") or []
    if not devices:
        return None
    d = devices[0]
    try:
        total = int(d["vram_total"])
        vram_free = int(d["vram_free"])
        torch_total = int(d.get("torch_vram_total") or 0)
        torch_free = int(d.get("torch_vram_free") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    free = max(0, vram_free - torch_free)
    foreign = max(0, total - free - torch_total)
    return GpuReading(total=total, free=free, comfy=torch_total,
                      foreign=foreign, name=str(d.get("name") or ""))


async def read_gpu(client: ComfyClient) -> GpuReading | None:
    """The live query, never raising: None means ComfyUI couldn't say."""
    try:
        return gpu_reading_from_stats(await client.system_stats())
    except (httpx.HTTPError, ValueError, TypeError):
        return None


@dataclass
class Reconciliation:
    """Recovery verdict for one pre-restart running slot."""
    kind: Literal["landed", "failed", "gone", "unverifiable"]
    result: ExecutionResult | None = None   # kind == landed
    cause: str | None = None                # kind == failed
    failure_kind: str = "error"             # kind == failed: the EngineFailure kind


@dataclass
class Handle:
    job_ref: str | None = None


@dataclass
class ComfyHandle(Handle):
    done: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_sent: bool = False
    error_hint: dict | None = None


@dataclass
class ExternalHandle(Handle):
    task: asyncio.Task | None = None


@runtime_checkable
class Executor(Protocol):
    name: str
    lane: Lane

    async def open(self) -> None: ...
    async def close(self) -> None: ...

    async def start(self, req: DispatchRequest) -> Handle:
        """Put the work in flight and return fast. Raises EngineFailure when
        the engine refuses at the door."""

    async def wait(self, handle: Handle) -> ExecutionResult:
        """Block until done; return the artefact or raise EngineFailure."""

    async def cancel(self, handle: Handle) -> None:
        """Best-effort stop. Must be safe against already-finished work."""

    async def reconcile(self, slot: dict) -> Reconciliation:
        """Recovery: find the truth for a pre-restart running slot row."""

    async def verify_charge(self, job_ref: str) -> float | None:
        """B4's free re-ask: what the provider's own record says
        this job billed, or None when it has no answer yet. Never a
        re-generate — this must not put work in flight. Local executors
        answer None: nothing bills on the GPU."""


# --- ComfyUI ---

def _queue_ids(queue_entries) -> set[str]:
    """ComfyUI queue entries are [number, prompt_id, prompt, ...]."""
    out = set()
    for e in queue_entries or []:
        if isinstance(e, (list, tuple)) and len(e) > 1:
            out.add(e[1])
    return out


def _history_error_text(entry: dict) -> str:
    msgs = (entry.get("status") or {}).get("messages") or []
    return f"execution error: {json.dumps(msgs)[:600]}"


class ComfyExecutor:
    """Local lane executor over the proven ComfyClient.

    The WS mirror only sets the handle's done-event (SIGNAL); `wait` reads
    `/api/history` for the TRUTH and confirm-polls it, because the entry lags
    the signal. `client_id` is process-scoped, so after a restart the mirror
    never hears about pre-restart jobs — `reconcile` polls history instead."""

    name = "comfy"
    lane: Lane = "local"

    def __init__(self, client: ComfyClient) -> None:
        self._client = client
        self._signals: dict[str, ComfyHandle] = {}

    async def open(self) -> None:
        self._client.start_ws_mirror(self._on_ws)

    async def close(self) -> None:
        await self._client.close()

    async def _on_ws(self, msg: dict) -> None:
        data = msg.get("data") or {}
        pid = data.get("prompt_id")
        if not pid or pid not in self._signals:
            return
        h = self._signals[pid]
        t = msg.get("type")
        if t == "execution_error":
            h.error_hint = data
            h.done.set()
        elif (t in ("execution_success", "execution_interrupted")
              or (t == "executing" and data.get("node") is None)):
            h.done.set()

    async def start(self, req: DispatchRequest) -> ComfyHandle:
        try:
            pid = await self._client.submit(req.submitted_graph)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise EngineFailure("error",
                                f"ComfyUI rejected the graph (HTTP "
                                f"{e.response.status_code})",
                                status=e.response.status_code,
                                detail=body) from e
        except httpx.HTTPError as e:
            raise EngineFailure("unreachable",
                                f"ComfyUI unreachable: {e}") from e
        h = ComfyHandle(job_ref=pid)
        self._signals[pid] = h
        return h

    async def wait(self, h: ComfyHandle) -> ExecutionResult:
        try:
            while True:
                with suppress(TimeoutError):
                    await asyncio.wait_for(h.done.wait(), config.COMFY_POLL_S)
                entry = await self._history(h.job_ref)
                if entry is not None or h.done.is_set():
                    return await self._finish(h, entry)
        finally:
            self._signals.pop(h.job_ref, None)

    async def _history(self, pid: str) -> dict | None:
        try:
            return await self._client.history(pid)
        except httpx.HTTPError as e:
            raise EngineFailure("unreachable",
                                f"ComfyUI unreachable: {e}") from e

    async def _finish(self, h: ComfyHandle, entry: dict | None) -> ExecutionResult:
        """The completion pattern: confirm-poll history until it shows
        outputs or an error — the entry lags the WS signal."""
        fail_kind = "interrupted" if h.interrupt_sent else "error"
        for _ in range(config.HISTORY_CONFIRM_TRIES):
            if entry is not None and (
                    entry.get("outputs")
                    or (entry.get("status") or {}).get("status_str") == "error"):
                break
            await asyncio.sleep(config.HISTORY_CONFIRM_INTERVAL_S)
            entry = await self._history(h.job_ref)
        if entry is None:
            raise EngineFailure(fail_kind,
                                "no history entry after completion signal")
        if (entry.get("status") or {}).get("status_str") == "error":
            if fail_kind == "error":
                # A1/A11: the card ran out of memory — its own kind, so
                # the face can name the frame and offer a size
                fail_kind = classify_execution_error(entry, h.error_hint)
            raise EngineFailure(fail_kind, _history_error_text(entry)[:800],
                                detail=json.dumps(h.error_hint or {})[:800])
        img = ComfyClient.first_image(entry)
        if img is None:
            raise EngineFailure(fail_kind,
                                "graph completed but produced no output image")
        blob = await self._client.view(img["filename"],
                                       img.get("subfolder") or "")
        why = blank_frame(blob)
        if why is not None:
            # A5a: success reported, nothing usable returned. The bytes
            # ride along so the record keeps them (law 7).
            raise EngineFailure("blank", f"the frame came back empty — {why}",
                                detail=f"ComfyUI output {img['filename']}: "
                                       f"{why}",
                                artefact=blob)
        return ExecutionResult(data=blob, filename=img["filename"])

    async def cancel(self, h: ComfyHandle) -> None:
        """Ownership-verified: /api/interrupt stops whatever is running, so
        only fire it when queue_state says the running prompt is ours."""
        try:
            q = await self._client.queue_state()
        except httpx.HTTPError:
            return  # can't verify; wait() will surface the truth
        if h.job_ref in _queue_ids(q.get("queue_pending")):
            h.interrupt_sent = True
            with suppress(httpx.HTTPError):
                await self._client.delete_queued(h.job_ref)
            h.done.set()   # a deleted prompt never signals; wake wait()
        elif h.job_ref in _queue_ids(q.get("queue_running")):
            h.interrupt_sent = True
            with suppress(httpx.HTTPError):
                await self._client.interrupt()
        # else: already finished — nothing to do, the result stands

    async def reconcile(self, slot: dict) -> Reconciliation:
        try:
            entry = await self._client.history(slot["job_ref"])
        except httpx.HTTPError as e:
            raise EngineFailure("unreachable",
                                f"ComfyUI unreachable during recovery: {e}") from e
        if entry is None:
            return Reconciliation("gone")
        if (entry.get("status") or {}).get("status_str") == "error":
            return Reconciliation("failed",
                                  failure_kind=classify_execution_error(entry),
                                  cause=_history_error_text(entry)[:800])
        img = ComfyClient.first_image(entry)
        if img is None:
            return Reconciliation(
                "failed", cause="completed but produced no output image")
        blob = await self._client.view(img["filename"],
                                       img.get("subfolder") or "")
        why = blank_frame(blob)
        if why is not None:
            return Reconciliation("failed", failure_kind="blank",
                                  cause=f"the frame came back empty — {why}")
        return Reconciliation(
            "landed", result=ExecutionResult(data=blob,
                                             filename=img["filename"]))

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None                      # nothing bills on the GPU


# --- OpenRouter ---

class OpenRouterExecutor:
    """External lane executor over the proven OpenRouter adapter. One long
    HTTP call per take — there is no pre-completion job reference, so
    job_ref stays None and restart recovery is always 'unverifiable'."""

    name = "openrouter"
    lane: Lane = "external"

    def __init__(self, client) -> None:
        self._client = client

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start(self, req: DispatchRequest) -> ExternalHandle:
        e = req.external or {}
        task = asyncio.create_task(
            self._client.generate(model=e["model"],
                                  prompt=e.get("prompt") or req.prompt or "",
                                  seed=e.get("seed"),
                                  params=e.get("params")),
            name=f"openrouter-{req.slot_id}")
        return ExternalHandle(job_ref=None, task=task)

    async def wait(self, h: ExternalHandle) -> ExecutionResult:
        try:
            out = await h.task
        except ExternalJobError as e:
            raise EngineFailure(e.kind, str(e), status=e.status,
                                charged_usd=e.charged_usd,
                                job_ref=e.job_id) from e
        except asyncio.CancelledError:
            h.task.cancel()   # waiter cancelled -> take the call down with us
            raise
        return ExecutionResult(
            data=out["png"],
            charged_usd=out["cost_usd"],
            external={"transport": "openrouter",
                      "request": out["request"],
                      "created": out.get("created"),
                      "generation_id": out.get("id")})

    async def cancel(self, h: ExternalHandle) -> None:
        if h.task:
            h.task.cancel()   # drop the connection; billing is all-or-nothing

    async def reconcile(self, _slot: dict) -> Reconciliation:
        return Reconciliation("unverifiable")

    async def verify_charge(self, job_ref: str) -> float | None:
        """The generation record's billed total — B4's free re-ask; None
        until the record answers (it lags the response by seconds)."""
        return await self._client.generation_cost(job_ref)
