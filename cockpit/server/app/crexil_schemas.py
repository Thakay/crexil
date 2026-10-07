"""Shared artist and agent preparation contracts.

Every mutation names its participant and delivery ID. Revisions protect saved
work across views; changing the request requires a new delivery ID. Flexible
control values retain unsupported inputs until the participant resolves them.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from .schemas import Actor, ControlType, HealthComfy, Node, SetupStatus, Turn

Payload = Literal['image', '3d', 'audio']


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=8, max_length=100)
    actor: Actor


class RecordAssessment(Command):
    reason: str = Field(min_length=1, max_length=4000)
    node_ids: list[str] = Field(default_factory=list, max_length=32)
    direction_id: str | None = None
    turn_id: str | None = None
    previous_id: str | None = None


class DecisionView(BaseModel):
    id: str
    event_id: str
    seq: int
    created_at: str
    kind: Literal['selection', 'sequence_choice', 'assessment']
    actor: Actor | None
    reason: str | None
    node_ids: list[str]
    direction_id: str | None
    turn_id: str | None
    sequence_id: str | None
    previous_id: str | None
    revised_by: str | None
    historical: bool


class ProjectRecord(BaseModel):
    decisions: list[DecisionView]
    next_before: int | None
    material_ids: list[str]
    direction_ids: list[str]
    turn_ids: list[str]


class MaterialRecord(BaseModel):
    node_id: str
    request: dict | None
    controls: dict
    submitted: dict | None
    regions: dict | None
    identity_hidden: bool | None = None      # a blind run's take before its reveal


class Reference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str
    contribution: str = Field(default='', max_length=4000)
    role: str | None = None
    strength: float | None = None


class OperationChoice(BaseModel):
    operation_id: str
    revision: str
    count: int = Field(default=1, ge=1, le=16)
    controls: dict[str, Any] = Field(default_factory=dict)


class ImageBox(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: int = Field(default=0, ge=0, strict=True)
    y: int = Field(default=0, ge=0, strict=True)
    width: int = Field(ge=1, le=64_000_000, strict=True)
    height: int = Field(ge=1, le=64_000_000, strict=True)


class ImagePlacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str
    crop: ImageBox | None = None
    placement: ImageBox | None = None


class ImagePreparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['crop', 'resize', 'arrange'] = 'crop'
    width: int = Field(default=1024, ge=1, le=8192, strict=True)
    height: int = Field(default=1024, ge=1, le=8192, strict=True)
    resample: Literal['nearest', 'lanczos'] = 'lanczos'
    background: Literal['transparent', 'white', 'black'] = 'transparent'
    items: list[ImagePlacement] = Field(default_factory=list, max_length=16)


class RegionInstruction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    action: Literal['keep', 'modify', 'replace', 'delete'] = 'modify'
    prompt: str = Field(default='', max_length=4000)
    mask_id: str | None = None


class Preparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['generate', 'continue', 'combine', 'region', 'custom', 'mesh', 'decimate', 'texture', 'import', 'prepare',
                    'voice-design', 'speak', 'sfx'] = 'generate'
    prompt: str = Field(default='', max_length=32000)
    references: list[Reference] = Field(default_factory=list, max_length=32)
    operations: list[OperationChoice] = Field(default_factory=list, max_length=32)
    controls: dict[str, Any] = Field(default_factory=dict)
    instructions: str = Field(default='', max_length=32000)
    attempt_limit: int = Field(default=16, ge=1, le=1000)
    spending_limit_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    spending_policy: Literal['hard_cap', 'published_tariff'] = 'hard_cap'
    purpose: Literal['creative_request', 'operation_test'] = 'creative_request'
    image_preparation: ImagePreparation | None = None
    regions: list[RegionInstruction] = Field(default_factory=list, max_length=8)
    region_overlap_policy: Literal['nd3-v1'] | None = None
    region_regenerate_unclaimed: bool = False
    # Engine identity is withheld from every read of the run until an
    # authored reveal; take positions are arranged once at Run (law 8).
    # Null or false: not blind.
    blind: bool | None = None


class MaskAdjustment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    crop: ImageBox | None = None
    resize_to_source: bool = False
    explanation: str = Field(min_length=1, max_length=4000)


class SaveMask(Command):
    source_node_id: str
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    name: str = Field(min_length=1, max_length=200)
    data_base64: str | None = Field(default=None, max_length=32 * 1024 * 1024)
    previous_mask_id: str | None = None
    adjustment: MaskAdjustment | None = None


class MaskSourceView(BaseModel):
    node_id: str
    sha256: str
    width: int
    height: int
    coordinates: str


class MaskView(BaseModel):
    id: str
    workspace_id: str
    source_node_id: str
    source_sha256: str
    width: int
    height: int
    name: str
    sha256: str
    original_sha256: str
    previous_mask_id: str | None
    transform: dict
    actor: Actor
    created_at: str
    available: bool


class CreateProject(Command):
    name: str = Field(min_length=1, max_length=200)
    brief: str = Field(default='', max_length=32000)


class ProjectLimits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_limit: int | None = Field(default=None, ge=0, strict=True)
    spending_limit_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    spending_policy: Literal['hard_cap', 'published_tariff'] = 'hard_cap'


class UpdateProject(Command):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    brief: str = Field(default='', max_length=32000)
    instructions: str = Field(default='', max_length=32000)
    limits: ProjectLimits | None = None


class ControlExecution(Command):
    revision: int = Field(ge=0)
    action: Literal['pause', 'resume', 'cancel']
    reason: str = Field(default='', max_length=4000)


class AttemptMove(Command):
    """One participant's move on one attempt: the attempt as they saw it
    (a later attempt of the same slot refuses as stale), and why."""
    attempt: int = Field(ge=0)
    reason: str = Field(default='', max_length=4000)


class AttemptMoveView(BaseModel):
    """What one move on one attempt did, recorded with its participant
    (law 23). `words` say it plainly, with what it cost, and name no engine
    (laws 3 and 5); `released` counts the held attempts a resume sent back to
    the queue."""
    slot_id: str
    attempt: int
    action: Literal['resume', 'cancel']
    words: str
    released: int = 0
    actor: Actor
    reason: str


class ExecutionControlView(BaseModel):
    scope: Literal['project', 'run', 'sequence']
    target_id: str
    workspace_id: str
    revision: int
    paused: bool
    cancel_requested: int = 0
    last_action: str | None
    actor: Actor | None
    reason: str
    requested_at: str | None


class CreateDirection(Command):
    title: str = Field(default='New direction', min_length=1, max_length=200)
    row_idx: int | None = Field(default=None, ge=0)
    setup: Preparation = Field(default_factory=Preparation)


class ConnectionView(BaseModel):
    """One provider's first-use state. A key is described, never shown."""
    provider: str
    label: str
    ok: bool | None                    # None: not checked
    detail: str
    key_source: Literal['env', 'stored'] | None = None


