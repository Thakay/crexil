"""Stack composition: the one place the store, the two-lane scheduler, the
real executors, the preset catalogue and the shared OpenRouter client meet the
app lifespan (the sole stack).

Two hard-won rules live here:
- Every executor that talks to ComfyUI gets its OWN ComfyClient.
  `start_ws_mirror` replaces the client's handler, so two executors on one
  client would silently steal each other's mirror — and one `close()` would
  close the other's HTTP client mid-teardown. ComfyUI happily serves multiple
  WS clients. `/api/health` probes through the ComfyExecutor's client (a
  plain `system_stats`, no handler involved).
- The OpenRouterClient IS shared (one key, one place; the setup route's
  `set_key` then propagates to the executor, and `OpenRouterExecutor.close()`
  is a no-op so nothing double-closes).

Startup runs checks: a down ComfyUI or a missing key BENCHES the lane with
actionable copy — the cockpit always boots (keyless/offline is a valid
local-only deployment). Two loud exceptions: a broken preset file
(`PresetError`) and a misconfigured COCKPIT_DB (`SchemaVersionError`) fail
boot with their own remediation message — degraded modes would silently run
a cockpit that lies about what it can do.

Tests inject scripted executors through `EXECUTOR_FACTORY` and reach the
running stack through `current`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from typing import Awaitable

import asyncio

from . import config, secrets_store
from .capture import Capture
from .comfy import ComfyClient
from .db import Database
from .executors import (ComfyExecutor, Executor, GpuReading,
                        OpenRouterExecutor, read_gpu)
from .mesh import MESH_ENGINES, MeshExecutor
from .openrouter import OpenRouterClient
from .presets import load_presets
from .providers import ProviderCatalog
from .scheduler import OnEvent, Scheduler


def route_engine(engine: str) -> str:
    """Engine id -> executor name. OpenRouter model ids are 'vendor/model' —
    the slash IS the router; local preset ids never contain one (the Run
    route refuses a slashed preset id, keeping the invariant enforced). The
    mesh engines route by id: the capability matrix names them all
    (the generators and the verb tools alike), and unlike a region
    take they never borrow a preset id, so no payload peek is needed here."""
    if engine in MESH_ENGINES:
        return "mesh"
    return "openrouter" if "/" in engine else "comfy"


def lane_of(engine: str) -> str:
    """Engine id -> lane, for view classification. Mesh is local (one
    GPU, serialised across executors), so only OpenRouter is external."""
    return "external" if route_engine(engine) == "openrouter" else "local"


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


def build_openrouter() -> OpenRouterClient:
    """The one shared OpenRouter client. Key precedence: env/.env
    wins; the UI-stored secret fills in; neither = a keyless client, which
    the startup check turns into a bench, never a crash."""
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
    if EXECUTOR_FACTORY is not None:
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
                     RegionExecutor(ComfyClient()), MeshExecutor()]
        if probe is None:
            client = comfy

            async def probe() -> GpuReading | None:   # C2/E6's live look
                return await read_gpu(client)
    capture = Capture(db)
    sched = Scheduler(db, executors, route=route_engine, on_event=on_event,
                      gpu_probe=probe, capture=capture)
    try:
        await sched.start()        # recovers, then opens the lanes
        if not scripted:
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
    return current


async def startup_checks(sched: Scheduler, comfy: ComfyClient,
                         openrouter: OpenRouterClient) -> None:
    """Startup checks: translate down-engines into benches with actionable
    copy, and heal stale startup benches replayed from the event log. Runs
    after recovery so it composes with the replay. Worst case adds two 5 s
    probe timeouts."""
    benches = sched.snapshot()["benches"]

    local = benches.get("lane:local")
    try:
        await comfy.system_stats()
        comfy_up = True
    except Exception:
        comfy_up = False
    if not comfy_up and local is None:
        await sched.bench(
            "lane:local", kind="unreachable",
            cause=f"startup check: ComfyUI unreachable at "
                  f"{config.COMFYUI_URL} — start it (systemctl --user start "
                  f'comfyui), then POST /api/unbench {{"scope": '
                  f'"lane:local"}}')
    elif comfy_up and local and local["kind"] in AUTO_CLEAR:
        await sched.unbench("lane:local")

    # The 3D runner is a SECOND local engine, so its absence is an
    # ENGINE bench, never the lane's — a down runner must not stop the image
    # takes beside it (law 1).
    await mesh_startup_check(sched, benches)

    ext = benches.get("lane:external")
    if not openrouter.key_present:
        if ext is None:
            await sched.bench(
                "lane:external", kind="no_key",
                cause="startup check: no OpenRouter key — save one via "
                      "/api/setup/openrouter, then POST /api/unbench "
                      '{"scope": "lane:external"} (a bench, not a crash)')
        return
    probe = await openrouter.probe()   # never raises
    if probe.get("ok"):
        if ext and ext["kind"] in AUTO_CLEAR | {"dead_key"}:
            await sched.unbench("lane:external")
    elif probe.get("error") == "key rejected (401)" and ext is None:
        await sched.bench(
            "lane:external", kind="dead_key",
            cause="startup check: OpenRouter turned the key away — replace "
                  "it via /api/setup/openrouter, then POST /api/unbench "
                  '{"scope": "lane:external"}')
    # an unreachable probe benches nothing: the first real take fails
    # kind=unreachable and benches the lane with a live cause instead


async def mesh_startup_check(sched: Scheduler, benches: dict) -> None:
    """The startup check on the 3D lane: a runner that isn't up benches its
    two engines with the one command that fixes it, and the cockpit boots
    anyway."""
    up = await probe_mesh_runner()
    for engine in sorted(MESH_ENGINES):
        scope = f"engine:{engine}"
        b = benches.get(scope)
        if not up and b is None:
            await sched.bench(
                scope, kind="unreachable",
                cause=f"startup check: the 3D engine runner is not answering "
                      f"at {config.THREED_URL} — start it (systemctl --user "
                      f"start 3d-runner), then POST /api/unbench "
                      f'{{"scope": "{scope}"}}')
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
    steps = [stack.scheduler.stop]
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
