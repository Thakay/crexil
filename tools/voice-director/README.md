# Voice director

The voice director is a voice agent built on ElevenAgents. Inside Crexil it is a conversation: the
artist talks, and the agent lists engines, prepares audio runs, plays takes and proposes picks, all
through the same shared actions the artist uses. The panel is `cockpit/web/src/app/voice-director/`.
This folder holds the agent's configuration and the one command that applies it to an ElevenLabs
account.

## What the agent may and may not do

| May | Never |
|---|---|
| List engines, read the project and a run | Hear or receive take audio: its tools return facts, labels and reasons only |
| Prepare a run; a free run whose engines all run on this machine starts at once | Submit a paid run, or any run that sends something off this machine: the artist confirms it with a click, with the price on the button and what leaves the machine on the card (laws 6, 15 and 22) |
| Play a take for the artist through the speakers | Pick, reveal a blind run, deliver or change limits (law 23) |
| Propose a pick with a reason, recorded as its own decision | Act unasked: it acts only on the artist's spoken request (law 15) |

Every write the agent makes is recorded as `agent:voice-director`. Every write the artist
confirms in the panel is recorded under the artist's name.

ElevenLabs' policy forbids using its output as input to any machine learning, and the language
model behind the agent, Anthropic's Claude Haiku 4.5 chosen inside ElevenAgents, is one. So no take
audio, URL or file address ever reaches the agent.
Before any take plays, whoever starts it, the panel mutes the microphone and turns the agent's voice down; the
microphone stays muted 400 ms after the take stops, and no take plays while a session connects.

## Files

| File | Holds |
|---|---|
| `agent.json` | The agent: name, tag, prompt (it names no price before `prepare_run` answers, then the run's attempts and reserved total as returned), language model, voice model, how long it waits in silence before it speaks up (30 s, so a playing take never prompts it), the session's longest duration, privacy, the provider's call limits, and the six client tools. No key, no account id |
| `apply.py` | Validates `agent.json` and creates or updates the agent and its tools |

The six tool names in `agent.json` must match the panel's handlers in
`cockpit/web/src/app/voice-director/tools.ts`. A test checks it.

## Apply

A dry run validates the file and prints every call a live run would make. It reads no key and
opens no connection:

```sh
python3 tools/voice-director/apply.py --dry-run
```

A live run needs `ELEVENLABS_API_KEY` in the environment. It never reads a file for it and never
prints it:

```sh
set -a; . ./.env; set +a          # from the checkout whose .env holds the key
python3 tools/voice-director/apply.py
```

It checks that the configured language model is offered, creates the six client tools and then
the agent, and prints the line to add to `.env`:

```text
ELEVENLABS_VOICE_AGENT_ID=...
```

To update an agent that exists, pass `--agent-id` or keep `ELEVENLABS_VOICE_AGENT_ID` set: apply
reads the agent's tool ids and updates them in place. `--voice-id` sets the director's own voice;
without it the platform's default voice speaks. `--session-max` must equal the installation's
`CREXIL_VOICE_SESSION_MAX_S` (default 300); the file's `max_duration_seconds` must equal it too.

Then set the minutes Crexil may spend, and restart the server:

```text
CREXIL_VOICE_MINUTES_CAP=10        # 0, the default, starts no session
CREXIL_VOICE_SESSION_MAX_S=300
```

## Costs and limits

Minutes are capped in three places (law 22):

- **Crexil's budget:** the server refuses a session when the minutes left cover less than one
  session, and the panel ends a session at its maximum.
- **The agent's own limit:** `conversation.max_duration_seconds` ends a session ElevenLabs-side.
- **The provider's call limits:** `agent.json`'s `call_limits` allow 10 conversations a day
  (`daily_limit`), one at a time (`agent_concurrency_limit`), and no burst calls past that limit at the
  higher rate (`bursting_enabled` false). Apply the file again after changing them. `apply.py` refuses a
  file that leaves `bursting_enabled` true or unset.

ElevenLabs lists ElevenAgents call minutes at $0.08 each beyond a plan's included minutes, with
the language model billed on top. The panel shows this as an estimate, never as a promise (law 17).

## Confirmed on the first live run

- `claude-haiku-4-5` is offered.
- ElevenLabs accepts `record_voice` and `retention_days`: a conversation keeps no audio and is deleted 7 days
  after it starts. Whether the account's data-use setting keeps conversations from improving models is a
  dashboard setting the account's owner checks.
- A conversation token lasts 15 minutes.
- Minutes are billed per second, at about 182 credits a minute for the call plus the model's charge, so a
  5-minute session costs about 1,500 credits all in.
