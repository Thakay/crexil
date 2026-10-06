"""Execute bounded 3D jobs through the installed runner on the shared local lane.

Frozen mesh envelopes retain staged source paths, parameters and optional byte
hashes. Validated source bytes are authoritative; runner job references are only
caches. Generation, reduction and repaint use the same durable slot lifecycle.

Completed jobs yield their GLB plus retained companions or explicit missing-file
receipts. Free collection only retrieves existing work. Restart recovery fetches
completed jobs and cancels still-running jobs before reporting them gone; it does
not reattach or decide whether another attempt is allowed.

The scheduler owns writes, attempt limits and serialization with image engines.
No local processing bills a provider. Long cold starts have no executor deadline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config
from .executors import (CollectedOutput, DispatchRequest, EngineFailure, ExecutionResult, Handle, Lane,
                        Reconciliation, _OOM_TEXT)

log = logging.getLogger("cockpit.mesh")

# The engine ids the capability matrix declares as payload "3d",
# mapped to the runner's own names. The cockpit's ids are the product's;
# the runner's are funnel.py's.
ENGINE_TO_RUNNER = {"trellis2-4b": "trellis2", "hunyuan3d-21": "hunyuan",
                    "gltfpack": "gltfpack"}
MESH_ENGINES = frozenset(ENGINE_TO_RUNNER)

# Which engines serve which verb (law 16). The RUNNER is the authority
# (it refuses a pairing it does not serve, in words), and this mirror exists
# so the run route can refuse before a turn is created rather than after a
# slot has been dispatched. `configs/engines.json` publishes the same truth
# to the surfaces (law 12: badge, never filter).
MESH_KINDS: dict[str, frozenset[str]] = {
    "generate": frozenset({"trellis2-4b", "hunyuan3d-21"}),
    "decimate": frozenset({"gltfpack", "trellis2-4b"}),
    "texture": frozenset({"hunyuan3d-21"}),
}
# a kind whose input is a mesh, not an image — the verbs that take a `3d`
# parent, and the ones whose bytes travel by reference or raw upload
MESH_INPUT_KINDS = frozenset({"decimate", "texture"})
# WHICH artefact each (kind, engine) takes, mirroring the runner's own
# `KINDS[...]["input"]`. DECIMATE is the first verb whose engines
# differ: gltfpack collapses the finished GLB, TRELLIS.2 re-extracts from the
# voxel field the parent take kept. That mapping IS the routing rule — one
# verb, and the engine follows from which artefact the parent has.
MESH_KIND_INPUTS: dict[str, str | dict[str, str]] = {
    "generate": "image",
    "decimate": {"gltfpack": "mesh", "trellis2-4b": "fields"},
    "texture": "mesh",
}
# The kinds that take a REFERENCE IMAGE beside their mesh: the first
# two-parent verb. The reference is a real parent node (shape locked,
# appearance branching), resolved by the route and carried in the envelope
# as a second staged path; the executor sends it as image_b64, the same
# field the generate kind reads.
MESH_REF_KINDS = frozenset({"texture"})
# the sidecar a TRELLIS.2 take lands beside its GLB, and the ONE place its
# name is written down — the tray's byte count, the purge and the executor's
# upload all derive it from the artefact (`api_v4._artefact_paths`)
FIELDS_SUFFIX = ".fields.npz"


def mesh_kind_input(kind: str, engine: str) -> str:
    """The artefact this (kind, engine) pair takes. A kind whose engines all
    take the same thing names it once; `decimate` names one per engine."""
    spec = MESH_KIND_INPUTS.get(kind, "image")
    return spec if isinstance(spec, str) else spec.get(engine, "mesh")


def fields_sidecar(artefact: str | Path) -> Path:
    """Where a mesh node's voxel field lies, if it kept one. Derived, never
    stored: one rule, and `_artefact_paths` reads the same one so purge frees
    exactly what the tray counted."""
    return Path(artefact).with_suffix(FIELDS_SUFFIX)
# gltfpack is deterministic; a kind that reads no seed is never sent one, so
# the ledger never records a seed that did nothing (law 12's honesty, applied
# to the as-run record)
SEEDED_KINDS = frozenset({"generate"})

TERMINAL = {"done", "failed", "cancelled"}


def is_mesh_payload(payload: dict | None) -> bool:
    """A submitted_graph that is a mesh envelope, not a ComfyUI graph — the
    scheduler's routing peek, mirroring `is_region_payload`."""
    return isinstance(payload, dict) and "mesh_job" in payload


