"""Manifest writer: the manifest is a sidecar JSON wrapping
the EXACT graph that was submitted for that take, plus provenance
extras. Re-run-from-manifest (later) resubmits `submitted_graph`
verbatim — no parameter reconstruction by hand.

Model files are identified by name + size + sha256; hashes are computed once
per file and cached in SQLite (an 8 GB checkpoint hashes in ~15 s the first
time, then it is free).
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .db import Database

# loader class -> [(input key, models subdir, role), ...]
LOADER_ROLES = {
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoints", "checkpoint")],
    "UNETLoader": [("unet_name", "diffusion_models", "diffusion_model")],
    "CLIPLoader": [("clip_name", "text_encoders", "text_encoder")],
    "VAELoader": [("vae_name", "vae", "vae")],
    "LoraLoader": [("lora_name", "loras", "lora")],
    "INPAINT_LoadFooocusInpaint": [("head", "inpaint", "inpaint_head"),
                                   ("patch", "inpaint", "inpaint_patch")],
    "INPAINT_LoadInpaintModel": [("model_name", "inpaint", "removal_model")],
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


async def model_files_meta(db: Database, *graphs: dict) -> list[dict]:
    files: list[dict] = []
    seen: set[str] = set()
    for graph in graphs:
        for node in graph.values():
            for key, sub, role_name in LOADER_ROLES.get(node.get("class_type"), []):
                name = node.get("inputs", {}).get(key)
                if not isinstance(name, str) or name in seen:
                    continue
                seen.add(name)
                path = config.MODELS_ROOT / sub / name
                meta: dict = {"role": role_name, "name": name}
                if path.is_file():
                    st = path.stat()
                    meta["size"] = st.st_size
                    cached = await db.cached_hash(str(path), st.st_size, st.st_mtime)
                    if cached is None:
                        cached = await asyncio.to_thread(_sha256_file, path)
                        await db.store_hash(str(path), st.st_size, st.st_mtime, cached)
                    meta["sha256"] = cached
                else:
                    meta["note"] = "file not visible from cockpit at accept time"
                files.append(meta)
    return files


def extract_loras(graph: dict) -> list[dict]:
    out = []
    for node in graph.values():
        if node.get("class_type") == "LoraLoader":
            ins = node.get("inputs", {})
            out.append({"name": ins.get("lora_name"),
                        "strength_model": ins.get("strength_model"),
                        "strength_clip": ins.get("strength_clip")})
    return out


# --- the manifest ---------------------------------------------------------
#
# The product model is workspaces/turns/slots/nodes, so the manifest's
# field list is mapped onto it here. This is manifest version 4.
#
# What the fields carry, and why:
#   - `workspace` names the asset's workspace (promotes per workspace slug)
#   - `parent_node_ids` is a LIST in node_parents' ordinal order (plural
#     parents)
#   - `cost` carries BOTH committed and charged (they are separate
#     values in the model, so they are separate values in the record)
#   - `replayable` states the tier on the asset itself: an external take
#     is a record, not a recipe, because providers move weights behind
#     stable version names
# Dormant: `loras` and `carry_channels` wait for their features (LoRA use
# at generation time, attribute carry channels).

MANIFEST_VERSION = 4

# The promoted orbit's name, beside the mesh rather than under `.inputs/`.
# One constant because three places say it: the manifest's own
# `mesh.turntable`, the promotion's extra file, and the preview's row.
TURNTABLE_SUFFIX = ".turntable.webp"

REPLAY_LOCAL = ("exact: the submitted graph is verbatim and every model "
                "file is pinned by sha256")
REPLAY_EXTERNAL = ("record only, not replayable: the request is exact, but "
                   "the provider can change the weights behind this model "
                   "name at any time")
# A mesh is a third tier, not either of the two above. The engine,
# its seed and its arguments are verbatim and the GLB itself is pinned, but
# the mesh engines live host-side in their own venvs with weights
# outside `~/models`, so nothing hashes them the way a ComfyUI graph's
# loaders are hashed. Claiming REPLAY_LOCAL here would put a sentence about
# pinned model files on a manifest whose `model.files` is empty (law 12:
# unverified is shown as unverified, never guessed).
REPLAY_MESH = ("close: the engine, its seed and its arguments are verbatim "
               "and the mesh file is pinned by sha256, but the engine's own "
               "weights are not hashed — it runs outside the cockpit")


def _png_resolution(path: Path) -> dict:
    """The resolution comes from the PROMOTED FILE, never from the
    inputs: region and img2img takes carry no width/height (the size lives
    in the region plan or the source), and an external take's size is
    whatever the provider chose."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return {"width": im.size[0], "height": im.size[1]}
    except Exception:
        return {"width": None, "height": None}


