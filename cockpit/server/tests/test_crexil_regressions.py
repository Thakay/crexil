"""Shared execution remains bounded across mutation and delivery races."""

import asyncio
import copy
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app import config, crexil_execution, wiring
from app.executors import ComfyExecutor, EngineFailure, GpuReading
from app.openrouter import OpenRouterClient
from fakes import Step
from test_api_v4 import booted, make_fakes, store
from test_crexil import command, image_bytes, imported, project
from test_crexil_execution import attest, draft, reviewed, settled
from test_executors import FakeComfy, SUCCESS_ENTRY

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    async def empty_catalogue(_client):
        return []

    async def forbidden_network(_transport, request):
        raise AssertionError(f"Regression test attempted HTTP: {request.url}")

    monkeypatch.setattr(OpenRouterClient, "list_models", empty_catalogue)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_network)
    monkeypatch.setattr(config, "COMFY_POLL_S", 0.001)
    monkeypatch.setattr(config, "GPU_RECHECK_S", 0.01)


async def test_gpu_held_oom_cannot_exceed_reviewed_attempt_count(tmp_path, monkeypatch):
    held = True

    async def gpu_reading():
        return GpuReading(total=24 << 30, free=(1 if held else 20) << 30,
                          comfy=1 << 30, foreign=(22 if held else 0) << 30)

    monkeypatch.setattr(wiring, "GPU_PROBE", gpu_reading)
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        p = await project(client)
        direction = await draft(client, p["id"], attempt_limit=2)
        review = await reviewed(client, direction)
        assert review["ready"] and review["attempts"] == 2
        scheduler = wiring.current.scheduler
        adopt = scheduler.adopt_prepared_turn
        failed_slot = None

        async def script_attempts(turn_id):
            nonlocal failed_slot
            turn = await store().get_turn(turn_id)
            failed_slot = turn["slots"][0]["id"]
            fakes["comfy"].script[failed_slot] = [
                Step(outcome=EngineFailure("oom", "CUDA out of memory")),
                Step(data=image_bytes()),
            ]
            fakes["comfy"].default = Step(data=image_bytes())
            await adopt(turn_id)

        monkeypatch.setattr(scheduler, "adopt_prepared_turn", script_attempts)
        response = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                     json=command(revision=0, review_token=review["token"]))
        assert response.status_code == 201, response.text
        for _ in range(100):
            events = await store().slot_events(failed_slot)
            if any(e["type"] in ("slot.failed", "slot.retrying") for e in events):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("The first OOM attempt did not produce a recorded outcome")
        # Releasing the card would trigger the old GPU-held redispatch branch.
        held = False
        await scheduler.gpu_check()
        turns = await settled(p["id"])
        assert len(fakes["comfy"].starts) == review["attempts"]
        assert [(s["attempt"], s["status"]) for s in turns[0]["slots"]] == [
            (1, "failed"), (1, "landed")]
        assert not any(e["type"] == "slot.retrying"
                       for e in await store().slot_events(failed_slot))


class RecordingComfy(FakeComfy):
    """Exercise the real executor without opening a transport or GPU runtime."""

    def __init__(self):
        super().__init__()
        self.uploads = []
        output = Image.new("RGB", (8, 6), (40, 80, 120))
        output.putpixel((3, 3), (240, 230, 220))
        data = io.BytesIO()
        output.save(data, format="PNG")
        self.images["out.png"] = data.getvalue()

    async def upload_image(self, blob, name):
        self.uploads.append((blob, name))
        return name

    async def submit(self, graph):
        pid = await super().submit(graph)
        self.history_map[pid] = copy.deepcopy(SUCCESS_ENTRY)
        return pid


@pytest.mark.parametrize("changed", [False, True], ids=["same-bytes", "changed-after-commit"])
async def test_committed_source_is_checked_before_real_executor_upload(
        tmp_path, monkeypatch, changed):
    comfy = RecordingComfy()
    fakes = make_fakes()
    fakes["comfy"] = ComfyExecutor(comfy)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        attest()
        p = await project(client)
        material = await imported(client, p["id"])
        direction = await draft(client, p["id"], action="continue",
                                references=[{"node_id": material["node"]["id"]}])
        review = await reviewed(client, direction)
        assert review["ready"], review["errors"]
        scheduler = wiring.current.scheduler
        adopt = scheduler.adopt_prepared_turn

        async def delayed_delivery(_turn_id):
            pass

        monkeypatch.setattr(scheduler, "adopt_prepared_turn", delayed_delivery)
        url = f"/api/crexil/directions/{direction['id']}/submit"
        body = command(revision=0, review_token=review["token"])
        accepted = await client.post(url, json=body)
        assert accepted.status_code == 201, accepted.text
        assert len(await store().list_turns(p["id"])) == 1
        assert comfy.uploads == comfy.submitted == []
        if changed:
            source_path = Path(material["node"]["output_path"])
            source_path.write_bytes(image_bytes() + b"changed after review")
        monkeypatch.setattr(scheduler, "adopt_prepared_turn", adopt)
        replay = await client.post(url, json=body)
        assert replay.status_code == 201 and replay.json() == accepted.json()
        turns = await settled(p["id"])
        if changed:
            assert comfy.uploads == comfy.submitted == []
            assert all(s["status"] == "failed" and s["kind"] == "source_changed"
                       for s in turns[0]["slots"])
            assert not [n for n in await store().list_nodes(p["id"]) if n["turn_id"]]
        else:
            assert len(comfy.uploads) == len(comfy.submitted) == review["attempts"]
            assert all(blob == image_bytes() for blob, _name in comfy.uploads)
            assert all(s["status"] == "landed" for s in turns[0]["slots"])


