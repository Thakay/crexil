"""Wire models: every JSON route carries a `response_model=` from
here → OpenAPI → generated TS types (`scripts/gen-api-types.sh`). The
component names ARE the TypeScript type names (`web/src/types.ts` aliases
them verbatim; the snapshot test enforces clean names).

Two rules that keep the wire honest:
- Slimming lives in code (`_slot_slim`, `_region_view`), NEVER in a
  response model — `response_model` filtering silently drops fields, so
  models are exhaustive and `tests/test_response_models.py` asserts
  payload ⊆ model_dump on real shapes.
- Optionality: a key absent on some code path is `T | None = None`; a key
  always present has no default (nullable value ≠ absent key). Config or
  upstream passthrough shapes (`Preset`, `InputSpec`, `ProviderModel`,
  `OpenRouterProbe`) set `extra="allow"` so author-added keys survive.

The WS frames stay out of the schema (OpenAPI 3.1 can't express them); typed
events live on `GET /api/events`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- vocabularies the web narrows on (StrEnum -> named OpenAPI component) ---


class Capability(StrEnum):
    txt2img = "txt2img"
    img2img = "img2img"
    region = "region"


class RegionAction(StrEnum):
    keep = "keep"
    modify = "modify"
    replace = "replace"
    delete = "delete"


# --- requests ---


class SetupKey(BaseModel):
    api_key: str


# --- health ---


class HealthBuild(BaseModel):
    id: str
    time: str


class HealthComfy(BaseModel):
    ok: bool
    version: str | None = None
    error: str | None = None


class HealthDir(BaseModel):
    path: str
    ok: bool


class HealthPromoteDir(BaseModel):
    path: str
    mounted: bool


class OpenRouterProbe(BaseModel):
    """`_openrouter_check()` / `probe()` merge — shared by health and setup."""
    model_config = ConfigDict(extra="allow")

    configured: bool
    requests_total: int | None = None
    ok: bool | None = None
    status: int | None = None
    error: str | None = None
    note: str | None = None


class HealthInfo(BaseModel):
    db: bool
    preview: bool = False
    build: HealthBuild
    comfyui: HealthComfy
    presets: list[str]
    preset_count: int
    workflows_dir: HealthDir
    models_root: HealthDir
    promote_dir: HealthPromoteDir
    openrouter: OpenRouterProbe


# --- catalog / providers ---


class InputTarget(BaseModel):
    node_title: str
    field: str


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    supported: bool                      # normalization always sets it
    label: str | None = None
    type: Literal["text", "int", "float", "enum", "seed"] | None = None
    required: bool | None = None
    default: Any = None
    placeholder: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[str] | None = None
    reason: str | None = None
    capabilities: list[Capability] | None = None  # absent = every turn type
    target: list[InputTarget] | None = None       # normalized to a list


class Preset(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str
    description: str
    modality: str
    workflows: dict[str, str]
    default_candidates: int
    inputs: list[InputSpec]
    model_family: str | None = None
    defaults_note: str | None = None


class ProviderModel(BaseModel):
    """One merged catalog entry (live ∪ providers.json pin)."""
    model_config = ConfigDict(extra="allow")

    id: str
    provider: str
    label: str
    name: str
    supports_seed: bool
    pinned: bool
    source: Literal["live", "config"]
    price_usd_per_image: float | None = None  # pre-generation ESTIMATE only
    price_note: str | None = None
    description: str | None = None
    params: dict[str, Any] | None = None
    enabled: bool | None = None               # config-fallback entries only
    endpoints: list[dict] = Field(default_factory=list)
    endpoints_fetched_at: str | None = None
    endpoint_discovery_error: str | None = None
    discovered_model: dict | None = None


class ProvidersInfo(BaseModel):
    transport: str
    key_present: bool
    fetched_at: str | None                    # null = config-only, no live fetch
    model_count: int
    models: list[ProviderModel]


# --- OpenRouter setup surface ---


class SetupStatus(BaseModel):
    configured: bool
    source: Literal["env", "stored"] | None
    masked: str | None
    env_overrides: bool
    probe: OpenRouterProbe | None = None      # absent on DELETE
    note: str | None = None


# --- the product model: workspaces, turns, slots, nodes ---


class SlotStatus(StrEnum):
    queued = "queued"
    running = "running"
    landed = "landed"
    failed = "failed"
    held = "held"
    skipped = "skipped"
    set_aside = "set_aside"
    late = "late"


class CancelAction(StrEnum):
    kept = "kept"
    skipped = "skipped"
    interrupting = "interrupting"
    cancelled_in_flight = "cancelled_in_flight"
    unowned = "unowned"


class CreateWorkspace(BaseModel):
    name: str = Field(min_length=1)


class Anchor(BaseModel):
    x: float
    y: float


class LocalBatch(BaseModel):
    """One local engine's share of a Run (the take count is per
    engine, never one global N). Slots are laid out batch by
    batch in list order, takes within a batch in order, so the grid reads
    engine by engine."""
    preset_id: str
    takes: int = Field(1, ge=1, le=16)
    # Raw preset-input values by key (steps, cfg, denoise, width…) — THIS
    # preset's own: the composer resolves each dial position per engine
    # (configs/dials.json), so two engines in one turn carry two
    # input dicts. The server stays dial-ignorant and only coerces to the
    # preset's declared bounds.
    inputs: dict = {}
    # Per-take overrides laid over `inputs`, in slot order — a SWEEP ONE
    # DIAL run posts one per take, each moving one key. Empty =
    # every take runs `inputs`; otherwise exactly `takes` entries. A swept
    # slot then carries its own dials, so a reroll of that cell rebuilds
    # its value, not the batch's.
    take_overrides: list[dict] = []


class ExternalBatch(BaseModel):
    """One external model's share of a Run — `takes` slots of it, each a
    fresh seed under the turn's policy, all concurrent and priced."""
    model_id: str
    takes: int = Field(1, ge=1, le=16)


