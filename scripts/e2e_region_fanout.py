#!/usr/bin/env python3
"""Live e2e: the three-tier mask fan-out against a running dev
server (:8001) and real ComfyUI, driven end to end.

Per case (bench/region_cases.json: committed sources + masks):
  1. re-mint the source in the workspace (raw-sdxl, the case's brief,
     fixed seed 424242; the committed source.png is a layout diagram,
     so the comparison reports DIFFER),
  2. POST /api/workspaces/{wid}/region-turns fanning the case's regions
     across raw-sdxl + raw-flux2 + raw-qwen-edit (one take each),
  3. wait for every slot to rest, save each engine's frame under
     .staging/e2e-region/<case>/, and assert KEEP regions byte-equal the
     source under the mask (nd3-v1's KEEP clause) on every landed engine.

`rc-combo-modify` is rc-combo with the scale region flipped to MODIFY:
KEEP + MODIFY + DELETE in one turn. A closing pair of plain
img2img (Refine) runs on klein + qwen collects the imageRef evidence.

Usage:  cd cockpit/server && uv run python ../../scripts/e2e_region_fanout.py
        [case ids…]          default: rc-modify rc-delete rc-keep rc-combo rc-combo-modify
Env:    COCKPIT_URL (default http://127.0.0.1:8001)

The summary (per engine per case: status · seconds · KEEP verdict) prints
at the end and lands in .staging/e2e-region/summary.json — the evidence
that flips configs/engines.json mask/imageRef states to verified
(run one GPU script at a time)."""

from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageChops

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "bench"
OUT = REPO / ".staging" / "e2e-region"
BASE = __import__("os").environ.get("COCKPIT_URL", "http://127.0.0.1:8001")
ENGINES = ["raw-sdxl", "raw-flux2", "raw-qwen-edit"]
SEED = 424242
TURN_TIMEOUT_S = 45 * 60

CASES = {c["id"]: c for c in
         json.loads((BENCH / "region_cases.json").read_text())["cases"]}
BRIEFS = {b["id"]: b for b in
          json.loads((BENCH / "briefs.json").read_text())["briefs"]}

# The combined case: KEEP + MODIFY + DELETE in one turn. It is rc-combo
# with the replace flipped to a modify (its own prompt, same mask).
COMBO_MODIFY = json.loads(json.dumps(CASES["rc-combo"]))
COMBO_MODIFY["id"] = "rc-combo-modify"
COMBO_MODIFY["regions"][1]["action"] = "modify"
COMBO_MODIFY["regions"][1]["prompt"] = "the brass scale now made of polished silver"
COMBO_MODIFY["expect"] = "flask byte-kept, scale modified toward silver, " \
                         "bottle removed, rest regenerates to the dawn brief"
CASES["rc-combo-modify"] = COMBO_MODIFY


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def wait_turn(client: httpx.Client, wid: str, turn_id: str) -> dict:
    deadline = time.monotonic() + TURN_TIMEOUT_S
    resting = {"landed", "failed", "skipped", "set_aside", "late", "held"}
    while time.monotonic() < deadline:
        ws = client.get(f"/api/workspaces/{wid}").json()
        turn = next(t for t in ws["turns"] if t["id"] == turn_id)
        if all(s["status"] in resting for s in turn["slots"]):
            return turn
        time.sleep(2)
    raise SystemExit(f"turn {turn_id} never settled inside "
                     f"{TURN_TIMEOUT_S}s")


def nodes_by_slot(client: httpx.Client, wid: str) -> dict[str, dict]:
    return {n["slot_id"]: n
            for n in client.get(f"/api/workspaces/{wid}/nodes").json()
            if n.get("slot_id")}


def mint_source(client: httpx.Client, wid: str, case: dict) -> tuple[str, str]:
    brief = BRIEFS[case["source_brief"]]
    r = client.post(f"/api/workspaces/{wid}/turns", json={
        "prompt": brief["prompt"],
        "seed": {"mode": "fixed", "value": SEED},
        "local": [{"preset_id": "raw-sdxl", "takes": 1,
                   "inputs": {"negative_prompt": brief.get("negative", "")}}],
    })
    r.raise_for_status()
    turn = wait_turn(client, wid, r.json()["turn"]["id"])
    slot = turn["slots"][0]
    if slot["status"] != "landed":
        raise SystemExit(f"{case['id']}: source mint failed — "
                         f"{slot['status']}: {slot['cause']}")
    node = nodes_by_slot(client, wid)[slot["id"]]
    minted = client.get(f"/api/nodes/{node['id']}/image").content
    committed = (BENCH / case["source"]).read_bytes()
    verdict = "MATCH" if minted == committed else "DIFFER"
    if verdict == "DIFFER":
        # byte equality is stricter than pixel truth: compare pixels
        a = Image.open(io.BytesIO(minted)).convert("RGB")
        b = Image.open(io.BytesIO(committed)).convert("RGB")
        if a.size == b.size and ImageChops.difference(a, b).getbbox() is None:
            verdict = "PIXEL-MATCH"
        else:
            diff = ImageChops.difference(a, b).getbbox() if a.size == b.size \
                else "size"
            print(f"  ! minted source differs from the committed PNG "
                  f"(diff bbox {diff}); expected, the committed source is a "
                  "layout diagram. Results are judged against the MINTED source")
    print(f"  source: node {node['id']} · committed-vs-minted {verdict}")
    return node["id"], verdict


def keep_masks(case: dict) -> Image.Image | None:
    union = None
    for r in case["regions"]:
        if r["action"] != "keep":
            continue
        m = Image.open(BENCH / r["mask"]).convert("L") \
            .point(lambda v: 255 if v >= 128 else 0)
        union = m if union is None else ImageChops.lighter(union, m)
    return union


