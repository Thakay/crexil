"""The committed-contract exporter: one canonical serialization of
`app.openapi()` shared by the CLI and the drift pytest.

    cd cockpit/server && uv run python -m app.openapi_export

writes `cockpit/openapi.json` — the artifact `scripts/gen-api-types.sh`
feeds to openapi-typescript. Importing `app.main` is side-effect-light (no
DB, no network; the lifespan never runs), and every environment-dependent
route is `include_in_schema=False`, so the output is deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def openapi_json() -> str:
    from .main import app
    return json.dumps(app.openapi(), indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SNAPSHOT_PATH.write_text(openapi_json())
    print(f"wrote {SNAPSHOT_PATH}")
