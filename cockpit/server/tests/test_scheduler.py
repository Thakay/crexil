"""The two-lane scheduler, all of it under
test with scripted fake executors:

- a mixed batch runs locals STRICTLY SEQUENTIAL and externals CONCURRENT
- killing an engine mid-batch benches it and holds its takes (law 1: one
  cause record for nine dead slots)
- cancel semantics (landed stay · queued cost nothing · in-flight
  external states its committed amount, never fabricates a charge)
- committed and charged both land (charge-once ledger)
- a mid-batch restart recovers (a NEW Scheduler over the same DB)
- retries are visible and permanent (law 9); the slot set never changes
  (law 8)

Ordering matters: turns are created AFTER the scheduler starts (the
production flow) — a turn created before start() is deliberately adopted by
recovery, which is exactly what the recovery section exercises.
"""

import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

import pytest

from app.db import Database
from app.executors import DispatchRequest, ExecutionResult, Reconciliation
from app.scheduler import ORPHAN_COPY, RetryRule, Scheduler
from fakes import FakeExecutor, Probe, Step, fail

pytestmark = pytest.mark.anyio

RESTING = {"landed", "failed", "skipped", "set_aside", "late"}
FAST_RETRY = {"rate_limit": RetryRule(1, 0.01),
              "unreachable": RetryRule(1, 0.01)}


# --- harness helpers ---

@asynccontextmanager
async def opened(tmp_path, name="v4.sqlite"):
    db = Database(tmp_path / name)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def running(db, tmp_path, executors, routes, **kw):
    sched = Scheduler(db, executors, route=lambda e: routes[e],
                      staging_dir=tmp_path / "staging",
                      retry_policy=kw.pop("retry_policy", FAST_RETRY), **kw)
    await sched.start()
    try:
        yield sched
    finally:
        await sched.stop()


async def make_batch(db, ws, engines, *, prompt="a rusty sword",
                     committed=None, verb="run", parents=()):
    """engines: list of (engine, lane). Returns (turn, requests) with
    payloads persisted at creation (restart-durable)."""
    specs = []
    for engine, lane in engines:
        if lane == "local":
            specs.append({"engine": engine,
                          "submitted_graph": {"graph_for": engine}})
        else:
            specs.append({"engine": engine,
                          "external": {"model": engine, "prompt": prompt},
                          "committed_usd": committed})
    turn = await db.create_turn_with_slots(ws["id"], prompt, {}, specs)
    reqs = [DispatchRequest(slot_id=s["id"], engine=s["engine"],
                            submitted_graph=s["submitted_graph"],
                            external=s["external"],
                            committed_usd=s["committed_usd"], prompt=prompt,
                            verb=verb, parents=tuple(parents))
            for s in turn["slots"]]
    return turn, reqs


async def settle(db, turn_id, until=RESTING | {"held"}, timeout_ticks=800):
    statuses = {}
    for _ in range(timeout_ticks):
        turn = await db.get_turn(turn_id)
        statuses = {s["id"]: s["status"] for s in turn["slots"]}
        if all(v in until for v in statuses.values()):
            return turn
        await asyncio.sleep(0.01)
    raise AssertionError(f"turn never settled: {statuses}")


async def wait_until_running(db, slot_id, ticks=300):
    for _ in range(ticks):
        if (await db.get_slot(slot_id))["status"] == "running":
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"slot {slot_id} never reached running")


async def events_of(db, type_):
    return [e for e in await db.events_since(0) if e["type"] == type_]


# --- lanes: mixed batch, locals sequential, externals concurrent ---

