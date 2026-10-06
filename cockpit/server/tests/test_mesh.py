"""The MeshExecutor against a scripted 3D runner (app/mesh.py).

The runner is mocked at the transport, so these are the executor's own
contracts: what it sends, what it makes of every answer, and the two
recovery verdicts that are deliberate rather than obvious.

    start       the envelope becomes one POST /jobs — engine translated to
                the runner's name, source read from disk and base64'd,
                slot id carried as client_ref
    refusals    a source that is gone, an engine the lane doesn't serve,
                a runner that refuses at the door, a runner that is down
    wait        polls to a terminal state; done fetches the GLB and puts
                the as-run record on submitted_graph
    failures    the runner's words ride through; an OOM in the engine's
                own output classifies as `oom`, not `error`; our own
                cancel is `interrupted`, never the engine's fault
    reconcile   landed / failed / gone from the job record — and the two
                calls: unreachable answers `unverifiable` (never raises,
                because raising benches the whole local lane), a still-
                running job is CANCELLED then requeued (no orphan)
"""

from __future__ import annotations

import base64
import json
import struct

import httpx
import pytest

from app.executors import DispatchRequest, EngineFailure
from app.mesh import (ENGINE_TO_RUNNER, MESH_ENGINES, MESH_INPUT_KINDS,
                      FIELDS_SUFFIX, MESH_KINDS, MESH_REF_KINDS, SEEDED_KINDS,
                      MeshExecutor, fields_sidecar, mesh_kind_input,
                      classify_engine_error, is_mesh_payload, mesh_envelope,
                      mesh_verb, parent_runner_job)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


GLB = struct.pack("<III", 0x46546C67, 2, 12)


def source(tmp_path, data=b"\x89PNG\r\n\x1a\nsource-bytes"):
    p = tmp_path / "source.png"
    p.write_bytes(data)
    return p


class Runner:
    """A scripted 3D runner. `records` is what GET /jobs/<id> answers, in
    order — the last entry repeats, so a poll settles."""

    def __init__(self, records, *, post=None, glb=GLB, fields=None):
        self.records = list(records)
        self.post = post or (lambda body: (202, {"job_id": "job-1",
                                                 "state": "queued"}))
        self.glb = glb
        self.fields = fields             # the voxel field it serves
        self.posted: list[dict] = []
        self.uploaded: list[bytes] = []
        self.uploaded_fields: list[bytes] = []
        self.deleted: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path == "/jobs":
                body = json.loads(request.content)
                self.posted.append(body)
                code, payload = self.post(body)
                return httpx.Response(code, json=payload)
            if request.method == "POST" and path == "/meshes":
                # the RAW route: bytes on the wire, not base64 JSON
                self.uploaded.append(request.content)
                return httpx.Response(201, json={"mesh_id": "up-1",
                                                 "bytes": len(request.content)})
            if request.method == "POST" and path == "/fields":
                # the same shape for the second artefact
                self.uploaded_fields.append(request.content)
                return httpx.Response(201, json={"fields_id": "fld-1",
                                                 "bytes": len(request.content)})
            if request.method == "DELETE" and path.startswith("/jobs/"):
                self.deleted.append(path.rsplit("/", 1)[-1])
                return httpx.Response(200, json={"cancel_requested": True})
            if path.endswith("/fields"):
                if self.fields is None:
                    return httpx.Response(404, json={"detail": "no fields"})
                return httpx.Response(200, content=self.fields)
            if path.endswith("/glb"):
                if self.glb is None:
                    return httpx.Response(404, json={"detail": "no mesh"})
                return httpx.Response(200, content=self.glb)
            if path.startswith("/jobs/"):
                if not self.records:
                    return httpx.Response(404, json={"detail": "no job"})
                rec = (self.records.pop(0) if len(self.records) > 1
                       else self.records[0])
                if rec is None:
                    return httpx.Response(404, json={"detail": "no job"})
                return httpx.Response(200, json=rec)
            return httpx.Response(404, json={"detail": "no route"})
        return httpx.MockTransport(handle)


def executor(runner: Runner) -> MeshExecutor:
    ex = MeshExecutor("http://runner.test")
    ex._client = httpx.AsyncClient(transport=runner.transport(),
                                   base_url="http://runner.test")
    return ex


def request(tmp_path, engine="trellis2-4b", **kw) -> DispatchRequest:
    return DispatchRequest(
        slot_id="slot-1", engine=engine, payload="3d", verb="to_3d",
        submitted_graph=mesh_envelope(engine, str(source(tmp_path)),
                                      seed=7, **kw))


