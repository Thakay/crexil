"""Schema v4 behaviour suite: pins the product-model store.

What this file pins:
- clean CREATE + stamp '4', pre-v4 files refused untouched
- the slot lifecycle and frozen grid geometry (PRD §2, law 8)
- reroll as pure append; old takes stay reachable (law 7)
- purge tombstones, never deletes; lineage survives (law 7)
- events append-only with a monotonic seq (law 9)
- committed vs charged separate; rollups sum the charged ledger only
- FTS5 over node prompts: the searchable tray
- the regions duck-type contract regions.py depends on
"""

import asyncio
import sqlite3
from contextlib import asynccontextmanager

import pytest

from app.db import (ALLOWED, SCHEMA_VERSION, Database, SchemaVersionError, new_id, now_iso,
                    slugify)

pytestmark = pytest.mark.anyio


# --- helpers ---

@asynccontextmanager
async def opened(tmp_path, name="v4.sqlite"):
    db = Database(tmp_path / name)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


async def mk_ws(db, name="Hero Sword"):
    return await db.create_workspace(name)


async def mk_turn(db, ws, engines=("klein", "z-image"), prompt="a rusty sword",
                  **kw):
    specs = [{"engine": e} for e in engines]
    return await db.create_turn_with_slots(
        ws["id"], prompt, {"cook": 3}, specs, **kw)


async def dispatch(db, slot, **over):
    kw = dict(job_ref=f"job-{slot['id']}", submitted_graph={"g": slot["pos"]},
              seed=42)
    kw.update(over)
    return await db.dispatch_slot(slot["id"], **kw)


async def land(db, slot, **over):
    kw = dict(payload="image", verb="run",
              output_path=f"/staging/{slot['id']}.png")
    kw.update(over)
    return await db.land_node(slot["id"], **kw)


def make_v3_archive(path):
    """A minimal file with the pre-v4 (v3) schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE sessions(id TEXT PRIMARY KEY, preset_id TEXT);"
        "INSERT INTO meta VALUES('schema_version','3');"
        "INSERT INTO sessions VALUES('abc','raw-klein');")
    conn.commit()
    conn.close()


def table_names(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                        " ORDER BY name").fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- open / version guard (the ladder starts at 4, forward only) ---

async def test_fresh_create_stamps_the_current_rung(tmp_path):
    async with opened(tmp_path) as db:
        cur = await db.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'")
        assert (await cur.fetchone())["value"] == SCHEMA_VERSION == "13"


async def test_a_v4_file_climbs_the_whole_ladder_on_open(tmp_path):
    """A v4 file gains the law columns (rung 5) and the
    failure kind (rung 6) and the stamp, rung by rung, rows intact."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "e"}])
        # roll the file back to v4 by hand: drop the rungs' columns + stamp
        await db.conn.execute("ALTER TABLE slots DROP COLUMN detail")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN retry_of")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN kind")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN frame")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN facts_json")
        await db.conn.execute(
            "UPDATE meta SET value='4' WHERE key='schema_version'")
    async with opened(tmp_path) as db:
        cur = await db.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'")
        assert (await cur.fetchone())["value"] == SCHEMA_VERSION
        slot = await db.get_slot(turn["slots"][0]["id"])
        assert slot["detail"] is None and slot["retry_of"] is None
        assert slot["kind"] is None
        assert slot["frame"] is None and slot["facts"] is None
        assert slot["engine"] == "e"          # rows survived the rungs


