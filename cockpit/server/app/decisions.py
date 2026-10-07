"""The decision record export: the artist's own record of what was chosen, among
which takes, by whom, and why, derived from selection and assessment events.

It is never a training or evaluation dataset and holds no audio: ElevenLabs'
Prohibited Use Policy forbids using its output in either, and Crexil records
decisions, not material. Lines carry identities and words only, never bytes,
paths or file addresses. A hidden run's candidates keep their engine withheld
until its reveal, and every line keeps its author (law 23).

A selection line's candidates are the takes of the runs its choice came from,
and `chosen` is the new selection. An assessment chooses nothing: its
candidates are the takes it judged, and `chosen` is empty.

A candidate's cost is its attempt's: the charge when it is known, else what the
attempt committed. An attempt is charged once however many takes it returns (a
Voice Design call returns three previews), so a line states that cost on the
first of the attempt's takes among its candidates, and the attempt's other takes
read null: summing a line's candidates counts each attempt once.
"""

from __future__ import annotations

from decimal import Decimal

from . import blind, wiring
from .crexil_record import decisions
from .crexil_store import CreativeStore
from .db import now_iso

PURPOSE = ("The artist's decision record: which take was chosen, among which, by whom and why. "
           "It is not a training or evaluation dataset and holds no audio.")


class NotBuilt(ValueError):
    """An export behavior that is not built yet; routes answer 501 in words."""


def _micro_usd(amount) -> int | None:
    return None if amount is None else int((Decimal(str(amount)) * 1_000_000).to_integral_value())


def _model(node: dict) -> str | None:
    """The model the take was made with, as its frozen request states it."""
    external = node.get('external') or {}
    if external.get('transport') == 'audio':
        return external.get('model_id')
    if wiring.route_engine(node['engine']) == 'openrouter':
        return node['engine']
    return external.get('model')


def _attempt(node: dict) -> tuple | str:
    """The attempt that made a take: its cell and attempt number, or the take
    itself for material that came from no run."""
    return (node['slot_id'], node['take']) if node['slot_id'] else node['id']


def _attempt_costs(nodes) -> dict:
    """Each attempt's one cost in dollars: the charge one of its takes carries
    when it is known, else what the attempt committed. Every take of an attempt
    carries its commitment; only the take that completed it carries its charge."""
    charged, committed = {}, {}
    for node in nodes:
        if node['charged_usd'] is not None:
            charged.setdefault(_attempt(node), node['charged_usd'])
        if node['committed_usd'] is not None:
            committed.setdefault(_attempt(node), node['committed_usd'])
    return {**committed, **charged}


def _candidate(node: dict, position: int, hidden: frozenset[str], cost: float | None) -> dict:
    if node['turn_id'] in hidden:
        return {'node_id': node['id'], 'position': position, 'engine': blind.HIDDEN_ENGINE,
                'provider': None, 'model': None, 'seed': None, 'cost_micro_usd': None}
    engine = node['engine']
    return {'node_id': node['id'], 'position': position, 'engine': engine or 'imported',
            'provider': wiring.route_engine(engine) if engine else None,
            'model': _model(node) if engine else None, 'seed': node['seed'], 'cost_micro_usd': _micro_usd(cost)}


def _candidates(considered: list[dict], hidden: frozenset[str], costs: dict) -> list[dict]:
    """The takes a decision was made among, in frozen grid order, each attempt's
    cost on the first of its takes here and null on the others."""
    stated, found = set(), []
    for position, node in enumerate(considered):
        attempt = _attempt(node)
        found.append(_candidate(node, position, hidden, None if attempt in stated else costs.get(attempt)))
        stated.add(attempt)
    return found


def _frozen_position(node: dict) -> tuple:
    """The frozen grid order (law 8): run, cell, then take within the cell. A
    candidate's `position` is its index in this order."""
    return (node['turn_idx'] if node['turn_idx'] is not None else -1,
            node['slot_pos'] if node['slot_pos'] is not None else -1, node['output_index'] or 0, node['id'])


async def export(db, workspace_id: str) -> dict:
    """A `DecisionExport` for one project, one record per decision event."""
    async with db._locked():
        await CreativeStore(db).project(workspace_id)
        nodes = {node['id']: node for node in await db.list_nodes(workspace_id, include_purged=True)}
        turns = {turn['id']: turn for turn in await db.list_turns(workspace_id)}
        hidden = await blind.hidden_turns(db, workspace_id)
        costs = _attempt_costs(nodes.values())
        blind_events = {row['seq'] for row in await db._all(
            "SELECT seq FROM events WHERE workspace_id=? AND json_extract(payload_json,'$.blind')=1", (workspace_id,))}
        records = []
        for item in await decisions(db, workspace_id):
            subjects = [nodes[n] for n in item['node_ids'] if n in nodes]
            chooses = item['kind'] != 'assessment'
            runs = {node['turn_id'] for node in subjects if node['turn_id']} or (
                {item['turn_id']} if item['turn_id'] in turns else set())
            if chooses or (item['turn_id'] and not subjects):
                considered = [n for n in nodes.values() if n['turn_id'] in runs] or subjects
            else:
                considered = subjects
            considered = sorted(considered, key=_frozen_position)
            run_id = next(iter(runs)) if len(runs) == 1 else None
            verbs = {node['verb'] for node in considered}
            records.append({
                'decision_id': item['event_id'], 'kind': item['kind'], 'project_id': workspace_id, 'run_id': run_id,
                'direction_id': item['direction_id'], 'verb': verbs.pop() if len(verbs) == 1 else None,
                'brief': (turns[run_id]['prompt'] if run_id else next(
                    (node['prompt'] for node in considered if node['prompt']), None)) or '',
                'candidates': _candidates(considered, hidden, costs),
                'chosen': item['node_ids'] if chooses else [], 'reason': item['reason'],
                'author': item['actor'], 'blind': item['seq'] in blind_events, 'decided_at': item['created_at']})
        return {'project_id': workspace_id, 'generated_at': now_iso(), 'purpose': PURPOSE, 'records': records}
