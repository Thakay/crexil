#!/usr/bin/env bash
# Stamped local Docker deploy. Bakes the git revision and the UTC build time
# into the image and the SPA bundle (surfaced in /api/health and the UI footer;
# a bundle/server mismatch renders a visible warning). A plain
# `docker compose up -d --build` still works but stamps "unknown"; use this
# script so the stamp is real. It builds and starts containers on this
# machine only, and only when you run it.
set -euo pipefail
cd "$(dirname "$0")/.."

GIT_REV=$(git rev-parse --short HEAD)
git diff --quiet && git diff --cached --quiet || GIT_REV="${GIT_REV}-dirty"
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "building cockpit image: ${GIT_REV} (${BUILD_TIME})"
docker compose build --build-arg GIT_REV="$GIT_REV" --build-arg BUILD_TIME="$BUILD_TIME"
docker compose up -d

# the host port compose actually published (it may come from .env)
PORT="$( (docker compose port cockpit 8000 2>/dev/null || true) | tail -n 1)"
PORT="${PORT##*:}"
PORT="${PORT:-${COCKPIT_PORT:-8000}}"
echo "waiting for :${PORT} ..."
for _ in $(seq 1 20); do
  sleep 2
  if curl -sf -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then break; fi
done
curl -s -m 5 "http://127.0.0.1:${PORT}/api/health" | python3 -m json.tool || {
  echo "health endpoint not answering; check: docker compose logs --tail 50 cockpit"
  exit 1
}

cat <<EOF

deployed ${GIT_REV}:
  cockpit:              http://localhost:${PORT}/#/w
  health / build stamp: http://localhost:${PORT}/api/health
EOF
