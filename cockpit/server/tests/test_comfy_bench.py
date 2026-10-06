"""ComfyUI's own failures bench ComfyUI alone (law 1): the voice runner and the
3D runner beside it keep serving. The local lane's bench is the one GPU, so a
held card still holds every local take (law 14). Availability is read from
the same rule that holds takes, so a catalogue never drifts from benching
(law 12)."""

import pytest

from app import config, wiring
from app.executors import EngineFailure
from app.scheduler import COMFY_SCOPE, bench_scope_for, runtime_scope
from fakes import FakeExecutor, Step, fail
from test_scheduler import events_of, make_batch, opened, running, settle

pytestmark = pytest.mark.anyio

ROUTES = {"klein": "comfy", "z-image": "comfy", "qwen3-tts": "voice-runner", "trellis2-4b": "mesh"}
LOCAL_TRIO = ("comfy", "voice-runner", "mesh")


def local_trio():
    return [FakeExecutor(name, "local") for name in LOCAL_TRIO]


# --- the table ---

def test_comfyui_and_its_region_executor_share_one_scope():
    assert runtime_scope("comfy", "local") == runtime_scope("region", "local") == COMFY_SCOPE
    for executor in ("comfy", "region"):
        assert bench_scope_for("unreachable", "local", "klein", executor) == COMFY_SCOPE
    assert bench_scope_for("unreachable", "local", "qwen3-tts", "voice-runner") == "provider:voice-runner"
    assert bench_scope_for("unreachable", "local", "trellis2-4b", "mesh") == "engine:trellis2-4b"
    assert bench_scope_for("unreachable", "external", "v/m", "openrouter") == "lane:external"
    assert bench_scope_for("dead_key", "external", "v/m", "openrouter") == "lane:external"


# --- the scheduler ---

async def test_a_down_comfyui_leaves_the_voice_and_3d_runners_serving(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        comfy, voice, mesh = local_trio()
        async with running(db, tmp_path, [comfy, voice, mesh], ROUTES) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local"), ("qwen3-tts", "local"),
                                                   ("trellis2-4b", "local"), ("z-image", "local")])
            comfy.script = {turn["slots"][0]["id"]: [Step(outcome=fail("unreachable")),
                                                     Step(outcome=fail("unreachable"))]}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert {sl["engine"]: sl["status"] for sl in done["slots"]} == {
                "klein": "failed", "qwen3-tts": "landed", "trellis2-4b": "landed", "z-image": "held"}
            assert [e["payload"]["scope"] for e in await events_of(db, "engine.benched")] == [COMFY_SCOPE]
            assert s.bench_holding("z-image").scope == COMFY_SCOPE
            assert s.bench_holding("qwen3-tts") is None and s.bench_holding("trellis2-4b") is None

            # resuming clears exactly ComfyUI's bench, and its held take runs
            await s.unbench(COMFY_SCOPE)
            done = await settle(db, turn["id"], until={"landed", "failed"})
        assert {sl["engine"]: sl["status"] for sl in done["slots"]}["z-image"] == "landed"
        assert [e["payload"]["scope"] for e in await events_of(db, "engine.unbenched")] == [COMFY_SCOPE]


