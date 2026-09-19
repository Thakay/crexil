"""The high-probability cases on a one-GPU workstation, the server's half under
fault injection:

  A1   out of video memory        — fails red AT ITS FRAME, no automatic
                                    retry, the record's smaller frame in
                                    the facts; RETRY AT that size rebuilds
                                    the cell and the size becomes the cell's
  A11  nondeterministic OOM       — the record says this engine held that
                                    very frame before: the size is absolved
                                    on the face, retry-as-is leads
  A9   repeat failure, one batch  — the second engine-level failure of one
                                    engine in one turn benches it FOR THAT
                                    TURN (its other turns run), holds the
                                    rest, one cause record; TRY ONCE MORE
                                    is that scope's unbench
  B2   rate limited               — retried on the provider's clock (its
                                    Retry-After, capped) up to four times,
                                    each an event, the wait visible; the
                                    external permit released meanwhile;
                                    give-up skips free; exhaustion words it
  C2   GPU held by another program — an OOM under a foreign hold is not the
                                    frame's fault: the take re-queues
                                    visibly, the local lane benches (locals
                                    only — externals land), and the lane
                                    picks itself up when the card frees;
                                    CHECK AGAIN and HOLD THE QUEUE
  E6   free VRAM + disk at Run    — the headroom sheet states the card and
                                    the drive; a run the drive can't hold
                                    is refused in words before a turn exists

The faces — the OOM buttons, the countdown, the C2 line, the run bar's
disarm — live in cockpit/web (grid.test.ts, compose.test.ts) and
e2e/hardware-cases.spec.ts drives them against the real stack over the
scripted-executor harness.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from app import api_v4, config, main, wiring
from app.executors import (EngineFailure, GpuReading,
                           classify_execution_error, gpu_reading_from_stats)
from app.openrouter import retry_after_from
from app.scheduler import (A9_KINDS, RetryRule, bench_scope_for,
                           gpu_held_cause, plain_cause, smaller_frame,
                           turn_scope)
from fakes import FakeExecutor, Step
from test_api_v4 import (MODEL, PRESET, WORKFLOW, hermetic_env, make_fakes,
                         make_ws, run, settle_turn, store)
from test_scheduler import (events_of, make_batch, opened, running, settle)

pytestmark = pytest.mark.anyio

GB = 1 << 30


def oom(msg="CUDA out of memory. Tried to allocate 2.00 GiB"):
    return EngineFailure("oom", msg, detail=json.dumps(
        {"exception_type": "torch.OutOfMemoryError",
         "exception_message": msg}))


def probe_of(box: dict):
    """A GPU probe reading whatever the test puts in `box` — None means
    ComfyUI couldn't say."""
    async def probe() -> GpuReading | None:
        return box.get("reading")
    return probe


def held(foreign_gb: float, total_gb: float = 24) -> GpuReading:
    foreign = int(foreign_gb * GB)
    return GpuReading(total=int(total_gb * GB),
                      free=int(total_gb * GB) - foreign - 2 * GB,
                      comfy=2 * GB, foreign=foreign, name="test card")


async def framed_batch(db, ws, engines, *, frame, prompt="a rusty sword"):
    """make_batch, every local request carrying the requested frame."""
    turn, reqs = await make_batch(db, ws, engines, prompt=prompt)
    reqs = [r if r.submitted_graph is None else
            type(r)(**{**r.__dict__, "frame": frame}) for r in reqs]
    return turn, reqs


# --- A1 / A11: out of video memory ------------------------------------------


def test_the_oom_classifier_reads_comfyuis_error_shape():
    hint = {"exception_type": "torch.OutOfMemoryError",
            "exception_message": "Allocation on device "}
    assert classify_execution_error(None, hint) == "oom"
    entry = {"status": {"status_str": "error", "messages": [
        ["execution_start", {}],
        ["execution_error", {"exception_type": "RuntimeError",
                             "exception_message": "CUDA out of memory. "
                                                  "Tried to allocate 1 GiB"}],
    ]}}
    assert classify_execution_error(entry) == "oom"
    assert classify_execution_error({"status": {"messages": [
        ["execution_error", {"exception_type": "KeyError",
                             "exception_message": "'clip'"}]]}}) == "error"
    assert classify_execution_error(None, None) == "error"


