"""Catalogue truth: what `/api/providers` serves is the live OpenRouter
list merged with providers.json pins — pins reorder and annotate, NEVER
filter (law 12); entries served without a live counterpart are marked
`source: "config"`; `model_count` counts what is served. Hermetic throughout;
the one live test (sampled ids/prices vs a direct fetch of the real service)
is opt-in via LIVE_CATALOG=1 — discovery is public, so it is keyless and $0.
"""

import json
import os
import time

import httpx
import pytest

from app import config
from app.providers import (CATALOG_TTL_S, ProviderCatalog, ProviderError,
                           _price_estimate, _supports_seed, load_providers)
from app.schemas import ProviderModel, ProvidersInfo
from test_response_models import assert_no_dropped_keys

pytestmark = pytest.mark.anyio


class FakeClient:
    """Scripted stand-in for OpenRouterClient's discovery surface."""

    def __init__(self, listing=None, endpoints=None, *,
                 list_error=None, endpoint_errors=()):
        self.listing = listing or []
        self.endpoints = endpoints or {}
        self.list_error = list_error
        self.endpoint_errors = set(endpoint_errors)
        self.list_calls = 0

    async def list_models(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.listing

    async def model_endpoints(self, model_id):
        if model_id in self.endpoint_errors:
            raise httpx.ReadTimeout("enrich failed")
        return self.endpoints.get(model_id, [])


def rec(unit, cost, billable="output_image"):
    return {"billable": billable, "unit": unit, "cost_usd": cost}


LISTING = [
    {"id": "acme/alpha", "name": "Alpha", "description": "x" * 300},
    {"id": "acme/beta", "name": "Beta"},
    {"id": "zeta/gamma", "name": "Gamma"},
]
ENDPOINTS = {
    "acme/alpha": [{"pricing": [rec("image", 0.01)],
                    "supported_parameters": ["seed"]}],
    "acme/beta": [{"pricing": [rec("token", 2e-05)],
                   "supported_parameters": []}],
    "zeta/gamma": [{"pricing": [rec("image", 0.02)],
                    "supported_parameters": []}],
}


def pin(mid, *, label=None, enabled=True, **extra):
    p = {"id": mid, "provider": mid.split("/", 1)[0],
         "label": label or mid, "enabled": enabled}
    p.update(extra)
    return p


def live_entry(mid, price=0.01):
    return {"id": mid, "name": mid.split("/", 1)[1].title(),
            "provider": mid.split("/", 1)[0], "description": "",
            "price_usd_per_image": price, "price_note": None,
            "supports_seed": False}


def by_id(models):
    return {m["id"]: m for m in models}


@pytest.fixture
def cat_env(tmp_path, monkeypatch):
    """providers.json + staging under tmp; returns a pin writer."""
    pfile = tmp_path / "providers.json"
    monkeypatch.setattr(config, "PROVIDERS_FILE", pfile)
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path / "staging")

    def write_pins(models):
        pfile.write_text(json.dumps({"models": models}))

    write_pins([])
    return write_pins


# --- unit: price derivation ---

def test_price_estimate_image_takes_min_rounded():
    assert _price_estimate([{"pricing": [rec("image", 0.05),
                                         rec("image", 0.0312339)]}]) \
        == (0.0312, None)


def test_price_estimate_prefers_image_over_other_units():
    assert _price_estimate([{"pricing": [rec("token", 2e-05),
                                         rec("image", 0.04)]}]) == (0.04, None)


def test_price_estimate_megapixel_scaled_and_noted():
    price, note = _price_estimate([{"pricing": [rec("megapixel", 0.02)]}])
    assert price == round(0.02 * 1.05, 4) and note == "per ~1MP image"


def test_price_estimate_token_priced_gets_note_not_fake_number():
    # the Gemini-family case: no made-up per-image number
    assert _price_estimate([{"pricing": [rec("token", 2e-05)]}]) \
        == (None, "~$20.00/M tok")


