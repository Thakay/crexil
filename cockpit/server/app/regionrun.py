"""Region-run plan + per-engine envelope builder.

One resolved mask set fans across every stepped engine ("EVERY ENGINE GETS
THE SAME REGIONS AND THE SAME PRECEDENCE" — the surface's own promise):
`regions.build_pipeline` plans the passes once, `crop.crop_box` fixes one
box per pass at plan time, and each engine batch gets its own envelope of
fully-patched pass graphs behind that shared geometry. The RegionExecutor
(region_exec.py) walks an envelope pass by pass — crop the working frame,
run the tier's recipe, composite back — and `regions.apply_keep` restores
KEEP byte-exactly at the end.

Tier semantics (the policy of record):
- SDXL rails are mask-native: the pass graphs take the cropped mask and
  composite internally; the raw region prompt rides as-is.
- klein / Qwen have no mask channel: MODIFY/REPLACE/regen run as
  instruction edits on the crop — the region prompt wrapped in a curated
  static template per action (never generated) — and the result lands
  only inside the effective mask at paste-back, edge-feathered.
- DELETE rides LaMa for every tier (on the pass crop, like every kind);
  KEEP is never a graph.

Templates are frozen, one instruction discipline per tier: klein takes
DESCRIPTIVE target-state phrasing, Qwen takes IMPERATIVE commands. "At the
center" is licensed by the crop itself: the box centres the region by
construction.
"""

from __future__ import annotations

import copy
import io

from PIL import Image

from . import crop as crop_mod
from . import presets as presets_mod
from . import regions as regions_mod

# --- tier map + instruction templates (curated static copy) ---

# model_family -> instruction-edit tier. Absent = mask-native rails: the
# raw region prompt rides unwrapped and the graph composites internally.
EDIT_TIER_FAMILIES = {
    "flux2-klein-4b": "klein",
    "qwen-image-edit-2511": "qwen",
}

TEMPLATES: dict[tuple[str, str], str] = {
    ("klein", "modify"):
        "the same scene, but {prompt}; everything else identical",
    ("klein", "replace"):
        "the same scene, but the object at the center is replaced with "
        "{prompt}; everything else identical",
    ("klein", "regen_rest"):
        "the same central subject, now in {prompt}; the subject itself "
        "unchanged",
    ("qwen", "modify"):
        "change the area at the center of the image to: {prompt}; keep "
        "everything else exactly the same",
    ("qwen", "replace"):
        "replace the object at the center of the image with {prompt}; keep "
        "everything else exactly the same",
    ("qwen", "regen_rest"):
        "change the background and surroundings to {prompt}; keep the "
        "central subject exactly as it is",
}

# Structural seed targets for region pass graphs (presets.SEED_FIELDS grown
# by the klein shape): patched ONLY where the field already exists on the
# node — klein's `sampler` is a SamplerCustomAdvanced with no `seed` input,
# so a blind patch would inject a junk field and miss the real `noise_seed`.
SEED_FIELDS = {"sampler": "seed", "removal_apply": "seed",
               "noise_seed": "noise_seed"}

# Geometry is the crop layer's, never the FRAME dial's: a region take lands
# at its source's size by construction, so these preset keys are ignored on
# region passes (documented at the route).
GEOMETRY_KEYS = {"width", "height"}


class RegionRunError(Exception):
    """A region run the artist can fix, in their words (422 at the route)."""


def tier_of(preset: dict) -> str | None:
    return EDIT_TIER_FAMILIES.get(preset.get("model_family") or "")


def pass_prompt(preset: dict, kind: str, raw_prompt: str | None
                ) -> tuple[str | None, str | None]:
    """(prompt to send, template id) for one pass on one engine."""
    if raw_prompt is None:
        return None, None
    tier = tier_of(preset)
    tpl = TEMPLATES.get((tier or "", kind))
    if tpl is None:
        return raw_prompt, "raw"
    return tpl.format(prompt=raw_prompt), f"{tier}-{kind}"


