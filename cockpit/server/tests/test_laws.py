"""Laws 1 to 10 of docs/PRD.md as enforced invariants: the
server's half, one section per law, each naming the mechanism that holds it.

Laws 2 · 4 · 5 · 6 are rendered laws and live in cockpit/web — `grid.test.ts`
(costLine / remedies / causeGroups), `TurnGrid.test.tsx`, `laws.test.ts` (the
no-modal scan) — and in `e2e/batch-laws.spec.ts` against the real stack.
Laws already pinned elsewhere are cited, not re-proven: test_scheduler.py
(benches, retries, the slot set), test_db.py (node/event triggers).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

from app.executors import EngineFailure
from app.scheduler import (INTERNAL_CAUSE, ORPHAN_COPY, Scheduler,
                           bench_scope_for, plain_cause, raw_detail)
from fakes import FakeExecutor, Step, fail
from test_scheduler import (FAST_RETRY, events_of, make_batch, opened,
                            running, settle)

pytestmark = pytest.mark.anyio

APP = Path(__file__).resolve().parents[1] / "app"

# Every kind the executors raise, plus the scheduler's own two and one the
# table has never heard of — a future executor must not be able to leak.
KINDS = ["dead_key", "moderation", "rate_limit", "model_down", "timeout",
         "unreachable", "interrupted", "error", "blank", "charged_blank",
         "oom", "internal", "restart", "some_new_kind"]

# Law 3's letter: no status codes, no stack traces, no "something went
# wrong". Braces catch a JSON dump, `Exception`/`Traceback`/`0x` a repr.
FORBIDDEN = re.compile(
    r"HTTP|\b[45]\d\d\b|Traceback|Exception|something went wrong|0x[0-9a-f]"
    r"|[{}\[\]]", re.I)


async def mk_ws(db):
    return await db.create_workspace("laws")


# --- law 1: failures are per slot; messages are per cause ------------------


async def test_law_1_one_cause_folds_to_one_record_and_identical_plain_lines(
        tmp_path):
    """Nine slots killed by one unplugged cable produce one message: the
    bench is the one cause record (test_scheduler proves the holding), and
    every slot that died of it carries the SAME plain line — the key the
    grid folds on — with the raw text apart, in `detail`."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        ext = FakeExecutor("openrouter", "external",
                           default=Step(outcome=fail(
                               "dead_key", "Unauthorized (HTTP 401)")))
        loc = FakeExecutor("comfy", "local")
        routes = {"vendor/a": "openrouter", "vendor/b": "openrouter",
                  "raw-flux": "comfy"}
        async with running(db, tmp_path, [ext, loc], routes) as sched:
            turn, reqs = await make_batch(
                db, ws, [("vendor/a", "external"), ("vendor/a", "external"),
                         ("vendor/b", "external"), ("raw-flux", "local")])
            await sched.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            benches = sched.snapshot()["benches"]
        assert list(benches) == ["lane:external"]      # one record
        assert benches["lane:external"]["cause"] == plain_cause(
            "dead_key", lane="external", engine="vendor/a")
        failed = [s for s in done["slots"] if s["status"] == "failed"]
        held = [s for s in done["slots"] if s["status"] == "held"]
        assert len(failed) >= 1 and len(failed) + len(held) == 3
        assert len({s["cause"] for s in failed}) == 1     # foldable
        for s in failed:
            assert not FORBIDDEN.search(s["cause"])
            assert "HTTP 401" in s["detail"]
        # the local slot never heard about it (law 1's first half)
        local = next(s for s in done["slots"] if s["engine"] == "raw-flux")
        assert local["status"] == "landed"


# --- law 3: no status codes, no stack traces, no "something went wrong" ---


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("lane", ["local", "external"])
def test_law_3_every_kind_reads_plainly_and_names_the_engine(kind, lane):
    engine = "openai/gpt-image-2" if lane == "external" else "raw-zimage"
    line = plain_cause(kind, lane=lane, engine=engine)
    assert not FORBIDDEN.search(line), line
    # the line names who failed: the engine, or the transport that dropped
    assert engine in line or "OpenRouter" in line or "ComfyUI" in line
    assert line == line.strip() and len(line) < 140


