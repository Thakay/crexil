"""Region turns: the mask fan-out's backend route.

Masks ride the run request as per-region base64 PNGs and become
content-addressed refs server-side (`regions` rows + staged files — the
regions.py contract; deliberately NO draft-CRUD ahead of the run).
One request = one turn: the region set resolves ONCE under nd3-v1
(every engine gets the same regions and the same precedence — the
surface's own words), one crop box per pass is fixed at plan time
(crop.py), and each mask-capable preset batch gets its own envelope of
pass graphs (regionrun.py) dispatched as ordinary slots — the
RegionExecutor walks them on the local lane. External models never appear
here: region turns stay on the local rails, structurally (the
request has no external field to refuse).

Refusals are 422s in the artist's words, one cause each (law 1); the
reroll branch lives in api_v4._reroll (a cell rebuilds from its dials
alone)."""

from __future__ import annotations

import base64
import binascii
import io
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import config, regionrun
from . import presets as presets_mod
from . import regions as regions_mod
from .db import new_id
from .executors import DispatchRequest
from .schemas import RegionList, RegionRun, RunResult

router = APIRouter(prefix="/api")

MASK_CAP_BYTES = 32 * (1 << 20)          # the mask editor's proven ceiling


def _decode_mask(b64: str, index: int) -> bytes:
    """Mask decoding discipline, refused in the artist's words:
    data-URL tolerant, capped, decodable."""
    try:
        blob = base64.b64decode(b64.split(",")[-1], validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(422, f"region {index + 1}'s mask isn't valid "
                            "base64 — redraw and send it again")
    if len(blob) > MASK_CAP_BYTES:
        raise HTTPException(422, f"region {index + 1}'s mask exceeds 32 MB")
    try:
        Image.open(io.BytesIO(blob)).verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(422, f"region {index + 1}'s mask is not a "
                            "decodable image")
    return blob


def _region_dir(node_id: str) -> Path:
    """Staged region artefacts live beside the node they were drawn on —
    they are lineage: nothing sweeps them, purge unlinks them."""
    d = config.STAGING_DIR / "regions" / node_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _upload_name(stack, blob: bytes) -> str:
    """Content-address bytes into ComfyUI's input dir (_stage_source's
    pattern): a scripted stack computes the name and uploads nothing."""
    sha = regions_mod.sha256_bytes(blob)
    name = f"cockpit-{sha[:16]}.png"
    if stack.comfy is not None:
        name = await stack.comfy.upload_image(blob, name)
    return name


async def stage_region_plan(stack, node: dict, plan: dict) -> tuple[dict,
                                                                    dict | None]:
    """Stage every pass's masks + the keep mask beside the node and upload
    the graph-facing crops; returns (source, keep) for the envelope. Each
    pass gains mask_path / mask_sha256 (the FULL-frame mask — the record's
    ref and the paste-back's) and mask_upload (the crop the graph loads)."""
    from .api_v4 import _stage_source
    rdir = _region_dir(node["id"])
    source_name, source_sha = await _stage_source(stack, node)
    for p in plan["passes"]:
        sha = regions_mod.sha256_bytes(p["mask"])
        path = rdir / f"pass-{sha[:16]}.png"
        if not path.exists():
            await run_in_threadpool(path.write_bytes, p["mask"])
        p["mask_path"], p["mask_sha256"] = str(path), sha
        p["mask_upload"] = await _upload_name(stack, p["mask_crop"])
    keep = None
    if plan.get("keep_mask"):
        ksha = regions_mod.sha256_bytes(plan["keep_mask"])
        kpath = rdir / f"keep-{ksha[:16]}.png"
        if not kpath.exists():
            await run_in_threadpool(kpath.write_bytes, plan["keep_mask"])
        keep = {"path": str(kpath), "sha256": ksha}
    source = {"path": node["output_path"], "sha256": source_sha,
              "name": source_name}
    return source, keep


@router.post("/workspaces/{wid}/region-turns", response_model=RunResult,
             status_code=201)
async def run_region_turn(wid: str, body: RegionRun):
    """One region fan-out = one turn: the drawn regions land as
    `regions` rows (consumed by this turn — lineage), the plan
    resolves once, and every stepped preset runs it with its own recipe,
    takes and seeds. KEEP holds byte-exactly whatever the tier."""
    from .api_v4 import (SourceError, _refuse_if_disk_cannot_hold,
                         _resolve_parents, _stack, _turn_view)
    stack = _stack()
    db, sched = stack.db, stack.scheduler
    ws = await db.get_workspace(wid)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    total = sum(b.takes for b in body.local)
    if total > 16:
        raise HTTPException(422, f"a run caps at 16 takes — this one asks "
                            f"for {total}")
    try:
        parents = await _resolve_parents(db, wid, body.parents)
    except SourceError as e:
        raise HTTPException(422, str(e))
    node = parents[0]

    policy = presets_mod.resolve_seed_policy(body.seed)
    if policy["mode"] == "locked" and not isinstance(policy.get("value"),
                                                     int):
        raise HTTPException(422, "a locked seed needs its value — unlock "
                            "it for a fresh roll per take")

    catalog = stack.catalog
    seen: set[str] = set()
    for batch in body.local:
        pid = batch.preset_id
        if pid in seen:
            raise HTTPException(422, f"preset '{pid}' is listed twice — "
                                "one row per engine, its takes on that row")
        seen.add(pid)
        if "/" in pid:
            raise HTTPException(422, "preset ids never contain '/' — that "
                                "spelling is an external model id, and "
                                "region turns stay on the local rails")
        preset = catalog.get(pid)
        if preset is None:
            raise HTTPException(422, f"unknown preset '{pid}'")
        try:
            regionrun.check_region_capable(preset)
        except regionrun.RegionRunError as e:
            raise HTTPException(422, str(e))

    # decode the drawn masks at the source's own size; list order IS
    # z-order (the surface's creation order — later wins within an action).
    # Rows are duck-typed with pre-generated ids and PERSISTED ONLY AFTER
    # every gate below passes — a refused request leaves no rows behind.
    src_blob = await run_in_threadpool(Path(node["output_path"]).read_bytes)
    size = Image.open(io.BytesIO(src_blob)).size
    rows: list[dict] = []
    masks: dict[str, Image.Image] = {}
    drawn: list[tuple[dict, bytes, str]] = []      # (row, blob, sha)
    rdir = _region_dir(node["id"])
    for i, spec in enumerate(body.regions):
        blob = _decode_mask(spec.mask_b64, i)
        mask = regions_mod.load_mask(blob, size)
        if regions_mod.is_empty(mask):
            raise HTTPException(422, f"region {i + 1} ('{spec.name}') has "
                                "no ink — draw it or drop the row")
        sha = regions_mod.sha256_bytes(blob)
        row = {"id": new_id(), "name": spec.name, "action": spec.action,
               "prompt": spec.prompt, "z_order": i,
               "mask_sha256": sha,
               "mask_path": str(rdir / f"drawn-{sha[:16]}.png")}
        rows.append(row)
        drawn.append((row, blob, sha))
        masks[row["id"]] = mask

    try:
        plan = regionrun.build_region_plan(rows, masks, size, body.prompt)
    except regionrun.RegionRunError as e:
        raise HTTPException(422, str(e))
    source, keep = await stage_region_plan(stack, node, plan)
    region_ids = [r["id"] for r in rows]
    mask_shas = {r["id"]: r["mask_sha256"] for r in rows}

    run_token = new_id()
    engines: dict[str, dict] = {}
    specs_list: list[dict] = []
    for batch in body.local:
        preset = catalog[batch.preset_id]
        inputs = dict(batch.inputs)
        inputs["seed"] = policy
        if body.prompt is not None and "prompt" not in inputs:
            inputs["prompt"] = body.prompt
        try:
            applied, input_specs, _ = presets_mod.applied_inputs(
                preset, inputs, "region")
        except Exception as e:
            raise HTTPException(422, f"preset '{preset['id']}' can't take "
                                f"these inputs: {e}")
        base_env = regionrun.build_engine_envelope(
            preset, applied, input_specs, plan,
            source=source, keep=keep, region_ids=region_ids)
        lineage = {"capability": "region", "parents": [node["id"]],
                   "source_image_sha256": source["sha256"],
                   "region_ids": region_ids, "mask_sha256s": mask_shas,
                   "policy": plan["policy"]}
        applied = {**applied, **lineage}
        engines[batch.preset_id] = applied
        for _ in range(batch.takes):
            pos = len(specs_list)
            seed = presets_mod.take_seed(policy, pos)
            env = regionrun.stamp_take(base_env, seed, run_token, pos)
            specs_list.append({"engine": batch.preset_id, "dials": applied,
                               "seed": seed, "submitted_graph": env,
                               "external": None, "committed_usd": None})

    await _refuse_if_disk_cannot_hold(db, [size[0] * size[1]] * total)

    # every gate passed — persist the drawn regions (they are
    # lineage from here on) and only then the turn
    for row, blob, sha in drawn:
        path = Path(row["mask_path"])
        if not path.exists():
            await run_in_threadpool(path.write_bytes, blob)
        await db.create_region(node["id"], row["name"], row["action"],
                               row["prompt"], row["mask_path"], sha,
                               id_=row["id"])

    anchor = (body.anchor.x, body.anchor.y) if body.anchor else None
    turn_dials = {
        "seed": policy, "capability": "region", "parents": [node["id"]],
        "source_image_sha256": source["sha256"], "engines": engines,
        "region": {
            "policy": plan["policy"], "size": list(plan["size"]),
            "region_ids": region_ids,
            "occluded_region_ids": plan["occluded_region_ids"],
            "keep_mask_sha256": (keep or {}).get("sha256"),
            "passes": [{"idx": p["idx"], "kind": p["kind"],
                        "capability": p["capability"],
                        "region_ids": p["region_ids"], "prompt": p["prompt"],
                        "box": p["box"], "mask_sha256": p["mask_sha256"]}
                       for p in plan["passes"]],
        }}
    turn = await db.create_turn_with_slots(wid, body.prompt, turn_dials,
                                           specs_list, anchor=anchor)
    await db.mark_regions_consumed(region_ids, turn["id"])
    requests = [DispatchRequest(
        slot_id=s["id"], engine=s["engine"],
        submitted_graph=specs_list[s["pos"]]["submitted_graph"],
        external=None, committed_usd=None, frame=(size[0], size[1]),
        payload="image", verb="region", prompt=body.prompt,
        parents=(node["id"],), seed=s["seed"])
        for s in turn["slots"]]
    await sched.submit_turn(turn["id"], requests)
    await db.touch_workspace(wid)
    fresh = await db.get_turn(turn["id"])
    return {"turn": _turn_view(fresh), "seq": await db.latest_seq()}


LINEAGE_KEYS = {"seed", "capability", "parents", "source_image_sha256",
                "region_ids", "mask_sha256s", "policy"}


async def rebuild_region_take(stack, slot: dict, turn: dict,
                              seed: int) -> tuple[dict, tuple[int, int]]:
    """Reroll for a region cell: the envelope rebuilt from the slot's dials
    alone — same regions rows, same plan, new seed. Refusals are 409s in
    reroll's currency (history named the cell, and the world moved since):
    the preset lost its region recipes, the source or a mask file is gone,
    or a consumed mask was repainted (the sha check is what keeps the
    rebuilt cell honest against a repainted mask file)."""
    from .api_v4 import SourceError, _resolve_parents
    db = stack.db
    dials = slot["dials"]
    preset = stack.catalog.get(slot["engine"])
    if preset is None:
        raise HTTPException(409, f"preset '{slot['engine']}' is no longer "
                            "configured — the old take stays and nothing "
                            "was spent; restore the preset to reroll")
    try:
        regionrun.check_region_capable(preset)
    except regionrun.RegionRunError:
        raise HTTPException(409, f"preset '{slot['engine']}' no longer has "
                            "its region recipes — the old take stays and "
                            "nothing was spent; restore them to reroll")
    try:
        parents = await _resolve_parents(db, turn["workspace_id"],
                                         list(dials.get("parents") or ()))
    except SourceError as e:
        raise HTTPException(409, f"{e} — the old take stays and nothing "
                            "was spent")
    node = parents[0]
    region_ids = list(dials.get("region_ids") or ())
    mask_shas = dials.get("mask_sha256s") or {}
    rows = await db.regions_by_ids(region_ids)
    if len(rows) != len(region_ids):
        raise HTTPException(409, "this cell's regions are gone from the "
                            "record — the old take stays and nothing was "
                            "spent")
    src_blob = await run_in_threadpool(Path(node["output_path"]).read_bytes)
    size = Image.open(io.BytesIO(src_blob)).size
    masks: dict[str, Image.Image] = {}
    for r in rows:
        try:
            blob = await run_in_threadpool(Path(r["mask_path"]).read_bytes)
        except OSError:
            raise HTTPException(409, f"region '{r['name']}'s mask file is "
                                "gone from staging — the old take stays "
                                "and nothing was spent")
        if regions_mod.sha256_bytes(blob) != mask_shas.get(r["id"]):
            raise HTTPException(409, f"region '{r['name']}'s mask changed "
                                "since this cell ran — the old take stays "
                                "and nothing was spent; run the surface "
                                "again for a fresh turn")
        masks[r["id"]] = regions_mod.load_mask(blob, size)
    try:
        plan = regionrun.build_region_plan(rows, masks, size,
                                           turn["prompt"])
    except regionrun.RegionRunError as e:
        raise HTTPException(409, f"{e} — the old take stays and nothing "
                            "was spent")
    source, keep = await stage_region_plan(stack, node, plan)
    inputs = {k: v for k, v in dials.items() if k not in LINEAGE_KEYS}
    try:
        applied, input_specs, _ = presets_mod.applied_inputs(
            preset, inputs, "region")
    except Exception as e:
        raise HTTPException(409, f"preset '{preset['id']}' can't take this "
                            f"cell's inputs any more: {e} — the old take "
                            "stays and nothing was spent")
    env = regionrun.build_engine_envelope(
        preset, applied, input_specs, plan,
        source=source, keep=keep, region_ids=region_ids)
    env = regionrun.stamp_take(env, seed, new_id(), slot["pos"])
    return env, size


def _region_view(r: dict) -> dict:
    return {**{k: r[k] for k in ("id", "node_id", "name", "action", "prompt",
                                 "mask_sha256", "z_order",
                                 "consumed_by_turn_id", "created_at")},
            "mask_url": f"/api/regions/{r['id']}/mask"}


@router.get("/nodes/{nid}/regions", response_model=RegionList)
async def node_regions(nid: str):
    """The committed regions of a take: what past region turns
    consumed (server truth for the provenance surface). Drafts never
    live here (they are browser compose state until Run)."""
    from .api_v4 import _stack
    db = _stack().db
    node = await db.get_node(nid)
    if node is None:
        raise HTTPException(404, "no such node")
    rows = await db.regions_for_node(nid)
    return {"regions": [_region_view(r) for r in rows]}


@router.get("/regions/{rid}/mask", response_model=None)
async def region_mask(rid: str):
    from .api_v4 import _stack
    db = _stack().db
    region = await db.get_region(rid)
    if region is None:
        raise HTTPException(404, "no such region")
    path = Path(region["mask_path"])
    if not path.is_file():
        raise HTTPException(404, "that region's mask file is gone — its "
                            "source take was likely purged")
    return FileResponse(path, media_type="image/png")
