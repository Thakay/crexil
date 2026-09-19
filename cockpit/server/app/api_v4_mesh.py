"""Mesh turns: the 3D lane's run route.

Mesh turns take their OWN route for the same reason region turns do — the
shape is different, not a variation on Run:

- No prompt reaches a mesh engine. The image is the input (the matrix's
  `imageRef`, verified), so there is no text channel to send. The source
  take's prompt rides along as lineage so the mesh reads as what it came
  from, but nothing is generated from it.
- No external model can appear. Structurally: this request has no
  external field, so there is nothing to refuse.
- No frame is asked for. The output is a mesh; the FRAME dial has no
  meaning here and the matrix says so (`maxResolution: null`).
- The engines are not presets. They are subprocesses in their own venvs
  with no committed ComfyUI graph, so a batch names the capability
  matrix's engine id (`trellis2-4b`, `hunyuan3d-21`, `gltfpack`) and its
  own declared params.

A mesh is always refine-shaped: it needs a parent, and there is no root
form of it. Every take rides `mesh_envelope` (`mesh.py`) as a
`{"mesh_job": …}` payload on the ordinary local lane, so a mesh serialises
against an image take with no code here.

Every 3D verb comes through this one route. `kind` names the step —
`generate` (an image parent), `decimate` (a mesh parent) or `texture` (a
mesh parent AND a reference image) — and nothing else about the shape
changes: the same parent resolution, the same matrix check, the same disk
refusal, the same turn. A verb that needed its own route would mean the
seam had not been found. What a kind DOES change is stated per kind and
refused in words: what its parent must be, whether a seed means anything,
whether it takes a second parent, and whether more than one take of it is
a different result or just a copy.

Refusals are 422s in the artist's words, one cause each (law 1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import config
from . import presets as presets_mod
from .executors import DispatchRequest
from .mesh import (MESH_ENGINES, MESH_INPUT_KINDS, MESH_KINDS, MESH_REF_KINDS,
                   SEEDED_KINDS, fields_sidecar, mesh_kind_input,
                   mesh_envelope, mesh_verb, parent_runner_job)
from .schemas import MeshHealth, MeshRun, RunResult

router = APIRouter(prefix="/api")

# A landed GLB from these engines runs 8–60 MB with its texture (funnel
# validation, `file_mb`); the turntable frames stay host-side. This is the
# E6 estimate used to refuse a run the drive can't hold — authored, like
# the image lane's bytes-per-pixel fallback, and deliberately generous.
#
# The voxel field raises it: a TRELLIS.2 generate lands its field beside the
# mesh (measured 2026-08-29: 21 MiB and 92 MiB on two props, against 34–41 MiB
# GLBs — the field is sized by occupied voxels, so a dense prop can cost more
# than its mesh). The figure is the worst case across engines and verbs, so it
# carries the sidecar unconditionally rather than branching — refusing a little
# early is the safe direction here.
MESH_ASSUMED_BYTES = 180 * (1 << 20)


def _matrix_engines() -> dict[str, dict]:
    """The capability matrix's mesh rows, by id. The matrix is the product's
    roster (law 12); this route never invents an engine the matrix has
    not published."""
    from .api_v4 import _read_config
    rows = _read_config("engines.json").get("engines") or []
    return {e["id"]: e for e in rows
            if e.get("payload") == "3d" and e.get("id")}


async def _refuse_if_disk_cannot_hold_meshes(count: int) -> None:
    """E6 on the 3D lane: a run the staging volume can't hold is refused in
    words before a turn exists. Meshes are sized in tens of megabytes, so
    this uses an authored per-mesh figure rather than the image lane's
    bytes-per-pixel reading (there are no pixels to read)."""
    from .api_v4 import _disk_free, _mb
    needed = count * MESH_ASSUMED_BYTES
    free = _disk_free()
    if free < needed + config.DISK_MARGIN_BYTES:
        short = needed + config.DISK_MARGIN_BYTES - free
        raise HTTPException(
            422, f"{count} mesh{'es' if count != 1 else ''} need up to "
                 f"about {_mb(needed)} and the cockpit's drive has "
                 f"{_mb(free)} free — free up about {_mb(short)} first; "
                 "nothing was started")


@router.post("/workspaces/{wid}/mesh-turns", response_model=RunResult,
             status_code=201)
async def run_mesh_turn(wid: str, body: MeshRun):
    """One 3D run = one turn: one source take, every stepped engine that
    serves this verb beside it, takes within a batch in order (the
    per-engine count, capped lower here because a mesh is minutes not
    seconds — and capped at one for a deterministic verb, which has no second
    answer to give)."""
    from .api_v4 import (SourceError, _resolve_parents, _stack, _turn_view)
    stack = _stack()
    db, sched = stack.db, stack.scheduler
    ws = await db.get_workspace(wid)
    if ws is None:
        raise HTTPException(404, "no such workspace")

    kind = body.kind
    if kind not in MESH_KINDS:
        raise HTTPException(
            422, f"'{kind}' is not a 3D verb — this lane serves "
                 f"{', '.join(sorted(MESH_KINDS))}")
    takes_a_mesh = kind in MESH_INPUT_KINDS

    total = sum(b.takes for b in body.mesh)
    if total > 8:
        raise HTTPException(422, f"a mesh run caps at 8 takes — this one "
                            f"asks for {total}; each one is minutes of GPU "
                            "time, taken one at a time")
    if kind not in SEEDED_KINDS and total > len(body.mesh):
        # not a cap for cost's sake: the same input through the same
        # deterministic tool is the same mesh, so the extra takes would be
        # copies wearing the word "take": never claim variety that
        # isn't there
        raise HTTPException(
            422, f"{kind} is deterministic — the same mesh and the same "
                 "handles give the same result every time, so more than one "
                 "take of it would be copies; ask for one")
    try:
        parents = await _resolve_parents(db, wid, body.parents,
                                         payload="3d" if takes_a_mesh
                                         else "image")
    except SourceError as e:
        raise HTTPException(422, str(e))
    # _resolve_parents already refuses a purged, foreign, missing-from-staging
    # or wrong-payload source in its own words — including "that node is a 3d
    # payload", which is the no-mesh-from-an-image refusal, and its mirror for
    # a verb pointed at a picture (one home per fact, and that one is
    # the run route's)
    node = parents[0]

    # The second parent. A kind that paints needs its reference image
    # named; a kind that doesn't refuses one rather than dropping it — a
    # request field that silently does nothing is a lie in the other
    # direction. The reference resolves through the same four source checks
    # the mesh did, as an image.
    ref_node = None
    if kind in MESH_REF_KINDS:
        if not body.reference:
            raise HTTPException(
                422, f"{kind} paints this mesh from a reference image — "
                     "name one as `reference` (the appearance parent; the "
                     "mesh stays the shape)")
        try:
            ref_node = (await _resolve_parents(db, wid, [body.reference],
                                               payload="image"))[0]
        except SourceError as e:
            raise HTTPException(422, str(e))
    elif body.reference:
        raise HTTPException(
            422, f"{kind} takes no reference image — that field belongs to "
                 "the texture verb")

    matrix = _matrix_engines()
    serving = MESH_KINDS[kind]
    seen: set[str] = set()
    for batch in body.mesh:
        eid = batch.engine_id
        if eid in seen:
            raise HTTPException(422, f"engine '{eid}' is listed twice — one "
                                "row per engine, its takes on that row")
        seen.add(eid)
        if eid not in MESH_ENGINES:
            raise HTTPException(
                422, f"'{eid}' is not a mesh engine — the 3D lane serves "
                     f"{', '.join(sorted(MESH_ENGINES))}")
        if eid not in serving:
            # law 16: engines declare the verbs they serve, and a pairing that
            # was never declared is refused here rather than at the runner's
            # door, after a turn already exists
            raise HTTPException(
                422, f"'{eid}' does not do {kind} — the engines that serve "
                     f"that verb are {', '.join(sorted(serving))}")
        if eid not in matrix:
            # the matrix is the roster; an engine it doesn't publish is not
            # offerable, and saying so beats running something unlisted
            raise HTTPException(
                422, f"the capability matrix has no record of '{eid}' — it "
                     "can't be offered until configs/engines.json publishes "
                     "it")
        if (mesh_kind_input(kind, eid) == "fields"
                and not fields_sidecar(node["output_path"]).is_file()):
            # Re-extraction needs the parent's voxel field, and only a
            # TRELLIS.2 take that kept one has it. The roster already dashes
            # this row (law 12), so this is the server re-check — and it names
            # the engine that CAN serve the mesh, which is the actionable
            # half of the refusal.
            raise HTTPException(
                422, f"'{matrix[eid]['name']}' re-extracts from the voxel "
                     "field a take kept, and this mesh has none — decimate "
                     "it with gltfpack instead, or generate it again to keep "
                     "one")

    policy = presets_mod.resolve_seed_policy(body.seed)
    if policy["mode"] == "locked" and not isinstance(policy.get("value"),
                                                     int):
        raise HTTPException(422, "a locked seed needs its value — unlock it "
                            "for a fresh roll per take")

    await _refuse_if_disk_cannot_hold_meshes(total)

    source_path = node["output_path"]
    mesh_ref = parent_runner_job(node) if takes_a_mesh else None
    ref_path = ref_node["output_path"] if ref_node else None
    # the child's parents, in meaning order: the shape first, then the
    # appearance; node_parents keeps the ordinals, and the canvas
    # draws an edge from each
    parent_ids = [node["id"]] + ([ref_node["id"]] if ref_node else [])
    lineage = {"capability": "to_3d", "kind": kind, "parents": parent_ids,
               "source_node_id": node["id"]}
    engines: dict[str, dict] = {}
    specs: list[dict] = []
    for batch in body.mesh:
        applied = {**lineage, "engine_id": batch.engine_id,
                   "params": dict(batch.params)}
        engines[batch.engine_id] = applied
        for _ in range(batch.takes):
            pos = len(specs)
            # a verb that reads no seed records none, rather than a number
            # the ledger would later imply had an effect
            seed = (presets_mod.take_seed(policy, pos)
                    if kind in SEEDED_KINDS else None)
            specs.append({
                "engine": batch.engine_id,
                "dials": {**applied, "seed_value": seed},
                "seed": seed,
                "submitted_graph": mesh_envelope(
                    batch.engine_id, source_path, kind=kind, seed=seed,
                    params=batch.params, mesh_job_ref=mesh_ref,
                    ref_path=ref_path),
                "external": None, "committed_usd": None})

    anchor = (body.anchor.x, body.anchor.y) if body.anchor else None
    # the source's own brief rides the turn so the mesh reads as what it
    # came from (Refine's carry, applied here); nothing generates from it.
    # For a texture take the appearance is the reference's, so ITS brief is
    # the one that describes the child; the mesh's is the fallback for an
    # imported or promptless reference.
    prompt = ((ref_node.get("prompt") if ref_node else None)
              or node.get("prompt"))
    turn_dials = {"seed": policy, **lineage, "engines": engines}
    turn = await db.create_turn_with_slots(wid, prompt, turn_dials, specs,
                                           anchor=anchor)
    requests = [DispatchRequest(
        slot_id=s["id"], engine=s["engine"],
        submitted_graph=specs[s["pos"]]["submitted_graph"],
        external=None, committed_usd=None, frame=None,
        payload="3d", verb=mesh_verb(kind), prompt=prompt,
        parents=tuple(parent_ids), seed=s["seed"])
        for s in turn["slots"]]
    await sched.submit_turn(turn["id"], requests)
    await db.touch_workspace(wid)
    fresh = await db.get_turn(turn["id"])
    return {"turn": _turn_view(fresh), "seq": await db.latest_seq()}


@router.get("/mesh/health", response_model=MeshHealth)
async def mesh_health():
    """Is the 3D lane's door open? The composer's roster reads this to say
    why a mesh row is dashed (law 12: badge, never filter — an unreachable
    runner is stated, not hidden)."""
    from .wiring import probe_mesh_runner
    up = await probe_mesh_runner()
    return {"ok": up, "url": config.THREED_URL,
            "engines": sorted(MESH_ENGINES),
            "kinds": {k: sorted(v) for k, v in MESH_KINDS.items()},
            "why": None if up else
            ("the 3D engine runner isn't answering — start it with "
             "`systemctl --user start 3d-runner`")}
