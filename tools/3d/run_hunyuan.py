#!/usr/bin/env python3
"""Hunyuan3D 2.1 image -> textured GLB runner (the 3D lane, scripts-first).

Runs inside tools/3d/envs/hunyuan/.venv (torch 2.5.1+cu124). Two sequential
stages in one process — shape (~10 GB VRAM) then paint (~21 GB); the shape
pipeline is freed before paint loads (24 GB card: never co-resident).
cwd flips to the vendor checkout so the repo's relative cfg/ckpt paths work.

  envs/hunyuan/.venv/bin/python run_hunyuan.py --image cutout.png --out outdir/

`--serve --socket <path>` keeps the process resident between
takes — shape pre-loaded, still one stage at a time — and takes arrive as
socket requests from runner.py; see serve_mode.py for the protocol.
"""
import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "Hunyuan3D-2.1"

os.environ.setdefault("HF_HOME", os.path.expanduser("~/models/hf"))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(VENDOR / "hy3dshape"))
sys.path.insert(0, str(VENDOR / "hy3dpaint"))


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True, help="input PNG (RGBA cutout preferred)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--max-num-view", type=int, default=6, choices=range(6, 10))
    ap.add_argument("--resolution", type=int, default=512, choices=[512, 768])
    # The grid the occupancy field is sampled on before marching cubes
    # — Hunyuan's own "resolution", and the shape stage's only real quality
    # tier. Upstream's default is 384; the choices are the product's offered
    # set, not the library's full 16-512 range (the low end makes a blob and
    # the surface has no honest sentence for it).
    ap.add_argument("--octree-resolution", type=int, default=384,
                    choices=[256, 384, 512])
    ap.add_argument("--seed", type=int, default=424242)
    ap.add_argument("--skip-paint", action="store_true", help="shape stage only")
    return ap


class Stages:
    """One stage resident at a time (both-resident peaked at 20.7 GB on
    an otherwise-empty card, so stage-swap stays the safe shape). `ensure_*`
    frees the other stage before loading, records what the swap cost, and
    costs nothing when the stage is already up — which is what makes a
    shape-only fan-out (--skip-paint) the warm cell the bench measured at
    35–39 s/take."""

    def __init__(self) -> None:
        self.shape = None
        self.paint = None
        self.paint_conf: tuple | None = None    # (max_num_view, resolution)

    def _free(self, name: str) -> None:
        if getattr(self, name) is not None:
            setattr(self, name, None)
            if name == "paint":
                self.paint_conf = None
            gc.collect()
            import torch
            torch.cuda.empty_cache()

    def ensure_shape(self) -> tuple:
        if self.shape is not None:
            return self.shape, 0.0
        self._free("paint")
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
        # hy3dgen's loader ignores the HF cache (own ~/.cache/hy3dgen tree); passing the
        # absolute HF snapshot path short-circuits it (os.path.join drops its base_dir).
        from huggingface_hub import snapshot_download
        snap = snapshot_download("tencent/Hunyuan3D-2.1", local_files_only=True)
        t0 = time.time()
        self.shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(snap)
        return self.shape, round(time.time() - t0, 2)

    def ensure_paint(self, max_num_view: int, resolution: int) -> tuple:
        # Paint's views/resolution are baked into its config at load; a
        # resident pipe built for another pair must reload, never silently
        # answer with the wrong one.
        if self.paint is not None and self.paint_conf != (max_num_view, resolution):
            self._free("paint")
        if self.paint is not None:
            return self.paint, 0.0
        self._free("shape")
        from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig
        t0 = time.time()
        conf = Hunyuan3DPaintConfig(max_num_view, resolution)
        conf.realesrgan_ckpt_path = "hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
        conf.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
        conf.custom_pipeline = "hy3dpaint/hunyuanpaintpbr"
        self.paint = Hunyuan3DPaintPipeline(conf)
        self.paint_conf = (max_num_view, resolution)
        return self.paint, round(time.time() - t0, 2)


