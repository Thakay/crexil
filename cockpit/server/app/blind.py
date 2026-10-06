"""Blind runs: a run whose engine identity is withheld until an authored reveal.

The run flag is set at preparation (`Preparation.blind`) and recorded at Run in
`blind_runs`. A run stays hidden until its first `run.revealed` event, which
names its author (law 23). While hidden, every read that tells its takes apart
by engine passes through `mask`: the snapshot, `/api/events`, `/api/queue`, the
workspace and node reads, the tray, the per-take record, record search, the
decision export and execution files. A masked item says so with
`identity_hidden: true`; its engine reads `HIDDEN_ENGINE`, never null, because a
null engine means imported material. Run totals and caps stay visible (law 22).
Delivery and Promote refuse a hidden take, so no manifest or hand-off folder
names its engine before the reveal.

What blindness does not hide: which engines the run asked for (the artist chose
them), which takes share one attempt, a take's own audible or visible content,
the queue's lane while a take is in flight (law 13 keeps the local lane
visible), and a failure's kind and retries (laws 4 and 9 keep the next move and
every retry visible). It guards judgment at the grid; it is not a secret against
the file bytes themselves.

Decisions stay selections and assessments: a selection or assessment made while
its run is hidden records `blind: true` through `selection_facts`. Take positions
are arranged once at Run by `arrange`; after that they never move (law 8).
"""

from __future__ import annotations

import random
from pathlib import PurePosixPath

HIDDEN_ENGINE = 'hidden'

# Keys that name, price or fingerprint one take's engine. Inside free-form
# records (event payloads, descriptors, origins) a hidden run drops them.
IDENTITY_KEYS = frozenset({
    'engine', 'engine_id', 'engine_revision', 'model', 'model_id', 'provider', 'transport', 'destination',
    'seed', 'job_ref', 'generation_id', 'committed_usd', 'charged_usd', 'cost_usd', 'cost_micro_usd',
    'reserved_usd', 'reserved_micro_usd', 'maximum_usd_per_attempt', 'tariff', 'measures',
    'receipt', 'receipts', 'terms', 'voice', 'alignment', 'external', 'submitted', 'submitted_graph',
    'operation_id', 'operation_revision', 'registration_id', 'registration_revision', 'executor',
    'frame', 'detail', 'duration_ms', 'container', 'codec', 'sample_rate', 'channels', 'controls', 'fixed',
    'filename', 'identity',
})

# The audio facts a take's own sound already carries stay readable.
_AUDIBLE = ('duration_s', 'loudness_lufs', 'peak_dbfs', 'peaks', 'text')

# One plain line per failure kind, naming no engine or provider (law 3). The
# kind stays on the cell, so its next move is still offered (law 4).
_CAUSES = {
    'oom': 'The card ran out of video memory for this take.',
    'blank': 'The engine returned an empty result.',
    'error': 'The engine reported an error.',
    'unreachable': 'The engine could not be reached.',
    'timeout': 'No result came back in time.',
    'dead_key': 'The provider refused its key or has no credit.',
    'rate_limit': 'The provider asked to slow down.',
    'charged_blank': 'The provider charged and returned nothing.',
    'moderation': "The provider's filter refused the request.",
    'model_down': 'The engine cannot serve this model right now.',
    'interrupted': 'This take was stopped.',
    'restart': 'The server restarted while this take was out.',
    'internal': 'Crexil failed on this take.',
    'collection': "This take's files could not be collected.",
    'preflight': 'The request was stopped before it was sent.',
}
_REVEAL_FOR_MORE = " Reveal the run to read the engine's own words."


class NotBuilt(ValueError):
    """A blind-run behavior that is not built yet; routes answer 501 in words."""


async def hidden_turns(db, workspace_id: str | None = None) -> frozenset[str]:
    """Runs that are blind and not yet revealed."""
    return frozenset(run['turn_id'] for run in await db.blind_runs(workspace_id) if not run['revealed'])


async def mark(db, turn_id: str, workspace_id: str, actor: dict) -> None:
    """Record at Run, inside the accepting transaction, that this run is blind."""
    await db.mark_blind_run(turn_id, workspace_id, actor)


async def apply(db, kind: str, items: list[dict], *, turn_of=None,
                workspace_id: str | None = None) -> list[dict]:
    """Mask every item that belongs to a hidden run. `turn_of` reads an item's
    turn id (default: its `turn_id`)."""
    hidden = await hidden_turns(db, workspace_id)
    if not hidden:
        return items
    turn_of = turn_of or (lambda item: item.get('turn_id'))
    return [mask(kind, item) if turn_of(item) in hidden else item for item in items]


async def is_hidden(db, turn_id: str | None) -> bool:
    return bool(turn_id) and turn_id in await hidden_turns(db)


def plain_cause(kind: str | None) -> str:
    """A hidden take's failure line: the kind's plain words, no engine named."""
    return _CAUSES.get(kind or '', 'This take did not land.') + _REVEAL_FOR_MORE


