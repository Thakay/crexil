"""Native image transport retains exact requests, images and ambiguous failures.

Every HTTP call and DNS lookup is scripted. No credentials or paid services
are used by these tests.
"""

import asyncio
import base64
import copy
import io
import json
import socket

import httpx
import pytest
from PIL import Image

from app import openrouter
from app.openrouter import ExternalJobError, OpenRouterClient, _public_https_url

pytestmark = pytest.mark.anyio


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), (11, 22, 33)).save(stream, format=fmt)
    return stream.getvalue()


def encoded(raw):
    return base64.b64encode(raw).decode("ascii")


def item(fmt="PNG", **metadata):
    return {"b64_json": encoded(image_bytes(fmt)), **metadata}


@pytest.fixture
async def client_factory():
    clients = []

    async def create(handler):
        client = OpenRouterClient(base_url="https://router.example", api_key="test-key")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="https://router.example", transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-key"})
        clients.append(client)
        return client

    yield create
    for client in clients:
        await client.close()


async def test_frozen_request_exact_copy_multiple_formats_and_full_receipt(client_factory):
    request = {"model": "acme/image", "prompt": "continue this scene", "n": 2,
               "provider": {"only": ["acme/east"], "allow_fallbacks": False},
               "input_references": [{"type": "image_url", "image_url": {
                   "url": "data:image/png;base64," + encoded(image_bytes())}}]}
    frozen = copy.deepcopy(request)
    response = {"id": "gen-a", "created": 123, "usage": {"cost": ".08", "tokens": 17},
                "data": [item(media_type="image/png", revised_prompt="one"),
                         item("JPEG", revised_prompt="two"), item("WEBP")],
                "provider_details": {"route": "acme/east"}}

    async def handle(req):
        assert json.loads(req.content) == frozen
        request["provider"]["only"].append("changed-after-dispatch")
        return httpx.Response(200, json=response)

    client = await client_factory(handle)
    events = []

    async def collect(event):
        events.append(event)

    result = await client.generate_frozen(request, on_output=collect)
    assert result["request"] == frozen
    assert result["response"] == response
    assert result["cost_usd"] == .08 and result["id"] == "gen-a"
    assert result["response_status"] == 200 and result["created"] == 123
    assert result["output_errors"] == []
    assert [v["bytes"] for v in result["outputs"]] == [image_bytes(f) for f in ("PNG", "JPEG", "WEBP")]
    assert [v["extension"] for v in result["outputs"]] == [".png", ".jpg", ".webp"]
    assert [v["media_type"] for v in result["outputs"]] == ["image/png", "image/jpeg", "image/webp"]
    assert result["outputs"][0]["metadata"] == response["data"][0]
    assert [(v["width"], v["height"]) for v in result["outputs"]] == [(3, 2)] * 3
    assert [event["kind"] for event in events] == ["receipt", "output", "output", "output"]
    assert all(event["receipt"]["request"] == frozen for event in events)
    assert client.requests_total == 1  # more returned images are never silently dropped


async def test_bad_outputs_and_provider_error_do_not_discard_other_images(client_factory):
    body = {"data": [item(), {"b64_json": "not base64"},
                     item("JPEG", media_type="image/png"),
                     {"url": "data:image/webp;base64," + encoded(image_bytes("WEBP"))},
                     {"b64_json": encoded(b"<svg/>")}, "malformed"],
            "error": {"message": "one image failed", "code": "partial"},
            "usage": {"cost": .05}}
    client = await client_factory(lambda req: httpx.Response(200, json=body))
    result = await client.generate_frozen({"model": "m", "prompt": "p"})
    assert [out["index"] for out in result["outputs"]] == [0, 3]
    assert [error["index"] for error in result["output_errors"]] == [None, 1, 2, 4, 5]
    assert result["response"] == body
    assert result["cost_usd"] == .05


@pytest.mark.parametrize("status,body,kind,cost", [
    (429, {"error": {"message": "later", "code": 9}}, "rate_limit", None),
    (402, {"error": "no credits"}, "dead_key", None),
    (200, {"error": {"message": "denied"}, "usage": {"cost": .03}, "id": "g"}, "charged_blank", .03),
    (200, {"data": [{"b64_json": "bad"}], "usage": {"cost": 0}}, "error", 0),
    (200, {"data": [], "usage": {"cost": "NaN"}}, "error", None),
])
async def test_no_usable_output_retains_error_receipt(client_factory, status, body, kind, cost):
    client = await client_factory(lambda req: httpx.Response(status, json=body, headers={"Retry-After": "3"}))
    retained = []

    async def collect(event):
        retained.append(event)

    request = {"model": "m", "prompt": "p"}
    with pytest.raises(ExternalJobError) as exc:
        await client.generate_frozen(request, on_output=collect)
    assert exc.value.kind == kind
    assert exc.value.charged_usd == cost
    assert exc.value.receipt["request"] == request
    assert exc.value.receipt["response"] == body
    assert exc.value.receipt["response_status"] == status
    assert retained[0]["kind"] == "receipt"
    assert retained[0]["receipt"]["response"] == body
    if status == 429:
        assert exc.value.retry_after_s == 3