def done(**over) -> dict:
    return {"id": "job-1", "state": "done", "engine": "trellis2",
            "seed": 7, "params": {}, "engine_args": [], "elapsed_s": 171.4,
            "runner_version": 1,
            "result": {"glb": "/host/runs/job-1/trellis2/trellis.glb",
                       "glb_sha256": "abc123",
                       "validation": {"triangles": 900_000,
                                      "texture_present": True,
                                      "file_mb": 31.2},
                       "turntable": {"frames": 30, "size": 640,
                                     "webp": "/host/runs/job-1/tt/"
                                             "turntable.webp"},
                       "cutout": {"method": "source-alpha"},
                       "engine_wall_s": 168.0}, **over}


# --- the envelope + the routing peek ---

def test_the_envelope_is_told_apart_from_a_comfy_graph():
    assert is_mesh_payload(mesh_envelope("trellis2-4b", "/s.png")) is True
    assert is_mesh_payload({"1": {"class_type": "KSampler"}}) is False
    assert is_mesh_payload(None) is False


def test_every_matrix_mesh_engine_has_a_runner_name():
    # the product's ids and funnel.py's names are different vocabularies;
    # a mesh engine with no translation would 'error' at the door
    assert set(ENGINE_TO_RUNNER) == {"trellis2-4b", "hunyuan3d-21", "gltfpack"}
    assert set(ENGINE_TO_RUNNER.values()) == {"trellis2", "hunyuan", "gltfpack"}


def test_every_engine_that_serves_a_verb_is_a_mesh_engine():
    # law 16: MESH_KINDS is the route's mirror of the runner's registry.
    # An engine listed against a verb but absent from the translation table
    # would be offerable and then fail at the door.
    for kind, engines in MESH_KINDS.items():
        assert engines, f"{kind} has no engine"
        assert engines <= MESH_ENGINES, f"{kind} names an unknown engine"
    assert set(MESH_KINDS) >= MESH_INPUT_KINDS
    assert set(MESH_KINDS) >= SEEDED_KINDS
    # generate is the only kind that reads an image, and the only one with a
    # seed to read — the two facts the route branches on
    assert set(MESH_KINDS) - MESH_INPUT_KINDS == {"generate"} == SEEDED_KINDS


def _runner_kinds() -> dict:
    """The runner's own KINDS registry, read as SOURCE — its venv is not
    importable from here, and that separation is the point (the engines
    live on the host). Validators are callables, so the shape is walked rather
    than literal_eval'd; every leaf a test asks about is a constant."""
    import ast
    from pathlib import Path

    def shape(n):
        if isinstance(n, ast.Dict):
            return {shape(k): shape(v) for k, v in zip(n.keys, n.values, strict=True)}
        if isinstance(n, (ast.Tuple, ast.List)):
            return tuple(shape(e) for e in n.elts)
        if isinstance(n, ast.Constant):
            return n.value
        return "<validator>"      # a lambda or an _int_in(...) call

    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / "tools/3d/runner.py").read_text())
    return next(shape(n.value) for n in ast.walk(tree)
                if isinstance(n, ast.AnnAssign)
                and getattr(n.target, "id", None) == "KINDS")


