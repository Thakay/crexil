# Crexil

Crexil is a local-first app that sends one brief to several AI models at once, hosted or on your own
GPU, to make images, voices, sound effects and 3D models. When a person picks a result, Crexil
records who picked it, what it was picked over and any reason given, so the decision is kept as well
as the output. It runs on one machine, for one user. The name stands for Creative Execution and
Iteration Ledger.

**[Watch the video](SEND_TIME_VIDEO_URL)** (2:29) · [The whole session](SEND_TIME_FULL_VIDEO_URL)
(8:25) · [How the agents work](https://crexil.thakay.dev/agents/) (interactive diagrams)

The video follows one game character, a dwarf blacksmith, as its voice is cast with the voice
director, an agent built on ElevenAgents. The whole session shows it at full length, in chapters.

## What it does

One brief goes to several models side by side. Images come from hosted models through OpenRouter
and from local diffusion models through ComfyUI. Voices come from ElevenLabs Voice Design, with lines
spoken by Eleven v4, and from Qwen3-TTS, an open-weight model on your own GPU. Sound effects come from
ElevenLabs Sound Effects, and 3D meshes from TRELLIS.2 and Hunyuan3D 2.1 on your GPU.

The results land in one grid whose cells never move. You compare them, blind if you want, pick one
with a short reason, and send it on to the next model (concept art to a 3D model, a voice to a
spoken line) or out to a hand-off folder as one character package laid out for game engines.

In a project, before a run goes out, it shows its price at published rates and what will leave the
machine. A run over its spending limit, or with a price Crexil can't know, is refused before
anything is sent. Every decision lands in an append-only record with its author and any reason
given, so you can still find out later who picked something and why. The older canvas view (`#/w`)
predates these rules. Its paid image runs have no spending limit, and its writes carry no delivery
id.

People and agents use the same actions. An MCP server lets Claude Code, or any MCP client, prepare
runs and propose picks, and the voice director does the same by conversation. Agents prepare and
propose; a project's spending limits, reveals and deliveries stay with a person, and the paid runs
the voice director prepares wait for a person's click, with the price on the button. Crexil has one
local user and no accounts, so each caller states its own name in the record, and these rules steer
agents rather than authenticate anyone.

ElevenLabs' terms travel with its results, so ElevenLabs audio never becomes another AI model's
input, and the run's drawer says why. What each model receives is in
[SECURITY.md](SECURITY.md#what-leaves-your-machine).

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/agentic-map/brief-to-delivery-dark.png">
  <img src="docs/agentic-map/brief-to-delivery-light.png" alt="From brief to delivery, where an agent stops. Any agent (the voice director, the MCP server or the CLI) creates a project, prepares a run and submits it. The Crexil server reviews the price, the token and every limit. A run over a cap, or at an unknown price, is refused and nothing is sent. Otherwise the run goes ahead when the agent may send it alone, or waits for the artist to confirm it with the price on the button. Engines run and the results land in the grid. An agent may propose a pick with a reason. The pick is the artist's click, since no agent tool offers one, and the server answers 403 to an agent that tries to reveal a blind run's engines or deliver.">
</picture>

One Python server, built on FastAPI, is the only writer. It owns one SQLite store, where every
change is an event with its author. The browser app, in React and TypeScript, the MCP server, the
command-line client and your own scripts all go through the same local HTTP API, so an agent goes
through the same reviews and spending limits as a person. Each provider sits behind an adapter, so a
new model from a provider Crexil already uses, making a kind of output it already handles, is
configuration. A new provider or a new kind of output needs an adapter. Local models take turns on
one GPU.

The voice director is an agent on ElevenAgents that talks with the artist through a panel in the
browser. The server asks ElevenLabs for a short-lived conversation token, so the API key never
leaves the server, and the agent works through six client tools that call the same API and are
recorded as `agent:voice-director`. No generated audio, file address or URL ever reaches the agent's
language model.

The [interactive agent map](https://crexil.thakay.dev/agents/) has this diagram and three more: who
talks to what, and the voice director's two sessions. Every box links to the lines of code it was
drawn from. [docs/agentic-map](docs/agentic-map/README.md) is its written reference, and
[docs/agents.md](docs/agents.md) is the HTTP contract for wiring Crexil into a pipeline or an agent.

## Design note: a paid call goes out at most once

Generation APIs charge per call, and answers get lost: a timeout, a dropped connection, a server
restart while a call is out. Sending the call again can charge twice. Giving up throws away work
that was paid for and leaves the cost on screen wrong. Crexil's rule is that a paid call goes out at
most once. If its answer is lost, Crexil tries to recover the result. If it can't be sure, it marks
the charge as not confirmed, keeps the money reserved, and never sends the call again on its own.

- **Intent before money.** Right before a paid call, one transaction rechecks the attempt, any
  cancel and every spending limit, then records that the call is going out. After a restart, an
  attempt without that record settles at a known zero, and one with it is never sent again
  (`cockpit/server/app/scheduler.py`, `_collecting_request` and `_recover`).
- **Recover, never regenerate.** A lost ElevenLabs speech result is found again in the account's
  history, which is why logging stays on. OpenRouter's kept response and the local voice runner's
  job id do the same for theirs (`cockpit/server/app/elevenlabs.py`, `_recover_speech`). An account
  with zero retention keeps no history, so there a lost speech answer stays unconfirmed.
- **When unsure, bind nothing.** If two history items could match, or another attempt could own the
  match, nothing is bound and the charge stays unconfirmed (`_own_take`). A lost result is better
  than a result charged twice.
- **Every project write is idempotent.** Each request carries a delivery id, and the server keeps a
  fingerprint of its body with the receipt, which commits with the work, so a submit whose answer
  was lost replays the same run (`cockpit/server/app/crexil_store.py`, `command`).
- **Money is checked before it moves:** at review, at submit, at dispatch and right before the send
  (`cockpit/server/app/crexil_budget.py`).
- **Plain HTTP, no SDK in between,** so every retry is one the code decides.

This has trade-offs. A lost result may stay uncollected rather than risk a double charge, speech
stays in the account's ElevenLabs history so it can be recovered, and one process is the only writer
of one SQLite store, so each guarantee is a transaction. That also means one user on one machine.

The spending limit works on published prices, while the provider bills what it measures; the
record keeps both. Voice Design and Sound Effects keep no history at ElevenLabs, so a lost answer
there stays unconfirmed, and so does an OpenRouter answer lost before it arrives. An idempotency key
on those ElevenLabs calls, or the create-then-get lifecycle its Flows generation APIs already have,
would let a client fetch such an answer instead of paying for it again.

The tests script each failure against fakes, never a live provider: lost answers, ambiguous
history, restarts mid-call, replayed submits, limits lowered mid-send. To read the code behind this,
start with `cockpit/server/app/scheduler.py` (its docstring, then `_recover`) and
`cockpit/server/app/elevenlabs.py` (its guarantees, then `_own_take`), then the tests in
`cockpit/server/tests/test_scheduler.py` and `cockpit/server/tests/test_audio_hardening.py`.

## Quick start

You need Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node with npm. No GPU, model or API
key is needed to run the checks or to look around.

```bash
(cd cockpit/server && uv sync)
(cd cockpit/web && npm ci)
scripts/check.sh          # ruff, pytest, vitest and the TypeScript checks
```

To see it run on throwaway data, with no model, no key and no folder of yours:

```bash
(cd cockpit/web && VITE_CREXIL_FRONT_DOOR=projects npm run build)
mkdir -p /tmp/crexil-dev/handoff
cd cockpit/server
COCKPIT_DB=/tmp/crexil-dev/cockpit-v4.sqlite COCKPIT_STAGING_DIR=/tmp/crexil-dev/staging \
  PROMOTE_DIR=/tmp/crexil-dev/handoff PROMOTE_ROOTS=/tmp/crexil-dev \
  COMFYUI_URL=http://127.0.0.1:9 THREED_URL=http://127.0.0.1:9 VOICE_RUNNER_URL=http://127.0.0.1:9 \
  OPENROUTER_API_KEY= ELEVENLABS_API_KEY= \
  COCKPIT_STATIC_DIR=../web/dist uv run uvicorn app.main:app --port 8001
# then open http://localhost:8001
```

With no model there is nothing to generate with, and `/api/health` answers 503 by design.

To make real results, copy `.env.example` to `.env` and give the installation its models and keys:
`OPENROUTER_API_KEY` for hosted images, `ELEVENLABS_API_KEY` for ElevenLabs voices and sounds,
`VOICE_RUNNER_URL` for the local voice runner ([tools/tts](tools/tts/README.md)), `THREED_URL` for
the 3D runner and `COMFYUI_URL` for ComfyUI. Deliveries land in `PROMOTE_DIR`, by default
`crexil-handoff` in your home folder, which you create. The voice director needs an agent on
ElevenAgents and a minutes cap. `tools/voice-director/apply.py` creates the agent from `agent.json`
and prints the id to put in `ELEVENLABS_VOICE_AGENT_ID`. While `CREXIL_VOICE_MINUTES_CAP` is 0, the
default, no session starts. The steps are in [tools/voice-director](tools/voice-director/README.md).
`scripts/seed_demo_project.py` builds an example project through the HTTP API. It reviews every run
and prints its price, and it spends only with `--live` and the exact total it reviewed.

The [developer guide](docs/development.md) covers the environment variables, the models, the
browser suites and the known traps.

## Principles

[docs/PRD.md](docs/PRD.md) holds 23 binding laws, and the code cites them as `law N`. These six
shape it most:

- **1.** One dead engine never stops the others.
- **9.** Nothing retries silently.
- **15.** Crexil never acts unasked, and never generates, judges, picks or alters a prompt on its own.
- **19.** The server is the only writer.
- **22.** Money never moves without a cap, and an unknown price counts as over it.
- **23.** Every decision has an author, and delivery is a human act.

## Stack and tests

| Part | Built with |
|---|---|
| Server | Python 3.12, FastAPI and SQLite, with one append-only event record; uv |
| Client | React, TypeScript and Vite; three.js for meshes; the ElevenLabs React SDK for the voice director |
| Models | ComfyUI and OpenRouter for images; ElevenAPI for voices and sounds, over plain HTTP so no SDK retries a paid call; a small runner for Qwen3-TTS; a runner for the 3D models |
| Agents | An MCP server over stdio (`tools/agent-door/`) and the voice director on ElevenAgents (`tools/voice-director/`) |

`scripts/check.sh` runs ruff, the server and client tests (pytest and vitest) and the TypeScript
checks, with no GPU, model or key, since models are faked or scripted. GitHub runs it on every push to
`main`. `scripts/e2e.sh` runs the Playwright browser suites offline against scripted models, in a
loopback-only network namespace where the kernel allows one.

## Project layout

| Path | What it holds |
|---|---|
| `cockpit/server` | The server: the scheduler, the record, the model adapters and the HTTP API |
| `cockpit/web` | The client, with its unit tests (vitest) and browser tests (Playwright) |
| `cockpit/openapi.json` | The committed API contract; the client's types are generated from it |
| `configs/` | Model and dial tables served to the client, plus example host configuration |
| `presets/`, `workflows/` | Presets and the ComfyUI reference graphs they bind to (adding one is configuration, never code) |
| `providers.json` | Pins and price notes for hosted image models |
| `tools/tts/` | The local voice runner for Qwen3-TTS |
| `tools/3d/` | The 3D lane: a small runner and wrappers around separately installed 3D models |
| `tools/agent-door/`, `tools/voice-director/` | The MCP server, and the voice director's agent configuration |
| `scripts/` | Checks, type generation, launchers, the CLI, the example project seed, the model downloader, benchmark and smoke tools |
| `bench/` | Fixed benchmark inputs: briefs and region-edit cases |
| `snapshots/pins.json` | The pinned versions of the ComfyUI runtime and its node packs |
| `docs/` | The product contract and laws, the failure catalogue, the studio and developer guides, the agent map, third-party terms |

Read before changing behavior: [docs/PRD.md](docs/PRD.md) (the product model and the laws),
[docs/failure-cases.md](docs/failure-cases.md) (the failure cases the code cites, such as `A1` or
`C2`), [docs/agents.md](docs/agents.md) (wiring Crexil into a studio pipeline or an agent),
[SECURITY.md](SECURITY.md) (the threat model and what leaves your machine), and
[CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) (conventions for people and for coding
agents).

## Status and limits

- **Single user, local first.** The server binds to localhost and has no accounts. Read
  [SECURITY.md](SECURITY.md) before you expose it anywhere.
- **Developed on Linux under WSL2** with one NVIDIA GPU. Other platforms are untested.
- **There is no installer yet.** The checks, the build and an isolated instance run from a clean
  checkout; the engines and models are separate installations.
- **Engines and models are not included.** ComfyUI, the voice and 3D engines and every model
  weight have their own licences. Some weights that the reference graphs and the 3D lane use are
  non-commercial or limited by territory, so read [docs/third-party.md](docs/third-party.md) before
  any commercial use.

This repository starts where Crexil was split from the private repository it grew up in, so its
first commits add the existing app area by area. It is built with AI coding agents under the laws in
`docs/PRD.md` and the failure catalogue in `docs/failure-cases.md`, and every commit that changes
the code carries a `Co-authored-by` trailer for the tools that worked on it.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how to propose a change.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party engines, models and services keep
their own terms: [docs/third-party.md](docs/third-party.md).
