#!/usr/bin/env python3
"""Idempotent model downloader driven by scripts/models-manifest.json.

Stdlib only; shells out to `hf download` (HF repos) and `curl` (plain URLs).
License metadata lives in the manifest and is shown, never enforced: every
entry prints its recorded licence before it downloads and in --list. With no
arguments only the starter group downloads, whose weights are all Apache-2.0;
a group holding non-commercial or unconfirmed weights downloads only when it
is named (PRD section 2: never a default, never blocked when asked for).
Gated repos are skipped with instructions unless an HF token is present.

Usage:
  scripts/download_models.py                       # the starter group, flux-core
  scripts/download_models.py --group sdxl-rails    # one group (repeatable)
  scripts/download_models.py --all                 # every group incl. deferred
  scripts/download_models.py --list                # show plan + on-disk status
  scripts/download_models.py --verify              # HEAD-check every source URL
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "models-manifest.json"
# The starter only: FLUX.2 klein 4B, its text encoder and its VAE, Apache-2.0.
DEFAULT_GROUPS = ["flux-core"]
LICENCES = ("Every entry prints the licence the manifest recorded. Some groups hold "
            "non-commercial or unconfirmed weights: read docs/third-party.md first.")

GATE_HELP = (
    "  gated repo and no HF token found.\n"
    "  Unlock: (1) accept the terms on https://huggingface.co/{repo}\n"
    "          (2) run `hf auth login` (or set HF_TOKEN in the environment),\n"
    "          then re-run this script."
)


def have_hf_token() -> bool:
    if os.environ.get("HF_TOKEN"):
        return True
    for p in ("~/.cache/huggingface/token", "~/.huggingface/token"):
        if Path(p).expanduser().is_file():
            return True
    return False


def source_url(entry: dict) -> str:
    src = entry["source"]
    if src["type"] == "url":
        return src["url"]
    return f"https://huggingface.co/{src['repo']}/resolve/main/{src['file']}"


def size_ok(path: Path, approx_gb: float) -> bool:
    # 15% tolerance below the recorded approximate size guards against
    # truncated downloads without demanding byte-exact manifest data.
    return path.is_file() and path.stat().st_size >= approx_gb * 1e9 * 0.85


def head_check(url: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["curl", "-sIL", "-o", "/dev/null", "-w", "%{http_code} %{size_download};%{url_effective}",
         "--max-time", "30", url],
        capture_output=True, text=True,
    )
    out = r.stdout.strip() or "000"
    code = out.split(" ", 1)[0]
    return code in ("200", "302"), code


def download_hf(entry: dict, dest: Path, tmp_root: Path) -> bool:
    src = entry["source"]
    with tempfile.TemporaryDirectory(dir=tmp_root) as td:
        r = subprocess.run(
            ["hf", "download", src["repo"], src["file"], "--local-dir", td],
            stdout=sys.stdout, stderr=subprocess.STDOUT,
        )
        if r.returncode != 0:
            return False
        got = Path(td) / src["file"]
        if not got.is_file():
            print(f"  ERROR: expected file not found after download: {got}")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(got), str(dest))
    return True


def download_url(entry: dict, dest: Path) -> bool:
    url = entry["source"]["url"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    r = subprocess.run(
        ["curl", "-L", "-C", "-", "--retry", "3", "--fail", "-o", str(part), url],
        stdout=sys.stdout, stderr=subprocess.STDOUT,
    )
    if r.returncode != 0:
        return False
    part.rename(dest)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", action="append", dest="groups")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    m = json.loads(MANIFEST.read_text())
    root = Path(os.path.expanduser(m["models_root"]))
    group_order = list(m["groups"])
    wanted = group_order if args.all else args.groups or DEFAULT_GROUPS
    unknown = [g for g in wanted for _ in [0] if g not in group_order]
    if unknown:
        print(f"unknown group(s): {unknown}; known: {group_order}")
        return 2

    entries = [e for g in group_order if g in wanted
               for e in m["entries"] if e["group"] == g]
    token = have_hf_token()
    print(LICENCES)

    if args.list or args.verify:
        for e in entries:
            if e.get("managed_by"):
                print(f"EXTERNAL {e['group']:12} {e['name']:70} ~{e['approx_gb']:.2f}GB"
                      f"  (managed by {e['managed_by'].split(' ')[0]}; license ledger only)"
                      f"  licence: {e['license']}")
                continue
            dest = root / e["dest"]
            state = "PRESENT" if size_ok(dest, e["approx_gb"]) else "missing"
            gate = " [GATED]" if e.get("requires_hf_gate") else ""
            line = (f"{state:8} {e['group']:12} {e['dest']:70} ~{e['approx_gb']:.2f}GB{gate}"
                    f"  licence: {e['license']}")
            if args.verify and state != "PRESENT":
                ok, code = head_check(source_url(e))
                line += f"  url:{code}{'' if ok else '  <-- BROKEN'}"
            print(line)
        return 0

    failed, skipped_gated, done, fresh = [], [], 0, 0
    for e in entries:
        if e.get("managed_by"):
            print(f"SKIP (external) [{e['group']}] {e['name']} — managed by {e['managed_by'].split(' ')[0]}")
            continue
        dest = root / e["dest"]
        tag = f"[{e['group']}] {e['name']}"
        if size_ok(dest, e["approx_gb"]):
            print(f"SKIP (present)  {tag} -> {dest}")
            done += 1
            continue
        if e.get("requires_hf_gate") and not token:
            print(f"SKIP (gated)    {tag}")
            print(GATE_HELP.format(repo=e["source"]["repo"]))
            skipped_gated.append(e["name"])
            continue
        print(f"GET  ~{e['approx_gb']}GB  {tag} -> {dest}  licence: {e['license']}", flush=True)
        ok = (download_url(e, dest) if e["source"]["type"] == "url"
              else download_hf(e, dest, root))
        if ok and size_ok(dest, e["approx_gb"]):
            print(f"OK   {tag}")
            done += 1
            fresh += 1
        else:
            print(f"FAIL {tag}")
            failed.append(e["name"])

    print(f"\n=== summary: {done}/{len(entries)} present ({fresh} new), "
          f"{len(skipped_gated)} gated-skipped, {len(failed)} failed ===")
    if skipped_gated:
        print("gated (unlock then re-run):", ", ".join(skipped_gated))
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
