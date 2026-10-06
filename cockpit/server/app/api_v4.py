"""The product-model surface: workspaces, Run, the queue view, cancel,
events, nodes and the tray. Reads the running stack from `wiring.current`.

Run covers txt2img (no parents) and img2img (plain parents, which is
what Refine is). A take's graph plumbing (the seed policy, the inputs
that apply, the seed and save-prefix stamps) is `presets.py`'s.
Region turns run on their own route (api_v4_regions.py): masks ride
that request and fan across the mask-capable presets behind one
crop+composite layer. Mesh turns run on theirs (api_v4_mesh.py).
"""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import shutil
import sqlite3
import statistics
import time
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from . import blind, config, wiring
from . import presets as presets_mod
from . import regions as regions_mod
from .db import new_id
from .executors import DispatchRequest
from .mesh import fields_sidecar
from .scheduler import parse_frame
from .schemas import (BackfillResult, CancelResult, Costs,
                      CreateWorkspace, Dials, Engines, Events, GpuCheckResult,
                      GpuHoldResult, Headroom, MoneyGate, RerollBody, Node, NodePos, NodePosition,
                      Purge, PurgeResult, QueueView, RenameWorkspace,
                      RerollResult,
                      Run, RunResult, Tray, TurnCancelResult,
                      Unbench, UnbenchResult, ViewUpdate, Workspace,
                      WorkspaceDetail, WorkspaceRow)

router = APIRouter(prefix="/api")

LOCAL_LABEL = "LOCAL · SEQUENTIAL, ONE GPU"          # law 13: the
EXTERNAL_LABEL = "EXTERNAL · CONCURRENT, PRICED"     # copy is server-sent

SLOT_KEYS = ("id", "turn_id", "pos", "engine", "status", "attempt", "seed",
             "committed_usd", "charged_usd", "cause", "detail", "retry_of",
             "kind", "job_ref", "frame", "facts", "started_at", "finished_at",
             "created_at")

# E6's stand-in area for a take whose frame isn't known here — an external
# model renders at the provider's default, an img2img take at its source's
# size — so the disk sum never says zero for a slot that will write a file.
ASSUMED_PX = 1024 * 1024


def _frame_of(dials: dict) -> tuple[int, int] | None:
    """A local slot's requested frame from its applied raws."""
    w, h = dials.get("width"), dials.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return (w, h)
    return None


# --- E6: free VRAM and free disk at Run ---


def _disk_free() -> int:
    """Free bytes on the volume the takes land on (the staging dir, or
    its nearest existing parent before the first take ever lands)."""
    path = config.STAGING_DIR
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


_BPP_CACHE: dict = {"at": 0.0, "value": None}
BPP_TTL_S = 30.0


async def _bytes_per_px(db) -> tuple[float, str]:
    """This card's own bytes-per-pixel — the median over the newest landed
    local takes' files against their frames — cached briefly (the composer
    asks every few seconds while armed). The fallback is authored config,
    said as such."""
    now = time.monotonic()
    if _BPP_CACHE["value"] is not None and now - _BPP_CACHE["at"] < BPP_TTL_S:
        return _BPP_CACHE["value"]
    ratios: list[float] = []
    for row in await db.landed_sizes():
        frame = parse_frame(row["frame"])
        if frame is None:
            continue
        try:
            size = os.stat(row["output_path"]).st_size
        except OSError:
            continue
        if size > 0:
            ratios.append(size / (frame[0] * frame[1]))
    value = ((statistics.median(ratios), "record") if ratios
             else (config.DISK_FALLBACK_BYTES_PER_PX, "fallback"))
    _BPP_CACHE.update(at=now, value=value)
    return value


def _mb(n: float) -> str:
    if n >= (1 << 30):
        return f"{n / (1 << 30):.1f} GB"
    return f"{max(1, round(n / (1 << 20)))} MB"


async def _refuse_if_disk_cannot_hold(db, areas: list[int]) -> None:
    """The server's half of E6: a run whose takes the staging volume can't
    hold is refused in words before a turn exists (the composer disarms
    first; an API caller gets the same sentence)."""
    bpp, _ = await _bytes_per_px(db)
    needed = int(sum(areas) * bpp)
    free = _disk_free()
    if free < needed + config.DISK_MARGIN_BYTES:
        short = needed + config.DISK_MARGIN_BYTES - free
        raise HTTPException(
            422, f"{len(areas)} slot{'s' if len(areas) != 1 else ''} need "
                 f"about {_mb(needed)} and the cockpit's drive has "
                 f"{_mb(free)} free — free up about {_mb(short)} first; "
                 "nothing was started")

NODE_KEYS = ("id", "workspace_id", "turn_id", "slot_id", "take", "payload",
             "verb", "prompt", "engine", "seed", "duration_ms",
             "committed_usd", "charged_usd", "set_aside", "x", "y",
             "created_at", "parents", "turn_idx", "slot_pos",
             # The one permanent mark a node can wear. Every surface
             # that draws a node draws it, so it rides the one node feed.
             "promoted_at")

# The mesh facts are DERIVED per view (like the promoted mark), not a
# column, so they are named here rather than in NODE_KEYS.

THUMB_MAX = 320                          # 158px card @2x
IMMUTABLE_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _stack() -> wiring.Stack:
    stack = wiring.current
    if stack is None or not stack.scheduler.started:
        raise HTTPException(503, "the scheduler is not running")
    return stack


def _slot_slim(slot: dict) -> dict:
    return {k: slot[k] for k in SLOT_KEYS}


def _turn_view(turn: dict) -> dict:
    return {**{k: v for k, v in turn.items() if k != "slots"},
            "slots": [_slot_slim(s) for s in turn["slots"]]}


# --- authored tables: the web reads them as-is ---


def _read_config(name: str) -> dict:
    """A table under configs/ — re-read per request like /api/presets, so
    editing it is live. Unreadable = a 503 in plain words: the composer
    can't offer dials it can't see, and says so rather than guessing."""
    path = config.CONFIGS_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"configs/{name} is unreadable "
                            f"({e.__class__.__name__}) — it is authored "
                            "config; restore it and the dials return")


@router.get("/dials", response_model=Dials)
async def dials():
    """configs/dials.json: the six-dial normalization. Nothing is
    resolved here: the composer maps position → raw per engine and posts
    raws in the run's `inputs`; the server stays dial-ignorant."""
    return _read_config("dials.json")


@router.get("/engines", response_model=Engines)
async def engines():
    """configs/engines.json: the capability matrix with its
    verification states; the FRAME warning and every roster row's
    capability badges are worded from it: the only two readers,
    both client-side (composer/dials.ts, composer/badges.ts). law 12."""
    d = _read_config("engines.json")
    return {**d, "external": d.get("externalLane")}


LOCAL_ONLY_RUN = ("LOCAL ONLY is on — this run sends nothing out. Flip the "
                  "switch in the top bar to send the external takes, or set "
                  "their rows to 0")