def test_the_published_verbs_agree_with_the_route_and_the_runner():
    """Three carriers hold the verb→engine truth and they must not
    drift. `tools/3d/runner.py` KINDS is the authority (it refuses at the
    door), `MESH_KINDS` is the route's mirror (so a pairing is refused before
    a turn exists), and `configs/engines.json` `verbs` is the published copy
    the roster badges from. One home per fact is kept by this test
    rather than by a shared import: the runner is a different interpreter in
    a different venv, so the mirror is a real copy — this is what keeps it
    honest."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]

    matrix = json.loads((root / "configs/engines.json").read_text())
    published: dict[str, set[str]] = {}
    for e in matrix["engines"]:
        if e["payload"] == "audio":
            continue                     # audio verbs: test_audio_contract
        for verb in e.get("verbs") or []:
            published.setdefault(verb, set()).add(e["id"])
        if e["payload"] != "3d":
            assert not e.get("verbs"), (
                f"{e['id']} is an image engine — the image lane's steps are "
                "presets, not engine-declared verbs")
    assert published == {k: set(v) for k, v in MESH_KINDS.items()}

    kinds = _runner_kinds()
    # engine names are funnel.py's, so translate before comparing
    runner_side = {k: {ENGINE_TO_RUNNER[e] for e in v}
                   for k, v in MESH_KINDS.items()}
    assert {k: set(spec["engines"]) for k, spec in kinds.items()} == runner_side
    # and the policies the route branches on
    assert {k for k, spec in kinds.items() if spec["seed"]} == set(SEEDED_KINDS)

    # `input` is per (kind, engine) once a verb's engines differ:
    # DECIMATE's gltfpack collapses a GLB, its trellis2 re-extracts from a
    # field set. Normalise both sides to {kind: {engine: artefact}} and hold
    # them equal; a kind whose engines agree still names its input once.
    def spread(spec: dict, engines) -> dict:
        inp = spec["input"] if isinstance(spec, dict) else spec
        return ({e: inp for e in engines} if isinstance(inp, str)
                else dict(inp))

    runner_inputs = {k: spread(spec, spec["engines"])
                     for k, spec in kinds.items()}
    route_inputs = {k: {ENGINE_TO_RUNNER[e]: mesh_kind_input(k, e) for e in v}
                    for k, v in MESH_KINDS.items()}
    assert runner_inputs == route_inputs
    # and MESH_INPUT_KINDS still names exactly the verbs that take a `3d`
    # parent — every engine of such a kind reads an artefact, never an image
    assert {k for k, v in route_inputs.items()
            if "image" not in v.values()} == set(MESH_INPUT_KINDS)

    # the kinds that take a reference image BESIDE their artefact:
    # the route requires a `reference` parent exactly where the runner
    # requires image_b64, or the request dies at the wrong door
    assert {k for k, spec in kinds.items()
            if spec.get("ref") == "image"} == set(MESH_REF_KINDS)
    assert set(MESH_INPUT_KINDS) >= MESH_REF_KINDS


def test_the_published_params_are_handles_the_runner_actually_takes():
    """`configs/engines.json` carries a `params` spec (the handles a
    surface may OFFER for a verb), and it sits one carrier away from the
    runner's argv whitelist, so it can drift the same way `verbs` could.

    The invariant is NOT equality, unlike the verbs guard. The runner accepts
    more than the product offers on purpose: `decimation_target` is a real
    TRELLIS.2 generate param whose job belongs to the DECIMATE verb, so
    it stays in the whitelist and out of the fold. What must hold is that
    everything offered is something the runner takes — an offered key the
    runner would refuse by name is a control that 422s the moment it is
    touched, which is exactly the law 12 failure (offering what the route
    refuses) the roster guard was written to prevent."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    matrix = json.loads((root / "configs/engines.json").read_text())
    kinds = _runner_kinds()

    for e in matrix["engines"]:
        params = e.get("params") or {}
        if e["payload"] == "audio":
            continue                     # audio params: test_audio_contract
        if e["payload"] != "3d":
            assert not params, (
                f"{e['id']} is an image engine — its handles are the preset's "
                "`inputs`, not an engine-declared params spec")
            continue
        for verb, knobs in params.items():
            assert verb in (e.get("verbs") or []), (
                f"{e['id']} publishes {verb} params but doesn't declare the verb")
            whitelist = kinds[verb]["engines"][ENGINE_TO_RUNNER[e["id"]]]
            for key, spec in knobs.items():
                if spec.get("supported") is False:
                    # the other direction of the same lie: a row saying "this
                    # engine has no such handle" while the runner takes it
                    assert key not in whitelist, (
                        f"{e['id']} publishes {key} as unsupported, but the "
                        "runner takes it — one of the two is wrong")
                    assert spec.get("why"), (
                        f"{e['id']}.{key} is dashed with no reason — law 12 asks "
                        "for the words, not just the absence")
                    continue
                assert key in whitelist, (
                    f"{e['id']} offers {key} for {verb}, which the runner "
                    f"refuses by name — it takes {', '.join(whitelist)}")
                # a control the surface can draw and a line it can print
                assert spec["default"] in spec["choices"], (
                    f"{e['id']}.{key} defaults to a value that is not one of "
                    "its choices")
                assert spec["state"] in ("verified", "unverified")
                # law 12's other half: a badge that claims proof names it
                assert spec["state"] == "unverified" or spec.get("evidence"), (
                    f"{e['id']}.{key} is badged verified with no evidence — "
                    "say what was run, or leave it unverified")
                assert spec["label"] and spec["effect"], (
                    f"{e['id']}.{key} needs a label and a plain-effect line "
                    "(permanently visible, never a tooltip)")