class SetupOverview(BaseModel):
    preview: bool
    comfyui_url: str
    comfyui: HealthComfy
    openrouter: SetupStatus
    connections: list[ConnectionView] = Field(default_factory=list)


class OperationRequirements(BaseModel):
    operation_id: str
    revision: str
    checked_at: str
    runtime_url: str
    state: str
    findings: list[dict]
    gaps: list[str]
    limitations: list[str]


class UpdateDirection(Command):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    setup: Preparation


class DirectionState(Command):
    revision: int = Field(ge=0)
    action: Literal['set_aside', 'restore', 'discard', 'reuse']


class DisplaySelection(Command):
    revision: int = Field(ge=0)
    node_ids: list[str] = Field(max_length=1000)
    reason: str = Field(default='', max_length=4000)


class ImportMaterial(Command):
    filename: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(max_length=180 * 1024 * 1024)
    parents: list[str] = Field(default_factory=list, max_length=32)
    tool: str = Field(default='', max_length=200)
    explanation: str = Field(default='', max_length=4000)
    direction_id: str | None = None


class ReuseSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_project_id: str
    node_id: str


class CreateReuse(Command, ReuseSelection):
    review_token: str
    explanation: str = Field(default='', max_length=4000)


class ReuseFile(BaseModel):
    filename: str
    sha256: str
    size_bytes: int


