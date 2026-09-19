"""OpenRouter image adapter (the
documented `/api/v1/images` contract driven through httpx — the same proven
client comfy.py uses; no hand-rolled HTTP semantics, no per-provider SDKs).

Verifiability: every outbound attempt is counted and logged on the
`cockpit.external` logger BEFORE the request leaves, and a local-only turn
never reaches this module (the Run route refuses it, api_v4.py). Failures map to
per-slot errors — a bad model route (402/429/502/503 on one slug) degrades
only that slot; a dead key surfaces the same way on every external
slot of the turn and never touches local ones.

Billing note (recorded from OpenRouter docs 2026-07-17): generation billing
is all-or-nothing — failed or cancelled generations are not billed.

B4: a 200 that carries `usage.cost` but no
image is "charged, nothing returned" — its own kind, `charged_blank`,
carrying the amount the response reported and the generation id. There is
no way to re-fetch the IMAGE (the endpoint never re-serves one), so the
free re-ask is the BILL: `generation_cost` reads GET /generation?id= —
the generation record, whose `total_cost` is the billed truth and which
lags the response by seconds — and the scheduler asks it three times over
about a minute before calling the amount unverified.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

from . import config

log = logging.getLogger("cockpit.external")

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
                 retry_after_s: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.charged_usd = charged_usd   # charged_blank: what the response said
        self.job_id = job_id             # the generation id, when one came back
        self.retry_after_s = retry_after_s   # B2: the provider's own window


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
            msg = (body.get("error") or {}).get("message") or fallback
        wait = retry_after_from(headers) if kind == "rate_limit" else None
        return ExternalJobError(kind, f"{msg} (HTTP {status})", status,
                                retry_after_s=wait)

    async def generate(self, *, model: str, prompt: str,
                       seed: int | None = None,
                       params: dict | None = None) -> dict:
        """One image via POST /images. Returns
        {png, cost_usd, created, request} — `request` is the EXACT body sent
        (the manifest records what actually went out)."""
        payload: dict = {"model": model, "prompt": prompt, "n": 1,
                         "output_format": "png"}
        if params:
            payload.update(params)
        if seed is not None:
            payload["seed"] = seed

        self.requests_total += 1
        log.info("openrouter request -> model=%s prompt_chars=%d seed=%s",
                 model, len(prompt), seed)
        try:
            resp = await self._client.post("/images", json=payload)
        except httpx.TimeoutException as e:
            # subclass of HTTPError — must be caught first. Its own kind so
            # the scheduler can bench the engine (a 300 s silence is
            # model_down-adjacent) and mark the charge unverified.
            raise ExternalJobError("timeout",
                                   f"no response within the read timeout: {e}")
        except httpx.HTTPError as e:
            raise ExternalJobError("unreachable", f"OpenRouter unreachable: {e}")

        if resp.status_code != 200:
            try:
                body = resp.json()
            except ValueError:
                body = None
            err = self._error_from(resp.status_code, body, resp.headers)
            log.warning("openrouter error <- model=%s kind=%s: %s",
                        model, err.kind, err)
            raise err

        data = resp.json()
        # error-in-200 shape (per OpenRouter error docs)
        if isinstance(data.get("error"), dict):
            err = ExternalJobError(
                "error", data["error"].get("message", "generation failed"),
                resp.status_code)
            log.warning("openrouter error <- model=%s: %s", model, err)
            raise err
        items = data.get("data") or []
        cost = (data.get("usage") or {}).get("cost")
        gid = data.get("id")
        if not items or not items[0].get("b64_json"):
            if cost is not None and float(cost) > 0:
                # B4: the provider took the job and billed it, and nothing
                # came back. The amount and the id ride the error so the
                # slot's ledger entry and its receipt are the response's
                # own words, never a guess.
                log.warning("openrouter charged_blank <- model=%s cost=%s "
                            "id=%s", model, cost, gid)
                raise ExternalJobError(
                    "charged_blank",
                    f"billed {float(cost):.4f} USD but the response carried "
                    "no image", resp.status_code,
                    charged_usd=float(cost), job_id=gid)
            raise ExternalJobError("error", "response contained no image",
                                   resp.status_code, job_id=gid)
        log.info("openrouter done <- model=%s cost=%s", model, cost)
        return {
            "png": base64.b64decode(items[0]["b64_json"]),
            "cost_usd": float(cost) if cost is not None else None,
            "created": data.get("created"),
            "id": gid,
            "request": payload,
        }

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
