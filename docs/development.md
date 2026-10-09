# Development guide

This guide is for developers who keep building Crexil. It covers the checks, the builds, an isolated
instance and the optional engines. It is not an installation guide for end users: packaging,
portability across operating systems and a newcomer installer are later work.

Crexil is the product name. `cockpit` is the name of the server and client component, and the
environment variables keep the `COCKPIT_` prefix.

## 1. Repository layout

| Path | What it holds |
|---|---|
| `cockpit/server/` | FastAPI server: `app/`, `tests/`, `pyproject.toml`, `uv.lock` |
| `cockpit/web/` | React, TypeScript and Vite client: `src/`, Playwright specs in `e2e/` |
| `cockpit/openapi.json` | The committed API document. The client types are generated from it |
| `configs/` | `engines.json` (capability table), `dials.json` (dial mapping), example host files for ComfyUI |
| `presets/`, `workflows/` | Presets and the ComfyUI graphs they bind to. Each folder has a README |
| `providers.json`, `snapshots/pins.json` | Hosted model pins, and the pinned ComfyUI runtime and node packs |
| `tools/3d/` | The 3D runner, its engine wrappers and `pins.json` |
| `scripts/` | Checks, type generation, deploy, engine launchers, model downloader, smoke and bench tools |
| `bench/` | Fixed bench inputs: briefs, region cases and their synthetic fixtures |
| `docs/` | `PRD.md` (product contract and laws), `failure-cases.md`, `third-party.md`, this guide |
| `Dockerfile`, `compose.yaml`, `.env.example` | Optional container build, and the environment template |

## 2. Prerequisites

- Python 3.12, `uv`, `bash` and `git`. The server is a uv project with a committed lock file.
- Node.js and npm. Vite 7 needs Node 20.19+ or 22.12+. The Docker build uses Node 24.
- Optional: Docker with Compose (`scripts/deploy.sh`), Playwright's Chromium (end-to-end tests).

No GPU, engine or API key is needed to run the checks. The suites use fakes and scripted executors.
Last verified on a clean checkout with Python 3.12, uv 0.11, Node 24 and npm 11.

## 3. Commands

```bash
# All from the repository root
(cd cockpit/server && uv sync --frozen)      # install from the lock files
(cd cockpit/web && npm ci)

scripts/check.sh                             # all checks: ruff, pytest, vitest, tsc -b, the specs' tsc
scripts/check.sh ruff --fix                  # ruff only; arguments after "ruff" go to ruff
(cd cockpit/server && uv run pytest -q)      # one file: uv run pytest tests/test_scheduler.py
(cd cockpit/web && npm run test)             # one folder: npx vitest run src/app/composer
(cd cockpit/web && npx tsc -b)
(cd cockpit/web && npx tsc -p tsconfig.e2e.json)   # the browser specs and both Playwright configs

(cd cockpit/server && uv run uvicorn app.main:app --reload)   # server on 127.0.0.1:8000
npm --prefix cockpit/web run dev             # hot reload on :5173, proxied to the source server on :8000;
                                             # COCKPIT_DEV_API=http://127.0.0.1:8010 targets the preview instead
(cd cockpit/web && npm run build)            # tsc -b, then vite build into cockpit/web/dist
(cd cockpit/web && npm run e2e)              # Playwright; needs the build above

scripts/gen-api-types.sh                     # after any route or wire-model change
scripts/preview.sh [port]                    # isolated preview on 127.0.0.1:8010 (or port); cannot generate or spend
scripts/crexil.py --help                     # the artist and agent client of the shared HTTP actions
scripts/seed_demo_project.py --help          # seeds the demo project through the HTTP actions; a dry run unless --live
uv run --script tools/agent-door/crexil_mcp.py --help   # the MCP agent door; see docs/agents.md
python3 tools/voice-director/apply.py --dry-run       # the voice director's agent configuration; see its README
scripts/deploy.sh                            # optional local Docker build and start
```

