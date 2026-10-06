# Agents and integrations

This guide is for anyone wiring Crexil into a studio pipeline or letting an agent work in it:
a tools engineer, a pipeline TD, or an AI agent's operator. It covers the HTTP contract, the
command-line client, the MCP server for agents, what lands in the delivery folder, and how to
recover from errors.

Crexil is a local server that owns one append-only record. The artist's interface, the CLI and
the agent door are all clients of the same shared actions. Each action is recorded with its
author, so the record shows what a person did and what an agent did. The product contract and
its laws are in [PRD.md](PRD.md).

## Run the server

Start the server as the [developer guide](development.md#3-commands) shows. By default it serves
`http://127.0.0.1:8000`. The installation's keys and folders live in `.env` at the checkout root,
never in the store or the repository.

The server answers loopback addresses only. A request whose `Host` is not a loopback name is
refused, and so is any request carrying a foreign `Origin`. That keeps a web page in a browser
tab from driving a local server that can spend money.

## The HTTP contract

`cockpit/openapi.json` is the contract. The shared actions live under `/api/crexil/`. Four rules
cover every write.

**An author on every write.** A write carries `actor: {"kind": "human" | "agent", "name": ...}`.
On one machine with one user, the actor is what the client states. The CLI makes you name it,
and the MCP door always writes as `agent:<name>`.

**A delivery id on every write.** A write also carries `request_id`, 8 to 100 characters. The
same id with the same body returns the saved answer and does nothing twice. The same id with a
different body is refused with `409`. If a response is lost, send the identical request again
with the same id.

**Revisions.** A write that changes existing state carries the `revision` you last read. A stale
revision is refused with `409`: read again, then decide.

**Review, then submit.** Work that may spend money runs in two steps:

1. Create or revise a draft run: `POST projects/{id}/directions`, or `PATCH directions/{id}`.
2. Read its review with `GET directions/{id}/review`. The review has no side effects. It answers
   whether the run can go, why not, what it reserves, and a `token` that binds exactly what will
   run and what it may cost.
3. Submit with `POST directions/{id}/submit` and `{"revision": ..., "review_token": ...}`.

Submit refuses a request that changed since its review with `409`. A `409` that carries the
header `X-Crexil-Conflict: spending` means other work's spending or the limits moved since the
review. Review again and submit with the new token. When only other work's spending moved and
the run still fits its limits, submit accepts it without a new review.

**Caps.** Every run states a spending limit, and the project has its own. A run whose known
prices exceed either is refused before any engine is called. An unknown price counts as over the
limit, never as zero. Free local engines reserve nothing.

### What an agent may and may not do

| Action | Agent | Notes |
|---|---|---|
| Discover engines, read projects, runs and the record | yes | `GET operations`, `GET projects/{id}`, `GET directions/{id}/run`, `GET projects/{id}/record` |
| Create a project, prepare and review a run | yes | |
| Submit a run | yes | Within the run's own limit and the project's |
| Propose takes or comment on a run, with a reason | yes | `POST projects/{id}/assessments`. Never changes the pick |
| Set the displayed selection | through HTTP only | The MCP door leaves picking to the artist |
| Reveal a blind run | no | `403`. Revealing would end the artist's blind comparison |
| Prepare or transfer a delivery | no | `403`. Delivery is the artist's act. Reviewing a delivery is open to agents |
| Start a voice session | no | `403` |

Agent-facing reads carry facts, ids, labels and reasons. They never carry a take's bytes or an
address to fetch them. `GET directions/{id}/run` is the compact read of one run, made for agents.
It holds no file addresses or waveform data, and a blind run names no engine until the artist
reveals it. ElevenLabs' terms forbid using its audio as input to any machine learning, and the
model behind an agent is one, so no agent tool hands audio to a model.

## The command-line client

`scripts/crexil.py` is a thin client of the same actions. It reads the server address from
`--url` or `CREXIL_URL`, and every change needs a participant through `--actor` or `CREXIL_ACTOR`.

```sh
export CREXIL_URL=http://127.0.0.1:8000 CREXIL_ACTOR=agent:Pipeline
scripts/crexil.py get operations
scripts/crexil.py post projects --json '{"name": "Dwarf blacksmith", "brief": "A forge-level smith"}'
scripts/crexil.py get directions/RUN/review
scripts/crexil.py post directions/RUN/submit --json '{"revision": 0, "review_token": "TOKEN"}'
scripts/crexil.py decisions PROJECT                 # the decision record, one JSON line per decision
```

Each change prints its delivery id. After a connection failure, retry with `--request-id` and
the same body.

## The MCP server

`tools/agent-door/crexil_mcp.py` is an MCP server over stdio that lets Claude Code, or any MCP
client, work through the same actions. Register it from the checkout root:

```sh
claude mcp add --transport stdio crexil -- uv run --script "$PWD/tools/agent-door/crexil_mcp.py" --agent-name Claude --max-run-usd 0
```

`--max-run-usd` is the door's own ceiling per run, on top of the server's limits. At `0`, the
agent can only run free local engines. A paid submission must also restate the amount its review
reserves. The [door's README](../tools/agent-door/README.md) lists the tools, flags and tests.

Door errors give the server's plain line, then the next move, then a stable kind:

| Kind | Meaning | Next move |
|---|---|---|
| `unreachable` | The server did not answer | Start it. For a write, replay with the named delivery id |
| `unavailable` | The server or an engine is starting or paused | Try again shortly |
| `not_found` | An id is unknown | Read again for current ids |
| `conflict` | Something changed since it was read, or spending moved since review | Read or review again |
| `refused` | The server refused the request; nothing was recorded or sent | Fix what the line names |
| `not_allowed` | The act is the artist's | Propose instead |
| `not_built` | The feature is not in this version | |
| `over_door_ceiling` | The run would spend more than this door allows | Ask the artist |
| `invalid_input` | The arguments do not describe a request | Fix the arguments |
| `server_error` | Crexil failed on its side and recorded it | Try again, or ask the artist |

## Worked example: casting a voice

An agent drafts voices for a character on the free local voice engine and recommends one. The
artist listens, picks and delivers. With the door registered at a `0` ceiling, the agent calls:

1. `crexil_list_engines` with `{"payload": "audio", "action": "voice-design"}`. The local engine,
   `audio:voice-design:qwen3-tts`, runs on this machine, sends nothing out and costs nothing.
2. `crexil_create_project` with `{"name": "Dwarf blacksmith", "brief": "A gruff smith for the forge level"}`.
3. `crexil_prepare_run`, which drafts and reviews the run without running it:

   ```json
   {"project_id": "PROJECT", "action": "voice-design",
    "prompt": "A gruff dwarf blacksmith, low and warm, a little out of breath",
    "engines": [{"id": "audio:voice-design:qwen3-tts", "count": 2,
                 "controls": {"preview_text": "Steel remembers every strike, and so do I. ..."}}],
    "spending_limit_usd": 0}
   ```

   The review answers `ready: true`, `reserved_usd: 0`, with a `review_token`.
4. `crexil_submit_run` with the run id, its revision, the token and `confirm_reserved_usd: 0`.
5. `crexil_get_run` with `{"wait_seconds": 55}`, until `settled` is true. The run's six takes,
   `0A` to `0B.3`, each carry their length, loudness and preview words.
6. `crexil_propose` with `{"take_ids": ["TAKE"], "reason": "Warmest growl, clearest consonants"}`.
   This records the agent's assessment. The artist's pick stays empty until the artist picks.

The artist then auditions the takes in Crexil, picks one with a reason, and delivers it. A
later run can `speak` the character's lines with that voice: pass the picked take as the run's
source, whose role is `voice`.

## Delivery and its files

Delivery copies each chosen take into the installation's hand-off folder, `PROMOTE_DIR` in
`.env`. It writes `<asset_id>.<ext>` beside its manifest `<asset_id>.json`, under
`<project-slug>/`, with meshes under `_3d/<project-slug>/`. Each item succeeds or fails on its
own, and a retry reconciles the same assets rather than copying them twice.

The manifest is version 4, a frozen public format. Changes to it are additive and versioned:
see the manifest paragraphs in [the product model](PRD.md#2-the-product-model). It names the
take, its lineage, the engine, the seed, the prompt as typed and as submitted, model files by
hash, and the cost.

Two exports summarize a project:

- `GET projects/{id}/deliveries.csv` has one row per delivered asset: asset id, take, engine,
  prompt, parent takes, delivery time, destination and delivery id.
- `GET projects/{id}/decisions`, or `decisions.jsonl` for one JSON line per decision, is the
  decision record. Each line says what was chosen or assessed, among which takes (with engine,
  provider, model, seed and cost), the reason, the author, whether the run was blind, and when.
  It is the artist's account of decisions, never a dataset, and it holds no media.

## Pipelines

- **Engine import.** Point a Unity or Unreal importer at the hand-off folder. Every asset
  arrives with its manifest, so the importer can stamp source, engine and seed into its asset
  metadata.
- **Asset tracking.** Read manifests into your asset system to keep lineage and model hashes with
  each asset. The decision record and `deliveries.csv` explain why each asset exists.
- **Reviews.** Agents can draft and propose while the artist keeps the decisions. Every proposal
  arrives with its reason, and the record shows whose it was.

## Errors and recovery

- **A lost answer.** Replay the same write with the same delivery id. The server answers with
  the work it already did and never does it twice.
- **A late or missing output.** A cell marked `collectable` in the run view can be collected
  free with `POST attempts/{slot}/collect`. Collection retrieves the existing attempt and never
  generates again.
- **A failed cell.** Its `kind` and plain `cause` say what happened, once per cause, and the run
  view's `summary` names each cause once. Retries the scheduler makes itself are visible on the
  cell.
