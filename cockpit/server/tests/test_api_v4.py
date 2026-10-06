"""The stack through the real app: real lifespan, real routers,
scripted executors injected via `wiring.EXECUTOR_FACTORY`, every path
monkeypatched hermetic (COMFYUI/OPENROUTER point at a closed port).

httpx ASGITransport does not run the lifespan, so `booted()` enters it
explicitly — boot failures (the archive refusal) surface as exceptions on
entry, exactly as they would kill a real deploy."""

import json
import sqlite3
from contextlib import asynccontextmanager

import httpx
import pytest

from app import config, main, wiring
from app.db import SchemaVersionError
from fakes import FakeExecutor, Step

pytestmark = pytest.mark.anyio

TEST_KEY = "sk-test-" + "0" * 24
PIN = {"id": "testvendor/testmodel", "provider": "testvendor",
       "label": "Test Model", "price_usd_per_image": 0.05,
       "supports_seed": True, "enabled": True}
PRESET = {
    "id": "testpreset",
    "display_name": "Test Preset",
    "description": "hermetic fixture",
    "modality": "image",
    "workflows": {"txt2img": "test-txt2img@v1",
                  "img2img": "test-img2img@v1"},
    "default_candidates": 2,
    "inputs": [
        {"key": "prompt", "type": "text",
         "target": {"node_title": "prompt_positive", "field": "text"}},
        {"key": "seed", "type": "seed",
         "target": [{"node_title": "sampler", "field": "seed"}]},
        # a numeric handle every recipe carries — what a dial resolves to
        {"key": "steps", "type": "int", "default": 4, "min": 1, "max": 16,
         "target": {"node_title": "sampler", "field": "steps"}},
        # capability-scoped: only an img2img run carries it
        {"key": "denoise", "type": "float", "default": 0.5,
         "min": 0.05, "max": 1.0, "capabilities": ["img2img"],
         "target": {"node_title": "sampler", "field": "denoise"}},
        # a raw knob no dial owns, the Advanced fold's
        {"key": "sampler_name", "type": "enum", "default": "euler",
         "choices": ["euler", "heun"],
         "target": {"node_title": "sampler", "field": "sampler_name"}},
    ],
}
# The roster's honest half (law 12): a preset with no img2img recipe stays in
# the catalogue and refuses a source in words.
TXT_ONLY = {
    "id": "txtonly",
    "display_name": "Txt Only",
    "description": "no img2img recipe committed",
    "modality": "image",
    "workflows": {"txt2img": "test-txt2img@v1"},
    "default_candidates": 1,
    "inputs": [
        {"key": "prompt", "type": "text",
         "target": {"node_title": "prompt_positive", "field": "text"}},
        {"key": "seed", "type": "seed",
         "target": [{"node_title": "sampler", "field": "seed"}]},
    ],
}
WORKFLOW = {
    "1": {"class_type": "CLIPTextEncode",
          "_meta": {"title": "prompt_positive"}, "inputs": {"text": ""}},
    "2": {"class_type": "KSampler",
          "_meta": {"title": "sampler"}, "inputs": {"seed": 0}},
    "3": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "cockpit"}},
}
IMG2IMG = {
    **WORKFLOW,
    "2": {"class_type": "KSampler", "_meta": {"title": "sampler"},
          "inputs": {"seed": 0, "denoise": 1.0}},
    "4": {"class_type": "LoadImage", "_meta": {"title": "source_image"},
          "inputs": {"image": "unset.png"}},
}


def hermetic_env(tmp_path, monkeypatch):
    """Point every config knob at tmp; nothing reaches the network."""
    presets_dir = tmp_path / "presets"
    workflows_dir = tmp_path / "workflows"
    presets_dir.mkdir()
    workflows_dir.mkdir()
    (presets_dir / "testpreset.json").write_text(json.dumps(PRESET))
    (presets_dir / "txtonly.json").write_text(json.dumps(TXT_ONLY))
    (workflows_dir / "test-txt2img@v1.json").write_text(json.dumps(WORKFLOW))
    (workflows_dir / "test-img2img@v1.json").write_text(json.dumps(IMG2IMG))
    providers_file = tmp_path / "providers.json"
    providers_file.write_text(json.dumps({"models": [PIN]}))
    for attr, value in {
        "DB_PATH": tmp_path / "v4.sqlite",
        "STAGING_DIR": tmp_path / "staging",
        "PRESETS_DIR": presets_dir,
        "WORKFLOWS_DIR": workflows_dir,
        "PROVIDERS_FILE": providers_file,
        "COMFYUI_URL": "http://127.0.0.1:9",
        "OPENROUTER_URL": "http://127.0.0.1:9",
        "OPENROUTER_API_KEY": TEST_KEY,
    }.items():
        monkeypatch.setattr(config, attr, value)


def make_fakes():
    return {"comfy": FakeExecutor("comfy", "local"),
            "openrouter": FakeExecutor("openrouter", "external")}


@asynccontextmanager
async def booted(tmp_path, monkeypatch, fakes=None, *,
                 raise_app_exceptions=True):
    """`raise_app_exceptions=False` lets a route crash answer as the 500
    the browser would see (Starlette re-raises after its handler runs;
    test_capture.py reads the body and the ledger row)."""
    hermetic_env(tmp_path, monkeypatch)
    fakes = fakes or make_fakes()
    monkeypatch.setattr(
        wiring, "EXECUTOR_FACTORY",
        lambda orc: [fakes["comfy"], fakes["openrouter"]])
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(
            app=main.app, raise_app_exceptions=raise_app_exceptions)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1") as client:
            yield client, fakes


def sched():
    return wiring.current.scheduler


def store():
    return wiring.current.db


# --- boot ---

async def test_boots_on_a_fresh_store(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch):
        assert (tmp_path / "v4.sqlite").is_file()
        assert wiring.current is not None
        assert sched().started
        assert store().path == tmp_path / "v4.sqlite"   # COCKPIT_DB, one store
    assert wiring.current is None            # stop cleared the handle


async def test_refuses_a_pre_v4_archive_with_the_actionable_message(
        tmp_path, monkeypatch):
    hermetic_env(tmp_path, monkeypatch)
    conn = sqlite3.connect(tmp_path / "v4.sqlite")
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE sessions(id TEXT PRIMARY KEY);"
        "INSERT INTO meta VALUES('schema_version','3');")
    conn.commit()
    conn.close()
    monkeypatch.setattr(wiring, "EXECUTOR_FACTORY",
                        lambda orc: list(make_fakes().values()))
    with pytest.raises(SchemaVersionError, match="COCKPIT_DB"):
        async with main.app.router.lifespan_context(main.app):
            pass


