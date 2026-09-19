"""Executor behaviour: pins the ComfyUI signal/truth split, the
OpenRouter kind mapping, ownership-verified cancel, and the reconcile
verdicts recovery depends on. Fake clients are duck-typed plain objects —
no HTTP, no new deps.
"""

import asyncio

import httpx
import pytest

from app import config
from app.comfy import ComfyClient
from app.executors import (ComfyExecutor, DispatchRequest, EngineFailure,
                           OpenRouterExecutor)
from app.openrouter import ExternalJobError, OpenRouterClient

pytestmark = pytest.mark.anyio


# --- fakes ---

SUCCESS_ENTRY = {
    "status": {"status_str": "success", "completed": True},
    "outputs": {"9": {"images": [
        {"filename": "out.png", "subfolder": "", "type": "output"}]}},
}
ERROR_ENTRY = {"status": {"status_str": "error",
                          "messages": [["execution_error",
                                        {"exception_message": "OOM"}]]}}
NO_IMAGE_ENTRY = {"status": {"status_str": "success"}, "outputs": {}}


class FakeComfy:
    def __init__(self):
        self.history_map = {}
        self.images = {"out.png": b"png-bytes"}
        self.queue = {"queue_pending": [], "queue_running": []}
        self.interrupts = 0
        self.deleted = []
        self.submit_error = None
        self.submitted = []
        self._handler = None

    def start_ws_mirror(self, handler):
        self._handler = handler

    async def push_ws(self, msg):
        await self._handler(msg)

    async def submit(self, graph):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append(graph)
        return f"pid-{len(self.submitted)}"

    async def history(self, pid):
        return self.history_map.get(pid)

    async def view(self, filename, subfolder="", type_="output"):
        return self.images[filename]

    async def queue_state(self):
        return self.queue

    async def interrupt(self):
        self.interrupts += 1

    async def delete_queued(self, pid):
        self.deleted.append(pid)

    async def close(self):
        pass


class FakeOR:
    def __init__(self, *, error=None, cost=0.04):
        self.error = error
        self.cost = cost
        self.calls = []

    async def generate(self, *, model, prompt, seed=None, params=None):
        self.calls.append({"model": model, "prompt": prompt, "seed": seed})
        if self.error:
            raise self.error
        return {"png": b"or-bytes", "cost_usd": self.cost, "created": 123,
                "request": {"model": model, "prompt": prompt, "seed": seed}}


def req(slot="s1", engine="klein", **kw):
    kw.setdefault("submitted_graph", {"g": 1})
    return DispatchRequest(slot_id=slot, engine=engine, **kw)


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(config, "COMFY_POLL_S", 0.01)
    monkeypatch.setattr(config, "HISTORY_CONFIRM_TRIES", 5)
    monkeypatch.setattr(config, "HISTORY_CONFIRM_INTERVAL_S", 0.01)


# --- ComfyExecutor ---

async def test_comfy_treats_ws_as_signal_and_history_as_truth():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    waiter = asyncio.create_task(ex.wait(h))
    await asyncio.sleep(0)                       # let wait park on the event
    fake.history_map[h.job_ref] = SUCCESS_ENTRY  # truth
    await fake.push_ws({"type": "execution_success",
                        "data": {"prompt_id": h.job_ref}})   # signal
    result = await asyncio.wait_for(waiter, 2)
    assert result.data == b"png-bytes" and result.filename == "out.png"


async def test_comfy_lands_by_polling_when_no_ws_signal_arrives():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.history_map[h.job_ref] = SUCCESS_ENTRY
    result = await asyncio.wait_for(ex.wait(h), 2)   # poll floor finds it
    assert result.data == b"png-bytes"


async def test_comfy_error_history_maps_to_engine_failure_with_messages():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.history_map[h.job_ref] = ERROR_ENTRY
    with pytest.raises(EngineFailure) as ei:
        await asyncio.wait_for(ex.wait(h), 2)
    assert ei.value.kind == "error" and "OOM" in str(ei.value)


async def test_comfy_missing_output_image_is_an_error():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.history_map[h.job_ref] = NO_IMAGE_ENTRY
    await fake.push_ws({"type": "executing",
                        "data": {"prompt_id": h.job_ref, "node": None}})
    with pytest.raises(EngineFailure, match="no output image"):
        await asyncio.wait_for(ex.wait(h), 2)


async def test_comfy_unreachable_submit_and_rejected_graph_map_to_kinds():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    fake.submit_error = httpx.ConnectError("refused")
    with pytest.raises(EngineFailure) as ei:
        await ex.start(req())
    assert ei.value.kind == "unreachable"
    resp = httpx.Response(400, text="bad node",
                          request=httpx.Request("POST", "http://x"))
    fake.submit_error = httpx.HTTPStatusError("400", request=resp.request,
                                              response=resp)
    with pytest.raises(EngineFailure) as ei:
        await ex.start(req())
    assert ei.value.kind == "error" and ei.value.detail == "bad node"


