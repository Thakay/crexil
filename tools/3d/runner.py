#!/usr/bin/env python3
"""3D engine runner: the host-side door to the 3D lane.

Why this exists: the cockpit ships as a container with no GPU and no
engine venvs, so the "local subprocess" cannot be spawned from inside
it. This is the same answer the repo already gives for the other GPU
workload — ComfyUI is a native service the cockpit reaches over HTTP — so
the 3D engines get the same shape rather than a fattened container. The
engines keep the exact environment they were proven in.

Runs in the torch-free funnel venv (`envs/funnel/.venv`, py3.12) on stdlib
only: no new dependency enters the repo for a service this small.

    tools/3d/envs/funnel/.venv/bin/python tools/3d/runner.py

Binds 127.0.0.1:8189, mirroring ComfyUI's bind exactly — the container
reaches it at `host.docker.internal:8189` through WSL's localhost relay
(the same relay, and the same known caveat: it can die after Windows sleep;
`wsl --shutdown` or networkingMode=mirrored). It has no accounts, so it
answers only requests addressed to this machine or to `host.docker.internal`,
never a web page (any Origin is refused), and job and upload bodies only in
their declared types. Listening beyond loopback takes
THREED_RUNNER_ALLOW_REMOTE=1; THREED_RUNNER_ALLOWED_HOSTS names further hosts.

**One job at a time.** The cockpit's local lane already serialises every GPU
take across executors, but this is the GPU's own door: a stray second
request queues rather than racing, so the one-heavy-job law holds even when
something other than the cockpit knocks. `funnel.py --no-comfy-check` is
passed for the same reason — the caller owns serialisation, and running the
script's own ComfyUI-idle gate here would be a second copy of the law.

**At most one resident engine child.** A warm take is 2–3×
cheaper (TRELLIS 75–81 s vs ~196 s; Hunyuan shape 35–39 s vs 66–108 s), so
the runner keeps the LAST-USED engine's serve child alive between takes —
the engine venv's own process in `--serve` mode, spoken to per take by the
funnel over a unix socket (protocol: serve_mode.py). Resident-idle is not
running: the one-heavy-job law counts executing jobs only. The resident is
evicted by SIGTERM (≤6 s to a clean card, measured), always in words, when
(a) another (kind, engine)'s turn comes, (b) the image lane claims the card
(the runner polls ComfyUI's queue itself), or (c) the idle TTL expires
(`THREED_RESIDENT_TTL_S`, 0 = residency off). Hunyuan stays stage-swap
inside its child — both stages resident pinned the whole card. The child's
host RSS is logged at load, per take and at eviction: WSL's RAM cap is the
budget residency actually spends. `/health` shows the resident and the last
eviction's words; a warm job's record carries `resident: {warm, load_s,
rss_mb}`. Every warm path degrades to the cold spawn, in words, never to a
wrong engine (the serve protocol's identity handshake).

**Job records outlive the cockpit.** Every state change writes
`runs/.jobs/<id>.json` atomically, which is what makes the executor's
`reconcile` a lookup rather than a guess: a cockpit restart mid-mesh comes
back, asks `GET /jobs/<id>`, and learns the truth — exactly the shape
ComfyUI's `/api/history` gives the image lane.

**argv is a boundary.** The POST body becomes a subprocess command line, so
params are whitelisted per (kind, engine) and type-checked (`KINDS`); an
unknown key is refused by name. Nothing from the wire is ever passed
through as a raw string.

**A job has a KIND (law 16).** A kind names the step a job runs, so every 3D
verb is the same job with the same states, the same records and the same
whitelist discipline:

    generate   image in  → mesh out   (trellis2 · hunyuan)
    decimate   mesh  in  → mesh out   (gltfpack — a classic tool, no model)
               fields in → mesh out   (trellis2, re-extraction: the
                                       same verb, and the only route below
                                       gltfpack's ~8–10% seam floor)
    texture    mesh + reference image → repainted mesh (hunyuan — Paint as
               its own stage: the first kind with TWO inputs. The
               mesh arrives like any artefact input; the reference rides
               `image_b64` beside it, the same field generate reads. Paint
               remeshes to 40k faces before painting, so the child carries
               Paint's topology — stated, never hidden)

`funnel.py` runs every kind and its `validate()` + `turntable()` tail runs
for every kind, so a child of any verb lands with facts and an orbit. A new
verb is a row in `KINDS` and an operator in the funnel, not a new service.

**An artefact input arrives two ways**, because neither alone is enough:

    {"mesh": {"job": "<runner job id>"}}     the GLB already in runs/<id>/
    {"mesh": {"upload": "<mesh id>"}}        bytes POSTed to /meshes first

The same two doors serve the second artefact, a TRELLIS.2 field set, sent
as `{"fields": {...}}` against POST /fields. Which one a job
takes is per (kind, engine), not per kind: DECIMATE's gltfpack collapses a
GLB, its trellis2 re-extracts from the field the take kept.

The job reference costs nothing when a verb follows its parent take — the
bytes are already here. But records age out (`THREED_RECORD_TTL_S`), and a
promoted or reloaded mesh was never this runner's to begin with, so the
upload path is the one that always works. It is a RAW body route, not
base64 in JSON: a 34 MB GLB does not belong in a JSON document.

Routes:
    GET    /health              → {ok, engines, kinds, busy, running, queued}
    POST   /jobs                → 202 {job_id}   body: kind? · engine ·
                                  image_b64 | mesh · seed? · params? ·
                                  client_ref?
    POST   /meshes              → 201 {mesh_id}  RAW GLB body (model/gltf-binary)
    POST   /fields              → 201 {fields_id} RAW .npz body
    GET    /jobs                → [record, …]    ?client_ref= filters
    GET    /jobs/<id>           → record
    GET    /jobs/<id>/glb       → the mesh bytes
    GET    /jobs/<id>/fields    → the voxel field this take kept
    GET    /jobs/<id>/preview   → turntable frame 0 (PNG)
    GET    /jobs/<id>/turntable → the whole orbit (animated WEBP), the node body
    DELETE /jobs/<id>           → cancel (terminates the child group)

States: queued → running → done | failed | cancelled
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

HERE = Path(__file__).resolve().parent
FUNNEL = HERE / "funnel.py"
PYTHON = HERE / "envs/funnel/.venv/bin/python"
RUNS = HERE / "runs"
JOBS = RUNS / ".jobs"
UPLOADS = RUNS / ".uploads"
FIELDS = RUNS / ".fields"
SERVE = RUNS / ".serve"

HOST = os.environ.get("THREED_RUNNER_HOST", "127.0.0.1")
PORT = int(os.environ.get("THREED_RUNNER_PORT", "8189"))
# Who may knock: the runner has no accounts, so it answers only requests
# addressed to this machine's loopback names or to host.docker.internal, the
# name the cockpit's container uses for the host (compose.yaml), and never a
# web page. Listening beyond loopback takes THREED_RUNNER_ALLOW_REMOTE=1, and
# THREED_RUNNER_ALLOWED_HOSTS (comma-separated) names further hosts clients use.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
ALLOW_REMOTE = os.environ.get("THREED_RUNNER_ALLOW_REMOTE") == "1"
ALLOWED_HOSTS = LOCAL_HOSTS | {h.strip().strip("[]").lower()
                               for h in os.environ.get("THREED_RUNNER_ALLOWED_HOSTS", "").split(",")
                               if h.strip()}
JOB_TYPE = "application/json"
# A refused body up to this size is read and dropped before the answer, so
# closing the connection never resets it before the refusal arrives.
DRAIN_LIMIT = 1024 * 1024
# A cold TRELLIS.2/Hunyuan take is 155–216 s (measured); the ceiling is a
# runaway guard, not a schedule. The cockpit has its own per-take timeout.
JOB_TIMEOUT_S = float(os.environ.get("THREED_JOB_TIMEOUT_S", "1800"))

# --- residency ---
# The runner may hold AT MOST ONE resident engine child — the last one used —
# and resident-idle is not running: the one-heavy-job law counts executing
# jobs only. Eviction is by SIGTERM (measured ≤6 s to a clean card) and is
# always said in words, in the log and on /health. TTL 0 turns residency off.
RESIDENT_TTL_S = float(os.environ.get("THREED_RESIDENT_TTL_S", "600"))
RESIDENT_POLL_S = float(os.environ.get("THREED_RESIDENT_POLL_S", "10"))
# a resident load lands in 26–73 s depending on pagecache (WSL caps host
# RAM); the ceiling is a stuck-child guard, past which the take runs cold
# instead
RESIDENT_LOAD_TIMEOUT_S = float(os.environ.get("THREED_RESIDENT_LOAD_TIMEOUT_S", "240"))
# eviction trigger (b): the image lane claims the card. The runner watches
# ComfyUI's queue itself — the runner is the GPU's own door, so it needs no
# cockpit signal — and an unreachable ComfyUI means the GPU is free, the
# same reading funnel.comfy_quiet() takes.
COMFY_URL = os.environ.get("THREED_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
MAX_IMAGE_BYTES = 64 * 1024 * 1024
# A 1M-triangle TRELLIS.2 GLB measures 35 MB with its 2048 texture; a 4096
# bake is the ceiling this is generous against. Raw body, never base64.
MAX_MESH_BYTES = 256 * 1024 * 1024
# A TRELLIS.2 field set measured 21–92 MiB narrowed and compressed across two
# 1024_cascade props (2026-08-29; the dense one is 245 MiB raw). It is sized by
# occupied voxels, not triangles, so the spread is the prop's. The ceiling is
# generous against a 1536 grid that this lane does not yet run.
MAX_FIELDS_BYTES = 512 * 1024 * 1024
GLB_MAGIC = b"glTF"
NPZ_MAGIC = b"PK\x03\x04"                        # .npz is a zip container

# The two artefacts a mesh-input kind can take, and everything that differs
# between them. Adding a third input type is a row here, not a second
# copy of the upload route, the resolver and the sweep.
#
#   ext/magic  what the door accepts, refused by name at the door
#   dir        where an upload lands; job refs read the producing run instead
#   result     the key under a job record's `result` that names this artefact
#   flag       the funnel argument it becomes
INPUTS: dict[str, dict] = {
    "mesh": {"ext": ".glb", "magic": GLB_MAGIC, "dir": UPLOADS,
             "result": "glb", "flag": "--mesh", "max": MAX_MESH_BYTES,
             "route": "/meshes", "ctype": "model/gltf-binary",
             "id_key": "mesh_id", "noun": "a mesh",
             "what": "a binary glTF — a mesh input is a .glb file, and this "
                     "one does not start with 'glTF'"},
    "fields": {"ext": ".npz", "magic": NPZ_MAGIC, "dir": FIELDS,
               "result": "fields", "flag": "--fields", "max": MAX_FIELDS_BYTES,
               "route": "/fields", "ctype": "application/octet-stream",
               "id_key": "fields_id", "noun": "a field set",
               "what": "a TRELLIS.2 field set — that input is the fields.npz "
                       "a take kept, and this one is not an npz archive"},
}
# Records are the reconcile surface, so they are kept long after the bytes
# stop being interesting; the sweep only trims what has aged out entirely.
RECORD_TTL_S = float(os.environ.get("THREED_RECORD_TTL_S", str(30 * 86400)))

log = logging.getLogger("3d.runner")

def _int_in(lo: int, hi: int):
    """`isinstance(True, int)` is True, so bools are excluded by hand — a
    switch arriving where a count belongs would otherwise reach argv as 1."""
    return lambda v: (v if isinstance(v, int) and not isinstance(v, bool)
                      and lo <= v <= hi else None)


# The kinds this runner serves, each declaring what it takes and the ONLY
# params that may reach argv (law 16). Adding a verb is a row here plus
# an operator in funnel.py — nothing else in this file knows the difference.
#
#   input     "image" (base64 in the JSON body) | "mesh" (a job ref or an upload)
#   ref       an artefact kind ALSO takes a reference image when this says
#             "image": image_b64 beside the mesh reference, the
#             first kind with two parents, and the second input rides the
#             same field generate reads rather than growing a third door
#   seed      whether a seed means anything; a seedless kind REFUSES one
#             rather than accepting a number it will not use
#   engines   engine -> {param: (kind, flag, validator)}; "value" takes
#             `--flag v`, "switch" takes `--flag` when true
#   required  params without which the kind has nothing to do
KINDS: dict[str, dict] = {
    "generate": {
        "input": "image",
        "seed": True,
        "required": (),
        "engines": {
            "trellis2": {
                "pipeline_type": ("value", "--pipeline-type",
                                  lambda v: v if v in ("1024_cascade", "512") else None),
                "texture_size": ("value", "--texture-size",
                                 lambda v: v if isinstance(v, int) and v in (1024, 2048, 4096) else None),
                "decimation_target": ("value", "--decimation-target",
                                      _int_in(1000, 4_000_000)),
            },
            "hunyuan": {
                # The shape stage's resolution tier: the grid the
                # occupancy field is sampled on before marching cubes, which
                # is what Hunyuan calls "resolution". The library takes
                # 16-512; these three are the product's offered set, because
                # the low end makes a blob and a tier the surface cannot
                # describe in words has no business being offered (law 12).
                "octree_resolution": ("value", "--octree-resolution",
                                      lambda v: v if v in (256, 384, 512) else None),
                "max_num_view": ("value", "--max-num-view", _int_in(6, 9)),
                "resolution": ("value", "--resolution",
                               lambda v: v if v in (512, 768) else None),
                "skip_paint": ("switch", "--skip-paint",
                               lambda v: True if v is True else (None if v is not False else False)),
            },
        },
    },
    "decimate": {
        # DECIMATE has TWO engines and they take different artefacts, so
        # `input` maps engine -> artefact. That mapping IS the routing rule:
        # one verb, and the engine follows from whether the parent's tensors
        # exist.
        "input": {"gltfpack": "mesh", "trellis2": "fields"},
        # gltfpack is deterministic: the same mesh and the same target give
        # the same child every time, so a seed here would be a number that
        # means nothing. Refused rather than ignored.
        "seed": False,
        "required": ("target_triangles",),
        "engines": {
            "gltfpack": {
                # the floor is a mesh, not a marker; the ceiling is the
                # densest thing either engine has produced here
                "target_triangles": ("value", "--decimate-target",
                                     _int_in(100, 4_000_000)),
                # gltfpack's -sa: hit the count exactly, disregarding
                # quality. A switch, not a quality dial — off asks how small
                # the mesh goes, on says the number is the budget. The surface
                # names the trade; the runner only refuses a non-boolean.
                "force": ("switch", "--decimate-force",
                          lambda v: True if v is True
                          else (None if v is not False else False)),
            },
            # TRELLIS.2 re-extraction: the same verb, the other
            # engine, and the one with no floor — it remeshes, re-unwraps and
            # re-bakes from the voxel field instead of collapsing the baked
            # result. Measured 2026-08-29: 15,000 asked of a 931,981-triangle
            # lantern landed 14,689 in 11.9 s, where gltfpack floored at
            # 79,170. It takes no `force`: there is no seam to override.
            "trellis2": {
                "target_triangles": ("value", "--decimate-target",
                                     _int_in(100, 4_000_000)),
                # extraction re-bakes, so the texture resolution is a real
                # handle here in a way it never is for a simplifier
                "texture_size": ("value", "--texture-size",
                                 lambda v: v if isinstance(v, int)
                                 and v in (1024, 2048, 4096) else None),
            },
        },
    },
    "texture": {
        # Hunyuan3D-Paint on any mesh. The mesh is the artefact input;
        # `ref` says this kind ALSO requires a reference image (image_b64) —
        # the appearance parent, beside the shape parent.
        "input": "mesh",
        "ref": "image",
        # Paint's multiview stage hard-seeds itself (seed_everything(0) in
        # hy3dpaint), so the same mesh and the same reference give the same
        # child — a seed here would be a number nothing reads. Refused.
        "seed": False,
        "required": (),
        # No params yet: paint views/resolution move little and cost minutes,
        # and offering them would need a funnel argv passthrough. Paint's own
        # defaults (6 views @ 512) are what generate's paint stage runs.
        "engines": {"hunyuan": {}},
    },
}


def kind_input(spec: dict, engine: str) -> str:
    """The artefact this (kind, engine) pair takes. A kind whose engines all
    take the same thing names it once; `decimate` names one per engine."""
    inp = spec["input"]
    return inp if isinstance(inp, str) else inp[engine]
# every engine this runner can name, across kinds — /health's roster
ENGINES = tuple(dict.fromkeys(e for k in KINDS.values() for e in k["engines"]))

# The (kind, engine) pairs that can go warm, each naming the
# serve child that holds them: the engine-venv process itself (the funnel is
# torch-free), spawned with `--serve --socket` and spoken to per take by the
# funnel over that socket. The venv paths come from funnel.ENGINES, their one
# home. Not here, deliberately: decimate/gltfpack (CPU, under a second) and
# decimate/trellis2 (re-extraction loads no weights — the boot is the whole
# cost, and it is small).
def _warm_table() -> dict:
    from funnel import ENGINES as engine_venvs
    return {
        ("generate", "trellis2"): {
            "python": engine_venvs["trellis2"]["python"],
            "script": HERE / "run_trellis.py", "identity": "trellis2"},
        ("generate", "hunyuan"): {
            "python": engine_venvs["hunyuan"]["python"],
            "script": HERE / "run_hunyuan.py", "identity": "hunyuan"},
        ("texture", "hunyuan"): {
            "python": engine_venvs["hunyuan"]["python"],
            "script": HERE / "paint_hunyuan.py", "identity": "hunyuan-paint"},
    }
WARM = _warm_table()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Refused(Exception):
    """A request the runner will not turn into a subprocess, in words."""


# --- job records (the reconcile surface) ---

def host_named(header: str | None) -> str | None:
    """The host a Host header names, lowercased, without its port or IPv6
    brackets; None when the header is missing or unreadable."""
    if not header:
        return None
    try:
        parts = urlsplit("//" + header.strip())
        _ = parts.port                                 # a malformed port raises
    except ValueError:
        return None
    return parts.hostname or None


def refusal(headers, allowed_hosts: frozenset[str]) -> tuple[int, str] | None:
    """Why a request is not served, or None. A Host outside the allow-list is
    DNS rebinding or a misaddressed client; an Origin is a web page."""
    host = host_named(headers.get("Host"))
    if host not in allowed_hosts:
        return 403, (f"Refused: this runner answers requests addressed to {', '.join(sorted(allowed_hosts))}; "
                     f"this one is addressed to {host or 'no host'}.")
    if headers.get("Origin") is not None:
        return 403, "Refused: this runner serves the cockpit's server, never a web page."
    return None


def wrong_type(headers, expected: str) -> tuple[int, str] | None:
    """A body that changes state must name its type. A web page can send plain
    text or a form without asking first, never these types, so the check
    closes that door."""
    given = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    return None if given == expected else (415, f"Refused: send the body as {expected}.")


def loopback(host: str) -> bool:
    name = host.strip().lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def check_bind(host: str, allow_remote: bool) -> None:
    """Refuse to listen beyond this machine unless asked by name: the runner
    has no accounts."""
    if not allow_remote and not loopback(host):
        raise ValueError(f"Refusing to listen on {host}: the runner has no accounts, so it serves this "
                         "machine only. To serve the network anyway, set THREED_RUNNER_ALLOW_REMOTE=1 "
                         "and name the hosts clients use in THREED_RUNNER_ALLOWED_HOSTS.")


def record_path(job_id: str) -> Path:
    return JOBS / f"{job_id}.json"


def write_record(rec: dict) -> None:
    """Atomic: reconcile must never read a half-written record."""
    JOBS.mkdir(parents=True, exist_ok=True)
    p = record_path(rec["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(p)


def read_record(job_id: str) -> dict | None:
    try:
        return json.loads(record_path(job_id).read_text())
    except (OSError, ValueError):
        return None


def all_records() -> list[dict]:
    out = []
    for p in sorted(JOBS.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def sweep_records() -> None:
    cutoff = time.time() - RECORD_TTL_S
    for p in JOBS.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    # uploaded artefacts age out on the same clock: they exist to be the
    # input of a job that is about to be submitted, not to be a store
    for spec in INPUTS.values():
        for p in spec["dir"].glob(f"*{spec['ext']}"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


# --- uploaded meshes (the input path that always works) ---

def store_upload(data: bytes, what: str = "mesh") -> dict:
    """A raw artefact from the wire, named by content so the same bytes sent
    twice are one file. Refuses anything whose magic is wrong — handing
    gltfpack a PNG would fail deep in a subprocess instead of at the door."""
    spec = INPUTS[what]
    if not data:
        raise Refused(f"that {what} upload was empty")
    if len(data) > spec["max"]:
        raise Refused(f"that {what} is {len(data) // 2**20} MB — the runner "
                      f"takes up to {spec['max'] // 2**20} MB")
    if not data.startswith(spec["magic"]):
        raise Refused(f"that upload is not {spec['what']}")
    import hashlib
    mesh_id = hashlib.sha256(data).hexdigest()[:16]
    store = spec["dir"]
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{mesh_id}{spec['ext']}"
    if not path.is_file():
        tmp = path.with_suffix(spec["ext"] + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    else:
        path.touch()                    # a re-sent artefact restarts its clock
    return {spec["id_key"]: mesh_id, "bytes": len(data)}


def resolve_mesh(ref, what: str = "mesh") -> tuple[Path, dict]:
    """An artefact input reference -> (path, what the record should remember).

    Both forms refuse in words that name the OTHER form as the fix, because
    the two failures an artist can actually hit are an expired record and an
    artefact this runner never made.

    The artefact is a parameter: `fields` resolves exactly the same
    way against the same records, and a TRELLIS.2 take that was run with
    `--no-save-fields` says so — a job with no field set is a different
    refusal from a job this runner has forgotten."""
    spec = INPUTS[what]
    route = spec["route"]
    if not isinstance(ref, dict) or not ref:
        raise Refused(f"this kind needs {spec['noun']} — send "
                      f"{what}={{\"job\": \"<runner job id>\"}} or upload one "
                      f"to POST {route} and send "
                      f"{what}={{\"upload\": \"<id>\"}}")
    if "job" in ref:
        job_id = ref["job"]
        if not isinstance(job_id, str) or not job_id:
            raise Refused(f"{what}.job must be a runner job id")
        rec = read_record(job_id)
        if rec is None:
            raise Refused(f"this runner has no record of job {job_id} any "
                          f"more — upload the {what} to POST {route} instead")
        got = (rec.get("result") or {}).get(spec["result"])
        if not got or not Path(got).is_file():
            raise Refused(f"job {job_id} has no {what} on disk to work from "
                          f"— upload the {what} to POST {route} instead")
        return Path(got), {"job": job_id}
    if "upload" in ref:
        mesh_id = ref["upload"]
        if not isinstance(mesh_id, str) or not mesh_id.isalnum():
            raise Refused(f"{what}.upload must be an id from POST {route}")
        path = spec["dir"] / f"{mesh_id}{spec['ext']}"
        if not path.is_file():
            raise Refused(f"no uploaded {what} {mesh_id} — POST the bytes to "
                          f"{route} again (uploads age out with records)")
        return path, {"upload": mesh_id}
    raise Refused(f"{what} takes one of: job (a runner job id) or upload (an "
                  f"id from POST {route})")


# --- residency ---

def rss_mb(pid: int | None) -> int | None:
    """Host RSS of the resident child: WSL's RAM cap is the budget residency
    actually spends (TRELLIS parks its 4B pipeline host-side), so it is
    logged at load, after every take and at eviction."""
    if pid is None:
        return None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def comfy_busy() -> bool:
    """Is the image lane using (or about to use) the card? Unreachable means
    idle — ComfyUI down is a free GPU, exactly funnel.comfy_quiet's reading."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/api/queue", timeout=3) as r:
            q = json.load(r)
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False