def test_decimate_declares_the_force_override_and_nothing_else_new():
    """`-sa` is an opt-in switch. The runner whitelists argv by
    name, so a surface can only offer what this row declares — and the target
    stays required, because a decimate with no count has nothing to do."""
    dec = _runner_kinds()["decimate"]
    assert dec["required"] == ("target_triangles",)
    assert set(dec["engines"]["gltfpack"]) == {"target_triangles", "force"}
    assert dec["engines"]["gltfpack"]["force"][:2] == ("switch",
                                                       "--decimate-force")


def test_the_verb_a_kind_records_keeps_generate_history_intact():
    # image -> mesh records `to_3d` and every landed mesh node carries
    # it; a rename would rewrite history to say the same thing. Every
    # other kind IS its verb.
    assert mesh_verb("generate") == "to_3d"
    assert mesh_verb("decimate") == "decimate"


def test_a_parent_runner_job_is_read_only_off_a_landed_mesh():
    landed = {"payload": "3d",
              "submitted_graph": {"mesh_job": {"runner_job_id": "job-7"}}}
    assert parent_runner_job(landed) == "job-7"
    # an image parent, a mesh that never recorded one, and nothing at all —
    # all three are ordinary: the executor uploads the bytes instead
    assert parent_runner_job({"payload": "image",
                              "submitted_graph": {"mesh_job":
                                                  {"runner_job_id": "j"}}}) is None
    assert parent_runner_job({"payload": "3d", "submitted_graph": {}}) is None
    assert parent_runner_job(None) is None


def test_an_engine_oom_is_its_own_kind_not_a_plain_error():
    assert classify_engine_error("torch.OutOfMemoryError: CUDA out of "
                                 "memory. Tried to allocate 2.00 GiB") == "oom"
    assert classify_engine_error("Allocation on device") == "oom"
    assert classify_engine_error("ValueError: bad mesh") == "error"
    assert classify_engine_error(None) == "error"


# --- start ---

async def test_start_posts_the_source_bytes_under_the_runners_own_name(
        tmp_path):
    runner = Runner([done()])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    assert h.job_ref == "job-1"
    body = runner.posted[0]
    assert body["engine"] == "trellis2"           # translated, not the id
    assert body["seed"] == 7
    assert body["client_ref"] == "slot-1"
    assert base64.b64decode(body["image_b64"]) == \
        (tmp_path / "source.png").read_bytes()
    await ex.close()


async def test_start_names_the_missing_source_rather_than_the_engine(
        tmp_path):
    ex = executor(Runner([done()]))
    req = DispatchRequest(
        slot_id="slot-1", engine="trellis2-4b", payload="3d",
        submitted_graph=mesh_envelope("trellis2-4b",
                                      str(tmp_path / "gone.png")))
    with pytest.raises(EngineFailure) as e:
        await ex.start(req)
    assert e.value.kind == "error"
    assert "no longer on disk" in str(e.value)
    await ex.close()


async def test_start_refuses_an_engine_this_lane_does_not_serve(tmp_path):
    ex = executor(Runner([done()]))
    with pytest.raises(EngineFailure) as e:
        await ex.start(request(tmp_path, engine="raw-zimage"))
    assert e.value.kind == "error"
    assert "not a mesh engine" in str(e.value)
    await ex.close()


# --- start: the verbs (law 16) ---

def mesh_source(tmp_path, data=b"glTF\x02\x00\x00\x00staged-mesh-bytes"):
    p = tmp_path / "parent.glb"
    p.write_bytes(data)
    return p


def verb_request(tmp_path, *, kind="decimate", engine="gltfpack",
                 mesh_job_ref=None, params=None) -> DispatchRequest:
    return DispatchRequest(
        slot_id="slot-1", engine=engine, payload="3d", verb=kind,
        submitted_graph=mesh_envelope(
            engine, str(mesh_source(tmp_path)), kind=kind,
            params=params or {"target_triangles": 40_000},
            mesh_job_ref=mesh_job_ref))


async def test_a_verb_sends_its_kind_and_never_an_image(tmp_path):
    runner = Runner([done()])
    ex = executor(runner)
    await ex.start(verb_request(tmp_path, mesh_job_ref="parent-job"))
    body = runner.posted[0]
    assert body["kind"] == "decimate"
    assert body["engine"] == "gltfpack"
    assert body["params"] == {"target_triangles": 40_000}
    assert "image_b64" not in body          # a mesh verb has no picture
    await ex.close()