async def test_app_routes_serve_health_and_presets(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        health = await client.get("/api/health")
        # ComfyUI is a closed port here, so degraded — but the shape holds
        assert health.status_code in (200, 503)
        body = health.json()
        assert body["build"]["id"] and body["preset_count"] == 2
        presets = await client.get("/api/presets")
        assert presets.status_code == 200
        assert [p["id"] for p in presets.json()] == ["testpreset", "txtonly"]


# --- v4 route helpers ---

MODEL = "testvendor/testmodel"


async def make_ws(client, name="Hero Sword"):
    r = await client.post("/api/workspaces", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def run(client, wid, **body):
    r = await client.post(f"/api/workspaces/{wid}/turns",
                          json={"prompt": "a rusty sword", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def settle_turn(turn_id, until=None, ticks=800):
    import asyncio
    until = until or {"landed", "failed", "skipped", "set_aside", "late",
                      "held"}
    statuses = {}
    for _ in range(ticks):
        turn = await store().get_turn(turn_id)
        statuses = {s["id"]: s["status"] for s in turn["slots"]}
        if all(v in until for v in statuses.values()):
            return turn
        await asyncio.sleep(0.01)
    raise AssertionError(f"turn never settled: {statuses}")


async def wait_running(slot_id, ticks=300):
    import asyncio
    for _ in range(ticks):
        if (await store().get_slot(slot_id))["status"] == "running":
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"slot {slot_id} never reached running")


# --- v4: queue empty state + workspaces ---

async def test_fresh_queue_states_the_one_gpu_truth(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        q = (await client.get("/api/queue")).json()
        assert q["depth"] == 0 and q["late"] == [] and q["held"] == []
        assert q["lanes"]["local"]["label"] == "LOCAL · SEQUENTIAL, ONE GPU"
        assert q["lanes"]["external"]["label"] \
            == "EXTERNAL · CONCURRENT, PRICED"
        assert q["seq"] >= 0 and q["now"]


async def test_workspace_crud_and_slug_collision(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        assert ws["slug"] == "hero-sword"
        dup = await client.post("/api/workspaces",
                                json={"name": "Hero  Sword"})
        assert dup.status_code == 409
        listing = (await client.get("/api/workspaces")).json()
        assert [w["id"] for w in listing] == [ws["id"]]
        assert (await client.get("/api/workspaces/zzz")).status_code == 404


async def test_the_list_is_most_recently_touched_first(tmp_path, monkeypatch):
    """The sidebar's order is the artist's trail back to what they
    were doing, so every verb that touches a workspace re-ranks it."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        first = await make_ws(client, "Hero Sword")
        second = await make_ws(client, "Rusty Shield")
        listing = (await client.get("/api/workspaces")).json()
        assert [w["id"] for w in listing] == [second["id"], first["id"]]

        await store().touch_workspace(first["id"])
        listing = (await client.get("/api/workspaces")).json()
        assert [w["id"] for w in listing] == [first["id"], second["id"]]


async def test_the_list_row_counts_live_nodes_and_a_blank_one_says_zero(
        tmp_path, monkeypatch):
    """The counts ride the list query, so the sidebar never fans a
    detail fetch per row. A purged node is gone from the count — lineage
    outlives purge (law 7) but the workspace no longer holds the take."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        row = (await client.get("/api/workspaces")).json()[0]
        assert (row["nodes"], row["promoted"]) == (0, 0)

        turn = await run(client, ws["id"],
                         local=[{"preset_id": "testpreset", "takes": 2}])
        await settle_turn(turn["turn"]["id"])
        row = (await client.get("/api/workspaces")).json()[0]
        assert (row["nodes"], row["promoted"]) == (2, 0)

        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        aside = await client.post(f"/api/nodes/{nodes[0]['id']}/set-aside")
        assert aside.status_code == 200, aside.text
        r = await client.post(f"/api/workspaces/{ws['id']}/purge",
                              json={"ids": [nodes[0]["id"]]})
        assert r.status_code == 200, r.text
        row = (await client.get("/api/workspaces")).json()[0]
        assert row["nodes"] == 1

        # the single-row readers don't compute them, and say so by omission
        detail = (await client.get(
            f"/api/workspaces/{ws['id']}")).json()
        assert "nodes" not in detail and "promoted" not in detail


async def test_rename_moves_the_name_and_never_the_slug(tmp_path, monkeypatch):
    """the slug is the promote folder. Renaming the asset must not
    strand the files already written under the old one."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client, "Hero Sword")
        r = await client.patch(f"/api/workspaces/{ws['id']}",
                               json={"name": "Hero Sword mk2"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Hero Sword mk2"
        assert r.json()["slug"] == "hero-sword"

        assert (await client.patch(f"/api/workspaces/{ws['id']}",
                                   json={"name": "  "})).status_code == 200
        assert (await client.patch(f"/api/workspaces/{ws['id']}",
                                   json={"name": ""})).status_code == 422
        assert (await client.patch("/api/workspaces/zzz",
                                   json={"name": "x"})).status_code == 404


# --- v4: Run (law 8) ---

async def test_run_creates_the_turn_whole_and_immediately_queued(
        tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = make_fakes()
    fakes["comfy"].default = Step(gate=gate)
    fakes["openrouter"].default = Step(gate=gate, cost=0.05)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        external=[{"model_id": MODEL, "takes": 1}])
        slots = out["turn"]["slots"]
        assert len(slots) == 3                       # the grid exists whole
        assert [s["pos"] for s in slots] == [0, 1, 2]
        assert all(s["status"] in ("queued", "running") for s in slots)
        assert slots[2]["committed_usd"] == 0.05
        assert out["seq"] > 0
        gate.set()
        done = await settle_turn(out["turn"]["id"], until={"landed"})
        assert all(s["status"] == "landed" for s in done["slots"])


async def test_run_refusals_name_the_problem(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        r = await client.post("/api/workspaces/zzz/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1}]})
        assert r.status_code == 404
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x"})
        assert r.status_code == 422          # zero takes
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "nope",
                                               "takes": 1}]})
        assert "unknown preset" in r.json()["detail"]
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "a/b",
                                               "takes": 1}]})
        assert "external model id" in r.json()["detail"]
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x",
                                    "external": [{"model_id": "no/model", "takes": 1}]})
        assert "no/model" in r.json()["detail"]
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"external": [{"model_id": MODEL, "takes": 1}]})
        assert "prompt brief" in r.json()["detail"]
        # a raw value the preset can't take (the Advanced fold's territory)
        # is refused in words, never a 500: an enum it doesn't
        # list, a number that isn't one
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1,
                                               "inputs": {"sampler_name":
                                                          "nope"}}]})
        assert r.status_code == 422
        assert "sampler_name" in r.json()["detail"]
        r = await client.post(f"/api/workspaces/{wid}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1,
                                               "inputs": {"steps": "hot"}}]})
        assert r.status_code == 422
        assert "can't take these inputs" in r.json()["detail"]


# --- v4: the queue view under load (law 13) ---

async def test_queue_view_shows_positions_elapsed_and_money(
        tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = make_fakes()
    fakes["comfy"].default = Step(gate=gate)
    fakes["openrouter"].default = Step(gate=gate, cost=0.05)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        external=[{"model_id": MODEL, "takes": 1}])
        local0 = out["turn"]["slots"][0]["id"]
        await wait_running(local0)
        q = (await client.get("/api/queue")).json()
        assert q["depth"] == 2                       # queues behind {2}
        lane = q["lanes"]["local"]["slots"]
        by_id = {v["id"]: v for v in lane}
        assert by_id[local0]["position"] == 0
        assert by_id[local0]["elapsed_ms"] is not None
        assert by_id[local0]["elapsed_ms"] >= 0
        queued = [v for v in lane if v["status"] == "queued"]
        assert [v["position"] for v in queued] == [1]
        ext = q["lanes"]["external"]["slots"]
        assert len(ext) == 1 and ext[0]["committed_usd"] == 0.05
        gate.set()
        await settle_turn(out["turn"]["id"], until={"landed"})
        q = (await client.get("/api/queue")).json()
        assert q["depth"] == 0 and q["lanes"]["external"]["slots"] == []


# --- v4: cancel (all outcomes in one breath) + late through the API ---

async def test_cancel_turn_states_all_outcomes_in_one_breath(
        tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = make_fakes()
    fakes["comfy"].default = Step(gate=gate)
    fakes["openrouter"].default = Step(outcome="hang")
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        external=[{"model_id": MODEL, "takes": 1}])
        tid = out["turn"]["id"]
        slots = out["turn"]["slots"]
        await wait_running(slots[0]["id"])
        await wait_running(slots[2]["id"])
        r = await client.post(f"/api/turns/{tid}/cancel")
        summary = r.json()
        assert summary["kept"] == 0
        assert summary["skipped"] == 1               # queued local: free
        assert summary["interrupted"] == 1           # running local: seconds lost
        assert summary["in_flight"] == [
            {"slot_id": slots[2]["id"], "committed_usd": 0.05}]
        gate.set()                                   # release the interrupt
        done = await settle_turn(tid)
        statuses = {s["id"]: s["status"] for s in done["slots"]}
        assert statuses[slots[1]["id"]] == "skipped"
        assert statuses[slots[2]["id"]] == "late"    # never failed
        q = (await client.get("/api/queue")).json()
        assert [v["id"] for v in q["late"]] == [slots[2]["id"]]
        assert q["late"][0]["awaiting"] is True      # the ride still lives
        assert q["late"][0]["committed_usd"] == 0.05
        second = await client.post(f"/api/turns/{tid}/cancel")
        assert second.json()["in_flight"] == []      # late rests as kept


async def test_late_bytes_land_into_the_cell_through_the_api(
        tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = make_fakes()
    fakes["openrouter"].default = Step(gate=gate, cost=0.04)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        tid = out["turn"]["id"]
        slot_id = out["turn"]["slots"][0]["id"]
        await wait_running(slot_id)
        r = await client.post(f"/api/slots/{slot_id}/cancel")
        assert r.json() == {"slot_id": slot_id,
                            "action": "cancelled_in_flight",
                            "status": None,
                            "committed_usd": 0.05}
        gate.set()                                   # the ride completes anyway
        await settle_turn(tid, until={"landed"})
        detail = (await client.get(
            f"/api/workspaces/{ws['id']}")).json()
        assert detail["charged_usd"] == 0.04         # the real charge
        assert detail["turns"][0]["slots"][0]["status"] == "landed"
        evs = (await client.get("/api/events?since=0")).json()["events"]
        types = [e["type"] for e in evs]
        assert types.index("slot.late") < types.index("slot.late_landed")


async def test_events_cursor_advances_and_never_replays(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        first = (await client.get("/api/events?since=0")).json()
        seqs = [e["seq"] for e in first["events"]]
        assert seqs and seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert first["seq"] == seqs[-1]
        again = (await client.get(
            f"/api/events?since={first['seq']}")).json()
        assert again["events"] == [] and again["seq"] == first["seq"]
        out2 = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(out2["turn"]["id"], until={"landed"})
        newer = (await client.get(
            f"/api/events?since={first['seq']}")).json()
        assert newer["events"]
        assert min(e["seq"] for e in newer["events"]) > first["seq"]


# --- v4: bench / unbench through the API (law 2: a pause, not a modal) ---

async def test_submit_to_a_benched_engine_holds_with_the_cause(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        await sched().bench("engine:testpreset", kind="model_down",
                            cause="down for the test")
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        assert out["turn"]["slots"][0]["status"] == "held"
        q = (await client.get("/api/queue")).json()
        assert q["held"][0]["bench"] == "engine:testpreset"
        assert q["benches"] == [{"scope": "engine:testpreset",
                                 "kind": "model_down",
                                 "cause": "down for the test"}]
        r = await client.post("/api/unbench",
                              json={"scope": "engine:testpreset"})
        assert r.json() == {"scope": "engine:testpreset", "cleared": True}
        done = await settle_turn(out["turn"]["id"], until={"landed"})
        assert done["slots"][0]["status"] == "landed"


# --- v4: the canvas feed: nodes list, image/thumb, position, view ---

async def test_nodes_list_serves_the_canvas_feed_and_leaks_nothing(
        tmp_path, monkeypatch):
    from app.api_v4 import NODE_KEYS
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        r = await client.get(f"/api/workspaces/{ws['id']}/nodes")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2
        for row in rows:
            # exact-keys parity: NODE_KEYS ⊆ model (else 500) and the model
            # adds nothing NODE_KEYS doesn't slim — no output_path, no
            # submitted_graph, no external, no dials on the wire. `mesh` is
            # the one DERIVED key (_mesh_view): a curated slice of
            # the mesh envelope, never the envelope, and null on an image.
            # `identity_hidden` is the blind-run flag, false outside one.
            assert set(row) == set(NODE_KEYS) | {"mesh", "identity_hidden"}
            assert row["mesh"] is None
            assert row["identity_hidden"] is None
            assert row["turn_idx"] == 0
            assert row["payload"] == "image"
            assert row["engine"] == "testpreset"
            assert row["take"] == 1
            assert row["parents"] == []
            assert row["x"] is None and row["y"] is None
            assert row["set_aside"] is False
        assert {row["slot_pos"] for row in rows} == {0, 1}
        assert (await client.get(
            "/api/workspaces/nope/nodes")).status_code == 404


async def test_nodes_list_excludes_purged_keeps_set_aside(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        aside, gone = nodes[0]["id"], nodes[1]["id"]
        await store().set_aside_node(aside)
        await store().purge_node(gone)
        rows = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        assert [r["id"] for r in rows] == [aside]
        assert rows[0]["set_aside"] is True


async def test_node_image_and_thumb_roundtrip(tmp_path, monkeypatch):
    from io import BytesIO
    from pathlib import Path
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.new("RGB", (640, 480), "#204060").save(buf, "PNG")
    png = buf.getvalue()
    fakes = make_fakes()
    fakes["comfy"].default = Step(data=png)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        nid = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]["id"]

        img = await client.get(f"/api/nodes/{nid}/image")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/png"
        assert "immutable" in img.headers["cache-control"]
        assert img.content == png

        th = await client.get(f"/api/nodes/{nid}/thumb")
        assert th.status_code == 200
        assert th.headers["content-type"] == "image/webp"
        assert "immutable" in th.headers["cache-control"]
        tim = PILImage.open(BytesIO(th.content))
        assert max(tim.size) <= 320
        assert abs(tim.size[0] / tim.size[1] - 640 / 480) < 0.02

        node = await store().get_node(nid)
        cache = Path(node["output_path"]).with_suffix(".thumb.webp")
        assert cache.is_file()
        mtime = cache.stat().st_mtime_ns
        again = await client.get(f"/api/nodes/{nid}/thumb")
        assert again.content == th.content
        assert cache.stat().st_mtime_ns == mtime      # served, not remade


async def test_node_image_and_thumb_refusals(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        assert (await client.get(
            "/api/nodes/nope/image")).status_code == 404
        assert (await client.get(
            "/api/nodes/nope/thumb")).status_code == 404

        # a 3d payload never has a thumbnail (payload gate beats file gate)
        mesh = await store().import_node(
            ws["id"], output_path=str(tmp_path / "missing.glb"),
            payload="3d")
        assert (await client.get(
            f"/api/nodes/{mesh['id']}/thumb")).status_code == 404

        # a corrupt artefact 404s in plain words, never 500s
        bad = tmp_path / "bad.png"
        bad.write_text("not an image")
        broken = await store().import_node(ws["id"], output_path=str(bad))
        r = await client.get(f"/api/nodes/{broken['id']}/thumb")
        assert r.status_code == 404
        assert "thumbnail" in r.json()["detail"]

        # purged nodes serve nothing (lineage stays, the wire does not)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        nid = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]["id"]
        await store().purge_node(nid)
        assert (await client.get(
            f"/api/nodes/{nid}/image")).status_code == 404
        assert (await client.get(
            f"/api/nodes/{nid}/thumb")).status_code == 404


async def test_node_position_roundtrip_and_refusals(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        nid = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]["id"]

        r = await client.post(f"/api/nodes/{nid}/position",
                              json={"x": 12.5, "y": -3.0})
        assert r.status_code == 200
        assert r.json() == {"id": nid, "x": 12.5, "y": -3.0}
        row = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]
        assert (row["x"], row["y"]) == (12.5, -3.0)

        assert (await client.post("/api/nodes/nope/position",
                                  json={"x": 0, "y": 0})).status_code == 404
        assert (await client.post(f"/api/nodes/{nid}/position",
                                  json={"x": 1.0})).status_code == 422
        await store().purge_node(nid)
        assert (await client.post(f"/api/nodes/{nid}/position",
                                  json={"x": 0, "y": 0})).status_code == 404


async def test_workspace_view_persists_the_viewport(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        vp = {"x": -120.5, "y": 40.0, "zoom": 0.8}
        r = await client.patch(f"/api/workspaces/{ws['id']}/view",
                               json={"viewport": vp})
        assert r.status_code == 200
        assert r.json()["viewport"] == vp
        assert r.json()["dials"] == ws["dials"]       # untouched by omission
        detail = (await client.get(
            f"/api/workspaces/{ws['id']}")).json()
        assert detail["viewport"] == vp               # survives reopen
        assert (await client.patch(
            "/api/workspaces/nope/view",
            json={"viewport": vp})).status_code == 404


# --- v4: cell verbs: reroll in place, set aside/restore ---

async def test_reroll_lands_a_new_take_in_the_same_slot(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        slot = turn["slots"][0]
        r = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slot"]["id"] == slot["id"]
        assert body["slot"]["status"] == "queued"
        assert body["slot"]["pos"] == slot["pos"]
        assert body["seq"] > 0
        again = await settle_turn(turn["id"], until={"landed"})
        assert len(again["slots"]) == 1          # law 8: geometry unchanged
        assert again["slots"][0]["attempt"] == 2
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        takes = [n for n in nodes if n["slot_id"] == slot["id"]]
        assert {n["take"] for n in takes} == {1, 2}  # old take stays (law 7)
        assert {n["verb"] for n in takes} == {"run", "reroll"}


async def test_reroll_external_requotes_todays_pin(tmp_path, monkeypatch):
    fakes = make_fakes()
    fakes["openrouter"].default = Step(cost=0.05)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        slot = turn["slots"][0]
        r = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert r.status_code == 200, r.text
        assert r.json()["slot"]["committed_usd"] == 0.05
        again = await settle_turn(turn["id"], until={"landed"})
        assert again["slots"][0]["charged_usd"] == 0.05


async def test_reroll_refuses_while_running_and_404s_unknown(
        tmp_path, monkeypatch):
    import asyncio
    gate = asyncio.Event()
    fakes = {"comfy": FakeExecutor("comfy", "local",
                                   default=Step(gate=gate)),
             "openrouter": FakeExecutor("openrouter", "external")}
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        slot = out["turn"]["slots"][0]
        await wait_running(slot["id"])
        r = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert r.status_code == 409
        assert "running" in r.json()["detail"]
        gate.set()
        await settle_turn(out["turn"]["id"], until={"landed"})
        assert (await client.post(
            "/api/slots/nope/reroll")).status_code == 404


# --- v4: Refine: plain parents through the img2img recipe ---

async def land_one(client, wid, **body):
    """One landed node id — the source every refine test starts from."""
    out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                    **body)
    await settle_turn(out["turn"]["id"], until={"landed"})
    nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
    return nodes[-1]["id"]


async def test_refine_runs_the_img2img_recipe_and_keeps_the_lineage(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        parent = await land_one(client, ws["id"])

        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "sharper edges",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 2}],
                                    "parents": [parent]})
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert len(turn["slots"]) == 2
        # the source rode into the graph content-addressed, and the
        # img2img-only dial came with it (capability input scoping)
        graph = (await store().get_slot(turn["slots"][0]["id"]))[
            "submitted_graph"]
        assert graph["4"]["inputs"]["image"].startswith("cockpit-")
        assert graph["2"]["inputs"]["denoise"] == 0.5
        assert turn["dials"]["parents"] == [parent]
        assert turn["dials"]["source_image_sha256"]

        done = await settle_turn(turn["id"], until={"landed"})
        assert [s["status"] for s in done["slots"]] == ["landed"] * 2
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        kids = [n for n in nodes if n["id"] != parent]
        assert len(kids) == 2
        assert all(n["parents"] == [parent] for n in kids)
        assert {n["verb"] for n in kids} == {"refine"}


# --- v4: dials: tables on the wire, per-take overrides, the lock ---

DIAL_ROSTER = ["anchor-hold", "prompt-grip", "cook-time", "frame", "seed",
               "sweep-one-dial"]


async def test_dial_and_engine_tables_serve_as_authored(tmp_path, monkeypatch):
    """The real configs/ files pass the wire models — the roster in
    order, anchors and badge notes intact, the matrix with its states —
    and authoring notes ($doc) stay home. An unreadable table is a 503 in
    words, never a 500."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        d = await client.get("/api/dials")
        assert d.status_code == 200, d.text
        dials = d.json()
        assert [x["id"] for x in dials["dials"]] == DIAL_ROSTER
        assert dials["position"] == {"min": 0, "max": 100, "default": 50}
        assert "$doc" not in dials
        grip = dials["dials"][1]
        assert grip["engines"]["z-image-base"]["anchors"] == [
            [0, 2.0], [50, 4.0], [100, 7.0]]
        assert grip["engines"]["z-image-turbo"]["transmits"] is False
        assert len(grip["engines"]["z-image-turbo"]["note"]) > 20
        assert dials["dials"][5]["sweep_slots"] == 4

        e = await client.get("/api/engines")
        assert e.status_code == 200, e.text
        engines = e.json()
        ids = {x["id"] for x in engines["engines"]}
        assert {"flux2-klein-4b", "z-image-turbo", "z-image-base",
                "sdxl-juggernaut-xi"} <= ids
        sdxl = next(x for x in engines["engines"]
                    if x["id"] == "sdxl-juggernaut-xi")
        assert sdxl["capabilities"]["maxResolution"]["state"] == "unverified"
        assert engines["external"]["defaults"]["seed"]["value"] is False

        monkeypatch.setattr(config, "CONFIGS_DIR", tmp_path / "nowhere")
        r = await client.get("/api/dials")
        assert r.status_code == 503
        assert "configs/dials.json" in r.json()["detail"]


async def test_take_overrides_give_each_slot_its_own_dials(
        tmp_path, monkeypatch):
    """A SWEEP ONE DIAL run: one turn, one key walking across its takes.
    Each swept slot's graph carries its own value and its dials record it,
    so a reroll of that cell rebuilds THAT value; a slot without an
    override inherits the turn's. A half-described sweep is refused whole
    (law 8)."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"], local=[{
            "preset_id": "testpreset", "takes": 3, "inputs": {"steps": 4},
            "take_overrides": [{"steps": 2}, {}, {"steps": 8}]}])
        slots = out["turn"]["slots"]
        stored = [await store().get_slot(s["id"]) for s in slots]
        assert [s["submitted_graph"]["2"]["inputs"]["steps"]
                for s in stored] == [2, 4, 8]
        assert [s["dials"]["steps"] for s in stored] == [2, 4, 8]
        # the batch's base lives on the turn under its engine
        assert out["turn"]["dials"]["engines"]["testpreset"]["steps"] == 4
        await settle_turn(out["turn"]["id"], until={"landed"})

        rr = await client.post(f"/api/slots/{slots[2]['id']}/reroll")
        assert rr.status_code == 200, rr.text
        rebuilt = (await store().get_slot(slots[2]["id"]))["submitted_graph"]
        assert rebuilt["2"]["inputs"]["steps"] == 8
        await settle_turn(out["turn"]["id"], until={"landed"})

        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 2,
                                               "take_overrides": [
                                                   {"steps": 2}]}]})
        assert r.status_code == 422
        assert "one per take" in r.json()["detail"]


async def test_locked_seed_broadcasts_one_value_to_every_take(
        tmp_path, monkeypatch):
    """The SEED dial's lock: one value to every take, local and external
    alike, so a dial change is the only thing that moves. The
    `fixed` policy keeps its seed+idx spread; a lock without a value is
    refused in words."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        external=[{"model_id": MODEL, "takes": 1}],
                        seed={"mode": "locked", "value": 7})
        assert [s["seed"] for s in out["turn"]["slots"]] == [7, 7, 7]
        await settle_turn(out["turn"]["id"], until={"landed"})
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        seed={"mode": "fixed", "value": 7})
        assert [s["seed"] for s in out["turn"]["slots"]] == [7, 8]
        await settle_turn(out["turn"]["id"], until={"landed"})
        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "x",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1}],
                                    "seed": {"mode": "locked"}})
        assert r.status_code == 422
        assert "locked seed needs its value" in r.json()["detail"]


