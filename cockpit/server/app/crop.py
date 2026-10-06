"""Shared crop geometry and exact pixel containment for region compositing.

Each pass uses its plan-time crop across engines. Paste-back clips edits to
the declared binary mask; feathering blends inward and never expands it.
Mask-native graphs still need this outer fence because decoding and graph
compositing can change pixels outside their internal mask. RGB and RGBA source
pixels outside the mask remain exact, and source alpha is preserved everywhere.
Mask coordinates must already match the source; this layer never resizes masks.

Pure PIL, no database or runtime access. A missing mask explicitly requests a
whole-box paste and is not sufficient for a Region Edit implementation.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageFilter

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

    A supplied mask is the exact edit boundary. Its threshold is 128 and
    feathering never crosses its binary edge. Source alpha is retained even
    when the engine returns RGB. A result at the wrong size is resized to the
    declared crop; source pixels and mask coordinates are never resampled.
    mask_png=None explicitly replaces the whole crop, without feathering.
    """
    frame = Image.open(io.BytesIO(frame_png))
    frame = frame.convert("RGBA" if "A" in frame.getbands()
                          or "transparency" in frame.info else "RGB")
    alpha = frame.getchannel("A") if frame.mode == "RGBA" else None
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
            raise ValueError("the edit mask must match the source image size")
        mask = mask.point(lambda v: 255 if v >= 128 else 0)
        m = mask.crop(box)
        if feather > 0:
            m = ImageChops.multiply(m.filter(ImageFilter.BoxBlur(feather)), m)
        frame.paste(result, (box[0], box[1]), m)
    if alpha is not None:
        frame.putalpha(alpha)
    out = io.BytesIO()
    frame.save(out, format="PNG")
    return out.getvalue()
