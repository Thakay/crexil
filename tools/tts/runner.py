#!/usr/bin/env python3
"""The local voice runner: the host-side door to the local voice engine.

It runs inside the voice engine's own environment and serves Qwen3-TTS over
HTTP on 127.0.0.1, so the cockpit never imports the engine's libraries and
the engine keeps the exact environment it was measured in. The cockpit
reaches it through `cockpit/server/app/local_voice.py`; README.md beside this
file says how to install and start it.

Two kinds of job:

    voice-design   a description of a voice and a line  ->  n takes, each a
                   new voice reading the line (batched in one call)
    speak          a line and a reference take           ->  n takes in the
                   reference take's voice

**One job at a time.** The cockpit's local lane already serializes every GPU
job across engines (law 14); this queue is the card's own door, so a stray
second request waits instead of racing.

**Nothing stays on the card between jobs.** Each job loads its model, runs,
and frees the memory again, so an image engine never finds the card held by
an idle voice model. Loading from the page cache takes about a second.

**Job records outlive the cockpit.** Every state change is written atomically
to `<state>/jobs/<id>.json`, which makes the cockpit's recovery after a
restart a lookup. A job this process did not finish is recorded as failed
when the runner starts again.

**The body is a boundary.** Fields are checked by name and type; an unknown
field is refused by name. Seeds are honored and recorded: a job without one
gets a drawn seed, so every take can be reproduced on this installation.

Routes:
    GET    /health                -> {ok, engine, version, busy, running, queued, models}
    POST   /jobs                  -> 202 {job_id}   body: kind, text, instruct
                                     (voice-design), voice {wav_b64, text}
                                     (speak), language, n, seed, client_ref
    GET    /jobs/<id>             -> the job record
    GET    /jobs/<id>/audio/<i>   -> take i (from 0) as 16-bit PCM WAV
    DELETE /jobs/<id>             -> cancel

States: queued -> running -> done | failed | cancelled

Standard library only at import time; the engine's libraries load in the
worker, so the request handling can be exercised without them.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import logging
import os
import queue
import secrets
import threading
import time
import traceback
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1"
ENGINE = "qwen3-tts"
KINDS = ("voice-design", "speak")
# The languages the engine reads, named as the engine row publishes them.
LANGUAGES = ("English", "Chinese", "Japanese", "Korean", "German", "French",
             "Russian", "Portuguese", "Spanish", "Italian")
FIELDS = frozenset({"kind", "text", "instruct", "voice", "language", "n", "seed", "client_ref"})
VOICE_FIELDS = frozenset({"wav_b64", "text"})
MAX_TEXT = 2000
MAX_TAKES = 8
MAX_SEED = 2**53 - 1                  # the cockpit's seed range (JavaScript-safe integers)
# The model card's guidance: a voice is taken from three seconds or more of speech.
MIN_REFERENCE_S = 3.0
MAX_BODY = 48 * 1024 * 1024
TERMINAL = frozenset({"done", "failed", "cancelled"})
# The pinned checkpoints (Hub revisions), resolved from the local cache only.
MODELS = {
    "voice-design": ("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", "5ecdb67327fd37bb2e042aab12ff7391903235d3"),
    "speak": ("Qwen/Qwen3-TTS-12Hz-1.7B-Base", "fd4b254389122332181a7c3db7f27e918eec64e3"),
}

log = logging.getLogger("voice.runner")


class Refused(ValueError):
    """A request the runner will not take, in plain words."""


class OutOfMemory(RuntimeError):
    """The engine ran out of video memory."""


@dataclass(frozen=True)
class Reference:
    """The take a `speak` job takes its voice from: PCM samples and their words."""
    pcm: bytes
    sample_rate: int
    channels: int
    sample_width: int
    text: str | None

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (self.sample_rate * self.channels * self.sample_width)


@dataclass(frozen=True)
class Spec:
    """A checked job: what the worker runs."""
    kind: str
    text: str
    instruct: str | None
    language: str
    n: int
    seed: int | None
    reference: Reference | None
    client_ref: str | None


def _text(body: dict, key: str, label: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{label} is missing.")
    if len(value) > MAX_TEXT:
        raise Refused(f"{label} has {len(value)} characters; the runner takes at most {MAX_TEXT}.")
    return value


def _reference(voice) -> Reference:
    if not isinstance(voice, dict):
        raise Refused("Speak needs a voice: the reference take's WAV bytes and its words.")
    unknown = sorted(set(voice) - VOICE_FIELDS)
    if unknown:
        raise Refused(f"Unknown voice field: {', '.join(unknown)}.")
    encoded = voice.get("wav_b64")
    if not isinstance(encoded, str) or not encoded:
        raise Refused("The voice take's bytes are missing.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Refused("The voice take's bytes are not valid base64.") from exc
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise Refused("The voice take is not a PCM WAV file.") from exc
    if not channels or not rate or width not in (1, 2, 3, 4):
        raise Refused("The voice take names no usable sample format.")
    words = voice.get("text")
    if words is not None and not isinstance(words, str):
        raise Refused("The voice take's words must be text.")
    words = (words or "").strip() or None
    found = Reference(pcm=pcm, sample_rate=rate, channels=channels, sample_width=width, text=words)
    if found.seconds < MIN_REFERENCE_S:
        raise Refused(f"The voice take is {found.seconds:.1f} seconds long; a voice is taken from at least "
                      f"{MIN_REFERENCE_S:g} seconds of speech. Design a longer preview line, or choose a longer take.")
    return found


def check(body) -> Spec:
    """The request as a job, or `Refused` naming what is wrong."""
    if not isinstance(body, dict):
        raise Refused("The body must be a JSON object.")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise Refused(f"Unknown field: {', '.join(unknown)}.")
    kind = body.get("kind")
    if kind not in KINDS:
        raise Refused(f"kind must be one of {', '.join(KINDS)}.")
    text = _text(body, "text", "The line to speak")
    language = body.get("language", "English")
    if language not in LANGUAGES:
        raise Refused(f"language must be one of {', '.join(LANGUAGES)}.")
    n = body.get("n", 1)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_TAKES:
        raise Refused(f"n must be a whole number from 1 to {MAX_TAKES}.")
    seed = body.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED):
        raise Refused(f"seed must be a whole number from 0 to {MAX_SEED}, or null.")
    client_ref = body.get("client_ref")
    if client_ref is not None and (not isinstance(client_ref, str) or len(client_ref) > 200):
        raise Refused("client_ref must be text of at most 200 characters.")
    if kind == "voice-design":
        if body.get("voice") is not None:
            raise Refused("Voice design takes a description, not a voice.")
        return Spec(kind, text, _text(body, "instruct", "The voice description"), language, n, seed, None, client_ref)
    if body.get("instruct") is not None:
        raise Refused("Speak takes its voice from a take, not a description.")
    return Spec(kind, text, None, language, n, seed, _reference(body.get("voice")), client_ref)


def wav(pcm: bytes, sample_rate: int) -> bytes:
    """16-bit mono PCM samples in a WAV container."""
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm)
    return out.getvalue()


class QwenBackend:
    """Qwen3-TTS from the local model snapshots, loaded for one job at a time.

    `design` and `speak` return 16-bit mono PCM per take, the sample rate and
    the facts of the run. The model is released after every job, whether it
    succeeded or not."""

    def __init__(self, paths: dict[str, str], device: str = "cuda:0") -> None:
        self._paths = paths
        self._device = device

    def models(self) -> dict:
        return {kind: Path(path).is_dir() for kind, path in self._paths.items()}

    def _load(self, kind: str):
        import torch
        from qwen_tts import Qwen3TTSModel
        return Qwen3TTSModel.from_pretrained(self._paths[kind], device_map=self._device,
                                             dtype=torch.bfloat16, attn_implementation="sdpa")

    def _run(self, kind: str, seed: int, generate) -> tuple[list[bytes], int, dict]:
        import gc

        import torch
        model, facts = None, {}
        try:
            started = time.perf_counter()
            model = self._load(kind)
            facts["load_s"] = round(time.perf_counter() - started, 2)
            torch.cuda.reset_peak_memory_stats()
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            torch.cuda.synchronize()
            started = time.perf_counter()
            wavs, rate, mode = generate(model)
            torch.cuda.synchronize()
            facts["generate_s"] = round(time.perf_counter() - started, 2)
            facts["peak_vram_mb"] = round(torch.cuda.max_memory_allocated() / 2**20)
            if mode:
                facts["clone_mode"] = mode
            return [pcm16(w) for w in wavs], int(rate), facts
        except RuntimeError as exc:              # torch.OutOfMemoryError is one
            if isinstance(exc, torch.OutOfMemoryError) or "out of memory" in str(exc).lower():
                raise OutOfMemory(str(exc)) from exc
            raise
        finally:
            del model
            gc.collect()
            torch.cuda.empty_cache()

    def design(self, spec: Spec, seed: int) -> tuple[list[bytes], int, dict]:
        def generate(model):
            wavs, rate = model.generate_voice_design(text=[spec.text] * spec.n, instruct=[spec.instruct] * spec.n,
                                                     language=[spec.language] * spec.n)
            return wavs, rate, None
        return self._run("voice-design", seed, generate)

    def speak(self, spec: Spec, seed: int) -> tuple[list[bytes], int, dict]:
        ref = spec.reference
        samples = floats(ref)

        def generate(model):
            # With the reference's words the engine continues the reference
            # (in-context cloning); without them it takes the speaker's voice only.
            mode = "in-context" if ref.text else "voice-only"
            prompt = model.create_voice_clone_prompt(ref_audio=(samples, ref.sample_rate), ref_text=ref.text,
                                                     x_vector_only_mode=ref.text is None)
            wavs, rate = model.generate_voice_clone(text=[spec.text] * spec.n, language=[spec.language] * spec.n,
                                                    voice_clone_prompt=prompt * spec.n)
            return wavs, rate, mode
        return self._run("speak", seed, generate)


def pcm16(samples) -> bytes:
    """Float samples in [-1, 1] as little-endian 16-bit PCM."""
    import numpy as np
    return (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()


def floats(ref: Reference):
    """The reference's samples as mono float32 in [-1, 1]."""
    import numpy as np
    width = ref.sample_width
    if width == 1:
        data = (np.frombuffer(ref.pcm, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 3:
        raw = np.frombuffer(ref.pcm, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        data = np.where(value >= 2**23, value - 2**24, value).astype(np.float32) / 2**23
    else:
        data = np.frombuffer(ref.pcm, dtype="<i2" if width == 2 else "<i4").astype(np.float32) / 2**(8 * width - 1)
    return data.reshape(-1, ref.channels).mean(axis=1) if ref.channels > 1 else data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Runner:
    """The job queue, its worker and its records."""

    def __init__(self, backend, state: Path) -> None:
        self.backend = backend
        self.jobs_dir = Path(state) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._specs: dict[str, Spec] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._running: str | None = None
        self._recover()
        self._worker = threading.Thread(target=self._work, name="voice-worker", daemon=True)
        self._worker.start()

    # --- records ---

    def _recover(self) -> None:
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("unreadable job record %s left as it is", path.name)
                continue
            if record.get("state") not in TERMINAL:
                record.update(state="failed", finished=_now(), error_kind="error",
                              error="The voice runner restarted before this job finished. Nothing was kept from it.")
                self._write(record)
            self._records[record["id"]] = record

    def _write(self, record: dict) -> None:
        path = self.jobs_dir / f"{record['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def _update(self, job_id: str, **fields) -> dict:
        with self._lock:
            record = self._records[job_id]
            record.update(fields)
            self._write(record)
            return dict(record)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            found = self._records.get(job_id)
            return dict(found) if found else None

    def health(self) -> dict:
        with self._lock:
            queued = sum(1 for r in self._records.values() if r["state"] == "queued")
        return {"ok": True, "engine": ENGINE, "version": VERSION, "busy": self._running is not None,
                "running": self._running, "queued": queued, "models": self.backend.models()}

    def take_path(self, job_id: str, index: int) -> Path | None:
        record = self.get(job_id)
        if not record or record["state"] != "done" or not 0 <= index < len(record.get("takes", [])):
            return None
        return self.jobs_dir / job_id / record["takes"][index]["file"]

    # --- requests ---

    def submit(self, spec: Spec) -> dict:
        job_id = uuid.uuid4().hex
        record = {"id": job_id, "engine": ENGINE, "kind": spec.kind, "state": "queued", "created": _now(),
                  "client_ref": spec.client_ref, "language": spec.language, "n": spec.n, "seed": spec.seed,
                  "text": spec.text, "instruct": spec.instruct, "runner_version": VERSION,
                  "model": MODELS[spec.kind][0],
                  "reference_seconds": round(spec.reference.seconds, 3) if spec.reference else None}
        with self._lock:
            self._records[job_id] = record
            self._specs[job_id] = spec
            self._write(record)
        self._queue.put(job_id)
        return dict(record)

    def cancel(self, job_id: str) -> tuple[int, dict]:
        """Queued work is cancelled at once; a running generation finishes and
        is recorded as cancelled, without its takes."""
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return 404, {"detail": "No such job."}
            if record["state"] == "queued":
                record.update(state="cancelled", finished=_now(), error="Cancelled before it started.")
                self._write(record)
                self._specs.pop(job_id, None)
            elif record["state"] == "running":
                record["cancel_requested"] = True
                self._write(record)
            return 200, dict(record)

    # --- the worker ---

    def stop(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                spec = self._specs.pop(job_id, None)
                record = self._records.get(job_id)
                if spec is None or record is None or record["state"] != "queued":
                    continue
                self._running = job_id
            try:
                self._run(job_id, spec)
            finally:
                self._running = None

    def _run(self, job_id: str, spec: Spec) -> None:
        seed = spec.seed if spec.seed is not None else secrets.randbelow(MAX_SEED + 1)
        self._update(job_id, state="running", started=_now(), seed=seed)
        started = time.perf_counter()
        try:
            run = self.backend.design if spec.kind == "voice-design" else self.backend.speak
            takes, rate, facts = run(spec, seed)
            if len(takes) != spec.n:
                raise RuntimeError(f"The engine returned {len(takes)} takes where {spec.n} were asked.")
        except OutOfMemory as exc:
            self._update(job_id, state="failed", finished=_now(), elapsed_s=round(time.perf_counter() - started, 2),
                         error_kind="oom", error="The voice engine ran out of video memory. Nothing was kept.",
                         tail=str(exc)[-800:])
            return
        except Exception as exc:                  # the engine's own failure, kept for the record
            self._update(job_id, state="failed", finished=_now(), elapsed_s=round(time.perf_counter() - started, 2),
                         error_kind="error", error=f"The voice engine failed ({type(exc).__name__}). Nothing was kept.",
                         tail="".join(traceback.format_exception(exc))[-800:])
            return
        elapsed = round(time.perf_counter() - started, 2)
        if self.get(job_id).get("cancel_requested"):
            self._update(job_id, state="cancelled", finished=_now(), elapsed_s=elapsed, **facts,
                         error="Cancelled while it was generating. Its takes were not kept.")
            return
        folder = self.jobs_dir / job_id
        folder.mkdir(exist_ok=True)
        files = []
        for index, pcm in enumerate(takes):
            name = f"take-{index + 1}.wav"
            temporary = folder / f"{name}.tmp"
            temporary.write_bytes(wav(pcm, rate))
            temporary.replace(folder / name)
            files.append({"index": index, "file": name, "seconds": round(len(pcm) / (2 * rate), 3)})
        self._update(job_id, state="done", finished=_now(), elapsed_s=elapsed, sample_rate=rate, takes=files, **facts)
        log.info("job %s %s: %d take(s) in %.2f s", job_id, spec.kind, len(files), elapsed)


class Handler(BaseHTTPRequestHandler):
    server_version = f"voice-runner/{VERSION}"

    @property
    def runner(self) -> Runner:
        return self.server.runner

    def log_message(self, fmt, *args) -> None:       # the runner's own log, not stderr noise
        log.debug("%s %s", self.address_string(), fmt % args)

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _parts(self) -> list[str]:
        return [part for part in self.path.split("?", 1)[0].split("/") if part]

    def do_GET(self) -> None:
        parts = self._parts()
        if parts == ["health"]:
            return self._json(200, self.runner.health())
        if len(parts) == 2 and parts[0] == "jobs":
            record = self.runner.get(parts[1])
            return self._json(200, record) if record else self._json(404, {"detail": "No such job."})
        if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "audio" and parts[3].isdigit():
            path = self.runner.take_path(parts[1], int(parts[3]))
            if path is None or not path.is_file():
                return self._json(404, {"detail": "No such take: the job is unknown, unfinished or has fewer takes."})
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self._json(404, {"detail": "No such route."})

    def do_POST(self) -> None:
        if self._parts() != ["jobs"]:
            return self._json(404, {"detail": "No such route."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._json(413 if length > MAX_BODY else 400,
                              {"detail": f"The body must be JSON of at most {MAX_BODY // 2**20} MB."})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"detail": "The body is not JSON."})
        try:
            spec = check(body)
        except Refused as exc:
            return self._json(422, {"detail": str(exc)})
        return self._json(202, {"job_id": self.runner.submit(spec)["id"]})

    def do_DELETE(self) -> None:
        parts = self._parts()
        if len(parts) != 2 or parts[0] != "jobs":
            return self._json(404, {"detail": "No such route."})
        status, body = self.runner.cancel(parts[1])
        return self._json(status, body)


def serve(runner: Runner, host: str, port: int) -> ThreadingHTTPServer:
    """A bound server for this runner (port 0 picks a free one); call serve_forever."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.runner = runner
    return server


def resolve(kind: str, given: str | None) -> str:
    """The model snapshot folder: the given one, else the pinned revision from the local cache."""
    if given:
        return given
    from huggingface_hub import snapshot_download
    repo, revision = MODELS[kind]
    try:
        return snapshot_download(repo, revision=revision, local_files_only=True)
    except Exception as exc:
        raise SystemExit(f"{repo} at revision {revision[:12]} is not in the local model cache. "
                         "Download it first (tools/tts/README.md).") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the local voice engine to the cockpit.")
    parser.add_argument("--host", default=os.environ.get("VOICE_RUNNER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VOICE_RUNNER_PORT", "8190")))
    parser.add_argument("--state", default=os.environ.get(
        "VOICE_RUNNER_STATE", str(Path.home() / ".local/state/crexil-voice-runner")))
    parser.add_argument("--design-model", default=os.environ.get("VOICE_RUNNER_DESIGN_MODEL"))
    parser.add_argument("--speak-model", default=os.environ.get("VOICE_RUNNER_SPEAK_MODEL"))
    parser.add_argument("--device", default=os.environ.get("VOICE_RUNNER_DEVICE", "cuda:0"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # The runner never reaches the network: models load from the local cache.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    paths = {"voice-design": resolve("voice-design", args.design_model),
             "speak": resolve("speak", args.speak_model)}
    runner = Runner(QwenBackend(paths, args.device), Path(args.state))
    server = serve(runner, args.host, args.port)
    log.info("voice runner %s serving %s on %s:%d", VERSION, ENGINE, args.host, server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runner.stop()


if __name__ == "__main__":
    main()