@pytest.mark.parametrize("status", [200, 502])
async def test_non_json_response_has_raw_receipt(client_factory, status):
    client = await client_factory(lambda req: httpx.Response(status, text="upstream exploded"))
    with pytest.raises(ExternalJobError) as exc:
        await client.generate_frozen({"model": "m", "prompt": "p"})
    assert exc.value.receipt["response"] == "upstream exploded"
    assert exc.value.charged_usd is None


# Raised before OpenRouter received the whole request: nothing can have been charged.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
            httpx.UnsupportedProtocol, httpx.WriteError, httpx.WriteTimeout)
# Raised after the request went out: the answer is lost and the charge unknown.
LOST = (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout)


@pytest.mark.parametrize("failure,kind,charge", [*((f, "unreachable", 0) for f in NOT_SENT),
                                                 *((f, "timeout", None) for f in LOST)])
async def test_transport_error_charges_zero_only_when_openrouter_never_received_the_request(
        client_factory, failure, kind, charge):
    sent = []

    async def handle(req):
        sent.append(json.loads(req.content))
        raise failure("scripted transport failure", request=req)

    client = await client_factory(handle)
    request = {"model": "m", "prompt": "p"}
    with pytest.raises(ExternalJobError) as exc:
        await client.generate_frozen(request)
    assert (exc.value.kind, exc.value.charged_usd) == (kind, charge)
    assert exc.value.receipt["request"] == request
    assert exc.value.receipt["response"] is None
    assert sent == [request] and client.requests_total == 1   # one request, never sent again


