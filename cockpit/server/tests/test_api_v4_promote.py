"""The v4 promote verb (app/api_v4_promote.py) through the real app.

    target      GET says what the destination IS, in words, with which disk
                it sits on; POST re-points it (the offer), validated
                BEFORE it is stored, and an empty path falls back to .env
    preview     every file the hold would write, by its final path, with the
                take's identity, what it cost (law 5) and the replay tier
                — and asking writes nothing
    promote     PNG + manifest land under PROMOTE_DIR/<workspace slug>/
              , the manifest carries the fields, the fact enters
                the ledger, and the node feed wears `promoted_at` after
    D5          a broken target refuses with both halves apart and nothing
                written; the take is untouched and can promote after a
                re-point
    twice       a second promotion is a second asset and a second row —
                the preview names the earlier ones

The seam beneath (validate-then-write, all-or-nothing, never create the
root) is test_promote.py's; the manifest's own shape is asserted here
because this route is where the v4 product model meets it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app import config, manifests
from fakes import FakeExecutor, Step
from test_api_v4 import booted, make_ws, run, settle_turn, store

pytestmark = pytest.mark.anyio


def png_bytes(w=96, h=64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 30, 40)).save(buf, "PNG")
    return buf.getvalue()


def promote_root(tmp_path, monkeypatch) -> Path:
    """The promote tree, and the fence around it. tmp_path stands in
    for the promotion DRIVE here, so a case can re-point beside the tree
    the way the artist can on D: — the cases that test the fence itself
    narrow or widen this deliberately."""
    root = tmp_path / "PROMOTED"
    root.mkdir()
    monkeypatch.setattr(config, "PROMOTE_DIR", root)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [tmp_path])
    return root


def local_fakes(data=None):
    return {"comfy": FakeExecutor("comfy", "local",
                                  default=Step(data=data or png_bytes())),
            "openrouter": FakeExecutor("openrouter", "external")}


async def one_landed_node(client, wid):
    turn = await run(client, wid, local=[{"preset_id": "testpreset",
                                          "takes": 1}])
    await settle_turn(turn["turn"]["id"])
    nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
    assert len(nodes) == 1, nodes
    return nodes[0]


# --- the target, and re-pointing it ---

async def test_the_target_says_where_it_is_and_which_disk_it_is_on(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        t = (await client.get("/api/promote-target")).json()
        assert t["ok"] is True and t["path"] == str(root)
        assert t["source"] == "env" and t["default_path"] == str(root)
        assert t["message"] is None and t["remedy"] is None
        # this machine's own disk is legal but never silent, and the
        # claim comes from the mount table, so a tmp_path root says it
        assert t["where"] == ("on this machine's own disk, not a mounted drive, "
                              "so nothing delivered here leaves this machine")


async def test_a_broken_target_is_read_as_broken_without_being_created(
        tmp_path, monkeypatch):
    gone = tmp_path / "PROMOTED"           # renamed away, never made
    monkeypatch.setattr(config, "PROMOTE_DIR", gone)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [tmp_path])  # on the drive
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        t = (await client.get("/api/promote-target")).json()
        assert t["ok"] is False
        assert "isn't there" in t["message"]
        assert "point the cockpit at another folder" in t["remedy"]
        assert not gone.exists()


async def test_repointing_stores_only_a_folder_that_works(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    good = tmp_path / "elsewhere"
    good.mkdir()
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": str(good)})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == str(good)
        assert r.json()["source"] == "set-here"
        assert (await client.get("/api/promote-target")).json()["path"] \
            == str(good)

        # a folder that isn't one is refused and NOT remembered
        r = await client.post("/api/promote-target",
                              json={"path": str(tmp_path / "nope")})
        assert r.status_code == 409
        body = r.json()
        assert "isn't there" in body["detail"]           # whole sentence
        assert "isn't there" in body["message"]          # and its two halves
        assert "point the cockpit at another folder" in body["remedy"]
        assert body["path"] == str(tmp_path / "nope")
        assert (await client.get("/api/promote-target")).json()["path"] \
            == str(good)

        # empty clears it back to .env's default
        r = await client.post("/api/promote-target", json={"path": ""})
        assert r.json()["source"] == "env"
        assert r.json()["path"] == str(config.PROMOTE_DIR)


async def test_a_relative_path_is_refused_in_words(tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": "PROMOTED"})
        assert r.status_code == 422
        assert "full path" in r.json()["detail"]


# --- the preview (before the hold) ---

async def test_the_preview_names_every_file_and_writes_nothing(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client, "Hero Sword")
        node = await one_landed_node(client, ws["id"])

        p = (await client.get(
            f"/api/nodes/{node['id']}/promote-preview")).json()
        assert p["folder"] == f"{root}/{ws['slug']}"
        paths = [f["path"] for f in p["files"]]
        assert paths == [f"{p['folder']}/{p['asset_id']}.png",
                         f"{p['folder']}/{p['asset_id']}.json"]
        assert all(f["what"] for f in p["files"])
        assert p["facts"].startswith("0a · TESTPRESET · S ")
        assert p["cost"] == "This take cost no money — it ran on your own card."
        assert p["replayable"].startswith("exact:")
        assert p["target"]["ok"] is True
        assert p["already"] == []
        # asking is not doing
        assert list(root.iterdir()) == []


async def test_the_preview_states_a_broken_target_without_refusing_to_answer(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        monkeypatch.setattr(config, "PROMOTE_DIR", tmp_path / "gone")
        p = (await client.get(
            f"/api/nodes/{node['id']}/promote-preview")).json()
        assert p["target"]["ok"] is False
        assert "isn't there" in p["target"]["message"]
        assert p["files"], "the plinth still says what it would write"


# --- promoting ---

async def test_promote_writes_the_pair_under_the_workspace_slug(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client, "Hero Sword")
        node = await one_landed_node(client, ws["id"])

        r = await client.post(f"/api/nodes/{node['id']}/promote")
        assert r.status_code == 200, r.text
        out = r.json()
        aid = out["asset_id"]
        png = root / ws["slug"] / f"{aid}.png"
        man = root / ws["slug"] / f"{aid}.json"
        assert out["promoted_path"] == str(png)
        assert out["manifest_path"] == str(man)
        assert png.read_bytes() == Path(
            (await store().get_node(node["id"]))["output_path"]).read_bytes()
        # no temp dir left behind
        assert not [p for p in (root / ws["slug"]).iterdir()
                    if p.name.startswith(".promote-")]

        m = json.loads(man.read_text())
        assert m["manifest_version"] == 4
        assert m["asset_id"] == aid
        assert m["workspace"] == {"id": ws["id"], "slug": ws["slug"],
                                  "name": "Hero Sword"}
        assert m["node_id"] == node["id"]
        assert m["turn"]["idx"] == 0 and m["slot"]["pos"] == 0
        assert m["parent_node_ids"] == []          # plural, empty here
        assert m["engine"] == "testpreset"
        assert m["transport"] == "local" and m["provider"] == "comfyui"
        assert m["local_only"] is True
        assert m["replayable"].startswith("exact:")
        assert m["prompts"]["positive"] == "a rusty sword"
        assert m["seed"] == node["seed"]
        assert m["resolution"] == {"width": 96, "height": 64}
        assert m["cost"] == {"committed_usd": None, "charged_usd": None}
        assert m["submitted_graph"] is not None    # verbatim
        assert m["regions"] == [] and m["region_policy"] is None
        assert m["loras"] == [] and m["carry_channels"] == []


async def test_the_promotion_is_a_ledger_row_and_the_node_wears_the_mark(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        assert node["promoted_at"] is None

        out = (await client.post(
            f"/api/nodes/{node['id']}/promote")).json()

        feed = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        assert feed[0]["promoted_at"] == out["promoted_at"]

        rows = await store().node_events(node["id"], "node.promoted")
        assert len(rows) == 1
        assert rows[0]["payload"]["promoted_path"] == out["promoted_path"]
        assert rows[0]["payload"]["manifest_path"] == out["manifest_path"]


async def test_promoting_twice_is_two_assets_and_the_preview_says_so(
        tmp_path, monkeypatch):
    """The artist owns the tree and may have moved what we wrote. Refusing
    would be the app claiming to know a folder it doesn't watch."""
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        first = (await client.post(
            f"/api/nodes/{node['id']}/promote")).json()

        p = (await client.get(
            f"/api/nodes/{node['id']}/promote-preview")).json()
        assert p["already"] == [first["promoted_at"]]

        second = (await client.post(
            f"/api/nodes/{node['id']}/promote")).json()
        assert second["asset_id"] != first["asset_id"]
        pngs = sorted(p.name for p in (root / ws["slug"]).glob("*.png"))
        assert pngs == sorted([f"{first['asset_id']}.png",
                               f"{second['asset_id']}.png"])
        # the mark still points at the FIRST — the moment it left
        feed = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        assert feed[0]["promoted_at"] == first["promoted_at"]


