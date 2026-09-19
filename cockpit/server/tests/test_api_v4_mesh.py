"""The mesh-turn route (app/api_v4_mesh.py) through the real app,
scripted executors injected (a fourth, "mesh"-named fake takes the mesh
slots — `route_engine` sends them there by engine id).

    one GPU       a mesh and a ComfyUI image take share
                  one GPU and NEVER overlap — one shared Probe across both
                  fakes, max_concurrent 1, whichever order they arrive in
    the run       one request = one turn: engine rows in batch order, the
                  envelope on every slot row, landings as `3d` nodes with
                  verb "to_3d" and the source as parent
    refusals      the full matrix in the artist's words: an engine the
                  lane doesn't serve, one listed twice, a mesh source that
                  is a mesh, a purged source, over-cap takes, 404 workspace
    lanes         a down runner benches its two ENGINES, never the local
                  lane — the image takes beside them keep running
    reroll        a mesh cell rebuilds from its dials alone: fresh
                  envelope, same params, a frame refused in words
    restart       a queued mesh slot survives a stop/start — the envelope
                  rides the slot row (the scheduler's recovery)
    health        GET /mesh/health states the door's state, never hides it

The executor's own contracts are test_mesh.py's.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app import main, wiring
from app.mesh import is_mesh_payload
from fakes import FakeExecutor, Probe, Step
from test_api_v4 import (hermetic_env, make_ws, settle_turn, store,
                         wait_running)

pytestmark = pytest.mark.anyio

TRELLIS = "trellis2-4b"
HUNYUAN = "hunyuan3d-21"


def mesh_fakes(probe=None):
    """One shared Probe across comfy and mesh — the serial-lane invariant
    is only meaningful ACROSS executors (the cost, accepted knowingly)."""
    probe = probe or Probe()
    return {"comfy": FakeExecutor("comfy", "local", probe=probe),
            "openrouter": FakeExecutor("openrouter", "external"),
            "mesh": FakeExecutor("mesh", "local", probe=probe),
            "_probe": probe}


@asynccontextmanager
async def booted_mesh(tmp_path, monkeypatch, fakes=None):
    if not (tmp_path / "presets").exists():
        hermetic_env(tmp_path, monkeypatch)
    else:                                   # a reboot on the same tree
        import shutil
        shutil.rmtree(tmp_path / "presets")
        shutil.rmtree(tmp_path / "workflows")
        hermetic_env(tmp_path, monkeypatch)
    fakes = fakes or mesh_fakes()
    monkeypatch.setattr(
        wiring, "EXECUTOR_FACTORY",
        lambda orc: [fakes["comfy"], fakes["openrouter"], fakes["mesh"]])
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1") as client:
            yield client, fakes


async def make_source(client, wid) -> dict:
    """One landed txt2img take — the image a mesh is built from."""
    r = await client.post(f"/api/workspaces/{wid}/turns",
                          json={"prompt": "a rusty sword",
                                "local": [{"preset_id": "testpreset",
                                           "takes": 1}]})
    assert r.status_code == 201, r.text
    await settle_turn(r.json()["turn"]["id"], until={"landed"})
    return (await client.get(f"/api/workspaces/{wid}/nodes")).json()[0]


async def wait_bench(scope, ticks=300):
    """The bench lands AFTER the row is failed (`_finalize_failure` writes
    the slot first), so a settled turn is not yet proof of a bench."""
    for _ in range(ticks):
        benches = wiring.current.scheduler.snapshot()["benches"]
        if scope in benches:
            return benches
        await asyncio.sleep(0.01)
    raise AssertionError(f"{scope} never benched: "
                         f"{wiring.current.scheduler.snapshot()['benches']}")


async def run_mesh(client, wid, node_id, *, mesh=None, **kw):
    return await client.post(
        f"/api/workspaces/{wid}/mesh-turns",
        json={"parents": [node_id],
              "mesh": mesh or [{"engine_id": TRELLIS, "takes": 1}], **kw})


# --- one GPU, never overlapping ---

async def test_a_mesh_and_a_comfy_take_never_hold_the_gpu_together(
        tmp_path, monkeypatch):
    """One 3D job runs through the subprocess executor and
    correctly serialises against a ComfyUI job. Both executors are local,
    so the scheduler's one lane awaits each take inline — the property is
    the seam's, and this is what proves it end to end."""
    fakes = mesh_fakes()
    fakes["comfy"].default = Step(delay_s=0.05)
    fakes["mesh"].default = Step(delay_s=0.05)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        # a mesh turn and an image turn in flight at the same moment
        mesh_r = await run_mesh(client, ws["id"], node["id"],
                                mesh=[{"engine_id": TRELLIS, "takes": 1},
                                      {"engine_id": HUNYUAN, "takes": 1}])
        assert mesh_r.status_code == 201, mesh_r.text
        img_r = await client.post(
            f"/api/workspaces/{ws['id']}/turns",
            json={"prompt": "a second sword",
                  "local": [{"preset_id": "testpreset", "takes": 2}]})
        assert img_r.status_code == 201
        await settle_turn(mesh_r.json()["turn"]["id"], until={"landed"})
        await settle_turn(img_r.json()["turn"]["id"], until={"landed"})
        assert fakes["_probe"].max_concurrent == 1
        # and both executors really did run: the lane was shared, not idle
        assert len(fakes["mesh"].starts) == 2
        assert len(fakes["comfy"].starts) == 3      # source + two takes


