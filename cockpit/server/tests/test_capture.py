"""Unhandled-failure capture into the event log (every failure path
produces a loggable record), under fault injection:

  a take crashing the cockpit's OWN code   — the slot fails `internal`
                                             with the error and the ledger
                                             row named in DETAILS; the
                                             lane serves the next take
  the lane loop itself (local · external)  — a crash outside the take
                                             fails THAT slot (skipped in
                                             words when it never ran) and
                                             the lane carries on
  a live-feed listener · the GPU probe ·
  a bill re-ask                            — recorded, once per cause
                                             (repeats fold), the product
                                             path unchanged
  a background task dying                  — the watch records what it
                                             died of
  a task nobody awaited                    — asyncio's own hook feeds the
                                             ledger, the default logger
                                             still runs
  a route crashing                         — 500 in words (law 3) whose
                                             words name the row; refusals
                                             (4xx) leave no row
  recovery failing at boot                 — recorded, then the boot fails
                                             loudly and the store closes

Every row is `cockpit.failure`: site · error · bounded traceback ·
best-effort ids (the events table is un-FK'd for this — db.py).
"""

from __future__ import annotations

import asyncio
import gc
import sys

import pytest

from app import capture as capture_mod
from app import main, wiring
from app.capture import (FAILURE_EVENT, MESSAGE_CHARS, TRACEBACK_CHARS,
                         Capture, describe)
from app.db import Database
from app.executors import EngineFailure, GpuReading
from app.scheduler import INTERNAL_CAUSE, Scheduler
from fakes import FakeExecutor, Step
from test_api_v4 import booted, hermetic_env, make_fakes
from test_laws import FORBIDDEN
from test_scheduler import (events_of, make_batch, opened, running, settle)

pytestmark = pytest.mark.anyio

GB = 1 << 30


async def failures(db):
    return await events_of(db, FAILURE_EVENT)


async def until(pred, ticks=500):
    for _ in range(ticks):
        if await pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


