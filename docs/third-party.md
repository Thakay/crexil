# Third-party software, models and services

Crexil's own code is Apache-2.0. Crexil orchestrates other people's engines, models and services,
and none of them is part of this repository. This page says what Crexil talks to, how, and what
you must check yourself. It is a map, not legal advice and not a clearance: licences and terms
change, and several entries below are marked unknown on purpose. What each engine and service
receives from you is in [SECURITY.md](../SECURITY.md#what-leaves-your-machine).

## What this repository contains

- Crexil's own source code, configuration, reference ComfyUI graphs and documentation.
- One third-party derived file: `tools/3d/trellis2-pipeline/pipeline.json`, a modified copy of
  the pipeline definition from `microsoft/TRELLIS.2-4B` (MIT, notice in `NOTICE`). The
  modification replaces the gated background-removal model with BiRefNet.
- Code that follows upstream usage examples: the TRELLIS.2 wrappers in `tools/3d/` follow the
  TRELLIS.2 README example (MIT, same notice), and the ComfyUI graphs started from ComfyUI's
  published examples and templates.
- No engine, no vendored upstream repository, no model weight, no generated asset.

Library dependencies are declared in `cockpit/server/pyproject.toml` (locked in `uv.lock`),
`cockpit/web/package.json` (locked in `package-lock.json`), the MCP server's own script header
(`tools/agent-door/crexil_mcp.py`) and the voice runner's `tools/tts/requirements.lock`, and are
installed by you, under their own licences. The UI fonts (IBM Plex, SIL Open Font License 1.1) come
from the `@fontsource` packages at build time.

## Engines and services Crexil drives (all optional, all installed or subscribed separately)

| Engine or service | How Crexil uses it | Licence or terms to check |
|---|---|---|
| ComfyUI | A separate process, reached over its HTTP and WebSocket API. Crexil sends graphs and reads results. Nothing from the ComfyUI program is linked or copied here. | GPL-3.0 |
| ComfyUI node packs pinned in `snapshots/pins.json` | Installed into your ComfyUI | Several are GPL-3.0. Some download models on first use (see background removal, below) |
| Qwen3-TTS 1.7B, VoiceDesign and Base (voices) | Run by `tools/tts/runner.py` in its own environment, reached over HTTP on this machine | Code and weights Apache-2.0, outputs usable commercially (repository and model cards, read 2026-10-05) |
| TRELLIS.2 (3D) | Run by `tools/3d/` in its own environment, as a subprocess | Engine and weights MIT, with TRELLIS's sparse-structure decoder (MIT). Its image conditioner, DINOv3, is gated under Meta's DINOv3 License, which sets no non-commercial limit but must travel with any copy. Its mesh extraction depends on `nvdiffrast` and `nvdiffrec`, under the NVIDIA Source Code License (1-Way Commercial): **non-commercial use only**, meaning research or evaluation |
| Hunyuan3D 2.1 (3D) | Run by `tools/3d/` in its own environment, as a subprocess. `tools/3d/pins.json` describes a small patch you apply to your own checkout of the upstream code (the `import bpy` made optional) | Tencent Hunyuan 3D 2.1 Community License: excludes the EU, the UK and South Korea, and the exclusion covers using or displaying its outputs there too; a separate licence is needed above one million monthly users. Its environment also pulls GPL-3.0 libraries (`pymeshlab`), and its paint stage uses DINOv2 (Apache-2.0) and Real-ESRGAN (BSD-3-Clause) |
| rembg (cutouts in the 3D lane) | `tools/3d/funnel.py` cuts the subject out before either 3D engine sees it | The library is MIT, and the funnel names its model, BiRefNet-general (MIT). Hunyuan3D 2.1's own remover runs only on an image without alpha, which the funnel never sends, and loads rembg's default model: U²-Net (Apache-2.0) in the pinned rembg 2.0.65. Newer rembg releases default to BRIA RMBG, which is non-commercial |
| gltfpack | Called as a command-line tool for mesh decimation | MIT |
| ElevenLabs: Voice Design, speech and sound effects | HTTPS calls with your key, straight to the API, never through its SDK; models and prices in `configs/engines.json` | ElevenLabs' Terms of Service and Prohibited Use Policy. On a paid plan the output is yours to use commercially; the free plan is for non-commercial use only. Its output must never be input to machine learning, so Crexil never feeds an ElevenLabs take to another AI engine, and the decision record is never a dataset |
| The voice director, a voice agent built on ElevenAgents | The browser holds a conversation with an agent configured from `tools/voice-director/agent.json`, opened with a short-lived token the server asks for with your key | ElevenLabs' terms, as above. The agent's language model is Anthropic's Claude Haiku 4.5 (`claude-haiku-4-5` in `agent.json`), chosen inside ElevenAgents and reached through ElevenLabs |
| Hosted image models through OpenRouter | HTTPS calls with your key; the model list from OpenRouter's live catalogue, pins and price notes in `providers.json` | OpenRouter's terms, plus each model provider's terms and usage policy, which decide what you may do with its output |
| An MCP client, such as Claude Code | Runs `tools/agent-door/crexil_mcp.py` over stdio | The client's terms, and those of the model it uses, cover what the door's answers tell it |

Crexil's server never links an engine: ComfyUI and the runners are separate programs, and the
wrappers in `tools/3d/` import each engine inside that engine's own, separately installed
environment. Crexil's Apache-2.0 licence covers Crexil's code and does not change the engines'
terms, and their terms do not change Crexil's. What you may do with the **output** of an engine,
a model or a service is decided by that engine's, model's or service's own terms.

## Model weights

Crexil never ships weights. `scripts/download_models.py` downloads them, driven by
`scripts/models-manifest.json`, which records for every entry its source and its licence as
found; some entries also record when that was checked and quote the licence text. The 3D lane's
weights are pinned in `tools/3d/pins.json`, and `tools/tts/README.md` downloads the voice runner's
two Qwen3-TTS models at pinned revisions. Read the manifest before you use a model commercially.

### What is on by default

The product rule (PRD §2) is that a non-commercial weight may be defined and is flagged, and is
never a default. Where things stand:

- **The downloader.** With no arguments it fetches only the `flux-core` group: FLUX.2 klein 4B, its
  text encoder (Qwen3-4B) and its VAE, all Apache-2.0. Every other group downloads only when you name
  it with `--group` or `--all`, and every entry prints its recorded licence before it downloads. The
  groups you can name hold non-commercial weights: in `sdxl-rails`, Juggernaut XI (below),
  4x-UltraSharp (CC BY-NC-SA 4.0) and the MAT inpainting model (treated as non-commercial), and in
  `sdxl-rails` and `krita-sd15`, the unconfirmed weights listed below.
- **The engines enabled in `configs/engines.json`** are the four audio rows: ElevenLabs Voice Design,
  speech and sound effects, under ElevenLabs' terms, and Qwen3-TTS, Apache-2.0. The hosted image lane
  opens with an OpenRouter key, under each model provider's terms.
- **The SDXL rails are not cleared.** The four SDXL graphs (`workflows/sdxl-*.json`) load Juggernaut
  XI, and the `prop-sheet` and `raw-sdxl` presets run Refine and region edits on them. Juggernaut XI's
  model card said CC BY-NC-ND 4.0 when checked on 2026-09-18, while the manifest's older record said
  CreativeML OpenRAIL++ plus RunDiffusion terms. Treat it as non-commercial.
- **The 3D lane** has no install script, so it is set up for nobody by default. Set up as
  `tools/3d/pins.json` describes, TRELLIS.2's mesh extraction runs on NVIDIA's non-commercial
  `nvdiffrast` and `nvdiffrec`, and Hunyuan3D 2.1's licence excludes the EU, the UK and South Korea.
  Neither is cleared for commercial work as pinned.
- **Background removal.** `briaai/RMBG-2.0` (non-commercial BRIA terms) is what some ComfyUI
  background-removal nodes, among them the pinned ComfyUI-RMBG pack, and newer rembg releases
  download on their own. Crexil's 3D lane names BiRefNet (MIT) instead, in the funnel and in the
  TRELLIS.2 pipeline file.

### Gated and unconfirmed weights

- **Gated weights need your own acceptance.** The DINOv3 conditioner used by TRELLIS.2 is
  gated on Hugging Face under Meta's DINOv3 License.
- **Unconfirmed licences stay marked unconfirmed.**
  - The Fooocus inpaint head and patch, which the MODIFY and REPLACE graphs use: the manifest
    records "not stated (Fooocus project: GPL-3.0)", and their model card said OpenRAIL when
    checked on 2026-09-18.
  - The Hyper-SD LoRAs (OpenRAIL++) and the EasyNegative embedding (CreativeML OpenRAIL-M): both
    recorded from repository cards and marked "verify"; a later check could not confirm either.
  - The 4x NMKD Superscale upscaler: no licence stated.

  Treat all of them as not cleared.
- A licence recorded in the manifest is what was found when it was recorded. It is not a
  guarantee that the terms are the same today.

## What this page does not settle

- Whether a particular commercial use of a particular engine, model or service is allowed. That
  is between you and its licensor.
- The provenance of any image you feed in as a reference or a bench input. The bench images
  in `bench/sources/` are synthetic and were generated by `bench/make_fixtures.py`.
