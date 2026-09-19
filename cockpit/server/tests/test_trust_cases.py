"""The six trust-critical cases (one per failure class), the server's
half of the fault-injection suite. One section per case:

  A5a  blank local output       — detected at the executor, absolved on
                                  the face, bytes kept, same-seed rerun
  B4   charged, nothing returned — its own kind, the amount in the ledger,
                                  three free bill re-asks (never a
                                  re-generate), the receipt's job id on
                                  the row
  C6   cancel mid-batch          — the three acts in one call (cited:
                                  test_scheduler / test_api_v4 own the
                                  breath; the mixed batch is here)
  D5   broken promote target     — tests/test_promote.py
  E5   prompt over token limit   — the server never truncates and never
                                  refuses; the warning is the web's
  F5   drag released over nothing — cockpit/web/e2e/drag.spec.ts

The rendered halves — remedies, receipt, the stop-the-queue breath, the
token warning — live in cockpit/web (grid.test.ts, tokens.test.ts,
TurnGrid.test.tsx, Composer.test.tsx) and e2e/trust-cases.spec.ts drives
all of it against the real stack over the scripted-executor harness.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image

from app.executors import EngineFailure, blank_frame
from app.scheduler import BILL_CHECK_ASKS, plain_cause
from fakes import FakeExecutor, Step
from test_api_v4 import (MODEL, booted, make_ws, run, settle_turn)
from test_scheduler import (events_of, make_batch, opened, running, settle,
                            wait_until_running)

pytestmark = pytest.mark.anyio


def plate(color, size=(64, 64), inner=None) -> bytes:
    im = Image.new("RGB", size, color)
    if inner is not None:
        im.paste(inner, (16, 16, 48, 48))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# --- A5a: blank or black frame from a local engine --------------------------


def test_a5a_the_detector_knows_an_empty_frame_from_a_dark_one():
    assert "black" in blank_frame(plate((0, 0, 0)))
    assert "white" in blank_frame(plate((255, 255, 255)))
    assert "one flat colour" in blank_frame(plate((40, 80, 120)))
    assert "64×64" in blank_frame(plate((2, 1, 3)))      # within levels
    # a dark frame with content is content
    assert blank_frame(plate((0, 0, 0), inner=(0, 0, 40))) is None
    assert blank_frame(plate((10, 10, 10), inner=(10, 30, 10))) is None
    # bytes that aren't an image are a different failure, not a blank
    assert blank_frame(b"not a png") is None
    assert blank_frame(b"") is None


def test_a5a_the_face_absolves_the_prompt_in_words():
    line = plain_cause("blank", lane="local", engine="raw-zimage")
    assert line.startswith("raw-zimage finished, but the frame came back empty")
    assert "not your prompt" in line


async def test_a5a_a_blank_take_fails_red_keeps_its_bytes_and_benches_nothing(
        tmp_path):
    """The engine said success and handed back nothing usable: the slot
    fails (never lands: a cell is judgeable or it is failed), the
    kind is on the row for the face's remedy, the empty frame is kept
    beside where the take would have landed and named in DETAILS (law
    7), and nothing else is touched — no bench, no automatic retry."""
    empty = plate((0, 0, 0))
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("A")
        loc = FakeExecutor("comfy", "local", default=Step(
            outcome=EngineFailure(
                "blank", "the frame came back empty — every pixel black",
                detail="ComfyUI output x.png: every pixel black",
                artefact=empty)))
        async with running(db, tmp_path, [loc], {"raw-zimage": "comfy"}) as s:
            turn, reqs = await make_batch(
                db, ws, [("raw-zimage", "local"), ("raw-zimage", "local")])
            await s.submit_turn(turn["id"], reqs)
            done = await settle(db, turn["id"])
            assert s.snapshot()["benches"] == {}
        for slot in done["slots"]:
            assert slot["status"] == "failed"
            assert slot["kind"] == "blank"
            assert slot["cause"] == plain_cause("blank", lane="local",
                                                engine="raw-zimage")
            assert "every pixel black" in slot["detail"]
            kept = [ln for ln in slot["detail"].splitlines()
                    if ln.startswith("the empty frame is kept at ")]
            assert len(kept) == 1
            path = kept[0].removeprefix("the empty frame is kept at ")
            assert Path(path).read_bytes() == empty
            assert slot["attempt"] == 1                 # no silent retry
        assert loc.starts == [s["id"] for s in done["slots"]]
        assert await events_of(db, "engine.benched") == []
        assert await events_of(db, "slot.retrying") == []
        nodes = await db.list_nodes(ws["id"])
        assert nodes == []                              # nothing landed


async def test_a5a_run_again_same_seed_keeps_the_seed_and_a_plain_reroll_rolls(
        tmp_path, monkeypatch):
    """The remedy: the same slot, engine, dials AND seed (the settings
    were fine — the engine wasn't). The default reroll still rolls."""
    fakes = {"comfy": FakeExecutor("comfy", "local"),
             "openrouter": FakeExecutor("openrouter", "external")}
    fakes["comfy"].default = Step(outcome=EngineFailure(
        "blank", "empty", artefact=plate((0, 0, 0))))
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        turn = await settle_turn(out["turn"]["id"])
        slot = turn["slots"][0]
        assert slot["status"] == "failed" and slot["kind"] == "blank"
        seed = slot["seed"]
        assert isinstance(seed, int)
        fakes["comfy"].default = Step()
        r = await client.post(f"/api/slots/{slot['id']}/reroll",
                              json={"keep_seed": True})
        assert r.status_code == 200, r.text
        assert r.json()["slot"]["seed"] == seed
        assert r.json()["slot"]["kind"] is None          # the face is clean
        again = await settle_turn(turn["id"], until={"landed"})
        assert again["slots"][0]["seed"] == seed
        assert again["slots"][0]["submitted_graph"]["2"]["inputs"]["seed"] \
            == seed
        r = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert r.status_code == 200, r.text
        assert r.json()["slot"]["seed"] != seed


# --- B4: charged but no image returned --------------------------------------


def test_b4_the_face_says_billed_and_nothing_back():
    line = plain_cause("charged_blank", lane="external",
                       engine="openai/gpt-image-2")
    assert line == ("openai/gpt-image-2 took the job and billed it, but no "
                    "image came back")


async def _charged_blank_batch(db, tmp_path, ext, *, job="gen-abc",
                               reported=0.04, gap=0.01):
    ws = await db.create_workspace("B")
    ext.default = Step(outcome=EngineFailure(
        "charged_blank", "billed 0.04 USD but the response carried no image",
        charged_usd=reported, job_ref=job))
    async with running(db, tmp_path, [ext], {"vendor/m": "openrouter"},
                       bill_check_gap_s=gap) as s:
        turn, reqs = await make_batch(db, ws, [("vendor/m", "external")],
                                      committed=0.04)
        await s.submit_turn(turn["id"], reqs)
        done = await settle(db, turn["id"])
        # the bill check runs AFTER the failure; give it its asks
        for _ in range(500):
            if not s.snapshot()["checks"] and (
                    await events_of(db, "slot.charge_verified")
                    or await events_of(db, "slot.charge_unverified")):
                break
            await asyncio.sleep(0.01)
        snap = s.snapshot()
    return ws, done, snap


async def test_b4_the_amount_enters_the_ledger_once_and_the_job_id_lands(
        tmp_path):
    async with opened(tmp_path) as db:
        ext = FakeExecutor("openrouter", "external")
        ext.bills["gen-abc"] = [0.04]
        ws, done, _ = await _charged_blank_batch(db, tmp_path, ext)
        slot = done["slots"][0]
        assert slot["status"] == "failed"
        assert slot["kind"] == "charged_blank"
        assert slot["charged_usd"] == 0.04             # the response's word
        assert slot["committed_usd"] == 0.04
        assert slot["job_ref"] == "gen-abc"            # the receipt's line
        assert slot["cause"] == plain_cause("charged_blank", lane="external",
                                            engine="vendor/m")
        assert await db.workspace_charged(ws["id"]) == 0.04
        failed = await events_of(db, "slot.failed")
        assert failed[0]["charged_usd"] == 0.04
        assert failed[0]["payload"]["kind"] == "charged_blank"
        # never a re-generate: one dispatch, ever
        assert ext.starts == [slot["id"]]
        assert await events_of(db, "slot.retrying") == []
        assert await events_of(db, "engine.benched") == []


async def test_b4_three_free_asks_then_the_amount_stays_as_reported(tmp_path):
    """Silence three times over: every ask is an event, the check was
    visible in the snapshot while it ran, and the give-up is recorded as
    charge_unverified — the ledger keeps the response's amount."""
    async with opened(tmp_path) as db:
        ext = FakeExecutor("openrouter", "external")   # no answers scripted
        ws, done, snap = await _charged_blank_batch(db, tmp_path, ext)
        assert ext.asks == ["gen-abc"] * BILL_CHECK_ASKS
        asks = await events_of(db, "slot.bill_check")
        assert [e["payload"]["ask"] for e in asks] == [1, 2, 3]
        assert all(e["payload"]["of"] == 3 for e in asks)
        unv = await events_of(db, "slot.charge_unverified")
        assert len(unv) == 1
        assert unv[0]["payload"]["reason"] == "bill_check"
        assert unv[0]["payload"]["asks"] == 3
        assert unv[0]["payload"]["charged_usd"] == 0.04
        assert await events_of(db, "slot.charge_verified") == []
        assert await db.workspace_charged(ws["id"]) == 0.04
        assert snap["checks"] == {}                     # done, not stuck


async def test_b4_the_check_is_visible_while_it_asks(tmp_path):
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("B")
        ext = FakeExecutor("openrouter", "external")
        ext.default = Step(outcome=EngineFailure(
            "charged_blank", "billed", charged_usd=0.04, job_ref="gen-1"))
        async with running(db, tmp_path, [ext], {"vendor/m": "openrouter"},
                           bill_check_gap_s=0.5) as s:
            turn, reqs = await make_batch(db, ws, [("vendor/m", "external")],
                                          committed=0.04)
            await s.submit_turn(turn["id"], reqs)
            sid = turn["slots"][0]["id"]
            seen = None
            for _ in range(300):
                checks = s.snapshot()["checks"]
                if sid in checks:
                    seen = checks[sid]
                    break
                await asyncio.sleep(0.01)
            assert seen == {"ask": 1, "of": 3}
            # stop() takes the check down with everything else; the asks
            # so far are in the ledger
        assert len(await events_of(db, "slot.bill_check")) >= 1


async def test_b4_an_answer_that_differs_corrects_the_ledger_once(tmp_path):
    async with opened(tmp_path) as db:
        ext = FakeExecutor("openrouter", "external")
        ext.bills["gen-abc"] = [None, 0.05]           # ready on the 2nd ask
        ws, done, _ = await _charged_blank_batch(db, tmp_path, ext)
        assert ext.asks == ["gen-abc", "gen-abc"]     # stopped once answered
        ver = await events_of(db, "slot.charge_verified")
        assert len(ver) == 1
        assert ver[0]["payload"] == {"charged_usd": 0.05,
                                     "reported_usd": 0.04, "asks": 2,
                                     "job_ref": "gen-abc"}
        charged = await events_of(db, "slot.charged")
        assert len(charged) == 1 and charged[0]["charged_usd"] == 0.01
        assert "bill check" in charged[0]["payload"]["note"]
        assert await db.workspace_charged(ws["id"]) == 0.05
        assert (await db.get_slot(done["slots"][0]["id"]))["charged_usd"] \
            == 0.05
        assert await events_of(db, "slot.charge_unverified") == []


async def test_b4_an_answer_that_agrees_touches_the_ledger_not_at_all(
        tmp_path):
    async with opened(tmp_path) as db:
        ext = FakeExecutor("openrouter", "external")
        ext.bills["gen-abc"] = [0.04]
        ws, _, _ = await _charged_blank_batch(db, tmp_path, ext)
        assert len(await events_of(db, "slot.charge_verified")) == 1
        assert await events_of(db, "slot.charged") == []
        assert await db.workspace_charged(ws["id"]) == 0.04


async def test_b4_without_a_job_id_the_receipt_is_all_there_is(tmp_path):
    async with opened(tmp_path) as db:
        ext = FakeExecutor("openrouter", "external")
        ws, done, _ = await _charged_blank_batch(db, tmp_path, ext, job=None)
        assert ext.asks == []
        assert await events_of(db, "slot.bill_check") == []
        unv = await events_of(db, "slot.charge_unverified")
        assert len(unv) == 1 and unv[0]["payload"]["reason"] == "no_job_id"
        assert unv[0]["payload"]["charged_usd"] == 0.04
        # the row keeps the executor's own handle (the fake stamps one at
        # start; OpenRouter's is None) — the response gave no generation id
        assert await db.workspace_charged(ws["id"]) == 0.04


async def test_b4_through_the_api_the_row_carries_kind_job_ref_and_the_check(
        tmp_path, monkeypatch):
    fakes = {"comfy": FakeExecutor("comfy", "local"),
             "openrouter": FakeExecutor("openrouter", "external")}
    fakes["openrouter"].default = Step(outcome=EngineFailure(
        "charged_blank", "billed", charged_usd=0.05, job_ref="gen-api"))
    fakes["openrouter"].bills["gen-api"] = [None, None, 0.05]
    monkeypatch.setattr("app.config.BILL_CHECK_GAP_S", 0.2)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"])
        sid = turn["slots"][0]["id"]
        seen = None
        for _ in range(200):
            q = (await client.get("/api/queue")).json()
            if q["checks"]:
                seen = q["checks"][0]
                break
            await asyncio.sleep(0.01)
        assert seen is not None and seen["slot_id"] == sid
        assert seen["of"] == 3 and seen["ask"] >= 1
        detail = (await client.get(f"/api/workspaces/{ws['id']}")).json()
        slot = detail["turns"][0]["slots"][0]
        assert slot["kind"] == "charged_blank"
        assert slot["job_ref"] == "gen-api"
        assert slot["charged_usd"] == 0.05
        assert slot["cause"].endswith("no image came back")
        for _ in range(300):
            q = (await client.get("/api/queue")).json()
            if not q["checks"]:
                break
            await asyncio.sleep(0.02)
        assert q["checks"] == []


# --- C6: cancel mid-batch — one call, three acts ---------------------------


async def test_c6_one_stop_is_three_acts_and_landed_work_is_never_touched(
        tmp_path):
    """A mixed batch with a landed external, a running local, a queued
    local and an external in flight: landed stays (the artist's work),
    the queued local is skipped free, the running local is abandoned with
    the seconds on record, the in-flight external rests late with its
    amount stated once — and if its ride still delivers, it lands
    into its own cell."""
    async with opened(tmp_path) as db:
        ws = await db.create_workspace("C")
        gate = asyncio.Event()
        ride = asyncio.Event()
        loc = FakeExecutor("comfy", "local", default=Step(gate=gate))
        ext = FakeExecutor("openrouter", "external")
        routes = {"raw-zimage": "comfy", "vendor/m": "openrouter"}
        async with running(db, tmp_path, [loc, ext], routes) as s:
            turn, reqs = await make_batch(
                db, ws, [("vendor/m", "external"), ("raw-zimage", "local"),
                         ("raw-zimage", "local"), ("vendor/m", "external")],
                committed=0.04)
            fast, slow = turn["slots"][0]["id"], turn["slots"][3]["id"]
            ext.script[fast] = Step(cost=0.04)
            ext.script[slow] = Step(gate=ride, cost=0.04)
            await s.submit_turn(turn["id"], reqs)
            for _ in range(500):
                st = await db.get_slot(fast)
                if st["status"] == "landed":
                    break
                await asyncio.sleep(0.01)
            await wait_until_running(db, turn["slots"][1]["id"])
            await wait_until_running(db, slow)
            summary = await s.cancel_turn(turn["id"])
            assert summary == {"kept": 1, "skipped": 1, "interrupted": 1,
                               "in_flight": [{"slot_id": slow,
                                              "committed_usd": 0.04}]}
            gate.set()
            mid = await settle(db, turn["id"], until={"landed", "failed",
                                                      "skipped", "late"})
            by = {sl["pos"]: sl for sl in mid["slots"]}
            assert by[0]["status"] == "landed" and by[0]["charged_usd"] == 0.04
            assert by[1]["status"] == "failed" and by[1]["cause"] == "cancelled"
            assert by[1]["kind"] == "interrupted"
            assert by[1]["started_at"] and by[1]["finished_at"]
            assert by[2]["status"] == "skipped"
            assert by[3]["status"] == "late" and by[3]["committed_usd"] == 0.04
            ride.set()                                  # the ride delivers
            done = await settle(db, turn["id"], until={"landed", "failed",
                                                       "skipped"})
        by = {sl["pos"]: sl for sl in done["slots"]}
        assert by[3]["status"] == "landed"
        assert [sl["pos"] for sl in done["slots"]] == [0, 1, 2, 3]  # law 8
        nodes = await db.list_nodes(ws["id"])
        assert sorted(n["slot_id"] for n in nodes) == sorted([fast, slow])
        assert await db.workspace_charged(ws["id"]) == 0.08
        skipped = await events_of(db, "slot.skipped")
        assert skipped[0]["payload"]["note"] == "nothing spent"
        assert len(await events_of(db, "slot.late_landed")) == 1


# --- E5: prompt over an engine's token limit -------------------------------


async def test_e5_the_server_never_truncates_and_never_refuses_a_long_prompt(
        tmp_path, monkeypatch):
    """The warning is the web's, per slot, before Run (tokens.ts). The
    server's whole duty is to pass the prompt through WHOLE — a silently
    shortened prompt would corrupt the comparison the batch exists to
    make — and never to 422 on length."""
    long_prompt = " ".join(f"word{i}" for i in range(600))   # ≫ 77 tokens
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"], prompt=long_prompt,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        assert turn["prompt"] == long_prompt
        local = next(s for s in turn["slots"] if s["engine"] == "testpreset")
        assert local["submitted_graph"]["1"]["inputs"]["text"] == long_prompt
        ext = next(s for s in turn["slots"] if s["engine"] == MODEL)
        assert ext["external"]["prompt"] == long_prompt
        assert all(s["status"] == "landed" for s in turn["slots"])