async def test_the_order_holds_the_other_way_round(tmp_path, monkeypatch):
    fakes = mesh_fakes()
    fakes["comfy"].default = Step(delay_s=0.05)
    fakes["mesh"].default = Step(delay_s=0.05)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        img_r = await client.post(
            f"/api/workspaces/{ws['id']}/turns",
            json={"prompt": "a second sword",
                  "local": [{"preset_id": "testpreset", "takes": 2}]})
        mesh_r = await run_mesh(client, ws["id"], node["id"])
        await settle_turn(img_r.json()["turn"]["id"], until={"landed"})
        await settle_turn(mesh_r.json()["turn"]["id"], until={"landed"})
        assert fakes["_probe"].max_concurrent == 1


# --- the run ---

async def test_one_request_is_one_turn_of_mesh_takes(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": TRELLIS, "takes": 2},
                                 {"engine_id": HUNYUAN, "takes": 1}])
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert [s["engine"] for s in turn["slots"]] == \
            [TRELLIS, TRELLIS, HUNYUAN]
        # the source's brief rides the turn — nothing generates from it
        assert turn["prompt"] == "a rusty sword"
        assert turn["dials"]["capability"] == "to_3d"
        assert turn["dials"]["parents"] == [node["id"]]
        source_path = (await store().get_node(node["id"]))["output_path"]
        settled = await settle_turn(turn["id"], until={"landed"})
        for slot in settled["slots"]:
            row = await store().get_slot(slot["id"])
            assert is_mesh_payload(row["submitted_graph"])
            job = row["submitted_graph"]["mesh_job"]
            assert job["source_path"] == source_path
            assert job["seed"] == row["seed"]
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        meshes = [n for n in nodes if n["payload"] == "3d"]
        assert len(meshes) == 3
        assert all(n["verb"] == "to_3d" for n in meshes)
        assert all(n["parents"] == [node["id"]] for n in meshes)


async def test_engine_params_ride_the_envelope_and_the_cells_dials(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": HUNYUAN, "takes": 1,
                                  "params": {"resolution": 768,
                                             "skip_paint": True}}])
        slot_id = r.json()["turn"]["slots"][0]["id"]
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        row = await store().get_slot(slot_id)
        assert row["submitted_graph"]["mesh_job"]["params"] == \
            {"resolution": 768, "skip_paint": True}
        assert row["dials"]["params"] == {"resolution": 768,
                                          "skip_paint": True}


# --- refusals, one cause each (law 1) ---

async def test_refuses_an_engine_the_3d_lane_does_not_serve(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": "raw-zimage", "takes": 1}])
        assert r.status_code == 422
        assert "not a mesh engine" in r.json()["detail"]


async def test_refuses_one_engine_listed_twice(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": TRELLIS, "takes": 1},
                                 {"engine_id": TRELLIS, "takes": 1}])
        assert r.status_code == 422
        assert "listed twice" in r.json()["detail"]


