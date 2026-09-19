# Presets: authoring guide (config + workflow JSON, never code)

A preset is one JSON file in this directory. The server reloads this
directory on **every `GET /api/presets`** (the composer's roster fetch):
edit or add a file and it is live on the next fetch. No restarts. A broken
file makes that route answer 500, naming the problem.

## Anatomy

```jsonc
{
  "id": "my-preset",                 // defaults to the filename
  "display_name": "My Preset",
  "description": "free text for people reading the file",
  "modality": "image",
  "model_family": "flux2-klein-4b",  // free text; lands in every manifest
  "workflows": { "txt2img": "flux2-txt2img@v1" },  // -> workflows/<ref>.json
  "default_candidates": 4,           // carried in the catalogue; takes are set per run
  "defaults_note": "free text: how to read the defaults",
  "inputs": [ /* see below */ ]
}
```

## Input specs (how values reach the graph)

Every supported input has a `target`: the graph node it patches, addressed
by the node's **stable title** (`_meta.title` in the workflow JSON) plus
the input field name. One input may patch several nodes (list of targets) —
e.g. width must hit both `latent_size` and the FLUX.2 `scheduler`.

| type | extra fields |
|---|---|
| `text` | `required`, `placeholder`, `default` |
| `int` / `float` | `min`, `max`, `step`, `default` |
| `enum` | `choices` (must be valid values for that node input) |
| `seed` | a fixed seed with N takes runs seed, seed+1, … (reproducible) |

The composer's dials drive the dial-owned inputs (`configs/dials.json`); its
Advanced fold shows every input that applies to the recipe, with its raw control.

Declaring a handle the model/graph cannot honor: set `"supported": false`
with a `"reason"`: the composer shows it disabled with the reason (visible,
never silently ignored). Example: `negative_prompt` on FLUX.2 klein.

## Capabilities

`workflows` is a capability map. Beyond `txt2img`, a preset may declare:

- `img2img`: single-pass iteration on a source take; needs a graph
  with `source_image` (LoadImage) + a `denoise` handle on `sampler`.
- `inpaint_modify`, `inpaint_replace`, `remove`, `composite` — the four
  graphs the server orchestrates for **region turns** (KEEP/MODIFY/REPLACE/
  DELETE per `nd3-v1`; see `workflows/README.md`). Declaring all four
  enables the region capability for the preset. `composite` is optional —
  KEEP restore runs server-side — but harmless to declare.

Inputs may declare `"capabilities": ["txt2img", "img2img", "region"]` — the
input applies (and renders) only for those turn types; absent = all. Scope
family-specific handles: e.g. FLUX.2 `steps`/`guidance` target flux2 nodes
and must be `["txt2img"]` when region turns run on the SDXL rails.
`denoise` is special in region turns: it drives MODIFY passes only
(REPLACE/regen passes are pinned at 1.0 by design). For non-txt2img turns
seeds are patched structurally (`sampler.seed`, `removal_apply.seed`) —
the declared seed target only applies to the preset's own txt2img graph.

## Adding a new capability or model

1. Author the graph in the ComfyUI editor (`http://localhost:8188`), give
   every node the cockpit touches a stable semantic title, then Workflow →
   **Export (API)** into `workflows/<family>-<capability>@vN.json` (the
   naming rule; bump `@vN`, never mutate a version in place).
2. Write the preset JSON referencing it, mapping inputs to node titles.
3. Reopen the composer: the preset is live. Models referenced by the
   graph must exist under `~/models` (add them to
   `scripts/models-manifest.json` + run the downloader, so licenses stay
   recorded).

Existing presets: `prop-sheet` (production framing), `raw-flux2` /
`raw-sdxl` / `raw-zimage` (all handles, no framing — freeform/style
experiments; zimage Base = CFG + negative + the LoRA-training target),
`sketch-zimage` (Turbo iteration loop: 4 takes, ~2 s each, cfg
pinned 1.0).

## External bench (`providers.json` + live catalog)

External fan-out models are NOT presets. The full model list is **live**:
the server caches OpenRouter's discovery API (`GET /images/models`, ~6 h
TTL, snapshot persisted in staging for offline use) with per-model price
estimates and seed support derived from the per-endpoint pricing records —
so new OpenRouter models and price changes appear in the picker without
any code or config change (`/api/providers?refresh=1` forces it). The
authoritative cost is NEVER the listed estimate: it comes
back per generation as `usage.cost` and lands on the take + manifest.
Token-priced models (the Gemini image family) show a "$X/M tok" note
instead of a fake per-image number.

Repo-root `providers.json` is the pins/overrides/fallback layer (the one
key is `OPENROUTER_API_KEY` in `.env`, bench auto-disables without it):
`enabled: true` pins a model to the top of the picker ("bench"),
`label`/`supports_seed`/`params`/`price_usd_per_image`
override the live data, and the file is the offline fallback before the
first successful catalog fetch. `supports_seed` stays false unless the
model route is verified to honor a seed: the request records exactly what
was sent. External turns take the txt2img brief only; region/img2img
stay on the local rails.
