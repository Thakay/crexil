"""Bench runner: the fixed test-input set through the cockpit API.

Runs every applicable brief from bench/briefs.json against the given
presets with a FIXED seed, through the same path the artist uses (one
workspace, one local-only turn per brief × preset, never raw ComfyUI), and
assembles a static contact sheet under /mnt/d/BENCH/<date>-<tag>/ that
opens on the Windows side by double-click: rows = briefs, columns = presets,
cell = thumbnail + elapsed + params, click-through to full size.

The sheet assembler is decoupled: results.json is the record, and
`--assemble-only --dir <sheet dir>` re-renders index.html from it, so
other runners (an edit-backend bench, external fan-out rows) can drop
cells into the same format and reuse the assembler.

Usage (from cockpit/server, its venv has PIL):
  uv run python ../../scripts/bench.py --presets raw-sdxl,raw-flux2,prop-sheet --tag baseline
  uv run python ../../scripts/bench.py --role region_source --presets raw-sdxl --tag region-sources
  uv run python ../../scripts/bench.py --assemble-only --dir /mnt/d/BENCH/2026-07-26-baseline

Notes: turns run sequentially so per-cell elapsed is generation time, not
queue wait (one-GPU truth); a failed cell records its error and the run
continues; parked briefs (ui-text) are skipped unless --include-parked.
BENCH_ROOT overrides the sheet root (a dry run against a harness server,
COCKPIT_HARNESS_EXECUTORS=1, lands under .staging instead of the drive).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH_ROOT = Path(os.environ.get("BENCH_ROOT", "/mnt/d/BENCH"))
THUMB_H = 260
TURN_TIMEOUT_S = 600


def api(base: str, path: str, payload=None, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
    return body if raw else json.loads(body or b"{}")


RESTING = {"landed", "failed", "skipped", "set_aside", "late", "held"}


def wait_turn(base: str, wid: str, tid: str) -> dict:
    """The turn rides the workspace detail; settled = every slot resting."""
    t0 = time.time()
    while time.time() - t0 < TURN_TIMEOUT_S:
        ws = api(base, f"/workspaces/{wid}")
        turn = next(t for t in ws["turns"] if t["id"] == tid)
        if all(s["status"] in RESTING for s in turn["slots"]):
            return turn
        time.sleep(2)
    raise RuntimeError(f"turn {tid} timed out after {TURN_TIMEOUT_S}s")


def nodes_by_slot(base: str, wid: str) -> dict[str, dict]:
    """Latest take per slot (bench never rerolls, so one each)."""
    out: dict[str, dict] = {}
    for n in api(base, f"/workspaces/{wid}/nodes"):
        sid = n.get("slot_id")
        if sid and (sid not in out or (n.get("take") or 0) > (out[sid].get("take") or 0)):
            out[sid] = n
    return out


def brief_inputs(brief: dict, preset: dict) -> dict:
    """The lane's handles for this brief — only what the preset declares as
    supported for txt2img; undeclared/unsupported handles are never sent
    (nothing is silently ignored server-side). The prompt and the seed
    ride the turn, not the lane."""
    specs = {s["key"]: s for s in preset["inputs"]
             if s.get("supported", True) is not False
             and (not s.get("capabilities") or "txt2img" in s["capabilities"])}
    inputs: dict = {}
    if "negative_prompt" in specs and brief.get("negative"):
        inputs["negative_prompt"] = brief["negative"]
    for k, v in (brief.get("params_hint") or {}).items():
        if k in specs:
            inputs[k] = v
    return inputs


def run(args) -> Path:
    base = args.url.rstrip("/") + "/api"
    try:
        health = api(base, "/health")
    except urllib.error.HTTPError as e:   # 503 is engine truth, not a refusal
        health = json.loads(e.read() or b"{}")
    presets = {p["id"]: p for p in api(base, "/presets")}
    missing = [p for p in args.presets if p not in presets]
    if missing:
        sys.exit(f"unknown preset(s): {missing}; server has {list(presets)}")

    data = json.loads((REPO / "bench" / "briefs.json").read_text())
    briefs = [b for b in data["briefs"] if b.get("role", "bench") == args.role]
    skipped = [b["id"] for b in briefs
               if b.get("parked") and not args.include_parked]
    if skipped:
        print(f"parked briefs skipped (--include-parked to run): {skipped}")
    briefs = [b for b in briefs
              if not (b.get("parked") and not args.include_parked)]
    if args.filter:
        briefs = [b for b in briefs
                  if any(f in b["id"] or f == b["sub_axis"]
                         for f in args.filter)]
    if not briefs:
        sys.exit("no briefs matched")

    out = BENCH_ROOT / f"{date.today().isoformat()}-{args.tag}"
    (out / "img").mkdir(parents=True, exist_ok=True)
    (out / "thumbs").mkdir(exist_ok=True)

    results = {
        "meta": {
            "date": date.today().isoformat(), "tag": args.tag,
            "seed": args.seed, "candidates": args.candidates,
            "role": args.role, "cockpit": args.url,
            "comfyui": health.get("comfyui"), "workspace": None,
            "presets": {pid: {"model_family": presets[pid].get("model_family"),
                              "defaults_note": presets[pid].get("defaults_note")}
                        for pid in args.presets},
        },
        "cells": [],
    }

    from PIL import Image  # cockpit/server venv

    ws = api(base, "/workspaces",
             {"name": f"bench {results['meta']['date']} {args.tag}"})
    wid = results["meta"]["workspace"] = ws["id"]
    print(f"workspace {wid} on {base}")
    for pid in args.presets:
        print(f"== {pid}")
        for brief in briefs:
            t0 = time.time()
            cell_base = {"brief_id": brief["id"], "sub_axis": brief["sub_axis"],
                         "preset_id": pid}
            inputs = brief_inputs(brief, presets[pid])
            try:
                run_result = api(base, f"/workspaces/{wid}/turns", {
                    "prompt": brief["prompt"],
                    "seed": {"mode": "fixed", "value": args.seed},
                    "local": [{"preset_id": pid, "takes": args.candidates,
                               "inputs": inputs}],
                    "local_only": True})
                turn = wait_turn(base, wid, run_result["turn"]["id"])
                nodes = nodes_by_slot(base, wid)
            except (urllib.error.HTTPError, RuntimeError, OSError) as e:
                detail = (e.read().decode()[:300]
                          if isinstance(e, urllib.error.HTTPError) else str(e))
                print(f"   {brief['id']:24s} TURN FAILED: {detail}")
                results["cells"].append({**cell_base, "cand_idx": 0,
                                         "status": "error", "error": detail})
                continue
            for s in turn["slots"]:
                node = nodes.get(s["id"]) if s["status"] == "landed" else None
                cell = {**cell_base, "cand_idx": s["pos"],
                        "status": "done" if node else "error",
                        "seed": s.get("seed"),
                        "elapsed_ms": node.get("duration_ms") if node else None,
                        "turn_id": turn["id"],
                        "params": {k: v for k, v in inputs.items()
                                   if k != "negative_prompt"}}
                if node:
                    name = f"{pid}--{brief['id']}--c{s['pos']}.png"
                    png = api(base, f"/nodes/{node['id']}/image", raw=True)
                    (out / "img" / name).write_bytes(png)
                    with Image.open(out / "img" / name) as im:
                        cell["resolution"] = list(im.size)
                        im.thumbnail((im.width, THUMB_H))
                        im.save(out / "thumbs" / name)
                    cell["img"] = f"img/{name}"
                    cell["thumb"] = f"thumbs/{name}"
                else:
                    cell["error"] = " · ".join(
                        x for x in (s["status"], s.get("cause"), s.get("detail"))
                        if x)[:300]
                results["cells"].append(cell)
            secs = (time.time() - t0)
            print(f"   {brief['id']:24s} {turn['slots'][0]['status']:6s}"
                  f" {secs:5.1f}s wall")

    (out / "results.json").write_text(json.dumps(results, indent=1))
    assemble(out)
    return out


def assemble(sheet_dir: Path) -> None:
    """results.json -> self-contained index.html (relative paths only)."""
    r = json.loads((sheet_dir / "results.json").read_text())
    meta, cells = r["meta"], r["cells"]
    preset_ids = list(meta["presets"])
    by_key = {}
    brief_order, seen = [], set()
    for c in cells:
        by_key.setdefault((c["brief_id"], c["preset_id"]), []).append(c)
        if c["brief_id"] not in seen:
            seen.add(c["brief_id"])
            brief_order.append((c["brief_id"], c["sub_axis"]))

    briefs_file = json.loads((REPO / "bench" / "briefs.json").read_text())
    prompts = {b["id"]: b["prompt"] for b in briefs_file["briefs"]}

    def cell_html(items):
        if not items:
            return "<td class='miss'>—</td>"
        parts = []
        for c in sorted(items, key=lambda x: x.get("cand_idx", 0)):
            if c["status"] == "done":
                el = (f"{c['elapsed_ms'] / 1000:.1f}s"
                      if c.get("elapsed_ms") is not None else "?")
                params = " ".join(f"{k}={v}" for k, v in
                                  (c.get("params") or {}).items())
                res = "×".join(map(str, c.get("resolution", [])))
                parts.append(
                    f"<a href='{c['img']}' target='_blank'>"
                    f"<img src='{c['thumb']}' loading='lazy'></a>"
                    f"<div class='m'>{el} · seed {c.get('seed')} · {res}"
                    f"{' · ' + escape(params) if params else ''}</div>")
            else:
                parts.append(f"<div class='err'>ERROR<br>"
                             f"{escape(c.get('error') or 'unknown')}</div>")
        return f"<td>{''.join(parts)}</td>"

    head = "".join(
        f"<th>{escape(p)}<div class='m'>"
        f"{escape(meta['presets'][p].get('model_family') or '')}</div></th>"
        for p in preset_ids)
    rows, last_axis = [], None
    for bid, axis in brief_order:
        if axis != last_axis:
            rows.append(f"<tr class='axis'><td colspan='{len(preset_ids) + 1}'>"
                        f"{escape(axis)}</td></tr>")
            last_axis = axis
        tds = "".join(cell_html(by_key.get((bid, p))) for p in preset_ids)
        rows.append(
            f"<tr><td class='brief'><b>{escape(bid)}</b>"
            f"<div class='m'>{escape(prompts.get(bid, ''))}</div></td>{tds}</tr>")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>bench {escape(meta['date'])}-{escape(meta['tag'])}</title><style>
body{{background:#101216;color:#d7dae0;font:14px system-ui;margin:1.2rem}}
h1{{font-size:1.1rem}} .m{{color:#8a93a2;font-size:.75rem;font-weight:400;
font-family:ui-monospace,monospace;margin-top:.2rem;max-width:26rem}}
table{{border-collapse:collapse}} td,th{{border:1px solid #262b34;
padding:.5rem;vertical-align:top;text-align:left}}
tr.axis td{{background:#1d2129;color:#e8a33d;font-weight:600;
font-size:.8rem;letter-spacing:.06em;text-transform:uppercase}}
td.brief{{max-width:27rem;min-width:16rem}} img{{height:{THUMB_H}px;
display:block;border-radius:4px}} a+.m{{margin-bottom:.5rem}}
.err{{color:#ff7b72;font-family:ui-monospace,monospace;font-size:.75rem;
max-width:16rem;white-space:pre-wrap}} .miss{{color:#4a5261}}
</style></head><body>
<h1>bench sheet — {escape(meta['date'])} · {escape(meta['tag'])}</h1>
<div class="m">fixed seed {meta['seed']} · {meta['candidates']} candidate(s)/cell ·
cockpit {escape(meta['cockpit'])} · comfyui {escape(str((meta.get('comfyui') or {}).get('version')))} ·
workspace {escape(json.dumps(meta.get('workspace') or meta.get('sessions')))}</div><br>
<table><tr><th>brief</th>{head}</tr>{''.join(rows)}</table></body></html>"""
    (sheet_dir / "index.html").write_text(html)
    print(f"sheet: {sheet_dir / 'index.html'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8001",
                    help="cockpit base URL (dev :8001, docker :8000)")
    ap.add_argument("--presets", default="raw-sdxl,raw-flux2,prop-sheet",
                    type=lambda s: s.split(","))
    ap.add_argument("--filter", type=lambda s: s.split(","), default=None,
                    help="brief id substrings or sub_axis names")
    ap.add_argument("--role", default="bench",
                    choices=["bench", "region_source"])
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--seed", type=int, default=424242)
    ap.add_argument("--candidates", type=int, default=1,
                    help="takes per cell (the lane's `takes`)")
    ap.add_argument("--include-parked", action="store_true")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--dir", type=Path, help="sheet dir for --assemble-only")
    args = ap.parse_args()
    if args.assemble_only:
        assemble(args.dir or sys.exit("--assemble-only needs --dir"))
    else:
        run(args)
