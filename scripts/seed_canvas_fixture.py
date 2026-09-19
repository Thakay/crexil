#!/usr/bin/env python3
"""Seed a v4 cockpit DB with LANDED nodes for the canvas e2e.

E2E cannot land real turns — the harness server boots real executors against
closed ports — so this writes straight through `app.db.Database`. WAL +
busy_timeout=5000 make the cross-process write safe while the harness server
holds the file; seed BEFORE the page starts interacting (specs do).

Run from cockpit/server so uv picks the right venv:

    cd cockpit/server && uv run python ../../scripts/seed_canvas_fixture.py \
        --db ../../.staging/e2e-harness/cockpit-v4.sqlite \
        --name canvas-<unique> --preset small

Presets
  verbs    one landed image + one landed mesh, side by side — the verb row's
           payload filter and NOT FOR THIS NODE group. 2 nodes.
  small    turn 0 at anchor (160,120): slot a landed WITH x/y, slot b landed
           x/y-NULL (exercises the client fallback layout), slot c landed
           then set aside; turn 1: slot d landed with parent a → one IMAGE
           edge. 4 nodes total.
  perf200  200 landed nodes (13 turns × ≤16 slots) on a 220×180 world grid,
           workspace viewport pinned to zoom 1 so fitView never runs — the
           onlyRenderVisibleElements culling bar is measurable.
  provenance  the record fixture: `small` with REAL turn dials (the seed
           policy, each engine's applied raws, the refine's lineage — the
           shape api_v4.run_turn writes), a second take landed in cell a
           (reroll, take 2) and an imported node with no prompt at all —
           the honest-blank case. 6 nodes; node_ids = [a, b, c, d, a2, imp].

Prints one JSON line: {"workspace_id": ..., "node_ids": [...]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cockpit" / "server"))

from PIL import Image  # noqa: E402  (path shim first)

from app.db import Database  # noqa: E402


def write_png(path: Path, color: tuple[int, int, int]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path, "PNG")
    return str(path)


async def land(db: Database, slot: dict, png: str, payload: str = "image",
               **kw) -> dict:
    # landed is only reachable from running (ALLOWED map) — dispatch first
    await db.dispatch_slot(slot["id"])
    # a real landing carries the turn's prompt (run_turn -> DispatchRequest);
    # land_node does NOT inherit that one, so seeded nodes must pass it or
    # they'd be prompt-less in a way no live node ever is
    turn = await db.get_turn(slot["turn_id"])
    kw.setdefault("prompt", turn["prompt"] if turn else None)
    return await db.land_node(
        slot["id"], payload=payload, verb="run", output_path=png, **kw)


async def seed_small(db: Database, wid: str, art: Path) -> list[str]:
    turn = await db.create_turn_with_slots(
        wid, "a rusty sword", {},
        [{"engine": "z-image-turbo", "seed": 41},
         {"engine": "flux2-klein-4b", "seed": 42},
         {"engine": "z-image-turbo", "seed": 43}],
        anchor=(160.0, 120.0))
    s = turn["slots"]
    a = await land(db, s[0], write_png(art / "a.png", (32, 64, 96)),
                   seed=41, x=160.0, y=120.0)
    b = await land(db, s[1], write_png(art / "b.png", (96, 64, 32)),
                   seed=42)                     # x/y NULL → client fallback
    c = await land(db, s[2], write_png(art / "c.png", (64, 96, 32)),
                   seed=43)
    await db.set_aside_node(c["id"])
    turn2 = await db.create_turn_with_slots(
        wid, "refine the blade", {},
        [{"engine": "z-image-turbo", "seed": 44}],
        anchor=(540.0, 120.0))
    d = await land(db, turn2["slots"][0],
                   write_png(art / "d.png", (48, 48, 128)),
                   seed=44, parents=[a["id"]], x=540.0, y=120.0)
    return [a["id"], b["id"], c["id"], d["id"]]


async def seed_provenance(db: Database, wid: str, art: Path) -> list[str]:
    """The record's every case on one canvas. Turn dials are
    the run route's own shape — `{"seed": policy, <lineage>, "engines":
    {preset: applied}}` — so THE RECIPE row prints what a live turn would."""
    zimage = {"steps": 9, "cfg": 1.0, "sampler_name": "euler",
              "scheduler": "simple", "width": 1024, "height": 1024}
    flux = {"steps": 4, "guidance": 1.0, "sampler_name": "euler",
            "width": 1024, "height": 1024}
    turn = await db.create_turn_with_slots(
        wid, "a rusty longsword resting on a stone table, worn leather grip,"
             " overcast light from a high window, shallow depth of field",
        {"seed": {"mode": "fixed", "value": 41},
         "engines": {"z-image-turbo": zimage, "flux2-klein-4b": flux}},
        [{"engine": "z-image-turbo", "seed": 41, "dials": zimage},
         {"engine": "flux2-klein-4b", "seed": 42, "dials": flux},
         {"engine": "z-image-turbo", "seed": 43, "dials": zimage}],
        anchor=(160.0, 120.0))
    s = turn["slots"]
    a = await land(db, s[0], write_png(art / "a.png", (32, 64, 96)),
                   seed=41, duration_ms=10_200, x=160.0, y=120.0)
    b = await land(db, s[1], write_png(art / "b.png", (96, 64, 32)),
                   seed=42, duration_ms=6_800, x=400.0, y=120.0)
    c = await land(db, s[2], write_png(art / "c.png", (64, 96, 32)),
                   seed=43, duration_ms=9_900, x=640.0, y=120.0)
    await db.set_aside_node(c["id"])
    refine = {**zimage, "denoise": 0.55, "capability": "img2img",
              "parents": [a["id"]], "source_image_sha256": "0" * 64}
    turn2 = await db.create_turn_with_slots(
        wid, "the same sword, the blade chipped and the grip re-wrapped",
        {"seed": {"mode": "random"}, "capability": "img2img",
         "parents": [a["id"]], "source_image_sha256": "0" * 64,
         "engines": {"z-image-turbo": refine}},
        [{"engine": "z-image-turbo", "seed": 44, "dials": refine}],
        anchor=(160.0, 420.0))
    d = await db.dispatch_slot(turn2["slots"][0]["id"], frame="1024x1024")
    d = await db.land_node(
        d["id"], payload="image", verb="refine",
        output_path=write_png(art / "d.png", (48, 48, 128)),
        prompt=turn2["prompt"], seed=44, duration_ms=8_400,
        parents=[a["id"]], x=160.0, y=420.0)
    # a second take in cell a: the reroll, take 2 — the old take stays
    await db.reroll_slot(s[0]["id"], 45)
    a2 = await land(db, await db.get_slot(s[0]["id"]),
                    write_png(art / "a2.png", (32, 96, 96)), seed=45,
                    duration_ms=10_400, x=160.0, y=270.0)
    await db.conn.execute("UPDATE nodes SET verb='reroll' WHERE id=?",
                          (a2["id"],))
    await db.conn.commit()
    imp = await db.import_node(
        wid, output_path=write_png(art / "imp.png", (80, 80, 80)),
        x=640.0, y=420.0)
    return [a["id"], b["id"], c["id"], d["id"], a2["id"], imp["id"]]


async def seed_verbs(db: Database, wid: str, art: Path) -> list[str]:
    """One image + one mesh, so the verb row's payload filter and its
    NOT FOR THIS NODE group are provable in a browser, not just in jsdom."""
    turn = await db.create_turn_with_slots(
        wid, "a rusty sword", {},
        [{"engine": "z-image-turbo", "seed": 51},
         {"engine": "trellis2", "seed": 52}],
        anchor=(160.0, 120.0))
    s = turn["slots"]
    img = await land(db, s[0], write_png(art / "img.png", (32, 64, 96)),
                     seed=51, x=160.0, y=120.0)
    mesh = await land(db, s[1], write_png(art / "mesh.png", (64, 96, 32)),
                      payload="3d", seed=52, x=440.0, y=120.0)
    return [img["id"], mesh["id"]]


async def seed_perf200(db: Database, wid: str, art: Path) -> list[str]:
    png = write_png(art / "cell.png", (40, 44, 52))   # one file, 200 rows
    ids: list[str] = []
    remaining, ti = 200, 0
    while remaining:
        n = min(16, remaining)
        turn = await db.create_turn_with_slots(
            wid, f"perf turn {ti}", {},
            [{"engine": "z-image-turbo", "seed": ti * 16 + i}
             for i in range(n)])
        for i, slot in enumerate(turn["slots"]):
            node = await land(db, slot, png, seed=ti * 16 + i,
                              x=float(ti * 220), y=float(i * 180))
            ids.append(node["id"])
        remaining -= n
        ti += 1
    # pin the viewport: no fitView on open, so culling at zoom 1 is provable
    await db.set_workspace_view(wid, viewport={"x": 0, "y": 0, "zoom": 1})
    return ids


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="seed landed canvas nodes into a v4 cockpit DB")
    ap.add_argument("--db", required=True)
    ap.add_argument("--name", required=True,
                    help="workspace name — unique per run, the DB accumulates")
    ap.add_argument("--preset",
                    choices=["small", "verbs", "perf200", "provenance"],
                    default="small")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    db = Database(db_path)
    await db.open()
    try:
        ws = await db.create_workspace(args.name)
        art = db_path.parent / "seed" / ws["slug"]
        seeder = {"small": seed_small, "verbs": seed_verbs,
                  "perf200": seed_perf200,
                  "provenance": seed_provenance}[args.preset]
        ids = await seeder(db, ws["id"], art)
        print(json.dumps({"workspace_id": ws["id"], "node_ids": ids}))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
