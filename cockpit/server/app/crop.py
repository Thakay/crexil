"""The shared crop half of the region crop+composite layer.

One box per pass, computed once at plan time from the pass's effective
mask and inherited by every fanned-out engine. A fan-out compares
identical crops, side by side, so the box is plan state, never
executor state. Every pass kind runs on the crop (SDXL's mask-native
graphs take any-size inputs, so cropping is purely input-side; LaMa
fills sharper on the zoomed crop than on the full frame); regen_rest
hits the full-frame fallback naturally. The composite half already exists:
regions.apply_keep restores KEEP byte-exactly at frame size after the
final pass, so nothing here can break the byte-exactness clause.

Pure PIL, no DB, no app imports (mask arithmetic, zero ML).
"""

from __future__ import annotations

import io

from PIL import Image, ImageFilter

# The box rule of record: crop around the mask bbox with a context margin.
# The numbers are authored here and pinned by test_crop.py.
MARGIN_FRAC = 0.25   # of the mask bbox's larger side, per side
MARGIN_MIN = 64      # px, never less context than this
MIN_SIDE = 512       # px, floor per side (klein/Qwen quality collapses below)
SNAP = 16            # sides are multiples of this (⊃ every family's /8)
FULL_FRAME_FRAC = 0.90  # box covering ≥ this of both dims -> full frame
PASTE_FEATHER_PX = 2    # masked paste-back edge feather (klein/Qwen tiers)

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 — PIL crop convention


def _axis(lo: int, hi: int, frame_len: int, margin: int) -> tuple[int, int]:
    """One axis of the box rule: margin, snap-up, min-side, centered
    growth, clamp to the frame with inward shift (size preserved while
    the frame allows)."""
    lo -= margin
    hi += margin
    size = hi - lo
    target = -(-size // SNAP) * SNAP  # snap up
    target = min(frame_len, max(MIN_SIDE, target))
    extra = target - size
    lo -= extra // 2
    hi = lo + target
    if lo < 0:
        hi -= lo
        lo = 0
    if hi > frame_len:
        lo -= hi - frame_len
        hi = frame_len
    if lo < 0:
        lo = 0
    return lo, hi


def crop_box(mask: Image.Image, frame: tuple[int, int]) -> Box | None:
    """The pass's crop box from its effective mask, or None = full frame.

    None when the mask is empty (nothing to scope) or when the boxed
    region covers >= FULL_FRAME_FRAC of both frame dimensions — at that
    point cropping buys nothing and full frame keeps maximal context.
    """
    bbox = mask.getbbox()
    if bbox is None:
        return None
    fw, fh = frame
    bx0, by0, bx1, by1 = bbox
    margin = max(MARGIN_MIN, round(MARGIN_FRAC * max(bx1 - bx0, by1 - by0)))
    x0, x1 = _axis(bx0, bx1, fw, margin)
    y0, y1 = _axis(by0, by1, fh, margin)
    if (x1 - x0) >= FULL_FRAME_FRAC * fw and (y1 - y0) >= FULL_FRAME_FRAC * fh:
        return None
    return (x0, y0, x1, y1)


def crop(img: Image.Image, box: Box | None) -> Image.Image:
    return img if box is None else img.crop(box)


def paste_back(frame_png: bytes, result_png: bytes, box: Box | None,
               mask_png: bytes | None,
               feather: int = PASTE_FEATHER_PX) -> bytes:
    """Composite a pass result back into the working frame.

    mask_png=None pastes the whole box (the SDXL tier: its graph already
    composited through the softened mask on the crop, so box-edge pixels
    are original ±1/255 and seam-free). With a mask (klein/Qwen tiers —
    no mask channel of their own), only the effective-mask pixels land,
    edge-feathered so the seam blends; everything outside the mask stays
    byte-identical to the working frame. A result at the wrong size
    (Qwen's ~1MP internal cap) is LANCZOS-resized to the box first.
    """
    frame = Image.open(io.BytesIO(frame_png)).convert("RGB")
    result = Image.open(io.BytesIO(result_png)).convert("RGB")
    if box is None:
        box = (0, 0, frame.width, frame.height)
    bw, bh = box[2] - box[0], box[3] - box[1]
    if result.size != (bw, bh):
        result = result.resize((bw, bh), Image.LANCZOS)
    if mask_png is None:
        frame.paste(result, (box[0], box[1]))
    else:
        mask = Image.open(io.BytesIO(mask_png)).convert("L")
        if mask.size != frame.size:
            mask = mask.resize(frame.size, Image.NEAREST)
        mask = mask.point(lambda v: 255 if v >= 128 else 0)
        m = mask.crop(box)
        if feather > 0:
            m = m.filter(ImageFilter.BoxBlur(feather))
        frame.paste(result, (box[0], box[1]), m)
    out = io.BytesIO()
    frame.save(out, format="PNG")
    return out.getvalue()
