"""ComfyUI client: ComfyUI's own queue is the scheduler; this client
submits, mirrors progress over the /ws socket, and fetches results over the
/api routes. The cockpit adds no job system of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import uuid
from typing import Awaitable, Callable

import httpx
import websockets

from . import config

log = logging.getLogger("cockpit.comfy")

Event = dict
EventHandler = Callable[[Event], Awaitable[None]]


class ComfyClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or config.COMFYUI_URL).rstrip("/")
        self.client_id = uuid.uuid4().hex
        self._http = httpx.AsyncClient(base_url=self.base, timeout=60.0)
        self._handler: EventHandler | None = None
        self._ws_task: asyncio.Task | None = None

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        await self._http.aclose()

    # --- REST ---

    async def submit(self, graph: dict) -> str:
        r = await self._http.post("/api/prompt",
                                  json={"prompt": graph, "client_id": self.client_id})
        r.raise_for_status()
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        r = await self._http.get(f"/api/history/{prompt_id}")
        r.raise_for_status()
        return r.json().get(prompt_id)

    async def view(self, filename: str, subfolder: str = "",
                   type_: str = "output") -> bytes:
        qs = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": type_})
        r = await self._http.get(f"/api/view?{qs}")
        r.raise_for_status()
        return r.content

    async def upload_image(self, blob: bytes, name: str) -> str:
        """Push an image into ComfyUI's input dir (image ingress).

        Callers pass content-addressed names (sha256-prefixed) so re-uploads
        are idempotent and stored graphs stay small — never base64-in-graph.
        Returns the server-side name to reference from LoadImage nodes.
        """
        r = await self._http.post(
            "/api/upload/image",
            files={"image": (name, blob, "image/png")},
            data={"overwrite": "true", "type": "input"})
        r.raise_for_status()
        j = r.json()
        sub = j.get("subfolder")
        return f"{sub}/{j['name']}" if sub else j["name"]

    async def system_stats(self) -> dict:
        r = await self._http.get("/api/system_stats", timeout=5.0)
        r.raise_for_status()
        return r.json()

    async def interrupt(self) -> None:
        """Interrupt whatever prompt is CURRENTLY executing (cancel).
        ComfyUI has no per-prompt interrupt — the caller must verify via
        queue_state() that the running prompt is ours before firing."""
        r = await self._http.post("/api/interrupt")
        r.raise_for_status()

    async def delete_queued(self, prompt_id: str) -> None:
        """Drop a still-queued prompt before it costs GPU time."""
        r = await self._http.post("/api/queue", json={"delete": [prompt_id]})
        r.raise_for_status()

    async def queue_state(self) -> dict:
        r = await self._http.get("/api/queue")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def output_files(history_entry: dict) -> list[dict]:
        """Retain every file descriptor with its runtime node, field and order.

        Roles cannot be inferred from a filename. Non-file output values stay
        in the history record and are never interpreted as filesystem paths.
        """
        files = []
        for node_id, out in (history_entry.get("outputs") or {}).items():
            if not isinstance(out, dict):
                continue
            for field, values in out.items():
                if not isinstance(values, list):
                    continue
                for ordinal, value in enumerate(values):
                    if isinstance(value, dict) and isinstance(value.get("filename"), str):
                        files.append({**value, "node_id": str(node_id), "field": field, "ordinal": ordinal})
        return files

    @staticmethod
    def first_image(history_entry: dict) -> dict | None:
        for out in (history_entry.get("outputs") or {}).values():
            for img in out.get("images") or []:
                if img.get("type") == "output":
                    return img
        return None

    # --- WS progress mirror ---

    def start_ws_mirror(self, handler: EventHandler) -> None:
        self._handler = handler
        self._ws_task = asyncio.create_task(self._ws_loop(), name="comfy-ws-mirror")

    async def _ws_loop(self) -> None:
        ws_url = (self.base.replace("http://", "ws://").replace("https://", "wss://")
                  + f"/ws?clientId={self.client_id}")
        while True:
            try:
                async with websockets.connect(ws_url, max_size=2 ** 24) as ws:
                    log.info("ws mirror connected")
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue  # preview frames, not used
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if self._handler:
                            await self._handler(msg)
            except asyncio.CancelledError:
                return
            except Exception as e:  # reconnect on any drop
                log.warning("ws mirror dropped (%s); reconnecting in 2s", e)
                await asyncio.sleep(2)