class ReuseReview(BaseModel):
    token: str
    source_project_id: str
    source_project_name: str
    source_node_id: str
    filename: str
    payload: str
    files: list[ReuseFile]
    total_bytes: int
    missing: list[str]
    errors: list[str]
    ready: bool


class DeliverySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_ids: list[str] = Field(min_length=1, max_length=64)
    destination: str = Field(default='', max_length=2000)


class CreateDelivery(Command, DeliverySelection):
    review_token: str


class DeliveryFile(BaseModel):
    path: str
    sha256: str
    size_bytes: int


class DeliveryItem(BaseModel):
    node_id: str
    filename: str
    asset_id: str
    state: Literal['prepared', 'incomplete', 'complete']
    files: list[DeliveryFile]
    requires_review: bool = False
    error: str | None = None
    missing: list[str] = Field(default_factory=list)
    completed_at: str | None = None


class DeliveryPackage(BaseModel):
    """The package folder a delivery writes, and its own files (`records.csv`,
    `decisions.json`, `package.json`), which land once every take is in place."""
    path: str
    state: Literal['prepared', 'incomplete', 'complete']
    error: str | None = None
    completed_at: str | None = None
    files: list[str]


class DeliveryReview(BaseModel):
    token: str
    destination: str
    target: dict
    ready: bool
    errors: list[str]
    items: list[DeliveryItem]
    package: DeliveryPackage | None = None


class DeliveryView(BaseModel):
    id: str
    workspace_id: str
    destination: str
    actor: Actor
    created_at: str
    items: list[DeliveryItem]
    package: DeliveryPackage | None = None


class ProjectView(BaseModel):
    id: str
    name: str
    slug: str
    viewport: dict
    dials: dict
    created_at: str
    updated_at: str
    brief: str
    instructions: str
    revision: int
    limits: dict


class DirectionView(BaseModel):
    id: str
    workspace_id: str
    row_idx: int
    title: str
    state: str
    setup: dict
    selection: list[str]
    revision: int
    set_aside: bool
    actor: Actor | None
    created_at: str
    updated_at: str
    turn_ids: list[str]
    node_ids: list[str]
    sequence_id: str | None = None
    sequence_step_key: str | None = None
    sequence_template: bool = False
    latest_decision: DecisionView | None = None


class SequenceDependency(BaseModel):
    model_config = ConfigDict(extra='forbid')
    step_key: str = Field(min_length=1, max_length=80)
    selection: Literal['all_successful', 'chosen']
    contribution: str = Field(default='', max_length=4000)
    placement: Literal['before_fixed', 'after_fixed'] = 'after_fixed'


class SequenceStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    direction_id: str
    revision: int = Field(ge=0)
    input_mode: Literal['together', 'each'] = 'together'
    dependencies: list[SequenceDependency] = Field(default_factory=list, max_length=32)


class CreateSequence(Command):
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default='', max_length=32000)
    limits: ProjectLimits = Field(default_factory=lambda: ProjectLimits(attempt_limit=16, spending_limit_usd=0))
    steps: list[SequenceStep] = Field(min_length=1, max_length=32)


class UpdateSequence(Command):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default='', max_length=32000)
    limits: ProjectLimits


class UpdateSequenceStep(Command):
    revision: int = Field(ge=0)
    input_mode: Literal['together', 'each']
    dependencies: list[SequenceDependency] = Field(default_factory=list, max_length=32)


class StartSequence(Command):
    revision: int = Field(ge=0)
    review_token: str


