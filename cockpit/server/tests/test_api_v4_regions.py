"""The region-turn route (app/api_v4_regions.py) through the real
app, scripted executors injected (the third, "region"-named fake takes the
region slots — the scheduler's envelope peek routes them).

    fan-out      one request = one turn: N mask-capable presets in batch
                 order, regions rows created + consumed, the shared
                 plan in the turn dials, the envelope on every slot row,
                 landings as ordinary nodes with verb "region"
    refusals     the full matrix, each in the artist's words: unknown /
                 incapable / duplicated / slashed presets, empty ink,
                 undecodable mask, keep-only without a prompt, foreign or
                 purged source, over-cap takes, 404 workspace
    lineage      a consumed region row refuses deletion
    reads        GET /nodes/{nid}/regions + GET /regions/{rid}/mask
    reroll       a region cell rebuilds from its dials alone: fresh
                 envelope, keep_seed honoured, frame refused in words,
                 a repainted mask refused as drift (409)
    restart      a queued/running region slot survives a stop/start —
                 the envelope rides the slot row (the scheduler's recovery)

The plan/envelope internals are test_regionrun.py's; the executor walk is
test_region_exec.py's; the e2e truth is scripts/e2e_region_fanout.py's.
"""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from contextlib import asynccontextmanager
from PIL import Image

from app import main, wiring
from fakes import FakeExecutor, Step
from test_api_v4 import (hermetic_env, make_ws, settle_turn, store,
                         wait_running)

pytestmark = pytest.mark.anyio

TEST_INPAINT = {
    "1": {"class_type": "CLIPTextEncode",
          "_meta": {"title": "prompt_positive"}, "inputs": {"text": ""}},
    "2": {"class_type": "KSampler", "_meta": {"title": "sampler"},
          "inputs": {"seed": 0, "denoise": 0.5}},
    "4": {"class_type": "LoadImage", "_meta": {"title": "source_image"},
          "inputs": {"image": "unset.png"}},
    "5": {"class_type": "LoadImageMask", "_meta": {"title": "region_mask"},
          "inputs": {"image": "unset.png", "channel": "red"}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "t"}},
}
TEST_REMOVE = {
    "4": {"class_type": "LoadImage", "_meta": {"title": "source_image"},
          "inputs": {"image": "unset.png"}},
    "5": {"class_type": "LoadImageMask", "_meta": {"title": "region_mask"},
          "inputs": {"image": "unset.png", "channel": "red"}},
    "6": {"class_type": "INPAINT_InpaintWithModel",
          "_meta": {"title": "removal_apply"}, "inputs": {"seed": 0}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "t"}},
}
TEST_REFEDIT = {
    "1": {"class_type": "CLIPTextEncode",
          "_meta": {"title": "prompt_positive"}, "inputs": {"text": ""}},
    "2": {"class_type": "SamplerCustomAdvanced", "_meta": {"title": "sampler"},
          "inputs": {}},
    "3": {"class_type": "RandomNoise", "_meta": {"title": "noise_seed"},
          "inputs": {"noise_seed": 0}},
    "4": {"class_type": "LoadImage", "_meta": {"title": "source_image"},
          "inputs": {"image": "unset.png"}},
    "7": {"class_type": "EmptyFlux2LatentImage",
          "_meta": {"title": "latent_size"},
          "inputs": {"width": 1024, "height": 1024}},
    "8": {"class_type": "Flux2Scheduler", "_meta": {"title": "scheduler"},
          "inputs": {"steps": 4, "width": 1024, "height": 1024}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "t"}},
}
REGION_SDXL = {
    "id": "regionsdxl", "display_name": "Region SDXL",
    "description": "hermetic mask-native tier", "modality": "image",
    "model_family": "test-rails",
    "workflows": {"txt2img": "test-txt2img@v1",
                  "img2img": "test-img2img@v1",
                  "inpaint_modify": "test-inpaint@v1",
                  "inpaint_replace": "test-inpaint@v1",
                  "remove": "test-remove@v1"},
    "default_candidates": 1,
    "inputs": [
        {"key": "prompt", "type": "text",
         "target": {"node_title": "prompt_positive", "field": "text"}},
        {"key": "seed", "type": "seed",
         "target": [{"node_title": "sampler", "field": "seed"}]},
        {"key": "denoise", "type": "float", "default": 0.5, "min": 0.05,
         "max": 1.0, "capabilities": ["img2img", "region"],
         "target": {"node_title": "sampler", "field": "denoise"}},
    ],
}
REGION_KLEIN = {
    "id": "regionklein", "display_name": "Region Klein",
    "description": "hermetic instruction tier", "modality": "image",
    "model_family": "flux2-klein-4b",
    "workflows": {"txt2img": "test-txt2img@v1",
                  "img2img": "test-refedit@v1",
                  "inpaint_modify": "test-refedit@v1",
                  "inpaint_replace": "test-refedit@v1",
                  "remove": "test-remove@v1"},
    "default_candidates": 1,
    "inputs": [
        {"key": "prompt", "type": "text",
         "target": {"node_title": "prompt_positive", "field": "text"}},
        {"key": "seed", "type": "seed",
         "target": [{"node_title": "noise_seed", "field": "noise_seed"}]},
        {"key": "steps", "type": "int", "default": 4, "min": 1, "max": 32,
         "target": {"node_title": "scheduler", "field": "steps"}},
        {"key": "width", "type": "int", "default": 1024, "min": 512,
         "max": 2048, "step": 64,
         "target": [{"node_title": "latent_size", "field": "width"},
                    {"node_title": "scheduler", "field": "width"}]},
        {"key": "height", "type": "int", "default": 1024, "min": 512,
         "max": 2048, "step": 64,
         "target": [{"node_title": "latent_size", "field": "height"},
                    {"node_title": "scheduler", "field": "height"}]},
    ],
}