async def test_a_v5_file_climbs_to_rung_6_on_open(tmp_path):
    """Rung 6 on a v5 file: one ALTER, the stamp, and
    a failed row keeps its face while gaining an empty kind."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "e"}])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid)
        await db.fail_slot(sid, cause="c", detail="d", kind="error")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN kind")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN frame")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN facts_json")
        await db.conn.execute(
            "UPDATE meta SET value='5' WHERE key='schema_version'")
    async with opened(tmp_path) as db:
        cur = await db.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'")
        assert (await cur.fetchone())["value"] == SCHEMA_VERSION
        slot = await db.get_slot(sid)
        assert slot["cause"] == "c" and slot["detail"] == "d"
        assert slot["kind"] is None           # pre-rung failures carry none


async def test_a_v6_file_climbs_to_rung_7_on_open(tmp_path):
    """Rung 7 on a v6 file: two ALTERs, the stamp, and
    a failed row keeps its face while gaining an empty frame and facts;
    the next dispatch stamps its frame and an OOM its facts."""
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {}, [{"engine": "e"}])
        sid = turn["slots"][0]["id"]
        await db.dispatch_slot(sid)
        await db.fail_slot(sid, cause="c", detail="d", kind="error")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN frame")
        await db.conn.execute("ALTER TABLE slots DROP COLUMN facts_json")
        await db.conn.execute(
            "UPDATE meta SET value='6' WHERE key='schema_version'")
    async with opened(tmp_path) as db:
        cur = await db.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'")
        assert (await cur.fetchone())["value"] == SCHEMA_VERSION
        slot = await db.get_slot(sid)
        assert slot["cause"] == "c" and slot["kind"] == "error"
        assert slot["frame"] is None and slot["facts"] is None
        await db.reroll_slot(sid, 5)
        await db.dispatch_slot(sid, frame="1536x1536")
        await db.fail_slot(sid, cause="oom", kind="oom",
                           facts={"smaller": "1152x1152"})
        slot = await db.get_slot(sid)
        assert slot["frame"] == "1536x1536"
        assert slot["facts"] == {"smaller": "1152x1152"}
        failed = [e for e in await db.slot_events(sid)
                  if e["type"] == "slot.failed"][-1]
        assert failed["payload"]["frame"] == "1536x1536"
        assert failed["payload"]["facts"] == {"smaller": "1152x1152"}


async def test_open_refuses_a_pre_v4_archive_with_an_actionable_error(tmp_path):
    p = tmp_path / "archive.sqlite"
    make_v3_archive(p)
    db = Database(p)
    with pytest.raises(SchemaVersionError, match="pre-v4 archive"):
        await db.open()
    await db.close()


async def test_the_refusal_names_the_escape_hatch(tmp_path):
    p = tmp_path / "archive.sqlite"
    make_v3_archive(p)
    db = Database(p)
    with pytest.raises(SchemaVersionError, match="COCKPIT_DB"):
        await db.open()
    await db.close()


async def test_refused_archive_is_left_untouched(tmp_path):
    p = tmp_path / "archive.sqlite"
    make_v3_archive(p)
    before = table_names(p)
    db = Database(p)
    with pytest.raises(SchemaVersionError):
        await db.open()
    await db.close()
    assert table_names(p) == before  # guard ran before any DDL


async def test_open_refuses_a_stamp_newer_than_the_code(tmp_path):
    p = tmp_path / "future.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES('schema_version','999');")
    conn.close()
    db = Database(p)
    with pytest.raises(SchemaVersionError, match="newer than this code"):
        await db.open()
    await db.close()


async def test_open_refuses_a_non_cockpit_database(tmp_path):
    p = tmp_path / "random.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE foo(bar TEXT)")
    conn.close()
    db = Database(p)
    with pytest.raises(SchemaVersionError, match="not a cockpit database"):
        await db.open()
    await db.close()


async def test_reopen_of_a_v4_file_is_idempotent_and_keeps_data(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
    async with opened(tmp_path) as db:
        again = await db.get_workspace(ws["id"])
        assert again and again["name"] == "Hero Sword"


async def test_open_defaults_to_config_v4_db_path(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "from-config.sqlite")
    db = Database()
    await db.open()
    try:
        assert db.path == tmp_path / "from-config.sqlite"
        assert db.path.exists()
    finally:
        await db.close()


async def test_default_path_is_the_one_store():
    # one store: COCKPIT_DB (config.py keeps the default file's `-v4` name
    # so a pre-v4 archive beside it is never the default; the guard above
    # refuses it anyway)
    from app import config
    db = Database()
    assert db.path == config.DB_PATH


async def test_the_archive_refusal_names_the_env_var(tmp_path):
    path = tmp_path / "v3.sqlite"
    make_v3_archive(path)
    db = Database(path)
    with pytest.raises(SchemaVersionError, match="COCKPIT_DB"):
        await db.open()
    await db.close()


async def test_wal_and_foreign_keys_are_enabled(tmp_path):
    async with opened(tmp_path) as db:
        cur = await db.conn.execute("PRAGMA journal_mode")
        assert (await cur.fetchone())[0] == "wal"
        cur = await db.conn.execute("PRAGMA foreign_keys")
        assert (await cur.fetchone())[0] == 1


# --- helpers of the module itself ---

def test_now_iso_is_utc_and_new_id_is_twelve_hex():
    assert now_iso().endswith("+00:00")
    nid = new_id()
    assert len(nid) == 12
    int(nid, 16)  # raises if not hex


def test_slugify_is_path_safe_and_never_empty():
    assert slugify("Hero Sword!  v2") == "hero-sword-v2"
    assert slugify("***") != ""


# --- workspaces ---

async def test_create_workspace_returns_a_row_with_a_slug(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db, "Hero Sword!")
        assert ws["slug"] == "hero-sword"
        assert ws["viewport"] == {} and ws["dials"] == {}


async def test_duplicate_slug_is_rejected(tmp_path):
    async with opened(tmp_path) as db:
        await mk_ws(db, "Hero Sword")
        with pytest.raises(sqlite3.IntegrityError):
            await mk_ws(db, "Hero Sword")


async def test_touch_workspace_bumps_updated_at(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        await db.touch_workspace(ws["id"])
        again = await db.get_workspace(ws["id"])
        assert again["updated_at"] >= ws["updated_at"]
        assert again["created_at"] == ws["created_at"]


async def test_set_workspace_view_persists_viewport_and_dials(tmp_path):
    async with opened(tmp_path) as db:  # reopening restores exactly
        ws = await mk_ws(db)
        await db.set_workspace_view(ws["id"], viewport={"x": 4, "zoom": 0.5},
                                    dials={"cook": 2})
        again = await db.get_workspace(ws["id"])
        assert again["viewport"] == {"x": 4, "zoom": 0.5}
        assert again["dials"] == {"cook": 2}


# --- turns + slots: atomic create, frozen geometry (law 8) ---

async def test_create_turn_with_slots_returns_turn_slots_and_event(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws, anchor=(120.0, -40.0))
        assert [s["pos"] for s in turn["slots"]] == [0, 1]
        assert all(s["status"] == "queued" for s in turn["slots"])
        assert turn["anchor_x"] == 120.0 and turn["anchor_y"] == -40.0
        events = await db.events_since(0)
        assert [e["type"] for e in events] == ["turn.created"]
        assert events[0]["payload"]["slots"] == 2


async def test_turn_creation_is_all_or_nothing(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        with pytest.raises(sqlite3.IntegrityError):
            await db.create_turn_with_slots(  # slot 2 poisoned: engine NULL
                ws["id"], "p", {}, [{"engine": "klein"}, {"engine": None}])
        cur = await db.conn.execute("SELECT COUNT(*) FROM turns")
        assert (await cur.fetchone())[0] == 0
        cur = await db.conn.execute("SELECT COUNT(*) FROM slots")
        assert (await cur.fetchone())[0] == 0
        assert await db.events_since(0) == []


async def test_an_empty_turn_is_refused(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        with pytest.raises(ValueError, match="at least one slot"):
            await db.create_turn_with_slots(ws["id"], "p", {}, [])


async def test_slot_positions_are_unique_per_turn(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws)
        with pytest.raises(sqlite3.IntegrityError):
            await db.conn.execute(
                "INSERT INTO slots(id, turn_id, pos, engine, created_at)"
                " VALUES(?,?,0,'klein',?)", (new_id(), turn["id"], now_iso()))


async def test_turn_idx_increments_per_workspace(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        assert (await mk_turn(db, ws))["idx"] == 0
        assert (await mk_turn(db, ws))["idx"] == 1
        other = await mk_ws(db, "Cave Troll")
        assert (await mk_turn(db, other))["idx"] == 0


async def test_slots_carry_their_spec_and_default_to_turn_dials(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await db.create_turn_with_slots(
            ws["id"], "p", {"cook": 3},
            [{"engine": "klein", "seed": 9, "dials": {"cook": 5}},
             {"engine": "z-image", "committed_usd": 0.02}])
        first, second = turn["slots"]
        assert first["seed"] == 9 and first["dials"] == {"cook": 5}
        assert second["dials"] == {"cook": 3}  # inherited effective copy
        assert second["committed_usd"] == 0.02


# --- slot lifecycle ---

async def test_dispatch_moves_queued_to_running_and_bumps_attempt(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        slot = await dispatch(db, slot)
        assert slot["status"] == "running"
        assert slot["attempt"] == 1
        assert slot["started_at"] is not None


async def test_dispatch_stores_job_ref_graph_and_committed_cost(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        slot = await dispatch(db, slot, job_ref="pr-77",
                              submitted_graph={"nodes": [1]},
                              committed_usd=0.03)
        assert slot["job_ref"] == "pr-77"
        assert slot["submitted_graph"] == {"nodes": [1]}  # reality, kept
        assert slot["committed_usd"] == 0.03


async def test_fail_slot_records_cause_attempt_engine_time_and_cost(tmp_path):
    async with opened(tmp_path) as db:  # the full permanent record
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        slot = await db.fail_slot(slot["id"], cause="engine returned nothing",
                                  charged_usd=0.01, detail="HTTP 502")
        assert slot["status"] == "failed" and slot["cause"]
        event = (await db.slot_events(slot["id"]))[-1]
        assert event["type"] == "slot.failed"
        assert event["payload"]["cause"] == "engine returned nothing"
        assert event["payload"]["attempt"] == 1
        assert event["payload"]["engine"] == "klein"
        assert event["ts"] and event["charged_usd"] == 0.01


@pytest.mark.parametrize("move", [
    "land_queued", "dispatch_landed", "aside_queued", "held_running"])
async def test_illegal_transitions_raise(tmp_path, move):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        if move == "land_queued":
            with pytest.raises(ValueError, match="illegal slot transition"):
                await land(db, slot)
        elif move == "dispatch_landed":
            await dispatch(db, slot)
            await land(db, slot)
            with pytest.raises(ValueError, match="illegal slot transition"):
                await dispatch(db, slot)
        elif move == "aside_queued":
            with pytest.raises(ValueError, match="illegal slot transition"):
                await db.transition_slot(slot["id"], "set_aside")
        elif move == "held_running":
            await dispatch(db, slot)
            with pytest.raises(ValueError, match="illegal slot transition"):
                await db.transition_slot(slot["id"], "held")


async def test_an_unknown_status_is_rejected_outright(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        with pytest.raises(ValueError, match="unknown slot status"):
            await db.transition_slot(slot["id"], "exploded")


async def test_held_skip_late_transitions_persist_with_events(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        a, b = (await mk_turn(db, ws))["slots"]
        await db.transition_slot(a["id"], "held")
        await db.transition_slot(a["id"], "queued")   # resume
        await db.transition_slot(a["id"], "skipped")
        await dispatch(db, b)
        await db.transition_slot(b["id"], "late")
        assert (await db.get_slot(a["id"]))["status"] == "skipped"
        assert (await db.get_slot(b["id"]))["status"] == "late"
        types = [e["type"] for e in await db.slot_events(a["id"])]
        assert types == ["slot.held", "slot.queued", "slot.skipped"]


async def test_a_late_slot_may_still_land(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await db.transition_slot(slot["id"], "late")
        node = await land(db, slot)
        assert node["take"] == 1


async def test_pending_slots_returns_exactly_queued_and_running(tmp_path):
    async with opened(tmp_path) as db:  # the restart-recovery query
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws, engines=("a", "b", "c", "d"))
        queued, running, landed, failed = turn["slots"]
        await dispatch(db, running)
        await dispatch(db, landed)
        await land(db, landed)
        await dispatch(db, failed)
        await db.fail_slot(failed["id"], cause="x")
        pending = {s["id"] for s in await db.pending_slots()}
        assert pending == {queued["id"], running["id"]}


async def test_active_slots_is_the_queue_view_and_carries_the_join(tmp_path):
    async with opened(tmp_path) as db:  # the queue-view query
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws, engines=("a", "b", "c", "d", "e", "f"))
        queued, running, held, late, landed, skipped = turn["slots"]
        await dispatch(db, running)
        await db.transition_slot(held["id"], "held")
        await dispatch(db, late)
        await db.transition_slot(late["id"], "late")
        await dispatch(db, landed)
        await land(db, landed)
        await db.transition_slot(skipped["id"], "skipped")
        active = await db.active_slots()
        assert {s["id"] for s in active} \
            == {queued["id"], running["id"], held["id"], late["id"]}
        for s in active:
            assert s["workspace_id"] == ws["id"]
            assert s["turn_idx"] == turn["idx"]
        assert [s["pos"] for s in active] == sorted(s["pos"] for s in active)


async def test_latest_seq_is_zero_fresh_and_tracks_the_tail(tmp_path):
    async with opened(tmp_path) as db:
        assert await db.latest_seq() == 0
        ws = await mk_ws(db)                 # workspace.created appends
        await mk_turn(db, ws)
        events = await db.events_since(0)
        assert events
        assert await db.latest_seq() == events[-1]["seq"]


# --- landing a node ---

async def test_land_node_links_node_slot_and_event_in_one_stroke(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws)
        slot = turn["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot, prompt="a rusty sword")
        assert node["workspace_id"] == ws["id"]
        assert node["turn_id"] == turn["id"]
        assert node["slot_id"] == slot["id"] and node["take"] == 1
        slot = await db.get_slot(slot["id"])
        assert slot["status"] == "landed" and slot["finished_at"]
        landed_events = [e for e in await db.events_since(0)
                         if e["type"] == "node.landed"]
        assert len(landed_events) == 1
        assert landed_events[0]["node_id"] == node["id"]


async def test_landing_is_atomic_when_a_parent_is_unknown(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        with pytest.raises(sqlite3.IntegrityError):
            await land(db, slot, parents=["nope"])
        cur = await db.conn.execute("SELECT COUNT(*) FROM nodes")
        assert (await cur.fetchone())[0] == 0
        assert (await db.get_slot(slot["id"]))["status"] == "running"
        assert not [e for e in await db.events_since(0)
                    if e["type"] == "node.landed"]


async def test_landed_node_round_trips_every_generative_field(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(
            db, slot, verb="refine", prompt="cobalt shield", engine="klein",
            seed=7, dials={"cook": 4}, submitted_graph={"n": 2},
            external={"req": 1}, duration_ms=4200, committed_usd=0.05,
            charged_usd=0.04, x=10.0, y=20.0)
        again = await db.get_node(node["id"])
        assert again["verb"] == "refine" and again["prompt"] == "cobalt shield"
        assert again["engine"] == "klein" and again["seed"] == 7
        assert again["dials"] == {"cook": 4}
        assert again["submitted_graph"] == {"n": 2}
        assert again["external"] == {"req": 1}
        assert again["duration_ms"] == 4200
        assert again["committed_usd"] == 0.05 and again["charged_usd"] == 0.04
        assert again["x"] == 10.0 and again["y"] == 20.0


async def test_landed_node_inherits_the_slot_dispatch_values(tmp_path):
    async with opened(tmp_path) as db:  # provenance copied at land
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot, seed=42, submitted_graph={"g": 0},
                       committed_usd=0.03)
        node = await land(db, slot)
        assert node["engine"] == "klein"
        assert node["seed"] == 42
        assert node["submitted_graph"] == {"g": 0}
        assert node["committed_usd"] == 0.03


async def test_parents_round_trip_in_order(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        roots = [await db.import_node(ws["id"], output_path=f"/i/{i}.png")
                 for i in range(3)]
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        order = [roots[2]["id"], roots[0]["id"], roots[1]["id"]]
        node = await land(db, slot, parents=order)
        assert (await db.get_node(node["id"]))["parents"] == order


async def test_take_equals_the_slot_attempt_at_land(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        first = await land(db, slot)
        await db.reroll_slot(slot["id"], 99)
        await dispatch(db, slot)
        second = await land(db, slot)
        assert (first["take"], second["take"]) == (1, 2)


async def test_import_node_lands_parentless_without_turn_or_slot(tmp_path):
    async with opened(tmp_path) as db:  # plain image parents
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i/ref.png",
                                    prompt="moodboard ref")
        assert node["verb"] == "import"
        assert node["turn_id"] is None and node["slot_id"] is None
        assert node["take"] is None and node["parents"] == []


async def test_payload_check_rejects_anything_but_image_and_3d(tmp_path):
    async with opened(tmp_path) as db:  # the enum extends when a payload lands
        ws = await mk_ws(db)
        with pytest.raises(sqlite3.IntegrityError):
            await db.conn.execute(
                "INSERT INTO nodes(id, workspace_id, payload, verb,"
                " output_path, created_at) VALUES(?,?,'video','run','/x',?)",
                (new_id(), ws["id"], now_iso()))
        node = await db.import_node(ws["id"], output_path="/m.glb",
                                    payload="3d")
        assert node["payload"] == "3d"


# --- reroll (per cell, in place) ---

async def test_reroll_requeues_the_same_slot_with_a_new_seed(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await land(db, slot)
        rerolled = await db.reroll_slot(slot["id"], 1234)
        assert rerolled["status"] == "queued"
        assert rerolled["seed"] == 1234
        assert rerolled["engine"] == slot["engine"]
        assert rerolled["dials"] == slot["dials"]
        assert rerolled["job_ref"] is None and rerolled["cause"] is None


async def test_reroll_keeps_the_old_take_reachable(tmp_path):
    async with opened(tmp_path) as db:  # law 7: nothing silently lost
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        first = await land(db, slot)
        await db.reroll_slot(slot["id"], 99)
        await dispatch(db, slot)
        second = await land(db, slot)
        takes = [n for n in await db.list_nodes(ws["id"])
                 if n["slot_id"] == slot["id"]]
        assert [t["id"] for t in takes] == [first["id"], second["id"]]


async def test_a_slot_never_holds_two_nodes_for_one_attempt(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot)
        with pytest.raises(sqlite3.IntegrityError):
            await db.conn.execute(
                "INSERT INTO nodes(id, workspace_id, slot_id, take, payload,"
                " verb, output_path, created_at)"
                " VALUES(?,?,?,?,'image','run','/x',?)",
                (new_id(), ws["id"], node["slot_id"], node["take"], now_iso()))


async def test_a_running_slot_cannot_be_rerolled(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        with pytest.raises(ValueError, match="illegal slot transition"):
            await db.reroll_slot(slot["id"], 5)


# --- set aside / restore (a place, not a delete) ---

async def test_set_aside_flags_the_node_moves_the_slot_and_logs(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot)
        node = await db.set_aside_node(node["id"])
        assert node["set_aside"] is True
        assert (await db.get_slot(slot["id"]))["status"] == "set_aside"
        types = [e["type"] for e in await db.events_since(0)]
        assert "node.set_aside" in types


async def test_restore_clears_the_flag_and_returns_the_slot(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot)
        await db.set_aside_node(node["id"])
        node = await db.restore_node(node["id"])
        assert node["set_aside"] is False
        assert (await db.get_slot(slot["id"]))["status"] == "landed"


async def test_aside_of_an_old_take_leaves_the_slot_alone(tmp_path):
    async with opened(tmp_path) as db:  # the slot mirrors its CURRENT take only
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        first = await land(db, slot)
        await db.reroll_slot(slot["id"], 99)
        await dispatch(db, slot)
        await land(db, slot)
        await db.set_aside_node(first["id"])   # old take -> tray
        assert (await db.get_slot(slot["id"]))["status"] == "landed"
        assert (await db.get_node(first["id"]))["set_aside"] is True


async def test_tray_lists_only_live_set_aside_nodes(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws, engines=("a", "b", "c"))
        nodes = []
        for slot in turn["slots"]:
            await dispatch(db, slot)
            nodes.append(await land(db, slot))
        await db.set_aside_node(nodes[0]["id"])
        await db.set_aside_node(nodes[1]["id"])
        await db.purge_node(nodes[1]["id"])
        tray = await db.tray_nodes(ws["id"])
        assert [n["id"] for n in tray] == [nodes[0]["id"]]


# --- purge (law 7: the only delete verb, and even it only tombstones) ---

async def test_purge_tombstones_and_reports_paths_for_deletion(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot)
        await db.create_region(node["id"], "blade", "modify", "sharper",
                               "/masks/m1.png", "aa" * 32)
        purged = await db.purge_node(node["id"])
        assert purged["purged_at"] is not None
        assert set(purged["purge_paths"]) == {node["output_path"],
                                              "/masks/m1.png"}
        assert await db.get_node(node["id"]) is not None  # lineage readable
        assert node["id"] not in [n["id"] for n in
                                  await db.list_nodes(ws["id"])]


async def test_children_lineage_survives_a_purged_parent(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        parent = await db.import_node(ws["id"], output_path="/i/p.png")
        child = await db.import_node(ws["id"], output_path="/i/c.png",
                                     parents=[parent["id"]])
        await db.purge_node(parent["id"])
        assert (await db.get_node(child["id"]))["parents"] == [parent["id"]]


async def test_a_purged_node_leaves_search(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png",
                                    prompt="a rusty sword")
        assert await db.search_nodes(ws["id"], "rusty")
        await db.purge_node(node["id"])
        assert await db.search_nodes(ws["id"], "rusty") == []


async def test_purge_twice_is_one_tombstone_and_one_event(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png")
        first = await db.purge_node(node["id"])
        second = await db.purge_node(node["id"])
        assert second["purged_at"] == first["purged_at"]
        purge_events = [e for e in await db.events_since(0)
                        if e["type"] == "node.purged"]
        assert len(purge_events) == 1


async def test_nodes_cannot_be_hard_deleted(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            await db.conn.execute("DELETE FROM nodes WHERE id=?",
                                  (node["id"],))


# --- events (law 9; the resume cursor) ---

async def test_events_reject_update_and_delete(tmp_path):
    async with opened(tmp_path) as db:
        await db.append_event("probe")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await db.conn.execute("UPDATE events SET type='forged'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await db.conn.execute("DELETE FROM events")


async def test_event_seq_is_strictly_monotonic_across_writers(tmp_path):
    async with opened(tmp_path) as db:
        events = await asyncio.gather(
            *(db.append_event(f"probe.{i}") for i in range(10)))
        seqs = sorted(e["seq"] for e in events)
        assert len(set(seqs)) == 10
        assert seqs == list(range(seqs[0], seqs[0] + 10))


async def test_events_since_returns_ordered_events_after_the_cursor(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        first = await db.append_event("a", workspace_id=ws["id"])
        await db.append_event("b", workspace_id=ws["id"])
        await db.append_event("c", workspace_id="elsewhere")
        tail = await db.events_since(first["seq"])
        assert [e["type"] for e in tail] == ["b", "c"]
        scoped = await db.events_since(0, workspace_id=ws["id"])
        assert [e["type"] for e in scoped] == ["a", "b"]


async def test_slot_events_record_every_attempt_permanently(tmp_path):
    async with opened(tmp_path) as db:  # law 9: nothing retries silently
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await db.fail_slot(slot["id"], cause="dead engine")
        await db.reroll_slot(slot["id"], 7)
        await dispatch(db, slot)
        await land(db, slot)
        types = [e["type"] for e in await db.slot_events(slot["id"])]
        assert types == ["slot.dispatched", "slot.failed", "slot.rerolled",
                         "slot.dispatched", "node.landed"]


# --- costs (committed and charged are separate; totals report charged) ---

async def test_committed_and_charged_are_separate_values(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot, committed_usd=0.05)
        node = await land(db, slot, charged_usd=0.04)
        assert node["committed_usd"] == 0.05
        assert node["charged_usd"] == 0.04
        slot = await db.get_slot(slot["id"])
        assert slot["committed_usd"] == 0.05 and slot["charged_usd"] == 0.04


async def test_rollups_sum_only_charged_never_committed(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        a, b = (await mk_turn(db, ws))["slots"]
        await dispatch(db, a, committed_usd=0.50)   # committed, never charged
        await land(db, a)
        await dispatch(db, b, committed_usd=0.03)
        await land(db, b, charged_usd=0.02)
        assert await db.workspace_charged(ws["id"]) == 0.02


async def test_workspace_rollup_is_scoped_to_the_workspace(tmp_path):
    async with opened(tmp_path) as db:
        ws1, ws2 = await mk_ws(db, "A"), await mk_ws(db, "B")
        for ws, usd in ((ws1, 0.10), (ws2, 0.30)):
            slot = (await mk_turn(db, ws))["slots"][0]
            await dispatch(db, slot)
            await land(db, slot, charged_usd=usd)
        assert await db.workspace_charged(ws1["id"]) == 0.10
        assert await db.workspace_charged(ws2["id"]) == 0.30


async def test_a_charge_on_a_failed_slot_still_lands_in_the_ledger(tmp_path):
    async with opened(tmp_path) as db:  # B4: charged but nothing returned
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await db.fail_slot(slot["id"], cause="provider returned nothing",
                           charged_usd=0.07)
        assert await db.workspace_charged(ws["id"]) == 0.07


async def test_record_charge_reaches_ledger_and_badge_mirror(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await db.record_charge(slot["id"], 0.05, note="billed post-cancel")
        assert await db.workspace_charged(ws["id"]) == 0.05
        assert (await db.get_slot(slot["id"]))["charged_usd"] == 0.05


async def test_a_reroll_never_erases_an_earlier_charge(tmp_path):
    async with opened(tmp_path) as db:  # the ledger is why events hold money
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        await land(db, slot, charged_usd=0.04)
        await db.reroll_slot(slot["id"], 5)
        await dispatch(db, slot)
        await land(db, slot, charged_usd=0.03)
        assert await db.workspace_charged(ws["id"]) == 0.07


# --- FTS search (the searchable tray) ---

async def test_search_finds_a_set_aside_node_by_prompt_token(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        slot = (await mk_turn(db, ws))["slots"][0]
        await dispatch(db, slot)
        node = await land(db, slot, prompt="a rusty sword in moonlight")
        await db.set_aside_node(node["id"])
        hits = await db.search_nodes(ws["id"], "rusty", aside_only=True)
        assert [n["id"] for n in hits] == [node["id"]]


async def test_search_scopes_to_the_workspace(tmp_path):
    async with opened(tmp_path) as db:
        ws1, ws2 = await mk_ws(db, "A"), await mk_ws(db, "B")
        await db.import_node(ws1["id"], output_path="/1.png",
                             prompt="rusty sword")
        await db.import_node(ws2["id"], output_path="/2.png",
                             prompt="rusty shield")
        hits = await db.search_nodes(ws1["id"], "rusty")
        assert [n["workspace_id"] for n in hits] == [ws1["id"]]


async def test_search_filters_by_engine_column(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws)   # engines klein + z-image
        for slot in turn["slots"]:
            await dispatch(db, slot)
            await land(db, slot, prompt="a rusty sword")
        hits = await db.search_nodes(ws["id"], "rusty", engine="klein")
        assert len(hits) == 1 and hits[0]["engine"] == "klein"


async def test_fts_tracks_insert_and_prompt_update(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png",
                                    prompt="a rusty sword")
        assert await db.search_nodes(ws["id"], "rusty")
        # prompts are immutable by convention; the trigger is insurance
        await db.conn.execute("UPDATE nodes SET prompt='cobalt shield'"
                              " WHERE id=?", (node["id"],))
        assert await db.search_nodes(ws["id"], "rusty") == []
        assert [n["id"] for n in await db.search_nodes(ws["id"], "cobalt")] \
            == [node["id"]]


async def test_search_tolerates_fts_metacharacters(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        await db.import_node(ws["id"], output_path="/i.png",
                             prompt="a rusty sword")
        for query in ('a "b AND c*', "NOT (x OR y)", '""', "sword-hilt:"):
            await db.search_nodes(ws["id"], query)   # must not raise
        assert await db.search_nodes(ws["id"], "   ") == []


# --- regions (the regions.py duck-type contract) ---

async def test_region_round_trip_keeps_the_duck_typed_keys(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png")
        region = await db.create_region(node["id"], "blade", "modify",
                                        "sharper", "/m.png", "ab" * 32)
        for key in ("id", "action", "z_order", "prompt", "name"):
            assert key in region  # regions.py consumes exactly these
        assert region["action"] == "modify" and region["z_order"] == 0
        assert (await db.regions_for_node(node["id"]))[0]["id"] == region["id"]
        assert (await db.regions_by_ids([region["id"]]))[0]["name"] == "blade"


async def test_z_order_auto_increments_per_node(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        a = await db.import_node(ws["id"], output_path="/a.png")
        b = await db.import_node(ws["id"], output_path="/b.png")
        r0 = await db.create_region(a["id"], "one", "keep", None, "/1", "x")
        r1 = await db.create_region(a["id"], "two", "delete", None, "/2", "x")
        other = await db.create_region(b["id"], "own", "keep", None, "/3", "x")
        assert (r0["z_order"], r1["z_order"], other["z_order"]) == (0, 1, 0)


async def test_unknown_region_action_is_rejected_by_the_schema(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png")
        with pytest.raises(sqlite3.IntegrityError):
            await db.create_region(node["id"], "bad", "explode", None,
                                   "/m.png", "x")


# --- promotion + the hash cache ---

async def test_record_promotion_appends_the_event_with_both_paths(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        node = await db.import_node(ws["id"], output_path="/i.png")
        event = await db.record_promotion(node["id"], "/prom/a.json",
                                          "/prom/a.png")
        assert event["type"] == "node.promoted"
        assert event["node_id"] == node["id"]
        assert event["payload"] == {"manifest_path": "/prom/a.json",
                                    "promoted_path": "/prom/a.png"}


async def test_hash_cache_round_trips_and_misses_on_changed_mtime(tmp_path):
    async with opened(tmp_path) as db:
        await db.store_hash("/models/a.safetensors", 100, 1.5, "deadbeef")
        assert await db.cached_hash("/models/a.safetensors", 100, 1.5) \
            == "deadbeef"
        assert await db.cached_hash("/models/a.safetensors", 100, 2.5) is None


# --- the transition map itself ---

def test_every_lifecycle_state_is_reachable_and_spelled_right():
    states = {"queued", "running", "landed", "failed", "held", "skipped",
              "set_aside", "late"}          # PRD §2, snake_cased
    assert set(ALLOWED) == states
    assert states == set().union(*ALLOWED.values()) | {"running"}


# --- tray entries are named like cards; children counts ---

async def test_tray_entries_carry_card_labels_and_children_counts(tmp_path):
    async with opened(tmp_path) as db:
        ws = await mk_ws(db)
        turn = await mk_turn(db, ws, engines=("klein", "z-image"))
        nodes = []
        for slot in turn["slots"]:
            await dispatch(db, slot)
            nodes.append(await land(db, slot))
        child = await db.import_node(ws["id"], output_path="/i/c.png",
                                     parents=[nodes[0]["id"]])
        assert await db.tray_nodes(ws["id"]) == []
        for n in nodes:
            await db.set_aside_node(n["id"])
        tray = await db.tray_nodes(ws["id"])
        assert {(n["turn_idx"], n["slot_pos"]) for n in tray} == {(0, 0), (0, 1)}
        assert tray[0]["created_at"] >= tray[1]["created_at"]   # newest first
        ids = [n["id"] for n in nodes]
        assert await db.children_counts(ids) == {nodes[0]["id"]: 1}
        assert await db.children_counts([]) == {}
        await db.purge_node(child["id"])            # a purged child stops counting
        assert await db.children_counts(ids) == {}


async def test_several_collected_images_share_one_attempt_and_one_charge(tmp_path):
    db = Database(tmp_path / 'group.sqlite')
    await db.open()
    try:
        workspace = await db.create_workspace('Related outputs')
        parent = await db.import_node(workspace['id'], output_path='/original.png')
        turn = await db.create_turn_with_slots(workspace['id'], 'Separate layers', {}, [{'engine':'example'}])
        slot = turn['slots'][0]
        await db.dispatch_slot(slot['id'], submitted_graph={'original':'graph'}, committed_usd=.2)
        first = await db.land_node(slot['id'], payload='image', verb='run', output_path='/body.png',
                                   output_index=0, complete=False, expected_attempt=1, parents=[parent['id']])
        second = await db.land_node(slot['id'], payload='image', verb='run', output_path='/coat.png',
                                    output_index=1, complete=False, expected_attempt=1, parents=[parent['id']])
        assert first['id'] != second['id']
        assert (await db.get_slot(slot['id']))['status'] == 'running'
        assert await db.workspace_charged(workspace['id']) == 0
        replay = await db.land_node(slot['id'], payload='image', verb='run', output_path='/coat.png',
                                    output_index=1, complete=False, expected_attempt=1)
        assert replay['id'] == second['id']
        assert replay['parents'] == [parent['id']]
        with pytest.raises(ValueError, match='different material'):
            await db.land_node(slot['id'], payload='image', verb='run', output_path='/changed.png',
                               output_index=1, complete=False, expected_attempt=1)
        completed = await db.land_node(slot['id'], payload='image', verb='run', output_path='/body.png',
                                       output_index=0, charged_usd=.15, expected_attempt=1)
        assert completed['id'] == first['id'] and completed['charged_usd'] == .15
        assert await db.workspace_charged(workspace['id']) == .15
        with pytest.raises(ValueError):
            await db.land_node(slot['id'], payload='image', verb='run', output_path='/coat.png',
                               output_index=1, charged_usd=.15, expected_attempt=1)
        assert await db.workspace_charged(workspace['id']) == .15
        await db.set_aside_node(first['id'])
        assert (await db.get_slot(slot['id']))['status'] == 'landed'
        await db.set_aside_node(second['id'])
        assert (await db.get_slot(slot['id']))['status'] == 'set_aside'
        await db.restore_node(first['id'])
        assert (await db.get_slot(slot['id']))['status'] == 'landed'
        events = await db.slot_events(slot['id'])
        assert sum(e['type'] == 'node.collected' for e in events) == 2
        assert sum(e['type'] == 'node.landed' for e in events) == 1
    finally:
        await db.close()


async def _legacy_v8_fixture(tmp_path):
    """A populated v8 file with the actual old node column and unique index."""
    from PIL import Image

    primary = tmp_path / 'legacy.png'
    Image.new('RGBA', (3, 2), (21, 45, 87, 113)).save(primary)
    source = tmp_path / 'legacy-v8.sqlite'
    async with opened(tmp_path, source.name) as db:
        workspace = await mk_ws(db)
        parent = await db.import_node(workspace['id'], output_path=str(primary))
        turn = await mk_turn(db, workspace)
        await dispatch(db, turn['slots'][0], committed_usd=.2)
        child = await land(db, turn['slots'][0], output_path=str(primary),
                           parents=[parent['id']], charged_usd=.15,
                           prompt='migration sentinel')
        await db.set_aside_node(child['id'])
        await dispatch(db, turn['slots'][1])
    conn = sqlite3.connect(source)
    try:
        conn.executescript(
            'DROP TABLE execution_outputs;'
            'DROP TABLE execution_groups;'
            'DROP INDEX idx_nodes_slot_take;'
            'ALTER TABLE nodes DROP COLUMN output_index;'
            'CREATE UNIQUE INDEX idx_nodes_slot_take ON nodes(slot_id,take) '
            'WHERE slot_id IS NOT NULL;'
            "UPDATE meta SET value='8' WHERE key='schema_version';")
        conn.commit()
    finally:
        conn.close()
    return source, primary, turn['slots'][1]['id']


def _legacy_record_snapshot(path):
    """Project only old fields, so an additive migration cannot hide rewrites."""
    conn = sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)
    try:
        snapshot = {}
        for table in ('workspaces', 'turns', 'slots', 'nodes', 'node_parents',
                      'regions', 'events'):
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')
                       if r[1] != 'output_index']
            names = ','.join(f'"{column}"' for column in columns)
            snapshot[table] = conn.execute(
                f'SELECT {names} FROM {table} ORDER BY rowid').fetchall()
        return snapshot
    finally:
        conn.close()


async def test_v8_copy_migrates_without_rewriting_records_or_primary_bytes(tmp_path):
    import shutil

    source, primary, running_sid = await _legacy_v8_fixture(tmp_path)
    source_bytes = source.read_bytes()
    image_bytes = primary.read_bytes()
    before = _legacy_record_snapshot(source)
    copy = tmp_path / 'migrated-v9.sqlite'
    shutil.copy2(source, copy)

    # Opening twice proves both the real v8 rung and its restart idempotence.
    for _ in range(2):
        async with opened(tmp_path, copy.name) as db:
            stamp = await db._one("SELECT value FROM meta WHERE key='schema_version'")
            assert stamp['value'] == SCHEMA_VERSION
            assert [r['name'] for r in await db._all(
                'PRAGMA index_info(idx_nodes_slot_take)')] == [
                    'slot_id', 'take', 'output_index']
            assert {r['output_index'] for r in await db._all(
                'SELECT output_index FROM nodes')} == {0}
            assert await db._all('PRAGMA foreign_key_check') == []
            assert (await db._one('PRAGMA integrity_check'))['integrity_check'] == 'ok'
            matches = await db._all(
                "SELECT rowid FROM node_fts WHERE node_fts MATCH 'sentinel'")
            assert len(matches) == 1
        assert _legacy_record_snapshot(copy) == before
        assert source.read_bytes() == source_bytes
        assert primary.read_bytes() == image_bytes

    # The migrated index permits siblings, while keeping identity and geometry.
    async with opened(tmp_path, copy.name) as db:
        first = await db.land_node(running_sid, payload='image', verb='run',
                                   output_path='/first.png', output_index=0,
                                   complete=False, expected_attempt=1)
        second = await db.land_node(running_sid, payload='image', verb='run',
                                    output_path='/second.png', output_index=1,
                                    complete=False, expected_attempt=1)
        assert first['id'] != second['id']
        assert (await db.get_slot(running_sid))['status'] == 'running'
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE constraint'):
            await db.conn.execute('UPDATE nodes SET output_index=0 WHERE id=?',
                                  (second['id'],))
        with pytest.raises(sqlite3.IntegrityError, match='frozen'):
            await db.conn.execute('UPDATE slots SET pos=99 WHERE id=?',
                                  (running_sid,))
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            await db.conn.execute('UPDATE events SET type=type')
    assert source.read_bytes() == source_bytes
    assert primary.read_bytes() == image_bytes


async def test_v8_index_failure_rolls_back_column_and_stamp_then_can_resume(tmp_path,
                                                                        monkeypatch):
    import shutil

    import aiosqlite

    source, primary, _ = await _legacy_v8_fixture(tmp_path)
    source_bytes = source.read_bytes()
    image_bytes = primary.read_bytes()
    before = _legacy_record_snapshot(source)
    copy = tmp_path / 'interrupted-v9.sqlite'
    shutil.copy2(source, copy)
    original_execute = aiosqlite.Connection.execute

    async def fail_index(connection, sql, *args, **kwargs):
        if sql.startswith('CREATE UNIQUE INDEX idx_nodes_slot_take'):
            raise sqlite3.OperationalError('injected replacement index failure')
        return await original_execute(connection, sql, *args, **kwargs)

    db = Database(copy)
    with monkeypatch.context() as patch:
        patch.setattr(aiosqlite.Connection, 'execute', fail_index)
        try:
            with pytest.raises(sqlite3.OperationalError, match='replacement index'):
                await db.open()
            stamp = await db._one("SELECT value FROM meta WHERE key='schema_version'")
            assert stamp['value'] == '8'
            assert 'output_index' not in {r['name'] for r in await db._all(
                'PRAGMA table_info(nodes)')}
            assert [r['name'] for r in await db._all(
                'PRAGMA index_info(idx_nodes_slot_take)')] == ['slot_id', 'take']
            assert not db.conn.in_transaction
        finally:
            await db.close()
    assert _legacy_record_snapshot(copy) == before
    async with opened(tmp_path, copy.name) as db:
        stamp = await db._one("SELECT value FROM meta WHERE key='schema_version'")
        assert stamp['value'] == SCHEMA_VERSION
    assert _legacy_record_snapshot(copy) == before
    assert source.read_bytes() == source_bytes
    assert primary.read_bytes() == image_bytes


async def test_v11_copy_adds_sequence_storage_without_changing_retained_work(tmp_path):
    import shutil

    from app.crexil_store import CreativeStore
    from app.execution_controls import record

    source = tmp_path / 'before-sequences.sqlite'
    image = tmp_path / 'retained-source.png'
    image.write_bytes(b'retained source bytes')
    async with opened(tmp_path, source.name) as db:
        workspace = await mk_ws(db)
        await db.import_node(workspace['id'], output_path=str(image))
        await CreativeStore(db).new_direction(workspace['id'], 'Waiting preparation',
            {'action': 'generate', 'prompt': 'Retain these words.'}, {'kind': 'human', 'name': 'Artist'})
        await record(db, 'project', workspace['id'], {'revision': 0, 'action': 'pause',
            'reason': 'Keep work held.', 'actor': {'kind': 'human', 'name': 'Artist'}})
        await db.conn.executescript("DROP TABLE sequence_runs; DROP TABLE sequence_steps; DROP TABLE sequences;"
            "UPDATE meta SET value='11' WHERE key='schema_version';")
    source_bytes = source.read_bytes()
    with sqlite3.connect(source) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before = {table: sorted(db.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) for table in tables if table != 'meta'}
    target = tmp_path / 'with-sequences.sqlite'
    shutil.copy2(source, target)
    for _ in range(2):
        async with opened(tmp_path, target.name) as db:
            assert (await db._one("SELECT value FROM meta WHERE key='schema_version'"))['value'] == '13'
            for table, rows in before.items():
                assert sorted([tuple(row) for row in await db._all(f'SELECT * FROM "{table}"')], key=repr) == rows
            for table in ('sequences', 'sequence_steps', 'sequence_runs'):
                assert await db._all(f'SELECT * FROM {table}') == []
            assert await db._all('PRAGMA foreign_key_check') == []
            assert (await db._one('PRAGMA integrity_check'))['integrity_check'] == 'ok'
        assert source.read_bytes() == source_bytes
        assert image.read_bytes() == b'retained source bytes'