async def test_refuses_a_mesh_built_from_a_mesh(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"])
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        mesh_node = next(n for n in nodes if n["payload"] == "3d")
        r = await run_mesh(client, ws["id"], mesh_node["id"])
        assert r.status_code == 422
        assert "has to be an image" in r.json()["detail"]
        assert "3d payload" in r.json()["detail"]


async def test_refuses_more_takes_than_the_lane_will_sit_through(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": TRELLIS, "takes": 4},
                                 {"engine_id": HUNYUAN, "takes": 4}])
        assert r.status_code == 201        # 8 is the cap, not over it
        r = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json={"parents": [node["id"]],
                  "mesh": [{"engine_id": TRELLIS, "takes": 4},
                           {"engine_id": HUNYUAN, "takes": 4},
                           {"engine_id": "x", "takes": 1}]})
        assert r.status_code == 422
        assert "caps at 8 takes" in r.json()["detail"]


async def test_refuses_an_unknown_workspace_and_a_foreign_source(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        other = await make_ws(client, "Another")
        node = await make_source(client, ws["id"])
        assert (await run_mesh(client, "nope", node["id"])).status_code == 404
        r = await run_mesh(client, other["id"], node["id"])
        assert r.status_code == 422


async def test_a_mesh_needs_a_source_at_all(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json={"parents": [],
                                    "mesh": [{"engine_id": TRELLIS}]})
        assert r.status_code == 422        # schema: exactly one parent


# --- a down runner is the ENGINES' problem, never the lane's ---

async def test_a_down_runner_benches_its_engines_not_the_local_lane(
        tmp_path, monkeypatch):
    from app.executors import EngineFailure
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(
        outcome=EngineFailure("unreachable", "the 3D engine runner is not "
                                             "answering"))
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"])
        await settle_turn(r.json()["turn"]["id"],
                          until={"failed", "held", "landed"})
        benches = await wait_bench(f"engine:{TRELLIS}")
        assert "lane:local" not in benches
        # the image engines beside it are untouched
        img = await client.post(
            f"/api/workspaces/{ws['id']}/turns",
            json={"prompt": "still working",
                  "local": [{"preset_id": "testpreset", "takes": 1}]})
        await settle_turn(img.json()["turn"]["id"], until={"landed"})


async def test_the_failed_mesh_cell_says_the_runner_not_comfyui(
        tmp_path, monkeypatch):
    from app.executors import EngineFailure
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(
        outcome=EngineFailure("unreachable", "not answering"))
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"])
        turn = await settle_turn(r.json()["turn"]["id"],
                                 until={"failed", "held"})
        slot = await store().get_slot(turn["slots"][0]["id"])
        assert "3D engine runner" in (slot["cause"] or "")
        assert "ComfyUI" not in (slot["cause"] or "")


# --- reroll ---

async def test_a_mesh_cell_rebuilds_from_its_dials_alone(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": HUNYUAN, "takes": 1,
                                  "params": {"resolution": 768}}])
        turn_id = r.json()["turn"]["id"]
        slot_id = r.json()["turn"]["slots"][0]["id"]
        await settle_turn(turn_id, until={"landed"})
        first_seed = (await store().get_slot(slot_id))["seed"]
        source_path = (await store().get_node(node["id"]))["output_path"]

        rr = await client.post(f"/api/slots/{slot_id}/reroll", json={})
        assert rr.status_code == 200, rr.text
        await settle_turn(turn_id, until={"landed"})
        row = await store().get_slot(slot_id)
        assert row["seed"] != first_seed
        job = row["submitted_graph"]["mesh_job"]
        assert job["engine"] == HUNYUAN
        assert job["params"] == {"resolution": 768}
        assert job["source_path"] == source_path


