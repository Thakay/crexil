# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==2.3.0", "httpx==0.28.1"]
# ///
"""Crexil's agent door: an MCP server over stdio through which an MCP client,
such as Claude Code, acts in Crexil with the same shared actions the artist uses.

The door is an HTTP client of one local Crexil server and nothing else. It never
imports server code or opens the store (law 19), and it talks to loopback
addresses only. Every write names this door's agent as `agent:<name>` and
carries a delivery id (law 23). A write whose response is lost is sent once more
with the same id and body, and the server answers a replay with the work it
already did, so nothing happens twice.

Money moves only within two caps. The run's own spending limit is enforced by
the server, and an unknown price counts as over it (law 22). This door adds its
own per-run ceiling, its `--max-run-usd` launch flag, $0 unless the artist raises
it. The ceiling compares money, not where an engine runs, so by default an agent
can run free work only, and a paid submission must restate the reviewed amount.

The engine listing stays small enough for a client's tool output: without a
payload or an action it names each engine's id, label, actions, payload, price
and readiness, and a call that names one adds where each engine runs, what
leaves the machine, its sources and its controls.

What an agent can do here: discover engines, open or create a project, prepare
a run, review its price, submit it, read the run, propose picks with a reason,
and read the decision record. What it cannot: pick, reveal a blind run, deliver,
change limits or start a voice session. The server refuses those to agents, and
this door offers no tool for them, because those decisions are the artist's
(law 15, law 23).

Tool results carry facts, ids, labels and reasons only. They never carry a
take's bytes, a file path or an address to fetch one. ElevenLabs' terms forbid
its audio as input to any machine learning, and the model behind an agent is one.

The HTTP core (`Door`) needs only httpx and pydantic. The MCP layer
(`build_server`) imports the `mcp` package when the server is built, so the core
can be tested without it.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Awaitable, Callable, Literal
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field

DEFAULT_URL = 'http://127.0.0.1:8000'
LOOPBACK = ('localhost', '127.0.0.1', '::1')
API = '/api/crexil/'
MICRO = Decimal(1_000_000)
WAIT_LIMIT_S = 55
# Where the artist raises the door's ceiling: the flag the door is launched with.
CEILING = "this door's ceiling, the --max-run-usd flag it is launched with (CREXIL_DOOR_MAX_RUN_USD)"
# Cells in these states are still moving; a wait ends when none is left.
MOVING = {'queued', 'running', 'late', 'retrying'}

ErrorKind = Literal['unreachable', 'unavailable', 'not_found', 'conflict', 'refused', 'not_allowed',
                    'not_built', 'over_door_ceiling', 'invalid_input', 'server_error']


class DoorError(Exception):
    """One refusal or failure: a plain line first (law 3), the next move, and a
    stable kind a client can act on (law 4)."""

    def __init__(self, kind: ErrorKind, line: str, next_move: str = '', *, delivery_id: str | None = None,
                 conflict: str | None = None):
        super().__init__(line)
        self.kind, self.line, self.next_move, self.delivery_id = kind, line, next_move, delivery_id
        self.conflict = conflict   # the server's named conflict, such as spending

    def __str__(self) -> str:
        return ' '.join(p for p in (self.line, self.next_move, f'(kind: {self.kind})') if p)


# --- what the tools return: facts, ids, labels and reasons only ---

class Connection(BaseModel):
    provider: str
    label: str
    ok: bool | None = Field(description='None when not checked')
    detail: str


def _unset(value) -> bool:
    return value is None


def _optional(description: str | None = None) -> Any:
    """A fact a result leaves out, rather than sending a null, when it has none."""
    return Field(default=None, exclude_if=_unset, description=description)


class Status(BaseModel):
    reachable: bool
    preview: bool = Field(description='An isolated preview generates nothing and spends nothing')
    agent: str = Field(description='The actor this door writes as')
    door_max_run_usd: float = Field(description="This door's per-run spending ceiling, its --max-run-usd launch flag; "
                                                "0 means free work only")
    connections: list[Connection]


class SourceRule(BaseModel):
    role: str
    accepts: list[str] = Field(description='Payload types this source may be')
    restrictions: list[str] = Field(description='Why some takes of those types are refused, in plain words')


class Control(BaseModel):
    key: str
    label: str | None = _optional()
    type: str | None = _optional()
    required: bool | None = _optional()
    default: Any = _optional()
    choices: list[Any] | None = _optional()
    min: float | None = _optional()
    max: float | None = _optional()
    min_length: int | None = _optional()
    max_length: int | None = _optional()
    supported: bool | None = _optional()
    reason: str | None = _optional()


DETAIL = 'Listed when the call names a payload or an action'


class Engine(BaseModel):
    id: str = Field(description='Pass this as an engine id to crexil_prepare_run')
    label: str
    actions: list[str] = Field(description='The run actions this engine serves, such as generate or voice-design')
    produces: str = Field(description='The payload a take of this engine is: image, audio or 3d')
    availability: str = Field(description='verified, unverified, incomplete or unavailable')
    reasons: list[str] = Field(description='Why the engine cannot run now, when it cannot')
    price_usd: float | None = Field(description='The price the catalogue states, per price_unit; null when it states none')
    price_unit: str | None = Field(description='What the price counts: attempt, image, 1k_characters, second or generation')
    location: str | None = _optional(f'Where the work runs. {DETAIL}')
    data_destination: str | None = _optional(f'What leaves the machine, and to whom. {DETAIL}')
    cost_basis: str | None = _optional(f'How the price is charged. {DETAIL}')
    takes_per_attempt: int | None = _optional(DETAIL)
    attempt_max: int | None = _optional(DETAIL)
    sources: list[SourceRule] | None = _optional(f'The takes a run of this engine may start from. {DETAIL}')
    controls: list[Control] | None = _optional(f'Values crexil_prepare_run accepts for this engine. {DETAIL}')


class Engines(BaseModel):
    engines: list[Engine]


class ProjectBrief(BaseModel):
    project_id: str
    name: str
    brief: str
    updated_at: str


class RunBrief(BaseModel):
    run_id: str
    title: str
    state: str
    action: str
    takes: int
    picked: int


class Decision(BaseModel):
    decision_id: str
    kind: str = Field(description='selection, sequence_choice or assessment; an assessment chooses nothing')
    author: str | None = Field(description='human:<name> or agent:<name>')
    reason: str | None
    take_ids: list[str]
    run_id: str | None
    at: str


class Projects(BaseModel):
    projects: list[ProjectBrief]


class Project(BaseModel):
    project_id: str
    name: str
    brief: str
    revision: int
    limits: dict[str, Any] = Field(description="The project's spending and attempt limits, which only the artist changes")
    charged_usd: float
    runs: list[RunBrief]
    recent_decisions: list[Decision]


class Created(BaseModel):
    project_id: str
    name: str
    delivery_id: str = Field(description='Replay with this id after a lost response; it never creates twice')


class EngineReview(BaseModel):
    id: str
    label: str
    count: int
    ready: bool
    errors: list[str]


class Review(BaseModel):
    run_id: str
    revision: int = Field(description='Pass as expected_revision when submitting')
    review_token: str = Field(description='Binds exactly what will run and what it may cost')
    ready: bool
    errors: list[str]
    attempts: int
    ready_attempts: int
    blocked_attempts: int
    reserved_usd: float = Field(description='What submitting reserves; restate it as confirm_reserved_usd')
    maximum_usd: float | None = Field(description='A hard bound when one exists; null for published-tariff prices')
    spending_policy: str
    door_max_run_usd: float
    within_door_ceiling: bool
    engines: list[EngineReview]
    next: str = Field(description='What to do next, in plain words')


class Prepared(BaseModel):
    run_id: str
    review: Review
    delivery_id: str


class Submitted(BaseModel):
    run_id: str
    turn_id: str = Field(description='This submission; a run may have several')
    reserved_usd: float
    submitted_attempts: int
    left_in_draft: list[str] = Field(description='Why some attempts stayed behind, when any did')
    delivery_id: str


class Audio(BaseModel):
    duration_s: float
    loudness_lufs: float | None
    text: str | None = Field(description='The spoken words, for speech')
    voice: dict | None = Field(description='Withheld while the run is blind')


class Take(BaseModel):
    take_id: str
    label: str = Field(description='How the grid names it, such as 3B or 3B.2')
    payload: str
    verb: str
    available: bool
    set_aside: bool
    chosen: bool = Field(description="In the run's displayed selection")
    audio: Audio | None = None


class Cell(BaseModel):
    label: str
    position: int = Field(description='Frozen grid position (law 8)')
    state: str
    attempt: int
    retrying: bool = Field(description="The scheduler's own visible retry is queued or running (law 9)")
    kind: str | None = Field(description='The failure kind, which picks the next move')
    cause: str | None = Field(description='The plain failure or hold line')
    engine: str = Field(description='"hidden" while a blind run is unrevealed')
    identity_hidden: bool
    seed: int | None
    committed_usd: float | None
    charged_usd: float | None
    elapsed_ms: int | None
    collectable: bool
    takes: list[Take]


class Submission(BaseModel):
    turn_id: str
    idx: int
    blind: bool
    revealed: bool
    paused: bool


class Run(BaseModel):
    run_id: str
    project_id: str
    title: str
    state: str
    revision: int
    action: str
    prompt: str
    blind: bool
    cap_usd: float
    reserved_usd: float
    charged_usd: float
    summary: str = Field(description='Counts by state, then each cause once')
    selection: list[str]
    submissions: list[Submission]
    cells: list[Cell]
    decisions: list[Decision]
    settled: bool = Field(description='No cell is queued, running or retrying; a draft is settled')


class Proposed(BaseModel):
    decision_id: str
    kind: str
    author: str | None
    take_ids: list[str]
    reason: str | None
    delivery_id: str


class Candidate(BaseModel):
    take_id: str
    position: int
    engine: str
    provider: str | None
    model: str | None
    seed: int | None
    cost_usd: float | None = Field(description="What the take's attempt cost, charged when known, else committed. "
                                               "An attempt is charged once however many takes it returns, so its cost "
                                               "is on the first of its takes here and null on the others; null too "
                                               "while a blind run is hidden")


class DecisionLine(BaseModel):
    decision_id: str
    kind: str
    run_id: str | None = Field(description='The run (direction) the decision concerns')
    turn_id: str | None
    verb: str | None
    brief: str = Field(description='The prompt as typed')
    candidates: list[Candidate]
    chosen: list[str]
    reason: str | None
    author: str | None
    blind: bool
    decided_at: str


class Decisions(BaseModel):
    purpose: str
    count: int
    truncated: bool
    records: list[DecisionLine]


class Found(BaseModel):
    decisions: list[Decision]
    take_ids: list[str]
    run_ids: list[str]


class EngineChoice(BaseModel):
    id: str = Field(description='An engine id from crexil_list_engines')
    count: int = Field(default=1, ge=1, le=16, description='Attempts on this engine')
    controls: dict[str, Any] = Field(default_factory=dict, description="Values for the engine's controls, by key")


class SourceRef(BaseModel):
    take_id: str
    contribution: str = Field(default='', max_length=4000, description='What this source should give the result')
    role: str | None = Field(default=None, description="The engine's source role, such as Voice")


# --- plain text: server words keep their meaning, never an address or a path ---

_ADDRESS = re.compile(r'\b(?:https?|wss?)://\S+')
_PATH = re.compile(r'(?<![\w.])/(?:home|tmp|mnt|var|srv|opt|Users|root|private|etc)/\S+')


def _place(match: re.Match) -> str:
    address, tail = match.group(0), ''
    while address and address[-1] in '.,;:)]\'"':
        address, tail = address[:-1], address[-1] + tail
    host = urlparse(address).hostname or ''
    return ('this machine' if host in LOOPBACK else host or 'a remote address') + tail


def plain(text: str | None) -> str | None:
    """Server-written text with each address reduced to where it is (this machine,
    or a remote host's name) and each filesystem path withheld, so a reason keeps
    its meaning without handing an agent something to fetch."""
    if text is None:
        return None
    return _PATH.sub('(path withheld)', _ADDRESS.sub(_place, text))


def actor_name(actor: dict | None) -> str | None:
    return f"{actor['kind']}:{actor['name']}" if actor else None


def dollars(micro: Decimal) -> str:
    """Micro-dollars as Crexil states money: cents when the amount has no finer
    part ("$0.00", "$0.60"), else every digit it has ("$0.0336")."""
    amount = (micro / MICRO).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
    return f'${amount:.2f}' if amount == amount.quantize(Decimal('0.01')) else f'${amount.normalize():f}'


def micro_of(usd: float | int | str) -> Decimal:
    return (Decimal(str(usd)) * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def check_loopback(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in LOOPBACK or parsed.username or parsed.password:
        raise ValueError('The door talks to a local Crexil server only: an http address on localhost, 127.0.0.1 or ::1.')
    return url.rstrip('/')


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get('detail')
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, str) and detail.strip():
        return plain(detail.strip())
    if isinstance(detail, list):
        parts = [f"{'.'.join(str(x) for x in item.get('loc', [])[1:]) or 'request'}: {item.get('msg', '')}"
                 for item in detail if isinstance(item, dict)]
        return 'The request was not valid: ' + '; '.join(parts[:5]) + '.'
    return f'Crexil answered {response.status_code}.'


def error_for(response: httpx.Response, *, delivery_id: str | None = None) -> DoorError:
    """Map one answer to the door's fixed kinds. The server's plain line comes
    first; the kind and the next move follow."""
    status, line = response.status_code, _detail(response)
    if status == 403:
        return DoorError('not_allowed', line, "This is the artist's act. Propose takes with a reason instead.",
                         delivery_id=delivery_id)
    if status == 404:
        return DoorError('not_found', line, 'Read the project or run again to get current ids.', delivery_id=delivery_id)
    if status == 409 and response.headers.get('x-crexil-conflict') == 'spending':
        return DoorError('conflict', line, 'Spending moved since your review. Review the run again, then submit '
                         'with its new token and amount if the artist still wants it.', delivery_id=delivery_id,
                         conflict='spending')
    if status == 409:
        return DoorError('conflict', line, 'Someone changed this since you read it. Read it again and use the '
                         'new revision or token.', delivery_id=delivery_id)
    if status in (400, 422):
        return DoorError('refused', line, 'Nothing was recorded or sent to an engine.', delivery_id=delivery_id)
    if status == 501:
        return DoorError('not_built', line, 'This feature is not built in this version.', delivery_id=delivery_id)
    if status == 503:
        return DoorError('unavailable', line, 'The server or an engine is starting or paused. Try again shortly, '
                         'or read crexil_status.', delivery_id=delivery_id)
    return DoorError('server_error', line, 'Crexil failed on its side and recorded the failure. Try again, or '
                     'ask the artist to look.', delivery_id=delivery_id)


class Door:
    """The HTTP core: one local Crexil server, one agent identity, one ceiling."""

    def __init__(self, url: str = DEFAULT_URL, agent_name: str = 'Claude', max_run_usd: float | str = 0, *,
                 client: httpx.AsyncClient | None = None, poll_s: float = 1.0):
        self.url = check_loopback(url)
        name = agent_name.strip()
        if not name or len(name) > 120:
            raise ValueError('Name the agent in 1 to 120 characters; the door writes as agent:<name>.')
        self.actor = {'kind': 'agent', 'name': name}
        self.ceiling = micro_of(max_run_usd)
        if self.ceiling < 0:
            raise ValueError("The door's ceiling is a dollar amount of zero or more.")
        self.poll_s = poll_s
        self._client = client or httpx.AsyncClient(base_url=self.url, timeout=httpx.Timeout(60, connect=5),
                                                   follow_redirects=False)

    @property
    def ceiling_usd(self) -> float:
        return float(self.ceiling / MICRO)

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- transport ---

    def _unreachable(self) -> DoorError:
        return DoorError('unreachable', f'Crexil is not answering at {self.url}.',
                         'Start the Crexil server, then try again.')

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            response = await self._client.get(API + path, params=params)
        except httpx.TransportError:
            raise self._unreachable() from None
        if response.status_code >= 400:
            raise error_for(response)
        return response.json()

    async def _write(self, method: str, path: str, values: dict, delivery_id: str | None = None) -> tuple[Any, str]:
        """One shared action. The body is built from the arguments alone, so a
        replay with the same delivery id is the same request."""
        delivery_id = delivery_id or str(uuid.uuid4())
        body = {**values, 'actor': self.actor, 'request_id': delivery_id}
        for attempt in (1, 2):
            try:
                response = await self._client.request(method, API + path, json=body)
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise DoorError('unreachable', 'Crexil did not answer, so whether this was done is unknown.',
                                    f'Replay with delivery_id {delivery_id} and the same arguments; a replay '
                                    'never does the work twice.', delivery_id=delivery_id) from None
        if response.status_code >= 400:
            raise error_for(response, delivery_id=delivery_id)
        return response.json(), delivery_id

    # --- reads ---

    async def status(self) -> Status:
        try:
            setup = await self._get('setup')
        except DoorError as error:
            if error.kind != 'unreachable':
                raise
            return Status(reachable=False, preview=False, agent=actor_name(self.actor),
                          door_max_run_usd=self.ceiling_usd, connections=[])
        return Status(reachable=True, preview=bool(setup.get('preview')), agent=actor_name(self.actor),
                      door_max_run_usd=self.ceiling_usd,
                      connections=[Connection(provider=c['provider'], label=c['label'], ok=c.get('ok'),
                                              detail=plain(c.get('detail', '')))
                                   for c in setup.get('connections', [])])

    async def list_engines(self, payload: str | None = None, action: str | None = None) -> Engines:
        """Every engine, compactly; with a payload or an action, the engines that
        match, each with its detail."""
        found = []
        for op in await self._get('operations'):
            if payload and op.get('output_payload') != payload:
                continue
            if action and action not in op.get('actions', []):
                continue
            found.append(Engine(
                id=op['id'], label=op['label'], actions=op.get('actions', []), produces=op.get('output_payload', 'image'),
                availability=op['availability'], reasons=[plain(r) for r in op.get('reasons', [])],
                price_usd=op.get('price_usd'), price_unit=op.get('price_unit'),
                **(_engine_detail(op) if payload or action else {})))
        return Engines(engines=found)

    async def projects(self) -> Projects:
        return Projects(projects=[ProjectBrief(project_id=p['id'], name=p['name'], brief=p.get('brief', '')[:200],
                                               updated_at=p['updated_at']) for p in await self._get('projects')])

    async def project(self, project_id: str) -> Project:
        snapshot = await self._get(f'projects/{_id(project_id)}')
        record = await self._get(f'projects/{_id(project_id)}/record', {'limit': 10})
        p = snapshot['project']
        runs = [RunBrief(run_id=d['id'], title=d['title'], state=d['state'], action=d.get('setup', {}).get('action', ''),
                         takes=len(d.get('node_ids', [])), picked=len(d.get('selection', [])))
                for d in snapshot.get('directions', [])]
        limits = {k: v for k, v in (p.get('limits') or {}).items() if isinstance(v, (int, float, str, bool)) or v is None}
        return Project(project_id=p['id'], name=p['name'], brief=p.get('brief', ''), revision=p['revision'],
                       limits=limits, charged_usd=snapshot.get('charged_usd', 0), runs=runs,
                       recent_decisions=[_decision(d) for d in record.get('decisions', [])])

    async def review(self, run_id: str) -> Review:
        return self._review(await self._get(f'directions/{_id(run_id)}/review'))

    def _review(self, answer: dict) -> Review:
        reserved = Decimal(answer.get('reserved_micro_usd', micro_of(answer.get('reserved_usd', 0))))
        within = reserved <= self.ceiling
        engines = [EngineReview(id=op.get('id', ''), label=op.get('label', op.get('id', '')), count=op.get('count', 0),
                                ready=bool(op.get('ready')), errors=[plain(e) for e in op.get('errors', [])])
                   for op in answer.get('snapshot', {}).get('operations', [])]
        if not answer['ready']:
            step = 'The run cannot be submitted as it is. Fix what the errors name, then prepare it again.'
        elif not within:
            step = (f'It reserves {dollars(reserved)}, over this door\'s ceiling of {dollars(self.ceiling)}. '
                    f'Ask the artist to submit it, or to raise {CEILING}.')
        elif reserved > 0:
            step = (f'Submitting reserves {dollars(reserved)}. Ask the artist before spending, then call '
                    'crexil_submit_run with this review_token and confirm_reserved_usd.')
        else:
            step = 'Free to run. Call crexil_submit_run with this review_token and confirm_reserved_usd 0.'
        return Review(run_id=answer['direction_id'], revision=answer['revision'], review_token=answer['token'],
                      ready=answer['ready'], errors=[plain(e) for e in answer['errors']], attempts=answer['attempts'],
                      ready_attempts=answer['ready_attempts'], blocked_attempts=answer['blocked_attempts'],
                      reserved_usd=float(reserved / MICRO), maximum_usd=answer.get('maximum_usd'),
                      spending_policy=answer.get('snapshot', {}).get('setup', {}).get('spending_policy', 'hard_cap'),
                      door_max_run_usd=self.ceiling_usd, within_door_ceiling=within, engines=engines, next=step)

    async def run(self, run_id: str, wait_seconds: float = 0,
                  progress: Callable[[float, float], Awaitable[None]] | None = None) -> Run:
        """The compact, blind-safe view of one run. With a wait, polls until the
        run settles or the wait ends, at most WAIT_LIMIT_S; the result says which."""
        view = await self._get(f'directions/{_id(run_id)}/run')
        wait = max(0.0, min(float(wait_seconds), WAIT_LIMIT_S))
        started = time.monotonic()
        while wait and not _settled(view) and (elapsed := time.monotonic() - started) < wait:
            if progress is not None:
                await progress(elapsed, wait)
            await asyncio.sleep(self.poll_s)
            view = await self._get(f'directions/{_id(run_id)}/run')
        return _run(view)

    async def decisions(self, project_id: str, kind: str | None = None, limit: int = 100) -> Decisions:
        export = await self._get(f'projects/{_id(project_id)}/decisions')
        records = [r for r in export.get('records', []) if kind in (None, r.get('kind'))]
        kept = records[-limit:] if limit else records
        return Decisions(purpose=export.get('purpose', ''), count=len(records), truncated=len(kept) < len(records),
                         records=[_decision_line(r) for r in kept])

    async def search(self, project_id: str, query: str, limit: int = 10) -> Found:
        found = await self._get(f'projects/{_id(project_id)}/record', {'q': query, 'limit': limit})
        return Found(decisions=[_decision(d) for d in found.get('decisions', [])],
                     take_ids=found.get('material_ids', []), run_ids=found.get('direction_ids', []))

    # --- writes ---

    async def create_project(self, name: str, brief: str = '', delivery_id: str | None = None) -> Created:
        made, delivery_id = await self._write('POST', 'projects', {'name': name, 'brief': brief}, delivery_id)
        return Created(project_id=made['id'], name=made['name'], delivery_id=delivery_id)

    async def prepare(self, project_id: str, action: str, prompt: str, engines: list[EngineChoice],
                      spending_limit_usd: float, *, spending_policy: str = 'hard_cap',
                      sources: list[SourceRef] | None = None, title: str | None = None,
                      attempt_limit: int | None = None, blind: bool = False, run_id: str | None = None,
                      expected_revision: int | None = None, delivery_id: str | None = None) -> Prepared:
        """Create or revise a draft run, then read its review. Never dispatches."""
        limit = micro_of(spending_limit_usd)
        if limit < 0:
            raise DoorError('invalid_input', 'A spending limit is zero or more dollars.')
        if limit > self.ceiling:
            raise DoorError('over_door_ceiling',
                            f'This run asks for a {dollars(limit)} spending limit; this door allows {dollars(self.ceiling)} per run.',
                            f'Run free work with a zero spending limit, or ask the artist to raise {CEILING}. '
                            'Nothing was sent.')
        if not engines:
            raise DoorError('invalid_input', 'Name at least one engine for the run.', 'See crexil_list_engines.')
        if run_id is not None and expected_revision is None:
            raise DoorError('invalid_input', 'Revising a draft needs the revision you last read.',
                            'Read the run with crexil_get_run and pass its revision as expected_revision.')
        current = {op['id']: op for op in await self._get('operations')}
        unknown = [e.id for e in engines if e.id not in current]
        if unknown:
            raise DoorError('refused', f"No engine is called {', '.join(unknown)}.",
                            'Use an id from crexil_list_engines. Nothing was sent.')
        setup: dict[str, Any] = {
            'action': action, 'prompt': prompt,
            'references': [{'node_id': s.take_id, 'contribution': s.contribution,
                            **({'role': s.role} if s.role else {})} for s in sources or []],
            'operations': [{'operation_id': e.id, 'revision': current[e.id]['revision'], 'count': e.count,
                            'controls': e.controls} for e in engines],
            'spending_limit_usd': float(limit / MICRO), 'spending_policy': spending_policy}
        if attempt_limit is not None:
            setup['attempt_limit'] = attempt_limit
        if blind:
            setup['blind'] = True
        if run_id is None:
            made, delivery_id = await self._write('POST', f'projects/{_id(project_id)}/directions',
                                                  {'title': (title or _title(action, prompt)), 'setup': setup}, delivery_id)
        else:
            if title is None:
                title = (await self._get(f'directions/{_id(run_id)}/run'))['title']
            made, delivery_id = await self._write('PATCH', f'directions/{_id(run_id)}',
                                                  {'revision': expected_revision, 'title': title, 'setup': setup}, delivery_id)
        return Prepared(run_id=made['id'], review=await self.review(made['id']), delivery_id=delivery_id)

    async def submit(self, run_id: str, expected_revision: int, review_token: str, confirm_reserved_usd: float,
                     delivery_id: str | None = None) -> Submitted:
        """Submit a reviewed run. The confirmed amount must be what the review
        reserves and within this door's ceiling, or nothing is sent (law 22)."""
        current = await self.review(run_id)
        reserved, confirmed = micro_of(current.reserved_usd), micro_of(confirm_reserved_usd)
        if confirmed != reserved:
            raise DoorError('refused', f'You confirmed {dollars(confirmed)}, but this run reserves {dollars(reserved)}.',
                            'Read the review again and confirm the amount it states. Nothing was sent.')
        if reserved > self.ceiling:
            raise DoorError('over_door_ceiling',
                            f'This run reserves {dollars(reserved)}; this door allows {dollars(self.ceiling)} per run.',
                            f'Ask the artist to submit it, or to raise {CEILING}. Nothing was sent.')
        try:
            made, delivery_id = await self._write('POST', f'directions/{_id(run_id)}/submit',
                                                  {'revision': expected_revision, 'review_token': review_token},
                                                  delivery_id)
        except DoorError as error:
            if error.conflict == 'spending':
                again = await self.review(run_id)
                error.next_move = (f"Spending moved since your review. Reviewed again: ready {str(again.ready).lower()}, "
                                   f"reserves {dollars(micro_of(again.reserved_usd))}, token {again.review_token}"
                                   + (f", errors: {' '.join(again.errors)}" if again.errors else '')
                                   + '. Submit with those only if the artist still wants the run.')
            raise
        return Submitted(run_id=run_id, turn_id=made['turn_id'], reserved_usd=current.reserved_usd,
                         submitted_attempts=current.ready_attempts,
                         left_in_draft=current.errors if current.blocked_attempts else [], delivery_id=delivery_id)

    async def propose(self, project_id: str, reason: str, take_ids: list[str] | None = None,
                      run_id: str | None = None, delivery_id: str | None = None) -> Proposed:
        """Record an assessment. It never changes a pick (law 23: the artist decides)."""
        if not reason.strip():
            raise DoorError('invalid_input', 'A proposal needs a reason in words.')
        if not take_ids and not run_id:
            raise DoorError('invalid_input', 'Name the takes, or the run, the proposal is about.')
        values: dict[str, Any] = {'reason': reason}
        if take_ids:
            values['node_ids'] = take_ids
        if run_id:
            values['direction_id'] = run_id
        made, delivery_id = await self._write('POST', f'projects/{_id(project_id)}/assessments', values, delivery_id)
        return Proposed(decision_id=made['id'], kind=made['kind'], author=actor_name(made.get('actor')),
                        take_ids=made.get('node_ids', []), reason=made.get('reason'), delivery_id=delivery_id)


def _id(value: str) -> str:
    """An id travels as one path segment, never as a path."""
    if not value or '/' in value or '\\' in value or value in ('.', '..'):
        raise DoorError('invalid_input', 'Use an id as Crexil gave it.')
    return quote(value, safe='')


def _title(action: str, prompt: str) -> str:
    words = ' '.join(prompt.split())
    return (f'{action}: {words}' if words else action)[:120]


def _engine_detail(op: dict) -> dict:
    """An engine's detail: where it runs, what leaves the machine, how it is
    charged, its sources and its controls."""
    return {'location': plain(op.get('execution_location', '')), 'data_destination': plain(op.get('data_destination', '')),
            'cost_basis': plain(op.get('cost_basis', '')), 'takes_per_attempt': op.get('takes_per_attempt', 1),
            'attempt_max': op.get('attempt_max', 16),
            'sources': [SourceRule(role=s['role'], accepts=s.get('accepts', []),
                                   restrictions=[plain(r) for r in s.get('restrictions', [])])
                        for s in op.get('sources', [])],
            'controls': [Control(**{k: (plain(v) if k == 'reason' else v) for k, v in c.items()
                                    if k in Control.model_fields}) for c in op.get('inputs', [])]}


def _decision(d: dict) -> Decision:
    return Decision(decision_id=d['id'], kind=d['kind'], author=actor_name(d.get('actor')), reason=d.get('reason'),
                    take_ids=d.get('node_ids', []), run_id=d.get('direction_id'), at=d['created_at'])


def _decision_line(r: dict) -> DecisionLine:
    return DecisionLine(
        decision_id=r['decision_id'], kind=r['kind'], run_id=r.get('direction_id'), turn_id=r.get('run_id'),
        verb=r.get('verb'), brief=r.get('brief', ''),
        candidates=[Candidate(take_id=c['node_id'], position=c['position'], engine=c['engine'], provider=c.get('provider'),
                              model=c.get('model'), seed=c.get('seed'),
                              cost_usd=None if c.get('cost_micro_usd') is None else c['cost_micro_usd'] / 1_000_000)
                    for c in r.get('candidates', [])],
        chosen=r.get('chosen', []), reason=r.get('reason'), author=actor_name(r.get('author')),
        blind=bool(r.get('blind')), decided_at=r['decided_at'])


def _settled(view: dict) -> bool:
    return not any(c['state'] in MOVING or c.get('retrying') for c in view.get('cells', []))


def _run(view: dict) -> Run:
    cells = []
    for c in view.get('cells', []):
        cells.append(Cell(
            label=c['label'], position=c['position'], state=c['state'], attempt=c['attempt'], retrying=c['retrying'],
            kind=c.get('kind'), cause=plain(c.get('cause')), engine=c['engine'], identity_hidden=bool(c.get('identity_hidden')),
            seed=c.get('seed'), committed_usd=c.get('committed_usd'), charged_usd=c.get('charged_usd'),
            elapsed_ms=c.get('elapsed_ms'), collectable=c.get('collectable', False),
            takes=[Take(take_id=t['node_id'], label=t['label'], payload=t['payload'], verb=t['verb'],
                        available=t['available'], set_aside=t['set_aside'], chosen=t['chosen'],
                        audio=Audio(**{k: t['audio'].get(k) for k in Audio.model_fields}) if t.get('audio') else None)
                   for t in c.get('takes', [])]))
    return Run(run_id=view['run_id'], project_id=view['project_id'], title=view['title'], state=view['state'],
               revision=view['revision'], action=view['action'], prompt=view['prompt'], blind=view['blind'],
               cap_usd=view['cap_usd'], reserved_usd=view['reserved_usd'], charged_usd=view['charged_usd'],
               summary=plain(view.get('summary', '')), selection=view.get('selection', []),
               submissions=[Submission(turn_id=s['turn_id'], idx=s['idx'], blind=s['blind'], revealed=s['revealed'],
                                       paused=bool(s.get('control', {}).get('paused'))) for s in view.get('submissions', [])],
               cells=cells, decisions=[_decision(d) for d in view.get('decisions', [])], settled=_settled(view))


# --- the MCP layer ---

Id = Annotated[str, Field(min_length=1, max_length=200)]
Delivery = Annotated[str | None, Field(description='Only to replay a write whose outcome was unknown')]

INSTRUCTIONS = """Crexil is a local bench for AI-made media. One brief goes to several engines,
the takes land in one grid, and every decision is recorded with its author and reason.

Words: a project holds runs; a run is one brief sent to one or more engines; each attempt lands
takes, named like 3B. Engines are image, audio (voice-design, speak, sfx) and 3D operations.

Typical flow: crexil_list_engines (with a payload or an action for each engine's controls),
crexil_get_project or crexil_create_project,
crexil_prepare_run (creates a draft and reviews it; nothing runs), crexil_submit_run with the
reviewed token and amount, crexil_get_run with wait_seconds until it settles, then
crexil_propose to recommend takes with a reason.

Rules: the artist decides. You propose; you never pick, reveal blind runs, deliver or change
limits. Ask the artist before any paid submission. This door's per-run ceiling is the
artist's --max-run-usd launch flag; at $0 only free work can run, since it compares money,
not where an engine runs. Results never contain media or file paths: you work from facts,
labels and reasons."""


def build_server(door: Door):
    """The MCP server over one Door. Imports the SDK here, so the core stays testable without it."""
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    server = MCPServer(name='crexil', title='Crexil agent door', instructions=INSTRUCTIONS, version='1.0.0')
    reading = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    writing = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
    spending = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

    async def call(step):
        try:
            return await step
        except DoorError as error:
            raise ToolError(str(error)) from None

    @server.tool(name='crexil_status', annotations=reading,
                 description='Whether Crexil is reachable, which providers are connected, who this door writes as, '
                             'and its per-run spending ceiling.')
    async def crexil_status() -> Status:
        return await call(door.status())

    @server.tool(name='crexil_list_engines', annotations=reading,
                 description='The engines a run can use: each one\'s id, label, actions, what it produces, its price '
                             'and whether it can run now and why not. Name a payload or an action to add where each '
                             'runs, what leaves the machine, how it is charged, its sources and its controls.')
    async def crexil_list_engines(
            payload: Annotated[Literal['image', 'audio', '3d'] | None, Field(description='Only engines producing this')] = None,
            action: Annotated[str | None, Field(description='Only engines serving this action, such as voice-design')] = None,
    ) -> Engines:
        return await call(door.list_engines(payload, action))

    @server.tool(name='crexil_list_projects', annotations=reading, description='The projects, newest first.')
    async def crexil_list_projects() -> Projects:
        return await call(door.projects())

    @server.tool(name='crexil_get_project', annotations=reading,
                 description="One project's brief, limits, runs and recent decisions.")
    async def crexil_get_project(project_id: Id) -> Project:
        return await call(door.project(project_id))

    @server.tool(name='crexil_create_project', annotations=writing,
                 description='Create a project with a name and a brief. Limits stay with the artist.')
    async def crexil_create_project(name: Annotated[str, Field(min_length=1, max_length=200)],
                                    brief: Annotated[str, Field(max_length=32000)] = '',
                                    delivery_id: Delivery = None) -> Created:
        return await call(door.create_project(name, brief, delivery_id))

    @server.tool(name='crexil_prepare_run', annotations=writing,
                 description='Create a draft run, or revise one with run_id and expected_revision, and return its '
                             'review: whether it can run, why not, and what it would reserve. Never runs anything. '
                             'Actions: generate, continue, combine, mesh, voice-design, speak, sfx.')
    async def crexil_prepare_run(
            project_id: Id,
            action: Annotated[str, Field(description='generate, voice-design, speak, sfx, continue, combine or mesh')],
            prompt: Annotated[str, Field(max_length=32000, description='The brief: words to speak, a voice to design, an image to make')],
            engines: Annotated[list[EngineChoice], Field(min_length=1, max_length=32)],
            spending_limit_usd: Annotated[float, Field(ge=0, description="The run's own cap; within this door's ceiling")],
            spending_policy: Literal['hard_cap', 'published_tariff'] = 'hard_cap',
            sources: Annotated[list[SourceRef] | None, Field(description='Takes the run starts from, such as a voice')] = None,
            title: Annotated[str | None, Field(max_length=200)] = None,
            attempt_limit: Annotated[int | None, Field(ge=1, le=1000)] = None,
            blind: Annotated[bool, Field(description='Hide which engine made each take until the artist reveals it')] = False,
            run_id: Annotated[str | None, Field(description='Revise this draft instead of creating one')] = None,
            expected_revision: Annotated[int | None, Field(description="The draft's revision you last read")] = None,
            delivery_id: Delivery = None) -> Prepared:
        return await call(door.prepare(project_id, action, prompt, engines, spending_limit_usd,
                                       spending_policy=spending_policy, sources=sources, title=title,
                                       attempt_limit=attempt_limit, blind=blind, run_id=run_id,
                                       expected_revision=expected_revision, delivery_id=delivery_id))

    @server.tool(name='crexil_review_run', annotations=reading,
                 description="Read a run's review again: whether it can run and what it reserves, with a fresh token.")
    async def crexil_review_run(run_id: Id) -> Review:
        return await call(door.review(run_id))

    @server.tool(name='crexil_submit_run', annotations=spending,
                 description='Submit a reviewed run. Restate the reviewed amount as confirm_reserved_usd. Refused '
                             "when the amount differs or exceeds this door's ceiling. Ask the artist before paid work.")
    async def crexil_submit_run(run_id: Id, expected_revision: Annotated[int, Field(ge=0)],
                                review_token: Annotated[str, Field(min_length=64, max_length=64)],
                                confirm_reserved_usd: Annotated[float, Field(ge=0)],
                                delivery_id: Delivery = None) -> Submitted:
        return await call(door.submit(run_id, expected_revision, review_token, confirm_reserved_usd, delivery_id))

    @server.tool(name='crexil_get_run', annotations=reading,
                 description="One run's grid: each cell's state, cause and takes, the selection and recent decisions. "
                             'With wait_seconds, waits up to 55 s for the run to settle; settled says whether it did. '
                             'A blind run names no engine until the artist reveals it.')
    async def crexil_get_run(run_id: Id, ctx: Context,
                             wait_seconds: Annotated[float, Field(ge=0, le=WAIT_LIMIT_S)] = 0) -> Run:
        async def progress(done: float, total: float) -> None:
            with contextlib.suppress(Exception):   # progress is a courtesy a client may not take
                await ctx.report_progress(done, total)
        return await call(door.run(run_id, wait_seconds, progress))

    @server.tool(name='crexil_propose', annotations=writing,
                 description='Recommend takes, or comment on a run, with a reason. Recorded as your assessment; it '
                             "never changes the artist's pick.")
    async def crexil_propose(project_id: Id, reason: Annotated[str, Field(min_length=1, max_length=4000)],
                             take_ids: Annotated[list[str] | None, Field(max_length=32)] = None,
                             run_id: Annotated[str | None, Field(description='Comment on a whole run')] = None,
                             delivery_id: Delivery = None) -> Proposed:
        return await call(door.propose(project_id, reason, take_ids, run_id, delivery_id))

    @server.tool(name='crexil_get_decisions', annotations=reading,
                 description="The project's decision record: what was chosen or assessed, among which takes, by whom "
                             'and why. It is the artist\'s record, never a dataset, and it holds no media.')
    async def crexil_get_decisions(project_id: Id,
                                   kind: Literal['selection', 'sequence_choice', 'assessment'] | None = None,
                                   limit: Annotated[int, Field(ge=1, le=200)] = 100) -> Decisions:
        return await call(door.decisions(project_id, kind, limit))

    @server.tool(name='crexil_search_record', annotations=reading,
                 description="Search a project's record by words in reasons, prompts and names.")
    async def crexil_search_record(project_id: Id, query: Annotated[str, Field(min_length=1, max_length=500)],
                                   limit: Annotated[int, Field(ge=1, le=50)] = 10) -> Found:
        return await call(door.search(project_id, query, limit))

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crexil's agent door: an MCP server over stdio.")
    parser.add_argument('--url', default=os.environ.get('CREXIL_URL') or DEFAULT_URL,
                        help='The local Crexil address (CREXIL_URL); loopback only')
    parser.add_argument('--agent-name', default=os.environ.get('CREXIL_AGENT_NAME') or 'Claude',
                        help='Written as agent:<name> on every action (CREXIL_AGENT_NAME)')
    parser.add_argument('--max-run-usd', default=os.environ.get('CREXIL_DOOR_MAX_RUN_USD') or '0',
                        help="This door's per-run spending ceiling in dollars (CREXIL_DOOR_MAX_RUN_USD); 0 means free work only")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)   # stdout carries the protocol
    try:
        door = Door(args.url, args.agent_name, args.max_run_usd)
    except (ValueError, ArithmeticError) as exc:
        parser.error(str(exc))
    build_server(door).run('stdio')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
