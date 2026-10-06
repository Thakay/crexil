"""Region-control core: effective-mask resolution and
region-turn pipeline planning.

everything here is orchestration around exported graphs — mask
arithmetic and pass ordering, zero ML. The cockpit chooses graphs and
parameters, nothing more.

Policy `nd3-v1`: per-pixel action precedence KEEP > DELETE > REPLACE >
MODIFY; within the same action the later-created region wins (z-order);
unclaimed pixels regenerate under the turn prompt iff at least one KEEP
region exists (a KEEP says what stays), otherwise they are preserved (the
regions say what changes).

KEEP restoration preserves original channel values and source alpha. It
requires matching source, result and mask geometry, never a hidden resize.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageChops

POLICY_ID = "nd3-v1"
ACTIONS = ("keep", "modify", "replace", "delete")
PRECEDENCE = ("keep", "delete", "replace", "modify")  # strongest first

# region-pipeline pass kinds -> preset workflow capability keys
PASS_CAPABILITIES = {
    "remove": "remove",
    "replace": "inpaint_replace",
    "modify": "inpaint_modify",
    "regen_rest": "inpaint_replace",
}
REGION_CAPABILITY_KEYS = sorted(set(PASS_CAPABILITIES.values()))


class RegionError(Exception):
    pass


def load_mask(png_bytes: bytes, size: tuple[int, int]) -> Image.Image:
    """Binary L-mode mask at the source image's resolution (white = region)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    if img.size != size:
        img = img.resize(size, Image.NEAREST)
    return img.point(lambda v: 255 if v >= 128 else 0)


def mask_png(mask: Image.Image) -> bytes:
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def is_empty(mask: Image.Image) -> bool:
    return mask.getbbox() is None


def _union(masks: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    out = Image.new("L", size, 0)
    for m in masks:
        out = ImageChops.lighter(out, m)
    return out


def effective_masks(regions: list[dict], masks: dict[str, Image.Image],
                    size: tuple[int, int]) -> tuple[dict[str, Image.Image],
                                                    Image.Image]:
    """Resolve overlaps per nd3-v1. Returns ({region_id: effective mask},
    unclaimed mask). Claiming order: action precedence, then later z first."""
    claimed = Image.new("L", size, 0)
    eff: dict[str, Image.Image] = {}
    for action in PRECEDENCE:
        tier = sorted((r for r in regions if r["action"] == action),
                      key=lambda r: -r["z_order"])
        for r in tier:
            m = ImageChops.subtract(masks[r["id"]], claimed)
            eff[r["id"]] = m
            claimed = ImageChops.lighter(claimed, m)
    return eff, ImageChops.invert(claimed)


def build_pipeline(regions: list[dict], masks: dict[str, Image.Image],
                   size: tuple[int, int], turn_prompt: str | None, *,
                   regenerate_unclaimed: bool = True) -> dict:
    """Plan the deterministic pass list for one region turn.

    Pass order: DELETE (one LaMa pass over the union) -> each REPLACE ->
    each MODIFY (z ascending, disjoint by construction) -> if KEEP exists,
    regenerate the unclaimed rest under the turn prompt -> final verbatim
    KEEP restore. Masks travel as PNG bytes under the "mask" key; the region
    route uploads them and replaces bytes with content-addressed refs.
    """
    for r in regions:
        if r["action"] not in ACTIONS:
            raise RegionError(f"region {r['id']}: unknown action {r['action']}")
    eff, unclaimed = effective_masks(regions, masks, size)
    keeps = [r for r in regions if r["action"] == "keep"]
    occluded = [r["id"] for r in regions
                if r["action"] != "keep" and is_empty(eff[r["id"]])]

    prompt = (turn_prompt or "").strip() or None
    passes: list[dict] = []

    def add(kind: str, mask: Image.Image, *, region_ids: list[str],
            prompt_: str | None, uses_denoise: bool = False) -> None:
        passes.append({
            "idx": len(passes), "kind": kind,
            "capability": PASS_CAPABILITIES[kind],
            "region_ids": region_ids, "prompt": prompt_,
            "uses_denoise": uses_denoise,
            "mask": mask_png(mask),
            "status": "pending", "prompt_id": None,
            "graph": None, "image_path": None,
        })

    deletes = [r for r in regions
               if r["action"] == "delete" and not is_empty(eff[r["id"]])]
    if deletes:
        add("remove", _union([eff[r["id"]] for r in deletes], size),
            region_ids=[r["id"] for r in deletes], prompt_=None)

    for action, kind in (("replace", "replace"), ("modify", "modify")):
        for r in sorted((r for r in regions if r["action"] == action),
                        key=lambda r: r["z_order"]):
            if is_empty(eff[r["id"]]):
                continue
            rp = (r.get("prompt") or "").strip() or prompt
            if not rp:
                raise RegionError(
                    f"region '{r['name']}' ({action}) has no region prompt and "
                    "the turn has no prompt to fall back to")
            add(kind, eff[r["id"]], region_ids=[r["id"]], prompt_=rp,
                uses_denoise=(action == "modify"))

    if regenerate_unclaimed and keeps and prompt and not is_empty(unclaimed):
        add("regen_rest", unclaimed,
            region_ids=[r["id"] for r in keeps], prompt_=prompt)

    if not passes:
        raise RegionError(
            "nothing to generate: no effective region needs work "
            "(keep-only turns need a turn prompt to regenerate the rest)")

    # KEEP is restored server-side after the final pass (apply_keep):
    # a PIL paste of the ORIGINAL pixels is byte-exact, where a composite
    # graph pass costs a GPU job and drifts ±1/255 through the float
    # round-trip (still compositing the original pixels back).
    keep_mask = None
    if keeps:
        keep_union = _union([eff[r["id"]] for r in keeps], size)
        if not is_empty(keep_union):
            keep_mask = mask_png(keep_union)

    return {"policy": POLICY_ID, "size": list(size),
            "occluded_region_ids": occluded, "passes": passes,
            "keep_mask": keep_mask}


def apply_keep(result_png: bytes, original_png: bytes,
               keep_mask_png: bytes) -> bytes:
    """Restore protected pixels and source alpha without resampling either."""
    original = Image.open(io.BytesIO(original_png))
    mode = ("RGBA" if "A" in original.getbands()
            or "transparency" in original.info else "RGB")
    original = original.convert(mode)
    result = Image.open(io.BytesIO(result_png)).convert(mode)
    if original.size != result.size:
        raise RegionError("the result must match the source image size "
                          "before protected pixels can be restored")
    mask = Image.open(io.BytesIO(keep_mask_png))
    if mask.size != original.size:
        raise RegionError("the protected mask must match the source image size")
    mask = load_mask(keep_mask_png, original.size)
    if mode == "RGBA":
        result.putalpha(original.getchannel("A"))
    result.paste(original, (0, 0), mask)
    out = io.BytesIO()
    result.save(out, format="PNG")
    return out.getvalue()