async def test_a_mesh_reroll_refuses_a_frame_in_words(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"])
        slot_id = r.json()["turn"]["slots"][0]["id"]
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        rr = await client.post(f"/api/slots/{slot_id}/reroll",
                               json={"frame": {"width": 512, "height": 512}})
        assert rr.status_code == 409
        assert "no frame to ask for" in rr.json()["detail"]


# --- restart recovery ---

async def test_a_queued_mesh_slot_survives_a_restart(tmp_path, monkeypatch):
    fakes = mesh_fakes()
    gate = asyncio.Event()
    fakes["mesh"].default = Step(gate=gate)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        node = await make_source(client, ws["id"])
        r = await run_mesh(client, ws["id"], node["id"],
                           mesh=[{"engine_id": TRELLIS, "takes": 2}])
        turn_id = r.json()["turn"]["id"]
        slots = r.json()["turn"]["slots"]
        await wait_running(slots[0]["id"])
        wid = ws["id"]

    # a second boot on the same store: the envelope rides the slot row, so
    # the mesh is rebuilt without the request that made it
    fakes2 = mesh_fakes()
    async with booted_mesh(tmp_path, monkeypatch, fakes2) as (client, _):
        turn = await settle_turn(turn_id, until={"landed", "failed",
                                                 "skipped"})
        assert all(s["status"] == "landed" for s in turn["slots"]), \
            [s["status"] for s in turn["slots"]]
        nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        assert len([n for n in nodes if n["payload"] == "3d"]) == 2


# --- the door's state, stated (law 12) ---

async def test_mesh_health_says_whether_the_door_is_open(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        monkeypatch.setattr(wiring, "probe_mesh_runner",
                            _answer(False))
        r = await client.get("/api/mesh/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "3d-runner" in body["why"]
        assert sorted(body["engines"]) == sorted([TRELLIS, HUNYUAN, "gltfpack"])
        # law 12 at the roster: which engines serve which verb, published as a
        # fact rather than left for a surface to guess
        assert body["kinds"] == {"generate": sorted([HUNYUAN, TRELLIS]),
                                 # TRELLIS.2 serves DECIMATE too, by
                                 # re-extraction rather than simplification
                                 "decimate": sorted(["gltfpack", TRELLIS]),
                                 # Paint as its own stage, any mesh
                                 "texture": [HUNYUAN]}

        monkeypatch.setattr(wiring, "probe_mesh_runner", _answer(True))
        body = (await client.get("/api/mesh/health")).json()
        assert body["ok"] is True and body["why"] is None


# The To-3D roster reads this route, so under scripted executors it
# must answer for the STAND-IN's door — otherwise an e2e run's mesh roster
# depends on whether a systemd unit happens to be up on the machine.
async def test_mesh_health_answers_for_the_harness_under_scripted_executors(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        # THREED_URL points nowhere reachable in the hermetic env; the real
        # probe would say down. The scripted executors make it the fake's
        # door, and the fake is always up.
        body = (await client.get("/api/mesh/health")).json()
        assert body["ok"] is True and body["why"] is None


def _answer(value):
    async def probe():
        return value
    return probe


# --- the verbs (law 16): the same route, a different kind ---

# a landed mesh the way MeshExecutor lands one, with the runner job id a
# verb passes along so the runner can reuse bytes it already holds
LANDED_PARENT = {"mesh_job": {
    "engine": "trellis2", "kind": "generate", "runner_job_id": "job-parent",
    "turntable": {"frames": 30, "size": 640, "webp": "/host/tt.webp"},
    "validation": {"triangles": 970535, "vertices": 721911,
                   "watertight": [False], "texture_present": True,
                   "file_mb": 35.3}}}


async def make_mesh(client, wid, src_id, record=None) -> dict:
    """One landed mesh take — the parent a mesh verb works on."""
    r = await run_mesh(client, wid, src_id)
    assert r.status_code == 201, r.text
    await settle_turn(r.json()["turn"]["id"], until={"landed"})
    nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
    return [n for n in nodes if n["payload"] == "3d"][0]


def decimate_body(node_id, *, takes=1, engine="gltfpack", target=40_000,
                  force=None, **kw):
    params = {"target_triangles": target}
    if force is not None:                       # the opt-in override
        params["force"] = force
    return {"parents": [node_id], "kind": "decimate",
            "mesh": [{"engine_id": engine, "takes": takes,
                      "params": params}], **kw}


async def test_a_verb_lands_a_child_beside_its_parent(tmp_path, monkeypatch):
    """law 16's shape: artefact in, artefact out, per slot, landing as a `3d`
    node with the mesh as parent and the kind as verb. No new route, no
    schema change — the seam is that this reads like the generate path."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])

        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"]))
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert turn["dials"]["kind"] == "decimate"
        assert turn["dials"]["parents"] == [parent["id"]]
        settled = await settle_turn(turn["id"], until={"landed"})

        row = await store().get_slot(settled["slots"][0]["id"])
        job = row["submitted_graph"]["mesh_job"]
        assert job["kind"] == "decimate"
        assert job["engine"] == "gltfpack"
        assert job["params"] == {"target_triangles": 40_000}
        # the source is the PARENT MESH's own artefact, and the parent's
        # runner job rides along so the bytes need not be re-sent
        assert job["source_path"] == \
            (await store().get_node(parent["id"]))["output_path"]
        assert job["mesh_job_ref"] == "job-parent"

        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        child = [n for n in nodes
                 if n["payload"] == "3d" and n["id"] != parent["id"]][0]
        assert child["verb"] == "decimate"
        assert child["parents"] == [parent["id"]]


async def test_the_force_override_travels_as_a_declared_param(
        tmp_path, monkeypatch):
    """`-sa` reaches gltfpack only through the runner's whitelist, so
    the cockpit's job is to carry it verbatim and record what was sent. It
    rides as an ordinary param — there is no second channel for an override,
    which is what keeps the as-run record complete."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json=decimate_body(parent["id"], target=13_309, force=True))
        assert r.status_code == 201, r.text
        settled = await settle_turn(r.json()["turn"]["id"], until={"landed"})
        row = await store().get_slot(settled["slots"][0]["id"])
        assert row["submitted_graph"]["mesh_job"]["params"] == {
            "target_triangles": 13_309, "force": True}
        # ...and declining it is recorded too, rather than left unset — the
        # face reads an un-forced count AS un-forced off this
        r2 = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json=decimate_body(parent["id"], target=95_000, force=False))
        settled2 = await settle_turn(r2.json()["turn"]["id"], until={"landed"})
        row2 = await store().get_slot(settled2["slots"][0]["id"])
        assert row2["submitted_graph"]["mesh_job"]["params"]["force"] is False


LANDED_DECIMATE = {"mesh_job": {
    "engine": "gltfpack", "kind": "decimate", "runner_job_id": "job-dec",
    "seed": None,
    # asked for 15,000 of a 952,326-triangle TRELLIS.2 mesh; the UV atlas
    # answered 79,170 (measured 2026-08-29)
    "params": {"target_triangles": 15_000, "force": False},
    "turntable": {"frames": 30, "size": 640, "webp": "/host/dec.webp"},
    "validation": {"triangles": 79_170, "vertices": 55_402,
                   "watertight": [False], "texture_present": True,
                   "file_mb": 4.1}}}


async def test_a_decimated_child_states_the_ask_beside_what_it_reached(
        tmp_path, monkeypatch):
    """The pair. A face printing only `triangles` would be honest
    about the artefact and silent about the refusal, and on a machine UV
    atlas the refusal is the interesting half — so the projection carries
    what was ASKED, off the job's own params, and whether `-sa` forced it."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        fakes["mesh"].default = Step(submitted_graph=LANDED_DECIMATE,
                                     filename="decimated.glb")
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"], target=15_000))
        settled = await settle_turn(r.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        child = [n for n in nodes if n["slot_id"] == settled["slots"][0]["id"]][0]
        assert child["mesh"]["asked_triangles"] == 15_000
        assert child["mesh"]["triangles"] == 79_170
        assert child["mesh"]["forced"] is False
        # the parent asked for nothing, so its pair stays null — which is
        # what makes the child's readable
        got = [n for n in nodes if n["id"] == parent["id"]][0]
        assert got["mesh"]["asked_triangles"] is None


async def test_a_verb_takes_a_mesh_parent_and_says_so_when_given_a_picture(
        tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(src["id"]))
        assert r.status_code == 422
        assert "has to be a mesh" in r.json()["detail"]


async def test_generate_still_refuses_a_mesh_parent(tmp_path, monkeypatch):
    """The no-mesh-from-a-mesh refusal, unmoved by the seam."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await run_mesh(client, ws["id"], parent["id"])
        assert r.status_code == 422
        assert "has to be an image" in r.json()["detail"]


async def test_an_unknown_verb_is_refused_by_name(tmp_path, monkeypatch):
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        r = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json={"parents": [src["id"]], "kind": "sculpt",
                  "mesh": [{"engine_id": TRELLIS, "takes": 1}]})
        assert r.status_code == 422
        assert "is not a 3D verb" in r.json()["detail"]
        assert "decimate" in r.json()["detail"]


async def test_an_engine_that_does_not_serve_the_verb_is_refused(
        tmp_path, monkeypatch):
    """law 12's grammar server-side: engines declare the verbs they serve, and
    a pairing that was never declared is refused before a turn exists."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"],
                                                 engine=HUNYUAN))
        assert r.status_code == 422
        assert f"'{HUNYUAN}' does not do decimate" in r.json()["detail"]
        # and the other way: gltfpack is not a generator
        r = await run_mesh(client, ws["id"], src["id"],
                           mesh=[{"engine_id": "gltfpack", "takes": 1}])
        assert r.status_code == 422
        assert "does not do generate" in r.json()["detail"]


async def test_more_than_one_take_of_a_deterministic_verb_is_refused(
        tmp_path, monkeypatch):
    """The fan-out promise is variety, and this tool has none to give: two
    takes would be two copies wearing the word."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"], takes=3))
        assert r.status_code == 422
        assert "deterministic" in r.json()["detail"]


