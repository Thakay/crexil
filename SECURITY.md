# Security

## The model Crexil assumes

Crexil is a single-user application on one machine. It trusts that machine and guards the edges a
browser or a network could reach. Read this before you expose it anywhere.

### Localhost trust

- **No accounts.** The server has no authentication. It listens on localhost, and the Docker setup
  publishes it on `127.0.0.1` only. Never bind it to a public or shared network interface.
- **Host and Origin guards.** The server answers only requests addressed to `localhost` or
  `127.0.0.1`, which defeats DNS rebinding, and it refuses any request whose `Origin` is not
  localhost, `null` and `file://` included, on every route. A web page in another tab cannot
  drive a local server that can spend money.
- **The key route.** Saving a key from the Connections room (`POST /api/setup/openrouter`) also needs
  a loopback client and the `X-Cockpit-Setup: 1` header. `compose.yaml` sets
  `COCKPIT_LOOPBACK_PUBLISH=1` because its clients arrive from a bridge address; remove that line if
  the publish ever leaves `127.0.0.1`.
- **Every program you run is trusted.** Anything running as you on this machine can call the API. A
  client states its own author (`human:` or `agent:`), and the server records it as stated. The agent
  limits below are rules for agent clients that say who they are, such as the MCP server and the
  voice director, which cannot write as a person. They are not a sandbox against a hostile program
  on your machine.

### Keys

- **Where keys live.** Keys come from the environment: `.env` at the checkout root, git-ignored and
  never baked into an image. The ElevenLabs key is read from the environment only. An OpenRouter key
  saved from the Connections room lives in `secrets.json` beside the store, mode 0600, git-ignored
  and left out of backups; an environment value wins over it. A zipped state folder attached to a bug
  report would carry `secrets.json`, so leave it out.
- **Never in the store** (law 18). No key is written to the database, an event, a receipt, a log, a
  project backup or a character package. The API shows a key only masked (its first six and last
  four characters), and the client bundle holds no key.
- **The voice director's token.** The server asks ElevenLabs for a short-lived conversation token
  with the key and hands it to the browser once. The key never leaves the server, the token stays out
  of every receipt, and a replayed start answers its session without asking for another token.

### Money

- **Every run states its cap** (law 22). On the project surface every run states a spending limit,
  and the project has its own. A run whose known prices exceed either is refused before any engine
  is called. An unknown price counts as over the limit, so a run that includes one is refused whole.
  Only the artist sets or raises a limit.
- **The cap is on published prices.** Review reserves what a provider's published tariff says; the
  provider bills what it measures, and the record keeps both. Set a limit on the provider's side too:
  a credit limit on the OpenRouter key, and the quota of your ElevenLabs plan.
- **The legacy canvas** (`#/w`) still has no per-run cap. What limits paid work there is its LOCAL
  ONLY switch and the limit of 16 takes per run.

### Agents

- **An author on every act** (law 23). Every write on the project surface names its participant, and
  the record shows what a person did and what an agent did. The legacy canvas records no author.
- **What agents may not do.** The server answers `403` when an agent tries to reveal a blind run,
  prepare or transfer a delivery, change a project's or a sequence's limits, create a sequence with
  limits other than the default, start or end a voice session, resume held work that reserves money
  or any attempt of a blind run, or submit paid work in a project whose spending limit the artist has
  not set. Agents may draft and review runs, submit them within the artist's limits, propose picks
  with reasons, and pause or cancel work. [docs/agents.md](docs/agents.md#what-an-agent-may-and-may-not-do)
  has the full table.
- **The MCP server** (`tools/agent-door/`) talks to a loopback Crexil only, follows no redirect and
  writes as `agent:<name>` and nothing else. Its own per-run ceiling defaults to $0 and is checked at
  prepare and again at submit, and a paid submission must restate the reviewed amount. The ceiling
  compares money, not where an engine runs. It offers no pick, reveal, delivery, limit or voice
  session tool, and its answers carry facts, ids, labels and reasons, never a take's bytes or a file
  address.
- **The voice director** writes as `agent:voice-director`. It submits only free runs whose engines all
  run on this machine (Crexil sends each request to a loopback address); any other run it prepares, paid
  or free, waits for the artist's click, with the price on the button and what leaves the machine on the
  card. Its tools never hand take
  audio, an address or a file name to the agent.

### Voice sessions

- Only the artist starts a session, and minutes are capped. `CREXIL_VOICE_MINUTES_CAP` is 0 unless
  set, which starts no session. An open session counts its whole maximum,
  `CREXIL_VOICE_SESSION_MAX_S`, until it ends. Only the artist ends it, and its minutes are the
  longer of what the panel observed and the server's own time since the start, never more than the
  maximum.
- The agent's own settings on ElevenLabs are backstops (`tools/voice-director/agent.json`): a token
  is required to talk to it, one conversation runs at a time, a daily limit applies, voice recording
  is off, conversations are kept 7 days, and the longest conversation equals
  `CREXIL_VOICE_SESSION_MAX_S` when both keep their defaults.
- All take audio plays through one player, which mutes the microphone before any take sounds, so no
  take reaches the conversation.

### The runners

The voice runner (`tools/tts/runner.py`) and the 3D runner (`tools/3d/runner.py`) have no accounts.
Each listens on loopback by default, refuses a request addressed to a foreign host or carrying any
`Origin`, and refuses a body without its JSON content type. Each listens beyond loopback only when
told so by name: `--allow-remote` or `VOICE_RUNNER_ALLOW_REMOTE=1` for the voice runner,
`THREED_RUNNER_ALLOW_REMOTE=1` for the 3D runner. Guard them as you guard the server.

### Blind runs

A blind run hides each take's engine, provider, model, seed, cost, timing and loudness on every read
until the artist reveals it, so the artist judges the take and not the name. It guards judgment at
the grid. It is not a secret against the file bytes, which can carry an engine's fingerprints (a
ComfyUI image's text chunks, a hosted image's provenance marks), or against the facts it leaves in
view: which engines the run asked for, which takes share an attempt, a failure's kind and its
retries, and the lane while a take is in flight.