class Run(BaseModel):
    """One Run: any number of local engines, each with its own
    take count and raws, plus any external models, each with its own
    count (the roster's rows, verbatim). One turn, ≤ 16 slots.

    `seed` is the turn's ONE policy: {"mode": "random"} rolls fresh per
    take · {"mode": "locked", "value": n} broadcasts one seed to every
    take of every engine (the SEED dial's lock — so engine and dial
    differences are the only variables in the batch) · {"mode": "fixed",
    "value": n} is the fixed seed+pos spread.

    `parents` are PLAIN parents — image nodes handed to the run as
    sources, with no declared meaning (style / identity / composition
    channels are not built yet). Empty = a txt2img root run; one entry = every
    local batch's `img2img` recipe with that node as `source_image`, which
    is what Refine is. It takes at most one for now: every committed
    img2img graph has exactly one source, and inventing a blend would be a
    guess.

    Region turns take their own route, POST /workspaces/{wid}/region-turns
    (RegionRun): masks ride that request, never this one."""
    prompt: str | None = None
    local: list[LocalBatch] = []
    external: list[ExternalBatch] = []
    seed: dict = {"mode": "random"}
    parents: list[str] = []
    anchor: Anchor | None = None
    # the LOCAL ONLY switch, proved here rather than trusted to the
    # UI: a run carrying it with any external batch is refused in
    # words before a turn exists, so no paid dispatch can follow from it.
    # Recorded on the turn's dials for the manifest.
    local_only: bool = False


class RegionSpec(BaseModel):
    """One drawn region riding the region run: the
    surface's draft, serialized. `mask_b64` is the region's white-on-black
    PNG at the source's natural size (regions.load_mask binarizes at 128);
    z-order is the LIST ORDER — later entries win within an action
    (nd3-v1), exactly the surface's creation order. `prompt` only where
    the action takes one: KEEP and DELETE carry none by design."""
    name: str = Field(min_length=1, max_length=120)
    action: RegionAction
    prompt: str | None = None
    mask_b64: str


class RegionBatch(BaseModel):
    """One mask-capable engine's share of a region fan-out: `takes`
    slots of this preset, each running the SAME resolved pass plan with
    its own seed. No take_overrides: sweeps don't apply to region turns
    yet."""
    preset_id: str
    takes: int = Field(1, ge=1, le=16)
    inputs: dict = {}


