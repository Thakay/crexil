"""Promote: the one irreversible act, and the one place the cockpit writes
into the artist's real production tree.

The seam it rides is `promote.py`: the target is validated IMMEDIATELY
before writing and never created, and a promotion is all-or-nothing — no
manifest ever sits in the promote folder without its artefact. This module adds the verb's
routes on top of it:

- `GET /promote-target` — what the destination IS right now, in words, so
  the plinth can say it before the hold (a reading, never a promise).
- `POST /promote-target` — re-point. Validated BEFORE it is stored, so the
  cockpit never remembers a broken target; an empty path clears the
  override and .env's default takes over again. Takes the artist's `D:\…`
  spelling as well as a POSIX path.
- `GET /promote-browse` — the picker's one call: one level of the promotion
  drive as HANDLES, each a string the server minted and a re-point takes
  verbatim, so the face never composes a path. Empty `path` is the top —
  the roots themselves. Reads only, fenced before anything is touched, and
  a folder that can't be listed answers with the same two halves a broken
  target does.
- `GET /nodes/{nid}/promote-preview` — every file the hold would write, by
  its final path, with the take's identity and what it cost (law 5).
  Asking writes nothing.
- `POST /nodes/{nid}/promote` — build the manifest, write the pair, record
  the fact.

Three shapes worth stating because none is obvious. Promoting twice is
allowed and says so: the artist owns the tree and may have moved or
deleted what we wrote, so a second promotion is a second asset id and a
second ledger row, and the preview names the earlier ones — refusing would
be the app claiming to know the state of a folder it doesn't watch. The
target is fenced to the promotion drive: the cockpit mounts the whole
drive so a re-point can reach folders the artist would actually name, and
`config.PROMOTE_ROOTS` is the fence that keeps a typo on it (a one-folder
mount would leave the safe paths unreachable and let `/tmp` through).
A mesh promotes as a mesh: the artefact keeps its own suffix, the manifest
carries the mesh's own measurements and replay tier, and the cached
turntable rides out beside the GLB, so a promoted mesh folder is readable
without a 3D viewer. A broken target is a 409, not a 503: `detail` is the
plain cause and the remedy rides beside it, so the face can draw the
re-point control under the sentence that asks for it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from . import config, manifests, promote
from .api_v4 import _live_node, _mesh_view, _stack, _turntable_file
from .db import new_id
from .schemas import (PromoteBrowse, PromotePreview, PromoteRepoint,
                      PromoteTarget, Promotion)

router = APIRouter(prefix="/api")

PROMOTE_DIR_KEY = "promote_dir"

# a bare relative path has no single meaning — it names one folder on
# the desktop and another inside the container — so it is the one spelling
# still refused, at every door that takes a path.
RELATIVE_REFUSED = (
    "give the folder's whole path — either the drive spelling "
    "(D:\\PROMOTED) or the full path from / — because a relative one "
    "means something different inside the cockpit's container than it does "
    "on your desktop")


def _broken(e: promote.PromoteTargetError) -> JSONResponse:
    """D5 refused, with its two halves kept apart. `detail` carries the
    whole sentence so every generic reader (compose.errText, verbs.verbErr)
    already says the right thing; `remedy` and `path` ride beside it so the
    promote plinth can draw its re-point control against the folder that
    actually broke. 409, not 503: nothing failed — the destination isn't
    one, and the take is untouched (main.py owns 500s)."""
    return JSONResponse(status_code=409,
                        content={"detail": f"{e.message} — {e.remedy}",
                                 "message": e.message, "remedy": e.remedy,
                                 "path": str(e.target)})


async def _root(db) -> tuple[Path, str]:
    """The promote root and where it came from. `.env` is the default; a
    re-point stored here wins (a container can't have its .env edited from
    the inside, which is exactly the D5 moment)."""
    stored = await db.get_meta(PROMOTE_DIR_KEY)
    if stored:
        return Path(stored), "set-here"
    return config.PROMOTE_DIR, "env"


async def _target(db) -> dict:
    root, source = await _root(db)
    return {**promote.target_state(root), "source": source,
            "default_path": str(config.PROMOTE_DIR)}


async def _orbit(node: dict) -> Path | None:
    """The mesh's turntable, ready to ride out with it, or None.

    The turntable routes already cache the orbit beside the node's own
    artefact, so the usual promotion carries a file that is simply there.
    `_turntable_file` is still the one door to it: on a mesh whose plate was
    never opened the cache is cold, and one fetch here is better than a
    promoted mesh that is nothing but bytes on the artist's disk.

    Never fatal, either way. A mesh with no orbit, a runner that isn't
    answering, a cache that can't be written — none of them is a reason to
    refuse the mesh itself, so each returns None and the promotion goes
    without it. What actually landed is in the response's `files` and in
    the manifest's `mesh.turntable`; neither claims an orbit that isn't
    there (law 12)."""
    if node["payload"] != "3d":
        return None
    try:
        return await _turntable_file(node)
    except (HTTPException, OSError):
        return None


def _folder_for(node: dict, workspace: dict) -> str:
    """`PROMOTE_DIR/<workspace-slug>/<asset_id>.*`, with the `_3d/`
    convention for meshes. The slug, not the name — it survives a rename,
    so a promoted path stays a promoted path (db.rename_workspace)."""
    slug = workspace["slug"]
    return f"_3d/{slug}" if node["payload"] == "3d" else slug


def _facts_line(node: dict, turn: dict | None, slot: dict | None) -> str:
    """The take's identity in the verb row's own grammar (`12b · ENGINE ·
    S 7`) — curated static text around stored values, nothing generated."""
    parts = []
    if turn is not None and slot is not None:
        parts.append(f"{turn['idx']}{chr(97 + slot['pos'])}")
    parts.append((node.get("engine") or "IMPORTED").upper())
    parts.append("NO SEED" if node.get("seed") is None
                 else f"S {node['seed']}")
    if node.get("take"):
        parts.append(f"TAKE {node['take'] + 1}")
    return " · ".join(parts)


def _cost_line(node: dict) -> str:
    """Law 5: money is always stated, including when the answer is
    "nothing". Committed and charged are separate values, so a divergence
    is visible rather than averaged away."""
    charged, committed = node.get("charged_usd"), node.get("committed_usd")
    if charged is None and committed is None:
        return "This take cost no money — it ran on your own card."
    if charged is None:
        return (f"About ${committed:.2f} was committed for this take; the "
                "provider hasn't reported a charge.")
    if committed is not None and abs(charged - committed) >= 0.005:
        return (f"${charged:.2f} was charged for this take "
                f"(${committed:.2f} was committed).")
    return f"${charged:.2f} was charged for this take."


async def _context(nid: str) -> tuple:
    """node + its workspace, turn, slot and parent rows — everything the
    manifest and the preview both need, fetched once."""
    db = _stack().db
    node = await _live_node(nid)
    workspace = await db.get_workspace(node["workspace_id"])
    turn = await db.get_turn(node["turn_id"]) if node["turn_id"] else None
    slot = await db.get_slot(node["slot_id"]) if node["slot_id"] else None
    parents = [p for p in
               [await db.get_node(pid) for pid in (node["parents"] or [])]
               if p is not None]
    return db, node, workspace, turn, slot, parents


@router.get("/promote-target", response_model=PromoteTarget)
async def promote_target():
    return await _target(_stack().db)


@router.post("/promote-target", response_model=PromoteTarget)
async def repoint_promote_target(body: PromoteRepoint):
    """The offer, honoured: point the cockpit at a folder that works, or
    clear it back to .env's. A folder that can't take a promotion is
    refused here rather than stored and discovered at the next hold."""
    db = _stack().db
    # `D:\PROMOTED` is how a Windows user writes it, so it
    # is translated here rather than refused. Only a path that is neither
    # spelling — a bare relative one — still has no single meaning.
    path = promote.from_windows(body.path)
    if not path:
        await db.set_meta(PROMOTE_DIR_KEY, None)
        return await _target(db)
    if not path.startswith("/"):
        raise HTTPException(422, RELATIVE_REFUSED)
    try:
        promote.validate_target(Path(path))
    except promote.PromoteTargetError as e:
        return _broken(e)               # refused, and nothing remembered
    await db.set_meta(PROMOTE_DIR_KEY, path)
    return await _target(db)


@router.get("/promote-browse", response_model=PromoteBrowse)
async def promote_browse(path: str = ""):
    """One level of the drive, as handles. Empty `path` is the top
    (the roots); either spelling is taken for a folder. Reads only.
    The fence is checked before the folder is touched, and a folder that
    can't be listed at all is the same 409 a broken target gives — halves
    apart, so the panel can say it and offer the way back."""
    text = promote.from_windows(path)
    if not text:
        return await asyncio.to_thread(promote.browse, None)
    if not text.startswith("/"):
        raise HTTPException(422, RELATIVE_REFUSED)
    try:
        return await asyncio.to_thread(promote.browse, Path(text))
    except promote.PromoteTargetError as e:
        return _broken(e)


@router.get("/nodes/{nid}/promote-preview", response_model=PromotePreview)
async def promote_preview(nid: str):
    """Every file the hold would write, before the hold. Writes nothing."""
    db, node, workspace, turn, slot, parents = await _context(nid)
    root, _ = await _root(db)
    asset_id = new_id()
    folder = _folder_for(node, workspace)
    dest = f"{root}/{folder}"

    mesh = _mesh_view(node) or {}
    # A mesh's rows are its own. The suffix is the artefact's own
    # (`promote_candidate` keeps it), the manifest holds different facts,
    # and the orbit rides beside the GLB — a promoted mesh that only a 3D
    # viewer can open is a folder the artist can't read.
    if node["payload"] == "3d":
        files = [{"path": f"{dest}/{asset_id}"
                          f"{Path(node['output_path']).suffix or '.glb'}",
                  "what": "the mesh, exactly the file the engine wrote"},
                 {"path": f"{dest}/{asset_id}.json",
                  "what": "its manifest — engine, seed, the cutout, the "
                          "mesh's own measurements, and the file itself by "
                          "sha256"}]
        if mesh.get("has_turntable"):
            files.append({"path": f"{dest}/{asset_id}"
                                  f"{manifests.TURNTABLE_SUFFIX}",
                          "what": "the engine's own turntable render, so "
                                  "the mesh has a face outside a 3D viewer"})
    else:
        files = [{"path": f"{dest}/{asset_id}.png",
                  "what": "the take, full resolution, byte for byte"},
                 {"path": f"{dest}/{asset_id}.json",
                  "what": "its manifest — engine, prompt, seed, model files "
                          "by sha256, the exact graph that ran"}]
    for i, parent in enumerate(parents):
        if Path(parent["output_path"]).is_file():
            name = "source.png" if i == 0 else f"source-{i + 1}.png"
            files.append({"path": f"{dest}/{asset_id}.inputs/{name}",
                          "what": "the image this mesh was made from"
                          if node["payload"] == "3d"
                          else "the image this take was made from"})
    region = ((turn or {}).get("dials") or {}).get("region") or {}
    for rid in region.get("region_ids") or []:
        files.append({"path": f"{dest}/{asset_id}.inputs/region-{rid}.png",
                      "what": "a region mask this take was drawn through"})

    already = [e["ts"] for e in
               await db.node_events(nid, "node.promoted")]
    return {
        "node_id": nid, "asset_id": asset_id, "folder": dest, "files": files,
        "facts": _facts_line(node, turn, slot),
        "cost": _cost_line(node),
        "replayable": (manifests.REPLAY_EXTERNAL if node.get("external")
                       else manifests.REPLAY_MESH if node["payload"] == "3d"
                       else manifests.REPLAY_LOCAL),
        "target": await _target(db),
        "already": already,
    }


@router.post("/nodes/{nid}/promote", response_model=Promotion)
async def promote_node(nid: str):
    """The hold's other end. The manifest is built first, the
    target validated immediately before the move, and the fact recorded
    only once both files are in place."""
    db, node, workspace, turn, slot, parents = await _context(nid)
    if not Path(node["output_path"]).is_file():
        raise HTTPException(
            409, "this take's file isn't on the cockpit's disk any more, so "
                 "there is nothing to copy out — the record of it stays")
    root, _ = await _root(db)
    asset_id = new_id()
    manifest, extra_files = await manifests.build_v4_manifest(
        db, asset_id=asset_id, workspace=workspace, node=node, turn=turn,
        slot=slot, parents=parents, turntable=await _orbit(node))
    try:
        art, man, _staged = await asyncio.to_thread(
            promote.promote_candidate, Path(node["output_path"]), manifest,
            _folder_for(node, workspace), asset_id, extra_files, root=root)
    except promote.PromoteTargetError as e:
        return _broken(e)               # nothing written, the take untouched
    event = await db.record_promotion(nid, str(man), str(art))
    await db.touch_workspace(node["workspace_id"])
    return {"node_id": nid, "asset_id": asset_id, "promoted_path": str(art),
            "manifest_path": str(man),
            "files": [str(art), str(man)]
                     + [f"{art.parent}/{rel}" for _src, rel in extra_files],
            "promoted_at": event["ts"], "seq": event["seq"]}
