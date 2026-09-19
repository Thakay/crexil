"""The region plan/envelope builder (app/regionrun.py) over the REAL
presets, workflows and configs (the integration the route trusts).

    plan            build_region_plan: one crop box per pass, shared by
                    every engine; DELETE stays full-frame; mask_crop at box
                    geometry; RegionError words pass through
    capability      check_region_capable names exactly the missing keys
    envelopes       per-tier binding — SDXL mask-native (region_mask +
                    denoise on MODIFY only, raw prompt), klein/Qwen
                    instruction tiers (templates per kind, crop geometry
                    into latent_size/scheduler, masked_paste, no
                    region_mask node), lama on every tier's remove
    seed            stamp_take's field-presence rule — klein seeds
                    noise_seed and never injects `seed` into its sampler
    geometry        the FRAME dial's width/height never reach a region
                    pass; the box owns the latent dims

The executor that walks these envelopes is region_exec.py (its own suite);
the route that stages masks/uploads is test_api_v4_regions.py.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app import presets as presets_mod
from app.regionrun import (RegionRunError, build_engine_envelope,
                           build_region_plan, check_region_capable,
                           pass_prompt, stamp_take)

SIZE = (2048, 1536)
CATALOG = presets_mod.load_presets()


def rect_mask(box, size=SIZE):
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def region(rid, action, z, prompt=None, name=None):
    return {"id": rid, "action": action, "z_order": z, "prompt": prompt,
            "name": name or rid}


def specs_for(preset, capability="region"):
    return {s["key"]: s for s in preset["inputs"]
            if s.get("supported", True)
            and (capability in s["capabilities"] if s.get("capabilities")
                 else True)}


def staged(plan):
    for p in plan["passes"]:
        p["mask_upload"] = f"cockpit-mask-p{p['idx']}.png"
        p["mask_sha256"] = "0" * 64
        p["mask_path"] = f"/staged/mask-p{p['idx']}.png"
    return plan


def combo_plan():
    rows = [region("rk", "keep", 1), region("rm", "modify", 2, "blue flame"),
            region("rr", "replace", 3, "silver goblet"),
            region("rd", "delete", 4)]
    masks = {"rk": rect_mask((0, 0, 400, 400)),
             "rm": rect_mask((600, 600, 900, 860)),
             "rr": rect_mask((1200, 300, 1500, 640)),
             "rd": rect_mask((300, 1100, 520, 1300))}
    return staged(build_region_plan(rows, masks, SIZE, "a workshop at dusk"))


SOURCE = {"path": "/staged/src.png", "sha256": "f" * 64,
          "name": "cockpit-src.png"}


def envelope(preset_id, plan):
    preset = CATALOG[preset_id]
    sp = specs_for(preset)
    applied = {k: s.get("default") for k, s in sp.items()
               if s.get("type") != "seed"}
    applied["prompt"] = "a workshop at dusk"
    return build_engine_envelope(preset, applied, sp, plan,
                                 source=SOURCE, keep=None,
                                 region_ids=["rk", "rm", "rr", "rd"])


def graph_of(env, idx):
    return env["region_plan"]["passes"][idx]["graph"]


def node_by_title(graph, title):
    ids = presets_mod.find_nodes_by_title(graph, title)
    assert ids, f"no node titled {title}"
    return graph[ids[0]]


# --- the plan ---


def test_plan_boxes_every_work_pass_crops():
    plan = combo_plan()
    kinds = [p["kind"] for p in plan["passes"]]
    assert kinds == ["remove", "replace", "modify", "regen_rest"]
    # remove crops too — LaMa fills sharper on the zoomed crop (e2e-observed)
    assert plan["passes"][0]["box"] is not None
    assert plan["passes"][1]["box"] is not None
    assert plan["passes"][2]["box"] is not None
    for p in plan["passes"]:
        assert p["mask_crop"].startswith(b"\x89PNG")


def test_mask_crop_is_at_box_geometry():
    plan = combo_plan()
    p = plan["passes"][2]                            # the modify pass
    x0, y0, x1, y1 = p["box"]
    import io
    img = Image.open(io.BytesIO(p["mask_crop"]))
    assert img.size == (x1 - x0, y1 - y0)


def test_region_error_words_pass_through():
    rows = [region("rk", "keep", 1)]
    masks = {"rk": rect_mask((0, 0, 400, 400))}
    with pytest.raises(RegionRunError, match="nothing to generate"):
        build_region_plan(rows, masks, SIZE, None)


def test_capability_gate_names_the_missing_keys():
    with pytest.raises(RegionRunError, match="raw-zimage.*inpaint_modify"):
        check_region_capable(CATALOG["raw-zimage"])
    for pid in ("raw-sdxl", "raw-flux2", "raw-qwen-edit", "prop-sheet"):
        check_region_capable(CATALOG[pid])           # no raise


# --- per-tier envelopes over one shared plan ---


def test_boxes_identical_across_engines():
    plan = combo_plan()
    envs = [envelope(pid, plan)
            for pid in ("raw-sdxl", "raw-flux2", "raw-qwen-edit")]
    for idx in range(4):
        boxes = {tuple(e["region_plan"]["passes"][idx]["box"] or ())
                 for e in envs}
        assert len(boxes) == 1


def test_sdxl_is_mask_native():
    env = envelope("raw-sdxl", combo_plan())
    passes = env["region_plan"]["passes"]
    assert [p["masked_paste"] for p in passes] == [False] * 4
    modify = passes[2]
    assert modify["template_id"] == "raw"
    assert modify["prompt"] == "blue flame"
    g = modify["graph"]
    assert node_by_title(g, "region_mask")["inputs"]["image"] \
        == modify["mask"]["upload"]
    assert node_by_title(g, "sampler")["inputs"]["denoise"] == 0.5
    replace = passes[1]
    assert node_by_title(replace["graph"], "sampler")["inputs"]["denoise"] \
        == 1.0                                      # pass-pinned, not the dial


def test_every_tier_removes_on_lama():
    plan = combo_plan()
    for pid in ("raw-sdxl", "raw-flux2", "raw-qwen-edit"):
        p = envelope(pid, plan)["region_plan"]["passes"][0]
        assert p["workflow"] == "lama-remove@v1"
        assert p["prompt"] is None and p["masked_paste"] is False
        assert node_by_title(p["graph"], "removal_loader")


def test_klein_tier_templates_geometry_and_masked_paste():
    plan = combo_plan()
    env = envelope("raw-flux2", plan)
    passes = env["region_plan"]["passes"]
    modify = passes[2]
    assert modify["masked_paste"] is True
    assert modify["template_id"] == "klein-modify"
    assert "blue flame" in modify["prompt"]
    assert "the same scene" in modify["prompt"]
    g = modify["graph"]
    assert node_by_title(g, "prompt_positive")["inputs"]["text"] \
        == modify["prompt"]
    # no region_mask node in the refedit graph — the mask acts at paste-back
    assert not presets_mod.find_nodes_by_title(g, "region_mask")
    # the box owns geometry: latent + scheduler at crop dims, not the
    # preset's 1024 default
    x0, y0, x1, y1 = modify["box"]
    for title in ("latent_size", "scheduler"):
        node = node_by_title(g, title)
        assert node["inputs"]["width"] == x1 - x0
        assert node["inputs"]["height"] == y1 - y0
    regen = passes[3]
    assert regen["template_id"] == "klein-regen_rest"
    assert "a workshop at dusk" in regen["prompt"]


def test_qwen_tier_prompt_field_and_geometry():
    plan = combo_plan()
    env = envelope("raw-qwen-edit", plan)
    replace = env["region_plan"]["passes"][1]
    assert replace["template_id"] == "qwen-replace"
    assert replace["prompt"].startswith("replace the object at the center")
    assert "silver goblet" in replace["prompt"]
    g = replace["graph"]
    assert node_by_title(g, "prompt_positive")["inputs"]["prompt"] \
        == replace["prompt"]
    x0, y0, x1, y1 = replace["box"]
    node = node_by_title(g, "latent_size")
    assert node["inputs"]["width"] == x1 - x0
    assert node["inputs"]["height"] == y1 - y0


def test_full_frame_pass_snaps_frame_dims_to_16():
    # regen_rest over a near-full unclaimed rest hits the full-frame
    # fallback; the instruction tiers' latent must still be /16
    rows = [region("rk", "keep", 1, name="the subject")]
    masks = {"rk": rect_mask((900, 700, 1100, 830), (1000, 750))}
    plan = staged(build_region_plan(rows, masks, (1000, 750), "night"))
    env = envelope("raw-flux2", plan)
    regen = env["region_plan"]["passes"][0]
    assert regen["kind"] == "regen_rest" and regen["box"] is None
    node = node_by_title(regen["graph"], "latent_size")
    assert node["inputs"]["width"] == 992 and node["inputs"]["height"] == 736


# --- seed + save-prefix stamping ---


def test_stamp_take_seeds_by_field_presence():
    plan = combo_plan()
    for pid, title, field in (("raw-flux2", "noise_seed", "noise_seed"),
                              ("raw-qwen-edit", "sampler", "seed"),
                              ("raw-sdxl", "sampler", "seed")):
        env = stamp_take(envelope(pid, plan), 4242, "runtok", 3)
        work = env["region_plan"]["passes"][2]["graph"]  # a modify pass
        assert node_by_title(work, title)["inputs"][field] == 4242
        remove = env["region_plan"]["passes"][0]["graph"]
        assert node_by_title(remove, "removal_apply")["inputs"]["seed"] == 4242


def test_klein_sampler_gets_no_junk_seed_field():
    env = stamp_take(envelope("raw-flux2", combo_plan()), 7, "t", 0)
    sampler = node_by_title(env["region_plan"]["passes"][2]["graph"],
                            "sampler")
    assert "seed" not in sampler["inputs"]           # SamplerCustomAdvanced


def test_stamp_take_namespaces_save_prefixes_per_pass():
    env = stamp_take(envelope("raw-sdxl", combo_plan()), 1, "tok123", 5)
    prefixes = []
    for p in env["region_plan"]["passes"]:
        for node in p["graph"].values():
            if node.get("class_type") == "SaveImage":
                prefixes.append(node["inputs"]["filename_prefix"])
    assert prefixes == [f"cockpit/v4/tok123/s5p{i}" for i in range(4)]


def test_stamp_take_leaves_the_base_envelope_alone():
    base = envelope("raw-sdxl", combo_plan())
    before = node_by_title(graph_of(base, 2), "sampler")["inputs"]["seed"]
    stamp_take(base, 999999, "t", 0)
    assert node_by_title(graph_of(base, 2), "sampler")["inputs"]["seed"] \
        == before


# --- template table ---


def test_pass_prompt_table():
    sdxl, flux, qwen = (CATALOG[p] for p in
                        ("raw-sdxl", "raw-flux2", "raw-qwen-edit"))
    assert pass_prompt(sdxl, "modify", "x") == ("x", "raw")
    assert pass_prompt(flux, "remove", None) == (None, None)
    p, tid = pass_prompt(qwen, "regen_rest", "a cave")
    assert tid == "qwen-regen_rest" and "a cave" in p
