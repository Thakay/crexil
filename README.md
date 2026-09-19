# Crexil

One brief, every engine, one grid, the comparison kept.

Crexil is a supervisory layer for AI-made media. You write one brief and it goes to every
image engine you have: local diffusion models through ComfyUI graphs and hosted models
through OpenRouter. The takes land side by side in one grid. Every take
remembers its parents, its engine, its seed and its cost. A winner goes on to refinement, to
local 3D engines, or out through Promote, which copies the artefact and its manifest to a hand-off folder
for your DCC tool. Nothing is silently lost, and every decision is recorded.

It is the front door and the record, never the workbench. It is not a chat app, a node-graph
editor, or version control for pixels.

## Status

Early, and under active development. Read this before you try it:

- **Single user, local first.** The server binds to localhost and has no accounts.
- **Developed on Linux under WSL2** with one NVIDIA GPU. Other platforms are untested.
- **There is no installer yet.** Packaging, portability and a first-run path for newcomers
  are planned work. Today this repository is a base for development: the checks, the build
  and an isolated instance run from a clean checkout, and nothing else is promised.
- **Engines and models are not included.** ComfyUI, the 3D engines and every model weight are
  separate installations with their own licences. Some default downloads, and the checkpoint
  the SDXL reference graphs load, are non-commercial or unconfirmed: read
  [docs/third-party.md](docs/third-party.md) before any commercial use.

Built for my own use. Issues are read, and pull requests are reviewed when I can.

## What is here

| Path | What it holds |
|---|---|
| `cockpit/server` | The server: FastAPI, SQLite, the scheduler and the engine adapters (Python 3.12, `uv`) |
| `cockpit/web` | The client: React, TypeScript, Vite; unit tests in vitest, end-to-end tests in Playwright |
| `cockpit/openapi.json` | The committed API contract; the client's types are generated from it |
| `configs/` | Engine and dial tables served to the client, plus example host configuration |
| `presets/`, `workflows/` | Presets and the ComfyUI reference graphs they bind to (adding one is config, never code) |
| `providers.json` | Hosted models and their price estimates |
| `tools/3d/` | The 3D lane: a small runner and wrappers around separately installed 3D engines |
| `scripts/` | Checks, type generation, launchers, the model downloader, bench and smoke tools |
| `bench/` | Fixed bench inputs: briefs and region-edit cases |
| `snapshots/pins.json` | The pinned versions of the ComfyUI runtime and its node packs |
| `docs/` | The product contract and laws, the failure case catalogue, the developer guide |

## For developers

You need Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node with npm. No GPU, engine
or API key is needed to run the checks.

```bash
(cd cockpit/server && uv sync)
(cd cockpit/web && npm ci)
scripts/check.sh          # ruff, pytest, vitest, tsc
```

To see it run on throwaway data, with no engine, no key and no promote folder of yours:

```bash
(cd cockpit/web && npm run build)
mkdir -p /tmp/crexil-dev/promoted
cd cockpit/server
COCKPIT_DB=/tmp/crexil-dev/cockpit-v4.sqlite COCKPIT_STAGING_DIR=/tmp/crexil-dev/staging \
  PROMOTE_DIR=/tmp/crexil-dev/promoted PROMOTE_ROOTS=/tmp/crexil-dev \
  COMFYUI_URL=http://127.0.0.1:9 THREED_URL=http://127.0.0.1:9 OPENROUTER_API_KEY= \
  COCKPIT_STATIC_DIR=../web/dist uv run uvicorn app.main:app --port 8001
# then open http://localhost:8001
```

With no engine the grid has nothing to generate with, and `/api/health` answers 503 by design. The
[developer guide](docs/development.md) covers the environment variables, type generation, the
optional engines and the known traps.

Read before changing behavior:

- [docs/PRD.md](docs/PRD.md): what the application is, the product model, and the 23 laws the
  code cites as `law N`.
- [docs/failure-cases.md](docs/failure-cases.md): the failure cases the code and tests cite by
  code, such as `A1` or `C2`.
- [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md): conventions, for people and
  for coding agents.

## History

This repository starts at the point where Crexil was separated from the private repository it
grew up in. The earlier history is not included, and the first commits add the existing
application area by area rather than replaying how it was written. AI coding tools contributed
to this code. From the first commit here on, a commit made with an AI tool carries a
`Co-authored-by` trailer for it.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party engines, models and
services keep their own terms: [docs/third-party.md](docs/third-party.md).
