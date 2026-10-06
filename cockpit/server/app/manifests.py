"""Write additive manifest v4 provenance and the existing delivery file list.

Submitted graphs, frozen shared requests and sequence decisions belong to the
exported take, never the current draft or slot attempt. Missing historical
evidence stays absent; imported material makes no claim about its creation.
The versioned shared_request extension retains the accepted preparation and
matching operation snapshot, with actual attempt controls identified separately.
Sequence authorization comes from the last matching event before acceptance,
never a sequence's mutable current authorization. This is an asset record, not
a project backup or permission to submit work. An audio take's manifest carries
its measured format, length and loudness, the words it speaks, the voice that
speaks them and its provider's terms on its use (law 20); every other
manifest's `audio` is null.

Model files are identified by name + size + sha256; hashes are computed once
per file and cached in SQLite (an 8 GB checkpoint hashes in ~15 s the first
time, then it is free).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from . import audio, config
from .execution_outputs import inventory
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
#   - `audio` describes an audio take for an engine importer and a mixer;
#     its `transport` and `provider` name the provider that made it
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
REPLAY_IMPORTED = "record only: original material is retained; its creation was not observed"
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
# A local voice take is the same third tier: the runner's request and seed are
# verbatim and the take is pinned, but its weights live in the runner's own
# environment, where nothing hashes them.
REPLAY_LOCAL_AUDIO = ("close: the engine, its seed and its request are verbatim "
                      "and the take is pinned by sha256, but the voice runner's "
                      "own weights are not hashed; it runs outside the cockpit")

# The audio facts a manifest repeats from the take's measurement and its
# provider's report. Peaks and timing alignment stay in the project.
AUDIO_FACTS = ("container", "codec", "sample_rate", "channels", "duration_s",
               "loudness_lufs", "peak_dbfs", "text", "voice")


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


async def _audio_blob(db, node: dict, material: dict | None) -> dict:
    """What an audio take is, for an engine importer and a mixer: its container,
    codec, sample rate, channels, length, measured loudness (ITU-R BS.1770
    integrated, LUFS) and peak, the words it speaks (for a voice design, its
    preview line), the voice that speaks them as the provider reported it, and the
    provider's terms on its use, such as ElevenLabs' rule that its audio is never
    input to another AI model. A fact the record does not carry stays null
    (law 12)."""
    facts = await db.audio_fact(node["id"]) or {}
    terms = ((material or {}).get("origin") or {}).get("terms")
    return {key: facts.get(key) for key in AUDIO_FACTS} | {"terms": terms}


def _sampler_blob(dials: dict) -> dict:
    return {k: dials[k] for k in
            ("steps", "guidance", "cfg", "sampler_name", "scheduler",
             "denoise")
            if k in dials}


async def _shared_request(db, node: dict, turn: dict | None) -> dict | None:
    """Project only this take's immutable acceptance and original node controls."""
    frozen = ((turn or {}).get('dials') or {}).get('crexil')
    if not frozen:
        return None
    dials = node.get('dials') or {}
    attempt = dials.get('crexil') or {}
    operation_id = attempt.get('operation_id') or node.get('engine')
    operation = next((op for op in frozen.get('operations', [])
                      if op.get('id') == operation_id
                      and op.get('revision') == attempt.get('operation_revision')), None)
    result = {key: copy.deepcopy(frozen[key]) for key in (
        'direction_id', 'revision', 'setup', 'project_revision', 'brief',
        'project_instructions', 'project_limits', 'sources', 'review_token',
        'actor', 'request_id', 'maximum_usd', 'sequence') if key in frozen}
    result.update(version=1, accepted_at=turn['created_at'], operation=copy.deepcopy(operation),
                  attempt={'slot_id': node.get('slot_id'), 'take': node.get('take'),
                           'operation_id': operation_id, 'seed': node.get('seed'),
                           'dials': copy.deepcopy(dials)})
    if result.get('sequence'):
        # Event order, unlike wall-clock timestamps, distinguishes authorization
        # changes and acceptance even when both happen in the same millisecond.
        event = await db._one(
            "SELECT seq,ts,payload_json FROM events WHERE workspace_id=? "
            "AND type='sequence.authorized' AND json_extract(payload_json,'$.sequence_id')=? "
            "AND seq < (SELECT MIN(seq) FROM events WHERE workspace_id=? AND turn_id=? "
            "AND type='turn.created') ORDER BY seq DESC LIMIT 1",
            (node['workspace_id'], result['sequence']['id'], node['workspace_id'], turn['id']))
        result['sequence_authorization'] = (
            {'event_seq': event['seq'], 'accepted_at': event['ts'], **json.loads(event['payload_json'])}
            if event else None)
    return result