class Resident:
    """At most one resident engine child — the last one used.

    The child is the engine venv's own process in `--serve` mode; `ensure()`
    hands the worker its socket (evicting a different resident first), and
    `release()` marks it idle. A poll thread walks the other two eviction
    triggers: the image lane claiming the card, and the idle TTL. Every
    eviction says its reason in words — in the log, and the last one on
    /health — because a disappearing 8 GB of VRAM must never be a mystery.

    `resident.json` beside the sockets remembers the child's pid across a
    runner crash, so a restart never strands an orphan holding the card."""

    PIDFILE = SERVE / "resident.json"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key: tuple[str, str] | None = None
        self.identity: str | None = None
        self.proc: subprocess.Popen | None = None
        self.socket_path: Path | None = None
        self.log_f = None
        self.load_s: float | None = None
        self.last_used = 0.0
        self.in_use = False
        self.last_eviction: str | None = None
        self._reap_orphan()
        SERVE.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._poll_loop, daemon=True,
                         name="3d-resident").start()

    # -- lifecycle --

    def ensure(self, kind: str, engine: str) -> Path | None:
        """A socket to a resident child for this (kind, engine), or None —
        run cold. Called only from the worker thread, so at most one spawn
        is ever in flight."""
        key = (kind, engine)
        spec = WARM.get(key)
        if spec is None or RESIDENT_TTL_S <= 0:
            return None
        with self.lock:
            if (self.key == key and self.proc is not None
                    and self.proc.poll() is None):
                self.in_use = True
                self.last_used = time.monotonic()
                return self.socket_path
            if self.proc is not None:
                self._evict_locked(f"the card's turn moved to {kind}/{engine}")
            sock = SERVE / f"{engine}-{kind}.sock"
            with suppress(OSError):
                sock.unlink()
            log_path = SERVE / f"{engine}-{kind}.log"
            self.log_f = open(log_path, "ab", 0)
            cmd = [str(spec["python"]), str(spec["script"]),
                   "--serve", "--socket", str(sock)]
            log.info("resident %s/%s: loading — %s (loads run 26–73 s with "
                     "pagecache)", kind, engine, " ".join(cmd))
            try:
                proc = subprocess.Popen(cmd, cwd=str(HERE),
                                        start_new_session=True,
                                        stdout=self.log_f,
                                        stderr=subprocess.STDOUT)
            except OSError as e:
                log.info("resident %s/%s could not start (%s) — running "
                         "this take cold", kind, engine, e)
                with suppress(OSError):
                    self.log_f.close()
                self.log_f = None
                return None
            self.key, self.identity = key, spec["identity"]
            self.proc, self.socket_path = proc, sock
            self.in_use, self.load_s = True, None
            self.last_used = time.monotonic()
            self.PIDFILE.write_text(json.dumps(
                {"pid": proc.pid, "kind": kind, "engine": engine,
                 "started": now()}))
        # wait for the socket OUTSIDE the lock — a load is tens of seconds
        # and /health must keep answering while it runs
        t0 = time.monotonic()
        while time.monotonic() - t0 < RESIDENT_LOAD_TIMEOUT_S:
            if proc.poll() is not None:
                break
            if sock.exists() and self._connectable(sock):
                elapsed = round(time.monotonic() - t0, 1)
                with self.lock:
                    self.load_s = elapsed
                    self.last_used = time.monotonic()
                log.info("resident %s/%s ready in %.1fs, rss %s MB",
                         kind, engine, elapsed, rss_mb(proc.pid))
                return sock
            time.sleep(0.5)
        with self.lock:
            self._evict_locked("the resident child never came up — "
                               "running this take cold")
        return None

    def release(self, kind: str, engine: str) -> int | None:
        """The take is over: mark idle, log the RSS, hand it back for the
        job record."""
        with self.lock:
            if self.key != (kind, engine) or self.proc is None:
                return None
            self.in_use = False
            self.last_used = time.monotonic()
            r = rss_mb(self.proc.pid) if self.proc.poll() is None else None
        if r is not None:
            log.info("resident %s/%s idle, rss %s MB (the host RAM budget)",
                     kind, engine, r)
        return r

    def evict(self, reason: str) -> None:
        with self.lock:
            self._evict_locked(reason)

    def info(self) -> dict | None:
        """/health's residency face: what is held, since when, at what cost."""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                return None
            kind, engine = self.key
            return {"kind": kind, "engine": engine,
                    "busy": self.in_use,
                    "idle_s": (0 if self.in_use
                               else round(time.monotonic() - self.last_used)),
                    "load_s": self.load_s,
                    "rss_mb": rss_mb(self.proc.pid),
                    "ttl_s": RESIDENT_TTL_S}

    # -- internals --

    def _evict_locked(self, reason: str) -> None:
        proc, key = self.proc, self.key
        if proc is not None and proc.poll() is None:
            span = round(time.monotonic() - self.last_used)
            held = f"mid-take {span}s in" if self.in_use else f"idle {span}s"
            words = (f"resident {key[0]}/{key[1]} evicted — {reason} "
                     f"({held}, rss {rss_mb(proc.pid)} MB)")
            log.info(words)
            self.last_eviction = words
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=15)       # measured ≤6 s to a clean card
            except subprocess.TimeoutExpired:
                with suppress(OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                with suppress(Exception):
                    proc.wait(timeout=5)
        elif proc is not None:
            log.info("resident %s died on its own (exit %s) — cleared",
                     key, proc.returncode)
        if self.log_f is not None:
            with suppress(OSError):
                self.log_f.close()
        if self.socket_path is not None:
            with suppress(OSError):
                self.socket_path.unlink()
        with suppress(OSError):
            self.PIDFILE.unlink()
        self.key = self.identity = self.proc = self.socket_path = None
        self.log_f, self.load_s, self.in_use = None, None, False

    def _connectable(self, sock: Path) -> bool:
        import socket as socketlib
        s = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        try:
            s.settimeout(2)
            s.connect(str(sock))
            return True
        except OSError:
            return False
        finally:
            with suppress(OSError):
                s.close()

    def _reap_orphan(self) -> None:
        """A crashed runner leaves its resident child holding the card; the
        pidfile is how the next runner finds and ends it, in words."""
        try:
            rec = json.loads(self.PIDFILE.read_text())
            pid = int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        with suppress(OSError):
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"--serve" in cmdline:
                log.info("orphaned resident child from a previous runner "
                         "(pid %d, %s/%s) — evicting it before serving",
                         pid, rec.get("kind"), rec.get("engine"))
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
        with suppress(OSError):
            self.PIDFILE.unlink()

    def _poll_loop(self) -> None:
        """Triggers (b) and (c): the image lane claiming the card, and the
        idle TTL. Only an IDLE resident is ever evicted here — a child mid-
        take belongs to the running job, and cancel/timeout own that path."""
        while True:
            time.sleep(RESIDENT_POLL_S)
            with self.lock:
                if self.proc is None:
                    continue
                if self.proc.poll() is not None:
                    self._evict_locked("it exited on its own")
                    continue
                if self.in_use:
                    continue
                idle = time.monotonic() - self.last_used
                if idle > RESIDENT_TTL_S:
                    self._evict_locked(f"idle TTL expired "
                                       f"({RESIDENT_TTL_S:.0f}s)")
                    continue
            if comfy_busy():                # HTTP outside the lock
                with self.lock:
                    if self.proc is not None and not self.in_use:
                        self._evict_locked("the image lane claimed the card "
                                           "(ComfyUI queue busy)")


# --- the one worker ---

class Worker:
    """One job at a time, in submission order. The GPU's own door."""

    def __init__(self) -> None:
        self.q: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()
        self.running: str | None = None
        self.proc: subprocess.Popen | None = None
        self.warm_key: tuple[str, str] | None = None   # the running job's resident, if any
        self.cancelled: set[str] = set()
        threading.Thread(target=self._loop, daemon=True, name="3d-worker").start()

    def submit(self, rec: dict) -> None:
        write_record(rec)
        self.q.put(rec["id"])

    def state(self) -> dict:
        with self.lock:
            return {"busy": self.running is not None,
                    "running": self.running,
                    "queued": self.q.qsize()}

    def cancel(self, job_id: str) -> bool:
        """Best-effort, and safe against already-finished work. A queued job
        is marked so the loop skips it; a running one has its whole process
        group terminated — the engine venv's python spawns children. A warm
        take's work lives in the RESIDENT child, outside the funnel's group,
        so cancelling one also evicts the resident: SIGTERM is the
        only interrupt an engine mid-take understands."""
        with self.lock:
            self.cancelled.add(job_id)
            proc, running, warm = self.proc, self.running, self.warm_key
        if running == job_id and proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                return False
            if warm is not None:
                RESIDENT.evict(f"job {job_id} was cancelled mid-take")
            return True
        return True

    def _loop(self) -> None:
        while True:
            job_id = self.q.get()
            try:
                self._run(job_id)
            except Exception:                       # the loop IS the lane
                log.exception("runner: job %s crashed outside the take", job_id)
                rec = read_record(job_id) or {"id": job_id}
                self._finish(rec, "failed",
                             error="the runner crashed while starting this job "
                                   "— its record carries what a bug report needs")
            finally:
                with self.lock:
                    self.running = self.proc = self.warm_key = None

    def _run(self, job_id: str) -> None:
        rec = read_record(job_id)
        if rec is None:
            return
        with self.lock:
            skip = job_id in self.cancelled
        if skip:
            self._finish(rec, "cancelled", error="cancelled before it started")
            return

        job_dir = RUNS / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        record_json = job_dir / "record.json"
        cmd = [str(PYTHON), str(FUNNEL),
               "--out-dir", str(job_dir),
               "--json", str(record_json),
               "--no-promote",          # promote is the cockpit's own verb
               "--no-comfy-check"]      # the caller owns serialisation
        kind = rec.get("kind") or "generate"
        if rec.get("input_path"):
            # the input is referenced where it already lies (another job's
            # directory, or the upload store) — copying 35 MB to make a
            # second copy of a file this process is about to read once buys
            # nothing; the funnel record captures its path and sha256
            flag = INPUTS[rec["input"]]["flag"]
            cmd += ["--kind", kind, flag, str(rec["input_path"])]
            if rec.get("ref") == "image":
                # The second parent: the reference image build_job
                # landed beside this record
                cmd += ["--image", str(job_dir / "reference.png")]
        else:
            cmd += ["--image", str(job_dir / "source.png"),
                    "--engines", rec["engine"]]
        if rec.get("seed") is not None:
            cmd += ["--seed", str(rec["seed"])]
        cmd += rec.get("engine_args") or []

        # running from here: a warm load is part of the take's clock
        rec |= {"state": "running", "started_at": now()}
        write_record(rec)
        with self.lock:
            self.running = job_id

        # The warm door. ensure() hands back a resident child's socket
        # (evicting a different resident first, loading if needed) or None,
        # and then the take runs cold.
        warm_sock = RESIDENT.ensure(kind, rec["engine"])
        if warm_sock is not None:
            cmd += ["--serve-socket", str(warm_sock)]
            rec["resident"] = {"warm": True, "load_s": RESIDENT.load_s}
            with self.lock:
                self.warm_key = (kind, rec["engine"])
        rec["cmd"] = cmd
        write_record(rec)

        # a cancel that landed during the resident load: stop before spawning
        with self.lock:
            skip = job_id in self.cancelled
        if skip:
            if warm_sock is not None:
                RESIDENT.release(kind, rec["engine"])
            self._finish(rec, "cancelled", error="cancelled before it started")
            return
        log.info("job %s: %s", job_id, " ".join(cmd))

        t0 = time.monotonic()
        # own process group: cancel must reach the engine's own children
        proc = subprocess.Popen(cmd, cwd=str(HERE), start_new_session=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        with self.lock:
            self.proc = proc
        try:
            out, _ = proc.communicate(timeout=JOB_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            if warm_sock is not None:
                # the runaway work lives in the resident child, not the
                # funnel's group — it goes with the job
                RESIDENT.evict(f"job {job_id} ran past the "
                               f"{JOB_TIMEOUT_S:.0f}s guard mid-take")
            out, _ = proc.communicate()
            self._finish(rec, "failed", elapsed_s=round(time.monotonic() - t0, 1),
                         tail=tail(out),
                         error=f"the engine ran past {JOB_TIMEOUT_S:.0f}s and was stopped")
            return

        elapsed = round(time.monotonic() - t0, 1)
        if warm_sock is not None:
            # the take is over either way; the RSS number rides the record
            r = RESIDENT.release(kind, rec["engine"])
            if r is not None:
                rec.setdefault("resident", {})["rss_mb"] = r
        with self.lock:
            was_cancelled = job_id in self.cancelled
        if was_cancelled:
            self._finish(rec, "cancelled", elapsed_s=elapsed, tail=tail(out),
                         error="cancelled while running")
            return

        funnel_rec = None
        try:
            funnel_rec = json.loads(record_json.read_text())
        except (OSError, ValueError):
            pass
        engine_rec = ((funnel_rec or {}).get("engines") or {}).get(rec["engine"]) or {}
        glb = engine_rec.get("glb")
        if proc.returncode == 0 and glb and Path(glb).is_file():
            self._finish(rec, "done", elapsed_s=elapsed, tail=tail(out),
                         result={"glb": glb,
                                 # a generate take keeps the voxel
                                 # field beside its GLB, and this is what
                                 # makes `{"fields": {"job": id}}` resolve —
                                 # None for an engine or a run that kept none
                                 "fields": (engine_rec.get("report") or {}).get("fields"),
                                 "glb_sha256": engine_rec.get("glb_sha256"),
                                 "validation": engine_rec.get("validation"),
                                 "turntable": engine_rec.get("turntable"),
                                 "cutout": (funnel_rec or {}).get("cutout"),
                                 "engine_wall_s": engine_rec.get("wall_s")})
        else:
            self._finish(rec, "failed", elapsed_s=elapsed, tail=tail(out),
                         error=engine_rec.get("error")
                         or f"the engine exited {proc.returncode} without a mesh")

    def _finish(self, rec: dict, state: str, **fields) -> None:
        rec |= {"state": state, "ended_at": now(), **fields}
        write_record(rec)
        with self.lock:
            self.cancelled.discard(rec["id"])


def tail(text: str | None, limit: int = 4000) -> str:
    """The engine's own last words — what a failed cell shows behind
    DETAILS. Bounded: a record is read, not archived."""
    t = (text or "").strip()
    return t[-limit:]


# --- the door ---

def build_job(body: dict) -> dict:
    """The whole door: a wire body becomes a queued record, or a refusal in
    words. Everything that could reach argv is checked here, by the kind's
    own whitelist — nothing downstream re-validates."""
    kind = body.get("kind") or "generate"
    if kind not in KINDS:
        raise Refused(f"unknown job kind {kind!r} — this runner serves "
                      f"{', '.join(KINDS)}")
    spec = KINDS[kind]
    engine = body.get("engine")
    if engine not in spec["engines"]:
        raise Refused(f"{engine!r} does not serve {kind} — this runner's "
                      f"{kind} engines are {', '.join(spec['engines'])}")

    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise Refused("params must be an object")
    missing = [k for k in spec["required"] if k not in params]
    if missing:
        raise Refused(f"{kind} needs {', '.join(missing)} — it has nothing "
                      f"to do without it")

    seed = body.get("seed")
    if seed is not None:
        if not spec["seed"]:
            raise Refused(f"{kind} takes no seed — {engine} is deterministic, "
                          f"so a seed would be a number nothing reads")
        if not (isinstance(seed, int) and not isinstance(seed, bool)
                and 0 <= seed < 2**63):
            raise Refused("seed must be a non-negative integer")

    # argv is a boundary: whitelist by name, validate by type, never pass
    # a wire string through.
    args: list[str] = []
    specs = spec["engines"][engine]
    for key, value in params.items():
        pspec = specs.get(key)
        if pspec is None:
            raise Refused(f"{engine} has no parameter {key!r} for {kind} — "
                          f"it takes {', '.join(specs) or 'none'}")
        ptype, flag, check = pspec
        ok = check(value)
        if ok is None:
            raise Refused(f"{key}={value!r} is not a value {engine} accepts")
        if ptype == "switch":
            if ok:
                args.append(flag)
        else:
            args += [flag, str(ok)]

    # the input, per (kind, engine) — an image in the body, or an artefact
    # by reference (a mesh, or a TRELLIS.2 field set)
    what = kind_input(spec, engine)
    data: bytes | None = None
    mesh_path: Path | None = None
    mesh_ref: dict | None = None
    if what == "image":
        data = _decode_image(body, "a mesh needs a source image — send it "
                                   "as image_b64")
    else:
        mesh_path, mesh_ref = resolve_mesh(body.get(what), what)

    # A kind may take a reference image BESIDE its artefact input:
    # the second parent. Same wire field the image kinds read; a texture
    # with no reference has nothing to paint from, so it is required.
    ref_data: bytes | None = None
    if spec.get("ref") == "image":
        ref_data = _decode_image(body, f"{kind} paints from a reference "
                                       "image — send it as image_b64")

    job_id = uuid.uuid4().hex[:16]
    rec = {"id": job_id, "state": "queued", "kind": kind, "engine": engine,
           "seed": seed, "params": params,
           "engine_args": args,
           "client_ref": body.get("client_ref"),
           "created_at": now(), "runner_version": 3}
    job_dir = RUNS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (job_dir / "source.png").write_bytes(data)
        rec["source_bytes"] = len(data)
    else:
        # named for the role, not the type: `decimate` takes a GLB from one
        # engine and a field set from the other, and the record says which
        rec["input"] = what
        rec["input_ref"] = mesh_ref
        rec["input_path"] = str(mesh_path)
        rec["source_bytes"] = mesh_path.stat().st_size
    if ref_data is not None:
        (job_dir / "reference.png").write_bytes(ref_data)
        rec["ref"] = "image"
        rec["ref_bytes"] = len(ref_data)
    return rec


def _decode_image(body: dict, missing: str) -> bytes:
    """image_b64 -> bytes, refused in words — one door for the source image
    and the reference image, so the two cannot drift."""
    b64 = body.get("image_b64")
    if not isinstance(b64, str) or not b64:
        raise Refused(missing)
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("image_b64 is not valid base64")
    if not data:
        raise Refused("the image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise Refused(f"the image is {len(data) // 2**20} MB — the "
                      f"runner takes up to {MAX_IMAGE_BYTES // 2**20} MB")
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "crexil-3d-runner/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):                 # journalctl owns the log
        log.info("%s - %s", self.address_string(), fmt % a)

    # -- helpers --
    def _json(self, code: int, payload, *, close: bool = False) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, why: tuple[int, str] | None) -> bool:
        """Answer a refusal and close the connection, saying so in the answer so
        no client reuses it. A body up to DRAIN_LIMIT is read and dropped first,
        so the close neither resets the answer in flight nor leaves the body to
        be read as the next request."""
        if why is None:
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if 0 < length <= DRAIN_LIMIT:
            self.rfile.read(length)
        self._json(why[0], {"detail": why[1]}, close=True)
        return True

    def _file(self, path: Path | None, ctype: str, missing: str) -> None:
        if path is None or not path.is_file():
            return self._json(404, {"detail": missing})
        self._bytes(200, path.read_bytes(), ctype)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise Refused("empty request body")
        if n > MAX_IMAGE_BYTES * 2:
            raise Refused("request body too large")
        return json.loads(self.rfile.read(n))

    # -- routes --
    def do_GET(self) -> None:
        if self._refuse(refusal(self.headers, ALLOWED_HOSTS)):
            return None
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if parts == ["health"]:
            return self._json(200, {
                "ok": True, "engines": list(ENGINES),
                # what each verb can be asked of, and by whom (law 12's grammar
                # at the door: the cockpit's badge reads from a fact, not a
                # guess about what this runner happens to have)
                "kinds": {k: list(v["engines"]) for k, v in KINDS.items()},
                # residency in words: what is held, at what
                # cost, and why the last resident left
                "resident": RESIDENT.info(),
                "last_eviction": RESIDENT.last_eviction,
                "runner_version": 3, **WORKER.state()})
        if parts == ["jobs"]:
            recs = all_records()
            ref = (parse_qs(u.query).get("client_ref") or [None])[0]
            if ref:
                recs = [r for r in recs if r.get("client_ref") == ref]
            return self._json(200, recs[:200])
        if len(parts) in (2, 3) and parts[0] == "jobs":
            rec = read_record(parts[1])
            if rec is None:
                return self._json(404, {"detail": f"no job {parts[1]}"})
            if len(parts) == 2:
                return self._json(200, rec)
            result = rec.get("result") or {}
            if parts[2] == "glb":
                glb = result.get("glb")
                return self._file(Path(glb) if glb else None,
                                  "model/gltf-binary",
                                  "this job has no mesh")
            if parts[2] == "fields":
                # The voxel field a generate take kept, so the cockpit
                # can hold it beside its own artefact and hand it back for a
                # re-extraction long after this runner's record ages out
                got = result.get("fields")
                return self._file(Path(got) if got else None,
                                  INPUTS["fields"]["ctype"],
                                  "this take kept no field set — it was run "
                                  "with --no-save-fields, or by an engine "
                                  "that has none")
            if parts[2] == "preview":
                webp = (result.get("turntable") or {}).get("webp")
                frame = Path(webp).parent / "frame_000.png" if webp else None
                return self._file(frame, "image/png",
                                  "this job has no turntable")
            if parts[2] == "turntable":
                # The whole orbit, one file. The cockpit caches this
                # beside its own artefact and derives its card plate from
                # frame 0 of it, so one fetch serves both faces.
                webp = (result.get("turntable") or {}).get("webp")
                return self._file(Path(webp) if webp else None, "image/webp",
                                  "this job has no turntable")
        return self._json(404, {"detail": f"no route {u.path}"})

    def do_POST(self) -> None:
        if self._refuse(refusal(self.headers, ALLOWED_HOSTS)):
            return None
        path = urlparse(self.path).path.rstrip("/")
        by_route = {v["route"]: k for k, v in INPUTS.items()}
        expected = INPUTS[by_route[path]]["ctype"] if path in by_route else JOB_TYPE if path == "/jobs" else None
        if expected and self._refuse(wrong_type(self.headers, expected)):
            return None
        if path in by_route:
            # a RAW body, deliberately: a 35 MB GLB (or a field set of the
            # same order) base64'd into a JSON document is a third again as
            # many bytes
            # and a parse of the whole thing to get back what was already on
            # the wire
            what = by_route[path]
            spec = INPUTS[what]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0:
                    raise Refused(f"send the {what} as the request body")
                if n > spec["max"]:
                    raise Refused(f"that {what} is {n // 2**20} MB — the "
                                  f"runner takes up to "
                                  f"{spec['max'] // 2**20} MB")
                out = store_upload(self.rfile.read(n), what)
            except Refused as e:
                return self._json(422, {"detail": str(e)})
            except (ValueError, OSError) as e:
                return self._json(400, {"detail": f"bad request: {e}"})
            return self._json(201, out)
        if path != "/jobs":
            return self._json(404, {"detail": f"no route {self.path}"})
        try:
            rec = build_job(self._read_body())
        except Refused as e:
            return self._json(422, {"detail": str(e)})
        except (ValueError, OSError) as e:
            return self._json(400, {"detail": f"bad request: {e}"})
        WORKER.submit(rec)
        return self._json(202, {"job_id": rec["id"], "state": rec["state"]})

    def do_DELETE(self) -> None:
        if self._refuse(refusal(self.headers, ALLOWED_HOSTS)):
            return None
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) != 2 or parts[0] != "jobs":
            return self._json(404, {"detail": f"no route {self.path}"})
        rec = read_record(parts[1])
        if rec is None:
            return self._json(404, {"detail": f"no job {parts[1]}"})
        WORKER.cancel(parts[1])
        if rec.get("state") in ("queued", "running"):
            rec = read_record(parts[1]) or rec
        return self._json(200, {"job_id": parts[1],
                                "state": (rec or {}).get("state"),
                                "cancel_requested": True})


WORKER: Worker
RESIDENT: Resident


def main() -> int:
    global WORKER, RESIDENT
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        check_bind(HOST, ALLOW_REMOTE)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    for path, what in ((PYTHON, "the funnel venv's python"), (FUNNEL, "funnel.py")):
        if not path.exists():
            print(f"missing {what}: {path}", file=sys.stderr)
            return 2
    JOBS.mkdir(parents=True, exist_ok=True)
    for spec in INPUTS.values():
        spec["dir"].mkdir(parents=True, exist_ok=True)
    sweep_records()
    # systemd stops with SIGTERM; without this the resident child — its own
    # session, deliberately — would outlive the runner and hold the card
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    RESIDENT = Resident()
    WORKER = Worker()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    log.info("3D runner on http://%s:%d — kinds: %s · residency ttl %.0fs"
             " (0 = off)",
             HOST, PORT,
             " · ".join(f"{k} ({', '.join(v['engines'])})"
                        for k, v in KINDS.items()),
             RESIDENT_TTL_S)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        RESIDENT.evict("the runner is shutting down")
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