class RegionRun(BaseModel):
    """One region fan-out: one source take, one region
    set, N mask-capable engines side by side. Every engine gets the same
    regions and the same precedence — the plan (passes, crop boxes,
    nd3-v1 resolution) is computed ONCE and shared; only the recipes
    differ per tier.

    `parents` names exactly one image node — the take the regions were
    drawn on. `prompt` is the turn prompt: region prompts fall back to it,
    and it drives the regenerate-the-rest pass when a KEEP exists.
    External models never appear here — region turns stay on the local
    rails, structurally."""
    parents: list[str] = Field(min_length=1, max_length=1)
    prompt: str | None = None
    regions: list[RegionSpec] = Field(min_length=1, max_length=8)
    local: list[RegionBatch] = Field(min_length=1)
    seed: dict = {"mode": "random"}
    anchor: Anchor | None = None


class MeshBatch(BaseModel):
    """One mesh engine's share of a To-3D run. `engine_id` is the
    capability matrix's id (`trellis2-4b` / `hunyuan3d-21`), not a preset:
    the mesh engines are subprocesses in their own venvs, so they have no
    committed ComfyUI graph to name.

    `params` are the engine's own declared handles (the Advanced fold's
    territory and grammar): the runner whitelists them by name and refuses an
    unknown one in words, so nothing from the wire ever reaches argv raw."""
    engine_id: str
    takes: int = Field(1, ge=1, le=4)
    params: dict = {}


class MeshRun(BaseModel):
    """One To-3D run: one source take, N mesh engines side by
    side. Mesh turns take their own route for the same reason region turns
    do — the shape is different, not a variation: no prompt reaches a mesh
    engine (the image IS the input, the `imageRef`), no external model can
    appear (structurally — there is no field), and there is no frame to ask
    for (the output is a mesh).

    `parents` names exactly one node — the take to build from. A mesh run is
    always refine-shaped: there is no root form of it.

    `kind` is the 3D verb (law 16): `generate` takes an image parent and
    makes a mesh; a mesh verb (`decimate`) takes a `3d` parent and makes a
    child beside it. The parent's payload, whether a seed means anything and
    how many takes are honest all follow from the kind, and the route refuses
    each in words.

    `reference` is the SECOND parent: the image node a `texture`
    take paints from. Its own field rather than a second entry in `parents`,
    because the two parents mean different things: `parents[0]` is the
    acted-on artefact (the shape), `reference` is the appearance. The child
    records both as parents, in that order. Required for a kind that paints;
    refused for one that doesn't."""
    parents: list[str] = Field(min_length=1, max_length=1)
    kind: str = "generate"
    reference: str | None = None
    mesh: list[MeshBatch] = Field(min_length=1)
    seed: dict = {"mode": "random"}
    anchor: Anchor | None = None


class MeshHealth(BaseModel):
    """Whether the 3D lane's door is open. The
    To-3D roster reads this to say why its rows are dashed — law 12 is badge,
    never filter, so `why` is the reason the artist sees, worded once
    server-side beside the probe that knows it (law 1/law 3)."""
    ok: bool
    url: str
    engines: list[str]
    # verb -> the engines that serve it (law 16). The roster reads this
    # to badge a verb an engine does not do, rather than hiding it (law 12).
    kinds: dict[str, list[str]] = {}
    why: str | None = None


class Region(BaseModel):
    """A committed region row (lineage). The mask itself is served at
    `mask_url` — mask_path never leaves the server."""
    id: str
    node_id: str
    name: str
    action: RegionAction
    prompt: str | None
    mask_sha256: str
    z_order: int
    consumed_by_turn_id: str | None
    created_at: str
    mask_url: str


class RegionList(BaseModel):
    regions: list[Region]


class Frame(BaseModel):
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)


class RerollBody(BaseModel):
    """The reroll route's optional body: the top
    bar's LOCAL ONLY switch riding the request; `keep_seed` — A5a's "run
    again, same seed": the same slot, the same engine, the same dials AND
    the same seed, because an empty frame is the engine's fault and the
    settings are worth a second pass as they are; and `frame` — A1's
    "retry at a smaller frame": the same cell rebuilt at this size (the
    preset's own bounds and 64-grid apply), which then IS the cell's size
    for every later reroll. A frame on a paid cell is refused in words —
    external models render at the provider's default."""
    local_only: bool = False
    keep_seed: bool = False
    frame: Frame | None = None