def _mesh_blob(node: dict) -> dict | None:
    """The fields for a payload that has none of them.

    A mesh has no resolution, no sampler, no prompt and no model files, and
    the facts it DOES have are already on the node — `funnel.py` measured
    them and the mesh executor parked the whole as-run record in
    `submitted_graph.mesh_job`. So this projects them up to the manifest's
    top level rather than leaving a promoted asset whose only mesh facts
    are buried in a graph envelope no downstream reader knows to open.

    Wider than `api_v4._mesh_view`, and deliberately: that projection is
    what a FACE may see (six measured facts plus two ids), this is what the
    promoted tree must carry to stand alone in the promote folder: the
    cutout method, the
    engine's own arguments and the GLB's sha256 included. Every value is
    the runner's own measurement; a fact the record doesn't carry stays
    null rather than defaulting to a claim (law 12).
    """
    job = ((node.get("submitted_graph") or {}).get("mesh_job") or {})
    if not job:
        return None
    v = job.get("validation") or {}
    wt = v.get("watertight")
    return {
        "engine": job.get("engine"),
        # which 3D verb made this artefact (law 16): a promoted mesh
        # that was decimated says so on the drive, not only in the cockpit.
        # A record without a kind IS a generate.
        "kind": job.get("kind") or "generate",
        "runner_job_id": job.get("runner_job_id"),
        "runner_version": job.get("runner_version"),
        "seed": job.get("seed"),
        "params": job.get("params") or {},
        "engine_args": job.get("engine_args") or [],
        "cutout": job.get("cutout"),
        "glb_sha256": job.get("glb_sha256"),
        "engine_wall_s": job.get("engine_wall_s"),
        # the funnel's own validation, flattened — `watertight` arrives as
        # one bool per geometry and an empty list claims nothing
        "triangles": v.get("triangles"),
        "vertices": v.get("vertices"),
        "watertight": all(wt) if isinstance(wt, list) and wt else None,
        "textured": v.get("texture_present"),
        "file_mb": v.get("file_mb"),
    }


def _sampler_blob(dials: dict) -> dict:
    return {k: dials[k] for k in
            ("steps", "guidance", "cfg", "sampler_name", "scheduler",
             "denoise")
            if k in dials}


