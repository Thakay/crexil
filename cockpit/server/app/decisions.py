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


def _candidate(node: dict, position: int, hidden: frozenset[str]) -> dict:
    if node['turn_id'] in hidden:
        return {'node_id': node['id'], 'position': position, 'engine': blind.HIDDEN_ENGINE,
                'provider': None, 'model': None, 'seed': None, 'cost_micro_usd': None}
    engine = node['engine']
    cost = node['charged_usd'] if node['charged_usd'] is not None else node['committed_usd']
    return {'node_id': node['id'], 'position': position, 'engine': engine or 'imported',
            'provider': wiring.route_engine(engine) if engine else None,
            'model': _model(node) if engine else None, 'seed': node['seed'], 'cost_micro_usd': _micro_usd(cost)}


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
                'candidates': [_candidate(node, index, hidden) for index, node in enumerate(considered)],
                'chosen': item['node_ids'] if chooses else [], 'reason': item['reason'],
                'author': item['actor'], 'blind': item['seq'] in blind_events, 'decided_at': item['created_at']})
        return {'project_id': workspace_id, 'generated_at': now_iso(), 'purpose': PURPOSE, 'records': records}