SIZE = (512, 512)


def source_png() -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(SIZE, 60).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def mask_b64(box, size=SIZE) -> str:
    m = Image.new("L", size, 0)
    if box is not None:
        m.paste(255, box)
    buf = io.BytesIO()
    m.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


REGIONS = [
    {"name": "the blade", "action": "keep", "mask_b64": mask_b64((0, 0, 160, 512))},
    {"name": "the wrap", "action": "modify", "prompt": "blue leather wrap",
     "mask_b64": mask_b64((256, 200, 400, 360))},
    {"name": "the stamp", "action": "delete", "mask_b64": mask_b64((420, 40, 500, 120))},
]


def region_fakes():
    return {"comfy": FakeExecutor("comfy", "local"),
            "openrouter": FakeExecutor("openrouter", "external"),
            "region": FakeExecutor("region", "local")}


@asynccontextmanager
async def booted_regions(tmp_path, monkeypatch, fakes=None):
    if not (tmp_path / "presets").exists():
        hermetic_env(tmp_path, monkeypatch)
    else:                                  # a reboot on the same tree
        import shutil
        shutil.rmtree(tmp_path / "presets")
        shutil.rmtree(tmp_path / "workflows")
        hermetic_env(tmp_path, monkeypatch)
    presets_dir = tmp_path / "presets"
    workflows_dir = tmp_path / "workflows"
    (presets_dir / "regionsdxl.json").write_text(json.dumps(REGION_SDXL))
    (presets_dir / "regionklein.json").write_text(json.dumps(REGION_KLEIN))
    (workflows_dir / "test-inpaint@v1.json").write_text(
        json.dumps(TEST_INPAINT))
    (workflows_dir / "test-remove@v1.json").write_text(json.dumps(TEST_REMOVE))
    (workflows_dir / "test-refedit@v1.json").write_text(
        json.dumps(TEST_REFEDIT))
    fakes = fakes or region_fakes()
    monkeypatch.setattr(
        wiring, "EXECUTOR_FACTORY",
        lambda orc: [fakes["comfy"], fakes["openrouter"], fakes["region"]])
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1") as client:
            yield client, fakes


async def make_source(client, fakes, wid) -> dict:
    """One landed txt2img take — the node the regions are drawn on."""
    fakes["comfy"].default = Step(data=source_png())
    r = await client.post(f"/api/workspaces/{wid}/turns",
                          json={"prompt": "a rusty sword",
                                "local": [{"preset_id": "regionsdxl",
                                           "takes": 1}]})
    assert r.status_code == 201, r.text
    await settle_turn(r.json()["turn"]["id"], until={"landed"})
    nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
    return nodes[0]


async def run_regions(client, wid, node_id, *, local=None, regions=None,
                      prompt="a night-forge scene", **kw):
    body = {"parents": [node_id], "prompt": prompt,
            "regions": regions if regions is not None else REGIONS,
            "local": local or [{"preset_id": "regionsdxl", "takes": 1}],
            **kw}
    return await client.post(f"/api/workspaces/{wid}/region-turns",
                             json=body)


# --- the fan-out ---