async def test_comfy_cancel_deletes_when_queued_and_wait_reports_interrupted():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.queue["queue_pending"] = [[0, h.job_ref, {}, {}, []]]
    await ex.cancel(h)
    assert fake.deleted == [h.job_ref] and fake.interrupts == 0
    with pytest.raises(EngineFailure) as ei:    # deleted prompt: no history ever
        await asyncio.wait_for(ex.wait(h), 2)
    assert ei.value.kind == "interrupted"


async def test_comfy_cancel_interrupts_only_our_running_prompt():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.queue["queue_running"] = [[0, "someone-elses-prompt", {}, {}, []]]
    await ex.cancel(h)
    assert fake.interrupts == 0                  # not ours — never fired
    fake.queue["queue_running"] = [[0, h.job_ref, {}, {}, []]]
    await ex.cancel(h)
    assert fake.interrupts == 1


async def test_comfy_interrupt_after_our_cancel_maps_to_interrupted_not_error():
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    await ex.open()
    h = await ex.start(req())
    fake.queue["queue_running"] = [[0, h.job_ref, {}, {}, []]]
    await ex.cancel(h)
    fake.history_map[h.job_ref] = ERROR_ENTRY    # ComfyUI records the abort
    await fake.push_ws({"type": "execution_interrupted",
                        "data": {"prompt_id": h.job_ref}})
    with pytest.raises(EngineFailure) as ei:
        await asyncio.wait_for(ex.wait(h), 2)
    assert ei.value.kind == "interrupted"


@pytest.mark.parametrize("entry,expected", [
    (None, "gone"),
    (ERROR_ENTRY, "failed"),
    (SUCCESS_ENTRY, "landed"),
])
async def test_comfy_reconcile_returns_the_recovery_verdict(entry, expected):
    fake = FakeComfy()
    ex = ComfyExecutor(fake)
    if entry is not None:
        fake.history_map["pid-old"] = entry
    rec = await ex.reconcile({"job_ref": "pid-old"})
    assert rec.kind == expected
    if expected == "landed":
        assert rec.result.data == b"png-bytes"


# --- OpenRouterExecutor ---

async def test_openrouter_result_carries_cost_and_the_exact_request():
    ex = OpenRouterExecutor(FakeOR(cost=0.07))
    h = await ex.start(req(external={"model": "gpt-5-image", "prompt": "p",
                                     "seed": 9}, submitted_graph=None))
    result = await ex.wait(h)
    assert result.data == b"or-bytes"
    assert result.charged_usd == 0.07
    assert result.external["request"]["model"] == "gpt-5-image"
    assert result.external["transport"] == "openrouter"


@pytest.mark.parametrize("kind", ["dead_key", "moderation", "rate_limit",
                                  "model_down", "timeout", "unreachable",
                                  "error"])
async def test_openrouter_maps_every_error_kind(kind):
    ex = OpenRouterExecutor(FakeOR(error=ExternalJobError(kind, "boom")))
    h = await ex.start(req(external={"model": "m", "prompt": "p"},
                           submitted_graph=None))
    with pytest.raises(EngineFailure) as ei:
        await ex.wait(h)
    assert ei.value.kind == kind


async def test_openrouter_cancel_cancels_the_in_flight_call():
    class Hanging:
        async def generate(self, **kw):
            await asyncio.sleep(60)
    ex = OpenRouterExecutor(Hanging())
    h = await ex.start(req(external={"model": "m", "prompt": "p"},
                           submitted_graph=None))
    await asyncio.sleep(0)
    await ex.cancel(h)
    with pytest.raises(asyncio.CancelledError):
        await ex.wait(h)


async def test_openrouter_reconcile_is_always_unverifiable():
    ex = OpenRouterExecutor(FakeOR())
    rec = await ex.reconcile({"job_ref": None})
    assert rec.kind == "unverifiable"


# --- the additive client methods themselves ---

class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {}


class _RecordingHTTP:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append((url, kw.get("json")))
        return _Resp()

    async def aclose(self):
        pass


async def test_client_interrupt_and_delete_post_the_documented_bodies():
    client = ComfyClient(base_url="http://test")
    await client._http.aclose()
    client._http = _RecordingHTTP()
    await client.interrupt()
    await client.delete_queued("pid-7")
    assert client._http.calls == [("/api/interrupt", None),
                                  ("/api/queue", {"delete": ["pid-7"]})]


async def test_openrouter_read_timeout_is_its_own_kind(monkeypatch):
    orc = OpenRouterClient(base_url="http://x", api_key="k")

    async def slow(url, json=None):
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(orc._client, "post", slow)
    with pytest.raises(ExternalJobError) as ei:
        await orc.generate(model="m", prompt="p")
    assert ei.value.kind == "timeout"
    await orc.close()
