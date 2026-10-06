#!/usr/bin/env bash
# Build and serve an isolated, persistent Crexil preview on localhost.
# All writable state stays in .preview; unavailable runtime adapters prevent
# GPU or paid work even if a caller removes a queue bench. This never deploys.
set -euo pipefail
cd "$(dirname "$0")/.."

PREVIEW_PORT="${1:-8010}"
python3 - "$PREVIEW_PORT" <<'PY'
import socket
import sys

try:
    port = int(sys.argv[1])
    if not 1024 <= port <= 65535 or port == 8000:
        raise ValueError("choose an unprivileged port other than production :8000")
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
except (ValueError, OSError) as exc:
    sys.exit(f"Preview cannot use this port: {exc}")
PY

export CREXIL_PREVIEW=1
export COCKPIT_REPO_ROOT="$PWD"
export COCKPIT_BUILD_TIME
COCKPIT_BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
export COCKPIT_BUILD_ID="preview-$(git rev-parse --short HEAD)-$(date -u +%Y%m%dT%H%M%S)-$$"
export VITE_BUILD_ID="$COCKPIT_BUILD_ID"
export VITE_CREXIL_PREVIEW=1
PREVIEW_DIR="$PWD/.preview"
mkdir -p "$PREVIEW_DIR/state" "$PREVIEW_DIR/staging" "$PREVIEW_DIR/promote"

echo "Building $COCKPIT_BUILD_ID"
(
    cd cockpit/web
    npm run build -- --outDir "$PREVIEW_DIR/static"
)

PREVIEW_PID=""
cleanup() {
    if [[ -n "$PREVIEW_PID" ]]; then
        kill "$PREVIEW_PID" 2>/dev/null || true
        wait "$PREVIEW_PID" 2>/dev/null || true
        rm -f "$PREVIEW_DIR/server.pid"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
(
    cd cockpit/server
    exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PREVIEW_PORT"
) >"$PREVIEW_DIR/server.log" 2>&1 &
PREVIEW_PID=$!
echo "$PREVIEW_PID" >"$PREVIEW_DIR/server.pid"

# Engine health is deliberately degraded. Readiness proves the database,
# preview contract, and exact bundle/server build match, not GPU availability.
if ! python3 - "$PREVIEW_PORT" "$PREVIEW_PID" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/api/health"
for _ in range(100):
    try:
        os.kill(int(sys.argv[2]), 0)
        try:
            response = urllib.request.urlopen(url, timeout=1)
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise
            response = exc
        with response:
            health = json.load(response)
        assert health["preview"] is True, "server is not a preview"
        assert health["db"] is True, "database did not open"
        assert health["build"]["id"] == os.environ["COCKPIT_BUILD_ID"], "build mismatch"
        assert health["build"]["time"] == os.environ["COCKPIT_BUILD_TIME"], "build time mismatch"
        assert health["openrouter"]["configured"] is False, "provider credentials present"
        assert health["openrouter"]["requests_total"] == 0, "unexpected provider request"
        assert health["comfyui"]["ok"] is False, "unexpected active GPU runtime"
        print(f"Preview ready: http://localhost:{sys.argv[1]}/")
        print(f"Health and build: {url}")
        print("Generation unavailable; imported sources and preparation persist in .preview.")
        break
    except (ConnectionError, urllib.error.URLError, TimeoutError):
        time.sleep(0.2)
else:
    sys.exit("Preview readiness timed out; inspect .preview/server.log")
PY
then
    tail -n 35 "$PREVIEW_DIR/server.log"
    exit 1
fi
wait "$PREVIEW_PID"