def _represents_mesh_file(receipt: dict, path: Path, suffix: str) -> bool:
    """Missing receipts still identify their standard companion by filename."""
    stored = receipt.get('path')
    return bool((stored and Path(stored).resolve() == path.resolve())
                or receipt.get('filename', '').endswith(suffix)
                or receipt.get('filename') == suffix.lstrip('.')
                or receipt.get('identity') == 'companion:' + suffix)


async def _legacy_mesh_files(db, node: dict, asset_id: str, material: dict | None,
                             execution: dict | None, mesh: dict | None,
                             turntable: Path | None) -> tuple[dict | None, list[tuple[Path, str]]]:
    """Expose owned legacy sidecars without replacing any recorded receipt.

    These are present file facts only. They do not create material records in
    the database, invent execution, or repair a missing or corrupt companion.
    """
    from .material_files import files_from_origin
    from .mesh import FIELDS_SUFFIX, fields_sidecar

    if node['payload'] != '3d' or node.get('purged_at'):
        return material, []
    receipts = []
    if material:
        public = {item['id']: item for item in material['related_files']}
        receipts.extend({**item, **public[item['id']]} for item in
                        files_from_origin(node, material['origin']))
    if execution:
        paths = {row['id']: row['output_path'] for row in await db._all(
            'SELECT id,output_path FROM execution_outputs WHERE group_id=?', (execution['id'],))}
        receipts.extend({**item, 'path': paths[item['id']]} for item in execution['outputs'])
    primary = Path(node['output_path'])
    orbit = turntable if turntable is not None else primary.with_suffix(TURNTABLE_SUFFIX)
    extras = []
    for source, suffix, identity, role, media, required in (
        (fields_sidecar(primary), FIELDS_SUFFIX, 'legacy-fields', 'companion', 'application/octet-stream', True),
        (orbit, TURNTABLE_SUFFIX, 'legacy-turntable', 'preview', 'image/webp', False),
    ):
        declared = [item for item in receipts if _represents_mesh_file(item, source, suffix)]
        if declared:
            if suffix == TURNTABLE_SUFFIX and mesh is not None:
                mesh['turntable'] = next((item['file'] for item in declared
                    if item.get('available') and not item.get('error')), None)
            continue
        if not source.is_file():
            continue
        if not source.resolve().is_relative_to(primary.parent.resolve()):
            raise ValueError('A mesh companion would leave its material directory.')
        if suffix == TURNTABLE_SUFFIX and mesh is not None:
            # Preserve the established generated-mesh orbit layout.
            mesh['turntable'] = f'{asset_id}{TURNTABLE_SUFFIX}'
            extras.append((source, mesh['turntable']))
            continue
        if material is None:
            material = {'sha256': await asyncio.to_thread(_sha256_file, primary),
                        'filename': primary.name,
                        'media_type': mimetypes.guess_type(primary.name)[0] or 'application/octet-stream',
                        'origin': {'kind': 'unattributed'}, 'related_files_version': 1, 'related_files': []}
        ids = {item['id'] for item in material['related_files']}
        base, counter = identity, 1
        while identity in ids:
            counter += 1
            identity = f'{base}-{counter}'
        relative = f'{asset_id}.outputs/{identity}{source.suffix}'
        material['related_files'].append({'id': identity, 'filename': source.name,
            'role': role, 'media_type': media, 'required': required,
            'sha256': await asyncio.to_thread(_sha256_file, source), 'size_bytes': source.stat().st_size,
            'error': None, 'available': True, 'file': relative})
        extras.append((source, relative))
    return material, extras


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
    # Callers may hold a newer run or slot. The node's immutable ownership is
    # the only authority for historical request context.
    turn = await db.get_turn(node['turn_id']) if node.get('turn_id') else None
    slot = await db.get_slot(node['slot_id']) if node.get('slot_id') else None
    dials = node.get("dials") or {}
    turn_dials = (turn or {}).get("dials") or {}
    external = node.get("external")
    graph = node.get("submitted_graph")
    restoration = copy.deepcopy(dials.get('crexil_restore'))
    if restoration:
        from .crexil_restore import context
        restored = await context(db, workspace['id'])
        relevant = {node['id'], *(node.get('parents') or [])}
        restoration['node_identities'] = {old: current for old, current in
            (restored or {}).get('identities', {}).get('nodes', {}).items() if current in relevant}
        restoration['model_hashes'] = 'Original installation model files are not available for verification here.'
    region_record = (graph or {}).get('region_plan') or {}
    region = turn_dials.get("region")
    is_mesh = node["payload"] == "3d"
    mesh = _mesh_blob(node) if is_mesh else None
    extra_files: list[tuple[Path, str]] = []

    if mesh is not None:
        # null when no orbit rode along — a mesh whose turntable was never
        # rendered or whose runner was down still promotes, and the record
        # says which of the two happened by not naming a file (law 12)
        mesh["turntable"] = None

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
        src = Path(parent["output_path"])
        suffix = src.suffix or '.bin'
        rel = f"{asset_id}.inputs/source{suffix}" if i == 0 \
            else f"{asset_id}.inputs/source-{i + 1}{suffix}"
        available = not parent.get('purged_at') and src.is_file()
        if available:
            extra_files.append((src, rel))
        source_info = await db._one('SELECT sha256 FROM material_info WHERE node_id=?', (parent['id'],))
        source_hash = (source_info['sha256'] if source_info else
                       await asyncio.to_thread(_sha256_file, src) if available else None)
        input_images.append({
            "node_id": parent["id"], "sha256": source_hash,
            "file": rel if available else None, "available": available,
        })

    regions: list[dict] = []
    if region and region.get("region_ids"):
        from .crexil_restore import resolve
        region_ids = [await resolve(db, workspace["id"], "regions", rid) for rid in region["region_ids"]]
        for r in await db.regions_by_ids(region_ids):
            rel = f"{asset_id}.inputs/region-{r['id']}.png"
            extra_files.append((Path(r["mask_path"]), rel))
            regions.append({
                "id": r["id"], "name": r["name"], "action": r["action"],
                "prompt": r["prompt"], "z_order": r["z_order"],
                "mask_sha256": r["mask_sha256"], "mask_file": rel,
            })

    # Runtime identity and completion belong to the attempt, not the mutable
    # current slot. A reroll must never rewrite an older output's provenance.
    execution = next((g for g in await inventory(db, workspace['id'])
                      if g['slot_id'] == node.get('slot_id') and g['take'] == node.get('take')), None)
    runtime_inputs = []
    if execution:
        for output in execution['outputs']:
            output['file'] = None
            if output['available']:
                if output['node_id'] == node['id']:
                    output['file'] = f"{asset_id}{Path(node['output_path']).suffix or '.png'}"
                else:
                    raw = await db._one('SELECT output_path FROM execution_outputs WHERE id=?', (output['id'],))
                    source = Path(raw['output_path'])
                    folder = 'inputs' if output['role'] == 'runtime input' else 'outputs'
                    output['file'] = f"{asset_id}.{folder}/{output['id']}{source.suffix}"
                    extra_files.append((source, output['file']))
            if output['role'] == 'runtime input':
                runtime_inputs.append({key: output[key] for key in
                    ('identity', 'sha256', 'file', 'available', 'size_bytes')} | {
                    'description': {key: value for key, value in output['descriptor'].items() if key != 'receipt'}})
    material = await db._one('SELECT * FROM material_info WHERE node_id=?', (node['id'],))
    material_record = ({'sha256': material['sha256'], 'filename': material['filename'],
                        'media_type': material['media_type'], 'origin': json.loads(material['origin_json'])}
                       if material else None)
    if material_record is not None:
        from .material_files import export_files
        related, retained = await export_files(db, node, asset_id)
        material_record.update(related_files_version=1, related_files=related)
        extra_files.extend(retained)
    material_record, legacy_files = await _legacy_mesh_files(
        db, node, asset_id, material_record, execution, mesh, turntable)
    extra_files.extend(legacy_files)
    if material_record is not None:
        # Live ownership paths are replaced by portable receipts. Nested source
        # records are historical evidence and retain their original contents.
        material_record['origin'].pop('retained_files', None)
    prepared_image = bool(material_record and material_record['origin'].get('kind') == 'prepared_image')
    unknown_creation = node.get('verb') == 'import' and not prepared_image
    # An audio take names the provider that made it from its own request: the
    # voice runner is a local engine, ElevenLabs a hosted one. The request a
    # provider reports keeps the body it sent, seed included.
    is_audio = node["payload"] == audio.PAYLOAD
    audio_provider = (external or {}).get("provider") if is_audio else None
    local_audio = audio.PROVIDERS.get(audio_provider) == "local"
    audio_seed = ((external or {}).get("seed") if "seed" in (external or {})
                  else (((external or {}).get("request") or {}).get("body") or {}).get("seed"))
    keep_hash = region_record.get('keep_mask_sha256') or (region or {}).get('keep_mask_sha256')
    keep_input = next((item for item in runtime_inputs
                       if item['identity'] == (region_record.get('keep') or {}).get('identity')), None)
    keep_scope = 'each completed pass' if region_record.get('keep_scope') == 'each_completed_pass' else 'the final pass'

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
        "capability": None if unknown_creation else dials.get("capability", "txt2img"),
        "parent_node_ids": list(node.get("parents") or []),
        "landed_at": node["created_at"],
        "restoration": restoration,
        "engine": node.get("engine"),
        "transport": (None if unknown_creation
                      else ("local" if local_audio else audio_provider) if is_audio
                      else "openrouter" if external else "local"),
        "provider": (None if unknown_creation else audio_provider if is_audio
                     else None if external or is_mesh or not graph else "comfyui"),
        # Two different facts, both the manifest's: what this ASSET is (it involved
        # no external call), and whether the artist's LOCAL ONLY switch was
        # down for the run that made it (the claim is recorded on the
        # turn; the switch being off doesn't make an asset non-local).
        "local_only": None if unknown_creation else local_audio if is_audio else external is None,
        "run_local_only": None if unknown_creation else bool(turn_dials.get("local_only")),
        "replayable": (REPLAY_IMPORTED if unknown_creation
                       else (REPLAY_LOCAL_AUDIO if local_audio else REPLAY_EXTERNAL) if is_audio
                       else REPLAY_EXTERNAL if external else REPLAY_MESH if is_mesh
                       else 'recipe: the recorded pixel transform, renderer version and source hashes describe this prepared image'
                       if prepared_image else REPLAY_IMPORTED
                       if not graph else REPLAY_LOCAL),
        "model": {
            "family": (external or {}).get("model_id") if is_audio
            else (external or {}).get("model") if external
            else node.get("engine"),
            "files": [] if dials.get("crexil_restore") else await model_files_meta(db, *graphs),
        },
        "loras": [] if external else extract_loras(graphs[-1] if graphs
                                                   else {}),
        "carry_channels": [],
        "style_ref": None,
        "prompts": {
            "positive": None if unknown_creation else node.get("prompt") if external
            else dials.get("prompt", node.get("prompt")),
            "negative": None if external else dials.get("negative_prompt"),
        },
        # What was ACTUALLY sent (no silent lies): an external model
        # that takes no seed records None, whatever the turn's policy said.
        "seed": audio_seed if is_audio and external else external.get("seed") if external else node.get("seed"),
        "sampler": {} if external else _sampler_blob(dials),
        "input_images": input_images,
        "runtime_inputs": runtime_inputs,
        "regions": regions,
        "region_policy": (region or {}).get("policy"),
        "keep_composite": ({"mask_sha256": keep_hash,
                            "mask_file": keep_input['file'] if keep_input else None,
                            "note": "server-side verbatim restore of KEEP "
                                    f"pixels after {keep_scope} (nd3-v1)"}
                           if keep_hash
                           else None),
        # An image's size comes from the promoted file; a mesh has no
        # resolution to read, and asking PIL for one would answer
        # {null, null} through a swallowed exception — the same shape a
        # corrupt PNG produces. Stated, not inferred: the mesh block below
        # carries the measurements a mesh actually has. Audio has none either:
        # its block says what it is.
        "resolution": (None if is_mesh or is_audio
                       else _png_resolution(Path(node["output_path"]))),
        "mesh": mesh,
        "audio": await _audio_blob(db, node, material_record) if is_audio else None,
        # committed is what the run promised, charged is what the
        # provider said it took. Both, always — a divergence is a fact
        # about this asset, not a rounding error to hide.
        "cost": {"committed_usd": node.get("committed_usd"),
                 "charged_usd": node.get("charged_usd")},
        "duration_ms": node.get("duration_ms"),
        "comfyui": None if external or is_mesh or unknown_creation or prepared_image else {"job_ref": execution["job_ref"] if execution else
            (slot or {}).get("job_ref") if (slot or {}).get("attempt") == node.get("take") else None},
        "execution": execution,
        "material": material_record,
        "shared_request": await _shared_request(db, node, turn),
        "external": external,
        "pipeline": ([{k: p.get(k) for k in
                       ("idx", "kind", "capability", "workflow", "region_ids",
                        "prompt", "box", "uses_denoise", "mask_sha256", "applied_controls",
                        "source_input", "mask_input", "effective_mask_input",
                        "graph")}
                      for p in passes] if passes else None),
        "submitted_graph": None if passes else graph,
        "region_plan": graph if passes else None,
    }
    return manifest, extra_files
