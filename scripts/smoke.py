#!/usr/bin/env python3
"""Smoke test: submit every graph in workflows/ with canned inputs and
verify an output image comes back. Stdlib only; non-zero exit on any failure.

For each workflows/*.json (API format):
  1. skip (with notice) if a referenced model file is not on disk yet,
  2. patch canned inputs: fixed seed, small resolution, canned prompt,
  3. POST /api/prompt, poll /api/history/{id}, fetch the image via /api/view,
  4. assert it is a real PNG; save a copy under .staging/smoke/.

Graphs with LoadImage/LoadImageMask nodes (img2img, inpaint, remove,
composite) reference canned `cockpit-smoke-*.png` inputs; those are
synthesized here and uploaded to ComfyUI's input dir before the run.

Env overrides: COMFYUI_URL (default http://127.0.0.1:8188),
MODELS_ROOT (default ~/models), SMOKE_KEEP_RES=1 to skip the small-res patch.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / "workflows"
OUT_DIR = REPO_ROOT / ".staging" / "smoke"
BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
MODELS_ROOT = Path(os.path.expanduser(os.environ.get("MODELS_ROOT", "~/models")))

CANNED_PROMPT = "a wooden crate, game prop, white background"
CANNED_SEED = 42
CANNED_RES = 768
TIMEOUT_S = 600

# loader class -> [(input key, models subdir), ...]
LOADER_DIRS = {
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoints")],
    "UNETLoader": [("unet_name", "diffusion_models")],
    "CLIPLoader": [("clip_name", "text_encoders")],
    "VAELoader": [("vae_name", "vae")],
    "LoraLoader": [("lora_name", "loras")],
    "INPAINT_LoadFooocusInpaint": [("head", "inpaint"), ("patch", "inpaint")],
    "INPAINT_LoadInpaintModel": [("model_name", "inpaint")],
}

# canned inputs for image-fed graphs: name -> (bg rgb, square rgb)
_SQUARE = (176, 336)  # square bounds in the 512x512 canvas
SMOKE_INPUTS = {
    "cockpit-smoke-src.png": ((200, 200, 200), (255, 60, 40)),
    "cockpit-smoke-alt.png": ((200, 200, 200), (40, 90, 255)),
    "cockpit-smoke-mask.png": ((0, 0, 0), (255, 255, 255)),
}


def api(path: str, payload: dict | None = None, raw: bool = False):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return body if raw else json.loads(body or b"{}")


def missing_models(graph: dict) -> list[str]:
    missing = []
    for node in graph.values():
        for key, sub in LOADER_DIRS.get(node.get("class_type"), []):
            name = node.get("inputs", {}).get(key)
            if isinstance(name, str) and not (MODELS_ROOT / sub / name).is_file():
                missing.append(f"{sub}/{name}")
    return missing


def synth_png(bg: tuple, square: tuple, size: int = 512) -> bytes:
    """Minimal stdlib PNG: solid background with a centered square."""
    a, b = _SQUARE
    row_out = bytes(bg) * size
    row_in = bytes(bg) * a + bytes(square) * (b - a) + bytes(bg) * (size - b)
    raw = b"".join(b"\x00" + (row_in if a <= y < b else row_out) for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def upload_input(name: str, blob: bytes) -> None:
    boundary = "----smoke" + uuid.uuid4().hex
    body = (
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
         f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode()
        + blob
        + (f"\r\n--{boundary}\r\nContent-Disposition: form-data; "
           f"name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    )
    req = urllib.request.Request(
        f"{BASE}/api/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def ensure_canned_inputs() -> None:
    for name, (bg, square) in SMOKE_INPUTS.items():
        upload_input(name, synth_png(bg, square))


def patch_canned(graph: dict) -> None:
    keep_res = os.environ.get("SMOKE_KEEP_RES") == "1"
    for node in graph.values():
        ct = node.get("class_type")
        ins = node.setdefault("inputs", {})
        title = node.get("_meta", {}).get("title", "")
        if ct == "CLIPTextEncode" and title == "prompt_positive":
            ins["text"] = CANNED_PROMPT
        if ct == "RandomNoise":
            ins["noise_seed"] = CANNED_SEED
        if ct == "KSampler":
            ins["seed"] = CANNED_SEED
        if not keep_res and ct in ("EmptyLatentImage", "EmptySD3LatentImage",
                                   "EmptyFlux2LatentImage", "Flux2Scheduler"):
            if "width" in ins:
                ins["width"] = CANNED_RES
            if "height" in ins:
                ins["height"] = CANNED_RES


def run_graph(name: str, graph: dict) -> tuple[bool, str]:
    client_id = str(uuid.uuid4())
    try:
        resp = api("/api/prompt", {"prompt": graph, "client_id": client_id})
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:800]
        return False, f"submit rejected ({e.code}): {detail}"
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        return False, f"no prompt_id in response: {resp}"

    t0 = time.time()
    entry = None
    while time.time() - t0 < TIMEOUT_S:
        hist = api(f"/api/history/{prompt_id}")
        entry = hist.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = json.dumps(status.get("messages", []))[:800]
                return False, f"execution error: {msgs}"
            if status.get("completed") or entry.get("outputs"):
                break
        time.sleep(1.0)
    else:
        return False, f"timeout after {TIMEOUT_S}s (prompt_id={prompt_id})"

    images = []
    for out in (entry.get("outputs") or {}).values():
        images.extend(out.get("images") or [])
    if not images:
        return False, "completed but no output images in history"

    img = images[0]
    qs = urllib.parse.urlencode({
        "filename": img["filename"],
        "subfolder": img.get("subfolder", ""),
        "type": img.get("type", "output"),
    })
    blob = api(f"/api/view?{qs}", raw=True)
    if len(blob) < 1000 or not blob.startswith(b"\x89PNG"):
        return False, f"fetched output is not a plausible PNG ({len(blob)} bytes)"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    copy = OUT_DIR / f"{name}.png"
    copy.write_bytes(blob)
    dt = time.time() - t0
    return True, f"{img['filename']} ({len(blob)//1024} KiB, {dt:.1f}s) -> {copy}"


def main() -> int:
    graphs = sorted(WORKFLOWS.glob("*.json"))
    if not graphs:
        print(f"no graphs in {WORKFLOWS}")
        return 1
    ensure_canned_inputs()
    failures = 0
    for gpath in graphs:
        name = gpath.stem
        graph = json.loads(gpath.read_text())
        miss = missing_models(graph)
        if miss:
            print(f"SKIP {name}: models not on disk yet: {', '.join(miss)}")
            continue
        patch_canned(graph)
        ok, detail = run_graph(name, graph)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
