"""Characterization of regions.py: pins its behaviour as it stands.

The policy is `nd3-v1`: per-pixel precedence KEEP > DELETE > REPLACE >
MODIFY; within a tier the later-created region (higher z_order) wins;
unclaimed pixels regenerate only on a KEEP-anchored turn. The code gates
regen additionally on a non-empty turn prompt (regen has nothing to run
on without one) and a non-empty unclaimed area — pinned here deliberately.

This suite is the executable form of that order.
"""

import io

import pytest
from PIL import Image

from app import regions

SIZE = (8, 8)


def rect_mask(box, size=SIZE):
    """L-mode mask: 255 inside box=(l, t, r, b), 0 outside."""
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def region(rid, action, z, prompt=None, name=None):
    return {"id": rid, "action": action, "z_order": z, "prompt": prompt,
            "name": name or rid}


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def white_pixels(mask):
    return {(x, y) for x in range(mask.width) for y in range(mask.height)
            if mask.getpixel((x, y)) == 255}


# --- effective_masks: the nd3-v1 precedence itself ---------------------------

@pytest.mark.parametrize("winner,loser", [
    ("keep", "delete"), ("keep", "replace"), ("keep", "modify"),
    ("delete", "replace"), ("delete", "modify"),
    ("replace", "modify"),
])
def test_precedence_pairwise(winner, loser):
    # loser gets the HIGHER z: precedence must beat recency across tiers
    regs = [region("w", winner, z=1), region("l", loser, z=2)]
    masks = {"w": rect_mask((0, 0, 4, 4)), "l": rect_mask((2, 2, 6, 6))}
    eff, _ = regions.effective_masks(regs, masks, SIZE)
    assert white_pixels(eff["w"]) == white_pixels(masks["w"])
    assert white_pixels(eff["l"]) == (white_pixels(masks["l"])
                                      - white_pixels(masks["w"]))


def test_same_action_later_z_wins_the_overlap():
    regs = [region("a", "modify", z=1, prompt="pa"),
            region("b", "modify", z=2, prompt="pb")]
    masks = {"a": rect_mask((0, 0, 4, 4)), "b": rect_mask((2, 2, 6, 6))}
    eff, _ = regions.effective_masks(regs, masks, SIZE)
    assert white_pixels(eff["b"]) == white_pixels(masks["b"])
    assert white_pixels(eff["a"]) == (white_pixels(masks["a"])
                                      - white_pixels(masks["b"]))


def test_unclaimed_is_the_inverse_of_all_claims():
    regs = [region("k", "keep", z=1)]
    masks = {"k": rect_mask((0, 0, 4, 8))}
    _, unclaimed = regions.effective_masks(regs, masks, SIZE)
    assert white_pixels(unclaimed) == white_pixels(rect_mask((4, 0, 8, 8)))


# --- build_pipeline: pass planning -------------------------------------------

def test_pass_order_capabilities_and_denoise():
    regs = [region("k", "keep", z=1), region("d", "delete", z=2),
            region("r", "replace", z=3, prompt="new thing"),
            region("m", "modify", z=4, prompt="tweak")]
    masks = {"k": rect_mask((0, 0, 2, 2)), "d": rect_mask((2, 0, 4, 2)),
             "r": rect_mask((4, 0, 6, 2)), "m": rect_mask((6, 0, 8, 2))}
    plan = regions.build_pipeline(regs, masks, SIZE, "the turn prompt")
    passes = plan["passes"]
    assert [p["kind"] for p in passes] == ["remove", "replace", "modify",
                                          "regen_rest"]
    assert [p["capability"] for p in passes] == [
        "remove", "inpaint_replace", "inpaint_modify", "inpaint_replace"]
    # denoise is pass-scoped: only MODIFY takes the handle
    assert [p["uses_denoise"] for p in passes] == [False, False, True, False]
    assert [p["idx"] for p in passes] == [0, 1, 2, 3]
    assert plan["policy"] == "nd3-v1"