class DecideSequence(Command):
    revision: int = Field(ge=0)
    direction_revision: int = Field(ge=0)
    candidate_token: str
    selections: dict[str, list[str]]
    reason: str = Field(min_length=1, max_length=4000)


class SequenceStepView(BaseModel):
    key: str
    ordinal: int
    direction_id: str
    input_mode: str
    dependencies: list[SequenceDependency]
    binding: dict | None
    decision: dict | None
    state: str
    reason: str
    candidates: dict[str, list[dict]]
    candidate_token: str
    runs: list[dict]


class SequenceView(BaseModel):
    id: str
    workspace_id: str
    title: str
    instructions: str
    limits: ProjectLimits
    state: str
    revision: int
    actor: Actor
    created_at: str
    updated_at: str
    steps: list[SequenceStepView]
    accounting: dict


class SequenceReview(BaseModel):
    sequence_id: str
    revision: int
    token: str
    ready: bool
    errors: list[str]
    snapshot: dict


class MaterialInfo(BaseModel):
    node_id: str
    sha256: str
    filename: str
    media_type: str
    width: int | None
    height: int | None
    origin: dict


class RetainedFile(BaseModel):
    id: str
    filename: str
    role: str
    media_type: str
    required: bool
    sha256: str | None
    size_bytes: int | None
    error: str | None
    available: bool
    file: str | None


class AudioFacts(BaseModel):
    """Measured once by the server at collection; the bytes never change.

    `loudness_lufs` is gated integrated loudness by ITU-R BS.1770-4 (null
    when no 400 ms block clears the absolute gate). `peaks` is the absolute
    sample peak of up to 256 equal spans, from 0 to 1, for drawing. `text` is
    the spoken words, `voice` the voice identity the engine reported, and
    `alignment` per-character timing when the engine reports it. Format facts
    are null only while a blind run withholds them."""
    container: str | None
    codec: str | None
    sample_rate: int | None
    channels: int | None
    duration_s: float
    loudness_lufs: float | None
    peak_dbfs: float | None
    peaks: list[float]
    text: str | None
    voice: dict | None
    alignment: dict | None


class MaterialNode(Node):
    output_index: int
    available: bool
    purged_at: str | None
    material: MaterialInfo | None
    retained_files: list[RetainedFile] = Field(default_factory=list)
    audio: AudioFacts | None = None


class ExecutionOutputView(BaseModel):
    id: str
    group_id: str
    identity: str
    ordinal: int
    node_id: str | None
    role: str
    filename: str
    media_type: str
    sha256: str | None
    size_bytes: int | None
    descriptor: dict
    error: str | None
    available: bool
    purged_at: str | None
    created_at: str


class ExecutionGroupView(BaseModel):
    id: str
    slot_id: str
    take: int
    state: str
    job_ref: str | None
    metadata: dict
    created_at: str
    updated_at: str
    outputs: list[ExecutionOutputView]
    identity_hidden: bool | None = None


class BlindRunView(BaseModel):
    """A run whose engine identity is withheld until an authored reveal."""
    turn_id: str
    workspace_id: str
    actor: Actor
    created_at: str
    revealed: bool
    revealed_at: str | None
    revealed_by: Actor | None


class ProjectSnapshot(BaseModel):
    project: ProjectView
    directions: list[DirectionView]
    nodes: list[MaterialNode]
    turns: list[Turn]
    charged_usd: float
    accounting: dict = Field(default_factory=dict)
    execution_controls: list[ExecutionControlView] = Field(default_factory=list)
    sequences: list[SequenceView] = Field(default_factory=list)
    restoration: dict | None = None
    event_seq: int
    execution_groups: list[ExecutionGroupView]
    blind_runs: list[BlindRunView] = Field(default_factory=list)


class ImportResult(BaseModel):
    node: dict
    direction: DirectionView