def test_law_3_the_schedulers_own_lines_read_plainly():
    for line in (INTERNAL_CAUSE, ORPHAN_COPY):
        assert not FORBIDDEN.search(line), line


def test_law_3_raw_detail_keeps_what_the_engine_said_and_stays_bounded():
    e = EngineFailure("error", "x" * 2000, status=500, detail="y" * 2000)
    d = raw_detail(e)
    assert d.startswith("error: xxx") and len(d) == 1600
    e2 = EngineFailure("timeout", "no result within 300s")
    assert raw_detail(e2) == "timeout: no result within 300s"


async def test_law_3_the_face_is_plain_and_the_raw_text_rides_detail(
        tmp_path):
    """What the engine actually said — status codes, JSON node messages —
    never reaches `cause`; it lands in `detail` on the row and in the
    slot.failed event with its kind, permanently."""
    raw_local = ('execution error: [{"exception_type": "OutOfMemoryError"}]'
                 " (HTTP 500)")
    raw_ext = "Request flagged by moderation (HTTP 403)"
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        loc = FakeExecutor("comfy", "local",
                           default=Step(outcome=EngineFailure(
                               "error", raw_local,
                               detail='{"node": "KSampler"}')))
        ext = FakeExecutor("openrouter", "external",
                           default=Step(outcome=fail("moderation", raw_ext)))
        routes = {"raw-zimage": "comfy", "vendor/m": "openrouter"}
        async with running(db, tmp_path, [loc, ext], routes) as sched:
            turn, reqs = await make_batch(
                db, ws, [("raw-zimage", "local"), ("vendor/m", "external")])
            await sched.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        by_engine = {s["engine"]: s for s in done["slots"]}
        local, external = by_engine["raw-zimage"], by_engine["vendor/m"]
        assert local["status"] == external["status"] == "failed"
        assert local["cause"] == plain_cause("error", lane="local",
                                             engine="raw-zimage")
        assert "not your prompt" in local["cause"]
        assert external["cause"] == plain_cause("moderation", lane="external",
                                                engine="vendor/m")
        for s in (local, external):
            assert not FORBIDDEN.search(s["cause"])
        assert raw_local in local["detail"]
        assert '{"node": "KSampler"}' in local["detail"]
        assert raw_ext in external["detail"]
        # the record: cause + kind + detail, forever
        failed = {e["slot_id"]: e for e in await events_of(db, "slot.failed")}
        assert failed[local["id"]]["payload"]["kind"] == "error"
        assert failed[local["id"]]["payload"]["cause"] == local["cause"]
        assert raw_local in failed[local["id"]]["payload"]["detail"]
        assert failed[external["id"]]["payload"]["kind"] == "moderation"


async def test_law_3_an_internal_crash_still_speaks_plainly(tmp_path,
                                                            monkeypatch):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        loc = FakeExecutor("comfy", "local")

        async def boom(_req):
            raise RuntimeError("Traceback (most recent call last): ...")
        loc.start = boom
        async with running(db, tmp_path, [loc], {"raw-zimage": "comfy"}) as s:
            turn, reqs = await make_batch(db, ws, [("raw-zimage", "local")])
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
        slot = done["slots"][0]
        assert slot["status"] == "failed"
        assert slot["cause"] == INTERNAL_CAUSE
        assert not FORBIDDEN.search(slot["cause"])
        # DETAILS names the error and the ledger row that holds its
        # traceback — not a log the artist can't open (test_capture.py)
        assert "RuntimeError" in slot["detail"]
        assert "recorded as event #" in slot["detail"]


# --- law 7: nothing is silently lost ---------------------------------------