def test_the_smaller_frame_comes_from_the_record_else_the_grid():
    # no history: ¾ per side, snapped down to the 64-grid
    assert smaller_frame((1536, 1536), []) == (1152, 1152)
    assert smaller_frame((2048, 1152), []) == (1536, 832)
    # the record's largest smaller frame wins over the arithmetic
    assert smaller_frame((1536, 1536), [(1024, 1024), (768, 768),
                                        (2048, 2048)]) == (1024, 1024)
    # nothing below the floor is offered
    assert smaller_frame((512, 512), []) is None
    assert smaller_frame((576, 512), [(512, 512)]) == (512, 512)


def test_an_oom_is_the_slots_alone_in_the_bench_table():
    assert bench_scope_for("oom", "local", "x") is None


def test_the_oom_faces_name_the_size_and_a11_absolves_it():
    a1 = plain_cause("oom", lane="local", engine="raw-zimage",
                     frame=(1536, 1536))
    assert a1 == ("raw-zimage ran out of video memory at 1536 × 1536 — a "
                  "smaller frame is the sure fix")
    a11 = plain_cause("oom", lane="local", engine="raw-zimage",
                      frame=(1536, 1536),
                      held_before={"turn_idx": 12, "pos": 0, "n": 1})
    assert "though it held that size on turn 12" in a11
    assert "the size isn't the problem" in a11
    assert "at this frame size" in plain_cause("oom", lane="local",
                                               engine="e")


async def test_a1_fails_red_at_its_frame_with_the_records_offer_and_no_retry(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local", default=Step(outcome=oom()))
        async with running(db, tmp_path, [loc], {"raw": "comfy"}) as s:
            turn, reqs = await framed_batch(
                db, ws, [("raw", "local"), ("raw", "local")],
                frame=(1536, 1536))
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            for _ in range(200):             # the bench lands a beat after
                benches = s.snapshot()["benches"]
                if benches:
                    break
                await asyncio.sleep(0.01)
        first = done["slots"][0]
        assert first["status"] == "failed" and first["kind"] == "oom"
        assert first["frame"] == "1536x1536"
        assert first["facts"] == {"frame": "1536x1536", "held_before": None,
                                  "smaller": "1152x1152"}
        assert first["cause"] == plain_cause("oom", lane="local",
                                             engine="raw", frame=(1536, 1536))
        assert first["attempt"] == 1                    # artist chooses
        assert await events_of(db, "slot.retrying") == []
        failed = await events_of(db, "slot.failed")
        assert failed[0]["payload"]["frame"] == "1536x1536"
        assert failed[0]["payload"]["facts"]["smaller"] == "1152x1152"
        # the second OOM of the same engine in the turn is A9's (below):
        # benched for the turn — the bench is turn-scoped, nothing wider
        assert list(benches) == [turn_scope("raw", turn["id"])]


async def test_a11_the_record_absolves_a_size_this_engine_held_before(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"raw": "comfy"}) as s:
            t1, r1 = await framed_batch(db, ws, [("raw", "local")],
                                        frame=(1536, 1536))
            await s.submit_turn(t1["id"], r1)
            await settle(db, t1["id"], until={"landed"})
            loc.default = Step(outcome=oom())
            t2, r2 = await framed_batch(db, ws, [("raw", "local")],
                                        frame=(1536, 1536))
            await s.submit_turn(t2["id"], r2)
            done = await settle(db, t2["id"])
        slot = done["slots"][0]
        assert slot["kind"] == "oom"
        assert slot["facts"]["held_before"] == {"turn_idx": t1["idx"],
                                                "pos": 0, "n": 1}
        assert slot["facts"]["smaller"] == "1152x1152"   # still offered
        assert f"though it held that size on turn {t1['idx']}" in slot["cause"]
        assert "the size isn't the problem" in slot["cause"]
        assert (await db.frames_landed("raw")) == [
            {"frame": "1536x1536", "n": 1, "turn_idx": t1["idx"], "pos": 0}]


# --- A9: repeat failure of the same engine inside one batch ----------------


