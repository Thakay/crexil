# Crexil

Crexil is a bench for AI-made media that keeps the decision, not only the output: which take won,
what it beat, who chose it, and why.

**[Watch the 90-second demo](SEND_TIME_VIDEO_URL)** · [The companion page](SEND_TIME_PAGE_URL): three
blind A/B casts to try, each with its reveal.

## What it is

One brief goes to many engines at once. Images come from hosted models through OpenRouter and from
local diffusion models through ComfyUI. Voices come from ElevenLabs Voice Design and from Qwen3-TTS,
an open-weight model on your own GPU. Sound effects come from ElevenLabs, and meshes from TRELLIS.2
and Hunyuan3D 2.1 on your GPU. Every take lands in one grid whose cells never move. You audition or
compare the takes, blind if you want, pick one with a one-line reason, and send it on: to another
engine, or out to a hand-off folder as a character package for a game team.

Every act lands in an append-only record with its author, and a pick with the artist's reason, so
later the record still answers why each asset exists. People and agents use the same actions. An MCP server
lets Claude Code, or any MCP client, prepare runs and propose picks, and the voice director, a voice
agent built on ElevenAgents, does the same by conversation. Agents prepare and propose; spending
limits, reveals and deliveries stay the artist's, and the voice director's paid runs wait for the
artist's click.

Crexil is the front door and the record, never the workbench. It is not a node-graph editor, a chat
app or a DCC tool.

## What the demo shows

The demo follows one game character, a dwarf blacksmith, through one project.

| Journey | What happens |
|---|---|
| Image casting | One character brief goes to three hosted image models. The takes land in a frozen grid, and the artist picks one with a reason. |
| Voice casting | One voice description goes to ElevenLabs Voice Design and to Qwen3-TTS on the local GPU. The takes play blind, at matched loudness, from the keyboard. The artist picks one with a reason, then reveals which engine made each take. |
| Sound | Footstep and forge-strike variations from ElevenLabs Sound Effects land as audio takes. |
| 3D | A chosen image becomes a mesh with TRELLIS.2 on the local GPU. |
| The record | The Filmstrip shows the character's image, voice, sound and mesh as one family. Each take shows its prompt as typed and as sent, its engine, seed, cost, reason and author, and search finds a pick by its reason. |
| Delivery | The chosen takes land in the hand-off folder as one character package: art, mesh, voice lines and sound effects, a manifest beside every file, a record summary and the decision record. |
| Guardrails | A run over its spending limit is refused before any paid call, and an unknown price counts as over the limit. When one engine fails, the others still land, and the failed cell says whether money moved. |
| The agent door | The voice director prepares runs from what the artist says, and a paid run waits for the artist's click, with its price on the button. Claude Code, through the MCP server, prepares and submits runs within its door's ceiling ($0 unless the artist raises it) and proposes picks with reasons. |