async def test_the_sidebar_count_is_assets_promoted_not_promotions(
        tmp_path, monkeypatch):
    """The sidebar row says how many of a workspace's takes have left
    for the tree. Promoting the same take twice is two assets on disk but
    one take gone out (the mark points at the first), so the count holds."""
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        row = (await client.get("/api/workspaces")).json()[0]
        assert (row["nodes"], row["promoted"]) == (1, 0)

        await client.post(f"/api/nodes/{node['id']}/promote")
        await client.post(f"/api/nodes/{node['id']}/promote")
        row = (await client.get("/api/workspaces")).json()[0]
        assert (row["nodes"], row["promoted"]) == (1, 1)


async def test_a_refine_carries_its_source_out_with_it(tmp_path, monkeypatch):
    """the asset is reproducible from the promoted tree alone, so the
    parent image rides along under `<asset_id>.inputs/`."""
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        source = await one_landed_node(client, ws["id"])
        turn = await run(client, ws["id"], parents=[source["id"]],
                         local=[{"preset_id": "testpreset", "takes": 1}])
        await settle_turn(turn["turn"]["id"])
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        child = [n for n in nodes if n["id"] != source["id"]][0]

        p = (await client.get(
            f"/api/nodes/{child['id']}/promote-preview")).json()
        assert any("source.png" in f["path"] for f in p["files"])

        out = (await client.post(
            f"/api/nodes/{child['id']}/promote")).json()
        aid = out["asset_id"]
        src = root / ws["slug"] / f"{aid}.inputs" / "source.png"
        assert src.is_file()
        assert f"{root}/{ws['slug']}/{aid}.inputs/source.png" in out["files"]
        m = json.loads((root / ws["slug"] / f"{aid}.json").read_text())
        assert m["parent_node_ids"] == [source["id"]]
        assert m["capability"] == "img2img"
        assert m["input_images"][0]["node_id"] == source["id"]
        assert m["input_images"][0]["file"] == f"{aid}.inputs/source.png"


