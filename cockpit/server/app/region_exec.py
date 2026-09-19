"""RegionExecutor: one region take = one multi-pass pipeline on the
local lane, behind the ordinary Executor protocol.

The scheduler stays payload-agnostic: a region take is registered like any
other because the whole plan rides `DispatchRequest.submitted_graph` as the
`{"region_plan": …}` envelope (regionrun.build_engine_envelope). This
executor walks the envelope pass by pass — crop the working frame to the
pass's plan-time box, content-address the crop into ComfyUI's input dir,
submit the pass graph through an inner ComfyExecutor (confirm-poll, blank
detection, oom classification and interrupt semantics reused verbatim),
composite the result back (crop.paste_back — whole box for the mask-native
rails, through the feathered effective mask for the instruction tiers) —
then restores KEEP byte-exactly (regions.apply_keep) and hands the frame
to the scheduler as one ordinary landing.

Restart truth: `start()` returns immediately with job_ref=None and the
pipeline runs inside `wait()`, so a crash mid-pipeline leaves the slot
"running without job_ref" — recovery's safe-requeue rule re-runs the
envelope from pass 0 (idempotent: content-addressed uploads, the take's
one seed baked into every pass; the cost is GPU time only). `reconcile`
is protocol completeness — with job_ref never set it is not reached.

A pass failure IS the take's failure, its kind preserved (oom keeps its
frame face, blank keeps its artefact — laws 3/7) with the pass named in
front of the message. Cancel interrupts the in-flight pass and stops the
chain (kind `interrupted`).
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, replace

from . import crop as crop_mod
from . import presets as presets_mod
from . import regions as regions_mod
from .comfy import ComfyClient
from .executors import (ComfyExecutor, ComfyHandle, DispatchRequest,
                        EngineFailure, ExecutionResult, Handle, Lane,
                        Reconciliation)


@dataclass
class RegionHandle(Handle):
    request: DispatchRequest | None = None
    cancelled: bool = False
    current: ComfyHandle | None = None      # the in-flight pass's handle
    # surfaced by scheduler.snapshot() so the queue view can say
    # "running · pass 2 of 4" (shape: {"current", "total"})
    passes: dict | None = None


class RegionExecutor:
    """lane="local" → the serial GPU lane holds the whole pipeline, which
    is exactly right: every pass is heavy local work."""

    name = "region"
    lane: Lane = "local"

    def __init__(self, client: ComfyClient) -> None:
        # its OWN client (wiring's rule): the inner executor's WS mirror
        # replaces the client's handler, so sharing would steal another
        # executor's signals. ComfyUI serves multiple WS clients happily.
        self._client = client
        self._inner = ComfyExecutor(client)

    async def open(self) -> None:
        await self._inner.open()

    async def close(self) -> None:
        await self._inner.close()

    async def start(self, req: DispatchRequest) -> RegionHandle:
        plan = (req.submitted_graph or {}).get("region_plan")
        if not plan or not plan.get("passes"):
            raise EngineFailure("error", "region take carries no plan — "
                                "reroll to rebuild it")
        return RegionHandle(job_ref=None, request=req)

    async def _upload(self, blob: bytes) -> str:
        sha = regions_mod.sha256_bytes(blob)
        return await self._client.upload_image(blob, f"cockpit-{sha[:16]}.png")

    @staticmethod
    async def _read(path: str, what: str) -> bytes:
        try:
            return await asyncio.to_thread(
                lambda: open(path, "rb").read())
        except OSError as e:
            raise EngineFailure(
                "error", f"the {what} went missing from staging mid-run "
                f"({path}) — reroll re-stages it") from e

    async def wait(self, h: RegionHandle) -> ExecutionResult:
        req = h.request
        plan = req.submitted_graph["region_plan"]
        total = len(plan["passes"])
        working = await self._read(plan["source"]["path"], "source image")
        as_run: list[dict] = []
        for p in plan["passes"]:
            if h.cancelled:
                raise EngineFailure("interrupted", "interrupted by cancel")
            h.passes = {"current": p["idx"] + 1, "total": total}
            graph = copy.deepcopy(p["graph"])
            box = tuple(p["box"]) if p["box"] else None
            crop_png = await asyncio.to_thread(
                self._crop_png, working, box)
            source_name = await self._upload(crop_png)
            presets_mod.patch_input(
                graph, {"node_title": "source_image", "field": "image"},
                source_name)
            inner = await self._inner.start(replace(req,
                                                    submitted_graph=graph))
            h.current = inner
            try:
                result = await self._inner.wait(inner)
            except EngineFailure as e:
                raise EngineFailure(
                    e.kind,
                    f"pass {p['idx'] + 1}/{total} ({p['kind']}): {e}",
                    status=e.status, detail=e.detail,
                    charged_usd=e.charged_usd, job_ref=e.job_ref,
                    artefact=e.artefact,
                    retry_after_s=e.retry_after_s) from e
            finally:
                h.current = None
            mask_png = None
            if p["masked_paste"]:
                mask_png = await self._read(p["mask"]["path"],
                                            f"pass {p['idx']} mask")
            working = await asyncio.to_thread(
                crop_mod.paste_back, working, result.data, box, mask_png)
            as_run.append({"idx": p["idx"], "kind": p["kind"],
                           "capability": p["capability"],
                           "workflow": p["workflow"],
                           "region_ids": p["region_ids"],
                           "prompt": p["prompt"],
                           "template_id": p["template_id"],
                           "box": p["box"],
                           "mask_sha256": p["mask"]["sha256"],
                           "prompt_id": inner.job_ref, "graph": graph})
        keep_applied = False
        if plan.get("keep"):
            source_png = await self._read(plan["source"]["path"],
                                          "source image")
            keep_png = await self._read(plan["keep"]["path"], "keep mask")
            working = await asyncio.to_thread(
                regions_mod.apply_keep, working, source_png, keep_png)
            keep_applied = True
        h.passes = None
        # the as-run record replaces the envelope on the landed node
        # (every pass with its EXACT graph and prompt_id)
        record = {"region_plan": {
            "policy": plan["policy"], "size": plan["size"],
            "source": {k: plan["source"][k] for k in ("sha256", "name")},
            "region_ids": plan["region_ids"],
            "occluded_region_ids": plan["occluded_region_ids"],
            "keep_applied": keep_applied,
            "keep_mask_sha256": (plan["keep"] or {}).get("sha256"),
            "passes": as_run,
        }}
        return ExecutionResult(data=working, submitted_graph=record)

    @staticmethod
    def _crop_png(frame_png: bytes, box) -> bytes:
        if box is None:
            return frame_png
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(frame_png)).convert("RGB")
        out = io.BytesIO()
        img.crop(box).save(out, format="PNG")
        return out.getvalue()

    async def cancel(self, h: RegionHandle) -> None:
        h.cancelled = True
        if h.current is not None:
            await self._inner.cancel(h.current)

    async def reconcile(self, _slot: dict) -> Reconciliation:
        # never reached in practice: job_ref is never set, so recovery's
        # running-without-job_ref rule requeues from the envelope instead
        return Reconciliation("gone")

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None                      # nothing bills on the GPU
