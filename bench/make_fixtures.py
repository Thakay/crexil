#!/usr/bin/env python3
"""Generate the committed region-case fixtures under bench/sources/.

Every image here is synthetic: drawn by this script, no model output, no metadata.

  mask-*.png   white-on-black, source-size, one plain ellipse or rectangle per region,
               placed where the object lands when the case's source brief is minted with
               the preset, seed and runtime recorded in region_cases.json
  source.png   a layout diagram of the case: the same shapes, coloured by action, on a
               neutral ground. It documents the mask layout. It is NOT the image the
               bench edits: the bench mints the real source from the brief at run time.

Run from cockpit/server so Pillow is available:
    uv run python ../../bench/make_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

BENCH = Path(__file__).resolve().parent
SIZE = (1024, 1024)
GROUND = (138, 138, 138)
HORIZON = (112, 112, 112)
ACTION_COLOUR = {
    "keep": (72, 160, 96),
    "modify": (64, 120, 200),
    "replace": (224, 148, 48),
    "delete": (200, 72, 72),
}

# (shape, box) per mask file; a box is (left, top, right, bottom), right and bottom exclusive
LAYOUT = {
    "sources/rc-keep/mask-keep-subject.png": ("ellipse", (160, 40, 881, 1001)),
    "sources/rc-modify/mask-modify-glass.png": ("rect", (330, 390, 701, 871)),
    "sources/rc-replace/mask-replace-teapot.png": ("ellipse", (370, 410, 661, 631)),
    "sources/rc-delete/mask-delete-barrel.png": ("rect", (485, 690, 676, 946)),
    "sources/rc-combo/mask-keep-flask.png": ("ellipse", (30, 380, 271, 701)),
    "sources/rc-combo/mask-replace-scale.png": ("rect", (760, 455, 1001, 701)),
    "sources/rc-combo/mask-delete-smallbottle.png": ("rect", (295, 470, 391, 671)),
}


def _draw(draw: ImageDraw.ImageDraw, shape: str, box: tuple[int, int, int, int], fill) -> None:
    left, top, right, bottom = box
    inclusive = (left, top, right - 1, bottom - 1)
    if shape == "ellipse":
        draw.ellipse(inclusive, fill=fill)
    else:
        draw.rectangle(inclusive, fill=fill)


def main() -> None:
    cases = json.loads((BENCH / "region_cases.json").read_text())["cases"]
    for case in cases:
        diagram = Image.new("RGB", SIZE, GROUND)
        pen = ImageDraw.Draw(diagram)
        pen.rectangle((0, 700, SIZE[0] - 1, SIZE[1] - 1), fill=HORIZON)
        for region in case["regions"]:
            shape, box = LAYOUT[region["mask"]]
            mask = Image.new("L", SIZE, 0)
            _draw(ImageDraw.Draw(mask), shape, box, 255)
            out = BENCH / region["mask"]
            out.parent.mkdir(parents=True, exist_ok=True)
            mask.save(out, optimize=True)
            _draw(pen, shape, box, ACTION_COLOUR[region["action"]])
        diagram.save(BENCH / case["source"], optimize=True)
        print(f"{case['id']}: {len(case['regions'])} mask(s) + source diagram")


if __name__ == "__main__":
    main()