def _local_windows(tz_minutes: int, now: datetime | None = None
                   ) -> tuple[datetime, datetime, datetime, datetime]:
    """Today's and this month's [start, end) in UTC, for a clock `tz_minutes`
    east of it — the artist's midnight, not the server's."""
    offset = timedelta(minutes=tz_minutes)
    local_now = (now or datetime.now(timezone.utc)) + offset
    day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    return (day - offset, day + timedelta(days=1) - offset,
            month - offset, next_month - offset)


@router.get("/costs", response_model=Costs)
async def costs(tz: int = Query(0, ge=-840, le=840)):
    """The corner totals — CHARGED only, the events ledger summed
    across every workspace; the pre-run estimate and a cell's committed
    quote never count. `tz` is the browser's offset from UTC in minutes,
    so "today" and "this month" turn over on the artist's calendar."""
    db = _stack().db
    now = datetime.now(timezone.utc)
    day_from, day_to, month_from, month_to = _local_windows(tz, now)
    local_now = now + timedelta(minutes=tz)
    return {"today_usd": await db.charged_between(day_from.isoformat(),
                                                  day_to.isoformat()),
            "month_usd": await db.charged_between(month_from.isoformat(),
                                                  month_to.isoformat()),
            "day": local_now.strftime("%Y-%m-%d"),
            "month": local_now.strftime("%Y-%m"),
            "tz_minutes": tz}


# --- workspaces ---


@router.post("/workspaces", response_model=Workspace, status_code=201)
async def create_workspace(body: CreateWorkspace):
    stack = _stack()
    try:
        return await stack.db.create_workspace(body.name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"a workspace named like {body.name!r} "
                            "already exists (slug collision)")


@router.get("/workspaces", response_model=list[WorkspaceRow])
async def list_workspaces():
    """Most-recently-touched first — every verb that changes a workspace
    touches it, so the order is the artist's own trail back to what they
    were doing. Rows carry their node and promoted counts."""
    return await _stack().db.list_workspaces()


@router.patch("/workspaces/{wid}", response_model=Workspace)
async def rename_workspace(wid: str, body: RenameWorkspace):
    """The display name only. The slug is frozen at create: it is the
    promote folder, so renaming the asset must never strand the
    files already under the old one."""
    db = _stack().db
    if await db.get_workspace(wid) is None:
        raise HTTPException(404, "no such workspace")
    await db.rename_workspace(wid, body.name)
    return await db.get_workspace(wid)


@router.get("/workspaces/{wid}", response_model=WorkspaceDetail)
async def workspace_detail(wid: str):
    db = _stack().db
    ws = await db.get_workspace(wid)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    hidden = await blind.hidden_turns(db, wid)
    turns = [_turn_view(t) for t in await db.list_turns(wid)]
    for turn in turns:
        if turn["id"] in hidden:
            turn["slots"] = [blind.mask("slot", slot) for slot in turn["slots"]]
    return {**ws, "turns": turns,
            "charged_usd": await db.workspace_charged(wid)}


@router.patch("/workspaces/{wid}/view", response_model=Workspace)
async def workspace_view(wid: str, body: ViewUpdate):
    """reopening restores the canvas exactly: the client persists the
    viewport and the dials here (debounced)."""
    db = _stack().db
    if await db.get_workspace(wid) is None:
        raise HTTPException(404, "no such workspace")
    await db.set_workspace_view(wid, viewport=body.viewport, dials=body.dials)
    return await db.get_workspace(wid)


# --- nodes (the canvas feed) ---


def _mesh_view(node: dict) -> dict | None:
    """Project the runner's measured mesh facts without inventing missing values.

    Requested and actual triangle counts remain distinct. Current companion
    bytes decide field availability after retrieval repairs; the execution
    receipt remains immutable. Turntables prefer retained local bytes; a runtime
    lookup requires an observed runner job belonging to this installation.
    """
    if node.get("payload") != "3d":
        return None
    job = ((node.get("submitted_graph") or {}).get("mesh_job") or {})
    v = job.get("validation") or {}
    wt = v.get("watertight")
    # What the verb ASKED for, off the job's own params, so a decimated
    # child can be read against its request. `target_triangles` is decimate's
    # required param, so its presence is what marks the pair as meaningful —
    # a generated mesh asked for no count and gets null here.
    p = job.get("params") or {}
    asked = p.get("target_triangles")
    return {
        "engine": job.get("engine"),
        "runner_job_id": job.get("runner_job_id"),
        "triangles": v.get("triangles"),
        "vertices": v.get("vertices"),
        "watertight": all(wt) if isinstance(wt, list) and wt else None,
        "textured": v.get("texture_present"),
        "file_mb": v.get("file_mb"),
        # what a client asks before requesting a plate; a mesh without one
        # keeps its card and its word (law 12), it is never hidden
        "has_turntable": bool(not node.get('purged_at') and (
            node.get('output_path') and Path(node['output_path']).with_suffix('.turntable.webp').is_file()
            or job.get('turntable') and job.get('runner_job_id') and not (node.get('dials') or {}).get('crexil_restore'))),
        "asked_triangles": asked if isinstance(asked, int) else None,
        "forced": p.get("force") is True,
        # Collection can recover a missing companion after the immutable
        # node receipt was written. Current bytes decide re-extraction.
        "fields_kept": bool(node.get("output_path") and not node.get("purged_at")
                            and fields_sidecar(node["output_path"]).is_file()),
    }


def _node_view(node: dict) -> dict:
    return {k: node[k] for k in NODE_KEYS} | {"mesh": _mesh_view(node)}


async def _live_node(nid: str) -> dict:
    """404 for unknown AND purged — get_node returns purged rows (lineage
    outlives purge, law 7) but no v4 surface serves them."""
    node = await _stack().db.get_node(nid)
    if node is None:
        raise HTTPException(404, "no such node")
    if node["purged_at"]:
        raise HTTPException(404, "node was purged")
    return node


@router.get("/workspaces/{wid}/nodes", response_model=list[Node])
async def workspace_nodes(wid: str):
    """The one node feed: landed + set-aside (the canvas draws the former,
    the tray the latter), never purged, never failed attempts
    (failures are grid cells + events)."""
    db = _stack().db
    if await db.get_workspace(wid) is None:
        raise HTTPException(404, "no such workspace")
    return await blind.apply(db, "node", [_node_view(n) for n in await db.list_nodes(wid)],
                             workspace_id=wid)


def _make_thumb(src: Path, dst: Path) -> None:
    # tmp + os.replace: two concurrent first requests can't torn-write
    with Image.open(src) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)                   # frame 0 of a mesh's orbit
        im.thumbnail((THUMB_MAX, THUMB_MAX))
        out = im if im.mode in ("RGB", "RGBA") else im.convert("RGBA")
        tmp = dst.with_suffix(f".tmp{os.getpid()}")
        # save_all=False: an animated source contributes ONE frame here —
        # the card plate is a still by design (the orbit is the lightbox's)
        out.save(tmp, "WEBP", quality=80, save_all=False)
    os.replace(tmp, dst)