async def test_a9_the_second_failure_benches_the_engine_for_this_turn_only(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"raw": "comfy",
                                                 "other": "comfy"}) as s:
            t1, r1 = await make_batch(
                db, ws, [("raw", "local")] * 4 + [("other", "local")])
            t2, r2 = await make_batch(db, ws, [("raw", "local")])
            for sid in (sl["id"] for sl in t1["slots"][:4]):
                loc.script[sid] = Step(outcome=EngineFailure("error", "boom"))
            await s.submit_turn(t1["id"], r1)
            await s.submit_turn(t2["id"], r2)
            done1 = await settle(db, t1["id"])
            done2 = await settle(db, t2["id"], until={"landed"})
            scope = turn_scope("raw", t1["id"])
            benches = s.snapshot()["benches"]
            assert list(benches) == [scope]
            assert benches[scope]["kind"] == "repeat"
            assert benches[scope]["cause"] == (
                "raw failed on slot 1, and again on slot 2 — benched for "
                "this turn; its remaining takes here are held, not dropped")
            by = {sl["pos"]: sl["status"] for sl in done1["slots"]}
            assert by == {0: "failed", 1: "failed", 2: "held", 3: "held",
                          4: "landed"}                    # the other engine ran
            assert done2["slots"][0]["status"] == "landed"   # other turn ran
            benched = await events_of(db, "engine.benched")
            assert len(benched) == 1
            assert sorted(benched[0]["payload"]["held"]) == sorted(
                sl["id"] for sl in t1["slots"][2:4])
            # TRY ONCE MORE: the held takes resume — and land this time
            for sid in (sl["id"] for sl in t1["slots"][2:4]):
                loc.script[sid] = Step()
            await s.unbench(scope)
            again = await settle(db, t1["id"],
                                 until={"landed", "failed"})
        by = {sl["pos"]: sl["status"] for sl in again["slots"]}
        assert by[2] == "landed" and by[3] == "landed"
        assert [sl["pos"] for sl in again["slots"]] == [0, 1, 2, 3, 4]


async def test_a9_counts_engine_level_kinds_only_and_never_across_turns(
        tmp_path):
    assert set(A9_KINDS) == {"error", "oom", "charged_blank"}
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(
            outcome=EngineFailure("moderation", "refused")))
        loc = FakeExecutor("comfy", "local", default=Step(
            outcome=EngineFailure("error", "boom")))
        routes = {"v/m": "or", "raw": "comfy"}
        async with running(db, tmp_path, [loc, ext], routes) as s:
            # three content refusals: no bench (the prompt's, not the engine's)
            t1, r1 = await make_batch(db, ws, [("v/m", "external")] * 3)
            await s.submit_turn(t1["id"], r1)
            done = await settle(db, t1["id"])
            assert all(sl["status"] == "failed" for sl in done["slots"])
            assert s.snapshot()["benches"] == {}
            # one engine failure per turn, twice: never a bench
            for _ in range(2):
                t, r = await make_batch(db, ws, [("raw", "local")])
                await s.submit_turn(t["id"], r)
                await settle(db, t["id"])
            assert s.snapshot()["benches"] == {}


# --- B2: rate limited ------------------------------------------------------


def test_retry_after_reads_seconds_dates_and_reset_epochs():
    now = 1_700_000_000.0
    assert retry_after_from({"Retry-After": "12"}, now) == 12.0
    assert retry_after_from({"Retry-After": "0"}, now) == 0.0
    from email.utils import formatdate
    at = formatdate(now + 30, usegmt=True)
    assert abs(retry_after_from({"Retry-After": at}, now) - 30) < 1.0
    assert retry_after_from({"X-RateLimit-Reset": str(int((now + 45) * 1000))},
                            now) == pytest.approx(45.0, abs=0.01)
    assert retry_after_from({"X-RateLimit-Reset": str(now + 7)}, now) \
        == pytest.approx(7.0, abs=0.01)
    assert retry_after_from({"X-RateLimit-Reset": "20"}, now) == 20.0
    assert retry_after_from({}, now) is None
    assert retry_after_from({"Retry-After": "soon"}, now) is None


def test_the_exhausted_face_counts_the_tries():
    assert plain_cause("rate_limit", lane="external", engine="v/m",
                       tries=5).endswith("still rate limited after 5 tries")
    assert plain_cause("rate_limit", lane="external", engine="v/m",
                       tries=2).endswith("after a second try")