async def test_a_held_card_still_holds_every_local_take(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        async with running(db, tmp_path, local_trio(), ROUTES) as s:
            await s.bench("lane:local", kind="gpu_held", cause="Another program holds the card.")
            turn, reqs = await make_batch(db, ws, [("klein", "local"), ("qwen3-tts", "local"),
                                                   ("trellis2-4b", "local")])
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert {sl["status"] for sl in done["slots"]} == {"held"}
            assert {s.bench_holding(e).scope for e in ("klein", "qwen3-tts", "trellis2-4b")} == {"lane:local"}
            await s.unbench("lane:local")
            done = await settle(db, turn["id"], until={"landed"})
        assert {sl["status"] for sl in done["slots"]} == {"landed"}


class SilentComfy(FakeExecutor):
    """A ComfyUI that cannot say what became of a job after a restart."""

    async def reconcile(self, slot, *, request=None):
        raise EngineFailure("unreachable", "ComfyUI is not answering")


async def test_recovery_benches_comfyui_alone_when_it_cannot_answer(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        turn, _ = await make_batch(db, ws, [("klein", "local")])
        await db.dispatch_slot(turn["slots"][0]["id"], job_ref="prompt-before-restart",
                               submitted_graph={"g": 1})
        executors = [SilentComfy("comfy", "local"), FakeExecutor("voice-runner", "local"),
                     FakeExecutor("mesh", "local")]
        async with running(db, tmp_path, executors, ROUTES) as s:
            assert set(s.snapshot()["benches"]) == {COMFY_SCOPE}
            assert s.bench_holding("qwen3-tts") is None and s.bench_holding("trellis2-4b") is None


# --- the boot check ---

class BootScheduler:
    """What the boot check sees and does: the replayed benches, and its own
    bench and unbench calls."""

    def __init__(self, benches=()):
        self.benches = {b["scope"]: b for b in benches}
        self.benched, self.unbenched = [], []

    def snapshot(self):
        return {"benches": dict(self.benches)}

    async def bench(self, scope, *, kind, cause):
        self.benched.append((scope, kind, cause))
        self.benches[scope] = {"scope": scope, "kind": kind, "cause": cause}

    async def unbench(self, scope):
        self.unbenched.append(scope)
        self.benches.pop(scope, None)


class Comfy:
    def __init__(self, up):
        self.up = up

    async def system_stats(self):
        if not self.up:
            raise OSError("connection refused")
        return {}


class NoKey:
    key_present = False


@pytest.fixture
def quiet_neighbours(monkeypatch):
    """Only ComfyUI's part of the boot check is under test here."""
    async def up(*_args, **_kw):
        return True
    monkeypatch.setattr(wiring, "probe_mesh_runner", up)
    monkeypatch.setattr(wiring.audio_executors, "adapter", lambda provider: None)


async def test_comfyui_down_at_boot_benches_comfyui_alone(quiet_neighbours):
    sched = BootScheduler()
    await wiring.startup_checks(sched, Comfy(up=False), NoKey())
    comfy = [b for b in sched.benched if b[0] == COMFY_SCOPE]
    assert len(comfy) == 1 and comfy[0][1] == "unreachable"
    # The address and this installation's start script, never a service unit or a raw route.
    assert comfy[0][2] == (f"Startup check: ComfyUI is not answering at {config.COMFYUI_URL}. Start this "
                           "installation's ComfyUI there (scripts/comfyui.sh), then resume it.")
    assert "lane:local" not in sched.benches


async def test_boot_heals_comfyuis_bench_and_a_stale_lane_bench_but_keeps_a_held_card(quiet_neighbours):
    replayed = BootScheduler([{"scope": COMFY_SCOPE, "kind": "unreachable", "cause": "earlier"},
                              {"scope": "lane:local", "kind": "unreachable", "cause": "an older build"}])
    await wiring.startup_checks(replayed, Comfy(up=True), NoKey())
    assert COMFY_SCOPE in replayed.unbenched and "lane:local" in replayed.unbenched

    stale_while_down = BootScheduler([{"scope": "lane:local", "kind": "unreachable", "cause": "an older build"}])
    await wiring.startup_checks(stale_while_down, Comfy(up=False), NoKey())
    assert stale_while_down.unbenched == ["lane:local"]
    assert COMFY_SCOPE in stale_while_down.benches            # ComfyUI's own bench holds its takes instead

    held_card = BootScheduler([{"scope": "lane:local", "kind": "gpu_held", "cause": "held"}])
    await wiring.startup_checks(held_card, Comfy(up=True), NoKey())
    assert "lane:local" not in held_card.unbenched           # the one GPU is not the boot check's to lift


# --- availability reads the same rule ---

async def test_while_comfyui_is_down_its_operations_say_so_and_voice_and_3d_do_not(tmp_path, monkeypatch):
    from test_audio_contract import booted_audio
    from test_operation_registry import prepared
    async with booted_audio(tmp_path, monkeypatch) as (client, _fakes):
        item = await prepared(client, monkeypatch)
        await wiring.current.scheduler.bench(COMFY_SCOPE, kind="unreachable", cause="ComfyUI is not answering")
        ops = {o["id"]: o for o in (await client.get("/api/crexil/operations")).json()}
    presets = [o for i, o in ops.items()
               if o.get("execution_location") == "Configured ComfyUI runtime" and not i.startswith("custom-")]
    assert presets and all(o["availability"] == "unavailable" and o["reasons"] for o in presets)
    custom = ops["custom-" + item["id"]]
    assert custom["availability"] == "unavailable"
    assert "The local runtime is paused or unavailable." in custom["reasons"]
    voices = [o for i, o in ops.items() if i.startswith("audio:") and i.endswith(":qwen3-tts")]
    assert voices and all(o["availability"] != "unavailable" for o in voices)
    meshes = [o for i, o in ops.items() if i.startswith("mesh:")]
    assert meshes and all(o["availability"] != "unavailable" for o in meshes)


# --- our own idle runner is not "another program" ---

async def test_an_idle_voice_runner_never_reads_as_a_held_card(tmp_path):
    """The voice runner keeps its CUDA context between jobs (0.6 to 1.1 GB
    measured). With the desktop's slice beside it, that stays under the
    default held-card threshold, so an image take's out-of-memory failure
    stays the frame's own (A1) and nothing benches the local lane."""
    from app.executors import GpuReading
    from test_hardware_cases import GB, framed_batch, oom, probe_of
    idle_runner, desktop = int(1.1 * GB), int(0.9 * GB)
    reading = GpuReading(total=24 * GB, free=24 * GB - 4 * GB - idle_runner - desktop,
                         comfy=4 * GB, foreign=idle_runner + desktop, name="test card")
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        comfy, voice, mesh = local_trio()
        async with running(db, tmp_path, [comfy, voice, mesh], ROUTES,
                           gpu_probe=probe_of({"reading": reading})) as s:
            turn, reqs = await framed_batch(db, ws, [("klein", "local")], frame=(2048, 2048))
            comfy.script = {turn["slots"][0]["id"]: Step(outcome=oom())}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert s.snapshot()["benches"] == {}
    assert done["slots"][0]["status"] == "failed" and done["slots"][0]["kind"] == "oom"


class Rejected:
    key_present = True

    async def probe(self):
        return {"ok": False, "error": "key rejected (401)"}


@pytest.mark.parametrize("openrouter", [NoKey(), Rejected()], ids=["no key", "a rejected key"])
async def test_every_startup_bench_names_its_address_and_move_never_a_unit_or_a_route(monkeypatch, openrouter):
    # On a shared machine a service unit's name can start another installation's runtime.
    async def down(*_args, **_kw):
        return False
    monkeypatch.setattr(wiring, "probe_mesh_runner", down)
    monkeypatch.setattr(wiring.audio_executors, "adapter", lambda provider: None)
    sched = BootScheduler()
    await wiring.startup_checks(sched, Comfy(up=False), openrouter)
    causes = {scope: cause for scope, _, cause in sched.benched}
    assert all("systemctl" not in cause and "/api/" not in cause for cause in causes.values()), causes
    assert causes["engine:trellis2-4b"] == (
        f"Startup check: the 3D engine runner is not answering at {config.THREED_URL}. "
        "Start this checkout's runner there (scripts/3d-runner.sh), then resume it.")
    assert "Connections" in causes["lane:external"]