async def test_mixed_batch_lands_out_of_order_and_completely(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("Hero Sword")
        release_local = asyncio.Event()
        local = FakeExecutor("comfy", "local", default=Step(gate=release_local))
        ext = FakeExecutor("or", "external", default=Step(cost=0.01))
        routes = {"klein": "comfy", "z-image": "comfy",
                  "gpt": "or", "gemini": "or"}
        async with running(db, tmp_path, [local, ext], routes) as sched:
            turn, reqs = await make_batch(
                db, ws, [("klein", "local"), ("z-image", "local"),
                         ("gpt", "external"), ("gemini", "external")],
                committed=0.02)
            await sched.submit_turn(turn["id"], reqs)
            for _ in range(800):
                current = await db.get_turn(turn["id"])
                if all(s['status'] == 'landed' for s in current['slots'][2:]):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('External work did not finish while the local lane was occupied.')
            assert all(s['status'] != 'landed' for s in current['slots'][:2])
            release_local.set()
            done = await settle(db, turn["id"], until={"landed"})
        nodes = await db.list_nodes(ws["id"])
        assert len(nodes) == 4                     # every slot landed a node
        externals = [n for n in nodes if n["engine"] in ("gpt", "gemini")]
        locals_ = [n for n in nodes if n["engine"] in ("klein", "z-image")]
        # External work finishes independently of a blocked local lane.
        assert max(e["created_at"] for e in externals) \
            <= min(n["created_at"] for n in locals_)
        assert {s["id"] for s in done["slots"]} \
            == {n["slot_id"] for n in nodes}


async def test_local_takes_never_overlap_even_under_load(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local", default=Step(delay_s=0.01))
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 8)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        assert local.probe.max_concurrent == 1     # locals run one at a time


async def test_local_serialism_spans_two_local_executors(tmp_path):
    async with opened(tmp_path) as db:             # the 3D shape
        ws = await db.create_workspace("A")
        probe = Probe()
        comfy = FakeExecutor("comfy", "local", default=Step(delay_s=0.01),
                             probe=probe)
        funnel = FakeExecutor("funnel3d", "local", default=Step(delay_s=0.01),
                              probe=probe)
        routes = {"klein": "comfy", "trellis": "funnel3d"}
        async with running(db, tmp_path, [comfy, funnel], routes) as s:
            turn, reqs = await make_batch(
                db, ws, [("klein", "local"), ("trellis", "local")] * 3)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        assert probe.max_concurrent == 1           # ONE GPU, across executors


async def test_external_takes_overlap_up_to_the_cap(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(delay_s=0.05))
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           external_concurrency=2) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")] * 5)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        assert ext.probe.max_concurrent == 2


async def test_one_slow_local_never_blocks_externals(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        local = FakeExecutor("comfy", "local", default=Step(gate=gate))
        ext = FakeExecutor("or", "external")
        routes = {"klein": "comfy", "gpt": "or"}
        async with running(db, tmp_path, [local, ext], routes) as s:
            turn, reqs = await make_batch(
                db, ws, [("klein", "local"), ("gpt", "external")])
            await s.submit_turn(turn["id"], reqs)
            nodes = []
            for _ in range(300):                    # external lands while
                nodes = await db.list_nodes(ws["id"])   # the local still runs
                if nodes:
                    break
                await asyncio.sleep(0.01)
            assert nodes and nodes[0]["engine"] == "gpt"
            gate.set()
            await settle(db, turn["id"], until={"landed"})


async def test_submit_turn_rejects_a_mismatched_request_set(tmp_path):
    async with opened(tmp_path) as db:             # law 8
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2)
            with pytest.raises(ValueError, match="law 8"):
                await s.submit_turn(turn["id"], reqs[:1])


async def test_the_slot_set_is_identical_before_and_after(tmp_path):
    async with opened(tmp_path) as db:             # law 8
        ws = await db.create_workspace("A")
        local = FakeExecutor(
            "comfy", "local",
            default=Step(outcome=fail("error")))   # even under failures
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 3)
            before = [sl["id"] for sl in turn["slots"]]
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        assert [sl["id"] for sl in done["slots"]] == before


# --- bench: killing an engine benches it and holds its takes ---