async def test_law_7_turns_and_slots_cannot_be_hard_deleted(tmp_path):
    """Nodes and events refuse DELETE by trigger (test_db); a turn holds
    the prompt and a slot the seed and every attempt — they refuse too."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "a prompt worth keeping", {}, [{"engine": "e"}])
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            await db.conn.execute("DELETE FROM slots WHERE id=?",
                                  (turn["slots"][0]["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            await db.conn.execute("DELETE FROM turns WHERE id=?",
                                  (turn["id"],))
        assert (await db.get_turn(turn["id"]))["prompt"] == \
            "a prompt worth keeping"


V4_MODULES = ["db.py", "scheduler.py", "api_v4.py", "api_v4_regions.py",
              "api_v4_promote.py", "executors.py", "harness.py", "wiring.py"]


def test_law_7_purge_is_the_only_delete_verb_in_the_v4_stack():
    """Static: across the v4 modules the only file removal is purge's
    `_unlink_all`, and the store's only hard DELETEs are the FTS mirror and
    the settings strip (never a node, prompt, seed, take or region)."""
    unlinks = {}
    for name in V4_MODULES:
        text = (APP / name).read_text()
        hits = re.findall(r"\.unlink\(|os\.remove\(|rmtree\(", text)
        if hits:
            unlinks[name] = len(hits)
    assert unlinks == {"api_v4.py": 1}
    src = (APP / "api_v4.py").read_text()
    fn = src[src.index("def _unlink_all"):]
    fn = fn[:fn.index("\n\n\n")]
    assert ".unlink(" in fn                # and nowhere else in the file
    assert src.count(".unlink(") == 1
    tables = re.findall(r"DELETE FROM (\w+)", (APP / "db.py").read_text())
    # `meta` is the store's settings strip, not the record — clearing
    # `promote_dir` puts the promote target back on .env's default.
    # A row here has never been a node, a prompt, a seed or a take.
    assert set(tables) == {"node_fts", "meta"}


# --- law 8: grid geometry is frozen at Run ---------------------------------


async def test_law_8_a_cell_never_moves_changes_turn_or_engine(tmp_path):
    """DDL holds it: `UNIQUE(turn_id, pos)` plus a trigger that refuses any
    UPDATE of pos / turn_id / engine. The scheduler's half — the request
    set must equal the slot set — is test_scheduler's."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        t1 = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "a"}, {"engine": "b"}])
        t2 = await db.create_turn_with_slots(ws["id"], "q", {},
                                             [{"engine": "a"}])
        sid = t1["slots"][0]["id"]
        for column, value in (("pos", 5), ("engine", "'c'"),
                              ("turn_id", f"'{t2['id']}'")):
            with pytest.raises(sqlite3.IntegrityError, match="frozen"):
                await db.conn.execute(
                    f"UPDATE slots SET {column}={value} WHERE id=?", (sid,))
        slot = await db.get_slot(sid)
        assert (slot["pos"], slot["engine"], slot["turn_id"]) == \
            (0, "a", t1["id"])
        # contents still move: status, attempts, money, the face
        await db.dispatch_slot(sid)
        await db.fail_slot(sid, cause="x")
        assert (await db.get_slot(sid))["status"] == "failed"


# --- law 9: nothing retries silently ---------------------------------------