def mesh_envelope(engine: str, source_path: str, *, kind: str = "generate",
                  seed: int | None = None, params: dict | None = None,
                  mesh_job_ref: str | None = None,
                  ref_path: str | None = None) -> dict:
    """The one place the envelope's shape is written down. `source_path` is
    a path in the cockpit's OWN staging volume — the runner never reads it,
    the executor does, and the bytes cross the wire (the container and the
    host share no working filesystem, by design).

    `kind` is the verb: `generate` sends the source as an image,
    a mesh-input kind sends it as a mesh. `mesh_job_ref` is the parent take's
    runner job id where one is known — an optimisation, never a requirement:
    it lets the runner reuse bytes it already has, and its absence (or
    expiry) costs one upload, never a failure.

    `ref_path` is the SECOND parent's staged path: the reference
    image a texture take paints from. Only a MESH_REF_KINDS kind carries one;
    `source_path` keeps its one meaning (the acted-on artefact) either way."""
    return {"mesh_job": {"engine": engine, "kind": kind,
                         "source_path": source_path,
                         "mesh_job_ref": mesh_job_ref,
                         "ref_path": ref_path,
                         "seed": seed, "params": params or {}}}


def mesh_verb(kind: str) -> str:
    """The `verb` a landed child records for a kind.

    `generate` keeps `to_3d`: the verb every landed image → mesh node
    records, in the store and in the lightbox's fixtures; renaming it would
    change stored records to say the same thing. Every other kind
    IS its verb, which is what makes a child readable as "this is what was
    done to its parent"."""
    return "to_3d" if kind == "generate" else kind


def parent_runner_job(node: dict | None) -> str | None:
    """The runner job id a parent mesh node landed with, when it has one.

    A verb passes it along so the runner can reuse bytes it already holds
    (`runs/<id>/`); it is an optimisation with a TTL behind it, so None and
    a stale id are both ordinary — the executor uploads instead."""
    if not node or node.get("payload") != "3d" or (node.get("dials") or {}).get("crexil_restore"):
        return None
    job = (node.get("submitted_graph") or {}).get("mesh_job") or {}
    ref = job.get("runner_job_id")
    return ref if isinstance(ref, str) and ref else None


def classify_engine_error(text: str | None) -> str:
    """`oom` when the engine ran out of video memory, else `error` — A1/A11's
    face names the card on the 3D lane too (the mesh engines are the biggest
    allocations on the card). Reuses the image lane's own text table so
    one truth serves both."""
    low = (text or "").lower()
    return "oom" if any(t in low for t in _OOM_TEXT) else "error"


@dataclass
class MeshHandle(Handle):
    """`job_ref` is the runner's job id — recorded on the slot row the moment
    it exists, which is what makes recovery a lookup."""
    engine: str = ""
    request: DispatchRequest | None = None


