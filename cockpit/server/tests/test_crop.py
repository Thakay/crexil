"""The crop half of the region crop+composite layer (app/crop.py).

    box rule        margin (frac-of-larger-side with a floor), snap-up to
                    /16, MIN_SIDE growth centered, edge clamp with inward
                    shift, full-frame fallback at >=90% of both dims,
                    empty mask -> full frame, determinism
    paste_back      whole-box paste (SDXL tier) leaves outside-box bytes
                    untouched; masked paste (klein/Qwen tiers) leaves
                    outside-mask bytes untouched beyond the feather;
                    wrong-size result resized to the box (Qwen ~1MP cap)
    KEEP round-trip crop -> mutate -> paste_back -> regions.apply_keep
                    restores KEEP pixels byte-exactly (the clause)

The composite half (apply_keep) is regions.py's, pinned in
tests/test_regions.py; the pipeline that consumes these boxes is
regionrun.py / region_exec.py, pinned in their own suites.
"""

from __future__ import annotations

import io

from PIL import Image

from app import regions as regions_mod
from app.crop import (FULL_FRAME_FRAC, MARGIN_MIN, MIN_SIDE, SNAP, crop,
                      crop_box, paste_back)

FRAME = (2048, 1536)


def rect_mask(box: tuple[int, int, int, int],
              size: tuple[int, int] = FRAME) -> Image.Image:
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def flat(color: tuple[int, int, int],
         size: tuple[int, int] = FRAME) -> Image.Image:
    return Image.new("RGB", size, color)


# --- the box rule ---


def test_box_contains_bbox_plus_margin_and_snaps_to_16():
    bbox = (900, 700, 1200, 900)  # 300 x 200
    box = crop_box(rect_mask(bbox), FRAME)
    assert box is not None
    x0, y0, x1, y1 = box
    margin = max(MARGIN_MIN, round(0.25 * 300))
    assert x0 <= bbox[0] - margin and x1 >= bbox[2] + margin
    assert y0 <= bbox[1] - margin and y1 >= bbox[3] + margin
    assert (x1 - x0) % SNAP == 0 and (y1 - y0) % SNAP == 0
    assert x0 >= 0 and x1 <= FRAME[0] and y0 >= 0 and y1 <= FRAME[1]


def test_small_mask_grows_to_min_side_centered():
    box = crop_box(rect_mask((1000, 700, 1080, 760)), FRAME)
    assert box is not None
    x0, y0, x1, y1 = box
    assert (x1 - x0) == MIN_SIDE and (y1 - y0) == MIN_SIDE
    # centered on the bbox, within snap tolerance
    assert abs(((x0 + x1) / 2) - 1040) <= SNAP
    assert abs(((y0 + y1) / 2) - 730) <= SNAP


def test_margin_floor_applies_to_tiny_masks():
    # bbox 40 x 40 -> frac margin 10 -> the 64 px floor wins
    box = crop_box(rect_mask((1000, 700, 1040, 740)), FRAME)
    assert box is not None
    assert box[0] <= 1000 - MARGIN_MIN and box[2] >= 1040 + MARGIN_MIN


def test_corner_mask_clamps_with_inward_shift_size_kept():
    box = crop_box(rect_mask((0, 0, 100, 100)), FRAME)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 == 0 and y0 == 0
    assert (x1 - x0) == MIN_SIDE and (y1 - y0) == MIN_SIDE


def test_near_full_mask_falls_back_to_full_frame():
    w, h = FRAME
    inset_w, inset_h = int(w * 0.02), int(h * 0.02)
    assert crop_box(
        rect_mask((inset_w, inset_h, w - inset_w, h - inset_h)), FRAME) is None


def test_empty_mask_is_full_frame():
    assert crop_box(Image.new("L", FRAME, 0), FRAME) is None


def test_frame_smaller_than_min_side_is_always_full_frame():
    small = (256, 256)
    assert crop_box(rect_mask((10, 10, 40, 40), small), small) is None


def test_box_is_deterministic():
    m = rect_mask((512, 384, 900, 800))
    assert crop_box(m, FRAME) == crop_box(m, FRAME)