class MoneyGate(BaseModel):
    """The LOCAL ONLY switch on the two verbs that re-spend: a
    reroll or backfill sent under it refuses every PAID cell in words and
    touches the local ones as usual. Optional: no body means off."""
    local_only: bool = False


class Costs(BaseModel):
    """The corner: today and this month, CHARGED only — summed
    from the events ledger across every workspace; a quote that hasn't
    been billed never counts. Both windows run on the artist's own clock
    (the browser sends its UTC offset in `tz`), so the day turns over at
    their midnight, not the server's."""
    today_usd: float
    month_usd: float
    day: str                             # YYYY-MM-DD in the artist's clock
    month: str                           # YYYY-MM
    tz_minutes: int                      # the offset applied, echoed


class Workspace(BaseModel):
    id: str
    name: str
    slug: str
    viewport: dict[str, Any]
    dials: dict[str, Any]
    created_at: str
    updated_at: str


class WorkspaceRow(Workspace):
    """One entry in the sidebar's list. The counts are aggregates
    the list query carries so the sidebar never fans N detail fetches; they
    live here and not on Workspace because the single-row readers don't
    compute them — a model that declared them everywhere would serve a zero
    that means "not counted", not "none"."""
    nodes: int                           # live nodes (purged excluded)
    promoted: int                        # ...of which have left for the tree


class RenameWorkspace(BaseModel):
    """the slug is the promote path and never moves, so a rename is
    the display name alone (db.rename_workspace)."""
    name: str = Field(min_length=1)


class Slot(BaseModel):
    """A grid cell (law 8). Blobs (submitted_graph/external) never leave the
    server — slimming happens in code, the model states the slim shape.
    `cause` is the failed face's plain line and `detail` the raw engine
    text behind its collapsed affordance (law 3); `retry_of` names
    the failure kind this attempt is the scheduler's own automatic retry
    of, so a running cell can say so (law 9). `kind` is the failure kind on
    the row: the face picks its remedy by it; `blank` (A5a)
    offers the same-seed rerun, `charged_blank` (B4) the receipt.
    `job_ref` is the engine's job id — for a paid take the provider's
    generation id, the receipt's one line the artist can't retype.
    `frame` is the requested size, "WxH", stamped at dispatch;
    `facts` the kind-specific structured facts behind the buttons — an
    `oom` cell's `smaller` ("1152x1152", the record's offer) and
    `held_before` ({turn_idx, pos, n}: this engine landed that very size
    before, so the size isn't the problem — A11)."""
    id: str
    turn_id: str
    pos: int
    engine: str
    status: SlotStatus
    attempt: int
    seed: int | None
    committed_usd: float | None
    charged_usd: float | None
    cause: str | None
    detail: str | None
    retry_of: str | None
    kind: str | None
    job_ref: str | None
    frame: str | None
    facts: dict[str, Any] | None
    started_at: str | None
    finished_at: str | None
    created_at: str


class Turn(BaseModel):
    id: str
    workspace_id: str
    idx: int
    prompt: str | None
    dials: dict[str, Any]
    anchor_x: float | None
    anchor_y: float | None
    created_at: str
    slots: list[Slot]


class WorkspaceDetail(Workspace):
    turns: list[Turn]
    charged_usd: float                   # summed from the ledger


class RunResult(BaseModel):
    turn: Turn
    seq: int                             # poll cursor bootstrap


# --- authored tables served as-is: the dial normalization and the
# capability matrix. Both files are the fact's home; these models are the
# wire shape the web reads and nothing more — unknown keys ($doc, authoring
# notes) drop on the way out. The server never interprets a dial.


class DialEngine(BaseModel):
    """One engine's row in one dial (configs/dials.json). transmits
    false is a badge, never a filter (law 12): `note` is the plain-words reason
    the control shows. Sliders carry `input` + `anchors` (position → raw,
    interpolated by the client) + `step`; the frame dial carries `inputs`;
    the seed dial `input`."""
    transmits: bool
    note: str
    input: str | None = None
    inputs: list[str] | None = None
    anchors: list[list[float]] | None = None
    step: float | None = None