async def test_b2_retries_on_the_providers_clock_visibly_then_lands(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("B")
        ext = FakeExecutor("or", "external")
        policy = {"rate_limit": RetryRule(4, 0.02)}
        async with running(db, tmp_path, [ext], {"v/m": "or"},
                           retry_policy=policy) as s:
            turn, reqs = await make_batch(db, ws, [("v/m", "external")])
            sid = turn["slots"][0]["id"]
            limited = EngineFailure("rate_limit", "slow down (HTTP 429)",
                                    retry_after_s=0.15)
            ext.script = {sid: [Step(outcome=limited)] * 4 + [Step()]}
            await s.submit_turn(turn["id"], reqs)
            seen = None
            for _ in range(300):
                waits = s.snapshot()["retries"]
                if sid in waits:
                    seen = waits[sid]
                    break
                await asyncio.sleep(0.005)
            assert seen is not None
            assert seen["kind"] == "rate_limit" and seen["of"] == 5
            assert seen["attempt"] == 2 and seen["window"] == "provider"
            assert seen["wait_s"] == pytest.approx(0.15)
            assert seen["next_try_at"]
            done = await settle(db, turn["id"], until={"landed"})
            assert s.snapshot()["retries"] == {}
        assert done["slots"][0]["attempt"] == 5
        retrying = await events_of(db, "slot.retrying")
        assert [e["payload"]["next_attempt"] for e in retrying] == [2, 3, 4, 5]
        assert all(e["payload"]["window"] == "provider"
                   and e["payload"]["of"] == 5
                   and e["payload"]["backoff_s"] == pytest.approx(0.15)
                   for e in retrying)


async def test_b2_the_wait_releases_the_permit_and_gives_up_free(tmp_path):
    """With one external permit, a slot waiting on the provider's clock
    must not hold the lane: another model's take lands meanwhile. And
    cancel during the wait is the artist's give-up — skipped, nothing
    spent, no further dispatch."""
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("B")
        ext = FakeExecutor("or", "external")
        policy = {"rate_limit": RetryRule(4, 5.0)}   # long cockpit default
        async with running(db, tmp_path, [ext], {"v/m": "or", "v/n": "or"},
                           retry_policy=policy, external_concurrency=1) as s:
            turn, reqs = await make_batch(
                db, ws, [("v/m", "external"), ("v/n", "external")])
            slow, other = (sl["id"] for sl in turn["slots"])
            ext.script = {slow: Step(outcome=EngineFailure(
                "rate_limit", "429", retry_after_s=5.0))}
            await s.submit_turn(turn["id"], reqs)
            for _ in range(300):
                if (await db.get_slot(other))["status"] == "landed":
                    break
                await asyncio.sleep(0.01)
            assert (await db.get_slot(other))["status"] == "landed"
            assert (await db.get_slot(slow))["status"] == "queued"
            assert slow in s.snapshot()["retries"]
            r = await s.cancel_slot(slow)
            assert r["action"] == "skipped"
            assert s.snapshot()["retries"] == {}
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "skipped"
        assert ext.starts.count(slow) == 1                 # never re-sent


async def test_b2_exhaustion_fails_in_words_and_the_window_is_capped(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_WAIT_S", 0.02)
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("B")
        ext = FakeExecutor("or", "external", default=Step(
            outcome=EngineFailure("rate_limit", "429", retry_after_s=3600)))
        policy = {"rate_limit": RetryRule(4, 5.0)}
        async with running(db, tmp_path, [ext], {"v/m": "or"},
                           retry_policy=policy) as s:
            turn, reqs = await make_batch(db, ws, [("v/m", "external")],
                                          committed=0.04)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert s.snapshot()["benches"] == {}            # never benches
        slot = done["slots"][0]
        assert slot["status"] == "failed" and slot["attempt"] == 5
        assert slot["kind"] == "rate_limit"
        assert slot["cause"].endswith("still rate limited after 5 tries")
        assert slot["charged_usd"] is None                  # nothing billed
        retrying = await events_of(db, "slot.retrying")
        assert len(retrying) == 4
        assert all(e["payload"]["backoff_s"] == pytest.approx(0.02)
                   for e in retrying)                       # capped


# --- C2: GPU held by another program ---------------------------------------


def test_the_reading_is_the_devices_own_free_not_torchs():
    r = gpu_reading_from_stats({"devices": [{
        "name": "cuda:0 NVIDIA GeForce RTX 4090", "vram_total": 24 * GB,
        "vram_free": 10 * GB, "torch_vram_total": 8 * GB,
        "torch_vram_free": 1 * GB}]})
    assert r.free == 9 * GB                    # torch's idle pool isn't free
    assert r.comfy == 8 * GB
    assert r.foreign == 24 * GB - 9 * GB - 8 * GB
    assert "4090" in r.name
    assert gpu_reading_from_stats({"devices": []}) is None
    assert gpu_reading_from_stats({"devices": [{"vram_total": "x"}]}) is None


def test_the_c2_face_names_a_size_never_a_program():
    line = gpu_held_cause(held(5.8))
    assert line.startswith("something else is holding 5.8 GB of the 24 GB "
                           "card — the local engines can't get a turn")
    assert "external takes carry on" in line
    assert "picks itself up" in line


async def test_c2_an_oom_under_a_foreign_hold_pauses_locals_only_and_resumes(
        tmp_path):
    box = {"reading": held(6.0)}
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("C")
        loc = FakeExecutor("comfy", "local")
        ext = FakeExecutor("or", "external", default=Step(cost=0.04))
        routes = {"raw": "comfy", "v/m": "or"}
        async with running(db, tmp_path, [loc, ext], routes,
                           gpu_probe=probe_of(box),
                           gpu_foreign_hold_bytes=int(2.5 * GB),
                           gpu_recheck_s=0.05) as s:
            turn, reqs = await framed_batch(
                db, ws, [("raw", "local"), ("raw", "local"),
                         ("v/m", "external")], frame=(1536, 1536))
            first, second, paid = (sl["id"] for sl in turn["slots"])
            loc.script = {first: [Step(outcome=oom()), Step()]}
            await s.submit_turn(turn["id"], reqs)
            for _ in range(300):
                if "lane:local" in s.snapshot()["benches"]:
                    break
                await asyncio.sleep(0.01)
            b = s.snapshot()["benches"]["lane:local"]
            assert b["kind"] == "gpu_held"
            assert b["cause"] == gpu_held_cause(held(6.0))
            assert s.snapshot()["gpu"]["rechecking"] is True
            # the OOM'd take is back in the queue, visibly, then held;
            # the sibling is held; the paid take is untouched (law 10)
            for _ in range(300):
                st = {sid: (await db.get_slot(sid))["status"]
                      for sid in (first, second, paid)}
                if st[first] == "held" and st[second] == "held" \
                        and st[paid] == "landed":
                    break
                await asyncio.sleep(0.01)
            assert st == {first: "held", second: "held", paid: "landed"}
            fs = await db.get_slot(first)
            assert fs["retry_of"] == "gpu_held" and fs["attempt"] == 1
            assert fs["kind"] is None                   # not a failure
            retrying = await events_of(db, "slot.retrying")
            assert retrying[0]["payload"]["kind"] == "gpu_held"
            assert retrying[0]["payload"]["foreign_bytes"] == 6 * GB
            # the card comes free: the lane picks itself up
            box["reading"] = held(0.5)
            done = await settle(db, turn["id"], until={"landed"})
            assert s.snapshot()["benches"] == {}
            assert s.snapshot()["gpu"]["rechecking"] is False
        assert {sl["status"] for sl in done["slots"]} == {"landed"}
        assert (await db.get_slot(first))["attempt"] == 2
        freed = await events_of(db, "gpu.freed")
        assert len(freed) == 1 and freed[0]["payload"]["by"] == "recheck"
        assert (await events_of(db, "engine.unbenched"))[0]["payload"][
            "scope"] == "lane:local"
        assert loc.starts == [first, first, second]


async def test_c2_check_again_and_hold_the_queue(tmp_path):
    box = {"reading": held(6.0)}
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("C")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"raw": "comfy"},
                           gpu_probe=probe_of(box),
                           gpu_foreign_hold_bytes=int(2.5 * GB),
                           gpu_recheck_s=0.05) as s:
            turn, reqs = await framed_batch(db, ws, [("raw", "local")],
                                            frame=(1024, 1024))
            sid = turn["slots"][0]["id"]
            loc.script = {sid: [Step(outcome=oom()), Step()]}
            await s.submit_turn(turn["id"], reqs)
            for _ in range(300):
                if "lane:local" in s.snapshot()["benches"]:
                    break
                await asyncio.sleep(0.01)
            # CHECK AGAIN while it is still held: the bench stands
            r = await s.gpu_check()
            assert r["held"] is True and r["cleared"] is False
            assert r["reading"]["foreign"] == 6 * GB
            assert r["threshold_bytes"] == int(2.5 * GB)
            # HOLD THE QUEUE: the re-check stops; a free card no longer
            # resumes anything on its own
            r = await s.gpu_hold()
            assert r == {"held": True, "rechecking": False}
            box["reading"] = held(0.0)
            await asyncio.sleep(0.25)
            assert "lane:local" in s.snapshot()["benches"]
            assert (await db.get_slot(sid))["status"] == "held"
            assert s.snapshot()["gpu"]["manual_hold"] is True
            # CHECK AGAIN now frees it (the artist asked)
            r = await s.gpu_check()
            assert r["cleared"] is True and r["held"] is False
            done = await settle(db, turn["id"], until={"landed"})
            assert s.snapshot()["gpu"]["manual_hold"] is False
        assert done["slots"][0]["status"] == "landed"
        assert (await events_of(db, "gpu.freed"))[0]["payload"]["by"] == "check"
        assert len(await events_of(db, "gpu.hold_queue")) == 1


