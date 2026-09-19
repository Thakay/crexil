# Third-party software, models and services

Crexil's own code is Apache-2.0. Crexil orchestrates other people's engines and models, and
none of them is part of this repository. This page says what Crexil talks to, how, and what
you must check yourself. It is a map, not legal advice and not a clearance: licences change,
and several entries below are marked unknown on purpose.

## What this repository contains

- Crexil's own source code, configuration, reference ComfyUI graphs and documentation.
- One third-party derived file: `tools/3d/trellis2-pipeline/pipeline.json`, a modified copy of
  the pipeline definition from `microsoft/TRELLIS.2-4B` (MIT, notice in `NOTICE`). The
  modification replaces the gated background-removal model with BiRefNet.
- Code that follows upstream usage examples: the TRELLIS.2 wrappers in `tools/3d/` follow the
  TRELLIS.2 README example (MIT, same notice), and the ComfyUI graphs started from ComfyUI's
  published examples and templates.
- No engine, no vendored upstream repository, no model weight, no generated asset.

Library dependencies are declared in `cockpit/server/pyproject.toml` (locked in `uv.lock`) and
`cockpit/web/package.json` (locked in `package-lock.json`) and are installed by you, under
their own licences. The UI fonts (IBM Plex, SIL Open Font License 1.1) come from the
`@fontsource` packages at build time.

## Engines Crexil drives (all optional, all installed separately)

| Engine | How Crexil uses it | Licence to check |
|---|---|---|
| ComfyUI | A separate process, reached over its HTTP and WebSocket API. Crexil sends graphs and reads results. Nothing from the ComfyUI program is linked or copied here. | GPL-3.0 |
| ComfyUI node packs pinned in `snapshots/pins.json` | Installed into your ComfyUI | Several are GPL-3.0 |
| TRELLIS.2 (3D) | Run by `tools/3d/` in its own environment, as a subprocess | Engine and weights MIT. Its mesh extraction depends on `nvdiffrast` and `nvdiffrec`, which are under the NVIDIA Source Code License: **non-commercial use only** |
| Hunyuan3D 2.1 (3D) | Run by `tools/3d/` in its own environment, as a subprocess. `tools/3d/pins.json` describes a small patch you apply to your own checkout of the upstream code (the `import bpy` made optional) | Tencent Hunyuan 3D 2.1 Community License: excludes the EU, the UK and South Korea, and that exclusion also covers outputs; a separate licence is needed above one million monthly users. Its environment also pulls GPL-3.0 libraries (`pymeshlab`) |
| gltfpack | Called as a command-line tool for mesh decimation | MIT |
| Hosted models through OpenRouter | HTTPS calls with your key; models and price estimates in `providers.json` | OpenRouter's terms plus each model provider's usage policy |

Crexil's server never links an engine: ComfyUI and the 3D runner are separate programs, and the
wrappers in `tools/3d/` import each engine inside that engine's own, separately installed
environment. Crexil's Apache-2.0 licence covers Crexil's code and does not change the engines'
terms, and their terms do not change Crexil's. What you may do with the **output** of an engine
or model is decided by that engine's and that model's licence.

## Model weights

Crexil never ships weights. `scripts/download_models.py` downloads them, driven by
`scripts/models-manifest.json`, which records for every entry its source and its licence as
found; some entries also record when that was checked and quote the licence text. The 3D lane's
weights are pinned in `tools/3d/pins.json`. Read the manifest before you use a model
commercially.

### What is downloaded and used by default today

The product rule (PRD §2) is that a non-commercial weight is flagged and never a default. Today
that rule is not met, and you should know where:

- `scripts/download_models.py` with no arguments fetches the `flux-core`, `sdxl-rails` and
  `krita-sd15` groups, and it prints no licence. Those groups include non-commercial weights
  (4x-UltraSharp, CC BY-NC-SA 4.0; the MAT inpainting model, treated as non-commercial), Juggernaut
  XI (below) and every weight in the unconfirmed list below.
- The four SDXL graphs (`workflows/sdxl-*.json`) load Juggernaut XI, and the `prop-sheet` and
  `raw-sdxl` presets run Refine and region edits on them. Juggernaut XI's model card said
  CC BY-NC-ND 4.0 when checked on 2026-09-18, while the manifest's older entry records
  CreativeML OpenRAIL++ plus RunDiffusion terms. Treat it as non-commercial.
- `briaai/RMBG-2.0` (non-commercial BRIA terms) is what some ComfyUI background-removal nodes
  download on their own; Crexil's 3D lane substitutes BiRefNet (MIT) for it.

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

- Whether a particular commercial use of a particular engine or model is allowed. That is
  between you and its licensor.
- The provenance of any image you feed in as a reference or a bench input. The bench images
  in `bench/sources/` are synthetic and were generated by `bench/make_fixtures.py`.
