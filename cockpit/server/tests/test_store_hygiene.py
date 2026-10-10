"""The scheduler writes through the store, collection writes files before its
receipt transaction, file hashes come from the cache off the event loop, and a
failure names the provider that failed and benches only that provider."""

import asyncio
import hashlib
import re
import threading
from contextlib import suppress
from pathlib import Path

import pytest

from app import db as db_module
from app import execution_outputs, scheduler as scheduler_module
from app.crexil_sequences import material_fact
from app.executors import CollectedOutput, DispatchRequest
from app.scheduler import bench_scope_for, lane_covers, plain_cause
from fakes import FakeExecutor, Step, fail
from test_execution_controls import control
from test_scheduler import FAST_RETRY, events_of, make_batch, opened, running, settle

pytestmark = pytest.mark.anyio


# --- every scheduler write goes through a store method under the lock ---

def test_the_scheduler_writes_only_through_store_methods():
    source = Path(scheduler_module.__file__).read_text(encoding="utf-8")
    assert "db.conn" not in source
    assert ".conn.execute" not in source


async def one_slot(db, engine="klein", name="A"):
    ws = await db.create_workspace(name)
    turn = await db.create_turn_with_slots(ws["id"], "an anvil", {}, [
        {"engine": engine, "submitted_graph": {"graph_for": engine}}])
    return ws, turn["slots"][0]["id"]


async def hold_open_and_roll_back(db, ws, inside: asyncio.Event, release: asyncio.Event):
    """Another task's transaction that is open while we write, then fails."""
    with suppress(RuntimeError):
        async with db._tx():
            await db.conn.execute("UPDATE workspaces SET name='renamed' WHERE id=?", (ws["id"],))
            inside.set()
            await release.wait()
            raise RuntimeError("the other task's work fails")


async def test_a_bare_write_joins_another_tasks_transaction_and_dies_with_it(tmp_path):
    """The hazard the store lock exists for: why the scheduler must not write bare."""
    async with opened(tmp_path) as db:
        ws, sid = await one_slot(db)
        inside, release = asyncio.Event(), asyncio.Event()
        other = asyncio.create_task(hold_open_and_roll_back(db, ws, inside, release))
        await inside.wait()
        await db.conn.execute("UPDATE slots SET cause=? WHERE id=?", ("waiting", sid))
        release.set()
        await other
        assert (await db.get_slot(sid))["cause"] is None


async def test_a_held_cause_survives_another_tasks_rollback(tmp_path):
    async with opened(tmp_path) as db:
        ws, sid = await one_slot(db)
        inside, release = asyncio.Event(), asyncio.Event()
        other = asyncio.create_task(hold_open_and_roll_back(db, ws, inside, release))
        await inside.wait()
        held = asyncio.create_task(db.transition_slot(
            sid, "held", payload={"pause": True}, cause="The project is paused."))
        await asyncio.sleep(0.05)
        assert not held.done()                     # it waits for the lock
        release.set()
        await other
        await held
        slot = await db.get_slot(sid)
        assert (slot["status"], slot["cause"]) == ("held", "The project is paused.")
        assert (await db.get_workspace(ws["id"]))["name"] == "A"


async def test_a_transition_keeps_sets_or_clears_the_cause_with_the_status(tmp_path):
    async with opened(tmp_path) as db:
        _, sid = await one_slot(db)
        await db.transition_slot(sid, "held", payload={"limit": True}, cause="Over the run's limit.")
        assert (await db.get_slot(sid))["cause"] == "Over the run's limit."
        await db.transition_slot(sid, "queued", payload={"reason": "limits now permit this request"},
                                 cause=None)
        assert (await db.get_slot(sid))["cause"] is None
        await db.transition_slot(sid, "held", payload={"limit": True}, cause="Over the run's limit.")
        await db.transition_slot(sid, "skipped")   # left out: the row keeps its cause
        assert (await db.get_slot(sid))["cause"] == "Over the run's limit."