async def test_without_a_probe_or_under_a_small_hold_an_oom_is_a1(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("C")
        loc = FakeExecutor("comfy", "local", default=Step(outcome=oom()))
        # a small hold (the desktop's own slice) is not C2
        box = {"reading": held(1.2)}
        async with running(db, tmp_path, [loc], {"raw": "comfy"},
                           gpu_probe=probe_of(box),
                           gpu_foreign_hold_bytes=int(2.5 * GB)) as s:
            turn, reqs = await framed_batch(db, ws, [("raw", "local")],
                                            frame=(1024, 1024))
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert s.snapshot()["benches"] == {}
        assert done["slots"][0]["kind"] == "oom"
        # no probe at all: same
        async with running(db, tmp_path, [loc], {"raw": "comfy"}) as s:
            turn, reqs = await framed_batch(db, ws, [("raw", "local")],
                                            frame=(1024, 1024))
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert s.snapshot()["benches"] == {}
        assert done["slots"][0]["kind"] == "oom"


async def test_a_gpu_held_bench_survives_restart_and_keeps_watching(tmp_path):
    box = {"reading": held(6.0)}
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("C")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"raw": "comfy"},
                           gpu_probe=probe_of(box),
                           gpu_foreign_hold_bytes=int(2.5 * GB),
                           gpu_recheck_s=0.05) as s:
            turn, reqs = await framed_batch(db, ws, [("raw", "local")],
                                            frame=(1024, 1024))
            sid = turn["slots"][0]["id"]
            loc.script = {sid: [Step(outcome=oom()), Step()]}
            await s.submit_turn(turn["id"], reqs)
            for _ in range(300):
                if (await db.get_slot(sid))["status"] == "held":
                    break
                await asyncio.sleep(0.01)
        # a fresh scheduler over the same store: the bench replays, the
        # re-check loop restarts, and a free card resumes the take
        box["reading"] = held(0.0)
        async with running(db, tmp_path, [loc], {"raw": "comfy"},
                           gpu_probe=probe_of(box),
                           gpu_foreign_hold_bytes=int(2.5 * GB),
                           gpu_recheck_s=0.05) as s:
            assert s.snapshot()["benches"]["lane:local"]["kind"] == "gpu_held"
            done = await settle(db, turn["id"], until={"landed"})
            assert s.snapshot()["benches"] == {}
        assert done["slots"][0]["status"] == "landed"


