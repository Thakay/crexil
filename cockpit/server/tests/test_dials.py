"""Dial normalization truth: configs/dials.json is the authored six-dial
table (under law 17). Pure config cross-check; the served table is
test_api_v4.py's. Pinned invariants: the roster in order; every local
image engine + the external lane answered in every dial (badge, never filter —
law 12); position 50 sends the canonical preset's default exactly (untouched
dials = the verified recipe); anchors monotone and inside preset bounds; the
word "sampler" nowhere in the file (it never appears above the Advanced
fold, and this file is above the fold by definition).
"""

import json

from app import config

DIALS_PATH = config.REPO_ROOT / "configs" / "dials.json"
ENGINES_PATH = config.REPO_ROOT / "configs" / "engines.json"

DIAL_ROSTER = ["anchor-hold", "prompt-grip", "cook-time", "frame", "seed", "sweep-one-dial"]
SLIDERS = {"anchor-hold", "prompt-grip", "cook-time"}
# dial position rises → raw falls (anchor-hold trades denoise down for hold)
INVERTED = {"anchor-hold"}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dials():
    return {d["id"]: d for d in _load(DIALS_PATH)["dials"]}


def _canonical_presets():
    doc = _load(DIALS_PATH)["$doc"]["canonical_presets"]
    return {eng: _load(config.PRESETS_DIR / f"{pid}.json") for eng, pid in doc.items()}


def _preset_input(preset, key):
    for spec in preset["inputs"]:
        if spec["key"] == key:
            return spec
    raise AssertionError(f"{preset['id']} has no input {key!r}")


def test_roster_is_the_six_dials_in_order():
    assert [d["id"] for d in _load(DIALS_PATH)["dials"]] == DIAL_ROSTER


def test_every_local_image_engine_and_external_answered_everywhere():
    engines = _load(ENGINES_PATH)["engines"]
    local_image = {e["id"] for e in engines
                   if e["lane"] == "local" and e["payload"] == "image"}
    assert local_image, "engines.json lost its local image engines?"
    for dial in _load(DIALS_PATH)["dials"]:
        rows = dial["engines"]
        if dial.get("kind") == "meta":
            assert rows == {}, "meta dials resolve through their target's rows"
            continue
        assert set(rows) == local_image | {"external"}, (
            f"{dial['id']}: every engine gets an answer — a mapping or a "
            f"badge-worthy reason, never silence (law 12)")


def test_canonical_presets_bind_real_engine_families():
    presets = _canonical_presets()
    for engine_id, preset in presets.items():
        assert preset["model_family"] == engine_id


def test_non_transmitting_rows_carry_plain_words():
    for dial in _dials().values():
        for engine_id, row in dial["engines"].items():
            if not row["transmits"]:
                assert len(row.get("note", "")) > 20, (
                    f"{dial['id']}/{engine_id}: the badge needs its reason in words")


def test_sliders_anchor_the_recipe_at_50_inside_preset_bounds():
    presets = _canonical_presets()
    for dial_id in SLIDERS:
        dial = _dials()[dial_id]
        for engine_id, row in dial["engines"].items():
            if not row["transmits"]:
                continue
            spec = _preset_input(presets[engine_id], row["input"])
            positions = [p for p, _ in row["anchors"]]
            raws = [r for _, r in row["anchors"]]
            assert positions == sorted(set(positions)), f"{dial_id}/{engine_id}"
            assert positions[0] == 0 and positions[-1] == 100
            assert 50 in positions
            at_50 = dict(row["anchors"])[50]
            assert at_50 == spec["default"], (
                f"{dial_id}/{engine_id}: position 50 must send the verified "
                f"recipe ({spec['default']}), got {at_50}")
            for raw in raws:
                assert spec["min"] <= raw <= spec["max"], (
                    f"{dial_id}/{engine_id}: anchor {raw} outside preset bounds")
            ordered = sorted(raws, reverse=dial_id in INVERTED)
            assert raws == ordered, (
                f"{dial_id}/{engine_id}: anchors must move one way only")


def test_frame_and_seed_bind_existing_preset_inputs():
    presets = _canonical_presets()
    frame, seed = _dials()["frame"], _dials()["seed"]
    for engine_id, row in frame["engines"].items():
        if row["transmits"]:
            for key in row["inputs"]:
                assert _preset_input(presets[engine_id], key)["type"] == "int"
    for engine_id, row in seed["engines"].items():
        if row["transmits"]:
            assert _preset_input(presets[engine_id], row["input"])["type"] == "seed"


def test_sweep_targets_are_sliders_and_the_copy_matches():
    sweep = _dials()["sweep-one-dial"]
    assert set(sweep["targets"]) <= SLIDERS
    assert sweep["sweep_slots"] == 4
    assert "four" in sweep["effect"], "the effect copy and sweep_slots tell one story"


def test_the_word_sampler_appears_nowhere():
    raw = DIALS_PATH.read_text(encoding="utf-8")
    assert "sampler" not in raw.lower(), "never above the fold — and this file is the fold's ceiling"


def test_effect_lines_present_everywhere():
    for dial in _dials().values():
        assert len(dial["effect"]) > 20, f"{dial['id']}: the effect line is the label's whole point"
