"""Scripted fake executors, shared by the scheduler suite and the API tests,
which wire them through the real app via `wiring.EXECUTOR_FACTORY`."""

import asyncio
from dataclasses import dataclass, field

from app.executors import (DispatchRequest, EngineFailure, ExecutionResult,
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
        return FakeHandle(job_ref=f"job-{req.slot_id}-{len(self.starts)}",
                          step=self._next_step(req.slot_id))

    async def wait(self, h: FakeHandle) -> ExecutionResult:
        self.probe.concurrent += 1
        self.probe.max_concurrent = max(self.probe.max_concurrent,
                                        self.probe.concurrent)
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
            self.probe.concurrent -= 1

    async def cancel(self, h: FakeHandle):
        self.cancels.append(h.job_ref)
        h.cancelled.set()

    async def reconcile(self, slot):
        return self.reconcile_map.get(slot["id"], Reconciliation("gone"))

    async def verify_charge(self, job_ref):
        self.asks.append(job_ref)
        answers = self.bills.get(job_ref)
        return answers.pop(0) if answers else None


def fail(kind, msg="boom"):
    return EngineFailure(kind, msg)
