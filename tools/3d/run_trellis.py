#!/usr/bin/env python3
"""TRELLIS.2 image -> PBR GLB runner (the 3D lane, scripts-first).

Runs inside tools/3d/envs/trellis2/.venv (torch 2.6.0+cu124, flash-attn 2.7.3).
The pipeline loads from tools/3d/trellis2-pipeline/ — a local copy of the HF
snapshot config with rembg pointed at ZhengPeng7/BiRefNet (ungated, MIT)
instead of the auto-gated briaai/RMBG-2.0; ckpts is a symlink into the HF
cache. Feed RGBA cutouts (the funnel does) and rembg never runs at all.

  envs/trellis2/.venv/bin/python run_trellis.py --image cutout.png --out outdir/
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "TRELLIS.2"
PIPE_DIR = HERE / "trellis2-pipeline"

os.environ.setdefault("HF_HOME", os.path.expanduser("~/models/hf"))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# nvdiffrast JIT-compiles its torch plugin on first use — needs the lane toolchain.
os.environ.setdefault("CUDA_HOME", str(HERE / "cuda124"))
os.environ["PATH"] = str(HERE / "cuda124" / "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("TORCH_CUDA_ARCH_LIST", "8.9")
sys.path.insert(0, str(VENDOR))


def ensure_token() -> None:
    """DINOv3 is HF-gated; make the CLI token visible under the custom HF_HOME."""
    if os.environ.get("HF_TOKEN"):
        return
    for p in (Path.home() / ".cache/huggingface/token", Path(os.environ["HF_HOME"]) / "token"):
        if p.is_file():
            os.environ["HF_TOKEN"] = p.read_text().strip()
            return


def ensure_pipeline_dir() -> None:
    from huggingface_hub import snapshot_download

    snap = Path(snapshot_download("microsoft/TRELLIS.2-4B", local_files_only=True))
    link = PIPE_DIR / "ckpts"
    if link.resolve() != (snap / "ckpts").resolve():
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(snap / "ckpts")


def save_fields(dst: Path, mesh, aabb: list, args) -> Path:
    """The six inputs `o_voxel.postprocess.to_glb` reads, written
    beside the GLB so re-extraction is a 30–45 s job instead of a whole
    take. Generate once, extract many.

    `vertices`/`faces` are saved AFTER `simplify()` — the arrays to_glb was
    actually handed, so a re-extraction starts from the same mesh and only
    the target moves. `layout` is a dict of slices, which npz has no type
    for, so it travels as JSON beside the arrays; `aabb` travels because
    this script chooses it, and a sidecar that assumed it would break the
    day it changes. The provenance block is what makes a loose .npz
    readable: which weights, which pipeline type, which seed.
    """
    import numpy as np

    layout = {k: [v.start, v.stop] for k, v in mesh.layout.items()}
    np.savez_compressed(
        dst,
        vertices=mesh.vertices.detach().cpu().numpy(),
        faces=mesh.faces.detach().cpu().numpy(),
        # narrowed on the way out and widened on the way in
        # (extract_trellis.py): `coords` indexes a 1024 grid, so int16 holds
        # it with room for 1536; `attrs` are colour/metallic/roughness/alpha
        # in [0, 1.05] that bake to 8-bit channels, so float16 is below the
        # noise floor of the destination. Measured 2026-08-29 on the lantern:
        # 244.6 MiB raw -> 133.6 compressed -> 92.1 narrowed + compressed,
        # and the re-extracted mesh is the same one (14,689 vs 14,625
        # triangles at a 15,000 ask — the remesher, not the precision).
        # The SIZE is per-prop, not per-triangle-count: a door handle of
        # comparable density measured 21.4 MiB, because the field is sized by
        # occupied voxels and a thin object occupies few.
        attrs=mesh.attrs.detach().cpu().numpy().astype(np.float16),
        coords=mesh.coords.detach().cpu().numpy().astype(np.int16),
        voxel_size=np.asarray(mesh.voxel_size),
        aabb=np.asarray(aabb, dtype=np.float32),
        meta=np.asarray(json.dumps({
            "layout": layout,
            "pipeline_type": args.pipeline_type,
            "seed": args.seed,
            "image": str(Path(args.image).resolve()),
            "weights": "microsoft/TRELLIS.2-4B@af44b45f2e35a493886929c6d786e563ec68364d",
            "format": "trellis2-fields-v1",
        })),
    )
    return dst


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True, help="input PNG (RGBA cutout preferred)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--pipeline-type", default="1024_cascade",
                    choices=["512", "1024", "1024_cascade"])  # 1536_cascade excluded: unstable in 24 GB
    ap.add_argument("--texture-size", type=int, default=2048)  # 4096 is the repo default; extraction cost doubles
    ap.add_argument("--decimation-target", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=424242)
    # The extraction inputs, kept beside the GLB so re-extraction can
    # re-run to_glb at another target without reloading the 4B pipeline.
    # ON by default: the artist cannot know at generate
    # time whether a lower LOD is wanted later — that decision comes after
    # looking at the mesh — and 21–92 MiB (measured across two props) is the
    # cheaper half of that bet against 134 s of GPU.
    ap.add_argument("--no-save-fields", dest="save_fields", action="store_false",
                    help="skip fields.npz — no re-extraction from this take")
    return ap


def load_pipeline():
    ensure_token()
    ensure_pipeline_dir()
    from trellis2.pipelines import Trellis2ImageTo3DPipeline

    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(PIPE_DIR))
    pipeline.cuda()
    return pipeline


def take(pipeline, args, load_s: float, resident: bool = False) -> None:
    """One take over an already-loaded pipeline. The whole cold run and every
    warm serve request come through here, so the outputs cannot drift:
    trellis.glb + trellis-run.json in --out, exactly as ever. `pipeline_type`,
    seed and the to_glb knobs are per-take arguments — one resident pipeline
    serves them all."""
    import torch
    from PIL import Image
    import o_voxel

    t0 = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    image = Image.open(args.image)
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    mesh = pipeline.run(image, seed=args.seed, pipeline_type=args.pipeline_type)[0]
    gen_s = time.time() - t1
    gen_vram = torch.cuda.max_memory_allocated()

    mesh.simplify(16777216)  # nvdiffrast limit (upstream example)
    aabb = [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]
    torch.cuda.reset_peak_memory_stats()
    t2 = time.time()
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=aabb,
        decimation_target=args.decimation_target,
        texture_size=args.texture_size,
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=True,
    )
    extract_s = time.time() - t2
    extract_vram = torch.cuda.max_memory_allocated()

    fields_path = None
    if args.save_fields:
        fields_path = save_fields(out / "fields.npz", mesh, aabb, args)

    glb_path = out / "trellis.glb"
    glb.export(str(glb_path), extension_webp=True)

    report = {
        "engine": "trellis2",
        "image": str(Path(args.image).resolve()),
        "glb": str(glb_path.resolve()),
        "fields": str(fields_path.resolve()) if fields_path else None,
        # a warm take pays no load — its resident child already did, once
        # (26–73 s observed, pagecache-hostage; the runner records the span)
        "resident": resident,
        "params": {
            "pipeline_type": args.pipeline_type,
            "texture_size": args.texture_size,
            "decimation_target": args.decimation_target,
            "seed": args.seed,
        },
        "timings_s": {
            "load": round(load_s, 2),
            "generate": round(gen_s, 2),
            "extract_glb": round(extract_s, 2),
            "total": round(load_s + time.time() - t0, 2),
        },
        "peak_vram_mb": {
            "generate": round(gen_vram / 2**20),
            "extract_glb": round(extract_vram / 2**20),
        },
        "versions": {
            "torch": torch.__version__,
            "weights": "microsoft/TRELLIS.2-4B@af44b45f2e35a493886929c6d786e563ec68364d",
        },
    }
    (out / "trellis-run.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["timings_s"] | {"peak_vram_mb": report["peak_vram_mb"]}, indent=2))


def main() -> int:
    if "--serve" in sys.argv:
        # Keep-alive mode: load once, then one take per socket
        # request, spoken to by runner.py. The bench proved the shape: three
        # takes over one resident pipeline, idle at 2.75 GB device, no creep.
        sp = argparse.ArgumentParser()
        sp.add_argument("--serve", action="store_true")
        sp.add_argument("--socket", required=True)
        sargs = sp.parse_args()
        t0 = time.time()
        pipeline = load_pipeline()
        load_s = round(time.time() - t0, 1)
        import gc
        import torch
        import serve_mode

        def one(argv: list[str]) -> None:
            try:
                take(pipeline, make_parser().parse_args(argv), 0.0, resident=True)
            finally:
                # the hygiene the bench ran between takes — what kept the
                # idle numbers flat across three takes
                gc.collect()
                torch.cuda.empty_cache()

        return serve_mode.serve("trellis2", sargs.socket, one,
                                {"load_s": load_s})

    args = make_parser().parse_args()
    t0 = time.time()
    pipeline = load_pipeline()
    take(pipeline, args, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