**Baseline.** On a clean checkout ruff passes, pytest reports 1424 passed and 2 skipped in about
155 seconds, vitest reports 868 passed in 71 files, and `tsc -b`, the specs' type-check and
`vite build` pass; `scripts/check.sh` takes about 165 seconds. The skipped tests are the live
checks: `LIVE_CATALOG=1` compares the provider catalogue with the live OpenRouter API (network, no
key, no cost), and `LIVE_ELEVENLABS=1` with `ELEVENLABS_API_KEY` exported in the shell makes one live
ElevenLabs sound effect (about $0.001; a test session never reads `.env`). Ruff's rules live in
`cockpit/server/pyproject.toml`; `scripts/check.sh` applies them to `cockpit/server`, `scripts` and
`tools`.

**Hermetic tests.** Every pytest session is sealed before any test module imports the app:
`tests/conftest.py` and `tools/agent-door/tests/conftest.py` apply `cockpit/server/tests/hermetic.py`. It
switches python-dotenv off (`PYTHON_DOTENV_DISABLED=1`, python-dotenv 1.2 or later) for the session and every
process a test starts; points `COMFYUI_URL`, `THREED_URL`, `THREED_COMFY_URL`, `VOICE_RUNNER_URL`,
`OPENROUTER_URL` and `ELEVENLABS_URL` at the closed port 9; points `COCKPIT_DB`, `COCKPIT_STAGING_DIR`,
`PROMOTE_DIR` and `PROMOTE_ROOTS` at the session's own temporary folder, so a test that names no store of its
own never opens the checkout's store or the key saved beside it; and blanks `OPENROUTER_API_KEY`,
`ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_AGENT_ID`, whatever the shell or a checkout's `.env` holds. A test
session keeps no key, except the ElevenLabs key exported for `LIVE_ELEVENLABS=1`. A test that needs an engine or
a key points `config` at its own fake. `tests/test_hermetic.py` proves the seal with a planted `.env`, saved key
and shell, and fails when `config` gains a setting named `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` or
`*_AGENT_ID`, or a web address, that the seal does not cover. The harness suite sets `PYTHON_DOTENV_DISABLED` for
every process it starts, the specs' seed scripts included; a preview never reads `.env`.

**End-to-end tests.** They need a fresh `npm run build`, `uv` on the `PATH`, Playwright's Chromium
(`npx playwright install chromium`) and a free port 8123. They need no engine: Playwright boots the
server itself with scripted executors, a store and a promote folder under `.staging/`, and
`COMFYUI_URL` pointed at a closed port.

**Type generation.** `scripts/gen-api-types.sh` regenerates `cockpit/openapi.json` from the server's
routes and models, then `cockpit/web/src/generated/openapi.d.ts` from that file. Commit both:
`cockpit/server/tests/test_openapi_snapshot.py` fails until the committed document matches the code.
Docstrings of routes and models are part of the document, so a docstring edit there counts.
Afterwards `tsc -b` flags every stale call site in the client.

**Preview.** `scripts/preview.sh` builds the client and serves an isolated instance with
`CREXIL_PREVIEW=1`. Its store, staging, built client and promote folder live under `.preview/` in
the checkout; it ignores `.env` and every path override, has no key, and points every engine at a
closed port, so it can prepare and record work but never generate or spend. It refuses port 8000.
It waits until `/api/health` confirms the preview, the store, the matching build and zero provider
requests, then serves until stopped.

**Agent client.** `scripts/crexil.py` drives the same HTTP actions as the artist interface, for
people and agents alike. `--actor` names the participant recorded with each change, mutations print
a delivery ID to reuse when retrying after a connection failure, and `--url` or `CREXIL_URL` picks
the instance (the source server on port 8000 by default). It never writes the store or staging itself (law 19).

**Docker.** `scripts/deploy.sh` is opt-in. It bakes the git revision and the build time into the
image, runs `docker compose up -d` and polls `/api/health`. A plain `docker compose up -d --build`
stamps the build `unknown`, and the UI footer shows `BUILD DRIFT` when the client and server ids
differ.

