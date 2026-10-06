# Local voice runner

`runner.py` serves a local voice engine, Qwen3-TTS, to the cockpit over HTTP. It runs inside
the engine's own Python environment, so the cockpit never imports the engine's libraries and
the engine keeps the exact versions it was measured with. The cockpit reaches it through
`cockpit/server/app/local_voice.py` as the `qwen3-tts` engine on the local lane.

It does two things:

| Verb | Model | What it does |
|---|---|---|
| `voice-design` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | Invents a voice from a text description and reads a line in it. One job returns several takes, each a different voice |
| `speak` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | Reads a new line in the voice of a reference take of at least 3 seconds |

## Install

You need Linux, an NVIDIA GPU with about 5 GB of free memory, Python 3.12 and
[uv](https://docs.astral.sh/uv/). The environment takes about 5 GB of disk and the two models
about 9 GB.

Pick a folder for the engine's environment and one for the model cache (any folders you like):

```sh
ENV=~/voice-engine/.venv
export HF_HOME=~/voice-engine/hf-cache          # where the models are downloaded

uv venv --python 3.12 "$ENV"
VIRTUAL_ENV="$ENV" uv pip install -r tools/tts/requirements.lock

"$ENV/bin/python" -c "from huggingface_hub import snapshot_download as get
get('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign', revision='5ecdb67327fd37bb2e042aab12ff7391903235d3')
get('Qwen/Qwen3-TTS-12Hz-1.7B-Base', revision='fd4b254389122332181a7c3db7f27e918eec64e3')"
```

`requirements.lock` pins every package of the measured install, including `qwen-tts` 0.1.1,
`torch` and `torchaudio` 2.11.0 (CUDA 13.0 builds) and `transformers` 4.57.3. FlashAttention is
not needed; the runner uses PyTorch's own attention.

## Run

```sh
HF_HOME=~/voice-engine/hf-cache "$ENV/bin/python" tools/tts/runner.py
```

It serves `127.0.0.1:8190`. Point the cockpit at it with `VOICE_RUNNER_URL` (that is the
default), and set `enabled` on the `qwen3-tts` row of `configs/engines.json`.

| Option | Variable | Default | Meaning |
|---|---|---|---|
| `--port` | `VOICE_RUNNER_PORT` | `8190` | The port |
| `--host` | `VOICE_RUNNER_HOST` | `127.0.0.1` | The address. The runner has no accounts; it listens beyond loopback only with `--allow-remote` |
| `--allow-remote` | `VOICE_RUNNER_ALLOW_REMOTE=1` | off | Listen beyond this machine |
| `--allow-host NAME` (repeatable) | `VOICE_RUNNER_ALLOWED_HOSTS` (comma-separated) | none | Also answer requests addressed to this host name |
| `--state` | `VOICE_RUNNER_STATE` | `~/.local/state/crexil-voice-runner` | Job records and takes |
| `--design-model`, `--speak-model` | `VOICE_RUNNER_DESIGN_MODEL`, `VOICE_RUNNER_SPEAK_MODEL` | the pinned revisions in the local cache | A model snapshot folder |
| `--device` | `VOICE_RUNNER_DEVICE` | `cuda:0` | The card |

## How it behaves

- **Only this machine's cockpit is served.** Requests addressed to another host, any request from a
  web page, and a job body that is not `application/json` are refused.
- **One job at a time.** Jobs queue first in, first out. The cockpit also runs local jobs one
  at a time across all engines (law 14).
- **Nothing stays loaded between jobs.** Each job loads its model, runs and frees it. Between
  jobs the process keeps only its CUDA context and PyTorch's own workspaces: 0.6 to 1.1 GB
  measured, released when the runner stops.
- **No network at run time.** Models load from the local cache only.
- **Records survive restarts.** Each job's record is written to `<state>/jobs/<id>.json` at every
  change, and its takes beside it. A job the runner did not finish is recorded as failed when it
  starts again.
- **Seeds are honored and recorded.** A job without a seed gets a drawn one. The same seed gives
  the same take on the same installation; other versions or cards are not promised the same.
- **Requests are checked by name.** An unknown field, a voice take shorter than 3 seconds or a
  file that is not PCM WAV is refused in plain words.

Routes: `GET /health`, `POST /jobs` (202 with `job_id`), `GET /jobs/<id>`,
`GET /jobs/<id>/audio/<i>` (take `i` from 0, as 16-bit PCM WAV), `DELETE /jobs/<id>` (a queued job
is cancelled at once; a running one finishes and is recorded as cancelled without its takes).

## Measured

On an RTX 4090 beside a resident ComfyUI, with the models in the page cache:

| Job | Time | Peak memory |
|---|---|---|
| Design 4 takes of about 4 s each | 14.2 s, of which 5.9 s loading the model the first time | 4.3 GB |
| Speak one line in a designed voice | 5.6 s, of which 1.5 s loading | 4.2 GB |

## Voices and consent

`speak` clones the voice of the take it is given. In Crexil that is an audio take in the
project: a designed take, or audio you imported. Clone only voices you have the right to use. A
take whose provider forbids its output as input to another AI model, such as ElevenLabs output,
is refused before it reaches the runner.

## Licence

Qwen3-TTS code and weights are Apache-2.0
([repository](https://github.com/QwenLM/Qwen3-TTS),
[model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)). Its outputs may be
used commercially. The runner is part of Crexil, also Apache-2.0.