class Dial(BaseModel):
    id: str
    label: str
    effect: str                          # the permanent plain-effect line
    kind: str | None = None              # None = slider · frame · seed · meta
    applies: str | None = None
    note: str | None = None
    engines: dict[str, DialEngine]
    aspects: list[str] | None = None     # frame
    default: dict[str, int] | None = None
    lock_scope: str | None = None        # seed
    sweep_slots: int | None = None       # meta (sweep)
    targets: list[str] | None = None


class DialPosition(BaseModel):
    min: int
    max: int
    default: int


class Dials(BaseModel):
    updated: str
    position: DialPosition
    dials: list[Dial]


class EngineCapability(BaseModel):
    """One capability flag with its verification state (law 12):
    unverified is shown as unverified, never guessed better."""
    value: Any = None
    state: Literal["verified", "unverified"]
    note: str
    evidence: str | None = None


class EngineParam(BaseModel):
    """One handle a 3D engine's Advanced fold may offer (matrix
    `params[verb][key]`). A capability says whether a channel exists; this
    says what the artist may turn, and it carries its own verification state
    for the same reason a capability does — a range nobody has run is shown
    as unverified rather than badged from the engine's docs (law 12).

    `supported: false` is the dashed row: a handle this engine does NOT have,
    published with `why` so the fold lists it and states the absence instead
    of leaving a gap the artist has to interpret. Such a row carries no
    control, which is why everything but `label` is optional here."""
    label: str
    effect: str | None = None
    type: Literal["enum"] | None = None
    choices: list[Any] = []
    default: Any = None
    state: Literal["verified", "unverified"] | None = None
    # what was run to earn `verified` — the same shape a capability's carries,
    # so a badge can always be traced to the bench that made it
    evidence: str | None = None
    supported: bool = True
    why: str | None = None


class Engine(BaseModel):
    """One row of the capability matrix (`configs/engines.json`, law 12).

    `verbs` is the 3D lane's own flag (law 16): the steps this engine
    serves. It is a LIST, not a flag per verb, because the roster asks it one
    way — "who does this verb" — and law 12's answer is to draw everyone and dash
    the rest. An image engine leaves it empty: the image lane's steps are
    presets, not engine-declared, so an empty list here means "not that kind of
    question", never "serves nothing".

    `params` is the 3D lane's other one: the handles a surface may
    offer, keyed by verb then by the runner's own param name. It is an OFFER,
    not the runner's whitelist — the runner takes more, and a handle the
    product chose not to expose is simply absent (test_mesh.py holds the
    subset). An image engine leaves it empty for the same reason it leaves
    `verbs` empty: its handles are the preset's `inputs`."""
    id: str
    name: str
    lane: Literal["local", "external"]
    payload: Literal["image", "3d"]
    verbs: list[str] = []
    params: dict[str, dict[str, EngineParam]] = {}
    price: str
    note: str | None = None
    capabilities: dict[str, EngineCapability]


class ExternalEngineLane(BaseModel):
    """The external lane's defaults — the catalogue is live, so there are
    no authored per-model rows (configs/engines.json externalLane)."""
    lane: Literal["external"]
    payload: str
    price: str
    catalogue: str
    defaults: dict[str, EngineCapability]


class Engines(BaseModel):
    updated: str
    engines: list[Engine]
    external: ExternalEngineLane


class RerollResult(BaseModel):
    """Reroll: the same cell re-armed (same engine, same dials, new seed)."""
    slot: Slot
    seq: int


