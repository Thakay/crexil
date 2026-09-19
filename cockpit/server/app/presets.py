"""Preset catalog loader: adding a sub-modality preset is a config
file + a workflow JSON, never code. Input schemas declare how UI values patch
graph nodes (by stable node title); unsupported handles are declared with
a reason so the UI disables them visibly instead of silently ignoring.

The second half is a take's plumbing — the seed policy, the inputs that apply
to a capability, and the two stamps every local graph gets (seed, save
prefix). The run, reroll, region and mesh routes all read it, so it lives
beside the graph patching it is made of rather than in any one route.
"""

from __future__ import annotations

import json
import secrets

from . import config

MAX_SEED = 2 ** 53 - 1  # JS-safe integer range; ComfyUI accepts up to 2**64
# rails graphs are known shapes: structural seed targets for non-txt2img passes
SEED_FIELDS = {"sampler": "seed", "removal_apply": "seed"}


class PresetError(Exception):
    pass


def _normalize_input(spec: dict) -> dict:
    out = dict(spec)
    out.setdefault("supported", True)
    caps = out.get("capabilities")
    if caps is not None:
        unknown = set(caps) - {"txt2img", "img2img", "region"}
        if unknown:
            raise PresetError(f"input '{spec.get('key')}': unknown "
                              f"capabilities {sorted(unknown)}")
    if out["supported"]:
        targets = out.get("target")
        if targets is None:
            raise PresetError(f"input '{spec.get('key')}' missing target")
        out["target"] = targets if isinstance(targets, list) else [targets]
    return out


def load_presets() -> dict[str, dict]:
    presets: dict[str, dict] = {}
    for path in sorted(config.PRESETS_DIR.glob("*.json")):
        p = json.loads(path.read_text())
        pid = p.get("id") or path.stem
        p["id"] = pid
        p["inputs"] = [_normalize_input(i) for i in p.get("inputs", [])]
        for cap, ref in p.get("workflows", {}).items():
            wf = config.WORKFLOWS_DIR / f"{ref}.json"
            if not wf.is_file():
                raise PresetError(
                    f"preset '{pid}' references missing workflow {wf.name} ({cap})")
        presets[pid] = p
    return presets


def load_workflow(ref: str) -> dict:
    return json.loads((config.WORKFLOWS_DIR / f"{ref}.json").read_text())


def find_nodes_by_title(graph: dict, title: str) -> list[str]:
    return [nid for nid, node in graph.items()
            if node.get("_meta", {}).get("title") == title]


def patch_input(graph: dict, target: dict, value) -> None:
    """Set one input on every node whose stable title matches."""
    nodes = find_nodes_by_title(graph, target["node_title"])
    if not nodes:
        raise PresetError(f"workflow has no node titled '{target['node_title']}'")
    for nid in nodes:
        graph[nid].setdefault("inputs", {})[target["field"]] = value


def coerce_value(spec: dict, value):
    """Clamp/convert a UI value per the input schema (what you set is what
    runs — clamping to the declared range is visible in the echoed inputs)."""
    t = spec.get("type")
    if t == "int":
        v = int(value)
    elif t == "float":
        v = float(value)
    elif t == "enum":
        if value not in spec.get("choices", []):
            raise PresetError(f"'{value}' not in choices for {spec['key']}")
        return value
    else:
        return value
    if "min" in spec:
        v = max(spec["min"], v)
    if "max" in spec:
        v = min(spec["max"], v)
    return v


# --- a take's plumbing: seed policy, applied inputs, the two graph stamps ---


def resolve_seed_policy(raw) -> dict:
    """The SEED dial's wire forms → one policy dict: `{"mode": "random"}`,
    `{"mode": "fixed", "value": n}` or `{"mode": "locked", "value": n}`; a
    bare number is a fixed seed, anything else is random."""
    if isinstance(raw, dict) and raw.get("mode") in ("random", "fixed",
                                                      "locked"):
        return raw
    if isinstance(raw, (int, float)):
        return {"mode": "fixed", "value": int(raw)}
    return {"mode": "random"}


def take_seed(policy: dict, pos: int) -> int:
    """The seed for the take at `pos` under a policy."""
    if policy["mode"] == "fixed":
        # fixed seed + N takes: seed+pos keeps the takes distinct yet the
        # turn stays exactly reproducible from the manifest
        return (int(policy.get("value", 0)) + pos) % MAX_SEED
    if policy["mode"] == "locked":
        # The SEED dial's lock (configs/dials.json lock_scope): ONE
        # value to every take, so engine and dial differences are the only
        # variables in the batch. A reroll is always a fresh roll: it
        # never passes a locked policy here.
        return int(policy["value"]) % MAX_SEED
    return secrets.randbelow(MAX_SEED)


def spec_applies(spec: dict, capability: str) -> bool:
    """A preset input applies to a take iff its declared `capabilities`
    (absent = all) include the take's capability (input scoping)."""
    caps = spec.get("capabilities")
    return capability in caps if caps else True


def applied_inputs(preset: dict, inputs: dict,
                   capability: str) -> tuple[dict, dict, list[dict]]:
    """Coerce the inputs that apply to this capability.
    Returns (applied, specs_by_key, seed_targets)."""
    specs = {s["key"]: s for s in preset["inputs"]
             if s.get("supported", True) and spec_applies(s, capability)}
    seed_policy = resolve_seed_policy(inputs.get("seed"))
    applied: dict = {"seed": seed_policy}
    seed_targets: list[dict] = []
    for key, spec in specs.items():
        if spec.get("type") == "seed":
            seed_targets = spec["target"]
            continue
        raw = inputs.get(key, spec.get("default"))
        applied[key] = coerce_value(spec, raw)
    return applied, specs, seed_targets


def patch_existing(graph: dict, target: dict, value) -> bool:
    """patch_input, but only where the target title exists (rails passes
    differ structurally: remove/composite have no sampler/prompt nodes)."""
    if not find_nodes_by_title(graph, target["node_title"]):
        return False
    patch_input(graph, target, value)
    return True


def patch_seed(graph: dict, seed: int, seed_targets: list[dict],
               capability: str) -> None:
    """A txt2img graph takes the seed at the preset's declared targets; the
    non-txt2img rails graphs are known shapes and take it structurally."""
    if capability == "txt2img":
        for target in seed_targets:
            patch_input(graph, target, seed)
        return
    for title, field in SEED_FIELDS.items():
        patch_existing(graph, {"node_title": title, "field": field}, seed)


def set_save_prefix(graph: dict, prefix: str) -> None:
    """Namespace the take's output files under ComfyUI's output dir."""
    for node in graph.values():
        if node.get("class_type") == "SaveImage":
            node["inputs"]["filename_prefix"] = prefix