async def test_fanout_one_turn_rows_consumed_envelopes_persisted(
        tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])
        r = await run_regions(
            client, ws["id"], node["id"],
            local=[{"preset_id": "regionsdxl", "takes": 2},
                   {"preset_id": "regionklein", "takes": 1}])
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert [s["engine"] for s in turn["slots"]] \
            == ["regionsdxl", "regionsdxl", "regionklein"]

        # the shared plan on the turn's dials: keep+modify+delete with a
        # turn prompt = remove -> modify -> regen_rest, nd3-v1
        region_dials = turn["dials"]["region"]
        assert region_dials["policy"] == "nd3-v1"
        assert [p["kind"] for p in region_dials["passes"]] \
            == ["remove", "modify", "regen_rest"]
        assert turn["dials"]["capability"] == "region"

        # regions rows exist and are consumed by this turn
        rows = await store().regions_for_node(node["id"])
        assert [x["action"] for x in rows] == ["keep", "modify", "delete"]
        assert all(x["consumed_by_turn_id"] == turn["id"] for x in rows)

        # every slot carries its envelope + rebuildable dials
        for s in turn["slots"]:
            full = await store().get_slot(s["id"])
            plan = full["submitted_graph"]["region_plan"]
            assert [p["kind"] for p in plan["passes"]] \
                == ["remove", "modify", "regen_rest"]
            assert full["dials"]["capability"] == "region"
            assert full["dials"]["region_ids"] == [x["id"] for x in rows]
            assert set(full["dials"]["mask_sha256s"]) == set(
                full["dials"]["region_ids"])
        # the instruction tier pastes through the mask; the rails don't
        klein = await store().get_slot(turn["slots"][2]["id"])
        kp = klein["submitted_graph"]["region_plan"]["passes"]
        assert [p["masked_paste"] for p in kp] == [False, True, True]
        assert kp[1]["template_id"] == "klein-modify"

        settled = await settle_turn(turn["id"], until={"landed"})
        assert all(s["status"] == "landed" for s in settled["slots"])
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        region_nodes = [n for n in nodes if n["verb"] == "region"]
        assert len(region_nodes) == 3
        assert all(n["parents"] == [node["id"]] for n in region_nodes)


async def test_dispatch_reaches_the_region_executor_not_comfy(
        tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])
        comfy_starts = len(fakes["comfy"].starts)
        r = await run_regions(client, ws["id"], node["id"])
        assert r.status_code == 201
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        assert len(fakes["region"].starts) == 1
        assert len(fakes["comfy"].starts) == comfy_starts


# --- refusals, each in words ---


async def test_refusal_matrix(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])

        async def refused(status, needle, **kw):
            r = await run_regions(client, ws["id"], node["id"], **kw)
            assert r.status_code == status, r.text
            assert needle in r.text, r.text

        await refused(422, "unknown preset",
                      local=[{"preset_id": "ghost", "takes": 1}])
        await refused(422, "can't take region turns",
                      local=[{"preset_id": "testpreset", "takes": 1}])
        await refused(422, "listed twice",
                      local=[{"preset_id": "regionsdxl", "takes": 1},
                             {"preset_id": "regionsdxl", "takes": 1}])
        await refused(422, "never contain",
                      local=[{"preset_id": "vendor/model", "takes": 1}])
        await refused(422, "caps at 16",
                      local=[{"preset_id": "regionsdxl", "takes": 16},
                             {"preset_id": "regionklein", "takes": 1}])
        await refused(422, "no ink", regions=[
            {"name": "empty", "action": "modify", "prompt": "x",
             "mask_b64": mask_b64(None)}])
        await refused(422, "not a decodable image", regions=[
            {"name": "junk", "action": "modify", "prompt": "x",
             "mask_b64": base64.b64encode(b"not a png").decode()}])
        await refused(422, "keep-only turns need a turn prompt",
                      prompt=None, regions=[
                          {"name": "all", "action": "keep",
                           "mask_b64": mask_b64((0, 0, 512, 512))}])
        # pydantic guards: no regions / no batches / two parents
        for body in ({"parents": [node["id"]], "regions": [],
                      "local": [{"preset_id": "regionsdxl"}]},
                     {"parents": [node["id"]], "regions": REGIONS,
                      "local": []},
                     {"parents": [node["id"], node["id"]],
                      "regions": REGIONS,
                      "local": [{"preset_id": "regionsdxl"}]}):
            r = await client.post(
                f"/api/workspaces/{ws['id']}/region-turns", json=body)
            assert r.status_code == 422
        r = await run_regions(client, ws["id"], "no-such-node")
        assert r.status_code == 422 and "no such source node" in r.text
        r = await client.post("/api/workspaces/zzz/region-turns",
                              json={"parents": [node["id"]],
                                    "regions": REGIONS,
                                    "local": [{"preset_id": "regionsdxl"}]})
        assert r.status_code == 404
        # nothing above created a turn or consumed a region
        assert (await store().regions_for_node(node["id"])) == []