async def test_a_deterministic_verb_records_no_seed(tmp_path, monkeypatch):
    """Even asked for a locked seed: the slot records None rather than a
    number the ledger would later imply had an effect."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json=decimate_body(parent["id"],
                               seed={"mode": "locked", "value": 424242}))
        assert r.status_code == 201, r.text
        slot = r.json()["turn"]["slots"][0]
        assert slot["seed"] is None
        row = await store().get_slot(slot["id"])
        assert row["submitted_graph"]["mesh_job"]["seed"] is None


# --- the node body: the plate, the orbit, the facts ---

# What a real MeshExecutor lands with (`mesh._result`), trimmed to the keys
# the node body reads. `watertight` is funnel's PER-GEOMETRY list, and one
# open surface is enough to make the answer no — TRELLIS.2 legitimately
# returns open surfaces, so this is a fact, never a verdict.
LANDED_MESH = {"mesh_job": {
    "engine": "trellis2", "runner_job_id": "job-abc123", "seed": 7,
    "turntable": {"frames": 30, "size": 640,
                  "webp": "/srv/crexil/tools/3d/runs/x/turntables/"
                          "turntable.webp"},
    "validation": {"triangles": 999588, "vertices": 691815,
                   "watertight": [True, False], "texture_present": True,
                   "file_mb": 33.8},
    "glb_sha256": "deadbeef"}}


async def test_a_landed_mesh_states_its_own_facts_and_never_the_envelope(
        tmp_path, monkeypatch):
    """The node feed grows ONE derived key for a mesh: the six
    measurements plus the two ids the turntable routes need, and the
    decimate pair. The envelope itself stays server-side like every other
    submitted_graph."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_MESH)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        out = await run_mesh(client, ws["id"], src["id"])
        await settle_turn(out.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        mesh = [n for n in nodes if n["payload"] == "3d"][0]
        assert mesh["mesh"] == {
            "engine": "trellis2", "runner_job_id": "job-abc123",
            "triangles": 999588, "vertices": 691815,
            "watertight": False,          # one open surface loses the all()
            "textured": True, "file_mb": 33.8, "has_turntable": True,
            # a GENERATED mesh asked for no triangle count, so the
            # pair is null rather than defaulted — which is what makes it
            # readable on the children that did ask
            "asked_triangles": None, "forced": False,
            # this fixture's take kept no voxel field, so the mesh
            # says so rather than leaving the surface to guess
            "fields_kept": False}
        # the slice, never the envelope
        assert "submitted_graph" not in mesh and "glb_sha256" not in mesh
        # an image take on the same feed says nothing about meshes
        assert [n for n in nodes if n["payload"] == "image"][0]["mesh"] is None


async def test_a_mesh_with_no_turntable_record_claims_nothing(
        tmp_path, monkeypatch):
    """A mesh landed before the record existed (or by an executor that
    reports none) keeps every other fact null rather than defaulted, and
    says it has no turntable — the card then keeps its word (law 12)."""
    async with booted_mesh(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        out = await run_mesh(client, ws["id"], src["id"])
        await settle_turn(out.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        mesh = [n for n in nodes if n["payload"] == "3d"][0]
        assert mesh["mesh"]["has_turntable"] is False
        assert mesh["mesh"]["triangles"] is None
        assert mesh["mesh"]["watertight"] is None
        assert mesh["mesh"]["textured"] is None

        # both faces refuse in words, and say the mesh itself still downloads
        for route in ("turntable", "thumb"):
            r = await client.get(f"/api/nodes/{mesh['id']}/{route}")
            assert r.status_code == 404
            assert "no turntable render" in r.json()["detail"]
            assert "still downloads" in r.json()["detail"]
        # ...and it does
        assert (await client.get(
            f"/api/nodes/{mesh['id']}/image")).status_code == 200


async def test_the_orbit_and_the_plate_are_fetched_once_and_cached(
        tmp_path, monkeypatch):
    """One fetch, two faces: the lightbox gets the whole orbit, the card
    gets frame 0 of the same file at <=320. Both land beside the artefact,
    so the second ask is a plain file serve — and neither ever touches the
    engine again."""
    from PIL import Image
    import io
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_MESH)
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        out = await run_mesh(client, ws["id"], src["id"])
        await settle_turn(out.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        mesh = [n for n in nodes if n["payload"] == "3d"][0]

        r = await client.get(f"/api/nodes/{mesh['id']}/turntable")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/webp"
        assert "immutable" in r.headers["cache-control"]
        orbit = Image.open(io.BytesIO(r.content))
        assert orbit.n_frames > 1          # it is an ORBIT, not a still

        r = await client.get(f"/api/nodes/{mesh['id']}/thumb")
        assert r.status_code == 200, r.text
        plate = Image.open(io.BytesIO(r.content))
        assert getattr(plate, "n_frames", 1) == 1     # the card is a still
        assert max(plate.size) <= 320

        # both caches sit beside the artefact under the node's own name, so
        # purge and the tray's byte count already cover them
        row = await store().get_node(mesh["id"])
        from pathlib import Path
        art = Path(row["output_path"])
        assert art.with_suffix(".turntable.webp").is_file()
        assert art.with_suffix(".thumb.webp").is_file()

        # the second ask is served from disk — delete the source of truth
        # and both faces still answer
        assert (await client.get(
            f"/api/nodes/{mesh['id']}/thumb")).status_code == 200


async def test_a_silent_runner_says_which_unit_to_start(tmp_path, monkeypatch):
    """The orbit lives on the host. With the real runner down the
    face gets one cause and the command that fixes it — never a status code
    (law 3), and never a hidden card (law 12)."""
    from app import api_v4, config
    monkeypatch.setattr(wiring, "EXECUTOR_FACTORY", None)
    monkeypatch.setattr(config, "HARNESS_EXECUTORS", False)
    monkeypatch.setattr(config, "THREED_URL", "http://127.0.0.1:9")
    with pytest.raises(Exception) as e:
        await api_v4._fetch_turntable("job-abc123")
    assert e.value.status_code == 404
    assert "3d-runner" in e.value.detail
    assert "isn't answering" in e.value.detail


# --- re-extraction is the second DECIMATE engine ---

async def test_re_extraction_is_refused_on_a_mesh_that_kept_no_field(
        tmp_path, monkeypatch):
    """One verb, two engines, and the parent decides which can serve it. The
    roster already dashes the row (law 12); this is the server re-check, and it
    names the engine that CAN take this mesh rather than only saying no."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"], engine=TRELLIS))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "re-extracts from the voxel field" in detail
        assert "gltfpack" in detail
        # and gltfpack takes the same mesh happily — the refusal is about
        # this engine's input, never about the mesh being unusable
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=decimate_body(parent["id"]))
        assert r.status_code == 201, r.text


# --- texture, the first verb with two parents ---


def texture_body(node_id, ref_id=None, *, takes=1, engine=HUNYUAN, **kw):
    body = {"parents": [node_id], "kind": "texture",
            "mesh": [{"engine_id": engine, "takes": takes}], **kw}
    if ref_id is not None:
        body["reference"] = ref_id
    return body


async def test_texture_lands_a_child_with_both_parents_in_meaning_order(
        tmp_path, monkeypatch):
    """The shape parent first, the appearance parent second — node_parents
    keeps the ordinals, the canvas draws an edge from each, and the turn's
    brief is the REFERENCE's (the appearance is what changed)."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        # a second image with its own brief — the repainted concept
        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "the sword, gilded",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1}]})
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        ref = [n for n in nodes if n["payload"] == "image"
               and n["id"] != src["id"]][0]

        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(parent["id"], ref["id"]))
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert turn["dials"]["kind"] == "texture"
        assert turn["dials"]["parents"] == [parent["id"], ref["id"]]
        assert turn["prompt"] == "the sword, gilded"
        settled = await settle_turn(turn["id"], until={"landed"})

        row = await store().get_slot(settled["slots"][0]["id"])
        job = row["submitted_graph"]["mesh_job"]
        assert job["kind"] == "texture"
        assert job["source_path"] == \
            (await store().get_node(parent["id"]))["output_path"]
        assert job["ref_path"] == \
            (await store().get_node(ref["id"]))["output_path"]
        assert job["mesh_job_ref"] == "job-parent"
        assert job["seed"] is None            # deterministic, like decimate

        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        child = [n for n in nodes if n["slot_id"] == settled["slots"][0]["id"]][0]
        assert child["verb"] == "texture"
        assert child["parents"] == [parent["id"], ref["id"]]


