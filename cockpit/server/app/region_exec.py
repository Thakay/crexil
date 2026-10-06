"""Execute one region take as an ordered pipeline on the serial local lane.

The region-plan envelope owns crop geometry, effective masks and pass order.
Source, KEEP and every effective mask are verified against their frozen hashes
before any dispatch, then held in memory so staging changes cannot alter a take.
Each pass crops the working image, submits its graph through ComfyExecutor,
then clips the result to the effective binary mask. Instruction tiers feather
inward; mask-native tiers retain their graph's blend within the same fence.
Every pass preserves source alpha and restores KEEP before another pass sees
the image. Trusting a mask-native graph alone would violate exact protection.

The final result carries each submitted graph and runtime prompt ID. The
scheduler owns database writes and final landing. Failures retain their kind
and identify the failing pass; cancellation interrupts the active pass.

The handle has no durable job reference, so interrupted execution remains
uncertain after restart and is not automatically repeated. Raw runtime files
and protected working frames are collected through the server after each pass,
before a later pass can fail or execution can stop. Exact normalized inputs
and uploaded crops are collected before dispatch, without creating image nodes.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, replace

from . import crop as crop_mod
from . import presets as presets_mod
from . import regions as regions_mod
from . import regionrun
from .comfy import ComfyClient
from .executors import (CollectedOutput, ComfyExecutor, ComfyHandle, DispatchRequest,
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

    async def _read_verified(self, ref: dict, what: str) -> bytes:
        blob = await self._read(ref["path"], what)
        if regions_mod.sha256_bytes(blob) != ref.get("sha256"):
            raise EngineFailure(
                "error", f"the {what} changed from its frozen SHA256; "
                "reroll to stage and review it again")
        return blob

    @staticmethod
    async def _retain_input(req: DispatchRequest, identity: str, blob: bytes,
                            purpose: str, *, submitted_graph: dict | None = None, **details) -> dict:
        ref = {'identity': 'region-input:' + identity,
               'sha256': regions_mod.sha256_bytes(blob), 'purpose': purpose, **details}
        if req.on_output:
            await req.on_output(CollectedOutput(identity=ref['identity'],
                filename=identity.replace(':', '-') + '.png', data=blob,
                payload=None, role='runtime input', descriptor=ref, submitted_graph=submitted_graph))
        return ref

    async def wait(self, h: RegionHandle) -> ExecutionResult:
        if h.cancelled:
            raise EngineFailure("interrupted", "interrupted by cancel")
        req = h.request
        plan = req.submitted_graph["region_plan"]
        total = len(plan["passes"])
        source_png = await self._read_verified(plan["source"], "source image")
        working = source_png
        keep_png = (await self._read_verified(plan["keep"], "KEEP mask")
                    if plan.get("keep") else None)
        masks = [await self._read_verified(p["mask"], f"pass {p['idx'] + 1} mask")
                 for p in plan["passes"]]
        source_ref = await self._retain_input(req, 'source', source_png, 'normalized source',
            **{key: value for key, value in plan['source'].items()
               if key in ('original_sha256', 'coordinates', 'name')}, size=plan['size'])
        keep_ref = (await self._retain_input(req, 'keep', keep_png, 'protected pixels', size=plan['size'])
                    if keep_png is not None else None)
        mask_refs = [await self._retain_input(req, f"pass:{p['idx']}:effective-mask", mask,
            'effective edit mask', pass_index=p['idx'], size=plan['size'])
            for p, mask in zip(plan['passes'], masks, strict=True)]
        as_run: list[dict] = []
        for p, mask_png, mask_ref in zip(plan["passes"], masks, mask_refs, strict=True):
            if h.cancelled:
                raise EngineFailure("interrupted", "interrupted by cancel")
            h.passes = {"current": p["idx"] + 1, "total": total}
            graph = copy.deepcopy(p["graph"])
            box = tuple(p["box"]) if p["box"] else None
            crop_png = await asyncio.to_thread(
                self._crop_png, working, box)
            crop_size = [box[2] - box[0], box[3] - box[1]] if box else plan['size']
            crop_ref = await self._retain_input(req, f"pass:{p['idx']}:source", crop_png,
                'runtime source crop', pass_index=p['idx'], box=p['box'], size=crop_size)
            if h.cancelled:
                raise EngineFailure("interrupted", "interrupted by cancel")
            source_name = await self._upload(crop_png)
            if h.cancelled:
                raise EngineFailure("interrupted", "interrupted by cancel")
            cropped_mask = await asyncio.to_thread(regionrun._crop_mask_bytes, mask_png, p['box'])
            crop_mask_ref = await self._retain_input(req, f"pass:{p['idx']}:mask", cropped_mask,
                'runtime mask crop', pass_index=p['idx'], box=p['box'], size=crop_size)
            if h.cancelled:
                raise EngineFailure('interrupted', 'interrupted by cancel')
            mask_name = p['mask'].get('upload')
            if p['mask'].get('upload_at_dispatch'):
                mask_name = await self._upload(cropped_mask)
                if h.cancelled:
                    raise EngineFailure("interrupted", "interrupted by cancel")
                presets_mod.patch_existing(graph, {'node_title': 'region_mask', 'field': 'image'}, mask_name)
            presets_mod.patch_input(
                graph, {"node_title": "source_image", "field": "image"},
                source_name)
            crop_ref = await self._retain_input(req, f"pass:{p['idx']}:source", crop_png,
                'runtime source crop', pass_index=p['idx'], box=p['box'], size=crop_size,
                upload=source_name, node_title='source_image', field='image', submitted_graph=graph)
            crop_mask_ref = await self._retain_input(req, f"pass:{p['idx']}:mask", cropped_mask,
                'runtime mask crop', pass_index=p['idx'], box=p['box'], size=crop_size,
                upload=mask_name, node_title='region_mask', field='image',
                graph_uses_mask=bool(presets_mod.find_nodes_by_title(graph, 'region_mask')), submitted_graph=graph)
            if h.cancelled:
                raise EngineFailure('interrupted', 'interrupted by cancel')
            async def raw_output(output: CollectedOutput, pass_index=p['idx'], pass_graph=graph) -> bytes | None:
                if req.on_output:
                    return await req.on_output(replace(output, identity=f'pass:{pass_index}/raw:' + output.identity,
                        role='raw pass output', descriptor={**output.descriptor, 'pass_index': pass_index},
                        submitted_graph=pass_graph))
                return None

            inner = await self._inner.start(replace(req, submitted_graph=graph,
                                                    sources=(), output_ports=(), on_output=raw_output))
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
            # Every runtime must meet the same outside-area promise. A graph
            # that already composites still drifts outside its own mask.
            feather = crop_mod.PASTE_FEATHER_PX if p["masked_paste"] else 0
            working = await asyncio.to_thread(
                crop_mod.paste_back, working, result.data, box, mask_png, feather)
            if keep_png is not None:
                working = await asyncio.to_thread(
                    regions_mod.apply_keep, working, source_png, keep_png)
            as_run.append({"idx": p["idx"], "kind": p["kind"],
                           "capability": p["capability"],
                           "workflow": p["workflow"],
                           "region_ids": p["region_ids"],
                           "prompt": p["prompt"],
                           "template_id": p["template_id"],
                           "box": p["box"],
                           "applied_controls": p.get('applied_controls', {}),
                           "source_input": crop_ref,
                           "mask_input": crop_mask_ref,
                           "effective_mask_input": mask_ref,
                           "mask_sha256": p["mask"]["sha256"],
                           "prompt_id": inner.job_ref, "graph": graph})
            if req.on_output:
                await req.on_output(CollectedOutput(identity=f"pass:{p['idx']}/protected",
                    filename=f"pass-{p['idx'] + 1}-protected.png", data=working,
                    role='final' if p['idx'] + 1 == total else 'intermediate',
                    descriptor={'pass_index': p['idx'], 'source_sha256': plan['source']['sha256'],
                                'mask_sha256': p['mask']['sha256'], 'job_ref': inner.job_ref,
                                'keep_mask_sha256': (plan.get('keep') or {}).get('sha256')},
                    submitted_graph={'region_plan': {'passes': copy.deepcopy(as_run),
                        'source': source_ref, 'keep': keep_ref,
                        'policy': plan['policy'], 'size': plan['size'],
                        'region_ids': plan['region_ids'], 'occluded_region_ids': plan['occluded_region_ids'],
                        'keep_applied': keep_png is not None,
                        'keep_scope': 'each_completed_pass',
                        'keep_mask_sha256': (plan.get('keep') or {}).get('sha256')}}))
        keep_applied = keep_png is not None
        h.passes = None
        # the as-run record replaces the envelope on the landed node
        # (every pass with its EXACT graph and prompt_id)
        record = {"region_plan": {
            "policy": plan["policy"], "size": plan["size"],
            "source": source_ref, "keep": keep_ref,
            "region_ids": plan["region_ids"],
            "occluded_region_ids": plan["occluded_region_ids"],
            "keep_applied": keep_applied,
            "keep_scope": 'each_completed_pass',
            "keep_mask_sha256": (plan["keep"] or {}).get("sha256"),
            "passes": as_run,
        }}
        return ExecutionResult(data=working, submitted_graph=record,
                               primary_identity=f"pass:{plan['passes'][-1]['idx']}/protected")

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

    async def reconcile(self, _slot: dict, *, request=None) -> Reconciliation:  # noqa: ARG002
        # A pass may have finished without a durable composite receipt.
        # Keep collected checkpoints; never start the pipeline again here.
        return Reconciliation("gone")

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None                      # nothing bills on the GPU
