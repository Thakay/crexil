"""Own slot transitions, one shared local lane and concurrent external lanes.

Every dispatch records its request before the runtime call. Queued work can be
adopted after restart; running work must reconcile with runtime evidence. Missing
history or a missing response does not prove that submission failed, so uncertain
work is never automatically resubmitted. Known active work is watched in its
original slot without creating another request. A runtime that stops answering
about a request it accepted leaves that attempt's outcome uncertain and benches
the runtime; its held takes wait for a participant's recorded resume, which lifts
that one bench once.

The database arbitrates landing, cancellation and charges. A late external result
keeps its original cell; missing billing evidence stays unverified. Shared actions
freeze their attempt bounds and whether their slots retry on their own; only audio
slots do, and a refused attempt that goes back to the queue settles its known
charge. Executors return transport facts and bytes; only the scheduler writes
outcomes, including the facts an adapter keeps through its request's record.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from . import audio, config, execution_outputs
from .audio_executors import LABELS as AUDIO_LABELS
from .capture import Capture, describe
from .db import Database
from .executors import (CollectedOutput, DispatchRequest, EngineFailure, ExecutionResult,
                        Executor, GpuReading, Handle, ProviderRecord, SourceImage, is_region_payload)
from .mesh import MESH_ENGINES

log = logging.getLogger("cockpit.scheduler")

OnEvent = Callable[[dict], Awaitable[None]]

RESTING = {"landed", "failed", "skipped", "set_aside", "late"}

ORPHAN_COPY = ("The cockpit restarted during the external call. Its outcome and charge "
               "are unverified. Check the provider record before deciding to run again.")
# The file name a primary output takes when its executor names none.
DEFAULT_FILENAMES = {'3d': 'output.glb', 'audio': 'output.wav'}
COLLECTABLE_COPY = ("The cockpit restarted during the external call. Its outcome and charge "
                    "are unverified. Collect existing files to look for its result; nothing "
                    "is generated again.")
UNCERTAIN_COPY = ("The runtime may have accepted this request, but its outcome could not "
                  "be confirmed. It was not submitted again. Reconcile the runtime record first.")
# Every external executor calls `before_send`, which records `slot.provider_sending`,
# right before its paid call; an attempt without that record never sent one.
UNSENT_COPY = 'The cockpit restarted before this request was sent. Nothing was charged.'


@dataclass(frozen=True)
class RetryRule:
    max_retries: int
    backoff_s: float


def default_retry_policy() -> dict[str, RetryRule]:
    """Conservative (law 9; A4 never retries): only clearly-transient
    kinds retry, visibly. A rate limit retries on the provider's clock up
    to RATE_LIMIT_RETRIES times (B2; the rule's backoff is the
    default when the provider names no window); an unreachable transport
    retries once. Everything else is terminal — reroll is the artist's
    verb."""
    return {"rate_limit": RetryRule(config.RATE_LIMIT_RETRIES,
                                    config.RATE_LIMIT_BACKOFF_S),
            "unreachable": RetryRule(1, config.RETRY_BACKOFF_S)}


# A9: the engine-level kinds whose repeat inside one turn benches the
# engine for that turn. Content-specific kinds (moderation) and the
# self-retrying ones (rate_limit) stay out; kinds that already bench the
# engine or lane at first failure (model_down, timeout, dead_key,
# unreachable) need no second rule; and A5a's `blank` keeps its own
# treatment: the same settings usually land on a second pass, so no bench
# (test-pinned), since an empty frame is a defect of one pass, not an
# engine that stopped responding.
A9_KINDS = ("error", "oom", "charged_blank")

FRAME_STEP = 64          # every local preset's width/height grid
FRAME_FLOOR = 512        # below this no smaller frame is offered


def frame_key(frame: tuple[int, int] | None) -> str | None:
    return f"{frame[0]}x{frame[1]}" if frame else None


def parse_frame(key: str | None) -> tuple[int, int] | None:
    if not key:
        return None
    try:
        w, h = key.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return None


def frame_words(frame: tuple[int, int]) -> str:
    return f"{frame[0]} × {frame[1]}"


def smaller_frame(requested: tuple[int, int],
                  landed: list[tuple[int, int]]) -> tuple[int, int] | None:
    """A1's offer: the largest frame this engine has LANDED with fewer
    pixels than the request (the record's own proof of fit), else ¾ per
    side snapped down to the 64-grid — nothing below the floor."""
    area = requested[0] * requested[1]
    held = [f for f in landed if f[0] * f[1] < area]
    if held:
        return max(held, key=lambda f: f[0] * f[1])
    w = max(FRAME_FLOOR, (requested[0] * 3 // 4) // FRAME_STEP * FRAME_STEP)
    h = max(FRAME_FLOOR, (requested[1] * 3 // 4) // FRAME_STEP * FRAME_STEP)
    if (w, h) == requested or w * h >= area:
        return None
    return (w, h)


# What an audio take was doing, and what the artist supplied, per verb: an
# audio failure names its act and absolves the text the artist wrote.
AUDIO_ACTS = {"speak": ("speaking", "your text"),
              "voice-design": ("designing the voice", "your description"),
              "sfx": ("making the sound", "your description")}
# Where a local runner's start-up instructions live; never a machine path.
RUNNER_HELP = {"voice-runner": "tools/tts/README.md says how to start it."}


def audio_label(engine: str, provider: str) -> str:
    """An audio engine's display name, as its row names it, else its provider's."""
    return (audio.engines().get(engine) or {}).get("name") or AUDIO_LABELS.get(provider, provider)


def audio_cause(kind: str, *, provider: str, lane: str, engine: str,
                verb: str | None) -> str | None:
    """The audio lane's own words: the provider or the engine by its display
    name, never an id, never a frame or an image (law 3), and what the
    outcome means for its charge and its next move (laws 4 and 5). None
    where the shared words fit (moderation, rate_limit, model_down,
    interrupted), which then name the engine by its display name too."""
    name, label = AUDIO_LABELS.get(provider, provider), audio_label(engine, provider)
    yours = AUDIO_ACTS.get(verb or "", ("working", "your text"))[1]
    if kind == "dead_key":
        return f"{name} turned the key away: nothing runs on {name} until the key is fixed"
    if kind == "unreachable":
        if lane == "local":
            return " ".join(filter(None, (f"{name} can't be reached. Is it running?",
                                          RUNNER_HELP.get(provider))))
        return f"{name} can't be reached: the network, most likely"
    if kind == "oom":
        return (f"{label} ran out of video memory: free the card (close what else "
                "is holding it) and try again")
    if kind == "error":
        return f"{label} failed: the engine, not {yours}"
    if kind == "blank":
        return f"{label} finished, but no audio came back: the engine, not {yours}"
    if kind == "charged_blank":
        return f"{label} took the job and billed it, but no audio came back"
    if kind == "timeout":
        if lane == "local":
            return f"{label} gave no result in time"
        lost = f"The answer from {label} was lost before it arrived. Its charge is unverified"
        if verb == "speak":                  # the provider keeps speech in its history
            return f"{lost}; collecting looks for the take without generating it again"
        return f"{lost}, and this take cannot be collected"
    if kind == "uncertain":
        return f"{label} may have made this take, but its outcome could not be confirmed, so it was not sent again"
    return None


# Law 3: the primary message never carries a status code, a stack
# trace or "something went wrong". One curated line per failure kind
# (static text, nothing generated), naming the engine and — where it matters
# — absolving the prompt. Whatever the engine actually said rides `detail`,
# behind the cell's collapsed affordance and in the ledger forever.
def plain_cause(kind: str, *, lane: str, engine: str,
                frame: tuple[int, int] | None = None,
                held_before: dict | None = None,
                tries: int | None = None,
                provider: str | None = None,
                verb: str | None = None) -> str:
    """One plain line per failure kind (law 3). `provider` is the executor
    that reached the engine; an audio provider gets the audio lane's own
    words, so a failure names the provider that failed, never another."""
    if kind == 'collection':
        return 'Some runtime files are missing. Collected files are kept. Collect again to retrieve this same attempt without generating.'
    if kind == 'source_changed':
        return 'A prepared source changed. Review its current bytes before trying again.'
    if kind == 'source_missing':
        return 'A prepared source is unavailable. Restore it before trying again.'
    external = lane == "external"
    mesh = engine in MESH_ENGINES        # the 3D lane's own words
    if mesh:
        if kind == "unreachable":
            return ("the 3D engine runner can't be reached — is it running? "
                    "(systemctl --user start 3d-runner)")
        if kind == "error":
            return (f"{engine} failed partway through building the mesh — "
                    "the engine, not your image")
        if kind == "oom":
            # a mesh has no frame to shrink: A1's "smaller frame" remedy has
            # no meaning here, so the copy says the true one
            return (f"{engine} ran out of video memory building the mesh — "
                    "free the card (close what else is holding it) and try "
                    "again")
        if kind == "blank":
            return (f"{engine} finished without a mesh — that's the engine, "
                    "not your image")
    if provider in audio.PROVIDERS:
        words = audio_cause(kind, provider=provider, lane=lane, engine=engine, verb=verb)
        if words:
            return words
        engine = audio_label(engine, provider)
    if kind == "dead_key":
        return ("OpenRouter turned the key away — nothing external runs "
                "until the key is fixed")
    if kind == "moderation":
        return (f"{engine} wouldn't run this prompt — their filter, not a "
                "fault at your end")
    if kind == "rate_limit":
        after = (f"after {tries} tries" if tries and tries > 2
                 else "after a second try")
        return (f"{engine} is holding us at the door — still rate limited "
                f"{after}")
    if kind == "oom":
        # A1/A11: the size named, and — when the record says this engine
        # held it before — the size explicitly absolved (A1's copy would
        # tell the artist to shrink a frame that demonstrably fits)
        at = (f"at {frame_words(frame)}" if frame else "at this frame size")
        if held_before:
            return (f"{engine} ran out of video memory {at}, though it held "
                    f"that size on turn {held_before['turn_idx']} — the "
                    "size isn't the problem; second attempts usually land")
        return (f"{engine} ran out of video memory {at} — a smaller frame "
                "is the sure fix")
    if kind == "model_down":
        return f"{engine} is down or answered with nothing usable"
    if kind == "timeout":
        return f"{engine} went silent — no result came back in time"
    if kind == "unreachable":
        return ("OpenRouter can't be reached — the network, most likely"
                if external else "ComfyUI can't be reached — is it running?")
    if kind == "interrupted":
        return f"{engine} was interrupted before it finished"
    if kind == "error":
        return (f"{engine} answered without an image" if external else
                f"{engine} failed partway through the graph — the engine, "
                "not your prompt")
    if kind == "blank":
        # A5a: the copy must explicitly absolve the prompt — quiet
        # misattribution is what makes an artist abandon a good engine
        return (f"{engine} finished, but the frame came back empty — "
                "that's the engine, not your prompt")
    if kind == "charged_blank":
        # B4: the single worst case; the amount is line 2's, the receipt
        # and the bill check are the buttons' and the ledger's
        return (f"{engine} took the job and billed it, but no image came "
                "back")
    if kind == "uncertain":
        return (f"{engine} may have made this take, but its outcome could not "
                "be confirmed, so it was not sent again")
    return f"{engine} failed"


INTERNAL_CAUSE = ("the cockpit hit an internal error on this take — reroll "
                  "to try again")

BILL_CHECK_ASKS = 3       # B4: re-fetch the same job ID three times


def raw_detail(e: EngineFailure) -> str:
    """The engine's own words, for the collapsed DETAILS and the record —
    never for the face. Bounded: a stack of ComfyUI node messages is long."""
    text = f"{e.kind}: {e}"
    if e.detail:
        text += f"\n{e.detail}"
    return text[:1600]


COMFY_EXECUTORS = frozenset({"comfy", "region"})   # the image and region executors share one ComfyUI
COMFY_SCOPE = "provider:comfy"


def runtime_scope(executor: str | None, lane: str) -> str:
    """The bench scope an executor's own key or runtime covers (law 1): an
    audio provider's, ComfyUI's (shared by the image and region executors),
    else its lane's. OpenRouter's key and network are the external lane's.
    The local lane's own bench is the one GPU (law 14), never a runtime's."""
    if executor in audio.PROVIDERS:
        return f"provider:{executor}"
    if executor in COMFY_EXECUTORS:
        return COMFY_SCOPE
    return f"lane:{lane}"


def bench_scope_for(kind: str, lane: str, engine: str,
                    provider: str | None = None) -> str | None:
    """Which scope an engine-level failure benches (law 1: nine slots killed
    by one cause produce one cause record). Content-specific and transient
    kinds never bench; an OOM is the slot's alone (A1: the artist chooses)
    unless the card is held, which the scheduler decides with the live
    reading, not this table. A dead key or an unreachable runtime benches
    the scope that key or runtime serves (`runtime_scope`) and nothing
    else: a down ComfyUI never stops the voice or 3D runner, and a down
    runner never stops ComfyUI."""
    if kind == "dead_key":
        # one key kills every engine behind it: an audio provider's key is
        # its own, any other key is OpenRouter's, the external lane's
        return f"provider:{provider}" if provider in audio.PROVIDERS else "lane:external"
    if kind in ("unreachable", "uncertain"):
        # a runtime that cannot be reached, or that stopped answering about
        # a request it had accepted; a down 3D runner is those two engines'
        # problem alone
        if engine in MESH_ENGINES:
            return f"engine:{engine}"
        return runtime_scope(provider, lane)
    if kind in ("model_down", "timeout"):
        return f"engine:{engine}"
    return None     # moderation | rate_limit | interrupted | error | blank | charged_blank | oom


def lane_covers(ex: Executor, lane: str) -> bool:
    """Whether a lane's bench holds this executor's takes. The external
    lane's bench is OpenRouter's key and network: an audio provider has its
    own scope, so another provider's fault never holds it (law 1). The local
    lane's bench is the one GPU, a held card or a pause of the whole lane,
    so it holds every local executor (law 14); ComfyUI's own failures bench
    `provider:comfy` instead (`runtime_scope`)."""
    return ex.lane == lane and not (lane == "external" and ex.name in audio.PROVIDERS)


def turn_scope(engine: str, turn_id: str) -> str:
    """A9's bench scope: this engine, this turn only."""
    return f"engine:{engine}@turn:{turn_id}"


def gpu_held_cause(reading: GpuReading) -> str:
    """C2's one message. WSL2 cannot name a Windows process, so the hold
    is a size — said as one, never guessed into a name."""
    gb = reading.foreign / (1 << 30)
    total = reading.total / (1 << 30)
    return (f"something else is holding {gb:.1f} GB of the {total:.0f} GB "
            "card — the local engines can't get a turn until it lets go; "
            "external takes carry on, and the queue picks itself up when "
            "the card is free")


@dataclass
class BenchEntry:
    """One live bench (A9/E6): the scope it covers, the kind, the plain
    cause, and `seq`, the `engine.benched` event that installed it (0 until
    that event is written). A bench lifted and installed again is another
    bench with its own seq, so a recorded resume lifts one bench, once. Not
    the wire model — `schemas.Bench` is what the queue view serialises this
    into."""
    scope: str
    kind: str
    cause: str
    seq: int = 0


@dataclass
class _Take:
    """The registry entry: the live lease on one slot."""
    request: DispatchRequest
    executor: Executor
    lane: str
    turn_id: str = ""
    handle: Handle | None = None
    task: asyncio.Task | None = None
    cancel_requested: bool = False
    detached: bool = False           # cancelled but the ride lives on
    started_mono: float | None = None
    recovered: bool = False


class Scheduler:
    def __init__(self, db: Database, executors: Sequence[Executor], *,
                 route: Callable[[str], str],
                 on_event: OnEvent | None = None,
                 external_concurrency: int | None = None,
                 retry_policy: Mapping[str, RetryRule] | None = None,
                 local_take_timeout_s: float | None = None,
                 external_take_timeout_s: float | None = None,
                 staging_dir: Path | str | None = None,
                 bill_check_gap_s: float | None = None,
                 gpu_probe: Callable[[], Awaitable[GpuReading | None]]
                 | None = None,
                 gpu_foreign_hold_bytes: int | None = None,
                 gpu_recheck_s: float | None = None,
                 capture: Capture | None = None) -> None:
        self.db = db
        # the one door for failures no kind names: the cockpit's
        # own code failing on a take, a lane loop, a background task
        self.capture = capture if capture is not None else Capture(db)
        self._capturing_emit = False
        self._executors = {e.name: e for e in executors}
        # an executor's own cap on takes in flight, below the shared external
        # permit; a take waiting for it stays queued (law 17)
        self._permits = {e.name: asyncio.Semaphore(e.max_concurrency) for e in executors
                         if getattr(e, "max_concurrency", None)}
        self._route = route
        self._on_event = on_event
        self._ext_sem = asyncio.Semaphore(
            external_concurrency or config.EXTERNAL_CONCURRENCY)
        self._retry = (dict(retry_policy) if retry_policy is not None
                       else default_retry_policy())
        self._local_timeout = (local_take_timeout_s
                               if local_take_timeout_s is not None
                               else config.LOCAL_TAKE_TIMEOUT_S)
        self._external_timeout = (external_take_timeout_s
                                  if external_take_timeout_s is not None
                                  else config.EXTERNAL_TAKE_TIMEOUT_S)
        self._staging = Path(staging_dir or config.STAGING_DIR)
        self._bill_gap = (bill_check_gap_s if bill_check_gap_s is not None
                          else config.BILL_CHECK_GAP_S)
        self._checks: dict[str, dict] = {}       # slot_id -> {ask, of} (B4)
        self._retries: dict[str, dict] = {}      # slot_id -> the wait (B2)
        # C2: the one live look at the card. None = no probe wired (tests
        # without one, or a stack without ComfyUI) — an OOM is then A1/A11
        # on its own, never C2.
        self._gpu_probe = gpu_probe
        # The card reads as held only when memory that is neither ComfyUI's
        # nor free passes this threshold. Our own runners count in that
        # remainder, since WSL2 cannot attribute memory to a process; an idle
        # voice runner keeps only its CUDA context (0.6 to 1.1 GB measured),
        # which the default 2.5 GB leaves room for beside the desktop's own
        # slice. A runner that is generating never sits beside an image
        # take: local jobs take turns (law 14).
        self._gpu_hold_bytes = (gpu_foreign_hold_bytes
                                if gpu_foreign_hold_bytes is not None
                                else config.GPU_FOREIGN_HOLD_BYTES)
        self._gpu_recheck_s = (gpu_recheck_s if gpu_recheck_s is not None
                               else config.GPU_RECHECK_S)
        self._gpu_task: asyncio.Task | None = None
        self._gpu_manual_hold = False            # HOLD THE QUEUE pressed
        self._gpu_last: GpuReading | None = None
        self._takes: dict[str, _Take] = {}
        self._tasks: set[asyncio.Task] = set()   # every spawned take task
        self._local_q: deque[str] = deque()
        self._local_wake = asyncio.Event()
        self._shielded: set[asyncio.Task] = set()
        self._benches: dict[str, BenchEntry] = {}
        self._emit_cursor = 0
        self._started = False
        self._stopping = False
        self._local_task: asyncio.Task | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for ex in self._executors.values():
            await ex.open()
        try:
            await self._recover()
            await self.apply_execution_controls()
        except Exception as e:
            # a failed recovery fails the boot, loudly — but the store is
            # open, so the reason outlives the process
            await self.capture.record(
                "scheduler.start", e,
                note="recovery failed and the lanes never opened; the store "
                     "is intact — fix forward and restart the cockpit")
            raise
        self._local_task = asyncio.create_task(
            self._local_loop(), name="scheduler-local-lane")
        self.capture.watch(
            self._local_task, "scheduler.task:scheduler-local-lane",
            note="the local lane's loop stopped; local takes queue but "
                 "nothing runs them until the cockpit restarts")
        b = self._benches.get("lane:local")
        if b and b.kind == "gpu_held":
            self._start_gpu_recheck()      # a replayed C2 keeps watching

    async def stop(self) -> None:
        """Crash-equivalent by design: running rows stay running; the next
        start() recovers them."""
        self._stopping = True
        tasks = list(self._tasks)      # spawned takes, registry-popped or not
        if self._local_task:
            tasks.append(self._local_task)
        if self._gpu_task:
            tasks.append(self._gpu_task)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with suppress(BaseException):
                await t
        # in-flight shielded finalisers get to finish their DB writes before
        # anyone closes the store under them — and one that died on the way
        # out is recorded, not swallowed by the gather
        results = await asyncio.gather(*list(self._shielded),
                                       return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                await self.capture.record(
                    "scheduler.finaliser", r,
                    note="a finaliser failed while the cockpit was stopping; "
                         "its slot shows where it stopped — reroll to re-run")
        for ex in self._executors.values():
            with suppress(Exception):
                await ex.close()

    # --- emit discipline: every appended event reaches on_event, in order ---

    async def _emit_new(self) -> None:
        recorded = False
        while True:
            events = await self.db.events_since(self._emit_cursor, limit=500)
            if not events:
                return
            failed: Exception | None = None
            for ev in events:
                self._emit_cursor = ev["seq"]
                if self._on_event:
                    try:
                        await self._on_event(ev)
                    except Exception as e:
                        log.warning("on_event callback failed for seq=%s",
                                    ev["seq"], exc_info=True)
                        failed = failed or e
            if failed is not None and not recorded and \
                    not self._capturing_emit:
                # one record per call, never re-entered: the record is
                # itself an event (emitted by the next pass of this loop),
                # and a listener that fails on every event would otherwise
                # chase its own tail; repeats fold in the capture
                recorded = True
                self._capturing_emit = True
                try:
                    await self.capture.record(
                        "scheduler.on_event", failed,
                        note="a live-feed listener failed on an event; the "
                             "ledger is untouched and the poll re-reads it")
                finally:
                    self._capturing_emit = False

    # --- routing / registry helpers ---

    def _executor_for(self, engine: str,
                      payload: dict | None = None) -> Executor:
        """The route callable stays single-arg (engine id); a region take is
        told apart by its PAYLOAD (the `{"region_plan": …}` envelope in
        submitted_graph) because its engine ids are ordinary preset
        ids. Lane-only callers (bench_for, _scope_covers) pass no payload:
        region and comfy are both local, so the lane answer is right anyway."""
        name = ("region" if is_region_payload(payload)
                and "region" in self._executors else self._route(engine))
        ex = self._executors.get(name)
        if ex is None:
            raise ValueError(f"no executor named {name!r} for engine {engine!r}")
        return ex

    def executor(self, name: str) -> Executor | None:
        """The registered executor by name, for availability questions."""
        return self._executors.get(name)

    def bench_for(self, engine: str, turn_id: str | None = None
                  ) -> BenchEntry | None:
        b = self._benches.get(f"engine:{engine}")
        if b:
            return b
        if turn_id:
            b = self._benches.get(turn_scope(engine, turn_id))
            if b:
                return b
        ex = self._executor_for(engine)
        b = self._benches.get(f"provider:{ex.name}")
        if b:
            return b
        return self._benches.get(f"lane:{ex.lane}") if lane_covers(ex, ex.lane) else None

    def standing(self, scope: str | None) -> BenchEntry | None:
        """The bench installed under this scope now, if one stands."""
        return self._benches.get(scope) if scope else None

    def bench_holding(self, engine: str) -> BenchEntry | None:
        """The bench a new take of this engine would wait behind now, by the
        rule that holds takes (`bench_for`), so a catalogue's availability
        never drifts from benching (law 12). None when nothing holds it, or
        when no executor serves the engine."""
        try:
            return self.bench_for(engine)
        except ValueError:
            return None

    @property
    def started(self) -> bool:
        """Accepting work: start() ran and stop() hasn't."""
        return self._started and not self._stopping

    def snapshot(self) -> dict:
        return {
            "local_queue": list(self._local_q),
            "takes": {sid: {"lane": t.lane,
                            "cancel_requested": t.cancel_requested,
                            "detached": t.detached,
                            "running": t.started_mono is not None,
                            # a region take's pipeline position:
                            # the queue view's "running · pass 2 of 4"
                            "passes": getattr(t.handle, "passes", None)}
                      for sid, t in self._takes.items()},
            "benches": {s: {"kind": b.kind, "cause": b.cause}
                        for s, b in self._benches.items()},
            # B4's bill checks in flight — the queue view carries them so
            # the cell can say "asking about the bill · 1 of 3" (law 9)
            "checks": {sid: dict(c) for sid, c in self._checks.items()},
            # B2's waits: attempt i of n and when the next try fires, so
            # the cell can count down on the provider's clock (law 9)
            "retries": {sid: dict(r) for sid, r in self._retries.items()},
            # C2: the last reading and whether the lane is re-checking
            "gpu": {"reading": (None if self._gpu_last is None else
                                self._gpu_last.__dict__),
                    "rechecking": bool(self._gpu_task
                                       and not self._gpu_task.done()),
                    "manual_hold": self._gpu_manual_hold},
        }

    def _shield(self, coro) -> asyncio.Future:
        """Shield a finaliser AND track it, so stop() can await stragglers
        instead of leaving them to hit a closed database."""
        t = asyncio.create_task(coro)
        self._shielded.add(t)
        t.add_done_callback(self._shielded.discard)
        return asyncio.shield(t)

    def _enqueue(self, slot_id: str) -> None:
        take = self._takes.get(slot_id)
        if take is None:
            return                       # finalised under us; nothing to run
        if take.lane == "local":
            self._local_q.append(slot_id)
            self._local_wake.set()
        else:
            if take.task and not take.task.done():
                take.task.cancel()   # stale semaphore-waiter from a pre-bench spawn
            take.task = asyncio.create_task(
                self._run_external(slot_id), name=f"take-{slot_id}")
            self._tasks.add(take.task)
            take.task.add_done_callback(self._tasks.discard)
            self.capture.watch(take.task, "scheduler.task:external-take",
                               slot_id=slot_id, turn_id=take.turn_id,
                               engine=take.request.engine)

    # --- submission ---

    async def submit_turn(self, turn_id: str,
                          requests: Sequence[DispatchRequest]) -> None:
        """Hand a created turn's prepared requests to the lanes. The request
        set must equal the turn's slot set exactly — the scheduler never
        invents or drops slots (law 8: geometry is frozen at Run)."""
        if not self._started:
            raise RuntimeError("scheduler not started")
        turn = await self.db.get_turn(turn_id)
        if turn is None:
            raise ValueError(f"unknown turn {turn_id}")
        by_id = {r.slot_id: r for r in requests}
        if set(by_id) != {s["id"] for s in turn["slots"]}:
            raise ValueError(
                "request set must match the turn's slots exactly (law 8)")
        already = set(by_id) & set(self._takes)
        if already:
            raise ValueError(
                f"slots already scheduled (recovery adopted them?): "
                f"{sorted(already)}")
        for slot in turn["slots"]:            # pos order
            req = by_id[slot["id"]]
            await self._register(slot, req, workspace_id=turn["workspace_id"],
                                 turn_id=turn_id)
        await self._emit_new()

    async def adopt_prepared_turn(self, turn_id: str) -> None:
        """Idempotently enqueue a durably committed shared-action turn.

        Slot payloads and slot.prepared events already exist in the transaction
        that accepted the action. Recovery uses the same record. Replayed
        delivery never reopens a resting or already registered slot.
        """
        if not self._started:
            raise RuntimeError("scheduler not started")
        async with self.db._locked():
            turn = await self.db.get_turn(turn_id)
            if turn is None:
                raise ValueError(f"unknown turn {turn_id}")
            for slot in turn['slots']:
                if slot['status'] != 'queued' or slot['id'] in self._takes:
                    continue
                if await self._hold_if_paused(slot):
                    continue
                req = await self._rebuild_request(slot)
                if req is None:
                    raise ValueError('The committed request payload is missing.')
                ex = self._executor_for(req.engine, req.submitted_graph)
                self._takes[slot['id']] = _Take(request=req, executor=ex, lane=ex.lane, turn_id=turn_id)
                bench = self.bench_for(req.engine, turn_id)
                try:
                    if bench:
                        await self._hold(slot['id'], bench)
                    else:
                        self._enqueue(slot['id'])
                except BaseException:
                    self._takes.pop(slot['id'], None)
                    raise
        await self._emit_new()

    async def resubmit_slot(self, slot_id: str,
                            request: DispatchRequest) -> None:
        """Single-slot re-entry after the caller's reroll_slot (reroll or
        backfill)."""
        if not self._started:
            raise RuntimeError("scheduler not started")
        slot = await self.db.get_slot(slot_id)
        if slot is None:
            raise ValueError(f"unknown slot {slot_id}")
        if slot["status"] != "queued":
            raise ValueError(f"slot {slot_id} is {slot['status']}, not queued")
        ids = await self.db._slot_ids(slot)
        await self._register(slot, request, workspace_id=ids["workspace_id"],
                             turn_id=slot["turn_id"])
        await self._emit_new()

    async def _register(self, slot: dict, req: DispatchRequest, *,
                        workspace_id: str | None, turn_id: str) -> None:
        ex = self._executor_for(req.engine, req.submitted_graph)
        self._takes[slot["id"]] = _Take(request=req, executor=ex, lane=ex.lane,
                                        turn_id=turn_id)
        # land-fields ride an event so restart recovery stays faithful;
        # payload durability is the ROW (stamped at creation) — never blobs here
        await self.db.append_event(
            "slot.prepared", workspace_id=workspace_id, turn_id=turn_id,
            slot_id=slot["id"],
            payload={"engine": req.engine, "payload": req.payload,
                     "verb": req.verb, "prompt": req.prompt,
                     "parents": list(req.parents), "x": req.x, "y": req.y})
        if await self._hold_if_paused(slot):
            self._takes.pop(slot['id'], None)
            return
        b = self.bench_for(req.engine, turn_id)
        if b:
            await self._hold(slot["id"], b)
        else:
            self._enqueue(slot["id"])

    async def _hold_if_paused(self, slot: dict) -> bool:
        from .execution_controls import ExecutionCancelled, ExecutionPaused, guard_dispatch
        try:
            await guard_dispatch(self.db, slot)
        except ExecutionCancelled:
            await self._skip_cancelled_retry(slot)
            return True
        except ExecutionPaused as exc:
            await self.db.transition_slot(slot['id'], 'held', payload={'pause': True, 'reason': str(exc)},
                                          cause=str(exc))
            return True
        return False

    async def _skip_cancelled_retry(self, slot: dict) -> None:
        from .execution_controls import cancel_requested
        async with self.db._tx():
            current = await self.db.get_slot(slot['id'])
            if (not current or current['attempt'] != slot['attempt']
                    or current['status'] not in ('queued', 'held')
                    or not current.get('retry_of') or not await cancel_requested(self.db, current)):
                return
            await self.db.transition_slot(slot['id'], 'skipped', payload={
                'reason': 'Cancellation requested before automatic retry', 'attempt': slot['attempt']})
            self._takes.pop(slot['id'], None)
            self._retries.pop(slot['id'], None)

    # --- lanes ---

    async def _local_loop(self) -> None:
        while True:
            while not self._local_q:
                self._local_wake.clear()
                await self._local_wake.wait()
            slot_id = self._local_q.popleft()
            try:
                take = self._takes.get(slot_id)
                if take is None:
                    continue
                b = self.bench_for(take.request.engine, take.turn_id)
                if b and not take.recovered:
                    await self._hold(slot_id, b)
                    continue
                await self._run_take_safely(slot_id)   # awaited inline: serialism
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # the loop itself is the lane: a crash outside the take
                # (a route with no executor, a hold that couldn't be
                # written) fails THAT slot and the lane serves the next
                await self._crashed(slot_id, e, "scheduler.local_lane")

    async def _run_external(self, slot_id: str) -> None:
        """One external take, re-entering after every retry wait OUTSIDE
        the permit (B2): a provider holding us at the door must not hold
        the lane's other providers with it."""
        try:
            while True:
                take = self._takes.get(slot_id)
                if take is None or take.cancel_requested:
                    return
                permit = self._permits.get(take.executor.name)
                async with permit or nullcontext(), self._ext_sem:
                    take = self._takes.get(slot_id)
                    if take is None or take.cancel_requested:
                        return
                    b = self.bench_for(take.request.engine, take.turn_id)
                    if b and not take.recovered:
                        await self._hold(slot_id, b)
                        return
                    wait_s = await self._run_take_safely(slot_id)
                if wait_s is None:
                    return
                await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._crashed(slot_id, e, "scheduler.external_lane")

    async def _run_take_safely(self, slot_id: str) -> float | None:
        """One buggy take must never kill its lane (law 1's spirit).
        Returns an external take's retry wait (seconds) when it should
        re-enter after sleeping outside the permit, else None."""
        try:
            return await self._run_take(slot_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._crashed(slot_id, e, "scheduler.take")
            return None

    async def _crashed(self, slot_id: str, exc: Exception, site: str) -> None:
        """The cockpit's OWN code failed on a take. The failure is
        recorded first — site, error, traceback, the slot's ids — then the
        slot fails as `internal` with the record's number in its DETAILS:
        the face stays plain (law 3) and the receipt points at a row the
        artist can reach, not a log they can't. A take that hadn't been
        dispatched yet is skipped in words rather than left queued behind
        nothing. Never raises."""
        take = self._takes.pop(slot_id, None)
        self._retries.pop(slot_id, None)
        ids: dict = {"slot_id": slot_id,
                     "turn_id": take.turn_id if take else None}
        engine = take.request.engine if take else None
        attempt = None
        with suppress(Exception):
            slot = await self.db.get_slot(slot_id)
            if slot:
                ids = await self.db._slot_ids(slot)
                engine, attempt = slot["engine"], slot["attempt"]
        row = await self.capture.record(
            site, exc, **ids, engine=engine, attempt=attempt,
            note="the take failed as internal; the lane carried on")
        where = (f"recorded as event #{row['seq']}" if row else
                 "the record couldn't be written — the cockpit's log has it")
        detail = f"internal take crash — {describe(exc)['error']}\n{where}"
        try:
            await self.db.fail_slot(slot_id, cause=INTERNAL_CAUSE,
                                    kind="internal", detail=detail)
        except ValueError:
            # not running: crashed before dispatch (queued) or moved under
            # us (landed/cancelled — the winner is right)
            with suppress(Exception):
                await self.db.transition_slot(
                    slot_id, "skipped",
                    payload={"reason": f"{INTERNAL_CAUSE}; {where}",
                             "detail": detail})
        except Exception as e2:
            await self.capture.record(
                "scheduler.crashed", e2, **ids, engine=engine,
                note="the internal failure could not be written to the slot")
        with suppress(Exception):
            await self._emit_new()

    async def _hold(self, slot_id: str, bench: BenchEntry) -> None:
        with suppress(ValueError):   # moved under us (cancelled/landed); the winner is right
            await self.db.transition_slot(slot_id, "held",
                                          payload={"bench": bench.scope})
        await self._emit_new()

    # --- one take ---

    async def _run_take(self, slot_id: str) -> float | None:
        take = self._takes[slot_id]
        req = take.request
        ex = take.executor
        timeout = (self._local_timeout if ex.lane == "local"
                   else self._external_timeout)
        while True:
            self._retries.pop(slot_id, None)     # the wait, if any, is over
            if take.cancel_requested and not take.recovered:
                await self._emit_new()
                return None   # stays queued; cancel_slot's sweep will skip it
            recovered = take.recovered
            take.recovered = False
            try:
                if recovered:
                    slot = await self.db.get_slot(slot_id)
                    if slot['status'] != 'running':
                        return None
                else:
                    # A crash between the call and receipt is uncertain, even
                    # when the runtime reference has not yet been recorded.
                    slot = await self.db.dispatch_slot(
                        slot_id, job_ref=None,
                        submitted_graph=req.submitted_graph, external=req.external,
                        committed_usd=req.committed_usd, seed=req.seed,
                        frame=frame_key(req.frame))
                    take.started_mono = time.monotonic()
            except ValueError as exc:
                from .crexil_budget import BudgetExceeded
                from .execution_controls import ExecutionCancelled, ExecutionPaused
                if isinstance(exc, ExecutionCancelled):
                    await self._skip_cancelled_retry(await self.db.get_slot(slot_id))
                elif isinstance(exc, (BudgetExceeded, ExecutionPaused)):
                    await self.db.transition_slot(slot_id, 'held', payload={'reason': str(exc),
                        'limit': isinstance(exc, BudgetExceeded), 'pause': isinstance(exc, ExecutionPaused)},
                        cause=str(exc))
                    self._takes.pop(slot_id, None)
                await self._emit_new()
                return None
            req = self._collecting_request(req, slot, ex.name)
            take.request = req
            await self._emit_new()
            try:
                if not recovered:
                    take.handle = await ex.start(req)
                    if take.handle.job_ref:
                        await self.db.set_job_ref(slot_id, take.handle.job_ref)
                    # A stop requested during source upload has no handle yet.
                    # Apply it as soon as the runtime supplies that handle.
                    if take.cancel_requested and take.lane == 'local':
                        await ex.cancel(take.handle)
                if timeout:
                    try:
                        result = await asyncio.wait_for(
                            ex.wait(take.handle), timeout)
                    except TimeoutError:
                        with suppress(Exception):
                            await ex.cancel(take.handle)
                        raise EngineFailure(
                            "timeout",
                            f"no result within {timeout:g}s") from None
                else:
                    result = await ex.wait(take.handle)
            except EngineFailure as e:
                if take.detached:
                    # the cancelled ride ended without a result — the
                    # slot rests `late`; _finalize_failure would fail/bench
                    # over a corpse
                    await self._shield(
                        self._finalize_late_failure(take, slot, e))
                    return None
                if (e.kind == "oom" and take.lane == "local"
                        and slot.get("dials", {}).get("crexil", {}).get("automatic_retry", True)
                        and not take.cancel_requested):
                    reading = await self._probe_gpu()
                    if reading is not None and \
                            reading.foreign >= self._gpu_hold_bytes:
                        # C2: not the frame's fault — the card is held
                        await self._shield(
                            self._gpu_held(take, slot, e, reading))
                        return None
                rule = self._retry.get(e.kind)
                if (rule and slot.get("dials", {}).get("crexil", {}).get("automatic_retry", True)
                        and slot["attempt"] <= rule.max_retries
                        and not take.cancel_requested):
                    wait_s = await self._retry_requeue(take, slot, e, rule)
                    if take.cancel_requested or wait_s is None:
                        return None          # cancel beat the backoff
                    if take.lane == "external":
                        return wait_s        # sleep outside the permit
                    await asyncio.sleep(wait_s)
                    continue
                await self._shield(self._finalize_failure(take, slot, e))
                return None
            except asyncio.CancelledError:
                raise                        # stop(): rows stay for recover()
            await self._shield(self._finalize_land(take, slot, result))
            return None

    async def _retry_requeue(self, take: _Take, slot: dict,
                             e: EngineFailure, rule: RetryRule
                             ) -> float | None:
        """Law 9: nothing retries silently — the retry is an event before it
        is a sleep. Returns the wait in seconds (the caller sleeps; an
        external take sleeps outside its permit), or None when the slot
        moved under us. A rate limit waits on the provider's clock when it
        named one (B2), capped so a hostile header can't park the slot."""
        wait_s = rule.backoff_s
        window = "cockpit"
        if e.kind == "rate_limit" and e.retry_after_s is not None:
            wait_s = min(max(0.0, e.retry_after_s), config.RATE_LIMIT_MAX_WAIT_S)
            window = "provider"
        of = rule.max_retries + 1
        next_at = (datetime.now(timezone.utc)
                   + timedelta(seconds=wait_s)).isoformat()
        if not await self._requeue_failed_attempt(take, slot, e, e.kind, {
                "kind": e.kind, "attempt": slot["attempt"],
                "next_attempt": slot["attempt"] + 1, "of": of,
                "backoff_s": wait_s, "window": window, "next_try_at": next_at}):
            return None
        self._retries[slot["id"]] = {
            "kind": e.kind, "attempt": slot["attempt"] + 1, "of": of,
            "wait_s": wait_s, "window": window, "next_try_at": next_at}
        await self._emit_new()
        return wait_s

    async def _requeue_failed_attempt(self, take: _Take, slot: dict,
                                      failure: EngineFailure, retry_of: str, payload: dict) -> bool:
        """The failed attempt can retry only before a durable stop wins its transaction."""
        from .execution_controls import cancel_requested
        async with self.db._tx():
            current = await self.db.get_slot(slot['id'])
            same_attempt = current and current['attempt'] == slot['attempt']
            stopped = take.cancel_requested or (same_attempt and await cancel_requested(self.db, current))
            if same_attempt and current['status'] == 'running' and not stopped:
                # The refused attempt's known charge (zero before generation)
                # settles it, so its reservation stops counting against every
                # cap (law 22); an unknown charge keeps it reserved.
                await self.db.append_event('slot.retrying', **await self.db._slot_ids(slot), payload=payload,
                                           charged_usd=await self.db._unrecorded_charge(slot, failure.charged_usd))
                await self.db.transition_slot(slot['id'], 'queued',
                    payload={'retry_of': retry_of}, retry_of=retry_of)
                return True
            if stopped:
                take.cancel_requested = True
        # The failure still needs a collector and its charge evidence. Never
        # abandon a running row just because its next attempt was refused.
        await self._shield(self._finalize_failure(take, slot, failure))
        return False

    # --- finalisers (each shielded by the caller; DB is the arbiter) ---

    async def collect_existing(self, slot_id: str, attempt: int) -> dict:
        slot = await self.db.get_slot(slot_id)
        if not slot or slot['attempt'] != attempt:
            raise ValueError('This attempt changed. Refresh its record before collecting files.')
        from .crexil_restore import guard as guard_restored
        guard_restored(slot)
        if slot['status'] not in {'failed', 'landed', 'set_aside', 'late'}:
            raise ValueError('This request is still active. Its current collector owns the files.')
        ex = self._executor_for(slot['engine'], slot['submitted_graph'])
        req = await self._rebuild_request(slot)
        if req is None:
            raise ValueError('The saved execution request is missing. No work was repeated.')
        req = self._collecting_request(req, slot, ex.name)
        if ex.name == 'openrouter' and (req.external or {}).get('transport') == 'openrouter-images':
            receipt = await self.db._one('SELECT o.* FROM execution_outputs o JOIN execution_groups g ON g.id=o.group_id '
                "WHERE g.slot_id=? AND g.take=? AND o.role='provider receipt'", (slot_id, attempt))
            if not receipt or not receipt['output_path'] or not receipt['sha256'] or not await asyncio.to_thread(
                    execution_outputs.matches_receipt, Path(receipt['output_path']), receipt['sha256']):
                raise ValueError('No intact provider response was retained. No paid request was repeated.')
            try:
                retained = await asyncio.to_thread(Path(receipt['output_path']).read_bytes)
                result = await ex.collect_receipt(req, json.loads(retained))
                from .executors import Reconciliation
                rec = Reconciliation('landed', result=result)
            except EngineFailure as exc:
                await execution_outputs.finish(self.db, slot, 'incomplete', {'failure_kind': exc.kind, 'cause': str(exc)})
                return await execution_outputs.group(self.db, slot)
        elif ex.name == 'comfy' and slot['job_ref']:
            rec = await ex.reconcile(slot, request=req)
        elif ex.name == 'mesh' and slot['job_ref']:
            item = await execution_outputs.group(self.db, slot)
            primary = await self.db._one("SELECT * FROM execution_outputs WHERE group_id=? AND identity='primary'", (item['id'],))
            if primary and await execution_outputs.purged_at(self.db, primary):
                raise ValueError('This mesh was purged. Collection cannot restore purged files.')
            rec = await ex.collect_existing(slot, request=req)
            if rec.kind == 'landed' and rec.result:
                primary = await self.db._one("SELECT * FROM execution_outputs WHERE group_id=? AND identity='primary'", (item['id'],))
                for suffix, blob in (rec.result.sidecars or {}).items():
                    if not suffix.startswith('.') or '/' in suffix or len(suffix) > 16:
                        raise ValueError('Invalid companion file extension.')
                    companion = CollectedOutput(identity='companion:' + suffix, filename='companion' + suffix,
                        data=blob, payload=None, role='companion')
                    try:
                        async with self.db._tx():
                            if await execution_outputs.purged_at(self.db, primary):
                                continue
                            path = Path(primary['output_path']).with_suffix(suffix)
                            await execution_outputs.collect(self.db, self._staging, slot, req, replace(companion, data=None), path=path)
                            await execution_outputs.collect(self.db, self._staging, slot, req, companion, path=path)
                    except OSError:
                        await execution_outputs.collect(self.db, self._staging, slot, req,
                            replace(companion, data=None, error='The companion file could not be stored.'))
        elif ex.name != 'mesh' and callable(getattr(ex, 'collect_existing', None)):
            rec = await ex.collect_existing(slot, request=req)
        else:
            raise ValueError('This runtime has no supported retrieval reference for the attempt. No work was repeated.')
        if rec.kind == 'landed':
            state = await execution_outputs.finish(self.db, slot, 'complete')
            if state == 'complete':
                await self.db.recovered_collection(slot_id, attempt)
        elif rec.kind == 'failed':
            await execution_outputs.finish(self.db, slot, 'incomplete', {'failure_kind': rec.failure_kind, 'cause': rec.cause})
        else:
            await execution_outputs.finish(self.db, slot, 'uncertain', {'runtime_state': rec.kind,
                'cause': 'The runtime has no completed result available for retrieval.'})
        await self._emit_new()
        return await execution_outputs.group(self.db, slot)

    def _collecting_request(self, req: DispatchRequest, slot: dict, provider: str) -> DispatchRequest:
        async def before_send() -> None:
            from .crexil_budget import guard_dispatch
            from .execution_controls import cancel_requested
            async with self.db._tx():
                current = await self.db.get_slot(slot['id'])
                if not current or current['attempt'] != slot['attempt'] or current['status'] != 'running':
                    raise ValueError('The request was stopped before it reached the provider.')
                if await cancel_requested(self.db, current):
                    raise ValueError('Cancellation was requested before this request reached the provider.')
                await guard_dispatch(self.db, current)
                await self.db.append_event('slot.provider_sending', **await self.db._slot_ids(slot),
                                           payload={'attempt': slot['attempt']})
        async def collected(output: CollectedOutput) -> bytes | None:
            # The callback persists facts through the server; engines own no DB.
            if output.role == 'provider receipt' and (output.external or {}).get('reported_cost_usd') is not None:
                await self._shield(self.db.report_provider_charge(slot, output.external['reported_cost_usd'],
                                                                 output.external.get('generation_id')))
            current = await self.db.get_slot(slot['id'])
            recorded = {**slot, 'job_ref': current.get('job_ref') if current else slot.get('job_ref')}
            if output.data is not None:
                await self._shield(execution_outputs.collect(self.db, self._staging, recorded, req, replace(output, data=None)))
            member = await self._shield(execution_outputs.collect(self.db, self._staging, recorded, req, output))
            await self._emit_new()
            if output.data is None and member['output_path'] and not await execution_outputs.purged_at(self.db, member):
                with suppress(OSError):
                    blob = await asyncio.to_thread(Path(member['output_path']).read_bytes)
                    if await asyncio.to_thread(execution_outputs.sha256_bytes, blob) == member['sha256']:
                        return blob
            return None
        return replace(req, on_output=collected, before_send=before_send,
                       record=self._provider_record(slot, provider), attempt=slot['attempt'])

    def _provider_record(self, slot: dict, provider: str) -> ProviderRecord:
        """The record one attempt's adapter reads and extends through the
        server. A saved voice is kept the moment it is answered, shielded, so a
        cancel or a stop right after the save never loses it."""
        async def attempts() -> dict:
            return await self.db.provider_attempts(provider, slot_id=slot['id'], attempt=slot['attempt'])

        async def saved_voice(generated_voice_id: str) -> str | None:
            return await self.db.saved_voice(provider, generated_voice_id)

        async def keep_saved_voice(generated_voice_id: str, voice_id: str, *, found: bool = False) -> None:
            await self._shield(self.db.record_saved_voice(slot, provider, generated_voice_id, voice_id, found=found))
            await self._emit_new()

        return ProviderRecord(attempts=attempts, saved_voice=saved_voice, keep_saved_voice=keep_saved_voice)

    async def resume_budget_holds(self, workspace_id: str | None = None) -> None:
        """Reconsider accepted, unsent work when its enclosing limits permit it."""
        from .crexil_budget import BudgetExceeded, guard_dispatch
        from .execution_controls import ExecutionCancelled, ExecutionPaused, guard_dispatch as guard_control
        async with self.db._locked():
            for slot in await self.db.slots_by_status('held'):
                ids = await self.db._slot_ids(slot)
                if workspace_id is not None and ids['workspace_id'] != workspace_id:
                    continue
                holds = [event for event in await self.db.slot_events(slot['id']) if event['type'] == 'slot.held']
                if not holds or not any(holds[-1]['payload'].get(key) for key in ('limit', 'pause')):
                    continue
                try:
                    await guard_control(self.db, slot)
                    await guard_dispatch(self.db, slot)
                except ExecutionCancelled:
                    await self._skip_cancelled_retry(slot)
                    continue
                except (BudgetExceeded, ExecutionPaused):
                    continue
                if await self._rebuild_request(slot) is None:
                    continue
                await self.db.transition_slot(slot['id'], 'queued', payload={'reason': 'limits now permit this request'},
                                              cause=None)
                if slot['id'] in self._takes:
                    with suppress(ValueError):
                        self._local_q.remove(slot['id'])
                    self._enqueue(slot['id'])
                else:
                    await self.adopt_prepared_turn(slot['turn_id'])

    async def apply_execution_controls(self) -> None:
        """Apply the recorded controls: skipped attempts leave the lanes,
        durable stops reach running attempts, and a recorded resume lifts the
        bench it names. A resume names one bench by the event that installed
        it, so a bench lifted and installed again since stays, and a replayed
        or restarted request never resumes twice."""
        from .execution_controls import cancellations, resumes
        for sid in list(self._takes):
            current = await self.db.get_slot(sid)
            if current and current['status'] == 'skipped':
                take = self._takes.pop(sid, None)
                if take:
                    take.cancel_requested = True
                    if take.task and not take.task.done():
                        take.task.cancel()
                with suppress(ValueError):
                    self._local_q.remove(sid)
                self._retries.pop(sid, None)
        for target in await cancellations(self.db):
            current = await self.db.get_slot(target['slot_id'])
            if current and current['attempt'] == target['attempt']:
                await self.cancel_slot(target['slot_id'])
        for intent in await resumes(self.db, {scope: b.seq for scope, b in self._benches.items()}):
            bench = self._benches.get(intent['bench'])
            if bench is not None and bench.seq == intent['bench_seq']:
                await self.unbench(intent['bench'], actor=intent['actor'], reason=intent['reason'])
        await self.resume_budget_holds()
        await self._emit_new()

    def _staging_path(self, slot: dict, result: ExecutionResult) -> Path:
        suffix = Path(result.filename).suffix if result.filename else ".png"
        p = (self._staging / slot["turn_id"] /
             f"{slot['id']}-take{slot['attempt']}{suffix or '.png'}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def _finalize_land(self, take: _Take, slot: dict,
                             result: ExecutionResult) -> None:
        req = take.request
        output = CollectedOutput(identity=result.primary_identity or 'primary',
            filename=result.filename or DEFAULT_FILENAMES.get(req.payload, 'output.png'),
            data=result.data, payload=None if req.payload == 'file' else req.payload, role='unclassified',
            descriptor=result.descriptor or {}, submitted_graph=result.submitted_graph, external=result.external)
        await execution_outputs.collect(self.db, self._staging, slot, req, replace(output, data=None),
            path=self._staging_path(slot, result) if not result.primary_identity else None)
        member = await execution_outputs.collect(self.db, self._staging, slot, req, output,
            path=self._staging_path(slot, result) if not result.primary_identity else None)
        if member['error'] and req.payload != 'file' and member['node_id'] is None:
            # the primary file arrived but cannot land as its declared payload
            await execution_outputs.finish(self.db, slot, 'incomplete', {'collection_errors': [member['error']]})
            await self._finalize_failure(take, slot, EngineFailure('collection', member['error'],
                                                                   charged_usd=result.charged_usd))
            return
        path = Path(member['output_path'])
        missing = []
        for suffix, blob in (result.sidecars or {}).items():
            # Legacy mesh consumers use these explicit sibling extensions.
            if not suffix.startswith('.') or '/' in suffix or len(suffix) > 16:
                raise ValueError('Invalid companion file extension.')
            companion = CollectedOutput(identity='companion:' + suffix, filename='companion' + suffix,
                data=blob, payload=None, role='companion')
            try:
                await execution_outputs.collect(self.db, self._staging, slot, req, replace(companion, data=None),
                                                path=path.with_suffix(suffix))
                await execution_outputs.collect(self.db, self._staging, slot, req, companion,
                                                path=path.with_suffix(suffix))
            except OSError as exc:
                missing.append(str(exc))
                await execution_outputs.collect(self.db, self._staging, slot, req,
                    replace(companion, data=None, error='The companion file could not be stored.'))
        state = await execution_outputs.finish(self.db, slot, 'incomplete' if missing else 'complete',
                                       {'collection_errors': missing, 'primary_output_id': member['id']})
        if state != 'complete':
            await self._finalize_failure(take, slot, EngineFailure('collection',
                'Some reported outputs remain unavailable. Collected files were kept.', charged_usd=result.charged_usd))
            return
        duration_ms = (int((time.monotonic() - take.started_mono) * 1000)
                       if take.started_mono is not None else None)
        charged = (round(result.charged_usd, 6)
                   if result.charged_usd is not None else None)
        node = None
        try:
            if req.payload == 'file':
                await self.db.complete_files(slot['id'], slot['attempt'], charged_usd=charged)
            else:
                node = await self.db.land_node(
                    slot["id"], payload=req.payload, verb=req.verb,
                    output_path=str(path), prompt=req.prompt,
                    parents=list(req.parents), external=result.external,
                    submitted_graph=result.submitted_graph,
                    duration_ms=duration_ms, charged_usd=charged,
                    x=req.x, y=req.y, output_index=member["ordinal"],
                    expected_attempt=slot["attempt"])
        except ValueError:
            # lost the race to another finaliser; the money still gets its
            # one ledger door and the artefact is kept (law 7)
            if charged is not None:
                charged = await self.db._unrecorded_charge(slot, charged)
                await self.db.record_charge(
                    slot["id"], charged,
                    note=f"generation completed after cancel; output kept at {path}")
        finally:
            self._takes.pop(slot["id"], None)
        if node is not None and take.detached:
            # the cancelled ride still delivered — it landed late,
            # into its own original frozen cell (late -> landed)
            ids = await self.db._slot_ids(slot)
            await self.db.append_event(
                "slot.late_landed", **ids, node_id=node["id"],
                payload={"charged_usd": charged,
                         "committed_usd": slot["committed_usd"],
                         "note": "bytes arrived after cancel; landed into "
                                 "the original frozen cell (laws 7+8)"})
        await self._emit_new()

    def _blank_path(self, slot: dict) -> Path:
        p = (self._staging / slot["turn_id"] /
             f"{slot['id']}-take{slot['attempt']}-blank.png")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def _oom_facts(self, engine: str, frame: tuple[int, int] | None
                         ) -> dict:
        """A1/A11 from the record (the failure history is an instrument):
        has this engine landed this frame before, and what smaller frame
        has it landed."""
        landed = await self.db.frames_landed(engine)
        facts: dict = {"frame": frame_key(frame), "held_before": None,
                       "smaller": None}
        if frame is None:
            return facts
        for row in landed:
            if row["frame"] == frame_key(frame):
                facts["held_before"] = {"turn_idx": row["turn_idx"],
                                        "pos": row["pos"], "n": row["n"]}
        held = [f for f in (parse_frame(r["frame"]) for r in landed) if f]
        small = smaller_frame(frame, held)
        facts["smaller"] = frame_key(small)
        return facts

    async def _finalize_failure(self, take: _Take, slot: dict,
                                e: EngineFailure) -> None:
        from .execution_controls import cancel_requested
        async with self.db._locked():
            current = await self.db.get_slot(slot['id'])
            if not current or current['attempt'] != slot['attempt']:
                return
            if current['status'] == 'late':
                await self._finalize_late_failure(take, slot, e)
                return
            if current['status'] != 'running':
                return
            if await cancel_requested(self.db, current):
                take.cancel_requested = True
            await self._finalize_running_failure(take, slot, e)

    async def _finalize_running_failure(self, take: _Take, slot: dict,
                                        e: EngineFailure) -> None:
        req = take.request
        await execution_outputs.finish(self.db, slot, 'incomplete', {'failure_kind': e.kind, 'cause': str(e)})
        cancelled = e.kind == "interrupted" and take.cancel_requested
        facts: dict | None = None
        if e.kind == "oom" and not cancelled:
            facts = await self._oom_facts(req.engine, req.frame)
        # A refusal the executor worded for the face (`plain`) keeps its own
        # words: they name the real cause and the next move (laws 3, 4).
        cause = ("cancelled" if cancelled else str(e) if e.kind == 'preflight' or e.plain else
                 plain_cause(e.kind, lane=take.lane, engine=req.engine,
                             frame=req.frame,
                             held_before=(facts or {}).get("held_before"),
                             tries=slot["attempt"],
                             provider=take.executor.name, verb=req.verb))
        detail = raw_detail(e)
        if e.artefact:
            # A5a: the empty frame is kept, not lost (law 7) — named in
            # DETAILS so a bug report can carry it
            path = self._blank_path(slot)
            await asyncio.to_thread(path.write_bytes, e.artefact)
            detail = f"{detail}\nthe empty frame is kept at {path}"
        if e.job_ref:
            # B4: the generation id the response carried — the receipt's
            # and the bill check's handle. Stamped while the row is still
            # running (set_job_ref's guard); a lost race means the winner
            # already knows what happened.
            with suppress(ValueError):
                await self.db.set_job_ref(slot["id"], e.job_ref)
        scope = None if take.cancel_requested else bench_scope_for(
            e.kind, take.lane, req.engine, take.executor.name)
        if scope:
            # Install the shared cause before publishing a terminal failure.
            # Readers of the completed row must already see its queue gate.
            kind, shared = e.kind, cause
            if e.kind == "uncertain":
                # A runtime that stopped answering: its other takes wait
                # behind its unreachable bench, in its own words (law 1).
                kind, shared = "unreachable", plain_cause(
                    "unreachable", lane=take.lane, engine=req.engine,
                    provider=take.executor.name, verb=req.verb)
            await self.bench(scope, kind=kind, cause=shared)
        failed = False
        try:
            await self.db.fail_slot(slot["id"], cause=cause, detail=detail,
                                    kind=e.kind, charged_usd=e.charged_usd,
                                    facts=facts)
            failed = True
        except ValueError:
            pass                             # lost to land/cancel; winner is right
        if failed and e.kind == "charged_blank":
            # the one automatic action, and it is not a retry (B4)
            t = asyncio.create_task(
                self._bill_check(slot, take.executor, e.job_ref,
                                 e.charged_usd),
                name=f"bill-check-{slot['id']}")
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
            self.capture.watch(
                t, "scheduler.task:bill-check", slot_id=slot["id"],
                turn_id=slot["turn_id"], engine=req.engine,
                note="the bill re-asks stopped early; the amount stays as "
                     "the response reported it")
        known_charge = await self.db._one("SELECT 1 FROM events WHERE slot_id=? AND type='slot.provider_charge_reported' "
                                          "AND json_extract(payload_json,'$.attempt')=?", (slot['id'], slot['attempt']))
        if (failed and e.kind == "timeout" and take.lane == "external"
                and slot["committed_usd"] is not None and not known_charge):
            # 300 s of silence: the call may have completed + billed server-side
            ids = await self.db._slot_ids(slot)
            await self.db.append_event(
                "slot.charge_unverified", **ids,
                payload={"committed_usd": slot["committed_usd"],
                         "reason": "timeout",
                         "note": "no response before the timeout; if the "
                                 "generation completed server-side it billed "
                                 "(unverified)"})
        self._takes.pop(slot["id"], None)
        await self._emit_new()
        if not take.cancel_requested and not scope and failed and e.kind in A9_KINDS:
            await self._repeat_check(slot, req.engine)

    async def _repeat_check(self, slot: dict, engine: str) -> None:
        """A9: the second engine-level failure of one engine inside one
        turn benches it for the turn — one cause, one message, the rest
        of its takes held rather than run into the same wall."""
        turn_id = slot["turn_id"]
        scope = turn_scope(engine, turn_id)
        if scope in self._benches:
            return
        failed = await self.db.failed_in_turn(turn_id, engine, A9_KINDS)
        if len(failed) < 2:
            return
        first, again = failed[0], failed[-1]
        cause = (f"{engine} failed on slot {first['pos'] + 1}, and again on "
                 f"slot {again['pos'] + 1} — benched for this turn; its "
                 "remaining takes here are held, not dropped")
        await self.bench(scope, kind="repeat", cause=cause)

    # --- C2: the card held by another program ---

    async def _probe_gpu(self) -> GpuReading | None:
        if self._gpu_probe is None:
            return None
        try:
            reading = await self._gpu_probe()
        except Exception as e:
            await self.capture.record(
                "scheduler.gpu_probe", e,
                note="the card couldn't be read and counts as no reading: "
                     "an OOM is the frame's own (never C2) and the headroom "
                     "sheet shows no card until it reads again")
            return None
        if reading is not None:
            self._gpu_last = reading
        return reading

    async def _gpu_held(self, take: _Take, slot: dict, e: EngineFailure,
                        reading: GpuReading) -> None:
        """The OOM was the hold's, not the frame's: the take goes back to
        the queue visibly (law 9) and the local lane benches under
        `gpu_held` — held takes, external takes untouched (law 10) — with
        the re-check loop watching for the card to come free."""
        if not await self._requeue_failed_attempt(take, slot, e, 'gpu_held', {
                     "kind": "gpu_held", "attempt": slot["attempt"],
                     "next_attempt": slot["attempt"] + 1,
                     "foreign_bytes": reading.foreign,
                     "total_bytes": reading.total,
                     "detail": raw_detail(e),
                     "note": "ran out of memory while something else holds "
                             "the card; re-queued, the lane waits for the "
                             "card to come free"}):
            return
        await self._emit_new()
        # the loop is armed BEFORE the bench flag goes up: the flag is what
        # the queue view (and HOLD THE QUEUE) sees, and a hold pressed in
        # the gap must find the loop already there to cancel — never be
        # undone by this method finishing later
        self._gpu_manual_hold = False
        self._start_gpu_recheck()
        await self.bench("lane:local", kind="gpu_held",
                         cause=gpu_held_cause(reading))   # sweeps this take

    def _start_gpu_recheck(self) -> None:
        if self._gpu_task and not self._gpu_task.done():
            return
        self._gpu_task = asyncio.create_task(self._gpu_recheck_loop(),
                                             name="gpu-recheck")
        self.capture.watch(
            self._gpu_task, "scheduler.task:gpu-recheck",
            note="the automatic re-check stopped; CHECK AGAIN looks now and "
                 "an unbench of lane:local resumes the lane")

    async def _gpu_recheck_loop(self) -> None:
        """Every GPU_RECHECK_S, look at the card; when the hold is gone the
        lane unbenches itself — "the queue picks itself up when the card
        is free". HOLD THE QUEUE (`gpu_hold`) stops this loop."""
        while True:
            await asyncio.sleep(self._gpu_recheck_s)
            b = self._benches.get("lane:local")
            if b is None or b.kind != "gpu_held" or self._gpu_manual_hold:
                return
            reading = await self._probe_gpu()
            if reading is not None and reading.foreign < self._gpu_hold_bytes:
                await self.db.append_event(
                    "gpu.freed", payload={"foreign_bytes": reading.foreign,
                                          "total_bytes": reading.total,
                                          "by": "recheck"})
                await self.unbench("lane:local")
                return

    async def gpu_check(self) -> dict:
        """CHECK AGAIN: one live look now. Under a gpu_held bench a clear
        card resumes the lane at once; otherwise the reading is reported
        and the bench stands."""
        reading = await self._probe_gpu()
        b = self._benches.get("lane:local")
        held = bool(b and b.kind == "gpu_held")
        cleared = False
        if held and reading is not None and \
                reading.foreign < self._gpu_hold_bytes:
            await self.db.append_event(
                "gpu.freed", payload={"foreign_bytes": reading.foreign,
                                      "total_bytes": reading.total,
                                      "by": "check"})
            await self.unbench("lane:local")
            cleared = True
        return {"reading": None if reading is None else reading.__dict__,
                "held": held and not cleared, "cleared": cleared,
                "threshold_bytes": self._gpu_hold_bytes,
                "rechecking": bool(self._gpu_task and not self._gpu_task.done()
                                   and not cleared)}

    async def card_still_held(self) -> str | None:
        """Why the card still holds the local lane (C2), in plain words with
        the next move; None when a reading now shows it free. No reading
        cannot show the card free, so it keeps the lane held."""
        reading = await self._probe_gpu()
        if reading is None:
            return ("The card cannot be read right now, so it cannot be shown free. "
                    "Try again in a moment.")
        if reading.foreign >= self._gpu_hold_bytes:
            return (f"Something else still holds {reading.foreign / (1 << 30):.1f} GB of the card. "
                    "Close what holds it, then resume.")
        return None

    async def gpu_hold(self) -> dict:
        """HOLD THE QUEUE: keep the lane benched until an explicit unbench —
        the re-check stops; nothing moves without the artist."""
        self._gpu_manual_hold = True
        if self._gpu_task and not self._gpu_task.done():
            self._gpu_task.cancel()
            with suppress(BaseException):
                await self._gpu_task
        b = self._benches.get("lane:local")
        await self.db.append_event("gpu.hold_queue", payload={
            "held": bool(b and b.kind == "gpu_held")})
        await self._emit_new()
        return {"held": bool(b and b.kind == "gpu_held"),
                "rechecking": False}

    async def _bill_check(self, slot: dict, executor: Executor,
                          job_ref: str | None,
                          reported: float | None) -> None:
        """B4's free re-asks: three over about a minute, each an event
        before it is a call; never puts work in flight. The answer is the
        provider's own record; a difference from what the response
        reported corrects the ledger by the delta, once."""
        ids = await self.db._slot_ids(slot)
        sid = slot["id"]
        if not job_ref:
            await self.db.append_event(
                "slot.charge_unverified", **ids,
                payload={"committed_usd": slot["committed_usd"],
                         "charged_usd": reported, "reason": "no_job_id",
                         "note": "the response carried no generation id, "
                                 "so the bill can't be re-asked — the "
                                 "receipt says the amount as reported"})
            await self._emit_new()
            return
        answer: float | None = None
        asks = 0
        try:
            for ask in range(1, BILL_CHECK_ASKS + 1):
                asks = ask
                await self.db.append_event(
                    "slot.bill_check", **ids,
                    payload={"ask": ask, "of": BILL_CHECK_ASKS,
                             "job_ref": job_ref})
                self._checks[sid] = {"ask": ask, "of": BILL_CHECK_ASKS}
                await self._emit_new()
                verify = getattr(executor, "verify_charge", None)
                try:
                    answer = (await verify(job_ref)) if verify else None
                except Exception as e:
                    await self.capture.record(
                        "scheduler.bill_check", e, **ids,
                        engine=slot["engine"], attempt=slot["attempt"],
                        note=f"re-ask {ask} of {BILL_CHECK_ASKS} failed and "
                             "counts as no answer")
                    answer = None
                if answer is not None:
                    break
                if ask < BILL_CHECK_ASKS:
                    await asyncio.sleep(self._bill_gap)
        finally:
            self._checks.pop(sid, None)
        if answer is None:
            await self.db.append_event(
                "slot.charge_unverified", **ids,
                payload={"committed_usd": slot["committed_usd"],
                         "charged_usd": reported, "reason": "bill_check",
                         "asks": asks, "job_ref": job_ref,
                         "note": f"asked {asks} times over about a minute; "
                                 "no answer — the amount stays as the "
                                 "response reported it"})
            await self._emit_new()
            return
        answer = round(answer, 6)
        delta = round(answer - (reported or 0.0), 6)
        if delta != 0:
            await self.db.record_charge(
                sid, delta, attempt=slot['attempt'],
                note=f"bill check: the provider's record says "
                     f"{answer:.4f} USD where the response reported "
                     f"{(reported or 0.0):.4f}")
        await self.db.append_event(
            "slot.charge_verified", **ids,
            payload={"charged_usd": answer, "reported_usd": reported, 'attempt': slot['attempt'],
                     "asks": asks, "job_ref": job_ref})
        await self._emit_new()

    async def _finalize_late_failure(self, take: _Take, slot: dict,
                                     e: EngineFailure) -> None:
        """Retain unresolved stopped calls and their eventual charge evidence.

        A verified unsent preflight stops without spending. Sent calls remain
        late for recovery; failure alone does not establish the provider's bill.
        """
        await execution_outputs.finish(self.db, slot, 'incomplete', {'failure_kind': e.kind, 'cause': str(e)})
        ids = await self.db._slot_ids(slot)
        if e.job_ref:
            await self.db.set_late_job_ref(slot['id'], slot['attempt'], e.job_ref)
        if e.charged_usd is not None:
            delta = await self.db._unrecorded_charge(slot, e.charged_usd)
            await self.db.record_charge(slot['id'], delta, attempt=slot['attempt'], note='Final charge after stop')
        unsent = e.kind == 'preflight' and e.charged_usd == 0
        if unsent:
            await self.db.confirm_unsent_stop(slot)
        await self.db.append_event(
            "slot.late_failed", **ids,
            payload={"kind": e.kind,
                     "attempt": slot['attempt'], "job_ref": e.job_ref, "charged_usd": e.charged_usd,
                     "cause": str(e) if e.plain else plain_cause(e.kind, lane=take.lane,
                                                                 engine=take.request.engine,
                                                                 provider=take.executor.name,
                                                                 verb=take.request.verb),
                     "detail": raw_detail(e),
                     "committed_usd": slot["committed_usd"],
                     "note": "Stopped before sending. No paid request was made." if unsent else
                             "The cancelled call ended without a result. Its charge remains unresolved "
                             "unless a provider amount has been recorded."})
        self._takes.pop(slot["id"], None)
        await self._emit_new()

    # --- bench: killing an engine benches it and holds its takes ---

    async def bench(self, scope: str, *, kind: str, cause: str) -> None:
        if scope in self._benches:
            return                           # law 1: one cause record
        entry = self._benches[scope] = BenchEntry(scope, kind, cause)   # flag FIRST, sync
        held = []
        for slot_id in list(self._takes):
            take = self._takes.get(slot_id)
            if take is None or not self._scope_covers(
                    scope, take.request.engine, take.turn_id):
                continue
            try:
                await self.db.transition_slot(slot_id, "held",
                                              payload={"bench": scope})
            except ValueError:
                continue                     # running/landed/skipped: not ours to park
            held.append(slot_id)
            with suppress(ValueError):
                self._local_q.remove(slot_id)
        installed = await self.db.append_event(
            "engine.benched",
            payload={"scope": scope, "kind": kind, "cause": cause,
                     "held": held})
        entry.seq = installed["seq"]
        await self._emit_new()

    async def held_by(self, scope: str) -> list[dict]:
        """The attempts a bench holds: held slots whose last hold names it.
        A slot held for another reason (a pause, a limit, another bench)
        stays where it is."""
        held = []
        for slot in await self.db.slots_by_status("held"):
            try:
                if not self._scope_covers(scope, slot["engine"], slot["turn_id"]):
                    continue
            except ValueError:
                continue                     # no executor serves this engine now
            holds = [e for e in await self.db.slot_events(slot["id"])
                     if e["type"] == "slot.held"]
            if holds and holds[-1]["payload"].get("bench") == scope:
                held.append(slot)
        return held

    async def unbench(self, scope: str, *, actor: dict | None = None,
                      reason: str = "") -> list[str]:
        """Lift a bench: the attempts it holds go back to the queue. A lift a
        participant asked for records them and their reason on its event
        (law 23). Returns the attempts that went back to the queue."""
        if scope not in self._benches:
            return []
        del self._benches[scope]
        if scope == "lane:local":
            self._gpu_manual_hold = False
        resumed = []
        for slot in await self.held_by(scope):
            if slot["id"] not in self._takes:
                req = await self._rebuild_request(slot)
                if req is None:
                    with suppress(ValueError):
                        await self.db.transition_slot(
                            slot["id"], "skipped",
                            payload={"reason": "prepared payload lost across "
                                               "restart; reroll to re-run"})
                    continue
                ex = self._executor_for(slot["engine"],
                                        slot["submitted_graph"])
                self._takes[slot["id"]] = _Take(request=req, executor=ex,
                                                lane=ex.lane,
                                                turn_id=slot["turn_id"])
            try:
                await self.db.transition_slot(slot["id"], "queued",
                                              payload={"unbench": scope})
            except ValueError:
                continue
            resumed.append(slot["id"])
            self._enqueue(slot["id"])
        authored = {"actor": actor, "reason": reason} if actor else {}
        await self.db.append_event(
            "engine.unbenched", payload={"scope": scope, "resumed": resumed, **authored})
        await self._emit_new()
        return resumed

    def _scope_covers(self, scope: str, engine: str,
                      turn_id: str | None = None) -> bool:
        if scope.startswith("engine:"):
            rest = scope.split(":", 1)[1]
            if "@turn:" in rest:             # A9: this engine, this turn only
                eng, tid = rest.split("@turn:", 1)
                return engine == eng and turn_id == tid
            return engine == rest
        if scope.startswith("provider:"):
            return self._executor_for(engine).name == scope.split(":", 1)[1]
        if scope.startswith("lane:"):
            return lane_covers(self._executor_for(engine), scope.split(":", 1)[1])
        return False

    # --- cancel (one breath) ---

    async def cancel_slot(self, slot_id: str) -> dict:
        slot = await self.db.get_slot(slot_id)
        if slot is None:
            raise ValueError(f"unknown slot {slot_id}")
        take = self._takes.get(slot_id)
        status = slot["status"]
        if status in RESTING:
            return {"slot_id": slot_id, "action": "kept", "status": status}
        if status in ("queued", "held"):
            if take:
                take.cancel_requested = True
            try:
                await self.db.transition_slot(
                    slot_id, "skipped",
                    payload={"reason": "cancelled", "note": "nothing spent"})
            except ValueError:               # moved under us; report what is
                fresh = await self.db.get_slot(slot_id)
                return {"slot_id": slot_id, "action": "kept",
                        "status": fresh["status"] if fresh else "unknown"}
            with suppress(ValueError):
                self._local_q.remove(slot_id)
            self._takes.pop(slot_id, None)
            self._retries.pop(slot_id, None)  # B2's give-up: the wait ends
            await self._emit_new()
            return {"slot_id": slot_id, "action": "skipped"}
        # running
        if take is None:
            return {"slot_id": slot_id, "action": "unowned",
                    "status": status}
        take.cancel_requested = True
        if take.lane == "local":
            if take.handle is not None:
                with suppress(Exception):
                    await take.executor.cancel(take.handle)
            return {"slot_id": slot_id, "action": "interrupting"}
        # external: the money is already in flight — never shoot the
        # ride. Detach it; the slot rests `late` and may still land into its
        # own cell. detached BEFORE the transition: the ride may resolve any
        # moment and its finaliser must see it.
        take.detached = True
        try:
            await self.db.transition_slot(
                slot_id, "late",
                payload={"reason": "cancel",
                         "committed_usd": slot["committed_usd"]})
        except ValueError:                   # landed/failed under us
            fresh = await self.db.get_slot(slot_id)
            return {"slot_id": slot_id, "action": "kept",
                    "status": fresh["status"] if fresh else "unknown"}
        ids = await self.db._slot_ids(slot)
        await self.db.append_event(
            "slot.charge_unverified", **ids,
            payload={"committed_usd": slot["committed_usd"],
                     "reason": "cancel",
                     "note": "call left in flight; if it completes, the "
                             "take lands late into its own cell — "
                             "incomplete generations are not billed "
                             "(unverified)"})
        await self._emit_new()
        return {"slot_id": slot_id, "action": "cancelled_in_flight",
                "committed_usd": slot["committed_usd"]}

    async def cancel_turn(self, turn_id: str) -> dict:
        turn = await self.db.get_turn(turn_id)
        if turn is None:
            raise ValueError(f"unknown turn {turn_id}")
        # flag the whole turn synchronously FIRST: the lane freed by an
        # interrupt below must not slip the next queued slot into dispatch
        # while this loop is still walking the grid
        for slot in turn["slots"]:
            take = self._takes.get(slot["id"])
            if take:
                take.cancel_requested = True
        summary: dict = {"kept": 0, "skipped": 0, "interrupted": 0,
                         "in_flight": []}
        for slot in turn["slots"]:
            r = await self.cancel_slot(slot["id"])
            if r["action"] in ("kept", "unowned"):
                summary["kept"] += 1
            elif r["action"] == "skipped":
                summary["skipped"] += 1
            elif r["action"] == "interrupting":
                summary["interrupted"] += 1
            elif r["action"] == "cancelled_in_flight":
                summary["in_flight"].append(
                    {"slot_id": r["slot_id"],
                     "committed_usd": r.get("committed_usd")})
        await self.db.append_event("turn.cancelled",
                                   workspace_id=turn["workspace_id"],
                                   turn_id=turn_id, payload=summary)
        await self._emit_new()
        return summary

    # --- restart recovery ---

    async def _rebuild_request(self, slot: dict) -> DispatchRequest | None:
        """Payloads from the row (creation- or dispatch-stamped); land-fields
        from the last slot.prepared event, else honest defaults."""
        if slot["submitted_graph"] is None and slot["external"] is None:
            return None
        land: dict = {}
        for ev in await self.db.slot_events(slot["id"]):
            if ev["type"] == "slot.prepared":
                land = ev["payload"]
        sources = []
        for source in slot.get('dials', {}).get('crexil', {}).get('source_uploads', []):
            node = await self.db.get_node(source['node_id'])
            sources.append(SourceImage(path=node['output_path'] if node and not node['purged_at'] else '',
                                       sha256=source['sha256'], name=source['name']))
        return DispatchRequest(
            slot_id=slot["id"], engine=slot["engine"],
            submitted_graph=slot["submitted_graph"],
            external=slot["external"],
            committed_usd=slot["committed_usd"],
            frame=parse_frame(slot["frame"]) or (tuple(land["frame"]) if land.get("frame") else None),
            payload=land.get("payload") or "image",
            verb=land.get("verb") or "run",
            prompt=land.get("prompt"),
            parents=tuple(land.get("parents") or ()),
            seed=slot["seed"], x=land.get("x"), y=land.get("y"), sources=tuple(sources),
            output_ports=tuple(slot.get('dials', {}).get('crexil', {}).get('output_ports', [])))

    async def _recover(self) -> None:
        # 1) rebuild benches from the event log + park the emit cursor at the
        #    pre-recovery tail so on_event replays nothing historical
        seq = 0
        while True:
            page = await self.db.events_since(seq, limit=500)
            if not page:
                break
            for ev in page:
                seq = ev["seq"]
                if ev["type"] == "engine.benched":
                    p = ev["payload"]
                    self._benches[p["scope"]] = BenchEntry(
                        p["scope"], p.get("kind", ""), p.get("cause", ""), ev["seq"])
                elif ev["type"] == "engine.unbenched":
                    self._benches.pop(ev["payload"]["scope"], None)
        self._emit_cursor = seq

        counts = {"requeued": 0, "landed": 0, "failed": 0, "orphaned": 0,
                  "skipped": 0, "unreachable": 0, "resumed": 0, "uncertain": 0}
        to_start: list[str] = []
        for slot in await self.db.pending_slots():
            ex = self._executor_for(slot["engine"], slot["submitted_graph"])
            req = await self._rebuild_request(slot)
            ids = await self.db._slot_ids(slot)
            if slot["status"] == "running":
                if req:
                    await execution_outputs.recover_files(self.db, self._staging, slot, req)
                if ex.lane == "external" and not await self.db._one(
                        "SELECT 1 FROM events WHERE slot_id=? AND type='slot.provider_sending' "
                        "AND json_extract(payload_json,'$.attempt')=?", (slot['id'], slot['attempt'])):
                    # Stopped before its paid call: nothing left, so its charge is a known zero (law 5).
                    await execution_outputs.finish(self.db, slot, 'incomplete',
                                                   {'failure_kind': 'preflight', 'cause': UNSENT_COPY})
                    with suppress(ValueError):
                        await self.db.fail_slot(slot['id'], cause=UNSENT_COPY, kind='preflight', charged_usd=0)
                    counts['failed'] += 1
                    continue
                if ex.lane == "external":
                    from .execution_controls import cancel_requested
                    stopped = await cancel_requested(self.db, slot)
                    retained = await self.db._one("SELECT 1 FROM execution_outputs o JOIN execution_groups g ON g.id=o.group_id "
                        "WHERE g.slot_id=? AND g.take=? AND o.role='provider receipt' AND o.sha256 IS NOT NULL", (slot['id'], slot['attempt']))
                    cause = ('Provider response retained. Collect existing files to recover available results.'
                             if retained else COLLECTABLE_COPY if callable(getattr(ex, 'collect_existing', None))
                             else ORPHAN_COPY)
                    await execution_outputs.finish(self.db, slot, 'uncertain', {'cause': cause})
                    # never auto-resubmit money, and say so
                    with suppress(ValueError):
                        if stopped:
                            await self.db.transition_slot(slot['id'], 'late',
                                payload={'reason': 'Cancellation remains unresolved after restart',
                                         'committed_usd': slot['committed_usd']})
                        else:
                            await self.db.fail_slot(slot["id"], cause=cause, kind="restart")
                    await self.db.append_event(
                        "slot.charge_unverified", **ids,
                        payload={"committed_usd": slot["committed_usd"],
                                 "reason": "restart",
                                 "note": "a generation that finished while "
                                         "the cockpit was down may have "
                                         "billed (unverified)"})
                    counts["orphaned"] += 1
                    continue
                if slot["job_ref"]:
                    try:
                        req = self._collecting_request(req, slot, ex.name) if req else None
                        rec = await ex.reconcile(slot, request=req)
                    except EngineFailure as e:
                        # no truth available: bench the executor's own
                        # runtime (runtime_scope), leave the row running;
                        # unbench retries recovery via requeue
                        await self.bench(runtime_scope(ex.name, ex.lane), kind=e.kind,
                                         cause=f"recovery: {e}")
                        counts["unreachable"] += 1
                        continue
                    if rec.kind == "landed" and req is not None:
                        take = _Take(request=req, executor=ex, lane=ex.lane,
                                     turn_id=slot["turn_id"])
                        self._takes[slot["id"]] = take
                        await self._finalize_land(take, slot, rec.result)
                        counts["landed"] += 1
                        continue
                    if rec.kind == "failed":
                        await execution_outputs.finish(self.db, slot, 'incomplete', {'failure_kind': rec.failure_kind, 'cause': rec.cause})
                        facts = None
                        if rec.failure_kind == "oom":
                            facts = await self._oom_facts(
                                slot["engine"], parse_frame(slot["frame"]))
                        with suppress(ValueError):
                            await self.db.fail_slot(
                                slot["id"], kind=rec.failure_kind,
                                detail=rec.cause, facts=facts,
                                cause=plain_cause(
                                    rec.failure_kind, lane=ex.lane,
                                    engine=slot["engine"],
                                    frame=parse_frame(slot["frame"]),
                                    held_before=(facts or {}).get(
                                        "held_before"),
                                    provider=ex.name,
                                    verb=req.verb if req else None))
                        counts["failed"] += 1
                        continue
                    if rec.kind == 'running' and rec.handle is not None and req is not None:
                        self._takes[slot['id']] = _Take(request=req, executor=ex, lane=ex.lane,
                            turn_id=slot['turn_id'], handle=rec.handle, recovered=True)
                        to_start.insert(0, slot['id'])
                        counts['resumed'] += 1
                        continue
                await execution_outputs.finish(self.db, slot, 'uncertain', {'cause': UNCERTAIN_COPY})
                with suppress(ValueError):
                    await self.db.fail_slot(slot['id'], kind='uncertain', cause=UNCERTAIN_COPY)
                await self.db.append_event('slot.delivery_uncertain', **ids,
                    payload={'job_ref': slot['job_ref'], 'note': UNCERTAIN_COPY})
                counts['uncertain'] += 1
            else:                            # queued
                if req is None:
                    with suppress(ValueError):
                        await self.db.transition_slot(
                            slot["id"], "skipped",
                            payload={"reason": "prepared payload lost across "
                                               "restart; reroll to re-run"})
                    counts["skipped"] += 1
                    continue
                self._takes[slot["id"]] = _Take(request=req, executor=ex,
                                                lane=ex.lane,
                                                turn_id=slot["turn_id"])
                to_start.append(slot["id"])
                counts["requeued"] += 1
        await self.db.append_event("scheduler.recovered", payload=counts)
        for slot_id in to_start:             # lanes fire only after the sweep
            self._enqueue(slot_id)
        await self._emit_new()
