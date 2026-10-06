#!/usr/bin/env python3
"""Prop funnel: promoted asset -> cutout -> both 3D engines -> validated GLBs + turntables.

Runs inside tools/3d/envs/funnel/.venv (torch-free). Engines run sequentially
as subprocesses inside their own venvs (one heavy GPU job at a time).

**Kinds (law 16).** A funnel run is one *kind* of step, and the kinds share
this file's tail rather than each growing its own:

    generate   image -> mesh, via `run_engine` in an engine's own venv
               (--image / an asset id is its input)
    decimate   mesh -> smaller mesh, and it has TWO engines because the
               input names which one:
                 --mesh <glb>    gltfpack's meshopt simplifier — a classic
                                 tool, no model, no GPU, under a second, and
                                 a TRELLIS mesh floors at ~8-10% of source
                 --fields <npz>  TRELLIS.2 re-extraction — to_glb re-run over
                                 the voxel field the take kept, on the card,
                                 ~12 s at 15k / ~46 s at 1M, and NO floor
    texture    mesh + reference image -> repainted mesh: Hunyuan3D-
               Paint over ANY mesh — the first kind with two parents. Takes
               --mesh <glb> AND --image <ref>; the reference goes through the
               same cutout() the generate kind uses. Paint remeshes its input
               to 40,000 faces and re-unwraps before painting, so the child
               carries Paint's topology, never its parent's (bench
               2026-08-30). The child's facts line states its own count.

`validate()` and `turntable()` take any GLB, so every kind's child
lands with the same measured facts and the same orbit — that is the seam law 16
asked for, and it is why a new verb costs an operator function and a row in
KINDS rather than a second script.

Input is a promoted asset id from $PROMOTE_DIR/<preset>/<asset_id>.{png,json}
(any manifest version, hash or slug naming; only the PNG is required, the rest is
best-effort lineage), or --image <path> for
a bench-source run (an engine verify run or a bench prop).

Working files stay in ext4 (tools/3d/runs/<name>/); final artifacts are
copied to $PROMOTE_DIR/_3d/<name>/ at the end (the promotion boundary):
  trellis.glb, hunyuan.glb, funnel.json, turntables/

Bench sheet: --sheet-dir accumulates bench-format cells (scripts/bench.py
--assemble-only renders the index.html; briefs.json lookup misses are fine).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _promote_dir() -> Path:
    """PROMOTE_DIR from the environment, then the repo .env, then the default:
    the same order as scripts/promote.py."""
    value = os.environ.get("PROMOTE_DIR")
    env_file = HERE.parents[1] / ".env"
    if not value and env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == "PROMOTE_DIR":
                value = val.strip()
    return Path(os.path.expanduser(value or "/mnt/d/PROMOTED"))


PROMOTED = _promote_dir()
RUNS = HERE / "runs"
ENGINES = {
    "trellis2": {
        "python": HERE / "envs/trellis2/.venv/bin/python",
        "runner": HERE / "run_trellis.py",
        "glb": "trellis.glb",
        "report": "trellis-run.json",
        "model_family": "TRELLIS.2-4B (MIT; NSCL render deps)",
    },
    "hunyuan": {
        "python": HERE / "envs/hunyuan/.venv/bin/python",
        "runner": HERE / "run_hunyuan.py",
        "glb": "hunyuan.glb",
        "report": "hunyuan-run.json",
        "model_family": "Hunyuan3D 2.1 (Tencent Community)",
    },
}

# What a re-extraction bakes at when nothing asks. It lives here
# rather than in the parser's `default=` because `--texture-size` serves
# two kinds — the re-bake and the generate-time knob — and only one of them
# has a funnel-side default: a generate flag left unset must reach the engine
# runner as ABSENT, so run_trellis.py's own default governs rather than this
# file quietly restating it.
EXTRACT_TEXTURE_SIZE = 2048

# Which engine runner takes each generate-time knob, and under which
# flag. This IS the routing rule — a knob names one engine, so sending
# TRELLIS.2 a paint camera count is impossible here rather than refused three
# processes later. Keys are `args` attribute names; the value is the flag the
# engine runner declares (`run_trellis.py` / `run_hunyuan.py` make_parser).
GENERATE_KNOBS: dict[str, tuple[str, str]] = {
    "pipeline_type": ("trellis2", "--pipeline-type"),
    "decimation_target": ("trellis2", "--decimation-target"),
    "texture_size": ("trellis2", "--texture-size"),
    "octree_resolution": ("hunyuan", "--octree-resolution"),
    "max_num_view": ("hunyuan", "--max-num-view"),
    "resolution": ("hunyuan", "--resolution"),
    "skip_paint": ("hunyuan", "--skip-paint"),
}


def generate_knobs(args) -> dict[str, list[str]]:
    """The knob flags this run asked for, split per engine runner. Only what
    was actually given travels: an unset flag stays absent so the engine's own
    declared default governs, rather than this file restating it and drifting
    from it (the hidden good default is the engine's)."""
    per: dict[str, list[str]] = {e: [] for e in ENGINES}
    for attr, (engine, flag) in GENERATE_KNOBS.items():
        v = getattr(args, attr, None)
        if v is None or v is False:
            continue
        per[engine] += [flag] if v is True else [flag, str(v)]
    return per


os.environ.setdefault("U2NET_HOME", os.path.expanduser("~/models/rembg"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_promoted(asset_id: str) -> tuple[Path, dict]:
    """Locate <asset_id>.png (+ manifest, any vintage) anywhere under PROMOTED except _3d/."""
    for preset_dir in sorted(PROMOTED.iterdir()):
        if not preset_dir.is_dir() or preset_dir.name == "_3d":
            continue
        png = preset_dir / f"{asset_id}.png"
        if png.is_file():
            manifest = {}
            mpath = preset_dir / f"{asset_id}.json"
            if mpath.is_file():
                try:
                    manifest = json.loads(mpath.read_text())
                except Exception as e:  # tolerate broken/legacy manifests
                    manifest = {"_manifest_read_error": str(e)}
            return png, manifest
    sys.exit(f"asset id {asset_id!r} not found under {PROMOTED} (excluding _3d/)")


def cutout(src_png: Path, dst_rgba: Path) -> dict:
    """RGBA via rembg birefnet-general; skipped when the source already has real alpha."""
    from PIL import Image

    img = Image.open(src_png)
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        img.save(dst_rgba)
        return {"method": "source-alpha", "note": "input already carried a meaningful alpha"}
    from rembg import new_session, remove

    t0 = time.time()
    out = remove(img.convert("RGB"), session=new_session("birefnet-general"))
    out.save(dst_rgba)
    return {
        "method": "rembg birefnet-general (INTERIM: util-cutout@v1 via ComfyUI replaces this when it lands)",
        "model_file": str(Path(os.environ["U2NET_HOME"]) / "birefnet-general.onnx"),
        "elapsed_s": round(time.time() - t0, 1),
    }


def comfy_quiet(force: bool, free: bool) -> None:
    """One-heavy-job rule: refuse to start while ComfyUI has work, optionally free its VRAM."""
    try:
        q = json.load(urllib.request.urlopen("http://localhost:8188/api/queue", timeout=3))
        busy = bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return  # ComfyUI down = GPU free
    if busy and not force:
        sys.exit("ComfyUI queue is not idle (one heavy job at a time) — retry later or pass --force")
    if free:
        req = urllib.request.Request(
            "http://localhost:8188/api/free",
            data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        print("[funnel] asked ComfyUI to unload models + free VRAM")


def serve_take(sock_path: str, identity: str, argv: list[str]) -> dict | None:
    """One take over a resident engine child: the socket
    runner.py handed us via --serve-socket, protocol in serve_mode.py.

    Returns None when the take NEVER STARTED (no socket, child gone, identity
    mismatch) — the caller runs cold, in words. Returns the child's outcome
    dict once the request was `accepted`: from that point the take is never
    re-run, so a child that dies mid-take fails the slot honestly instead of
    silently paying for the work twice."""
    import socket as socketlib
    try:
        s = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        s.connect(sock_path)
        f = s.makefile("rwb")
        f.write((json.dumps({"for": identity, "args": argv}) + "\n").encode())
        f.flush()
        first = json.loads(f.readline() or b"null")
    except (OSError, ValueError) as e:
        print(f"[funnel] {identity}: warm door failed before the take ({e}) "
              f"— running cold", flush=True)
        return None
    if not (isinstance(first, dict) and first.get("accepted")):
        why = (first or {}).get("refused") or (first or {}).get("error") or first
        print(f"[funnel] {identity}: warm door refused ({why}) — running cold",
              flush=True)
        return None
    try:
        line = f.readline()
        resp = json.loads(line) if line else None
    except (OSError, ValueError):
        resp = None
    if not isinstance(resp, dict):
        return {"ok": False, "exit_code": 1,
                "error": "the resident engine died mid-take"}
    return resp


def _engine_result(name: str, outdir: Path, exit_code: int, wall_s: float,
                   report_name: str, glb_name: str,
                   warm_error: str | None = None, resident: bool = False) -> dict:
    """The shared tail of every engine call, cold or warm: read the report
    the take wrote, check the GLB, say what failed in the engine's words."""
    result = {"engine": name, "exit_code": exit_code, "wall_s": round(wall_s, 1)}
    if resident:
        result["resident"] = True
    report = outdir / report_name
    if report.is_file():
        result["report"] = json.loads(report.read_text())
    glb = outdir / glb_name
    if exit_code == 0 and glb.is_file():
        result["glb"] = str(glb)
        result["glb_sha256"] = sha256(glb)
    else:
        result["error"] = warm_error or (
            f"exit {exit_code}, glb {'present' if glb.is_file() else 'missing'}")
    return result


def run_engine(name: str, rgba: Path, outdir: Path, extra: list[str],
               serve_socket: str | None = None) -> dict:
    e = ENGINES[name]
    argv = ["--image", str(rgba), "--out", str(outdir), *extra]
    t0 = time.time()
    resp = serve_take(serve_socket, name, argv) if serve_socket else None
    if resp is not None:
        print(f"[funnel] {name} (warm): {' '.join(argv)}", flush=True)
        return _engine_result(name, outdir, resp.get("exit_code", 1),
                              time.time() - t0, e["report"], e["glb"],
                              warm_error=resp.get("error"), resident=True)
    cmd = [str(e["python"]), str(e["runner"]), *argv]
    print(f"[funnel] {name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd)
    return _engine_result(name, outdir, proc.returncode, time.time() - t0,
                          e["report"], e["glb"])


GLTFPACK = HERE / "bin/gltfpack"


def decimate(src_glb: Path, outdir: Path, target_triangles: int,
             source_triangles: int, force: bool = False) -> dict:
    """The `decimate` kind (law 16): gltfpack's meshopt simplifier, run
    over a GLB. A classic tool — no model, no GPU, no weights to load — which
    is exactly why it is the seam's proof: seconds, on any mesh, from any
    source.

    **The target is a request the mesh may refuse, and that is deliberate.**
    gltfpack has an `-sa` flag that forces the exact triangle count
    "disregarding quality", and this verb keeps it off by default: on a
    TRELLIS.2 prop, forcing 952,326 → 13,309 produced shattered facets and a
    texture smeared into noise, while the same tool told to stop where the mesh
    stops gave 79,174 triangles with the ornament and texture fully intact. A
    triangle count that is exactly what was typed, on a mesh that has been
    ruined to get there, is not honesty — it is a true number attached to a
    dead artefact.

    So `force` is `-sa`, and it is **off unless a caller asks for it**.
    It is not a quality knob with a bad setting: it is a different question.
    Off asks "how small does this mesh go" and the child answers with the count
    it reached; on says "give me this number whatever it costs the surface",
    which is a real answer when the count is a hard budget and the artefact is
    going to be re-authored downstream anyway. The surface that offers it says
    which trade is being made, and the child records `forced` either
    way so the number is never read as the un-forced one.

    **What sets the floor** (measured 2026-08-29, and it is not the error
    budget — raising `-se` from 0.01 to 0.3 moved the result only 79,174 ->
    77,736): the mesh carries 664,517 vertices over 471,937 unique positions,
    a 1.41x split, because a machine UV atlas cuts the surface into charts and
    every chart boundary splits its vertices. A quadric simplifier locks
    attribute seams — collapsing them tears the texture mapping, which is
    precisely the smearing `-sa` produced. So ~8-10% of source is the floor
    TRELLIS.2's own atlas imposes, and no flag argues with it (`-sp`
    permissive and `-slb` lock-borders both changed nothing). Below the floor
    needs a re-unwrap and a re-bake, which is re-extraction (`extract()`) for a
    TRELLIS mesh — a fresh remesh from the voxel field, not a collapse of the
    baked result — and a retopo tool for anything else.

    The floor is per-artefact, not a law of the lane: Hunyuan's output, already
    remeshed to 40,000 faces by Paint with a 1.21x split, hits every target
    exactly (20,000 / 10,000 / 4,000, no forcing).

    So this verb answers what the mesh allows and the child's facts line
    prints its own count; a surface that wants to say "this mesh would not go
    below N" compares `report.target_triangles` against the child's
    `validation.triangles` (the cockpit projects the same pair off the job's
    own `params`, in `api_v4._mesh_view`).

    One more flag carries a decision: **`-noq` (no quantization)**, so the
    child is plain glTF with no required extension. Quantized output is ~26%
    smaller and both trimesh and three.js read it, but a decimated mesh exists
    to leave for a DCC (law 16: the cockpit is the front door, never the
    workbench), and `KHR_mesh_quantization` is one more thing that has to be
    supported on the far side.

    The ratio is computed here because gltfpack takes one: `validate()` on the
    input already measured the source's triangles, so nothing is guessed."""
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / "decimated.glb"
    ratio = min(1.0, max(1e-6, target_triangles / max(1, source_triangles)))
    cmd = [str(GLTFPACK), "-i", str(src_glb), "-o", str(dst),
           "-si", f"{ratio:.6f}", "-noq"] + (["-sa"] if force else [])
    print(f"[funnel] decimate: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print((proc.stdout or "") + (proc.stderr or ""), flush=True)
    result = {"engine": "gltfpack", "exit_code": proc.returncode,
              "wall_s": round(time.time() - t0, 1),
              "report": {"tool": "gltfpack 1.2 (meshoptimizer)",
                         "target_triangles": target_triangles,
                         "source_triangles": source_triangles,
                         "ratio": round(ratio, 6),
                         # what was asked of the simplifier, recorded beside
                         # what it reached: `forced` is the difference between
                         # "this is where the mesh stopped" and "this is the
                         # number, and the surface paid for it"
                         "forced": force,
                         "argv": cmd[1:],
                         "stdout": ((proc.stdout or "") + (proc.stderr or ""))[-2000:]}}
    if proc.returncode == 0 and dst.is_file():
        result["glb"] = str(dst)
        result["glb_sha256"] = sha256(dst)
    else:
        result["error"] = (f"gltfpack exited {proc.returncode}, glb "
                           f"{'present' if dst.is_file() else 'missing'}")
    return result


def extract(fields: Path, outdir: Path, decimation_target: int,
            texture_size: int) -> dict:
    """The `extract` kind (law 16): TRELLIS.2 re-extraction. It re-runs
    `o_voxel.postprocess.to_glb` over the voxel field a take already
    produced, at another triangle target or texture size.

    **Why this is not `decimate` wearing another engine's name.** gltfpack
    collapses triangles in the finished GLB and the machine UV atlas locks
    the seams, so a TRELLIS mesh floors at ~8-10% of its source triangles
    (see `decimate()`'s docstring for the measurement). Re-extraction goes
    back to the voxel field: it remeshes, re-unwraps with xatlas and re-bakes
    the texture from the attribute volume, so nothing is locked and there is
    no floor. It is the only way below one for a TRELLIS mesh.

    **What it costs**: the bake alone — no weights, no diffusion, no DINOv3.
    A fresh take pays ~60-105 s of pipeline load plus 23-119 s of generation
    before extraction even starts; this pays neither.

    **What it needs**: `fields.npz` from `run_trellis.py --save-fields`. Only
    a TRELLIS.2 take has one, and only if it was kept — an engine that cannot
    serve a mesh says so in words rather than being hidden (law 12).

    The GPU gate is the caller's, exactly as `generate`'s is: unlike
    `decimate`, this runs on the card and takes its turn under the
    one-heavy-job law."""
    outdir.mkdir(parents=True, exist_ok=True)
    e = ENGINES["trellis2"]
    cmd = [str(e["python"]), str(HERE / "extract_trellis.py"),
           "--fields", str(fields), "--out", str(outdir),
           "--decimation-target", str(decimation_target),
           "--texture-size", str(texture_size)]
    print(f"[funnel] extract: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd)
    result = {"engine": "trellis2", "exit_code": proc.returncode,
              "wall_s": round(time.time() - t0, 1)}
    report = outdir / "trellis-extract.json"
    if report.is_file():
        rep = json.loads(report.read_text())
        # the same pair `decimate` records: what was asked, beside what the
        # child reached — so one facts line serves both routes to a smaller
        # mesh (the cockpit's `asked_triangles`)
        result["report"] = {"tool": "o_voxel.postprocess.to_glb (TRELLIS.2)",
                            "target_triangles": decimation_target,
                            "texture_size": texture_size,
                            # re-extraction has no seam floor to refuse with,
                            # so nothing is ever forced here
                            "forced": False,
                            "source": rep.get("source"),
                            "timings_s": rep.get("timings_s"),
                            "peak_vram_mb": rep.get("peak_vram_mb")}
    glb = outdir / "trellis.glb"
    if proc.returncode == 0 and glb.is_file():
        result["glb"] = str(glb)
        result["glb_sha256"] = sha256(glb)
    else:
        result["error"] = (f"extract_trellis.py exited {proc.returncode}, glb "
                           f"{'present' if glb.is_file() else 'missing'}")
    return result


def texture(mesh: Path, rgba: Path, outdir: Path,
            serve_socket: str | None = None) -> dict:
    """The `texture` kind (law 16): Hunyuan3D-Paint over any mesh, with a
    reference image driving the appearance — shape locked, appearance
    branching. The first kind with two parents.

    **What actually happens to the mesh** (measured 2026-08-30):
    paint_hunyuan.py strips it to geometry (Paint discards
    materials anyway, and its pymeshlab loader cannot read a TRELLIS.2
    `EXT_texture_webp` GLB at all), Paint simplifies it to 40,000 faces
    (upstream's own hard default), xatlas re-unwraps, and the multiview
    diffusion paints albedo + metallic + roughness from the reference. So
    the child is an honest 40k retexture wearing Paint's topology and UVs —
    the facts line prints ITS count, and the parent is untouched, which is
    the seam's rule holding by construction.

    Deterministic by upstream's hand: the multiview stage seeds itself 0,
    so the same (mesh, reference) pair gives the same child — the kind is
    seedless, like decimate.

    On the card, heaviest step in the lane (13-14 GB peak): the GPU gate is
    the caller's, exactly as generate's is."""
    outdir.mkdir(parents=True, exist_ok=True)
    e = ENGINES["hunyuan"]
    argv = ["--mesh", str(mesh), "--image", str(rgba), "--out", str(outdir)]
    t0 = time.time()
    resp = serve_take(serve_socket, "hunyuan-paint", argv) if serve_socket else None
    if resp is not None:
        print(f"[funnel] texture (warm): {' '.join(argv)}", flush=True)
        exit_code, warm_error, resident = resp.get("exit_code", 1), resp.get("error"), True
    else:
        cmd = [str(e["python"]), str(HERE / "paint_hunyuan.py"), *argv]
        print(f"[funnel] texture: {' '.join(cmd)}", flush=True)
        proc = subprocess.run(cmd)
        exit_code, warm_error, resident = proc.returncode, None, False
    result = {"engine": "hunyuan", "exit_code": exit_code,
              "wall_s": round(time.time() - t0, 1)}
    if resident:
        result["resident"] = True
    report = outdir / "paint-run.json"
    if report.is_file():
        rep = json.loads(report.read_text())
        result["report"] = {"tool": "Hunyuan3D-Paint 2.1 (multiview PBR)",
                            "source": rep.get("source"),
                            "pbr": rep.get("pbr"),
                            "params": rep.get("params"),
                            "timings_s": rep.get("timings_s"),
                            "peak_vram_mb": rep.get("peak_vram_mb")}
    glb = outdir / "textured.glb"
    if exit_code == 0 and glb.is_file():
        result["glb"] = str(glb)
        result["glb_sha256"] = sha256(glb)
    else:
        result["error"] = warm_error or (
            f"paint_hunyuan.py exited {exit_code}, glb "
            f"{'present' if glb.is_file() else 'missing'}")
    return result


def validate(glb: Path) -> dict:
    """Recorded, not pass/fail — TRELLIS.2 O-Voxel legitimately produces open surfaces."""
    import numpy as np
    import trimesh

    scene = trimesh.load(str(glb))
    geoms = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    tex = any(
        getattr(getattr(g.visual, "material", None), "baseColorTexture", None) is not None
        for g in geoms
    )
    ext = (scene.extents if hasattr(scene, "extents") and scene.extents is not None
           else np.zeros(3))
    return {
        "geometries": len(geoms),
        "triangles": int(sum(len(g.faces) for g in geoms)),
        "vertices": int(sum(len(g.vertices) for g in geoms)),
        "watertight": [bool(g.is_watertight) for g in geoms],
        "texture_present": bool(tex),
        "bbox_extents": [round(float(x), 4) for x in ext],
        "file_mb": round(glb.stat().st_size / 2**20, 1),
    }


def turntable(glb: Path, outdir: Path, frames: int, size: int) -> dict:
    """Same orbit for every engine: yaw sweep at ~20° elevation, radius from the bbox."""
    import numpy as np
    import pyrender
    import trimesh
    from PIL import Image

    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    outdir.mkdir(parents=True, exist_ok=True)
    tm = trimesh.load(str(glb))
    scene = pyrender.Scene(bg_color=[0.06, 0.07, 0.09, 1.0], ambient_light=[0.25] * 3)
    geoms = tm.geometry.values() if isinstance(tm, trimesh.Scene) else [tm]
    for g in geoms:
        scene.add(pyrender.Mesh.from_trimesh(g, smooth=False))
    center = tm.bounds.mean(axis=0)
    radius = float(np.linalg.norm(tm.extents)) * 1.1 + 1e-6
    cam = pyrender.PerspectiveCamera(yfov=np.deg2rad(40))
    elev = np.deg2rad(20)
    r = pyrender.OffscreenRenderer(size, size)
    images = []
    cam_node = light_node = None
    for i in range(frames):
        yaw = 2 * np.pi * i / frames
        eye = center + radius * np.array(
            [np.cos(elev) * np.sin(yaw), np.sin(elev), np.cos(elev) * np.cos(yaw)]
        )
        fwd = (center - eye) / np.linalg.norm(center - eye)
        right = np.cross(fwd, [0, 1, 0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        pose = np.eye(4)
        pose[:3, 0], pose[:3, 1], pose[:3, 2], pose[:3, 3] = right, up, -fwd, eye
        for node in (cam_node, light_node):
            if node is not None:
                scene.remove_node(node)
        cam_node = scene.add(cam, pose=pose)
        light_node = scene.add(pyrender.DirectionalLight(intensity=3.5), pose=pose)
        color, _ = r.render(scene)
        img = Image.fromarray(color)
        img.save(outdir / f"frame_{i:03d}.png")
        images.append(img)
    r.delete()
    webp = outdir / "turntable.webp"
    images[0].save(webp, save_all=True, append_images=images[1:], duration=120, loop=0)
    return {"frames": frames, "size": size, "webp": str(webp)}


def sheet_cells(sheet_dir: Path, name: str, results: dict, run_dir: Path) -> None:
    """Accumulate bench-format cells so scripts/bench.py --assemble-only renders the sheet."""
    sheet_dir.mkdir(parents=True, exist_ok=True)
    (sheet_dir / "img").mkdir(exist_ok=True)
    (sheet_dir / "thumbs").mkdir(exist_ok=True)
    rj = sheet_dir / "results.json"
    data = (json.loads(rj.read_text()) if rj.is_file() else
            {"meta": {"date": str(date.today()), "tag": "3d-funnel", "presets": {},
                      "seed": 424242, "candidates": 1, "sessions": {},
                      "cockpit": "n/a (tools/3d funnel)", "comfyui": None}, "cells": []})
    for eng, res in results.items():
        data["meta"]["presets"].setdefault(
            eng, {"model_family": ENGINES.get(eng, {}).get("model_family", eng)})
        cell = {"brief_id": name, "preset_id": eng, "sub_axis": "3d-funnel (props)",
                "cand_idx": 0, "seed": 424242}
        if "glb" in res:
            tt = run_dir / eng / "turntables"
            img_name = f"{name}--{eng}.webp"
            thumb_name = f"{name}--{eng}.png"
            shutil.copy2(tt / "turntable.webp", sheet_dir / "img" / img_name)
            shutil.copy2(tt / "frame_000.png", sheet_dir / "thumbs" / thumb_name)
            v = res.get("validation", {})
            cell |= {
                "status": "done",
                "img": f"img/{img_name}",
                "thumb": f"thumbs/{thumb_name}",
                "elapsed_ms": int(res["wall_s"] * 1000),
                "resolution": [],
                "params": {"tris": v.get("triangles"), "tex": v.get("texture_present"),
                           "mb": v.get("file_mb")},
            }
        else:
            cell |= {"status": "error", "error": res.get("error", "unknown")}
        data["cells"] = [c for c in data["cells"]
                         if not (c["brief_id"] == name and c["preset_id"] == eng)] + [cell]
    rj.write_text(json.dumps(data, indent=1))
    print(f"[funnel] sheet cells -> {rj}  (render: scripts/bench.py --assemble-only --dir {sheet_dir})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("asset", nargs="?", help="promoted asset id (or use --image)")
    ap.add_argument("--image", help="direct image path (bench-source / verify mode)")
    ap.add_argument("--engines", default="trellis2,hunyuan")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--frame-size", type=int, default=640)
    ap.add_argument("--sheet-dir", help="accumulate bench sheet cells into this BENCH dir")
    ap.add_argument("--no-promote", action="store_true", help="skip the copy to PROMOTED/_3d/")
    ap.add_argument("--free-comfy", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even if the ComfyUI queue is busy")
    ap.add_argument("--trellis-args", default="", help="extra args for run_trellis.py")
    ap.add_argument("--hunyuan-args", default="", help="extra args for run_hunyuan.py")
    # Single-take mode: the cockpit drives one engine per slot, into a
    # directory it owns, and reads the record back as JSON. Its local lane
    # already serialises every GPU take across executors, so the ComfyUI-idle
    # gate below would be a second copy of the same law.
    ap.add_argument("--out-dir", help="run directory (default tools/3d/runs/<name>)")
    ap.add_argument("--json", dest="json_out", help="also write the record here")
    ap.add_argument("--no-comfy-check", action="store_true",
                    help="skip the ComfyUI-idle gate — the caller owns serialisation")
    # A unix socket to a resident engine child, handed down by
    # runner.py when the resident matches this job. The identity handshake in
    # serve_take() means a mismatched socket degrades to a cold spawn, never
    # to the wrong engine; passing none runs every take cold.
    ap.add_argument("--serve-socket",
                    help="unix socket of a resident engine child (runner.py)")
    ap.add_argument("--seed", type=int, help="seed for every engine runner")
    # Kinds (law 16): the step this run performs. `generate` is every
    # flag above; `decimate` takes a mesh and a triangle budget instead of an
    # image, and shares this file's validate/turntable/record tail.
    ap.add_argument("--kind", default="generate",
                    choices=("generate", "decimate", "texture"),
                    help="the step to run (default generate)")
    # DECIMATE has two engines and the INPUT names which one: a GLB
    # goes to gltfpack, a TRELLIS.2 field set goes back through to_glb. That
    # is the routing rule itself — an engine is chosen by whether the parent's
    # tensors exist — so it needs no flag of its own.
    ap.add_argument("--mesh", help="input GLB (--kind decimate, gltfpack; "
                                   "--kind texture, with --image)")
    ap.add_argument("--fields", help="input fields.npz (--kind decimate, trellis2 "
                                     "re-extraction)")
    ap.add_argument("--decimate-target", type=int,
                    help="target triangle count (--kind decimate)")
    ap.add_argument("--texture-size", type=int,
                    help="texture resolution to bake at — the re-bake size "
                         f"(--kind decimate, trellis2, default {EXTRACT_TEXTURE_SIZE}) "
                         "and the generate-time knob (trellis2)")
    # The generate-time knobs, forwarded to the ONE engine runner that takes
    # each. runner.py's whitelist sends them here as argv, so this parser
    # declares every one: a knob it had never heard of would die at
    # `unrecognized arguments` the first time a surface offered it. They are
    # named here for the same reason argv is a boundary anywhere else:
    # `--trellis-args` is a bench convenience that splits an untyped string,
    # and a value the cockpit sends deserves a parsed flag with the engine it
    # belongs to written down.
    ap.add_argument("--pipeline-type",
                    help="voxel resolution tier (--kind generate, trellis2)")
    ap.add_argument("--decimation-target", type=int,
                    help="face count at extraction (--kind generate, trellis2)")
    ap.add_argument("--octree-resolution", type=int,
                    help="marching-cubes grid (--kind generate, hunyuan)")
    ap.add_argument("--max-num-view", type=int,
                    help="paint cameras (--kind generate, hunyuan)")
    ap.add_argument("--resolution", type=int,
                    help="paint multiview resolution (--kind generate, hunyuan)")
    ap.add_argument("--skip-paint", action="store_true",
                    help="shape stage only (--kind generate, hunyuan)")
    # gltfpack's -sa: hit the number exactly, disregarding quality. Off by
    # default and named for what it costs, because the un-forced result is
    # the honest one (see decimate()'s docstring).
    ap.add_argument("--decimate-force", action="store_true",
                    help="force the exact triangle count, at the surface's "
                         "expense (--kind decimate)")
    args = ap.parse_args()

    manifest: dict = {}
    funnel_record = {
        "funnel_version": 1,
        "kind": args.kind,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "engines": {},
    }

    if args.kind == "texture":
        # The first kind with two inputs: a mesh and a reference
        # image. Both are required; the reference goes through the same
        # cutout the generate kind proved.
        if args.asset or args.fields:
            ap.error("--kind texture takes --mesh and --image, not an asset "
                     "id or a field set")
        if not args.mesh or not args.image:
            ap.error("--kind texture needs both --mesh <glb> (the shape) and "
                     "--image <png> (the appearance)")
        src_glb = Path(args.mesh).resolve()
        if not src_glb.is_file():
            sys.exit(f"no mesh at {src_glb}")
        src_png = Path(args.image).resolve()
        if not src_png.is_file():
            sys.exit(f"no reference image at {src_png}")
        name = src_glb.stem
        run_dir = Path(args.out_dir).resolve() if args.out_dir else RUNS / name
        run_dir.mkdir(parents=True, exist_ok=True)
        # Paint is the heaviest step in the lane — the gate stands, exactly
        # as it does for generate.
        if not args.no_comfy_check:
            comfy_quiet(args.force, args.free_comfy)
        rgba = run_dir / "input-rgba.png"
        cut = cutout(src_png, rgba)
        print(f"[funnel] cutout: {cut}")
        src_facts = validate(src_glb)
        funnel_record["source"] = {"glb": str(src_glb), "sha256": sha256(src_glb),
                                   "validation": src_facts,
                                   "image": str(src_png),
                                   "image_sha256": sha256(src_png)}
        funnel_record["cutout"] = cut
        outdir = run_dir / "hunyuan"
        res = texture(src_glb, rgba, outdir, serve_socket=args.serve_socket)
        if "glb" in res:
            res["validation"] = validate(Path(res["glb"]))
            res["turntable"] = turntable(Path(res["glb"]), outdir / "turntables",
                                         args.frames, args.frame_size)
        funnel_record["engines"]["hunyuan"] = res
        print(f"[funnel] hunyuan texture: {json.dumps({k: res[k] for k in res if k != 'report'}, default=str)[:400]}")
    elif args.kind == "decimate" and args.fields:
        # TRELLIS.2 re-extraction: the same verb, the other engine.
        if args.asset or args.image or args.mesh:
            ap.error("--kind decimate --fields takes a field set, not an "
                     "image, an asset id or a GLB")
        if not args.decimate_target:
            ap.error("--kind decimate needs --decimate-target <triangles>")
        fields = Path(args.fields).resolve()
        if not fields.is_file():
            sys.exit(f"no field set at {fields}")
        name = fields.parent.name
        run_dir = Path(args.out_dir).resolve() if args.out_dir else RUNS / name
        run_dir.mkdir(parents=True, exist_ok=True)
        # This one IS on the card, unlike gltfpack — so the gate stands,
        # exactly as it does for generate.
        if not args.no_comfy_check:
            comfy_quiet(args.force, args.free_comfy)
        funnel_record["source"] = {"fields": str(fields), "sha256": sha256(fields)}
        outdir = run_dir / "trellis2"
        res = extract(fields, outdir, args.decimate_target,
                      args.texture_size or EXTRACT_TEXTURE_SIZE)
        if "glb" in res:
            res["validation"] = validate(Path(res["glb"]))
            res["turntable"] = turntable(Path(res["glb"]), outdir / "turntables",
                                         args.frames, args.frame_size)
        funnel_record["engines"]["trellis2"] = res
        print(f"[funnel] trellis2 extract: {json.dumps({k: res[k] for k in res if k != 'report'}, default=str)[:400]}")
    elif args.kind == "decimate":
        if args.asset or args.image:
            ap.error("--kind decimate takes --mesh or --fields, not an image "
                     "or an asset id")
        if not args.mesh:
            ap.error("--kind decimate needs --mesh <glb> (gltfpack) or "
                     "--fields <npz> (trellis2 re-extraction)")
        if not args.decimate_target:
            ap.error("--kind decimate needs --decimate-target <triangles>")
        src_glb = Path(args.mesh).resolve()
        if not src_glb.is_file():
            sys.exit(f"no mesh at {src_glb}")
        name = src_glb.stem
        run_dir = Path(args.out_dir).resolve() if args.out_dir else RUNS / name
        run_dir.mkdir(parents=True, exist_ok=True)
        # No ComfyUI gate: gltfpack is CPU-only and takes under a second, so
        # the one-heavy-job law has nothing to serialise here.
        src_facts = validate(src_glb)
        funnel_record["source"] = {"glb": str(src_glb), "sha256": sha256(src_glb),
                                   "validation": src_facts}
        outdir = run_dir / "gltfpack"
        res = decimate(src_glb, outdir, args.decimate_target,
                       src_facts.get("triangles") or 0,
                       force=args.decimate_force)
        if "glb" in res:
            res["validation"] = validate(Path(res["glb"]))
            res["turntable"] = turntable(Path(res["glb"]), outdir / "turntables",
                                         args.frames, args.frame_size)
        funnel_record["engines"]["gltfpack"] = res
        print(f"[funnel] gltfpack: {json.dumps({k: res[k] for k in res if k != 'report'}, default=str)[:400]}")
    else:
        if bool(args.asset) == bool(args.image):
            ap.error("give exactly one of: asset id | --image")

        if args.asset:
            src_png, manifest = find_promoted(args.asset)
            name = args.asset
        else:
            src_png = Path(args.image).resolve()
            name = src_png.stem
        run_dir = Path(args.out_dir).resolve() if args.out_dir else RUNS / name
        run_dir.mkdir(parents=True, exist_ok=True)

        if not args.no_comfy_check:
            comfy_quiet(args.force, args.free_comfy)

        rgba = run_dir / "input-rgba.png"
        cut = cutout(src_png, rgba)
        print(f"[funnel] cutout: {cut}")

        funnel_record["source"] = {
            "asset_id": args.asset,
            "png": str(src_png),
            "sha256": sha256(src_png),
            "manifest_version": manifest.get("manifest_version",
                                             "none" if not manifest else "pre-v1 stub"),
            "preset_id": manifest.get("preset_id"),
        }
        funnel_record["cutout"] = cut

        seed_args = ["--seed", str(args.seed)] if args.seed is not None else []
        # The knobs come first and the bench string last, so a `--trellis-args`
        # written by hand at the terminal still wins the duplicate — argparse
        # takes the later value, and the person typing is the one debugging.
        knobs = generate_knobs(args)
        extra = {"trellis2": seed_args + knobs["trellis2"] + args.trellis_args.split(),
                 "hunyuan": seed_args + knobs["hunyuan"] + args.hunyuan_args.split()}
        for eng in [e.strip() for e in args.engines.split(",") if e.strip()]:
            outdir = run_dir / eng
            outdir.mkdir(exist_ok=True)
            res = run_engine(eng, rgba, outdir, extra.get(eng, []),
                             serve_socket=args.serve_socket)
            if "glb" in res:
                res["validation"] = validate(Path(res["glb"]))
                res["turntable"] = turntable(Path(res["glb"]), outdir / "turntables",
                                             args.frames, args.frame_size)
            funnel_record["engines"][eng] = res
            print(f"[funnel] {eng}: {json.dumps({k: res[k] for k in res if k != 'report'}, default=str)[:400]}")

    record = json.dumps(funnel_record, indent=2)
    (run_dir / "funnel.json").write_text(record)
    if args.json_out:
        Path(args.json_out).write_text(record)

    if not args.no_promote:
        dest = PROMOTED / "_3d" / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run_dir / "funnel.json", dest / "funnel.json")
        for eng, res in funnel_record["engines"].items():
            if "glb" in res:
                # a kind outside the generate table (gltfpack) keeps its own
                # filename rather than borrowing an engine's
                shutil.copy2(res["glb"],
                             dest / (ENGINES.get(eng, {}).get("glb")
                                     or Path(res["glb"]).name))
                shutil.copytree(run_dir / eng / "turntables", dest / "turntables" / eng,
                                dirs_exist_ok=True)
        print(f"[funnel] promoted -> {dest}")

    if args.sheet_dir:
        sheet_cells(Path(args.sheet_dir), name, funnel_record["engines"], run_dir)

    failed = [e for e, r in funnel_record["engines"].items() if "glb" not in r]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