async def test_prior_output_collected_before_download_cancellation(client_factory, monkeypatch):
    body = {"data": [item(), {"url": "https://images.example/second.png"}], "usage": {"cost": .04}}
    client = await client_factory(lambda req: httpx.Response(200, json=body))
    events = []

    async def collect(event):
        events.append(event)

    async def cancelled(url):
        assert [e["kind"] for e in events] == ["receipt", "output"]
        assert events[0]["receipt"]["response"] == body
        raise asyncio.CancelledError

    monkeypatch.setattr(client, "_download_output", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await client.generate_frozen({"model": "m", "prompt": "p"}, on_output=collect)
    assert events[1]["output"]["bytes"] == image_bytes()


async def test_collection_failure_stops_further_output_fetch(client_factory, monkeypatch):
    client = await client_factory(lambda req: httpx.Response(200, json={"data": [item(), {"url": "https://x/image"}]}))

    async def collect(event):
        if event["kind"] == "output":
            raise OSError("receipt storage full")

    async def unexpected(url):
        pytest.fail("retrieval continued after durable collection failed")

    monkeypatch.setattr(client, "_download_output", unexpected)
    with pytest.raises(OSError, match="storage full"):
        await client.generate_frozen({"model": "m", "prompt": "p"}, on_output=collect)


async def test_receipt_replay_retries_collection_without_repeating_generation(client_factory, monkeypatch):
    posts = []
    response = {"id": "paid-once", "usage": {"cost": .12}, "data": [
        item(), {"url": "https://images.example/second.webp"}, {"b64_json": "malformed"}]}

    async def handle(request):
        posts.append(request.method)
        return httpx.Response(200, json=response)

    client = await client_factory(handle)
    callbacks = []

    async def collect(event):
        callbacks.append(copy.deepcopy(event))

    async def failed_download(url):
        raise ExternalJobError("invalid_output", "download unavailable",
                               receipt={"download_url": url, "status": 503})

    monkeypatch.setattr(client, "_download_output", failed_download)
    first = await client.generate_frozen({"model": "m", "prompt": "p"}, on_output=collect)
    retained = callbacks[0]["receipt"]
    receipt_bytes = json.dumps(retained, sort_keys=True).encode()
    assert "output_errors" not in retained
    assert [out["index"] for out in first["outputs"]] == [0]
    assert [error["index"] for error in first["output_errors"]] == [1, 2]

    async def successful_download(url):
        return image_bytes("WEBP")

    monkeypatch.setattr(client, "_download_output", successful_download)
    callbacks.clear()
    replay = await client.collect_receipt(retained, on_output=collect)
    assert posts == ["POST"] and client.requests_total == 1
    assert [out["index"] for out in replay["outputs"]] == [0, 1]
    assert replay["outputs"][0]["bytes"] == image_bytes()
    assert replay["outputs"][1]["bytes"] == image_bytes("WEBP")
    assert [error["index"] for error in replay["output_errors"]] == [2]
    assert replay["response"] == response and replay["cost_usd"] == .12
    assert [event["kind"] for event in callbacks] == ["receipt", "output", "output", "output_error"]
    assert json.dumps(callbacks[0]["receipt"], sort_keys=True).encode() == receipt_bytes
    assert json.dumps(retained, sort_keys=True).encode() == receipt_bytes


async def test_receipt_replay_recovers_after_original_collection_was_cancelled(client_factory):
    body = {"id": "cancelled-collector", "data": [item(), item("JPEG")], "usage": {"cost": .08}}
    client = await client_factory(lambda req: httpx.Response(200, json=body))
    saved = None

    async def cancel_after_receipt(event):
        nonlocal saved
        assert event["kind"] == "receipt"
        saved = copy.deepcopy(event["receipt"])
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await client.generate_frozen({"model": "m", "prompt": "p"}, on_output=cancel_after_receipt)
    result = await client.collect_receipt(saved)
    assert [output["bytes"] for output in result["outputs"]] == [image_bytes(), image_bytes("JPEG")]
    assert result["cost_usd"] == .08 and client.requests_total == 1


async def test_https_download_pins_public_address_without_credentials(client_factory, monkeypatch):
    client = await client_factory(lambda req: httpx.Response(200, json={
        "data": [{"url": "https://images.example/image.png", "revised_prompt": "kept"}]}))
    monkeypatch.setattr(openrouter.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    original_client = httpx.AsyncClient
    downloads = []

    async def handle(req):
        downloads.append(req)
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "images.example"
        assert req.extensions["sni_hostname"] == "images.example"
        assert "authorization" not in req.headers and "cookie" not in req.headers
        return httpx.Response(200, content=image_bytes())

    def download_client(**kwargs):
        assert kwargs["trust_env"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", download_client)
    result = await client.generate_frozen({"model": "m", "prompt": "p"})
    assert len(downloads) == 1
    assert result["outputs"][0]["bytes"] == image_bytes()
    assert result["outputs"][0]["metadata"]["revised_prompt"] == "kept"


@pytest.mark.parametrize("status", [302, 503])
async def test_failed_download_preserves_previous_output_and_error_body(client_factory, monkeypatch, status):
    body = {"data": [item(), {"url": "https://images.example/second.png"}], "usage": {"cost": .04}}
    client = await client_factory(lambda req: httpx.Response(200, json=body))
    monkeypatch.setattr(openrouter.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    original_client = httpx.AsyncClient
    downloads = []

    async def handle(req):
        downloads.append(req)
        return httpx.Response(status, json={"reason": "unavailable"},
                              headers={"Location": "https://127.0.0.1/private"})

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    result = await client.generate_frozen({"model": "m", "prompt": "p"})
    assert len(downloads) == 1  # never follows the redirect to another destination
    assert [out["index"] for out in result["outputs"]] == [0]
    error = result["output_errors"][0]
    assert error["index"] == 1 and error["metadata"] == body["data"][1]
    assert error["details"]["status"] == status
    assert error["details"]["response"] == {"reason": "unavailable"}
    assert result["cost_usd"] == .04


@pytest.mark.parametrize("url,address", [
    ("http://images.example/a", "93.184.216.34"),
    ("https://user:pass@images.example/a", "93.184.216.34"),
    ("https://images.example:8080/a", "93.184.216.34"),
    ("https://images.example/a", "127.0.0.1"),
    ("https://images.example/a", "169.254.169.254"),
    ("https://images.example/a", "10.0.0.2"),
    ("https://images.example/a", "::1"),
])
async def test_output_urls_refuse_unsafe_destinations(monkeypatch, url, address):
    monkeypatch.setattr(openrouter.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ExternalJobError, match="unsafe output URL"):
        await _public_https_url(url)


@pytest.mark.parametrize("patch", [{"n": 20}, {"n": True}, {"stream": True},
                                  {"output_format": "svg"}, {"prompt": ""},
                                  {"input_references": [{"image_url": "data:image/png;base64,AA=="}]}])
async def test_invalid_frozen_request_never_dispatches(client_factory, patch):
    client = await client_factory(lambda req: pytest.fail("invalid request was sent"))
    with pytest.raises(ExternalJobError) as exc:
        await client.generate_frozen({"model": "m", "prompt": "p", **patch})
    assert exc.value.kind == "invalid_request" and client.requests_total == 0
    assert exc.value.charged_usd == 0


async def test_legacy_generate_compatible_but_cannot_override_protected_fields(client_factory):
    sent = []

    async def handle(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"data": [item()], "usage": {"cost": .02}})

    client = await client_factory(handle)
    result = await client.generate(model="m", prompt="p", seed=42, params={"quality": "high"})
    assert result["png"] == image_bytes()
    assert result["cost_usd"] == .02
    assert result["request"] == sent[0] == {"model": "m", "prompt": "p", "seed": 42,
                                            "n": 1, "output_format": "png", "quality": "high"}
    for field, value in {"model": "other", "prompt": "other", "n": 10,
                         "output_format": "jpeg", "seed": 9, "input_references": []}.items():
        with pytest.raises(ExternalJobError, match="protected"):
            await client.generate(model="m", prompt="p", params={field: value})
    assert len(sent) == 1


async def test_legacy_refuses_to_relabel_non_png_bytes(client_factory):
    client = await client_factory(lambda req: httpx.Response(200, json={
        "data": [item("JPEG")], "usage": {"cost": .02}}))
    with pytest.raises(ExternalJobError, match="non-PNG") as exc:
        await client.generate(model="m", prompt="p")
    assert exc.value.charged_usd == .02
    assert exc.value.receipt["response"]["data"][0] == item("JPEG")