# --- E6 + the routes: headroom, disk refusal, retry at a frame -------------


FRAMED_PRESET = {
    **PRESET,
    "inputs": PRESET["inputs"] + [
        {"key": "width", "type": "int", "default": 1024, "min": 512,
         "max": 2048, "step": 64, "capabilities": ["txt2img"],
         "target": {"node_title": "latent_size", "field": "width"}},
        {"key": "height", "type": "int", "default": 1024, "min": 512,
         "max": 2048, "step": 64, "capabilities": ["txt2img"],
         "target": {"node_title": "latent_size", "field": "height"}},
    ],
}
FRAMED_WORKFLOW = {
    **WORKFLOW,
    "5": {"class_type": "EmptyLatentImage", "_meta": {"title": "latent_size"},
          "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
}


def framed_env(tmp_path, monkeypatch):
    """The hermetic env with a preset that carries a frame."""
    hermetic_env(tmp_path, monkeypatch)
    (tmp_path / "presets" / "testpreset.json").write_text(
        json.dumps(FRAMED_PRESET))
    (tmp_path / "workflows" / "test-txt2img@v1.json").write_text(
        json.dumps(FRAMED_WORKFLOW))


@asynccontextmanager
async def booted_framed(tmp_path, monkeypatch, fakes, probe=None):
    """test_api_v4.booted over the framed preset, with the card probe
    injected through wiring's seam."""
    framed_env(tmp_path, monkeypatch)
    monkeypatch.setattr(wiring, "EXECUTOR_FACTORY",
                        lambda orc: [fakes["comfy"], fakes["openrouter"]])
    monkeypatch.setattr(wiring, "GPU_PROBE", probe)
    api_v4._BPP_CACHE.update(at=0.0, value=None)
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1") as client:
            yield client


async def test_e6_headroom_states_the_card_and_the_drive(tmp_path,
                                                          monkeypatch):
    box = {"reading": held(3.0)}
    fakes = make_fakes()
    monkeypatch.setattr(config, "GPU_FOREIGN_HOLD_BYTES", int(2.5 * GB))
    async with booted_framed(tmp_path, monkeypatch, fakes,
                             probe_of(box)) as client:
        h = (await client.get("/api/headroom")).json()
        assert h["gpu"]["foreign_bytes"] == 3 * GB
        assert h["gpu"]["total_bytes"] == 24 * GB
        assert h["gpu"]["name"] == "test card"
        assert h["gpu_held"] is True
        assert h["gpu_hold_threshold_bytes"] == int(2.5 * GB)
        assert h["disk_free_bytes"] > 0
        assert h["disk_margin_bytes"] == config.DISK_MARGIN_BYTES
        assert h["bytes_per_px"] == config.DISK_FALLBACK_BYTES_PER_PX
        assert h["bytes_per_px_source"] == "fallback"
        # a landed take with a frame teaches the sheet this card's ratio
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1,
                                "inputs": {"width": 1024, "height": 1024}}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        api_v4._BPP_CACHE.update(at=0.0, value=None)
        h = (await client.get("/api/headroom")).json()
        assert h["bytes_per_px_source"] == "record"
        assert h["bytes_per_px"] == pytest.approx(
            len(b"artifact") / (1024 * 1024))
        # the card can't be read: nothing is claimed
        box["reading"] = None
        h = (await client.get("/api/headroom")).json()
        assert h["gpu"] is None and h["gpu_held"] is False


