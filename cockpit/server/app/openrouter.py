"""Transport native OpenRouter images and preserve exact dispatch receipts.

Frozen requests are copied and sent without parameter merging or retries.
Every output is independently validated, with original raster bytes and full
provider metadata retained. An optional callback durably collects each result
before the next output is fetched. HTTPS output retrieval never carries the
OpenRouter key and rejects private destinations and redirects.

Legacy generate returns one verified PNG. Errors retain the request, response
and reported charge when available; transport failure never proves no charge.
This adapter supplies no spending guarantee: tariff selection, authorization
and reservations belong to the caller before dispatch.
"""

from __future__ import annotations

import base64
import asyncio
import binascii
import copy
import io
import ipaddress
import json
import logging
import math
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from . import config

log = logging.getLogger("cockpit.external")

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_RASTER_FORMATS = {"PNG": ("image/png", ".png"),
                   "JPEG": ("image/jpeg", ".jpg"),
                   "WEBP": ("image/webp", ".webp")}
_LEGACY_PROTECTED = {"model", "prompt", "n", "output_format", "seed",
                     "input_references", "stream"}

# error kinds, coarse on purpose: the UI shows the message, the e2e asserts
# the kind. "dead_key" covers invalid key AND exhausted credits — both kill
# the whole external bench, which is the single-key trade.
_STATUS_KINDS = {
    401: ("dead_key", "OpenRouter key invalid or disabled"),
    402: ("dead_key", "OpenRouter account out of credits"),
    403: ("moderation", "request flagged by provider moderation"),
    429: ("rate_limit", "rate limited by OpenRouter"),
    502: ("model_down", "model is down or returned an invalid response"),
    503: ("model_down", "no provider currently serves this model"),
}


class ExternalJobError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None,
                 *, charged_usd: float | None = None,
                 job_id: str | None = None,
                 retry_after_s: float | None = None,
                 receipt: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.charged_usd = charged_usd   # charged_blank: what the response said
        self.job_id = job_id             # the generation id, when one came back
        self.retry_after_s = retry_after_s   # B2: the provider's own window
        self.receipt = receipt or {}


def _reported_cost(body) -> float | None:
    usage = body.get("usage") if isinstance(body, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, bool):
        return None
    try:
        parsed = float(cost)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _validated_image(raw: bytes, metadata: dict) -> dict:
    """Keep original bytes; the detected encoding determines the extension."""
    if len(raw) > _MAX_OUTPUT_BYTES:
        raise ExternalJobError("invalid_output", "image exceeds the 64 MiB output limit")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            actual = _RASTER_FORMATS.get(im.format)
            if actual is None:
                raise ExternalJobError("invalid_output", "unsupported image encoding")
            width, height = im.size
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ExternalJobError("invalid_output", f"invalid image bytes: {exc}")
    media_type, extension = actual
    declared = metadata.get("media_type")
    if declared and declared != media_type:
        raise ExternalJobError("invalid_output", "image media_type does not match its bytes")
    return {"bytes": raw, "media_type": media_type, "extension": extension,
            "width": width, "height": height, "metadata": metadata}