ElevenLabs' terms travel with its takes: an ElevenLabs take never becomes another AI engine's input,
and the run's drawer says why. What each engine receives is in
[SECURITY.md](SECURITY.md#what-leaves-your-machine).

## Run it

You need Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node with npm. No GPU, engine or API
key is needed to run the checks or to look around.

```bash
(cd cockpit/server && uv sync)
(cd cockpit/web && npm ci)
scripts/check.sh          # ruff, pytest, vitest and the TypeScript checks
```

To see it run on throwaway data, with no engine, no key and no folder of yours:

```bash
(cd cockpit/web && npm run build)
mkdir -p /tmp/crexil-dev/handoff
cd cockpit/server
COCKPIT_DB=/tmp/crexil-dev/cockpit-v4.sqlite COCKPIT_STAGING_DIR=/tmp/crexil-dev/staging \
  PROMOTE_DIR=/tmp/crexil-dev/handoff PROMOTE_ROOTS=/tmp/crexil-dev \
  COMFYUI_URL=http://127.0.0.1:9 THREED_URL=http://127.0.0.1:9 VOICE_RUNNER_URL=http://127.0.0.1:9 \
  OPENROUTER_API_KEY= ELEVENLABS_API_KEY= \
  COCKPIT_STATIC_DIR=../web/dist uv run uvicorn app.main:app --port 8001
# then open http://localhost:8001
```

With no engine there is nothing to generate with, and `/api/health` answers 503 by design.

To make real takes, copy `.env.example` to `.env` and give the installation its engines and keys:
`OPENROUTER_API_KEY` for hosted images, `ELEVENLABS_API_KEY` for ElevenLabs voices and sounds,
`VOICE_RUNNER_URL` for the local voice runner ([tools/tts](tools/tts/README.md)), `THREED_URL` for
the 3D runner and `COMFYUI_URL` for ComfyUI. Deliveries land in `PROMOTE_DIR`, by default
`crexil-handoff` in your home folder, which you create. The voice director needs an agent on
ElevenAgents and a minutes cap. `tools/voice-director/apply.py` creates the agent from `agent.json`
and prints the id to put in `ELEVENLABS_VOICE_AGENT_ID`. While `CREXIL_VOICE_MINUTES_CAP` is 0, the
default, no session starts. The steps are in [tools/voice-director](tools/voice-director/README.md).
`scripts/seed_demo_project.py` builds the demo's project through the HTTP API: it reviews every run
and prints its price, and it spends only with `--live` and the exact total it reviewed.

The [developer guide](docs/development.md) covers the environment variables, the engines, the browser
suites and the known traps.

## The laws

[docs/PRD.md](docs/PRD.md) holds 23 binding laws, and the code cites them as `law N`. In one line each:

1. One dead engine never stops the others, and one cause gives one message.
2. No modals: nothing blocks the canvas or the queue.
3. No status codes, "something went wrong" or stack traces in the primary message.
4. Every failure offers the next move as a button.
5. The cost line states time for local work and dollars for hosted work, including "you weren't
   charged" where money was in play.
6. The costed action is never the default button, and its price is on its face.
7. Nothing is silently lost: purge is the only delete, and it is held and explained.
8. Grid geometry is frozen at Run.
9. Nothing retries silently.
10. The queue pauses only when every remaining slot is sure to fail.
11. Three input triggers only, and no hover toolbars.
12. Badge, never filter: an engine that cannot do something stays visible, with the reason.
13. One GPU means local engines take turns, and every surface with a queue says so.
14. One heavy local job at a time.
15. Crexil never acts unasked: it never generates, judges, picks or alters a prompt on its own.
16. The front door and the record, never the workbench.
17. Observed, never predicted.
18. Secrets never live in the store.
19. The server is the only writer.
20. A new medium is an adapter: a payload, its engines, a body, an inspection surface, a delivery
    convention and its verbs, and nothing else.
21. Compatibility is by type, never by provider.
22. Money never moves without a cap, and an unknown price counts as over it.
23. Every decision has an author, a pick carries a reason, and delivery is a human act.

## Stack and tests

| Part | Built with |
|---|---|
| Server | Python 3.12, FastAPI and SQLite, with one append-only event record; uv |
| Client | React, TypeScript and Vite; three.js for meshes; the ElevenLabs React SDK for the voice director |
| Engines | ComfyUI and OpenRouter for images; ElevenAPI for voices and sounds, over plain HTTP so no SDK retries a paid call; a small runner for Qwen3-TTS; a runner for the 3D engines |
| Agent door | An MCP server over stdio (`tools/agent-door/`) and the voice director on ElevenAgents (`tools/voice-director/`) |

`scripts/check.sh` runs ruff, more than 2,000 server and client tests (pytest and vitest) and the
TypeScript checks, with no GPU, engine or key: engines are faked or scripted. `scripts/e2e.sh` runs
the Playwright browser suites offline against scripted engines, in a loopback-only network namespace
where the kernel allows one.

## What is here

| Path | What it holds |
|---|---|
| `cockpit/server` | The server: the scheduler, the record, the engine adapters and the HTTP API |
| `cockpit/web` | The client, with its unit tests (vitest) and browser tests (Playwright) |
| `cockpit/openapi.json` | The committed API contract; the client's types are generated from it |
| `configs/` | Engine and dial tables served to the client, plus example host configuration |
| `presets/`, `workflows/` | Presets and the ComfyUI reference graphs they bind to (adding one is configuration, never code) |
| `providers.json` | Pins and price notes for hosted image models |
| `tools/tts/` | The local voice runner for Qwen3-TTS |
| `tools/3d/` | The 3D lane: a small runner and wrappers around separately installed 3D engines |
| `tools/agent-door/`, `tools/voice-director/` | The MCP server, and the voice director's agent configuration |
| `scripts/` | Checks, type generation, launchers, the CLI, the demo seed, the model downloader, bench and smoke tools |
| `bench/` | Fixed bench inputs: briefs and region-edit cases |
| `snapshots/pins.json` | The pinned versions of the ComfyUI runtime and its node packs |
| `docs/` | The product contract and laws, the failure catalogue, the studio and developer guides, third-party terms |

Read before changing behavior: [docs/PRD.md](docs/PRD.md) (the product model and the laws),
[docs/failure-cases.md](docs/failure-cases.md) (the failure cases the code cites, such as `A1` or
`C2`), [docs/agents.md](docs/agents.md) (wiring Crexil into a studio pipeline or an agent),
[SECURITY.md](SECURITY.md) (the threat model and what leaves your machine), and
[CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) (conventions for people and for coding
agents).

## Status

- **Single user, local first.** The server binds to localhost and has no accounts. Read
  [SECURITY.md](SECURITY.md) before you expose it anywhere.
- **Developed on Linux under WSL2** with one NVIDIA GPU. Other platforms are untested.
- **There is no installer yet.** The checks, the build and an isolated instance run from a clean
  checkout; the engines are separate installations.
- **Engines and models are not included.** ComfyUI, the voice and 3D engines and every model weight
  have their own licences. Some weights that the reference graphs and the 3D lane use are
  non-commercial or limited by territory: read [docs/third-party.md](docs/third-party.md) before any
  commercial use.

Built for my own use. Issues are read, and pull requests are reviewed when I can.

## History

This repository starts at the point where Crexil was separated from the private repository it grew
up in. The earlier history is not included, and the first commits add the existing application area
by area rather than replaying how it was written. I built Crexil with AI coding tools, and the history
says so: every commit that changes the code carries a `Co-authored-by` trailer for the tool that
worked on it, Claude on each of them, and Codex as well on the commit that imported the project
surface.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party engines, models and services keep
their own terms: [docs/third-party.md](docs/third-party.md).