def scrub(value):
    """A free-form record without the keys that identify a take's engine."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key not in IDENTITY_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def _hidden_name(filename: str, label: str) -> str:
    """An engine-neutral file name that keeps the extension the bytes need."""
    return label + PurePosixPath(filename or '').suffix


def mask(kind: str, item: dict) -> dict:
    """The one masking rule. `kind` is one of: 'node', 'slot', 'event', 'queue',
    'group', 'material_record'. Returns the item as a hidden run's reader may see
    it: `identity_hidden` true, engine `HIDDEN_ENGINE`, and model, seed, per-take
    cost, job reference, provider descriptors, timing and engine-naming causes
    withheld. Shapes keep every field their wire model requires."""
    out = dict(item)
    if kind == 'event':
        out['charged_usd'] = None
        out['payload'] = {**scrub(item.get('payload') or {}), 'identity_hidden': True}
        if 'cause' in out['payload']:
            out['payload']['cause'] = plain_cause(out['payload'].get('kind'))
        return out
    out['identity_hidden'] = True
    if kind == 'material_record':
        out.update(request=None, controls={}, submitted=None)
        return out
    if 'engine' in out:
        out['engine'] = HIDDEN_ENGINE
    for key in ('seed', 'committed_usd', 'charged_usd', 'job_ref', 'frame', 'facts', 'detail',
                'duration_ms', 'started_at', 'finished_at', 'external', 'submitted_graph'):
        if key in out:
            out[key] = None
    if 'dials' in out:
        out['dials'] = {key: out['dials'][key] for key in ('prompt', 'parents', 'verb') if key in (out['dials'] or {})}
    if out.get('cause'):
        out['cause'] = plain_cause(out.get('kind'))
    if kind == 'queue' and out.get('bench') and not str(out['bench']).startswith('lane:'):
        out['bench'] = None                    # an engine or engine-in-run scope names it
    if kind == 'group':
        out['metadata'] = scrub(out.get('metadata') or {})
        out['outputs'] = [
            {**member, 'identity': f"output:{member['ordinal']}", 'descriptor': scrub(member.get('descriptor') or {}),
             'filename': _hidden_name(member['filename'], f"output-{member['ordinal']}")}
            for member in out.get('outputs') or [] if member.get('node_id') is not None]
    if kind == 'node':
        if out.get('material'):
            material = dict(out['material'])
            material['filename'] = _hidden_name(material['filename'], 'hidden-take')
            material['origin'] = scrub(material.get('origin') or {})
            out['material'] = material
        if 'retained_files' in out:
            out['retained_files'] = []
        if out.get('audio'):
            heard = {key: out['audio'][key] for key in _AUDIBLE if key in out['audio']}
            out['audio'] = {'container': None, 'codec': None, 'sample_rate': None, 'channels': None,
                            'voice': None, 'alignment': None, **heard}
    return out


def arrange(specs: list[dict]) -> list[dict]:
    """The frozen order of a blind run's takes, decided once at Run from the
    system's randomness, so positions never group takes by engine (law 8)."""
    arranged = list(specs)
    random.SystemRandom().shuffle(arranged)
    return arranged


async def _turns_of(db, node_ids: list[str]) -> set[str]:
    turns = set()
    for nid in node_ids:
        node = await db.get_node(nid)
        if node and node['turn_id']:
            turns.add(node['turn_id'])
    return turns


async def facts_for(db, node_ids: list[str], turn_ids: list[str] = ()) -> dict:
    """What a decision records about blindness: the hidden runs among its
    subjects, or nothing when none is hidden."""
    hidden = sorted((await _turns_of(db, node_ids) | {t for t in turn_ids if t}) & await hidden_turns(db))
    return {'blind': True, 'blind_turn_ids': hidden} if hidden else {}


async def selection_facts(db, direction_id: str, node_ids: list[str]) -> dict:  # noqa: ARG001
    """Facts a selection event records about blindness: `{'blind': True,
    'blind_turn_ids': [...]}` when a chosen take's run is still hidden."""
    return await facts_for(db, node_ids)


def may_serve(member: dict) -> bool:
    """Whether a hidden run's execution file may be served: the takes themselves
    stay playable; receipts, companions and runtime inputs wait for the reveal."""
    return member.get('node_id') is not None


async def reveal(db, turn_id: str, body) -> dict:
    """Record the authored reveal of a blind run (law 23) and return its
    `BlindRunView`. A run already revealed keeps its first reveal and author."""
    turn = await db.get_turn(turn_id)
    run = next((r for r in await db.blind_runs(turn['workspace_id']) if r['turn_id'] == turn_id), None) if turn else None
    if run is None:
        raise LookupError('This run is not a blind run in this project.')
    if not run['revealed']:
        await db.append_event('run.revealed', workspace_id=run['workspace_id'], turn_id=turn_id,
                              payload={'actor': body.actor.model_dump(), 'reason': body.reason})
        await db.touch_workspace(run['workspace_id'])
        run = next(r for r in await db.blind_runs(run['workspace_id']) if r['turn_id'] == turn_id)
    return run
