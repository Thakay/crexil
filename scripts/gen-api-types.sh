#!/usr/bin/env bash
# The contract ritual: server models -> committed OpenAPI -> generated TS
# types. Run after ANY route or wire-model change, then commit BOTH outputs
# (cockpit/openapi.json + cockpit/web/src/generated/openapi.d.ts);
# tests/test_openapi_snapshot.py fails until you do.
set -euo pipefail
cd "$(dirname "$0")/.."

(cd cockpit/server && uv run python -m app.openapi_export)
(cd cockpit/web && npm run --silent gen:api)

echo "--- outputs (commit both if changed) ---"
git status --short cockpit/openapi.json cockpit/web/src/generated/ 2>/dev/null || true