def test_price_estimate_ignores_foreign_and_malformed_records():
    assert _price_estimate([
        {"pricing": [rec("image", 0.01, billable="input_text"),
                     rec("image", "free"), {"unit": "image"}]},
        {"pricing": None}, {}]) == (None, None)


def test_supports_seed_derived_from_any_endpoint():
    assert _supports_seed([{"supported_parameters": ["size"]},
                           {"supported_parameters": ["seed"]}]) is True
    assert _supports_seed([{"supported_parameters": ["size"]}]) is False
    assert _supports_seed([{}]) is False


# --- unit: pins loader ---

def test_load_providers_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS_FILE", tmp_path / "nope.json")
    assert load_providers() == {"models": []}


def test_load_providers_defaults_and_validation(tmp_path, monkeypatch):
    pfile = tmp_path / "providers.json"
    monkeypatch.setattr(config, "PROVIDERS_FILE", pfile)

    pfile.write_text(json.dumps({"models": [
        {"id": "a/b", "provider": "a", "label": "AB"}]}))
    m = load_providers()["models"][0]
    assert m["enabled"] is True and m["supports_seed"] is False

    pfile.write_text("{broken")
    with pytest.raises(ProviderError):
        load_providers()

    pfile.write_text(json.dumps({"models": [{"id": "a/b", "provider": "a"}]}))
    with pytest.raises(ProviderError):
        load_providers()


def test_repo_providers_json_is_valid_and_pins_only():
    """The checked-in config is pins/overrides ONLY — it must
    never grow back into being *the* list or a transport config."""
    raw = json.loads(config.PROVIDERS_FILE.read_text())
    assert set(raw) == {"models"}
    for m in load_providers()["models"]:
        assert set(m) <= {"id", "provider", "label", "price_usd_per_image",
                          "supports_seed", "enabled", "params"}


# --- catalog semantics: merge, order, sources ---

async def test_never_filter_full_live_list_served(cat_env):
    cat_env([pin("acme/alpha"), pin("acme/beta", enabled=False)])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    models = (await cat.get(force=True))["models"]
    assert {m["id"] for m in models} == {m["id"] for m in LISTING}


async def test_pins_annotate_and_pin_price_wins(cat_env):
    cat_env([
        pin("acme/alpha", label="Alpha!", price_usd_per_image=0.5),
        pin("acme/beta", label="B2", enabled=False, supports_seed=True,
            params={"quality": "high"}),
        pin("zeta/gamma"),  # no price override -> live estimate fills
    ])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    by = by_id((await cat.get(force=True))["models"])
    a = by["acme/alpha"]
    assert a["label"] == "Alpha!" and a["price_usd_per_image"] == 0.5
    assert a["pinned"] is True and a["source"] == "live"
    assert len(a["description"]) == 200
    b = by["acme/beta"]
    assert b["pinned"] is False          # enabled:false = not pinned, NOT hidden
    assert b["label"] == "B2" and b["supports_seed"] is True
    assert b["params"] == {"quality": "high"}
    assert b["price_usd_per_image"] is None and b["price_note"] == "~$20.00/M tok"
    assert by["zeta/gamma"]["price_usd_per_image"] == 0.02


async def test_reorder_pins_float_nothing_dropped(cat_env):
    cat_env([pin("zeta/gamma")])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    models = (await cat.get(force=True))["models"]
    assert [m["id"] for m in models] == ["zeta/gamma", "acme/alpha", "acme/beta"]


async def test_config_fallback_entries_marked_and_counted(cat_env):
    cat_env([pin("ghost/model", label="Ghost", price_usd_per_image=0.1),
             pin("dead/model", enabled=False)])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    data = await cat.get(force=True)
    by = by_id(data["models"])
    ghost = by["ghost/model"]
    assert ghost["source"] == "config" and ghost["pinned"] is True
    assert ghost["name"] == "Ghost" and ghost["price_note"] is None
    assert by["dead/model"]["source"] == "config"
    assert by["dead/model"]["pinned"] is False
    assert all(by[m["id"]]["source"] == "live" for m in LISTING)
    assert data["model_count"] == len(data["models"]) == 5