# --- the mesh's turntable ---
#
# The orbit is rendered by `funnel.py` and lives on the HOST, beside the run
# it came from (`tools/3d/runs/...`). The runner keeps its working files on
# the host, and the container can't reach it. So the cockpit asks the runner
# for it exactly as MeshExecutor asks for the GLB, ONCE, and caches the bytes
# beside its own artefact. From then on both mesh faces are plain file serves
# off immutable node pixels, and they keep working with the runner down.
#
# One fetch, two faces: the card/cell/tray plate is frame 0 of this file
# (node_thumb's <=320 derivative), the lightbox is the whole orbit.

TURNTABLE_MISSING = ("this mesh has no turntable render, so there is no "
                     "plate — the mesh file itself still downloads")

RUNNER_SILENT = ("the turntable lives on the host and the 3D engine runner "
                 "isn't answering — start this checkout's runner "
                 "(scripts/3d-runner.sh), then reload")


async def _fetch_turntable(job: str) -> bytes:
    """The orbit's bytes from the runner. Under scripted executors the
    harness IS the mesh engine, so its render is the one that matters —
    probing the host would make a card's body depend on whether a systemd
    unit happens to be up on this machine, which is the coupling the
    harness exists to cut (`wiring.probe_mesh_runner` stands down the same
    way, for the same reason)."""
    if config.CREXIL_PREVIEW:
        raise HTTPException(503, "This preview cannot fetch a turntable from "
                            "the 3D runtime. The imported mesh remains available.")
    if wiring.EXECUTOR_FACTORY is not None or config.HARNESS_EXECUTORS:
        from .harness import turntable_webp
        return turntable_webp(job)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{config.THREED_URL}/jobs/{job}/turntable")
    except httpx.HTTPError:
        raise HTTPException(404, RUNNER_SILENT) from None
    if r.status_code != 200 or not r.content:
        # the runner answers in words too; one cause on the face (law 1)
        raise HTTPException(404, TURNTABLE_MISSING)
    return r.content


async def _turntable_file(node: dict) -> Path:
    """The cached orbit beside this node's artefact, fetched on first ask.
    404s in words on every path that can't produce one — a mesh whose orbit
    is unreachable keeps its card and its word (law 12)."""
    if node["payload"] != "3d":
        raise HTTPException(404, "only a mesh has a turntable")
    if node.get('purged_at'):
        raise HTTPException(404, 'This mesh was purged. Its preview is unavailable.')
    dst = Path(node["output_path"]).with_suffix(".turntable.webp")
    if dst.is_file():
        return dst
    if (node.get("dials") or {}).get("crexil_restore"):
        raise HTTPException(404, "This restored mesh has no retained turntable. Its old runtime is not contacted.")
    mesh = _mesh_view(node) or {}
    job = mesh.get("runner_job_id")
    if not mesh.get("has_turntable") or not job:
        raise HTTPException(404, TURNTABLE_MISSING)
    data = await _fetch_turntable(str(job))
    tmp = dst.with_suffix(f".tmp{os.getpid()}")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    os.replace(tmp, dst)             # two concurrent firsts can't tear
    return dst


@router.get("/nodes/{nid}/image",
            responses={200: {"content": {"image/png": {}},
                             "description": "the staged node artefact"}})
async def node_image(nid: str):
    node = await _live_node(nid)
    path = Path(node["output_path"])
    if not path.is_file():
        raise HTTPException(404, "artefact file is missing from staging")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, headers=IMMUTABLE_CACHE,
                        stat_result=await blind.file_stat(_stack().db, node["turn_id"], path))


@router.get("/nodes/{nid}/turntable",
            responses={200: {"content": {"image/webp": {}},
                             "description": "the mesh's whole orbit"}})
async def node_turntable(nid: str):
    """The 3D lane's full-res face: 30 frames of the engine's own
    render, one animated WEBP. The mesh lightbox shows it while the GLB
    streams from `/image`, and falls back to it when the viewer can't
    draw."""
    node = await _live_node(nid)
    path = await _turntable_file(node)
    return FileResponse(path, media_type="image/webp", headers=IMMUTABLE_CACHE,
                        stat_result=await blind.file_stat(_stack().db, node["turn_id"], path))


@router.get("/nodes/{nid}/thumb",
            responses={200: {"content": {"image/webp": {}},
                             "description": "cached ≤320px card derivative"}})
async def node_thumb(nid: str):
    """Cards render thumbs, never full-res (the canvas performance budget). Generated
    once with Pillow, cached beside the original; immutable like the node.

    A mesh has no pixels of its own, so its plate is frame 0 of the cached
    turntable: same ≤320 derivative, same cache name, so purge and
    the tray's byte count already cover it."""
    node = await _live_node(nid)
    if node["payload"] == "3d":
        src = await _turntable_file(node)
        dst = Path(node["output_path"]).with_suffix(".thumb.webp")
    elif node["payload"] == "image":
        src = Path(node["output_path"])
        dst = src.with_suffix(".thumb.webp")
    else:
        raise HTTPException(404, "no thumbnail for this payload")
    if not src.is_file():
        raise HTTPException(404, "artefact file is missing from staging")
    if not dst.is_file():
        try:
            await run_in_threadpool(_make_thumb, src, dst)
        except (OSError, ValueError):
            raise HTTPException(404, "artefact is not a readable image, so "
                                "there is no thumbnail; the original may "
                                "still open in the lightbox")
    return FileResponse(dst, media_type="image/webp", headers=IMMUTABLE_CACHE,
                        stat_result=await blind.file_stat(_stack().db, node["turn_id"], dst))


@router.post("/nodes/{nid}/position", response_model=NodePosition)
async def node_position(nid: str, body: NodePos):
    """The mutable-view exception: a drag moves where the artist is
    working, never what happened. No event — viewport-class state."""
    await _live_node(nid)
    await _stack().db.set_node_position(nid, body.x, body.y)
    return {"id": nid, "x": body.x, "y": body.y}


# --- the set-aside tray + purge (law 7) ---


def _artefact_paths(node: dict, regions: list[dict], companions: list[Path] | None = None) -> list[Path]:
    """Everything a node owns on disk: the staged artefact, the two caches
    beside it (node_thumb's and node_turntable's naming), its voxel-field
    sidecar and its region masks. The turntable cache and the field set are
    listed unconditionally — each only ever exists for a mesh, and a path
    that isn't there counts as nothing.

    The list has to be complete: a TRELLIS.2 field set
    measured 21–92 MB against a 34–41 MB mesh, so a tray entry that omitted it
    could understate the purge by more than the artefact itself."""
    out = Path(node["output_path"])
    return list(dict.fromkeys([out, out.with_suffix(".thumb.webp"),
             out.with_suffix(".turntable.webp"), fields_sidecar(out)]
            + [Path(r["mask_path"]) for r in regions] + (companions or [])))