class NodeMesh(BaseModel):
    """What a landed mesh IS: a slim, curated projection off the
    node's `submitted_graph.mesh_job`, never the envelope itself (Node's
    standing rule: blobs stay server-side). Every fact here was measured by
    `funnel.py`'s own `validate()`; nothing is guessed, and a fact the
    record doesn't carry is null rather than defaulted (law 12: unverified
    capability is shown as unverified, never invented).

    `runner_job_id` is what `/nodes/{nid}/turntable` looks the orbit up by,
    and `has_turntable` is what a client asks before requesting a plate — a
    mesh without one keeps its card and its word, it is never hidden.

    `watertight` reduces funnel's PER-GEOMETRY list to the one question a
    face asks; an empty list claims nothing. TRELLIS.2 legitimately returns
    false here (open surfaces are not a failure), so this is a fact on the
    face, not a verdict."""
    engine: str | None
    runner_job_id: str | None
    triangles: int | None
    vertices: int | None
    watertight: bool | None
    textured: bool | None
    file_mb: float | None
    has_turntable: bool
    # The DECIMATE pair. `asked_triangles` is what the run REQUESTED,
    # kept beside the count the mesh actually reached, because on a machine
    # UV atlas those differ by design — a TRELLIS.2 mesh floors at ~8–10% of
    # its source and no flag argues with it (funnel.decimate's docstring
    # carries the measurement). A surface that only printed `triangles` would
    # be honest about the artefact and silent about the refusal. Null on every
    # node that was not decimated, which is what makes the pair readable.
    asked_triangles: int | None = None
    # whether `-sa` forced the count: the difference between "this is where
    # the mesh stopped" and "this is the number, and the surface paid for it"
    forced: bool = False
    # Whether this mesh kept its voxel field. It is the whole of
    # whether TRELLIS.2 can re-extract from it, so the surface reads it to
    # dash the row with a reason rather than offering a run that would be
    # refused (law 12).
    fields_kept: bool = False


class Node(BaseModel):
    """A compact landed-material view, slimmed by api_v4._node_view.

    This view omits paths, submitted graphs, external envelopes and dials.
    Explicit Crexil provenance reads expose saved requests and settings separately;
    current runtime definitions never substitute for a take's historical facts.
    The card footer uses turn_idx and slot_pos; its letters identify slots.
    """
    id: str
    workspace_id: str
    turn_id: str | None                  # imported nodes have neither
    slot_id: str | None
    take: int | None
    payload: Literal["image", "3d"]      # grows only when a payload lands
    verb: str
    prompt: str | None
    engine: str | None
    seed: int | None
    duration_ms: int | None
    committed_usd: float | None
    charged_usd: float | None
    set_aside: bool
    x: float | None                      # NULL until a drag persists
    y: float | None
    created_at: str
    parents: list[str]                   # ordered — plural parents
    turn_idx: int | None
    slot_pos: int | None
    # When this take left for the asset tree, or null. Read from the
    # `node.promoted` ledger row, not a column — promotion is an
    # append-only fact. It is the one permanent mark on a node
    # (un-promoting happens only in the asset tree), so every surface that
    # draws a node can draw it.
    promoted_at: str | None = None
    # A 3d take's own facts, null on an image. Derived like
    # `promoted_at` rather than stored — the mesh record already lives on
    # the node, this is the slice of it a face may see.
    mesh: NodeMesh | None = None


class TrayNode(Node):
    """A tray entry: the node plus what purging it
    would do — bytes on disk now (artefact + thumb + masks) and how many
    live children would keep only the prompt record."""
    bytes: int
    children: int


class Tray(BaseModel):
    nodes: list[TrayNode]              # newest set-aside first
    bytes: int                           # the "frees 1.4 GB" readout


class Purge(BaseModel):
    """The one irreversible verb (law 7). Ids must all be live set-aside
    nodes of the workspace — purge empties the tray, never the canvas —
    and the call is all-or-nothing, so the held button's count is exact."""
    ids: list[str] = Field(min_length=1)


class PurgeResult(BaseModel):
    purged: int
    freed_bytes: int
    children: int                        # live nodes that lost a parent image
    seq: int


class BackfillRefusal(BaseModel):
    slot_id: str
    pos: int
    reason: str                          # plain words, cost stated


class BackfillResult(BaseModel):
    """Backfill: every set-aside slot of the turn re-armed with a fresh
    seed, in one action. Refusals are per slot and never stop the others
    (law 1)."""
    turn: Turn
    rerolled: int
    refused: list[BackfillRefusal]
    seq: int


class NodePos(BaseModel):
    x: float
    y: float


class NodePosition(BaseModel):
    id: str
    x: float
    y: float


class ViewUpdate(BaseModel):
    """View-state write (exact restore): viewport and dials ride the
    same db call. Omitted field = untouched."""
    viewport: dict[str, Any] | None = None
    dials: dict[str, Any] | None = None