async def test_model_down_benches_the_engine_and_holds_its_takes(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external",
                           default=Step(outcome=fail("model_down")))
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")] * 3)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        statuses = sorted(sl["status"] for sl in done["slots"])
        assert statuses == ["failed", "held", "held"]   # one dead, rest held
        assert len(await events_of(db, "engine.benched")) == 1  # law 1


async def test_a_benched_engine_never_stops_the_other_engines(tmp_path):
    async with opened(tmp_path) as db:             # law 1
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext],
                           {"gpt": "or", "gemini": "or"},
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(
                db, ws,
                [("gpt", "external")] * 2 + [("gemini", "external")] * 2)
            gpt_slots = [sl["id"] for sl in turn["slots"][:2]]
            ext.script = {sid: Step(outcome=fail("model_down"))
                          for sid in gpt_slots}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        by = {sl["id"]: sl["status"] for sl in done["slots"]}
        gemini = [sl for sl in done["slots"] if sl["engine"] == "gemini"]
        assert all(sl["status"] == "landed" for sl in gemini)
        assert by[gpt_slots[0]] == "failed" and by[gpt_slots[1]] == "held"


async def test_dead_key_benches_the_whole_external_lane_in_one_record(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        routes = {"gpt": "or", "gemini": "or", "flux": "or"}
        async with running(db, tmp_path, [ext], routes,
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(
                db, ws, [("gpt", "external"), ("gemini", "external"),
                         ("flux", "external")])
            ext.script = {turn["slots"][0]["id"]:
                          Step(outcome=fail("dead_key"))}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        statuses = sorted(sl["status"] for sl in done["slots"])
        assert statuses == ["failed", "held", "held"]   # OTHER engines held too
        benched = await events_of(db, "engine.benched")
        assert len(benched) == 1
        assert benched[0]["payload"]["scope"] == "lane:external"


async def test_comfy_unreachable_benches_comfyui_alone(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local")
        routes = {"klein": "comfy", "z-image": "comfy"}
        async with running(db, tmp_path, [local], routes) as s:
            turn, reqs = await make_batch(
                db, ws, [("klein", "local"), ("z-image", "local")])
            local.script = {turn["slots"][0]["id"]:
                            [Step(outcome=fail("unreachable")),
                             Step(outcome=fail("unreachable"))]}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        by = {sl["engine"]: sl["status"] for sl in done["slots"]}
        assert by == {"klein": "failed", "z-image": "held"}
        assert (await events_of(db, "engine.benched"))[0]["payload"]["scope"] \
            == "provider:comfy"


async def test_moderation_fails_the_slot_without_benching(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")] * 2)
            ext.script = {turn["slots"][0]["id"]:
                          Step(outcome=fail("moderation"))}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        assert sorted(sl["status"] for sl in done["slots"]) \
            == ["failed", "landed"]
        assert await events_of(db, "engine.benched") == []


async def test_unbench_requeues_only_bench_held_slots_and_they_land(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")] * 3)
            ext.script = {turn["slots"][0]["id"]:
                          Step(outcome=fail("model_down"))}
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"])
            # a hold that is NOT the bench's: park a fresh slot by hand
            other_turn, _ = await make_batch(db, ws, [("gpt", "external")])
            await db.transition_slot(other_turn["slots"][0]["id"], "held",
                                     payload={"reason": "manual"})
            await s.unbench("engine:gpt")
            done = await settle(db, turn["id"])
        assert sorted(sl["status"] for sl in done["slots"]) \
            == ["failed", "landed", "landed"]
        other = await db.get_slot(other_turn["slots"][0]["id"])
        assert other["status"] == "held"            # not the bench's to resume


async def test_a_take_in_flight_when_the_bench_lands_still_finishes(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        ext = FakeExecutor("or", "external",
                           default=Step(gate=gate, cost=0.01))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, turn["slots"][0]["id"])
            await s.bench("engine:gpt", kind="model_down", cause="manual")
            gate.set()                               # bench parks queued only
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "landed"


async def test_bench_state_survives_a_restart_via_events(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            await s.bench("engine:gpt", kind="model_down", cause="down")
        ext2 = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext2], {"gpt": "or"}) as s2:
            assert s2.bench_for("gpt") is not None
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            await s2.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "held"  # held on arrival


# --- cancel semantics ---

async def test_cancel_leaves_landed_slots_untouched(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(cost=0.02))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.02)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
            summary = await s.cancel_turn(turn["id"])
        assert summary == {"kept": 1, "skipped": 0, "interrupted": 0,
                           "in_flight": []}
        assert (await db.get_slot(turn["slots"][0]["id"]))["status"] \
            == "landed"


async def test_cancel_skips_queued_slots_and_says_nothing_spent(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        local = FakeExecutor("comfy", "local", default=Step(gate=gate))
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 3)
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, turn["slots"][0]["id"])
            summary = await s.cancel_turn(turn["id"])
            gate.set()
            done = await settle(db, turn["id"])
        assert summary["skipped"] == 2 and summary["interrupted"] == 1
        skipped_evs = [e for e in await events_of(db, "slot.skipped")
                       if e["payload"].get("note") == "nothing spent"]
        assert len(skipped_evs) == 2
        assert sorted(sl["status"] for sl in done["slots"]) \
            == ["failed", "skipped", "skipped"]
        failed = [sl for sl in done["slots"] if sl["status"] == "failed"][0]
        assert failed["cause"] == "cancelled"        # interrupted local


async def test_cancel_of_in_flight_external_states_the_amount(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(outcome="hang"))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            summary = await s.cancel_turn(turn["id"])
            done = await settle(db, turn["id"])
        assert summary["in_flight"] == [{"slot_id": slot_id,
                                         "committed_usd": 0.05}]
        assert done["slots"][0]["status"] == "late"      # never failed
        assert done["slots"][0]["cause"] is None
        late_evs = await events_of(db, "slot.late")
        assert len(late_evs) == 1
        assert late_evs[0]["payload"]["from"] == "running"
        unverified = await events_of(db, "slot.charge_unverified")
        assert len(unverified) == 1
        assert unverified[0]["payload"]["committed_usd"] == 0.05
        assert unverified[0]["payload"]["reason"] == "cancel"
        assert await db.workspace_charged(ws["id"]) == 0   # never fabricated


# --- late (a paid cancelled take rests late, lands in its own cell) ---

async def test_cancelled_external_rests_late_and_the_ride_is_not_shot(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(outcome="hang"))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            r = await s.cancel_slot(slot_id)
            assert r["action"] == "cancelled_in_flight"
            assert r["committed_usd"] == 0.05
            assert (await db.get_slot(slot_id))["status"] == "late"
            snap = s.snapshot()
            assert snap["takes"][slot_id]["detached"] is True
        assert ext.cancels == []             # the ride was never shot
        assert await events_of(db, "engine.benched") == []
        assert len(await events_of(db, "slot.charge_unverified")) == 1


async def test_late_bytes_land_into_the_original_cell(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        ext = FakeExecutor("or", "external",
                           default=Step(gate=gate, cost=0.04))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            r = await s.cancel_slot(slot_id)
            assert r["action"] == "cancelled_in_flight"
            gate.set()                       # the ride completes anyway
            await settle(db, turn["id"], until={"landed"})
        slot = await db.get_slot(slot_id)
        assert slot["status"] == "landed"
        nodes = await db.list_nodes(ws["id"])
        assert len(nodes) == 1
        assert nodes[0]["slot_id"] == slot_id    # its own frozen cell
        assert nodes[0]["take"] == 1
        assert await db.workspace_charged(ws["id"]) == 0.04
        types = [e["type"] for e in await db.events_since(0)]
        assert types.index("slot.late") < types.index("slot.late_landed")
        late_landed = await events_of(db, "slot.late_landed")
        assert late_landed[0]["node_id"] == nodes[0]["id"]


async def test_late_wait_failure_rests_late_and_records_it(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        ext = FakeExecutor("or", "external",
                           default=Step(gate=gate, outcome=fail("error")))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            await s.cancel_slot(slot_id)
            gate.set()                       # the ride dies without a result
            for _ in range(200):
                if await events_of(db, "slot.late_failed"):
                    break
                await asyncio.sleep(0.01)
        assert (await db.get_slot(slot_id))["status"] == "late"  # rests late
        late_failed = await events_of(db, "slot.late_failed")
        assert len(late_failed) == 1
        assert late_failed[0]["payload"]["kind"] == "error"
        assert await events_of(db, "slot.failed") == []
        assert await events_of(db, "engine.benched") == []
        assert await db.workspace_charged(ws["id"]) == 0


async def test_detached_wait_releases_the_external_permit(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        ext = FakeExecutor("or", "external",
                           script={}, default=Step(cost=0.01))
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           external_concurrency=1) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            slot_id = turn["slots"][0]["id"]
            ext.script[slot_id] = Step(gate=gate, outcome=fail("error"))
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, slot_id)
            await s.cancel_slot(slot_id)     # detached; still holds the permit
            turn2, reqs2 = await make_batch(db, ws, [("gpt", "external")])
            await s.submit_turn(turn2["id"], reqs2)
            await asyncio.sleep(0.05)        # permit still held by the ride
            assert (await db.get_slot(turn2["slots"][0]["id"]))["status"] \
                == "queued"
            gate.set()                       # ride resolves -> permit freed
            await settle(db, turn2["id"], until={"landed"})
        assert (await db.get_slot(slot_id))["status"] == "late"


async def test_a_second_cancel_of_a_late_slot_keeps_it(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(outcome="hang"))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            first = await s.cancel_slot(slot_id)
            second = await s.cancel_slot(slot_id)
        assert first["action"] == "cancelled_in_flight"
        assert second == {"slot_id": slot_id, "action": "kept",
                          "status": "late"}
        assert len(await events_of(db, "slot.charge_unverified")) == 1


async def test_a_late_slot_survives_restart_untouched(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(outcome="hang"))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            slot_id = turn["slots"][0]["id"]
            await wait_until_running(db, slot_id)
            await s.cancel_slot(slot_id)
        # stop() killed the detached ride; the slot rests late durably
        assert (await db.get_slot(slot_id))["status"] == "late"
        ext2 = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext2], {"gpt": "or"}) as s2:
            await asyncio.sleep(0.05)
            assert (await db.get_slot(slot_id))["status"] == "late"
            assert slot_id not in s2.snapshot()["takes"]
        assert ext2.starts == []             # never re-adopted, never re-run
        recovered = await events_of(db, "scheduler.recovered")
        assert recovered[-1]["payload"]["requeued"] == 0


async def test_cancel_is_idempotent(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local",
                             default=Step(outcome=fail("error")))
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"])
            first = await s.cancel_turn(turn["id"])
            second = await s.cancel_turn(turn["id"])
        assert first["kept"] == 2 and second["kept"] == 2


async def test_output_arriving_after_a_lost_race_is_charged_once_and_kept(tmp_path):
    """The record_charge door: the slot was finalised out from under
    the take, the result still arrived with confirmed money — bytes kept
    (law 7), charge recorded exactly once."""
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        ext = FakeExecutor("or", "external",
                           default=Step(gate=gate, cost=0.04))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            slot_id = turn["slots"][0]["id"]
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, slot_id)
            await db.fail_slot(slot_id, cause="forced by test")
            gate.set()
            for _ in range(200):
                if await db.workspace_charged(ws["id"]) > 0:
                    break
                await asyncio.sleep(0.01)
        assert await db.workspace_charged(ws["id"]) == 0.04
        charged = [e for e in await db.events_since(0)
                   if e["charged_usd"] is not None]
        assert len(charged) == 1 and charged[0]["type"] == "slot.charged"
        note = charged[0]["payload"]["note"]
        staged = [p for p in (tmp_path / "staging").rglob("*") if p.is_file()]
        assert staged and any(str(p) in note for p in staged)