### Files

- The server writes takes to its staging folder and, on delivery or Promote, copies them into the
  hand-off folder (`PROMOTE_DIR`, by default `crexil-handoff` in your home folder). It never creates
  that folder, and `PROMOTE_ROOTS` fences where a re-point may go: unset, the hand-off folder itself.
- A character package names the artist who delivered it (`package.json`, `decisions.json`), and its
  mesh manifests keep absolute staging paths. A project backup holds the project's record, which
  names its participants and the folders its deliveries went to. Check both for names and paths
  before you share one.

## What leaves your machine

Crexil sends nothing anywhere until a run, a readiness check or a voice session needs it, and its
client loads no outside script or font. Each destination, and what it receives:

- **ElevenLabs Voice Design** (`api.elevenlabs.io`): the voice description and its preview line. A
  preview you speak with is saved as a voice in your ElevenLabs account, which uses one of its voice
  slots; Crexil never deletes a saved voice.
- **ElevenLabs speech:** each line and the voice that speaks it. The speech stays in your ElevenLabs
  history, because Crexil keeps ElevenLabs' logging on so that a lost take can be collected without
  paying again; zero retention is an Enterprise feature that would turn that off. The first line in a
  designed voice saves that voice, using a voice slot.
- **ElevenLabs sound effects:** the sound description and its length.
- **ElevenLabs, for your account:** Crexil reads your subscription and your voices, to say whether the
  engines are ready and to find a saved voice by its label, and reads your speech history to recover
  a lost take. These reads carry your key and no project data. Your ElevenLabs account has its own
  data-use setting; check it on ElevenLabs' side.
- **The voice director** (ElevenLabs Agents), in the panel's own words: "While a session runs, your
  microphone audio goes to ElevenLabs and the director's language model, and so does what the
  director reads about this project: the brief, run prompts, take labels and reasons. ElevenLabs
  keeps each conversation for 7 days. Take audio never goes: the microphone mutes before any take
  plays." The language model is `claude-haiku-4-5`, reached through ElevenLabs. Running
  `tools/voice-director/apply.py` sends the agent's configuration, its prompt and its tools, to your
  ElevenLabs account.
- **OpenRouter** (`openrouter.ai`): the prompt, any source images and the run's settings, which
  OpenRouter passes on to the model's provider under that provider's data policy. Your OpenRouter
  account's settings decide whether prompts are logged or used for training. Crexil also reads
  OpenRouter's model list every six hours and checks the key; neither sends a prompt.
- **An MCP client:** the door's answers go to the client, such as Claude Code, and from it to the
  model it uses: project names and briefs, prompts, take labels, prices and reasons. Never take
  audio, a file or an address.
- **Local engines** (ComfyUI, the voice runner, the 3D runner): requests go to this machine only, and
  nothing Crexil sends them leaves it. What a runtime does on its own, such as downloading weights on
  first use or a ComfyUI node pack's own calls, is that runtime's: start the runners with
  `HF_HUB_OFFLINE=1` once their weights are cached.

Before anything runs, the drawer says the same for each operation, and the Connections room lists
each provider's destination.

## Reporting a vulnerability

Please report security problems privately rather than in a public issue. If the repository's
private vulnerability reporting is enabled ("Report a vulnerability" under the Security tab),
use it; otherwise open an issue that asks for a private contact and leave the details out.
Include what you found, how to reproduce it, and what it lets an attacker do. You will get an
answer as soon as the maintainer can give one; this is a one-person project, so please allow
some time before disclosing.
