"""The app-level routes: health, the preset catalogue, the external model
bench and the OpenRouter setup surface. Everything about a workspace lives
in `api_v4*.py`; this module is what the deploy script, the footer and the
composer's rosters read regardless of workspace. State comes from the
running stack (`wiring.current`) — nothing here holds its own."""

from __future__ import annotations

import logging
import time

import os

from fastapi import APIRouter, HTTPException, Request, Response

from . import config, secrets_store, wiring
from .schemas import (HealthInfo, Preset, ProvidersInfo, SetupKey,
                      SetupStatus)

log = logging.getLogger("cockpit.api")
router = APIRouter(prefix="/api")


def _stack() -> wiring.Stack:
    stack = wiring.current
    if stack is None:
        raise HTTPException(503, "the cockpit is still booting")
    return stack


@router.get("/health", response_model=HealthInfo,
            responses={503: {"model": HealthInfo,
                             "description": "degraded — same shape"}})
async def health(response: Response):
    stack = _stack()
    checks: dict = {"db": True,
                    "build": {"id": config.BUILD_ID,
                              "time": config.BUILD_TIME}}
    if stack.comfy is None:
        # scripted executors (pytest fakes, the e2e harness) own no
        # ComfyClient — degraded is the honest answer, not a guess
        checks["comfyui"] = {"ok": False,
                             "error": "no ComfyUI client under scripted "
                                      "executors"}
    else:
        try:
            stats = await stack.comfy.system_stats()
            checks["comfyui"] = {"ok": True,
                                 "version": stats["system"]["comfyui_version"]}
        except Exception as e:
            checks["comfyui"] = {"ok": False, "error": str(e)[:200]}
    checks["presets"] = sorted(stack.catalog)
    # loud when a mount comes up empty: zero presets or an unreadable
    # workflows/models tree is a 503, not a quietly empty catalog
    checks["preset_count"] = len(stack.catalog)
    checks["workflows_dir"] = {
        "path": str(config.WORKFLOWS_DIR),
        "ok": any(config.WORKFLOWS_DIR.glob("*.json"))}
    checks["models_root"] = {
        "path": str(config.MODELS_ROOT),
        "ok": config.MODELS_ROOT.is_dir() and any(config.MODELS_ROOT.iterdir())}
    checks["promote_dir"] = {"path": str(config.PROMOTE_DIR),
                             "mounted": config.PROMOTE_DIR.parent.exists()}
    checks["openrouter"] = await _openrouter_check()
    # the external bench never gates overall health: a keyless/offline
    # deployment is a valid local-only cockpit
    ok = (checks["comfyui"].get("ok", False) and checks["preset_count"] > 0
          and checks["workflows_dir"]["ok"])
    # deploy.sh's `curl -sf` wait loop and other health probes key on this split
    response.status_code = 200 if ok else 503
    return checks


# openrouter reachability probe, cached so frequent health polls (the UI
# footer, deploy.sh, probes) do not hammer the API
_or_probe: dict = {"ts": 0.0, "result": None}


async def _openrouter_check() -> dict:
    orc = _stack().openrouter
    out: dict = {"configured": orc.key_present}
    # Telemetry: proves (non-)dispatch without log parsing
    out["requests_total"] = orc.requests_total
    if not out["configured"]:
        return out
    if _or_probe["result"] is None or time.time() - _or_probe["ts"] > 300:
        _or_probe["result"] = await orc.probe()
        _or_probe["ts"] = time.time()
    out.update(_or_probe["result"])
    return out


@router.get("/presets", response_model=list[Preset])
async def presets():
    # reload on every catalog fetch: editing/adding presets/*.json is live;
    # the next fetch picks the change up (no restarts)
    from .presets import PresetError, load_presets
    try:
        catalog = load_presets()
    except PresetError as e:
        raise HTTPException(500, f"preset config error: {e}")
    _stack().catalog = catalog
    return list(catalog.values())


@router.get("/providers", response_model=ProvidersInfo)
async def providers_catalog(refresh: bool = False):
    """External model bench: the LIVE OpenRouter catalog (TTL-cached,
    snapshot-persisted) merged with providers.json pins/overrides. Prices
    are pre-generation estimates — the billed truth is per-response
    `usage.cost`. `?refresh=1` forces a live re-fetch."""
    from .providers import ProviderError
    stack = _stack()
    try:
        data = await stack.provider_catalog.get(force=refresh)
    except ProviderError as e:
        raise HTTPException(500, f"providers config error: {e}")
    return {"transport": "openrouter",
            "key_present": stack.openrouter.key_present,
            **data}


# --- OpenRouter setup ----------------------------------------------------
# The key-WRITE surface of a deliberately auth-less localhost app: guarded by
# the app-wide Host/Origin middleware (main.py) PLUS a loopback gate and a
# custom-header requirement (forces a CORS preflight, killing simple-request
# CSRF). The key is never echoed back (only a masked form: the first six and
# last four characters) and never logged.


def _masked(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…"


def _setup_status() -> dict:
    orc = _stack().openrouter
    return {
        "configured": orc.key_present,
        "source": getattr(orc, "key_source", None),
        "masked": _masked(orc.api_key) if orc.key_present else None,
        "env_overrides": bool(config.OPENROUTER_API_KEY),
    }


def _setup_gate(request: Request) -> None:
    # loopback tripwire: if this app is ever exposed beyond localhost, the
    # key-write surface refuses until a real auth story exists. The
    # docker path sets COCKPIT_LOOPBACK_PUBLISH=1 because compose publishes
    # on 127.0.0.1 while the container sees the bridge address.
    client = (request.client.host if request.client else "") or ""
    if client not in ("127.0.0.1", "::1") \
            and os.environ.get("COCKPIT_LOOPBACK_PUBLISH") != "1":
        raise HTTPException(403, "setup is localhost-only; a remote "
                            "deployment needs a real auth story first")
    if request.headers.get("x-cockpit-setup") != "1":
        raise HTTPException(403, "missing X-Cockpit-Setup header")


@router.get("/setup/openrouter", response_model=SetupStatus)
async def setup_status():
    out = _setup_status()
    out["probe"] = await _openrouter_check()
    return out


@router.post("/setup/openrouter", response_model=SetupStatus)
async def setup_save(request: Request, body: SetupKey):
    _setup_gate(request)
    key = body.api_key.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        raise HTTPException(400, "that does not look like an OpenRouter API key")
    orc = _stack().openrouter
    probe = await orc.probe(candidate_key=key)
    if not probe.get("ok"):
        raise HTTPException(
            400, f"key check failed: {probe.get('error') or probe}")
    secrets_store.set_secret("openrouter_api_key", key)
    if config.OPENROUTER_API_KEY:
        note = "stored, but the .env key overrides it while set"
    else:
        orc.set_key(key, source="stored")
        note = None
    _or_probe["result"] = None  # health re-probes with the new key
    out = _setup_status()
    out["probe"] = await _openrouter_check()
    if note:
        out["note"] = note
    return out


@router.delete("/setup/openrouter", response_model=SetupStatus)
async def setup_delete(request: Request):
    _setup_gate(request)
    secrets_store.delete_secret("openrouter_api_key")
    orc = _stack().openrouter
    env_key = config.OPENROUTER_API_KEY
    orc.set_key(env_key or "", source="env" if env_key else None)
    _or_probe["result"] = None
    return _setup_status()