def raise_once(exc):
    """A stand-in that raises on its first call and behaves on the rest."""
    calls = {"n": 0}

    def make(real):
        async def stand_in(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise exc
            return await real(*a, **kw)
        return stand_in
    return make


def lane_loop_bomb(sched, message):
    """`bench_for` that raises once — only when the LANE LOOP calls it
    (submit_turn's own registration call passes): the crash lands inside
    `_local_loop` / `_run_external`, outside any take."""
    real = sched.bench_for
    fired = {"done": False}

    def bench_for(engine, turn_id=None):
        caller = sys._getframe(1).f_code.co_name
        if caller in ("_local_loop", "_run_external") and not fired["done"]:
            fired["done"] = True
            raise RuntimeError(message)
        return real(engine, turn_id)
    return bench_for


# --- the record itself ------------------------------------------------------


def test_describe_names_the_error_and_stays_bounded():
    try:
        raise ValueError("x" * 5000)
    except ValueError as e:
        d = describe(e)
    assert d["exc_type"] == "ValueError"
    assert d["error"].startswith("ValueError: xxx")
    assert len(d["message"]) == MESSAGE_CHARS
    # the tail is kept — the innermost frame and the message live there
    assert len(d["traceback"]) <= TRACEBACK_CHARS + 1
    assert d["traceback"].startswith("…")
    assert d["traceback"].rstrip().endswith("x" * 20)
    assert "Traceback" in describe(RuntimeError("bare")) ["traceback"] or \
        describe(RuntimeError("bare"))["error"] == "RuntimeError: bare"


async def test_record_writes_one_row_with_site_error_traceback_and_ids(
        tmp_path):
    async with opened(tmp_path) as db:
        cap = Capture(db)
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            row = await cap.record("unit.site", e, workspace_id="ws-1",
                                   turn_id="t-1", slot_id="s-1",
                                   engine="klein", attempt=2,
                                   note="what it means")
        rows = await failures(db)
        assert len(rows) == 1 and rows[0]["seq"] == row["seq"]
        ev = rows[0]
        assert (ev["workspace_id"], ev["turn_id"], ev["slot_id"]) == \
            ("ws-1", "t-1", "s-1")
        assert ev["charged_usd"] is None
        p = ev["payload"]
        assert p["site"] == "unit.site" and p["kind"] == "internal"
        assert p["error"] == "RuntimeError: boom"
        assert "raise RuntimeError" in p["traceback"]
        assert (p["engine"], p["attempt"], p["note"]) == ("klein", 2,
                                                          "what it means")
        assert p["repeats_folded"] == 0


async def test_repeats_fold_inside_the_window_and_are_counted_after(
        tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        cap = Capture(db)
        clock = {"now": 1000.0}
        monkeypatch.setattr(capture_mod.time, "monotonic",
                            lambda: clock["now"])
        first = await cap.record("site", RuntimeError("same"))
        again = await cap.record("site", RuntimeError("same"))
        assert again == {"seq": first["seq"], "folded": True}
        other = await cap.record("site", RuntimeError("different"))
        assert other["seq"] != first["seq"]
        elsewhere = await cap.record("other.site", RuntimeError("same"))
        assert elsewhere["seq"] not in (first["seq"], other["seq"])
        assert len(await failures(db)) == 3
        clock["now"] += capture_mod.REPEAT_WINDOW_S + 1
        later = await cap.record("site", RuntimeError("same"))
        assert later["seq"] > other["seq"]
        assert later["payload"]["repeats_folded"] == 1


async def test_record_never_raises_when_the_store_is_closed_or_refuses(
        tmp_path, monkeypatch):
    db = Database(tmp_path / "v4.sqlite")
    await db.open()
    cap = Capture(db)

    async def refuse(*a, **kw):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(db, "append_event", refuse)
    assert await cap.record("site", ValueError("x")) is None
    await db.close()
    assert not db.opened
    assert await cap.record("site", ValueError("y")) is None


# --- a take crashing the cockpit's own code --------------------------------


async def test_a_crashed_take_fails_internal_naming_the_record_and_the_lane_lives(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local")
        loc.start = raise_once(RuntimeError("cockpit bug in dispatch"))(
            loc.start)
        async with running(db, tmp_path, [loc], {"raw-zimage": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("raw-zimage", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        first, second = sorted(done["slots"], key=lambda x: x["pos"])
        # the take: red, plain, internal — the error and the row in DETAILS
        assert first["status"] == "failed" and first["kind"] == "internal"
        assert first["cause"] == INTERNAL_CAUSE
        assert not FORBIDDEN.search(first["cause"])
        rows = await failures(db)
        assert len(rows) == 1
        ev = rows[0]
        assert first["detail"].startswith(
            "internal take crash — RuntimeError: cockpit bug in dispatch")
        assert f"recorded as event #{ev['seq']}" in first["detail"]
        # the row: site, ids, engine, attempt, the traceback
        assert ev["slot_id"] == first["id"] and ev["turn_id"] == turn["id"]
        assert ev["workspace_id"] == ws["id"]
        p = ev["payload"]
        assert p["site"] == "scheduler.take"
        assert (p["engine"], p["attempt"]) == ("raw-zimage", 1)
        assert "cockpit bug in dispatch" in p["traceback"]
        # the slot's own ledger row: cause · kind · attempt · engine
        failed = (await events_of(db, "slot.failed"))[0]
        assert failed["payload"]["kind"] == "internal"
        assert failed["payload"]["attempt"] == 1
        assert failed["payload"]["engine"] == "raw-zimage"
        assert f"#{ev['seq']}" in failed["payload"]["detail"]
        # one buggy take never kills its lane (law 1's spirit)
        assert second["status"] == "landed"


async def test_a_crash_in_the_local_lane_loop_skips_that_slot_in_words(
        tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"raw-zimage": "comfy"}) as s:
            monkeypatch.setattr(s, "bench_for",
                                lane_loop_bomb(s, "no route for this lane"))
            turn, reqs = await make_batch(db, ws, [("raw-zimage", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        first, second = sorted(done["slots"], key=lambda x: x["pos"])
        # never dispatched, so never `failed`: skipped, with the reason
        assert first["status"] == "skipped"
        assert second["status"] == "landed"
        rows = await failures(db)
        assert len(rows) == 1 and rows[0]["payload"]["site"] == \
            "scheduler.local_lane"
        assert rows[0]["slot_id"] == first["id"]
        assert rows[0]["payload"]["engine"] == "raw-zimage"
        skipped = [e for e in await db.slot_events(first["id"])
                   if e["type"] == "slot.skipped"]
        assert len(skipped) == 1
        reason = skipped[0]["payload"]["reason"]
        assert reason.startswith(INTERNAL_CAUSE)
        assert f"recorded as event #{rows[0]['seq']}" in reason
        assert "no route for this lane" in skipped[0]["payload"]["detail"]


async def test_a_crash_in_the_external_lane_is_recorded_once_and_skips(
        tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        ext = FakeExecutor("openrouter", "external", default=Step(cost=0.01))
        async with running(db, tmp_path, [ext], {"v/m": "openrouter"}) as s:
            monkeypatch.setattr(s, "bench_for",
                                lane_loop_bomb(s, "external lane bug"))
            turn, reqs = await make_batch(db, ws, [("v/m", "external")] * 2,
                                          committed=0.01)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        statuses = sorted(sl["status"] for sl in done["slots"])
        assert statuses == ["landed", "skipped"]
        rows = await failures(db)
        # the lane's own except records it; the task watch sees a task that
        # ended cleanly — one row, not two
        assert [r["payload"]["site"] for r in rows] == \
            ["scheduler.external_lane"]


# --- listeners, probes, re-asks: recorded, folded, product path unchanged --


async def test_a_failing_listener_is_recorded_once_and_never_chased(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")

        async def broken(ev):
            raise RuntimeError("ui went away")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"klein": "comfy"},
                           on_event=broken) as s:
            turn, reqs = await make_batch(db, ws, [("klein", "local")] * 2)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"], until={"landed"})
        # stop() awaited the last finaliser's emit: nothing is left behind
        # the cursor, the record included
        assert s._emit_cursor == await db.latest_seq()
        assert all(sl["status"] == "landed" for sl in done["slots"])
        rows = await failures(db)
        # one cause, one row — the record is itself an event the listener
        # fails on, and that repeat folds instead of breeding another
        assert len(rows) == 1
        assert rows[0]["payload"]["site"] == "scheduler.on_event"
        assert rows[0]["payload"]["error"] == "RuntimeError: ui went away"


async def test_a_failing_gpu_probe_is_recorded_and_reads_as_no_card(
        tmp_path):
    async with opened(tmp_path) as db:
        async def probe():
            raise RuntimeError("system_stats parse")
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"klein": "comfy"},
                           gpu_probe=probe) as s:
            r1 = await s.gpu_check()
            r2 = await s.gpu_check()
        assert r1["reading"] is None and r2["reading"] is None
        rows = await failures(db)
        assert len(rows) == 1                     # the second folded
        assert rows[0]["payload"]["site"] == "scheduler.gpu_probe"


async def test_a_failing_bill_reask_is_recorded_with_the_slot_and_counts_as_no_answer(
        tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("B")
        ext = FakeExecutor("openrouter", "external", default=Step(
            outcome=EngineFailure("charged_blank", "billed, no image",
                                  charged_usd=0.04, job_ref="gen-1")))

        async def verify(job_ref):
            raise RuntimeError("provider record endpoint 5xx")
        ext.verify_charge = verify
        async with running(db, tmp_path, [ext], {"v/m": "openrouter"},
                           bill_check_gap_s=0.01) as s:
            turn, reqs = await make_batch(db, ws, [("v/m", "external")],
                                          committed=0.04)
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            await until(lambda: events_of(db, "slot.charge_unverified"))
        slot = done["slots"][0]
        unverified = (await events_of(db, "slot.charge_unverified"))[0]
        assert unverified["payload"]["reason"] == "bill_check"
        assert unverified["payload"]["asks"] == 3
        rows = await failures(db)
        assert len(rows) == 1                     # three asks, one cause
        assert rows[0]["payload"]["site"] == "scheduler.bill_check"
        assert rows[0]["slot_id"] == slot["id"]
        assert rows[0]["payload"]["engine"] == "v/m"
        assert rows[0]["payload"]["attempt"] == 1


# --- background tasks and tasks nobody awaited ------------------------------


async def test_a_background_task_that_dies_is_recorded_by_its_watch(
        tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        free = GpuReading(total=24 * GB, free=20 * GB, comfy=2 * GB,
                          foreign=0, name="test card")

        async def probe():
            return free
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"klein": "comfy"},
                           gpu_probe=probe, gpu_recheck_s=0.01) as s:
            await s.bench("lane:local", kind="gpu_held", cause="held")

            async def unbench(scope):
                raise RuntimeError("unbench exploded")
            monkeypatch.setattr(s, "unbench", unbench)
            s._start_gpu_recheck()
            await until(lambda: failures(db))
            await s.capture.drain()
        rows = await failures(db)
        assert len(rows) == 1
        p = rows[0]["payload"]
        assert p["site"] == "scheduler.task:gpu-recheck"
        assert p["error"] == "RuntimeError: unbench exploded"
        assert "CHECK AGAIN" in p["note"]


async def test_a_finaliser_dying_during_stop_is_recorded_not_swallowed(
        tmp_path):
    async with opened(tmp_path) as db:
        loc = FakeExecutor("comfy", "local")
        async with running(db, tmp_path, [loc], {"klein": "comfy"}) as s:
            async def doomed():
                raise RuntimeError("finaliser died")
            t = asyncio.create_task(doomed())
            s._shielded.add(t)
            await asyncio.sleep(0.01)
        rows = await failures(db)
        assert [r["payload"]["site"] for r in rows] == ["scheduler.finaliser"]


async def test_a_task_nobody_awaited_reaches_the_ledger_through_asyncios_hook(
        tmp_path):
    async with opened(tmp_path) as db:
        cap = Capture(db)
        loop = asyncio.get_running_loop()
        seen = []
        loop.set_exception_handler(lambda lp, ctx: seen.append(ctx))
        cap.install(loop)
        try:
            async def stray():
                raise RuntimeError("nobody awaited me")
            t = loop.create_task(stray(), name="stray-task")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert t.done()
            del t
            gc.collect()                      # "Task exception was never retrieved"
            await cap.drain()
            rows = await failures(db)
            assert len(rows) == 1
            p = rows[0]["payload"]
            assert p["site"] == "asyncio:stray-task"
            assert p["error"] == "RuntimeError: nobody awaited me"
            assert "never retrieved" in p["note"]
            assert len(seen) == 1             # the previous handler still ran
        finally:
            cap.uninstall()
        assert loop.get_exception_handler() is not None
        loop.set_exception_handler(None)


# --- a route crashing: 500 in words, the row named; refusals leave none ----


async def test_a_crashing_route_answers_in_words_and_names_the_row(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch,
                      raise_app_exceptions=False) as (client, _):
        db = wiring.current.db
        r = await client.post("/api/workspaces", json={"name": "A"})
        wid = r.json()["id"]

        async def boom(*a, **kw):
            raise RuntimeError("route bug")
        monkeypatch.setattr(db, "get_workspace", boom)
        r = await client.get(f"/api/workspaces/{wid}")
        assert r.status_code == 500
        body = r.json()
        assert not FORBIDDEN.search(body["detail"])
        assert "spent" in body["detail"]            # law 5: money stated
        rows = await failures(db)
        assert len(rows) == 1
        assert body["event_seq"] == rows[0]["seq"]
        assert f"event #{rows[0]['seq']}" in body["detail"]
        assert rows[0]["payload"]["site"] == \
            f"api GET /api/workspaces/{wid}"
        assert rows[0]["workspace_id"] == wid       # from the path
        assert "route bug" in rows[0]["payload"]["traceback"]


async def test_a_refusal_leaves_no_failure_row(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch,
                      raise_app_exceptions=False) as (client, _):
        r = await client.get("/api/workspaces/nope")
        assert r.status_code == 404
        r = await client.post("/api/workspaces", json={"name": 7})
        assert r.status_code == 422
        assert await failures(wiring.current.db) == []


# --- recovery failing at boot ----------------------------------------------


async def test_a_recovery_crash_is_recorded_then_fails_the_boot_loudly(
        tmp_path, monkeypatch):
    async with opened(tmp_path) as db:
        async def broken(*a, **kw):
            raise RuntimeError("pending_slots query broke")
        monkeypatch.setattr(db, "pending_slots", broken)
        s = Scheduler(db, [FakeExecutor("comfy", "local")],
                      route=lambda e: "comfy", staging_dir=tmp_path / "st")
        with pytest.raises(RuntimeError, match="pending_slots"):
            await s.start()
        await s.stop()
        rows = await failures(db)
        assert len(rows) == 1
        assert rows[0]["payload"]["site"] == "scheduler.start"
        assert "restart the cockpit" in rows[0]["payload"]["note"]


async def test_start_releases_the_store_when_the_boot_fails(
        tmp_path, monkeypatch):
    hermetic_env(tmp_path, monkeypatch)
    fakes = make_fakes()
    monkeypatch.setattr(wiring, "EXECUTOR_FACTORY",
                        lambda orc: list(fakes.values()))

    async def broken(self):
        raise RuntimeError("recovery broke")
    monkeypatch.setattr(Scheduler, "_recover", broken)
    closed = []
    real_close = Database.close

    async def close(self):
        closed.append(self.path)
        await real_close(self)
    monkeypatch.setattr(Database, "close", close)
    with pytest.raises(RuntimeError, match="recovery broke"):
        async with main.app.router.lifespan_context(main.app):
            pass
    assert wiring.current is None
    assert tmp_path / "v4.sqlite" in closed      # no leaked aiosqlite thread
