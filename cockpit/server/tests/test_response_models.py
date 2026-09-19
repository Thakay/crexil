"""Parity guard: `response_model` FILTERS, so a field the model forgets
is silently dropped from the wire and no test elsewhere would notice. These
tests validate the real shapes (repo preset files, the region view built in
code) and assert payload ⊆ model_dump, so a dropped key fails here instead
of degrading the UI quietly."""

import pytest

from app.api_v4_regions import _region_view
from app.presets import load_presets
from app.schemas import Preset, Region

pytestmark = pytest.mark.anyio


def assert_no_dropped_keys(payload: dict, model) -> None:
    dumped = model.model_dump()
    missing = set(payload) - set(dumped)
    assert not missing, (
        f"{type(model).__name__} would drop {sorted(missing)} from the wire")


def test_every_real_preset_validates_and_loses_nothing():
    catalog = load_presets()          # the real presets/ dir — config truth
    assert catalog, "no presets found — PRESETS_DIR misconfigured for tests?"
    for pid, preset in catalog.items():
        model = Preset.model_validate(preset)
        assert_no_dropped_keys(preset, model)
        assert model.id == pid
        for spec, spec_model in zip(preset["inputs"], model.inputs, strict=True):
            assert_no_dropped_keys(spec, spec_model)


REGION_ROW = {
    # exactly db.regions_for_node's row shape
    "id": "r1", "node_id": "n2", "name": "sword hilt",
    "action": "modify", "prompt": "gilded", "mask_path": "/staging/r1.png",
    "mask_sha256": "ab" * 32, "z_order": 0, "consumed_by_turn_id": None,
    "created_at": "2026-07-29T00:00:00+00:00",
}


def test_region_view_validates_and_mask_path_stays_private():
    view = _region_view(REGION_ROW)
    model = Region.model_validate(view)
    assert_no_dropped_keys(view, model)
    dumped = model.model_dump()
    assert "mask_path" not in dumped
    assert dumped["mask_url"] == "/api/regions/r1/mask"