def keep_check(result_png: bytes, source_png: bytes,
               keep: Image.Image) -> str:
    out = Image.open(io.BytesIO(result_png)).convert("RGB")
    src = Image.open(io.BytesIO(source_png)).convert("RGB")
    if out.size != src.size:
        return f"FAIL (size {out.size} vs {src.size})"
    diff = ImageChops.difference(out, src).convert("L")
    masked = ImageChops.multiply(diff, keep.resize(out.size, Image.NEAREST))
    return "byte-exact" if masked.getbbox() is None \
        else f"FAIL (diff inside keep at {masked.getbbox()})"


def run_case(client: httpx.Client, wid: str, case: dict,
             summary: list) -> None:
    print(f"\n=== {case['id']} — {case['semantic']}")
    node_id, src_verdict = mint_source(client, wid, case)
    source_png = client.get(f"/api/nodes/{node_id}/image").content
    regions = [{"name": r["name"], "action": r["action"],
                "prompt": r["prompt"], "mask_b64": b64(BENCH / r["mask"])}
               for r in case["regions"]]
    r = client.post(f"/api/workspaces/{wid}/region-turns", json={
        "parents": [node_id], "prompt": case["turn_prompt"],
        "regions": regions,
        "seed": {"mode": "fixed", "value": SEED},
        "local": [{"preset_id": e, "takes": 1} for e in ENGINES],
    })
    if r.status_code != 201:
        raise SystemExit(f"{case['id']}: region run refused — {r.text}")
    body = r.json()["turn"]
    print(f"  plan: {[p['kind'] for p in body['dials']['region']['passes']]}"
          f" · turn {body['id']}")
    turn = wait_turn(client, wid, body["id"])
    nodes = nodes_by_slot(client, wid)
    keep = keep_masks(case)
    case_dir = OUT / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    Path(case_dir / "source.png").write_bytes(source_png)
    for slot in turn["slots"]:
        engine = slot["engine"]
        row = {"case": case["id"], "engine": engine,
               "status": slot["status"], "source": src_verdict,
               "secs": None, "keep": None, "cause": slot["cause"]}
        if slot["status"] == "landed":
            node = nodes[slot["id"]]
            png = client.get(f"/api/nodes/{node['id']}/image").content
            (case_dir / f"{engine}.png").write_bytes(png)
            row["secs"] = round((node["duration_ms"] or 0) / 1000, 1)
            if keep is not None:
                row["keep"] = keep_check(png, source_png, keep)
        summary.append(row)
        print(f"  {engine}: {row['status']}"
              + (f" · {row['secs']}s" if row["secs"] else "")
              + (f" · KEEP {row['keep']}" if row["keep"] else "")
              + (f" · {row['cause']}" if row["cause"] else ""))


def refine_evidence(client: httpx.Client, wid: str, summary: list) -> None:
    """One plain img2img per edit tier — engines.json imageRef's live
    observation."""
    print("\n=== refine (imageRef evidence)")
    case = CASES["rc-modify"]
    node_id, _ = mint_source(client, wid, case)
    prompts = {
        "raw-flux2": "the same rustic iron lantern on the wooden crate, but "
                     "at golden sunset light; everything else identical",
        "raw-qwen-edit": "change the lighting to warm golden sunset light; "
                         "keep everything else exactly the same",
    }
    for engine, prompt in prompts.items():
        r = client.post(f"/api/workspaces/{wid}/turns", json={
            "prompt": prompt, "parents": [node_id],
            "seed": {"mode": "fixed", "value": SEED},
            "local": [{"preset_id": engine, "takes": 1}],
        })
        if r.status_code != 201:
            raise SystemExit(f"refine on {engine} refused — {r.text}")
        turn = wait_turn(client, wid, r.json()["turn"]["id"])
        slot = turn["slots"][0]
        row = {"case": "refine", "engine": engine, "status": slot["status"],
               "secs": None, "keep": None, "cause": slot["cause"],
               "source": "—"}
        if slot["status"] == "landed":
            node = nodes_by_slot(client, wid)[slot["id"]]
            png = client.get(f"/api/nodes/{node['id']}/image").content
            d = OUT / "refine"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{engine}.png").write_bytes(png)
            row["secs"] = round((node["duration_ms"] or 0) / 1000, 1)
        summary.append(row)
        print(f"  {engine}: {row['status']}"
              + (f" · {row['secs']}s" if row["secs"] else "")
              + (f" · {row['cause']}" if row["cause"] else ""))


def main() -> int:
    wanted = sys.argv[1:] or ["rc-modify", "rc-delete", "rc-keep",
                              "rc-combo", "rc-combo-modify"]
    OUT.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        client.get("/api/queue").raise_for_status()
        ws = client.post("/api/workspaces",
                         json={"name": f"e2e-region {int(time.time())}"})
        ws.raise_for_status()
        wid = ws.json()["id"]
        print(f"workspace {wid} on {BASE}")
        for cid in wanted:
            run_case(client, wid, CASES[cid], summary)
        refine_evidence(client, wid, summary)

    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print("\n=== summary ===")
    bad = 0
    for row in summary:
        ok = row["status"] == "landed" and (row["keep"] in (None,
                                                            "byte-exact"))
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {row['case']:>10} "
              f"{row['engine']:>14} {row['status']:>7} "
              f"{(str(row['secs']) + 's') if row['secs'] else '—':>8} "
              f"{row['keep'] or '—'}")
    print(f"\noutputs: {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