class MeshExecutor:
    """Local lane executor over the host-side 3D runner.

    Owns its own httpx client (the ComfyExecutor's rule, for the same
    reason: nothing else's lifetime should decide when this one closes).
    """

    name: str = "mesh"
    lane: Lane = "local"

    def __init__(self, base_url: str | None = None) -> None:
        self._base = (base_url or config.THREED_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle ---

    async def open(self) -> None:
        if self._client is None:
            # no read timeout on the poll: the runner answers immediately,
            # but a GLB can be tens of MB over the loopback relay
            self._client = httpx.AsyncClient(base_url=self._base,
                                             timeout=httpx.Timeout(60.0,
                                                                   connect=5.0))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise EngineFailure("unreachable",
                                "the 3D runner client was never opened")
        return self._client

    def _unreachable(self, e: Exception, when: str) -> EngineFailure:
        """Unreachable, on this lane: down is a bench with actionable copy,
        never a crash. The words name the address and the checkout's own start
        script, never a service unit another installation may own."""
        return EngineFailure(
            "unreachable",
            f"the 3D engine runner is not answering at {self._base} {when} "
            f"— start this checkout's runner there (scripts/3d-runner.sh); "
            f"the image engines beside this one are unaffected",
            detail=str(e)[:600])

    # --- the four verbs ---

    async def start(self, req) -> MeshHandle:
        job = (req.submitted_graph or {}).get("mesh_job") or {}
        frozen_sources = self._checked_sources(job)
        engine = job.get("engine") or req.engine
        kind = job.get("kind") or "generate"
        runner_engine = ENGINE_TO_RUNNER.get(engine)
        if runner_engine is None:
            raise EngineFailure(
                "error", f"'{engine}' is not a mesh engine — this lane "
                         f"serves {', '.join(sorted(MESH_ENGINES))}")
        # WHICH artefact this (kind, engine) reads: the staged GLB
        # for gltfpack, its voxel-field sidecar for TRELLIS.2 re-extraction.
        takes = mesh_kind_input(kind, engine)
        source = Path(job.get("source_path") or "")
        if takes == "fields":
            source = fields_sidecar(source)
        try:
            data = frozen_sources[str(source)] if str(source) in frozen_sources else source.read_bytes()
        except OSError as e:
            # law 7 read from the other side: the source take's bytes are
            # gone, so say which artefact rather than failing the engine's
            # name
            if takes == "fields":
                # a different fact from a missing file, and the only one the
                # artist can act on: this mesh never kept a field, so this
                # engine can never serve it — the other one can
                raise EngineFailure(
                    "error", "this mesh kept no voxel field, so TRELLIS.2 has "
                             "nothing to re-extract from — decimate it with "
                             "gltfpack, or generate it again to keep one",
                    detail=f"{source}: {e}") from e
            what = ("mesh" if kind in MESH_INPUT_KINDS else "source image")
            raise EngineFailure(
                "error", f"the {what} this take was built from is no longer "
                         f"on disk — reroll the take it came from",
                detail=f"{source}: {e}") from e

        body: dict = {"kind": kind, "engine": runner_engine,
                      "seed": job.get("seed") if kind in SEEDED_KINDS else None,
                      "params": job.get("params") or {},
                      "client_ref": req.slot_id}
        by_ref = False
        if takes != "image":
            ref = job.get("mesh_job_ref")
            if ref:
                # the runner probably still holds the parent's bytes in its
                # own runs/ — try that before pushing tens of megabytes it
                # already has (a field set runs 21–92 MB, so this matters more
                # here than it ever did for a GLB)
                body[takes] = {"job": ref}
                by_ref = True
            else:
                body[takes] = {"upload": await self._upload_mesh(data, takes)}
        else:
            body["image_b64"] = base64.b64encode(data).decode()

        if kind in MESH_REF_KINDS:
            # The second parent: the reference image, staged like any
            # take, sent as image_b64 beside the mesh reference. Its absence
            # is the reference's failure, named as such, never the mesh's.
            ref_source = Path(job.get("ref_path") or "")
            try:
                ref_bytes = frozen_sources[str(ref_source)] if str(ref_source) in frozen_sources else ref_source.read_bytes()
            except OSError as e:
                raise EngineFailure(
                    "error", "the reference image this take paints from is "
                             "no longer on disk — reroll the take it came "
                             "from, or pick another reference",
                    detail=f"{ref_source}: {e}") from e
            body["image_b64"] = base64.b64encode(ref_bytes).decode()

        r = await self._post_job(body)
        if r.status_code == 422 and by_ref:
            # The runner validates kind, engine and params BEFORE it resolves
            # a mesh reference, so a 422 at this point is about the reference
            # — the record aged out (`THREED_RECORD_TTL_S`) or its file went.
            # The cockpit holds the artefact of record, so it just sends it.
            log.info("mesh: runner no longer holds job %s — uploading the "
                     "staged %s instead (%s)", job.get("mesh_job_ref"),
                     takes, _detail(r))
            body[takes] = {"upload": await self._upload_mesh(data, takes)}
            r = await self._post_job(body)
        if r.status_code == 422:
            # the runner refused the request itself — its words are already
            # plain and name the parameter
            raise EngineFailure("error", _detail(r))
        if r.status_code >= 400:
            raise EngineFailure("error",
                                f"the 3D runner refused this take "
                                f"({r.status_code}) — {_detail(r)}")
        return MeshHandle(job_ref=(r.json() or {}).get("job_id"),
                          engine=engine, request=req)

    @staticmethod
    def _checked_sources(job: dict) -> dict[str, bytes]:
        """Validate frozen source receipts before cache reuse or any upload."""
        if 'source_hashes' not in job:
            return {}
        receipts = job['source_hashes']
        if not isinstance(receipts, list):
            raise EngineFailure('error', 'The frozen mesh source receipts are invalid.')
        checked = {}
        for receipt in receipts:
            if not isinstance(receipt, dict) or not isinstance(receipt.get('path'), str) or not isinstance(receipt.get('sha256'), str):
                raise EngineFailure('error', 'The frozen mesh source receipts are invalid.')
            path = Path(receipt['path'])
            try:
                blob = path.read_bytes()
            except OSError as exc:
                raise EngineFailure('error', 'A frozen mesh source is no longer available. Review the preparation again.',
                                    detail=str(path)) from exc
            if hashlib.sha256(blob).hexdigest() != receipt['sha256']:
                raise EngineFailure('error', 'A frozen mesh source changed. Review the preparation again.', detail=str(path))
            checked[str(path)] = blob
        return checked

    async def _post_job(self, body: dict) -> httpx.Response:
        try:
            return await self._http.post("/jobs", json=body)
        except httpx.HTTPError as e:
            raise self._unreachable(e, "at the door") from e

    # the runner's raw-upload routes, one per artefact
    _UPLOAD = {"mesh": ("/meshes", "model/gltf-binary", "mesh_id", "mesh"),
               "fields": ("/fields", "application/octet-stream", "fields_id",
                          "field set")}

    async def _upload_mesh(self, data: bytes, what: str = "mesh") -> str:
        """Push an artefact to the runner's raw route and answer its id.

        Raw, not base64 in the job body: a 35 MB mesh is a third again as
        many bytes encoded, and a field set of the same order costs the same
        third — the runner would parse the whole JSON document to recover
        what was already on the wire."""
        route, ctype, id_key, noun = self._UPLOAD[what]
        try:
            r = await self._http.post(
                route, content=data, headers={"Content-Type": ctype},
                timeout=httpx.Timeout(300.0, connect=5.0))
        except httpx.HTTPError as e:
            raise self._unreachable(e, f"while sending the {noun}") from e
        if r.status_code >= 400:
            raise EngineFailure("error",
                                f"the 3D runner would not take this {noun} — "
                                f"{_detail(r)}")
        mesh_id = (r.json() or {}).get(id_key)
        if not mesh_id:
            raise EngineFailure("error",
                                f"the 3D runner accepted the {noun} but named "
                                f"no id for it")
        return mesh_id

    async def wait(self, h: MeshHandle) -> ExecutionResult:
        if not h.job_ref:
            raise EngineFailure("error", "the 3D runner gave no job id")
        while True:
            try:
                rec = await self._fetch(h.job_ref)
            except httpx.HTTPError as e:
                raise self._unreachable(e, "while the mesh was running") from e
            if rec is None:
                raise EngineFailure(
                    "error", "the 3D runner has no record of this job any "
                             "more — it may have been restarted mid-take")
            state = rec.get("state")
            if state in TERMINAL:
                return await self._land(h, rec)
            await asyncio.sleep(config.THREED_POLL_S)

    async def cancel(self, h: MeshHandle) -> None:
        """Best-effort and safe against already-finished work — the runner
        terminates the engine's whole process group, since the venv python
        spawns children of its own."""
        if not h.job_ref:
            return
        with suppress(httpx.HTTPError):   # wait() surfaces what actually happened
            await self._http.delete(f"/jobs/{h.job_ref}")

    async def reconcile(self, slot: dict, *, request=None) -> Reconciliation:
        """Recovery is a LOOKUP, which is the whole reason the runner keeps
        job records. Two verdicts are deliberate rather than obvious:

        - **Unreachable answers `unverifiable`, never raises.** A raised
          EngineFailure benches `lane:local` in `_recover`, which would stop
          the ComfyUI takes beside this one. Falling through to a requeue
          instead lets the ordinary failure path handle it, where
          `bench_scope_for` scopes a mesh's unreachable to `engine:` — one
          dead engine never stops the others.
        - **A still-running job is cancelled, then requeued.** The scheduler
          has no re-attach, so the alternative is a duplicate mesh queued
          behind an orphan nobody is waiting on. Cancelling bounds the waste
          to what had already been spent, and leaves nothing running that no
          slot owns."""
        job_ref = slot.get("job_ref")
        if not job_ref:
            return Reconciliation("gone")     # crashed pre-submit; safe to requeue
        try:
            rec = await self._fetch(job_ref)
        except httpx.HTTPError:
            return Reconciliation("unverifiable")
        if rec is None:
            return Reconciliation("gone")
        state = rec.get("state")
        if state not in TERMINAL:
            with suppress(httpx.HTTPError):
                await self._http.delete(f"/jobs/{job_ref}")
            return Reconciliation("gone")
        if state == "done":
            try:
                result = await self._land(MeshHandle(job_ref=job_ref, request=request), rec)
            except (httpx.HTTPError, EngineFailure) as e:
                return Reconciliation("failed",
                                      cause=f"the mesh landed but could not "
                                            f"be fetched during recovery: {e}")
            return Reconciliation("landed",
                                  result=result)
        cause, kind = _failure_words(rec)
        return Reconciliation("failed", cause=cause, failure_kind=kind)

    async def collect_existing(self, slot: dict, *, request=None) -> Reconciliation:
        """Retrieve a completed job without submitting or cancelling any work."""
        job_ref = slot.get('job_ref')
        if not job_ref:
            return Reconciliation('gone')
        try:
            rec = await self._fetch(job_ref)
        except httpx.HTTPError:
            return Reconciliation('unverifiable')
        if rec is None:
            return Reconciliation('gone')
        if rec.get('state') not in TERMINAL:
            return Reconciliation('unverifiable')
        try:
            result = await self._land(MeshHandle(job_ref=job_ref, request=request), rec)
        except EngineFailure as exc:
            return Reconciliation('failed', cause=str(exc), failure_kind=exc.kind)
        return Reconciliation('landed', result=result)

    async def verify_charge(self, _job_ref: str) -> float | None:
        return None                      # nothing bills on the GPU

    # --- internals ---

    async def _fetch(self, job_ref: str) -> dict | None:
        r = await self._http.get(f"/jobs/{job_ref}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def _glb(self, job_ref: str) -> bytes:
        r = await self._http.get(f"/jobs/{job_ref}/glb")
        if r.status_code >= 400:
            raise EngineFailure("error",
                                f"the mesh file could not be read back from "
                                f"the runner — {_detail(r)}")
        data = r.content
        if not data:
            raise EngineFailure("error",
                                "the runner returned an empty mesh file")
        return data

    async def _land(self, h: MeshHandle, rec: dict) -> ExecutionResult:
        state = rec.get("state")
        if state == "cancelled":
            # our own cancel is never the engine's fault
            raise EngineFailure("interrupted",
                                rec.get("error") or "the mesh was cancelled")
        if state == "failed":
            cause, kind = _failure_words(rec)
            raise EngineFailure(kind, cause, detail=_tail_detail(rec))
        on_output = h.request.on_output if h.request else None
        primary = CollectedOutput(identity='primary', filename=f"{rec.get('engine')}.glb", payload='3d',
                                  descriptor={'runner_job_id': h.job_ref, 'route': 'glb'})
        cached = await on_output(primary) if on_output else None
        try:
            data = cached if cached is not None else await self._glb(h.job_ref or "")
        except httpx.HTTPError as e:
            raise self._unreachable(e, "while fetching the mesh") from e
        fields, error = await self._fields(h.job_ref or "", rec)
        result = self._result(rec, data, fields)
        if on_output:
            await on_output(CollectedOutput(identity=primary.identity, filename=primary.filename,
                data=data, payload='3d', descriptor=primary.descriptor, submitted_graph=result.submitted_graph))
            if error:
                await on_output(CollectedOutput(identity='companion:' + FIELDS_SUFFIX,
                    filename='companion' + FIELDS_SUFFIX, payload=None, role='companion',
                    descriptor={'runner_job_id': h.job_ref, 'route': 'fields',
                                'advertised_path': (rec.get('result') or {}).get('fields')}, error=error))
        return result

    async def _fields(self, job_ref: str, rec: dict) -> tuple[bytes | None, str | None]:
        """Keep advertised fields or an explicit retryable missing receipt.

        A mesh without advertised fields is complete without a companion.
        A failed companion fetch never discards its usable GLB.
        """
        if not (rec.get("result") or {}).get("fields"):
            return None, None
        try:
            r = await self._http.get(f"/jobs/{job_ref}/fields",
                                     timeout=httpx.Timeout(300.0, connect=5.0))
        except httpx.HTTPError as e:
            log.warning("mesh: job %s kept a field set but it could not be "
                        "fetched (%s) — the mesh lands without it", job_ref, e)
            return None, f'The runner advertised voxel fields, but retrieval failed: {e}'
        if r.status_code >= 400 or not r.content:
            log.warning("mesh: job %s kept a field set but the runner would "
                        "not serve it (%s)", job_ref, r.status_code)
            return None, f'The runner advertised voxel fields, but returned {r.status_code} with {len(r.content)} bytes.'
        return r.content, None

    def _result(self, rec: dict, data: bytes,
                fields: bytes | None = None) -> ExecutionResult:
        """The as-run record rides `submitted_graph` (the region
        executor's convention): the engine's own args, the validation the
        funnel measured, and where the turntable frames live on the host —
        which is what the slot's body is drawn from."""
        result = rec.get("result") or {}
        engine = rec.get("engine")
        return ExecutionResult(
            data=data,
            payload="3d",
            filename=f"{engine}.glb",
            sidecars={FIELDS_SUFFIX: fields} if fields else None,
            submitted_graph={"mesh_job": {
                "engine": engine,
                "kind": rec.get("kind") or "generate",
                # which artefact this take READ, and how it was referenced —
                # `decimate` reads a GLB from one engine and a field set from
                # the other, so naming the type is the honest record
                "input": rec.get("input"),
                "input_ref": rec.get("input_ref"),
                # and whether a reference image rode beside it: the
                # second parent's mark on the as-run record
                "ref": rec.get("ref"),
                # and whether it KEPT one, which is what decides whether
                # re-extraction can ever be offered on this node
                "fields_kept": fields is not None,
                "runner_job_id": rec.get("id"),
                "seed": rec.get("seed"),
                "params": rec.get("params") or {},
                "engine_args": rec.get("engine_args") or [],
                "cutout": result.get("cutout"),
                "validation": result.get("validation"),
                "turntable": result.get("turntable"),
                "glb_sha256": result.get("glb_sha256"),
                "engine_wall_s": result.get("engine_wall_s"),
                "runner_version": rec.get("runner_version")}},
            detail={"runner_job_id": rec.get("id"),
                    "elapsed_s": rec.get("elapsed_s")})


def _detail(r: httpx.Response) -> str:
    try:
        return str((r.json() or {}).get("detail") or r.text)[:600]
    except (json.JSONDecodeError, ValueError):
        return r.text[:600]


def _tail_detail(rec: dict) -> str:
    """The engine's own last words — the failed cell's collapsed DETAILS
    (law 3: the plain cause on the face, the raw text behind it)."""
    return str(rec.get("tail") or "")[:800]


def _failure_words(rec: dict) -> tuple[str, str]:
    """(cause, kind) for a failed runner record."""
    cause = str(rec.get("error") or "the mesh engine failed without saying "
                                    "why")[:800]
    return cause, classify_engine_error(f"{cause}\n{rec.get('tail') or ''}")