# --- the read surfaces ---


async def test_node_regions_and_mask_serving(tmp_path, monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])
        assert (await client.get(
            f"/api/nodes/{node['id']}/regions")).json() == {"regions": []}
        r = await run_regions(client, ws["id"], node["id"])
        assert r.status_code == 201
        listing = (await client.get(
            f"/api/nodes/{node['id']}/regions")).json()["regions"]
        assert [x["name"] for x in listing] \
            == ["the blade", "the wrap", "the stamp"]
        assert listing[1]["prompt"] == "blue leather wrap"
        assert all(x["consumed_by_turn_id"] for x in listing)
        mask = await client.get(listing[0]["mask_url"])
        assert mask.status_code == 200
        assert mask.content.startswith(b"\x89PNG")
        assert (await client.get("/api/regions/zzz/mask")).status_code \
            == 404
        assert (await client.get(
            "/api/nodes/zzz/regions")).status_code == 404


# --- reroll (on a region cell) ---


async def test_reroll_rebuilds_the_envelope_from_dials(tmp_path,
                                                       monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])
        r = await run_regions(client, ws["id"], node["id"])
        turn = r.json()["turn"]
        await settle_turn(turn["id"], until={"landed"})
        slot_id = turn["slots"][0]["id"]
        old = await store().get_slot(slot_id)

        rr = await client.post(f"/api/slots/{slot_id}/reroll", json={})
        assert rr.status_code == 200, rr.text
        await settle_turn(turn["id"], until={"landed"})
        fresh = await store().get_slot(slot_id)
        assert fresh["seed"] != old["seed"]
        plan = fresh["submitted_graph"]["region_plan"]
        assert [p["kind"] for p in plan["passes"]] \
            == ["remove", "modify", "regen_rest"]

        kept = await client.post(f"/api/slots/{slot_id}/reroll",
                                 json={"keep_seed": True})
        assert kept.status_code == 200
        await settle_turn(turn["id"], until={"landed"})
        assert (await store().get_slot(slot_id))["seed"] == fresh["seed"]

        framed = await client.post(f"/api/slots/{slot_id}/reroll",
                                   json={"frame": {"width": 512,
                                                   "height": 512}})
        assert framed.status_code == 409
        assert "lands at its source's size" in framed.text


async def test_reroll_refuses_a_repainted_mask_as_drift(tmp_path,
                                                        monkeypatch):
    async with booted_regions(tmp_path, monkeypatch) as (client, fakes):
        ws = await make_ws(client)
        node = await make_source(client, fakes, ws["id"])
        r = await run_regions(client, ws["id"], node["id"])
        turn = r.json()["turn"]
        await settle_turn(turn["id"], until={"landed"})
        rows = await store().regions_for_node(node["id"])
        from pathlib import Path
        repaint = Image.new("L", SIZE, 0)
        repaint.paste(255, (0, 0, 50, 50))
        buf = io.BytesIO()
        repaint.save(buf, "PNG")
        Path(rows[1]["mask_path"]).write_bytes(buf.getvalue())
        rr = await client.post(
            f"/api/slots/{turn['slots'][0]['id']}/reroll", json={})
        assert rr.status_code == 409
        assert "changed since this cell ran" in rr.text


# --- restart recovery ---


async def test_region_slot_survives_a_restart(tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = region_fakes()
    fakes["region"].default = Step(gate=gate)
    async with booted_regions(tmp_path, monkeypatch, fakes) as (client, f):
        ws = await make_ws(client)
        node = await make_source(client, f, ws["id"])
        r = await run_regions(client, ws["id"], node["id"])
        turn_id = r.json()["turn"]["id"]
        slot_id = r.json()["turn"]["slots"][0]["id"]
        await wait_running(slot_id)
    # stopped mid-take (crash-equivalent). Reboot on the same store: the
    # envelope rides the slot row, recovery requeues and redispatches.
    fakes2 = region_fakes()
    async with booted_regions(tmp_path, monkeypatch, fakes2) as (client, f):
        settled = await settle_turn(turn_id, until={"landed"})
        assert all(s["status"] == "landed" for s in settled["slots"])
        assert fakes2["region"].starts  # the region executor took it again
