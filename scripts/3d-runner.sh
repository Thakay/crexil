#!/usr/bin/env bash
# Launch the 3D engine runner (foreground). Service alternative:
#   systemctl --user start 3d-runner
# Mirrors scripts/comfyui.sh: the GPU work stays native, the cockpit
# reaches it over HTTP. Flags live here, not hardcoded.
set -euo pipefail

# REPO defaults to the checkout this script lives in.
REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
export THREED_RUNNER_HOST="${THREED_RUNNER_HOST:-127.0.0.1}"
export THREED_RUNNER_PORT="${THREED_RUNNER_PORT:-8189}"

exec "$REPO/tools/3d/envs/funnel/.venv/bin/python" "$REPO/tools/3d/runner.py" "$@"