async def test_a_root_run_carries_no_source_and_no_img2img_dial(
        tmp_path, monkeypatch):
    """The other half of capability scoping: denoise is declared
    img2img-only, so a txt2img take must not be silently carrying it."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        graph = (await store().get_slot(out["turn"]["slots"][0]["id"]))[
            "submitted_graph"]
        assert "denoise" not in graph["2"]["inputs"]
        assert "4" not in graph                  # no LoadImage in txt2img
        await settle_turn(out["turn"]["id"], until={"landed"})
        node = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]
        assert node["parents"] == [] and node["verb"] == "run"


async def test_refine_refusals_name_the_source_problem(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        parent = await land_one(client, wid)
        other = await make_ws(client, "Second Sword")

        async def refuse(**body):
            r = await client.post(f"/api/workspaces/{wid}/turns",
                                  json={"prompt": "x",
                                        "local": [{"preset_id": "testpreset",
                                                   "takes": 1}], **body})
            assert r.status_code == 422, r.text
            return r.json()["detail"]

        assert "no such source node" in await refuse(parents=["nope"])
        assert "single parent" in await refuse(parents=[parent, parent])
        assert "another workspace" in await refuse(
            parents=[await land_one(client, other["id"])])
        assert "no img2img recipe" in await refuse(
            parents=[parent], local=[{"preset_id": "txtonly", "takes": 1}])
        # external models take a text brief only: proved server-side, not
        # merely hidden in the composer
        assert "text brief only" in await refuse(
            parents=[parent], external=[{"model_id": MODEL, "takes": 1}])
        # the file behind a landed node can still go missing
        from pathlib import Path
        Path((await store().get_node(parent))["output_path"]).unlink()
        assert "missing from staging" in await refuse(parents=[parent])


async def test_reroll_of_a_refined_cell_restages_its_source(
        tmp_path, monkeypatch):
    """Reroll on a refined cell: the dials carry the parent, so the rebuilt
    graph is the img2img one again — not a txt2img fallback."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        parent = await land_one(client, ws["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "sharper edges",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1}],
                                    "parents": [parent]})
        turn = await settle_turn(r.json()["turn"]["id"], until={"landed"})
        slot = turn["slots"][0]

        rr = await client.post(f"/api/slots/{slot['id']}/reroll")
        assert rr.status_code == 200, rr.text
        graph = (await store().get_slot(slot["id"]))["submitted_graph"]
        assert graph["4"]["inputs"]["image"].startswith("cockpit-")
        await settle_turn(turn["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        latest = [n for n in nodes if n["slot_id"] == slot["id"]
                  and n["take"] == 2][0]
        assert latest["verb"] == "reroll" and latest["parents"] == [parent]


async def test_reroll_says_so_when_the_source_went_missing(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        parent = await land_one(client, ws["id"])
        r = await client.post(f"/api/workspaces/{ws['id']}/turns",
                              json={"prompt": "sharper edges",
                                    "local": [{"preset_id": "testpreset",
                                               "takes": 1}],
                                    "parents": [parent]})
        turn = await settle_turn(r.json()["turn"]["id"], until={"landed"})
        from pathlib import Path
        Path((await store().get_node(parent))["output_path"]).unlink()
        rr = await client.post(
            f"/api/slots/{turn['slots'][0]['id']}/reroll")
        assert rr.status_code == 409
        detail = rr.json()["detail"]
        assert "missing from staging" in detail
        assert "nothing was spent" in detail     # law 5: say what it cost


async def test_set_aside_is_a_place_and_restore_brings_it_back(
        tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        nid = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]["id"]
        r = await client.post(f"/api/nodes/{nid}/set-aside")
        assert r.status_code == 200, r.text
        assert r.json()["set_aside"] is True
        slot = (await store().get_turn(turn["id"]))["slots"][0]
        assert slot["status"] == "set_aside"     # footprint kept
        # idempotent — a second press changes nothing
        assert (await client.post(
            f"/api/nodes/{nid}/set-aside")).json()["set_aside"] is True
        r = await client.post(f"/api/nodes/{nid}/restore")
        assert r.status_code == 200
        assert r.json()["set_aside"] is False
        slot = (await store().get_turn(turn["id"]))["slots"][0]
        assert slot["status"] == "landed"
        assert (await client.post(
            "/api/nodes/nope/set-aside")).status_code == 404
        assert (await client.post(
            "/api/nodes/nope/restore")).status_code == 404


async def test_reroll_of_a_set_aside_slot_backfills_it(
        tmp_path, monkeypatch):
    """Backfill = reroll of an emptied slot: the aside node keeps its
    record while the slot runs a fresh take in the same footprint."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        local=[{"preset_id": "testpreset", "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        slot_id = turn["slots"][0]["id"]
        nid = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()[0]["id"]
        await client.post(f"/api/nodes/{nid}/set-aside")
        r = await client.post(f"/api/slots/{slot_id}/reroll")
        assert r.status_code == 200, r.text
        await settle_turn(turn["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        aside = [n for n in nodes if n["id"] == nid]
        assert aside and aside[0]["set_aside"] is True
        assert len([n for n in nodes if n["slot_id"] == slot_id]) == 2


# --- the set-aside tray, purge, backfill ---


async def test_tray_lists_set_aside_takes_with_bytes_and_children(
        tmp_path, monkeypatch):
    """The tray reads one feed (set-aside, never purged) and states what a
    purge would cost before any hold: bytes on disk — thumb cache included
    once it exists — and the live children that would keep only the prompt
    record."""
    from io import BytesIO
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.new("RGB", (640, 480), "#204060").save(buf, "PNG")
    png = buf.getvalue()
    fakes = make_fakes()
    fakes["comfy"].default = Step(data=png)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 2}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        a, b = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        kid = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                        parents=[a["id"]])
        await settle_turn(kid["turn"]["id"], until={"landed"})

        assert (await client.get(
            f"/api/workspaces/{wid}/tray")).json() == {
                "nodes": [], "bytes": 0}
        assert (await client.get(
            "/api/workspaces/nope/tray")).status_code == 404

        await client.post(f"/api/nodes/{a['id']}/set-aside")
        await client.post(f"/api/nodes/{b['id']}/set-aside")
        tray = (await client.get(f"/api/workspaces/{wid}/tray")).json()
        by_id = {n["id"]: n for n in tray["nodes"]}
        assert set(by_id) == {a["id"], b["id"]}
        assert by_id[a["id"]]["children"] == 1      # the refine child
        assert by_id[b["id"]]["children"] == 0
        assert by_id[a["id"]]["bytes"] == len(png)
        assert by_id[a["id"]]["turn_idx"] == 0 and by_id[a["id"]]["slot_pos"] == 0
        assert tray["bytes"] == 2 * len(png)
        # the thumb cache is part of what a purge frees
        assert (await client.get(
            f"/api/nodes/{a['id']}/thumb")).status_code == 200
        tray = (await client.get(f"/api/workspaces/{wid}/tray")).json()
        assert {n["id"]: n["bytes"] for n in tray["nodes"]}[a["id"]] > len(png)


async def test_purge_empties_the_tray_and_only_the_tray(
        tmp_path, monkeypatch):
    """Law 7's one delete verb: tombstone + files gone, lineage kept, and
    it refuses — purging nothing — unless every id is a live set-aside take
    of this workspace (the held button promised an exact count)."""
    from pathlib import Path
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 2}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        a, b = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        kid = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                        parents=[a["id"]])
        await settle_turn(kid["turn"]["id"], until={"landed"})
        purge = f"/api/workspaces/{wid}/purge"

        # still on the canvas: refused in words, nothing purged
        r = await client.post(purge, json={"ids": [a["id"]]})
        assert r.status_code == 422 and "still on the canvas" in r.json()["detail"]
        await client.post(f"/api/nodes/{a['id']}/set-aside")
        await client.post(f"/api/nodes/{b['id']}/set-aside")
        # one bad id poisons the whole call — all or nothing
        r = await client.post(purge, json={"ids": [a["id"], "nope"]})
        assert r.status_code == 422 and "nothing was purged" in r.json()["detail"]
        assert len((await client.get(
            f"/api/workspaces/{wid}/tray")).json()["nodes"]) == 2
        assert (await client.post(purge, json={"ids": []})).status_code == 422

        path = Path((await store().get_node(a["id"]))["output_path"])
        assert path.is_file()
        r = await client.post(purge, json={"ids": [a["id"]]})
        assert r.status_code == 200, r.text
        assert r.json()["purged"] == 1
        assert r.json()["freed_bytes"] == len(b"artifact")
        assert r.json()["children"] == 1
        assert not path.exists()
        assert (await client.get(
            f"/api/nodes/{a['id']}/image")).status_code == 404
        nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        assert a["id"] not in [n["id"] for n in nodes]
        child = [n for n in nodes if n["parents"]]
        assert child and child[0]["parents"] == [a["id"]]   # lineage kept
        tray = (await client.get(f"/api/workspaces/{wid}/tray")).json()
        assert [n["id"] for n in tray["nodes"]] == [b["id"]]
        # gone is gone: a second hold on the same id is refused, not silent
        r = await client.post(purge, json={"ids": [a["id"]]})
        assert r.status_code == 422 and "not in this workspace's tray" in r.json()["detail"]
        assert (await client.post("/api/workspaces/nope/purge",
                                  json={"ids": [b["id"]]})).status_code == 404
        # a node of another workspace is refused the same way
        other = await make_ws(client, "Other")
        r = await client.post(f"/api/workspaces/{other['id']}/purge",
                              json={"ids": [b["id"]]})
        assert r.status_code == 422


async def test_backfill_refills_only_the_set_aside_slots(
        tmp_path, monkeypatch):
    """Backfill in one action: every set-aside slot re-armed with a fresh seed,
    landed slots untouched, the aside takes still in the tray (law 7), the
    grid never reflowing (law 8)."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 2}],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        by_slot = {n["slot_id"]: n for n in nodes}
        s0, s1, s2 = turn["slots"]
        await client.post(f"/api/nodes/{by_slot[s0['id']]['id']}/set-aside")
        await client.post(f"/api/nodes/{by_slot[s2['id']]['id']}/set-aside")

        r = await client.post(f"/api/turns/{turn['id']}/backfill")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rerolled"] == 2 and body["refused"] == []
        statuses = {s["pos"]: s["status"] for s in body["turn"]["slots"]}
        assert statuses[1] == "landed"
        # the fakes land instantly, so re-armed reads as any of the three
        assert statuses[0] in {"queued", "running", "landed"}
        assert statuses[2] in {"queued", "running", "landed"}
        assert [s["pos"] for s in body["turn"]["slots"]] == [0, 1, 2]

        done = await settle_turn(turn["id"], until={"landed"})
        assert [s["status"] for s in done["slots"]] == ["landed"] * 3
        nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        assert len(nodes) == 5
        fresh = [n for n in nodes if n["take"] == 2]
        assert {n["verb"] for n in fresh} == {"backfill"}
        assert {n["slot_id"] for n in fresh} == {s0["id"], s2["id"]}
        tray = (await client.get(f"/api/workspaces/{wid}/tray")).json()
        assert len(tray["nodes"]) == 2              # the aside takes stay
        # the external cell re-quoted today's pin (committed, not charged)
        assert done["slots"][2]["committed_usd"] == 0.05

        # nothing left to fill is not an error
        r = await client.post(f"/api/turns/{turn['id']}/backfill")
        assert r.status_code == 200 and r.json()["rerolled"] == 0
        assert (await client.post(
            "/api/turns/nope/backfill")).status_code == 404


async def test_backfill_refusals_are_per_slot_and_stop_nothing(
        tmp_path, monkeypatch):
    """Law 1: a slot that can't be re-armed says why, in words, and the
    rest of the batch still runs."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]
        out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                        external=[{"model_id": MODEL, "takes": 1}])
        turn = await settle_turn(out["turn"]["id"], until={"landed"})
        for n in (await client.get(f"/api/workspaces/{wid}/nodes")).json():
            await client.post(f"/api/nodes/{n['id']}/set-aside")
        monkeypatch.setattr(wiring.current, "catalog", {})   # the preset vanished
        r = await client.post(f"/api/turns/{turn['id']}/backfill")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rerolled"] == 1
        assert [x["pos"] for x in body["refused"]] == [0]
        assert "no longer configured" in body["refused"][0]["reason"]
        assert "nothing was spent" in body["refused"][0]["reason"]
        statuses = {s["pos"]: s["status"] for s in body["turn"]["slots"]}
        assert statuses[0] == "set_aside"
        assert statuses[1] in {"queued", "running", "landed"}
        done = await settle_turn(turn["id"])
        assert [s["status"] for s in done["slots"]] == ["set_aside", "landed"]


# --- v4: per-engine batches (the take count is per engine) ---


async def test_run_lays_out_batches_engine_by_engine_each_with_its_own_raws(
        tmp_path, monkeypatch):
    """Two local engines and an external model in one turn: slots follow
    the roster order batch by batch, every local slot carries ITS preset's
    applied raws (two engines, two input dicts), an external row's
    takes are that many priced slots, and the lock broadcasts one seed
    across every engine. The turn's dials hold the shared part plus each
    engine's applied under `engines`; a reroll of any cell rebuilds from
    the cell alone."""
    fakes = make_fakes()
    fakes["openrouter"].default = Step(cost=0.05)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        ws = await make_ws(client)
        out = await run(
            client, ws["id"],
            local=[{"preset_id": "testpreset", "takes": 2,
                    "inputs": {"steps": 8}},
                   {"preset_id": "txtonly", "takes": 1}],
            external=[{"model_id": MODEL, "takes": 2}],
            seed={"mode": "locked", "value": 11})
        turn = out["turn"]
        assert [s["engine"] for s in turn["slots"]] == [
            "testpreset", "testpreset", "txtonly", MODEL, MODEL]
        assert [s["pos"] for s in turn["slots"]] == [0, 1, 2, 3, 4]
        assert [s["seed"] for s in turn["slots"]] == [11] * 5
        assert [s["committed_usd"] for s in turn["slots"]] == [
            None, None, None, 0.05, 0.05]
        stored = [await store().get_slot(s["id"]) for s in turn["slots"]]
        assert stored[0]["submitted_graph"]["2"]["inputs"]["steps"] == 8
        assert stored[1]["dials"]["steps"] == 8
        assert "steps" not in stored[2]["dials"]    # txtonly has no handle
        assert set(turn["dials"]["engines"]) == {"testpreset", "txtonly"}
        assert turn["dials"]["engines"]["testpreset"]["steps"] == 8
        assert turn["dials"]["seed"] == {"mode": "locked", "value": 11}
        await settle_turn(turn["id"], until={"landed"})

        third = turn["slots"][2]["id"]
        rr = await client.post(f"/api/slots/{third}/reroll")
        assert rr.status_code == 200, rr.text
        assert (await store().get_slot(third))["engine"] == "txtonly"
        await settle_turn(turn["id"], until={"landed"})


async def test_batch_refusals_name_the_row(tmp_path, monkeypatch):
    """A row listed twice, a sweep half-described on one engine, and a
    total over the cap are refused in words naming the row — never a 500,
    never a partial turn (law 8)."""
    async with booted(tmp_path, monkeypatch) as (client, _):
        ws = await make_ws(client)
        wid = ws["id"]

        async def refuse(**body):
            r = await client.post(f"/api/workspaces/{wid}/turns",
                                  json={"prompt": "x", **body})
            assert r.status_code == 422, r.text
            return r.json()["detail"]

        assert "listed twice" in await refuse(
            local=[{"preset_id": "testpreset", "takes": 1},
                   {"preset_id": "testpreset", "takes": 1}])
        assert "listed twice" in await refuse(
            external=[{"model_id": MODEL, "takes": 1},
                      {"model_id": MODEL, "takes": 1}])
        d = await refuse(local=[{"preset_id": "testpreset", "takes": 1},
                                {"preset_id": "txtonly", "takes": 2,
                                 "take_overrides": [{}]}])
        assert "txtonly" in d and "one per take" in d
        d = await refuse(local=[{"preset_id": "testpreset", "takes": 9},
                                {"preset_id": "txtonly", "takes": 8}])
        assert "caps at 16" in d and "17" in d
        detail = (await client.get(f"/api/workspaces/{wid}")).json()
        assert detail["turns"] == []


# --- v4: the cost surfaces: LOCAL ONLY proved on every money verb,
# the corner totals charged-only by the artist's clock ---

async def test_local_only_is_proved_on_every_money_verb(tmp_path, monkeypatch):
    """the switch rides the request, and the server — not the roster
    — is what keeps money in. A run under it with an external batch never
    becomes a turn; a local-only run records the flag for the manifest and
    the external executor never starts; a reroll or backfill under it
    refuses the PAID cell in words and leaves the local ones alone."""
    fakes = make_fakes()
    fakes["openrouter"].default = Step(cost=0.05)
    async with booted(tmp_path, monkeypatch, fakes) as (client, fk):
        ws = await make_ws(client)
        wid = ws["id"]
        r = await client.post(
            f"/api/workspaces/{wid}/turns",
            json={"prompt": "x", "local_only": True,
                  "local": [{"preset_id": "testpreset", "takes": 1}],
                  "external": [{"model_id": MODEL, "takes": 1}]})
        assert r.status_code == 422, r.text
        assert "LOCAL ONLY" in r.json()["detail"]
        detail = (await client.get(f"/api/workspaces/{wid}")).json()
        assert detail["turns"] == []                  # nothing was armed

        out = await run(client, wid, local_only=True,
                        local=[{"preset_id": "testpreset", "takes": 1}])
        turn = await store().get_turn(out["turn"]["id"])
        assert turn["dials"]["local_only"] is True    # The flag, recorded
        await settle_turn(turn["id"], until={"landed"})
        assert fk["openrouter"].starts == []          # provably no call

        # a mixed turn without the switch, then the switch on the verbs
        # that re-spend: the paid cell is refused, the local one rerolls
        out = await run(client, wid,
                        local=[{"preset_id": "testpreset", "takes": 1}],
                        external=[{"model_id": MODEL, "takes": 1}])
        assert "local_only" not in (await store().get_turn(
            out["turn"]["id"]))["dials"]
        t2 = await settle_turn(out["turn"]["id"], until={"landed"})
        ext = next(s for s in t2["slots"] if s["engine"] == MODEL)
        loc = next(s for s in t2["slots"] if s["engine"] != MODEL)
        assert len(fk["openrouter"].starts) == 1
        r = await client.post(f"/api/slots/{ext['id']}/reroll",
                              json={"local_only": True})
        assert r.status_code == 409, r.text
        d = r.json()["detail"]
        assert "LOCAL ONLY" in d and MODEL in d and "nothing was spent" in d
        assert len(fk["openrouter"].starts) == 1      # still one
        assert (await store().get_slot(ext["id"]))["status"] == "landed"
        r = await client.post(f"/api/slots/{loc['id']}/reroll",
                              json={"local_only": True})
        assert r.status_code == 200, r.text
        await settle_turn(t2["id"], until={"landed"})
        # no body at all is the switch off — the paid reroll goes out
        r = await client.post(f"/api/slots/{ext['id']}/reroll")
        assert r.status_code == 200, r.text
        await settle_turn(t2["id"], until={"landed"})
        assert len(fk["openrouter"].starts) == 2

        # backfill under the switch: per-slot refusal for the paid cell,
        # the local cell refilled — law 1, nothing stops the others
        nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
        current = {n["slot_id"]: n for n in nodes
                   if n["slot_id"] in {ext["id"], loc["id"]}
                   and n["take"] == 2}
        for n in current.values():
            await client.post(f"/api/nodes/{n['id']}/set-aside")
        r = await client.post(f"/api/turns/{t2['id']}/backfill",
                              json={"local_only": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rerolled"] == 1
        assert [x["slot_id"] for x in body["refused"]] == [ext["id"]]
        assert "LOCAL ONLY" in body["refused"][0]["reason"]
        assert len(fk["openrouter"].starts) == 2      # still two
        await settle_turn(t2["id"], until={"landed", "set_aside"})
        assert (await store().get_slot(ext["id"]))["status"] == "set_aside"
        assert (await store().get_slot(loc["id"]))["status"] == "landed"


async def test_costs_corner_reports_charged_by_the_artists_clock(
        tmp_path, monkeypatch):
    """the corner sums CHARGED from the ledger across every
    workspace — the pin's quote (0.05) never counts, the bill (0.04) does
    — and the day turns over at the ARTIST's midnight: the same charge is
    today at noon in their clock and yesterday at 01:30."""
    from datetime import datetime, timedelta, timezone
    fakes = make_fakes()
    fakes["openrouter"].default = Step(cost=0.04)
    async with booted(tmp_path, monkeypatch, fakes) as (client, _):
        r = await client.get("/api/costs")
        assert r.status_code == 200, r.text
        c = r.json()
        assert c["today_usd"] == 0 and c["month_usd"] == 0
        assert c["tz_minutes"] == 0
        assert c["day"].startswith(c["month"])
        ws = await make_ws(client)
        out = await run(client, ws["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        assert out["turn"]["slots"][0]["committed_usd"] == 0.05
        await settle_turn(out["turn"]["id"], until={"landed"})
        c = (await client.get("/api/costs")).json()
        assert c["today_usd"] == 0.04 and c["month_usd"] == 0.04

        # a second workspace's charge, landed three hours ago (the ledger
        # is append-only, so the clock is patched rather than the rows):
        # the corner is the artist's, not the workspace's, and it reads
        # from two clocks — local noon (still today) vs local 01:30 (that
        # was yesterday)
        from app import db as db_mod
        real_clock = db_mod.now_iso
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(
            db_mod, "now_iso",
            lambda: (now - timedelta(hours=3)).isoformat())
        ws2 = await make_ws(client, "Other Sword")
        out = await run(client, ws2["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        monkeypatch.setattr(db_mod, "now_iso", real_clock)
        minute = now.hour * 60 + now.minute

        def offset(local_minute):            # a clock reading local_minute now
            tz = local_minute - minute
            return tz + 1440 if tz < -840 else tz - 1440 if tz > 840 else tz

        noon = (await client.get(
            f"/api/costs?tz={offset(720)}")).json()
        assert noon["today_usd"] == 0.08
        early = (await client.get(
            f"/api/costs?tz={offset(90)}")).json()
        assert early["today_usd"] == 0.04
        # 400 days back is nobody's month
        monkeypatch.setattr(
            db_mod, "now_iso",
            lambda: (now - timedelta(days=400)).isoformat())
        ws3 = await make_ws(client, "Old Sword")
        out = await run(client, ws3["id"],
                        external=[{"model_id": MODEL, "takes": 1}])
        await settle_turn(out["turn"]["id"], until={"landed"})
        monkeypatch.setattr(db_mod, "now_iso", real_clock)
        c = (await client.get("/api/costs?tz=%d" % offset(720))).json()
        assert c["today_usd"] == 0.08 and c["month_usd"] == 0.08
        # the offset is bounded to a real clock
        assert (await client.get("/api/costs?tz=9000")).status_code == 422
