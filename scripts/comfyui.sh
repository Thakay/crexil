#!/usr/bin/env bash
# Launch the pinned ComfyUI runtime (foreground). Service alternative:
#   systemctl --user start comfyui
# Flags are a known WSL2 bug surface (pinned-memory OOM): keep them here, not hardcoded elsewhere.
set -euo pipefail

COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
PORT="${COMFY_PORT:-8188}"
EXTRA_FLAGS="${COMFY_FLAGS:---enable-manager --disable-pinned-memory}"

cd "$COMFY_DIR"
exec "$COMFY_DIR/.venv/bin/python" main.py --port "$PORT" $EXTRA_FLAGS "$@"