async def test_a_late_job_ref_stamps_only_that_late_attempt(tmp_path):
    async with opened(tmp_path) as db:
        _, sid = await one_slot(db)
        await db.dispatch_slot(sid)
        await db.transition_slot(sid, "late")
        await db.set_late_job_ref(sid, 0, "wrong-attempt")
        assert (await db.get_slot(sid))["job_ref"] is None
        await db.set_late_job_ref(sid, 1, "runtime-7")
        assert (await db.get_slot(sid))["job_ref"] == "runtime-7"
        _, running_sid = await one_slot(db, name="B")
        await db.dispatch_slot(running_sid)
        await db.set_late_job_ref(running_sid, 1, "not-late")
        assert (await db.get_slot(running_sid))["job_ref"] is None


async def test_a_paused_hold_carries_its_cause_and_resuming_clears_it(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as sched:
            await control(db, "project", ws["id"], "pause", reason="review the takes")
            turn, reqs = await make_batch(db, ws, [("klein", "local")])
            await sched.submit_turn(turn["id"], reqs)
            held = await settle(db, turn["id"])
            slot = held["slots"][0]
            assert slot["status"] == "held" and slot["cause"]
            await control(db, "project", ws["id"], "resume")
            await sched.resume_budget_holds(ws["id"])
            done = await settle(db, turn["id"], until={"landed"})
        assert done["slots"][0]["cause"] is None


# --- collection: bytes on disk before the receipt transaction ---

async def running_slot(db, tmp_path):
    ws, sid = await one_slot(db)
    slot = await db.dispatch_slot(sid)
    req = DispatchRequest(slot_id=sid, engine="klein", submitted_graph={"graph_for": "klein"},
                          prompt="an anvil")
    return ws, slot, req, tmp_path / "staging"


async def test_collection_writes_bytes_before_its_transaction_and_off_the_loop(tmp_path, monkeypatch):
    seen = []
    real_write, real_hash = execution_outputs.atomic_bytes, execution_outputs.sha256_bytes
    async with opened(tmp_path) as db:
        def write(path, data):
            seen.append(("write", db._tx_lock.locked(), threading.current_thread() is threading.main_thread()))
            real_write(path, data)

        def digest(data):
            seen.append(("hash", db._tx_lock.locked(), threading.current_thread() is threading.main_thread()))
            return real_hash(data)

        monkeypatch.setattr(execution_outputs, "atomic_bytes", write)
        monkeypatch.setattr(execution_outputs, "sha256_bytes", digest)
        ws, slot, req, staging = await running_slot(db, tmp_path)
        row = await execution_outputs.collect(db, staging, slot, req, CollectedOutput(
            identity="primary", filename="take.png", data=b"anvil bytes", role="primary"))
        assert {kind for kind, *_ in seen} == {"write", "hash"}
        assert all(not locked and not on_loop for _, locked, on_loop in seen), seen
        assert Path(row["output_path"]).read_bytes() == b"anvil bytes"
        assert row["sha256"] == hashlib.sha256(b"anvil bytes").hexdigest()
        assert len(await db.list_nodes(ws["id"])) == 1


async def test_a_failed_write_leaves_no_receipt_and_no_node(tmp_path, monkeypatch):
    def full_disk(path, data):
        raise OSError(28, "No space left on device")

    async with opened(tmp_path) as db:
        monkeypatch.setattr(execution_outputs, "atomic_bytes", full_disk)
        ws, slot, req, staging = await running_slot(db, tmp_path)
        with pytest.raises(OSError):
            await execution_outputs.collect(db, staging, slot, req, CollectedOutput(
                identity="primary", filename="take.png", data=b"anvil bytes", role="primary"))
        assert await db._all("SELECT * FROM execution_groups") == []
        assert await db._all("SELECT * FROM execution_outputs") == []
        assert await db.list_nodes(ws["id"]) == []


async def test_a_receipt_changed_after_placement_is_still_written_off_the_loop(tmp_path, monkeypatch):
    """When placement could not put the bytes in place (a concurrent collector
    moved the receipt), the transaction writes them, still on a worker thread."""
    seen = []
    real_write = execution_outputs.atomic_bytes

    async def nothing_placed(db, staging, slot, output, digest, facts, path):
        return output, digest, facts, set()

    async with opened(tmp_path) as db:
        def write(path, data):
            seen.append(threading.current_thread() is threading.main_thread())
            real_write(path, data)

        monkeypatch.setattr(execution_outputs, "_place", nothing_placed)
        monkeypatch.setattr(execution_outputs, "atomic_bytes", write)
        ws, slot, req, staging = await running_slot(db, tmp_path)
        row = await execution_outputs.collect(db, staging, slot, req, CollectedOutput(
            identity="primary", filename="take.png", data=b"anvil bytes", role="primary"))
        assert seen == [False]
        assert Path(row["output_path"]).read_bytes() == b"anvil bytes"


# --- file hashes: the store's cache, computed off the event loop ---

async def test_material_hashes_come_from_the_cache_off_the_loop(tmp_path, monkeypatch):
    computed = []
    real = db_module.sha256_file

    def counting(path):
        computed.append(threading.current_thread() is threading.main_thread())
        return real(path)

    monkeypatch.setattr(db_module, "sha256_file", counting)
    async with opened(tmp_path) as db:
        ws, slot, req, staging = await running_slot(db, tmp_path)
        await execution_outputs.collect(db, staging, slot, req, CollectedOutput(
            identity="primary", filename="take.png", data=b"anvil bytes", role="primary"))
        node = (await db.list_nodes(ws["id"]))[0]
        first = await material_fact(db, node["id"])
        second = await material_fact(db, node["id"])
        assert first == second
        assert first["sha256"] == hashlib.sha256(b"anvil bytes").hexdigest()
        assert computed == [False]                 # hashed once, on a worker thread
        Path(node["output_path"]).write_bytes(b"different bytes")
        changed = await material_fact(db, node["id"])
        assert changed["sha256"] == hashlib.sha256(b"different bytes").hexdigest()
        assert computed == [False, False]


async def test_a_missing_file_has_no_hash(tmp_path):
    async with opened(tmp_path) as db:
        assert await db.file_sha256(tmp_path / "gone.wav") is None


# --- a failure names the provider that failed (law 3) ---

STATUS_CODE = re.compile(r"\b[1-5]\d\d\b")


@pytest.mark.parametrize("kind,lane,engine,provider,verb,expected", [
    # OpenRouter and ComfyUI keep their words
    ("dead_key", "external", "openai/gpt-image-2", "openrouter", "run",
     "OpenRouter turned the key away — nothing external runs until the key is fixed"),
    ("unreachable", "external", "openai/gpt-image-2", "openrouter", "run",
     "OpenRouter can't be reached — the network, most likely"),
    ("unreachable", "local", "klein", "comfy", "run", "ComfyUI can't be reached — is it running?"),
    ("error", "external", "openai/gpt-image-2", "openrouter", "run",
     "openai/gpt-image-2 answered without an image"),
    # audio providers name themselves and their act
    ("dead_key", "external", "elevenlabs-v4", "elevenlabs", "speak",
     "ElevenLabs turned the key away: nothing runs on ElevenLabs until the key is fixed"),
    ("unreachable", "external", "elevenlabs-sfx", "elevenlabs", "sfx",
     "ElevenLabs can't be reached: the network, most likely"),
    ("unreachable", "local", "qwen3-tts", "voice-runner", "speak",
     "The local voice runner can't be reached. Is it running? tools/tts/README.md says how to start it."),
    # an audio engine by its display name, never its id
    ("oom", "local", "qwen3-tts", "voice-runner", "voice-design",
     "Qwen3-TTS 1.7B ran out of video memory: free the card (close what else is holding it) and try again"),
    ("error", "external", "elevenlabs-v4", "elevenlabs", "speak",
     "Eleven v4 failed: the engine, not your text"),
    ("error", "external", "elevenlabs-voice-design", "elevenlabs", "voice-design",
     "ElevenLabs Voice Design failed: the engine, not your description"),
    ("error", "external", "elevenlabs-sfx", "elevenlabs", "sfx",
     "ElevenLabs Sound Effects failed: the engine, not your description"),
    ("error", "local", "qwen3-tts", "voice-runner", "speak",
     "Qwen3-TTS 1.7B failed: the engine, not your text"),
    ("blank", "local", "qwen3-tts", "voice-runner", "speak",
     "Qwen3-TTS 1.7B finished, but no audio came back: the engine, not your text"),
    ("charged_blank", "external", "elevenlabs-sfx", "elevenlabs", "sfx",
     "ElevenLabs Sound Effects took the job and billed it, but no audio came back"),
    # a lost answer says what its charge and its next move are (laws 3, 4 and 5)
    ("timeout", "external", "elevenlabs-v4", "elevenlabs", "speak",
     "The answer from Eleven v4 was lost before it arrived. Its charge is unverified; collecting looks for the take "
     "without generating it again"),
    ("timeout", "external", "elevenlabs-voice-design", "elevenlabs", "voice-design",
     "The answer from ElevenLabs Voice Design was lost before it arrived. Its charge is unverified, and this take cannot "
     "be collected"),
    ("timeout", "external", "elevenlabs-sfx", "elevenlabs", "sfx",
     "The answer from ElevenLabs Sound Effects was lost before it arrived. Its charge is unverified, and this take cannot "
     "be collected"),
    ("timeout", "local", "qwen3-tts", "voice-runner", "speak", "Qwen3-TTS 1.7B gave no result in time"),
    ("uncertain", "external", "elevenlabs-voice-design", "elevenlabs", "voice-design",
     "ElevenLabs Voice Design may have made this take, but its outcome could not be confirmed, so it was not sent "
     "again"),
    ("rate_limit", "external", "elevenlabs-sfx", "elevenlabs", "sfx",
     "ElevenLabs Sound Effects is holding us at the door — still rate limited after 3 tries"),
    ("uncertain", "local", "klein", "comfy", "run",
     "klein may have made this take, but its outcome could not be confirmed, so it was not sent again"),
])
def test_a_failure_names_the_provider_that_failed(kind, lane, engine, provider, verb, expected):
    line = plain_cause(kind, lane=lane, engine=engine, frame=(1024, 1024), tries=3,
                       provider=provider, verb=verb)
    assert line == expected
    assert not STATUS_CODE.search(line)            # law 3


def test_audio_words_never_mention_frames_images_another_provider_or_an_engine_id():
    for kind in ("dead_key", "unreachable", "oom", "error", "blank", "charged_blank", "timeout", "uncertain",
                 "moderation", "rate_limit", "model_down", "interrupted", "restart"):
        for provider, lane, engine in (("elevenlabs", "external", "elevenlabs-v4"),
                                       ("voice-runner", "local", "qwen3-tts")):
            line = plain_cause(kind, lane=lane, engine=engine, frame=(512, 512),
                               provider=provider, verb="speak")
            assert not re.search(r"frame|image|ComfyUI|OpenRouter|elevenlabs-|qwen3-tts", line), (kind, provider, line)


# --- a provider's fault benches that provider only (law 1) ---

def test_audio_providers_bench_their_own_scope():
    assert bench_scope_for("dead_key", "external", "openai/gpt-image-2", "openrouter") == "lane:external"
    assert bench_scope_for("dead_key", "external", "elevenlabs-v4", "elevenlabs") == "provider:elevenlabs"
    assert bench_scope_for("unreachable", "external", "elevenlabs-sfx", "elevenlabs") == "provider:elevenlabs"
    assert bench_scope_for("unreachable", "local", "qwen3-tts", "voice-runner") == "provider:voice-runner"
    assert bench_scope_for("unreachable", "local", "klein", "comfy") == "provider:comfy"
    assert bench_scope_for("unreachable", "local", "trellis2-4b", "mesh") == "engine:trellis2-4b"
    assert bench_scope_for("model_down", "external", "elevenlabs-v4", "elevenlabs") == "engine:elevenlabs-v4"


def test_the_external_lane_bench_never_covers_an_audio_provider():
    assert lane_covers(FakeExecutor("or", "external"), "external")
    assert not lane_covers(FakeExecutor("elevenlabs", "external"), "external")
    assert lane_covers(FakeExecutor("voice-runner", "local"), "local")   # one GPU (law 14)


async def test_a_down_voice_runner_never_stops_the_image_takes_behind_it(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        runner = FakeExecutor("voice-runner", "local",
                              default=Step(outcome=fail("unreachable")))
        comfy = FakeExecutor("comfy", "local")
        routes = {"qwen3-tts": "voice-runner", "klein": "comfy"}
        async with running(db, tmp_path, [runner, comfy], routes, retry_policy=FAST_RETRY) as sched:
            turn, reqs = await make_batch(db, ws, [("qwen3-tts", "local"), ("qwen3-tts", "local"),
                                                   ("klein", "local"), ("klein", "local")])
            await sched.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        by_engine = {}
        for slot in done["slots"]:
            by_engine.setdefault(slot["engine"], []).append(slot["status"])
        assert sorted(by_engine["qwen3-tts"]) == ["failed", "held"]
        assert by_engine["klein"] == ["landed", "landed"]
        benched = await events_of(db, "engine.benched")
        assert [event["payload"]["scope"] for event in benched] == ["provider:voice-runner"]
        failed = next(s for s in done["slots"] if s["status"] == "failed")
        assert "The local voice runner can't be reached" in failed["cause"]


async def test_an_elevenlabs_key_fault_never_holds_openrouter_takes_and_the_reverse(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        eleven = FakeExecutor("elevenlabs", "external", default=Step(outcome=fail("dead_key")))
        router = FakeExecutor("or", "external")
        routes = {"elevenlabs-v4": "elevenlabs", "elevenlabs-sfx": "elevenlabs", "gpt": "or"}
        async with running(db, tmp_path, [eleven, router], routes, external_concurrency=1) as sched:
            turn, reqs = await make_batch(db, ws, [("elevenlabs-v4", "external"),
                                                   ("elevenlabs-sfx", "external"),
                                                   ("gpt", "external"), ("gpt", "external")])
            await sched.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        status = {}
        for slot in done["slots"]:
            status.setdefault(slot["engine"], []).append(slot["status"])
        assert status["gpt"] == ["landed", "landed"]
        assert sorted(status["elevenlabs-v4"] + status["elevenlabs-sfx"]) == ["failed", "held"]
        benched = await events_of(db, "engine.benched")
        assert [event["payload"]["scope"] for event in benched] == ["provider:elevenlabs"]

    async with opened(tmp_path, name="reverse.sqlite") as db:
        ws = await db.create_workspace("B")
        eleven = FakeExecutor("elevenlabs", "external")
        router = FakeExecutor("or", "external", default=Step(outcome=fail("dead_key")))
        async with running(db, tmp_path, [eleven, router], routes, external_concurrency=1) as sched:
            turn, reqs = await make_batch(db, ws, [("gpt", "external"), ("gpt", "external"),
                                                   ("elevenlabs-v4", "external")])
            await sched.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        status = {slot["engine"]: [] for slot in done["slots"]}
        for slot in done["slots"]:
            status[slot["engine"]].append(slot["status"])
        assert status["elevenlabs-v4"] == ["landed"]
        assert sorted(status["gpt"]) == ["failed", "held"]
        assert [e["payload"]["scope"] for e in await events_of(db, "engine.benched")] == ["lane:external"]
