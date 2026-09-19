# Crexil cockpit image. The cockpit is containerized; ComfyUI and the 3D runner
# stay native services on the host with direct GPU access.
#
# Build:  docker compose build
# Run:    docker compose up -d   -> http://localhost:8000

# --- stage 1: web bundle ---
FROM node:24-slim AS web
# build stamp: scripts/deploy.sh passes the git rev; a plain
# `docker compose up -d --build` stamps "unknown", which the UI footer
# shows, so drift announces itself instead of hiding
ARG GIT_REV=unknown
WORKDIR /web
COPY cockpit/web/package.json cockpit/web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY cockpit/web .
ENV VITE_BUILD_ID=$GIT_REV
RUN npm run build

# --- stage 2: server ---
FROM python:3.12-slim AS server
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /usr/local/bin/
WORKDIR /srv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY cockpit/server/pyproject.toml cockpit/server/uv.lock ./
RUN uv sync --frozen --no-dev
COPY cockpit/server/app ./app
COPY --from=web /web/dist ./static

ARG GIT_REV=unknown
ARG BUILD_TIME=

# paths inside the container; compose mounts the real things here
ENV COCKPIT_REPO_ROOT=/repo \
    COCKPIT_STATIC_DIR=/srv/static \
    COCKPIT_DB=/data/cockpit-v4.sqlite \
    COCKPIT_STAGING_DIR=/data/staging \
    MODELS_ROOT=/models \
    PROMOTE_DIR=/mnt/d/PROMOTED \
    COCKPIT_BUILD_ID=$GIT_REV \
    COCKPIT_BUILD_TIME=$BUILD_TIME

EXPOSE 8000
# 0.0.0.0 inside the container only; the host publish binds 127.0.0.1
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