class OperationInput(BaseModel):
    """One public control. Absent facts stay null; producers may add more keys."""
    model_config = ConfigDict(extra='allow')
    key: str
    label: str | None = None
    type: ControlType | None = None
    default: Any = None
    required: bool | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[Any] | None = None
    min_length: int | None = None
    max_length: int | None = None
    supported: bool | None = None
    reason: str | None = None


class VoiceRule(BaseModel):
    """The voices a speech role takes: those designed or saved at `providers`.
    A voice take from any other provider is refused with `reason`, which the
    roster shows before the engine is chosen (law 12)."""
    providers: list[str]
    reason: str


class SourceRoleView(BaseModel):
    """One source an operation takes, in order, with the payloads it accepts
    (law 21), the reasons some takes of those payloads are refused, and the
    voices a speech role takes when its engine names them (`voices`; None takes
    any voice its provider's terms allow)."""
    role: str
    accepts: list[Payload]
    restrictions: list[str] = Field(default_factory=list)
    voices: VoiceRule | None = None


class TariffView(BaseModel):
    """A published price, exact in integer micro-dollars (one millionth of a US
    dollar). `measure` names the control or text the unit counts; a
    reservation from it is still a tariff, never a hard bound (law 22)."""
    unit: Literal['attempt', 'image', '1k_characters', 'second', 'generation']
    micro_usd: int = Field(ge=0)
    measure: str | None = None
    source: str = ''
    read_on: str = ''


class OperationView(BaseModel):
    id: str
    label: str
    revision: str
    actions: list[str]
    inputs: list[OperationInput]
    source_limit: int
    evidence: Literal['declared', 'observed', 'qualified']
    availability: Literal['verified', 'unverified', 'incomplete', 'unavailable']
    reasons: list[str]
    execution_location: str
    data_destination: str
    price_usd: float | None
    maximum_usd_per_attempt: float | None
    cost_basis: str
    # role names only; `sources` carries their accepted payloads
    source_roles: list[str] = Field(default_factory=list)
    sources: list[SourceRoleView] = Field(default_factory=list)
    output_payload: Literal['image', '3d', 'audio', 'file'] = 'image'
    limitations: list[str] = Field(default_factory=list)
    attempt_max: int = 16
    takes_per_attempt: int = 1
    provider: str | None = None
    price_unit: Literal['attempt', 'image', '1k_characters', 'second', 'generation'] | None = None
    tariff: TariffView | None = None
    pinned: bool = False


class ExecutionReview(BaseModel):
    direction_id: str
    revision: int
    token: str
    ready: bool
    errors: list[str]
    attempts: int
    ready_attempts: int
    blocked_attempts: int
    maximum_usd: float | None
    reserved_usd: float = 0
    # the same reservation, exact in integer micro-dollars
    reserved_micro_usd: int = 0
    # each operation of the run, by id: whether its attempts retry on their
    # own, visibly (law 9). Audio attempts do; image and 3D attempts never do.
    automatic_retry: dict[str, bool] = Field(default_factory=dict)
    snapshot: dict


class SubmitDirection(Command):
    revision: int = Field(ge=0)
    review_token: str = Field(min_length=64, max_length=64)


class SubmittedDirection(BaseModel):
    direction: DirectionView
    turn_id: str
    review_token: str


class CollectExecution(Command):
    take: int = Field(ge=1)


class RevealRun(Command):
    reason: str = Field(default='', max_length=4000)


class DecisionCandidate(BaseModel):
    """One take the decision was made among, as the record states it."""
    node_id: str
    position: int                      # frozen grid position (law 8)
    engine: str
    provider: str | None
    model: str | None
    seed: int | None
    cost_micro_usd: int | None         # charged when known, else committed


class DecisionRecord(BaseModel):
    """One decision, derived from a selection or an assessment event."""
    decision_id: str
    kind: Literal['selection', 'sequence_choice', 'assessment']   # an assessment chooses nothing
    project_id: str
    run_id: str | None                 # the turn
    direction_id: str | None
    verb: str | None
    brief: str                         # the prompt as typed
    candidates: list[DecisionCandidate]
    chosen: list[str]
    reason: str | None
    author: Actor | None
    blind: bool
    decided_at: str