async def test_a_verb_reuses_the_parents_runner_job_when_it_has_one(tmp_path):
    """The bytes are already on the host — sending 35 MB the runner is
    holding would be the whole cost of the verb."""
    runner = Runner([done()])
    ex = executor(runner)
    await ex.start(verb_request(tmp_path, mesh_job_ref="parent-job"))
    assert runner.posted[0]["mesh"] == {"job": "parent-job"}
    assert runner.uploaded == []
    await ex.close()


async def test_a_verb_with_no_job_ref_uploads_the_staged_mesh(tmp_path):
    """A promoted or reloaded mesh was never this runner's, so there is no
    job to point at — the cockpit sends the artefact it holds."""
    runner = Runner([done()])
    ex = executor(runner)
    await ex.start(verb_request(tmp_path, mesh_job_ref=None))
    assert runner.posted[0]["mesh"] == {"upload": "up-1"}
    assert runner.uploaded == [mesh_source(tmp_path).read_bytes()]
    await ex.close()


async def test_a_forgotten_job_ref_falls_back_to_the_upload_once(tmp_path):
    """Records age out (`THREED_RECORD_TTL_S`). The runner checks kind,
    engine and params BEFORE it resolves a mesh reference, so a 422 on a
    by-reference take is about the reference — and the cockpit, which holds
    the artefact of record, just sends it."""
    refusals = iter([(422, {"detail": "this runner has no record of job "
                                      "parent-job any more"}),
                     (202, {"job_id": "job-1", "state": "queued"})])
    runner = Runner([done()], post=lambda b: next(refusals))
    ex = executor(runner)
    h = await ex.start(verb_request(tmp_path, mesh_job_ref="parent-job"))
    assert h.job_ref == "job-1"
    assert [b["mesh"] for b in runner.posted] == [{"job": "parent-job"},
                                                  {"upload": "up-1"}]
    assert len(runner.uploaded) == 1
    await ex.close()


async def test_the_upload_fallback_is_tried_once_not_forever(tmp_path):
    runner = Runner([], post=lambda b: (422, {"detail": "no uploaded mesh"}))
    ex = executor(runner)
    with pytest.raises(EngineFailure) as e:
        await ex.start(verb_request(tmp_path, mesh_job_ref="parent-job"))
    assert "no uploaded mesh" in str(e.value)
    assert len(runner.posted) == 2          # by ref, then by upload, then out
    await ex.close()


async def test_a_seedless_verb_sends_no_seed(tmp_path):
    """gltfpack is deterministic: a seed on the wire would be a number the
    ledger later implies had an effect."""
    runner = Runner([done()])
    ex = executor(runner)
    req = verb_request(tmp_path, mesh_job_ref="p")
    req.submitted_graph["mesh_job"]["seed"] = 424242    # even if one is set
    await ex.start(req)
    assert runner.posted[0]["seed"] is None
    await ex.close()


async def test_a_verb_names_the_missing_mesh_not_the_missing_image(tmp_path):
    ex = executor(Runner([done()]))
    req = DispatchRequest(
        slot_id="slot-1", engine="gltfpack", payload="3d", verb="decimate",
        submitted_graph=mesh_envelope("gltfpack", str(tmp_path / "gone.glb"),
                                      kind="decimate"))
    with pytest.raises(EngineFailure) as e:
        await ex.start(req)
    assert "the mesh this take was built from" in str(e.value)
    await ex.close()


async def test_a_landed_child_records_the_verb_that_made_it(tmp_path):
    runner = Runner([done(kind="decimate", engine="gltfpack",
                          input="mesh", input_ref={"upload": "up-1"})])
    ex = executor(runner)
    h = await ex.start(verb_request(tmp_path))
    res = await ex.wait(h)
    job = res.submitted_graph["mesh_job"]
    assert job["kind"] == "decimate"
    # the record names the artefact TYPE beside the reference, because
    # decimate reads a GLB from one engine and a field set from the other
    assert (job["input"], job["input_ref"]) == ("mesh", {"upload": "up-1"})
    assert job["engine"] == "gltfpack"
    await ex.close()


async def test_a_generate_child_still_records_generate(tmp_path):
    """The default survives: a record with no kind at all reads as generate,
    which is what every mesh node landed before kinds existed is."""
    runner = Runner([done()])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    res = await ex.wait(h)
    assert res.submitted_graph["mesh_job"]["kind"] == "generate"
    await ex.close()


