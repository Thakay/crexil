# Workflows — graph conventions

API-format ComfyUI graph exports, named `<family>-<capability>@vN.json`
(e.g. `flux2-txt2img@v1.json`). Presets reference them by that name.

Rules:

- **API format only** — ComfyUI editor → Workflow → **Export (API)**. The
  regular save is the editor's layout format and is not executable via
  `/api/prompt`.
- **Stable titles** — every node a preset patches carries
  `"_meta": {"title": "..."}` (`prompt_positive`, `sampler`, `latent_size`,
  `scheduler`, `noise_seed`, …). The cockpit addresses nodes by title, never
  by node id.
- **Versions are immutable** — manifests embed the exact graph that produced
  every accepted asset; changing a shipped file breaks
  re-runnability. Structural changes go into a new `@vN+1`.
- **Family skeletons differ, checkpoints don't** — a new checkpoint within a
  family is a filename swap inside the existing graph; a new model family
  gets its own skeleton from its official ComfyUI template/example.
- Custom nodes a graph requires must be pinned in `snapshots/pins.json`
  — no graph may silently depend on an unpinned pack.
- **Authoring stays in ComfyUI's graph format**: the editor above or a
  text editor, as long as the result is an API-format export.
- **Always the `/api/*`-prefixed routes** — `POST /api/prompt`,
  `GET /api/history/{id}`, `GET /api/queue`, WS `/ws`.

Two of the graphs, as examples: `flux2-txt2img@v1` (FLUX.2 klein-4B: UNETLoader +
CLIPLoader[type=flux2, Qwen3-4B] + FluxGuidance/BasicGuider +
Flux2Scheduler + SamplerCustomAdvanced — guidance-distilled, no negative
channel) · `sdxl-txt2img@v1` (Juggernaut XI: classic checkpoint + dual
CLIPTextEncode + KSampler, real negative prompt + CFG).

Region/iteration graphs (orchestration of proven patterns, zero
new ML; all on the SDXL rails):

- `sdxl-img2img@v1` — LoadImage → VAEEncode → KSampler with a live
  `denoise` handle. Plain img2img iteration.
- `sdxl-inpaint@v1` — the **MODIFY** recipe: GrowMaskWithBlur (soft mask)
  → INPAINT_VAEEncodeInpaintConditioning → Fooocus patch
  (head + v26 patch) + DifferentialDiffusion → KSampler (denoise ≈0.5,
  patchable up to ~0.8 for stronger rework) → ImageCompositeMasked.
  Fractional denoise keeps original region content in play (validated:
  0.5 preserves, 0.75 reworks toward the region prompt keeping color).
- `sdxl-replace@v1` — the **REPLACE** recipe: same skeleton plus
  INPAINT_MaskedFill(neutral) before encoding and denoise 1.0, so
  original region content is discarded and regenerated per the region
  prompt. Also serves the KEEP scenario's "regenerate everything I
  didn't protect" pass (server passes the complement mask). Trap
  learned: plain InpaintModelConditioning leaks original content into
  `ApplyFooocusInpaint` even at denoise 1.0 — the pack's
  VAEEncodeInpaintConditioning node (masked-area-neutralized
  `latent_inpaint` output) is required.
- `lama-remove@v1` — the **DELETE** recipe: INPAINT_LoadInpaintModel
  (big-lama.pt) + INPAINT_InpaintWithModel + ImageCompositeMasked.
  Promptless coherent fill.
- `util-composite@v1` — ImageCompositeMasked utility: restores **KEEP**
  region pixels verbatim as a pipeline's final pass (belt-and-braces
  against feathered inpaint edges).

Image-fed graphs default their LoadImage/LoadImageMask inputs to canned
`cockpit-smoke-*.png` names; `scripts/smoke.py` synthesizes + uploads
those before each run, and the cockpit patches them per turn (images
enter ComfyUI via `POST /api/upload/image` with content-addressed
names — never base64-in-graph, to keep stored graphs/manifests small).