async def test_cancel_turn_appends_one_summary_event(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")])
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"])
            await s.cancel_turn(turn["id"])
        evs = await events_of(db, "turn.cancelled")
        assert len(evs) == 1
        assert evs[0]["payload"]["kept"] == 1


# --- money: committed and charged both land ---

async def test_committed_flows_to_the_node_and_charged_to_the_ledger(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(cost=0.04))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")],
                                          committed=0.05)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        node = (await db.list_nodes(ws["id"]))[0]
        assert node["committed_usd"] == 0.05
        assert node["charged_usd"] == 0.04
        assert await db.workspace_charged(ws["id"]) == 0.04


async def test_each_confirmed_charge_enters_the_ledger_exactly_once(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external", default=Step(cost=0.03))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")] * 3,
                                          committed=0.03)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        charged = [e for e in await db.events_since(0)
                   if e["charged_usd"] is not None]
        assert len(charged) == 3
        assert await db.workspace_charged(ws["id"]) == 0.09


async def test_local_takes_never_write_a_charge(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        assert await db.workspace_charged(ws["id"]) == 0
        assert all(n["charged_usd"] is None
                   for n in await db.list_nodes(ws["id"]))


# --- retry (law 9: visible, recorded, never after cancel) ---

async def test_rate_limit_retries_once_visibly_then_lands(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            sid = turn["slots"][0]["id"]
            ext.script = {sid: [Step(outcome=fail("rate_limit")), Step()]}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"], until={"landed"})
        assert done["slots"][0]["attempt"] == 2
        retrying = await events_of(db, "slot.retrying")
        assert len(retrying) == 1
        assert retrying[0]["payload"]["kind"] == "rate_limit"
        node = (await db.list_nodes(ws["id"]))[0]
        assert node["take"] == 2                      # landed on the retry


async def test_unreachable_retries_once_then_fails_and_benches(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external",
                           default=Step(outcome=fail("unreachable")))
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        slot = done["slots"][0]
        assert slot["status"] == "failed" and slot["attempt"] == 2
        assert len(await events_of(db, "slot.retrying")) == 1
        assert (await events_of(db, "engine.benched"))[0]["payload"]["scope"] \
            == "lane:external"


async def test_model_down_and_moderation_never_retry(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext],
                           {"gpt": "or", "gemini": "or"}) as s:
            turn, reqs = await make_batch(
                db, ws, [("gpt", "external"), ("gemini", "external")])
            ext.script = {
                turn["slots"][0]["id"]: Step(outcome=fail("model_down")),
                turn["slots"][1]["id"]: Step(outcome=fail("moderation"))}
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        assert all(sl["attempt"] == 1 for sl in done["slots"])
        assert await events_of(db, "slot.retrying") == []


async def test_a_cancel_during_backoff_beats_the_retry(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"},
                           retry_policy={"rate_limit":
                                         RetryRule(1, 0.3)}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            sid = turn["slots"][0]["id"]
            ext.script = {sid: [Step(outcome=fail("rate_limit")), Step()]}
            await s.submit_turn(turn["id"], reqs)
            for _ in range(200):                     # wait for the backoff
                if await events_of(db, "slot.retrying"):
                    break
                await asyncio.sleep(0.01)
            await s.cancel_slot(sid)
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "skipped"
        assert len(ext.starts) == 1                  # no second dispatch


async def test_retry_history_is_permanent_in_slot_events(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"}) as s:
            turn, reqs = await make_batch(db, ws, [("gpt", "external")])
            sid = turn["slots"][0]["id"]
            ext.script = {sid: [Step(outcome=fail("rate_limit")), Step()]}
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        types = [e["type"] for e in await db.slot_events(sid)]
        assert types == ["slot.prepared", "slot.dispatched", "slot.retrying",
                         "slot.queued", "slot.dispatched", "execution.output_discovered", "node.collected",
                         "execution.output_collected", "node.landed"]


# --- restart recovery: a mid-batch restart recovers ---
# These tests DELIBERATELY leave state behind and start a fresh Scheduler
# over the same DB — turns created before start() are recovery's to adopt.

async def test_restart_finishes_queued_slots_and_preserves_active_uncertainty(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        gate = asyncio.Event()
        local = FakeExecutor("comfy", "local", default=Step(gate=gate))
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 3)
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, turn["slots"][0]["id"])
        # stop() left the in-flight row running (crash semantics)
        assert (await db.get_slot(turn["slots"][0]["id"]))["status"] \
            == "running"
        local2 = FakeExecutor("comfy", "local")      # fresh process
        async with running(db, tmp_path, [local2], {"klein": "comfy"}):
            done = await settle(db, turn["id"], until={"landed", "failed"})
        assert [sl['status'] for sl in done['slots']] == ['failed', 'landed', 'landed']
        assert done['slots'][0]['kind'] == 'uncertain'
        assert done['slots'][0]['id'] not in local2.starts
        assert len(local2.starts) == 2
        assert len(await events_of(db, 'slot.delivery_uncertain')) == 1


async def test_recovered_slots_keep_their_land_fields(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        parent = await db.import_node(ws["id"], output_path="/i/ref.png")
        gate = asyncio.Event()
        local = FakeExecutor("comfy", "local", default=Step(gate=gate))
        async with running(db, tmp_path, [local], {"klein": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2,
                                          verb="refine",
                                          parents=[parent["id"]])
            await s.submit_turn(turn["id"], reqs)
            await wait_until_running(db, turn["slots"][0]["id"])
        local2 = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local2], {"klein": "comfy"}):
            await settle(db, turn["id"], until={"landed", "failed"})
        for node in await db.list_nodes(ws["id"]):
            if node["id"] == parent["id"]:
                continue
            assert node["verb"] == "refine"           # from slot.prepared
            assert node["parents"] == [parent["id"]]


async def test_running_local_with_history_output_lands_on_recovery(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        turn, _ = await make_batch(db, ws, [("klein", "local")])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid, job_ref="pid-old",
                               submitted_graph={"g": 1})
        local = FakeExecutor("comfy", "local")
        local.reconcile_map[sid] = Reconciliation(
            "landed", result=ExecutionResult(data=b"recovered-bytes"))
        async with running(db, tmp_path, [local], {"klein": "comfy"}):
            done = await settle(db, turn["id"], until={"landed"})
        node = (await db.list_nodes(ws["id"]))[0]
        assert node["duration_ms"] is None            # observed or absent
        files = [p for p in (tmp_path / "staging").rglob("*") if p.is_file()]
        assert files and files[0].read_bytes() == b"recovered-bytes"
        assert done["slots"][0]["status"] == "landed"


async def test_running_local_with_error_history_fails_on_recovery(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        turn, _ = await make_batch(db, ws, [("klein", "local")])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid, job_ref="pid-old",
                               submitted_graph={"g": 1})
        local = FakeExecutor("comfy", "local")
        local.reconcile_map[sid] = Reconciliation("failed",
                                                  cause="error: OOM")
        async with running(db, tmp_path, [local], {"klein": "comfy"}):
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "failed"
        # the face is the plain line (law 3); history's text is detail
        assert "not your prompt" in done["slots"][0]["cause"]
        assert done["slots"][0]["detail"] == "error: OOM"


async def test_running_local_without_job_ref_is_uncertain_and_never_resubmitted(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        turn, _ = await make_batch(db, ws, [("klein", "local")])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid, submitted_graph={"g": 1})   # no job_ref:
        local = FakeExecutor("comfy", "local")                  # pre-submit crash
        async with running(db, tmp_path, [local], {"klein": "comfy"}):
            done = await settle(db, turn["id"], until={"failed"})
        assert done['slots'][0]['kind'] == 'uncertain'
        assert done['slots'][0]['attempt'] == 1
        assert local.starts == []
        assert await db.list_nodes(ws['id']) == []


async def test_orphaned_external_fails_honestly_with_unverified_amount(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        turn, _ = await make_batch(db, ws, [("gpt", "external")],
                                   committed=0.06)
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid, external={"model": "gpt", "prompt": "p"},
                               committed_usd=0.06)
        ext = FakeExecutor("or", "external")
        async with running(db, tmp_path, [ext], {"gpt": "or"}):
            done = await settle(db, turn["id"])
        slot = done["slots"][0]
        assert slot["status"] == "failed"
        assert slot["cause"] == ORPHAN_COPY           # never auto-resubmitted
        assert ext.starts == []
        unverified = await events_of(db, "slot.charge_unverified")
        assert unverified[0]["payload"]["reason"] == "restart"
        assert unverified[0]["payload"]["committed_usd"] == 0.06
        assert await db.workspace_charged(ws["id"]) == 0


async def test_a_queued_slot_without_payload_is_skipped_loudly(tmp_path):
    async with opened(tmp_path) as db:               # law 7: loud, never silent
        ws = await db.create_workspace("A")
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "klein"}])   # no payload persisted
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}):
            done = await settle(db, turn["id"])
        assert done["slots"][0]["status"] == "skipped"
        skipped = (await events_of(db, "slot.skipped"))[0]
        assert "reroll to re-run" in skipped["payload"]["reason"]
        assert local.starts == []


