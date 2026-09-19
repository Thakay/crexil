"""External provider catalog (OpenRouter, single key).

Two layers, so the bench stays current without ever trusting staleness for
billing (the authoritative cost is always the per-generation `usage.cost`
in the response — the prices here are pre-generation display estimates):

- LIVE: OpenRouter's discovery API (`GET /images/models`, unauthenticated)
  is cached with a TTL and persisted as a snapshot in staging, so the full
  model list + price estimates track OpenRouter without code or config
  churn; offline the last snapshot keeps serving.
- CONFIG: providers.json is not *the* list. It is the pinned bench
  (models surfaced at the top of the picker; `enabled: true` = pinned),
  per-model overrides (label, supports_seed, params; a pinned per-image
  price WINS over the live estimate: the single-source rule, the live
  number/note demotes to secondary display), and the offline fallback
  when no snapshot exists yet.

Per-model price estimates come from the per-endpoint pricing records:
`unit == "image"` maps directly; token-priced models (the Gemini image
family bills per token) get a "$X/M tok" note instead of a made-up
per-image number; megapixel pricing is estimated at ~1MP (1024²).
`supports_seed` is derived live (any serving endpoint declaring a `seed`
parameter) unless the config overrides it — the request records what was
actually sent either way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from . import config

log = logging.getLogger("cockpit.providers")

CATALOG_TTL_S = 6 * 3600  # refresh the live list at most every 6 h


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
        try:
            listing = await self.client.list_models()
        except Exception as e:
            log.warning("openrouter catalog fetch failed (keeping %s): %s",
                        "snapshot" if self._live else "config-only", e)
            return
        sem = asyncio.Semaphore(8)

        async def enrich(m: dict) -> dict:
            entry = {"id": m["id"], "name": m.get("name") or m["id"],
                     "provider": m["id"].split("/", 1)[0],
                     "description": (m.get("description") or "")[:200]}
            try:
                async with sem:
                    eps = await self.client.model_endpoints(m["id"])
                entry["price_usd_per_image"], entry["price_note"] = \
                    _price_estimate(eps)
                entry["supports_seed"] = _supports_seed(eps)
            except Exception:
                entry["price_usd_per_image"] = entry["price_note"] = None
                entry["supports_seed"] = False
            return entry

        enriched = await asyncio.gather(*(enrich(m) for m in listing
                                          if m.get("id")))
        self._live = {e["id"]: e for e in enriched}
        self._fetched_at = time.time()
        self._save_snapshot()
        log.info("openrouter catalog refreshed: %d models", len(self._live))

    def _kick_refresh(self) -> asyncio.Task:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh())
        return self._refresh_task

    # --- merged views ---

    def _merged(self) -> list[dict]:
        pins = {m["id"]: m for m in load_providers()["models"]}
        out: list[dict] = []
        seen: set[str] = set()
        for mid, live in self._live.items():
            pin = pins.get(mid, {})
            out.append({
                **live,
                "label": pin.get("label") or live["name"],
                # the config pin is the display price when present; the live
                # estimate only fills gaps (billed truth stays `usage.cost`)
                "price_usd_per_image": (
                    pin.get("price_usd_per_image")
                    if pin.get("price_usd_per_image") is not None
                    else live.get("price_usd_per_image")),
                "supports_seed": pin.get("supports_seed",
                                         live.get("supports_seed", False)),
                "params": pin.get("params"),
                "pinned": bool(pin.get("enabled")),
                "source": "live",
            })
            seen.add(mid)
        # pins with no live entry yet (offline / first boot): config fallback
        for mid, pin in pins.items():
            if mid in seen:
                continue
            out.append({**pin, "name": pin["label"], "pinned": pin["enabled"],
                        "price_note": None, "source": "config"})
        out.sort(key=lambda m: (not m.get("pinned"), m["provider"], m["id"]))
        return out

    def selectable(self) -> dict[str, dict]:
        """id -> entry for everything a turn may dispatch to (sync, cached)."""
        if not self._snapshot_loaded:
            self._load_snapshot()
        return {m["id"]: m for m in self._merged()}

    async def get(self, force: bool = False) -> dict:
        if not self._snapshot_loaded:
            self._load_snapshot()
        stale = (self._fetched_at is None
                 or time.time() - self._fetched_at > CATALOG_TTL_S)
        if force or (stale and not self._live):
            # nothing usable yet (or explicit refresh): fetch inline
            await self._refresh()
        elif stale:
            self._kick_refresh()  # stale-while-revalidate
        models = self._merged()
        return {
            "fetched_at": (datetime.fromtimestamp(
                self._fetched_at, timezone.utc).isoformat()
                if self._fetched_at else None),
            # count what is actually served (merged), not just the live layer
            "model_count": len(models),
            "models": models,
        }