# --- D5 on the real verb ---

async def test_a_broken_target_refuses_the_hold_and_leaves_the_take_alone(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        gone = tmp_path / "renamed-away"
        monkeypatch.setattr(config, "PROMOTE_DIR", gone)

        r = await client.post(f"/api/nodes/{node['id']}/promote")
        assert r.status_code == 409
        body = r.json()
        assert "isn't there" in body["message"]
        assert "the take is untouched" in body["remedy"]
        assert body["detail"] == f"{body['message']} — {body['remedy']}"
        assert body["path"] == str(gone)
        assert not gone.exists()               # never created
        assert list(root.iterdir()) == []
        feed = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        assert feed[0]["promoted_at"] is None
        assert await store().node_events(node["id"], "node.promoted") == []

        # and after re-pointing, the same take promotes
        assert (await client.post("/api/promote-target",
                                  json={"path": str(root)})).status_code == 200
        out = (await client.post(
            f"/api/nodes/{node['id']}/promote")).json()
        assert Path(out["promoted_path"]).is_file()


async def test_promoting_an_unknown_or_purged_node_is_a_404(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        assert (await client.post("/api/nodes/nope/promote")).status_code \
            == 404
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        await client.post(f"/api/nodes/{node['id']}/set-aside")
        await client.post(f"/api/workspaces/{ws['id']}/purge",
                          json={"ids": [node["id"]]})
        r = await client.post(f"/api/nodes/{node['id']}/promote")
        assert r.status_code == 404


async def test_a_take_whose_file_is_gone_is_refused_in_words(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        Path((await store().get_node(node["id"]))["output_path"]).unlink()
        r = await client.post(f"/api/nodes/{node['id']}/promote")
        assert r.status_code == 409
        assert "isn't on the cockpit's disk any more" in r.json()["detail"]
        assert "the record of it stays" in r.json()["detail"]


# --- the manifest's hardest case: a region take ---

async def test_a_region_take_promotes_its_masks_and_its_whole_pipeline(
        tmp_path, monkeypatch):
    """A region take's `submitted_graph` is the {region_plan} envelope, not
    a ComfyUI graph. The manifest wants the passes, the drawn masks and
    the KEEP composite in the record — so the manifest reads the envelope
    rather than filing it as one opaque graph."""
    from test_api_v4_regions import (booted_regions, make_source,
                                     region_fakes, run_regions)

    root = promote_root(tmp_path, monkeypatch)
    fakes = region_fakes()
    fakes["region"].default = Step(data=png_bytes(512, 512))
    async with booted_regions(tmp_path, monkeypatch, fakes) as (client, fk):
        ws = await make_ws(client, "Night Forge")
        source = await make_source(client, fk, ws["id"])
        r = await run_regions(client, ws["id"], source["id"])
        assert r.status_code == 201, r.text
        await settle_turn(r.json()["turn"]["id"], until={"landed"})
        nodes = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        take = [n for n in nodes if n["verb"] == "region"][0]

        p = (await client.get(
            f"/api/nodes/{take['id']}/promote-preview")).json()
        assert any("region-" in f["path"] for f in p["files"])
        assert any("source.png" in f["path"] for f in p["files"])

        out = (await client.post(
            f"/api/nodes/{take['id']}/promote")).json()
        aid = out["asset_id"]
        folder = root / ws["slug"]
        m = json.loads((folder / f"{aid}.json").read_text())

        assert m["capability"] == "region"
        assert m["parent_node_ids"] == [source["id"]]
        assert m["region_policy"] == "nd3-v1"
        assert m["regions"], "the drawn regions are the record"
        for r_ in m["regions"]:
            assert set(r_) >= {"id", "name", "action", "prompt", "z_order",
                               "mask_sha256", "mask_file"}
            assert (folder / r_["mask_file"]).is_file()
        # the passes, each with the EXACT graph that ran — and the
        # envelope filed as itself, not as a graph
        assert m["submitted_graph"] is None
        assert m["region_plan"] is not None
        assert m["pipeline"] and all(
            set(p_) >= {"idx", "kind", "capability", "graph", "box"}
            for p_ in m["pipeline"])
        assert (folder / f"{aid}.inputs" / "source.png").is_file()


# --- the fence and the spelling, through the route ---

async def test_a_writable_but_off_drive_folder_is_refused_not_stored(
        tmp_path, monkeypatch):
    """The hole this closes (probed live, 2026-08-27): /tmp and /data exist
    in the container and are writable, so without the fence the route
    accepts and stores them, and a promotion lands where a rebuild or
    `down -v` erases it. The fence answers before the filesystem is
    consulted at all."""
    root = promote_root(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [root])   # fence = the tree
    off_drive = tmp_path / "container-tmp"
    off_drive.mkdir()                              # exists and is writable
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": str(off_drive)})
        assert r.status_code == 409, r.text
        body = r.json()
        assert "is outside" in body["message"]
        assert "only promote onto the drive it was given" in body["message"]
        assert "pick a folder on that drive" in body["remedy"]
        # nothing remembered: the target is still the real one
        assert (await client.get("/api/promote-target")).json()["path"] \
            == str(root)


async def test_a_subfolder_of_the_drive_is_a_legal_re_point(
        tmp_path, monkeypatch):
    """The point of the widened mount: steering means something. A folder
    beside the promote dir, on the same drive, is a legal destination."""
    root = promote_root(tmp_path, monkeypatch)
    drive = tmp_path
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [drive])
    beside = drive / "PROMOTED_SCRATCH"
    beside.mkdir()
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": str(beside)})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == str(beside)

        # and a promotion actually lands there
        ws = await make_ws(client, "Hero Sword")
        node = await one_landed_node(client, ws["id"])
        out = (await client.post(
            f"/api/nodes/{node['id']}/promote")).json()
        assert Path(out["promoted_path"]).is_file()
        assert str(beside) in out["promoted_path"]
        assert list(root.iterdir()) == []       # not the old default


async def test_the_target_payload_carries_both_spellings_and_the_fence(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [tmp_path])
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        t = (await client.get("/api/promote-target")).json()
        assert t["path"] == str(root)
        assert t["windows_path"] is None        # tmp_path is not a drive
        assert t["roots"] == [str(tmp_path)]
        assert t["roots_said"] == str(tmp_path)


async def test_the_route_takes_the_drive_spelling_the_artist_types(
        tmp_path, monkeypatch):
    """`D:\\PROMOTED` is how a Windows user names this tree. The drive
    spelling is translated at the edge, and still fenced
    and still validated, so a drive the cockpit can't see is refused for
    the RIGHT reason."""
    promote_root(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [Path("/mnt/z")])
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": "Z:\\PROMOTED"})
        assert r.status_code == 409
        body = r.json()
        assert body["path"] == "/mnt/z/PROMOTED"     # translated
        assert "isn't mounted" in body["message"] or \
               "isn't there" in body["message"]
        assert "outside" not in body["message"]         # the fence let it by


async def test_a_bare_relative_path_is_still_the_only_422(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.post("/api/promote-target",
                              json={"path": "PROMOTED"})
        assert r.status_code == 422
        # the message names BOTH spellings it would have taken
        assert "D:\\PROMOTED" in r.json()["detail"]
        assert "full path from /" in r.json()["detail"]


async def test_the_promote_route_is_fenced_even_past_a_stale_setting(
        tmp_path, monkeypatch):
    """Defence in depth: the fence lives in validate_target, which every
    write goes through — so a root that got stored before the fence
    tightened (or was hand-edited into the store) is refused at the hold,
    with nothing written."""
    promote_root(tmp_path, monkeypatch)
    off_drive = tmp_path / "container-tmp"
    off_drive.mkdir()
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [tmp_path])
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        ws = await make_ws(client)
        node = await one_landed_node(client, ws["id"])
        # slipped past the route, straight into the store
        await store().set_meta("promote_dir", str(off_drive))
        monkeypatch.setattr(config, "PROMOTE_ROOTS",
                            [tmp_path / "PROMOTED"])

        r = await client.post(f"/api/nodes/{node['id']}/promote")
        assert r.status_code == 409
        assert "is outside" in r.json()["message"]
        assert list(off_drive.iterdir()) == []
        assert await store().node_events(node["id"], "node.promoted") == []


# --- a mesh promotes as a mesh, into `_3d/<slug>/` ---
#
# The route's own seam is tested here rather than in test_api_v4_mesh.py,
# but the mesh LANE is that file's (its `booted_mesh` registers the third
# executor, and `LANDED_MESH` is the as-run record a real MeshExecutor
# lands with) — so the scaffolding is borrowed rather than rebuilt.


def mesh_fakes_that_land_a_glb(record=None):
    """The three executors a mesh turn needs, with the mesh one landing a
    file NAMED like the engine's — `MeshExecutor` sends `<engine>.glb`, and
    that suffix is what the promotion carries out."""
    from fakes import Probe
    from test_api_v4_mesh import LANDED_MESH
    probe = Probe()
    mesh = FakeExecutor("mesh", "local", probe=probe)
    mesh.default = Step(data=b"glTF binary", filename="trellis2.glb",
                        submitted_graph=record or LANDED_MESH)
    return {"comfy": FakeExecutor("comfy", "local", probe=probe,
                                  default=Step(data=png_bytes())),
            "openrouter": FakeExecutor("openrouter", "external"),
            "mesh": mesh}


async def landed_mesh(client, wid):
    """A source image and the mesh built from it, both landed."""
    from test_api_v4_mesh import TRELLIS, run_mesh
    src = await one_landed_node(client, wid)
    out = await run_mesh(client, wid, src["id"],
                         mesh=[{"engine_id": TRELLIS, "takes": 1}])
    assert out.status_code == 201, out.text
    await settle_turn(out.json()["turn"]["id"], until={"landed"})
    nodes = (await client.get(f"/api/workspaces/{wid}/nodes")).json()
    return src, [n for n in nodes if n["payload"] == "3d"][0]


async def test_a_mesh_promotes_into_3d_as_a_glb_with_its_orbit(
        tmp_path, monkeypatch):
    """The cockpit's own path to `_3d/`. The GLB keeps
    its suffix, the manifest sits beside it, the orbit rides out so the
    folder is readable without a 3D viewer, and the source image is under
    `.inputs/` because the image IS the input."""
    from test_api_v4_mesh import booted_mesh
    root = promote_root(tmp_path, monkeypatch)
    async with booted_mesh(tmp_path, monkeypatch,
                           mesh_fakes_that_land_a_glb()) as (client, _f):
        ws = await make_ws(client)
        _src, mesh = await landed_mesh(client, ws["id"])
        r = await client.post(f"/api/nodes/{mesh['id']}/promote")
        assert r.status_code == 200, r.text
        body = r.json()
        folder = root / "_3d" / ws["slug"]
        aid = body["asset_id"]
        assert body["promoted_path"] == str(folder / f"{aid}.glb")
        assert (folder / f"{aid}.glb").read_bytes() == b"glTF binary"
        assert sorted(str(p.relative_to(folder))
                      for p in folder.rglob("*") if p.is_file()) == [
            f"{aid}.glb", f"{aid}.inputs/source.png", f"{aid}.json",
            f"{aid}.turntable.webp"]
        # the orbit was carried, not re-rendered, and the caller is told
        assert (folder / f"{aid}.turntable.webp").stat().st_size > 0
        assert str(folder / f"{aid}.turntable.webp") in body["files"]
        # the ledger row points at the mesh itself
        feed = (await client.get(
            f"/api/workspaces/{ws['id']}/nodes")).json()
        assert [n for n in feed
                if n["id"] == mesh["id"]][0]["promoted_at"] is not None


async def test_the_mesh_manifest_carries_the_meshs_own_facts(
        tmp_path, monkeypatch):
    """A manifest for a payload with none of the image fields: no resolution to
    read, no model files to hash, and the measurements funnel.py made
    instead — projected to the top level, because a promoted asset must
    stand alone on D: without a reader knowing to open a graph envelope."""
    from test_api_v4_mesh import booted_mesh
    root = promote_root(tmp_path, monkeypatch)
    async with booted_mesh(tmp_path, monkeypatch,
                           mesh_fakes_that_land_a_glb()) as (client, _f):
        ws = await make_ws(client)
        src, mesh = await landed_mesh(client, ws["id"])
        r = await client.post(f"/api/nodes/{mesh['id']}/promote")
        aid = r.json()["asset_id"]
        man = json.loads((root / "_3d" / ws["slug"]
                          / f"{aid}.json").read_text())
        assert man["payload"] == "3d" and man["capability"] == "to_3d"
        assert man["mesh"] == {
            "engine": "trellis2",
            # a record from before kinds existed carries no kind and IS a
            # generate — the promoted artefact says which verb made it
            "kind": "generate", "runner_job_id": "job-abc123",
            "runner_version": None, "seed": 7, "params": {},
            "engine_args": [], "cutout": None, "glb_sha256": "deadbeef",
            "engine_wall_s": None, "triangles": 999588, "vertices": 691815,
            "watertight": False, "textured": True, "file_mb": 33.8,
            "turntable": f"{aid}.turntable.webp"}
        # a GLB has no resolution, and PIL failing on one is not an answer
        assert man["resolution"] is None
        # The third tier: nothing here claims pinned model files
        assert man["replayable"] == manifests.REPLAY_MESH
        assert man["model"]["files"] == []
        # the image it was made from, promoted with it
        assert man["input_images"][0]["file"] == f"{aid}.inputs/source.png"
        assert man["parent_node_ids"] == [src["id"]]


async def test_the_preview_describes_a_mesh_as_a_mesh(tmp_path, monkeypatch):
    """The words before the hold are the payload's own — a plinth that
    promises a PNG and writes a GLB has told the artist the wrong thing
    about the one irreversible act."""
    from test_api_v4_mesh import booted_mesh
    promote_root(tmp_path, monkeypatch)
    async with booted_mesh(tmp_path, monkeypatch,
                           mesh_fakes_that_land_a_glb()) as (client, _f):
        ws = await make_ws(client)
        _src, mesh = await landed_mesh(client, ws["id"])
        pv = (await client.get(
            f"/api/nodes/{mesh['id']}/promote-preview")).json()
        assert pv["folder"].endswith(f"/_3d/{ws['slug']}")
        paths = [f["path"] for f in pv["files"]]
        assert paths[0].endswith(f"{pv['asset_id']}.glb")
        assert any(p.endswith(".turntable.webp") for p in paths)
        assert not any(p.endswith(f"{pv['asset_id']}.png") for p in paths)
        whats = " ".join(f["what"] for f in pv["files"])
        assert "the mesh, exactly the file the engine wrote" in whats
        assert "turntable render" in whats
        assert "the image this mesh was made from" in whats
        assert pv["replayable"] == manifests.REPLAY_MESH
        # asking still wrote nothing
        assert not Path(pv["folder"]).exists()


async def test_a_mesh_with_no_orbit_still_promotes_and_says_so(
        tmp_path, monkeypatch):
    """law 12 on the irreversible act: a mesh whose turntable was never
    rendered promotes anyway, and neither the folder nor the manifest
    claims an orbit that isn't there."""
    from test_api_v4_mesh import booted_mesh
    root = promote_root(tmp_path, monkeypatch)
    bare = {"mesh_job": {"engine": "trellis2", "runner_job_id": "job-x",
                         "seed": 7, "glb_sha256": "beefdead"}}
    async with booted_mesh(tmp_path, monkeypatch,
                           mesh_fakes_that_land_a_glb(bare)) as (client, _f):
        ws = await make_ws(client)
        _src, mesh = await landed_mesh(client, ws["id"])
        pv = (await client.get(
            f"/api/nodes/{mesh['id']}/promote-preview")).json()
        assert not any(p["path"].endswith(".turntable.webp")
                       for p in pv["files"])
        r = await client.post(f"/api/nodes/{mesh['id']}/promote")
        assert r.status_code == 200, r.text
        aid = r.json()["asset_id"]
        folder = root / "_3d" / ws["slug"]
        assert (folder / f"{aid}.glb").is_file()
        assert not (folder / f"{aid}.turntable.webp").exists()
        man = json.loads((folder / f"{aid}.json").read_text())
        assert man["mesh"]["turntable"] is None
        assert man["mesh"]["triangles"] is None      # never defaulted


# --- the picker: one level of the drive, as handles ---

async def test_the_browser_starts_at_the_drive_and_hands_back_handles(
        tmp_path, monkeypatch):
    """The top is the fence itself, enumerated: every root, as a handle
    whose `path` a re-point takes verbatim — the face never composes one."""
    root = promote_root(tmp_path, monkeypatch)       # roots = [tmp_path]
    (root / "hero-sword").mkdir()
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        top = (await client.get("/api/promote-browse")).json()
        assert top["here"] is None and top["parent"] is None
        assert [f["path"] for f in top["folders"]] == [str(tmp_path)]
        assert top["folders"][0]["ok"] is True

        # stepping in: the handle's own path, back to the same route
        level = (await client.get(
            "/api/promote-browse",
            params={"path": top["folders"][0]["path"]})).json()
        assert level["here"]["path"] == str(tmp_path)
        assert level["parent"] is None            # a lone root: nowhere up
        # (the harness keeps its own folders under tmp_path too)
        assert "PROMOTED" in [f["name"] for f in level["folders"]]

        inner = (await client.get(
            "/api/promote-browse", params={"path": str(root)})).json()
        assert inner["parent"] == str(tmp_path)
        assert inner["here"]["name"] == "PROMOTED"
        assert [f["name"] for f in inner["folders"]] == ["hero-sword"]
        assert inner["note"] is None

        # and choosing one is the re-point the field already offers
        pick = inner["folders"][0]
        r = await client.post("/api/promote-target",
                              json={"path": pick["path"]})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == str(root / "hero-sword")
        assert r.json()["source"] == "set-here"


async def test_the_browser_lists_folders_only_sorted_and_skips_housekeeping(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    for name in ("zeta", "Alpha", "mid", "$RECYCLE.BIN", ".hidden",
                 "System Volume Information"):
        (root / name).mkdir()
    (root / "a-file.png").write_bytes(b"x")
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        level = (await client.get(
            "/api/promote-browse", params={"path": str(root)})).json()
        assert [f["name"] for f in level["folders"]] == ["Alpha", "mid",
                                                          "zeta"]


async def test_the_browser_badges_a_folder_it_cannot_promote_into(
        tmp_path, monkeypatch):
    """law 12: an unfit folder is shown with its reason, never hidden. A
    symlink that leaves the drive is listed, marked, and stepping into it
    is refused by the same fence — resolved, so `..` can't slip past."""
    root = promote_root(tmp_path, monkeypatch)
    (root / "fine").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        level = (await client.get(
            "/api/promote-browse", params={"path": str(root)})).json()
        by_name = {f["name"]: f for f in level["folders"]}
        assert by_name["fine"]["ok"] is True and by_name["fine"]["why"] is None
        assert by_name["escape"]["ok"] is False
        assert "is outside" in by_name["escape"]["why"]

        for bad in (by_name["escape"]["path"], str(outside),
                    f"{root}/../../{outside.name}"):
            r = await client.get("/api/promote-browse", params={"path": bad})
            assert r.status_code == 409, bad
            body = r.json()
            assert "is outside" in body["message"]
            assert "only look around the drive" in body["message"]
            assert "start from the drive" in body["remedy"]
            assert body["detail"] == f"{body['message']} — {body['remedy']}"
    outside.rmdir()


async def test_the_browser_refuses_what_is_not_a_folder_in_words(
        tmp_path, monkeypatch):
    root = promote_root(tmp_path, monkeypatch)
    (root / "a-file.png").write_bytes(b"x")
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        r = await client.get("/api/promote-browse",
                             params={"path": str(root / "gone")})
        assert r.status_code == 409
        assert "isn't there" in r.json()["message"]
        assert not (root / "gone").exists()             # looking never makes
        r = await client.get("/api/promote-browse",
                             params={"path": str(root / "a-file.png")})
        assert r.status_code == 409
        assert "is a file, not a folder" in r.json()["message"]
        r = await client.get("/api/promote-browse",
                             params={"path": "PROMOTED"})
        assert r.status_code == 422
        assert "full path" in r.json()["detail"]


async def test_with_several_roots_up_from_a_root_is_the_top(
        tmp_path, monkeypatch):
    promote_root(tmp_path, monkeypatch)
    d, e = tmp_path / "d", tmp_path / "e"
    d.mkdir(), e.mkdir()
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [d, e])
    async with booted(tmp_path, monkeypatch, local_fakes()) as (client, _):
        top = (await client.get("/api/promote-browse")).json()
        assert [f["path"] for f in top["folders"]] == [str(d), str(e)]
        at_d = (await client.get("/api/promote-browse",
                                 params={"path": str(d)})).json()
        assert at_d["parent"] == ""                     # promote.TOP
        again = (await client.get("/api/promote-browse",
                                  params={"path": at_d["parent"]})).json()
        assert again == top