def test_mid_size_mask_stays_a_crop():
    # bbox ~55% of each dim: with the frac margin the box lands under the
    # 90% fallback on both dims and stays a crop
    w, h = FRAME
    box = crop_box(rect_mask((0, 0, int(w * 0.55), int(h * 0.55))), FRAME)
    assert box is not None
    assert (box[2] - box[0]) < FULL_FRAME_FRAC * w


# --- crop / paste_back ---


def test_crop_none_is_identity_and_box_is_exact():
    img = flat((10, 20, 30))
    img.paste((200, 100, 50), (100, 100, 300, 300))
    assert crop(img, None) is img
    c = crop(img, (100, 100, 300, 300))
    assert c.size == (200, 200)
    assert c.getpixel((0, 0)) == (200, 100, 50)


def test_whole_box_paste_changes_only_the_box():
    frame = flat((10, 20, 30))
    box = (512, 512, 1024, 1024)
    out = paste_back(png(frame), png(flat((250, 0, 0), (512, 512))), box, None)
    o = Image.open(io.BytesIO(out))
    assert o.getpixel((600, 600)) == (250, 0, 0)
    assert o.getpixel((0, 0)) == (10, 20, 30)
    assert o.getpixel((511, 600)) == (10, 20, 30)
    assert o.getpixel((600, 1024)) == (10, 20, 30)


def test_masked_paste_stays_inside_the_mask_beyond_the_feather():
    frame = flat((10, 20, 30))
    mask = rect_mask((700, 700, 900, 900))
    box = (512, 512, 1024, 1024)
    out = paste_back(png(frame), png(flat((250, 0, 0), (512, 512))), box,
                     png(mask), feather=2)
    o = Image.open(io.BytesIO(out))
    assert o.getpixel((800, 800)) == (250, 0, 0)          # inside the mask
    assert o.getpixel((600, 600)) == (10, 20, 30)          # in-box, off-mask
    assert o.getpixel((695, 800)) == (10, 20, 30)          # beyond the feather
    assert o.getpixel((100, 100)) == (10, 20, 30)          # outside the box


def test_wrong_size_result_is_resized_to_the_box():
    frame = flat((10, 20, 30))
    box = (512, 512, 1024, 1024)
    # the Qwen case: the engine returned a smaller frame than the crop
    out = paste_back(png(frame), png(flat((0, 200, 0), (256, 256))), box, None)
    o = Image.open(io.BytesIO(out))
    assert o.getpixel((600, 600)) == (0, 200, 0)
    assert o.getpixel((1023, 1023)) == (0, 200, 0)


def test_full_frame_paste_with_mask():
    frame = flat((10, 20, 30))
    mask = rect_mask((100, 100, 300, 300))
    out = paste_back(png(frame), png(flat((0, 0, 250))), None, png(mask),
                     feather=0)
    o = Image.open(io.BytesIO(out))
    assert o.getpixel((200, 200)) == (0, 0, 250)
    assert o.getpixel((400, 400)) == (10, 20, 30)


# --- the clause: KEEP byte-exact through the crop round-trip ---


def test_keep_is_byte_exact_through_crop_mutate_paste_apply_keep():
    source = Image.effect_noise(FRAME, 64).convert("RGB")
    keep_box, work_box = (0, 0, 700, 1536), (900, 600, 1400, 1000)
    keep_mask, work_mask = rect_mask(keep_box), rect_mask(work_box)

    box = crop_box(work_mask, FRAME)
    assert box is not None
    mutated = crop(source, box).point(lambda v: 255 - v)  # the "engine"
    working = paste_back(png(source), png(mutated), box, png(work_mask))
    final = regions_mod.apply_keep(working, png(source),
                                   regions_mod.mask_png(keep_mask))

    out = Image.open(io.BytesIO(final)).convert("RGB")
    assert out.crop(keep_box).tobytes() == source.crop(keep_box).tobytes()
    # and the work region actually changed
    assert out.getpixel((1100, 800)) != source.getpixel((1100, 800))
