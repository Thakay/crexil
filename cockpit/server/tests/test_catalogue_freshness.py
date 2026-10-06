"""Catalogue freshness: reads refresh ahead in the background and never repeat a
failed refresh (law 9); a review awaits one shared refresh before compiling
hosted requests, waits a bounded time, and survives its own cancellation; a
failed refresh keeps the last facts, marked with their age and a plain reason
(laws 3 and 17). Hermetic: every network call is a scripted fake.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import config, hosted_images, providers
from app.providers import CATALOG_TTL_S, REFRESH_AHEAD_S, ProviderCatalog
from test_providers import ENDPOINTS, LISTING, FakeClient

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("env")]

HOUR = 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Pins and the catalogue snapshot live under a throwaway folder."""
    pins = tmp_path / "providers.json"
    pins.write_text(json.dumps({"models": []}))
    monkeypatch.setattr(config, "PROVIDERS_FILE", pins)
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path / "staging")


class GatedClient(FakeClient):
    """A fake whose listing waits for the test to open a gate."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = asyncio.Event()

    async def list_models(self):
        self.list_calls += 1
        await self.gate.wait()
        if self.list_error is not None:
            raise self.list_error
        return self.listing


def snapshot(age_s: float, *, discovery_error: str | None = None) -> None:
    """A catalogue snapshot whose facts were confirmed age_s seconds ago."""
    confirmed = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    (config.STAGING_DIR / "openrouter-catalog.json").write_text(json.dumps({
        "fetched_at": time.time() - age_s,
        "models": [{"id": "acme/alpha", "name": "Alpha", "provider": "acme",
                    "description": "", "price_usd_per_image": 0.01, "price_note": None,
                    "supports_seed": False,
                    "endpoints": [{"provider_tag": "acme", "pricing": []}],
                    "endpoints_fetched_at": confirmed.isoformat(),
                    "endpoint_discovery_error": discovery_error}]}))


def confirmed_age(entry: dict) -> float:
    fetched = datetime.fromisoformat(entry["endpoints_fetched_at"])
    return (datetime.now(timezone.utc) - fetched).total_seconds()


async def settle(cat: ProviderCatalog) -> None:
    if cat._refresh_task is not None:
        await cat._refresh_task


# --- passive reads ---

async def test_a_fresh_catalogue_is_never_refetched_by_reads():
    snapshot(60)
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    cat.selectable()
    await cat.ensure_fresh(["acme/alpha"])
    await settle(cat)
    assert fake.list_calls == 0


async def test_reads_refresh_ahead_in_the_background():
    snapshot(2 * HOUR)
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    served = cat.selectable()                     # answers now, from the cache
    assert list(served) == ["acme/alpha"]
    assert served["acme/alpha"]["catalogue_state"]["refreshing"] is True
    await settle(cat)
    assert fake.list_calls == 1
    assert set(cat.selectable()) == {m["id"] for m in LISTING}


async def test_reads_never_repeat_a_failed_refresh():
    snapshot(2 * HOUR)
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    cat = ProviderCatalog(dead)
    cat.selectable()
    await settle(cat)
    for _ in range(5):                             # a polling client reads again and again
        cat.selectable()
        await asyncio.sleep(0)
    assert dead.list_calls == 1


def test_a_read_without_a_running_loop_starts_nothing():
    snapshot(2 * HOUR)
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    assert list(cat.selectable()) == ["acme/alpha"]
    assert cat._refresh_task is None


async def test_preview_never_refreshes(monkeypatch):
    monkeypatch.setattr(config, "CREXIL_PREVIEW", True)
    snapshot(7 * HOUR)
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    cat.selectable()
    await cat.ensure_fresh(["acme/alpha"])
    await settle(cat)
    assert fake.list_calls == 0


# --- before review ---

async def test_review_refreshes_a_stale_catalogue_before_compiling():
    snapshot(7 * HOUR)
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    await cat.ensure_fresh(["acme/alpha"])
    entry = cat.selectable()["acme/alpha"]
    assert fake.list_calls == 1
    assert 0 <= confirmed_age(entry) < 60          # compiles against fresh facts
    assert entry["catalogue_state"]["last_failure"] is None


async def test_review_skips_operations_the_catalogue_does_not_serve():
    snapshot(7 * HOUR)
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    cat._failed_at = time.time()                   # keep passive reads quiet
    await cat.ensure_fresh(["local-preset", "mesh:trellis"])
    assert fake.list_calls == 0


async def test_concurrent_reviews_share_one_refresh():
    snapshot(7 * HOUR)
    gated = GatedClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(gated)
    reviews = [asyncio.create_task(cat.ensure_fresh(["acme/alpha"])) for _ in range(4)]
    await asyncio.sleep(0.01)
    gated.gate.set()
    await asyncio.gather(*reviews)
    assert gated.list_calls == 1


async def test_review_waits_a_bounded_time_and_the_refresh_continues(monkeypatch):
    monkeypatch.setattr(providers, "REFRESH_WAIT_S", 0.05)
    snapshot(7 * HOUR)
    gated = GatedClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(gated)
    started = time.monotonic()
    await cat.ensure_fresh(["acme/alpha"])
    assert time.monotonic() - started < 1
    entry = cat.selectable()["acme/alpha"]
    assert entry["catalogue_state"]["refreshing"] is True
    with pytest.raises(ValueError, match="A catalogue refresh is running"):
        hosted_images.compile_request(entry, None, None, [])
    gated.gate.set()
    await settle(cat)
    assert 0 <= confirmed_age(cat.selectable()["acme/alpha"]) < 60


async def test_a_cancelled_review_never_cancels_the_shared_refresh():
    snapshot(7 * HOUR)
    gated = GatedClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(gated)
    review = asyncio.create_task(cat.ensure_fresh(["acme/alpha"]))
    await asyncio.sleep(0.01)
    review.cancel()
    with pytest.raises(asyncio.CancelledError):
        await review
    gated.gate.set()
    await settle(cat)
    assert not cat._refresh_task.cancelled()
    assert set(cat.selectable()) == {m["id"] for m in LISTING}


async def test_review_retries_a_failed_refresh_at_most_once_a_minute():
    snapshot(7 * HOUR)
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    cat = ProviderCatalog(dead)
    await cat.ensure_fresh(["acme/alpha"])
    await cat.ensure_fresh(["acme/alpha"])         # an immediate second review
    assert dead.list_calls == 1
    cat._started_at -= providers.RETRY_AFTER_S     # a minute later
    await cat.ensure_fresh(["acme/alpha"])
    assert dead.list_calls == 2


async def test_review_retries_a_model_whose_endpoint_discovery_failed():
    snapshot(60, discovery_error="enrich failed")
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    entry = cat.selectable()["acme/alpha"]
    with pytest.raises(ValueError, match="Review again in a minute"):
        hosted_images.compile_request(entry, None, None, [])
    await cat.ensure_fresh(["acme/alpha"])
    assert fake.list_calls == 1
    assert cat.selectable()["acme/alpha"]["endpoint_discovery_error"] is None


# --- a failed refresh: keep the facts, state their age plainly ---

async def test_a_failed_refresh_keeps_the_facts_with_their_age_and_a_plain_reason():
    snapshot(7 * HOUR)
    dead = FakeClient(list_error=httpx.ConnectError("[Errno 111] Connection refused"))
    cat = ProviderCatalog(dead)
    await cat.ensure_fresh(["acme/alpha"])
    entry = cat.selectable()["acme/alpha"]
    state = entry["catalogue_state"]
    assert state["last_failure"] == "the model catalogue service could not be reached"
    assert state["failed_at"] is not None and state["fetched_at"] is not None
    assert confirmed_age(entry) > 6 * HOUR         # the old facts are kept, never re-stamped
    with pytest.raises(ValueError) as refused:
        hosted_images.compile_request(entry, None, None, [])
    message = str(refused.value)
    assert "last confirmed 7 h ago" in message
    assert "within 6 hours" in message
    assert "could not be reached" in message
    assert "Errno" not in message and "111" not in message      # law 3


@pytest.mark.parametrize("error, reason", [
    (httpx.ReadTimeout("slow"), "did not answer in time"),
    (httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://x"),
                           response=httpx.Response(503)), "refused the request"),
    (ValueError("bad json"), "could not be read"),
])
async def test_each_failure_kind_has_one_plain_reason(error, reason):
    snapshot(7 * HOUR)
    cat = ProviderCatalog(FakeClient(list_error=error))
    await cat.ensure_fresh(["acme/alpha"])
    assert reason in cat.state()["last_failure"]
    assert "503" not in cat.state()["last_failure"]


async def test_a_successful_refresh_clears_the_failure():
    snapshot(7 * HOUR)
    fake = FakeClient(LISTING, ENDPOINTS, list_error=httpx.ConnectError("down"))
    cat = ProviderCatalog(fake)
    await cat.ensure_fresh(["acme/alpha"])
    assert cat.state()["last_failure"]
    fake.list_error = None
    cat._started_at -= providers.RETRY_AFTER_S
    await cat.ensure_fresh(["acme/alpha"])
    assert cat.state()["last_failure"] is None and cat.state()["failed_at"] is None


def test_an_unconfirmed_model_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="have not been confirmed"):
        hosted_images.compile_request({"id": "acme/alpha", "endpoints": []}, None, None, [])


# --- the observed state never churns a model's revision ---

async def test_catalogue_state_never_changes_a_revision():
    snapshot(2 * HOUR)
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    cat = ProviderCatalog(dead)
    before = hosted_images.revision(cat.selectable()["acme/alpha"])   # refreshing
    await settle(cat)
    after = cat.selectable()["acme/alpha"]                              # failed
    assert after["catalogue_state"]["last_failure"]
    assert hosted_images.revision(after) == before


def test_freshness_bounds_keep_their_order():
    assert REFRESH_AHEAD_S < CATALOG_TTL_S
    assert providers.REFRESH_WAIT_S < providers.RETRY_AFTER_S