async def test_law_9_a_retry_marks_its_slot_while_it_runs(tmp_path):
    """The retry is an event (test_scheduler) AND a mark on the row: while
    attempt 2 is queued or running, `retry_of` names the kind it answers,
    the old face is clean (cause/detail NULL), and the attempt count says
    2 — so the cell can say RETRYING rather than looking like a fresh
    take. Landing leaves the record its scar; the face shows none."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        gate = asyncio.Event()
        ext = FakeExecutor("openrouter", "external")
        async with running(db, tmp_path, [ext], {"vendor/m": "openrouter"},
                           retry_policy=FAST_RETRY) as sched:
            turn, reqs = await make_batch(db, ws, [("vendor/m", "external")],
                                          committed=0.04)
            sid = turn["slots"][0]["id"]
            ext.script[sid] = [
                Step(outcome=fail("rate_limit", "slow down (HTTP 429)")),
                Step(gate=gate, cost=0.04)]
            await sched.submit_turn(turn["id"], reqs)
            for _ in range(500):
                slot = await db.get_slot(sid)
                if slot["status"] == "running" and slot["attempt"] == 2:
                    break
                await asyncio.sleep(0.01)
            assert slot["attempt"] == 2
            assert slot["retry_of"] == "rate_limit"
            assert slot["cause"] is None and slot["detail"] is None
            gate.set()
            done = await settle(db, turn["id"])
        slot = done["slots"][0]
        assert slot["status"] == "landed"
        assert slot["retry_of"] == "rate_limit"      # the record, not a scar
        assert [e["payload"]["retry_of"] for e in
                await events_of(db, "slot.queued")] == ["rate_limit"]


async def test_law_9_a_manual_reroll_is_not_a_retry(tmp_path):
    """Reroll is the artist's verb: it clears the mark, and a fresh attempt
    born of it never wears RETRYING."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "vendor/m",
                                 "external": {"model": "vendor/m"}}])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid)
        await db.transition_slot(sid, "queued", payload={"retry_of": "x"},
                                 retry_of="rate_limit")
        assert (await db.get_slot(sid))["retry_of"] == "rate_limit"
        await db.dispatch_slot(sid)
        assert (await db.get_slot(sid))["retry_of"] == "rate_limit"
        await db.fail_slot(sid, cause="c", detail="d", kind="error")
        fresh = await db.reroll_slot(sid, 99)
        assert fresh["retry_of"] is None
        assert fresh["cause"] is None and fresh["detail"] is None
        await db.dispatch_slot(sid)
        assert (await db.get_slot(sid))["retry_of"] is None


# --- law 10: the queue pauses only when every remaining slot must fail ----


@pytest.mark.parametrize("lane", ["local", "external"])
def test_law_10_a_bench_never_reaches_beyond_what_is_guaranteed_to_fail(lane):
    """The whole table, both lanes: a dead key kills every external take
    (one key), an unreachable transport kills its lane, a down or silent
    engine kills its own takes — and nothing else pauses anything."""
    eng = "x"
    assert bench_scope_for("dead_key", lane, eng) == "lane:external"
    assert bench_scope_for("unreachable", lane, eng) == f"lane:{lane}"
    assert bench_scope_for("model_down", lane, eng) == f"engine:{eng}"
    assert bench_scope_for("timeout", lane, eng) == f"engine:{eng}"
    for kind in ("moderation", "rate_limit", "interrupted", "error",
                 "blank", "charged_blank", "oom", "internal", "restart",
                 "some_new_kind"):
        assert bench_scope_for(kind, lane, eng) is None, kind


async def test_law_10_there_is_no_scope_wider_than_a_lane(tmp_path):
    """No "all" scope exists: `_scope_covers` knows engine: and lane: and
    nothing else, so nothing can pause both lanes with one record."""
    async with opened(tmp_path) as db:
        loc = FakeExecutor("comfy", "local")
        ext = FakeExecutor("openrouter", "external")
        routes = {"raw": "comfy", "v/m": "openrouter"}
        sched = Scheduler(db, [loc, ext], route=lambda e: routes[e],
                          staging_dir=tmp_path / "s")
        assert sched._scope_covers("engine:raw", "raw")
        assert not sched._scope_covers("engine:raw", "v/m")
        assert sched._scope_covers("lane:local", "raw")
        assert not sched._scope_covers("lane:local", "v/m")
        for scope in ("all", "*", "everything", "lane:*", ""):
            assert not sched._scope_covers(scope, "raw")
            assert not sched._scope_covers(scope, "v/m")