def take(stages: Stages, args, resident: bool = False) -> None:
    """One shape(+paint) take over whatever `stages` holds. Cold runs and warm
    serve requests share this body, so hunyuan.glb + hunyuan-run.json land
    identically either way; the `*_load` timings say what THIS take paid —
    0 for a stage that was already resident."""
    import torch
    from PIL import Image

    t0 = time.time()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    image_path = str(Path(args.image).resolve())

    report = {
        "engine": "hunyuan3d-2.1",
        "image": image_path,
        "resident": resident,
        "params": {
            "max_num_view": args.max_num_view,
            "resolution": args.resolution,
            "octree_resolution": args.octree_resolution,
            "seed": args.seed,
        },
        "timings_s": {},
        "peak_vram_mb": {},
        "versions": {
            "torch": torch.__version__,
            "weights": "tencent/Hunyuan3D-2.1@0b94677654c57bb9a6b6845cd7b704ccf551d327",
        },
    }

    # ---- stage 1: shape ----
    shape_pipe, load_s = stages.ensure_shape()
    report["timings_s"]["shape_load"] = load_s

    image = Image.open(image_path)
    if image.mode == "RGB":  # funnel always sends RGBA; this is a fallback
        from hy3dshape.rembg import BackgroundRemover
        image = BackgroundRemover()(image)

    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    try:
        gen = torch.Generator().manual_seed(args.seed)
        mesh = shape_pipe(image=image, generator=gen,
                          octree_resolution=args.octree_resolution)[0]
    except TypeError:
        # the seed is the kwarg this pipeline has refused before; the octree
        # grid rides the same fallback so a refusal downgrades to the
        # library's own default rather than failing the take, and the report
        # says which handle went unread rather than claiming both landed
        report["params"]["seed"] = None
        report["params"]["octree_resolution"] = None
        mesh = shape_pipe(image=image)[0]
    report["timings_s"]["shape_generate"] = round(time.time() - t1, 2)
    report["peak_vram_mb"]["shape"] = round(torch.cuda.max_memory_allocated() / 2**20)

    shape_glb = out / "hunyuan-shape.glb"
    mesh.export(str(shape_glb))
    report["shape_glb"] = str(shape_glb)

    # drop OUR references before the stage swap — with `shape_pipe` still
    # bound here, Stages._free("shape") would free nothing and both stages
    # would grind co-resident at the card's ceiling (observed live on a
    # warm test take)
    del mesh, shape_pipe
    gc.collect()
    torch.cuda.empty_cache()

    # ---- stage 2: paint (PBR texture) ----
    if not args.skip_paint:
        paint_pipe, paint_load_s = stages.ensure_paint(args.max_num_view,
                                                       args.resolution)
        report["timings_s"]["paint_load"] = paint_load_s

        torch.cuda.reset_peak_memory_stats()
        t3 = time.time()
        # save_mesh writes OBJ content regardless of extension; upstream's OBJ->GLB step
        # is bpy (no cp310 wheel exists) — so take the OBJ and convert with trimesh.
        obj_path = paint_pipe(
            mesh_path=str(shape_glb),
            image_path=image_path,
            output_mesh_path=str(out / "hunyuan-textured.obj"),
            save_glb=False,
        )
        report["timings_s"]["paint"] = round(time.time() - t3, 2)
        report["peak_vram_mb"]["paint"] = round(torch.cuda.max_memory_allocated() / 2**20)
        # The shared PBR embed: albedo plus the packed metallic/
        # roughness map, so the GLB carries what Paint actually baked instead
        # of the albedo alone (a packaging bug).
        from paint_hunyuan import obj_to_glb_pbr
        glb_path = out / "hunyuan.glb"
        report["pbr"] = obj_to_glb_pbr(Path(obj_path), glb_path)
        report["glb"] = str(glb_path)

    report["timings_s"]["total"] = round(time.time() - t0, 2)
    (out / "hunyuan-run.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["timings_s"] | {"peak_vram_mb": report["peak_vram_mb"]}, indent=2))


def main() -> int:
    if "--serve" in sys.argv:
        # Keep-alive mode, spoken to by runner.py. The child holds ONE
        # stage at a time; a full generate still pays its swap, exactly
        # as the cold path does — the warm win is the shape fan-out and the
        # boot it never pays again. Shape is pre-loaded so the ready signal
        # means "a take starts now", not "a load starts now".
        sp = argparse.ArgumentParser()
        sp.add_argument("--serve", action="store_true")
        sp.add_argument("--socket", required=True)
        sargs = sp.parse_args()
        os.chdir(VENDOR)
        from torchvision_fix import apply_fix
        apply_fix()
        stages = Stages()
        t0 = time.time()
        stages.ensure_shape()
        load_s = round(time.time() - t0, 1)
        import torch
        import serve_mode

        def one(argv: list[str]) -> None:
            try:
                take(stages, make_parser().parse_args(argv), resident=True)
            finally:
                gc.collect()
                torch.cuda.empty_cache()

        return serve_mode.serve("hunyuan", sargs.socket, one, {"load_s": load_s})

    args = make_parser().parse_args()
    # resolve user paths before the chdir — the repo's cfg/ckpt paths are cwd-relative
    args.out = str(Path(args.out).resolve())
    args.image = str(Path(args.image).resolve())
    os.chdir(VENDOR)

    # basicsr 1.4.2 vs torchvision 0.20 import break — repo ships the official fix.
    from torchvision_fix import apply_fix
    apply_fix()

    take(Stages(), args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