class DecisionExport(BaseModel):
    """The artist's decision record: never a training or evaluation dataset,
    and it holds no audio."""
    project_id: str
    generated_at: str
    purpose: str
    records: list[DecisionRecord]


class RunAudio(BaseModel):
    """What an agent may know of an audio take: measured facts and the spoken words,
    never its bytes or an address to fetch them. ElevenLabs' terms forbid its audio
    as input to any machine learning, and an agent's language model is one."""
    duration_s: float
    loudness_lufs: float | None
    text: str | None
    voice: dict | None                 # withheld while the run is blind


class RunTake(BaseModel):
    """One landed take of a run, named the way the grid names it. Facts, ids and
    labels only: no bytes and no file address."""
    node_id: str
    label: str                         # run index, cell letter, ".n" for a later output: "3B", "3B.2"
    payload: Payload
    verb: str
    available: bool                    # the bytes are on disk and not purged
    set_aside: bool
    chosen: bool                       # in the run's displayed selection
    audio: RunAudio | None = None
    identity_hidden: bool | None = None


class RunCell(BaseModel):
    """One grid cell, an attempt, in its frozen position (law 8). A blind run's
    cell reads engine `hidden` with seed, per-take cost and timing withheld."""
    slot_id: str
    turn_id: str
    label: str
    position: int
    state: str                         # the slot's status
    attempt: int
    retrying: bool                     # the scheduler's own retry is queued or running (law 9)
    kind: str | None                   # the failure kind; it picks the next move (law 4)
    cause: str | None                  # the plain line (law 3)
    detail: str | None                 # the engine's raw text, at most 500 characters
    engine: str
    seed: int | None
    committed_usd: float | None
    charged_usd: float | None
    elapsed_ms: int | None
    collectable: bool                  # reported files are missing and can be collected for free
    takes: list[RunTake]
    identity_hidden: bool | None = None


class RunSubmission(BaseModel):
    """One submission of a run and its execution control; the control revision is
    what a pause, resume or cancel must echo."""
    turn_id: str
    idx: int
    created_at: str
    blind: bool
    revealed: bool
    control: ExecutionControlView


class RunView(BaseModel):
    """One run, compact and blind-safe, for agents and other light readers; never
    the project snapshot. Run totals and the cap stay visible (law 22)."""
    run_id: str                        # the direction
    project_id: str
    title: str
    state: str
    revision: int                      # what a pick or a revision of the draft must echo
    action: str
    prompt: str
    blind: bool
    cap_usd: float
    reserved_usd: float
    charged_usd: float
    submissions: list[RunSubmission]
    cells: list[RunCell]
    selection: list[str]
    decisions: list[DecisionView]      # the latest ten that concern this run
    summary: str                       # one plain line: counts by state, each cause once (law 1)
    event_seq: int                     # the cursor for /api/events


class StartVoiceSession(Command):
    project_id: str | None = Field(default=None, max_length=100)   # the project the panel is open on
    connection: Literal['webrtc', 'websocket'] = 'webrtc'


class EndVoiceSession(Command):
    elapsed_s: float = Field(ge=0, le=86400, allow_inf_nan=False)  # as the panel observed it (law 17)
    conversation_id: str | None = Field(default=None, max_length=200)


class VoiceSessionView(BaseModel):
    session_id: str
    project_id: str | None
    actor: Actor
    started_at: str
    max_seconds: int
    connection: Literal['webrtc', 'websocket']
    ended_at: str | None
    ended_by: Actor | None
    elapsed_s: float | None
    conversation_id: str | None


class VoiceUsage(BaseModel):
    """The voice director's minutes against the installation's cap. An open session
    counts its whole maximum until it ends (law 22); ended sessions count what the
    panel observed (law 17). The price is ElevenLabs' published list price per
    minute, an estimate; model use is billed on top."""
    ready: bool
    reasons: list[str]                 # why a session cannot start now, in plain words
    cap_seconds: int
    session_max_seconds: int
    used_seconds: float
    reserved_seconds: int
    left_seconds: float
    usd_per_minute: float
    open_sessions: list[VoiceSessionView]