### Browser tests

`scripts/e2e.sh [normal|preview|all]` runs the Playwright suites offline, each in a fresh folder
from `mktemp`. `normal` runs every spec against the harness server: scripted executors, the normal
build, and a hosted catalogue from `providers.json` alone. `preview` runs the crexil journeys
against the isolated preview build. `all`, the default, runs both. `CREXIL_E2E_PORT` picks the port
(default 8123), and `CREXIL_E2E_KEEP=1` keeps a passing run's folder. Where unprivileged user
namespaces exist, the suites run in a network namespace with only loopback. A test that holds in
one mode only skips itself in the other, with its reason. `npm run e2e` runs the harness suite in
place, with state in `.staging/e2e`. Specs take store, promote and screenshot paths from
`cockpit/web/e2e/paths.ts`.

`crexil-audio.spec.ts` runs blind voice casting end to end on the scripted executors: it prepares a
blind voice-design run, auditions it from the keyboard, picks a take with a reason, reveals the run
and reads the record and its export. Every voice-design operation the harness offers takes part;
scripted audio takes are four-second tones. The harness suite sets a placeholder
`ELEVENLABS_API_KEY`, with `ELEVENLABS_URL` on a closed port, so the ElevenLabs operations are
ready; the scripted executors never call ElevenLabs. It also checks that the digits reach the voices of a
row whose images come first, that an attempt returning three takes shows its one charge once, and that a
local voice marks ElevenLabs v4 before it is chosen, and that after the reveal each face names its engine's
model ("Voice Design"), the take carrying the charge whole at 1440 and 1920.

Playwright runs specs without type-checking them. `scripts/check.sh` type-checks the specs,
`paths.ts` and both Playwright configs through `cockpit/web/tsconfig.e2e.json`: the app's strict
options plus Node's types, which the app's own `tsconfig.json` leaves out so Node globals never
type-check in browser code.

## 4. Running an isolated instance

An instance is its store, its staging folder, its promote folder and its port. By default the store
and staging sit inside the checkout. To run beside another installation, move them all:

```bash
(cd cockpit/web && npm run build)
mkdir -p /tmp/crexil-dev/promoted
cd cockpit/server
COCKPIT_DB=/tmp/crexil-dev/cockpit-v4.sqlite \
COCKPIT_STAGING_DIR=/tmp/crexil-dev/staging \
PROMOTE_DIR=/tmp/crexil-dev/promoted \
PROMOTE_ROOTS=/tmp/crexil-dev \
COCKPIT_STATIC_DIR=../web/dist \
OPENROUTER_API_KEY= \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
```

- `COCKPIT_DB` is the SQLite store. A key saved through the setup route lives in `secrets.json` in
  the same folder, so a moved store has its own saved key. `COCKPIT_STAGING_DIR` holds staged takes, region
  masks, manifests and the cached provider catalogue.
- `PROMOTE_DIR` is the hand-off folder and `PROMOTE_ROOTS` the fence around it. The server never
  creates the promote folder, so create it first. A re-point made in the UI is saved in the store
  and wins over `PROMOTE_DIR` for that store.
- `COCKPIT_STATIC_DIR` is the built client. The default, `cockpit/server/static`, is filled only
  inside the Docker image, so a run from source points it at `cockpit/web/dist`. The folder is
  looked up once, at start, so build first.
- The port is uvicorn's `--port`; no variable sets it for a run from source. Docker publishes
  `127.0.0.1:8000` (or `COCKPIT_PORT`), Playwright uses 8123, and `scripts/bench.py` and
  `scripts/e2e_region_fanout.py` expect 8001. The Vite proxy targets the source server on port
  8000 unless `COCKPIT_DEV_API` names another server, such as the preview, and
  `scripts/preview.sh` defaults to 8010.
- `COMFYUI_URL` and `THREED_URL` choose the engines. Two instances that share one ComfyUI share one
  GPU queue. Point both at a closed port to keep an instance away from real engines.