async def test_e6_a_run_the_drive_cannot_hold_is_refused_before_any_row(
        tmp_path, monkeypatch):
    fakes = make_fakes()
    monkeypatch.setattr(api_v4, "_disk_free", lambda: 40 << 20)
    monkeypatch.setattr(config, "DISK_MARGIN_BYTES", 0)
    async with booted_framed(tmp_path, monkeypatch, fakes) as client:
        ws = await make_ws(client)
        # 9 takes at 2048² × 1.5 B/px ≈ 54 MB — over 40 MB free
        r = await client.post(
            f"/api/workspaces/{ws['id']}/turns",
            json={"prompt": "p", "local": [
                {"preset_id": "testpreset", "takes": 9,
                 "inputs": {"width": 2048, "height": 2048}}]})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == (
            "9 slots need about 54 MB and the cockpit's drive has 40 MB "
            "free — free up about 14 MB first; nothing was started")
        detail = (await client.get(f"/api/workspaces/{ws['id']}")).json()
        assert detail["turns"] == []
        # a smaller run fits: 1 take at 1024² ≈ 1.5 MB
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1,
                                "inputs": {"width": 1024, "height": 1024}}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        assert turn["slots"][0]["frame"] == "1024x1024"


async def test_retry_at_a_smaller_frame_rebuilds_the_cell_and_keeps_the_size(
        tmp_path, monkeypatch):
    fakes = make_fakes()
    fakes["comfy"].default = Step(outcome=oom())
    async with booted_framed(tmp_path, monkeypatch, fakes) as client:
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1,
                                "inputs": {"width": 1536, "height": 1536}}])
        turn = await settle_turn(out["turn"]["id"])
        slot = turn["slots"][0]
        assert slot["status"] == "failed" and slot["kind"] == "oom"
        assert slot["frame"] == "1536x1536"
        assert slot["facts"]["smaller"] == "1152x1152"
        detail = (await client.get(f"/api/workspaces/{ws['id']}")).json()
        wire = detail["turns"][0]["slots"][0]
        assert wire["frame"] == "1536x1536"
        assert wire["facts"] == slot["facts"]
        seed = slot["seed"]
        # RETRY AT 1152 × 1152, same seed: the graph carries the size, the
        # cell's dials keep it, and the next plain reroll keeps it too
        fakes["comfy"].default = Step()
        r = await client.post(f"/api/slots/{slot['id']}/reroll",
                              json={"keep_seed": True,
                                    "frame": {"width": 1152, "height": 1152}})
        assert r.status_code == 200, r.text
        assert r.json()["slot"]["seed"] == seed
        assert r.json()["slot"]["kind"] is None
        again = await settle_turn(turn["id"], until={"landed"})
        s2 = again["slots"][0]
        assert s2["frame"] == "1152x1152"
        assert s2["dials"]["width"] == 1152 and s2["dials"]["height"] == 1152
        assert s2["submitted_graph"]["5"]["inputs"] == {
            "width": 1152, "height": 1152, "batch_size": 1}
        r = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert r.status_code == 200, r.text
        third = await settle_turn(turn["id"], until={"landed"})
        assert third["slots"][0]["frame"] == "1152x1152"
        # the record now knows 1152² and 1536² was never held: a later OOM
        # at 1536² offers 1152² from the record, not the arithmetic
        assert (await store().frames_landed("testpreset"))[0]["frame"] \
            == "1152x1152"
        # a frame outside the preset's bounds is clamped visibly
        r = await client.post(f"/api/slots/{slot['id']}/reroll",
                              json={"frame": {"width": 4096, "height": 64}})
        assert r.status_code == 200, r.text
        fourth = await settle_turn(turn["id"], until={"landed"})
        assert fourth["slots"][0]["frame"] == "2048x512"