async def test_texture_without_a_reference_is_refused_in_words(
        tmp_path, monkeypatch):
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(parent["id"]))
        assert r.status_code == 422
        assert "reference image" in r.json()["detail"]


async def test_a_reference_on_a_kind_that_takes_none_is_refused(
        tmp_path, monkeypatch):
    """A field that silently does nothing is a lie in the other direction."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(
            f"/api/workspaces/{ws['id']}/mesh-turns",
            json=decimate_body(parent["id"], reference=src["id"]))
        assert r.status_code == 422
        assert "takes no reference image" in r.json()["detail"]


async def test_texture_wants_a_mesh_shape_and_an_image_reference(
        tmp_path, monkeypatch):
    """Both parents are payload-checked, each in its own words."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        # an image as the shape parent
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(src["id"], src["id"]))
        assert r.status_code == 422
        assert "has to be a mesh" in r.json()["detail"]
        # a mesh as the reference
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(parent["id"], parent["id"]))
        assert r.status_code == 422
        assert "has to be an image" in r.json()["detail"]


async def test_texture_is_deterministic_one_take_no_seed(
        tmp_path, monkeypatch):
    """Paint hard-seeds its diffusion, so a second take of the same pair is
    a copy — refused in the deterministic verb's own words."""
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=LANDED_PARENT,
                                 filename="mesh.glb")
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        src = await make_source(client, ws["id"])
        parent = await make_mesh(client, ws["id"], src["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(parent["id"], src["id"],
                                                takes=2))
        assert r.status_code == 422
        assert "deterministic" in r.json()["detail"]
        # and the engines that don't paint say so
        r = await client.post(f"/api/workspaces/{ws['id']}/mesh-turns",
                              json=texture_body(parent["id"], src["id"],
                                                engine="gltfpack"))
        assert r.status_code == 422
        assert "does not do texture" in r.json()["detail"]