def _snap16(v: int) -> int:
    return max(16, (v // 16) * 16)


def _crop_mask_bytes(mask_png: bytes, box) -> bytes:
    """The pass mask at crop geometry — what a mask-native graph loads."""
    if box is None:
        return mask_png
    img = Image.open(io.BytesIO(mask_png)).convert("L")
    return regions_mod.mask_png(img.crop(tuple(box)))


def build_region_plan(rows: list[dict], masks: dict, size: tuple[int, int],
                      turn_prompt: str | None, *, regenerate_unclaimed: bool = True) -> dict:
    """The engine-neutral plan: regions.build_pipeline + one crop box per
    pass. Each pass gains `box` (None = full frame) and `mask_crop` (the
    mask at crop geometry, bytes); DELETE stays full-frame on LaMa. Raises
    RegionRunError in the artist's words."""
    try:
        plan = regions_mod.build_pipeline(rows, masks, size, turn_prompt,
                                         regenerate_unclaimed=regenerate_unclaimed)
    except regions_mod.RegionError as e:
        raise RegionRunError(str(e))
    for p in plan["passes"]:
        # every kind crops — including remove: LaMa on the zoomed crop
        # fills with sharp local texture where the full 1MP frame gave it
        # a blurry low-frequency patch (observed live, e2e); the
        # margin + MIN_SIDE keep its context, and its internal composite
        # keeps the box edge seamless
        mask_img = regions_mod.load_mask(p["mask"], size)
        box = crop_mod.crop_box(mask_img, size)
        p["box"] = list(box) if box else None
        p["mask_crop"] = _crop_mask_bytes(p["mask"], p["box"])
    return plan


def check_region_capable(preset: dict) -> None:
    missing = [c for c in regions_mod.REGION_CAPABILITY_KEYS
               if c not in preset.get("workflows", {})]
    if missing:
        raise RegionRunError(
            f"preset '{preset['id']}' can't take region turns — it has no "
            f"{', '.join(missing)} recipe committed; pick a mask-capable "
            "engine, or set its takes to 0")


def _patch_if_field(graph: dict, title: str, field: str, value) -> None:
    """Patch by title, but only nodes that already carry the field — the
    field-presence rule that keeps a klein sampler free of junk `seed`."""
    for nid in presets_mod.find_nodes_by_title(graph, title):
        inputs = graph[nid].setdefault("inputs", {})
        if field in inputs:
            inputs[field] = value


def _control_targets(graph: dict, targets: list[dict]) -> list[tuple[str, str]]:
    """Only existing fields can consume a public control on a native pass."""
    return [(nid, target['field']) for target in targets
            for nid in presets_mod.find_nodes_by_title(graph, target['node_title'])
            if target['field'] in graph[nid].get('inputs', {})]


_patch_existing = presets_mod.patch_existing


def build_engine_envelope(preset: dict, applied: dict, specs: dict,
                          plan: dict, *, source: dict, keep: dict | None,
                          region_ids: list[str], workflows: dict | None = None) -> dict:
    """One engine's pass graphs over the shared plan — everything but the
    per-take seed and save prefix (stamp_take) and the runtime working-frame
    upload (the executor's). `specs` are the preset's region-applicable
    input specs (presets.applied_inputs shape); each plan pass must already
    carry `mask_upload` / `mask_sha256` / `mask_path` and `box`.

    Envelope contract (rides DispatchRequest.submitted_graph, persisted on
    the slot row, rebuilt verbatim by restart recovery):
      {"region_plan": {policy, size, source {path,sha256,name},
                       keep {path,sha256}|None, region_ids,
                       occluded_region_ids, passes [...]}}
    """
    prompt_spec = specs.get("prompt")
    passes = []
    for p in plan["passes"]:
        graph = (copy.deepcopy(workflows[p['capability']]) if workflows is not None else
                 presets_mod.load_workflow(preset["workflows"][p["capability"]]))
        tier = tier_of(preset)
        controls = {}
        # preset handles where the pass graph has the node — geometry and
        # denoise are pass-scoped, never the preset loop's
        for key, spec in specs.items():
            if spec.get("type") == "seed" or key == "denoise" \
                    or key in GEOMETRY_KEYS or key == "prompt" \
                    or key not in applied:
                continue
            for nid, field in _control_targets(graph, spec['target']):
                graph[nid]['inputs'][field] = applied[key]
                controls[key] = applied[key]
        prompt, template_id = pass_prompt(preset, p["kind"], p["prompt"])
        if prompt is not None:
            if prompt_spec is None:
                raise RegionRunError('This region recipe has no mapped instruction input.')
            targets = _control_targets(graph, prompt_spec['target'])
            if not targets:
                raise RegionRunError('This region recipe has no mapped instruction input.')
            for nid, field in targets:
                graph[nid]['inputs'][field] = prompt
            controls['prompt'] = prompt
        if p.get("uses_denoise") and "denoise" in applied:
            for nid, field in _control_targets(graph, specs['denoise']['target']):
                graph[nid]['inputs'][field] = applied['denoise']
                controls['denoise'] = applied['denoise']
        if _control_targets(graph, [{'node_title': title, 'field': field}
                                    for title, field in SEED_FIELDS.items()]):
            controls['seed'] = applied.get('seed', {'mode': 'random'})
        # the shared box owns geometry: crop dims into whatever latent /
        # scheduler nodes the graph carries (field-presence guarded — SDXL
        # pass graphs have neither and derive size from the crop itself)
        if p["box"] is not None:
            bw, bh = p["box"][2] - p["box"][0], p["box"][3] - p["box"][1]
        else:
            bw, bh = _snap16(plan["size"][0]), _snap16(plan["size"][1])
        for title in ("latent_size", "scheduler"):
            _patch_if_field(graph, title, "width", bw)
            _patch_if_field(graph, title, "height", bh)
        # the mask at crop geometry, for mask-native graphs (klein/Qwen
        # graphs have no region_mask node — the mask acts at paste-back)
        _patch_existing(graph, {"node_title": "region_mask",
                                "field": "image"}, p["mask_upload"])
        controls = {key: value for key, value in controls.items() if key == 'seed' or any(
            graph[nid]['inputs'][field] == value for nid, field in _control_targets(graph, specs[key]['target']))}
        passes.append({
            "idx": p["idx"], "kind": p["kind"],
            "capability": p["capability"],
            "workflow": preset["workflows"][p["capability"]],
            "region_ids": p["region_ids"],
            "prompt": prompt, "template_id": template_id,
            "applied_controls": controls,
            "box": p["box"],
            # instruction tiers paste through the effective mask (their
            # graphs never saw it); mask-native passes composited already
            "masked_paste": bool(tier and p["kind"] != "remove"),
            "mask": {"path": p["mask_path"], "sha256": p["mask_sha256"],
                     "upload": p["mask_upload"]},
            "graph": graph,
        })
    return {"region_plan": {
        "policy": plan["policy"], "size": list(plan["size"]),
        "source": dict(source), "keep": dict(keep) if keep else None,
        "region_ids": list(region_ids),
        "occluded_region_ids": list(plan["occluded_region_ids"]),
        "passes": passes,
    }}


def stamp_take(envelope: dict, seed: int | None, run_token: str, pos: int) -> dict:
    """A take's own copy: seed into every pass graph (field-presence rule),
    save prefixes namespaced per pass."""
    env = copy.deepcopy(envelope)
    for p in env["region_plan"]["passes"]:
        graph = p["graph"]
        for title, field in SEED_FIELDS.items():
            _patch_if_field(graph, title, field, seed)
        if 'seed' in p.get('applied_controls', {}):
            p['applied_controls']['seed'] = seed
        for node in graph.values():
            if node.get("class_type") == "SaveImage":
                node["inputs"]["filename_prefix"] = (
                    f"cockpit/v4/{run_token}/s{pos}p{p['idx']}")
    return env