class VoiceSessionStarted(BaseModel):
    """What the browser needs to start one conversation. The ElevenLabs key never
    leaves the server (law 18); the short-lived token or signed URL is handed over
    once and never stored."""
    session: VoiceSessionView
    conversation_token: str | None     # WebRTC
    signed_url: str | None             # WebSocket
    usage: VoiceUsage


class GraphTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str = ''
    field: str = ''


class PublicControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = ''
    label: str = ''
    type: ControlType = 'text'
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[Any] = Field(default_factory=list, max_length=1000)
    targets: list[GraphTarget] = Field(default_factory=list, max_length=32)
    when: dict[str, Any] = Field(default_factory=dict)


class SourcePort(GraphTarget):
    label: str = ''
    role: str = ''
    kind: Literal['image', 'mask'] = 'image'
    when: dict[str, Any] = Field(default_factory=dict)


class OutputPort(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str = ''
    field: str = 'images'
    ordinal: int | None = Field(default=None, ge=0)
    role: Literal['final', 'alternative', 'layer', 'mask', 'preview', 'companion', 'intermediate', 'unclassified'] = 'unclassified'
    payload: Literal['image', '3d', 'file'] = 'image'
    primary: bool = False
    required: bool = True
    when: dict[str, Any] = Field(default_factory=dict)


class OperationDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    graph: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default='', max_length=8000)
    actions: list[Literal['generate', 'continue', 'combine', 'custom']] = Field(default_factory=lambda: ['custom'])
    inputs: list[PublicControl] = Field(default_factory=list, max_length=128)
    sources: list[SourcePort] = Field(default_factory=list, max_length=32)
    outputs: list[OutputPort] = Field(default_factory=list, max_length=128)
    requirements: str = Field(default='', max_length=8000)


class OperationBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    runtime: Literal['comfy'] = 'comfy'
    cost_review: Literal['unknown', 'unmetered', 'paid'] = 'unknown'
    cost_note: str = Field(default='', max_length=4000)
    downloads: Literal['unknown', 'none', 'ready', 'required'] = 'unknown'
    dependency_note: str = Field(default='', max_length=4000)


class ImportOperation(Command):
    name: str = Field(min_length=1, max_length=200)
    source_name: str = Field(default='', max_length=255)
    source_text: str = Field(default='', max_length=8 * 1024 * 1024)
    source_node_id: str | None = None


class UpdateOperation(Command):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    definition: OperationDefinition
    binding: OperationBinding
    adaptation_note: str = Field(default='', max_length=8000)


class InspectOperation(Command):
    revision: int = Field(ge=0)


class RegistrationView(BaseModel):
    id: str
    name: str
    revision: int
    operation_revision: str
    source_name: str
    source_text: str
    source_sha256: str
    source_kind: str
    source: dict
    definition: OperationDefinition
    binding: OperationBinding
    adaptation_note: str
    actor: Actor
    created_at: str
    updated_at: str
    gaps: list[str]
    inspection: dict | None
    evidence: list[dict]
    revisions: list[dict]


class ReserveArchive(Command):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, strict=True)


class RestoreArchive(Command):
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    name: str | None = Field(default=None, min_length=1, max_length=200)


class ArchiveUpload(BaseModel):
    id: str
    filename: str
    size_bytes: int
    actor: Actor
    created_at: str
    state: Literal['waiting', 'ready']
    sha256: str | None
    review: dict | None


class ArchiveRestoreResult(BaseModel):
    project_id: str
    name: str
    unavailable_files: int


class ProjectBackup(BaseModel):
    id: str
    project_id: str
    project_name: str
    created_at: str
    sha256: str
    size_bytes: int
    records: dict[str, int]
    files: list[dict]
