"""Cockpit entrypoint. Serves the built SPA + the /api routes on localhost;
ComfyUI stays a separate native service that this app only drives
over HTTP/WS (the cockpit never generates anything itself).

Dev:    cd cockpit/server && uv run uvicorn app.main:app --reload
Docker: see repo Dockerfile / compose.yaml
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from urllib.parse import urlparse

from fastapi.middleware.trustedhost import TrustedHostMiddleware

from . import (api, api_crexil, api_v4, api_v4_mesh, api_v4_promote, api_v4_regions, config,
               wiring)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class LocalOriginMiddleware:
    """Reject browser requests whose Origin is not localhost (CSRF / DNS-
    rebinding hardening for a deliberately auth-less localhost app).
    Pure ASGI so it covers WebSockets too: a future live feed
    inherits the guard; without it, any website open in the local browser
    could attach cross-origin and read events (prompts of unreleased
    work)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            origin = next((v.decode("latin1")
                           for k, v in scope.get("headers") or []
                           if k == b"origin"), None)
            if origin:
                # "null" (sandboxed iframes, file://) is attacker-territory:
                # legit use is always the localhost-served SPA
                host = (urlparse(origin).hostname or "").lower()
                if origin == "null" or host not in LOCAL_HOSTS:
                    if scope["type"] == "http":
                        await send({"type": "http.response.start",
                                    "status": 403,
                                    "headers": [(b"content-type",
                                                 b"application/json")]})
                        await send({"type": "http.response.body",
                                    "body": b'{"detail":"cross-origin request'
                                            b' blocked (localhost-only)"}'})
                    else:
                        await send({"type": "websocket.close", "code": 4403})
                    return
        await self.app(scope, receive, send)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cockpit")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # the whole stack boots in wiring.start: presets, the shared OpenRouter
    # client, the store on COCKPIT_DB, the two-lane scheduler over real
    # executors. A failed boot releases everything it opened before raising
    # (a leaked aiosqlite connection is a non-daemon thread that turns a
    # loud config error into a hang).
    stack = await wiring.start()
    log.info("cockpit up: presets=%s comfy=%s db=%s external_bench=%s",
             sorted(stack.catalog), config.COMFYUI_URL, stack.db.path,
             "on" if stack.openrouter.key_present else "off (no key)")
    try:
        yield
    finally:
        # runs even when an exception is thrown in at the yield (a crashed
        # shutdown, a failing test inside the lifespan) — otherwise the
        # aiosqlite connection threads outlive the loop and block exit.
        try:
            await wiring.stop(stack)
        except Exception:
            log.exception("lifespan teardown step failed")


app = FastAPI(title="Crexil", lifespan=lifespan)
# hardening for an auth-less localhost app: Host allow-list defeats
# DNS rebinding, Origin allow-list defeats cross-site requests to localhost
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
app.add_middleware(LocalOriginMiddleware)
app.include_router(api.router)
app.include_router(api_crexil.router)
app.include_router(api_v4.router)
app.include_router(api_v4_regions.router)
app.include_router(api_v4_promote.router)
app.include_router(api_v4_mesh.router)


# A route that crashes answers in words (law 3: no status codes,
# no stack traces on the face) and leaves a ledger row the words point at.
# HTTPExceptions and validation errors are refusals with their own words
# and never come through here; this is the net for the cockpit's own bugs.
INTERNAL_500 = (
    "The cockpit hit an internal error on this request. Nothing is known to "
    "have been spent — what changed, if anything, is in the record{where}. "
    "Try once more; if it repeats, the record carries what a bug report needs.")

# route path params -> ledger ids, best-effort (the events table is un-FK'd)
_ID_PARAMS = {"wid": "workspace_id", "workspace_id": "workspace_id",
              "turn_id": "turn_id", "slot_id": "slot_id",
              "nid": "node_id", "node_id": "node_id"}


@app.exception_handler(Exception)
async def unhandled_failure(request: Request, exc: Exception) -> JSONResponse:
    stack = wiring.current
    row = None
    if stack is not None and stack.capture is not None:
        ids = {col: str(v) for key, col in _ID_PARAMS.items()
               if (v := request.path_params.get(key)) is not None}
        row = await stack.capture.record(
            f"api {request.method} {request.url.path}", exc, **ids,
            note="the request was answered 500 in words; nothing else was "
                 "done for it")
    seq = row["seq"] if row else None
    where = (f" (event #{seq})" if seq is not None else
             " — except the store was closed, so only the cockpit's log has it")
    return JSONResponse(status_code=500,
                        content={"detail": INTERNAL_500.format(where=where),
                                 "event_seq": seq})
if config.HARNESS_EXECUTORS:
    # e2e harness controls: env-gated (playwright only) and
    # schema-hidden, so the committed OpenAPI contract cannot flap
    from .harness import router as harness_router
    app.include_router(harness_router)


# human pointers, not contract surface — and no_spa's registration depends on
# the environment, which would flap the committed OpenAPI snapshot
@app.get("/api", include_in_schema=False)
async def api_index():
    return JSONResponse({"name": "Crexil", "see": "/api/health"})


if config.STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True),
              name="spa")
else:
    @app.get("/", include_in_schema=False)
    async def no_spa():
        return JSONResponse(
            {"note": "SPA not built yet; API is live under /api",
             "static_dir": str(config.STATIC_DIR)})
