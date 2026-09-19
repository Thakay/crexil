"""The OpenAPI document is a committed artifact (`cockpit/openapi.json`)
— the TS types are generated from it, so drift between code and snapshot must
fail loudly, here, not silently in the browser."""

import re

import pytest

from app.openapi_export import SNAPSHOT_PATH, openapi_json

pytestmark = pytest.mark.anyio


def test_openapi_matches_committed_snapshot():
    assert SNAPSHOT_PATH.is_file(), (
        f"{SNAPSHOT_PATH} missing — run scripts/gen-api-types.sh and commit "
        "openapi.json + cockpit/web/src/generated/openapi.d.ts")
    assert openapi_json() == SNAPSHOT_PATH.read_text(), (
        "OpenAPI drifted from cockpit/openapi.json — run "
        "scripts/gen-api-types.sh and commit openapi.json + "
        "cockpit/web/src/generated/openapi.d.ts")


def test_component_names_are_clean():
    """Component names become TS type names. FastAPI mangles collisions
    (app__api__Session) and splits diverging request/response schemas
    (Session-Input). Both produce names outside the plain alphanumeric
    convention, so both fail this regex."""
    import json
    doc = json.loads(openapi_json())
    for name in doc.get("components", {}).get("schemas", {}):
        assert re.fullmatch(r"[A-Za-z0-9]+", name), (
            f"unclean component name {name!r} — a model collision or "
            "request/response schema split; rename the model in "
            "app/schemas.py")