class Passes(BaseModel):
    """A region take's pipeline position: pass `current` of
    `total` — the queue view's "running · pass 2 of 4"."""
    current: int
    total: int


class SlotView(BaseModel):
    """One queue-strip row. Observed time only: no ETAs anywhere
    (law 17)."""
    id: str
    turn_id: str
    turn_idx: int
    workspace_id: str
    engine: str
    status: SlotStatus
    attempt: int
    retry_of: str | None                 # law 9: an automatic retry says so
    position: int | None                 # local lane: 0 = running, 1+ = queued
    elapsed_ms: int | None               # running rows: observed, server clock
    committed_usd: float | None
    charged_usd: float | None
    cancel_requested: bool
    awaiting: bool                       # late: the detached ride still lives
    bench: str | None                    # held: the covering bench scope
    passes: Passes | None = None       # region takes only
    created_at: str


class LocalLane(BaseModel):
    label: str                           # law 13: the one-GPU truth, server-sent
    depth: int                           # what a new Run queues behind
    slots: list[SlotView]


class ExternalLane(BaseModel):
    label: str
    concurrency: int
    active: int
    slots: list[SlotView]


class Lanes(BaseModel):
    local: LocalLane
    external: ExternalLane


class Bench(BaseModel):
    scope: str
    kind: str
    cause: str                           # law 1: one cause record, plain words


class RetryWait(BaseModel):
    """B2: a queued take waiting for its next automatic attempt;
    attempt i of n, and when the next try fires (`next_try_at`, server
    clock; `window` says whose clock: the provider's Retry-After or the
    cockpit's default). The cell counts down from it; GIVE UP is the
    slot's cancel."""
    slot_id: str
    kind: str
    attempt: int
    of: int
    wait_s: float
    window: Literal["provider", "cockpit"]
    next_try_at: str


class GpuReading(BaseModel):
    """One live look at the card, in bytes (C2/E6): ComfyUI's own
    /system_stats read the cockpit's way. `foreign_bytes` is what neither
    the engine nor free space accounts for: something else's hold (the
    desktop's slice included, which is why the threshold sits above 0).
    WSL2 cannot see Windows processes — a hold has a size, never a name."""
    total_bytes: int
    free_bytes: int
    comfy_bytes: int
    foreign_bytes: int
    name: str


class GpuState(BaseModel):
    """C2 in the queue view: the last reading, whether the lane is
    re-checking the card on its own, and whether HOLD THE QUEUE stopped
    that."""
    reading: GpuReading | None
    rechecking: bool
    manual_hold: bool


class GpuCheckResult(BaseModel):
    """CHECK AGAIN's answer: the reading now; `cleared` = the lane was
    held for the card and this look freed it."""
    reading: GpuReading | None
    held: bool
    cleared: bool
    threshold_bytes: int
    rechecking: bool


class GpuHoldResult(BaseModel):
    held: bool
    rechecking: bool


class Headroom(BaseModel):
    """E6's fact sheet at Run: the card as it is now and the
    staging drive's free space, plus this card's own bytes-per-pixel so
    the composer can say how much the slots need from the frames it
    knows. `gpu` null = ComfyUI couldn't say (nothing is claimed);
    `gpu_held` = the foreign hold is at or over the C2 threshold."""
    gpu: GpuReading | None
    gpu_hold_threshold_bytes: int
    gpu_held: bool
    disk_free_bytes: int
    disk_margin_bytes: int
    disk_path: str
    bytes_per_px: float
    bytes_per_px_source: Literal["record", "fallback"]
    at: str


class BillCheck(BaseModel):
    """B4: a charged-but-blank slot's free bill re-ask in flight,
    visible while it runs (law 9), recorded as events either way."""
    slot_id: str
    ask: int
    of: int


class QueueView(BaseModel):
    seq: int
    now: str                             # the clock elapsed_ms was computed with
    depth: int
    lanes: Lanes
    held: list[SlotView]
    late: list[SlotView]
    benches: list[Bench]
    checks: list[BillCheck]
    retries: list[RetryWait]           # B2's countdowns
    gpu: GpuState                      # C2's card state