@pytest.mark.parametrize("transitioned", [False, True], ids=["before-hold", "after-hold"])
async def test_cancelled_adoption_can_replay_then_resume_once(tmp_path, monkeypatch, transitioned):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes["comfy"].default = Step(data=image_bytes())
        p = await project(client)
        direction = await draft(client, p["id"])
        review = await reviewed(client, direction)
        assert review["ready"], review["errors"]
        scheduler = wiring.current.scheduler
        adopt, hold = scheduler.adopt_prepared_turn, scheduler._hold
        entered = asyncio.Event()
        release = asyncio.Event()

        async def interrupted_hold(slot_id, bench):
            if transitioned:
                await hold(slot_id, bench)
            entered.set()
            await release.wait()
            if not transitioned:
                await hold(slot_id, bench)

        async def pause_before_adoption(turn_id):
            await scheduler.bench("lane:local", kind="manual", cause="Paused after acceptance")
            await adopt(turn_id)

        monkeypatch.setattr(scheduler, "_hold", interrupted_hold)
        monkeypatch.setattr(scheduler, "adopt_prepared_turn", pause_before_adoption)
        url = f"/api/crexil/directions/{direction['id']}/submit"
        body = command(revision=0, review_token=review["token"])
        delivery = asyncio.create_task(client.post(url, json=body))
        try:
            await asyncio.wait_for(entered.wait(), 2)
        finally:
            delivery.cancel()
            with pytest.raises(asyncio.CancelledError):
                await delivery
        turns = await store().list_turns(p["id"])
        assert len(turns) == 1 and fakes["comfy"].starts == []
        assert turns[0]["slots"][0]["status"] == ("held" if transitioned else "queued")
        monkeypatch.setattr(scheduler, "_hold", hold)
        monkeypatch.setattr(scheduler, "adopt_prepared_turn", adopt)
        replay = await client.post(url, json=body)
        assert replay.status_code == 201, replay.text
        assert replay.json()["turn_id"] == turns[0]["id"]
        await scheduler.unbench("lane:local")
        turns = await settled(p["id"])
        assert len(turns) == 1 and len(fakes["comfy"].starts) == review["attempts"]
        assert all(s["attempt"] == 1 and s["status"] == "landed" for s in turns[0]["slots"])
        assert sum(e["type"] == "direction.submitted" for e in await store().events_since(0)) == 1


@pytest.mark.parametrize("boundary", ["catalogue", "compiled"])
async def test_workflow_mutation_cannot_replace_attested_compiled_bytes(
        tmp_path, monkeypatch, boundary):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes["comfy"].default = Step(data=image_bytes())
        p = await project(client)
        direction = await draft(client, p["id"])
        review = await reviewed(client, direction)
        assert review["ready"] and review["maximum_usd"] == 0
        graph_path = config.WORKFLOWS_DIR / "test-txt2img@v1.json"
        approved = json.loads(graph_path.read_text())
        changed = {**approved, "unreviewed": {
            "class_type": "UnreviewedPaidOperation", "inputs": {"spend": 99}}}
        mutations = []

        def mutate():
            graph_path.write_text(json.dumps(changed))
            mutations.append(boundary)

        if boundary == "catalogue":
            catalogue = crexil_execution.catalogue

            def mutate_after_hashing(stack, graphs=None):
                result = catalogue(stack, graphs)
                if graphs is not None:
                    mutate()
                return result

            monkeypatch.setattr(crexil_execution, "catalogue", mutate_after_hashing)
        else:
            original_review = crexil_execution.review

            async def mutate_after_compilation(stack, did, **kwargs):
                result = await original_review(stack, did, **kwargs)
                mutate()
                return result

            monkeypatch.setattr(crexil_execution, "review", mutate_after_compilation)
        response = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                     json=command(revision=0, review_token=review["token"]))
        assert response.status_code == 201, response.text
        turns = await settled(p["id"])
        assert mutations and json.loads(graph_path.read_text()) == changed
        assert len(fakes["comfy"].starts) == review["attempts"]
        assert turns[0]["dials"]["crexil"]["review_token"] == review["token"]
        for slot in turns[0]["slots"]:
            assert slot["committed_usd"] == 0
            graph = slot["submitted_graph"]
            assert set(graph) == set(approved)
            assert {key: node["class_type"] for key, node in graph.items()} == {
                key: node["class_type"] for key, node in approved.items()}
        operations = (await client.get("/api/crexil/operations")).json()
        current = next(op for op in operations if op["id"] == "testpreset")
        assert current["maximum_usd_per_attempt"] is None
        assert current["revision"] != review["snapshot"]["operations"][0]["revision"]