async def build_v4_manifest(db, *, asset_id: str, workspace: dict,
                            node: dict, turn: dict | None, slot: dict | None,
                            parents: list[dict],
                            turntable: Path | None = None
                            ) -> tuple[dict, list[tuple[Path, str]]]:
    """Returns (manifest, extra_files). extra_files are (source path, path
    relative to the asset's folder) pairs, promoted under
    `<asset_id>.inputs/` so the asset is reproducible from the promoted
    tree alone.

    `turntable` is the cached orbit for a mesh, when the route could
    resolve one. It rides at the TOP level rather than under
    `.inputs/`, because it is not an input the asset was made from — it is
    the only way to see what the GLB is without a 3D viewer. The route
    decides whether an orbit exists; the layout and the manifest's word
    for it are decided here, so the file's name has one home."""
    dials = node.get("dials") or {}
    turn_dials = (turn or {}).get("dials") or {}
    external = node.get("external")
    graph = node.get("submitted_graph")
    region = turn_dials.get("region")
    is_mesh = node["payload"] == "3d"
    mesh = _mesh_blob(node) if is_mesh else None
    extra_files: list[tuple[Path, str]] = []

    if mesh is not None:
        # null when no orbit rode along — a mesh whose turntable was never
        # rendered or whose runner was down still promotes, and the record
        # says which of the two happened by not naming a file (law 12)
        mesh["turntable"] = None
        if turntable is not None and turntable.is_file():
            rel = f"{asset_id}{TURNTABLE_SUFFIX}"
            extra_files.append((turntable, rel))
            mesh["turntable"] = rel

    # A region take's `submitted_graph` is the {region_plan} envelope,
    # not a ComfyUI graph: the passes carry the graphs. Hash the
    # models out of every pass so the record pins what actually ran. A
    # mesh's is the {mesh_job} envelope and carries no graph at
    # all — it is said here rather than left to the loader walk finding
    # nothing, because "no model files" is a fact about the 3D lane, not
    # an empty result (law 12, and `replayable` below says the same).
    passes = ((graph or {}).get("region_plan") or {}).get("passes") or [] \
        if isinstance(graph, dict) and "region_plan" in graph else []
    if passes:
        graphs = [p["graph"] for p in passes if p.get("graph")]
    elif external or is_mesh or not graph:
        graphs = []
    else:
        graphs = [graph]

    input_images: list[dict] = []
    for i, parent in enumerate(parents):
        rel = f"{asset_id}.inputs/source.png" if i == 0 \
            else f"{asset_id}.inputs/source-{i + 1}.png"
        src = Path(parent["output_path"])
        if src.is_file():
            extra_files.append((src, rel))
        input_images.append({
            "node_id": parent["id"],
            "sha256": dials.get("source_image_sha256") if i == 0 else None,
            "file": rel,
        })

    regions: list[dict] = []
    if region and region.get("region_ids"):
        for r in await db.regions_by_ids(list(region["region_ids"])):
            rel = f"{asset_id}.inputs/region-{r['id']}.png"
            extra_files.append((Path(r["mask_path"]), rel))
            regions.append({
                "id": r["id"], "name": r["name"], "action": r["action"],
                "prompt": r["prompt"], "z_order": r["z_order"],
                "mask_sha256": r["mask_sha256"], "mask_file": rel,
            })

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "asset_id": asset_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": {"id": workspace["id"], "slug": workspace["slug"],
                      "name": workspace["name"]},
        "node_id": node["id"],
        "turn": None if turn is None else {"id": turn["id"],
                                           "idx": turn["idx"]},
        "slot": None if slot is None else {"id": slot["id"],
                                           "pos": slot["pos"]},
        "take": node.get("take"),
        "payload": node["payload"],
        "verb": node["verb"],
        "capability": dials.get("capability", "txt2img"),
        "parent_node_ids": list(node.get("parents") or []),
        "landed_at": node["created_at"],
        "engine": node.get("engine"),
        "transport": "openrouter" if external else "local",
        "provider": None if external else "comfyui",
        # Two different facts, both the manifest's: what this ASSET is (it involved
        # no external call), and whether the artist's LOCAL ONLY switch was
        # down for the run that made it (the claim is recorded on the
        # turn; the switch being off doesn't make an asset non-local).
        "local_only": external is None,
        "run_local_only": bool(turn_dials.get("local_only")),
        "replayable": (REPLAY_EXTERNAL if external
                       else REPLAY_MESH if is_mesh else REPLAY_LOCAL),
        "model": {
            "family": (external or {}).get("model") if external
            else node.get("engine"),
            "files": await model_files_meta(db, *graphs),
        },
        "loras": [] if external else extract_loras(graphs[-1] if graphs
                                                   else {}),
        "carry_channels": [],
        "style_ref": None,
        "prompts": {
            "positive": node.get("prompt") if external
            else dials.get("prompt", node.get("prompt")),
            "negative": None if external else dials.get("negative_prompt"),
        },
        # What was ACTUALLY sent (no silent lies): an external model
        # that takes no seed records None, whatever the turn's policy said.
        "seed": external.get("seed") if external else node.get("seed"),
        "sampler": {} if external else _sampler_blob(dials),
        "input_images": input_images,
        "regions": regions,
        "region_policy": (region or {}).get("policy"),
        "keep_composite": ({"mask_sha256": region["keep_mask_sha256"],
                            "note": "server-side verbatim restore of KEEP "
                                    "pixels after the final pass (nd3-v1)"}
                           if region and region.get("keep_mask_sha256")
                           else None),
        # An image's size comes from the promoted file; a mesh has no
        # resolution to read, and asking PIL for one would answer
        # {null, null} through a swallowed exception — the same shape a
        # corrupt PNG produces. Stated, not inferred: the mesh block below
        # carries the measurements a mesh actually has.
        "resolution": (None if is_mesh
                       else _png_resolution(Path(node["output_path"]))),
        "mesh": mesh,
        # committed is what the run promised, charged is what the
        # provider said it took. Both, always — a divergence is a fact
        # about this asset, not a rounding error to hide.
        "cost": {"committed_usd": node.get("committed_usd"),
                 "charged_usd": node.get("charged_usd")},
        "duration_ms": node.get("duration_ms"),
        "comfyui": None if external else {"job_ref": (slot or {}).get("job_ref")},
        "external": external,
        "pipeline": ([{k: p.get(k) for k in
                       ("idx", "kind", "capability", "workflow", "region_ids",
                        "prompt", "box", "uses_denoise", "mask_sha256",
                        "graph")}
                      for p in passes] if passes else None),
        "submitted_graph": None if passes else graph,
        "region_plan": graph if passes else None,
    }
    return manifest, extra_files