async def test_selectable_covers_every_served_entry(cat_env):
    cat_env([pin("zeta/gamma", enabled=False), pin("ghost/model")])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    data = await cat.get(force=True)
    assert set(cat.selectable()) == {m["id"] for m in data["models"]}


async def test_wire_models_lose_nothing(cat_env):
    cat_env([pin("ghost/model")])
    cat = ProviderCatalog(FakeClient(LISTING, ENDPOINTS))
    data = await cat.get(force=True)
    payload = {"transport": "openrouter", "key_present": False, **data}
    assert_no_dropped_keys(payload, ProvidersInfo.model_validate(payload))
    for m in data["models"]:
        assert_no_dropped_keys(m, ProviderModel.model_validate(m))


# --- catalog lifecycle: snapshot, TTL, failure ---

async def test_snapshot_written_and_served_across_restart(cat_env):
    cat_env([])
    await ProviderCatalog(FakeClient(LISTING, ENDPOINTS)).get(force=True)
    snap = json.loads(
        (config.STAGING_DIR / "openrouter-catalog.json").read_text())
    assert {m["id"] for m in snap["models"]} == {m["id"] for m in LISTING}
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    data = await ProviderCatalog(dead).get()
    assert dead.list_calls == 0            # fresh snapshot -> no fetch at all
    assert {m["id"] for m in data["models"]} == {m["id"] for m in LISTING}
    assert all(m["source"] == "live" for m in data["models"])


async def test_stale_snapshot_survives_dead_network(cat_env):
    cat_env([])
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    (config.STAGING_DIR / "openrouter-catalog.json").write_text(json.dumps(
        {"fetched_at": time.time() - CATALOG_TTL_S - 60,
         "models": [live_entry("acme/alpha")]}))
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    cat = ProviderCatalog(dead)
    data = await cat.get()          # stale + data: serve now, revalidate in bg
    assert [m["id"] for m in data["models"]] == ["acme/alpha"]
    await cat._refresh_task         # let the failed revalidation land
    assert dead.list_calls == 1
    assert list(cat.selectable()) == ["acme/alpha"]    # kept, never wiped


async def test_unreadable_snapshot_degrades_to_config_only(cat_env):
    cat_env([pin("ghost/model")])
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    (config.STAGING_DIR / "openrouter-catalog.json").write_text("not json")
    dead = FakeClient(list_error=httpx.ConnectError("down"))
    data = await ProviderCatalog(dead).get()
    assert [m["id"] for m in data["models"]] == ["ghost/model"]
    assert data["models"][0]["source"] == "config"
    assert data["fetched_at"] is None


async def test_fresh_cache_never_refetches_force_always_does(cat_env):
    cat_env([])
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    await cat.get(force=True)
    await cat.get()
    assert fake.list_calls == 1
    await cat.get(force=True)
    assert fake.list_calls == 2


async def test_first_boot_without_snapshot_fetches_inline(cat_env):
    cat_env([])
    fake = FakeClient(LISTING, ENDPOINTS)
    data = await ProviderCatalog(fake).get()
    assert fake.list_calls == 1 and len(data["models"]) == 3


async def test_fetch_failure_keeps_existing_data(cat_env):
    cat_env([])
    fake = FakeClient(LISTING, ENDPOINTS)
    cat = ProviderCatalog(fake)
    first = await cat.get(force=True)
    fake.list_error = httpx.ConnectError("down")
    again = await cat.get(force=True)
    assert {m["id"] for m in again["models"]} \
        == {m["id"] for m in first["models"]}
    assert again["fetched_at"] == first["fetched_at"]  # truth not re-stamped