class CancelResult(BaseModel):
    """Cancel in one breath: landed stays (kept) · queued/held cost nothing
    (skipped) · running local is abandoned (interrupting) · running external
    is already paid, the amount stated (cancelled_in_flight + committed_usd,
    resting late)."""
    slot_id: str
    action: CancelAction
    status: SlotStatus | None = None   # kept/unowned: what the slot is
    committed_usd: float | None = None   # cancelled_in_flight: the stated amount


class InFlight(BaseModel):
    slot_id: str
    committed_usd: float | None


class TurnCancelResult(BaseModel):
    kept: int
    skipped: int
    interrupted: int
    in_flight: list[InFlight]


class Unbench(BaseModel):
    scope: str                           # "lane:local" | "lane:external" | "engine:<id>"


class UnbenchResult(BaseModel):
    scope: str
    cleared: bool


class Event(BaseModel):
    """One ledger row, verbatim. `seq` is the resume cursor; the poll and
    any push transport share this shape."""
    seq: int
    ts: str
    type: str
    workspace_id: str | None
    turn_id: str | None
    slot_id: str | None
    node_id: str | None
    charged_usd: float | None
    payload: dict[str, Any]


class Events(BaseModel):
    events: list[Event]
    seq: int                             # last delivered, or the since= echo


# --- promote ---------------------------------------------------


class PromoteTarget(BaseModel):
    """Where promotion writes, and whether it could right now (D5).

    A READING, not a promise: the write validates again immediately before
    it moves, because the drive can go between this and the hold.
    """
    path: str
    # the same folder in a Windows user's spelling (`D:\PROMOTED`),
    # or null off a Windows drive. Printed BESIDE `path`, never instead — a
    # refusal, a manifest and a log all speak the cockpit's own spelling.
    windows_path: str | None
    ok: bool
    message: str | None                  # what's wrong, in the artist's words
    remedy: str | None                   # what to do about it
    where: str                           # which disk this path is on
    source: Literal["env", "set-here"]   # .env's default, or re-pointed here
    default_path: str                    # what .env says, for RESET
    roots: list[str]                     # where a target may go at all
    roots_said: str                      # the same, in the artist's spelling


class PromoteRepoint(BaseModel):
    r"""The re-point offer. An empty path clears the override and
    falls back to .env. A path that can't take a promotion is refused in
    words and never stored — the cockpit never remembers a broken target.

    Takes either spelling: `D:\SOMETHING` is translated at the edge,
    because that is how a Windows user names the folder."""
    path: str = ""


class PromoteFolder(BaseModel):
    """One folder as the picker hands it back: a handle the server
    minted. `path` is what a re-point takes, verbatim; the face never
    composes one. `ok` says whether it could take a promotion right now and
    `why` says why not, so an unfit folder is shown de-emphasised with its
    reason rather than hidden (law 12)."""
    name: str
    path: str
    windows_path: str | None
    ok: bool
    why: str | None


class PromoteBrowse(BaseModel):
    """One level of the promotion drive, for the picker. `here` is
    the folder listed, or null at the top — the roots themselves (the
    fence, enumerated). `parent` is where UP goes: a folder's path, "" for
    the top, or null when there is nowhere up (a lone root). Reads only."""
    here: PromoteFolder | None
    parent: str | None
    folders: list[PromoteFolder]
    note: str | None                     # when the list can't be read, why


class PromoteFile(BaseModel):
    """One file this promotion would write, by its final path."""
    path: str
    what: str                            # curated static words


class PromotePreview(BaseModel):
    """What the hold would do, stated before it (a spend / manifest
    summary spelled out before the hold). Nothing is written by
    asking."""
    node_id: str
    asset_id: str                        # the id this promotion would carry
    folder: str                          # PROMOTE_DIR/<workspace slug>[/…]
    files: list[PromoteFile]
    facts: str                           # the take's identity line
    cost: str                            # law 5 — say what it cost, always
    replayable: str                      # The tier, in words
    target: PromoteTarget
    already: list[str]                   # ISO stamps of earlier promotions


class Promotion(BaseModel):
    """The append-only fact: PNG + manifest in the asset tree."""
    node_id: str
    asset_id: str
    promoted_path: str
    manifest_path: str
    files: list[str]
    promoted_at: str
    seq: int
