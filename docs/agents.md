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
limit, never as zero, so a run that includes an engine with an unknown price is refused whole:
nothing in it is sent. Free local engines reserve nothing. Only the artist changes a project's
or a sequence's limits.

### What an agent may and may not do

| Action | Agent | Notes |
|---|---|---|
| Discover engines, read projects, runs and the record | yes | `GET operations`, `GET projects/{id}`, `GET directions/{id}/run`, `GET projects/{id}/record` |
| Create a project, prepare and review a run | yes | |
| Submit a run | yes | Free work anywhere; work that reserves money only in a project with a spending limit the artist set (`403` otherwise), within the run's own limit and the project's |
| Create a sequence | default limits only | `403` when its limits differ from the default (16 attempts, $0, hard cap) |
| Pause, resume or cancel a project, run or sequence | pause and cancel; resume free work only | `POST execution/{scope}/{id}`. `403` when the resume would send held attempts that reserve money, let a paused sequence that may spend send its next runs, or send a hidden run's attempts |
| Change a project's or a sequence's spending limits | no | `403`. A limit decides how much money may move. Other project edits stay open to agents |
| Cancel one attempt | yes | `POST attempts/{slot}/cancel` with the attempt number it saw. Stopping never spends; a sent paid request keeps its cell and its charge facts |
| Resume the engine that holds an attempt | free work only | `POST attempts/{slot}/resume`. `403` when the held attempts it would send reserve money: resuming paid work is the artist's act. Any attempt of a hidden run, whatever its price, answers `403` in one neutral line |
| Propose takes or comment on a run, with a reason | yes | `POST projects/{id}/assessments`. Never changes the pick |
| Set the displayed selection | through HTTP only | The MCP door leaves picking to the artist |
| Reveal a blind run | no | `403`. Revealing would end the artist's blind comparison |
| Prepare or transfer a delivery | no | `403`. Delivery is the artist's act. Reviewing a delivery is open to agents |
| Start a voice session | no | `403` |
| End a voice session | no | `403`. The artist who started it ends it; its minutes are at least the server's own time since the start, at most the session maximum |

Agent-facing reads carry facts, ids, labels and reasons. They never carry a take's bytes or an
address to fetch them. `GET directions/{id}/run` is the compact read of one run, made for agents.
It names takes as the grid does: the run's number, the cell's letters, and for an attempt's later
takes their number from 2, such as `3B` or `3B.2`. A take is counted among its attempt's takes only,
never its receipt or companion files, so a hosted engine's first take reads `3B` as a local one's
does. Cells past `Z` continue as `3AA`, `3AB` and on.
It holds no file addresses or waveform data, and a blind run names no engine until the artist
reveals it. ElevenLabs' terms forbid using its audio as input to any machine learning, and the
model behind an agent is one, so no agent tool hands audio to a model. While any attempt of a blind run is
queued, running or late, every cell of that run reads `running` with no takes, and the summary counts its
attempts done; a hidden take's loudness waits for the reveal.

A blind run compares at least two engines and is sent whole or not at all. Until the artist
reveals it, its takes show only the run's creation time, and continuing from or reusing one of
its takes is refused with `422`: reveal first.

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

`scripts/seed_demo_project.py` is a worked example of the same client: it carries one character brief
through image, voice, sound and 3D runs, prints each run's reviewed price before it sends anything, and
spends only with `--live` and the exact total it reviewed.

Each change prints its delivery id. After a connection failure, retry with `--request-id` and
the same body.

## The MCP server

`tools/agent-door/crexil_mcp.py` is an MCP server over stdio that lets Claude Code, or any MCP
client, work through the same actions. Register it from the checkout root:

```sh
claude mcp add --transport stdio crexil -- uv run --script "$PWD/tools/agent-door/crexil_mcp.py" --agent-name Claude --max-run-usd 0
```