async def test_a_kept_field_set_is_offered_counted_and_purged_with_its_mesh(
        tmp_path, monkeypatch):
    """Keep-by-default has three consequences and they must agree: the node
    says it kept one, the tray counts its bytes (it is the LARGER half of
    what a mesh costs the drive), and purge frees it with the mesh."""
    from app.mesh import fields_sidecar
    npz = b"PK\x03\x04" + b"voxel-field" * 400
    kept = {"mesh_job": {**LANDED_PARENT["mesh_job"], "fields_kept": True}}
    fakes = mesh_fakes()
    fakes["mesh"].default = Step(submitted_graph=kept,
                                 filename="mesh.glb", data=b"glTF-mesh-bytes",
                                 sidecars={".fields.npz": npz})
    async with booted_mesh(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        src = await make_source(client, wid)
        parent = await make_mesh(client, wid, src["id"])
        assert parent["mesh"]["fields_kept"] is True

        # re-extraction is now offerable on it — the same body the fieldless
        # parent above was refused for
        r = await client.post(f"/api/workspaces/{wid}/mesh-turns",
                              json=decimate_body(parent["id"], engine=TRELLIS))
        assert r.status_code == 201, r.text

        node = await store().get_node(parent["id"])
        assert fields_sidecar(node["output_path"]).is_file()

        await client.post(f"/api/nodes/{parent['id']}/set-aside")
        tray = (await client.get(f"/api/workspaces/{wid}/tray")).json()
        entry = [n for n in tray["nodes"] if n["id"] == parent["id"]][0]
        # the promise the hold-to-confirm makes has to include the sidecar,
        # or it understates the purge by more than the mesh itself
        assert entry["bytes"] > len(npz)

        r = await client.post(f"/api/workspaces/{wid}/purge",
                              json={"ids": [parent["id"]]})
        assert r.status_code == 200, r.text
        assert not fields_sidecar(node["output_path"]).exists()
        assert not Path(node["output_path"]).exists()
