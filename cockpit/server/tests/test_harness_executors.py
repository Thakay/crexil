"""The directive-driven harness executor behind the Playwright law
suite. Pins the pieces the e2e leans on — FIFO directives that survive being
posted before OR after the lane picks the slot up, real PNG artefacts (the
/thumb route must work), lane-faithful cancel semantics, and the waiting
registry the specs use to synchronise."""

import asyncio
import io

import pytest
from PIL import Image

from app.executors import DispatchRequest, EngineFailure
from app.harness import (Directive, HarnessExecutor, HarnessRegistry,
                         REGISTRY, build_executors)

pytestmark = pytest.mark.anyio


def req(slot_id: str) -> DispatchRequest:
    return DispatchRequest(slot_id=slot_id, engine="raw-zimage")


async def test_directive_posted_before_wait_resolves_immediately():
    ex = HarnessExecutor("comfy", "local")
    REGISTRY.post("s-pre", Directive(outcome="land"))
    h = await ex.start(req("s-pre"))
    result = await asyncio.wait_for(ex.wait(h), 1)
    Image.open(io.BytesIO(result.data)).verify()   # a real, decodable PNG
    assert result.charged_usd is None
    assert result.external is None                 # local lands carry none


async def test_directive_posted_after_wait_releases_the_block():
    ex = HarnessExecutor("comfy", "local")
    h = await ex.start(req("s-post"))
    task = asyncio.create_task(ex.wait(h))
    await asyncio.sleep(0.05)
    assert not task.done()                         # genuinely blocked
    assert "s-post" in REGISTRY.waiting            # and visible to /state
    REGISTRY.post("s-post", Directive(outcome="land"))
    result = await asyncio.wait_for(task, 1)
    assert result.data.startswith(b"\x89PNG")
    assert "s-post" not in REGISTRY.waiting


async def test_fail_directive_raises_the_scripted_kind_and_message():
    ex = HarnessExecutor("comfy", "local")
    REGISTRY.post("s-fail", Directive(
        outcome="fail", kind="model_down", message="scripted: engine is down"))
    h = await ex.start(req("s-fail"))
    with pytest.raises(EngineFailure) as e:
        await asyncio.wait_for(ex.wait(h), 1)
    assert e.value.kind == "model_down"
    assert str(e.value) == "scripted: engine is down"


async def test_external_land_carries_charge_and_transport():
    ex = HarnessExecutor("openrouter", "external")
    REGISTRY.post("s-ext", Directive(outcome="land", charged_usd=0.04))
    h = await ex.start(req("s-ext"))
    result = await asyncio.wait_for(ex.wait(h), 1)
    assert result.charged_usd == 0.04
    assert result.external == {"transport": "harness"}


async def test_cancel_is_lane_faithful():
    # local: EngineFailure("interrupted") — the scheduler's cancelled=True path
    local = HarnessExecutor("comfy", "local")
    h = await local.start(req("s-cancel-l"))
    task = asyncio.create_task(local.wait(h))
    await asyncio.sleep(0.05)
    await local.cancel(h)
    with pytest.raises(EngineFailure) as e:
        await asyncio.wait_for(task, 1)
    assert e.value.kind == "interrupted"
    # external: CancelledError — a shot-down HTTP task (fakes.py semantics)
    ext = HarnessExecutor("openrouter", "external")
    h2 = await ext.start(req("s-cancel-e"))
    task2 = asyncio.create_task(ext.wait(h2))
    await asyncio.sleep(0.05)
    await ext.cancel(h2)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task2, 1)


async def test_fifo_feeds_reroll_attempts_in_order():
    reg = HarnessRegistry()
    reg.post("s-fifo", Directive(outcome="fail", kind="error", message="one"))
    reg.post("s-fifo", Directive(outcome="land"))
    cancelled = asyncio.Event()
    first = await reg.next_directive("s-fifo", cancelled)
    second = await reg.next_directive("s-fifo", cancelled)
    assert (first.outcome, first.message) == ("fail", "one")
    assert second.outcome == "land"


def test_build_executors_wears_the_real_names():
    by_name = {e.name: e.lane for e in build_executors()}
    assert by_name == {"comfy": "local", "openrouter": "external",
                       "region": "local",   # region takes, one directive
                       "mesh": "local"}     # the 3D lane's stand-in


# --- the trust-critical cases' fault injection -----------------------------


async def test_default_plates_carry_content_and_a_black_plate_is_blank():
    """Every scripted plate has an inner tone — a flat plate would read as
    A5a's empty frame to the real detector — and `color: [0,0,0]` IS that
    empty frame: the local lane fails it `blank` with the bytes kept."""
    ex = HarnessExecutor("comfy", "local")
    REGISTRY.post("s-plate", Directive(outcome="land", color=(40, 80, 120)))
    result = await asyncio.wait_for(ex.wait(await ex.start(req("s-plate"))), 1)
    im = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert max(hi - lo for lo, hi in im.getextrema()) > 4
    REGISTRY.post("s-black", Directive(outcome="land", color=(0, 0, 0)))
    with pytest.raises(EngineFailure) as e:
        await asyncio.wait_for(ex.wait(await ex.start(req("s-black"))), 1)
    assert e.value.kind == "blank"
    assert "black" in str(e.value)
    assert e.value.artefact is not None
    assert Image.open(io.BytesIO(e.value.artefact)).getextrema() == \
        ((0, 0), (0, 0), (0, 0))


async def test_an_external_black_plate_lands_the_check_is_the_local_lanes():
    ex = HarnessExecutor("openrouter", "external")
    REGISTRY.post("s-ext-black", Directive(outcome="land", color=(0, 0, 0),
                                           charged_usd=0.04))
    result = await asyncio.wait_for(
        ex.wait(await ex.start(req("s-ext-black"))), 1)
    assert result.charged_usd == 0.04


async def test_charged_blank_directive_carries_the_amount_id_and_bill_script():
    ex = HarnessExecutor("openrouter", "external")
    REGISTRY.post("s-b4", Directive(
        outcome="fail", kind="charged_blank", message="billed, no image",
        charged_usd=0.04, job_id="gen-h", bill=[None, 0.05]))
    with pytest.raises(EngineFailure) as e:
        await asyncio.wait_for(ex.wait(await ex.start(req("s-b4"))), 1)
    assert e.value.kind == "charged_blank"
    assert e.value.charged_usd == 0.04 and e.value.job_ref == "gen-h"
    assert await ex.verify_charge("gen-h") is None       # not ready yet
    assert await ex.verify_charge("gen-h") == 0.05
    assert await ex.verify_charge("gen-h") is None       # script exhausted
    assert await ex.verify_charge("unknown") is None