async def test_the_runners_own_refusal_comes_through_in_its_words(tmp_path):
    runner = Runner([], post=lambda b: (422, {"detail": "trellis2 has no "
                                                        "parameter 'rm'"}))
    ex = executor(runner)
    with pytest.raises(EngineFailure) as e:
        await ex.start(request(tmp_path))
    assert e.value.kind == "error"
    assert "no parameter 'rm'" in str(e.value)
    await ex.close()


async def test_a_runner_that_is_down_is_unreachable_with_the_fix(tmp_path):
    def dead(request):
        raise httpx.ConnectError("connection refused")
    ex = MeshExecutor("http://runner.test")
    ex._client = httpx.AsyncClient(transport=httpx.MockTransport(dead),
                                   base_url="http://runner.test")
    with pytest.raises(EngineFailure) as e:
        await ex.start(request(tmp_path))
    assert e.value.kind == "unreachable"
    assert "scripts/3d-runner.sh" in str(e.value) and "systemctl" not in str(e.value)
    await ex.close()


# --- wait ---

async def test_wait_polls_to_done_and_lands_the_mesh_with_its_record(
        tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "THREED_POLL_S", 0.001)
    runner = Runner([{"id": "job-1", "state": "queued"},
                     {"id": "job-1", "state": "running"},
                     done()])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    result = await ex.wait(h)
    assert result.data == GLB
    assert result.payload == "3d"
    assert result.filename == "trellis2.glb"
    # the as-run record is what a manifest reads later
    job = result.submitted_graph["mesh_job"]
    assert job["runner_job_id"] == "job-1"
    assert job["validation"]["triangles"] == 900_000
    assert job["turntable"]["frames"] == 30
    assert job["glb_sha256"] == "abc123"
    assert result.detail["elapsed_s"] == 171.4
    await ex.close()


async def test_a_failed_mesh_carries_the_engines_own_last_words(tmp_path):
    runner = Runner([{"id": "job-1", "state": "failed",
                      "engine": "trellis2",
                      "error": "exit 1, glb missing",
                      "tail": "Traceback…\nValueError: bad input"}])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    with pytest.raises(EngineFailure) as e:
        await ex.wait(h)
    assert e.value.kind == "error"
    assert "exit 1, glb missing" in str(e.value)
    assert "ValueError: bad input" in (e.value.detail or "")
    await ex.close()


async def test_an_oom_in_the_engines_output_becomes_the_oom_kind(tmp_path):
    runner = Runner([{"id": "job-1", "state": "failed",
                      "engine": "trellis2", "error": "exit 1",
                      "tail": "torch.OutOfMemoryError: CUDA out of memory"}])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    with pytest.raises(EngineFailure) as e:
        await ex.wait(h)
    assert e.value.kind == "oom"
    await ex.close()


async def test_our_own_cancel_is_interrupted_never_the_engines_fault(
        tmp_path):
    runner = Runner([{"id": "job-1", "state": "cancelled",
                      "engine": "trellis2",
                      "error": "cancelled while running"}])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    await ex.cancel(h)
    assert runner.deleted == ["job-1"]
    with pytest.raises(EngineFailure) as e:
        await ex.wait(h)
    assert e.value.kind == "interrupted"
    await ex.close()


async def test_a_done_job_whose_mesh_cannot_be_read_back_fails_plainly(
        tmp_path):
    runner = Runner([done()], glb=None)
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    with pytest.raises(EngineFailure) as e:
        await ex.wait(h)
    assert e.value.kind == "error"
    await ex.close()


async def test_a_job_the_runner_has_forgotten_is_said_not_guessed(tmp_path):
    runner = Runner([None])
    ex = executor(runner)
    h = await ex.start(request(tmp_path))
    with pytest.raises(EngineFailure) as e:
        await ex.wait(h)
    assert "no record of this job" in str(e.value)
    await ex.close()


# --- reconcile: the two deliberate verdicts ---

async def test_reconcile_lands_a_mesh_that_finished_while_we_were_down():
    runner = Runner([done()])
    ex = executor(runner)
    r = await ex.reconcile({"job_ref": "job-1"})
    assert r.kind == "landed"
    assert r.result.data == GLB and r.result.payload == "3d"
    await ex.close()


async def test_reconcile_reports_a_failure_with_its_kind():
    runner = Runner([{"id": "job-1", "state": "failed", "engine": "trellis2",
                      "error": "exit 1",
                      "tail": "CUDA out of memory"}])
    ex = executor(runner)
    r = await ex.reconcile({"job_ref": "job-1"})
    assert r.kind == "failed" and r.failure_kind == "oom"
    await ex.close()


