#!/usr/bin/env python3
"""Hunyuan3D-Paint as its own stage: mesh + reference image -> textured GLB
(law 16, the TEXTURE verb).

Runs inside tools/3d/envs/hunyuan/.venv, exactly as run_hunyuan.py does —
this is that script's paint stage carved out so it can be pointed at ANY
mesh, not only the shape stage's own output. cwd flips to the vendor
checkout so the repo's relative cfg/ckpt paths work.

  envs/hunyuan/.venv/bin/python paint_hunyuan.py --mesh m.glb --image ref.png --out outdir/

**The input mesh is stripped to geometry first.** Two reasons, both measured
(bench 2026-08-30): Paint's own remesh path loads the GLB with pymeshlab,
whose STB reader cannot decode a TRELLIS.2 texture (`EXT_texture_webp`) and
refuses the whole file; and Paint discards the input's materials anyway —
it repaints from scratch. Stripping is not a loss, it is what the verb means.

**Paint remeshes to 40,000 faces before painting** (upstream
`hy3dpaint/utils/simplify_mesh_utils.py`, `target_count=40000` — a default
its caller never overrides), then re-unwraps with xatlas. So the child
carries Paint's topology and UVs, never its parent's. `--no-remesh` skips
the simplify for a mesh already at or below budget; xatlas still re-unwraps
(there is no keep-the-parent-UVs path in the pipeline).

**The GLB embeds the full PBR set.** Paint bakes albedo + metallic +
roughness; upstream's OBJ→GLB step is bpy (no cp310 wheel), and a plain
trimesh round-trip keeps the albedo only, which is a packaging bug.
`obj_to_glb_pbr` packs metallic/roughness into one metallicRoughness
texture (glTF: G=roughness, B=metallic) on a trimesh PBRMaterial, so
TEXTURED on the cockpit's facts line means what it says. run_hunyuan.py
imports the same helper for its own paint stage.

Determinism: the multiview diffusion hard-seeds itself (seed_everything(0)
in hy3dpaint/utils/multiview_utils.py), so the same mesh and the same
reference give the same child — the TEXTURE kind is seedless, like
decimate, and the runner refuses a seed by name.
"""
import argparse
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


def obj_to_glb_pbr(obj_path: Path, glb_path: Path) -> dict:
    """Paint's OBJ + texture sidecars -> one GLB with the PBR set embedded.

    Paint writes `<stem>.jpg` (albedo) beside `<stem>_metallic.jpg` and
    `<stem>_roughness.jpg`. glTF wants the last two as ONE map — roughness
    in G, metallic in B — so they are packed here and the material carries
    `metallicRoughnessTexture`. A run whose sidecars are missing still gets
    its albedo GLB and says so, rather than failing the take over the maps.
    """
    import numpy as np
    import trimesh
    from PIL import Image

    mesh = trimesh.load(str(obj_path), force="mesh")
    stem = str(obj_path.with_suffix(""))
    albedo = Image.open(f"{stem}.jpg").convert("RGB")
    note = {"albedo": True, "metallic_roughness": False}
    metallic_p, rough_p = Path(f"{stem}_metallic.jpg"), Path(f"{stem}_roughness.jpg")
    mr_img = None
    if metallic_p.is_file() and rough_p.is_file():
        met = np.asarray(Image.open(metallic_p).convert("L"))
        rough = np.asarray(Image.open(rough_p).convert("L"))
        if met.shape != rough.shape:
            rough = np.asarray(Image.open(rough_p).convert("L")
                               .resize((met.shape[1], met.shape[0])))
        mr = np.zeros((*met.shape, 3), dtype=np.uint8)
        mr[..., 1] = rough                       # glTF: G = roughness
        mr[..., 2] = met                         # glTF: B = metallic
        mr_img = Image.fromarray(mr)
        note["metallic_roughness"] = True
    mat = trimesh.visual.material.PBRMaterial(
        baseColorTexture=albedo, metallicRoughnessTexture=mr_img)
    mesh.visual = trimesh.visual.TextureVisuals(uv=mesh.visual.uv, material=mat)
    mesh.export(str(glb_path))
    return note


