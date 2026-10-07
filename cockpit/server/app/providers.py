"""Cache OpenRouter discovery and merge display pins without filtering models.

Original model and endpoint descriptions survive discovery and offline
snapshots, including provider identity, capability bounds and every pricing
variant. Endpoint timestamps advance only after successful endpoint discovery;
a failed refresh preserves the prior facts and records a plain reason, which
every served entry carries with the catalogue's age (law 17).

Freshness: passive reads start one background refresh once the catalogue is
REFRESH_AHEAD_S old and never repeat a failed one (law 9). A review awaits
`ensure_fresh` first, so hosted requests compile against current endpoint
facts; it shares any running refresh, waits at most REFRESH_WAIT_S, and starts
a new attempt at most once per RETRY_AFTER_S. Every refresh is single-flight.

Legacy display estimates and config prices remain separate from these facts.
They are neither endpoint tariffs nor provider-enforced spending ceilings.
Token pricing gets a per-token note; megapixel estimates assume about 1MP.
The catalogue supplies evidence, never spending authorization or paid calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timezone

import httpx

from . import config

log = logging.getLogger("cockpit.providers")

CATALOG_TTL_S = 6 * 3600  # endpoint facts older than this cannot compile a hosted request
REFRESH_AHEAD_S = 3600    # reads refresh in the background once the catalogue is this old
REFRESH_WAIT_S = 15.0     # a review waits this long for a running refresh, then uses the facts it has
RETRY_AFTER_S = 60.0      # a review starts a new refresh at most this often


def _plain_failure(exc: Exception) -> str:
    """One plain reason for a failed catalogue refresh; never a status code (law 3)."""
    if isinstance(exc, httpx.TimeoutException):
        return "the model catalogue service did not answer in time"
    if isinstance(exc, httpx.HTTPStatusError):
        return "the model catalogue service refused the request"
    if isinstance(exc, (httpx.TransportError, OSError)):
        return "the model catalogue service could not be reached"
    return "the model catalogue answered in a form that could not be read"


def _iso(stamp: float | None) -> str | None:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None


class ProviderError(Exception):
    pass


def load_providers() -> dict:
    """Load + validate providers.json (pins/overrides/fallback)."""
    if not config.PROVIDERS_FILE.is_file():
        return {"models": []}
    try:
        data = json.loads(config.PROVIDERS_FILE.read_text())
    except json.JSONDecodeError as e:
        raise ProviderError(f"providers.json is not valid JSON: {e}")
    models = []
    for m in data.get("models", []):
        for field in ("id", "provider", "label"):
            if not isinstance(m.get(field), str) or not m[field]:
                raise ProviderError(
                    f"providers.json model entry missing '{field}': {m}")
        m.setdefault("enabled", True)
        m.setdefault("supports_seed", False)
        models.append(m)
    return {"models": models}


def _price_estimate(endpoints: list[dict]) -> tuple[float | None, str | None]:
    """Best-effort per-image estimate from endpoint pricing records."""
    per_image, per_token, per_mp = [], [], []
    for ep in endpoints:
        for p in ep.get("pricing") or []:
            if p.get("billable") != "output_image":
                continue
            cost = p.get("cost_usd")
            if not isinstance(cost, (int, float)):
                continue
            unit = p.get("unit")
            if unit == "image":
                per_image.append(cost)
            elif unit == "token":
                per_token.append(cost)
            elif unit == "megapixel":
                per_mp.append(cost)
    if per_image:
        return round(min(per_image), 4), None
    if per_mp:
        return round(min(per_mp) * 1.05, 4), "per ~1MP image"
    if per_token:
        return None, f"~${min(per_token) * 1e6:,.2f}/M tok"
    return None, None


def _supports_seed(endpoints: list[dict]) -> bool:
    return any("seed" in (ep.get("supported_parameters") or {})
               for ep in endpoints)


class ProviderCatalog:
    """TTL-cached merged view of the live OpenRouter catalog + config pins.

    Reads are sync from cache (turn validation must not block on HTTP);
    refreshes run as a single background task, stale-while-revalidate.
    """

    def __init__(self, client) -> None:  # client: OpenRouterClient
        self.client = client
        self._live: dict[str, dict] = {}   # id -> live entry
        self._fetched_at: float | None = None
        self._refresh_task: asyncio.Task | None = None
        self._snapshot_loaded = False
        self._started_at: float | None = None  # when the latest refresh began
        self._failed_at: float | None = None   # the latest failed refresh; a success clears it
        self._failure: str | None = None       # its plain reason

    # --- snapshot (offline fallback across restarts) ---

    @property
    def _snapshot_path(self):
        return config.STAGING_DIR / "openrouter-catalog.json"

    def _load_snapshot(self) -> None:
        self._snapshot_loaded = True
        try:
            snap = json.loads(self._snapshot_path.read_text())
            self._live = {m["id"]: m for m in snap.get("models", [])}
            self._fetched_at = snap.get("fetched_at")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("catalog snapshot unreadable: %s", e)

    def _save_snapshot(self) -> None:
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self._snapshot_path.write_text(json.dumps(
                {"fetched_at": self._fetched_at,
                 "models": list(self._live.values())}))
        except OSError as e:
            log.warning("catalog snapshot not written: %s", e)

    # --- refresh ---

    async def _refresh(self) -> None:
        if config.CREXIL_PREVIEW:
            return  # Preview serves config/snapshots, including forced refresh.
        try:
            listing = await self.client.list_models()
            if not isinstance(listing, list) or any(not isinstance(m, dict) for m in listing):
                raise ValueError("the model listing is not a list of models")
        except Exception as e:
            self._failed_at, self._failure = time.time(), _plain_failure(e)
            log.warning("openrouter catalog fetch failed (keeping %s): %s",
                        "snapshot" if self._live else "config-only", e)
            return
        sem = asyncio.Semaphore(8)

        async def enrich(m: dict) -> dict:
            previous = self._live.get(m["id"], {})
            entry = {"id": m["id"], "name": m.get("name") or m["id"],
                     "provider": m["id"].split("/", 1)[0],
                     "description": (m.get("description") or "")[:200],
                     "discovered_model": deepcopy(m),
                     "endpoints": deepcopy(previous.get("endpoints", [])),
                     "endpoints_fetched_at": previous.get("endpoints_fetched_at"),
                     "endpoint_discovery_error": None}
            try:
                async with sem:
                    eps = await self.client.model_endpoints(m["id"])
                if not isinstance(eps, list) or any(not isinstance(ep, dict) for ep in eps):
                    raise ValueError("endpoint discovery returned an invalid list")
                entry["endpoints"] = deepcopy(eps)
                entry["endpoints_fetched_at"] = datetime.now(timezone.utc).isoformat()
                entry["price_usd_per_image"], entry["price_note"] = \
                    _price_estimate(eps)
                entry["supports_seed"] = _supports_seed(eps)
            except Exception as exc:
                entry["endpoint_discovery_error"] = str(exc)
                entry["price_usd_per_image"] = previous.get("price_usd_per_image")
                entry["price_note"] = previous.get("price_note")
                entry["supports_seed"] = previous.get("supports_seed", False)
            return entry

        enriched = await asyncio.gather(*(enrich(m) for m in listing
                                          if m.get("id")))
        self._live = {e["id"]: e for e in enriched}
        self._fetched_at = time.time()
        self._failed_at = self._failure = None
        self._save_snapshot()
        log.info("openrouter catalog refreshed: %d models", len(self._live))

    def _kick_refresh(self) -> asyncio.Task:
        """Start a refresh unless one is running; every caller shares that one."""
        if self._refresh_task is None or self._refresh_task.done():
            self._started_at = time.time()
            self._refresh_task = asyncio.create_task(self._refresh())
        return self._refresh_task

    def _refreshing(self) -> bool:
        return self._refresh_task is not None and not self._refresh_task.done()

    def _older_than(self, seconds: float) -> bool:
        return self._fetched_at is None or time.time() - self._fetched_at > seconds

    def _may_start(self) -> bool:
        """A new refresh started on request: at most once per RETRY_AFTER_S."""
        return self._started_at is None or time.time() - self._started_at >= RETRY_AFTER_S

    def _refresh_ahead(self) -> None:
        """Passive reads keep the catalogue current in the background. They never
        repeat a failed refresh (law 9); only a review or a forced refresh does."""
        if (config.CREXIL_PREVIEW or self._failed_at is not None
                or not self._older_than(REFRESH_AHEAD_S)):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # a synchronous caller has no loop to refresh on
        self._kick_refresh()

    def state(self) -> dict:
        """The catalogue's observed freshness (law 17), served on every entry."""
        return {"fetched_at": _iso(self._fetched_at), "refreshing": self._refreshing(),
                "last_failure": self._failure, "failed_at": _iso(self._failed_at)}

    async def ensure_fresh(self, operation_ids: Iterable[str] | None = None) -> None:
        """Before a review compiles hosted requests: when the catalogue is older than
        REFRESH_AHEAD_S, or a requested model's endpoint discovery failed, await a
        refresh. A running refresh is shared; a new one starts at most once per
        RETRY_AFTER_S. Waits at most REFRESH_WAIT_S, after which the refresh keeps
        running and the review uses the facts at hand. A cancelled review never
        cancels the shared refresh. Never raises; failures are recorded, not looped."""
        if config.CREXIL_PREVIEW:
            return
        served = self.selectable()
        wanted = list(served) if operation_ids is None else [i for i in operation_ids if i in served]
        if not wanted:
            return
        undiscovered = any(served[i].get("endpoint_discovery_error") for i in wanted)
        if not (self._older_than(REFRESH_AHEAD_S) or undiscovered):
            return
        if not self._refreshing() and not self._may_start():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._kick_refresh()), REFRESH_WAIT_S)
        except TimeoutError:
            pass
        except Exception as exc:
            self._failed_at, self._failure = time.time(), _plain_failure(exc)
            log.warning("openrouter catalog refresh failed: %s", exc)

    # --- merged views ---

    def _merged(self) -> list[dict]:
        pins = {m["id"]: m for m in load_providers()["models"]}
        state = self.state()
        out: list[dict] = []
        seen: set[str] = set()
        for mid, live in self._live.items():
            pin = pins.get(mid, {})
            out.append({
                **live,
                "label": pin.get("label") or live["name"],
                # the legacy composer's estimate: the config pin when present,
                # the live estimate filling gaps (billed truth stays `usage.cost`);
                # a project's drawer shows what review reserves instead
                # (hosted_images.listed_price)
                "price_usd_per_image": (
                    pin.get("price_usd_per_image")
                    if pin.get("price_usd_per_image") is not None
                    else live.get("price_usd_per_image")),
                "supports_seed": pin.get("supports_seed",
                                         live.get("supports_seed", False)),
                "params": pin.get("params"),
                "pinned": bool(pin.get("enabled")),
                "source": "live",
                "catalogue_state": state,
            })
            seen.add(mid)
        # pins with no live entry yet (offline / first boot): config fallback
        for mid, pin in pins.items():
            if mid in seen:
                continue
            out.append({**pin, "name": pin["label"], "pinned": pin["enabled"],
                        "price_note": None, "source": "config",
                        "catalogue_state": state})
        out.sort(key=lambda m: (not m.get("pinned"), m["provider"], m["id"]))
        # Consumers compile frozen requests from these facts. Mutating a returned
        # nested endpoint must never rewrite the catalogue's cached evidence.
        return deepcopy(out)

    def selectable(self) -> dict[str, dict]:
        """id -> entry for everything a turn may dispatch to (sync, cached)."""
        if not self._snapshot_loaded:
            self._load_snapshot()
        self._refresh_ahead()
        return {m["id"]: m for m in self._merged()}

    async def get(self, force: bool = False) -> dict:
        if not self._snapshot_loaded:
            self._load_snapshot()
        if force or (not self._live and self._older_than(CATALOG_TTL_S)
                     and (self._refreshing() or self._may_start())):
            # explicit refresh, or nothing usable yet: fetch inline, shared
            await asyncio.shield(self._kick_refresh())
        else:
            self._refresh_ahead()  # stale-while-revalidate
        models = self._merged()
        return {
            "fetched_at": (datetime.fromtimestamp(
                self._fetched_at, timezone.utc).isoformat()
                if self._fetched_at else None),
            # count what is actually served (merged), not just the live layer
            "model_count": len(models),
            "models": models,
        }