def _bytes_on_disk(groups: list[list[Path]]) -> list[int]:
    sizes = []
    for paths in groups:
        total = 0
        for path in paths:
            with contextlib.suppress(OSError):   # already gone counts as nothing
                total += path.stat().st_size
        sizes.append(total)
    return sizes


def _unlink_all(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass                         # the tombstone is the truth (law 7);
                                         # a stubborn file is staging litter


@router.get("/workspaces/{wid}/tray", response_model=Tray)
async def workspace_tray(wid: str):
    """The set-aside tray: off the canvas, still in the record. Each entry
    states what purging it would cost — bytes freed and children orphaned
    of their image — so the held button's copy is true before the hold."""
    db = _stack().db
    if await db.get_workspace(wid) is None:
        raise HTTPException(404, "no such workspace")
    nodes = await db.tray_nodes(wid)
    kids = await db.children_counts([n["id"] for n in nodes])
    from .execution_outputs import owned_companions
    groups = [_artefact_paths(n, await db.regions_for_node(n["id"]), await owned_companions(db, n["id"]))
              for n in nodes]
    sizes = await run_in_threadpool(_bytes_on_disk, groups) if groups else []
    entries = [{**_node_view(n), "bytes": size,
                "children": kids.get(n["id"], 0)}
               for n, size in zip(nodes, sizes, strict=True)]
    entries = await blind.apply(db, "node", entries, workspace_id=wid)
    return {"nodes": entries, "bytes": sum(e["bytes"] for e in entries)}


@router.post("/workspaces/{wid}/purge", response_model=PurgeResult)
async def purge_nodes(wid: str, body: Purge):
    """The only delete verb, and the only place it fires from is the tray:
    every id must be a live set-aside node of this workspace or nothing is
    purged (a held button promised an exact count). The row tombstones
    (lineage survives — a child keeps its parent id and its prompt record,
    D1), then the files go; the caches die with their source."""
    db = _stack().db
    if await db.get_workspace(wid) is None:
        raise HTTPException(404, "no such workspace")
    from .execution_outputs import owned_companions
    groups = []
    try:
        # Validate and tombstone the entire request together. Required-source
        # guards may refuse any target; no file is touched until all commit.
        async with db._tx():
            nodes = []
            for nid in dict.fromkeys(body.ids):
                node = await db.get_node(nid)
                if node is None or node["purged_at"] or node["workspace_id"] != wid:
                    raise HTTPException(422, "A take is not in this workspace's tray; nothing was purged. Reopen the tray.")
                if not node["set_aside"]:
                    raise HTTPException(422, "A take is still on the canvas. Set it aside first; nothing was purged.")
                nodes.append(node)
            kids = await db.children_counts([n["id"] for n in nodes])
            for node in nodes:
                regions = await db.regions_for_node(node["id"])
                purged = await db.purge_node(node["id"])
                groups.append(_artefact_paths(purged, regions, await owned_companions(db, node["id"])))
            await db.touch_workspace(wid)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    freed = sum(await run_in_threadpool(_bytes_on_disk, groups))
    for paths in groups:
        await run_in_threadpool(_unlink_all, paths)
    return {"purged": len(nodes), "freed_bytes": freed,
            "children": sum(kids.values()), "seq": await db.latest_seq()}


# --- Run (the grid appears whole, immediately) ---

class SourceError(Exception):
    """A parent this run can't read, in the words the artist would fix it
    by. One home for the copy, two currencies: Run answers 422 (the request
    named it), reroll answers 409 with the cost clause (history named it, and
    the world moved since)."""


async def _resolve_parents(db, wid: str, parent_ids: list[str], *,
                           payload: str = "image") -> list[dict]:
    """Plain parents -> the live nodes a run reads as sources.

    It takes at most one for now: every committed img2img graph has
    exactly one `source_image`, so a second parent would have to be guessed
    into a channel that isn't built (anchor channels do not exist yet).

    `payload` is what the run needs its source to BE — "image" for every
    image run and for image → mesh, "3d" for a mesh verb (law 16:
    DECIMATE works on a mesh, not a picture of one). One home for the four
    source checks; only the payload sentence differs."""
    if len(parent_ids) > 1:
        raise SourceError(
            "one source image per run — the committed recipes take a single "
            "parent, and channels for a second (style, composition) aren't "
            "built yet")
    out = []
    for nid in parent_ids:
        node = await db.get_node(nid)
        if node is None:
            raise SourceError(f"no such source node '{nid}'")
        if node["purged_at"]:
            raise SourceError("that source take was purged — its lineage is "
                              "kept but its artefact is gone")
        if node["workspace_id"] != wid:
            raise SourceError("that source take belongs to another workspace")
        if node["payload"] != payload:
            want = "a mesh" if payload == "3d" else "an image"
            raise SourceError(f"a source has to be {want} — that node is a "
                              f"{node['payload']} payload")
        if not Path(node["output_path"]).is_file():
            raise SourceError("that source take's artefact is missing from "
                              "staging")
        out.append(node)
    return out


async def _stage_source(stack, node: dict) -> tuple[str, str]:
    """Content-address the parent's staged bytes into ComfyUI's input dir and
    return (graph name, sha256), the proven img2img ingress.
    Re-uploading identical bytes is a no-op, so a reroll re-stages rather
    than trusting a name to still be there.

    A scripted stack (pytest fakes, the e2e harness) owns no ComfyClient:
    the name is still content-addressed and still patched into the graph, but
    nothing is uploaded — that graph is never submitted to a real ComfyUI
    either."""
    src = Path(node["output_path"])
    blob = await run_in_threadpool(src.read_bytes)
    sha = regions_mod.sha256_bytes(blob)
    name = f"cockpit-{sha[:16]}{src.suffix or '.png'}"
    if stack.comfy is not None:
        name = await stack.comfy.upload_image(blob, name)
    return name, sha


def _build_local_graph(preset: dict, inputs: dict, capability: str,
                       source_name: str | None, *, workflow: dict | None = None):
    """The per-take base graph + what a take still needs stamped on it.
    Returns (applied, seed_targets, base). A preset whose graph is missing a
    declared target is a config error the artist can act on, not a 500 —
    and so is a raw value the preset can't take (an enum it doesn't list,
    a number that isn't one): the Advanced fold is the first
    surface that sends those, and an API caller gets the refusal in words."""
    try:
        applied, input_specs, seed_targets = presets_mod.applied_inputs(
            preset, inputs, capability)
    except (presets_mod.PresetError, ValueError, TypeError) as e:
        raise HTTPException(422, f"preset '{preset['id']}' can't take these "
                            f"inputs: {e}")
    base = (copy.deepcopy(workflow) if workflow is not None else
            presets_mod.load_workflow(preset["workflows"][capability]))
    try:
        if source_name is not None:
            presets_mod.patch_input(
                base, {"node_title": "source_image", "field": "image"},
                source_name)
        for key, spec in input_specs.items():
            if spec.get("type") == "seed":
                continue
            for target in spec["target"]:
                presets_mod.patch_input(base, target, applied[key])
    except presets_mod.PresetError as e:
        raise HTTPException(422, f"preset '{preset['id']}' and its "
                            f"{capability} graph disagree: {e}")
    return applied, seed_targets, base


@router.post("/workspaces/{wid}/turns", response_model=RunResult,
             status_code=201)
async def run_turn(wid: str, body: Run):
    """One Run = one turn of every batch's takes, laid out engine by engine
    in roster order. Each local slot carries ITS preset's
    applied raws (two engines in one turn have two input dicts), so
    a reroll rebuilds any cell from the cell alone; the turn's own
    dials hold what is shared — the seed policy, the lineage — plus every
    engine's applied under `engines`. Refusals are in the artist's words,
    one cause each (law 1)."""
    stack = _stack()
    db, sched = stack.db, stack.scheduler
    ws = await db.get_workspace(wid)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    local_total = sum(b.takes for b in body.local)
    external_total = sum(b.takes for b in body.external)
    total = local_total + external_total
    if total < 1:
        raise HTTPException(422, "a run needs at least one take")
    if total > 16:
        raise HTTPException(422, f"a run caps at 16 takes — this one asks "
                            f"for {total}")

    # Parents decide the capability: sources present = every batch's
    # img2img recipe (Refine), none = a txt2img root run.
    try:
        parents = await _resolve_parents(db, wid, body.parents)
    except SourceError as e:
        raise HTTPException(422, str(e))
    capability = "img2img" if parents else "txt2img"
    if body.local_only and body.external:
        # Proved here rather than trusted to the switch: under
        # LOCAL ONLY no external batch survives into a turn, so there is
        # no slot the external lane could ever dispatch.
        raise HTTPException(422, LOCAL_ONLY_RUN)
    if parents and body.external:
        # Proved server-side rather than merely hidden in the UI:
        # an OpenRouter image model takes a text brief, not our pixels.
        raise HTTPException(422, "external models take a text brief only — "
                            "run the source-image takes locally, or clear "
                            "the source to send this prompt out")

    policy = presets_mod.resolve_seed_policy(body.seed)
    if policy["mode"] == "locked" and not isinstance(policy.get("value"),
                                                     int):
        raise HTTPException(422, "a locked seed needs its value — unlock "
                            "it for a fresh roll per take")
    run_token = new_id()                     # namespaces ComfyUI output files
    specs: list[dict] = []
    # Both the turn and every local slot carry these, so a reroll can
    # rebuild the same img2img graph from the cell alone: reroll in place
    # holds on a refined cell.
    lineage: dict = {}
    engines: dict[str, dict] = {}            # preset id → its applied raws

    if body.local:
        catalog = stack.catalog
        for batch in body.local:
            pid = batch.preset_id
            if pid in engines:
                raise HTTPException(422, f"preset '{pid}' is listed twice — "
                                    "one row per engine, its takes on that "
                                    "row")
            if "/" in pid:
                raise HTTPException(422, "preset ids never contain '/' — "
                                    "that spelling is an external model id")
            if catalog.get(pid) is None:
                raise HTTPException(422, f"unknown preset '{pid}'")
            if capability not in catalog[pid].get("workflows", {}):
                raise HTTPException(422, f"preset '{pid}' has no "
                                    f"{capability} recipe committed — pick "
                                    "an engine that does, or set its takes "
                                    "to 0")
            if batch.take_overrides and len(batch.take_overrides) != batch.takes:
                raise HTTPException(422, f"take_overrides has "
                                    f"{len(batch.take_overrides)} entries "
                                    f"for {batch.takes} {pid} takes — one "
                                    "per take in slot order, or none")
            engines[pid] = {}                # claimed; filled below
        source_name, source_sha = (await _stage_source(stack, parents[0])
                                   if parents else (None, None))
        lineage = ({"capability": capability,
                    "parents": [n["id"] for n in parents],
                    "source_image_sha256": source_sha} if parents else {})
        for batch in body.local:
            preset = catalog[batch.preset_id]
            inputs = dict(batch.inputs)
            inputs["seed"] = policy          # the turn's one policy
            if body.prompt is not None and "prompt" not in inputs:
                inputs["prompt"] = body.prompt
            applied, seed_targets, base = _build_local_graph(
                preset, inputs, capability, source_name)
            applied.update(lineage)
            engines[batch.preset_id] = applied
            overrides = batch.take_overrides
            for i in range(batch.takes):
                spec = {"engine": batch.preset_id, "dials": applied}
                if overrides and overrides[i]:
                    # A swept take: its own graph from its own
                    # values, and its own dials on the slot so a reroll
                    # rebuilds THIS cell's value rather than the batch's.
                    own, _, graph = _build_local_graph(
                        preset, {**inputs, **overrides[i]},
                        capability, source_name)
                    spec["dials"] = {**own, **lineage}
                else:
                    graph = copy.deepcopy(base)
                pos = len(specs)
                seed = presets_mod.take_seed(policy, pos)
                presets_mod.patch_seed(graph, seed, seed_targets, capability)
                presets_mod.set_save_prefix(
                    graph, f"cockpit/v4/{run_token}/s{pos}")
                specs.append({**spec, "seed": seed, "submitted_graph": graph})

    if body.external:
        if not (body.prompt or "").strip():
            raise HTTPException(422, "external models need a prompt brief")
        available = stack.provider_catalog.selectable()
        seen: set[str] = set()
        for batch in body.external:
            mid = batch.model_id
            if mid in seen:
                raise HTTPException(422, f"model '{mid}' is listed twice — "
                                    "one row per model, its takes on that "
                                    "row")
            seen.add(mid)
            mcfg = available.get(mid)
            if mcfg is None:
                raise HTTPException(422, f"unknown external model '{mid}' "
                                    "(not in the OpenRouter catalog or "
                                    "providers.json)")
            for _ in range(batch.takes):
                seed = presets_mod.take_seed(policy, len(specs))
                sent_seed = seed if mcfg.get("supports_seed") else None
                specs.append({
                    "engine": mid, "seed": seed,
                    "external": {"model": mid, "prompt": body.prompt,
                                 "seed": sent_seed,
                                 "params": mcfg.get("params")},
                    "committed_usd": mcfg.get("price_usd_per_image")})

    # E6: the frames this run asks for, and whether the drive can take
    # the takes — refused in words before any row exists
    frames = [_frame_of(sp.get("dials") or {}) if sp.get("submitted_graph")
              else None for sp in specs]
    await _refuse_if_disk_cannot_hold(
        db, [f[0] * f[1] if f else ASSUMED_PX for f in frames])

    anchor = (body.anchor.x, body.anchor.y) if body.anchor else None
    turn_dials = {"seed": policy, **lineage, "engines": engines}
    if body.local_only:
        turn_dials["local_only"] = True      # The manifest flag lives here
    turn = await db.create_turn_with_slots(wid, body.prompt, turn_dials,
                                           specs, anchor=anchor)
    parent_ids = tuple(n["id"] for n in parents)
    verb = "refine" if parents else "run"
    requests = [DispatchRequest(
        slot_id=s["id"], engine=s["engine"],
        submitted_graph=s["submitted_graph"], external=s["external"],
        committed_usd=s["committed_usd"], frame=frames[s["pos"]],
        payload="image", verb=verb,
        prompt=body.prompt, parents=parent_ids, seed=s["seed"])
        for s in turn["slots"]]
    await sched.submit_turn(turn["id"], requests)
    await db.touch_workspace(wid)
    fresh = await db.get_turn(turn["id"])    # queued or held (benched)
    return {"turn": _turn_view(fresh), "seq": await db.latest_seq()}


# --- cell verbs: reroll in place · set aside / restore ---


async def _reroll(stack, slot: dict, turn: dict, verb: str,
                  *, local_only: bool = False,
                  keep_seed: bool = False,
                  frame: tuple[int, int] | None = None) -> dict:
    """Reroll-in-place machinery, shared by reroll (one cell) and backfill
    (every set-aside cell of a turn): same slot, same engine, same dials,
    new seed. The dispatch payload is rebuilt run_turn's way (db.reroll_slot
    wipes the stored one): graphs and prices are config, not history, so a
    reroll re-reads both — an external re-quotes at today's pin, and a
    refined cell re-stages its source image (the cell's dials carry the
    parent ids). Refusals are HTTPExceptions in the artist's words, cost
    stated; returns the re-armed slot. `local_only` is the top bar's
    switch riding the request: a paid cell under it is refused
    before anything is re-armed. `keep_seed` (A5a) reruns with the
    cell's own seed — the empty frame was the engine's, so the settings
    stand as they are. `frame` (A1) rebuilds the cell at a new
    size, which becomes the cell's own dials from then on."""
    db, sched = stack.db, stack.scheduler
    slot_id = slot["id"]
    if slot.get('dials', {}).get('crexil'):
        raise HTTPException(409, 'Reuse this direction and review a new request. Its sources, operation revision and limits must be checked together.')
    if local_only and wiring.lane_of(slot["engine"]) != "local":
        raise HTTPException(409, f"LOCAL ONLY is on — '{slot['engine']}' is "
                            "a paid model, so this cell stays as it is and "
                            "nothing was spent; flip the switch to reroll it")
    if frame is not None and wiring.lane_of(slot["engine"]) != "local":
        raise HTTPException(409, f"'{slot['engine']}' renders at the "
                            "provider's default size — a frame can't be "
                            "sent out; the cell stays as it is and nothing "
                            "was spent")
    seed = (slot["seed"] if keep_seed and slot["seed"] is not None
            else presets_mod.take_seed({"mode": "random"}, 0))
    engine = slot["engine"]
    graph, external, committed = None, None, None

    parent_ids = list(slot["dials"].get("parents") or ())

    if slot["dials"].get("capability") == "region":
        # A region cell rebuilds its whole envelope from its dials
        # (same regions, same plan, new seed) — api_v4_regions owns the
        # machinery and its 409 words
        from . import api_v4_regions
        if frame is not None:
            raise HTTPException(409, "a region take lands at its source's "
                                "size — retry it as it is, or edit the "
                                "regions on a smaller source")
        env, size = await api_v4_regions.rebuild_region_take(
            stack, slot, turn, seed)
        try:
            fresh = await db.reroll_slot(slot_id, seed, committed_usd=None,
                                         dials=None)
        except ValueError as e:
            raise HTTPException(409, str(e))
        await sched.resubmit_slot(slot_id, DispatchRequest(
            slot_id=slot_id, engine=engine, submitted_graph=env,
            external=None, committed_usd=None, frame=size,
            payload="image", verb="region", prompt=turn["prompt"],
            parents=tuple(parent_ids), seed=seed))
        return fresh

    if slot["dials"].get("capability") == "to_3d":
        # A mesh cell rebuilds from its dials alone (same source,
        # same engine, same params, new seed) — the source is re-resolved
        # so a set-aside or purged parent refuses rather than silently
        # pointing at a path that has gone
        from .mesh import (MESH_INPUT_KINDS, SEEDED_KINDS, mesh_envelope,
                           mesh_verb, parent_runner_job)
        if frame is not None:
            raise HTTPException(409, "a mesh has no frame to ask for — its "
                                "size is the mesh's own; retry it as it is")
        kind = slot["dials"].get("kind") or "generate"
        want = "3d" if kind in MESH_INPUT_KINDS else "image"
        try:
            parents = await _resolve_parents(db, turn["workspace_id"],
                                             parent_ids, payload=want)
        except SourceError as e:
            raise HTTPException(409, f"{e} — the old take stays and nothing "
                                "was spent")
        source = parents[0]
        if not source.get("output_path"):
            raise HTTPException(409, "the artefact this take was built from "
                                "is no longer on disk — the old take stays; "
                                "reroll that take first")
        if kind not in SEEDED_KINDS:
            seed = None                  # a deterministic verb reads no seed
        env = mesh_envelope(engine, source["output_path"], kind=kind,
                            seed=seed,
                            params=slot["dials"].get("params") or {},
                            mesh_job_ref=parent_runner_job(source))
        try:
            fresh = await db.reroll_slot(slot_id, seed, committed_usd=None,
                                         dials=None)
        except ValueError as e:
            raise HTTPException(409, str(e))
        await sched.resubmit_slot(slot_id, DispatchRequest(
            slot_id=slot_id, engine=engine, submitted_graph=env,
            external=None, committed_usd=None, frame=None,
            payload="3d", verb=mesh_verb(kind), prompt=turn["prompt"],
            parents=tuple(parent_ids), seed=seed))
        return fresh

    capability = "img2img" if parent_ids else "txt2img"

    if wiring.lane_of(engine) == "local":
        preset = stack.catalog.get(engine)
        if preset is None:
            raise HTTPException(409, f"preset '{engine}' is no longer "
                                "configured — the old take stays and nothing "
                                "was spent; restore the preset to reroll")
        if capability not in preset.get("workflows", {}):
            raise HTTPException(409, f"preset '{engine}' no longer has a "
                                f"{capability} recipe — the old take stays "
                                "and nothing was spent; restore it to reroll")
        source_name = None
        if parent_ids:
            try:
                parents = await _resolve_parents(
                    db, turn["workspace_id"], parent_ids)
            except SourceError as e:
                raise HTTPException(409, f"{e} — the old take stays and "
                                    "nothing was spent")
            source_name, _ = await _stage_source(stack, parents[0])
        inputs = {k: v for k, v in slot["dials"].items() if k != "seed"}
        if frame is not None:
            if capability != "txt2img":
                raise HTTPException(409, "a refined cell takes its size "
                                    "from its source image — retry it as "
                                    "it is, or refine a smaller source")
            inputs["width"], inputs["height"] = frame
        applied, seed_targets, graph = _build_local_graph(
            preset, inputs, capability, source_name)
        if frame is not None:
            # the preset's bounds and grid may have moved the numbers —
            # what runs is what the cell keeps (clamping is visible)
            new_dials = {**slot["dials"], **{k: applied[k] for k in
                                             ("width", "height")
                                             if k in applied}}
            frame = _frame_of(new_dials)
        else:
            new_dials = None
            frame = _frame_of(slot["dials"])
        presets_mod.patch_seed(graph, seed, seed_targets, capability)
        presets_mod.set_save_prefix(
            graph, f"cockpit/v4/{new_id()}/s{slot['pos']}")
    else:
        mcfg = stack.provider_catalog.selectable().get(engine)
        if mcfg is None:
            raise HTTPException(409, f"model '{engine}' has left the "
                                "catalogue — the old take stays and nothing "
                                "was spent; pin it in providers.json to "
                                "reroll")
        committed = mcfg.get("price_usd_per_image")
        external = {"model": engine, "prompt": turn["prompt"],
                    "seed": seed if mcfg.get("supports_seed") else None,
                    "params": mcfg.get("params")}
        new_dials = None
        frame = None

    try:
        fresh = await db.reroll_slot(slot_id, seed, committed_usd=committed,
                                     dials=new_dials)
    except ValueError as e:                  # not in a rerollable state
        raise HTTPException(409, str(e))
    await sched.resubmit_slot(slot_id, DispatchRequest(
        slot_id=slot_id, engine=engine, submitted_graph=graph,
        external=external, committed_usd=committed, frame=frame,
        payload="image", verb=verb, prompt=turn["prompt"],
        parents=tuple(parent_ids), seed=seed))
    return fresh


@router.post("/slots/{slot_id}/reroll", response_model=RerollResult)
async def reroll_slot(slot_id: str, body: RerollBody | None = None):
    """Reroll: one cell, in place (see _reroll). The optional body is the
    LOCAL ONLY switch, A5a's `keep_seed` and A1's `frame`; no body means
    none of them."""
    stack = _stack()
    db = stack.db
    slot = await db.get_slot(slot_id)
    if slot is None:
        raise HTTPException(404, "no such slot")
    turn = await db.get_turn(slot["turn_id"])
    frame = ((body.frame.width, body.frame.height)
             if body and body.frame else None)
    fresh = await _reroll(stack, slot, turn, "reroll",
                          local_only=bool(body and body.local_only),
                          keep_seed=bool(body and body.keep_seed),
                          frame=frame)
    await db.touch_workspace(turn["workspace_id"])
    return {"slot": _slot_slim(fresh), "seq": await db.latest_seq()}


@router.post("/turns/{turn_id}/backfill", response_model=BackfillResult)
async def backfill_turn(turn_id: str, body: MoneyGate | None = None):
    """Backfill, set aside's batch verb: every set-aside slot of
    the turn re-armed with a fresh seed from its own engine, one action —
    so a batch of nine stays a batch of nine. The aside takes keep their
    rows (law 7) and their tray entries; the slots run new takes in the
    same footprints (law 8). A slot that can't be re-armed (preset gone,
    source purged, model left the catalogue — or a paid cell under the
    LOCAL ONLY switch) is refused in words and never stops the
    others (law 1). Nothing to fill is not an error."""
    stack = _stack()
    db = stack.db
    turn = await db.get_turn(turn_id)
    if turn is None:
        raise HTTPException(404, "no such turn")
    local_only = bool(body and body.local_only)
    rerolled, refused = 0, []
    for slot in turn["slots"]:
        if slot["status"] != "set_aside":
            continue
        try:
            await _reroll(stack, slot, turn, "backfill",
                          local_only=local_only)
            rerolled += 1
        except HTTPException as e:
            refused.append({"slot_id": slot["id"], "pos": slot["pos"],
                            "reason": str(e.detail)})
    if rerolled:
        await db.touch_workspace(turn["workspace_id"])
    return {"turn": _turn_view(await db.get_turn(turn_id)),
            "rerolled": rerolled, "refused": refused,
            "seq": await db.latest_seq()}


async def _node_view_full(db, node: dict) -> dict:
    """db.get_node stays unjoined by contract (list_nodes docstring); the
    wire shape carries the two label keys, so single-node responses fetch
    them here. A blind run's take answers masked until its reveal, its times
    its run's creation time, as every other node read gives them."""
    turn = await db.get_turn(node["turn_id"]) if node["turn_id"] else None
    slot = await db.get_slot(node["slot_id"]) if node["slot_id"] else None
    view = _node_view({**node, "turn_idx": turn["idx"] if turn else None,
                       "slot_pos": slot["pos"] if slot else None})
    (view,) = await blind.apply(db, "node", [view], workspace_id=node["workspace_id"])
    return view


@router.post("/nodes/{nid}/set-aside", response_model=Node)
async def set_aside_node(nid: str):
    """Set aside is a place, not a delete: the node dims, the slot keeps its
    footprint, restore is one press away. Idempotent."""
    db = _stack().db
    node = await _live_node(nid)
    if not node["set_aside"]:
        node = await db.set_aside_node(nid)
        await db.touch_workspace(node["workspace_id"])
    return await _node_view_full(db, node)


@router.post("/nodes/{nid}/restore", response_model=Node)
async def restore_node(nid: str):
    db = _stack().db
    node = await _live_node(nid)
    if node["set_aside"]:
        node = await db.restore_node(nid)
        await db.touch_workspace(node["workspace_id"])
    return await _node_view_full(db, node)


# --- the queue view ---


@router.get("/queue", response_model=QueueView)
async def queue_view():
    stack = _stack()
    db, sched = stack.db, stack.scheduler
    snap = sched.snapshot()
    now = datetime.now(timezone.utc)
    local_pos = {sid: i for i, sid in enumerate(snap["local_queue"])}

    local, external, held, late = [], [], [], []
    for s in await db.active_slots():
        lane = wiring.lane_of(s["engine"])
        status = s["status"]
        elapsed = None
        if status == "running" and s["started_at"]:
            started = datetime.fromisoformat(s["started_at"])
            elapsed = max(0, int((now - started).total_seconds() * 1000))
        position = None
        if lane == "local":
            if status == "running":
                position = 0
            elif s["id"] in local_pos:
                position = 1 + local_pos[s["id"]]
        take = snap["takes"].get(s["id"])
        bench = None
        if status == "held":
            # the covering scope by the scheduler's own rule: the engine's,
            # this turn's (A9), the provider's or the lane's; the cell offers
            # its resume against it
            covering = sched.bench_for(s["engine"], s["turn_id"])
            bench = covering.scope if covering else None
        view = {
            "id": s["id"], "turn_id": s["turn_id"],
            "turn_idx": s["turn_idx"], "workspace_id": s["workspace_id"],
            "engine": s["engine"], "status": status,
            "attempt": s["attempt"], "retry_of": s["retry_of"],
            "position": position,
            "elapsed_ms": elapsed, "committed_usd": s["committed_usd"],
            "charged_usd": s["charged_usd"],
            "cancel_requested": bool(take and take["cancel_requested"]),
            "awaiting": bool(status == "late" and take
                             and take.get("detached")),
            "bench": bench, "passes": (take or {}).get("passes"),
            "created_at": s["created_at"],
        }
        if status == "held":
            held.append(view)
        elif status == "late":
            late.append(view)
        elif lane == "local":
            local.append(view)
        else:
            external.append(view)

    depth = len(local)                       # queued+running local rows
    local, external, held, late = [await blind.apply(db, "queue", rows)
                                   for rows in (local, external, held, late)]
    # An engine-and-run bench (A9's) names its engine and slot positions, so
    # one whose run is blind and unrevealed stays out of the list.
    hidden = await blind.hidden_turns(db)
    return {
        "seq": await db.latest_seq(),
        "now": now.isoformat(),
        "depth": depth,
        "lanes": {
            "local": {"label": LOCAL_LABEL, "depth": depth, "slots": local},
            "external": {
                "label": EXTERNAL_LABEL,
                "concurrency": config.EXTERNAL_CONCURRENCY,
                "active": sum(1 for v in external
                              if v["status"] == "running"),
                "slots": external},
        },
        "held": held,
        "late": late,
        "benches": [{"scope": scope, **b}
                    for scope, b in snap["benches"].items()
                    if blind.scope_run(scope) not in hidden],
        "checks": [{"slot_id": sid, **c}
                   for sid, c in snap.get("checks", {}).items()],
        "retries": [{"slot_id": sid, **r}
                    for sid, r in snap.get("retries", {}).items()],
        "gpu": _gpu_state(snap.get("gpu") or {}),
    }


def _gpu_view(reading: dict | None) -> dict | None:
    if reading is None:
        return None
    return {"total_bytes": reading["total"], "free_bytes": reading["free"],
            "comfy_bytes": reading["comfy"],
            "foreign_bytes": reading["foreign"],
            "name": reading.get("name") or ""}


def _gpu_state(g: dict) -> dict:
    return {"reading": _gpu_view(g.get("reading")),
            "rechecking": bool(g.get("rechecking")),
            "manual_hold": bool(g.get("manual_hold"))}


# --- E6 + C2: the card and the drive, and the artist's two moves ---


@router.get("/headroom", response_model=Headroom)
async def headroom():
    """E6's fact sheet: the card now (ComfyUI's own reading — null when it
    can't say; nothing is guessed), the staging drive's free bytes, and
    this card's bytes-per-pixel so the composer can state what a run
    needs from the frames it knows."""
    stack = _stack()
    reading = None
    if stack.gpu_probe is not None:
        try:
            reading = await stack.gpu_probe()
        except Exception:
            reading = None
    bpp, source = await _bytes_per_px(stack.db)
    return {
        "gpu": _gpu_view(None if reading is None else reading.__dict__),
        "gpu_hold_threshold_bytes": config.GPU_FOREIGN_HOLD_BYTES,
        "gpu_held": bool(reading is not None
                         and reading.foreign >= config.GPU_FOREIGN_HOLD_BYTES),
        "disk_free_bytes": _disk_free(),
        "disk_margin_bytes": config.DISK_MARGIN_BYTES,
        "disk_path": str(config.STAGING_DIR),
        "bytes_per_px": bpp,
        "bytes_per_px_source": source,
        "at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/gpu/check", response_model=GpuCheckResult)
async def gpu_check():
    """C2's CHECK AGAIN: one live look; a held lane resumes if the card is
    free."""
    r = await _stack().scheduler.gpu_check()
    return {**r, "reading": _gpu_view(r["reading"])}


@router.post("/gpu/hold", response_model=GpuHoldResult)
async def gpu_hold():
    """C2's HOLD THE QUEUE: the lane stays benched until an unbench; the
    automatic re-check stops."""
    return await _stack().scheduler.gpu_hold()


# --- cancel (one breath) + unbench + events ---


@router.post("/slots/{slot_id}/cancel", response_model=CancelResult)
async def cancel_slot(slot_id: str):
    try:
        return await _stack().scheduler.cancel_slot(slot_id)
    except ValueError:
        raise HTTPException(404, "no such slot")


@router.post("/turns/{turn_id}/cancel", response_model=TurnCancelResult)
async def cancel_turn(turn_id: str):
    try:
        return await _stack().scheduler.cancel_turn(turn_id)
    except ValueError:
        raise HTTPException(404, "no such turn")


@router.post("/unbench", response_model=UnbenchResult)
async def unbench(body: Unbench):
    """Lift a bench from the legacy canvas (UNBENCH, TRY ONCE MORE) by the
    project surface's resume rules: the lift names its participant on its
    event (law 23); a runtime's bench lifts only once a live check finds
    its cause gone, else 422 says what is still wrong (laws 3 and 4); and an
    agent never sends held work that reserves money, nor any attempt of a
    hidden run (403)."""
    from .execution_controls import HIDDEN_RESUME, PAID_RESUME, remaining_cause, reserves_money
    stack = _stack()
    sched = stack.scheduler
    bench = sched.standing(body.scope)
    if bench is None:
        return {"scope": body.scope, "cleared": False}
    if body.actor.kind != "human":
        held = await sched.held_by(body.scope)
        hidden = await blind.hidden_turns(stack.db)
        if any(slot["turn_id"] in hidden for slot in held):
            raise HTTPException(403, HIDDEN_RESUME)
        if reserves_money(held):
            raise HTTPException(403, PAID_RESUME)
    why = await remaining_cause(stack, bench)
    if why:
        raise HTTPException(422, why)
    await sched.unbench(body.scope, actor=body.actor.model_dump())
    return {"scope": body.scope, "cleared": True}


@router.get("/events", response_model=Events)
async def events(since: int = 0, workspace_id: str | None = None,
                 limit: int = 500):
    db = _stack().db
    rows = await db.events_since(since, workspace_id=workspace_id,
                                 limit=max(1, min(limit, 500)))
    rows = await blind.apply(db, "event", rows, workspace_id=workspace_id)
    return {"events": rows,
            "seq": rows[-1]["seq"] if rows else since}
