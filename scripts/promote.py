#!/usr/bin/env python3
"""The promotion boundary: copy a PNG + its sidecar manifest JSON from ext4
staging into the Windows-mounted promoted tree, per preset.

A standalone CLI that bypasses the cockpit: nothing it copies is recorded in
the store, the PROMOTE_ROOTS fence is not applied, and the cockpit's own
Promote writes to the same PROMOTE_DIR.
Copies are atomic-ish
(temp name in the destination dir, then rename); never symlinks.

Usage:
  scripts/promote.py <image.png> --preset prop-sheet [--manifest m.json]
                     [--asset-id abc123]

PROMOTE_DIR resolution order: $PROMOTE_DIR, repo .env, /mnt/d/PROMOTED.
Prints the two promoted paths on success.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_env_file() -> dict:
    env = {}
    p = REPO_ROOT / ".env"
    if p.is_file():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def promote_dir() -> Path:
    return Path(os.environ.get("PROMOTE_DIR")
                or read_env_file().get("PROMOTE_DIR")
                or "/mnt/d/PROMOTED")


def atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.tmp-{os.getpid()}"
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def promote(image: Path, preset: str, manifest: Path | None,
            asset_id: str | None) -> tuple[Path, Path]:
    if not image.is_file():
        raise SystemExit(f"image not found: {image}")
    asset_id = asset_id or uuid.uuid4().hex[:12]
    target = promote_dir() / preset
    img_dest = target / f"{asset_id}.png"
    man_dest = target / f"{asset_id}.json"

    if manifest:
        if not manifest.is_file():
            raise SystemExit(f"manifest not found: {manifest}")
        atomic_copy(manifest, man_dest)
    else:
        # Standalone use (no cockpit manifest): record the minimum honest facts.
        stub = {
            "asset_id": asset_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "preset_id": preset,
            "provider": "comfyui-local",
            "note": "promoted via scripts/promote.py without a full cockpit manifest",
            "source_image": str(image),
        }
        tmp = man_dest.parent / f".{man_dest.name}.tmp-{os.getpid()}"
        man_dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(stub, indent=2))
        os.replace(tmp, man_dest)

    atomic_copy(image, img_dest)
    return img_dest, man_dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--asset-id")
    a = ap.parse_args()
    img, man = promote(a.image, a.preset, a.manifest, a.asset_id)
    print(img)
    print(man)
    return 0


if __name__ == "__main__":
    sys.exit(main())