async def _public_https_url(url: str) -> tuple[httpx.URL, str]:
    """Validate each download destination before the unauthenticated request."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            raise ValueError("output URL must be public HTTPS without credentials")
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(rec[4][0]).is_global
                                for rec in addresses):
            raise ValueError("output URL points to a non-public address")
        # Pin the resolved address so a second DNS lookup cannot switch the
        # download to a private host. TLS still verifies the original hostname.
        original = httpx.URL(url)
        return original.copy_with(host=addresses[0][4][0]), original.raw_host.decode("ascii")
    except (ValueError, OSError, httpx.InvalidURL) as exc:
        raise ExternalJobError("invalid_output", f"unsafe output URL: {exc}")


def retry_after_from(headers, now: float | None = None) -> float | None:
    """B2: how long the provider asked us to wait, in seconds. Read from
    `Retry-After` as seconds or an HTTP date, else `X-RateLimit-Reset` as
    an epoch (seconds or milliseconds). None when it named no window; the
    scheduler then waits its own default and says so."""
    now = time.time() if now is None else now
    ra = headers.get("Retry-After") if headers is not None else None
    if ra:
        ra = ra.strip()
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                when = parsedate_to_datetime(ra).timestamp()
                return max(0.0, when - now)
            except (TypeError, ValueError, IndexError):
                pass
    reset = headers.get("X-RateLimit-Reset") if headers is not None else None
    if reset:
        try:
            when = float(reset.strip())
        except ValueError:
            return None
        if when > 1e11:                  # milliseconds since the epoch
            when /= 1000.0
        if when > now - 3600:            # an epoch, not a duration
            return max(0.0, when - now)
        return max(0.0, when)            # a plain duration in seconds
    return None


class OpenRouterClient:
    def __init__(self, base_url: str | None = None,
                 api_key: str | None = None) -> None:
        self.base_url = (base_url or config.OPENROUTER_URL).rstrip("/")
        self.api_key = config.OPENROUTER_API_KEY if api_key is None else api_key
        # no Authorization header without a key: the discovery endpoints are
        # public and an empty "Bearer" would turn them into 401s
        headers = {"HTTP-Referer": "http://localhost",
                   "X-Title": "Crexil"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # image generations regularly take tens of seconds; connect fast, read long
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers,
            timeout=httpx.Timeout(300.0, connect=10.0))
        # adapter-side telemetry for local-only's "provably no external
        # calls": /api/health surfaces these, the e2e asserts they do not
        # move under a local-only turn.
        self.requests_total = 0
        self.key_source: str | None = "env" if self.api_key else None

    @property
    def key_present(self) -> bool:
        return bool(self.api_key)

    def set_key(self, key: str, source: str | None = None) -> None:
        """Swap the active key at runtime (setup UI) — no restart needed.
        The key itself is never logged."""
        self.api_key = (key or "").strip()
        if self.api_key:
            self._client.headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            self._client.headers.pop("Authorization", None)
        self.key_source = source if self.api_key else None
        log.info("openrouter key %s (source=%s)",
                 "set" if self.api_key else "cleared", self.key_source)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _error_from(status: int, body: dict | None,
                    headers=None) -> ExternalJobError:
        kind, fallback = _STATUS_KINDS.get(status, ("error", "external call failed"))
        msg = fallback
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                msg = error.get("message") or fallback
        wait = retry_after_from(headers) if kind == "rate_limit" else None
        return ExternalJobError(kind, f"{msg} (HTTP {status})", status,
                                retry_after_s=wait)

    async def _download_output(self, url: str) -> bytes:
        destination, hostname = await _public_https_url(url)
        # This client has no API key, cookies, proxy credentials or redirects.
        async with (
            httpx.AsyncClient(timeout=60.0, trust_env=False) as client,
            client.stream("GET", destination, follow_redirects=False,
                          headers={"Host": f"[{hostname}]" if ":" in hostname else hostname},
                          extensions={"sni_hostname": hostname}) as response,
        ):
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > _MAX_OUTPUT_BYTES:
                    raise ExternalJobError("invalid_output", "image exceeds the 64 MiB output limit")
            if response.status_code != 200:
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    body = raw.decode("utf-8", errors="replace")
                raise ExternalJobError(
                    "invalid_output", f"image download returned HTTP {response.status_code}",
                    receipt={"download_url": url, "status": response.status_code,
                             "headers": dict(response.headers), "response": body})
            return bytes(raw)

    async def _output_bytes(self, item: dict) -> bytes:
        encoded = item.get("b64_json")
        if encoded is None:
            url = item.get("url")
            if not isinstance(url, str):
                raise ExternalJobError("invalid_output", "output has no image bytes or supported URL")
            if url.startswith("data:"):
                prefix, separator, encoded = url.partition(",")
                if not separator or not prefix.startswith("data:image/") or not prefix.endswith(";base64"):
                    raise ExternalJobError("invalid_output", "unsupported image data URL")
            else:
                return await self._download_output(url)
        try:
            if not isinstance(encoded, str) or len(encoded) > 4 * ((_MAX_OUTPUT_BYTES + 2) // 3):
                raise ValueError("missing or oversized base64 image")
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ExternalJobError("invalid_output", f"invalid base64 image: {exc}")

    async def generate_frozen(
        self, request: dict, *,
        on_output: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Send a precompiled native request without merging controls or retrying.

        Returns every valid raster in outputs (bytes, actual media_type,
        extension, dimensions, index, original metadata), plus output_errors
        and the complete JSON-safe receipt. Callback events carry kind=output
        or output_error, the matching record and receipt. An initial kind=receipt
        event precedes status handling and output retrieval. Collection failures
        and cancellation propagate; prior callback receipts remain usable.
        """
        try:
            payload = json.loads(json.dumps(request, allow_nan=False))
            if not isinstance(payload, dict) or payload != request:
                raise ValueError("request must be a JSON object")
            if any(not isinstance(payload.get(k), str) or not payload[k]
                   for k in ("model", "prompt")):
                raise ValueError("model and prompt must be nonempty strings")
            count = payload.get("n", 1)
            if type(count) is not int or not 1 <= count <= 10:
                raise ValueError("n must be an integer from 1 to 10")
            if payload.get("stream", False) is not False:
                raise ValueError("frozen image transport requires a buffered response")
            if payload.get("output_format", "png") not in ("png", "jpeg", "webp"):
                raise ValueError("frozen image transport supports PNG, JPEG and WebP")
            refs = payload.get("input_references", [])
            if not isinstance(refs, list) or len(refs) > 16:
                raise ValueError("input_references must be an array of up to 16 images")
            for ref in refs:
                if (not isinstance(ref, dict) or ref.get("type") != "image_url"
                        or not isinstance(ref.get("image_url"), dict)
                        or not isinstance(ref["image_url"].get("url"), str)
                        or not ref["image_url"]["url"].startswith(("data:image/", "https://", "http://"))):
                    raise ValueError("references require type=image_url and image_url.url")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExternalJobError("invalid_request", str(exc), charged_usd=0)

        receipt = {"request": payload, "response": None, "response_status": None,
                   "cost_usd": None, "created": None, "id": None}
        self.requests_total += 1
        log.info("openrouter request -> model=%s prompt_chars=%d seed=%s",
                 payload["model"], len(payload["prompt"]), payload.get("seed"))
        try:
            response = await self._client.post("/images", json=payload)
        except httpx.TimeoutException as exc:
            raise ExternalJobError("timeout", f"no response within the read timeout: {exc}",
                                   receipt=receipt)
        except httpx.HTTPError as exc:
            raise ExternalJobError("unreachable", f"OpenRouter unreachable: {exc}",
                                   receipt=receipt)

        try:
            body = response.json()
        except ValueError:
            body = response.text
        receipt.update(response=body, response_status=response.status_code,
                       response_headers=dict(response.headers), cost_usd=_reported_cost(body))
        if isinstance(body, dict):
            receipt.update(id=body.get("id"), created=body.get("created"))
        return await self.collect_receipt(receipt, on_output=on_output)

    async def collect_receipt(
        self, receipt: dict, *,
        on_output: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Collect a retained response without generation or paid-request counting.

        Base64 bytes replay locally; URL outputs use the same unauthenticated
        validated download path. The original receipt callback is unchanged on
        every replay. Collection errors belong to the result, never the supplied
        receipt or its callback, so durable receipt bytes keep their identity.
        """
        receipt = copy.deepcopy(receipt)
        body, status = receipt.get("response"), receipt.get("response_status")
        if on_output is not None:
            await on_output({"kind": "receipt", "receipt": receipt})
        if status != 200:
            error = self._error_from(status, body, httpx.Headers(receipt.get("response_headers") or {}))
            error.receipt = receipt
            error.charged_usd = receipt["cost_usd"]
            error.job_id = receipt["id"]
            raise error
        if not isinstance(body, dict):
            raise ExternalJobError("error", "response was not a JSON object", 200, receipt=receipt)

        outputs, errors = [], []
        if body.get("error") is not None:
            provider_error = body["error"]
            error = {"index": None, "kind": "provider_error",
                     "message": (provider_error.get("message", "generation failed")
                                 if isinstance(provider_error, dict) else str(provider_error)),
                     "metadata": provider_error, "details": {}}
            errors.append(error)
            if on_output is not None:
                await on_output({"kind": "output_error", "output_error": error, "receipt": receipt})
        items = body.get("data") or []
        if not isinstance(items, list):
            items = [items]  # retain a malformed output record as a visible error
        for index, item in enumerate(items):
            try:
                if not isinstance(item, dict):
                    raise ExternalJobError("invalid_output", "image output was not an object")
                raw = await self._output_bytes(item)
                output = {"index": index, **_validated_image(raw, item)}
            except (ExternalJobError, httpx.HTTPError) as exc:
                error = {"index": index, "kind": getattr(exc, "kind", "invalid_output"),
                         "message": str(exc), "metadata": item,
                         "details": getattr(exc, "receipt", {})}
                errors.append(error)
                event = {"kind": "output_error", "output_error": error, "receipt": receipt}
            else:
                outputs.append(output)
                event = {"kind": "output", "output": output, "receipt": receipt}
            if on_output is not None:
                await on_output(event)

        collected_receipt = {**receipt, "output_errors": errors}
        if not outputs:
            cost = receipt["cost_usd"]
            provider_error = body.get("error")
            message = (provider_error.get("message")
                       if isinstance(provider_error, dict) else None)
            kind = "charged_blank" if cost is not None and cost > 0 else "error"
            if not message:
                message = (f"billed {cost:.4f} USD but the response carried no usable image"
                           if kind == "charged_blank" else "response contained no usable image")
            raise ExternalJobError(kind, message, status,
                                   charged_usd=cost, job_id=receipt["id"], receipt=collected_receipt)
        log.info("openrouter done <- model=%s cost=%s outputs=%d",
                 receipt["request"]["model"], receipt["cost_usd"], len(outputs))
        return {**collected_receipt, "outputs": outputs}

    async def generate(self, *, model: str, prompt: str,
                       seed: int | None = None,
                       params: dict | None = None) -> dict:
        """Legacy one-PNG entry point, with exact request and result receipts.

        Display config can supply optional image controls but cannot replace
        the requested model, prompt, count, format, seed or source references.
        """
        if params and _LEGACY_PROTECTED.intersection(params):
            raise ExternalJobError("invalid_request", "params cannot override protected image request fields",
                                   charged_usd=0)
        payload = {"model": model, "prompt": prompt, "n": 1,
                   "output_format": "png", **(params or {})}
        if seed is not None:
            payload["seed"] = seed
        result = await self.generate_frozen(payload)
        first = result["outputs"][0]
        if first["media_type"] != "image/png":
            receipt = {k: v for k, v in result.items() if k != "outputs"}
            raise ExternalJobError("invalid_output", "legacy image request returned a non-PNG image",
                                   charged_usd=result["cost_usd"], job_id=result["id"],
                                   receipt=receipt)
        return {**result, "png": first["bytes"]}

    async def generation_cost(self, generation_id: str) -> float | None:
        """B4's free re-ask: the generation record's `total_cost` (GET
        /generation?id=), or None when the record isn't there yet, the
        call fails, or the field is missing. Never raises — an unanswered
        ask is an answer the scheduler counts."""
        try:
            resp = await self._client.get(
                "/generation", params={"id": generation_id}, timeout=15.0)
        except httpx.HTTPError as e:
            log.info("openrouter generation lookup failed id=%s: %s",
                     generation_id, e)
            return None
        if resp.status_code != 200:
            return None
        try:
            rec = (resp.json() or {}).get("data") or {}
        except ValueError:
            return None
        cost = rec.get("total_cost")
        return float(cost) if cost is not None else None

    # --- discovery (public endpoints; feed the ProviderCatalog cache) ---

    async def list_models(self) -> list[dict]:
        """All image-capable models on OpenRouter (GET /images/models)."""
        resp = await self._client.get("/images/models", timeout=20.0)
        resp.raise_for_status()
        return (resp.json().get("data")) or []

    async def model_endpoints(self, model_id: str) -> list[dict]:
        """Per-model serving endpoints incl. pricing records."""
        resp = await self._client.get(
            f"/images/models/{model_id}/endpoints", timeout=20.0)
        resp.raise_for_status()
        return (resp.json().get("endpoints")) or []

    async def probe(self, candidate_key: str | None = None) -> dict:
        """Key/reachability check — GET /key costs nothing and
        authenticates. Never raises. With `candidate_key`, probes THAT key
        (setup validation) without touching the active client state."""
        headers = None
        if candidate_key is not None:
            headers = {"Authorization": f"Bearer {candidate_key}"}
        elif not self.key_present:
            return {"ok": False, "note": "no key configured"}
        try:
            resp = await self._client.get("/key", timeout=5.0,
                                          headers=headers)
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"unreachable: {e}"[:200]}
        if resp.status_code == 401:
            return {"ok": False, "error": "key rejected (401)"}
        return {"ok": resp.status_code == 200,
                "status": resp.status_code}