async def test_a_slot_with_no_job_ref_is_gone_and_safely_requeued():
    ex = executor(Runner([done()]))
    assert (await ex.reconcile({"job_ref": None})).kind == "gone"
    await ex.close()


async def test_a_still_running_job_is_cancelled_then_requeued():
    # the scheduler has no re-attach: the alternative is a duplicate mesh
    # queued behind an orphan nobody is waiting on
    runner = Runner([{"id": "job-1", "state": "running", "engine": "trellis2"}])
    ex = executor(runner)
    r = await ex.reconcile({"job_ref": "job-1"})
    assert r.kind == "gone"
    assert runner.deleted == ["job-1"]
    await ex.close()


async def test_an_unreachable_runner_never_benches_the_whole_local_lane():
    # raising here makes _recover bench lane:local, which would stop the
    # ComfyUI takes beside this one; unverifiable requeues instead
    def dead(request):
        raise httpx.ConnectError("connection refused")
    ex = MeshExecutor("http://runner.test")
    ex._client = httpx.AsyncClient(transport=httpx.MockTransport(dead),
                                   base_url="http://runner.test")
    assert (await ex.reconcile({"job_ref": "job-1"})).kind == "unverifiable"
    await ex.close()


async def test_nothing_bills_on_the_gpu():
    ex = executor(Runner([done()]))
    assert await ex.verify_charge("job-1") is None
    await ex.close()


# --- re-extraction, the second DECIMATE engine ---

NPZ = b"PK\x03\x04" + b"fields-bytes"


def fields_source(tmp_path, *, keep=True) -> str:
    """A staged mesh, with or without the voxel field beside it. The sidecar
    is DERIVED from the artefact (`fields_sidecar`), so a test that writes it
    by hand would be testing its own guess — this uses the one rule."""
    glb = tmp_path / "parent.glb"
    glb.write_bytes(GLB)
    if keep:
        fields_sidecar(glb).write_bytes(NPZ)
    return str(glb)


def extract_request(tmp_path, *, keep=True, mesh_job_ref=None):
    engine = "trellis2-4b"
    return DispatchRequest(
        slot_id="slot-1", engine=engine, payload="3d", verb="decimate",
        submitted_graph=mesh_envelope(
            engine, fields_source(tmp_path, keep=keep), kind="decimate",
            params={"target_triangles": 15_000},
            mesh_job_ref=mesh_job_ref))


async def test_re_extraction_sends_the_field_set_never_the_mesh(tmp_path):
    """The routing rule, at the wire: one verb, and the artefact follows the
    engine. gltfpack gets the GLB; TRELLIS.2 gets the field set beside it."""
    runner = Runner([done(kind="decimate", engine="trellis2")])
    ex = executor(runner)
    await ex.start(extract_request(tmp_path))
    body = runner.posted[0]
    assert body["kind"] == "decimate" and body["engine"] == "trellis2"
    assert body["fields"] == {"upload": "fld-1"}
    assert "mesh" not in body and "image_b64" not in body
    # and the bytes that went up are the sidecar's, not the mesh's
    assert runner.uploaded_fields == [NPZ] and runner.uploaded == []
    await ex.close()


async def test_re_extraction_prefers_the_parents_job_reference(tmp_path):
    """A 92 MB field set is worth not sending twice: when the runner still
    holds the parent take, the reference goes instead of the bytes."""
    runner = Runner([done(kind="decimate", engine="trellis2")])
    ex = executor(runner)
    await ex.start(extract_request(tmp_path, mesh_job_ref="parent-job"))
    assert runner.posted[0]["fields"] == {"job": "parent-job"}
    assert runner.uploaded_fields == []
    await ex.close()


async def test_a_mesh_that_kept_no_field_says_so_and_names_the_other_engine(
        tmp_path):
    """law 12 at the moment of failure: the reason is the mesh's, not the
    engine's, and the words name what the artist can actually do."""
    runner = Runner([done(kind="decimate", engine="trellis2")])
    ex = executor(runner)
    with pytest.raises(EngineFailure) as e:
        await ex.start(extract_request(tmp_path, keep=False))
    assert "kept no voxel field" in str(e.value)
    assert "gltfpack" in str(e.value)
    assert runner.posted == []           # nothing was dispatched
    await ex.close()