async def test_enrichment_failure_degrades_that_model_only(cat_env):
    cat_env([])
    fake = FakeClient(LISTING, ENDPOINTS, endpoint_errors={"acme/beta"})
    by = by_id((await ProviderCatalog(fake).get(force=True))["models"])
    assert by["acme/beta"]["price_usd_per_image"] is None
    assert by["acme/beta"]["supports_seed"] is False
    assert by["acme/alpha"]["price_usd_per_image"] == 0.01


# --- the route, through the real app ---

async def test_route_serves_pins_only_before_first_fetch(tmp_path, monkeypatch):
    from test_api_v4 import booted
    async with booted(tmp_path, monkeypatch) as (client, _):
        data = (await client.get("/api/providers")).json()
        assert data["transport"] == "openrouter"
        assert data["key_present"] is True
        assert data["fetched_at"] is None
        assert [m["id"] for m in data["models"]] == ["testvendor/testmodel"]
        m = data["models"][0]
        assert m["source"] == "config" and m["pinned"] is True
        assert data["model_count"] == len(data["models"]) == 1


async def test_route_refresh_forces_live_fetch_and_merges(tmp_path, monkeypatch):
    from app import wiring
    from test_api_v4 import booted
    async with booted(tmp_path, monkeypatch) as (client, _):
        fake = FakeClient(LISTING, ENDPOINTS)
        wiring.current.provider_catalog.client = fake
        data = (await client.get("/api/providers",
                                 params={"refresh": 1})).json()
        assert fake.list_calls == 1
        assert data["fetched_at"] is not None
        by = by_id(data["models"])
        assert set(by) == {m["id"] for m in LISTING} | {"testvendor/testmodel"}
        assert data["model_count"] == len(data["models"]) == 4
        assert by["testvendor/testmodel"]["source"] == "config"
        assert all(by[m["id"]]["source"] == "live" for m in LISTING)
        assert data["models"][0]["id"] == "testvendor/testmodel"  # pin floats


# --- live truth (opt-in): the served list IS openrouter.ai, never placeholder ---

@pytest.mark.skipif(os.environ.get("LIVE_CATALOG") != "1",
                    reason="live OpenRouter comparison is opt-in: LIVE_CATALOG=1")
async def test_live_catalogue_matches_direct_fetch(tmp_path, monkeypatch):
    """The core clause against the real service (keyless, since discovery
    is public, and $0): every id from a direct fetch is served and marked
    live; sampled prices equal a direct recompute through the same rules;
    pins only float/annotate."""
    from app.openrouter import OpenRouterClient
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path)  # spare repo snapshot
    async with httpx.AsyncClient(base_url=config.OPENROUTER_URL,
                                 timeout=30.0) as direct:
        resp = await direct.get("/images/models")
        resp.raise_for_status()
        listing = resp.json().get("data") or []
        assert len(listing) >= 10, "upstream catalogue suspiciously small"
        direct_ids = [m["id"] for m in listing if m.get("id")]

        client = OpenRouterClient(api_key="")
        try:
            data = await ProviderCatalog(client).get(force=True)
        finally:
            await client.close()
        served = by_id(data["models"])

        missing = [i for i in direct_ids if i not in served]
        assert not missing, f"live models not served: {missing}"
        assert all(served[i]["source"] == "live" for i in direct_ids)
        pins = {m["id"]: m for m in load_providers()["models"]}
        assert len(set(direct_ids) - set(pins)) >= 5   # never just the pins
        assert data["model_count"] == len(data["models"])

        ordered = sorted(set(direct_ids))
        sample = [ordered[i] for i in sorted(
            {0, len(ordered) // 3, len(ordered) // 2,
             2 * len(ordered) // 3, len(ordered) - 1})]
        for mid in sample:
            r = await direct.get(f"/images/models/{mid}/endpoints")
            r.raise_for_status()
            price, _ = _price_estimate(r.json().get("endpoints") or [])
            pinned_price = pins.get(mid, {}).get("price_usd_per_image")
            want = pinned_price if pinned_price is not None else price
            assert served[mid]["price_usd_per_image"] == want, mid