- `.env` at the checkout root is read by every instance started from that checkout, except a preview,
  the harness suite's server and a test session. A process environment value wins, even an empty one. `OPENROUTER_API_KEY=` above, with a fresh store folder,
  leaves the instance without a key, so it cannot spend money.

`GET /api/health` reports the store (`db`), the build (`build.id`, `build.time`; `dev` is a run from
source), ComfyUI (`comfyui.ok` with `version`, or `error`), the loaded presets, the workflows and
model folders, the promote folder and the OpenRouter key state. It answers 200 only when ComfyUI
answers, a preset loaded and the workflows folder holds graphs. Otherwise it answers 503 with the
same body, so an instance without ComfyUI reports 503 by design; probe `GET /api` for liveness, as
the Playwright setup does. `GET /api/mesh/health` covers the 3D runner.

## 5. Environment variable reference

Every variable read in `cockpit/server/app/config.py`. `<root>` is `COCKPIT_REPO_ROOT`.

| Variable | Default | Meaning |
|---|---|---|
| `COCKPIT_REPO_ROOT` | the checkout root, found from the location of `config.py` (`/repo` in the image) | Base of the default paths. `.env` is read from here |
| `CREXIL_PREVIEW` | unset | `1` runs an isolated preview: store, staging, built client and promote folder under `<root>/.preview`, `.env` and path overrides ignored, no key, every engine URL closed. Generation and spending are impossible |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI base URL |
| `THREED_URL` | `http://127.0.0.1:8189` | 3D runner base URL |
| `THREED_RUNNER_ALLOW_REMOTE` | unset | Read by the 3D runner: `1` lets it listen beyond this machine |
| `THREED_RUNNER_ALLOWED_HOSTS` | empty | Read by the 3D runner: extra host names it answers (comma-separated) |
| `THREED_RUNNER_HOST` | `127.0.0.1` | Read by the 3D runner: the address it listens on |
| `THREED_RUNNER_PORT` | `8189` | Read by the 3D runner: the port it listens on. Point `THREED_URL` at it |
| `COCKPIT_THREED_POLL_S` | `2` | Seconds between polls of a running 3D job |
| `COCKPIT_PRESETS_DIR` | `<root>/presets` | Preset folder |
| `COCKPIT_WORKFLOWS_DIR` | `<root>/workflows` | Graph folder |
| `COCKPIT_CONFIGS_DIR` | `<root>/configs` | Folder of `dials.json` and `engines.json`, served as they are |
| `COCKPIT_STAGING_DIR` | `<root>/.staging/cockpit` | Working files of the instance |
| `COCKPIT_DB` | `<root>/cockpit/server/cockpit-v4.sqlite` | The SQLite store |
| `PROMOTE_DIR` | `~/crexil-handoff` | Hand-off folder that Promote copies into, and where a delivery writes its character package folders. Made absolute at start; empty reads as unset. The server never creates it. Under WSL it is often a folder on a Windows drive |
| `PROMOTE_ROOTS` | empty, which means `PROMOTE_DIR` itself | Colon-separated folders a promote target may sit in. A target outside them is refused |
| `COCKPIT_STATIC_DIR` | `cockpit/server/static` | Built web client served at `/` |
| `MODELS_ROOT` | `~/models` | Model tree. Read only: file hashes for manifests, and the health check |
| `COCKPIT_PROVIDERS_FILE` | `<root>/providers.json` | Pins and overrides for hosted models |
| `OPENROUTER_URL` | `https://openrouter.ai/api/v1` | OpenRouter base URL. Override it to use a mock |
| `OPENROUTER_API_KEY` | empty | Secret. Empty turns the hosted image lane off, unless a key is saved through the setup route |
| `ELEVENLABS_API_KEY` | empty | Secret, read from the environment only and never stored. Empty turns the ElevenLabs audio engines off |
| `ELEVENLABS_URL` | `https://api.elevenlabs.io` | ElevenAPI base URL. Override it to use a mock |
| `VOICE_RUNNER_URL` | `http://127.0.0.1:8190` | Local voice runner base URL |
| `ELEVENLABS_VOICE_AGENT_ID` | empty | The id of the voice director, a voice agent built on ElevenAgents; `tools/voice-director/apply.py` prints it |
| `CREXIL_VOICE_MINUTES_CAP` | `0` | Voice director minutes the installation allows. 0 starts no session |
| `CREXIL_VOICE_SESSION_MAX_S` | `300` | The longest voice session; keep it equal to the agent's `max_duration_seconds` |
| `COCKPIT_BUILD_ID` | `dev` | Build id shown by `/api/health` and the UI footer |
| `COCKPIT_BUILD_TIME` | empty | Build time shown with the id |
| `COCKPIT_EXTERNAL_CONCURRENCY` | `4` | Hosted takes in flight at once |
| `COCKPIT_LOCAL_TAKE_TIMEOUT_S` | empty (no limit) | Time limit for one local take. Leave room for model warm-up and multi-pass region takes |
| `COCKPIT_EXTERNAL_TAKE_TIMEOUT_S` | empty (the adapter's 300 s read timeout applies) | Time limit for one hosted take |
| `COCKPIT_RETRY_BACKOFF_S` | `2` | Wait before the single retry of an unreachable engine |
| `COCKPIT_BILL_CHECK_GAP_S` | `20` | Gap between the three bill checks after a charged but empty response |
| `COCKPIT_RATE_LIMIT_RETRIES` | `4` | Retries of a rate-limited take |
| `COCKPIT_RATE_LIMIT_BACKOFF_S` | `10` | Wait when the provider names no retry window |
| `COCKPIT_RATE_LIMIT_MAX_WAIT_S` | `120` | Cap on a provider's retry window |
| `COCKPIT_GPU_FOREIGN_HOLD_GB` | `2.5` | GPU memory held by other programs above which an out-of-memory failure means "the card is held" |
| `COCKPIT_GPU_RECHECK_S` | `15` | Seconds between checks of a held card |
| `COCKPIT_DISK_FALLBACK_BYTES_PER_PX` | `1.5` | Size estimate per pixel for the free-disk check at Run, before any take has landed |
| `COCKPIT_DISK_MARGIN_BYTES` | `67108864` (64 MiB) | Free space that check keeps in reserve |
| `COCKPIT_HARNESS_EXECUTORS` | unset | `1` swaps the engines for scripted executors and mounts `/api/harness`. Tests only |
| `COCKPIT_COMFY_POLL_S` | `1.0` | Poll interval while a ComfyUI job runs |
| `COCKPIT_HISTORY_CONFIRM_TRIES` | `20` | Attempts to read a finished job from ComfyUI's history |
| `COCKPIT_HISTORY_CONFIRM_INTERVAL_S` | `0.5` | Seconds between those attempts |

Launchers and tools read a few of their own (`COMFY_*`, `THREED_*`, `BENCH_ROOT`, `COCKPIT_URL`,
`SMOKE_KEEP_RES`, `HF_TOKEN`); each script names them. `compose.yaml` reads `COCKPIT_PORT`,
`MODELS_ROOT`, `PROMOTE_DIR`, `PROMOTE_ROOTS`, `PROMOTE_MOUNT`, `OPENROUTER_API_KEY`,
`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_AGENT_ID`, `CREXIL_VOICE_MINUTES_CAP` and
`CREXIL_VOICE_SESSION_MAX_S` from `.env`. It mounts `PROMOTE_MOUNT`, else the hand-off folder, at the
same path inside the container, and a missing folder stops the start instead of being created.

The client reads two build-time variables. `VITE_CREXIL_PREVIEW=1`, which `scripts/preview.sh`
sets, builds the preview client. `VITE_CREXIL_FRONT_DOOR` (`projects` or `workspaces`) names what
the bare URL opens; unset, a preview build opens the project surface and every other build the
legacy Home.

## 6. External engines

All engines are optional and none is bundled. The server generates nothing itself. An engine that is
down is benched with its reason, and the server still boots. **Engines, node packs, model weights
and hosted models are not part of this repository. Each carries its own licence and terms, and some
forbid commercial use.** See [third-party.md](third-party.md). `scripts/models-manifest.json`
records a licence note per entry, which the downloader prints; nothing enforces it.

Audio engines are rows of `configs/engines.json` with payload `audio`, each naming its provider.
They ship disabled; set `enabled` when an installation can reach them. A row states per verb how many
interleaved channels a raw PCM answer carries, `pcm_channels` (1 unless stated). It sits beside `fixed`, never in
it, because `fixed` fields are sent to the provider. ElevenLabs Sound Effects answers `pcm_48000` in stereo, so its
row says 2; speech and Voice Design answer in mono.

The local voice engine (Qwen3-TTS) runs in its own environment behind `tools/tts/runner.py`;
`tools/tts/README.md` covers install and start. Set `VOICE_RUNNER_URL` if it serves elsewhere than
`http://127.0.0.1:8190`, and enable the `qwen3-tts` row. Each attempt's job carries the client reference
`<slot>:<attempt>`; the runner keeps one job per reference and answers `GET /jobs?client_ref=<ref>`.

ElevenLabs is reached directly over HTTP, never through its SDK, because the SDK retries on its own and
could charge twice for one take. `LIVE_ELEVENLABS=1` with `ELEVENLABS_API_KEY` exported in the shell runs
one opt-in live sound-effect test that costs about $0.001; `scripts/check.sh` never runs it.

A designed ElevenLabs voice becomes a saved voice, which takes one of the account's voice slots, the
first time it speaks. Crexil saves each designed voice at most once: the save is kept in the record
(`voice.saved`) as soon as ElevenLabs answers, and before saving Crexil looks the voice up in the
account by its `crexil_gen_voice_id` label. Crexil never deletes a voice; free slots in the
ElevenLabs account. A save refused because every slot is in use says so.

### Local ComfyUI lane

- ComfyUI is a separate checkout with its own virtual environment, reached at `COMFYUI_URL`.
  `scripts/comfyui.sh` starts it in the foreground from `COMFY_DIR`. `configs/comfyui.service` is an
  example systemd user unit, and `configs/extra_model_paths.yaml` maps the model tree into ComfyUI's
  model folders. Check the paths in both before use.
- `snapshots/pins.json` pins ComfyUI `v0.27.1`, frontend package `1.45.20`, the integrated manager
  `comfyui_manager 4.2.2`, Python `3.12.3`, torch `2.12.1+cu130`, torchvision `0.27.1+cu130`,
  torchaudio `2.11.0+cu130` and seven node packs by commit. The launch flags are
  `--enable-manager --disable-pinned-memory`.
- Binding: a preset in `presets/` names its engine as `model_family`, an id in
  `configs/engines.json`, and its `workflows` map names one graph in `workflows/` per capability.
  Preset inputs patch graph nodes by their stable title (`_meta.title`), never by node id. A graph
  version (`@vN`) never changes after it ships. Presets reload on every catalogue fetch, so no
  restart is needed.
- Model files come from `scripts/download_models.py`, driven by `scripts/models-manifest.json`
  (`--list`, `--verify`, `--group`, `--all`). With no arguments it fetches only the `flux-core`
  starter group, all Apache-2.0; any other group downloads when named, and every entry prints its
  recorded licence before it downloads. It needs the `hf` CLI, `curl`, and for gated
  repositories a Hugging Face token. It writes under the manifest's `models_root` key; `MODELS_ROOT`
  moves only where the server and `scripts/smoke.py` look.
- `scripts/smoke.py` submits every graph in `workflows/` to a running ComfyUI and checks that a PNG
  comes back. It skips graphs whose model files are missing. After any change to ComfyUI, a node
  pack or a model: update `snapshots/pins.json`, then run it. Like `scripts/bench.py` and
  `scripts/e2e_region_fanout.py` it drives real engines, so run one such script at a time.

### 3D runner

- `tools/3d/runner.py` is a small standard-library HTTP service, on `127.0.0.1:8189` unless
  `THREED_RUNNER_HOST` and `THREED_RUNNER_PORT` say otherwise. A second installation on the same
  machine takes another port and points its server's `THREED_URL` at it. Start it with
  `scripts/3d-runner.sh`, or the example unit `scripts/3d-runner.service` under a unit name no
  other installation on the machine uses. Restart it after any change in `tools/3d/`: a running
  runner and its loaded engine keep their old source.
- Once the weights are in the Hugging Face cache, start the runner with `HF_HUB_OFFLINE=1`: the
  engines then read the cache and never download or write to it. `funnel.py` reads `PROMOTE_DIR`
  from the environment, or else from the checkout's `.env`, only when a run resolves a promoted
  asset or promotes; the runner never promotes, so it never opens `.env`.
- Measured on one RTX 4090: a first TRELLIS.2 take loads the engine in about 65 s, generates in
  about 73 s at `1024_cascade` and extracts the mesh in about 38 s at texture size 2048. The card
  peaks about 14 GB above idle, and the resident engine holds about 25 GB of host memory. The GLB
  of about 940,000 triangles weighs about 36 MB, and its kept voxel field can reach about 180 MB.
  A take's `elapsed_s` includes the engine load it waited for, and `resident.load_s` says how long
  that was (0 when the engine was already loaded).
- The environments hold absolute paths: virtual-environment entry scripts and `activate` files,
  the CUDA toolchain's gcc `specs` (which add an `-rpath` to every link), linker scripts,
  `pkg-config` files and package metadata. A copy of the lane in another folder works once those
  text files name the new folder; compiled files keep their build-time strings harmlessly. Check a
  copy with GNU `grep -rlI <old folder>` (a search that honours `.gitignore` skips the
  environments, which ignore themselves), `readelf -d` for `RPATH` and `RUNPATH`, and
  `find -lname`.
- A job has a kind: `generate` (image to mesh, TRELLIS.2 or Hunyuan3D 2.1), `decimate` (gltfpack on
  a GLB, or TRELLIS.2 re-extraction from a kept voxel field) or `texture` (Hunyuan3D paint with a
  reference image). `tools/3d/funnel.py` runs each kind.
- One heavy GPU job at a time (law 14). The runner runs one job and queues the rest, and the
  server's local lane serialises mesh takes against image takes. Between takes the runner keeps at
  most one engine loaded (`THREED_RESIDENT_TTL_S`, 600 seconds; `0` turns this off).
- `tools/3d/pins.json` pins the vendor commits of TRELLIS.2 and Hunyuan3D-2.1, one virtual
  environment per engine (their pins conflict: torch `2.6.0+cu124` and `2.5.1+cu124`, both on Python
  3.10), a torch-free Python 3.12 environment for the runner, the CUDA 12.4 compiler toolchain,
  weight revisions on Hugging Face, and gltfpack 1.2 with checksums.
- The environments, vendored sources, the gltfpack binary and job output are git-ignored
  (`tools/3d/.gitignore`), the weights sit in a Hugging Face cache, and this lane has no install
  script yet. A runner that is down benches only the 3D engines.

### Hosted models

- Hosted images go through one transport, OpenRouter, with one key, `OPENROUTER_API_KEY`, from
  `.env` or the Connections room's key form, which saves it through the setup route
  (`POST /api/setup/openrouter` with the header `X-Cockpit-Setup: 1`, loopback clients only) into
  `secrets.json` beside the store; the environment wins. Without a key the hosted image lane is
  benched, nothing else. Hosted audio goes to ElevenLabs with `ELEVENLABS_API_KEY`, read from the
  environment only.
- The model list is OpenRouter's live catalogue, cached for six hours with a snapshot in the staging
  folder. `providers.json` holds pins, overrides and the offline fallback, and `enabled: true` pins
  a model to the top of the picker. Listed prices are estimates; the billed cost comes back with
  each generation. Model ids and prices never live in code.

## 7. Conventions

- Comments and docstrings are contracts in the present tense: what the thing owns, guarantees and
  never touches. No ticket numbers, no dates, no history; the commit message holds the reason. See
  `CONTRIBUTING.md`.
- Laws are cited as `law N` (section 3 of [PRD.md](PRD.md); never renumbered). Failure cases are
  cited by code, such as `B4` (charged but no image returned) or `C2` (the GPU is held by another
  program); the catalogue is [failure-cases.md](failure-cases.md).
- The OpenAPI ritual: type generation (section 3) follows every route or wire-model change.
- A new engine, preset or graph is configuration, not code. A store schema change is a new
  forward-only step in `cockpit/server/app/db.py`. Scripts and tools use the HTTP API, because the
  server is the only writer of the store (law 19). The one exception is
  `scripts/seed_canvas_fixture.py`, which seeds a throwaway e2e store directly.

## 8. Known limits and traps

- **Platform.** Development happens on Linux under WSL2 with one NVIDIA GPU; other platforms are
  untested. The unit files assume systemd user services. The container reaches host engines through
  `host.docker.internal`, which under WSL2 can die after the host sleeps. Under WSL2 the Linux monotonic clock can run a few percent fast
  against the Windows clock (4.1% measured on one machine), so durations the runner and the server measure
  read long there.
- **No authentication.** The server trusts localhost: a Host allow-list, an Origin check, and a key
  setup route that refuses non-loopback clients unless `COCKPIT_LOOPBACK_PUBLISH=1` (set in
  `compose.yaml`, where clients arrive from a bridge address). Never expose the port.
  [SECURITY.md](../SECURITY.md) holds the threat model as built and what leaves the machine.
- **The voice director's column.** While it is open it sets `--director-inset` on the root element, and every
  Filmstrip surface keeps its right edge clear of `var(--director-inset, 0px)`: in-flow surfaces sit inside the
  project room's `.workspace`, which yields through `margin-right`; fixed layers yield through `right`; the verb
  card floats inside the work area (`.stage`); the picker's position subtracts the inset; the top bar and footer
  stay full width. A new surface must yield too.
- **One player for take audio.** All take audio plays through `audio/player.ts`, whose before-play listeners let
  the voice director mute its microphone first; never play take audio through another media element.
- **Store schema.** `db.py` upgrades an older store when it opens it (schema 13 today). Older code
  refuses a newer file, and a file older than schema 4 is refused, never migrated. Use a fresh
  `COCKPIT_DB` when you move back across a schema change. `docker compose down -v` deletes the
  `cockpit-data` volume, which holds the store and the staging folder. Compose names that volume
  after the project, which defaults to the checkout folder's name, so a checkout in another folder
  starts on a new, empty store; use `docker compose -p <name>` or `COMPOSE_PROJECT_NAME` to keep
  an existing one.
- **Restart.** Stopping the server counts as a crash, and `--reload` does it on every save. The next
  start recovers running rows: local takes reconcile through ComfyUI's history or the runner's job
  records; a hosted take in flight fails with an unverified charge, never resent.
- **pytest hang.** A run that hangs after its summary line left a store open: an aiosqlite
  connection is a non-daemon thread. Keep the cleanup paths in `app/main.py` and `app/wiring.py`.
- **Client tests.** vitest runs without globals, so call `cleanup()` in `afterEach` when a file
  renders more than once. jsdom has no canvas: never assert pixels there. The end-to-end store under
  `.staging/e2e-harness/` grows across runs, so specs use unique names, and they run with one
  worker. The canvas renders only visible cards: prove an off-screen card through the API.
- **Disk growth.** Nothing prunes the job folders under `tools/3d/runs/` (clear old ones by hand,
  never during a take) or the `cockpit-*` uploads in ComfyUI's input folder (safe to delete; every
  run uploads again). Staged region files are lineage and stay.