async def test_a_generate_lands_its_field_set_beside_the_mesh(tmp_path):
    """Keep-by-default: the field rides home as a
    sidecar, and the record says it did — which is what lets the surface
    offer re-extraction on this node and dash it on one without."""
    runner = Runner([done(result={**done()["result"], "fields":
                                  "/host/runs/job-1/fields.npz"})],
                    fields=NPZ)
    ex = executor(runner)
    res = await ex.wait(await ex.start(request(tmp_path)))
    assert res.sidecars == {FIELDS_SUFFIX: NPZ}
    assert res.submitted_graph["mesh_job"]["fields_kept"] is True
    await ex.close()


async def test_a_take_that_kept_no_field_lands_without_one(tmp_path):
    """Hunyuan has no voxel field to keep, and neither does a TRELLIS.2 run
    told not to — the mesh lands the same either way, saying so."""
    runner = Runner([done()])
    ex = executor(runner)
    res = await ex.wait(await ex.start(request(tmp_path)))
    assert res.sidecars is None
    assert res.submitted_graph["mesh_job"]["fields_kept"] is False
    await ex.close()


async def test_a_field_set_the_runner_cannot_serve_costs_the_take_nothing(
        tmp_path):
    """Best-effort by construction: the mesh is the take, the field is a
    capability. Losing the second never costs the first."""
    runner = Runner([done(result={**done()["result"], "fields":
                                  "/host/runs/job-1/fields.npz"})],
                    fields=None)         # the runner 404s it
    ex = executor(runner)
    res = await ex.wait(await ex.start(request(tmp_path)))
    assert res.data == GLB               # the mesh still landed
    assert res.sidecars is None
    assert res.submitted_graph["mesh_job"]["fields_kept"] is False
    await ex.close()


# --- texture, the first kind with two parents ---

REF_PNG = b"\x89PNG\r\n\x1a\nreference-bytes"


def texture_request(tmp_path, *, with_ref=True, mesh_job_ref=None):
    ref = tmp_path / "reference.png"
    if with_ref:
        ref.write_bytes(REF_PNG)
    return DispatchRequest(
        slot_id="slot-1", engine="hunyuan3d-21", payload="3d", verb="texture",
        submitted_graph=mesh_envelope(
            "hunyuan3d-21", str(mesh_source(tmp_path)), kind="texture",
            mesh_job_ref=mesh_job_ref, ref_path=str(ref)))


async def test_texture_sends_the_mesh_and_the_reference_together(tmp_path):
    """The two-parent wire shape: the mesh travels like any artefact input
    (job ref or upload), the reference rides image_b64 beside it — the same
    field generate reads, not a third door."""
    runner = Runner([done(kind="texture", engine="hunyuan")])
    ex = executor(runner)
    await ex.start(texture_request(tmp_path, mesh_job_ref="parent-job"))
    body = runner.posted[0]
    assert body["kind"] == "texture" and body["engine"] == "hunyuan"
    assert body["mesh"] == {"job": "parent-job"}
    assert base64.b64decode(body["image_b64"]) == REF_PNG
    # deterministic — Paint hard-seeds itself, so no seed goes out
    assert body["seed"] is None
    await ex.close()


async def test_texture_uploads_the_staged_mesh_when_no_job_ref(tmp_path):
    runner = Runner([done(kind="texture", engine="hunyuan")])
    ex = executor(runner)
    await ex.start(texture_request(tmp_path))
    assert runner.posted[0]["mesh"] == {"upload": "up-1"}
    assert runner.uploaded == [mesh_source(tmp_path).read_bytes()]
    await ex.close()


async def test_a_missing_reference_names_the_reference_not_the_mesh(
        tmp_path):
    """The failure is the second parent's, and the words say which artefact
    and what to do — never a generic engine error."""
    ex = executor(Runner([done()]))
    with pytest.raises(EngineFailure) as e:
        await ex.start(texture_request(tmp_path, with_ref=False))
    assert "reference image" in str(e.value)
    assert "pick another reference" in str(e.value)
    await ex.close()


async def test_a_textured_child_records_its_kind_and_its_reference(tmp_path):
    runner = Runner([done(kind="texture", engine="hunyuan",
                          input="mesh", input_ref={"upload": "up-1"},
                          ref="image")])
    ex = executor(runner)
    res = await ex.wait(await ex.start(texture_request(tmp_path)))
    job = res.submitted_graph["mesh_job"]
    assert job["kind"] == "texture"
    assert (job["input"], job["ref"]) == ("mesh", "image")
    assert res.filename == "hunyuan.glb"    # the runner record's own name
    await ex.close()


def test_texture_is_its_own_verb():
    assert mesh_verb("texture") == "texture"