def test_deletes_collapse_to_one_remove_pass_over_the_union():
    regs = [region("d1", "delete", z=1), region("d2", "delete", z=2)]
    masks = {"d1": rect_mask((0, 0, 2, 2)), "d2": rect_mask((4, 0, 6, 2))}
    plan = regions.build_pipeline(regs, masks, SIZE, None)
    (p,) = plan["passes"]
    assert p["kind"] == "remove"
    assert set(p["region_ids"]) == {"d1", "d2"}
    assert p["prompt"] is None
    got = regions.load_mask(p["mask"], SIZE)
    assert white_pixels(got) == (white_pixels(masks["d1"])
                                 | white_pixels(masks["d2"]))


def test_replace_passes_run_z_ascending():
    regs = [region("hi", "replace", z=5, prompt="b"),
            region("lo", "replace", z=1, prompt="a")]
    masks = {"hi": rect_mask((4, 0, 6, 2)), "lo": rect_mask((0, 0, 2, 2))}
    plan = regions.build_pipeline(regs, masks, SIZE, None)
    assert [p["region_ids"] for p in plan["passes"]] == [["lo"], ["hi"]]


def test_regen_rest_carries_turn_prompt_and_keep_ids():
    plan = regions.build_pipeline([region("k", "keep", z=1)],
                                  {"k": rect_mask((0, 0, 4, 8))}, SIZE, "fill")
    (p,) = plan["passes"]
    assert p["kind"] == "regen_rest"
    assert p["prompt"] == "fill"
    assert p["region_ids"] == ["k"]


def test_without_keep_the_rest_is_preserved():
    # without a KEEP the regions say what changes: no regen_rest, even with a turn prompt
    plan = regions.build_pipeline([region("m", "modify", z=1, prompt="tweak")],
                                  {"m": rect_mask((0, 0, 2, 2))}, SIZE, "turn")
    assert [p["kind"] for p in plan["passes"]] == ["modify"]


def test_keep_only_without_turn_prompt_raises():
    with pytest.raises(regions.RegionError, match="keep-only"):
        regions.build_pipeline([region("k", "keep", z=1)],
                               {"k": rect_mask((0, 0, 4, 8))}, SIZE, None)


def test_full_canvas_keep_raises_nothing_to_generate():
    with pytest.raises(regions.RegionError, match="nothing to generate"):
        regions.build_pipeline([region("k", "keep", z=1)],
                               {"k": rect_mask((0, 0, 8, 8))}, SIZE, "fill")


def test_region_prompt_falls_back_to_turn_prompt():
    plan = regions.build_pipeline([region("r", "replace", z=1)],
                                  {"r": rect_mask((0, 0, 2, 2))}, SIZE,
                                  "turn says")
    assert plan["passes"][0]["prompt"] == "turn says"


def test_replace_with_no_prompt_anywhere_names_the_region():
    regs = [region("r", "replace", z=1, name="left arm")]
    with pytest.raises(regions.RegionError, match="left arm"):
        regions.build_pipeline(regs, {"r": rect_mask((0, 0, 2, 2))}, SIZE, None)


def test_blank_turn_prompt_counts_as_no_prompt():
    regs = [region("r", "replace", z=1, name="left arm")]
    with pytest.raises(regions.RegionError, match="left arm"):
        regions.build_pipeline(regs, {"r": rect_mask((0, 0, 2, 2))}, SIZE, "   ")


def test_unknown_action_raises():
    with pytest.raises(regions.RegionError, match="unknown action"):
        regions.build_pipeline([region("x", "extract", z=1)],
                               {"x": rect_mask((0, 0, 2, 2))}, SIZE, None)