def strip_to_geometry(src: Path, dst: Path) -> dict:
    """Geometry only, materials dropped — see the module docstring for why.
    trimesh reads the webp-textured GLB pymeshlab cannot; concatenating a
    Scene mirrors what Paint's own loaders do downstream."""
    import trimesh
    mesh = trimesh.load(str(src), force="mesh")
    out = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces,
                          process=False)
    out.export(str(dst))
    return {"triangles": int(len(mesh.faces)),
            "vertices": int(len(mesh.vertices))}


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mesh", required=True, help="input mesh (GLB, any engine's)")
    ap.add_argument("--image", required=True,
                    help="reference image (RGBA cutout preferred)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--max-num-view", type=int, default=6, choices=range(6, 10))
    ap.add_argument("--resolution", type=int, default=512, choices=[512, 768])
    ap.add_argument("--no-remesh", dest="remesh", action="store_false",
                    help="skip Paint's 40k simplify (xatlas still re-unwraps)")
    return ap


def load_paint(max_num_view: int, resolution: int):
    from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline

    conf = Hunyuan3DPaintConfig(max_num_view, resolution)
    conf.realesrgan_ckpt_path = "hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
    conf.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
    conf.custom_pipeline = "hy3dpaint/hunyuanpaintpbr"
    return Hunyuan3DPaintPipeline(conf)


def take(paint_pipe, args, load_s: float, resident: bool = False) -> None:
    """One strip → paint → convert take over an already-loaded Paint pipe.
    Cold runs and warm serve requests share this body — textured.glb +
    paint-run.json land identically either way. The bench's warm
    repeat was 43.0 vs 43.7 s over one resident pipe: identical work, no
    creep."""
    import torch

    report = {
        "engine": "hunyuan3d-paint-2.1",
        "mesh": str(Path(args.mesh).resolve()),
        "image": str(Path(args.image).resolve()),
        "resident": resident,
        "params": {"max_num_view": args.max_num_view,
                   "resolution": args.resolution,
                   "remesh": args.remesh},
        "timings_s": {"paint_load": round(load_s, 2)},
        "peak_vram_mb": {},
        "versions": {
            "torch": torch.__version__,
            "weights": "tencent/Hunyuan3D-2.1@0b94677654c57bb9a6b6845cd7b704ccf551d327",
        },
    }
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    mesh_path = Path(args.mesh).resolve()
    image_path = str(Path(args.image).resolve())

    t0 = time.time()
    source = out / "paint-input.glb"
    report["source"] = strip_to_geometry(mesh_path, source)
    report["timings_s"]["strip"] = round(time.time() - t0, 2)

    torch.cuda.reset_peak_memory_stats()
    t2 = time.time()
    # save_mesh writes OBJ content regardless of extension; the GLB is built
    # below with the full PBR set rather than by upstream's bpy path.
    obj_path = paint_pipe(
        mesh_path=str(source),
        image_path=image_path,
        output_mesh_path=str(out / "textured.obj"),
        use_remesh=args.remesh,
        save_glb=False,
    )
    report["timings_s"]["paint"] = round(time.time() - t2, 2)
    report["peak_vram_mb"]["paint"] = round(torch.cuda.max_memory_allocated() / 2**20)

    t3 = time.time()
    glb_path = out / "textured.glb"
    report["pbr"] = obj_to_glb_pbr(Path(obj_path), glb_path)
    report["timings_s"]["convert"] = round(time.time() - t3, 2)
    report["glb"] = str(glb_path)

    report["timings_s"]["total"] = round(load_s + time.time() - t0, 2)
    (out / "paint-run.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["timings_s"] | {"peak_vram_mb": report["peak_vram_mb"]},
                     indent=2))


def main() -> int:
    if "--serve" in sys.argv:
        # Keep-alive mode, spoken to by runner.py: the paint pipe loads once
        # (10–16 s it never pays again) and holds 7.7 GB idle, flat across
        # takes (bench-measured). Paint's views/resolution are baked into the
        # config at load, so a serve child is pinned to the default 6 views @
        # 512, the only pair anything sends today (the runner would key a
        # non-default child separately).
        sp = argparse.ArgumentParser()
        sp.add_argument("--serve", action="store_true")
        sp.add_argument("--socket", required=True)
        sargs = sp.parse_args()
        os.chdir(VENDOR)
        from torchvision_fix import apply_fix
        apply_fix()
        t0 = time.time()
        paint_pipe = load_paint(6, 512)
        load_s = round(time.time() - t0, 1)
        import gc
        import torch
        import serve_mode

        def one(argv: list[str]) -> None:
            args = make_parser().parse_args(argv)
            if (args.max_num_view, args.resolution) != (6, 512):
                raise RuntimeError("this resident Paint child is built for "
                                   "6 views @ 512 — run a non-default take cold")
            try:
                take(paint_pipe, args, 0.0, resident=True)
            finally:
                gc.collect()
                torch.cuda.empty_cache()

        return serve_mode.serve("hunyuan-paint", sargs.socket, one,
                                {"load_s": load_s})

    args = make_parser().parse_args()
    # resolve user paths before the chdir — cfg/ckpt paths are cwd-relative
    args.mesh = str(Path(args.mesh).resolve())
    args.image = str(Path(args.image).resolve())
    args.out = str(Path(args.out).resolve())
    os.chdir(VENDOR)

    # basicsr 1.4.2 vs torchvision 0.20 import break — repo ships the official fix.
    from torchvision_fix import apply_fix
    apply_fix()

    t0 = time.time()
    paint_pipe = load_paint(args.max_num_view, args.resolution)
    take(paint_pipe, args, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
