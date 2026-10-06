"""Compose the store, scheduler, executors and catalogues for the app lifespan.

Each ComfyUI executor owns its client: a shared client's WebSocket handler
would be overwritten and teardown would close another executor's connection.
The OpenRouter client is shared so key changes reach its executor immediately;
the stack owns its close. Health probes use the image executor's ComfyClient.

Unavailable engines and missing credentials bench their lane without
preventing startup. Invalid presets or an incompatible database fail startup
with remediation. Tests can inject executors and inspect the current stack.

Preview takes precedence over test factories and supplies only unavailable
executors plus a network-denying provider transport. Removing a bench cannot
enable generation. Records remain real; no generated results are simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from typing import Awaitable

import asyncio
import httpx

from . import audio, audio_executors, config, secrets_store
from .capture import Capture
from .comfy import ComfyClient
from .db import Database
from .executors import (ComfyExecutor, EngineFailure, Executor, GpuReading,
                        OpenRouterExecutor, Reconciliation, read_gpu)
from .mesh import MESH_ENGINES, MeshExecutor
from .openrouter import OpenRouterClient
from .presets import load_presets
from .providers import ProviderCatalog
from .scheduler import COMFY_SCOPE, OnEvent, Scheduler


# Every executor and the lane it serves. Local executors share one GPU and
# take turns (law 14); external ones run concurrently and may cost money.
EXECUTOR_LANES = {"comfy": "local", "region": "local", "mesh": "local",
                  "openrouter": "external", **audio.PROVIDERS}


def route_engine(engine: str) -> str:
    """Engine id -> executor name, from the source that declares the engine.

    Audio engines name their provider in the capability matrix (PRD §2),
    and the provider names the executor. Mesh engines are the matrix's 3D
    rows. OpenRouter model ids are 'vendor/model': the slash is only the
    OpenRouter catalogue's own claim, checked after the declared sources.
    Every other id is a ComfyUI preset (the Run route refuses a slashed
    preset id). A region take borrows a preset id and is told apart by its
    payload in the scheduler, not here."""
    provider = audio.route(engine)
    if provider is not None:
        return provider
    if engine in MESH_ENGINES:
        return "mesh"
    return "openrouter" if "/" in engine else "comfy"


def lane_of(engine: str) -> str:
    """Engine id -> lane, for view classification (law 13)."""
    return EXECUTOR_LANES.get(route_engine(engine), "local")


# Test seam: set to a callable(openrouter) -> Sequence[Executor] and
# start builds the scheduler over those instead of the real engines
# (startup probes are skipped — they are about the real engines).
EXECUTOR_FACTORY: Callable[[OpenRouterClient], Sequence[Executor]] | None = None

# Test seam for the card reading (C2/E6): set to an async callable
# returning a GpuReading or None and the scheduler + the headroom route use
# it instead of ComfyUI's /system_stats. With fakes and no probe set, an
# OOM is A1/A11 on its own and the headroom route reports no card.
GPU_PROBE: Callable[[], Awaitable[GpuReading | None]] | None = None

# The running stack, set by start / cleared by stop — the routers read it.
current: Stack | None = None

# Startup-probe benches that auto-clear once the resource reappears. A
# mid-run dead_key bench clears only on a SUCCESSFUL key probe, never on
# mere key presence.
AUTO_CLEAR = {"no_key", "unreachable"}
PREVIEW_UNAVAILABLE = (
    "Generation is unavailable in this preview. Sources and preparation "
    "are saved locally; no GPU work or paid requests are sent.")


class PreviewTransport(httpx.AsyncBaseTransport):
    """Deny all provider requests, including key checks and bill lookups."""

    async def handle_async_request(self, request):
        raise httpx.ConnectError(PREVIEW_UNAVAILABLE, request=request)


class PreviewOpenRouter(OpenRouterClient):
    """The ordinary provider interface with no credentials or network path."""

    def __init__(self):
        self.base_url = "http://127.0.0.1:9"
        self.api_key = ""
        self.key_source = None
        self.requests_total = 0
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=PreviewTransport())


@dataclass
class UnavailableExecutor:
    """A real refusal boundary, never a generator or a successful test double."""
    name: str
    lane: str

    async def open(self):
        pass

    async def close(self):
        pass

    async def start(self, _req):
        raise EngineFailure("model_down", PREVIEW_UNAVAILABLE)

    async def wait(self, _handle):
        raise EngineFailure("model_down", PREVIEW_UNAVAILABLE)

    async def cancel(self, _handle):
        pass

    async def reconcile(self, _slot, *, request=None):  # noqa: ARG002
        return Reconciliation("failed", cause=PREVIEW_UNAVAILABLE,
                              failure_kind="model_down")

    async def verify_charge(self, _job_ref):
        return None


@dataclass
class Stack:
    db: Database
    scheduler: Scheduler
    openrouter: OpenRouterClient
    provider_catalog: ProviderCatalog
    # the preset catalogue as loaded at boot; `GET /api/presets` reloads it
    # from disk and rebinds this field (editing presets/*.json is live)
    catalog: dict[str, dict]
    comfy: ComfyClient | None      # the ComfyExecutor's own client; None with fakes
    gpu_probe: Callable[[], Awaitable[GpuReading | None]] | None = None
    capture: Capture | None = None  # the unhandled-failure door
    sequences: object | None = None


def build_openrouter() -> OpenRouterClient:
    """The one shared OpenRouter client. Key precedence: env/.env
    wins; the UI-stored secret fills in; neither = a keyless client, which
    the startup check turns into a bench, never a crash."""
    if config.CREXIL_PREVIEW:
        return PreviewOpenRouter()
    env_key = config.OPENROUTER_API_KEY
    stored_key = secrets_store.get_secret("openrouter_api_key")
    openrouter = OpenRouterClient(api_key=env_key or stored_key or "")
    openrouter.key_source = ("env" if env_key
                             else "stored" if stored_key else None)
    return openrouter


async def start(on_event: OnEvent | None = None) -> Stack:
    global current
    catalog = load_presets()       # PresetError -> loud boot failure
    openrouter = build_openrouter()
    provider_catalog = ProviderCatalog(openrouter)
    # warm the live model catalogue now, so Run's validation and the picker
    # have the full bench without waiting for the first /api/providers hit
    if not config.CREXIL_PREVIEW:
        asyncio.create_task(provider_catalog.get())
    db = Database()                # config.DB_PATH
    try:
        await db.open()            # SchemaVersionError -> loud boot failure
    except Exception:
        await db.close()
        await openrouter.close()
        raise
    comfy: ComfyClient | None = None
    scripted = EXECUTOR_FACTORY is not None or config.HARNESS_EXECUTORS
    probe = GPU_PROBE
    if config.CREXIL_PREVIEW:
        executors = [UnavailableExecutor(name, lane)
                     for name, lane in EXECUTOR_LANES.items()]
        probe = None
    elif EXECUTOR_FACTORY is not None:
        executors = list(EXECUTOR_FACTORY(openrouter))
    elif config.HARNESS_EXECUTORS:
        from .harness import build_executors, gpu_probe   # e2e mode
        executors = build_executors()
        probe = probe or gpu_probe
    else:
        comfy = ComfyClient()
        # the region executor gets its OWN client too (same WS-mirror rule);
        # its close() closes it
        from .region_exec import RegionExecutor
        executors = [ComfyExecutor(comfy), OpenRouterExecutor(openrouter),
                     RegionExecutor(ComfyClient()), MeshExecutor(),
                     *audio_executors.build()]
        if probe is None:
            client = comfy

            async def probe() -> GpuReading | None:   # C2/E6's live look
                return await read_gpu(client)
    capture = Capture(db)
    sched = Scheduler(db, executors, route=route_engine, on_event=on_event,
                      gpu_probe=probe, capture=capture)
    try:
        await sched.start()        # recovers, then opens the lanes
        if config.CREXIL_PREVIEW:
            for lane in ("local", "external"):
                await sched.bench(f"lane:{lane}", kind="preview",
                                  cause=PREVIEW_UNAVAILABLE)
        elif not scripted:
            await startup_checks(sched, comfy, openrouter)
    except BaseException:
        # a boot that fails here must still release the store — an open
        # aiosqlite connection is a non-daemon thread that turns a loud
        # failure into a hang
        await sched.stop()
        await db.close()
        await openrouter.close()
        raise
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    # the net under everything: a task nobody awaited, a callback that
    # raised, becomes a ledger row
    capture.install(asyncio.get_running_loop())
    current = Stack(db=db, scheduler=sched, openrouter=openrouter,
                    provider_catalog=provider_catalog, catalog=catalog,
                    comfy=comfy, gpu_probe=probe, capture=capture)
    from .crexil_sequences import Coordinator
    current.sequences = Coordinator(current)
    current.sequences.start()
    return current


async def startup_checks(sched: Scheduler, comfy: ComfyClient,
                         openrouter: OpenRouterClient) -> None:
    """Startup checks: translate down-engines into benches with actionable
    copy, and heal stale startup benches replayed from the event log. Runs
    after recovery so it composes with the replay. Worst case adds two 5 s
    probe timeouts."""
    benches = sched.snapshot()["benches"]

    # ComfyUI's absence benches ComfyUI alone (COMFY_SCOPE): the voice and 3D
    # runners beside it keep serving (law 1). The local lane's own bench is
    # the one GPU (law 14).
    own = benches.get(COMFY_SCOPE)
    try:
        await comfy.system_stats()
        comfy_up = True
    except Exception:
        comfy_up = False
    if not comfy_up and own is None:
        await sched.bench(
            COMFY_SCOPE, kind="unreachable",
            cause=f"Startup check: ComfyUI is not answering at {config.COMFYUI_URL}. "
                  "Start this installation's ComfyUI there (scripts/comfyui.sh), then resume it.")
    elif comfy_up and own and own["kind"] in AUTO_CLEAR:
        await sched.unbench(COMFY_SCOPE)
    stale = benches.get("lane:local")
    if stale and stale["kind"] in AUTO_CLEAR:
        # a lane bench for an unreachable runtime is a record from before
        # ComfyUI had its own scope; replayed, it would hold every local
        # engine, so it goes (ComfyUI's own bench above holds its takes)
        await sched.unbench("lane:local")

    # The 3D runner is a SECOND local engine, so its absence is an
    # ENGINE bench, never the lane's — a down runner must not stop the image
    # takes beside it (law 1).
    await mesh_startup_check(sched, benches)
    # Audio adapters bench their own engines the same way, when they say how.
    for provider in audio.PROVIDERS:
        module = audio_executors.adapter(provider)
        if module is not None and callable(getattr(module, "startup_check", None)):
            await module.startup_check(sched, benches)

    ext = benches.get("lane:external")
    if not openrouter.key_present:
        if ext is None:
            await sched.bench(
                "lane:external", kind="no_key",
                cause="Startup check: there is no OpenRouter key. Save one in Connections, "
                      "then resume hosted work.")
        return
    probe = await openrouter.probe()   # never raises
    if probe.get("ok"):
        if ext and ext["kind"] in AUTO_CLEAR | {"dead_key"}:
            await sched.unbench("lane:external")
    elif probe.get("error") == "key rejected (401)" and ext is None:
        await sched.bench(
            "lane:external", kind="dead_key",
            cause="Startup check: OpenRouter turned the key away. Replace the key in "
                  "Connections, then resume hosted work.")
    # an unreachable probe benches nothing: the first real take fails
    # kind=unreachable and benches the lane with a live cause instead


async def mesh_startup_check(sched: Scheduler, benches: dict) -> None:
    """The startup check on the 3D lane: a runner that isn't up benches its
    two engines with the address it is expected at and the checkout's own
    start script, never a service unit another installation may own, and
    the cockpit boots anyway."""
    up = await probe_mesh_runner()
    for engine in sorted(MESH_ENGINES):
        scope = f"engine:{engine}"
        b = benches.get(scope)
        if not up and b is None:
            await sched.bench(
                scope, kind="unreachable",
                cause=f"Startup check: the 3D engine runner is not answering at {config.THREED_URL}. "
                      "Start this checkout's runner there (scripts/3d-runner.sh), then resume it.")
        elif up and b and b["kind"] in AUTO_CLEAR:
            await sched.unbench(scope)


async def probe_mesh_runner() -> bool:
    """One 5 s look at the runner's door; never raises.

    Under scripted executors (the e2e harness mode, and EXECUTOR_FACTORY in
    pytest) the harness IS the mesh executor, so its door is the one that
    matters: probing the host would make the To-3D roster's state depend on
    whether a systemd unit happens to be running on this machine,
    which is the coupling the harness exists to cut. `startup_checks`
    already stands down the same way."""
    if config.CREXIL_PREVIEW:
        return False
    if EXECUTOR_FACTORY is not None or config.HARNESS_EXECUTORS:
        return True
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{config.THREED_URL}/health")
            return r.status_code == 200 and bool((r.json() or {}).get("ok"))
    except Exception:
        return False


async def stop(stack: Stack) -> None:
    global current
    # scheduler first: shielded finalisers must land their DB writes before
    # the store closes; its stop() also closes the executor-owned ComfyClients.
    # In-flight external takes ride the shared openrouter client, so it
    # closes after them; one failed step must not skip the rest.
    steps = ([stack.sequences.stop] if stack.sequences else []) + [stack.scheduler.stop]
    if stack.capture is not None:
        # scheduled captures land before the store closes; after this a
        # straggler goes to the log only (the capture says so)
        steps += [stack.capture.drain, stack.capture.uninstall]
    steps += [stack.db.close, stack.openrouter.close]
    failed: list[BaseException] = []
    for step in steps:
        try:
            out = step()
            if asyncio.iscoroutine(out):
                await out
        except Exception as e:      # noqa: PERF203 — each step must run
            failed.append(e)
    current = None
    if failed:
        raise failed[0]