`--max-run-usd` is the door's own ceiling per run, on top of the server's limits. It compares
money, not where an engine runs: at `0` the agent can run only work whose review reserves nothing.
Today that is the local engines, but a hosted model that the live catalogue prices at $0 would pass
the ceiling and send its prompt off the machine. A paid submission must also restate the amount its
review reserves. A refusal over the ceiling names the flag. Without a `payload` or an `action`,
`crexil_list_engines` names each engine by its id, label, actions, payload, price with its unit and
readiness, small enough for any client's tool output; with one, it adds where each engine runs, what
leaves the machine, how it is charged, its sources and its controls. The [door's README](../tools/agent-door/README.md) lists the tools, flags and tests.

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
| `over_door_ceiling` | The run would spend more than this door allows | Ask the artist, who can raise `--max-run-usd` |
| `invalid_input` | The arguments do not describe a request | Fix the arguments |
| `server_error` | Crexil failed on its side and recorded it | Try again, or ask the artist |

## The voice director

The voice director is the second agent door: an ElevenLabs Agents conversation in a column at the right of
the project surface, opened from `VOICE DIRECTOR ›` in the top bar and configured in `tools/voice-director/`. Its tools call the same shared actions as
`agent:voice-director`; a run starts on its request only when it is free and every engine it chose runs on
this machine, and any other run, and a pick, wait for the artist's click. Under Docker, `compose.yaml` points the
runtimes at `host.docker.internal`, which counts as off this machine, so the director's local runs wait for the click
there too. During a session the microphone
audio and what the director reads about the project (the brief, run prompts, take labels and reasons) go to
ElevenLabs and the agent's language model, and ElevenLabs keeps each conversation for 7 days. Take audio never
does: the microphone mutes before any take plays.

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

The artist then auditions the takes in the run's grid room (a digit plays a take of the run that holds focus, else of the
row's first audio run; Space pauses; X switches two marked takes), selects one with S and a reason, reveals a blind run, and delivers it. A
later run can `speak` the character's lines with that voice: pass the picked take as the run's
source, whose role is `voice`. One run speaks one line, so each line, and each voice, gets its own
run. To keep a character's runs together on the Filmstrip, create the sections that start from its
words with one `row_idx`, and a continuation with the `row_idx` after its source's row.

## Delivery and its files

Delivery copies the chosen takes into the installation's hand-off folder, `PROMOTE_DIR` in
`.env` (unset, `crexil-handoff` in the user's home folder; the server never creates it), as one
character package: a folder named after the project and the delivery, such as
`dwarf-blacksmith_3f9a0c1d2e4b/`. Each take lands in its medium's folder: `Art/` for images,
`Models/` for meshes as GLB, `Audio/Voice/` for spoken lines, `Audio/Voice/Previews/` for voice
design previews and `Audio/SFX/` for sound effects. A take is named by its grid label and its own
words, such as `Audio/Voice/3A_bring-me-the-ore.wav`, and its manifest sits beside it under the
same name, `3A_bring-me-the-ore.json`. The words are the line it speaks, else its prompt, else its
source's prompt. Material from no run, such as an import, is named `import-`, the start of its id
and its file name. Names hold only letters, digits, hyphens and one underscore, so Unity, Unreal
Engine and Godot import them as they are, and a take keeps its name in every delivery.

Original bytes are copied exactly. Where an engine cannot import an image's format, a PNG of its
pixels lands beside the original: Unity and Unreal Engine import no WebP, and Godot imports no
TIFF. Nothing else is converted: PNG, JPEG, 16-bit PCM WAV and GLB import in all three, Unity
reading GLB through Unity's glTFast package. A source or another take of the same attempt that
the package also delivers is referenced at its package path, never copied twice. What no game engine
imports stays in the store and keeps its record in the manifest, its SHA-256 and size with `file` null,
without its bytes: another take of that attempt that the package leaves out, the take's provider
receipt, and a mesh's voxel field, which the 3D engine keeps for re-extraction. A base64 copy of media
inside a manifest, such as a hosted image in its provider's answer or a source in the request, reads as
`{"omitted": "base64", "sha256": ..., "size_bytes": ...}` (with `media_type` for a `data:` URL), so each
file travels once. Sources outside the package are copied into `<name>_inputs/`, and a take's other output
files into `<name>_outputs/`. The store and its backups keep every file and record whole.

Once every take is in place, three files land at the package's root, `package.json` last, so a
package with `package.json` is whole:

- `records.csv`, the record summary: one row per take with its asset id, label, role, path,
  take, engine, prompt, parent takes, SHA-256, delivery time and delivery id.
- `decisions.json`, the decision record for these takes, in the `decisions` export's shape. It
  holds no audio and no file paths.
- `package.json`, every other file with its role, path, SHA-256 and size. A take's own file also
  names its label, asset id, payload, verb, media type and manifest; a conversion names its
  original and the engines that need it; `not_delivered` lists the takes the review refused,
  with the reason. Its `package_version` is 2; it changes whenever what a package holds changes.

Each take succeeds or fails on its own. A retry reconciles the same names and never copies a
file twice, and a file already in place with other bytes is left untouched. A take of a blind run
is refused until the run is revealed. Godot reads every `.csv` as a translation table, so in
Godot set `records.csv` to Keep File, or leave it out of the project.

The manifest is version 4, a frozen public format. Changes to it are additive and versioned:
see the manifest paragraphs in [the product model](PRD.md#2-the-product-model). It names the
take, its lineage, the engine, the seed, the prompt as typed and as submitted, model files by
hash, and the cost. An audio take's manifest adds `audio`: its container, codec, sample rate,
channels, length in seconds (`duration_s`), measured loudness (`loudness_lufs`, ITU-R BS.1770
integrated) and peak (`peak_dbfs`), the words it speaks (`text`), the voice as the provider
reported it (`voice`), and the provider's terms on its use (`terms`), such as ElevenLabs' rule
that its audio is never input to another AI model. Its `verb` says which audio verb made it, and
`transport` and `provider` name the voice runner (`local`, `voice-runner`) or ElevenLabs
(`elevenlabs`).

Two exports summarize a project:

- `GET projects/{id}/deliveries.csv` has one row per delivered asset: asset id, take, engine,
  prompt, parent takes, delivery time, destination and delivery id.
- `GET projects/{id}/decisions`, or `decisions.jsonl` for one JSON line per decision, is the
  decision record. Each line says what was chosen or assessed, among which takes (with engine,
  provider, model, seed and cost), the reason, the author, whether the run was blind, and when.
  A candidate's `cost_micro_usd` is its attempt's cost: the charge when known, else what the attempt
  committed. An attempt is charged once however many takes it returns, so a line states that cost on
  the first of the attempt's takes among its candidates; its other takes, such as a Voice Design call's
  second and third previews, read `null`, so summing a line's candidates counts each attempt once. A
  hidden run's candidates state no cost and name no attempt until the reveal. Each candidate names its attempt (`attempt_id`),
  shared by the takes one call returned; null for material from no run and while the run is hidden. The MCP
  door's `crexil_get_decisions` candidates carry it too.
  It is the artist's account of decisions, never a dataset, and it holds no media.

## Pipelines

- **Engine import.** Copy a package folder, or its `Art/`, `Models/` and `Audio/` folders, into a
  Unity, Unreal Engine or Godot project. Every take arrives with its manifest under the same
  name, so an importer can stamp source, engine and seed into its asset metadata, and
  `package.json` lists every file with its hash.
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
  cell. A take whose runtime stopped answering reads `uncertain`: it was not sent again, and
  collecting looks it up.
- **A held attempt.** `POST attempts/{slot}/resume` with `{"attempt": n}` resumes the engine that
  holds it. A hold on one engine for one run (TRY ONCE MORE) and an engine's own hold lift on
  request; a runtime's hold (a runner, ComfyUI, a key, the card) lifts only once that runtime
  answers, else `422` says what to fix. The hold lifts once even when the request is sent again,
  every attempt it sends meets the spending limits again, and the record names who resumed it.
- **One attempt to stop.** `POST attempts/{slot}/cancel` with `{"attempt": n}` skips an unsent
  attempt; a running one is asked to stop; a request already sent to a paid provider keeps its
  cell, and its charge stays unverified until the provider reports it. A later attempt of the same
  slot answers `409`: read again.
- **Retries.** The review's `automatic_retry` says per operation whether its attempts retry on
  their own: audio attempts do, visibly; image and 3D attempts never do.