async def test_recovery_appends_a_summary_event(tmp_path):
    async with opened(tmp_path) as db:
        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"}):
            pass
        evs = await events_of(db, "scheduler.recovered")
        assert len(evs) == 1
        assert set(evs[0]["payload"]) >= {"requeued", "orphaned", "skipped"}


# --- emit discipline ---

async def test_every_transition_reaches_on_event_in_seq_order(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        seen = []

        async def on_event(ev):
            seen.append(ev)

        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"},
                           on_event=on_event) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")])
            await s.submit_turn(turn["id"], reqs)
            await settle(db, turn["id"], until={"landed"})
        seqs = [e["seq"] for e in seen]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        types = [e["type"] for e in seen]
        assert {"slot.prepared", "slot.dispatched", "node.landed"} \
            <= set(types)


async def test_a_failing_on_event_callback_never_breaks_a_take(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")

        async def broken(ev):
            raise RuntimeError("ui went away")

        local = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [local], {"klein": "comfy"},
                           on_event=broken) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"], until={"landed"})
        assert all(sl["status"] == "landed" for sl in done["slots"])


async def test_recovery_collects_a_still_running_prompt_without_new_dispatch(tmp_path):
    from fakes import FakeHandle
    async with opened(tmp_path) as db:
        ws = await db.create_workspace('Running job')
        turn, _ = await make_batch(db, ws, [('klein', 'local')])
        sid = turn['slots'][0]['id']
        await db.dispatch_slot(sid, job_ref='still-running', submitted_graph={'g': 1})
        local = FakeExecutor('comfy', 'local')
        local.reconcile_map[sid] = Reconciliation('running', handle=FakeHandle(
            job_ref='still-running', step=Step(data=b'collected-existing-result')))
        async with running(db, tmp_path, [local], {'klein': 'comfy'}):
            done = await settle(db, turn['id'], until={'landed'})
        assert done['slots'][0]['attempt'] == 1
        assert done['slots'][0]['job_ref'] == 'still-running'
        assert local.starts == []
        nodes = await db.list_nodes(ws['id'])
        assert len(nodes) == 1
        assert Path(nodes[0]['output_path']).read_bytes() == b'collected-existing-result'
