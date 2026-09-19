#!/usr/bin/env python3
"""TRELLIS.2 re-extraction: generate once, extract many (law 16).

`run_trellis.py --save-fields` keeps the six arrays `o_voxel.postprocess.to_glb`
reads. This script re-runs that one call at another `decimation_target` /
`texture_size` and never loads the 4B pipeline: no weights, no diffusion, no
DINOv3 — just the bake. That is the whole point of the kind. A fresh take is
~60–105 s of load plus 23–119 s of generation before extraction even starts;
this is extraction alone.

Why it is the better DECIMATE for a TRELLIS mesh: gltfpack collapses triangles
in the finished GLB, and the machine UV atlas locks the seams — measured
2026-08-29, a TRELLIS mesh floors at ~8–10% of its source triangles before the
texture shreds. Re-extraction goes back to the voxel field and remeshes,
re-unwraps and re-bakes from scratch, so it has no such floor.

Runs in the same venv as run_trellis.py (envs/trellis2/.venv).

  envs/trellis2/.venv/bin/python extract_trellis.py \
      --fields fields.npz --out outdir/ --decimation-target 50000
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

os.environ.setdefault("HF_HOME", os.path.expanduser("~/models/hf"))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# nvdiffrast JIT-compiles its torch plugin on first use — needs the lane
# toolchain, exactly as the generate entry does.
os.environ.setdefault("CUDA_HOME", str(HERE / "cuda124"))
os.environ["PATH"] = str(HERE / "cuda124" / "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("TORCH_CUDA_ARCH_LIST", "8.9")

FORMAT = "trellis2-fields-v1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fields", required=True, help="fields.npz from --save-fields")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--texture-size", type=int, default=2048)
    ap.add_argument("--decimation-target", type=int, default=1_000_000)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import torch
    import o_voxel

    t0 = time.time()
    fields = np.load(args.fields)
    meta = json.loads(str(fields["meta"]))
    if meta.get("format") != FORMAT:
        # A field set this script cannot read is a refusal in words, not a
        # crash three frames into a bake.
        print(f"fields file is {meta.get('format')!r}, not {FORMAT!r} — it was "
              f"written by a different version of run_trellis.py and this "
              f"extractor cannot read it", file=sys.stderr)
        return 2
    layout = {k: slice(a, b) for k, (a, b) in meta["layout"].items()}
    dev = "cuda"
    # The sidecar stores narrow (coords int16 — a 1024 grid indexes to 1023;
    # attrs float16 — channels that bake to 8 bits), which halves the file.
    # to_glb wants the wide types, so the widening happens here and the
    # storage decision never reaches the bake.
    vertices = torch.from_numpy(fields["vertices"]).float().to(dev)
    faces = torch.from_numpy(fields["faces"]).int().to(dev)
    attrs = torch.from_numpy(fields["attrs"]).float().to(dev)
    coords = torch.from_numpy(fields["coords"]).int().to(dev)
    voxel_size = fields["voxel_size"].item()
    aabb = fields["aabb"].tolist()
    load_s = time.time() - t0

    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    glb = o_voxel.postprocess.to_glb(
        vertices=vertices,
        faces=faces,
        attr_volume=attrs,
        coords=coords,
        attr_layout=layout,
        voxel_size=voxel_size,
        aabb=aabb,
        decimation_target=args.decimation_target,
        texture_size=args.texture_size,
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=True,
    )
    extract_s = time.time() - t1
    extract_vram = torch.cuda.max_memory_allocated()

    glb_path = out / "trellis.glb"
    glb.export(str(glb_path), extension_webp=True)

    report = {
        "engine": "trellis2",
        "kind": "extract",
        "fields": str(Path(args.fields).resolve()),
        "glb": str(glb_path.resolve()),
        "params": {
            "texture_size": args.texture_size,
            "decimation_target": args.decimation_target,
        },
        # the parent take's own facts, carried so a re-extracted child can
        # say what it came from without a lineage lookup
        "source": {k: meta.get(k) for k in ("pipeline_type", "seed", "image", "weights")},
        "timings_s": {
            "load_fields": round(load_s, 2),
            "extract_glb": round(extract_s, 2),
            "total": round(time.time() - t0, 2),
        },
        "peak_vram_mb": {"extract_glb": round(extract_vram / 2**20)},
        "versions": {"torch": torch.__version__},
    }
    (out / "trellis-extract.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["timings_s"] | {"peak_vram_mb": report["peak_vram_mb"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