async def test_a_frame_is_refused_on_a_paid_cell_in_words(tmp_path,
                                                          monkeypatch):
    fakes = make_fakes()
    fakes["openrouter"].default = Step(cost=0.05)
    async with booted_framed(tmp_path, monkeypatch, fakes) as client:
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        sid = turn["slots"][0]["id"]
        r = await client.post(f"/api/slots/{sid}/reroll",
                              json={"frame": {"width": 512, "height": 512}})
        assert r.status_code == 409
        assert "renders at the provider's default size" in r.json()["detail"]
        assert "nothing was spent" in r.json()["detail"]
        assert (await store().get_slot(sid))["status"] == "landed"


async def test_the_queue_view_carries_the_wait_and_the_card(tmp_path,
                                                            monkeypatch):
    box = {"reading": held(0.5)}
    fakes = make_fakes()
    monkeypatch.setattr(config, "RATE_LIMIT_BACKOFF_S", 0.5)
    fakes["openrouter"].default = Step(outcome=EngineFailure(
        "rate_limit", "429"))
    async with booted_framed(tmp_path, monkeypatch, fakes,
                             probe_of(box)) as client:
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        sid = out["turn"]["slots"][0]["id"]
        seen = None
        for _ in range(300):
            q = (await client.get("/api/queue")).json()
            if q["retries"]:
                seen = q["retries"][0]
                break
            await asyncio.sleep(0.01)
        assert seen is not None and seen["slot_id"] == sid
        assert seen["of"] == config.RATE_LIMIT_RETRIES + 1
        assert seen["window"] == "cockpit"
        assert seen["wait_s"] == pytest.approx(0.5)
        assert seen["next_try_at"] > q["now"]
        assert q["gpu"] == {"reading": None, "rechecking": False,
                            "manual_hold": False}
        # give up: skipped free, the wait gone
        r = await client.post(f"/api/slots/{sid}/cancel")
        assert r.json()["action"] == "skipped"
        q = (await client.get("/api/queue")).json()
        assert q["retries"] == []
        # the gpu routes answer even with nothing held
        r = await client.post("/api/gpu/check")
        assert r.status_code == 200
        assert r.json()["held"] is False and r.json()["cleared"] is False
        assert r.json()["reading"]["foreign_bytes"] == int(0.5 * GB)
        r = await client.post("/api/gpu/hold")
        assert r.json() == {"held": False, "rechecking": False}


async def test_the_queue_view_names_a_turn_scoped_bench_on_its_held_cells(
        tmp_path, monkeypatch):
    """A9 through the API: the held cells' `bench` is the turn scope (the
    cell resumes against it), the bench list carries kind `repeat`, and
    the same engine's take in another turn is not held."""
    fakes = make_fakes()
    fakes["comfy"].default = Step(outcome=EngineFailure("error", "boom"))
    async with booted_framed(tmp_path, monkeypatch, fakes) as client:
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 3}])
        turn = await settle_turn(out["turn"]["id"])
        by = {s["pos"]: s["status"] for s in turn["slots"]}
        assert by == {0: "failed", 1: "failed", 2: "held"}
        scope = turn_scope("testpreset", turn["id"])
        q = (await client.get("/api/queue")).json()
        assert [b["scope"] for b in q["benches"]] == [scope]
        assert q["benches"][0]["kind"] == "repeat"
        assert [h["bench"] for h in q["held"]] == [scope]
        fakes["comfy"].default = Step()
        other = await run(client, ws["id"],
                         local=[{"preset_id": "testpreset", "takes": 1}])
        assert (await settle_turn(other["turn"]["id"], until={"landed"}))
        r = await client.post("/api/unbench", json={"scope": scope})
        assert r.json() == {"scope": scope, "cleared": True}
        again = await settle_turn(turn["id"], until={"landed", "failed"})
        assert again["slots"][2]["status"] == "landed"
        assert (await client.get("/api/queue")).json()["benches"] == []