def test_fully_occluded_work_region_is_reported_and_skipped():
    regs = [region("k", "keep", z=1), region("m", "modify", z=2, prompt="p")]
    masks = {"k": rect_mask((0, 0, 4, 4)), "m": rect_mask((1, 1, 3, 3))}
    plan = regions.build_pipeline(regs, masks, SIZE, "turn")
    assert plan["occluded_region_ids"] == ["m"]
    assert [p["kind"] for p in plan["passes"]] == ["regen_rest"]


def test_keep_is_never_reported_occluded():
    # k1 sits fully under the later k2; only non-KEEP regions can occlude out
    regs = [region("k1", "keep", z=1), region("k2", "keep", z=2)]
    masks = {"k1": rect_mask((0, 0, 2, 2)), "k2": rect_mask((0, 0, 4, 4))}
    plan = regions.build_pipeline(regs, masks, SIZE, "fill")
    assert plan["occluded_region_ids"] == []


def test_keep_mask_is_the_keep_union_and_none_without_keep():
    regs = [region("k", "keep", z=1), region("m", "modify", z=2, prompt="p")]
    masks = {"k": rect_mask((0, 0, 4, 8)), "m": rect_mask((4, 0, 8, 8))}
    plan = regions.build_pipeline(regs, masks, SIZE, None)
    km = regions.load_mask(plan["keep_mask"], SIZE)
    assert white_pixels(km) == white_pixels(masks["k"])

    plan2 = regions.build_pipeline([region("m", "modify", z=1, prompt="p")],
                                   {"m": rect_mask((0, 0, 2, 2))}, SIZE, None)
    assert plan2["keep_mask"] is None


def test_pass_envelope_fields():
    plan = regions.build_pipeline([region("r", "replace", z=1, prompt="p")],
                                  {"r": rect_mask((0, 0, 2, 2))}, SIZE, None)
    (p,) = plan["passes"]
    assert p["status"] == "pending"
    assert p["prompt_id"] is None and p["graph"] is None
    assert p["image_path"] is None
    assert isinstance(p["mask"], bytes)
    assert p["mask"][:8] == b"\x89PNG\r\n\x1a\n"
    assert plan["size"] == [8, 8]


# --- mask I/O ----------------------------------------------------------------

def test_load_mask_thresholds_at_128():
    img = Image.new("L", SIZE, 0)
    img.putpixel((0, 0), 127)
    img.putpixel((1, 0), 128)
    m = regions.load_mask(png(img), SIZE)
    assert m.getpixel((0, 0)) == 0
    assert m.getpixel((1, 0)) == 255


def test_load_mask_resizes_nearest_to_target_size():
    m = regions.load_mask(png(rect_mask((0, 0, 2, 2), size=(4, 4))), SIZE)
    assert m.size == SIZE
    assert white_pixels(m) == white_pixels(rect_mask((0, 0, 4, 4)))


def test_is_empty():
    assert regions.is_empty(Image.new("L", SIZE, 0))
    assert not regions.is_empty(rect_mask((0, 0, 1, 1)))


# --- apply_keep: the byte-exact restore --------------------------------------

def test_apply_keep_restores_original_pixels_exactly():
    original = Image.new("RGB", SIZE, (10, 20, 30))
    result = Image.new("RGB", SIZE, (200, 100, 50))
    out = Image.open(io.BytesIO(regions.apply_keep(
        png(result), png(original), png(rect_mask((0, 0, 4, 8))))))
    assert out.getpixel((0, 0)) == (10, 20, 30)    # kept: original, exact
    assert out.getpixel((7, 7)) == (200, 100, 50)  # rest: the generated result


def test_apply_keep_tolerates_size_mismatch():
    original = Image.new("RGB", (4, 4), (10, 20, 30))
    result = Image.new("RGB", SIZE, (200, 100, 50))
    out = Image.open(io.BytesIO(regions.apply_keep(
        png(result), png(original), png(rect_mask((0, 0, 4, 8))))))
    assert out.size == SIZE
    assert out.getpixel((0, 0)) == (10, 20, 30)  # uniform original survives resize
